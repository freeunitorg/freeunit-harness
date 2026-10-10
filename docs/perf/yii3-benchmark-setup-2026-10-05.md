# Yii3 application-server benchmark: how FreeUnit was run (2026-10-05)

Source: https://github.com/yii3-benchmarks/app-api (HEAD 6e67cb5, 2026-10-05), cloned to
the session scratch directory (`bench-src/app-api`).
Results read: `results/samdark_2026-10-05-pg2000/` (the published run; source_commit cd266cb).
Nothing from the repo was executed. Paths are relative to the repo. Full per-stage data (all runtimes): `yii3-benchmark-stages-2026-10-05.csv`.
CPU per stage below is the mean of the sampler rows in that stage's 1/13 time slice (approximate, as in the report).

## 1. Verdict on the brief

| Claim in the brief | Result |
|---|---|
| FreeUnit 1.37.0 built from source | True. Release tarball, sha256 pinned, `--openssl --zlib --zstd --brotli`, PHP module only. |
| "uses the 1.36.1 entrypoint, newer omitted it" | Partly wrong. See section 2.3. The file is byte-identical in 1.36.1 and 1.37.0. |
| 20 workers = `processes: 20` | True, a fixed number, not a range. |
| `limits.requests` recycles workers | True, 10000 requests per PHP process (same number for FPM, Rapira, OxPHP, FrankenPHP worker). |
| `/` served as index.php script target | True, through a `share` plus `fallback`. See 2.1. |
| FreeUnit runs the Yii app in a persistent worker | False. FreeUnit runs `public/index.php` once per request (classic). |
| Raw per-stage data is committed | True: `summary.json`, `docker-stats.csv`, `wrkx-N.log` per scenario. |

## 2. FreeUnit as run

### 2.1 Unit config: `docker/runtimes/freeunit.json` (whitespace compacted, content verbatim)

```json
{"listeners": {"*:80": {"pass": "routes"}},
 "routes": [
   {"match": {"uri": ["!/assets/*", "*.php", "*.php/*"]},
    "action": {"pass": "applications/yii/direct"}},
   {"action": {"share": "/app/public$uri", "fallback": {"pass": "applications/yii/index"}}}],
 "applications": {"yii": {
   "type": "php", "processes": 20,
   "targets": {"direct": {"root": "/app/public/"},
               "index":  {"root": "/app/public/", "script": "index.php"}},
   "limits": {"requests": 10000, "timeout": 30}}},
 "settings": {"http": {"max_body_size": 8388608, "header_read_timeout": 30,
   "body_read_timeout": 30, "send_timeout": 30, "idle_timeout": 60}}}
```

What it does for the two benchmarked URLs (`/` and `/postgres/orders`):

- Neither URI matches `*.php`, so route 1 is skipped. Route 2 runs `share /app/public$uri`.
- The share fails (for `/` Unit appends the default index `index.html`; `/app/public/postgres/orders` does not exist).
  The `fallback` passes to target `index`, which runs `/app/public/index.php`. So every request costs one failed
  `open()` on a router thread, then the PHP script. I read `src/nxt_http_static.c` at tag 1.37.0: the open is
  synchronous and the "fallback taken" log line is only written when `log_route` is on (it is not).
- Fixed pool: `processes: 20`, no `max`/`spare`/`idle_timeout` object. All 20 PHP processes exist from config load.
- `limits.requests: 10000` restarts a PHP process after 10000 requests; `limits.timeout: 30` is a per-request timer.
- Not set: `listen_threads` (router threads then default to `nxt_ncpu`, here 32; `src/nxt_router.c:3099` at 1.37.0),
  `user`, `isolation`, `compression`, `access_log`, `index`, `options`/`ini`: no access log, no compression, apps run as `www-data`.

### 2.2 Dockerfile: `docker/runtimes/Dockerfile`, stages `freeunit-build` and `freeunit`

