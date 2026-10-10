# LTO and PGO for FreeUnit: findings, prototype, plan (2026-10-08)

> **Status 2026-10-08: PARKED by the maintainer** (no viable benefit seen; save and iterate later). Lab jobs 07 to 13 were prepared and validated but never run. Branch `build/lto-option` (3d9a81ca3) is local only and not pushed. To resume: rebase the branch, run jobs 08, 09 and 10 in that order, and continue only if `r_instructions` improves outside the 2.5 percent band.

Lead: Fable 5.1 (stopped by a rate limit); sections 0, 1, 4, 7 and 10 written and the rest checked by Opus 5.5 on the same day.
Source tree: `freeunitorg/freeunit` `origin/master` 856d23b9e (fetched 2026-10-08).
Prototype branch: `build/lto-option` in a scratch worktree (not pushed): 51fb92c21 (build) and 3d9a81ca3 (ci). Nothing was pushed, posted or merged.
Every `file:line` below is at 856d23b9e unless a branch is named. Check with `git show 856d23b9e:<path> | sed -n 'Np'`.

## 0. Answer in two sentences

Land `./configure --lto` (opt-in, default off) and the hook-gate fix in 1.38.0, and change no shipped build in 1.37.1; the gate fix matters already, because Ubuntu, Fedora and EL9 package builds are fat-LTO builds through vendor flags today. Turn LTO on in the Docker images, change the `-O` default or start PGO only after the lab jobs of section 8 show a gain outside the 2.5% null band; no throughput number exists yet.

## 1. Findings table

| # | Claim | Verified | Evidence |
|---|---|---|---|
| 1 | Alpine enabled LTO by default | no | abuild `default.conf` has `-Os`, no `-flto`; only Rust gets `CARGO_PROFILE_RELEASE_LTO=true` (section 3.1, re-read 2026-10-08) |
| 2 | The Alpine 1.37.0 package is LTO-built | no package exists | 1.37.0-r0 is unpublished; 1.36.1-r0 `libunit.a` members are plain ELF, not bitcode (section 3.2) |
| 3 | Ubuntu, Fedora and EL9 package builds of FreeUnit are fat-LTO builds today | yes, by inference | vendor flags reach `--cc-opt` (`pkg/deb/debian/rules.in`, `pkg/rpm/unit.spec.in:20`); flags re-measured in ubuntu:26.04 and almalinux:9 containers; no actual Ubuntu or Fedora build log of Unit exists (section 3.3) |
| 4 | Docker images and the trixie deb are not LTO builds | yes | Debian `dpkg-buildflags --status`: `lto=no` (re-measured in debian:trixie) |
| 5 | Source builds are `-O1` | yes | `auto/cc/test:74,118`; the reason for keeping `-O2` off is void (section 2.1) |
| 6 | The hook gate misses a leak on a fat-object build | yes | planted leak: 856d23b9e gate exit 0 with 54 hook symbols, new gate exit 1 (re-run 2026-10-08 on the build host) |
| 7 | gcc links fat objects with LTO even without `-flto` on the link line | yes | two-file probe, gcc 15.2 (section 2.3, re-run 2026-10-08) |
| 8 | LTO makes FreeUnit faster | not measured | only sizes (unitd text -3.1%); jobs 07 to 13 are prepared, not run |
| 9 | No distribution builds C packages with PGO by default | yes, for the 9 rows of section 3.1 | per-package PGO examples in section 3.1 have no source (section 10) |

## 2. What the build does today

### 2.1 Flags of a source build

- `auto/cc/test:74` (gcc) and `:118` (clang) set plain `-O`, which is `-O1`. `-O2` is commented out at `:83` and `:125` with the note "-O2 enables -fstrict-aliasing and -fstrict-overflow". That reason no longer applies: `:68` and `:112` pass `-fno-strict-overflow`, and `:84` and `:127` pass `-fno-strict-aliasing` unconditionally, so `-O2` would not enable either.
- Other flags: `-pipe -fPIC -fvisibility=hidden -funsigned-char -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Wwrite-strings -Wmissing-prototypes -Werror -g`; clang adds `-Wstrict-overflow=5`.
- `auto/make:24`: `CFLAGS = $NXT_CFLAGS $NXT_CC_OPT $CFLAGS $(EXTRA_CFLAGS)`. `--cc-opt` and an environment `CFLAGS` at configure time come after `-O`, so a distro `-O2` wins. `--ld-opt` fills `NXT_LD_OPT` on the two link macros (`auto/make:26-27`). Every link line also carries `$(CFLAGS)` (`auto/make:483` for unitd, `:354-426` for the test programs), so a flag in CFLAGS reaches compile and link.
- No assembly sources (`git ls-files | grep -E '\.(S|s)$'` is empty).
- `--tests` (`auto/make:190-265`) compiles every core and libunit source a second time with `-DNXT_TESTS=1` into `X.test.o` and archives `libnxt-tests.a` and `libunit-tests.a`; the test programs link only those (`:340-430`).
- The hook gate `.github/scripts/check-test-hooks.sh` derives hook symbols with `nm` from `X.test.o` minus `X.o`, strips debug info with `objcopy` and compares `X.o` to `X.test.o` with `cmp` (check 1), requires the hooks absent from unitd, `libnxt.a`, `libunit.a`, `nxt_unit.o` and every module (check 2), and present in the test programs that linked that object (check 3).
- Modules compile their source with `$(CFLAGS)` plus the language's own flags and link `build/src/nxt_unit.o` (`auto/modules/php:604,618`). `libunit.a` is installed for external applications (`auto/make:580-605`) and `unit.pc` carries `@CFLAGS@` (`:632,643`). The Rust `wasm-wasi-component` build script links `build/src/nxt_unit.o`; the Node module links `libunit.a` through node-gyp. So libunit objects are consumed by foreign compilers and by links without `-flto`: they must stay usable as plain objects (fat LTO objects) or stay non-LTO.

### 2.2 Where the shipped builds get their flags (measured 2026-10-08 in containers, docker 29.8.2)

