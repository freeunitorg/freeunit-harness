# Drupal application-server benchmark: plan (2026-10-07)

Planning pass, nothing built or measured. Model: https://yii3-benchmarks.github.io/app-api/ (repo
https://github.com/yii3-benchmarks/app-api, read at 6e67cb5; see `yii3-benchmark-setup-2026-10-05.md`).
Every number in this file is either cited to a file and line or marked as a claim to measure. A claim
without a measurement does not go on the published page.

Sources read for this plan, all in this directory unless a path says otherwise:
`yii3-benchmark-setup-2026-10-05.md`, `yii3-benchmark-stages-2026-10-05.csv`, `rapira-architecture-2026-10.md`
section 7, `perf-plan-2026-10.md`, `perf-lab.md`, `../../tools/perf-lab/cells.sh`, the warm-fork gist
https://gist.github.com/andypost/f5982635f996e48b3973282602c48eae (files `readme.md`, `setup-stand.sh`,
`fork_spawn.php`, `drupal_boot_cron.php`, `drupal_boot_queue.php`, `drupal_stub.py`), Alpine MR
https://gitlab.alpinelinux.org/alpine/aports/-/merge_requests/109208 (head cfeddb5727f, read through the GitLab API).

## 1. Goal

Publish one page, rebuilt from committed raw data, that compares the same Drupal site on three runtimes:
Apache with mod_php, nginx with PHP-FPM, and FreeUnit. The page answers four questions with one chart each:

1. Front page, anonymous: requests per second at the cap, p50 and p99 below the cap, server CPU per request.
2. Front page, authenticated: the same four numbers for a request that bypasses Drupal's page cache.
3. Cron tick: wall time and CPU of one `cron` run, four ways (section 3.3).
4. Queue drain: wall time and CPU for 50 items, the same four ways.

Why these and not the Yii3 pair: the Yii3 run showed that classic runtimes differ by under 10 percent on the
same app (`yii3-benchmark-setup-2026-10-05.md:329-333`), so a Drupal page that only reran "front page" would
repeat that finding. The cron and queue workloads are where FreeUnit has a story to test: issue 321 (closed,
fixed in 1.37.0 by `nxt_unit_request_done_detached()`, `src/nxt_php_sapi.c:282` at origin/master d88f45e13),
schedules (PR 452, head 7356b7a4, open, mergeable), and later PHP worker mode (tracker issue 596; no PR exists
yet on 2026-10-07).

Non-goals: a "fastest PHP server" ranking; Rapira, FrankenPHP, RoadRunner (they are in the Yii3 page already;
add them only after the three base runtimes publish); HTTPS; HTTP/2 (FreeUnit has none).

## 2. Repository layout

Name: `freeunitorg/benchmarks-drupal`. The org already has `freeunitorg/benchmarks-yii3` (a fork of app-api
from 2026-08-07, default branch `frankenphp-classic`, no Pages, last push 2026-08-24), so the name pattern
exists. Recommendation: a new repository, not a fork of app-api. The app-api tooling is wrkx-specific and the
report renderer reads wrkx logs (`tools/compile-wrkx-results.php`, `tools/render-benchmark-report.php`); we
measure with oha and the perf-lab TSV, so little carries over except the shape.

```
benchmarks-drupal/
  README.md                  what, how to run, how to read; links the published page
  Makefile                   bench, bench-all, report, publish
  site/                      composer project per branch; see 2.1
    11.4/  12.0/
  docker/
    Dockerfile.php           one PHP image (php:8.5-cli base), extensions, php-production.ini; FROM for all runtimes
    Dockerfile.apache        httpd prefork + mod_php on the same PHP build
    Dockerfile.nginx-fpm     nginx + php-fpm
    Dockerfile.freeunit      unitd + php module, 1.37.0 (section 4.1)
    runtimes/                apache.conf, nginx.conf, php-fpm.conf, freeunit.json, freeunit-worker.json (phase 1)
    compose.yml              profiles: apache, nginx-fpm, freeunit; mariadb per run
  modules/fu_queue_test/     the gist's #[QueueWorker] module (1 ms of work per item), copied verbatim
  apps/                      fork_spawn.php, drupal_boot_cron.php, drupal_boot_queue.php, drupal_stub.py from the gist
  bench/
    cells.sh                 the perf-lab cell runner, generalised: WORKLOAD=drupal-front|drupal-auth|cron|queue
    jobs/                    perf-lab job files, one per published chart
    perf-compare.py          copy of tools/perf-compare.py with its test and fixture
  results/<run-id>/          results.tsv, summary.json, meta.txt, compare.txt, unit/nginx/apache config as run
  report/                    render.py: results/*/summary.json -> index.html; no numbers in templates
  .github/workflows/pages.yml  renders report/ on push to master, pushes to gh-pages
```

