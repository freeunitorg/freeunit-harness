# Release A/B: `tools/bench-release.sh`

`tools/bench-engines.sh` answers "which engine is faster" over a scenario
matrix. `tools/bench-release.sh` answers the narrower question a release cut
actually asks — "does the candidate cost more router CPU per request than
what shipped" — for exactly two builds, one metric, measured straight from
the kernel instead of from the load generator's own timers.

```bash
tools/bench-release.sh 1.36.0 /path/to/build-1.36.0 rc /path/to/build-rc 6
```

A single invocation is not a trustworthy A/B on its own — see "Even 8 cells
isn't enough alone" below for the null gate, `--swap` run, and
`tools/bench-crossover.py` combine that make one.

## The metric, and why it's read this way

Router **request-thread** CPU in microseconds/request, from
`/proc/<router-pid>/task/<tid>/stat` fields 14+15 (`utime`+`stime`, clock
ticks), summed over threads whose **`comm` is `unitd`**. Selecting by `comm`
is not a style choice: the OTel exporter runs on its own thread(s) —
`OpenTelemetry.T`, `reqwest-interna` — and if those get folded into the sum,
the request-path number silently includes batch-exporter CPU the request
itself never spent. `router_thread_ticks()`/`router_thread_count()` in the
script filter on `comm` for exactly this reason.

## Run the null pair first

Point A and B at the **same** build (`ALLOW_SAME=1`, two labels) before
trusting any real comparison:

```bash
ALLOW_SAME=1 tools/bench-release.sh same-1 /path/to/build same-2 /path/to/build
```

If a binary compared against itself does not settle near 0%, nothing else
this script reports means anything yet. This caught three separate
methodology defects — including the ordering bug below — before any release
number was trusted.

## 8-cell counterbalance, not ABBA, not rotation

With exactly two cells, alternating order **per repetition** (rep0: A,B |
rep1: B,A | rep2: A,B | ...) puts identical labels back-to-back **across the
repetition boundary** — rep0 ends on B, rep1 starts on B — and the second
occurrence of a label inherits a warm state (page cache, allocator arenas)
the first never gets. That produced a **significant +3.7% difference between
a binary and itself.**

The first fix tried was a 4-cell **ABBA** block per repetition:
`CELLS=("$X" "$Y" "$Y" "$X")`. The comment above that line claimed "each
label occupies one outer and one inner slot" — but the *code* puts X on
**both** outer slots and Y on **both** inner slots, every repetition,
because there are only two distinct positions (outer, inner) to hand out to
two labels across four slots. Comment and code disagreed, and on a host
with a **non-linear** per-slot effect (not the linear drift ABBA is designed
to cancel) that confounds slot position with label identity outright. A
2026-08-28 null pair (same binary, two labels) on the reference bench host came out at
**+2.77%** (95% CI [1.74%, 3.80%]) with this 4-cell block — later reproduced
at **±2.8%** — seven-plus of eight reps on the same side. The build sitting
in the outer slot pair read consistently higher than the same build in the
inner slot pair.

The actual fix is an **8-cell** block per repetition: `X Y Y X Y X X Y`.
This gives each label a full **outer pair** (slots `{0,3}` or `{4,7}`) *and*
a full **inner pair** (slots `{1,2}` or `{5,6}`) — the thing the 4-cell
comment described but the 4-cell code couldn't deliver. With the 8-cell
sequence the same null pair passed: **+0.14%**, 95% CI [−0.97%, +1.25%], sd
dropped from 3.35 to 1.14. `bench-release.sh` implements this sequence;
`--swap` (or `SWAP=1`) swaps which build is `X` and which is `Y`, so the
same builds can be run with the opposite slot assignment — see the next
section for why that matters even after the 8-cell fix.

**This is specific to two cells.** `bench-engines.sh`'s scenario × engine
matrix has more than two logical cells, where straight rotation is a proper
Latin square and needs no special-casing — do not "fix" that harness to use
an 8-cell block, it doesn't have the bug this one fixes.

