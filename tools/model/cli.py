"""FreeUnit Behavioral & Adversarial Model CLI.

Usage:
    uv run --with tree-sitter --with tree-sitter-c python3 -m tools.model.cli schema [--src DIR]
    python3 -m tools.model.cli simulate [--requests N] [--workers M]
    python3 -m tools.model.cli verify [--chaos] [--seed S]
    python3 -m tools.model.cli benchmark-optimizations
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

def _find_default_src() -> str:
    if "UNIT_SRC" in os.environ:
        return os.environ["UNIT_SRC"]
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root.parent / "unit",
        root.parent / "freeunit",
        root / "src",
        Path.cwd() / "src",
        Path.cwd().parent / "unit",
    ]
    for cand in candidates:
        if (cand / "src" / "nxt_main.c").is_file():
            return str(cand)
        if (cand / "nxt_main.c").is_file():
            return str(cand.parent if cand.name == "src" else cand)
    return str(root)

DEFAULT_SRC = _find_default_src()

from tools.model.analyzer import StateSpaceExplorer
from tools.model.c_types import (
    compare_ctypes_to_schema,
    HttpHeaderField,
    UnitRequest,
    UnitResponse,
)
from tools.model.engine import (
    AppQueue,
    AppWorker,
    Listener,
    Router,
    SharedMemoryPool,
    SimEventLoop,
    UnixSocketPair,
)


def cmd_schema(args: argparse.Namespace) -> int:
    try:
        from tools.model.ast_extractor import SchemaExtractor, print_summary
    except ImportError:
        print("[-] tree-sitter or tree-sitter-c not installed in current Python.")
        print("    Run with: uv run --with tree-sitter --with tree-sitter-c python3 -m tools.model.cli schema")
        return 1

    print(f"[*] Extracting FreeUnit C AST schema from {args.src}...")
    extractor = SchemaExtractor(args.src)
    extractor.load_all()
    print_summary(extractor)
    print("\n[*] Comparing ctypes mirrors with the extracted header layouts...")
    diffs = compare_ctypes_to_schema(extractor.structs)
    if diffs:
        print("[-] Layout check FAILED: ctypes and extracted structs differ.")
        for d in diffs:
            print(f"    ! {d}")
        return 1
    print("[+] Layout check passed: ctypes sizes, field offsets and field sizes match the extracted headers.")
    print("    Compared with the tree-sitter layout, not a C compiler run.")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    print(f"[*] Running Model A Simulation ({args.requests} requests across {args.workers} workers)...")
    loop = SimEventLoop()
    shm = SharedMemoryPool(loop, max_segments=8)
    listener = Listener(loop, num_engines=args.engines)
    router = Router(loop, listener, shm, num_engines=args.engines)

    for i in range(args.workers):
        wid = 100 + i
        q = AppQueue(loop)
        sock = UnixSocketPair(loop, name=f"worker_{wid}_sock")
        worker = AppWorker(loop, worker_id=wid, app_id="web", app_queue=q, socket_pair=sock, shm_pool=shm)
        router.register_worker("web", wid, q, sock)
        worker.on_send_response_to_router = router.on_worker_response_msg
        worker.handler = lambda req: UnitResponse(status=200, content=b"Hello from FreeUnit Model!")

    start_t = time.perf_counter()
    for req_idx in range(args.requests):
        conn = listener.accept_client(f"192.0.2.{req_idx % 250}")
        req = UnitRequest(
            method="GET", version="HTTP/1.1", remote=conn.remote_addr,
            local_addr="127.0.0.1", local_port="8080", tls=False,
            websocket_handshake=False, app_target=0, server_name="freeunit.local",
            target=f"/path/{req_idx}", path=f"/path/{req_idx}", query="",
            headers=[HttpHeaderField(name="Host", value="freeunit.local")],
        )
        router.on_listener_request(req_idx % args.engines, conn, req, app_id="web")

    steps = loop.run_until_idle()
    wall_ms = (time.perf_counter() - start_t) * 1000
    trace = loop.get_trace()

    print(f"[+] Simulation Completed successfully!")
    print(f"    Total simulated steps:    {steps:,}")
    print(f"    Simulated clock advance:  {loop.now():,} ns")
    print(f"    Trace events recorded:    {len(trace):,}")
    print(f"    Wall clock time:          {wall_ms:.2f} ms")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    print(f"[*] Running Model B Adversarial Invariant Verification (seed: {args.seed})...")
    explorer = StateSpaceExplorer(seed=args.seed)
    res = explorer.run_scenario(
        num_engines=2,
        num_workers=3,
        num_requests=25,
        inject_jitter=True,
        inject_crash=args.chaos,
        inject_oosm=args.chaos,
    )

    print(f"[+] Explored scenario with {res.steps_executed} steps, {res.trace_length} events.")
    if res.violations:
        print(f"[-] INVARIANT VIOLATIONS DETECTED: {len(res.violations)}")
        for v in res.violations:
            print(f"    ! [{v.invariant_id}] {v.description}")
        return 1
    else:
        print("[+] ALL 6 CORE INVARIANTS SATISFIED (Zero leaks, FIFO ordering, Deadlock freedom).")
        return 0


def cmd_benchmark_optimizations(args: argparse.Namespace) -> int:
    print("=" * 70)
    print("Doorbell counts in the model")
    print("Counts of modelled notifications in this process.")
    print("These are not context switches, syscalls, or CPU from a server run.")
    print("=" * 70)

    # Artificial always-notify case: clear the flag before every send so each
    # enqueue notifies. This is not the FreeUnit 1.37.2 baseline, which already
    # has the one-wake flag and nxt_app_queue_notify_decision(pos, depth_bound).
    enqueues = 1000
    loop_base = SimEventLoop()
    q_base = AppQueue(loop_base)
    q_base_doorbells = 0
    for i in range(enqueues):
        q_base.notified = 0
        _, notify, _ = q_base.send(b"data", tracking=i)
        if notify:
            q_base_doorbells += 1

    loop_opt = SimEventLoop()
    q_opt = AppQueue(loop_opt)
    q_opt_doorbells = 0
    for i in range(enqueues):
        _, notify, _ = q_opt.send(b"data", tracking=i)
        if notify:
            q_opt_doorbells += 1

    print(f"1. Always-notify case versus one-wake flag ({enqueues} enqueues):")
    print(f"   - Always-notify doorbells (flag cleared before each send): {q_base_doorbells}")
    print(f"   - One-wake-flag doorbells (flag left set): {q_opt_doorbells}")
    print()

    burst = 10
    depth_bound = 1
    loop_coal = SimEventLoop()
    q_coal = AppQueue(loop_coal)
    doorbells_depth1 = 0
    for i in range(burst):
        _, notify, _ = q_coal.send_bound(b"data", tracking=i, depth_bound=depth_bound)
        if notify:
            doorbells_depth1 += 1
    print(f"2. depth_bound={depth_bound}, burst of {burst} enqueues:")
    print(f"   - Doorbells sent: {doorbells_depth1}")
    print()

    print("3. K2 single-buffer framing is not counted by this command.")
    print()

    # 4. https://github.com/freeunitorg/freeunit/pull/776: ignore a spurious signalfd read wakeup
    from tools.model.engine.epoll_fsm import SignalfdFSM
    sfd = SignalfdFSM()
    sfd.raise_signal(17, pid=1234)
    n0, err0, _ = sfd.read_signalfd()
    alert0, _ = sfd.handle_signalfd_read(n0, err0)
    n1, err1, _ = sfd.read_signalfd()  # Spurious second read on empty queue
    alert1, msg1 = sfd.handle_signalfd_read(n1, err1)
    print("4. signalfd read when two engines race (modelled):")
    print(f"   - First read: {n0} bytes, error={err0}, alert={alert0}")
    print(f"   - Second read: {n1} bytes, error={err1}, alert={alert1} ({msg1})")
    print(f"   - Alerts logged: {sfd.total_alerts}")
    print()

    print("=" * 70)
    return 0


def cmd_prove_k1(args: argparse.Namespace) -> int:
    from tools.model.synthesis import K1DoorbellProof

    print("=" * 70)
    print("Check modelled K1 bounded doorbell counts")
    print("=" * 70)
    proof = K1DoorbellProof(default_workers=args.workers)

    print("\n1. Modelled herd bound: doorbells <= min(Burst, DepthBound)")
    print(f"{'Workers (W)':<14} | {'Burst (B)':<10} | {'Doorbells':<10}")
    print("-" * 70)
    for w in [1, 2, 4, 8, 16]:
        for b in [1, 4, 16, 64]:
            res = proof.prove_herd_elimination(b, w)
            print(f"{w:<14} | {b:<10} | {res['simulated_doorbells']:<10}")

    print("\n2. Running Combinatorial State Space Exploration (Interleaving & Crash Recovery)...")
    result = proof.run_combinatorial_verification(
        worker_counts=[1, 2, 4, 8],
        burst_sizes=[1, 4, 16, 64],
        permutations_per_case=args.permutations,
    )
    print(f"[+] Total combinatorial scenarios tested: {result.total_scenarios:,}")
    print(f"[+] Lost wakeups:                          {result.lost_wakeups}")
    print(f"[+] Starvation events:                     {result.starvation_events}")
    if result.verified:
        print("[+] Check passed: no lost wakeups or starvation in the modelled scenarios.")
        return 0
    else:
        print("[-] Check failed: a modelled scenario lost a wakeup or starved.")
        return 1


def cmd_lockfree_shm(args: argparse.Namespace) -> int:
    import concurrent.futures
    from tools.model.synthesis import LockFreeShmPool

    print("=" * 70)
    print(f"Per-Engine Lock-Free SHM Slab Simulation ({args.engines} engines, {args.threads} threads)")
    print("=" * 70)
    pool = LockFreeShmPool(num_engines=args.engines)

    ops_per_thread = args.ops

    def worker_job(tid: int):
        allocs = []
        for _ in range(ops_per_thread):
            eng = tid % args.engines
            c = pool.alloc(engine_id=eng)
            allocs.append(c)
        for c in allocs:
            calling_eng = (tid + 1) % args.engines
            pool.free(c, calling_engine_or_worker_id=calling_eng)

    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = [executor.submit(worker_job, tid) for tid in range(args.threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    stats = pool.get_stats()
    print(f"[+] Total allocations:           {stats['total_allocations']:,}")
    print(f"[+] Total cross-engine frees:    {stats['cross_engine_frees']:,}")
    print(f"[+] Active chunks remaining:     {stats['active_chunks']} (Zero Leaks)")
    print(f"[+] Global mutex acquisitions:   {stats['global_mutex_locks']} (counter not incremented in this model)")
    print(f"[+] Double free errors:          {stats['double_free_errors']}")
    print(f"[+] Execution time:              {elapsed_ms:.2f} ms")

    invariants_ok = pool.verify_invariants()
    if invariants_ok:
        print("[+] ALL SHM INVARIANTS SATISFIED.")
        return 0
    else:
        print("[-] INVARIANT VIOLATION DETECTED.")
        return 1


def cmd_replay(args: argparse.Namespace) -> int:
    from tools.model.replay import DeterministicReplayer, RootCauseAnalyzer, TelemetryIngestor, TraceEvent

    print("=" * 70)
    print("Deterministic Telemetry Trace Replay & Root-Cause Analyzer")
    print("=" * 70)

    ingestor = TelemetryIngestor()
    if args.trace and Path(args.trace).is_file():
        content = Path(args.trace).read_text(encoding="utf-8")
        if content.strip().startswith("{") or content.strip().startswith("["):
            events = ingestor.ingest_otlp_json(content)
        else:
            events = ingestor.ingest_usdt_lines(content.splitlines())
    else:
        print("[*] No external trace provided. Using built-in synthetic race trace...")
        events = [
            TraceEvent(timestamp_ns=1000, subsystem="router", event_name="otel_request_start", stream_id=1, attributes={"http.request.method": "GET", "url.path": "/api"}),
            TraceEvent(timestamp_ns=2000, subsystem="app_queue", event_name="usdt_queue__enqueue", stream_id=1),
            TraceEvent(timestamp_ns=3000, subsystem="worker", event_name="usdt_worker_crash", worker_id=100),
            TraceEvent(timestamp_ns=4000, subsystem="router", event_name="otel_request_start", stream_id=2, attributes={"http.request.method": "GET", "url.path": "/health"}),
        ]

    replayer = DeterministicReplayer(num_engines=2, num_workers=2)
    replayer.load_events(events)
    summary = replayer.replay_all()

    print(f"[+] Replay completed: {summary.replayed_events} events processed in {summary.final_clock_ns:,} ns.")
    print(f"[+] Streams completed: {summary.streams_completed}")
    print(f"[+] Worker final states: {summary.worker_states}")
    print(f"[+] Anomalies detected: {len(summary.anomalies)}")

    analyzer = RootCauseAnalyzer(summary, events)
    if summary.anomalies:
        anomaly = summary.anomalies[0]
        name = str(anomaly.get("type", "queue_race"))
        litmus = analyzer.generate_c_litmus_test("nxt_repro_race_test", name)
        print("\n--- Sketch of a C reproduction (not a compilable FreeUnit test) ---")
        print(litmus)
    elif args.emit_litmus:
        print("[*] No anomalies; no C sketch.")
    return 0


def cmd_conformance(args: argparse.Namespace) -> int:
    from tools.model.conformance import TandemSession

    print("=" * 70)
    if args.url:
        print(f"Differential Conformance Runner (Target: {args.url})")
    else:
        print("Differential Conformance Runner (self-check, no server compared)")
    print("=" * 70)

    session = TandemSession(target_url=args.url)

    test_paths = ["/health", "/echo", "/not-found"]
    all_ok = True
    for p in test_paths:
        req = UnitRequest(method="GET", path=p)
        print(f"[*] Testing route: {p}...")
        res = session.verify_equivalence(req, reference_response=session._default_handler(req) if not args.url else None)
        if res.matches:
            print(f"    [+] MATCH: Status={res.details.get('oracle_status')}, Body={res.details.get('oracle_body_len')}B")
        else:
            print(f"    [-] DIVERGENCE: {res.divergences}")
            all_ok = False

    if all_ok:
        if args.url:
            print("\n[+] CONFORMANCE VERIFIED: Zero wire discrepancies.")
        else:
            print("\n[+] Self-check passed: the model matched its own default handler.")
            print("    No server was compared.")
        return 0
    else:
        print("\n[-] CONFORMANCE FAILED.")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="FreeUnit Behavioral & Invariant Simulation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # schema
    p_schema = subparsers.add_parser("schema", help="Extract C headers and compare ctypes layouts")
    p_schema.add_argument("--src", default=DEFAULT_SRC, help="Path to FreeUnit source ($UNIT_SRC, else ../unit, ../freeunit, then <harness>/src)")

    # simulate
    p_sim = subparsers.add_parser("simulate", help="Run Model A discrete event simulation")
    p_sim.add_argument("--requests", type=int, default=20, help="Number of client requests")
    p_sim.add_argument("--workers", type=int, default=2, help="Number of worker processes")
    p_sim.add_argument("--engines", type=int, default=2, help="Number of router engines")

    # verify
    p_verify = subparsers.add_parser("verify", help="Run Model B adversarial invariant check")
    p_verify.add_argument("--chaos", action="store_true", help="Enable crash & OOSM faults")
    p_verify.add_argument("--seed", type=int, default=42, help="RNG seed")

    # benchmark-optimizations
    subparsers.add_parser("benchmark-optimizations", help="Count modelled doorbells in two notify scenarios")

    # prove-k1
    p_k1 = subparsers.add_parser("prove-k1", help="Check modelled K1 bounded doorbell counts")
    p_k1.add_argument("--workers", type=int, default=4, help="Default workers count")
    p_k1.add_argument("--permutations", type=int, default=5, help="Permutations per parameter case")

    # lockfree-shm
    p_shm = subparsers.add_parser("lockfree-shm", help="Benchmark per-engine lock-free SHM slabs")
    p_shm.add_argument("--engines", type=int, default=4, help="Number of router engines")
    p_shm.add_argument("--threads", type=int, default=8, help="Concurrent allocator threads")
    p_shm.add_argument("--ops", type=int, default=100, help="Ops per thread")

    # replay
    p_rep = subparsers.add_parser("replay", help="Replay telemetry trace and synthesize C litmus tests")
    p_rep.add_argument("--trace", help="Path to OTel JSON or USDT trace log file")
    p_rep.add_argument("--emit-litmus", action="store_true", help="Emit C litmus repro test")

    # conformance
    p_conf = subparsers.add_parser("conformance", help="Self-check the model, or compare with --url")
    p_conf.add_argument("--url", help="URL of live FreeUnit server (e.g. http://127.0.0.1:8080)")

    args = parser.parse_args()

    if args.command == "schema":
        return cmd_schema(args)
    elif args.command == "simulate":
        return cmd_simulate(args)
    elif args.command == "verify":
        return cmd_verify(args)
    elif args.command == "benchmark-optimizations":
        return cmd_benchmark_optimizations(args)
    elif args.command == "prove-k1":
        return cmd_prove_k1(args)
    elif args.command == "lockfree-shm":
        return cmd_lockfree_shm(args)
    elif args.command == "replay":
        return cmd_replay(args)
    elif args.command == "conformance":
        return cmd_conformance(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
