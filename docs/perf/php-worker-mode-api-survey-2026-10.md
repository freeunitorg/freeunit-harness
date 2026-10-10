# PHP worker-mode APIs of the servers that beat FreeUnit: survey (2026-10-06)

Goal: add a persistent PHP worker mode to FreeUnit (src/nxt_php_sapi.c) that existing framework
runners can use with a small adapter. Everything below was read online on 2026-10-06 from the
default branches (FrankenPHP `main`, Rapira `main`, OxPHP `main`, runners `master`). Nothing was cloned.

## 1. Bottom line

1. The shape that won is one function: `handle_request(callable $handler): bool`, called in a
   `while` loop at the top level of a boot script. FrankenPHP and Rapira use the same shape.
   The handler takes no arguments, reads superglobals, answers with `echo` and `header()`.
2. Symfony 7.4+, Laravel Octane and the Yii FrankenPHP runner all reduce to
   `function_exists('frankenphp_handle_request')` plus, for Symfony and Octane,
   `$_SERVER['FRANKENPHP_WORKER']`. A FreeUnit worker that offers an opt-in FrankenPHP alias layer
   works with the Yii runner and Symfony with zero upstream PRs.
3. RoadRunner is a different protocol (PSR-7 over goridge pipes) and OxPHP a different shape (`oxphp_worker()`
   runs the whole loop in one call). Do not copy either. FreeUnit already has `fastcgi_finish_request()` (src/nxt_php_sapi.c:231).

## 2. The benchmark that triggered this

Source: https://yii3-benchmarks.github.io/app-api/ (2026-10-05, PHP 8.5.11, FrankenPHP 1.13.0, FreeUnit 1.37.0).
Path `/` (no DB), successful RPS at the throughput cap:

| Run | RPS | Mode |
|---|---|---|
| Rapira dispatcher | 111,499 | persistent, request object, no superglobals |
| Rapira worker | 102,703 | persistent, superglobals |
| OxPHP worker | 79,785 | persistent, ZTS threads |
| FrankenPHP worker | 66,624 | persistent, superglobals |
| RoadRunner | 45,258 | persistent, PSR-7 over pipes |
| FreeUnit | 11,445 | classic (bootstrap per request) |
| FrankenPHP classic | 10,562 | classic |
| PHP-FPM + Nginx | 10,264 | classic |

FreeUnit is on par with the classic runs. The gap is persistence, not the HTTP path. Config used:
`docker/runtimes/freeunit.json` in https://github.com/Yii3-Benchmarks/app-api (20 processes, `limits.requests` 10000,
no worker option exists, so none was used). On DB `/postgres/orders`: FreeUnit 6,217 vs FrankenPHP
worker 12,709 vs Rapira dispatcher 12,907.

## 3. FrankenPHP

Repo https://github.com/php/frankenphp (MIT). Stub: https://raw.githubusercontent.com/php/frankenphp/main/frankenphp.stub.php

```php
function frankenphp_handle_request(callable $callback): bool {}
function headers_send(int $status = 200): int {}
function frankenphp_finish_request(): bool {}          // @alias fastcgi_finish_request
function frankenphp_request_headers(): array {}        // @alias apache_request_headers, getallheaders
function frankenphp_response_headers(): array|bool {}  // @alias apache_response_headers
function frankenphp_log(string $message, int $level = 0, array $context = []): void {}
```

Semantics, read from frankenphp.c (`PHP_FUNCTION(frankenphp_handle_request)`, line 841), threadworker.go
and docs/worker.md:

| Topic | Behaviour |
|---|---|
| Call shape | One request per call. Blocks until a request arrives. Param parsed with `Z_PARAM_FUNC`. |
| Handler arguments | None in HTTP mode (`param_count` is 0 unless a Go embedder passes a value). |
| Return value | `true` after one request. `false` on shutdown, restart or max-requests reboot. |
| Handler return value | Ignored for HTTP. Only the Go library API reads it. |
| Not in worker mode | Throws `RuntimeException`: "frankenphp_handle_request() called while not in worker mode". |
| Readiness | The script first runs inside a dummy request. The first call to `frankenphp_handle_request` ends it and marks the worker ready (`metrics.ReadyWorker`). |
| Boot failure | Exit before the first call counts as a start failure. Backoff `failureCount^2 * 100 ms`, capped at 1 s. Default `max_consecutive_failures` is 6, then FrankenPHP exits with "too many consecutive failures". |
| Fatal error or uncaught exception | Exception inside the handler: `zend_exception_error(E_ERROR)`, then the script ends. Non-zero exit: logged "unexpected termination, restarting" at WARN, worker script restarts. Exit status 0 restarts quietly. |
| `$_SERVER` | Before the first call: process env plus worker script values. During a call: built from the request. Reset each request: `$_GET $_POST $_COOKIE $_FILES $_SERVER $_REQUEST`. `$_ENV` is NOT reset. |
| Worker marker | `$_SERVER['FRANKENPHP_WORKER'] = '1'` (fp_worker.go:193). Not `FRANKENPHP_WORKER_CONFIG`, which does not exist in the repo files read. `FRANKENPHP_CONFIG` is the Caddy config injection env var (docs/config.md:302). |
| Max requests | App level: loops read `$_SERVER['MAX_REQUESTS']` (set with `env MAX_REQUESTS n` in the worker block). Server level: Caddy `max_requests` (experimental, reboots the thread; `handle_request` returns false). |
| Output and headers | Standard SAPI hooks: `ub_write`, `send_headers`, `flush` go to Go. Headers go out on first write, `flush()` or `headers_send()`. At request end: `php_output_end_all`, `sapi_deactivate`. |
| Early finish | `frankenphp_finish_request()` (alias `fastcgi_finish_request`): `php_output_end_all(); php_header();` then closes the request context. Returns false if already done. The handler keeps running. |

What the frameworks call:

| Consumer | File | Calls |
|---|---|---|
| Symfony 7.4+ (core) | https://raw.githubusercontent.com/symfony/runtime/7.4/Runner/FrankenPhpWorkerRunner.php | `ignore_user_abort(true)`, `frankenphp_handle_request($handler)` in a do/while, `$kernel->terminate()`, `gc_collect_cycles()`. Selected in SymfonyRuntime.php:176 by `$_SERVER['FRANKENPHP_WORKER'] ?? false`. Loop limit: `FRANKENPHP_LOOP_MAX`, default 500. |
| Symfony < 7.4 | https://github.com/php-runtime/frankenphp-symfony (src/Runner.php 63 lines, src/Runtime.php 38 lines) | Same calls. |
| Laravel Octane | https://raw.githubusercontent.com/laravel/octane/2.x/bin/frankenphp-worker.php | Exits unless `$_SERVER['FRANKENPHP_WORKER']` and `function_exists('frankenphp_handle_request')`. Loop `while ($requestCount < $maxRequests && @frankenphp_handle_request($handleRequest))`. `FrankenPhpClient::marshalRequest()` is `Request::capture()` (superglobals). Response is `$response->send()`. `MAX_REQUESTS` default 1000. |
| Yii3 | https://raw.githubusercontent.com/yiisoft/yii-runner-frankenphp/master/src/FrankenPHPApplicationRunner.php | `function_exists('frankenphp_handle_request')` decides worker vs one-shot. `$_SERVER['MAX_REQUESTS']`. Request from `$_SERVER`, `getallheaders()` (falls back to `$_SERVER`), `php://input`. Response through `SapiEmitter` (`header()`, `echo`). Then `StateResetter->reset()`, `gc_collect_cycles()`. |
| Yii3 benchmark | `worker-frankenphp.php` in Yii3-Benchmarks/app-api | 26 lines, one `new FrankenPHPApplicationRunner(...)->run()`. |

Key finding: all three consumers use plain superglobals and `header()`/`echo`. Nothing needs Go-side objects.

## 4. Rapira

Repo https://github.com/rapira-rs/rapira (MIT, Rust, "created by the RoadRunner maintainers", README description).
Docs https://rapira.rs/docs/worker and https://rapira.rs/docs/dispatcher. Stubs:
`crates/sapi/rapira.stub.php`, `crates/plugins/http/rapira_http.stub.php`.