`bench-engines.sh` does alternate order **per round** for its two builds
(A/B, B/A, A/B, ...), which has the *same two-cell rotation shape* as the bug
above. It has not been shown to suffer from it — a round there is a full
scenario pass, not a single request burst, which may dilute a cold-cache
boundary effect below significance — but it also hasn't been checked. Treat
that as open, not as verified-fine.

## Even 8 cells isn't enough alone — swap and cross over

The 8-cell block cancels a *constant* per-slot-pair offset as well as linear
drift, but it does not prove there isn't a **subtler** slot-position effect
that a same-vs-same null run happens not to expose (a null run is symmetric
in a way a real A/B is not: swapping two identical binaries changes nothing
to detect). The way this showed up: with the 8-cell block passing its null
gate cleanly, a **real** A/B (shipped build vs. an RC) on the reference bench host read
**−1.58%** (95% CI [−2.57%, −0.58%]) on a normal run — and **reversed sign**
to **+0.23%** on a second run of the exact same two builds with `--swap`
(the RC now sitting on the slot set the shipped build occupied in the first
run, and vice versa). Part of that −1.58% was which slot set the candidate
happened to land on, not the candidate itself.

**A trustworthy A/B is therefore three runs, not one:**

1. **Null gate** — `ALLOW_SAME=1`, same build under two labels. Must settle
   near 0% before anything else is trusted.
2. **Normal run** — `bench-release.sh labelA buildA labelB buildB`.
3. **Swap run** — `bench-release.sh --swap labelA buildA labelB buildB`
   (same args, same build dirs — only the slot assignment flips).

Then combine (2) and (3) with `tools/bench-crossover.py normal.tsv
swap.tsv`. The slot-set term enters the two runs with **opposite sign**
(the candidate is on the "expensive" slot set in one run and the "cheap"
one in the other), so it cancels in the average of the two runs' per-rep
differences; half the two runs' *disagreement* is a direct estimate of the
residual slot-set bias rather than something hidden inside a single number.
On the reference bench host that residual came out to about **±0.9%** — treat that as this
harness's resolution floor on that host, not a bug to chase further: a real
difference smaller than the residual cannot currently be told apart from
slot-set noise, no matter how many reps you add to a single run.

A single ABBA or 8-cell run, however clean its own null gate looked, is not
a substitute for the swap-and-cross-over pair once the effect you're
measuring is close to that floor.

## Report the interval, not just the point estimate

`bench-release.sh`'s paired summary prints a 95% CI half-width alongside the
delta. **A point estimate whose interval covers zero means "no difference
detected," not "N% faster/slower."** A −8.7% figure with exactly that
problem nearly went into a release report before anyone checked the
interval — the script's summary now refuses to let that number stand alone,
printing `NO DIFFERENCE DETECTED` when the half-width is at least as large as
the mean.

## Other guards, and the failure each one caught

- **Binary-identity check** (`sha256sum` on both `sbin/unitd`, refuses to run
  unless `ALLOW_SAME=1`) — catches "compared a tree against itself." The
  script prints the first 16 hex characters of each hash as a shorthand
  label (e.g. `A=a38690c5...`). **These are `sha256sum` prefixes of the
  compiled binary, not commit hashes** — a bench tree is built from a
  gitignored clone under `./src` with no `.git` of its own (see the
  top-level `README.md`), so there is no commit to point at. Two identical
  prefixes mean two identical binaries; they say nothing about which source
  revision produced them. Don't read a hash like `a38690c5` out of a bench
  note and go looking for it with `git log --all --grep` or `git show` — it
  isn't in the repo.
- **200-check before measuring** — a cell that 404s or 500s measures the
  error path, not the workload.
- **Hard-fail on zero CPU delta** — the counter not moving means the
  measurement is broken, not that the request was free.
- **`trap cleanup EXIT`** kills the router on every exit path, including
  `die()`. A failed cell that leaves a server holding the port poisons every
  cell that runs after it with a config-rejected error that looks like a new
  bug, not a leftover process.
- **Locale.** `LC_ALL=C` is forced at the top of the script. On a box with
  `LC_NUMERIC=ru_RU.UTF-8` (this one), `awk`'s `printf "%.2f"` emits
  `108,70` — comma decimal — which still looks fine in a terminal and
  silently corrupts the TSV for anything downstream expecting `.`. Found by
  accident while testing this script, not by design; worth having as a
  guard on any box whose locale you haven't checked.