### 2.1 Drupal branches and how to build the site

Verified on the GitHub mirror `drupal/drupal` on 2026-10-07:

| branch | tip | latest tag | note |
|---|---|---|---|
| `11.x` | 89f3e765b (2026-10-05) | | development |
| `11.4.x` | 0077935ed (2026-10-03) | 11.4.8 | current stable series |
| `main` | 47430c368 (2026-10-06) | 12.0.0-beta1 | Drupal 12 development; there is no `12.x` branch |
| `12.0.x` | cb1b6c0c7 (2026-10-06) | | release branch for 12.0.0 |

Core profiles on both lines: `demo_umami`, `minimal`, `standard` (`core/profiles` on `11.x` and `main`).
`demo_umami` is still marked "(Experimental)" in `demo_umami.info.yml`.

Pin tags, never branches: `drupal/recommended-project:11.4.8` and `drupal/recommended-project:12.0.0-beta1`
(12.0.0 is planned for the week of 2026-12-07; rebuild the 12 site at 12.0.0 and keep both result sets).
Build with `composer create-project` inside the PHP image, commit `composer.lock`, and record the
`drupal/core` version in `meta.txt`. The Yii3 run did not commit its lock file and so its runner versions are
unknown (`yii3-benchmark-setup-2026-10-05.md:152-154`); do not repeat that.

Profiles: install `demo_umami` for the content-bearing site. "Full CMS" in the brief can mean the `standard`
profile or the separate product `drupal/cms`. Recommendation: `demo_umami` first on both lines; `drupal/cms`
as a third site only after the first page publishes, because it adds contrib modules whose 12 compatibility is
not known today. Record the choice in `meta.txt`; never mix profiles inside one chart.

Database: one MariaDB container per run, pinned by digest, the same for every runtime. The gist stand used
SQLite; it is fine for the one-process cron and queue measurement but not for 20 concurrent workers. The
Drupal 12 floors (PHP 8.5, MariaDB 10.11) come from the 2026-10-07 research note and must be re-read from
`core/composer.json` and `core/lib/Drupal/Core/Database/Driver/mysql/Install/Tasks.php` at the pinned tag
before the Dockerfiles are written.

PHP: `php:8.5.11-cli-trixie` as in the Yii3 Dockerfile (`yii3-benchmark-setup-2026-10-05.md:55`), one build
for all three runtimes, `php-production.ini` copied from app-api (`:108-126`) with `opcache.validate_timestamps=0`
and JIT off. Drupal 11.4 needs PHP 8.3 or later and 12 needs 8.5, so 8.5 serves both lines.

## 3. Workloads

| key | request | what it measures | Drupal cache state |
|---|---|---|---|
| `front-anon` | `GET /` without cookies | server plus Drupal page cache hit: the cache bin read, the response copy | `page_cache` hit after warm-up |
| `front-auth` | `GET /` with a session cookie of one authenticated user | the full render path through `dynamic_page_cache` | page cache bypassed |
| `cron` | one cron run | bootstrap plus `\Drupal::service('cron')->run()` | n/a |
| `queue` | drain 50 items of `fu_queue_test` | claim, `processItem()`, delete per item | n/a |

### 3.1 front-anon

Every runtime must serve the same bytes: compare `Content-Length` and a body hash in preflight. Disable
`big_pipe` for the anonymous page or the body is chunked and the hash differs per runtime; record the module
state. Expect `X-Drupal-Cache: HIT` on every measured request; a MISS in the measured window makes the cell
INVALID (the same guard class as `alerts` in `perf-lab.md:134`).

### 3.2 front-auth

Log one user in through the browser flow once per run, store the cookie, and send it on every connection. The
20 workers then all render for the same session; that is a shared-state hot spot in `sessions` and
`cache_dynamic_page_cache`, equal for all runtimes. Run it after `front-anon`, and record `X-Drupal-Dynamic-Cache`
on the first and last request of the window.

### 3.3 cron and queue: four arms

The gist measured two arms, cold CLI against warm fork, and its headline ratios (readme "Measured results") are
Unit 1.36.1, PHP 8.4.25, Drupal 11.4.6 on SQLite, one small stand. The 2026-09-12 measurements in the issue 321
thread found that a warm fork is not faster than a plain HTTP cron request on an already warm worker, and that
`opcache` without `file_cache` makes a one-shot CLI cron slower, not faster. Neither set was taken with the
controls below, so neither goes on the page. The arms to run, all on the same site and database state:

