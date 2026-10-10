# OTel compiled in but not configured: the per-request overhead (2026-10-08)

Code: freeunitorg/freeunit `origin/master` 856d23b9. Every `file:NNN` is a line at that commit.
Branch: `perf/otel-is-init-atomic` in a worktree of a peer session. Machine-code evidence is from the build host
(x86-64, gcc, cargo 1.98.0 release profile). Nothing was benchmarked for this page. The perf lab job is written
but was not run (section 8).

Question: what does a `--otel` build pay per request when `settings/telemetry` is absent, and is the reviewer's
analysis in otel.txt right. Companion: [[otel-self-dos-analysis]] (memory), `docs/perf/perf-lab.md`.

## 1. Result in one paragraph

The reviewer is right on the mechanism and the fix, and overstates the contention. `nxt_http_request_create()`
calls `nxt_otel_rs_is_init()` once per request (src/nxt_http_request.c:287, not :284). That function locked the
provider mutex to read `Option::is_some()`. On x86-64 it ran 30 instructions on its hot path, with one
`lock cmpxchg` and one `xchg`, on a cache line that every worker thread writes. The mutex is held by the router
thread only for one `Option` store or take, so a request could block for the time of one pointer move per
reconfigure, not for the whole reconfigure. The fix is a static `AtomicBool`: the function is now 4
instructions with no read-modify-write. The saving is about 26 instructions and two locked operations per
request out of about 140,000, which the perf lab cannot resolve (null band 2.5 to 3 percent). The change is
justified by the machine code and by removing a lock from the request path. Every other OTel hook on the
request path returns on `r->otel == NULL`, and `r->otel` is only allocated when the check passes, so nothing
else is worth changing.

## 2. The reviewer's claims, verified

| # | Claim (otel.txt) | Verified | Evidence | Cost per request | Action |
|---|---|---|---|---|---|
| 1 | `nxt_http_request.c:284` calls `nxt_otel_rs_is_init()` on every request | Yes, at :287 | `#if (NXT_HAVE_OTEL) if (nxt_otel_rs_is_init()) { r->otel = nxt_mp_zget(...) }` in `nxt_http_request_create()`, run on the worker engine threads for every request, configured or not | C side: `call`, `test %al,%al`, `jne` (3 instructions, identical at -O and -O2) plus the callee | keep the call, change the callee |
| 2 | `is_init` is `provider_slot().lock().map(is_some).unwrap_or(0)` at lib.rs:277 | Yes, at lib.rs:287-292 (`Mutex` import :37, `provider_slot()` :123) | quoted | 78 instructions in the function, 30 on the hot path: `lock cmpxchg` (lock), `xchg` (unlock), two loads of `GLOBAL_PANIC_COUNT` through the GOT (poison check), no TLS | replaced by `IS_INIT.load(Acquire)`: `movzbl`, `test`, `setne`, `ret` |
| 3 | FFI transition plus an uncontended `std::sync::Mutex` lock and unlock | Yes | direct call into the static `libotel.a`; the `R_X86_64_PLT32` relocation resolves at link time, no PLT in `unitd` | as row 2 | as row 2 |
| 4 | request creation can block while `nxt_otel_rs_init()` holds the mutex on reconfigure | Partly | init holds the mutex only for `*slot = Some(provider)` (lib.rs:420-422); `nxt_otel_rs_shutdown_tracer()` :751 and `nxt_otel_rs_shutdown_bounded()` :687 only for `g.take()`; the exporter build and `provider.shutdown()` run outside the lock | a worker that hits the window pays a futex wait and wake (two syscalls) once, for the time of one `Option` move; 60 reconfigures/s at 100 rps showed no latency effect ([[otel-self-dos-analysis]]) | the fix removes the window anyway; it is not the main cost |
| 5 | the rest is cheap: hooks return on `r->otel == NULL`; `r->otel` is allocated only when `is_init()` | Yes | section 3 | three out-of-line NULL checks, about 20 instructions | not worth changing |
| 6 | fix: `static IS_INIT: AtomicBool` with `Relaxed`, set in init and shutdown; or cache the flag on the C side | Adopted with `Acquire`/`Release`; C-side cache rejected | section 4 and 5 | 4 instructions, 0 read-modify-write | done |
| 7 | "format it in the style of #588" | #588 = one change per commit, a PR body that says CHANGES is untouched, a validation block with exact commands | https://github.com/freeunitorg/freeunit/pull/588 | | followed |