## Pin cores, and check the box is quiet

`ROUTER_CPU` and `LOAD_CPUS` must be disjoint taskset sets — unpinned,
run-to-run variance was ~8%; pinning roughly halved it. The script also
warns (non-fatally, `pgrep` by process name — not `-f`, see the verification
skill for why) if a compiler/linker looks to be running, or load average
exceeds half of `nproc`. A concurrent build was the actual, eventually-found
root cause of noise in one run that no amount of counterbalancing fixed —
`ALLOW_NOISY=1` silences the warning once you've checked by hand that the box
really is idle.

**The script's quiet-box check only reads the 1-minute load average
(`/proc/loadavg` field 1).** On one run the 1-minute figure looked fine while
the 5- and 15-minute figures sat at 1.0-1.3 — evidence of contention that had
only just tailed off, not a quiet box with a clean history. Check all three
by hand (`uptime` or `cat /proc/loadavg`) before trusting a run whose 1-minute
number alone passed; a box that was busy a few minutes ago can still be
warming caches or arenas unevenly across cells.

## Check SMT siblings and the CPU governor before pinning

`ROUTER_CPU` and `LOAD_CPUS` being numerically disjoint is not the same as
being on **disjoint physical cores**. On the reference bench host, the router was pinned to
`cpu2` while the load generator ran on `LOAD_CPUS=4-7` — a set that
includes `cpu6`, which `cat
/sys/devices/system/cpu/cpu2/topology/thread_siblings_list` shows is
`cpu2`'s hyperthread sibling. The generator and the router were then
contending for the same physical core's execution units on every cell,
which is the exact confound `taskset` pinning exists to remove — just
expressed through SMT instead of the scheduler moving threads around.
`bench-release.sh` now prints the router CPU's sibling list and refuses to
run if `LOAD_CPUS` intersects it, unless `ALLOW_SMT_OVERLAP=1`. Check the
sibling list by hand on any new host before choosing `ROUTER_CPU` and
`LOAD_CPUS`, the same way you'd check the box is otherwise quiet (see the
[verify skill](../../freeunit-verify/SKILL.md) for the general habit of confirming a
guard actually catches what you think it catches).

The same 2026-08-28 run also had the CPU governor set to **`powersave`**,
which lets frequency scale mid-cell — non-stationarity indistinguishable
from a real delta to a tool that only measures CPU ticks, not wall-clock
throughput at a fixed frequency. `bench-release.sh` now refuses to run when
the router CPU's governor is `powersave`, unless `ALLOW_POWERSAVE=1` is set
deliberately. Fix: `sudo cpupower frequency-set -g performance`, or write
`performance` to the cpu's `scaling_governor` as root — the script prints
the current governor either way. Run `bench-release.sh --preflight-only` to
check the governor (and SMT siblings) alone, before committing to a build.

**The harness's variable names are `A=`/`B=`, not `BASE=`/`FIX=` or similar**
(`make bench A=<dir> B=<dir>` at the top level; `LABEL_A`/`BUILD_A`/`LABEL_B`/
`BUILD_B` inside `bench-release.sh` itself). Editing a saved invocation with
the wrong name — e.g. `sed -i "s|^FIX=.*|...|"` against a line that is
actually `B=...` — matches nothing, exits 0, and the run then silently
compares the wrong pair while looking healthy (see the [verify
skill](../../freeunit-verify/SKILL.md) for the general shape of this failure). After
editing any such line, grep it back and read what it actually says.

**Governor needs root on the reference bench host.** `scaling_governor` is root-owned and `sudo` prompts for a password there,
so an agent cannot switch `powersave` → `performance`. Either the user sets it before the run or the run
must tolerate frequency excursions: on 2026-08-28 one swap run read 50–70 µs/req for two reps against ~82
everywhere else and had to be discarded and re-run. The preflight prints the governor and refuses
`powersave` unless `ALLOW_POWERSAVE=1` is set deliberately (`bench-release.sh --preflight-only` checks
this without needing a build).