| arm | trigger | process | maps to |
|---|---|---|---|
| A `drush-cold` | `drush core:cron` and `drush queue:run fu_queue_test` from `drupal_stub.py` with `TRIGGER=drush` | new CLI process per tick, cold opcache | the default of most hosting guides |
| B `http-detached` | `GET /cron/{key}` on each runtime; for FreeUnit the request detaches via `fastcgi_finish_request()` (`src/nxt_php_sapi.c:231-282`) | one of the 20 web workers | what `automated_cron` does, and the issue 321 case |
| C `fork-warm` | `GET /?job=cron` on `fork_spawn.php` from the gist | warm-opcache grandchild of a Unit worker | the gist's proposal; FreeUnit only |
| D `schedule` (phase 2) | PR 452 schedule into a second application with `processes: 1` | dedicated worker | the product form from `perf-plan-2026-10.md:120-177` |

Metrics per arm: trigger latency as seen by the ticker, job wall time from the boot script's `total_s` and
`kernel_s`/`cron_s` split (`drupal_boot_cron.php:57-60`), job CPU from `/proc/<pid>/schedstat` of the process
that ran it, RSS and PSS of that process at the end, process count before and after 20 ticks (the gist found
2 leaked processes per tick before its `posix_kill` fix, readme "Fault found"), and for arm B the visitor p99
of `front-anon` measured during the cron run. The last one is the number issue 321 was about.

Controls the gist lacked: `QUEUE_FILL=50` before every drain, run from the same script (`drupal_boot_queue.php:52-58`);
`drush cache:rebuild` never inside a window; the 900 s cron lock released between ticks or every second tick
is a no-op (`\Drupal::lock()` on `cron`); opcache status `cached_scripts` and `hits` recorded per tick
(`fork_spawn.php:56-61`) to prove warm or cold rather than assume it.

### 3.4 Mapping to the gist's files

`drupal_stub.py` already switches between arm A (`TRIGGER=drush`) and arm C (`TRIGGER=http`, `SPAWN_URL`) and
logs one line per tick to `STUB_LOG`. Arm B needs one more `TRIGGER=cron-url` value that GETs `/cron/{key}`;
that is a 10-line change. `drupal_boot_cron.php` and `drupal_boot_queue.php` run unchanged in arms A and C
(`PHP_SAPI === 'cli' && FORK_CHILD !== '1'` selects the output path, `:65` and `:100`). Arm B uses Drupal's own
`system.cron` route and gets its timing from the access log and schedstat, not from the boot script.

## 4. Runtime configuration

All three: 20 PHP workers, recycle after 10000 requests, 8 MiB body limit, 30 s timeouts, no access log in the
measured window, no compression (oha sends no `Accept-Encoding` unless told), the same `php-production.ini`,
the same document root, the same `settings.php` with `$settings['trusted_host_patterns']` open.

### 4.1 FreeUnit 1.37.0

Start from `docker/runtimes/freeunit.json` of app-api (`yii3-benchmark-setup-2026-10-05.md:25-37`) and change:

- Routes: Drupal needs `index.php` to answer every path that is not a file. Keep `share "/app/web$uri"` with
  `fallback` to the `index` target. Add a first route that passes `/cron/*`, `/user/*`, `/node/*` and `/` straight
  to the app: the Yii3 analysis showed one failed `open()` per request on the `share` step
  (`yii3-benchmark-setup-2026-10-05.md:43-46`); measure it as a separate cell (section 5.4) before deciding.
- `settings.listen_threads`: set it to the size of `SERVER_CPUS` rather than the default `nxt_ncpu`
  (`src/nxt_router.c:3099` at 1.37.0, `:49-50` of the setup doc).
- `processes: 20` fixed, `limits.requests: 10000`, `limits.timeout: 30`, as in Yii3.
- `options.admin`: `opcache.enable=1`, `opcache.validate_timestamps=0` as the lab does (`tools/perf-lab/cells.sh:71-75`).
  Note that `options.admin` applies after `php_module_startup`, so it cannot load a zend_extension; opcache is
  already compiled in the Docker PHP.
- Cron arm C: a second application `jobs` with `processes: 1`, `root` at `apps/`, `pcntl` and `posix` present.

Binary: two choices, record which one a result set used.

1. Build from the 1.37.0 release tarball inside `Dockerfile.freeunit`, as app-api does (`:55-62`), sha256
   `8ae237d5a32d77bb8a7426d844ce75aeb18583a4e47318f55e17839d865fad8c`. Fewer moving parts; this is the default.