```dockerfile
FROM php:8.5.11-cli-trixie AS freeunit-build
  curl -fsSL https://github.com/freeunitorg/freeunit/archive/refs/tags/1.37.0.tar.gz -o /tmp/freeunit.tar.gz
  echo '8ae237d5a32d77bb8a7426d844ce75aeb18583a4e47318f55e17839d865fad8c  /tmp/freeunit.tar.gz' | sha256sum -c -
  ./configure --prefix=/usr --statedir=/var/lib/unit --control=unix:/var/run/control.unit.sock
    --runstatedir=/var/run --pid=/var/run/unit.pid --logdir=/var/log --log=/var/log/unit.log
    --tmpdir=/var/tmp --user=www-data --group=www-data --modulesdir=/usr/lib/unit/modules
    --openssl --zlib --zstd --brotli
  ./configure php ; make -j"$(nproc)" ; make install
FROM php:8.5.11-cli-trixie AS freeunit
COPY --from=freeunit-build /usr/sbin/unitd /usr/sbin/unitd
COPY --from=freeunit-build /usr/lib/unit/modules /usr/lib/unit/modules
# FreeUnit 1.37.0 no longer includes this script; retain the pinned 1.36.1 entrypoint.
ADD --chmod=755 --checksum=sha256:3ae4c711f022a351c0b89042c4fde21260fa0578d28fd52e1585cad15f5df91b \
    https://raw.githubusercontent.com/freeunitorg/freeunit/1.36.1/pkg/docker/docker-entrypoint.sh /usr/local/bin/freeunit-entrypoint.sh
  install-php-extensions intl pdo_pgsql
COPY docker/runtimes/freeunit.json /docker-entrypoint.d/config.json
COPY docker/runtimes/php-production.ini /usr/local/etc/php/conf.d/zz-production.ini
ENTRYPOINT ["/usr/local/bin/freeunit-entrypoint.sh"]
CMD ["unitd", "--no-daemon", "--control", "unix:/var/run/control.unit.sock"]
```

- Base: Debian trixie, PHP 8.5.11 CLI (NTS), same PHP minor as php-fpm and RoadRunner images.
- Modules: only the `php` module for PHP 8.5.11. No njs, no otel, no `--tests`, no `--debug`, no extra CFLAGS, no
  `--hardening` option. Build is the default release build. Same unitd binary in the second stage.
- Extensions installed: `intl`, `pdo_pgsql` (`mbstring` is built into the Docker PHP; php-fpm also gets `opcache`).
  I did not run `php -i` in the image. PHP 8.5 builds opcache in, so `install-php-extensions opcache` is not needed (not verified).
- Compose (`docker/benchmarks.compose.yml`, service `freeunit`): `./:/app` bind mount, `env_file: dev/.env`
  (APP_ENV=prod, APP_DEBUG=false, PGSQL_HOST=postgres, VALKEY_HOST=valkey), port 9991:80, nofile 65536,
  json-file logging 10m x 3, no cpu or memory limits, no `cpuset`. Healthcheck: `/proc/1/comm = unitd` and `curl /` every 30 s.

### 2.3 The entrypoint note

Dockerfile comment: "FreeUnit 1.37.0 no longer includes this script; retain the pinned 1.36.1 entrypoint."
README: "The Docker entrypoint remains pinned to 1.36.1 because the 1.37.0 source archive no longer includes it."

My check in the FreeUnit checkout (read-only `git show`):
- `1.36.1:pkg/docker/docker-entrypoint.sh` and `1.37.0:pkg/docker/docker-entrypoint.sh` both have sha256
  `3ae4c711f022a351c0b89042c4fde21260fa0578d28fd52e1585cad15f5df91b`. The script did not change.
- `.gitattributes` has `pkg/** export-ignore` in both tags. `git archive` of 1.36.1 and of 1.37.0 both contain only
  an empty `pkg/` entry. So no release tarball ever carried the script; the author fetched it from a git tag URL
  and the same URL with `1.37.0` would give the same bytes. The note is wrong in its reason, harmless in effect.

