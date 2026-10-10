# FreeUnit CI matrix audit, 2026-10-07

> This is the earlier audit. The current page is [ci-rebalance-2026-10-08.md](ci-rebalance-2026-10-08.md); the status of each recommendation (checked 2026-10-09) is in the Status column of section 3 and section 4 here and of section 6 there.

Scope: `.github/workflows` and `.github/scripts` at `origin/master` 872bf041 (2026-10-07).
Method: workflow text, job logs of master push run 37575481761 and scheduled run 37303026074
(`gh api`), `tools/ci-cost.py`, runner-image readmes, upstream release pages.
Read-only. Nothing was pushed or posted.

Minutes below are job-minutes: each job rounded up to a whole minute, as ci-cost does.
The repository is public, so GitHub bills 0. The real cost is runner queue time (31 min queued
across 33 jobs in run 37575481761) and cache churn.

## Answer in two sentences

PR runs test gcc 13.3.0 and clang 18.1.3 (ubuntu-24.04 image). The newest are gcc 16.2 and
clang 23.1.3, and only the weekly `build-distros.yml` job reaches them (gcc 16.2.1 and
clang 23.1.2 on Fedora rawhide), and that job builds but never runs `build/tests`.

## 1. The matrix as it is

Runner facts, measured in the job logs of 2026-10-07: `ubuntu-latest` is `ubuntu-24.04`
(image 20260927.320 and 20261004.327). Its default compilers are gcc 13.3.0 and, after
`apt install clang`, clang 18.1.3. `macos-15` is arm64 with Apple clang 17.0.0.
Action pins: tags `@v7` for checkout, `@v6` cache, `@v7` upload-artifact, `@v8` download-artifact,
`@v7` setup-python/go/node, `@v6` setup-java, `@v2` setup-php, `@v1` setup-ruby. SHA pins (with a
version comment) in `build-deb.yml`, `rust-version.yml`, `release-docker.yml`, `audit.yml`,
`unitctl.yml` and `claude.yml`.

Every PR workflow uses concurrency group `<workflow>-<PR number or run id>`, cancel on PR only.
`build-distros.yml` has no concurrency block. I did not check `build-deb.yml` and `release-docker.yml`.

