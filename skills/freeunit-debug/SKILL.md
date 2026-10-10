---
name: freeunit-debug
description: >-
  Debug FreeUnit crashes, hangs and leaks with a debug build and a debugger. Use for a crash, a hang, a leak, or a test that fails in a way the log does not explain.
---

# Skill: debug

**Trigger:** a crash, a hang, a leak, or a test that fails in a way the log does
not explain.

```bash
make build CONFIGURE_FLAGS="--debug --openssl --tests"
cd src && make D=1 unitd        # -O0 on top, for a usable debugger
```

`--debug` (Unit's own debug log level) and `D=1` (optimization) are different
knobs that compose — see [`freeunit-build/references/configure.md`](../freeunit-build/references/configure.md).

## What matters

**Attach to the right process.** Unit is a main process, a controller, a router,
and per-app workers; the pid you started is the main process, which is rarely
where the bug is.

```bash
make start
make status              # lists main / controller / router with pids
gdb -p <router-pid>
```

For a crash at startup, `set follow-fork-mode child` before letting it run, or
run the router in the foreground if you can reproduce without the supervisor.

**The log is the primary instrument, not the debugger.** `--debug` output from
the router usually answers ordering questions faster, because attaching
perturbs the timing you are trying to observe.

**Check `pgrep -a unitd` before every reproduction attempt.** A survivor from a
previous run holding the port produces failures that look like your bug — see
[`freeunit-test/references/failure-shapes.md`](../freeunit-test/references/failure-shapes.md).

**A 503 is two different failures.** Under a slow instrumented build a delayed
app spawns its worker on demand, so a test that fires immediately gets a 503
from the router before anything is wrong — that is a readiness race in the test.
Request until you get a 200 before asserting anything, and check whether a
worker existed at all before investigating the request path.

## References

- [`references/sanitizers.md`](references/sanitizers.md) — the ASan+UBSan build,
  and the two ways it silently lies to you.
- [`references/instrumenting.md`](references/instrumenting.md) — why a
  home-made instrument reports nothing, how to prove a fix with it, and why a
  C subtest that is silent on success cannot show it ran at all.
- [`../freeunit-cross-arch/SKILL.md`](../freeunit-cross-arch/SKILL.md) — architecture-specific
  crashes: emulated reproduction, capturing a faulting PC, ARM alignment.
- [`../freeunit-verify/SKILL.md`](../freeunit-verify/SKILL.md) — `pkill -f <pattern>` can match
  the invoking shell's own command line and kill itself, surfacing as a bare
  `exit 255` that reads like a flaky host, not like the check killing itself.
