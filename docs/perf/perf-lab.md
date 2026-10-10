# FreeUnit perf lab

Small performance changes to FreeUnit are measured on two shared servers by any agent, then compared number by
number. One command runs a job. One tool does all the arithmetic and prints a verdict that an agent copies.
Trackers: https://github.com/freeunitorg/freeunit/issues/597 (wave 0, wave 4, wave 5) and
https://github.com/freeunitorg/freeunit/issues/596.

State on 2026-10-07: the tools below exist and ran a smoke null gate on the perf box. Jobs 01 to 05 are written,
not yet run. This page supersedes the "never in agents" line in `perf-plan-2026-10.md` section 5: an agent may run
a lab job, because the claim and the idle gate below serialise jobs.

```sh
cp tools/perf-lab/lab.env.example run/lab.env        # once; fill in, run/ is gitignored
python3 tools/perf-lab/test_perf_compare.py         # proves the compare arithmetic, exit 0
tools/perf-job.sh tools/perf-lab/jobs/01-null-hello.job
tools/perf-job.sh run/perf-lab/<run-id>              # resume an interrupted run
PERF_JOB_CHECK=1 tools/perf-job.sh <job>             # validate a job file only
```

## Configuration and private notes

Tracked files name variables, never hosts. The values live in `run/lab.env` (gitignored; `LAB_ENV=<file>`
overrides the path). `tools/perf-job.sh` refuses to start and names each missing variable.

| variable | meaning |
|---|---|
| `PERF_HOST`, `SSH_USER` | the measurement box; empty `SSH_USER` uses `~/.ssh/config` |
| `LAB_DOCKER_HOST` | the image-build and cross-arch box. Not `DOCKER_HOST`: the docker CLI reads that name |
| `REMOTE_WORKDIR` | large disk on the perf box: bare mirror `freeunit.git`, `trees/`, `runs/` |
| `CLAIM_DIR` | directory of box claims, shared with the older `CLAIMED-<session>-<purpose>` convention |
| `LAB_IMAGE` | image from `tools/perf-lab/Dockerfile` (a PHP embed image plus perf, strace) on the perf box |
| `OHA`, `REPO_URL` | oha binary on the perf box (mounted into the container); FreeUnit git URL |
| `SERVER_CPUS`, `LOAD_CPUS` | unitd and oha CPU sets; the SMT siblings of `SERVER_CPUS` stay idle |
| `PERF_WINDOW`, `SHORT_JOB_MIN` | box local time window for jobs longer than `SHORT_JOB_MIN` (15) minutes |
| `GOVERNOR` | `record` (write governor and EPP into meta.txt) or `require-performance` (refuse otherwise) |
| `IDLE_MAX_PCT`, `WAIT_MAX_MIN` | idle gate limit (5 percent CPU) and the wait before BUSY (30 minutes) |

Private host notes (names, disk layout, measured timers) go in `run/` or in `CLAUDE.local.md` and
`AGENTS.local.md` at the harness root. Both are gitignored, per the Claude Code and AGENTS.md conventions.
A public copy of the harness works unchanged once someone writes their own `run/lab.env`.

## a. Schedule

| box | does | never |
|---|---|---|
| `PERF_HOST` | perf jobs, pytest, native builds; the lab image is built here (one apt layer) | image builds that pull GBs during a job |
| `LAB_DOCKER_HOST` | Docker image builds, s390x and cross-arch under qemu, Alpine builds | perf numbers: it runs review containers |

Load measured on 2026-10-07 from an unprivileged account (box local time, CEST). sysstat is installed but its
collection is off, so there is no load history; enabling it needs root (add it to the root handover list).

| time | load | source |
|---|---|---|
| 00:00 | logrotate, dpkg-db-backup (short) | systemd timers, both boxes |
| about 04:00 to 04:30 | man-db (randomised start) | systemd timer |
| about 06:15 to 07:30 | apt-daily-upgrade (randomised start) | systemd timer |
| randomised | apt-daily | systemd timer |
| 06:00 to 22:00 | GitLab runner jobs on the perf box: 37 jobs in 14 days, about 22 minutes of run time in total | `journalctl -u gitlab-runner` |
| unknown | Ansible | not discoverable: no sudo `BECOME-SUCCESS` events in auth logs since 2026-09-12, no user crontab entry |
| any time | other agent sessions: builds, pytest, benches | the main conflict; claims and the idle gate handle it |