| Workflow | Job | Runner | Compiler actually used | Configure flags | What it tests | Trigger and paths |
|---|---|---|---|---|---|---|
| build-test.yml | prepare | ubuntu-latest (24.04) | none | none | builds the leg list from `pkg/eol.json` | PR, push master; configure, auto/, go/, src/, test/, pkg/contrib/, pkg/eol.json, workflow, check-test-hooks.sh |
| build-test.yml | core | ubuntu-latest | gcc 13.3.0 via ccache | `--tests --openssl --njs --zlib --zstd --brotli --otel`, OpenSSL 3.6.5 built from source (3.5.9 since PR 613, merged 2026-10-08 as 97bc5f4a) | one core build, shipped as artifact | same |
| build-test.yml | test x25 (python 3.12/3.13/3.14, java 17/21/25/26/27, php 8.3/8.4/8.5, go 1.25/1.26/1.27, node 20/22/24/26, ruby 3.3/3.4/4.0, perl, unit, wasm, wasm-wasi-component) | ubuntu-latest | gcc 13.3.0 for modules; wasm guest uses apt clang 18 | module `./configure <mod>` | pytest per module, python sharded in 3, `--restart` and unprivileged extras | same |
| build-test.yml | c-tests | ubuntu-latest | gcc 13.3.0 | `--openssl --tests`, distro OpenSSL 3.0.13 | `build/tests`, 3 libunit tests, check-test-hooks.sh | same |
| build-test.yml | c-tests-pcre1 | ubuntu-24.04 (pinned) | gcc 13.3.0 | `--no-pcre2 --tests` | `build/tests` with PCRE 1 | same |
| build-test.yml | c-tests-asan | ubuntu-latest | clang 18.1.3 | `--cc=clang --debug --tests`, ASan+UBSan | `build/tests`, 3 libunit tests, LSan use_stacks=0 | same |
| build-test.yml | openssl4 | ubuntu-latest | gcc 13.3.0 | `--openssl --tests`, OpenSSL 4.0.3 from source, `-DOPENSSL_NO_DEPRECATED` | `build/tests` | same |
| build-test.yml | hardening-strict (gcc) | ubuntu-latest | gcc 13.3.0 | `--hardening=strict --tests --openssl` | flags probed, `build/tests` | same |
| build-test.yml | hardening-strict (clang) | ubuntu-latest | clang 18.1.3 | same, `--cc=clang` | same | same |
| build-test-macos.yml | c-tests-macos | macos-15 (arm64, pinned) | Apple clang 17.0.0 | `--tests --openssl`, Homebrew openssl@3, pcre2 | `build/tests`, 3 libunit tests, 16 KiB pages | PR, push master; configure, auto/, src/, workflow |
| sanitize.yml | sanitize python / php / go | ubuntu-latest | gcc 13.3.0 | `--debug --tests --openssl`, `-fsanitize=address,undefined` | pytest subsets under ASan+UBSan; go leg is `experimental` | PR, push master and `pre-*`; configure, version, auto/, go/, src/, test/, scripts |
| fuzzing.yml | libFuzzer | ubuntu-latest | clang 18.1.3 | `--fuzz=-fsanitize=fuzzer`, ASan+UBSan | 10 fuzz targets, 60 s each | PR only (no push) and dispatch; src/nxt_http*, h1proto*, conf*, utf8*, controller*, unit*, router*, port_memory_int.h, span.h, configure, auto/, fuzzing/ |
| analysis-clang-ast.yml | clang-ast | ubuntu-latest, container `debian:testing` | clang 21.1.8 (Debian) | `--cc=clang --openssl --debug --tests` with ngx-ast plugin | build of all modules, `build/tests` | PR to master, push master; configure, auto/, src/, test/, pkg/contrib/ |
| build-distros.yml | fedora-rawhide gcc / clang | ubuntu-latest, container `fedora:rawhide` | gcc 16.2.1 / clang 23.1.2 | `--openssl --otel --zlib --zstd --brotli --tests` | build of all modules; runs only 2 libunit tests | schedule Mon 04:17 UTC, dispatch |
| build-distros.yml | alpine-edge gcc / clang | container `alpine:edge` | gcc 15.2.0 / clang 23.1.2 | same | build of core, php83, python, perl, ruby, java; 2 libunit tests | same |
| build-distros.yml | alpine-no-linux-headers | container `alpine:edge` | alpine gcc (version not read) | `--openssl --tests` | memfd_create probe | same |
| build-deb.yml | build-trixie, smoke-test x4, package-qa x3, release | ubuntu-latest, container `debian:trixie` | trixie default gcc and `clang` (not read from a log) | pkg/deb Makefile | deb build, install smoke test for php 8.3/8.4/8.5 and python 3.13 | push to current, stable, develop, tags; dispatch |
| release-docker.yml | setup, build x variants x {amd64, arm64}, manifest | ubuntu-latest and ubuntu-24.04-arm | Debian trixie gcc inside the Dockerfiles | pkg/docker Dockerfiles | image build and push, no tests | tags, dispatch |
| security-seccomp.yml | seccomp-af-alg | ubuntu-latest | gcc in `python:3.13-slim-trixie` build | Dockerfile | seccomp and Docker hardening scripts | push (any branch) and PR; src/nxt_capability* and similar, pkg/docker/ |
| security-syscalls.yml | syscalls (glibc, musl) | ubuntu-latest | n/a | `test/syscalls/run-in-docker.sh` | syscall drift, advisory (`continue-on-error`) | PR, push master, dispatch |
| unitctl.yml | test, musl check, build x4, release | ubuntu-latest, macos-latest (= macos-26) | rustc stable | cargo | unitctl build, tests, release assets | PR (unitctl paths), every push to master, tags |
| rust-version.yml, audit.yml | check x8, audit per lock, pins | ubuntu-latest | rustc stable | cargo | MSRV check, cargo-audit | Cargo paths; audit also weekly |
| eol-check.yml, lint-whitespace.yml, claude.yml | one job each | ubuntu-latest | n/a | n/a | EOL JSON validator (weekly), whitespace, claude bot | see files |