What the script does (`1.36.1:pkg/docker/docker-entrypoint.sh`): if `/var/lib/unit` is empty, it starts `unitd` once in the
background, waits up to 5 s for `/var/run/control.unit.sock`, PUTs every `*.json` in `/docker-entrypoint.d/` to
`/config` with curl, sends SIGTERM through `/var/run/unit.pid`, then `exec "$@"` starts
`unitd --no-daemon --control unix:/var/run/control.unit.sock`. So the final server starts from the saved state.
It also handles `*.pem` (certificates), `*.js` (njs modules) and `*.sh`; none exist here.
It needs `curl`, which the image installs. Nothing in it affects request speed.

### 2.4 PHP settings: `docker/runtimes/php-production.ini` (shared by all runtimes, appended as `zz-production.ini`)

The image first copies `php.ini-production` to `php.ini`, then this file overrides. There is no FreeUnit-only ini.

```ini
expose_php = Off
display_errors = Off
log_errors = On
error_log = /proc/self/fd/2
memory_limit = 256M
max_execution_time = 30
variables_order = "EGPCS"
realpath_cache_size = 4096K
realpath_cache_ttl = 600
opcache.enable = 1
opcache.enable_cli = 1
opcache.enable_file_override = 1
opcache.memory_consumption = 256
opcache.interned_strings_buffer = 32
opcache.max_accelerated_files = 20000
opcache.validate_timestamps = 0
opcache.jit = disable
```

`enable_file_override` was added on 2026-10-05 (commit e75f2db). No preload, JIT is off. Opcache is per container;
Unit forks app processes from a prototype process, so one shared cache is expected (not verified).
Evidence the env reaches the Unit app: `/postgres/orders` returns 14236 bytes per response with 0 errors on FreeUnit.

## 3. The other runtimes

All use 20 workers, `max_requests` 10000 (RoadRunner uses a memory supervisor instead), 8 MiB body limit, 30 s timeouts.

| Runtime | Config file | Workers | Recycle | Mode (entry point) |
|---|---|---|---|---|
| FreeUnit | `freeunit.json` | `processes: 20` fixed | `limits.requests` 10000 | classic, `public/index.php` per request |
| PHP-FPM + Nginx | `php-fpm.conf`, `nginx.conf` | `pm = static`, `max_children 20` | `pm.max_requests 10000` | classic, FastCGI, `/index.php` per request; nginx `access_log off`, gzip on, no FastCGI keepalive |
| FrankenPHP classic 1.13.0 | `frankenphp-classic.Caddyfile` | `num_threads 20`, `max_threads 20` | none | classic, `php_server`, `public/index.php`; `encode zstd br gzip` |
| FrankenPHP worker | `frankenphp-worker.Caddyfile` | `worker num 20`, `num_threads 1`, `max_threads 21` | `MAX_REQUESTS 10000` env, read by the runner | worker, `/app/worker-frankenphp.php`, `match *` |
| RoadRunner 2025.1.15 | `roadrunner.yaml` | `num_workers: 20`, `max_queue_size 1024`, relay pipes | supervisor `max_worker_memory 256`, `exec_ttl 30s`; no request count | worker, `php -d max_execution_time=0 worker-roadrunner.php`; middleware `static, gzip` |
| Rapira (nightly-php8.5) worker | `rapira.toml` | `processes = 20` | `max_requests = 10000` | `mode = "worker"`, `worker-rapira.php` |
| Rapira classic | `rapira-classic.toml` | `processes = 20` | `max_requests = 10000` | `mode = "classic"`, same entry file, fresh app per request |
| Rapira dispatcher | `rapira-dispatcher.toml` | `processes = 20` | `max_requests = 10000` | `mode = "dispatcher"`, same entry file, persistent app, exchange API |
| OxPHP 0.12.0 worker | Dockerfile env | `PHP_WORKERS=20` (ZTS PHP 8.5.11) | `MAX_REQUESTS=10000` via worker code, `WORKER_MAX_MEMORY_MIB=256` | worker, `/app/worker-oxphp.php` |
| OxPHP classic | Dockerfile env | `PHP_WORKERS=20` | none | classic, `ENTRY_FILE=index.php` |