## 3. Everything else a `--otel` build runs per request when unconfigured

| Site | Guard | When it runs | Cost | Verdict |
|---|---|---|---|---|
| `NXT_OTEL_TRACE()` at src/nxt_http_request.c:323 (start), :596 (ready), src/nxt_h1proto.c:1586 (header send) | `nxt_otel_test_and_call_state()` src/nxt_otel.c:582-586 returns on `r == NULL || r->otel == NULL` | every request, 3 times | out-of-line call, two compares, return: about 7 instructions each, about 20 per request (0.015 percent) | not worth changing; an inline guard in the macro would expose `r->otel` in the header for nothing measurable |
| `NXT_OTEL_TRACE()` at src/nxt_h1proto.c:2036 | guarded by `r->otel != NULL` at :2035 | body path | 1 compare | nothing to do |
| `nxt_otel_request_error_path()` src/nxt_http_error.c:69 | src/nxt_otel.c:620-624 returns on `r->otel == NULL` | error responses only | 1 call | nothing to do |
| `Traceparent` and `Tracestate` handlers in the generic field hash, src/nxt_h1proto.c:198-199 | src/nxt_otel.c:703-706 and :804-807 return `NXT_OK` on `r->otel == NULL` | only when the client sends the header; the hash lookup runs for every header anyway | 1 call per such header | nothing to do |
| `r->otel` allocation, src/nxt_http_request.c:288 | inside `if (nxt_otel_rs_is_init())` | only when configured | 0 when unconfigured | nothing to do |
| other `nxt_otel_rs_*` calls | all in src/nxt_otel.c under `r->otel != NULL` paths, plus src/nxt_router.c:3627-3639 (reconfigure) and src/nxt_otel.c:849 (exit) | never per request when unconfigured | 0 | nothing to do |
| `/status`: src/nxt_router.c:2175 `nxt_otel_rs_export_stats()` calls `is_init()` | | per `/status` request on the router thread | now a flag load | nothing to do |
| thread-local access | none on the C side for OTel; the old Rust path read `GLOBAL_PANIC_COUNT` through the GOT, not TLS | | | nothing to do |
| libunit and the modules | `grep -rli otel src --include=*.c --include=*.h` names only router, http, otel, status, runtime and conf_validation files; src/nxt_unit.c and every module have no OTel reference | never | 0 | nothing to do |
| process memory | `libotel.a` is 47,007,240 bytes and adds about 6.5 MB RSS once per router ([[otel-self-dos-analysis]]) | per process, not per request | | out of scope |

## 4. Design

One static `AtomicBool` `IS_INIT` in src/otel/src/lib.rs, next to `EXPORT_FAILED`. The provider slot stays the
owner; the flag mirrors it.

- `nxt_otel_rs_is_init()` returns `IS_INIT.load(Ordering::Acquire) as u8`. The function, its name, signature and
  `#[no_mangle]` are kept, so the C side and the 12-symbol FFI surface are unchanged.
- `nxt_otel_rs_init()` stores `true` with `Release` right after `*slot = Some(provider)`, inside the same
  `if let Ok(mut slot)` block, so the flag is set only when the store happened.
- `nxt_otel_rs_shutdown_tracer()` and `nxt_otel_rs_shutdown_bounded()` store `false` with `Release` as their
  first statement, before the tracer slot is dropped, so new requests stop allocating `r->otel` first.