2. Alpine packages from MR 109208. The MR is a draft (`opened`, `draft: true`, head cfeddb5727f, pipeline 475378
   failed on s390x only, `alpine-job-2541985-triage.md`). Edge `community` still carries `freeunit-1.36.1-r0` and
   `freeunit-php85-1.36.1-r0` on 2026-10-07, so there is no 1.37.0 apk to download; it must be built with
   `abuild -r` from the MR branch in the aports container on the Docker box. The APKBUILD builds php83, php84 and
   php85 modules with `--hardening=default` (`community/freeunit/APKBUILD:11-13,102-107` at cfeddb5727f). Use this
   path when the point is "the distribution binary", and never mix the two inside one chart.

### 4.2 nginx and PHP-FPM

Copy `nginx.conf` and `php-fpm.conf` from app-api (read at 6e67cb5): `pm = static`, `pm.max_children = 20`,
`pm.max_requests = 10000`, `access_log off`. Change the `location /` to Drupal's documented rewrite
(`try_files $uri /index.php?$query_string`), add the `location ~ '\.php$|^/update.php'` block from Drupal's
nginx recipe, and set `gzip off` so the three runtimes send the same bytes. FPM has no equivalent of the
detach-and-count fix of issue 321; `fastcgi_finish_request()` detaches but FPM keeps counting the worker busy,
which is correct and is what arm B compares against.

### 4.3 Apache with mod_php

`httpd` prefork MPM (mod_php needs a non-threaded MPM with NTS PHP, which `php:8.5-cli` is),
`MaxRequestWorkers 20`, `ServerLimit 20`, `StartServers 20`, `MaxConnectionsPerChild 10000`, `KeepAlive On`,
`MaxKeepAliveRequests 10000`, `AllowOverride All` for Drupal's `.htaccess`, or copy its rewrite rules into the
vhost and set `AllowOverride None` and measure both. `LogLevel warn`, no `CustomLog`. Apache has no detach:
`fastcgi_finish_request()` does not exist under mod_php, so arm B on Apache holds the connection for the whole
cron run; record that as a result, not a defect of the harness.

## 5. Measurement protocol

Reuse the perf lab (`perf-lab.md`) rather than the app-api scripts: it already has the claim and idle gate,
the swapped-slot block design, the null phase, `schedstat` CPU and `perf stat` counters, and a verdict tool
with a tested fixture.

### 5.1 Box, pinning, gate

`PERF_HOST` from `run/lab.env`, `SERVER_CPUS` for the runtime container, `LOAD_CPUS` for oha, SMT siblings of
`SERVER_CPUS` idle (`perf-lab.md:33`). The database container gets its own third set; add `DB_CPUS` to
`lab.env.example`. Idle gate at 5 percent, `PERF_WINDOW` for anything over 15 minutes (`:34-36`). A full matrix
(2 Drupal lines x 3 runtimes x 2 page workloads x 2 blocks) is over 15 minutes, so it runs in the window.

Governor is `powersave` with EPP `balance_power` on both boxes and cannot be changed without root
(`perf-lab.md:85-86`). Measured consequence: `server_us` null band 33 to 36 percent, `rps` 48 percent,
instruction counters 2.5 percent, at 2 s cells (`:140-152`). So the primary metric for A/B claims is
`s_instructions`; `rps`, `p50_ms`, `p99_ms` and `server_us` are reported as the published headline only with
their null band beside them, and only once job 01 has measured the bands at 8 s cells (`:233-235`).

### 5.2 Load generator

oha, pinned on `LOAD_CPUS`, `-z DUR -w` closed loop for the cap search and `-q RATE` for the latency cells
(`perf-lab.md:105,113-114`). The Yii3 page used wrkx with 13 ramp stages of 30 s (`yii3-benchmark-setup-2026-10-05.md:195-197`);
reproduce the shape with a `RATE` list, for example 500 1000 2000 4000 8000, and define the cap as the first
rate whose completed rps is under 95 percent of the target, as `detectStageRpsCap` does (`:206-207`). Keep
latency from rates below the cap only; past it the number is queue depth (`:204-205,348-349`). wrk2 and wrkx
are not installed on the perf box (`perf-lab.md:131`); do not add a second tool until oha's `-q` behaviour is
compared with wrkx on one runtime once.

Concurrency: `CONNS=32` for the cap cells (above 20 workers, so the queue is non-empty) and `CONNS=6` for the
latency cells, the two sizes job 01 already uses.

### 5.3 Metrics per cell

From `cells.sh` as is: `rps`, `p50_ms`, `p99_ms`, `server_us` (sum of `schedstat` over the server process tree),
`s_instructions`, `s_cycles`, `s_cs`, guards `ok`, `bad`, `alerts`. New for this benchmark: `db_us` (schedstat
of the MariaDB container, so a runtime that drives the database harder is visible), `bytes` per response, and
the two Drupal cache headers. For Apache and nginx the "server tree" is two trees (nginx plus php-fpm; httpd
alone); sum them, as the Yii3 sampler summed `php` plus `nginx` containers (`yii3-benchmark-setup-2026-10-05.md:208-209`).

