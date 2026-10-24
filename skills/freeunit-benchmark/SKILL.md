---
name: freeunit-benchmark
description: >-
  Measure FreeUnit performance with an A/B harness on pinned cores, noise floor first. Use when you want to claim a change made Unit faster or slower.
---

# Skill: benchmark

**Trigger:** you want to claim a change made Unit faster or slower.

Build the two things you are comparing into separate directories, then:

```bash
make bench A=/path/to/build-before B=/path/to/build-after ROUNDS=4
```

or call the script directly, which is where the real option surface lives:

```bash
tools/bench-engines.sh before /path/to/build-a after /path/to/build-b 4
```

## What matters

**Establish the noise floor first.** Point both slots at the *same* build and
run the identical matrix. Whatever spread that A/A run shows is the smallest
difference you are entitled to call real:

```bash
ALLOW_SAME=1 tools/bench-engines.sh base /path/to/build base /path/to/build 3
```

Skipping this is the single most common way to publish a number that does not
survive contact with a second opinion.

**Pin the cores, and never share them.** Server and load generator must be on
disjoint CPU sets and nothing else may run on either. Two benchmark runs on one
box collide even when they look independent — and the numbers stay plausible,
which is what makes it dangerous. Benchmarks are serial, foreground work: not
from inside an agent, not alongside a build or a test suite.

**Failed requests invalidate a measurement, they do not annotate it.** A run
where the server shed load has a higher RPS *and* a better p99 than one that
served everything. `MAX_FAILED` defaults to 0 for that reason.

**Assert what you are actually running.** The harness greps each build's own
`using "<name>" event engine` line and aborts if both report the same engine,
because an accidental A/A reports a truthful, meaningless 0%. A build predating
that log line records `unknown` and the check is skipped — you are then on your
own to prove the two builds differ.

**Two cells need an 8-cell counterbalance, not rotation, and not just ABBA.**
Comparing exactly two builds and alternating order *per repetition* (A,B |
B,A | A,B | ...) puts identical labels back-to-back across the repetition
boundary, and the second occurrence inherits a warm cache the first doesn't —
this produced a significant +3.7% difference between a binary and itself in
one run. A 4-cell A,B,B,A block per repetition is *not* enough either: it
puts one label on both outer slots and the other on both inner slots, and on
a host with a non-linear per-slot effect a null pair still failed at ±2.8%.
`tools/bench-release.sh` uses an 8-cell block (`X Y Y X Y X X Y`) instead,
plus a `--swap` run and `tools/bench-crossover.py` to cancel residual
slot-set bias — see
[`references/release-ab.md`](references/release-ab.md#8-cell-counterbalance-not-abba-not-rotation)
for the full mechanism, including why this does *not* apply to
`bench-engines.sh`'s multi-scenario matrix (more than two cells makes
straight rotation a proper Latin square).

**A point estimate without an interval is not a result.** Report a 95% CI
half-width next to every delta. A −8.7% figure whose interval covered zero
nearly went into a release report before anyone checked — see
[`references/release-ab.md`](references/release-ab.md#report-the-interval-not-just-the-point-estimate).

## References

- [`references/guide.md`](references/guide.md) — end-to-end guide to
  `freeunit-harness` and benchmarking: overview, cell architecture, preflight
  (`tools/doctor`) and benchmark commands, reading JSONL / `epx-fit.py` /
  `perf-compare.py` results, and references to host `sysctl` tuning, PR #810,
  and PR #809.
- `docs/perf/perf-lab.md` (from the repository root): the perf lab:
  `tools/perf-job.sh <job>` runs a claimed, idle-gated A/B with its own null
  phase on the perf box, and `tools/perf-compare.py` prints the verdict.
  Use it for any per-request cost claim that small models must be able to rerun.
- `docs/perf/scaling-kit.md` (from the repository root): the router-thread
  scaling kit, `tools/perf-lab/scaling/run.sh`. Interleaved sweeps over
  listen_threads, mutex calls per request from two shim snapshots, perf c2c,
  and the traps: clock drift, loadgen-bound cells, one-run pairs.
- [`references/methodology.md`](references/methodology.md) — the scenario
  matrix, the knobs, and how to read RPS against CPU-per-request.
- [`references/startup.md`](references/startup.md) — `tools/bench-startup.sh`,
  the startup/lifecycle variant: wall-clock time to "router started", to the
  first 200 from a fresh worker, across a reconfigure, and to exit. Reach for
  it when the change under test runs *before* the first request — a
  `bench-release.sh` result on such a change is a truthful, uninformative 0%,
  and is only half the report.
- [`references/release-ab.md`](references/release-ab.md) — `tools/bench-release.sh`,
  the two-build release-cut variant: request-thread CPU straight from
  `/proc`, ABBA ordering, and the guard list with the failure each one caught.
- [`../freeunit-verify/SKILL.md`](../freeunit-verify/SKILL.md) — a `.replace()` with no
  assertion silently no-op'd a cell-label substitution in a benchmark script
  once; every row in an otherwise-clean-looking run was mislabeled.
