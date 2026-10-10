# Startup-path benches: `tools/bench-startup.sh`

`bench-engines.sh` and `bench-release.sh` both measure the **request path** —
one as RPS across a scenario matrix, the other as router CPU per request.
Neither can see a change that runs *before* the first request and never runs
again: a capability probe in runtime init, a credential check in the
`PROCESS_READY` handshake, an extra syscall on the fork path, a new field
zeroed at process creation.

Pointing `bench-release.sh` at such a change produces a number that is
truthful and useless: 0% ± the harness floor. That is worth reporting — it is
the falsifiable claim "this did not leak into the request path" — but it is
*not* a measurement of the change. The change lives somewhere else, and the
report is incomplete until that somewhere else is measured too.

`tools/bench-startup.sh` is that second instrument.

## The four phases, and what each one covers

| column | interval | what runs inside it |
|---|---|---|
| `a_router_ms` | `exec unitd` → `"router started"` in the log | runtime init, `nxt_capability_set()`, credential/uid setup, fork of main's children, controller and router start |
| `b_first200_ms` | config PUT → first HTTP 200 | controller applies the config, app process forks, libunit init, `PROCESS_READY`, shm queue mapping, first request served |
| `c_reconf_ms` | second config PUT → first 200 from the **new** app | the same worker handshake against an already-warm router — isolates worker start from process-tree start |
| `d_term_ms` | `SIGTERM` → main has exited | QUIT propagation, worker drain, teardown |

Reading them together is the point. A change that moves `a` but not `b` is in
runtime init. One that moves `b` and `c` equally is in the worker handshake.
One that moves `b` but not `c` is in something the first config does that a
reconfigure does not — module discovery, for instance.

## Why wall clock, and what that costs

Startup is not a throughput question, so there is no per-request denominator
to normalise against; the honest metric is elapsed time. That has one
consequence worth stating loudly, because it inverts an assumption carried
over from `bench-release.sh`:

**A `powersave` governor is a bigger problem here, not a smaller one.**
`bench-release.sh` counts CPU ticks, which are frequency-invariant to first
order — a slower clock costs more wall time for the same tick count. Wall
clock has no such protection: a core still sitting at 800 MHz when unitd
execs reads directly as a slow startup. And startup is exactly the workload
that catches a CPU cold, because nothing was running a millisecond earlier.
The script enforces the governor for that reason and takes `ALLOW_POWERSAVE=1`
only as a deliberate, reported override.

## Resolution

Every poll loop in the script is fork-free — `mapfile` for the log,
`exec {fd}<>/dev/tcp/…` for HTTP, `kill -0` for liveness — so a poll iteration
costs on the order of 0.1 ms rather than the ~5 ms a `curl` or `grep`
subprocess would cost. The control API is put on a **TCP** port rather than a
unix socket for the same reason: the PUT that starts phase (b)'s clock is then
issued from bash itself, so a `curl` process spawn is not sitting inside the
measured interval.

None of that makes the *measurement* accurate to 0.1 ms. These are wall-clock
timings of a process tree being forked and scheduled; on an idle 8-core box
the run-to-run spread is a few milliseconds even with pinning. **The IQR the
summary prints is the resolution of that run.** Do not claim a between-build
difference smaller than the wider of the two IQRs being compared.

## Median and IQR, not mean and sd

Startup latency is bounded below — there is a minimum amount of work — and has
a long right tail from stray scheduling delays and page-fault storms. A mean
chases the tail and a standard deviation implies a symmetry the distribution
does not have. The median reports the typical start; p90 reports the tail
explicitly rather than smearing it into the centre.

This is a genuine difference from `bench-release.sh`, which reports mean ± a
normal-approximation CI over *paired per-rep differences* — legitimate there
because the pairing removes most of the skew before the averaging happens.
There is no equivalent pairing here: two builds' iterations are separate
samples, not a matched pair per rep.

## Counterbalancing is still the caller's job

The slot-position and thermal effects documented in
[`release-ab.md`](release-ab.md#8-cell-counterbalance-not-abba-not-rotation)
are exactly as real for startup timing as for CPU per request. Running build
A's 20 iterations and then build B's 20 confounds the label with the block —
the same failure mode that produced a significant +3.7% difference between a
binary and itself.

`bench-startup.sh` deliberately does **not** implement a two-build A/B itself.
`bench-release.sh` needed three iterations of design (rotation → 4-cell ABBA →
8-cell, each caught by a null gate) before its two-cell ordering was
trustworthy, and shipping a second, unvalidated copy of that machinery would
be worse than leaving the blocking to the caller. Block it explicitly:

```bash
for blk in 1 2 3 4; do
  for lbl in A B B A; do
    RESULTS=$OUTDIR/startup-$lbl-blk$blk.tsv \
      tools/bench-startup.sh --label "$lbl" "$BUILD_DIR_FOR_$lbl" 10
  done
done
```

and run a **null gate first**, the same way — the same build under two labels.
If the same binary does not come out on top of itself, no A/B from that host
means anything yet. This is not optional just because the metric changed.

## Interpreting "no difference" honestly

For a change confined to the startup path, the complete report is two
measurements, not one:

1. `bench-release.sh` — the request path is unaffected. Expected result: a
   delta whose CI spans zero. Reported as "no difference detected", never as
   a percentage.
2. `bench-startup.sh` — the startup path is where the change actually is.
   Expected result for a check costing a handful of instructions once per
   process: a median difference far inside the IQR.

Both being null is the *predicted* outcome, and predicting it in advance is
what makes it evidence rather than an absence of evidence. Write the
prediction down before the run, so a surprising result is recognisable as a
surprise.
