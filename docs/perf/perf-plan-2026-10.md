# FreeUnit performance plan, October 2026

Written 2026-10-06 from five research reports in this directory. Code facts are at
`origin/master` 47d2f933. Benchmark facts are from the Yii3 run of 2026-10-05
(https://yii3-benchmarks.github.io/app-api/). Nothing here has been built or measured yet by us.
Every number marked "estimate" needs the measurement named next to it.

Companion documents:

- `yii3-benchmark-setup-2026-10-05.md` and `yii3-benchmark-stages-2026-10-05.csv`: how the benchmark ran FreeUnit.
- `rapira-architecture-2026-10.md`: the current state of Rapira 0.9.0, for later comparison.
- `freeunit-php-path-2026-10.md`: the 27-step request path, the cost split, the worker-mode design, 13 ranked changes.
- `php-worker-mode-api-survey-2026-10.md`: the worker APIs of FrankenPHP, Rapira, RoadRunner and OxPHP; adoption numbers.
- `csrc-map.md`: how to load router, libunit and port IPC into one model context for cross-subsystem analysis.
- `release-plan-1.37.1.md`: the point release of the week of 2026-10-12.

## 1. Where FreeUnit stands

| Runtime, `/` endpoint | rps at cap | CPU per request | mode |
|---|---|---|---|
| Rapira dispatcher | 111,499 | 0.17 ms | persistent app, no superglobals |
| Rapira worker | 102,703 | 0.17 ms | persistent app |
| OxPHP worker | 79,785 | | persistent app |
| FrankenPHP worker | 66,624 | | persistent app |
| RoadRunner | 45,258 | | persistent app, PSR-7 over pipes |
| FreeUnit 1.37.0 | 11,445 | 1.78 ms | script per request |
| FrankenPHP classic | 10,562 | | script per request |
| PHP-FPM + nginx | 10,264 | | script per request |
| Rapira classic | 9,138 | | script per request |

Facts that decide the plan:

1. Every runtime saturates the same 20 PHP workers at about 19.5 to 20.5 cores. Peak rps is cores divided by CPU
   per request. FreeUnit's CPU per request splits into about 1.45 to 1.55 ms of Yii bootstrap (81 to 87 percent,
   including a Valkey round trip per request), 0.08 to 0.11 ms of router plus IPC (5 to 6 percent), and about
   1 percent of scheduling.
2. FreeUnit is already the fastest script-per-request runtime on both endpoints. No router change moves the
   classic number by more than a few percent, because the router runs on cores the 20 workers do not use.
3. The only change that closes the 10x is a persistent worker mode for PHP. The report estimates 80 to 90k rps
   for a FreeUnit worker mode as the code is today, and 90 to 100k with the IPC changes of phase 2.
4. In worker mode the router cost becomes visible: at 100k rps a 58 us router needs 5.8 cores on a 16-core host
   that also runs 20 workers and the load generator. That is why the IPC items matter after worker mode, not before.
5. The benchmark setup was fair. FreeUnit got a default release build, 20 fixed processes and the shared php.ini.
   Two small things cost FreeUnit without being bugs: the `share` route probes `/app/public/index.html` on every
   `/` request (ENOENT, then the fallback), and the SAPI name "cli-server" makes index.php run its built-in-server
   branch. Both are configuration or compatibility, not speed.
6. Rapira has gaps FreeUnit does not have: no TLS on HTTP, no HTTP/2, no WebSocket, no compression, no access log,
   no routing, no proxy, no isolation, static configuration, whole-body buffering in both directions. Its benchmark
   read errors come from `max_requests` recycling closing idle keep-alive connections. FreeUnit's router owns the
   connections, so it had zero errors with the same `limits.requests`. This is the positioning: the same persistent
   speed with the operational layer Rapira lacks.

## 2. Goal

By 2026-11-15, a FreeUnit scenario in the Yii3 benchmark that runs the Yii application in worker mode and reaches
the FrankenPHP worker number (about 67k rps) or better, with zero errors. By the end of 2026, the Rapira worker
number (about 100k rps). The DB endpoint is PostgreSQL-bound at about 12.5 to 12.9k for every worker runtime; the
goal there is parity, which worker mode alone gives.

## 3. Phases