### 5.4 One measurement per claim

| claim the page might make | cell that proves it | falsified by |
|---|---|---|
| FreeUnit serves the cached front page with less server CPU than nginx+FPM | `front-anon-c32`, `s_instructions`, null gate PASS | NO CLAIM, or a NOISY null |
| the `share` probe costs a measurable share of a cached page | FreeUnit A: share+fallback, B: direct pass; `r_instructions` | NO CLAIM at a 2.5 percent band |
| the authenticated page is Drupal-bound and the runtimes tie | `front-auth-c32`, server CPU within the null band for all three | a CLAIM in either direction; then say which runtime, not "tie" |
| a warm fork cron tick is faster than a cold drush tick | arm C vs arm A, `total_s` and job CPU, 20 ticks, `cached_scripts` stable in C | medians within the per-tick spread |
| a warm fork is faster than a plain detached HTTP cron | arm C vs arm B on FreeUnit | B at or below C (the 2026-09-12 measurement said 0.97x; re-measure) |
| a cron run no longer stalls visitors on FreeUnit 1.37.0 | arm B with 1.36.1 and 1.37.0 side by side, visitor p99 and worker count from `/status` | p99 moves the same way in both, or `/status` shows no "detached" worker |
| a fork child leaks no process | process count before and after 20 ticks | any growth |
| Apache holds the client for the whole cron | arm B latency on Apache equals `total_s` | it does not; then find what detached |
| opcache without `file_cache` slows a one-shot CLI cron | arm A with `opcache.enable_cli` 0, 1, and 1 plus `file_cache` | no ordering outside the spread |

Every A/B row carries its null phase in the same window (`perf-lab.md:113-114`). One run per cell is not
a result; the Yii3 page had one run per scenario (`yii3-benchmark-setup-2026-10-05.md:348`) and its own
rerun moved classic numbers by 1 to 3 percent (`:325`).

### 5.5 Preflight, recorded in `meta.txt`

`php -i` from inside each runtime (opcache on, `validate_timestamps=0`, no JIT; the Yii3 analysis could not
prove opcache was on in its FreeUnit image, `:346-347`), `unitd --version`, `nginx -V`, `httpd -V`,
`php-fpm -v`, `drush status` (Drupal version, DB driver), module list, `PHP_SAPI` as seen by Drupal (FreeUnit
reports `cli-server`, `src/nxt_php_sapi.c:302`; Drupal only tests `cli`, so the web path is unaffected, but
`core/includes/errors.inc` branches on it), body hash per URL per runtime, governor and EPP, kernel, oha version.

## 6. Phases

### Phase 0: baseline, classic mode, all three runtimes

1. Repository, Dockerfiles, both sites built and installed, `fu_queue_test` enabled, `meta.txt` complete.
2. `bench/cells.sh` gains `WORKLOAD=drupal-front` and `drupal-auth` (the `conf_for` and `url_for` cases,
   `tools/perf-lab/cells.sh:69-92`) and a `RUNTIME` axis so a cell can start httpd or nginx+fpm instead of unitd.
   This is the largest change: today `start_unit` is the only server starter.
3. Job 01 of the lab first, on the same day, to set the bands (`perf-lab.md:231-235`).
4. `front-anon` and `front-auth` on 11.4.8 for the three runtimes, then 12.0.0-beta1.
5. Cron and queue arms A, B, C on FreeUnit; A and B on nginx+FPM and Apache.
6. First page publishes with the null bands printed under every chart.

Deliverable: the page and `results/<run-id>/` for every chart. Exit: every row of 5.4 that applies to phase 0
has a verdict line, CLAIM or NO CLAIM, quoted on the page.

### Phase 1: FreeUnit worker mode, when it lands

Blocked on tracker 596; no PR exists on 2026-10-07. The design in `perf-plan-2026-10.md:82-107` is a PHP target
member `"worker": true` and `unit_handle_request(callable)`. Drupal has no worker runner today; Drupal 11.4 and
12 boot through `Drupal\Core\Runtime\DrupalRuntime` on Symfony `HttpKernelRunner`, so the entry would be a
`runtime/unit-symfony`-style runner (`perf-plan-2026-10.md:114`) plus a Drupal-specific reset of static state
(`Database::$connections`, `\Drupal::getContainer()`), which nobody has written. Plan: `freeunit-worker` as a
fourth runtime column, same cells as phase 0, plus a 1M-request soak with RSS per worker every 100k requests
(`rapira-architecture-2026-10.md` section 7 item 9), and a correctness gate: 1000 requests alternating two users
must never return the other user's name in the body.

