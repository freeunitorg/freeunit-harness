# Router-thread scaling kit

`tools/perf-lab/scaling/` measures how throughput changes with the number of router threads
(`settings.listen_threads`) across several builds. It also counts mutex calls per request and profiles one point
with perf and perf c2c. It was written for the atomics train and padding experiments of October 2026
([freeunit#742](https://github.com/freeunitorg/freeunit/pull/742),
[freeunit#749](https://github.com/freeunitorg/freeunit/pull/749),
[freeunit#752](https://github.com/freeunitorg/freeunit/pull/752)) and ran on two lab boxes: x86 with 4 cores and SMT,
and aarch64 with 2 cores and no SMT.

Use `tools/perf-job.sh` ([perf-lab.md](perf-lab.md)) for an A/B of one change: it has a null gate and a verdict.
Use this kit when the question is about scaling over router threads, about several variants at once, or about
locks and shared cache lines.

## Run it

```sh
cp tools/perf-lab/lab.env.example run/lab.env     # once: PERF_HOST, REMOTE_WORKDIR, CLAIM_DIR, LAB_IMAGE, OHA
K=tools/perf-lab/scaling
$K/run.sh claim 240                               # a perflab-1 claim on the box, 240 minutes
$K/run.sh build base=origin/master head=my-branch # git archive of each ref of UNIT_REPO, built in LAB_IMAGE
VARIANTS="base head" NS="1 2 3" RUNS=5 DUR=15 PAIRS=head:base $K/run.sh sweep s1
$K/run.sh watch s1                                # returns when summary.txt is there
$K/run.sh fetch s1                                # to run/perf-lab/scaling/s1
python3 $K/freq.py run/perf-lab/scaling/s1        # did the clock move with the variant?
$K/run.sh shim && VARIANTS="base head" $K/run.sh locks l1   # mutex calls per request
N=2 V=head C2C_G=1 $K/run.sh profile p1           # perf record, futex count, c2c at n=2
$K/run.sh release
```

`NATIVE=1` builds and runs on the box without docker, as the box user. `run.sh` without a stage prints every stage
and knob. `run.sh stop TAG` ends a run before its next cell. `run.sh watch` does the same when a foreign claim
appears, and exits 3.

## Method

- **Pinning.** The kit reads the topology from `thread_siblings_list` (both forms: `0,4` and `0-1`). The server runs
  on the first thread of cores 0..n-1, and the SMT siblings of those cores stay idle. oha runs on both threads of
  the other cores. With n equal to the core count, oha runs on the siblings of the server cores and the row says
  `shared`. Without SMT, a `shared` row puts oha on the server cpus. The kit ignores `SERVER_CPUS` and `LOAD_CPUS`
  in `lab.env`. A sweep skips an n above the core count and says so in `run.log` and `meta.txt`. The profile
  default is N=3, or the core count when it is smaller (N=2 on the 2-core aarch64 box).
- **Interleaved rounds.** Round r runs every variant once, each with a fresh unitd, a 3 s warm-up and a `DUR`
  window. The variant order rotates by one per round. A drift of the box over the hours then spreads over all
  variants. analyze.py pairs the runs of one round.
- **Verdict.** analyze.py prints medians, the spread, a bootstrap ci95 of median(b)/median(a) and the per-round
  ratios. `noise` when the ci95 holds 1.0 or the ratios change sign; `few runs` below 5 runs per side.
- **A run without load is a failure.** When the oha result of a run cannot be read, sweep.sh writes `NA` in its row,
  never zeros. analyze.py leaves a run with `NA` or 0 rps out of the medians, marks the cell `NO LOAD k/n`, gives
  its ratios the verdict `NO LOAD`, and exits 1. sweep.sh then keeps the tables as `summary.txt.failed` and writes
  `FAILED`; it writes `summary.txt` and `DONE` only on success. `run.sh watch` waits for `DONE` and exits 5 on
  `FAILED`. A locks run does the same with `locks-summary.txt`.
- **Lock counts.** `LOCKS=1` preloads `mutexshim.so`. For each run the kit copies the shim files after the warm-up
  and after the measured window, each after 2.5 s idle, each copy twice 0.3 s apart. Calls per request are
  (second snapshot minus first) / oha requests of the window. The shim slows every lock call, so a locks run
  writes no throughput.
- **Own processes.** Every kill is limited to processes whose exe is under the kit's tree directory.

## Traps

- **One run per side is noise.** In an earlier lock measurement, the same one-run pair on a shared box gave +6%
  for one build, and then 1.8% the other way.
  Even 5 interleaved runs per cell leave a wide interval: on x86 a -5.2% ratio (capp n=2) had a ci95 of
  0.917..1.000, and the outlier variant of that cell (58842 rps against 55452..56372 for the other 7) did not
  reproduce in a rerun. Rerun a cell before you trust one variant that stands out.
- **The clock drifts.** The x86 lab box runs the `powersave` governor with EPP `balance_power`, and changing it
  needs root. meta.txt records both. Over 320 x86 runs the server cpus ran at a median of 2600 MHz, from 2375 to 3253 MHz. In one cell the
  variant medians spread from 2419 to 2820 MHz and Spearman(freq, rps) was +0.84. There, rps measures the clock,
  not the code. Run `freq.py` before you claim a delta.
- **Loadgen-bound cells measure oha.** On the 4-core box, n=3 leaves one core for oha. oha used 97-98% of it while
  the server used 74-84%. analyze.py marks a cell `LOADGEN-BOUND` when oha uses over 90% of its cpus. Such a cell
  cannot show a server-side difference.
- **Count locks from two snapshots.** A count read once, at the end of the run or at exit, holds the start-up
  and the warm-up. A copy taken while the shim rewrites its file is short; the kit takes the larger of two copies.
- **Never kill by name on a shared box.** `pkill -x unitd` in a native run would have killed 658 orphan unitd
  router and controller pairs of another session on one lab box.
- **The lab container has no tracefs.** `perf stat -e syscalls:sys_enter_futex` fails with "unknown tracepoint".
  profile.sh then counts futex calls with `strace -c -f` on the router. On x86 that worked for capp (11947 calls in
  10 s) and recorded nothing for static. Kernel symbols in perf and c2c stay unresolved under `kptr_restrict`.
- **`perf c2c report --stats` prints the summary only,** without the cache line tables. profile.sh writes both.
- **Native runs keep the control socket short.** A unix socket path holds at most 107 bytes, so a native run puts
  unitd's state in `/tmp/scaling-TAG`.
- **Root files in the results.** The lab container runs as root. The box user cannot remove root files from the
  results directory, so a sweep and a profile give every file to the owner of that directory when they exit.
- **An ssh drop must not end a run.** The container starts detached (`docker run -d`) and a native run starts under
  `setsid nohup`. The output goes to `results/TAG/run.log` on the box.

## Files

| file | does |
|---|---|
| `run.sh` | POSIX sh driver on your machine: claim, build, shim, sweep, locks, profile, watch, stop, fetch, release |
| `lib.sh` | topology, pinning, start and stop of unitd, oha (bash, sourced) |
| `sweep.sh` | the sweep; `LOCKS=1` for lock counts (bash, on the box) |
| `profile.sh` | perf record, futex count, perf c2c at one point (bash, on the box) |
| `build-tree.sh`, `capp.c` | the release build (-O2, frame pointers) and the libunit test application |
| `mutexshim.c`, `locks.py` | the lock counter and the snapshot diff |
| `analyze.py`, `freq.py` | medians, ratios and verdicts; clock per run |

Results on the box: `$REMOTE_WORKDIR/scaling/results/TAG/`: `sweep.tsv` (one row per run, with an epoch `ts`),
`meta.txt` (topology, governor, the parameters that this kind of run used, the sha of each tree; a profile run
writes one too), `freq.log`, `summary.txt`, `DONE` or `FAILED`,
and for a locks run
`locks.tsv`, `locks-summary.txt` and `sites-*.txt`. Tests: `tools/tests/test_perf_lab_scaling.py`.
