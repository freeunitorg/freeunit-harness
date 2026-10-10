# Build knobs and host quirks

## Make variables

The generated root `Makefile` (`make help` in the source tree) accepts:

| Variable | Effect |
|---|---|
| `V=1` | verbose — show the actual compiler command lines |
| `D=1` | debug build, `-O0` |
| `E=0` | disable `-Werror` |
| `EXTRA_CFLAGS=` | append compiler options |

`D=1` is a *different thing* from `./configure --debug`: `D=1` is optimization
level, `--debug` is Unit's own debug log level and assertions. They compose.

## Configure options

`./configure --help` lists them all. The ones that come up: `--debug`,
`--tests` (also builds the C unit-test runner), `--openssl`, `--zlib`,
`--zstd`, `--brotli`, `--njs`, `--otel`, `--hardening=off|default|strict`.

`--tests` no longer defines `NXT_TESTS` for the whole build; see
[`../SKILL.md`](../SKILL.md). `--hardening` is off by default. `default` adds
the stack protector, `_FORTIFY_SOURCE=3` (2 if 3 fails), stack-clash and
control-flow protection (`-fcf-protection`, or `-mbranch-protection=standard`
on arm64), and RELRO/BIND_NOW/noexecstack. `strict` also adds
`-Wformat=2`, `-Wvla`, `-Wimplicit-fallthrough` and
`-ftrivial-auto-var-init=pattern` (`auto/cc/hardening`). Each flag is probed,
so an old compiler drops it without an error. A bare `--hardening` is an
invalid option; give the level.

The harness passes `CONFIGURE_FLAGS` through, so
`make build CONFIGURE_FLAGS="--debug --openssl --tests"` reconfigures.

## When a module will not build

**Missing dev headers.** `./configure <module>` either fails outright or the
module ends up silently absent, in which case its tests *skip* — "Unit has no
python module(s)" — rather than failing. `make doctor` reports what is present;
`./configure <module>` is the authority.

**`./configure python` looks for `python-config`.** That is the default
(`auto/modules/python`). An image or distro with only `python3-config` stops
with `no Python found`. Pass `./configure python --config=python3-config`, as
CI does (`analysis-clang-ast.yml`).

**A stock Debian image cannot link the Ruby module.** `RbConfig["libdir"]`
returns `/usr/lib` while the real `libruby-X.Y.so` is under
`/usr/lib/<triplet>/`, and `auto/modules/ruby` probes only the former.
`ci/Dockerfile.dev` carries the symlink workaround; the real fix belongs in
`auto/modules/ruby`, probing the multiarch path via
`dpkg-architecture -q DEB_HOST_MULTIARCH`.

**PHP on Alpine needs three flags, and the error names none of them.**
`./configure php` stops with `error: no PHP embed SAPI found` even with
`php85-embed` installed and `embed` listed in the SAPI line. Two independent
causes, both visible only in `build/autoconf.err` — read it before theorising:

- Unit links `-lphp` and emits a `-L` only when `--lib-path` is given, but
  Alpine installs `libphp.so` into `/usr/lib/php<V>/`. Pass
  `--lib-path=/usr/lib/php85`.
- PHP 8.5's `zend_vm_opcodes.h` declares `__attribute__((preserve_none))`,
  which gcc 15.2 does not know. Feature probes compile with `-Werror`, so the
  ignored attribute becomes `[-Werror=attributes]` and the probe fails to
  compile. Add `-Wno-attributes` to the **core** configure's `--cc-opt` — it
  has to be there, not on the module, because `auto/save` records `--cc-opt`
  into `build/autoconf.data` for modules to inherit. Clang accepts the
  attribute, so `--cc=clang` is the alternative.

Full invocation in `ci/Dockerfile.alpine`. The general lesson outlives the
versions: a configure probe failure reports the *feature* as missing, never the
compiler diagnostic that caused it, so `build/autoconf.err` is the only place
the real error exists.

**`--otel` needs a rustc at least as new as `src/otel/Cargo.lock`'s MSRV
floor**, which is the highest `rust-version` among the locked crates — `tonic`
today (1.88 for `tonic@0.14.6`). Read it, don't remember it:
`cargo metadata --format-version 1 --locked --manifest-path src/otel/Cargo.toml`.
Each Rust crate in the tree has its own floor — `tools/unitctl` sits at 1.95
(`sysinfo`), well above `src/otel` — so check the crate you are building.
Several distros ship a rustc *below* that floor — Alpine 3.22 (1.87.0), Debian
trixie (1.85) and bookworm (1.63), Ubuntu noble (1.75) — while Alpine ≥ 3.24
(1.96.1) clears it. Where the distro is too old, install a toolchain via
`rustup` rather than using the distro package (this is what the repo's `.deb`
build does), or pin `tonic` back. Current versions per distro, the check
command, and the "re-run on each Rust release" procedure live in
[`freeunit-release/references/packaging-deps.md`](../../freeunit-release/references/packaging-deps.md).
Runtime `.so` deps and the exporter's
plain-`http://`-only TLS story are release/packaging concerns, not build
ones — see
[`freeunit-release/references/packaging-deps.md`](../../freeunit-release/references/packaging-deps.md).