### Phase 2: schedules plus worker mode for cron and queue

After PR 452 merges and phase 1 exists: arm D. A schedule into a `jobs` application with `processes: 1`
(the capacity-separation recipe, `perf-plan-2026-10.md:142-147`). Publish `last_duration_ms` from
`/status/schedules`, the visitor p99 during a tick, and the abandoned-worker test (a run over `timeout`;
`perf-plan-2026-10.md:148-153`). Compare against arm B on all three runtimes and arm A as the baseline.

## 7. GitHub Pages publishing

app-api publishes from the `gh-pages` branch, root path, legacy build (GitHub Pages API for
`Yii3-Benchmarks/app-api`: `source.branch gh-pages`, `build_type legacy`, `https_enforced true`). Do the same:

- `results/<run-id>/` is committed to `master` with `results.tsv`, `summary.json`, `meta.txt`, `compare.txt` and
  the configs as run. Nothing else is a source of truth.
- `report/render.py` reads every `summary.json`, groups by Drupal line, workload and runtime, and writes
  `index.html` with one chart per question of section 1, the null band under each chart, and the PERF verdict
  lines verbatim. Charts follow the `dataviz` skill palette; no number is typed into a template.
- `pages.yml` runs `render.py` on push to `master` and force-pushes `gh-pages` with `--force-with-lease`. No
  benchmark runs in Actions: the shared runners cannot pin cores or read hardware counters (`perf-lab.md:217-219`).
- The page header states host class, governor, PHP, Drupal tag, runtime versions and the run date, copied from
  `meta.txt`, so a reader can tell two runs apart without opening the data.
- Owner: `freeunitorg` so the URL is `freeunitorg.github.io/benchmarks-drupal`; a personal account works the same way
  if the org decision is slow. Moving later keeps the data because it is in git.

## 8. Reusing the gist stand under /tmp/fu-pkgs

`setup-stand.sh` unpacks Alpine edge apks into `$ROOT/root`, patches `RUNPATH` of the PHP module with
`patchelf`, and prints the rootless `unitd` command (`setup-stand.sh:12-58`). It is the fastest way to a
one-process cron and queue stand without Docker. Facts that break a plain rerun on 2026-10-07:

- `/tmp/fu-pkgs` does not exist on this laptop; the stand must be rebuilt.
- The pins are stale. Edge `community` now has `php84-8.4.26-r0`; the script asks for `php84-8.4.25-r0`
  (`setup-stand.sh:21-27`), and edge keeps only the current build, so `wget` fails and the script exits at
  "FAIL". Edge `community` has `freeunit-1.36.1-r0` and `freeunit-php85-1.36.1-r0`; there is no 1.37.0 apk.
- To get 1.37.0 in this stand: build the apks from MR 109208 (section 4.1, choice 2) and point the script at
  a local directory instead of `BASE_C`. A `PKGDIR` override is a 3-line change.
- Prefer `freeunit-php85` plus `php85-*` over php84: it matches the Docker PHP line and Drupal 12's floor.
- Keep the gist's two fixes: children end with `posix_kill(getmypid(), SIGKILL)`, never `exit()`
  (`fork_spawn.php:40-43`), and the handler reaps twice (`:143-145`). The issue 321 thread adds a third hazard:
  a fork child that touches the inherited PDO connection sends `COM_QUIT` on the parent's socket. The boot
  scripts open their own kernel after the fork, so they do not hit it, but any shared bootstrap before
  `pcntl_fork()` would.
- `/tmp` is a 7 GB tmpfs on the laptop; a Drupal site with `demo_umami` plus two PHP lines fits, but do not
  put MariaDB data there.

Use the stand for the cron and queue arms only, and only for development of the scripts. Published numbers
come from the perf box.

## 9. Risks