To find the Ansible schedule, ask the maintainer or read the controller (its cron, AWX or CI schedule); the
managed boxes cannot show it to this account. Until then: `PERF_WINDOW=01:00-06:00` for jobs over 15 minutes.
It avoids CI, apt upgrades and daytime sessions. Shorter jobs run any time the gate passes. A run hit by load
that starts mid-job shows up as a NOISY null gate, which is why every A/B job carries its own null phase.

**Claim.** `remote.sh` writes `$CLAIM_DIR/CLAIMED-perflab-<run-id>` under `flock $CLAIM_DIR/.perflab.lock`:

```
format=perflab-1   who=<user@host>   purpose=perf-job <job>   host=<box>   pid=<pid>
started=<epoch>    expires=<started + (2 x estimated minutes + 30) x 60>
```

(one `key=value` per line). A trap removes it at exit, also on BUSY and on error. A lab claim is stale 10 minutes
after `expires` when its pid is gone; the next job moves it to `$CLAIM_DIR/reaped/` and logs that. An empty file
is a legacy claim: honoured while younger than `LEGACY_CLAIM_MAX_H` (72 hours), then ignored with a warning naming
it. The lab never deletes another session's file. On 2026-10-07 one legacy claim from another project was 775 hours old.

**Idle gate.** Before the claim (waiting), and again after the builds (waiting up to 5 minutes):
1. no other live claim;
2. inside `PERF_WINDOW`, or the job is at most `SHORT_JOB_MIN` minutes;
3. box CPU busy at most `IDLE_MAX_PCT` over 10 s, from `/proc/stat`. Load average is not used: it counts blocked
   tasks and lags. When busy, the message names processes over 2 percent of a CPU in a 3 s sample, own session
   excluded. Idle orphan unitd do not block (4 on the perf box, 54 on the docker box today); they cost no CPU;
4. governor and EPP are read and recorded. Both boxes run `powersave` with EPP `balance_power`, and changing it
   needs root, so the default is `record`; see the metric choice below.

**Busy.** Poll every 20 s for up to `WAIT_MAX_MIN`, then stop with exit 75 and one line:
`PERF <job> <run>: BUSY, nothing measured: <reason>`. The job never kills anything on the box. Its own cleanup is
`pkill -x unitd` inside its own container and `docker rm -f perflab-<run-id>` by exact name.

## b. Job format

A job is a `KEY=VALUE` file in `tools/perf-lab/jobs/`. Unknown keys are refused. Opus or Fable writes job files.

| key | default | meaning |
|---|---|---|
| `JOB`, `ISSUE`, `EXPECT`, `FALSIFY` | | name; the tracker item; the expected delta; the number that falsifies it |
| `REV_A`, `REV_B` | | `origin/<branch>`, `pr/<number>`, a tag or a SHA; equal revs, no patch and equal per-side configure arguments make a null-only job |
| `PATCH_A`, `PATCH_B` | | a mutation patch applied on top of the rev (path relative to the job file) |
| `WORKLOAD` | hello | `hello` (hello.php, root-only PHP target, opcache on), `server`, `static1k` (1 KiB share), `static1k-hdrs`, `capp` (C floor), and the router-only route tables `rx0`, `rx1`, `rx32`, `drupal-ci` (below) |
| `CONNS` | 6 | concurrency cells, for example `6 32`; each is its own cell name, `hello-c6` |
| `WARM`, `DUR` | 2, 8 | seconds of warm-up and of measurement per cell |
| `REPS` | 2 | blocks; even, because each normal block needs its swapped twin |
| `RATE`, `APP_PROCS` | none, 4 | oha `-q` (open-loop-like, for latency claims); PHP or external processes |
| `PERF`, `STRACE` | 0, 0 | collect perf counters; run strace probe cells (`STRACE_SEC` 3 s at `STRACE_RATE` 2000 req/s) |
| `NULL_GATE`, `NULL_LIMIT_PCT` | 1, 5 | run the null phase; the largest null band that still allows a claim |
| `PRIMARY` | server_us | the metric in the one-line verdict |
| `FUNCS`, `CONFIGURE_ARGS` | | `binary:symbol` list for function sizes; extra `./configure` arguments for both sides |
| `CONFIGURE_ARGS_A`, `CONFIGURE_ARGS_B` | | appended to `CONFIGURE_ARGS` for that side only, for example `CONFIGURE_ARGS_B=--regex-jit`. One SHA built two ways gets two trees and two phases. Letters, digits, blanks and `, . = / _ + -` only |

