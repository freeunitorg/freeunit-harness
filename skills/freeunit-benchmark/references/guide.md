# FreeUnit Harness and Benchmark Guide

This guide explains how `freeunit-harness` is structured, how its benchmark
cells work, how to verify and run benchmarks on a host, and how to interpret
JSONL and TSV results using the harness's statistical analysis tools.

---

## 1. Overview

`freeunit-harness` is the companion operational, verification, and performance
benchmarking repository for [FreeUnit](https://github.com/freeunitorg/freeunit).

### Why `freeunit-harness` exists

Micro-optimizations and concurrency changes in an asynchronous HTTP router and
application server (`unitd`) often produce effects of 1% to 5% in CPU time or
instruction count per request, while naive load-generator runs on Linux hosts
routinely drift by 3% to 15% due to CPU frequency scaling, C-state wakeup
latencies, SMT sibling contention, and page-cache/allocator warm-up order.

`freeunit-harness` solves this by keeping operational and benchmarking tooling
out of the core C repository while enforcing strict measurement discipline:

- **Reproducible A/B and N×C experiments:** Counterbalanced cell ordering,
  crossover slot swapping, and mandatory A/A null gates prevent thermal drift
  or slot-position bias from masquerading as code improvements or regressions.
- **Kernel-level cost attribution:** Instead of relying solely on client-side
  requests-per-second (RPS), the harness snapshots kernel accounting interfaces
  (`/proc/<pid>/task/<tid>/stat`, `schedstat`, `status`, `sched`, `perf stat`,
  and isolated `strace -c` probes) to attribute CPU microseconds, instructions,
  context switches, and wakeups per request separately to the router and to
  application workers.
- **Public/private separation:** All scripts and documentation in the repository
  are host-agnostic and checked by `./tools/check-public.sh --all`. Machine
  addresses, CPU core maps, and local scratch paths live exclusively in
  gitignored `run/lab.env` and `run/` directories.
- **Connection to `freeunit` and analytical modeling:** Every performance pull
  request in `freeunitorg/freeunit` (such as descriptor scaling in
  [PR #810](https://github.com/freeunitorg/freeunit/pull/810), close-on-exec
  hygiene in [PR #809](https://github.com/freeunitorg/freeunit/pull/809), or
  `libunit` shared-port wakeup changes) is validated against the harness before
  claiming a win. Multi-worker `(N, C)` campaign outputs also feed empirical
  parameter fitting (`epx-fit.py`) for FreeUnit's queueing and contention cost
  models.

---

## 2. Architecture

### Benchmark cells and experimental designs

A **cell** is the atomic unit of measurement in the harness:

1. Spawn a fresh `unitd` instance inside its own process group (`setsid`),
   pinned to `SERVER_CORES` / `ROUTER_CPU`, and configure the target listener
   and application via the control socket.
2. Run an unmeasured **warm-up pass** so JIT caches, allocator arenas, and
   kernel socket buffers reach steady state.
3. Snapshot kernel counters for the router and worker PIDs/TIDs immediately
   before the timed window.
4. Drive load with `oha` (pinned to `LOAD_CORES` / `LOAD_CPUS`, using `-w` on
   duration runs so in-flight requests complete cleanly at the deadline).
5. Snapshot kernel counters immediately after `oha` exits, verify zero failed
   requests and zero `[alert]` lines in `unit.log`, and tear down the process
   group via PGID escalation (`SIGTERM`, poll, `SIGKILL`).

Depending on the question being asked, the harness organizes cells into four
experimental layouts:

| Tool / Workflow | Cell Layout | Primary Question |
|---|---|---|
| `tools/bench-engines.sh` | **5-scenario matrix × even rounds** (`A/B`, then `B/A` per round): `ret200_ka_hi` (`-c 256`), `ret200_ka_lo` (`-c 8`, herd detector), `ret200_close` (`-c 16`), `proxy_ka` (`-c 64`), `php_ka` | Does an event-engine or hot-path change alter throughput, tail latency, or router CPU across keep-alive, connection-close, proxy, and PHP workloads? |
| `tools/bench-release.sh` & `tools/perf-job.sh` | **8-cell counterbalanced blocks** (`X Y Y X Y X X Y` in normal blocks; `Y X X Y X Y Y X` in swapped/odd blocks) plus dedicated `strace` probe slots (`8..9`) | Does candidate build `B` change router or server CPU/instructions per request relative to baseline `A` beyond the A/A noise floor? |
| `tools/bench-startup.sh` | **4-phase cold-start + scaling sequence** (`daemon_boot`, `put_empty`, `put_8_apps`, `first_200`, `scale_1_to_8`) | Did a controller, discovery, or process-spawn change regress cold boot, configuration `PUT`, or `1 -> 8` worker scaling latency? |
| `http-bench.sh` / `epx-campaign.sh` | **`(N_workers × Concurrency)` grid** across interleaved branch orders (`A -> B` on odd reps, `B -> A` on even reps) | How do wakeup fan-out (`voluntary_cs / req`), worker load balance (`load_cv`), latency (`p50`/`p99`), and throughput scale as worker count `N` and concurrency `C` grow? |

#### Why 8-cell counterbalance + crossover (`--swap`) is used

Simple alternation (`A, B | B, A | A, B`) places identical builds back-to-back
across block boundaries, giving the second occurrence a warmer cache/arena
state. A 4-cell `ABBA` block (`X Y Y X`) places `X` on both outer slots `{0,3}`
and `Y` on both inner slots `{1,2}`, which confounds build identity with
non-linear per-slot warm-up curves.

The 8-cell block `X Y Y X Y X X Y` assigns each label one outer pair and one
inner pair, canceling linear drift. Pairing a normal block (`X=A, Y=B`) with a
swapped block (`X=B, Y=A`, via `--swap` in `bench-release.sh` or odd blocks in
`perf-job.sh`) cancels any residual non-linear slot-set bias.

### What is measured

Each benchmark collects client-side load metrics alongside kernel-level server
accounting:

- **Throughput and latency (`oha --output-format json`):**
  - `rps`: completed HTTP `200` requests per second.
  - `p50_ms`, `p90_ms`, `p99_ms`, `p999_ms`: response latency percentiles (in
    milliseconds or microseconds depending on the schema). In closed-loop mode
    (`oha -c C` without `-q`), throughput and mean latency are reciprocals via
    Little's Law (`RPS ≈ C / mean_latency`); for independent latency claims,
    use open-loop rate throttling (`RATE` / `oha -q`).
- **Router and server CPU time per request (`router_us`, `server_us`):**
  - `bench-release.sh` reads `/proc/<router_pid>/task/<tid>/stat` fields 14 and
    15 (`utime + stime` in `SC_CLK_TCK` ticks) strictly for threads whose
    `/proc/<router_pid>/task/<tid>/comm` is `unitd`. Threads named
    `OpenTelemetry.T` or `reqwest-interna` are asynchronous batch exporters and
    are excluded so timer-driven background flushes do not pollute request-path
    CPU cost.
  - `perf-job.sh` reads `/proc/<pid>/task/*/schedstat` field 1 (nanoseconds on
    CPU) across the router and application worker trees.
- **Hardware counters (`perf stat`):**
  - `r_instructions`, `s_instructions`, `r_cycles`, `s_cycles`: retired CPU
    instructions and cycles per request. Instruction counts are invariant to
    CPU frequency scaling (`powersave` vs `performance` governor) and exhibit
    the tightest null bands (~2.5%).
- **Wakeups, context switches, and CPU migrations:**
  - Read from `/proc/<pid>/task/*/status` (`voluntary_ctxt_switches`,
    `nonvoluntary_ctxt_switches`) and `/proc/<pid>/task/*/sched`
    (`se.statistics.nr_migrations`, `se.statistics.nr_wakeups_migrate`).
  - **Wakeup ratio (`voluntary_cs / requests`):** In multi-worker `libunit`
    benchmarks, a single-worker baseline uses ~1–2 voluntary context switches
    per request. Thundering-herd wakeups on shared ports inflate this ratio as
    `N_workers` grows; targeted wakeup fixes (`EPOLLEXCLUSIVE` or one-idle-ctx
    doorbells) drive `voluntary_cs / req` back toward ~1.0.
- **Syscall profiles (`rsys_*`, `asys_*`):**
  - Collected by `strace -c -f` in dedicated probe slots (`slot 8..9`) so
    `ptrace` overhead never contaminates timed CPU or latency cells.
- **C-states, CPU frequency governors, and SMT siblings (`methodology.md`):**
  - At low concurrency (`c=8`), idle cores enter hardware sleep states (`C1E`,
    `C6` with 10–100+ µs exit latency). A change that alters wakeup fan-out
    changes how deeply cores sleep between requests, shifting apparent CPU time
    and latency even when instruction count per request is unchanged.
  - Sharing physical cores (SMT hyperthreads) between `SERVER_CORES` and
    `LOAD_CORES` causes 5–15% execution jitter. Both `bench-release.sh` and
    `perf-job.sh` check `/sys/devices/system/cpu/cpu*/topology/thread_siblings_list`
    and CPU scaling governors before running.

---

## 3. How to run

### 1. Environment setup

Create your local, gitignored environment configuration from the template:

```sh
cp tools/perf-lab/lab.env.example run/lab.env
```

Edit `run/lab.env` to define your host roles (`PERF_HOST`, `BUILD_HOST`,
`ALPINE_HOST`), CPU core sets (`SERVER_CPUS`, `LOAD_CPUS`), and work
directories. Verify that no private hostnames or paths leak into tracked files:

```sh
./tools/check-public.sh --all
```

### 2. Preflight verification with `tools/doctor`

Before running any benchmark on a host, run the preflight doctor check:

```sh
./tools/doctor
# or:
make doctor
```

`./tools/doctor` checks four areas and exits non-zero only if required build
tools are missing:

- **Required tools:** `git`, `cc`, `make`, `python3`, `curl`, `sh`.
- **Optional tools:** `pytest` (with `pytest-Tempdir`), `ab`, `wrk`, `oha`.
- **Kernel & descriptor limits:** inspects the live host limits that silently
  throttle or invalidate high-concurrency FreeUnit benchmarks (see
  [`methodology.md`](methodology.md#host-kernel-and-sysctl-tuning)):
  - `RLIMIT_NOFILE` (`ulimit -n` soft >= `65536`)
  - `net.core.somaxconn` (>= `4096`) and `net.ipv4.tcp_max_syn_backlog` (>= `4096`)
  - `net.ipv4.ip_local_port_range` (width >= `28000` ports for `Connection: close` runs)
  - `net.core.rmem_max` and `net.core.wmem_max` (>= `16777216` bytes for shm/unix-socket IPC)
  - `kernel.perf_event_paranoid` (`<= 1` when collecting hardware `perf stat` counters)
- **Local config:** verifies whether `run/lab.env` is present.

To verify SMT sibling isolation and the `performance` CPU frequency governor
specifically for release A/B runs:

```sh
ROUTER_CPU=2 LOAD_CPUS=4-7 ./tools/bench-release.sh --preflight-only
```

### 3. Running the benchmarks

#### Event-engine and scenario matrix (`bench-engines.sh`)

```sh
SERVER_CORES=0-3 LOAD_CORES=4-7 \
  ./tools/bench-engines.sh baseline /path/to/build-a candidate /path/to/build-b 6

# Render a Markdown report from the raw output:
./tools/bench-engines-report.py ./bench-engines-results.txt
```

#### Release router-CPU 3-run crossover protocol (`bench-release.sh`)

Always execute all three steps (A/A null gate, normal A/B, and `--swap` A/B),
then combine the normal and swapped TSVs with `bench-crossover.py`:

```sh
# 1. Null gate (same binary under two labels; must settle near 0%):
ALLOW_SAME=1 ./tools/bench-release.sh \
  a1 /path/to/build-a a2 /path/to/build-a

# 2. Normal 8-cell run (6 measured blocks + rep 0 warm-up):
./tools/bench-release.sh \
  baseline /path/to/build-a candidate /path/to/build-b

# 3. Swapped 8-cell run (reverses slot assignment):
./tools/bench-release.sh --swap \
  baseline /path/to/build-a candidate /path/to/build-b

# 4. Crossover statistical summary and verdict:
./tools/bench-crossover.py \
  /tmp/.../release-baseline-vs-candidate-keepalive.tsv \
  /tmp/.../release-baseline-vs-candidate-swap-keepalive.tsv
```

#### Automated perf-lab jobs and regression bisection (`perf-job.sh`)

```sh
# Validate a job file without running:
PERF_JOB_CHECK=1 ./tools/perf-job.sh tools/perf-lab/jobs/01-null-hello.job

# Run a full job (acquires claim lock, checks idle gate, runs null + A/B phases):
./tools/perf-job.sh tools/perf-lab/jobs/01-null-hello.job

# Bisect a performance regression between two commits or tags:
./tools/perf-bisect.sh --dry-run 1.36.1 1.37.0
```

#### Cold startup and worker scaling (`bench-startup.sh`)

```sh
./tools/bench-startup.sh \
  baseline /path/to/build-a candidate /path/to/build-b 15
```

---

## 4. How to read results

### JSONL cell format (`bench-schema.json` / multi-worker campaigns)

Multi-worker `(N, C)` campaigns (`http-bench.sh` / `epx-campaign.sh`) emit one
JSON object per measured cell (`.jsonl`), recording client throughput/latency
alongside router and worker `/proc` deltas:

```json
{
  "schema": "freeunit-bench-v1",
  "run_id": "20261024T120000Z",
  "commit": "a4eb27fcb",
  "branch": "master",
  "scenario": "c_app",
  "n_workers": 4,
  "concurrency": 64,
  "rep": 1,
  "requests": 195400,
  "duration_s": 10.0,
  "rps": 19540.0,
  "latency_us": {
    "p50": 3120.0,
    "p90": 4180.0,
    "p99": 6450.0,
    "p999": 11200.0,
    "max": 18900.0
  },
  "router_cpu": {
    "user_ms": 2140.0,
    "sys_ms": 3890.0,
    "cpu_us_per_req": 30.86,
    "voluntary_cs": 198200,
    "nonvoluntary_cs": 4120,
    "migrations": 0
  },
  "workers_cpu": {
    "total_user_ms": 1820.0,
    "total_sys_ms": 2960.0,
    "total_cpu_us_per_req": 24.46,
    "voluntary_cs_total": 196100,
    "voluntary_cs_per_req": 1.004,
    "wakeup_ratio": 1.004,
    "load_cv": 0.031
  },
  "errors": 0
}
```

Key fields to inspect in each JSONL record:

- `errors`: must be `0`. Non-zero errors (such as socket resets or descriptor
  exhaustion before [PR #810](https://github.com/freeunitorg/freeunit/pull/810))
  invalidate the cell.
- `workers_cpu.wakeup_ratio` (`voluntary_cs_per_req`): measures context switches
  per completed request across application workers. Values near `1.0` indicate
  one worker woken per request; values scaling linearly with `n_workers`
  indicate a thundering-herd wakeup regression.
- `workers_cpu.load_cv`: coefficient of variation (`stdev / mean`) of CPU time
  across the `N` workers. Low `load_cv` (`< 0.15`) shows balanced traffic
  distribution; high `load_cv` shows worker starvation or lock convoying.

### Reading `epx-fit.py` / `epx-analyze.py` output

The campaign analysis scripts (`epx-fit.py` / `epx-analyze.py`) aggregate JSONL
cell records across repetitions for each `(scenario, n_workers, concurrency)`
cell and compute four statistical layers:

1. **Medians and IQR (outlier-resistant cell summaries):**
   - For every `(branch, scenario, N, C)` cell across repetitions `rep = 1..R`,
     the analyzer reports the **median** and **interquartile range (IQR = Q75 - Q25)**
     alongside mean and standard deviation for `rps`, `p50`, `p99`,
     `router_cpu.cpu_us_per_req`, `workers_cpu.total_cpu_us_per_req`, and
     `voluntary_cs_per_req`.
   - Why medians first: occasional OS housekeeping ticks (timer interrupts, RCU
     callbacks) skew single-rep means on tail latency (`p99`), whereas the
     cell median and IQR remain stable.
2. **Paired deltas and 95% bootstrap confidence intervals:**
   - Because repetitions are run in interleaved pairs (`rep r` measures both `A`
     and `B` back-to-back in alternating order), the analyzer forms paired
     per-repetition relative deltas:
     `delta_r = (B_r - A_r) / A_r * 100%`.
   - It then resamples `{delta_1, ..., delta_R}` with replacement (`n_boot = 2000`,
     fixed PRNG seed for reproducibility) and takes the `2.5th` and `97.5th`
     percentiles of the bootstrap means as the **95% bootstrap CI**
     `[ci_low, ci_high]`.
   - A change in a cell is statistically significant only if the 95% bootstrap
     CI excludes `0` **and** exceeds the host's empirical noise floor.
3. **Noise floor and null band:**
   - An A/A run (same commit tested against itself across all `(N, C)` cells)
     defines the empirical **noise floor** (or `null_band = |null_delta| + CI_half_width`).
   - On pinned hardware with the `performance` governor, instruction-count and
     kernel CPU-per-request noise floors typically sit around `0.9%` to `2.5%`,
     while unthrottled closed-loop `rps` and `p99` have wider bands (`2%` to `5%`).
   - Any A/B delta whose magnitude falls inside the null band is indistinguishable
     from measurement noise regardless of its nominal p-value.
4. **Regression detection and model fitting:**
   - **Regression flag:** a cell is flagged as a regression when `ci_low > +noise_floor`
     on a lower-is-better metric (`cpu_us_per_req`, `p50`, `p99`,
     `voluntary_cs_per_req`) or `ci_high < -noise_floor` on `rps`.
   - **Parameter fitting (`epx-fit.py`):** fits per-wakeup kernel scheduling cost
     and contention coefficients across the `N_workers ∈ {1, 2, 4, 8}` and
     `concurrency` grid against the queueing model so wakeup-reduction patches
     can be checked against predicted scalability curves.

### Reading `perf-compare.py` and `bench-crossover.py` verdicts

For 8-cell block runs (`tools/perf-job.sh` -> `tools/perf-compare.py`, and
`tools/bench-release.sh` -> `tools/bench-crossover.py`), the tools compute all
statistics automatically using Welch/Student-t 95% confidence intervals across
normal and swapped blocks and print one of five verdicts:

| Verdict | Meaning |
|---|---|
| `CLAIM` | `|delta|` exceeds the null band, the 95% CI excludes `0`, and at least 3 of 4 adjacent slot pairs agree in sign (`perf-compare.py`), or the crossover 95% CI excludes `0` and exceeds the slot-set bias floor (`bench-crossover.py`). |
| `NO CLAIM` / `NO DIFFERENCE DETECTED` | The 95% CI crosses `0` or `|delta|` lies within the null band / slot-set bias floor. Report as no measurable difference. |
| `NOISY` | The A/A null gate band exceeded `NULL_LIMIT_PCT` (default `5.0%`), or normal vs `--swap` runs disagreed beyond the host's resolution floor. Discard the run and fix host isolation before re-measuring. |
| `INVALID` | A guard failed: at least one cell recorded failed HTTP requests (`bad > 0`), `[alert]` lines in `unit.log` (`alerts > 0`), or zero completed requests (`ok == 0`). |

---

## 5. References

- **[Benchmarking Methodology (`skills/freeunit-benchmark/references/methodology.md`)](methodology.md)** —
  Core rules on load-generator artifacts, C-state and CPU frequency effects,
  statistical significance, and **[Host kernel and `sysctl` tuning](methodology.md#host-kernel-and-sysctl-tuning)**
  (`RLIMIT_NOFILE`, `net.core.somaxconn`, `net.ipv4.tcp_max_syn_backlog`,
  `net.ipv4.ip_local_port_range`, `net.core.rmem_max` / `net.core.wmem_max`,
  and `kernel.perf_event_paranoid`).
- **[Release A/B Benchmark (`skills/freeunit-benchmark/references/release-ab.md`)](release-ab.md)** —
  Detailed walkthrough of `tools/bench-release.sh`, 8-cell counterbalanced
  blocks (`X Y Y X Y X X Y`), `--swap` crossover pairing, and `/proc` thread
  filtering.
- **[Startup and Scaling Benchmark (`skills/freeunit-benchmark/references/startup.md`)](startup.md)** —
  4-phase cold-start (`daemon_boot`, `put_empty`, `put_8_apps`, `first_200`)
  and `1 -> 8` worker scaling benchmark protocol.
- **`docs/perf/perf-lab.md` — FreeUnit Perf Lab** —
  Automated job files (`tools/perf-job.sh`), box claim locking, CPU idle gates,
  `perf-compare.py` verdict rules, and regression bisection (`tools/perf-bisect.sh`).
- **[FreeUnit PR #810 — `core: raise the soft RLIMIT_NOFILE in the router and controller`](https://github.com/freeunitorg/freeunit/pull/810)** —
  Raises `rlim_cur` to `rlim_max` in the router and controller processes at
  startup so high-concurrency benchmarks and multi-worker deployments do not
  fail with `EMFILE (24: Too many open files)` when started from shells with a
  default soft limit of `1024`.
- **[FreeUnit PR #809 — `core: create and receive descriptors close-on-exec`](https://github.com/freeunitorg/freeunit/pull/809)** —
  Marks internal event-engine descriptors (`epoll_create1(EPOLL_CLOEXEC)`,
  `kqueue` `FD_CLOEXEC`, `signalfd4`, `eventfd2`, and `MSG_CMSG_CLOEXEC`)
  close-on-exec at creation and receipt so descriptors do not leak across
  `fork()` and `execve()` into application workers.