| risk | effect | mitigation |
|---|---|---|
| `powersave` governor | `server_us` and `rps` bands of 33 to 48 percent at 2 s cells (`perf-lab.md:151-152`) | counters as primary; 8 s cells; publish bands; ask for `performance` on the root handover list |
| page cache makes `front-anon` a cache-bin test | the three runtimes tie and the chart says nothing | keep it, it is the common case; `front-auth` is the render test; say so on the page |
| one shared session in `front-auth` | lock contention in `sessions` equal for all; may dominate | measure with 1 and with 20 sessions once; publish the one that is lower in `db_us` |
| `big_pipe` and `Content-Length` | chunked bodies differ between runtimes, body hash guard fails | disable `big_pipe` for the measured pages; record it |
| Drupal 12 is beta | 12.0.0-beta1 numbers are stale by December | pin the tag in `meta.txt`; rerun at 12.0.0; keep both |
| `drupal/cms` on 12 | contrib compatibility unknown | not in phase 0 |
| cron lock 900 s | arm B and C second tick is a no-op that still reports `cron_ok: true` | release the lock or alternate; count `cron_s` under 10 ms as a no-op and exclude |
| fork child hazards | leaked workers serving traffic; shared PDO socket | the gist fixes, process-count guard, no bootstrap before fork |
| Apache cannot detach | arm B latency equals job time | report it; that is the comparison |
| worker mode does not exist | phase 1 has no date | phase 0 publishes without it; the page says "classic" |
| PR 452 unmerged | phase 2 has no date | same |
| MR 109208 is a draft, s390x pipeline failed | no distribution 1.37.0 apk | tarball build is the default; apk is an opt-in column |
| gist pins stale | setup-stand.sh fails at download | `PKGDIR` override; new pins |
| load generator on the same host | the Yii3 run never sampled host CPU (`yii3-benchmark-setup-2026-10-05.md:341-342`) | `LOAD_CPUS` disjoint from `SERVER_CPUS` and `DB_CPUS`; record `/proc/stat` per cell |
| numbers from earlier private notes (8.5x, 0.97x, 1.6x) | tempting to quote | none appears on the page until re-measured under section 5 |

## 10. Open decisions for the maintainer

1. Org: `freeunitorg/benchmarks-drupal` beside `benchmarks-yii3`, or a personal account.
2. "Full CMS": `standard` profile or `drupal/cms`. Recommendation: `demo_umami` only in phase 0.
3. Binary column for FreeUnit: tarball build (default) or also the Alpine apk from MR 109208.
4. Whether to ask for the `performance` governor on the perf box before phase 0; without it the headline
   `rps` chart carries a wide band.

## 11. Progress log (maintainer-approved 2026-10-07; Strace executes, the maintainer decides)

Decisions settled 2026-10-07 (finding `run/agent-findings/strace-20261007-drupal-bench-decisions.md`):
1a separate `freeunitorg/benchmarks-drupal`; 2c `demo_umami` only in phase 0; 3a tarball default plus
master-HEAD column; 4b powersave + record (instructions/req headline).

### 2026-10-07 ~23:00 CEST — remeasure 1.37.0 done, MR 109208 triaged, root pending

- Perf: `remeasure-1.37.0-20261007T031948Z` (1.36.1 `90c5ffb8` vs 1.37.0 `61b297ac`, hello c6/c32, the reference bench host)
  finished 05:36Z after two idle-gate waits (kworker 22%, tests 33%). Verdict:
  `CLAIM hello-c32: B is 12.6% more server instructions per request, null band 1.2, 8/8 agree`;
  `NOISY hello-c6` (null band 8.3 > limit 5.0). Report: `run/perf-lab/remeasure-1.37.0-20261007T031948Z/`.
  Implication for §5: the baseline page will show a real 1.37.0-vs-1.36.1 delta at c32 — publish it with
  the NOISY c6 caveat, do not average the cells.
- Alpine MR 109208 (`cfeddb5727f`, draft): parent pipeline 475378 `failed` via bridge `build-jobs` →
  child 475379 8/9 green, only `build-s390x` failed with `job_execution_timeout`. Verdict: infra flake
  (emulated s390x), not a freeunit breakage; x86_64/aarch64 apks are usable. Gist rev 2 waits on the
  apk column decision (3a: opt-in, tarball default).
- Root on the reference bench host: no new tmux session visible (two unrelated sessions only, checked 22:56 CEST, load 0.06).
  Governor experiment (Step 1) waits for the session + the 01:00-06:00 window. Box rules hold: claim,
  one unit-build at a time, restore powersave after.
- Tool fix: `tools/perf-job.sh` rejected `BUILD_HOST`/`ALPINE_HOST` from `run/lab.env`; `LAB_KEYS`
  extended (host-role keys allowed). Lesson: lab.env carries two namespaces (perf-lab + hosts) and the
  parser allow-listed only one — keep the allow-list in sync with `lab.env.example`.

### Lessons learned (for reuse, not just this plan)

1. `PERF ... NOISY` is a result, not a retry ticket: the c6 cell cannot resolve wave-0 items at 2s cells;
   rerun at 8s cells (job 01) before calling it a regression. Lesson: read the null band before the delta.
2. MR pipeline `failed` needs the bridge walk (parent → `bridges` → child → per-job table) before blaming
   the package: aports uses parent-child pipelines and the parent shows only 2 verify jobs green.
