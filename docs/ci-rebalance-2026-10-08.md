# FreeUnit CI rebalance, 2026-10-08

Repository: https://github.com/freeunitorg/freeunit. Read-only. Nothing was pushed, posted, rerun or dispatched.

## 0. Basis

- End state judged: `origin/master` (b2360a1d) merged with `ci/cleanup-1` (PR 611, 8ff58024), `ci/openssl-3.5-lts`
  (PR 613, 7e0a371b), `ci/compiler-legs` (PR 621, 903551f9) and `ci/macos-unitd-smoke` (PR 617, 50d3c3d1).
  All four merge with no conflict (`git merge-tree --write-tree`, synthetic commit 728060d4, objects only).
  Line numbers marked "end" are in that merged `build-test.yml`; lines marked "master" are in `origin/master`.
- Run data: every run of the 12 PR, push and schedule workflows from 2026-09-24 to 2026-10-08 (4684 runs; listed in
  12-hour windows because the API caps a listing at 1000). Durations are medians of the 12 newest completed runs per
  workflow and event (mostly 2026-10-07/08). Raw data and scripts were kept in the session's scratch directory.
- "job-min" = the sum of each job's execution time rounded up to a whole minute, the basis of
  `freeunit-harness/tools/ci-cost.py`. ci-cost.py was used for two runs and agrees (it needs `--repo freeunitorg/freeunit`
  outside a checkout). The repository is public, so `/timing` reports 0 billed minutes. The real costs are queue time,
  wall time and runner concurrency.
- One build-test run today: 33 jobs, 93 job-min (master run https://github.com/freeunitorg/freeunit/actions/runs/37770620055,
  77m58s execution, 8.1 min wall). PR 621 makes it 36 jobs, 99 job-min.

## 1. Coverage map (end state)

Medians are execution minutes, master push / PR. Flakes = failed on attempt 1, green on a rerun of the same run, in the window.
"Hang" = cancelled at `timeout-minutes`.

### build-test.yml (PR and master push; paths configure, auto/, go/, src/, test/, pkg/contrib/, pkg/eol.json; master:3-26)

| Job | Builds | Tests | Timeout | Median | Flakes / hangs |
|---|---|---|---|---|---|
| prepare | nothing | leg list from pkg/eol.json (end:59-97) | 10 | 0.1 | 0 |
| core | gcc 13.3, OpenSSL 3.5.9 from source (PR 613), njs, zlib, zstd, brotli, otel, `--tests`, no hardening (end:99-296) | none; ships the build as an artifact | 20 | 0.9 / 1.1 | 0 |
| test (python-3.12/3.13/3.14) | python module on the core | own `test_python*`, `test_asgi*` whole; every other `test/test_*.py` split over the 3 legs by measured time (end:353-470); plus capget/configuration unprivileged and `--restart` subsets | 45 | 5.1 / 6.7 / 6.0 | 1 flake (3.14); hangs 2 (python 2026-10-03, python-3.13 2026-09-26) |
| test (unit) | core only | whole `test/` tree; 400 passed, 1089 skipped (job 113289441722) | 45 | 4.1 / 4.2 | 0 |
| test (java-17/21/25/26/27) | java module | `test_java*`, `test_app_lifecycle.py` | 45 | 3.6 to 3.7 | 1 flake (java-26 configure); 4 configure failures from Maven 429/403; hangs 2 |
| test (php-8.3/8.4/8.5) | php module | `test_php*` | 45 | 2.9 to 3.0 | 0 |
| test (go-1.25/1.26/1.27) | go module | `test_go*` | 45 | 1.9 to 2.4 | 0 |
| test (node-20/22/24/26) | node module | `test_node*` | 45 | 1.5 to 2.0 | 2 flakes (node-24, websocket); hangs 2 |
| test (ruby-3.3/3.4/4.0) | ruby module | `test_ruby*` | 45 | 1.0 to 1.1 | 0 |
| test (perl) | perl on runner perl 5.38 | `test_perl*` | 45 | 1.0 | 3 jobs in the apt hang |
| test (wasm), (wasm-wasi-component) | wasmtime from pkg/contrib, guest via clang 18 | `test_wasm*`, smoke check that a test PASSED (end:939-957) | 45 | 1.0, 1.9 | 1 flake (wasmtime build, 2026-10-04) |
| c-tests | gcc 13.3, distro OpenSSL 3.0.13, PCRE2 | `build/tests`, 3 libunit programs, check-test-hooks.sh (end:1004-1054) | 20 | 1.3 / 1.4 | 0 |
| c-tests-pcre1 | gcc 13.3, `--no-pcre2`, no OpenSSL | `build/tests` (end:1056-1089) | 20 | 0.9 / 0.8 | 0 |
| c-tests-asan | clang 18.1, `--debug`, ASan+LSan+UBSan | `build/tests`, 3 libunit programs (end:1091-1160) | 20 | 2.0 | 0 |
| openssl4 | gcc 13.3, OpenSSL 4.0.3 from source, `-DOPENSSL_NO_DEPRECATED` (end:1162-1240) | `build/tests` | 30 | 0.9 | 0 |
| hardening-strict (gcc, clang) | gcc 13.3 / clang 18.1, `--hardening=strict` (end:1242-1304) | `build/tests` | 20 | 1.0, 1.2 | 0 |
| hardening-strict-fedora (gcc, clang), PR 621 | `fedora:45`, gcc 16.2.1 / clang 23.1.2, `--hardening=strict`, Fedora OpenSSL 4.0.3 (end:1306-1386) | `build/tests`, 3 libunit programs, check-test-hooks.sh | 20 | 1.2, 1.1 (n=1) | n/a |
| c-tests-almalinux8, PR 621 | `almalinux:8`, gcc 8.5.0, OpenSSL 1.1.1k FIPS (end:1388-1435) | same | 20 | 1.5 (n=1) | n/a |