Cost of one build-test.yml run (run 37575481761): 33 jobs, 74.7 execution minutes, 90 job-minutes
rounded. The 25 language legs are 67.3 execution minutes (77 rounded). The harness table in
`docs/review-pipeline.md` says 69 of 75; the two measurements agree within rounding.

## 2. Compilers

### Newest releases today

| Compiler | Newest | Date | Source |
|---|---|---|---|
| gcc | 16.2 (15.3, 14.4, 13.5 still maintained) | 2026-08-07 | https://gcc.gnu.org/releases.html |
| clang | 23.1.3 (22.1.8, 21.1.8, 20.1.8) | 2026-10-06 | https://github.com/llvm/llvm-project/releases |

### What CI runs

| Leg | gcc | clang | Distance from newest |
|---|---|---|---|
| All PR legs on the runner image | 13.3.0 | 18.1.3 | gcc: 3 majors. clang: 5 majors |
| analysis-clang-ast (debian:testing) | none | 21.1.8 | 2 majors |
| macOS | none | Apple clang 17.0.0 | not comparable |
| build-distros, weekly | 16.2.1 (Fedora), 15.2.0 (Alpine) | 23.1.2 (both) | newest, one patch behind |

Answers:
- Does the strict-hardening leg use the newest clang? No. It uses clang 18.1.3 (job 112643301531).
- Does any leg use the newest gcc? Only the weekly Fedora rawhide job (gcc 16.2.1). It does not
  run `build/tests`: the workflow has `make tests`, `unit_close_test` and `unit_port_recv_test`
  and nothing else, and the log has `LD build/tests` only.
- Is there a floor leg? No. The packaging targets imply the floor. `pkg/rpm/Makefile` builds for
  centos8 (RHEL 8: gcc 8.5.0, OpenSSL 1.1.1k, pcre2 10.32, measured today in `almalinux:8`) and
  amazonlinux2 (an older gcc than 8.5, not measured; OpenSSL via openssl11-devel). `pkg/deb/Makefile` lists focal,
  jammy, bullseye, bookworm, trixie and noble. No workflow in this repository builds rpm at all,
  and the only deb build is trixie. `auto/ssltls` declares OpenSSL 1.1.1 as the floor and no
  leg builds against it. The compile flags are fixed by `auto/cc/test`: `-std=gnu11`,
  `-Wall -Wextra -Wmissing-prototypes -Werror` always on, so any new warning breaks the build.

### What a gcc 16 or clang 23 PR leg would add