## 4. Worker entry points and the PHP-side API

`composer.json` requires: `yiisoft/yii-runner-http ^3.2.1` (classic, `public/index.php`), `yiisoft/yii-runner-roadrunner ^3.2.0`,
`yiisoft/yii-runner-frankenphp ^1.0`, `yiisoft/yii-runner-rapira @dev`, `rapira/contract @dev`. `composer.lock` is not committed
(only its sha256 `560c46f5...` is in `run-context.json`), so exact runner revisions are unknown. I read the runner
sources on GitHub HEAD on 2026-10-06 (copies in the session scratch directory, `runners/`); the benchmark may have used older commits.
Deviation from the brief: the worker files are 26 to 85 lines but I quote only the distinguishing lines to stay under the
350-line limit. Each file is `require src/bootstrap.php`, one runner object, `->run()`; read them from the clone for the rest.

```php
// worker-rapira.php (26 lines)      new RapiraApplicationRunner(rootPath: __DIR__, debug: Environment::appDebug(), ...,
//                                      temporaryErrorHandler: new ErrorHandler(... PlainTextRenderer)); $runner->run();
// worker-roadrunner.php (27 lines)  ini_set('display_errors','stderr');
//                                   (new RoadRunnerHttpApplicationRunner(rootPath: $root, ...))->withTemporaryErrorHandler(...)->run();
// worker-frankenphp.php (26 lines)  new FrankenPHPApplicationRunner(rootPath: $root, ...); $runner->run();
// worker-oxphp.php (85 lines)       anonymous class extends Yiisoft\Yii\Runner\ApplicationRunner; run() builds the container,
//   $application->start();
//   oxphp_worker(static function () use (...): void {
//       $request  = $requestFactory->create()->withAttribute('applicationStartTime', microtime(true));
//       $response = $application->handle($request);          // ErrorCatcher on Throwable
//       $emitter->emit($response);                           // SapiEmitter
//   finally: $application->afterEmit($response); $container->get(StateResetter::class)->reset(); gc_collect_cycles();
//            if ($maxRequests > 0 && ++$requests >= $maxRequests) Worker::current()->scheduleExit(); });
//   $application->shutdown();
```

| Runtime | PHP-side API | Request in | Response out | Yii adapter |
|---|---|---|---|---|
| FrankenPHP | `frankenphp_handle_request(callable $handler): bool`, handler takes no argument | superglobals rebuilt per request; `RequestFactory` makes a PSR-7 request | `SapiEmitter` (header(), echo) | `yii-runner-frankenphp`; falls back to one `$handler()` call when the function is absent (classic) |
| RoadRunner | `Spiral\RoadRunner\Http\PSR7Worker::waitRequest()` and `respond()` over goridge pipes | PSR-7 request object from the Go side | `respond($psr7Response)` | `yii-runner-roadrunner` (`RoadRunnerHttpWorker`), loop `while(true)` |
| Rapira worker | `Rapira\handle_request(callable): bool`, `Rapira\get_mode(): Mode` | superglobals, `SapiRequestFactory` | `SapiEmitter` | `yii-runner-rapira`, `SapiServer::run()` |
| Rapira dispatcher | `Rapira\get_dispatcher()->receive(): Exchange` | `Exchange` object, `DispatcherRequestFactory`; cancelled exchanges are skipped | `ExchangeEmitter` writes into the Exchange; `ClosedException` ends the loop | `DispatcherServer::run()` |
| Rapira classic | same `get_mode()` returns `Mode::Classic` | superglobals | `SapiEmitter` | `SapiServer::once()` |
| OxPHP | `oxphp_worker(callable)`, `OxPHP\Server\Worker::current()->scheduleExit()` | superglobals via Yii `RequestFactory` | `SapiEmitter` | hand-written runner in `worker-oxphp.php` on `yiisoft/yii-runner` |
| FreeUnit / FPM / classic | none (the script ends each request) | superglobals | `SapiEmitter` | `yii-runner-http` `HttpApplicationRunner` in `public/index.php` |