| Channel | Flag source | -O | LTO today |
|---|---|---|---|
| Source build `./configure` | `auto/cc/test` | `-O` | no |
| Docker images, `minimal` included (`pkg/docker/Dockerfile.php-8.3:43-44` and siblings): `dpkg-buildflags --get CFLAGS` with `hardening=+all,-pie` and `-fPIC` appended, `-specs=` lines removed; unitd is built at `:65-70` with `--cc-opt="$CC_OPT" --ld-opt="$LD_OPT"`, the modules at `:74-80` with `--cc-opt` only; base `debian:trixie-slim` or `php:*-cli-trixie` | Debian vendor: `-g -O2 -Werror=implicit-function-declaration -ffile-prefix-map=/=. -fstack-protector-strong -fstack-clash-protection -Wformat -Werror=format-security -fcf-protection -fPIC`; LDFLAGS `-Wl,-z,relro -Wl,-z,now -Wl,--as-needed -pie` | `-O2` | no |
| `pkg/deb` on Debian trixie (`debian/rules.in:8-14,60-66`: `DPKG_EXPORT_BUILDFLAGS=1`, `CFLAGS= ./configure --cc-opt="$(CFLAGS)" --ld-opt="$(LDFLAGS)"`); the only deb CI is trixie (`build-deb.yml`) | Debian vendor, as above | `-O2` | no |
| `pkg/deb` on Ubuntu noble (dpkg 1.22.6) | Ubuntu vendor: `-g -O2 -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer -ffile-prefix-map=/=. -flto=auto -ffat-lto-objects -fstack-protector-strong -fstack-clash-protection -Wformat -Werror=format-security -fcf-protection`; LDFLAGS `-Wl,-Bsymbolic-functions -flto=auto -ffat-lto-objects -Wl,-z,relro` | `-O2` | yes, fat |
| `pkg/deb` on Ubuntu jammy (dpkg 1.21.1) | same; `-flto=auto -ffat-lto-objects` appears twice | `-O2` | yes, fat |
| `pkg/rpm` (`unit.spec.in:20` `CC_OPT %{optflags} -fPIC`; `:21` `LD_OPT -Wl,-z,relro -Wl,-z,now -pie`) on Fedora 44 (redhat-rpm-config-344) | `%{optflags}` = `-O2 -flto=auto -ffat-lto-objects -fexceptions -g -grecord-gcc-switches -pipe -Wall ... -D_FORTIFY_SOURCE=3 ... -fcf-protection -mtls-dialect=gnu2 -fno-omit-frame-pointer`; `%_lto_cflags` = `-flto=auto -ffat-lto-objects` | `-O2` | yes, fat |
| `pkg/rpm` on AlmaLinux 9.8 (redhat-rpm-config-210) | `-O2 -flto=auto -ffat-lto-objects ... -march=x86-64-v2` | `-O2` | yes, fat |
| `pkg/rpm` on AlmaLinux 8.10 (redhat-rpm-config-131) | `-O2 -g -pipe ...`; `%_lto_cflags` empty | `-O2` | no |
| Alpine `community/freeunit` (`--cc=clang-22 --hardening=default --tests`, no `-flto` in the APKBUILD; published: 1.36.1-r0, the 1.37.0 bump is local and unpublished) | abuild `default.conf`: `-Os -fstack-clash-protection -Wformat -Werror=format-security`, plus `-fno-plt` on x86_64 from the abuild APKBUILD; abuild LDFLAGS never reach unitd (configure ignores `LDFLAGS`), see section 3.2 | `-Os` | no |

Notes:
- `pkg/rpm/Makefile:145` and `pkg/deb/Makefile:172` pass `--tests` to configure, so on Ubuntu and Fedora the `X.test.o` objects are fat LTO objects too. Packaging never runs `build/tests` or the hook gate.
- No rpm is built in CI. `packages.freeunit.org` serves contrib tarballs only (njs, wasmtime, wasi-sysroot, libunit-wasm); `/fedora/`, `/centos/`, `/ubuntu/`, `/debian/` and `/alpine/` answer 404 (2026-10-08). The channels users get are the Docker images, the trixie deb artifacts, Alpine and source builds. None of those four is an LTO build today. Only the source build is an `-O1` build; Alpine is `-Os`.
- Correction (Opus 5.5, 2026-10-08): an earlier draft listed `Dockerfile.minimal` as an `-O1` build because of the bare `./configure` at `:75` and `:79`. That configure only feeds `make version` for the empty module step. unitd of the minimal image is built at `:65-70` with the Debian `-O2` flags, as in every other image.
- The link line gets `-flto=auto` from CFLAGS even where `LD_OPT` has none (rpm), because every link uses `$(CFLAGS)`.

### 2.3 Measured link behaviour of fat objects (laptop, gcc 15.2, clang 21.1, two-file probe)

| Compiler, compile flags | link without `-flto` | link with the LTO flag | link with `-fno-lto` |
|---|---|---|---|
| gcc `-flto=auto -ffat-lto-objects` | LTO link (helper inlined, symbol gone): the plugin claims fat objects | LTO link | plain link |
| clang `-flto=thin -ffat-lto-objects` (`.llvm.lto` section) | plain link | LTO link (helper inlined, symbol kept) | plain link |

