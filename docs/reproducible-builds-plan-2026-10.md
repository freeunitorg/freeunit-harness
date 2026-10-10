# Reproducible builds of FreeUnit: findings and plan (2026-10-08)

Plan, not yet started. Every citation is to `freeunitorg/freeunit` `origin/master` at
`856d23b9` (2026-10-08) unless a PR head is named. Check a line with
`git show origin/master:<path> | sed -n 'Np'` before relying on it.

The goal: two builds of the same release, on two machines, in two directories, at two
times, give byte-identical `unitd`, modules, `libunit.a`, packages and images, or a
diff that names only the inputs that are allowed to differ.

## 1. Status after PR 588

https://github.com/freeunitorg/freeunit/pull/588 ("chore: post-release housekeeping
after 1.37.0") is **open, not merged** (head `d53b1037` on 2026-10-08, Codex review
running). It is about the release version, not about bit-for-bit builds. What it does
that this plan builds on:

| Item in 588 | What it gives reproducibility | Verified how |
|---|---|---|
| Item 4: `ARG UNIT_VERSION` in every Dockerfile; `release-docker.yml` passes `--build-arg UNIT_VERSION=<tag>` instead of a sed rewrite | The image source is one declared input. The tag that is built is the tag in the file. | Seven image builds in the PR body, `unitd --version` and the label agree for 1.37.0 and 1.36.1. |
| Item 5: `.github/scripts/check-version.sh` and `.github/scripts/version-sites` | One list of every file that carries the release version; CI fails on drift. | Planted errors in the PR body each fail with the file named; passes on the head, fails on tags 1.36.1 and 1.37.0 as expected. |
| Item 6: `tools/bump-version.sh` | A release bump is a script, so the version inputs are the same on every run. | Two runs give the same 41-file diff; a second run changes nothing. |
| Item 7: Rust pin rows | The Docker Rust toolchain (1.95.0) is checked across ten files. | `RUST_VERSION=1.96.0` planted in `test/run-local.sh` fails the check. |
| Item 8 and 9: lint on master pushes and on tags | The release commit itself is checked. | Extracted step run with real, empty and all-zero base. |

What 588 leaves for this plan: everything below. It never touches compiler flags,
tarballs, `ar`, cargo, jar, rpm macros or `SOURCE_DATE_EPOCH`. The Docker base images
stay tags (`FROM php:8.5-cli-trixie`), and `apt-get install` stays unpinned.

Prior work: none. The offline archive (nginx/unit and freeunitorg, mirror of 2026-09-12)
and live `gh search` have no issue or PR about byte-identical builds. The nearest is
https://github.com/nginx/unit/issues/1537 (ac000, 2025-01-10, open): cargo needs the
network during the OTel and wasm builds; remicollet builds RPMs offline from a vendored
cargo directory. `TODO.md:419` lists "Reproducible binaries across platforms" as a benefit
of prebuilt Rust artifacts, with no plan. `git grep SOURCE_DATE_EPOCH origin/master`
returns nothing.

## 2. Measured: two C builds on the build host

Experiment on 2026-10-08, gcc 13.3.0, GNU ld and ar 2.42 (Ubuntu 24.04), tree exported
with `git archive origin/master` into `$BUILD_ROOT/repro-fable-2026-10-08/{a,bbbbbbbbbbbb/b}`.
Build B also ran with `umask 002`, `TZ=Asia/Tokyo`, `LANG=C.UTF-8`, 78 s later.
`./configure --prefix=/usr/local --openssl && make -j && make build/lib/libunit.a`.
The directory was deleted after the run. No php-config on the box, so no module was built.

| Build pair | unitd | libunit.a | libnxt.a | nxt_auto_config.h |
|---|---|---|---|---|
| plain A vs B | differ | differ | differ | same |
| `--cc-opt="-ffile-prefix-map=$PWD=."` | differ | **same** | differ | differ (the flag text) |
| plain configure, `make EXTRA_CFLAGS="-ffile-prefix-map=$PWD=."` | **same** | **same** | **same** | same |