Versions in the PR 621 rows were read from the logs of run https://github.com/freeunitorg/freeunit/actions/runs/37773347895.

### Other workflows

| Workflow / job | Builds | Tests | Trigger | Timeout | Median | Flakes |
|---|---|---|---|---|---|---|
| sanitize.yml python / php / go | gcc 13.3, `--debug --openssl`, ASan+UBSan `-O1` (master:245-262 end) | pytest subsets: 11 files python, 3 php, 2 go; go leg is `experimental`, so it cannot fail (sanitize.yml:196) | PR, push master and pre-*; source paths | 40 | 5.6 / 2.4 / 2.1 | 1 (python) |
| analysis-clang-ast.yml | `debian:testing` clang 21.1.8 with the ngx-ast plugin, all modules compiled (analysis-clang-ast.yml:37-127) | `build/tests` (:88) | PR to master, push master | 20 | 3.4 | 1 (wasmtime, 2026-10-04) |
| build-test-macos.yml | macos-15 arm64, Apple clang 17, Homebrew OpenSSL 3 | `build/tests`; PR 617 adds a unitd smoke request | PR, push master; configure, auto/, src/ | 30 | 1.2 | 0 (PR 617's smoke step fails today, run 37775865377) |
| fuzzing.yml | clang 18, libFuzzer + ASan/UBSan | 10 targets, 60 s each | PR only, http/conf/router/unit paths | 30 | 11.3 | 0 |
| security-seccomp.yml | Docker image python-3.13-slim | AF_ALG seccomp check | PR and (after PR 611) master push; capability/process paths | 20 | 4.0 | 3 (docker image build, 2026-09-30 x2, 2026-10-04) |
| security-syscalls.yml | Docker glibc and musl | syscall drift, `continue-on-error: true` | PR, push master; source paths | 20 | 0.5 | 1 |
| unitctl.yml | rustc stable, 4 targets | cargo test | PR (unitctl paths), master push (paths after PR 611), tags | 25/30 | 16 job-min per run | 1 |
| audit.yml, rust-version.yml | cargo | cargo-audit, MSRV | Cargo paths; audit weekly | n/a | 10 job-min each | 1 (audit, wasmtime download) |
| lint-whitespace, eol-check | none | whitespace; eol.json | PR | 10 | 0.1 to 0.4 | 0 |
| build-distros.yml | fedora:rawhide gcc/clang, alpine:edge gcc/clang, alpine without linux-headers | builds all modules and `make tests` but does not run `build/tests` (master:59-65); runs 2 libunit programs | weekly Mon 04:17 UTC, dispatch | 45 | 7.3 to 7.9 per job, 32 to 34 job-min | 0 |

Deb build, Docker release and claude.yml run on tags, release branches or comments and are out of scope.

## 2. Redundancy

### 2.1 test (unit) duplicates the python legs
The unit leg runs the whole tree with no language module (master:305). In master run 37770620055 I parsed 375 of its 400 PASSED
lines. All 375 also passed in one of the three python legs of the same run. Zero tests passed only in the unit leg.
There are no nested test directories, so the "depth" argument in the comment at end:347-352 does not apply today.
In the window the unit leg failed alone twice. One was an `alert(s)` assert in a run where python-3.12 and 3.13 were
cancelled (https://github.com/freeunitorg/freeunit/actions/runs/36129421008), so it does not prove unique coverage.
The other was PR 406's new step. Cost: 5 job-min per run. Verdict: redundant. PR 406 adds a step to this leg; it must move.

### 2.2 Per-version language legs
Of 2434 java, 2316 node, 1737 php, 1737 ruby, 1276 go leg executions in the window, I list every failure where the other
versions of the same runtime passed in the same attempt:
- java-21, java-26 x2: "Configure java", Maven Central returned 429 or 403 for `tomcat-servlet-api-9.0.122.jar` (auto/modules/java_get_jar:41).
- java-27: setup-java got HTTP 429.
- node-20, node-24 x2: `test_app_lifecycle_churn_under_keepalive[node]` and the websocket frame-order bug (PR 573).
- php-8.5: one `alert(s)` assert on master (run 37194990503).
- python-3.12 x5, 3.14 x1: these legs run different shared files, so a lone failure is a file, not a version.

None of these is a version-specific product bug. In two weeks no older or middle version caught anything the newest
version missed. Version differences do matter when a module's source or the runtime changes (the python home/prefix gap,
the PHP tzdata crash). So run all versions when that module's paths change, and always on master. Per PR, one version
per runtime is enough.

### 2.3 C-test legs
Ten jobs run `build/tests` on every source PR in the end state. Failures in the window that no other C leg caught:

| Leg | Runs with job data | Failures | Caught alone |
|---|---|---|---|
| c-tests-asan | 503 | 10 | 4 (sanitizer reports, e.g. run 36214149991) |
| openssl4 | 624 | 1 | 1: `nxt_openssl_server_init_test.c:84` const qualifier under OpenSSL 4.0 headers (run 36323278065) |
| c-tests | 624 | 6 | 0 |
| c-tests-pcre1 | 170 | 0 | 0 |
| hardening-strict gcc 13.3 / clang 18.1 | 349 each | 0 | 0 |

- **hardening-strict on the runner image** does not earn its place next to the Fedora job. The strict-only flags are
  `-Wformat=2 -Wvla -Wimplicit-fallthrough -ftrivial-auto-var-init=pattern` (auto/cc/hardening:149-153). They are front-end
  and codegen options that gcc 12+ and clang 16+ treat the same way. Version-specific `-Werror` drift at default flags is
  still covered on gcc 13.3 by `core` and `c-tests`, on clang 18 by `c-tests-asan`. Zero failures in 349 runs.
- **openssl4** overlaps the Fedora job. Fedora 45 links OpenSSL 4.0.3 (log of run 37773347895: "OpenSSL 4.0.3 29 Sep 2026").
  The one unique catch was a header constness error, which the Fedora build hits too. The only difference left is
  `-DOPENSSL_NO_DEPRECATED` (master:1221). Add that flag to one Fedora leg and drop openssl4. Fedora's OpenSSL may move to
  4.1 inside Fedora 45's life; that is acceptable for a "newest" slot.
- **c-tests-pcre1** builds with no OpenSSL and PCRE 1. `build/tests` has a regex test (src/test/nxt_tests.c:78), so it tests
  something real. It costs 1 job-min. Keep it until PCRE 1 is dropped (audit row 13).
- **c-tests-asan** keep per PR: 4 unique catches.
- **c-tests** keep: it is the "what Ubuntu 24.04 ships" build with check-test-hooks.sh.
- **analysis-clang-ast** runs `build/tests` with clang 21 (:88). With clang 23 in the Fedora job it adds little test
  coverage; the plugin is the point. Leave it; the step is seconds.

### 2.4 Sanitize vs c-tests-asan
Different scopes: sanitize runs pytest against a sanitized unitd (gcc), c-tests-asan runs C tests (clang). Not redundant.

## 3. Gaps after the in-flight PRs

| Gap | Evidence it matters | Cheapest test |
|---|---|---|
| **32-bit** (no 32-bit build anywhere) | Alpine's 1.37.0 builders found 3 issues CI could not: test sources fail `-Werror` on x86/armv7 (PR 579, merged), `min_length` ignored on 32-bit (PR 581, a product bug, 5 tests), gcc 32-bit build error `nxt_h1proto.c:3574` (PR 582) | `alpine:edge` with `--platform linux/386` runs natively on the x86-64 runner. Build, `build/tests`, and the core-only pytest set (static, compression, tls, proxy). This also covers musl C tests |
| musl C tests | build-distros builds `build/tests` on Alpine but does not run it (master:59-65, 201) | same leg as above |
| big-endian | PR 581 also found `level` became 0 on s390x | weekly s390x qemu leg, about 15 min for the C suite (measured on the Docker host 2026-08-28); pytest under qemu is too slow for CI |
| arm64 Linux | only release-docker builds arm64, with no tests; weak-memory test bugs showed on riscv64/loongarch64 (PR 580) | `c-tests` on `ubuntu-24.04-arm`, about 2 job-min |
| OpenSSL 4.0 TLS pytest | every pytest leg links the OpenSSL 3.5 slot; 4.0 runs only `build/tests` | build python into the Fedora gcc leg and run `test/test_tls*.py`, `test_reconfigure_tls.py`, `test_status_tls.py`; master push always, PR when `src/nxt_openssl*`, `src/nxt_tls*`, `auto/ssltls` or those tests change |
| hardened build under pytest | `core` has no `--hardening` (default is off, auto/cc/hardening:5), and nothing in pkg/ passes it | build `core` with `--hardening=default`; every leg then tests a FORTIFY=3 binary at no extra cost |
| UBSan on language modules | sanitize covers python and php; go is non-blocking; java, node, ruby, perl, wasm never run sanitized | weekly sanitize legs for node and ruby first (C/C++ glue); not per PR |
| `--h2` | not on master. PR 470 adds an `http2` job on `ubuntu-latest` with `actions/checkout@v5` | when the h2 stack lands, rebase that job to `ubuntu-24.04`/`@v7`, and drop its duplicate `build/tests` step (or move `--h2` into `core`) |
| all-skipped legs | only wasm checks that a test PASSED (end:939-957); branch ci/legs-followup (42b4f397) has the general check, never merged | port that check to every leg |
| Floor of OpenSSL 1.1.1 / gcc 8.5 | closed by PR 621 (c-tests-almalinux8) | n/a |

## 4. Placement

| Job (end state after the changes below) | Trigger | Draft PRs | Why |
|---|---|---|---|
| prepare, core | PR, master | yes | everything depends on them |
| python-3.12/3.13/3.14 | PR, master | yes | together they are the language-agnostic suite; dropping one drops a third of it |
| newest of each runtime (java-27, php-8.5, go-1.27, node-26, ruby-4.0), perl, wasm, wasm-wasi-component | PR, master | yes | libunit and router changes reach every module |
| other runtime versions (java 17/21/25/26, php 8.3/8.4, go 1.25/1.26, node 20/22/24, ruby 3.3/3.4) | master push; PR only when `src/<lang>/`, `auto/modules/<lang>*`, `test/test_<lang>*`, `pkg/eol.json` or the workflow change | same rule | 0 version-specific catches in 2 weeks |
| test (unit) | removed | n/a | 375/375 duplicated |
| c-tests, c-tests-asan, c-tests-pcre1, c-tests-almalinux8, hardening-strict-fedora x2 | PR, master | yes | each is a distinct axis; 1 to 2 job-min each |
| hardening-strict (runner), openssl4 | removed | n/a | covered by Fedora |
| new i386 musl leg | PR, master | yes | largest gap with proven bugs |
| new c-tests-arm64 | PR, master | yes | cheap |
| OpenSSL 4 TLS pytest | master; PR on TLS paths | yes | |
| sanitize python/php | PR, master | yes | unchanged |
| sanitize go | make blocking or remove | n/a | it cannot fail today |
| fuzzing | PR (paths) | yes | unchanged |
| clang-ast, macOS, syscalls, seccomp | unchanged (PR 611 placement) | yes | small |
| build-distros (+ `build/tests`, + s390x C tests, + node/ruby sanitize) | weekly | n/a | rolling distros and slow emulation |

Draft PRs: I do not recommend a separate draft matrix (audit row 10). Drafts were 739 of 3784 PR runs and 10338 of
53344 build-test PR job-min (19.4%). The path rule above cuts every PR, draft or not, and gets about 4 times the saving.
A second trigger type (`ready_for_review`) adds a full run on every draft-to-ready flip for little gain.

## 5. Flakiness and timeouts

- **2026-10-07 apt hang, 17:09 to 21:40 UTC.** 21 jobs stopped in "Install packages" or "Install libperl-dev". 18 ran
  to their job limit (20, 30, 40 or 45 min). About 650 job-min, and the cause of the 20 to 41 min master queues in the data.
  It was not only the perl leg: it hit every job that runs apt in that window. Fix: `timeout-minutes: 5` on each apt
  step and `-o Acquire::Retries=3 -o Acquire::http::Timeout=30`. The retry then happens inside the job.
- **Websocket hang.** 5 jobs (node x2, java x2, python x1) hung after the autobahn case 7_13_1/7_13_2 until the 45-min
  limit: 2026-09-28, 2026-10-03 x2, 2026-10-07 x2. That it is group 9 is inferred from the position in the log. PR 573
  is the fix. One more hang (python-3.13, 2026-09-26) stopped after `test_python_keepalive_reconfigure_2`, a different cause.
  The test client uses a blocking `sendall()` with no timeout (test/unit/applications/websockets.py:212).
  Fix regardless of PR 573: `timeout-minutes: 15` on the pytest step (the slowest leg's whole job is p90 8.4 min) and
  pytest-timeout with a per-test limit so the log names the stuck test. I did not check that noble ships
  `python3-pytest-timeout`; pip works in any case.
- **Job timeouts far above the median.** test 45 vs median 1 to 7 (the comment at end:301-302 ties it to a cold wasmtime
  build); c-tests, pcre1, asan, hardening 20 vs about 1; openssl4 30 vs 0.9; sanitize 40 vs 5.6. A job limit only costs
  when something hangs, and step limits on apt and pytest cover both measured hangs. Keep the job limits for cold caches.
- **Java configure 429/403.** 4 failures in 2 weeks (runs 36199891625, 36355864558, 37224192544; job 112362089351 was
  setup-java). Five java legs fetch the same jars from Maven Central at once. Fix: an `actions/cache` for the jar directory
  that `auto/modules/java_get_jar` uses (`$HOME/.m2/repository`, under sudo that is root's home), keyed on the jar versions.
- **seccomp image build.** 3 flakes in "Build FreeUnit python-3.13-slim image". Add a retry on the docker build step.
- **Real flake rate is low.** 13 rerun-green jobs in 2 weeks, 0 same-sha cross-run flips. Hangs and the apt incident
  cost far more than reruns.

## 6. Ordered changes, one idea per PR

Status checked 2026-10-09 against `origin/master` and the pull requests named.

Minutes: rounded job-min per build-test run from the medians above. "PR" means a source PR push that does not touch a
language module.

| # | PR | Files | Risk | Per PR run | Per master push | Watch in first run | Who | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | ci: bound apt and pytest steps by time | build-test.yml, sanitize.yml (apt steps, pytest steps) | low | 0 normally; avoids about 920 job-min in this window (650 apt, 270 hangs) | same | step limits appear in the job's step list; no leg hits them | Sonnet | done, https://github.com/freeunitorg/freeunit/pull/627 |
| 2 | ci: run one version per runtime on PRs, all on master and on module paths | build-test.yml `prepare` (needs changed files: `gh api repos/.../pulls/N/files` or checkout with `fetch-depth: 0` and `git diff base...head`) | medium: a version-specific break lands on master and shows there | about -37 (java -16, php -6, go -6, node -6, ruby -3 to -4) | 0 | a src/-only PR gets 12 test legs; a `src/java/` PR gets all 5 java legs; master gets all | judgement (which version per PR: newest, or newest LTS for java), then Sonnet | open, in https://github.com/freeunitorg/freeunit/pull/643 |
| 3 | ci: drop the unit leg | build-test.yml `prepare`, end:330-334 | low; PR 406 adds a step to this leg and must move it to a python leg | -5 | -5 | the union of python legs still lists the 375 tests | Sonnet after the maintainer agrees | open, in https://github.com/freeunitorg/freeunit/pull/643 (drops the unit leg) |
| 4 | ci: add a 32-bit musl leg (Alpine i386) | build-test.yml | medium: rust for fake_upstream on i386, capability tests in a container | about +4 (not measured) | +4 | `uname -m` in the container is i686; `build/tests` exit 0; `test_static_compression.py` passes on master (PR 581 merged) or fails before it | judgement, then Sonnet | open |
| 5 | ci: drop the runner hardening-strict legs and openssl4; add `-DOPENSSL_NO_DEPRECATED` to Fedora gcc | build-test.yml (after PR 621) | low | -4 | -4 | Fedora gcc configure log shows the define; OpenSSL 4.0.3 found | judgement (small), then Sonnet | open, in https://github.com/freeunitorg/freeunit/pull/643 |
| 6 | ci: cache the Java jars | build-test.yml java steps | low | 0; ends about 2 failures a week | same | second run shows no "getting remote" lines | Sonnet | open |
| 7 | ci: fail a leg whose module tests all skipped | build-test.yml pytest step (port 42b4f397) | low | 0 | 0 | every leg finds a PASSED line for its module | Sonnet | open: master checks a PASSED line only in the wasm and wasm-wasi-component legs |
| 8 | ci: build the core with `--hardening=default` | build-test.yml `core` | low to medium: FORTIFY=3 can abort a test that reads past a buffer, which is the point | 0 | 0 | configure prints the hardening flags; full pytest green | judgement | open |
| 9 | ci: run `build/tests` in the weekly distro jobs | build-distros.yml | low; PR 621 proved it runs in fedora:45 and almalinux:8 containers without privileges | 0 | 0 (weekly +2) | Monday run | Sonnet | done, https://github.com/freeunitorg/freeunit/pull/628 |
| 10 | ci: c-tests on arm64 | build-test.yml | low | +2 | +2 | runner label available to the org | Sonnet | open |
| 11 | ci: TLS pytest against OpenSSL 4 | build-test.yml (Fedora gcc leg + python module) | medium | +3 on TLS paths only | +3 | `test_tls*.py` pass with 4.0.3 | judgement | open |
| 12 | ci: decide the sanitize go leg | sanitize.yml:196 | low | 0 or -3 | same | | judgement | open |
| 13 | ci: weekly s390x C tests and node/ruby sanitize | build-distros.yml | medium (qemu time) | 0 | 0 (weekly +15 to +25) | first Monday run | judgement | open |

Net effect of 2, 3, 4, 5 and 10 on a source PR run: about 99 to 59 job-min. Master push: about 99 to 96.
Rows 9 (EOL runtimes) and 10 (draft matrix) of the audit are subsumed by change 2: EOL versions are never "newest",
so they leave PRs and stay on master, with no policy change to support.

## 7. Where the data disagrees with the brief or the audit

- "Branches without PRs: ci/astgrep-gates, ci/perf-gates." Both have open, non-draft PRs: 449
  (https://github.com/freeunitorg/freeunit/pull/449) and 450 (https://github.com/freeunitorg/freeunit/pull/450, based on 449).
  ci/legs-followup and ci/distros-weekly-schedule have no PR. ci/distros-weekly-schedule (2026-08-29) is superseded:
  master already has the weekly cron (build-distros.yml:8-9).
- "An apt hang in the perl leg on 2026-10-07." It hit 21 jobs across all apt steps, 18 outside the perl leg, about 650 job-min.
- Audit row 10 and harness `docs/review-pipeline.md:94-98` propose a draft matrix with `unit` and only `python-3.14`.
  That loses about two thirds of the shared suite: shared files are split over the three python legs (end:353-470), and the
  unit leg skips 1089 tests that need python. Keep all three python legs in any reduced matrix.
- Drafts are 19.4% of build-test PR job-min, so a draft-only matrix is the smaller lever (section 4).
- The comment at build-distros.yml:59-63 (master) says `build/tests` needs capabilities a container may not have.
  PR 621 runs it as a plain container job, green.
- review-pipeline.md lists the unit leg at 3.5 min; today it is 4.1 to 4.2.
- "Queue time is the real cost" (audit section 1): PR queue median is at most 2 min per job. The long master queues in
  the window come from the apt incident.

## 8. What I could not verify

- The organization's concurrent-job limit, so the effect of fewer jobs on queue time is not quantified.
- Whether `ubuntu-24.04-arm` is enabled for this organization.
- That an i386 Alpine container runs the core pytest subset on a GitHub runner (PR 579 reports a local i386 Alpine build).
- That noble ships `python3-pytest-timeout`.
- The websocket hang's group number (inferred from log position).
- Logs of the 16 c-tests-macos failures with no green sibling; I assumed PR iteration, as for the other non-flake failures.
- Job data exist for 3900 of 4684 runs: every failed run, every rerun, every PR run and their siblings. Successful
  push runs without job data cannot hide a failure, but per-job "seen" counts in 2.3 are below the true run count.
- Durations of the PR 621 jobs are one sample each.