Every worker runner does the same after each response: `$application->afterEmit()`, `StateResetter->reset()`, `gc_collect_cycles()`.
The classic path (FreeUnit, FPM) rebuilds config merge plan, DI container, router and middleware stack on every request.
That is where the ~1.6 ms CPU per request goes (section 6), not in the server.

## 5. Load generator and method

- Tool: wrkx (wrk2 derivative) built from `https://github.com/devhands-io/wrkx` at commit `bec57539360771bedc2fc63a48e3746f1b7a9975`
  (`benchmark/Dockerfile.wrkx`, `WRKX_REF`), run as `docker run --network=host`, so the client shares the host with the server.
- Host: AMD Ryzen 9 7950X (16 cores, 32 threads), 93 GiB, Ubuntu 26.04.1, kernel 7.0.0-34, Docker 29.8.1. Other dev
  containers (mariadb, postgres, minio, node) were running. Command: `make bench-all MODE=ramp THREADS=32 CONNECTIONS=256`.
- Per scenario: `-t 32 -c 256 -d 30s -R <target> --latency -s wrkx-report.lua`; 13 sequential 30 s stages with targets
  2500, 5000, 10000, 15000, 20000, 25000, 30000, 40000, 50000, 75000, 100000, 150000, 200000 RPS (`tools/run-wrkx-benchmark.sh`).
  Each stage is a separate wrkx process with its own calibration before the 30 s.
- Warm-up: one unmeasured 10 s run at 1000 RPS per endpoint, same threads and connections, before sampling starts.
  Each runtime is rebuilt and health-checked; PostgreSQL and Valkey are fresh per runtime.
- Preflight: `curl` must return 200. Requests: plain keep-alive `GET`, no `Accept-Encoding`; responses 209 bytes (`/`), 14236 bytes (DB).
- Errors (`benchmark/wrkx-report.lua`): `connect + read + write + status + timeout`. wrk counts status > 399 as `status`; wrk default
  timeout (2 s) applies, no `--timeout` is passed. Body content is not checked.
- Successful RPS (`tools/compile-wrkx-results.php`): `(requests - errors) / durationUs`. "Completed RPS" is `requests / duration`.
  Latency is wrkx's coordinated-omission-corrected histogram (mean, p95, p99). Past saturation it measures queue growth
  (FreeUnit `/` 4.7 s average at the 15000 stage), not service time.
- RPS cap (`tools/render-benchmark-report.php`, `detectStageRpsCap`): first stage whose successful RPS is under 95% of the target.
  The report's "Successful RPS" column is the value at that stage, not the maximum of the run.
- CPU and memory (`tools/sample-docker-stats.php`): `docker stats --no-stream` every ~1 s plus its own run time (about 2.3 s per row),
  summed over the service's containers (PHP-FPM = `php` + `nginx`). CPU is docker's percent (100 = one core, thread-level, so SMT
  siblings count as full cores). Memory is cgroup usage (includes page cache), not RSS. Sampling starts after warm-up.
- PostgreSQL 18.6 `max_connections=2000` (run 1 had 200), seeded with 20000 customers and 200000 orders, indexes on
  `orders(created_at DESC, id DESC)` and others. Query: join `orders` to `customers`, `ORDER BY created_at DESC, id DESC LIMIT 50`.
  Connections are `PDO::ATTR_PERSISTENT => true` per PHP process (`config/common/di/db.php`); FreeUnit sampled at most 21 clients (20 + monitor).
  Valkey 9.1.2 exists but neither endpoint reads from it. App: `yiisoft/db-pgsql`, `APP_ENV=prod`, `APP_DEBUG=false`.