3. `strace -p` on a claimed box is still forbidden on others' pids: check `ps` owner first even when idle.
4. Gist numbers (8.5x, 0.97x) stay barred from the page until `reproduce.sh` reruns at 1.37.0 — done above
   for hello-gate; Drupal `total_s` rerun still open (needs `/tmp/fu-pkgs-1.37.0` stand).

### 2026-10-08 — PR 616 merged (php chdir hook removed)

Phase-0 item `04-php-chdir` answered on master (merge `856d23b9`, head `45fc3fc1`, +54/-56;
test `test_php_application_root_cwd_after_script_chdir`; dsh reader-verified pre-merge).
`origin/master` now `d50d12c2`. Job 04 runs `REV_A=61b297ac REV_B=master` for the
`asys_chdir 1.000→0` delta. Lesson: the review pipeline (dsh first-pass + reader analysis)
cleared a risky-path removal in one round — cheap passes before Fable works here too.

### 2026-10-08 evening — PR 635 merged (refused WHOAMI → worker exits)

Worker lifecycle fix on master (merge `067e6be9`, head `5f6aebe8`). `origin/master` now `067e6be9`.
Relevant to phase-2 schedules/worker-mode (a hung worker is a lost process until respawn); the 1.37.0
baseline predates it — list as post-baseline fix on the bench page.

### 2026-10-09 — job 04b FALSIFIED as written (chdir stays by design)

Run `04b-php-chdir-20261008T204123Z` (1.37.0 vs master@067e6be9, c6): `asys_chdir 1.000→1.000`
(+0.00, band 0.00), `s_instructions -1.08% NO CLAIM` (null band 3.78, PASS). The EXPECT misread
PR 616: it removed the chdir hook, keeping the per-request chdir — the syscall remains by design.
Lesson: read the PR title literally ("the per-request chdir makes it [the hook] unnecessary") before
writing EXPECT; a hook removal is not a syscall removal. `origin/master` now `0f1ec0b1`.

### 2026-10-09 ~01:30 — c32 regression CAUGHT: 39700f29 (+12.9%)

Bisect over `1.36.1..1.37.0` (131 commits): `1453d33c` CLEAR (NO CLAIM, sendmsg ~3.5 both sides —
the extra message predates it); `39700f29^` (sendmsg 2.3) vs master (3.5) CLAIM +13.2%; single-commit
`39700f29^` vs `39700f29` CLAIM +12.86% s_instructions (CI [+11.5,+14.2], band 1.93, 8/8),
`asys_sendmsg 2.260→3.588 (+58.8%)`, `r_instructions +13.96%`. Commit: "libunit: keep the messages
of a request in order on a queue port" — a correctness fix, so the message may be its price; next is
a code read for piggyback options (K2-adjacent). Lesson: strace counters (`asys_sendmsg`) bisect
faster than instructions — one exact metric beat the noisy totals; single-commit A/B jobs (~8 min)
resolve a 131-commit range in 3 jobs when suspects are ordered by mechanism.

### 2026-10-09 ~02:10 — validate master vs 1.37.0: -2% direction, NO CLAIM

Run `validate-master-1370-20261008T233754Z`: `s_instructions -1.90%` (band 2.59),
`r_instructions -2.94%` (band 4.45), sendmsg unchanged. Post-1.37.0 work (592 epoll, 616, 635)
leans faster but inside noise; the 39700f29 +12.6% stands. Lesson: syscall savings (epoll_ctl)
need not move instruction counts at saturation — validate each metric on its own band, and never
promote a direction to a claim.

### 2026-10-09 ~02:40 — job 03 borderline: -2.3% + smaller nxt_mp_free

Run `03-mp-junk-fill-20261008T235359Z`: `r_instructions -2.29%` NO CLAIM (band 2.89, but CI
excludes 0 and 12/16 cells agree), `s_instructions -2.32%` CLAIM, `nxt_mp_free 868→790` bytes.
Direction, size, and app-side CLAIM agree — one rerun promotes it. Lesson: a split verdict
(primary NO CLAIM, secondary CLAIM) is not a failure; record both and rerun instead of rounding
either into a claim.

### 2026-10-09 ~03:00 — job 02 capp floor live (c32 PASS, c6 NOISY)

Run `02-capp-floor-20261009T000759Z`: the capp build step works; null c32 band 1.8 PASS,
c6 band 5.4 NOISY. Floor TSVs are in `run/perf-lab/02-capp-floor-20261009T000759Z/` for the
K2 comparison. Lesson: c6 at short cells is NOISY across workloads (hello and capp alike) —
default future wave-0/4 jobs to 8s cells and treat 2s c6 as smoke only.
