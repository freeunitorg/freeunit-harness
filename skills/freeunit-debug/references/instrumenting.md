# Home-made instruments: why they report nothing

When the built-in tooling is not enough you end up adding a crash handler, a
trace file, an ad-hoc counter. Four failure modes recur, and all of them look
like "no bug found" — or, in the last case, like "the test covered it".

## Anything the router writes, it writes unprivileged

The router and controller drop to an unprivileged user. A debug artifact your
instrumentation creates with `O_CREAT` in a root-owned directory fails with
`EACCES` and is silently lost — and the write error is invisible precisely
because you added the code to diagnose something else. The run then reports zero
crashes **while crashing**.

Pre-create the file world-writable and open it without `O_CREAT`:

```sh
: > /path/to/CRASH.txt; chmod 0666 /path/to/CRASH.txt
```

## An instrument that reports nothing has two explanations

"No crash recorded" and "the recorder could not write" look identical from the
outside. So do "no UBSan report" and "reports went to a `log_path` you did not
read". Before concluding a bug is absent, prove the instrument fires: reproduce
the *known* positive with it first. A negative from an unvalidated instrument is
not evidence.

## Prove a fix with the instrument that produced the failure

Not with the absence of a symptom. If a crash handler recorded 20 faults on the
broken build, keep that exact handler in the fixed build, so "zero records" is a
measurement rather than an inference. Symptom-absence is equally consistent with
having perturbed the layout, disabled the path, or broken the harness.

## A subtest that only logs on failure cannot prove it ran

The C suite's tests log an umbrella line and nothing else on the happy path.
`nxt_mp_test()` (`src/test/nxt_mp_test.c`) opens with `mem pool test started`
and closes with `mem pool test passed`; every check between them is silent
unless it fails, in which case it logs `NXT_LOG_NOTICE` and returns
`NXT_ERROR`. That closing line therefore means "the function reached its last
statement" — not "each case inside it executed".

So a boundary case added inside such a test, returning `NXT_OK` without
logging, is indistinguishable in the output from a case that was compiled out,
skipped by a guard, or never reached. Two of them, added to prove an allocator
invariant, printed exactly the same successful run as a build where they did
not exist.

Prove they ran, with an **incremental instrumented rebuild**:

1. Add a `nxt_log_error(NXT_LOG_NOTICE, thr->log, "...")` inside each case —
   naming the case, and the value it computed, so the line is evidence rather
   than a heartbeat.
2. Recompile only the touched objects (`make ctest`, or `cd src && make
   tests`; the build is incremental, so this is two objects, not the tree).
3. Rerun as a **clearly separate run** — a fresh invocation with its output
   captured to its own file. Comparing against scroll-back from the earlier
   run is how a stale artifact gets read as the new one (see the [verify
   skill](../../freeunit-verify/SKILL.md)).
4. Restore the tree and confirm the restore: `git diff` on the test file must
   come back empty before you commit anything.

The same reasoning applies to any `assert`-free branch you are relying on: an
instrument that only speaks when something is wrong tells you nothing about
whether it was ever asked.