- Rapira runs recorded socket-read errors (486 to 16959 per scenario). Others: 0 errors.

## 6. Raw numbers (pg2000 run)

Class summary, all ten runtimes. "Cap stage" is the first stage under 95% of target. CPU ms per request = app CPU % x 10 / completed RPS.

| Runtime | `/` max successful | `/` at cap stage | `/` CPU ms/req (2.5k) | DB max successful | DB at cap stage | DB CPU ms/req (2.5k) | errors |
|---|---|---|---|---|---|---|---|
| Rapira dispatcher | 111499 | 111499 (150k) | 0.21 | 13512 | 12907 (15k) | 0.39 | 17813 |
| Rapira worker | 102828 | 102703 (150k) | 0.21 | 13210 | 12569 (15k) | 0.40 | 16533 |
| OxPHP worker | 80165 | 79785 (100k) | 0.27 | 12823 | 12705 (15k) | 0.60 | 0 |
| FrankenPHP worker | 68196 | 66624 (75k) | 0.35 | 12709 | 12709 (15k) | 0.60 | 0 |
| RoadRunner | 45506 | 45258 (50k) | 0.49 | 10619 | 10619 (15k) | 0.98 | 0 |
| **FreeUnit** | **11455** | **11445 (15k)** | **1.63** | **6241** | **6217 (10k)** | **2.03** | 0 |
| Rapira classic | 10964 | 9138 (10k) | 1.59 | 5954 | 4713 (5k) | 2.09 | 820 |
| FrankenPHP classic | 10605 | 10562 (15k) | 1.75 | 6070 | 6029 (10k) | 2.19 | 0 |
| PHP-FPM + Nginx | 10370 | 10264 (15k) | 1.77 | 5806 | 5806 (10k) | 2.27 | 0 |
| OxPHP classic | 8774 | 8749 (10k) | 2.08 | 5044 | 5044 (10k) | 2.74 | 0 |

Per-stage tables (stages 2500, 10000, 15000, 50000, 100000, 200000 of 13; all stages are in the CSV).

`/` successful RPS

| target | FreeUnit | Rapira disp. | Rapira worker | FrankenPHP worker | RoadRunner | PHP-FPM+Nginx |
|---|---|---|---|---|---|---|
| 2500 | 2501 | 2501 | 2501 | 2501 | 2501 | 2501 |
| 10000 | 9993 | 9980 | 9978 | 10000 | 10001 | 9988 |
| 15000 | 11445 | 14944 | 14963 | 15001 | 14989 | 10264 |
| 50000 | 11455 | 49911 | 49894 | 49967 | 45258 | 10177 |
| 100000 | 11263 | 99833 | 99041 | 67024 | 45122 | 10185 |
| 200000 | 11171 | 111360 | 102828 | 68196 | 44724 | 10203 |

`/` average latency ms

| target | FreeUnit | Rapira disp. | Rapira worker | FrankenPHP worker | RoadRunner | PHP-FPM+Nginx |
|---|---|---|---|---|---|---|
| 2500 | 4.4 | 1.0 | 1.1 | 1.3 | 1.4 | 4.9 |
| 10000 | 5.2 | 1.0 | 1.1 | 1.1 | 1.4 | 11.7 |
| 15000 | 4743.2 | 1.0 | 1.1 | 1.7 | 1.8 | 6326.5 |
| 50000 | 15428.4 | 1.2 | 1.2 | 1.8 | 1880.5 | 15894.8 |
| 100000 | 17746.9 | 43.8 | 252.1 | 6537.5 | 10957.1 | 17924.1 |
| 200000 | 18913.9 | 8820.5 | 9657.6 | 13210.7 | 15605.5 | 18953.1 |

`/` app CPU % (100 = one core)