A cell is one fresh unitd, a warm-up, then oha `-z DUR -w` (`-w` waits for in-flight requests; without it oha
counts one "aborted due to deadline" per connection, which the first smoke recorded as failures). A block is 8
cells, `X Y Y X Y X X Y`; odd blocks run `Y X X Y X Y Y X`. The null phase (A against a copy of A) and the A/B
phase alternate block by block in the same window. Builds run in `LAB_IMAGE` on the perf box (release, no
`--debug`), from `git archive` of the resolved SHA, cached by SHA, patch, image and arguments.

**Router-only workloads.** No application; the route table is a file in `tools/perf-lab/conf/<workload>.json`
that `cells.sh` PUTs after replacing `@PORT@`. They measure the router and the main process only. No
`listen_threads` is set, as in the other workloads.

| workload | route table | request |
|---|---|---|
| `rx0` | the Drupal how-to deny list as its 32 original globs (one negative), `return 404`, then `return 200`; no regex | `/node/42/edit`, misses every glob |
| `rx1` | one anchored URI regex that matches, `return 200`; the last step returns 404 | `/catalog/section/item42` |
| `rx32` | 31 anchored regex misses (each `return 404`), then the matching regex; the last step returns 404. Patterns from PR 551 `docs/pcre2-jit-benchmark.md` | `/catalog/section/item42` |
| `drupal-ci` | the same deny list with every glob rewritten as `~(?i)...`, as the how-to's case caveat suggests: 32 regexes, 30 of them unanchored at the start, then `return 200` | `/node/42/edit`, misses every regex |

