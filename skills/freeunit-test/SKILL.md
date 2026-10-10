---
name: freeunit-test
description: >-
  Run the FreeUnit pytest suite in isolation and write regression tests. Use when you changed something and need to know whether it still works, or you are writing a regression test for a fix.
---

# Skill: test

**Trigger:** you changed something and need to know whether it still works, or
you are writing a regression test for a fix.

```bash
make test                          # the whole python suite
make test TESTS=test/test_static.py
make test TESTS="test -k keepalive"
make test SUDO=sudo                # includes privilege-sensitive cases
make ctest                         # the C unit-test runner
```

Directly: `cd src && python3 -m pytest --print-log test`, and `./build/tests`
for the C suite (needs `./configure --tests`, see the
[build skill](../freeunit-build/SKILL.md)).

## What matters

**One stray `unitd` anywhere on the host breaks every run.** The suite
wildcard-binds `*:8080` while probing capabilities at session start, before any
test is selected. Use `tools/test-isolated`; its header comment is the full
explanation.

**`--restart` is per-test-file, not a global preference.**
`test_graceful_reload.py` *requires* it — its autouse fixture skips every test
without it, because the tests signal the master. But `--restart` makes conftest
`rmtree` the temp dir on teardown, which trips
`test_php_application_forbidden`'s restricted-permission fixture with a
`PermissionError`. Pass it only on the legs whose tests need it.

**A test that needs a flag no workflow passes has never run.**
`test_graceful_reload.py` silently skipped for two months because no CI leg
passed `--restart`, so its full-drain assertion had never once executed. When
you find an autouse skip fixture, check whether anything actually supplies its
condition.

**A new regression test must be shown to fail on the unpatched code.** Build a
second worktree at the base commit and run the same test there. A test that
passes before and after your fix is testing nothing — and this is the step that
most often reveals it is asserting the wrong thing.

**In the C suite, "test passed" means the function reached its end.** Cases
that return `NXT_OK` without logging are invisible: `nxt_mp_test()` prints
`mem pool test passed` whether the boundary case you just added executed or
not. Prove a new case ran with an incremental instrumented rebuild —
[`../freeunit-debug/references/instrumenting.md`](../freeunit-debug/references/instrumenting.md#a-subtest-that-only-logs-on-failure-cannot-prove-it-ran).

**Some tests need root.** Isolation, clone credentials and user-switching cases
need privileges; CI runs the suite under `sudo -E`. `make test` never escalates
on its own — pass `SUDO=sudo` deliberately.

### Fixture ports in `src/test/` must be released in two steps

`nxt_port_close()` releases descriptors and mappings only; `nxt_runtime_port_remove()`
releases the port itself (its `rt->ports` entry, the runtime's reference, and the
port's own memory pool, which a fixture's `nxt_mp_destroy()` cannot reach). Call both,
`remove` while the fixture runtime is still installed. Missing the second call was found
three times on 2026-08-28 across #211 and #212 (`nxt_router_new_port_test.c`,
`nxt_port_fd_test.c`) — it passes locally and leaks under leak-checking or repeated runs.

### A symlinked `JAVA_HOME` breaks Java rootfs isolation

`auto/modules/java` binds `NXT_JAVA_HOME` (the `--home` argument, verbatim) into the app rootfs
(`nxt_java_mounts[]`, ~line 329) but takes `NXT_JAVA_LIB_PATH` from `java -XshowSettings`
(`sun.boot.library.path`), which is the *resolved* directory. When `JAVA_HOME` is a symlink (GitHub's
toolcache adopts preinstalled JDKs as symlinks into `/usr/lib/jvm`), the chroot contains the symlink
path but not the real one and the JVM dies with "Error occurred during initialization of VM"
(`test_java_isolation_rootfs_chroot_war`). CI fix: `./configure java --home="$(readlink -f "$JAVA_HOME")"`.
The product-side gap (bind the resolved path, or both) is open — found 2026-08-28 by PR #235, the
first run with the matrix JDK.

## References

- [`references/pytest-options.md`](references/pytest-options.md) — the options
  `test/conftest.py` adds, and what each is actually for.
- [`references/failure-shapes.md`](references/failure-shapes.md) — orphaned
  daemons after a hard death, reproducing a CI-only flake, headers that vanish
  only on CI, duplicate headers parsing as lists.
- [`../freeunit-verify/SKILL.md`](../freeunit-verify/SKILL.md) — `$?` after `./build/tests |
  tail` is `tail`'s exit status, not the suite's; `nxt_assert` is a no-op
  outside `--debug`, so a broken invariant can pass locally and only abort
  in CI's debug leg.