### Phase 0: 1.37.1, week of 2026-10-12. Small, safe, and the market-facing fixes

Code, all low risk, each with its own test and strace or perf evidence:

| item | where | size | gain | test |
|---|---|---|---|---|
| No `epoll_ctl` MOD on every port drain; enable read only when it is disabled | src/nxt_port_socket.c:2251 | 3 lines | 1 to 3 us per request, estimate. DONE 2026-10-06 in https://github.com/freeunitorg/freeunit/pull/592 (2e334334): about 4 MODs per request, not 1 (7998 to 2 for 2000 requests); the epoll_wait drop seen under strace was timing, not a saving | `strace -c` on the router before and after; full suite |
| Pool junk fill `memset(0x5A)` on every small free in release builds; make it debug-only like the shm poison in 76c406b3 | src/nxt_mp.c:144, :924, :945 | 4 lines | under 0.5 us, estimate | suite; ASan run |
| A target with only `root` still calls `chdir()` on every request | src/nxt_php_sapi.c:1241 | 10 lines | 1 syscall per request. REJECTED 2026-10-07: path compare breaks symlink swaps, stat() costs the same as chdir(); see [pr601-chdir-vs-stat-2026-10.md](pr601-chdir-vs-stat-2026-10.md) and https://github.com/freeunitorg/freeunit/pull/601. 601 merged as tests only (b2360a1d). The review found the same stale-directory bug in `script` targets since 1.18.0; fixed in https://github.com/freeunitorg/freeunit/pull/604, which sets the directory on every request | strace; test_php_targets.py |

Not code, but they move market share more than any code in a point release:

- The 22 old-style ghcr tags without the hyphen point at 1.35.5 (21) and 1.35.2 (`latest-jsc11`). yiisoft/docs cites
  `latest-php8.5`. Re-point them from release-docker.yml and ask yiisoft/docs to use the hyphenated name. Details in
  `release-plan-1.37.1.md` section 4.
- Send the benchmark maintainer a FreeUnit config fix: route `/` straight to the app, keep `share` for assets only.
  Also tell them the entrypoint note is wrong (`pkg/**` is export-ignored in every tarball, the published images
  carry the entrypoint).
- Fix docs/php-lifecycle.md in this harness: it says statics and singletons persist between requests. They do not.

### Phase 1: 1.38.0, target branch by 2026-10-26. PHP worker mode

The design is in `freeunit-php-path-2026-10.md` section 3, the API in `php-worker-mode-api-survey-2026-10.md`
section 8. One PR, about 300 lines in src/nxt_php_sapi.c, 20 lines of config in 3 files, 150 lines of tests. libunit
and the router do not change.

- Config: a PHP target member `"worker": true`. First version: one worker target per app.
- PHP surface, FrankenPHP semantics so existing runners are a copy with a name change:
  `unit_handle_request(callable $handler): bool`, `unit_is_worker(): bool`, `unit_finish_request(): bool`,
  `unit_request_headers(): array`, `unit_response_headers(): array|bool`. The handler takes no arguments, reads
  superglobals, answers with `echo` and `header()`. `false` means leave the loop: QUIT, reconfigure or
  `limits.requests`.
- Mechanism: the worker script runs as one long PHP request. `unit_handle_request` loops on `nxt_unit_run_once`
  with a handler that only parks the request, does the partial activate (output, SAPI, superglobals, timeout), calls
  the callable, does the partial deactivate, and ends the request. A fatal error ends the script and the router
  respawns the process from the prototype.
- Decision for the maintainer: this trades full `php_request_shutdown` isolation for speed, as FrankenPHP does.
  Request-scoped extension state must be reset by hand and user code can leak state between requests. Recommended:
  take the trade, document it, ship `gc_collect_cycles()` advice.
- Tests that must fail before the change: superglobals do not leak between requests; `header()` resets; an uncaught
  exception gives 500; a fatal error respawns; `limits.requests` recycles; `fastcgi_finish_request` inside the loop;
  output buffering on and off; `flush()` keeps its 1.37.0 behaviour.
- Open question before coding: whether to define the `frankenphp_*` names as an opt-in alias layer. It makes Symfony
  7.4 and the Yii runner work with zero adapter PRs, but code that sees `frankenphp_handle_request` may also call
  Mercure or `frankenphp_log`. Recommended: ship the `unit_*` names first, measure adapter uptake, decide the alias
  layer in 1.38.1.

