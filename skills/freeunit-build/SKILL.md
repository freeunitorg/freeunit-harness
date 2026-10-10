---
name: freeunit-build
description: >-
  Configure and build FreeUnit unitd and its language modules. Use when you need a unitd binary: a first build, a new language module, a debug build, or a build with different options.
---

# Skill: build

**Trigger:** you need a `unitd` binary — first build, a new language module, a
debug build, or a build with different options.

```bash
make doctor        # what this box can build; changes nothing
make build         # configure + make unitd + the modules in MODULES
make rebuild       # rebuild without re-running configure
```

Underneath that is Unit's own two-step — `./configure` for the server, then
`./configure <module>` per language module, then `make`:

```bash
cd src
./configure --debug --openssl --tests --prefix=$PWD/build
./configure php
make -j$(nproc) unitd
make -j$(nproc) php
```

Artifacts land in `build/` **inside the source tree** — `build/sbin/unitd`,
`build/lib/unit/modules/php.unit.so` (`auto/modules/php`) — with no
`make install` step.

## What matters

**`./configure` rewrites the tree it runs in.** It regenerates the root
`Makefile` and repopulates `build/`, so running it inside a checkout you are
editing replaces that checkout's configured state. Build from the
harness-managed clone or a `git worktree`.

**The build directory is not a free choice.** `NXT_BUILD_DIR` moves the
artifacts, but `test/conftest.py` resolves the binary as
`<source-root>/build/sbin/unitd` (`conftest.py:466`) with no override. Move the
build and every pytest run exits with "Could not find unit". Leave it at the
default.

**`--tests` is required before `make tests`.** Without it the target errors out
with `to make tests you need to configure --tests option` (`auto/make`).

**`--tests` does not put hooks in `unitd`** (since #413, `96f88f76`).
`make tests` compiles every core and libunit source a second time with
`-DNXT_TESTS=1`, into `build/lib/libnxt-tests.a` and
`build/lib/libunit-tests.a`. Only the test programs link those. `unitd`,
`libnxt.a`, `libunit.a` and the modules stay hook-free. The gate
`.github/scripts/check-test-hooks.sh` checks this from the objects. It needs
the plain `build/lib/libunit.a`, which `make tests` does not build. Without
it the gate fails with `build/src/nxt_unit.test.o has no plain object
build/src/nxt_unit.o`. The order CI's `c-tests` job uses:

```bash
./configure --openssl --tests
make && make build/lib/libunit.a && make tests
.github/scripts/check-test-hooks.sh     # last line: ok: hooks are in the test programs only
./build/tests
```

`tools/unit-build ... tests` runs this sequence on every target.

**Module configure inherits the core configure's flags.** `auto/save` records
`--cc-opt`/`--ld-opt` into `build/autoconf.data`, and a module configured
afterwards picks them up. That is what keeps instrumentation consistent between
`unitd` and its modules — see [`freeunit-debug/references/sanitizers.md`](../freeunit-debug/references/sanitizers.md)
for what breaks when it does not. It also means reconfiguring the core with
different flags invalidates modules built against the old ones; rebuild them.

**Compile-clean is not correct, because PR CI does not compile everything.**
`fuzzing.yml` has its `pull_request:` trigger commented out, so `fuzzing/*.c` is
never built on a PR. Change a shared function's signature and every PR check can
pass while the fuzz build breaks. Grep `src/`, `src/test/` **and** `fuzzing/` —
not just what CI compiles.

## References

- [`references/configure.md`](references/configure.md) — build knobs, the
  options that come up, and the host-specific reasons a module refuses to build.
- [`../freeunit-verify/SKILL.md`](../freeunit-verify/SKILL.md) — a stale `./build/tests` binary
  runs happily on code that never recompiled if you don't check the build
  step's own exit status, not just the run that follows it.