Consequence: under gcc, keeping the test programs non-LTO needs `-fno-lto` on their link lines or LTO flags kept out of their rules; it is not enough to compile the test objects without `-flto`. Any plain `cc` link of the fat `libunit.a` (external apps, the lab's `capp`) is an LTO link under gcc; that is correct code, only slower to link.

### 2.4 Precedent in the archives (nginx/unit, offline dump)

- https://github.com/nginx/unit/issues/467 (2020-08-18, "Build error on upcoming Fedora 33"): the first Fedora LTO-by-default build broke on `-Werror=maybe-uninitialized` in `src/nxt_http_route.c:854`, reproduced by VBart with gcc 10.1.0 and `-O2 -flto`; judged a false positive. LTO moves warnings to the link step and changes which ones fire. With `-Werror` this is a build break class, so an LTO CI leg is the only guard.
- https://github.com/nginx/unit/issues/563 (2021-07-04): the python module failed to link against a conda `libpython` built with LTO by another gcc; workaround `--ld-opt="-fno-lto"`. A foreign static library with slim LTO objects from a different compiler version fails at link whatever FreeUnit does; `--lto` does not make it worse, but the option text should name `--ld-opt=-fno-lto` as the escape.

## 3. Distribution defaults (Sonnet research agent, 2026-10-08; primary sources only; containers pulled with `--platform linux/amd64`)

### 3.1 Defaults for C packages

| Distro | LTO default | Exact flags | Since | Fat | `-fno-semantic-interposition` | `-fno-plt` | -O | PGO default | Source |
|---|---|---|---|---|---|---|---|---|---|
| Alpine edge, 3.22, 3.23, 3.24 (abuild 3.15.0 to 3.18.0_rc5, identical flag lines) | no (Rust release profile only: `CARGO_PROFILE_RELEASE_LTO=true`) | CFLAGS `-Os -fstack-clash-protection -Wformat -Werror=format-security` plus `-fno-plt` on x86_64; LDFLAGS `-Wl,--as-needed,-O1,--sort-common -Wl,-z,pack-relative-relocs` | never for C (`git log -S flto` on the abuild repo is empty) | n/a | no | x86_64 only | `-Os` | no | https://gitlab.alpinelinux.org/alpine/abuild/-/blob/master/default.conf ; https://gitlab.alpinelinux.org/alpine/aports/-/blob/master/main/abuild/APKBUILD |
| Debian trixie (dpkg 1.22.22), unstable (1.23.11) | no (`dpkg-buildflags --status`: `optimize features: lto=no`) | `-g -O2 -Werror=implicit-function-declaration -ffile-prefix-map=/=. -fstack-protector-strong -fstack-clash-protection -Wformat -Werror=format-security -fcf-protection`; opt-in `optimize=+lto` adds `-flto=auto -ffat-lto-objects` | never (feature since dpkg 1.21.0) | when opted in | no | no | -O2 | no | https://salsa.debian.org/dpkg-team/dpkg/-/blob/main/scripts/Dpkg/Vendor/Debian.pm ; https://wiki.debian.org/ToolChain/LTO |
| Ubuntu 22.04, 24.04, 26.04 | yes on amd64, arm64, ppc64el, s390x; not riscv64, armhf; per-package opt-out list `lto-disabled-list` | `-flto=auto -ffat-lto-objects` in CFLAGS and LDFLAGS (section 2.2); LDFLAGS also `-Wl,-Bsymbolic-functions` | 21.04 (dpkg 1.20.7.1ubuntu4, 2021-03-19) | yes | no | no | -O2 (ppc64el -O3) | no | https://salsa.debian.org/dpkg-team/dpkg/-/blob/main/scripts/Dpkg/Vendor/Ubuntu.pm ; https://github.com/ubuntu/wiki-archives/blob/main/UbuntuWiki/T/ToolChain-LTO.wiki ; https://lists.ubuntu.com/archives/ubuntu-devel/2021-March/041469.html |
| Fedora 44, rawhide (redhat-rpm-config 344/345) | yes (opt-out `%define _lto_cflags %{nil}`; riscv64 excluded) | gcc `-flto=auto -ffat-lto-objects`; clang `-flto=thin -ffat-lto-objects`; `%_general_options -O2 %{?_lto_cflags} -fexceptions -g -grecord-gcc-switches -pipe`; FORTIFY 3; frame pointers on | Fedora 33 (commit 5baaf4a9, 2020-07-21); clang thin in F40 | yes ("out of an abundance of caution") | no | no | -O2 | no (CPython, GCC, Firefox are per-package PGO) | https://src.fedoraproject.org/rpms/redhat-rpm-config/blob/rawhide/f/macros ; https://fedoraproject.org/wiki/LTOByDefault |
| RHEL 9 (c9s, redhat-rpm-config 211), RHEL 10 (c10s) | yes | as Fedora: gcc `-flto=auto -ffat-lto-objects`, clang `-flto=thin -ffat-lto-objects`; `-march=x86-64-v2` on 9 | from the RHEL 9 branch start (GA release unverified); RHEL 8 has none (measured: `%_lto_cflags` empty on AlmaLinux 8.10) | yes | no | no | -O2 | no | https://gitlab.com/redhat/centos-stream/rpms/redhat-rpm-config/-/blob/c9s/macros ; https://gitlab.com/redhat/centos-stream/rpms/redhat-rpm-config/-/blob/c10s/macros |
| Arch (pacman 7.1, devtools config) | yes (`OPTIONS=(... lto)`) | `LTOFLAGS="-flto=auto"`; CFLAGS `-march=x86-64 -mtune=generic -O2 -pipe -fno-plt -fexceptions -Wp,-D_FORTIFY_SOURCE=3 -Wformat -Werror=format-security -fstack-clash-protection -fcf-protection -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer` | official packages 2021-11-28 (devtools 16b7a0e7, RFC 0004); `-flto=auto` 2022-03-07 | no | no | yes | -O2 | no | https://gitlab.archlinux.org/archlinux/packaging/packages/pacman/-/blob/main/makepkg.conf ; https://gitlab.archlinux.org/archlinux/rfcs/-/blob/master/rfcs/0004-lto-by-default.md |
| openSUSE Tumbleweed | yes | `%_lto_cflags -flto=auto` in Optflags and `%build_ldflags` | 2019 (rpm-config-SUSE 2019-10-29 and 2019-11-06; exact Tumbleweed date unverified, the openSUSE wiki returned 403) | no | no | no | -O2 | no | https://api.opensuse.org/public/source/openSUSE:Factory/_config ; https://github.com/openSUSE/rpm-config-SUSE/blob/master/suse/macros |
| Gentoo (amd64 profile 23.0) | no, user choice (`-flto` or `-flto=thin` in COMMON_FLAGS plus `USE=lto`) | `CFLAGS="-O2 -pipe"`; LDFLAGS `-Wl,-O1 -Wl,--as-needed -Wl,-z,pack-relative-relocs` | n/a | user | no | no | -O2 | no | https://github.com/gentoo/gentoo/blob/master/profiles/default/linux/amd64/23.0/make.defaults ; https://wiki.gentoo.org/wiki/LTO |
| NixOS (nixpkgs master, 2026-05-29) | no (no `lto` in stdenv adapters, make-derivation or cc-wrapper; `knownHardeningFlags` has no LTO entry) | the `fortify` hardening flag puts `-O2 -U_FORTIFY_SOURCE` before the package's flags | never | n/a | no | no | package decides, -O2 floor | no | https://github.com/NixOS/nixpkgs/blob/master/pkgs/stdenv/generic/make-derivation.nix ; https://github.com/NixOS/nixpkgs/blob/master/pkgs/build-support/cc-wrapper/add-hardening.sh |

Summary: LTO by default is gcc `-flto=auto` everywhere it is on; fat objects in Fedora, RHEL and Ubuntu (they ship static libraries and strip the IR afterwards: `brp-strip-lto`, `dh_strip`); slim in Arch and openSUSE. No distribution sets `-fno-semantic-interposition`. PGO is never a default; it is per package (CPython in Fedora, Debian, Arch and Alpine with `--enable-optimizations --with-lto`; Fedora GCC `profiledbootstrap`; Fedora Firefox on x86_64). nginx, httpd, Redis/Valkey, PostgreSQL and HAProxy are not PGO-built in Fedora rawhide or Debian sid.

### 3.2 Alpine and freeunit, the facts

- The brief said "Alpine enabled LTO by default" and "check whether the 1.37.0 Alpine build is LTO-built". Both premises fail. Alpine never enabled LTO for C. `freeunit` 1.37.0-r0 is not published: the apk and the builder log return 404 for edge and v3.24 on 2026-10-08; edge and v3.24 carry `freeunit` 1.36.1-r0; 3.22 and 3.23 carry `unit` 1.35.0. The local aports checkout (`ed8452bbd87a`, 2026-10-05) holds the 1.37.0 bump unpublished.
- What abuild gives a clang build on edge x86_64 (reproduced in `alpine:edge`, abuild 3.18.0_rc5-r1, clang22 22.1.8-r1): `CFLAGS=-Os -fstack-clash-protection -Wformat -Werror=format-security -fno-plt`. With the 1.37.0 tarball (sha512 matches the APKBUILD) and `./configure --cc=clang-22 --hardening=default --tests`, the generated `build/Makefile` CFLAGS end in `... -D_FORTIFY_SOURCE=2 -Os -fstack-clash-protection -Wformat -Werror=format-security -fno-plt`, so the effective level is `-Os`, and `grep -c -i lto build/Makefile` is 0. FORTIFY comes from Alpine's patched compilers (`#define _FORTIFY_SOURCE 2` at `-Os`), not from abuild.
- configure never reads the `LDFLAGS` environment variable (`git grep LDFLAGS` in `auto/` and `configure` finds only module-specific `NXT_*_LDFLAGS`), so abuild's `--as-needed`, `-O1`, `--sort-common` and `-z pack-relative-relocs` never reach `unitd`; the shipped binary has no RELR entry. This is a separate packaging gap, not an LTO item.
- Is the published 1.36.1-r0 LTO-built? No. `freeunit-dev-1.36.1-r0.apk` ships `usr/lib/libunit.a` whose five members are `ELF 64-bit LSB relocatable` with `.comment` `Alpine clang version 22.1.8`; a clang `-flto` object is LLVM bitcode (control: `clang-22 -Os -flto=thin -c a.c` gives `LLVM IR bitcode`), and nothing in the flag chain adds `-ffat-lto-objects`. The builder log (https://build.alpinelinux.org/buildlogs/build-edge-x86_64/community/freeunit/freeunit-1.36.1-r0.log, 2163 lines) has 0 lines with `-flto`, but the make output is quiet, so the log alone proves nothing. `readelf -S` and `.comment` on a stripped binary prove nothing either.
- The Alpine toolchain can do LTO with the aport's compiler: on edge, `clang-22 -Os -flto=thin -c a.c b.c` and `clang-22 -flto=thin a.o b.o` link with GNU ld through `LLVMgold.so` (the driver passes `-plugin .../LLVMgold.so`); `lld` is not needed; `-flto=thin -ffat-lto-objects` is accepted and the object stays ELF; `gcc -flto=auto -ffat-lto-objects` links through `liblto_plugin.so`. Two-file test only; a whole-tree Alpine LTO build was not run.

### 3.3 Precedent for this code under distro LTO

- Debian and Ubuntu have no `unit` source package (sources.debian.org 404; Launchpad "no current release"). The only Launchpad item is the `freeunit-org` PPA with `unit 1.35.3+git202604271804-0~ppa2~ubuntu24.04.1`, whose two builds failed at `debian/rules clean` before any compile. So there is no observed Ubuntu LTO build of Unit; section 2.2 is an inference from `pkg/deb/debian/rules.in`.
- Closest evidence that a server of this style builds under Ubuntu's gcc LTO at scale: nginx 1.28.3-2ubuntu1.11 on Ubuntu 26.04, 239 compile lines with `-flto=auto -ffat-lto-objects`, gcc 15.2.0-16ubuntu1 (https://launchpad.net/~ubuntu-security-proposed/+archive/ubuntu/ppa/+build/33580480/+files/buildlog_ubuntu-resolute-amd64.nginx_1.28.3-2ubuntu1.11_BUILDING.txt.gz); its riscv64 twin has none, as `Ubuntu.pm` predicts.
- In this tree: https://github.com/nginx/unit/issues/467 (section 2.4) is the one recorded LTO breakage, a `-Werror=maybe-uninitialized` false positive under gcc 10.1 `-O2 -flto`.

## 4. What LTO and PGO buy: literature and the measurement design

The literature pass was not done: the lead agent stopped before it. No number in this plan comes from published benchmarks, and the "expected magnitudes" in section 8 are guesses written before any run. Two facts from primary sources:

- gcc honours per-file options at the LTO link: "Command-line optimization and target options are now streamed on a per-function basis and honored by the link-time optimizer" (GCC 5, https://gcc.gnu.org/gcc-5/changes.html). The GIMPLE producer of the strict LTO unitd carries `-fno-strict-overflow -fno-strict-aliasing` (section 5 evidence), so the two flags that keep FreeUnit's aliasing and overflow semantics reach the LTO back end.
- Measured here: unitd text -3.1% (gcc 13.3, `-O`), python3.unit.so text -4.6%. Size is not speed.

The measurement design is section 8: A/B instruction counts on the perf lab at a fixed SHA, with `-O2` and LTO separated, because every distribution and Docker build is already `-O2` and the source build is `-O1`. PGO needs a training phase that the runner does not have (job 12).

## 5. Prototype: `./configure --lto` (branch `build/lto-option`, Opus agent, verified on the build host)

Commits: `build: add the "./configure --lto" option` and, separately so it can be dropped, `ci: build and test with --lto` (one job `c-tests-lto`: gcc, `--lto --openssl --tests`, both check scripts, `build/tests` and the three libunit tests). Files (`git diff --stat` of 51fb92c21): `auto/cc/lto` (new, 211 lines), `auto/options`, `auto/help`, `auto/summary`, `configure`, `auto/make` (47 lines changed), `.github/scripts/check-test-hooks.sh` (55), `.github/scripts/check-lto.sh` (new, 242 lines); the ci commit adds 44 lines to `build-test.yml`. PR body draft: `pr-body-lto.md` in the session scratch directory; raw evidence: `evidence-lto.md`. Heads: 51fb92c21 (build, includes the `libunit-install` strip and the clang full-LTO mark) and 3d9a81ca3 (ci; 8c99f4c43 with a corrected commit message, same tree).

What it does:
- `--lto` is `--lto=auto`; values `off|auto|thin|full`, default `off`. gcc: auto `-flto=auto` (falls back to `-flto` on a gcc that rejects `auto`), full `-flto`, thin is a configure error. clang: auto and thin `-flto=thin`, full `-flto`. Every object also gets `-ffat-lto-objects`.
- Flags go into `NXT_CFLAGS` (every compile; the unitd and test links pass `$(CFLAGS)`), `NXT_MODULE_LINK` and `NXT_SHARED_LOCAL_LINK` (module and libnxt.so links do not pass CFLAGS). Not into `NXT_LD_OPT`, which the Go module hands to `#cgo LDFLAGS` (auto/modules/go:130), where Go's allowlist has no `-flto` form.
- The probe (`auto/cc/lto`) compiles two objects, archives one with `$AR -r -c`, links with LTO, then links again with `-fno-lto`; a compiler that cannot make fat objects (clang before 17, Mach-O) or a linker without the plugin stops configure. It runs after every feature probe, so `nxt_auto_config.h` and the module probes are unchanged.
- The nine test program links get `-fno-lto` (auto/make). Reason, measured: under gcc a link of fat objects is an LTO link even without `-flto` on the link line; an LTO link can inline a hook away, which breaks check 3 of the hook gate.
- Deviation from the first design: the `X.test.o` objects keep the LTO flags. gcc 13.3 emits different machine code for `src/nxt_unit.c` in a fat object (an out-of-line `nxt_unit_process_find()`), so check 1 of the gate (`cmp` of `X.o` and `X.test.o`) is only valid when both objects have the same flags.
- No slim mode and no `ar` probing: slim objects would break `libunit.a` for Go, Node.js, the wasm-wasi-component build and the gate's `objcopy`; `-ffat-lto-objects` is required.
- Gate fixes that are needed on any fat-object build, including an Ubuntu or Fedora package build with `--tests` today: GNU `nm` auto-loads the LTO plugin and lists only the IR globals of a fat object (59 hook symbols instead of 79), so `nm` now gets `--target=<format>` from `objdump -f`; check 1 removes `.gnu.lto_*`, `.gnu.debuglto_*` and `.llvm.lto` before `cmp` (gcc gives those sections a random suffix per compile, so two compiles never compared equal, and the old gate passed an LTO build with a planted leak). On a non-LTO build the new gate's output is byte-identical to the old one.
- `check-lto.sh`: every non-test object under `build/src` must carry IR and machine code; unitd, `libnxt.so` and every `*.unit.so` must show an LTO link: gcc leaves a "GNU GIMPLE" compile unit in the debug info (the IR sections are `SHF_EXCLUDE` and never reach a binary, so their absence proves nothing); clang ThinLTO leaves `<name>.llvm.<n>` symbols; a clang link with LTO also puts two or more compile units on one DWARF 5 string offsets table, which is the only mark of `--lto=full`. A clang full-LTO program without DWARF 5 string offsets is reported as "not checked". A clang ThinLTO module that imports nothing across modules has neither mark and would be a false error (two-file probe; no clang module build was checked).
- `make libunit-install` of a `--lto` build installs `build/lib/install/libunit.a`, a copy without the IR sections (`objcopy -R`). Before: gcc 15.2 linking the installed gcc 13.3 archive failed with "bytecode stream ... LTO version 13.1 instead of the expected 15.1"; after: the link succeeds and the program runs. The configure probe runs the same objcopy step.
- Without the option, `build/Makefile`, `Makefile`, `build/include/nxt_auto_config.h` and `build/autoconf.data` are `cmp`-identical to 856d23b9e for six argument sets with gcc 15.2 and clang 21.1, and after `./configure php`. Re-checked by the reviewer on the final tree for four sets ("", `--tests --openssl`, `--tests --openssl --hardening=strict`, `--cc=clang --tests --openssl`): all four files the same.

Evidence (the build host, gcc 13.3.0, binutils 2.42, `nice -n 19 taskset -c 6,7 make -j2`):

| Run | `warning:` lines | `build/tests` + 3 libunit tests | check-test-hooks.sh | check-lto.sh |
|---|---|---|---|---|
| `--lto --tests --openssl` | 0 | exit 0 | exit 0, 79 hook symbols | exit 0: 111 of 111 objects carry IR, unitd has the GNU GIMPLE unit |
| control `--tests --openssl` | 0 | exit 0 | exit 0, 79 | exit 1: "111 of 111 objects carry no LTO IR" and "build/sbin/unitd has no "GNU GIMPLE" compile unit" (the failing-before value) |
| `--lto --hardening=strict --tests --openssl` | 0 (no `-Wlto-type-mismatch`, `-Wodr`, lto-wrapper lines) | exit 0 | exit 0 | exit 0 |
| `--cc=clang-18 --lto --tests --openssl` (ubuntu-24.04 container) | 0 | exit 0 | exit 0, 77 | exit 0, 620 ThinLTO symbols |
| LTO build with `-DNXT_TESTS=1` planted on the `nxt_port_socket.o` rule | | | new gate exit 1: `::error::src/nxt_port_socket.c tests NXT_TESTS but build/src/nxt_port_socket.o and build/src/nxt_port_socket.test.o are the same object; the plain object was compiled with the macro`; the 856d23b9e gate exits 0 on the same build (the failing-before value for the gate change) | |
| Python module, `--lto --tests --openssl --debug`, `freeunit-test:latest`, 7 test files | 0 | pytest 257 passed, 16 skipped, exit 0 | | exit 0, GIMPLE unit in `python3.unit.so` |

| Size (bytes) | `--lto` | plain |
|---|---|---|
| unitd text | 527460 | 544098 (-3.1%) |
| python3.unit.so text (`--debug`) | 137533 | 144163 (-4.6%) |
| libnxt.a | 18825164 | 8777780 (fat objects carry code and IR) |
| libunit.a | 1074388 | 536892 |
| `make -j2` wall | 13.4 s | 10.1 s (+33%) |

Laptop cross-check (gcc 15.2, jobs agent, local build of 856d23b9e, 0 warnings in all four variants): unitd text `-O` 506706, `-O2` 540326, `-O` + LTO 490432, `-O2` + LTO 579433 bytes.

Not covered by the prototype: clang 22 on Alpine, macOS (Mach-O has no fat LTO objects, so `--lto` stops at configure there), `--fuzz`, the Go, Node.js, Java, Perl, Ruby, PHP and wasm module builds, clang module builds, throughput (section 10).

## 6. Risks

| # | Risk | What was found | Mitigation in the prototype or plan |
|---|---|---|---|
| 1 | `-Werror` plus warnings that only appear at the LTO link (`-Wmaybe-uninitialized` across TUs, `-Wlto-type-mismatch`, `-Wodr`, `-Wstringop-overflow`) break the build; https://github.com/nginx/unit/issues/467 was exactly this with gcc 10.1 | gcc 13.3 (plain and strict), clang 18.1, gcc 15.2 and gcc 14.2 (lab image) build the tree with 0 warnings under LTO | the `c-tests-lto` job; the weekly `build-distros.yml` jobs should also pass `--lto` to see gcc 16 and clang 23 |
| 2 | Shipped `libunit.a` with IR: a consumer using another gcc major cannot link it (`lto1: bytecode stream ... generated with LTO version 13.1 instead of the expected 15.1`, measured gcc 13.3 objects with gcc 15.2) | real for `make libunit-install` on the first prototype head | done in 51fb92c21: `libunit-install` installs a copy without `.gnu.lto_*`, `.gnu.debuglto_*`, `.llvm.lto`, as Fedora (`brp-strip-lto`) and Ubuntu (`dh_strip`) do; the gcc 15.2 link of the stripped archive succeeds. The Node.js module still links the unstripped `build/lib/libunit.a` (`auto/modules/nodejs:132`); fine with the same compiler, not tested with another |
| 3 | The hook gate is wrong on fat-object builds today (Ubuntu and Fedora package flags with `--tests`): `nm` lists IR globals only, and check 1 never compares equal | measured: 59 vs 79 hook symbols; a planted leak passed the old gate | fixed in the gate; the fix is independent of `--lto` and is worth landing even if the option is not |
| 4 | Test programs under an LTO link lose hook symbols (check 3) | measured on the laptop and on the build host | `-fno-lto` on the nine test links |
| 5 | Foreign static libraries with slim IR from another compiler (https://github.com/nginx/unit/issues/563, conda libpython) fail at the module link | pre-existing, not caused by `--lto` | document `--ld-opt=-fno-lto` as the escape (not in the prototype's help text; a docs item) |
| 6 | UB exposed by cross-TU inlining | the tree compiles with `-fno-strict-aliasing` and `-fno-strict-overflow`; with gcc these are recorded per function in the IR and honoured at the LTO link (GCC 5 notes, section 4; the strict unitd's GIMPLE producer lists both); for clang the same is expected because the flags change the IR at compile time (no TBAA metadata, no `nsw`), not checked; the C suite, the hook gate, the Python smoke and the hardening-strict build pass | ASan and fuzzing legs stay non-LTO (they do not pass `--lto`); any LTO-only crash is first reproduced without `--lto` |
| 7 | Debug info and crash PCs: the project debugs arch-specific crashes by PC | not measured (section 10) | keep `-g` (auto/cc/test adds it); `addr2line -i` names inlined frames |
| 8 | Build time and memory on 2-vCPU runners and under qemu (s390x, armv7, riscv64) | the build host: +33% wall at `-j2`; GNU time max RSS of the whole `make -j2` 68972 KB with LTO, 68052 KB without (largest single process, not the link step alone); qemu not measured | `-flto=auto` uses the jobserver; `--lto` stays opt-in and off in the qemu legs; the weekly distro jobs can afford it |
| 9 | Reproducible builds | not measured (section 10) | `-flto=auto` does not change partitioning; the reproducible-builds plan (`docs/reproducible-builds-plan-2026-10.md`) adds `-ffile-prefix-map` separately |
| 10 | PGO profile staleness and the gcc `.gcda` version lock | no PGO build was made (section 10) | profiles never in-tree; generated in the same CI job that uses them; only for Docker images where the compiler is pinned by the base image |
| 11 | Behaviour change from `-O2` rather than from LTO | distro and Docker builds (minimal included) are `-O2`; source builds are `-O1`; the comment that keeps `-O2` off is void (section 2.1) | jobs 08 and 09 separate the two; a `-O2` default is a separate decision with its own PR |
| 12 | Packaging under `pkg/` | Ubuntu and Fedora/EL9 packages are already fat-LTO builds through vendor flags; Debian and Docker are not; rpm `LD_OPT` has no `-flto` but every link passes CFLAGS | nothing to change for `--lto`; `pkg/docker` is where a default-on LTO would be set (section 7) |
| 13 | Go module with distro `--cc-opt` flags | `#cgo CFLAGS: ${CFLAGS} ${NXT_CC_OPT}` receives Ubuntu's `-flto=auto -ffat-lto-objects`; Go 1.26 `security.go`: `validCompilerFlags` has `-f(no-)?lto` and `-f(no-)?fat-lto-objects` (whole-flag match, so `-flto=auto` fails), `validLinkerFlags` has no LTO flag (re-read at https://github.com/golang/go/blob/go1.26.0/src/cmd/go/internal/work/security.go). Measured with `go build` (prototype agent): `#cgo CFLAGS: -flto=auto` is an invalid flag. `--lto` never reaches cgo (it does not touch `NXT_CC_OPT` or `NXT_LD_OPT`). The deb go package uses `go-install-src`, and the go Docker images use Debian flags, so no shipped build breaks | only a user who runs `make go-install` with Ubuntu flags in `--cc-opt` fails; if that matters, filter `-flto*` and `-ffat-lto-objects` out of the cgo flags in `auto/modules/go` (separate PR) |
| 14 | Sanitizer and fuzzing builds | CI legs `c-tests-asan` and `fuzzing` do not pass `--lto` and keep working unchanged | keep them non-LTO; never add `--lto` to them |

## 7. Recommendation per release, with gates

This section is the one source for what ships when. Section 5 describes the prototype, section 8 the measurements the gates name, section 9 the CI cost.

**1.37.1 (patch release): no change.** No `--lto`, no `-O` change, no Docker or packaging change. A configure option is a feature, and no throughput number exists. The gate fix in `check-test-hooks.sh` has no consumer before an LTO CI job exists (packaging never runs the gate), so it waits for 1.38.0 too.

**1.38.0: land `./configure --lto`, default off, with the gate fix.** The two commits of section 5, as one PR, through the usual review chain (Fable gate, Codex, verify, lease-push). Gates before merge:

1. `c-tests-lto` passes on GitHub, and its real job-minutes are recorded against the 3-minute estimate (section 9). If the cost is not wanted, drop the ci commit and pass `--lto` in the weekly `build-distros.yml` rawhide jobs instead; then a PR can break `--lto` unnoticed for up to a week.
2. Without the option the generated files stay `cmp`-identical to the base (re-run after every rebase; section 5).
3. One module build with `--lto` per link style, each passing `check-lto.sh` and its pytest subset: php (the lab workload), python (done), nodejs (links `build/lib/libunit.a` through node-gyp), go (`go-install-src` and a cgo link of the installed archive). The prototype covered python only.
4. One clang build with modules: `check-lto.sh` on a clang ThinLTO `*.unit.so` either passes or the script gets a "not checked" rule for modules (section 5).

Not in 1.38.0 unless lab results arrive first: LTO in the Docker images. That is the only shipped channel where FreeUnit sets the flags itself (risk 12), and it is set by adding `--lto` to the unitd and module `./configure` lines of `pkg/docker/Dockerfile.*`. Gate: job 09 (`-O2` against `-O2` + LTO, hello) and job 10 (static1k) both show `r_instructions` lower by more than the 2.5% null band, `rsys_*` unchanged, and every image's module smoke passes with `--lto`. If either job gives NO CLAIM, the images stay as they are.

**Later (1.39.0 or after):**

- A `-O2` default for source builds: a separate PR, gated on job 08 (`-O` against `-O2`) and on a warning-free build with gcc 13 to 16 and clang 18 to 23. The `auto/cc/test` comment that keeps `-O2` off is void (section 2.1), but that alone is no reason to change the shipped code generation.
- `--lto` in the weekly rawhide gcc 16 and clang 23 jobs, whatever happens to `c-tests-lto` (risk 1).
- PGO: not before all three hold. The perf runner has a training phase (job 12 is blocked on it). The LTO gate for the Docker images above has passed, because PGO is only worth its build cost on top of LTO. A `--pgo` design trains and consumes the profile in one job with one pinned compiler (risk 10). Scope: Docker images only. No distribution builds C servers with PGO by default (section 3.1), so packagers would not use it.

## 8. Lab jobs, in run order (prepared and validated, not run)

Files: `tools/perf-lab/jobs/07-lto-hello.job` to `13-o2-lto-capp.job` and `jobs/patches/cflags-o2.diff`, `cflags-lto-fat.diff`, `cflags-o2-lto-fat.diff`. Each passed `PERF_JOB_CHECK=1 tools/perf-job.sh <job>` with `CHECK <job>: ok, phases 2, cells '6', 4 blocks, about 13 min of cells` (re-run for all seven on 2026-10-08 after the review; the three patches also pass `patch -p1 --dry-run` on an 856d23b9e unpack) (under `SHORT_JOB_MIN` 15, so they may run by day). Each patch passes `git apply --check` on 856d23b9e. Both sides pin the SHA 856d23b9e; the patches add flags after the `-O` line of `auto/cc/test` (gcc branch for LTO, both branches for `-O2`). The runner applies `PATCH_A`/`PATCH_B` with `patch -p1` to the `git archive` unpack before `./configure` (`remote.sh:151`). The lab image `freeunit-perf-lab:1` is Debian 13 with gcc 14.2.0, binutils 2.44, `liblto_plugin.so` in `/usr/lib/bfd-plugins` and `gcc-ar`.

| Order | Job | A | B | Workload | PRIMARY | Question |
|---|---|---|---|---|---|---|
| 1 | 08-o2-hello | master `-O` | `-O2` | hello c=6 | r_instructions | how much of a distro build's gain is `-O2`, not LTO |
| 2 | 07-lto-hello | master `-O` | `-O` + `-flto=auto -ffat-lto-objects` | hello c=6 | r_instructions | LTO alone at the project's own level |
| 3 | 09-o2-lto-hello | `-O2` | `-O2` + LTO | hello c=6 | r_instructions | the distro-relevant delta (Docker images are `-O2`) |
| 4 | 10-lto-static1k | master `-O` | `-O` + LTO | static1k c=6 | r_instructions | router-only path |
| 5 | 13-o2-lto-capp | `-O2` | `-O2` + LTO | capp c=6 | s_instructions | LTO for libunit in an external C app, both sides at `-O2` |
| 6 | 11-lto-capp | master `-O` | `-O` + LTO | capp c=6 | s_instructions | confounded: the `cc -O2` link of `lab_capp` (`remote.sh:156`) LTO-links the fat `libunit.a` and lifts it to `-O2` in B (lab_capp 66742 to 55133 bytes, its LTO unit records "-O1 -O2"); run after 13 and read it as "-O2 plus LTO for libunit" |
| 7 | 12-pgo-hello | master | blocked | hello | r_instructions | needs a runner training phase; `REV_B=BLOCKED-pgo-needs-runner-training-phase` makes a real run fail with "cannot resolve REV_B"; the header lists the proposed keys `PGO_SIDE`, `PGO_TRAIN`, `PGO_TRAIN_SEC`, `PGO_TRAIN_CONNS` and the steps |

Decision rule for each A/B job: the PERF verdict line of `tools/perf-compare.py` on `r_instructions` (or `s_instructions`), null band 2.5% at 8 s cells (perf-lab.md). A claim needs the delta outside the band; `rsys_*` and `asys_*` must be unchanged (a compiler flag changes no syscall count). `FUNCS` is empty on purpose: LTO and `-O2` rename and clone functions (`.lto_priv`, `.isra`, `.part`), so per-symbol sizes are not comparable; prove the flag reached B afterwards with `readelf -S build/src/nxt_router.o | grep -c gnu.lto` (above 0 in B, 0 in A) and `nm build/sbin/unitd | grep -c lto_priv`. Expected magnitudes are guesses written before any run: 1 to 4% for LTO at `-O` (cross-file inlining of pool, queue and hash helpers), more for `-O2` alone. No literature number backs them (section 4).

Runner change needed for a `--lto` A/B without a source patch: `docs/perf/lto-pgo-runner-configure-args-ab.diff` (not applied) adds optional `CONFIGURE_ARGS_A` and `CONFIGURE_ARGS_B` to `tools/perf-job.sh` and `tools/perf-lab/remote.sh` (default: the shared `CONFIGURE_ARGS`) and makes the tree name and build stamp distinct per side; `patch -p1 --dry-run` is clean, `PERF_JOB_CHECK` on synthetic jobs goes from 1 to 2 phases when B's arguments differ, and existing jobs give unchanged output. The patched `remote.sh` never ran. The key table in `docs/perf/perf-lab.md` section b would need the two new rows.

## 9. CI cost

Baseline (audit of 2026-10-07): `build-test.yml` is 33 jobs and 74.7 execution minutes per run; `c-tests` is about 2 job-minutes. The `c-tests-lto` job in the second prototype commit repeats `c-tests` with `--lto`: the build host measured `make -j2` at 13.4 s against 10.1 s and `make tests` of the same order, so the job is about 3 job-minutes (an estimate: the job has not run on GitHub), under the 20-minute timeout, and adds one runner slot per run. It replaces nothing. It uses `ubuntu-latest` like `c-tests`; the audit's PR 1 pins every job to `ubuntu-24.04` before the 26.04 switch on 2026-10-19, and this job should be pinned with the rest. Options that cost no extra slot: pass `--lto` in the existing weekly `build-distros.yml` Fedora rawhide and Alpine edge jobs (gcc 16, clang 23, musl) where the newest compilers are, and in `analysis-clang-ast.yml` only if the plugin tolerates LTO objects (not checked). Do not add `--lto` to `c-tests-asan`, `sanitize.yml` or `fuzzing.yml`. If a `--pgo` option lands later, its CI job is a train-then-rebuild sequence: two builds plus a short training run, about 6 job-minutes, and it must run in the same job that consumes the profile (gcc `.gcda` files are tied to the gcc major).

## 10. Not verified

### 10.1 Spot checks made by the reviewer (2026-10-08)

| Claim | Result | Source |
|---|---|---|
| `auto/cc/test:68,74,83,84,112,118,125,127` (`-O`, `-O2` commented out, `-fno-strict-*`) | correct | `git show 856d23b9e:auto/cc/test` |
| `auto/make:24,26-28,483,354-426`; `auto/modules/php` compiles with `$(CFLAGS)` and links `nxt_unit.o` | correct | `git show 856d23b9e:auto/make` |
| configure never reads `LDFLAGS` | correct: the only bare `LDFLAGS` in `auto/` and `configure` is the `#cgo LDFLAGS` text at `auto/modules/go:130` | `git grep` at 856d23b9e |
| `Dockerfile.minimal` builds an `-O1` unitd | **wrong, fixed** (section 2.2): unitd is built at `:65-70` with `--cc-opt="$CC_OPT"` | `git show 856d23b9e:pkg/docker/Dockerfile.minimal` |
| Docker flags; Debian trixie `lto=no` | correct; LDFLAGS were incomplete (`-Wl,-z,now -Wl,--as-needed -pie` missing), fixed | `dpkg-buildflags` in `debian:trixie` with the Dockerfile's variables |
| Ubuntu `-flto=auto -ffat-lto-objects` in CFLAGS and LDFLAGS; arch list amd64, arm64, ppc64el, s390x | correct (26.04 measured; arch list in `Ubuntu.pm`) | `ubuntu:26.04` container; https://salsa.debian.org/dpkg-team/dpkg/-/blob/main/scripts/Dpkg/Vendor/Ubuntu.pm |
| EL9 `-O2 -flto=auto -ffat-lto-objects ... -march=x86-64-v2`; EL8 `%_lto_cflags` empty | correct (AlmaLinux 9, redhat-rpm-config-210); EL8 empty only without redhat-rpm-config, not re-checked with it | containers |
| Fedora `_gcc_lto_cflags` / `_clang_lto_cflags`, `%_general_options -O2 %{?_lto_cflags}` | correct | https://src.fedoraproject.org/rpms/redhat-rpm-config/blob/rawhide/f/macros (lines 426-428, 447) |
| Alpine CFLAGS `-Os -fstack-clash-protection -Wformat -Werror=format-security`, `-fno-plt` on x86_64, LDFLAGS `-Wl,--as-needed,-O1,--sort-common` plus `-z pack-relative-relocs` | correct; `-fno-plt` and `pack-relative-relocs` come from the abuild APKBUILD (3.18.0_rc5), not `default.conf` | https://gitlab.alpinelinux.org/alpine/abuild/-/blob/master/default.conf ; https://gitlab.alpinelinux.org/alpine/aports/-/blob/master/main/abuild/APKBUILD |
| Arch `LTOFLAGS="-flto=auto"`, `lto` in `OPTIONS` | correct | https://gitlab.archlinux.org/archlinux/packaging/packages/pacman/-/blob/main/makepkg.conf |
| Section 2.3 link table | correct (gcc 15.2.0, clang 21.1.8, two-file probe) | laptop probe |
| `packages.freeunit.org` serves no distro repositories | correct: `/` 200, `/fedora/ /ubuntu/ /debian/ /alpine/ /centos/` 404 | curl |
| Go `validLinkerFlags` has no LTO flag | correct | https://github.com/golang/go/blob/go1.26.0/src/cmd/go/internal/work/security.go |
| Planted-leak failing value of the gate change | reproduced: 856d23b9e gate exit 0 with 54 hook symbols, new gate exit 1 with the check 1 error and 73 symbols | the build host, `$BUILD_ROOT/lto-proto-lto-mut`, outputs in `$BUILD_ROOT/lto-proto-runs/recheck-1008/` |
| No generated-file change without `--lto` | reproduced for 4 argument sets (section 5) | laptop configure only |

### 10.2 Claims with no primary source or no measurement behind them

- Throughput or latency of any LTO, `-O2` or PGO build. None was measured; jobs 07 to 13 have not run. The 1 to 4% guess in section 8 is a guess.
- Section 4 literature: not done. The clang half of risk 6 (aliasing and overflow semantics survive the LTO link because they are encoded in the IR) is reasoning, not a test.
- Section 3.1: "Since" dates for openSUSE (the wiki returned 403) and the RHEL 9 GA status (both marked in the table); "No distribution sets `-fno-semantic-interposition`" covers only the nine rows; the per-package PGO list (CPython in Fedora, Debian, Arch and Alpine; Fedora GCC `profiledbootstrap`; Fedora Firefox) and "nginx, httpd, Redis/Valkey, PostgreSQL and HAProxy are not PGO-built" have no URL. Ubuntu jammy "the flags appear twice" was measured by the research agent and not re-checked. Gentoo, NixOS and openSUSE rows were not re-read.
- Section 3.2: the Alpine publish state (404s for 1.37.0-r0) and the `freeunit-dev-1.36.1-r0` member analysis were measured by the research agent on 2026-10-08 and not re-checked.
- Section 2.2: Ubuntu deb and rpm "LTO today" are inferences from the packaging files; no Ubuntu or Fedora build of FreeUnit exists to confirm them (section 3.3).
- Section 2.1: the wasm-wasi-component build script links `build/src/nxt_unit.o` (no line cited).
- Section 5: the clang 17 floor for `-ffat-lto-objects` is from memory; the clang 17 release notes and option reference do not mention the flag. The probe makes the floor irrelevant at build time. Laptop size figures (`-O` 506706, `-O2` 540326, LTO 490432, `-O2` + LTO 579433) were measured by the jobs agent; no artifacts were kept.
- Prototype gaps: no module other than python built with `--lto`; no clang module build; no macOS, `--fuzz`, Alpine clang 22 or cross build. clang module and `libnxt.so` links pass no `-O`, so the LTO back end runs at the plugin default level there (believed `O2`), not at unitd's `-O`; not measured. `objcopy` is called by name, so a cross build with a host-only objcopy stops at the probe; not tested.
- Risk 7 (debug info and `addr2line -i` on LTO crash PCs), risk 9 (reproducibility of two LTO builds, `-j1` against `-j2`), risk 10 (PGO profile staleness): not measured. Risk 8: link-step memory not isolated, qemu legs not measured.
- Section 8: the lab image facts (gcc 14.2.0, binutils 2.44, plugin path) and the `lab_capp` sizes (66742 and 55133 bytes) come from the jobs agent. The runner change `docs/perf/lto-pgo-runner-configure-args-ab.diff` never ran.
- Section 9: the 3 job-minute cost of `c-tests-lto` is an estimate; whether `analysis-clang-ast.yml` tolerates LTO objects was not checked.