Adapters, in parallel with the PR, each a small package or PR:

| framework | what | size | where |
|---|---|---|---|
| Yii3 | copy yiisoft/yii-runner-frankenphp, change the detection and loop call | 253 lines, about 10 changed | PR to yiisoft to detect both, or `freeunit/yii-runner-unit` |
| Symfony 7.4+ | `runtime/unit-symfony` after `runtime/frankenphp-symfony` | 63 + 38 lines | php-runtime org PR, or our own package first |
| Laravel Octane | `bin/unit-worker.php` plus client | about 150 lines; a start command is 300 to 400 more | separate package first; Octane's server list is hard-coded |
| Slim, Mezzio, PSR-15 | a documented worker script | docs | unit-docs |

Then: a PR to yii3-benchmarks/app-api adding a `freeunit-worker` scenario, so the next public run shows it.

### The 1.38.0 headline: schedules plus worker mode

Schedules are PR 452 (https://github.com/freeunitorg/freeunit/pull/452, head 03e363a1, 4105 lines, docs/adr/0004-schedules.md):
the router sends a GET to `applications/<app>[/<target>]` every `interval` seconds, with `jitter`, `timeout`,
`overlap: skip`, trusted headers, `run_on_start`, `/status/schedules`, access log and an OTel span per run. Upstream
nginx Unit declined this scope (issue 1528), so it is already a fork differentiator.

Why the two together are more than the sum:

- Worker mode alone is parity. By 2026 every serious PHP server has it. It makes FreeUnit eligible, not different.
- Schedules alone pay a full bootstrap per tick in classic mode, and a long run occupies one of the traffic workers.
  That is the mechanism of issue 321 (Drupal's automated_cron hijacks a visitor's worker for up to 240 s). The ADR
  itself lists "a worker loop (not possible in PHP)" as a reason schedules exist; worker mode makes that sentence obsolete.
- Together: the tick is a request into a warm application. The cron run costs the handler time, not a bootstrap.
  The run is bounded by `timeout`, counted in `/status`, logged and traced, and needs no cron container, no sidecar,
  no CronJob object, no `schedule:work` process. The compose file has one service.
- Nobody has this. FrankenPHP has no scheduler. Rapira has none. RoadRunner's cron is a community plugin
  (https://github.com/roadrunner-plugins/cron, 26 stars) that spawns a shell command per tick, so a cold `php artisan`
  each time. The combination is the only design where a scheduled tick is a 0.2 ms request to a persistent app.

Design consequences the combination forces, each of which must be settled before 1.38.0 ships both:

1. Capacity separation. PHP targets share the application's processes, so a `jobs` target does not isolate anything.
   The recipe is a second application with the same code, its own worker script and `processes: 1` (a scalar sets
   spare and max to 1, so the idle reaper never touches it; never `idle_timeout: 0`). Schedules pass to that app.
   Traffic never waits behind a cron run, request-scoped state leaked by a job never reaches a visitor, and
   `limits.timeout` can be long for jobs and short for web. Per-target process pools would be the clean form; that
   is a router change for a later release.
2. Abandoned workers. On a schedule timeout the router abandons the worker: it does not stop it and does not return
   it to the idle pool (docs/schedules.md "Timeout"). In worker mode that is a persistent process lost until PHP's own
   `max_execution_time` kills the handler, the script dies and the router respawns the process from the prototype.
   The worker-mode design re-arms `zend_set_timeout()` per request, which is what makes this recover. Required test:
   a schedule run over `timeout` in a worker-mode app with `processes: 1`; the process is replaced and the next tick
   runs. Required doc line: set `max_execution_time` at or below the schedule `timeout` in the jobs app.
3. `unit_finish_request()` in a schedule handler. It ends the run while the handler goes on, so `overlap: skip` no
   longer covers the trailing work. In worker mode the process is still inside the handler, so the next tick waits in
   the app queue rather than overlapping; with `processes: 1` runs still serialize. Document: do not call
   `unit_finish_request()` from a scheduled handler.
4. Start-up. `run_on_start` fires about 1 s after the config is applied, while a worker-mode process may still be
   bootstrapping the application; the request waits in the queue and that wait counts against the run's `timeout`.
   Fine for idempotent endpoints; say it in the docs. Schedule state is in memory only, so a restart means
   bootstrap plus an immediate tick.
5. Order of landing. The two PRs touch disjoint files (src/nxt_router_schedule.c, src/nxt_http_devnull.c versus
   src/nxt_php_sapi.c), so they are reviewed and merged independently; 452 first because it has no dependency and
   its tests exist, worker mode second, then one combined test (test_schedules_php.py with a worker target: a static
   counter proves the application stayed in memory across ticks, and the per-run `last_duration_ms` is the demo number).

Framework recipes, which are the market-share part:

| framework | today | with schedules plus worker mode |
|---|---|---|
| Drupal | automated_cron in a visitor request (issue 321), or system cron plus drush | automated_cron off; schedule `/cron/KEY` to the jobs app. The product form of the 321 fix |
| WordPress | wp-cron on visitor traffic, or `DISABLE_WP_CRON` plus system cron | `DISABLE_WP_CRON`; schedule `/wp-cron.php?doing_wp_cron` |
| Laravel | `schedule:run` from cron every minute, or a `schedule:work` process beside Octane | a 5-line route calling `Artisan::call('schedule:run')`, guarded by a trusted header the schedule sends; `interval: 60` |
| Symfony Scheduler | `messenger:consume scheduler_default` as a long process | a route that runs the scheduler transport once per tick; recipe to validate |

Proof before the claim goes public: a Drupal run on the reference bench host with four cells: classic versus worker, shared versus
dedicated jobs app. Publish the cron tick cost (`last_duration_ms`) and the visitor p99 during a cron run.

### Phase 2: 1.38.x, November. IPC cost in worker mode

Only after phase 1 is measurable. Ranked by estimated gain; details and line numbers in
`freeunit-php-path-2026-10.md` section 4.

| # | change | estimate per request | size | risk |
|---|---|---|---|---|
| K2 | one message per small PHP response: header, body and `last` in one `nxt_unit_buf_send_done` | 8 to 15 us | 60 to 90 lines | low to medium: keep flush() semantics |
| K1 | one wake per request: ring one idle process's own port, or EPOLLEXCLUSIVE in libunit | 25 to 75 us at low and mid load, near 0 at saturation | 80 to 150 lines | medium: a lost wake stalls a request |
| 3 | Content-Length instead of chunked when the response is one message | 1 to 3 us, fewer bytes | 20 to 40 lines | low |
| 7 | request headers in a small buffer, not a 16 KiB shm chunk under a mutex; drop the O(n^2) duplicate merge | 1 to 3 us | 60 to 120 lines | medium |
| 11 | one shm chunk per response instead of per write (the 14 KiB DB response) | 2 to 5 us on large responses | 40 lines | medium |
| 4 | ACK carried in the first response message | 3 to 6 us | 50 to 100 lines | high: message order, see the 1.37.0 fix 66048b7b |
| 6 | send the last message before `php_request_shutdown` | latency only | 20 to 30 lines | medium |
| 8, 9, 10 | reuse the request pool; no script `fopen` per request; header buffer without malloc | 1 to 4 us each | small | low to medium |
| 12 | request release on a lowest-priority work queue, not a 0 ms timer (src/nxt_router.c:8776): today it costs one empty `epoll_wait(..., 0)` per request | 0.5 to 1 us at c=1, about 0 at saturation | ~30 lines | medium: request lifetime order, see item 8 |
| 13 | one `recvmmsg` per port drain instead of `recvmsg` plus the EAGAIN probe (src/nxt_port_socket.c:2190-2270); the probe cannot simply be skipped, because marker-less datagrams would strand under edge-triggered epoll | up to 4 syscalls at c=1, about 1 at saturation; not counted | 100 to 200 lines | medium: marker pairing, and the single suspend slot must become a list |
| 14 | libunit: after a READ_QUEUE marker finds the queue empty, return AGAIN without a second `recvmsg`; read the marker without `poll` when `q->notified` is set (src/nxt_unit.c:7779-7820) | 1 to 2 syscalls in the app | 20 to 40 lines | low |

Items 12 to 14 come from a Fable review of PR 592 (2026-10-07). PR 592 (2e334334) removed the per-drain `EPOLL_CTL_MOD`:
about 4 per request, against the 1 that Phase 0 estimated. Counted per request at c=1, the app sends 3 datagrams: ack, headers, body.
A 4th wake carries the queued `done`. Each drain is one data `recvmsg` plus one EAGAIN `recvmsg`. K2 no longer saves an `epoll_ctl`.
The plan's "timers make no syscall per request" is wrong; see item 12.

K1 matters most for latency at low concurrency, which is what a user sees first. K2 matters most for throughput at
saturation. Do K2 first because it is self-contained and testable with `strace -c`.

### Phase 3: measurement and the public story

- A `type: external` C application on libunit as a lab floor: it measures FreeUnit's own per-request cost with no
  interpreter. It is not a benchmark entry; it is the control for every phase 2 change.
- Rerun protocol (`rapira-architecture-2026-10.md` section 7): same host class, wrkx at the same pin, a trivial-script
  control per runtime, `max_requests = 0` for Rapira to confirm the read-error cause, CPU per request as the primary
  metric, 8 cells plus a swapped rerun for any A/B claim.
- Publish a short page: "FreeUnit worker mode for PHP" with the number, the config and the one-paragraph trade-off.
  Submit the Symfony runtime component page and the Laravel Octane docs listings once the adapters exist.

## 4. The C-source map as an analysis tool

`csrc-map.md` and `tools/build-pack.py` (tree-sitter, byte-identical rebuilds, `--rev --level 0|1|2 --extra`).
Measured at 47d2f933: nxt_router.c 9,284 lines, nxt_unit.c 8,712, the five port files 6,502; 27,492 lines and
16,390 non-comment lines together, so the core is not 10k. Strippable noise (debug, asserts, licenses, NXT_TESTS
blocks) is 4.7 percent. Token sizes by the Read tool: L0 skeleton 101k, L1 hot path 256k, L2 full 371k. All three
fit a 1M window; use L2 by default and add nxt_application.c (`--extra`), which the first validation missed.

Validation: a Sonnet subagent with only the L1 pack answered the three known-answer questions correctly with
file:line citations (NXT_AGAIN needs `port->queue != NULL` and the router main port has no queue; the port error
handler drains `port->messages` and tears nothing down; a message enters the queue only when `stream == 0 || last`).
Its two data-structure proposals graded B- and C+: embedding the mmap bufs in the request slab saves under 5 percent
of the IPC cost because the objects already sit on free lists, and caching the response-port lookup is safe in
libunit (`lib->mutex` plus an lvlhsh find per request, src/nxt_unit.c:1851) but unsafe in the router, where
`app->port_hash` changes under `app->mutex`. Conclusion: the map works for flow and invariant questions; for
optimization proposals it needs a measured profile beside it, or it proposes changes below the noise floor.
Use it for the phase 2 reviews (K1, K2, item 4), where the three subsystems must be read together.

## 5. Measurement rules

- Benchmarks run serially on the perf box through the perf lab (`perf-lab.md`); its claim and idle gate
  replace the older "main session only, never in agents" rule. Primary metric under `powersave`: instructions or cycles per request.
- Primary metric: server CPU per request from `perf stat` or cgroup CPU, not rps alone.
- Before any change: `perf stat -e context-switches,syscalls:sys_enter_epoll_ctl` per request at c=1, 8, 32, 256
  with 20 workers, HTTP/1.1 keep-alive, oha or wrk, never ab.
- Every estimate in section 3 is replaced by a measured number in the PR body before merge.

## 6. Decisions needed from the maintainer

1. Worker mode in 1.38.0 with the partial-reset model (recommended yes).
2. `unit_*` names only, or also the `frankenphp_*` alias layer (recommended: names first, alias later).
3. Who owns the adapter packages: our org (`freeunitorg/`) or PRs to yiisoft, php-runtime and laravel first
   (recommended: our org for Symfony and Octane, a PR to yiisoft because the runner is 10 changed lines).
4. Whether to contact the benchmark maintainer now with the config fix and the entrypoint correction, or only
   with the worker scenario PR (recommended: now, it is a goodwill message and costs nothing).