The weekly rawhide job built the tree on 2026-10-05 with `-Werror` and passed on both. So the code
is clean today. What a PR leg adds is time and execution:
1. A new warning is caught in the PR, not up to 7 days later on the Monday job.
2. The tests run. The weekly job never executes `build/tests`, so a new optimizer that
   miscompiles or exploits undefined behavior is not tested. Known examples: clang 21 deletes
   null-pointer arithmetic more aggressively
   (https://releases.llvm.org/21.1.0/tools/clang/docs/ReleaseNotes.html); gcc 15 no longer
   clears union padding for `{0}` (https://gcc.gnu.org/gcc-15/changes.html).
3. Warnings that are in `-Wall` or `-Wextra` and are new:

| Compiler | Change that can fail `-Werror` | Source |
|---|---|---|
| gcc 15 | `-Wunterminated-string-initialization` in `-Wextra` (C): `char a[3] = "foo";` | https://gcc.gnu.org/onlinedocs/gcc-15.2.0/gcc/Warning-Options.html |
| gcc 16 | `-Wunused-but-set-variable=3` is the default: a variable only changed by `++` or `+=` now warns | https://gcc.gnu.org/gcc-16/porting_to.html |
| clang 21 | `-Wunterminated-string-initialization` in `-Wextra`; `-Wcast-function-type-mismatch` in `-Wextra` | https://releases.llvm.org/21.1.0/tools/clang/docs/ReleaseNotes.html |
| clang 22 | `-Wincompatible-pointer-types` is an error by default; new `-Walloc-size` | https://releases.llvm.org/22.1.0/tools/clang/docs/ReleaseNotes.html |
| clang 23 | `-Wunused-but-set-variable` covers file-scope statics; `-Wsizeof-pointer-memaccess` covers `snprintf`; `-Wunused-template` in `-Wall` | https://releases.llvm.org/23.1.0/tools/clang/docs/ReleaseNotes.html |

Not an issue for this tree: gcc 15 switched the default to `-std=gnu23`, but `auto/cc/test` forces
`-std=gnu11`. `-fhardened` (gcc 14 and later,
https://gcc.gnu.org/onlinedocs/gcc-16.2.0/gcc/Program-Instrumentation-Options.html) is not probed by
`auto/cc/hardening`; the strict leg tests the separate flags only. The release notes of clang 20 to 23 have no new
hardening flag and no new `-Wimplicit-fallthrough` or `-Wstrict-prototypes` change. The rawhide job
already passes the first row of gcc 15 and gcc 16 (it compiled).

### Recommended compiler matrix (one of each, no more)

| Slot | Version | Where | Config | Replaces |
|---|---|---|---|---|
| newest gcc | 16.2.1 | container `fedora:45` (gcc 16.2.1-2.fc45, https://packages.fedoraproject.org/pkgs/gcc/gcc/) | `--hardening=strict --tests --openssl`, run `build/tests` | hardening-strict (gcc) on the runner image |
| newest clang | 23.1.2 | same container (clang 23.1.2-2.fc45, https://packages.fedoraproject.org/pkgs/llvm/clang/) | same with `--cc=clang` | hardening-strict (clang) on the runner image |
| floor | gcc 8.5.0, OpenSSL 1.1.1k, pcre2 10.32 | container `almalinux:8` (tag updated 2026-10-02) | `--openssl --tests`, run `build/tests` | adds one leg; also covers the OpenSSL 1.1.1 floor |

Keep `c-tests` (gcc 13.3, default flags, distro OpenSSL 3.0.13) and `c-tests-asan` (clang 18)
on a pinned `ubuntu-24.04`: they are the "what a distro ships" and sanitizer legs. The weekly
rawhide job stays as the canary for the next release. Net change: +1 leg, about +2 job-minutes per run.
The alternative to Fedora is apt.llvm.org clang-23 and the official `gcc:16.2` image
(https://hub.docker.com/_/gcc, tag exists). Fedora gives both in one image.

## 3. Outdated or redundant

| # | Finding | Evidence | Action | Saves per run | Status |
|---|---|---|---|---|---|
| 1 | `ubuntu-latest` flips to Ubuntu 26.04 from 2026-10-19 to 2026-11-19 | https://github.com/actions/runner-images/issues/14748; 26.04 image has gcc 13.4, 14.3, 15.2 and clang 20, 21, 22.1.2 (https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2604-Readme.md); `c-tests-pcre1` already pins 24.04 because 26.04 lacks libpcre3-dev | Pin `ubuntu-24.04` in every `runs-on`, move to 26.04 on a date you choose | 0 | see section 4 row 1 (obsolete) |
| 2 | OpenSSL 3.6.5 slot is non-LTS, EOL 2026-11-01 | `build-test.yml` env `OPENSSL_VERSION: 3.6.5`; https://openssl-library.org/policies/releasestrat/ ; 3.5.9 is LTS to 2030-04-08 | Build 3.5.x in the core job. The slot name `/opt/openssl-3.6` and `OPENSSL36_*` must be renamed. Update `tested_ceiling` in `pkg/eol.json` | 0 | done, https://github.com/freeunitorg/freeunit/pull/613 |
| 3 | Three legs for runtimes past upstream EOL: go-1.25 (EOL 2026-08-19), java-26 (2026-09-22), node-20 (2026-04-30) | `prepare` takes every entry of `pkg/eol.json`, no filter; eol.json marks go-1.25 and java-26 `"note": "EOL"` | Filter EOL entries in the jq (keep images). eol.json policy gives 12 months of support after upstream EOL, so this is a policy call | 2+3+2 = 7 | superseded by https://github.com/freeunitorg/freeunit/pull/643 (open) |
| 4 | ccache in all 24 non-core legs. The legs build only a module; `core` builds the C objects | Comment at "Cache ccache" says it serves "njs, unit and the OpenSSL build"; that is now `core`. Repo cache is 10.84 GB in 525 entries, 8.10 GB of it (75%) in 508 ccache entries keyed with `run_id` | Remove the ccache steps and the `ccache` package from the `test` job. Keep it in `core` | about 2, plus 7 GB of cache for OpenSSL, njs, wasmtime, cargo | done, https://github.com/freeunitorg/freeunit/pull/611 |
| 5 | `unitctl.yml` runs 3 jobs on every master push | `on.push.branches: [master]` has no `paths`; tags still run | Add the `pull_request` paths under `push` | 15 per master push (41 pushes in 7 days) | done, https://github.com/freeunitorg/freeunit/pull/611 |
| 6 | `security-seccomp.yml` `push:` has no `branches` | PR branches run it twice | Add `branches: [master]` | 4 to 5 per extra run (30 in 7 days) | done, https://github.com/freeunitorg/freeunit/pull/611 |
| 7 | "Kill mono process" step does nothing on 24.04 | job 112643494068 log: `Unit mono-xsp4.service could not be found.`; references runner-images issue 2821 | Delete the step in `test` | under 1 | done, https://github.com/freeunitorg/freeunit/pull/611 |
| 8 | Draft PR pushes run the full matrix | harness `docs/review-pipeline.md`: about 20 of 75 execution minutes needed | Reduced matrix on drafts, full on `ready_for_review` | about 55 per draft push | superseded by https://github.com/freeunitorg/freeunit/pull/643 (open) |
| 9 | `sanitize.yml` go leg cannot fail | `experimental: true` sets `continue-on-error` on configure, make, run and report steps; the log shows 23 PASSED tests | Make it real or remove it | 3 if removed | open |
| 10 | `c-tests-pcre1` exists for the PCRE 1 fallback only | all `pkg/rpm` and `pkg/deb` specs need pcre2; the leg needs a pinned old image | Source decision: drop PCRE 1 support, then the leg | 2 | open |
| 11 | Single-value matrix axis `os: [ubuntu-latest]` | `build-test.yml` `test` job; leg names carry the suffix | Cosmetic. Branch `master` has no protection and the one ruleset is disabled, so renames break nothing today | 0 | done, https://github.com/freeunitorg/freeunit/pull/611 (check names are `test (<build>)`) |
| 12 | Gaps, not savings: perl is built only against the runner's system perl (5.38, upstream EOL 2026-07-02); no OpenSSL 1.1.1 or LibreSSL leg; Linux is x86-64 only although `ubuntu-24.04-arm` is free for public repos and already used by `release-docker.yml` | `prepare` comment; runner label list at https://docs.github.com/en/actions/reference/runners/github-hosted-runners | The el8 leg covers 1.1.1. An arm64 `c-tests` leg is cheap (about 1 min) | -1 | open, except the RHEL 8 floor leg with OpenSSL 1.1.1k: done, https://github.com/freeunitorg/freeunit/pull/621 |

Checked and fine:
- Action versions. Latest majors today: checkout v7.0.1, cache v6.1.0, upload-artifact v7.0.1, download-artifact v8.0.1, setup-python v7.0.0, setup-go v7.0.0, setup-node v7.0.0, setup-java v6.0.1, docker/build-push v7.4.0, setup-buildx v4.4.1, login v4.6.0
  (releases pages of each repo, fetched 2026-10-07). Every pin matches. artifact v3 and cache v1/v2 were
  shut down in 2025 (https://github.blog/changelog/2024-04-16-deprecation-notice-v3-of-the-artifact-actions/).
  Node 20 was removed from runners on 2026-09-23
  (https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions); master run
  37575481761 is green after it. Dependabot updates actions weekly.
- Runtime versions against upstream (https://endoflife.date): php 8.3/8.4/8.5, python 3.12/3.13/3.14, ruby 3.3/3.4/4.0, node 22/24/26, go 1.26/1.27, java 17/21/25/27 are all supported and the newest of each is present. `pkg/eol.json` agrees.
- `ubuntu-22.04` is not used anywhere (its retirement is 2027-04-17, https://github.com/actions/runner-images/issues/14254). `macos-14` is not used (retires 2026-11-02, https://github.com/actions/runner-images/issues/13518). `macos-15` is not on any announced retirement list that I read.
- Fuzzing path filter covers 10 patterns, not 3 (an older private note said 3). `nxt_port_memory.c` and `nxt_websocket*` are not in it; I did not check which sources the 10 targets link.

## 4. Cleanup plan, one idea per PR

Order: EOL dates first (pin, OpenSSL), then minutes saved. "Sonnet" means a Sonnet agent can prepare it
from this page. "Judgement" means the maintainer decides first.

| # | PR title | Files | Risk | Watch in first run | Who | Status |
|---|---|---|---|---|---|---|
| 1 | ci: pin ubuntu-24.04 until the 26.04 move is tested | all `runs-on: ubuntu-latest` in `.github/workflows/*.yml` | low | no job changes name or toolchain; compare gcc 13.3.0 in the configure log | Sonnet | obsolete: https://github.com/freeunitorg/freeunit/pull/611 pinned ubuntu-26.04; c-tests-pcre1 and the unitctl build job stay on ubuntu-24.04 |
| 2 | ci: build OpenSSL 3.5 LTS in place of 3.6 (EOL 2026-11-01) | `build-test.yml` (env, cache key, slot path, `OPENSSL36_*`), `pkg/eol.json` `tested_ceiling` | medium: TLS tests link the new library | `NXT_HAVE_OPENSSL_VERSION` shows 3.5; `test_tls.py` and python legs; cache miss builds once | Judgement for 3.5 versus 4.0, then Sonnet | done, https://github.com/freeunitorg/freeunit/pull/613 |
| 3 | ci: run unitctl on master pushes only when unitctl paths change | `unitctl.yml` | low | a master push without unitctl paths starts no run; a tag still builds | Sonnet | done, https://github.com/freeunitorg/freeunit/pull/611 |
| 4 | ci: run seccomp on master pushes only | `security-seccomp.yml` | low | PR branch pushes start one run, not two | Sonnet | done, https://github.com/freeunitorg/freeunit/pull/611 |
| 5 | ci: drop the ccache steps from the language legs | `build-test.yml` `test` job | low | repo cache total falls from 10.8 GB; leg times unchanged within 5 s | Sonnet | done, https://github.com/freeunitorg/freeunit/pull/611 |
| 6 | ci: remove the mono step | `build-test.yml` | none | none | Sonnet | done, https://github.com/freeunitorg/freeunit/pull/611 |
| 7 | ci: test gcc 16 and clang 23 with strict hardening and run the C tests | `build-test.yml` (`hardening-strict` becomes a Fedora 45 container job) | medium: `build/tests` may need privileges in a container; dnf install adds time | tests pass in the container; if they need `--privileged`, say so before merging | Judgement, then Sonnet | done, https://github.com/freeunitorg/freeunit/pull/621 (and https://github.com/freeunitorg/freeunit/pull/636) |
| 8 | ci: add a RHEL 8 floor leg (gcc 8.5, OpenSSL 1.1.1k) | `build-test.yml` | medium: the tree may not build with gcc 8.5 under `-Werror` today (not tried) | run it on a fork with `workflow_dispatch` first | Judgement (a red leg may mean dropping AL2 or RHEL 8) | done, https://github.com/freeunitorg/freeunit/pull/621 |
| 9 | ci: skip upstream-EOL runtimes in the test matrix | `build-test.yml` `prepare` jq | low technically, policy risk | leg count 25 to 22; images still built by `release-docker.yml` | Judgement | superseded by https://github.com/freeunitorg/freeunit/pull/643 (open): one version per runtime on PRs |
| 10 | ci: reduced matrix for draft PRs | `build-test.yml` and the other PR workflows | medium: new trigger type `ready_for_review` | a draft push runs about 8 legs; marking ready runs all | Judgement (design in `docs/review-pipeline.md`) | superseded by https://github.com/freeunitorg/freeunit/pull/643 (open) |
| 11 | ci: decide the sanitize go leg | `sanitize.yml` | low | leg either fails visibly or is gone | Judgement | open |
| 12 | ci: run `build/tests` in the weekly distro jobs | `build-distros.yml` | low: may expose container limits | Monday run | Judgement | done, https://github.com/freeunitorg/freeunit/pull/628 |
| 13 | core: drop PCRE 1 and its CI leg | `src/nxt_pcre.c`, `auto/pcre`, `build-test.yml` | high: source change | n/a | Judgement, not a CI-only PR | open |

PR 7 and PR 8 together add one leg (about +2 job-minutes). PRs 3 to 6 and 9 save about 5 job-minutes per
build-test run plus about 15 per master push without unitctl changes.

## 5. What I could not verify

- Default `gcc` and `clang` of the ubuntu-26.04 image. The readme lists versions without marking a default.
- Compiler versions in `build-deb.yml` (trixie container) and in the Docker images. I read no log of those runs.
- Whether `build/tests` passes inside a `fedora:45` container, under gcc 8.5 on `almalinux:8`, or with gcc 16.2 and clang 23 with `--hardening=strict`. I ran none of them. The weekly rawhide job only proves the build.
- Whether the ccache steps give any hit in the language legs. There are no ccache statistics in the logs. The claim rests on what those legs compile.
- The 10 GB per-repository cache limit. I measured 11.02 GB active by `gh api repos/freeunitorg/freeunit/actions/cache/usage` and did not re-read the GitHub limit page.
- The Alpine aports pipeline. An older private note says it uses clang 22. GitHub CI itself builds Alpine edge with clang 23.1.2 (job 111740029869, run 37303026074), so that note is only half right.
- Whether `fedora:45` is the right pin: Docker Hub lists the tag (updated 2026-08-26) and the Fedora package page shows 45 as stable.
- `taiki-e/install-action` (v2.87.24 pinned) latest version, and `claude-code-action` (v1.0.241 pinned, v1.0.244 latest).
- LibreSSL support status in `auto/ssltls`; no leg uses it.
- Whether PR 588 has merged. It was open at the time of reading, so `check-version.sh` is not in `origin/master` and is not covered here.
- The macOS 15 retirement date; I found no announcement.

## 6. Verified later the same day (a peer session, on the build host)

Source: origin/master 872bf041. Containers with `--platform linux/amd64 --cpus 4`, default capabilities.

| Image | Compiler | OpenSSL | configure | `make` | `make tests` | `./build/tests` |
|---|---|---|---|---|---|---|
| fedora:45 | gcc 16.2.1 | 4.0.3 | `--openssl --tests --hardening=strict` | 0, 0 warnings | 0, 0 warnings | exit 0 |
| fedora:45 | clang 23.1.2 | 4.0.3 | same plus `--cc=clang` | 0, 0 warnings | 0, 0 warnings | exit 0 |
| almalinux:8 | gcc 8.5.0 | 1.1.1k FIPS | `--openssl --tests` | 0, 0 warnings | 2: `-Werror=main` | not built |

- The gcc 8.5 failure is one local variable named `main` in `src/test/nxt_router_sender_test.c:273`.
  With `-Wno-error=main` the tests build and pass. Branch `tests/rename-main-local` renames it.
- `build/tests` needs no extra privileges in a container. So PR 7 and PR 8 can run it as a plain container job.
- fedora:45 ships OpenSSL 4.0.3. A Fedora leg also covers OpenSSL 4.0.
- ubuntu-26.04 image defaults (runner-images 39c421f5, toolset-2604.json and install scripts, not run):
  gcc 15.2.0 (the distro package; 13.4 and 14.3 also installed), clang 21.1.8 (20 and 22 also installed).