Rejected: caching the flag on the C side (the reviewer's second option). The C side has the same writer and
reader threads, so it would need its own atomic with the same ordering question, and a second source of truth.
The saving over the Rust flag is the `call` and `ret` pair, about 4 instructions. Also rejected: exporting the
`AtomicBool` as a C symbol and loading it from C; that ties the C code to the Rust atomic's layout for the same
4 instructions.

Also rejected: an inline `r->otel != NULL` guard in `NXT_OTEL_TRACE()` (section 3). About 20 instructions per
request, far under the noise floor, for a wider header.

## 5. Race and memory-ordering analysis

Writers of the flag: the router thread only. `nxt_otel_rs_init()` and `nxt_otel_rs_uninit()` are called from
`nxt_router_conf_create()` (src/nxt_router.c:3627-3639); `nxt_otel_rs_shutdown_bounded()` from
`nxt_otel_shutdown()` at exit (src/nxt_otel.c:849-862, from src/nxt_runtime.c:690). Readers: every worker engine
thread in `nxt_http_request_create()`, and the router thread in the reconfigure, `/status` and exit paths.

1. **Init after no provider, or after a shutdown (reconfigure).** Order in `nxt_otel_rs_init()`: shutdown the old
   provider (flag already false), build the exporter, `global::set_tracer_provider`, write `tracer_slot()`
   (RwLock, released), store `provider_slot()`, then `IS_INIT.store(true, Release)`. A worker that loads `true`
   with `Acquire` sees everything sequenced before the store: the tracer and the global provider. Before the
   change the mutex gave the same guarantee (the mutex acquire in `is_init()` synchronised with the release in
   init). Requests created during the window see `false` and are not traced, as before, when the slot was
   `None` between `take()` and the store.
2. **Why not `Relaxed`.** Memory-safe: every SDK object is behind its own lock or `Arc`, and nothing is
   dereferenced on the strength of the flag. But a `Relaxed` load has no happens-before with the tracer write, so
   a worker could see `true` and then take the `tracer_slot()` read lock before the router's write in the
   lock's order, find `None`, and fall back to `global::tracer()`, which can still be the previous, shut-down
   provider. The span is then created and dropped silently. `Acquire`/`Release` closes that at no cost on
   x86-64: the load is `movzbl` (objdump, section 7) and the store a plain `mov`. On aarch64 they are `ldar`
   and `stlr`; on s390x plain loads and stores (not verified on hardware; the build host has no aarch64 Rust target).
3. **Shutdown while a request is between the check and span creation.** The flag is cleared first, then the
   tracer slot, then the provider is taken and shut down outside the lock. A request that read `true` just
   before the clear has or will allocate `r->otel`; when it reaches `nxt_otel_rs_get_or_create_trace()` it
   finds `tracer_slot()` empty and falls back to the global tracer, whose provider is shut down, so the span is
   dropped. This is the pre-change behaviour: the mutex was never held across the check and the later tracer
   read, so the same window existed. The flag only moves the "not traced" cut one statement earlier.
4. **A request holding a span when the provider is shut down.** A `BoxedSpan` owns an `Arc` to the tracer's
   provider internals; ending it after `provider.shutdown()` is a silent drop, documented at the head of
   `nxt_otel_rs_shutdown_bounded()`. Unchanged.
5. **Flag and slot disagreement.** Between `*slot = Some(provider)` and the flag store (both under the mutex,
   same block), and between the flag clear and `g.take()` in a shutdown (flag `false`, slot `Some`, while the
   tracer is dropped). Nobody reads the slot except the three router-thread functions, so the only observable
   difference is `is_init()` answering 0 slightly before the slot empties. At exit `nxt_otel_shutdown()`
   checks `is_init()` on the same thread that clears it, sequentially.
6. **Failure paths.** A bad protocol returns before `nxt_otel_rs_shutdown_tracer()`: flag and slot both stay
   set (tested). An exporter build error returns after it: both cleared. A poisoned mutex (a panic while holding
   it, impossible in practice because the critical section is one `Option` move) left the old `is_init()` at 0;
   the new one reports the flag, which the failed store left unchanged.
7. **Requests in flight at process exit.** `nxt_otel_rs_shutdown_bounded()` now clears the flag before the
   flush, so a request created after that point gets no `r->otel` instead of a span on a dead provider. The
   worker engines are not joined at exit (issue 219), so spans ended after the flush are lost either way; this
   is the pre-existing limitation, unchanged.

## 6. Tests and the mutation proof

Four tests in a `#[cfg(test)] mod tests` at the end of src/otel/src/lib.rs. They serialise on a `static SERIAL`
mutex because the slots are process globals and cargo runs tests in parallel threads. Each test starts from
`nxt_otel_rs_shutdown_tracer()`. Init uses protocol `http` and `http://127.0.0.1:1/v1/traces`; the http
exporter builds its blocking client on a spawned thread and does not connect (opentelemetry-otlp 0.33.0
src/exporter/http/mod.rs:245), and no span is ever ended, so nothing is exported.

| Test | Pins | On origin/master |
|---|---|---|
| `is_init_does_not_wait_for_the_provider_lock` | a helper thread holds the provider lock 1 s; `is_init()` must answer within 500 ms (half the hold, after the review; the first draft said 200 ms) | fails: `is_init() took 1.000075221s while the provider lock was held` |
| `init_and_shutdown_flip_is_init` | 0 before init, 1 after (slot and tracer installed), 1 within 500 ms while the lock is held, 0 after `shutdown_tracer` (slot and tracer gone) | fails: `is_init() took 1.000076289s while the provider lock was held` |
| `reconfigure_keeps_is_init_set` | init A, init B (shuts A down first), 1 and slot `Some`; `shutdown_bounded(2000)` gives 0 and `None` | passes (behaviour unchanged; pins the flag against the slot) |
| `bad_protocol_keeps_the_live_provider` | init with `bogus` after a good init: log callback fired, flag 1, slot `Some` | passes (same) |

Runs on the build host, `CARGO_TARGET_DIR=$PWD/target cargo test --offline` in copies of the old and new crate:
- old (origin/master lib.rs plus the final test module): `test result: FAILED. 2 passed; 2 failed` in serial
  and parallel mode (the values above are from the serial run; the parallel run gave 1.000079738 s and
  1.000101789 s with the first draft of the module).
- new: `test result: ok. 4 passed; 0 failed` in 7 runs of the first draft and 2 runs (parallel and serial) of
  the committed module, 0 warnings. `rustfmt --check --config-path .rustfmt.toml` (max_width 80) reports the
  same 11 pre-existing hunks on origin/master and on the new file, none in the added lines.
- extra mutations of the new code: removing the store in init fails 3 tests (`left: 0, right: 1`); removing the
  clear in `shutdown_bounded` fails `reconfigure_keeps_is_init_set` (`left: 1, right: 0`); removing the clear in
  `shutdown_tracer` fails 3 tests. Every store is covered.
- `nxt_otel_rs_shutdown_bounded(2000)` returned 1 (FLUSHED) in 3 of 3 probe runs: an empty batch returns `Ok`
  without calling the exporter (opentelemetry_sdk 0.33.0 src/trace/span_processor.rs:779). The test does not
  assert it.

CI does not run the crate's tests today. https://github.com/freeunitorg/freeunit/pull/615 adds
`cargo test --release --locked --manifest-path src/otel/Cargo.toml` to build-test.yml; this branch does not
duplicate that hunk. Until one of the two merges, the tests run by hand.

`test/test_otel.py` (29 cases, needs `fake_otlp`) was not run: the build host has no `/usr/local/bin/fake_otlp` and no
pytest outside a container; the only harness path is `unit-build -t alpine pytest` on the Docker host with the whole
suite under `-x`. Two of its cases go through the changed function (`test_otel_status_absent_without_telemetry`
via `nxt_otel_rs_export_stats`, and every test boundary that configures telemetry on and off).

## 7. Build and machine-code evidence

`tools/unit-build -t remote -H build -s <worktree> --otel --hardening --debug tests`, then `verify`
(`./configure --openssl --otel --debug --hardening=strict --tests`):

```
-rwxrwxr-x 1 builder builder 21674352 2026-10-08_14:25:17 build/tests
./build/tests exit: 0
hook gate exit 0: ok: hooks are in the test programs only
warning: lines in $BUILD_CACHE/logs/unitbuild-wt.log: 1
unit-build: tests PASSED
libotel.a 47007250 bytes, 12 nxt_otel_rs symbols in unitd
```

That is the build of the committed tree (ab5d0ed0; `unitd` mtime 14:24:59, the synced lib.rs has the 7
`IS_INIT` references). The first draft built the same way at 14:13:34 with `libotel.a` 47007240 bytes; the
review changed comments, one line break and a test constant, and `is_init()` is the same 4 instructions in both.

The one warning line is cargo's `failed to connect to jobserver from environment variable MAKEFLAGS=" -j5
--jobserver-auth=3,4"`, build-system noise that is not from the code.

`nxt_otel_rs_is_init` in the built `unitd` (cargo release profile; the C `-O` level does not reach the crate):

```
00000000001c9180 <nxt_otel_rs_is_init>:
  movzbl 0x6907c9(%rip),%eax        # 859950 <_RNvCs10NfGWZPPw3_4otel7IS_INIT.0>
  test   %al,%al
  setne  %al
  ret
```

Before (origin/master `libotel.a`, release): 78 instructions, hot path 30: `push` x3, `lock cmpxchg` on the
mutex word, a load of `GLOBAL_PANIC_COUNT` and of the poison flag, the `Option` load, a second
`GLOBAL_PANIC_COUNT` load, `xchg` on the mutex word (unlock), `cmp $2` (waiters), `setne`, `pop` x3, `ret`. Cold
paths call `lock_contended`, futex wake, `is_zero_slow_path` and `panic_cannot_unwind`. Two locked
read-modify-write operations per call, each a full barrier on x86-64, on one cache line that every worker
thread writes. No TLS access.

The C call site in `nxt_http_request_create()` is unchanged and identical at the project's `-O` (in `unitd`)
and at `-O2` (same `CFLAGS` with `-O2`, `objdump -dr` of the object): `call nxt_otel_rs_is_init`,
`test %al,%al`, `jne`.

Estimate, not measured: an uncontended locked operation on x86-64 costs about 20 cycles when the line is local;
a worker whose line was last written by another worker also pays a cache-line transfer. Instruction counts
cannot show that, and cycles are noisier in the lab.

## 8. The perf lab job

`tools/perf-lab/jobs/06-otel-is-init.job` with `tools/perf-lab/jobs/patches/otel-is-init-atomic.diff` (the
branch's diff, applies to origin/master with `patch -p1 --dry-run`). `CONFIGURE_ARGS=--otel`, workload hello
(its config has no telemetry, so this is the unconfigured path), `PERF=1 STRACE=1`, `PRIMARY=r_instructions`
(the lab reports `s_instructions` as well; `PRIMARY` only picks the headline), `FUNCS=unitd:nxt_otel_rs_is_init
unitd:nxt_http_request_create` for the function sizes.

```
$ PERF_JOB_CHECK=1 tools/perf-job.sh tools/perf-lab/jobs/06-otel-is-init.job
CHECK 06-otel-is-init: ok, phases 2, cells '6', 4 blocks, about 15 min of cells
```

EXPECT: NO CLAIM on instructions (the delta is inside the null band), `nxt_otel_rs_is_init` shrinks from 0x120
to 0xd bytes, every `rsys_` syscall count equal in A and B. FALSIFY: instructions higher in B than the null band,
any `rsys_` count that differs, or the function not smaller in B.

Blocker before it can run: `--otel` makes auto/otel exit 1 without rustc and cargo, and the lab image
`freeunit-perf-lab:1` has neither (`docker run --rm --user $(id -u):$(id -g) freeunit-perf-lab:1 sh -c 'cargo
--version'`: `cargo: not found`, 2026-10-08). `PERF_JOB_CHECK` does not catch this; the run would end with
"build A failed". The image needs a Rust toolchain and an offline cargo registry, or the crate's dependencies
fetched at build time.

## 9. Overlap with PR 615

https://github.com/freeunitorg/freeunit/pull/615 (head bd22e8d9) edits `nxt_otel_rs_copy_traceparent()`,
which follows `nxt_otel_rs_init()`, and appends its own `#[cfg(test)] mod tests` at the end of lib.rs.
`git merge-tree --write-tree ab5d0ed0 bd22e8d9` exits 1 with one conflict, in src/otel/src/lib.rs.
`git merge-file` of origin/master, this branch's file without its test module and 615's file: 0 conflicts, and
the merged file holds both changes. So the only conflict is the end of file, where both append a module. A
second `mod tests` is E0428, so whichever merges second folds both bodies into one `mod tests` (both start with
`use super::*;`). 615's three tests must then take this branch's `serial()` lock too: they assume no provider is
installed, and `init_and_shutdown_flip_is_init` installs a live one (sampling 1.0) on a parallel thread, so
615's `static_attribute_values_cost_no_allocation` would get a recording span and fail now and then (review
finding, section 10). The hardening wave PRs 445 to 448 touch no file of this branch.

## 10. Review

One Fable pass on the diff (it got the diff and the review targets, not the conclusions above).

| Finding | Severity | Applied |
|---|---|---|
| Ordering: Acquire/Release needed and sufficient for "sees 1 implies the tracer is installed"; with Relaxed the flag store may become visible before the tracer write on aarch64 (not on x86-64 or s390x). The Release on the two clears publishes nothing; Relaxed would be equivalent, Release is harmless. Cases (b) to (d) rest on the RwLock and the mutex, not on the flag; unchanged. | holds | kept Release on the clears for uniformity |
| Every change of the slot is paired with a flag update; "flag true, slot None" cannot occur; no other writer of `provider_slot()` in src/. | holds | |
| Poison: the guarded statements cannot panic; a panic in an `extern "C"` fn aborts since Rust 1.81; on poison the new code reports 0 like the old `unwrap_or(0)`. | holds | |
| Mutation is deterministic, not timing luck: the holder owns the mutex before the clock starts; a `try_lock()` mutation passes T1 and fails T2. | holds | |
| PR 615's test module takes no serial lock and assumes no provider; the merged module needs its tests serialised. | should fix at merge | documented in section 9 and in the PR body |
| `LIMIT` 200 ms: raise to half of `HOLD` for a 2x margin on a loaded CI box. | nit | applied: 500 ms |
| rustfmt at the repo's `max_width = 80`: one new line at 83 columns (the implementer had checked against the default 100). | should fix | applied: line split; verified with `--config-path .rustfmt.toml` |
| Wording: "A request also blocks while init or shutdown holds the mutex" overstates (one assignment or one `take()`); "before the tracer goes" and "Start the clock" are idioms; the "cleared first" comments describe intent, not a formal guarantee (a Release store is not ordered before a later lock RMW by the model; it is on x86-64 and on ARMv8 in practice, and the fallback covers a late reader). | should fix | applied: "waits for the few instructions during which init or shutdown holds the mutex"; "before the tracer is dropped"; "Measure only once the lock is held"; the comments now name the fallback |
| `provider_slot()` doc could say that `IS_INIT` mirrors it. | nit | applied |
| The http exporter never connects at build time (opentelemetry-otlp 0.33.0 http/mod.rs:238-262); an empty batch returns Ok without export (opentelemetry_sdk 0.33.0 span_processor.rs:779-781). | holds | |
| No CHANGES entry needed. | holds | |

The findings changed the commit, so it carries `Reviewed-by: Claude Fable 5.1`.

## 11. Deliverables

- Commit ab5d0ed0eda736738815bb867d98b9db8f7e68f4 "otel: read an atomic flag in nxt_otel_rs_is_init()" on
  `perf/otel-is-init-atomic`, based on origin/master 856d23b9, in the worktree
  `wt` in that session's scratch directory.
  `src/otel/src/lib.rs | 209 insertions, 4 deletions`. Trailers: `Co-Authored-By: Claude Opus 5.5`,
  `Reviewed-by: Claude Fable 5.1`. No `Claude-Session` line. Not pushed.
- PR body: `pr-body.md` in the same scratch directory.
- Lab job: tools/perf-lab/jobs/06-otel-is-init.job and tools/perf-lab/jobs/patches/otel-is-init-atomic.diff
  (uncommitted, like the rest of tools/perf-lab/jobs/). The patch is `git show ab5d0ed0 -- src/otel/src/lib.rs`;
  `patch -p1` on origin/master reproduces the committed file byte for byte, and `PERF_JOB_CHECK=1` passes.
- CHANGES and docs/changes.xml: not touched. No user-visible behaviour changes; the precedent is PR 588.