Worker mode (same shape as FrankenPHP):
```php
namespace Rapira { function handle_request(callable $handler): bool {} }   // true per request, false when draining
namespace { function rapira_finish_request(): bool {} }                    // classic and worker only
```
Contract (docs/worker): fills `$_GET $_POST $_SERVER $_COOKIE $_FILES $_REQUEST`; handler gets no arguments; return
value ignored; call only from the top-level loop (nested call throws `\Error`); outside worker mode throws
`Rapira\Exception\NotInWorkerModeError`. Uncaught exception or fatal ends the script, which is re-run.
`max_requests` recycles with `zend_bailout()` inside `handle_request` (rap_disp.c, `RAPIRA_HANDLE_RECYCLE`).

Dispatcher mode (no superglobals, no SAPI request; Rust hands PHP a read-only object):
```php
function get_mode(): Mode {}                 // enum Mode { Classic, Worker, Dispatcher }
function get_dispatcher(): Dispatcher {}     // throws NoDispatcherError outside dispatcher mode
interface HttpDispatcher {
    public function receive(int $timeout = -1): Exchange;   // microseconds; -1 blocks; TimeoutException, ClosedException
    public function tryReceive(): ?Exchange;                // never blocks
}
interface Exchange {
    public function getRequest(): Request;                  // method uri target authority protocol headers body remote server tls receivedAt
    public function writeHead(int $status, array $headers = []): void;   // array<string, list<string>>
    public function writeBody(string $content, bool $eos = true): void;
    public function sendFile(string $path, int $offset = 0, ?int $length = null, bool $eos = true): void;
    public function writeTrailers(array $trailers): void;
    public function flush(): void;
}
```
`dispatcher-sync.php`: `while (true) { $ex = $d->receive(); ... $ex->writeHead(200, [...]); $ex->writeBody($body); }`,
catch `ClosedException` to leave. `dispatcher-async.php`: one `Fiber` per exchange, up to 100; loop calls
`receive()` when idle and `tryReceive()` while fibers are in flight, resumes all fibers each turn; a streaming handler
calls `Fiber::suspend()` between `writeBody($chunk, eos: false)` calls. Discrepancy to know: the docs page says each
worker handles one exchange at a time and "Fibers do not change this rule"; the example (added 2026-08-14) does
interleave. Not resolved; the Yii runner uses the sync loop only.

Yii runner: https://github.com/yiisoft/yii-runner-rapira (created 2026-08-18, no stable tag). Detection, in
RapiraApplicationRunner.php: `function_exists('Rapira\get_mode') ? get_mode() : Mode::Classic`, then
`Mode::Dispatcher` runs `DispatcherServer` (`receive()` loop, `ExchangeEmitter` writes head and body),
`Mode::Worker` runs `SapiServer` (`while (handle_request($handler));`), `Mode::Classic` runs once.
Needs `rapira/contract` (0.9.0, 606 downloads). Rapira docs say Laravel worker mode is not supported yet; Symfony is
supported with a hand-written worker.php that does not use symfony/runtime.

Dispatcher is 8.6% faster than worker here (111,499 vs 102,703). Likely cause (not measured): no superglobal fill per
request; the request is one object built in Rust. A second API of this kind is not needed for adoption.

## 5. RoadRunner

