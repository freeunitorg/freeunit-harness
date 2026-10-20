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
  `FAILED`. A locks run does the same with `locks-summary.txt`. Each locks cell gets a row in `locks.tsv` even
  when the shim wrote no file: role `none` and calls `NA`. Its summary line says `NO LOAD` or `NO COUNTS`.
- **Lock counts.** `LOCKS=1` preloads `mutexshim.so`. For each run the kit copies the shim files after the warm-up
  and after the measured window, each after 2.5 s idle, each copy twice 0.3 s apart. Calls per request are
  (second snapshot minus first) / oha requests of the window. The shim slows every lock call, so a locks run
  writes no throughput. A site in `sites-*.txt` is the function that called `pthread_mutex_lock()`, or the caller
  of `nxt_thread_mutex_lock()` when the call went through that wrapper. libunit calls `pthread_mutex_lock()`
  directly, so an application site is the libunit function itself.
- **Per-process perf.** profile.sh attaches every perf call to the kit's processes with `-p` (unitd, router,
  controller, prototypes, applications; `pids.txt` lists them). The system-wide `-a -C <server cpus>` of the first
  kit is an opt-in, `PERF_SCOPE=cpus`: it needs `perf_event_paranoid` 0 or lower, or CAP_PERFMON in the initial
  user namespace. Per-process mode leaves out the time of other tasks on the server cpus, and kernel work done
  for the kit outside its own threads. At `perf_event_paranoid` 2 the kit records user space only:
  `perf record --all-user --user-callchains`, and `perf c2c record --all-user`, or on AMD
  `perf record -W -d --sample-cpu -e ibs_op/swfilt=1/u` (kernel 6.14 or later). These user space forms come from
  a laptop test and the kernel source; they are unverified on the lab boxes.
- **Preflight.** profile.sh first reads `perf_event_paranoid`, `kptr_restrict`, `ptrace_scope`, the PMU devices in
  `/sys/bus/event_source/devices` and its own capabilities, and writes `preflight.txt`: one line per step, `ok` or
  `unavailable` with the sysctl or capability that is missing. When a requested step (`PROFILE`, default
  `record futex c2c`) cannot work, it writes `FAILED` and exits 1 before unitd starts. Then
  `perf stat -e task-clock,PERF_EVENT -- true` must count. `PREFLIGHT_ONLY=1 run.sh profile TAG` stops there.
  Without a hardware PMU, `PROFILE="record futex" PERF_EVENT=cpu-clock` samples the clock; the kit never falls back
  to it on its own.
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
- **The lab container has no tracefs.** `perf stat -e syscalls:sys_enter_futex` failed with "unknown tracepoint".
  The preflight now picks `strace -c -f` on the router when the tracepoint cannot work. On x86 strace worked for
  capp (11947 calls in 10 s) and recorded nothing for static. Kernel symbols in perf and c2c stay unresolved under
  `kptr_restrict`. `PERF_KERNEL=1` changes both ("Perf privileges" below).
- **`perf c2c report --stats` prints the summary only,** without the cache line tables. profile.sh writes both.
- **Native runs keep the control socket short.** A unix socket path holds at most 107 bytes, so a native run puts
  unitd's state in `/tmp/scaling-TAG`.
- **Root files in the results.** The lab container runs as root. The box user cannot remove root files from the
  results directory, so a sweep and a profile give every file to the owner of that directory when they exit.
- **An ssh drop must not end a run.** The container starts detached (`docker run -d`) and a native run starts under
  `setsid nohup`. The output goes to `results/TAG/run.log` on the box.

## Perf privileges

- **A container capability does not grant perf privilege by itself.** perf checks CAP_PERFMON and CAP_SYS_ADMIN
  in the initial user namespace. In a rootless Docker or Podman container, `--cap-add PERFMON` is a capability of
  the container's user namespace: the kernel ignores it for perf, and it only lets `perf_event_open` past the
  default seccomp profile. The process then gets what the host user gets at the host's `perf_event_paranoid`. Only
  in a rootful container is the capability a real one. The preflight counts a capability only in the initial user
  namespace.
- **perf_event_paranoid** is global; a container sees the host value and cannot change it.
  - 0 or lower: system-wide mode (`PERF_SCOPE=cpus`) works for an ordinary user. -1 also allows tracepoints.
  - 1: per-process mode, user and kernel.
  - 2 (the upstream default): per-process mode, user space only.
  - 3: software events only, as another session reports for Debian and Ubuntu kernels. Unverified here.
  - 4 (Ubuntu): every perf call needs a capability, software events and `-p` included. Whether CAP_PERFMON is
    enough or CAP_SYS_ADMIN is needed is unverified. With CAP_PERFMON only, the preflight prints a note, and the
    probe (`perf stat -e task-clock,PERF_EVENT -- true`) decides.
- **A VM may have no hardware PMU.** Then cycles, instructions and c2c fail in every mode with any capability or
  sysctl. Software events (cpu-clock, task-clock) still work: `PROFILE="record futex" PERF_EVENT=cpu-clock`.
- **Kernel symbols** need `kptr_restrict` 0 with `perf_event_paranoid` 1 or lower, or CAP_SYSLOG in the initial
  user namespace at `kptr_restrict` 1. **The futex tracepoint** needs `perf_event_paranoid` -1 or a capability,
  and a readable tracefs. **strace -p** needs `ptrace_scope` 0, or CAP_SYS_PTRACE at 1 or 2. Yama accepts
  CAP_SYS_PTRACE in the target's user namespace, so in a container it works rootless too.
- **The container of run.sh profile** gets `--cap-add PERFMON` (perf) and `--cap-add SYS_PTRACE` (the strace
  fallback). A sweep and a locks run get no capability. `PERF_KERNEL=1` also adds `--cap-add SYSLOG` (kernel
  symbols at `kptr_restrict` 1) and `-v /sys/kernel/tracing:/sys/kernel/tracing` (the futex tracepoint). Both work
  only with a rootful docker daemon, and the bind mount gives the container the host's tracefs, so they are an
  opt-in. `NATIVE=1` runs as the box user, with no capability.
- **The lab boxes.** Another session probed two boxes read-only on 2026-10-19. These facts are reported, not
  verified by the kit; run `PREFLIGHT_ONLY=1 run.sh profile TAG` on a box first.
  - fu-build-arm (o-build): kernel 6.18.54-virt, `perf_event_paranoid` 1, no hardware PMU. cycles, instructions
    and c2c fail in every mode; cpu-clock and task-clock work.
  - pro4s-4: kernel 6.8.0, perf 6.8.12, `perf_event_paranoid` 4, CapEff 0. Every perf call fails for an ordinary
    user, `-p` and software events included.
  - pro4s-2: not probed.
  - The first kit's profile ran in the lab container with `--cap-add PERFMON`. Why that was enough is unexplained.

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