What differed in the plain pair, by section:
- `.debug_line_str` holds the compilation directory (`DW_AT_comp_dir`). This is the only
  place the path appears; `__FILE__` strings are relative (`src/nxt_cert.c`), so `.rodata`
  has no path. Cause: `auto/cc/test:100` adds `-g` for gcc unconditionally.
- `.note.gnu.build-id` differs because it hashes the above. After `strip`, the two
  binaries differ in exactly 20 bytes, all inside the build-id note.
- `ar tv` shows `0/0 ... Jan 1 1970`: the archive headers are already deterministic on
  this binutils. The archives differ only through their members.
- Time, time zone, locale and umask changed nothing. The path is the only input that leaked.

Why `--cc-opt` was not enough: `auto/options:154-156` appends every configure argument
verbatim to `NXT_CONFIGURE_OPTIONS`; `configure:82` writes it to `nxt_auto_config.h`;
`src/nxt_runtime.c:1087-1088` compiles it into `unitd` (`unitd --version` prints
`configured as ./configure ...`); `auto/make:635` and `:646` write it into `unit.pc`
(`confargs=` in `src/unit.pc.in:3`). A path inside the flag therefore lands in the binary.
This matters for every packager: `pkg/deb/debian/rules.in:65` passes
`--cc-opt="$(CFLAGS)"` from `dpkg-buildflags`, and dpkg's `reproducible=+fixfilepath`
default puts `-ffile-prefix-map=<buildpath>=.` into CFLAGS; `pkg/rpm/unit.spec.in:20,92`
passes `%{optflags}` the same way (Fedora's redhat-rpm-config adds the same flag).

## 3. Findings table

Severity: **blocks** = two honest builds of one release differ; **cosmetic** = differs
but not in a shipped artifact, or only in metadata; **input** = deterministic for a fixed
input that the packager controls. Size: XS one line, S under 30 lines, M a file or a
workflow, L cross-cutting.

| # | Source of non-determinism | Where (origin/master) | Severity | Fix | Size |
|---|---|---|---|---|---|
| 1 | Build path in DWARF: `-g` always on, no prefix map | `auto/cc/test:99-100` `NXT_CFLAGS="$NXT_CFLAGS -g"`; no `prefix-map` anywhere in `auto/` | blocks (measured) | configure probes `-ffile-prefix-map=$(pwd)=.` and adds it to `NXT_CFLAGS` after the `-g` line, for gcc and clang. Not through `NXT_CC_OPT`, so it stays out of the version string. | S |
| 2 | Configure line embedded verbatim, including `--cc-opt`/`--ld-opt` text that carries the path | `auto/options:154-156`; `configure:82`; `src/nxt_runtime.c:1088`; `auto/make:635,646`; `src/unit.pc.in:3` | blocks under dpkg and rpm (measured with `--cc-opt`) | In `auto/options`, strip `-ffile-prefix-map=*`, `-fdebug-prefix-map=*`, `-fmacro-prefix-map=*` from the value before it is appended, or record the user's path as `.` when it equals `$(pwd)`. Keep the rest of the line: it is useful and deterministic for fixed args. | S |
| 3 | `ar -r -c` without `D` | `auto/os/conf:24,47,74,81` `NXT_STATIC_LINK="\$(AR) -r -c"` | input (deterministic on distro binutils, measured; not on a binutils built without `--enable-deterministic-archives`, nor on BSD/macOS ar) | Probe `ar -D` in configure and add it when accepted. | XS |
| 4 | Build-id differs with content | linker default; no `--build-id` flag in `auto/` | none once 1 and 2 are fixed (measured: same build-id in the E/F pair) | Nothing. Do not set `--build-id=none`; distros want it. | 0 |
| 5 | OTel static lib: `cargo rustc --release` with no `--locked`, no `--remap-path-prefix` | `auto/make:45` `RUST_FLAGS = --release`; `auto/make:689-691`; `src/otel/Cargo.lock` is committed but not enforced | blocks (crate source paths under `$CARGO_HOME/registry/src/...` and the workspace path go into panic strings and debug info; a lock update changes code) | `RUST_FLAGS = --release --locked`; pass `-- --remap-path-prefix=$(pwd)=. --remap-path-prefix=$CARGO_HOME=/cargo` (default `CARGO_HOME` to `$HOME/.cargo`). Honour `RUSTFLAGS` from the environment for packagers. | S |
| 6 | wasm-wasi-component module: same as 5 | `auto/modules/wasm-wasi-component:88-90` `cargo build --release --manifest-path ...` | blocks | Same as 5, in the module script. | S |
| 7 | unitctl release binaries: no `--locked`, no remap, no strip, floating runner images | `tools/unitctl/GNUmakefile:45` `RELEASE_BUILD_FLAGS ?= --quiet --release --bin $(OUTPUT_BINARY)`; `.github/workflows/unitctl.yml:265-266,292` | blocks | `--locked`, `RUSTFLAGS="--remap-path-prefix=$HOME/.cargo=/cargo --remap-path-prefix=$PWD=."`, `strip = true` in `[profile.release]`, pin `rust-toolchain.toml`. | S |
| 8 | Java jars: `jar c` stamps entries with the build time | `auto/modules/java:585,600` `$NXT_JAVA_HOME/bin/jar c -C ...` | blocks | `jar --date=<ISO from SOURCE_DATE_EPOCH>` (JDK 19+; probe it) or `touch -d @$SOURCE_DATE_EPOCH` the class tree before `jar c`. Downloaded jars are sha512-checked (`auto/modules/java_get_jar:17-21`), so they are fixed inputs. | S |
| 9 | deb orig tarball: plain `tar -czf` of the working tree | `pkg/deb/Makefile:283-286` | blocks (mtime, owner, order, gzip header; untracked files under `src test tools` get in) | `git archive --format=tar.gz --prefix=$(SRCDIR)/ HEAD -- <same paths>`, as `pkg/Makefile:14-18` already does for `make dist`; or `tar --sort=name --mtime=@$SOURCE_DATE_EPOCH --owner=0 --group=0 --numeric-owner \| gzip -n`. | S |
| 10 | rpm source tarball: same | `pkg/rpm/Makefile:209-212` | blocks | Same as 9. | S |
| 11 | `make dist`: `git archive` of the tag | `pkg/Makefile:14-18` | none (git archive sets mtime from the commit, owner 0, sorted; gzip header has no name and mtime 0) | Keep. Set `tar.tar.gz.command` only if a git version changes the output; record the sha512 that is already written. | 0 |
| 12 | rpm header BUILDTIME and BUILDHOST; file mtimes not clamped | no `source_date_epoch_from_changelog`, `clamp_mtime_to_source_date_epoch`, `use_source_date_epoch_as_buildtime`, `_buildhost` in `pkg/rpm/unit.spec.in` or `unit.module.spec.in`; `pkg/rpm/Makefile:217` passes no `-D` | blocks | Four `%global` lines at the top of both spec templates, `_buildhost reproducible`. The changelog dates come from `docs/changes.xml` (`date="2026-10-04" time="00:00:00 +0000"` at `:10`), so the epoch is fixed. | XS |
| 13 | deb: relies on dpkg defaults | `pkg/deb/debian/rules.in:12-15` (`hardening=+all,-pie`, `buildflags.mk`); `:165` `dh_strip --dbg-package` | none (dpkg sets `SOURCE_DATE_EPOCH` from `debian/changelog`, dh clamps mtimes and runs `strip-nondeterminism`) | Nothing, once 2 is fixed; dpkg's `-ffile-prefix-map` then does its job. | 0 |
| 14 | Changelog generation falls back to `date` for an undated entry | `docs/Makefile:19-20` `CURDATE:=$(shell date ...)`; `docs/changes.xslt:252-255` | cosmetic (every release entry carries a date) | Derive `CURDATE` from `SOURCE_DATE_EPOCH` when set. | XS |
| 15 | Docker base images by tag; `apt-get install` from live mirrors | `pkg/docker/template.Dockerfile:1,15,102`; every `Dockerfile.*:1` (`FROM php:8.5-cli-trixie`) | blocks (expected: an image is a snapshot of Debian on the day) | Pin `FROM ...@sha256:` (Dependabot `docker` ecosystem keeps them fresh); for apt, either accept, or point sources at `snapshot.debian.org` by the tag date. Record the decision in `pkg/docker/README.md`. | M |
| 16 | buildx: no `SOURCE_DATE_EPOCH`, no `rewrite-timestamp`, provenance at default | `.github/workflows/release-docker.yml:134-146` | blocks (layer mtimes, `created`) | `env: SOURCE_DATE_EPOCH: <commit time of the tag>` on the build step and `outputs: type=image,rewrite-timestamp=true`; see https://docs.docker.com/build/ci/github-actions/reproducible-builds/ . | S |
| 17 | Module build inside the Dockerfile uses `git clone --depth 1 -b <tag>` | `template.Dockerfile:39` | input (the tag is the input; 588 makes it one declared ARG) | Nothing beyond 588. | 0 |
| 18 | npm package: `cp -rp` then `npm publish`, by hand | `pkg/npm/Makefile:13,21`; module side `auto/modules/nodejs:178` `npm pack` | cosmetic (npm normalises entry mtimes to 1985-10-26; not measured here) | Measure once with `npm pack` twice and `cmp`. | XS |
| 19 | Python, Perl, Ruby, PHP, Go modules | `auto/modules/python:76` rpath, `auto/modules/ruby:189-206` and `auto/modules/php:556-561` mount tables from the host interpreter | input (host paths of the interpreter; same on the same distro) | Nothing. Covered by 1 and 2 for the C part. | 0 |
| 20 | contrib downloads (njs, wasmtime, wasi-sysroot) | `pkg/contrib/Makefile:44,90,124`; `pkg/contrib/src/*/SHA512SUMS` | none (pinned by sha512) | Nothing. | 0 |
| 21 | Cargo crates fetched at package-build time; no vendoring | deb/rpm builds run cargo online; `pkg/deb/build-local.sh:360-380` only mirrors | input today; blocks once a crate is yanked or the index moves | Later: `cargo vendor` into the release tarball (nginx/unit 1537, remicollet's approach), or a `vendor.tar.xz` release asset with a sha512. | L |
| 22 | Object and link order | static lists in `auto/sources` and `auto/make`; no `find`, `ls` or glob in `auto/` | none | Nothing. | 0 |
| 23 | `NXT_SYSTEM_VERSION` (`uname -r`), `NXT_COMPILER_VERSION` in `nxt_auto_config.h` | `configure:84`; `auto/os/test:6-92` | none (no reader in `src/`, so not in a binary; measured: the header was identical across builds) | Optional: drop the `uname -r` define. | XS |
| 24 | `SOURCE_DATE_EPOCH` | not mentioned anywhere in the tree | gap | Items 8, 9, 10, 14, 16 read it; configure exports it to `Makefile` when set. Format per https://reproducible-builds.org/docs/source-date-epoch/ : unsigned seconds, UTC. | with each item |
| 25 | Docs | `unit-docs` is a separate repo; no sphinx under `.github` or `pkg` | out of scope here | Note for a docs-repo plan: sphinx honours `SOURCE_DATE_EPOCH` for `html_last_updated_fmt`. | 0 |

## 4. Phased plan

One PR per row. Each PR body quotes the sha256 pair before and after on the build host (section 5).

### Phase 1, for 1.37.x (quick wins, no behaviour change)

| PR | Rows | Content | Test that fails before |
|---|---|---|---|
| **R1: build: keep the build path out of the binaries** | 1, 2, 3 | `auto/cc/test`: probe and add `-ffile-prefix-map=$(pwd)=.` after `-g` (gcc and clang). `auto/options`: do not record prefix-map flags in `NXT_CONFIGURE_OPTIONS`. `auto/os/conf`: `ar -D` when the probe passes. | Two builds in two paths, `cmp build/sbin/unitd`: differs at origin/master (measured), identical after. `unitd --version` from a dpkg-style `--cc-opt` has no path. |
| **R2: pkg: deterministic source tarballs** | 9, 10 | `git archive` in `pkg/deb/Makefile` and `pkg/rpm/Makefile`, same path list. | `sha256sum` of two `orig.tar.gz` made one minute apart. |
| **R3: rpm: SOURCE_DATE_EPOCH macros** | 12 | Four macros in both spec templates. | `rpm -qp --qf '%{BUILDTIME} %{BUILDHOST}'` on two builds. |
| **R4: rust: locked and remapped builds** | 5, 6, 7 | `--locked`, `--remap-path-prefix` for otel, wasm-wasi-component, unitctl; `strip = true` for unitctl. | `strings libotel.a \| grep registry/src` non-empty before, empty after; `cargo build --locked` fails on a planted lock edit. |
| **R5: java: dated jars** | 8 | `jar --date` from `SOURCE_DATE_EPOCH` (probe), else touch before pack. | `unzip -l unit-http-*.jar` timestamps differ before, equal after. |

Order: R1 first. It is the only one with a measured, end-to-end proof already, it
unblocks every packager, and it is small. R2 and R3 are independent and can go the same
week. Check overlap with the hardening wave before touching `auto/cc/*` (#445 to #448
do not touch `auto/`, but `--hardening` lives in `auto/cc/hardening:51,77`, which runs the
probe with `$NXT_CC_OPT $CFLAGS`; R1's flag must be in `NXT_CFLAGS` before that probe or
be harmless to it).

### Phase 2, for 1.38

| PR | Rows | Content |
|---|---|---|
| **R6: docker: pinned bases and dated layers** | 15, 16 | `@sha256` digests in `template.Dockerfile` and the generated files (`make dockerfiles` must carry them; `check-version.sh` already diffs the output), Dependabot `docker` updates, `SOURCE_DATE_EPOCH` and `rewrite-timestamp` in `release-docker.yml`. Decide apt snapshots (open question 3). |
| **R7: CI: reproducibility job** | section 5 | New job in `build-test.yml`: two builds, `cmp`, diffoscope on failure. |
| **R8: cargo vendoring for packages** | 21 | A `vendor` tarball as a release asset, used by deb and rpm offline. Pairs with the issue nginx/unit 1537. |
| **R9: docs** | 14, 25 | `CURDATE` from the epoch; a short "Reproducible builds" page in `unit-docs` that states the inputs (toolchain, configure line, `SOURCE_DATE_EPOCH`) and the verification command. |

## 5. Verification and CI

### Local, on the build host (what the agent ran, as a script)

```sh
set -eu
. "${LAB_ENV:-run/lab.env}"     # BUILD_HOST, BUILD_ROOT; UNIT_SRC is your FreeUnit checkout
S=$BUILD_ROOT/repro-$USER-$(date +%F)
mkdir -p $S && git -C "$UNIT_SRC" archive origin/master | ssh "$BUILD_HOST" "mkdir -p $S/src && tar -x -C $S/src"
ssh "$BUILD_HOST" "cd $S && cp -r src a && mkdir -p bbbbbbbbbbbb && cp -r src bbbbbbbbbbbb/b
  cd $S/a && ./configure --prefix=/usr/local --openssl && make -j\$(nproc) && make build/lib/libunit.a
  cd $S/bbbbbbbbbbbb/b && umask 002 && env TZ=Asia/Tokyo LANG=C.UTF-8 sh -c './configure --prefix=/usr/local --openssl && make -j\$(nproc) && make build/lib/libunit.a'
  cd $S && for f in sbin/unitd lib/libunit.a lib/libnxt.a include/nxt_auto_config.h; do
    cmp -s a/build/\$f bbbbbbbbbbbb/b/build/\$f && echo \"same   \$f\" || echo \"DIFFER \$f\"; done
  strings -a a/build/sbin/unitd | grep -c $S || true
  readelf -n a/build/sbin/unitd | grep 'Build ID'; readelf -n bbbbbbbbbbbb/b/build/sbin/unitd | grep 'Build ID'
  rm -rf $S"
```

Variations to add once R1 is in: a different user (`sudo -u`), `--cc=clang`, a module
(`./configure php` on a box with php-dev; the build host has none, the `freeunit-test:latest`
container does), and `SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)` for R2 to R5.
diffoscope is not installed on the build host; `pipx install diffoscope` or the
`registry.salsa.debian.org/reproducible-builds/diffoscope` image gives the section-level
report when `cmp` fails. The full Debian method is `reprotest` (varies time, path, user,
umask, locale, kernel, timezone, file order): `reprotest --vary=-kernel 'sh -c "./configure && make"' build/sbin/unitd`
in the test container is the closest to what Debian would run.

### CI job proposal

Workflow: `build-test.yml` (it already builds on PRs and master; the Rust and Docker paths
have their own workflows). New job `reproducible`, one Ubuntu runner:

1. Checkout twice into `/home/runner/work/a` and `/home/runner/work/long-path-xxxx/b`.
2. Build A plainly; build B with `umask 002`, `TZ=Asia/Tokyo`, `LANG=C.UTF-8`, a `faketime`
   offset of a day, and `SOURCE_DATE_EPOCH` of the commit in both.
3. `cmp` `unitd`, `libunit.a`, `libnxt.a`, `unit.pc` and every `*.unit.so` that the job
   configured (php and python are cheap on the runner).
4. On a mismatch, run diffoscope (`pip install diffoscope`) and upload the HTML as an artifact.

Cost (estimate, not measured): one plain build is a few minutes on `ubuntu-latest`; two
builds plus the diffoscope install should stay under 10 minutes. Take the real number from
the first run and compare it with the per-workflow minutes in `review-pipeline.md`. Run it on PRs that touch
`auto/`, `configure`, `src/**/Makefile*`, `pkg/` and the workflow, and on every push to
master. Tag pushes get the same job through the `push: tags` trigger that 588 adds to
`lint-whitespace.yml`; the deb job in `build-deb.yml` can add a second `dpkg-buildpackage`
run with a changed path and `cmp` the `.deb` once R2 and R1 are in.

## 6. How distributions check it, and what they report

| Ecosystem | Package | State on 2026-10-08 | Report |
|---|---|---|---|
| Debian, Ubuntu | none | `tracker.debian.org/pkg/{unit,nginx-unit,freeunit}` all 404; `tests.reproducible-builds.org/.../unit.html` 404 | Not packaged, so no report. Method if it were: build twice with `reprotest` variations, compare with diffoscope. dpkg 1.22 sets `SOURCE_DATE_EPOCH` from `debian/changelog` and `-ffile-prefix-map` through `reproducible=+fixfilepath` (https://manpages.debian.org/trixie/dpkg-dev/dpkg-buildflags.1.en.html ). Row 2 is what would break it. |
| Alpine | `community/freeunit` (the maintainer maintains it; a local aports checkout, 1.37.0 in the local branch, 1.36.1 on `gl/master`, 2026-07-24) | `APKBUILD` configures with `--cc=clang-$_clang_ver --hardening=default --tests --njs --otel`, source `https://github.com/freeunitorg/freeunit/archive/refs/tags/$pkgver.tar.gz`. abuild exports `SOURCE_DATE_EPOCH` from the APKBUILD's git commit. `reproducible.alpinelinux.org` did not resolve from here. | No status found. Alpine's rebuilderd compares the apk bit-for-bit; the build path is `/home/buildozer/aports/community/freeunit/src/...`, which is the same on every builder, so row 1 hides there but row 2 does not (`--cc-opt` carries njs cflags only, no path). |
| Arch | AUR `nginx-unit-*` at 1.31.0 (2023-09-17); not in official repos | `reproducible.archlinux.org/api/v0/pkgs/list?name=nginx-unit` returns `[]` | No report. |
| NixOS | `pkgs/by-name/un/unit/package.nix`, version 1.35.0 (upstream nginx/unit), no freeunit | `--control`, `--pid`, `--user`, `--group`; `--openssl`, `--no-ipv6`, `--debug` conditional. Nix builds in `/build` with a fixed name and `SOURCE_DATE_EPOCH=315532800`. | r13y.com returned 403; not checked. Nix's fixed path masks row 1. |
| Fedora, openSUSE | `packages.fedoraproject.org/pkgs/unit/` 404 | remicollet ships `unit` in Remi's repo from a vendored cargo dir (nginx/unit 1537). Fedora 41+ builds with `clamp_mtime_to_source_date_epoch` and `-ffile-prefix-map` in `%optflags`. | No report. Row 2 and row 12 apply. |

Checks each ecosystem applies, with the source: `SOURCE_DATE_EPOCH`
(https://reproducible-builds.org/docs/source-date-epoch/ ), build path via
`-ffile-prefix-map` (https://reproducible-builds.org/docs/build-path/ ), archive
normalisation `tar --sort=name --mtime --owner=0` and `gzip -n`
(https://reproducible-builds.org/docs/archives/ ), JVM jars by `SOURCE_DATE_EPOCH`
(https://reproducible-builds.org/docs/jvm/ ), Docker `rewrite-timestamp`
(https://docs.docker.com/build/ci/github-actions/reproducible-builds/ ).

## 7. Open questions for the maintainer

1. **Should `unitd --version` keep the full configure line?** It is the main debugging aid
   in issues (users paste it). R1 keeps it and only drops prefix-map flags. The alternative,
   dropping `--cc-opt`/`--ld-opt` entirely, is cleaner for reproducibility and loses
   information. Recommendation: keep, strip only path-map flags.
2. **Where does `SOURCE_DATE_EPOCH` come from for a release?** Proposal: the commit time of
   the tag (`git log -1 --format=%ct <tag>`), set by `pkg/Makefile`, `build-deb.yml`,
   `release-docker.yml` and `unitctl.yml`; configure exports it to the `Makefile` when set
   and never invents one.
3. **Docker apt snapshots or not?** Pinning base digests is cheap (R6). Pinning apt to
   `snapshot.debian.org` makes the image reproducible but freezes security updates in
   the base layer until the next release; most projects pin the digest and accept apt drift.
   Recommendation: digests yes, snapshots no, and say so in `pkg/docker/README.md`.
4. **Vendor cargo crates into the release tarball (R8)?** It makes deb and rpm builds
   offline and fixes the crate set, at the cost of a 50 to 100 MB asset per release. The
   Fedora and FreeBSD packagers asked for it (nginx/unit 1537).
5. **Debian packaging upstream.** There is no `freeunit` in Debian. If one is wanted,
   R1 to R3 are the preconditions for a green `tests.reproducible-builds.org` page.
6. **Alpine follow-up.** Once R1 lands in a release, check the apk on the Alpine rebuilder
   and record the result here.

## 8. What was not verified

- No module was built on the build host (no php-config); the measured result covers `unitd`,
  `libunit.a` and `libnxt.a` only. The container `freeunit-test:latest` on the build host has php
  and python; run the script there for R1's PR body.
- npm tarball mtimes (row 18) and `git archive` gzip stability across git versions (row 11)
  were not measured.
- The distro reproducibility sites were partly unreachable from this host (DNS for
  `reproducible.alpinelinux.org`, 403 from r13y.com and gitlab.alpinelinux.org); the Alpine
  facts come from a local aports checkout.
- Whether a published 1.37.0 `.deb` contains the build path in `unitd --version` was not
  checked; by row 2 and `rules.in:65` it should, and one `apt download` plus
  `dpkg-deb -x` settles it.