Repo https://github.com/roadrunner-server/roadrunner (Go). PHP side: `spiral/roadrunner-worker`
(https://github.com/roadrunner-php/worker), `spiral/roadrunner-http` (https://github.com/roadrunner-php/http),
`spiral/goridge` (https://github.com/spiral/goridge-php).

```php
// Spiral\RoadRunner\Http\PSR7Worker
public function __construct(WorkerInterface $worker, ServerRequestFactoryInterface $requestFactory,
    StreamFactoryInterface $streamFactory, UploadedFileFactoryInterface $uploadsFactory)
public function waitRequest(): ?ServerRequestInterface
public function respond(ResponseInterface $response): void
```
Protocol cost per request (from HttpWorker.php, Worker.php, Frame.php): Go parses the request and sends one goridge frame
(binary header with crc32 + JSON context + raw body) over stdin/stdout pipes. PHP does `json_decode` of the
context (headers, cookies, uploads, attributes), `parse_str` of the raw query, builds a PSR-7 object graph, and on the
way back `json_encode`s status and headers and `(string)$response->getBody()` (no streaming unless `$chunkSize > 0`).
Frameworks pay again at the edges: Symfony needs HttpFoundation <-> PSR-7 bridges (runtime/roadrunner-symfony-nyholm
Runner.php), Octane calls `toPsr7Response()` (src/RoadRunner/RoadRunnerClient.php). Yii3 uses PSR-7 natively, so it is
the cheapest fit (`RoadRunnerHttpApplicationRunner`, mode from `RR_MODE` via `Environment::fromGlobals()`).
Benchmark result: 45,258 RPS vs FrankenPHP 66,624 on the same Yii app.

Consumers: Octane `bin/roadrunner-worker` (`PSR7Worker`, `waitRequest()` loop); Symfony via the community
package; Yii3 via yiisoft/yii-runner-roadrunner; Spiral via spiral/roadrunner-bridge.

## 6. OxPHP

https://github.com/oxphp/oxphp (docs https://oxphp.dev). Rust, PHP ZTS threads in one process. AGPL-3.0, 4 stars,
created 2026-02-07, 12 releases v0.1.0 to v0.12.0 (2026-09-25). README line 40: "OxPHP is not production-ready yet."
Worker API (oxphp.stub.php, docs/php/worker-class.md):

```php
function oxphp_worker(callable $handler): bool {}   // enters the loop; returns true once the loop EXITS
function oxphp_finish_request(): bool {}
function oxphp_is_worker(): bool {}
final class OxPHP\Server\Worker { static current(): self; scheduleExit(): void; requestCount(): int; ... serve(callable $h): void }
```
Differences from FrankenPHP: one call runs the whole loop; recycling is `Worker::current()->scheduleExit()` or
`WORKER_MAX_MEMORY_MIB`; fibers multiplex requests per thread; `$_ENV` is not reset. The Yii worker script in the benchmark
(`worker-oxphp.php`) is a hand-written 85-line subclass of `ApplicationRunner`; there is no published Yii runner for it.
Reports `cli-server` as SAPI name, so Yii's `public/index.php` has a special case for it.

## 7. Swoole, OpenSwoole and nginx Unit upstream

Swoole has its own event loop and HTTP server (callbacks, coroutines), not a "handle one request" loop. Runners: Octane
(`--server=swoole`), `runtime/swoole`. Yii3 has no official one (community `razonyang/yii-runner-swoole`, 6 downloads).
Not a model for FreeUnit.

nginx/unit archive (offline mirror, 1087 issues, 467 PRs, 8590 comments, complete to 2025-10-08):

| Query | Result |
|---|---|
| "worker mode", swoole, reactphp, php-pm, octane, psr-7, "bootstrap once", "stay in memory" | zero hits in issues, PRs, comments |
| roadrunner | one comment (issue 945, 2023): a Laravel user found Unit and RoadRunner in a Habr comparison |
| frankenphp | one comment, issue 916 (2023-12-13): user "meanwhile i will check frankenphp" after a WordPress multisite problem went unsolved |
| issue 219 "Replacement for php-fpm's fastcgi_finish_request" | VBart wrote a patch on 2020-10-19; use case was post-response shutdown functions |
| issue 866 "Ruby: add support for preload and to fork workers?" | open, Ruby only |
| issue 1105 "Python: uvloop support" | open, Python only |

Conclusion: upstream never discussed a PHP worker mode.
Fork archive (freeunitorg/freeunit, snapshot 2026-09-11): issue 27 "Integration with PHP true-async" (open, link only to
https://true-async.github.io/en/architecture/frankenphp.html), PR 11 TrueAsync graceful shutdown (closed). No worker-mode
issue. An earlier note found the TrueAsync support in master to be a dead branch.

## 8. Common denominator and proposed FreeUnit surface

What runners abstract over:

| Abstraction | Where | What it needs from the server |
|---|---|---|
| `Symfony\Component\Runtime\RunnerInterface::run(): int` | symfony/runtime | A loop primitive plus a detection flag. |
| `Laravel\Octane\Contracts\Client` (`marshalRequest`, `respond`, `error`) | laravel/octane | Superglobals in, `Response::send()` out. A `--server` match arm (swoole, roadrunner, frankenphp are hard-coded in StartCommand.php:52-56). |
| `Yiisoft\Yii\Runner\ApplicationRunner` subclass | yii-runner-* | A loop primitive plus `function_exists()` detection. |

Minimal PHP surface (names follow FrankenPHP semantics exactly):

```php
function unit_handle_request(callable $handler): bool {}   // one request per call; false = exit the loop
function unit_is_worker(): bool {}                          // true only in worker mode (function_exists is not enough)
function unit_finish_request(): bool {}                     // same body as today's fastcgi_finish_request()
function unit_request_headers(): array {}                   // also getallheaders() / apache_request_headers()
function unit_response_headers(): array|bool {}             // also apache_response_headers()
```
Phase 2, optional: `unit_headers_send(int $status = 200): int` (103 Early Hints), `unit_log(string $message, int $level = 0, array $context = []): void`.

Rules to copy: handler takes no arguments and its return is ignored; superglobals reset per request, `$_ENV` documented as not
reset; first call marks the worker ready; call only at top level; outside worker mode throw `RuntimeException`; return false on
QUIT, reconfigure and when `limits.requests` is reached; a fatal error ends the script and the process manager restarts it
with backoff; `$_SERVER` before the first call holds the process environment.

Opt-in alias layer (config key, off by default): define `frankenphp_handle_request`, `frankenphp_finish_request`,
`frankenphp_request_headers`, `frankenphp_response_headers` and set `$_SERVER['FRANKENPHP_WORKER'] = '1'`. Risk: code that
branches on `function_exists('frankenphp_handle_request')` may also use other FrankenPHP features (Mercure, `frankenphp_log`).
Keep it opt-in and say so in the docs. This is the zero-PR path.

Feasibility sketch (read, not built): src/nxt_php_sapi.c runs `nxt_unit_run()` (line 537) and calls
`nxt_php_request_handler()` (line 1122), which does `php_request_startup()` (1344), `php_execute_script()` (1363),
`php_request_shutdown()` (1377) per request. A worker needs the script run once, then `unit_handle_request()` calls
`nxt_unit_run_once()` (src/nxt_unit.h:229) for one request and does the request startup and shutdown that
`frankenphp_worker_request_startup()` and `frankenphp_worker_request_shutdown()` do (frankenphp.c:513, 573). The
`fastcgi_finish_request` code already uses `nxt_unit_request_done_detached()`. The SAPI name is `"cli-server"` (line 302);
adapters that test `PHP_SAPI` should be checked.

## 9. Adapters FreeUnit needs

| Framework | Zero-PR path (alias layer) | Native adapter | Size | Send the PR to |
|---|---|---|---|---|
| Yii3 | Works if `frankenphp_handle_request` exists. | `Runner` copy that checks `unit_is_worker()` | copy of a 253-line file; the lines that change are the `function_exists` check and the loop call | New package `freeunit/yii-runner-unit`, or a PR to https://github.com/yiisoft/yii-runner-frankenphp to detect both |
| Symfony >= 7.4 | Works if `$_SERVER['FRANKENPHP_WORKER']` is set. | `Runner` + `Runtime` (copy of php-runtime/frankenphp-symfony) | 63 + 38 lines | New package `runtime/unit-symfony` under https://github.com/php-runtime (community org, accepts new runtimes); core PR to symfony/runtime is a harder ask |
| Laravel Octane | `octane:start --server=frankenphp` still starts Caddy, so the alias layer does not help. Manual `bin/unit-worker.php` works. | `bin/unit-worker.php` (copy of frankenphp-worker.php, 105 lines), `UnitClient` (copy of FrankenPhpClient, 51), `StartUnitCommand` writing a unit.json and running `unitd` (StartFrankenPhpCommand is 389 lines), `server` match arm and install stub | about 150 lines for worker plus client; 300 to 400 more for the command | https://github.com/laravel/octane. Expect a hard sell: three servers hard-coded, Unit is not in Laravel docs. Start as a separate package that registers a command. |
| Slim, Mezzio, plain PSR-15 | Plain superglobal loop (docs/worker.md shows one). | none | 0 | Document a short worker script |

## 10. Adoption signals (read 2026-10-06)

Servers:

| Server | GitHub stars | Docker Hub pulls | Latest release | Cadence |
|---|---|---|---|---|
| FrankenPHP (php/frankenphp) | 11,389 | dunglas/frankenphp 11,486,704 | v1.13.0, 2026-10-04 | 12 releases 2025-12-24 to 2026-10-04 |
| RoadRunner | 8,511 | spiralscout/roadrunner 1,138,776 (current image is on ghcr, so undercounts) | v2025.1.15, 2026-06-17 | monthly patches to 2026-06-17, none since |
| Swoole | 18,922 | phpswoole/swoole 2,317,410 | n/a | not checked |
| OpenSwoole | 862 | openswoole/swoole 243,192 (last push 2025-02-04) | n/a | n/a |
| nginx/unit (archived 2025-10-08) | 5,538 | nginx/unit 12,524,323 (last push 2023-02-28) | 1.34.x | frozen |
| FreeUnit | 57 | freeunitorg/freeunit 11,913 | 1.37.0 | active |
| Rapira | 46 | ghcr only | v0.9.0, 2026-10-05 | v0.7.0 08-15, v0.8.0 09-02, v0.8.1 09-05, v0.9.0 10-05; created 2026-06-12 |
| OxPHP | 4 (AGPL-3.0, "not production-ready") | ghcr only | v0.12.0, 2026-09-25 | 12 releases in 7 months |

Adapter packages (Packagist, total / monthly):

| Package | Total | Monthly | Note |
|---|---|---|---|
| symfony/runtime | 114,191,066 | 5,648,735 | every Symfony app; FrankenPHP runner is inside since 7.4 |
| runtime/frankenphp-symfony | 4,207,082 | 225,291 | legacy path for Symfony < 7.4 |
| runtime/swoole | 198,601 | 3,178 | |
| runtime/roadrunner-symfony-nyholm | 132,893 | 2,523 | |
| laravel/octane | 33,914,939 | 2,658,741 | 4,046 stars, v2.20.0 2026-09-22 |
| spiral/roadrunner-http | 12,498,434 | 689,435 | |
| spiral/roadrunner-worker | 14,908,952 | 927,805 | |
| spiral/roadrunner-bridge | 2,380,283 | 122,404 | Spiral framework adapter |
| yiisoft/yii-runner-http (classic) | 254,883 | 19,396 | |
| yiisoft/yii-runner-roadrunner | 13,809 | 1,245 | 34 stars, since 2021 |
| yiisoft/yii-runner-frankenphp | 1,185 | 590 | 6 stars, 1.0.0 announced 2026-04-12 |
| yiisoft/yii-runner-rapira | 28 | 28 | 1 star, no stable tag |

Where servers are listed:

| Docs | Lists | Unit? |
|---|---|---|
| Symfony runtime component (7.4 branch, components/runtime.rst) | PHP-PM, ReactPHP, Swoole, FrankenPHP | no |
| Symfony web server page (setup/web_server_configuration.rst) | FrankenPHP section; Nginx, Apache | yes, one tip line linking "How to run Symfony applications using NGINX Unit" |
| Laravel Octane docs (12.x octane.md) | FrankenPHP, Open Swoole, Swoole, RoadRunner | no (no match in octane, deployment, installation) |
| FrankenPHP docs | pages for Symfony, Laravel, Yii3 (docs/yii3.md), WordPress | n/a |
| Yii3 | FrankenPHP runner announced on yiiframework.com/news/800; RoadRunner docs page for Yii 3 | not checked |

Packagist search "nginx unit" finds no worker runner: only a log analyzer, SDKs and one module (baks-dev/nginx-unit, 49 downloads).

## 11. Corrections and open points

- `FRANKENPHP_WORKER_CONFIG` was not found in frankenphp.c, worker.go, config.md or worker.md. The real names are
  `$_SERVER['FRANKENPHP_WORKER']` and `FRANKENPHP_CONFIG`. GitHub code search needs auth, so a repo-wide absence is unproven.
- The Yii Rapira "dispatcher" runner is synchronous; "async" exists only as an example in the Rapira repo.
- Not verified: whether FreeUnit's SAPI provides `getallheaders()` (Yii falls back to `$_SERVER`); Yii guide pages; Swoole cadence.
- No code was built or run. The section 8 sketch comes from reading source only.

Other sources: offline archive of nginx/unit and freeunitorg/freeunit (skill freeunit-gh-archive, stamped 2026-09-11);
Packagist, Docker Hub and GitHub REST APIs queried 2026-10-06; file URLs are in the sections above.