| target | FreeUnit | Rapira disp. | Rapira worker | FrankenPHP worker | RoadRunner | PHP-FPM+Nginx |
|---|---|---|---|---|---|---|
| 2500 | 407 | 52 | 53 | 87 | 123 | 442 |
| 10000 | 1779 | 170 | 171 | 280 | 433 | 1968 |
| 15000 | 1950 | 249 | 258 | 419 | 591 | 2049 |
| 50000 | 1952 | 812 | 856 | 1521 | 2023 | 2035 |
| 100000 | 1944 | 1748 | 1854 | 1936 | 2025 | 2032 |
| 200000 | 1971 | 1986 | 1944 | 1984 | 1949 | 2030 |


`/postgres/orders` successful RPS

| target | FreeUnit | Rapira disp. | Rapira worker | FrankenPHP worker | RoadRunner | PHP-FPM+Nginx |
|---|---|---|---|---|---|---|
| 2500 | 2501 | 2497 | 2497 | 2501 | 2501 | 2501 |
| 10000 | 6217 | 9982 | 9952 | 9993 | 9997 | 5806 |
| 15000 | 6229 | 12907 | 12569 | 12709 | 10619 | 5797 |
| 50000 | 6223 | 13446 | 12859 | 12630 | 10584 | 5783 |
| 100000 | 6199 | 13478 | 13003 | 12610 | 10585 | 5791 |
| 200000 | 6145 | 13500 | 13156 | 12551 | 10589 | 5786 |

`/postgres/orders` average latency ms

| target | FreeUnit | Rapira disp. | Rapira worker | FrankenPHP worker | RoadRunner | PHP-FPM+Nginx |
|---|---|---|---|---|---|---|
| 2500 | 6.5 | 4.0 | 4.2 | 3.3 | 5.0 | 9.0 |
| 10000 | 7528.0 | 57.0 | 108.9 | 4.7 | 52.1 | 8374.0 |
| 15000 | 11716.7 | 2719.7 | 3090.1 | 3033.7 | 5839.4 | 12259.0 |
| 50000 | 17513.0 | 14130.1 | 14621.0 | 14929.9 | 15765.6 | 17672.2 |
| 100000 | 18775.4 | 17057.6 | 17182.4 | 17499.0 | 17877.6 | 18831.1 |
| 200000 | 19424.0 | 18540.4 | 18447.1 | 18752.3 | 18936.1 | 19415.3 |

`/postgres/orders` app CPU % (100 = one core)

| target | FreeUnit | Rapira disp. | Rapira worker | FrankenPHP worker | RoadRunner | PHP-FPM+Nginx |
|---|---|---|---|---|---|---|
| 2500 | 507 | 97 | 100 | 150 | 245 | 567 |
| 10000 | 1293 | 389 | 411 | 573 | 977 | 1419 |
| 15000 | 1348 | 518 | 581 | 716 | 989 | 1425 |
| 50000 | 1303 | 601 | 602 | 741 | 990 | 1424 |
| 100000 | 1346 | 580 | 626 | 738 | 991 | 1420 |
| 200000 | 1345 | 594 | 625 | 735 | 992 | 1422 |


## 7. History and FreeUnit notes in the repo

No README, CHANGELOG or commit message reports a FreeUnit problem. FreeUnit appears only in version pins. No FreeUnit rows exist in the April k6 results.

| Date | Commit | Change |
|---|---|---|
| 2026-08-07 | 961721a | `freeunit.json` created with no `processes` key (one PHP process); image `ghcr.io/freeunitorg/freeunit:latest-php8.5` |
| 2026-09-22 | 6039396 | `processes: 20` added |
| 2026-09-22 | fa8527d | Build from source, 1.36.1 (comment: the published `latest-php8.5` image "still contains FreeUnit 1.35.5"); `limits` and `settings.http` added |
| 2026-10-05 | e75f2db | `opcache.enable_file_override = 1` for all runtimes |
| 2026-10-05 | 42a3082 | FreeUnit 1.37.0; cd266cb PHP 8.5.10 to 8.5.11 |

FreeUnit max successful RPS across the three published runs, with the classic runtimes beside it:

| Run | FreeUnit `/` | FPM `/` | FrankenPHP classic `/` | FreeUnit DB | FPM DB | FrankenPHP classic DB |
|---|---|---|---|---|---|---|
| 2026-09-30 (FreeUnit 1.36.1, PHP 8.5.10, pg 200 conn) | 8889 | 8230 | 8262 | 5369 | 5024 | 5001 |
| 2026-10-05 run 1 (1.37.0, PHP 8.5.11, pg 200) | 11493 | 10631 | 10650 | 6354 | 5967 | 6122 |
| 2026-10-05 rerun (pg 2000, published) | 11455 | 10370 | 10605 | 6241 | 5806 | 6070 |

All classic runtimes gained 26 to 33% on `/` and 15 to 21% on DB between 09-30 and 10-05; worker runtimes stayed flat (FrankenPHP worker `/` 68767 to 68196).
So the gain tracks `enable_file_override` and PHP 8.5.11, not FreeUnit 1.37.0. Run to run noise is 1 to 3%.

## 8. What looks unfair, misconfigured or unproven for FreeUnit

1. No misconfiguration found. The config is plain and equal to FPM static 20 and FrankenPHP classic 20 threads. In its class (no persistent app)
   FreeUnit is first on both endpoints: `/` +8% over FrankenPHP classic and +10% over FPM; DB +3% and +7%. Run 1 shows the same order.
2. FreeUnit has no worker mode, so it pays the full Yii bootstrap per request: 1.63 ms CPU at 2.5k RPS versus 0.21 to 0.35 ms for worker
   runtimes. The headline chart ranks FreeUnit 6th of 10 on `/` because of this class gap (about 10x), not because of Unit's router.
   This data cannot split Unit overhead from Yii bootstrap: FPM+Nginx (1.77), FrankenPHP classic (1.75), Rapira classic (1.59) are all in the same range.
   A control with a trivial PHP script is needed to measure Unit alone.
3. `/` is CPU-bound by the 20 PHP processes: FreeUnit shows 1950 to 2030% CPU of a 2000% ceiling from the 15k stage on.
4. The DB endpoint is worker-bound, not CPU-bound, for all classic runtimes. FreeUnit at its cap: app CPU about 1300% (65% of 20 workers),
   PostgreSQL 710% (worker runtimes drive PostgreSQL to about 1450% at 12.7k RPS, so PostgreSQL has room). 20 workers / 6241 RPS = 3.2 ms per
   request, of which 2.08 ms is CPU; the other ~1.1 ms is the blocking PostgreSQL round trip (PostgreSQL costs about 1.14 ms CPU per
   request in both runs). So `processes` above 20, or a `{"max","spare"}` range, is the first lever for the DB number. This is a hypothesis
   to test; FPM and FrankenPHP classic share the same cap, so the report is symmetric.
5. Router threads default to `nxt_ncpu` = 32 (`settings.listen_threads` is unset) on a 16 core / 32 thread host that also runs wrkx with 32 threads.
   Machine-level CPU was not sampled, only containers, so host saturation by the load generator is not excluded.
6. Every request does one failed `open()` in the `share` step before the fallback to `index.php`. Nginx `try_files`, Caddy `php_server` and the
   RoadRunner `static` middleware do an equivalent file check, so it is symmetric. A route that passes known non-file URIs straight to the app would skip it.
7. Not unfair: gzip is on for nginx, Caddy and RoadRunner but off for Unit; wrkx sends no `Accept-Encoding`, so none is used.
8. Unproven: opcache is active in the Unit image (inferred from the 26 to 33% classic gain, no `php -i`); `images.json` creation times (10:15Z) predate the
   commit that pinned PHP 8.5.11 (13:31Z), so it does not prove image contents; `composer.lock` is not committed, so runner versions are unknown.
9. One run per scenario. Latency past saturation is queue growth, not comparable across runtimes; use stages below the cap
   (FreeUnit `/` at 10k: 5.2 ms average, 9.9 ms p95).