In `rx1` and `rx32` a 200 proves that the matching regex matched; a fall-through gives 404 and the phase is
INVALID. Every cell also records `xmaps`, the count of anonymous executable mappings in the router: PCRE2 JIT
code lives there. `meta.txt` records `configure_a`, `configure_b`, `regex_jit_a`, `regex_jit_b` (does the tree's
`nxt_auto_config.h` define `NXT_REGEX_JIT`) and `pcre2_jit` (does the image's libpcre2-8 report JIT). The
build log `build-<tree>.log` keeps the `./configure` output.

Workload `capp` is `src/test/nxt_unit_app_test.c` linked against the plain `build/lib/libunit.a` as
`build/lab_capp`. `build/unit_app_test` is not used: it links `libunit-tests.a`, the test-hook archive. `capp` is
built, not yet run. A Yii workload is not built; `yii3-benchmark-setup-2026-10-05.md` is the starting point.

Why the lab does not call the older tools: the perf box has no `php-config` on the host, so `unit-build` cannot
build the PHP module there; `bench-release.sh` is static-only and reads CPU on the host, where perf is blocked
(`perf_event_paranoid` is 4 on both boxes). `cells.sh` is the bench-app-shm kit (PR 535) generalised.

**Metrics** (all per 200 response; counted in the measured window only):

| metric | exact source |
|---|---|
| `server_us`, `router_us` | sum of field 1 of `/proc/<pid>/task/*/schedstat` (ns on CPU) over unitd and all descendants, or the router, before and after the oha run |
| `s_*`, `r_*`: `cycles`, `instructions`, `cs`, `migrations` | `perf stat -x, -e cycles,instructions,context-switches,cpu-migrations -p <pids>` in the container, `--cap-add PERFMON` |
| `rps`, `p50_ms`, `p99_ms` | oha `--output-format json`; closed loop unless `RATE` is set; wrk2 is not installed |
| `rsys_*`, `asys_*` | `strace -c -f -p <router>` and `-p <each app process>` in probe cells only (strace slows the target, so probes never feed CPU metrics); includes `epoll_ctl`, `sendmsg`, `recvmsg` when present |
| `fsize_bytes` | `nm -S --defined-only build/sbin/unitd` (or the module) for each `FUNCS` symbol |
| guards: `ok`, `bad`, `alerts`, `probe_ok` | any non-200, oha error, `[alert]` in the unit log, or 0 requests makes the phase INVALID |

Machine-code work beyond sizes is manual (Opus or Fable), in the lab container on the perf box:
`perf record -g -p <router pid> -- sleep 5` under load, then `perf annotate --stdio -s <function>`;
`objdump -d --no-show-raw-insn build/sbin/unitd | awk '/<nxt_mp_free>:/,/^$/'` in A and B.

**Noise floor**, measured 2026-10-07: smoke job `null-hello-c6-smoke` (master 7d729582 against itself,
hello.php, c=6, 4 PHP processes, 2 blocks of 2 s cells after 1 s warm-up, governor powersave, EPP balance_power).
Null band = |null delta| + 95 percent CI half-width.

| metric | null band % | usable for small deltas |
|---|---|---|
| `r_instructions`, `s_instructions` | 2.46, 2.56 | yes, the most stable |
| `r_cycles`, `s_cycles` | 2.70, 5.44 | yes for router; server needs more cells |
| exact syscalls (`asys_chdir` 1.000, `rsys_writev` 1.000, `asys_rt_sigaction` 8.000) | 0.00 | yes, exact |
| `rsys_total`, `asys_total` | 4.45, 8.07 | totals include polling (`epoll_wait`, `poll`) |
| `r_cs`, `s_cs` | 12.9, 12.9 | no, at 2 s cells |
| `router_us`, `server_us` | 33.2, 35.8 | no under powersave: per-cell server_us ran from 99 to 281 us |
| `rps`, `p50_ms`, `p99_ms` | 47.7, 50.7, 37.4 | no |

The first smoke, otherwise identical, gave counter bands of 4.9 to 7.4 percent. Under `powersave` the clock
changes between cells, and CPU time per request follows the clock while cycle and instruction counts do not.
So, under `GOVERNOR=record`, `PRIMARY` is `s_instructions`, `r_instructions` or `s_cycles`, never `server_us`.
To measure: the bands at 8 s cells and c=32 (job 01), `static1k`, `capp`, and the `performance` governor. The
older notes "c=6 CPU about 2 percent, c=32 about 8 percent" came from 8 s cells on the other box; not reproduced.

## c. Comparison format

`results.tsv`, one row per value: `job phase side rev cell block slot metric value`. `phase` is `null`, `ab` or
`build`; slots 0 to 7 are timed cells, 8 and 9 strace probes. `meta.txt` holds revs, SHAs, unitd hashes,
governor, EPP, kernel, CPU, image, oha version and the gate reading.

```sh
python3 tools/perf-compare.py RESULTS.tsv --meta meta.txt --json summary.json --primary s_instructions --null-limit 5
```

`perf-job.sh` runs it and writes `compare.txt` and `summary.json` beside the TSV. Per (phase, cell, metric): the
delta is the mean of per-block deltas (normal and swapped blocks weigh the same, so the slot-set bias cancels),
with a Welch standard error per block, `se = sqrt(sum se_k^2) / K`, `df = sum(n_a + n_b - 2)`, 95 percent CI by t.
Adjacent slot pairs (0,1) (2,3) (4,5) (6,7) (8,9) each hold one A and one B; "cells agree" counts the pairs whose
sign matches the delta. Verdicts: **CLAIM** when |delta| is over the null band, the CI excludes 0 and at least 3
of 4 pairs agree; **NO CLAIM** otherwise; **NOISY** when the null band is over `NULL_LIMIT_PCT`; **INVALID** on a
guard; **PASS** or **NOISY** for a null phase. `summary.json` holds `job`, `run_id`, `primary`, `headline`,
`meta`, and per result `phase cell metric description a_mean b_mean delta_pct ci_low ci_high null_band_pct
cells_agree cells_total method verdict line b_better`, plus `sizes`.

The one line an agent pastes, from the smoke and from the fixture:

```
PERF null-hello-c6-smoke null-hello-c6-smoke-20261007T003520Z: NOISY hello-c6: null gate: A and a copy of A differ by -0.9 percent in server cycles per request, 95 percent CI [-5.4, 3.6], null band 5.4, limit 5.0: NOISY | report ... | json ...
CLAIM hello-c6: B is 5.0 percent less server CPU per request (us), outside the null band of 0.9, 8 of 8 cells agree
```

`tools/perf-lab/test_perf_compare.py` checks every formula on `testdata/fixture.tsv`, whose values are worked by
hand in its docstring. A broken t quantile makes it fail, so run it before trusting a new copy.

## d. Roles by model tier

| tier | does | never |
|---|---|---|
| Haiku, Sonnet, open models | run `tools/perf-job.sh <job>`; paste the PERF line and the summary.json table into the PR | edit a job, a patch, `NULL_LIMIT_PCT`, the cells, `lab.env`, `cells.sh` or `perf-compare.py`; compute any number; rerun until a verdict changes; run two jobs at once; kill a process on a box |
| Sonnet | write the PR's measurement section from summary.json and the TSV: job file, revs, verdict lines, the null gate | add a claim the verdict does not make |
| Opus, Fable | write job files and patches; choose the next iteration; interpret a surprise (a NOISY null, a CLAIM in the wrong direction, counters moving without the expected syscall change); decide one rerun | change a limit or a cell after seeing the result |
| DeepSeek, Codex | review the C diff and the job file against the claim | measure |

OpenCode: `.opencode/commands/perf-job.md` (`/perf-job <job file>`) runs the job through the `perf-runner`
agent in `.opencode/agents/perf-runner.md`, which may run only `tools/perf-job.sh`, the compare test and `cat`
under `run/perf-lab/`, and may not edit files. No model is set; the user's config picks it. Formats:
https://opencode.ai/docs/commands/ and https://opencode.ai/docs/agents/. To use it from the FreeUnit checkout,
copy both files to `~/.config/opencode/`. The bash tool timeout is not documented; a killed poller loses nothing,
because the run is detached on the box and `tools/perf-job.sh run/perf-lab/<run-id>` resumes it.

Open-weights models with dependable tool calls through OpenRouter (Qwen3-Coder, GLM-4.6, DeepSeek-V3.x) should
manage this: one command, one line, a JSON verdict to copy. That is an expectation, not yet tested. Choosing
the next iteration and explaining a surprising result still need Opus or Fable.

## e. CI offloading

| on GitHub-hosted runners | why |
|---|---|
| build, C tests, pytest | as today |
| `strace -c` syscall counts per request on a fixed request count | exact counts; the smoke shows 0.00 bands for deterministic syscalls |
| binary and function size deltas; PR 450 `disasm-diff.sh` and `layout-check.sh` | deterministic for one compiler |
| instruction counts | only with `valgrind --tool=callgrind` or `cachegrind` on a fixed request count. Hardware counters (`perf stat cycles`) are expected to be unsupported on the hosted Azure VMs; not verified |

Not in CI: CPU time per request, rps and latency. Shared vCPUs move them more than the deltas under test.
PR 450's gates stay non-blocking (`continue-on-error`) and catch code-shape changes; lab jobs measure cost.
https://github.com/freeunitorg/freeunit/pull/451 (USDT) adds probe points the lab can count later with bpftrace.

Recommendation: no self-hosted Actions runner on the perf box. The repository is public, so a self-hosted
runner would run fork PR code there. It would also need a root service beside the existing gitlab-runner, and
it would ignore the claim files that other sessions honour. Use the claim file plus a queue instead: agents run
`perf-job.sh` on demand now. Next step: a nightly queue: a user crontab entry on the perf box (user crontabs
work there) runs the queued jobs serially in `PERF_WINDOW` through the same `remote.sh`. Not built yet.

## f. The first five jobs

Run in this order. Each A/B job carries its own null phase; job 01 sets the day's reference.

| job | 597 item | expected | falsified by |
|---|---|---|---|
| `01-null-hello` | wave 5, rerun protocol | counter null bands at or under 2.5 percent at 8 s cells, c=6 and c=32 | a counter band over 3 percent: fix the lab before jobs 03 to 05 |
| `02-capp-floor` | wave 5, C external-app floor | router instructions per request within 10 percent of job 01; app side far below PHP | router instructions differ by over 10 percent: the floor does not isolate the app |
| `03-mp-junk-fill` | wave 0, `nxt_mp_free_junk` memset in release (`src/nxt_mp.c:144`, unguarded) | `r_instructions` 0.5 to 2 percent lower; `nxt_mp_free` or `nxt_mp_chunk_free` smaller | NO CLAIM at a null band of 2.5 percent or less, or no size change |
| `04-php-chdir` | wave 0, `chdir()` per request for a root-only PHP target | `asys_chdir` 1.000 (measured on master) to 0; `asys_total` down by 1 | `asys_chdir` still 1.000 in B |
| `05-k2-one-message` | wave 4, K2 one message per small response | `asys_sendmsg` (3.64 to 3.70 today) down by 1 or more; `s_cycles` at least 5 percent lower | `asys_sendmsg` unchanged, or NO CLAIM for `s_cycles` in both cells |

Jobs 04 and 05 carry `REV_B=pr/NNN` until their fix exists. Next after these: K1 (`s_cs` per request at c=1, 8,
32, 256 with 20 workers) and Content-Length. For Content-Length, note that `rsys_writev` is already 1.000 per
request for hello.php, so the "separate tail write" must first be shown on a response that has one.

## Bisecting a regression

`tools/perf-bisect.sh GOOD BAD` finds the first commit at which one metric rose. It lists
`git rev-list --reverse GOOD..BAD` in a FreeUnit checkout (`--repo`, else `$UNIT_REPO` or `$UNIT_SRC`; use tags or SHAs that
resolve locally, the clone is shallow), then bisects. Each step writes a job file (`REV_A` is GOOD, `REV_B` the
candidate, one cell `hello-c32`, `WARM=2 DUR=8 REPS=2 PERF=1 STRACE=0`, `PRIMARY` the metric) and runs
`tools/perf-job.sh` on it, so every step is a full lab job (about 7 minutes of cells plus builds, with its own null
phase). 314 commits need 9 steps at most. BAD is the last candidate and is never measured.

```sh
tools/perf-bisect.sh --dry-run 1.36.1 1.37.0        # list facts, first job file, the all-GOOD and all-BAD paths
tools/perf-bisect.sh --threshold 5 --max-steps 3 1.36.1 1.37.0
```

A step reads `summary.json` of its run: the `ab` row of the cell and metric, fields `verdict`, `delta_pct`,
`ci_low`, `ci_high`, `null_band_pct`. BAD is verdict `CLAIM` with `delta_pct` over `--threshold` (default 5).
GOOD is `CLAIM` or `NO CLAIM` with `delta_pct` at or under it. Everything else is RETRY: exit 75 (BUSY),
`NOISY`, `INVALID`, `NO DATA`, `NO NULL GATE`, or a delta over the threshold that is not a claim. The driver then
stops and guesses nothing. A candidate that does not build (`build B failed` in the run's `error` file) is SKIP:
the next probe is the nearest untested commit.

The log is `run/perf-lab/bisect-GOOD-BAD-DATE.log`, one tab-separated line per step: time, index, sha, verdict,
delta, CI, null band, `r_instructions` delta, run directory, subject. Running the same command again reads the
newest log for that pair and continues; RETRY lines are ignored, so that commit is measured again. Exit codes: 0
culprit found (prints the commit and the last measured GOOD), 2 inconclusive (names the narrowest range; also
`--max-steps` reached, or a GOOD found after a BAD), 75 box busy or retry, 1 lab error.

If the list holds merge commits, the script says so. The list is a linear walk, not history; a culprit on a merged
side branch can show up at the merge. `PERF_JOB=<program>` replaces `tools/perf-job.sh` for tests; it must take a
job file and print the one `PERF ...` line. Do not run `perf-bisect.sh` without `--dry-run` unless you mean to
start lab jobs on the perf box.

## Files and hard-coded hosts

`tools/perf-job.sh` (local entry), `tools/perf-bisect.sh` (commit bisect over lab jobs), `tools/perf-compare.py` (math), `tools/perf-lab/remote.sh` (claim, gate,
build, release), `cells.sh` (in-container cells), `Dockerfile`, `lab.env.example`, `jobs/`, `www/`,
`test_perf_compare.py` with `testdata/fixture.tsv`. Results: `run/perf-lab/<run-id>/` locally and
`$REMOTE_WORKDIR/runs/<run-id>/` on the box, each with its own copy of the lab scripts.

Host names in tracked harness files, to clear before a public copy: comments in `tools/bench-release.sh`,
`bench-crossover.py`, `bench-startup.sh`, `unit-build`, `capget-fallback-smoke.sh`, and text in
`docs/context-priming.md`, `docs/perf/*.md`, `skills/freeunit-benchmark/references/release-ab.md`,
`skills/freeunit-release/references/packaging-deps.md`. No script embeds a host in code: `unit-build` takes `-H`, and
the `bench-*.sh` scripts run on the box itself. Host roles for the other tools are read from this same file:
`tools/hosts` resolves a role name (`build`, `alpine`, `lab`, `infer`) to a host using `BUILD_HOST`,
`ALPINE_HOST`, `PERF_HOST` and `INFER_HOST` (see `docs/host-roles.md`), and `unit-build -H` accepts either a
role or a literal name. There is no second file; `PERF_HOST` here is also the `lab` role. The lab calls none of
these scripts, so the lab changed none of them.
