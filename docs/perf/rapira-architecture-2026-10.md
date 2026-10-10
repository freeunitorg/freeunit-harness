# Rapira 0.9.0 architecture, for comparison with FreeUnit

Source: a local Rapira checkout, HEAD `74069f0`
("chore(main): release 0.9.0", 2026-10-05). Every `crates/...` and `src/...`
citation is at that commit. FreeUnit citations are at `origin/master` `47d2f933`.
Benchmark data: https://github.com/yii3-benchmarks/app-api at `6e67cb5`, result set
`results/samdark_2026-10-05-pg2000`, and the per-stage table
`docs/perf/yii3-benchmark-stages-2026-10-05.csv` in this harness.

Caveat: the benchmark image copies `ghcr.io/rapira-rs/rapira:nightly-php8.5` (app-api
`docker/runtimes/Dockerfile`), not the 0.9.0 tag. The last Rapira run started 18:44Z; the 0.9.0 commit is 19:06Z.

## 1. What Rapira is, and its release state

- A PHP application server in Rust. It links libphp (the embed SAPI) into its own binary
  and serves HTTP itself. There is no separate web server and no separate PHP process type.
- Version 0.9.0 (`Cargo.toml:6`). MIT. Packaging maintainer: Valery Piashchynski
  (`nfpm.yaml`), the RoadRunner author. The design borrows RoadRunner and PHP-FPM names
  (`max_requests`, `request_terminate_timeout`, `process_control_timeout`, scoreboard).
- Size: 31,007 lines of Rust in `crates/` and `src/`, of which 13,785 are `crates/tests`.
  C glue: 1,295 lines in `crates/sapi/*.c|*.h` and 2,260 in the plugin C files and headers.
- Workspace crates: `master` (fork and supervise), `sapi` (PHP embedding, modes),
  `net` (listener and accept), `plugins/http`, `plugins/grpc`, `middleware/static_files`,
  `interceptors/auth`, `observability`, `scoreboard`, `config`, `php_build`, `tests`.
- Dependencies that matter (Cargo.lock): hyper 1.11.1 (HTTP/1.1 server only for the
  `http` plugin), tokio 1.53.1, tower 0.5, tower-http 0.7.1 (`ServeDir` for static files),
  connectrpc (git pin) for gRPC, socket2, mimalloc 0.1.52 as the global allocator for
  Rust code (`src/main.rs:18-19`). PHP's own heap stays the Zend memory manager.
- NTS only. `crates/sapi/rapira_sapi.h:15-16` is a `#error` on a ZTS header set.
- libphp is linked dynamically: `cargo:rustc-link-lib=dylib=php` (`crates/sapi/build.rs:24`).
  Zend structs come from bindgen at build time, so the binary is tied to one PHP minor.
  `check_linked_php()` refuses a libphp of another minor at boot (`crates/sapi/src/start.rs:70-81`).
- PHP versions: 8.4 and 8.5 in releases (README). `cfg(php87)` exists for 8.7 development
  headers (`crates/sapi/build.rs:26-33`). On 8.4 the SAPI registers as `"fastcgi"` so that
  OPcache starts (`crates/sapi/src/module.rs:26-32`).
- Release libphp: built from php-src with `.github/php-configure-flags.txt`
  (`--enable-embed=shared --disable-all` plus a fixed list: opcache, pdo_pgsql, intl,
  sockets, ffi and others). Docker image `ghcr.io/rapira-rs/rapira:php8.5` is a `scratch`
  payload: the `rapira` binary, `libphp.so` from the official `php:8.5-cli-trixie` image,
  and six extra extensions (`Dockerfile`).
- Platforms: linux-x86_64, linux-aarch64 (built in rockylinux:9, glibc 2.34), macos-aarch64
  (`.github/workflows/build-binaries.yml:111-133`). deb and rpm `rapira-php8.4|8.5`, libphp
  under `/usr/lib/rapira` (`nfpm.yaml`). macOS uses a tokio accept loop (`crates/net/src/lib.rs:111-144`).

## 2. Process and thread model

- **Master process.** Single-threaded. It parses `rapira.toml`, binds every listener
  (`src/main.rs:93-109`), then runs PHP MINIT once (`rapira_sapi::boot_master`,
  `src/main.rs:134-138`, `crates/sapi/src/start.rs:92-110`). Then it forks workers
  (`crates/master/src/process.rs:148-149`). Workers inherit the MINIT state and the OPcache
  shared memory. FreeUnit does the same: `nxt_php_setup` calls `php_module_startup` in the
  prototype before forking apps (`src/nxt_php_sapi.c:445`).
- **Pools.** One pool per plugin: `[http.pool]`, `[grpc.pool]`, plus an observability pool
  that is also a forked process (`src/main.rs:117-131`). Each pool runs a fixed `processes`
  count. The default is `available_parallelism()` (`crates/config/src/pool.rs:52-56`).
  There is no dynamic scaling. The scoreboard reserves two slots per worker for reload
  overlap and caps the total at 4096 slots (`crates/master/src/lib.rs:46-52`,
  `crates/scoreboard/src/lib.rs:4`).
- **Threads in one HTTP worker process** (counted from the code, not measured): main
  (`src/worker.rs:24-80`); the PHP thread, the only one that runs PHP
  (`crates/sapi/src/start.rs:150`); the plugin thread with the blocking accept loop
  (`crates/sapi/src/plugin.rs:154`); 2 tokio worker threads for connections
  (`crates/sapi/src/plugin.rs:141-146`); a signal thread (`src/worker.rs:83-95`); a lifeline
  thread (`crates/master/src/lifeline.rs:5-18`). That is 7, plus tokio blocking threads on demand.
  20 workers give about 140 threads, plus the master and the observability process.
- **Listening socket.** The master binds it with `SO_REUSEADDR`, backlog 65535 (capped by
  `somaxconn`), no `SO_REUSEPORT` (`crates/net/src/listen.rs:12,35-55`). The master keeps its
  copy so respawned workers inherit it. All workers share one accept queue.
- **Accept and distribution (Linux).** Each worker registers the shared listener in its own
  epoll instance with `EPOLLEXCLUSIVE` and blocks in `epoll_wait` on the plugin thread
  (`crates/net/src/accept_linux.rs:105-115`). After each accept it deletes and re-adds the
  listener, which moves this worker to the tail of the kernel wait queue: a round-robin
  borrowed from nginx (`crates/net/src/accept_linux.rs:117-124`). That costs two extra
  `epoll_ctl` calls per connection, not per request. The accepted socket gets `TCP_NODELAY`
  and is handed to tokio (`crates/net/src/lib.rs:170-193`).
- **Connection ownership.** A connection belongs to the worker process that accepted it for
  its whole life. All its keep-alive requests go to that worker's single PHP thread. There is
  no central router and no work stealing between workers. With 256 connections and 20
  workers, each worker owns about 13 connections.
- **Request handoff.** A tokio task runs hyper for the connection. It builds a Rust
  `Request`, boxes it as a `Work` unit and pushes it into a `std::sync::mpsc::sync_channel`
  of capacity 1024 (`crates/sapi/src/start.rs:136`). The PHP thread blocks in `recv()` on that
  channel. If the queue is full, the sender retries every 1 ms for up to 30 s and then answers
  503 (`crates/sapi/src/work.rs:12,133-160`). The response comes back on a tokio `mpsc`
  channel of capacity 4 (`crates/plugins/http/src/exchange.rs:11`).
- **Recycling.** `max_requests` (default 0, unlimited) gets a per-process jitter: the
  effective quota is `max + 1 + hash(pid) % (max/2)`, so 10000 becomes 10001 to 15000
  (`src/worker.rs:12-21`). Every handled request ticks the quota (`crates/sapi/src/scoreboard.rs:44`,
  `crates/sapi/src/quota.rs:46-57`). At the quota the worker stops accepting, drains its
  connections and exits with code 88; the master respawns at once (`crates/master/src/lib.rs:20-26`).
  There is no memory-based recycle. The `rapira_worker_rss_bytes` metric is report only.
- **Request timeout.** `request_terminate_timeout_secs` (default 0, off) is enforced by the
  master watchdog from the scoreboard: SIGTERM, then SIGKILL on a later tick
  (`crates/master/src/pool.rs:281-308`). It kills the whole worker process.
- **Signals.** SIGHUP or SIGUSR2: rolling reload. One new-generation worker is forked first,
  and an old one is drained only after the new one reports idle or active
  (`crates/master/src/pool.rs:138-147`). The TOML is not re-read: `serve()` parses it once and
  the fork closure reuses it (`src/main.rs:111-162`). SIGUSR1 logs status. SIGTERM, SIGINT,
  SIGQUIT stop gracefully; a second one forces (`crates/master/src/pctl.rs:56-72`).
- **Scoreboard.** One `MAP_SHARED|MAP_ANONYMOUS` mmap made before the fork. Each slot holds
  atomics: state, pid, handled, errors, recycles, last_activity_ms, pending, exit counters,
  failed_on_full_queue (`crates/scoreboard/src/lib.rs:14-31,60-75`). The master watchdog and
  the Prometheus endpoint read it.

## 3. The three modes

The mode is set per pool by `mode = "classic" | "worker" | "dispatcher"`. The default is
`dispatcher` (`crates/config/src/pool.rs:64`). The gRPC plugin serves dispatcher only
(`crates/plugins/grpc/src/lib.rs:57-59`).

### Classic

- Per request: `php_request_startup()`, `php_execute_script(entrypoint)`,
  `php_request_shutdown()` (`crates/sapi/src/classic_worker.rs:22-49`). This is the same
  lifecycle as FreeUnit's PHP module (`src/nxt_php_sapi.c:1344-1377`) and PHP-FPM.
- Nothing persists across requests except OPcache and the process. The working directory is
  kept: workers chdir once and set `SAPI_OPTION_NO_CHDIR` (`crates/sapi/module.c:399-405`).
- PHP sees normal superglobals, `header()`, `echo`. The application needs no Rapira API.

### Worker

- The entrypoint runs once inside one long "boot request" (`php_request_startup` with CLI-like
  argv, `crates/sapi/src/rapira_worker.rs:52-75`). The script loops on
  `\Rapira\handle_request(callable $handler): bool` (`crates/sapi/rapira_dispatcher.c:44-70`).
- Per request, inside the boot request (`crates/sapi/src/rapira_worker.rs:151-197`):
  bind the request context, `rapira_request_activate()` = `php_output_activate`,
  `sapi_activate`, RINIT of `filter` and `imap` only, output buffering per ini, drop `$_FILES`
  and `$_SESSION`, re-arm and rebuild the superglobals (`crates/sapi/module.c:126-237,300-319`).
  Then call the handler with no arguments. Then `rapira_request_teardown()`:
  `php_output_end_all`, session flush and reset, `php_output_deactivate`, `sapi_deactivate`
  (`crates/sapi/module.c:336-367`). The engine itself is not deactivated: no
  `zend_deactivate`, no executor shutdown, no heap reset, no destructor sweep
  (`crates/sapi/module.c:280`).
- What persists: every object, class, static, the autoloader and the heap.
- PHP API: the handler reads `$_SERVER`, `$_GET`, `$_POST`, `$_COOKIE`, `php://input`, and
  answers with `header()` and `echo`. `rapira_finish_request()` sends the response early.
  `handle_request()` returns false when the worker must stop.
- A fatal error (bailout) in a handler makes the cycle "recycle": `php_request_shutdown`, then
  the entrypoint runs again in the same process and thread (`crates/sapi/src/rapira_worker.rs:78-136`).
  Connections survive. Five boot failures in a row mark the worker unhealthy (line 32).

### Dispatcher (the default)

- The script calls `\Rapira\get_dispatcher()` once and loops on
  `$dispatcher->receive(int $timeout = -1): Exchange` (also `tryReceive(): ?Exchange`).
  It ends on `Rapira\Exception\ClosedException` (README; `crates/plugins/http/rapira_http.stub.php`).
- `Rapira\Http\Exchange`: `getRequest(): Request`, `writeHead(int $status, array $headers = [])`,
  `writeBody(string $content, bool $eos = true)`, `sendFile(path, offset, length, eos)`,
  `writeTrailers(array)`, `flush()`, `isFinalized()`, `isCancelled()`.
- `Rapira\Http\Request` is a `final readonly` class: `method`, `uri`, `target`, `authority`,
  `protocol`, `headers` (name to list of values, wire order), `body` (string, or a parsed
  `Multipart` with fields and spooled files), `remote`, `server`, `tls`, `receivedAt`.
  It is built once per exchange on the first `getRequest()` call, with cached property slot
  offsets (`crates/plugins/http/src/php/request.rs:155-263`).
- Per request there is no SAPI activation, no superglobal build and no output layer.
  `receive()` pulls the unit, re-arms the `max_execution_time` timer and attaches the unit to
  an object (`crates/sapi/src/exchange/receive.rs:11-87`). The HTTP config sets
  `superglobals = (mode != Dispatcher)`, so the transport thread does not even build the
  CGI strings (`crates/plugins/http/src/config.rs:296`, `crates/sapi/src/types.rs:243-254`).
- One unit at a time: `receive()` throws while the previous exchange is unfinalized
  (`crates/sapi/src/exchange/receive.rs:21-26`). There is no concurrency inside one PHP thread.
- Response body: streamed. Each `writeBody()` copies the PHP string once into a `Bytes` frame
  (`crates/plugins/http/src/php/respond.rs:299`). `writeBody(..., eos: true)` seals the response
  at once. Request body: not streamed. It is fully buffered before dispatch (section 4).

### How the Yii runner uses the modes

`worker-rapira.php` builds `Yiisoft\Yii\Runner\Rapira\RapiraApplicationRunner` and calls
`run()`. The runner (yiisoft/yii-runner-rapira `245eaf4`) detects the mode with
`function_exists('Rapira\get_mode') ? get_mode() : Mode::Classic`. Then
(`src/RapiraApplicationRunner.php:149-153`):

- Classic: `SapiServer::once()`. The full Yii bootstrap (config merge, DI container,
  router) runs on every request, as with FreeUnit, FPM and FrankenPHP classic.
- Worker: `SapiServer::run()` = `while (handle_request($handler));`. It builds the PSR-7
  request with `SapiRequestFactory` from superglobals and emits with a SAPI emitter
  (`header()` plus `echo`).
- Dispatcher: `DispatcherServer::run()` = `while (true) serve($dispatcher->receive())`.
  It builds PSR-7 from `Exchange::getRequest()` and emits with `ExchangeEmitter`
  (`writeHead` plus one `writeBody` for a body of known size).
- Both persistent modes call `RequestCycle::finish()` after emit: `afterEmit`,
  `StateResetter::reset()` and `gc_collect_cycles()` on every request.

### Why dispatcher beats worker (111,499 vs 102,703 rps)

At saturation (150k target stage) the container used 19.20 cores for dispatcher and
19.29 cores for worker (stages CSV). That is 0.172 ms and 0.188 ms of CPU per request.
Worker spends about 9% more CPU per request. The extra work is all on the single PHP thread:

1. Request in. Worker mode builds `ReqC` CStrings on the transport thread
   (`crates/sapi/src/types.rs:243-254`), then on the PHP thread runs `sapi_activate`,
   `php_output_activate`, re-arms every auto global and rebuilds `$_SERVER` through
   `register_server_variables`: about 20 fixed names plus one mangled `HTTP_*` name per header
   (`crates/sapi/src/callbacks.rs:263-345`), plus `$_GET` and `$_COOKIE` parsing. Yii then
   reverses `HTTP_*` back to header names in `SapiRequestFactory`. Dispatcher hands PHP one
   readonly object whose header array comes straight from hyper's `HeaderMap`.
2. Response out. Worker mode goes through `header()` (SAPI header list), the output layer and
   `ub_write`, which appends into a `Vec`; `send_headers` parses the SAPI lines back into a
   `HeaderMap` (`crates/sapi/src/callbacks.rs:164-240`). Dispatcher takes an array and a
   string and builds frames directly.
3. Teardown and latency path. Worker mode runs `php_output_end_all`, session reset,
   `sapi_deactivate`, and `php_call_shutdown_functions` per request
   (`crates/sapi/module.c:281-284,336-367`). Also, the SAPI flush callback is `NULL`
   (`crates/sapi/src/module.rs:39`), so the buffered response leaves only in `Context::finish`
   after the handler and teardown return (`crates/sapi/src/types.rs:273-304`). Yii's
   `StateResetter` and `gc_collect_cycles()` therefore run before the client gets its bytes.
   In dispatcher mode `writeBody(eos: true)` sends first, and that cleanup overlaps the network write.

Below saturation both modes show the same CPU per request (about 0.17 ms at 10k rps), so
items 1 to 3 are a small fixed cost. They matter only when the PHP thread is the bottleneck.

## 4. The request data path

Steps for one HTTP/1.1 request in worker or dispatcher mode, all inside one process:

1. hyper on a tokio thread reads and parses the head into `http::Request`. Header read
   timeout = `keepalive_timeout_secs` (default 60), which in hyper 1.11 also covers the idle
   time between keep-alive requests (`crates/plugins/http/src/serve.rs:39-42`;
   hyper `src/proto/h1/conn.rs:219-233`).
2. `check_request` validates fields. Header names with `_` are dropped by default
   (`unsafe_field_names = "drop"`, `crates/plugins/http/src/lib.rs:43-50`).
3. The body is collected into one `Vec<u8>`, capped by `max_body_size_mb` (default 8)
   with 413, each frame bounded by `keepalive_timeout` (`crates/plugins/http/src/handler.rs:333-361`).
   PHP never sees a partial body. Multipart is parsed in Rust on the blocking pool in
   dispatcher mode only; the other modes feed php-src's rfc1867 parser via `read_post`.
4. `request::build` moves the `HeaderMap` out of hyper's parts without copying values
   (`crates/plugins/http/src/request.rs:8-53`). Method, URI and protocol become owned
   `String`s. The request is boxed and moved over the channel: a pointer move, no serialization.
5. The PHP thread wakes (futex in `std::mpsc`). Classic and worker modes read from the
   C strings and the header map through SAPI callbacks; `read_post` copies body bytes into PHP's
   buffer (`crates/sapi/src/callbacks.rs:242-251`). Dispatcher builds zvals from the same data.
6. Response: classic and worker modes buffer the whole body in `Context.body` (cap 1 GiB,
   `crates/sapi/src/callbacks.rs:31`) and send `Head + one Chunk + End` with a
   `content-length` at the end. `flush()` in PHP does nothing for the client. Dispatcher sends
   frames as written; a known length is framed with `content-length`, otherwise chunked.
7. The tokio task waits up to 10 ms for the first body frame so head and body go out
   together (`crates/plugins/http/src/handler.rs:416`). `write_timeout_secs` (default 30)
   bounds each write stall (`crates/plugins/http/src/bridge.rs:240-330`).

Other transport facts:

- HTTP/2 and TLS: none in the `http` plugin. It is `hyper::server::conn::http1` only
  (`crates/plugins/http/src/serve.rs:7,39`). `https` is hard-coded false and `tls` is `None`
  (`crates/plugins/http/src/handler.rs:208`, `crates/plugins/http/src/request.rs:45`). The PHP
  `Tls` class exists but nothing fills it. A TLS proxy in front is required.
- No WebSocket, no `Upgrade`, no compression. `upgrade` and `connection` are stripped from
  PHP's response headers (`crates/plugins/http/src/response.rs:25-35`). Keep-alive has no
  request limit; a connection closes on idle timeout, client close, or worker drain.
- Static files: `[http.static]` middleware with tower-http `ServeDir` (ETag, Range,
  preconditions) and a per-worker cache: 16 MiB total, files up to 256 KiB, revalidated every
  1 s (`crates/middleware/static_files/src/cache.rs:41-46`). A miss falls through to PHP. The
  cache is per process, so 20 workers hold up to 20 copies.
- `Exchange::sendFile()` does not use `sendfile(2)`. It `pread`s slices into heap buffers on the
  blocking pool (`crates/plugins/http/src/bridge.rs:41-52`).
- gRPC plugin: unary RPCs only, gRPC, gRPC-Web and Connect, over connectrpc with HTTP/2
  keepalive settings (`crates/plugins/grpc/src/serve.rs:57-58`), bearer-token interceptor.
  Dispatcher mode only. Separate listener and separate worker pool.

### Hops and copies that FreeUnit pays and Rapira does not

| Step | FreeUnit (router plus app process) | Rapira |
|---|---|---|
| Client socket to PHP | router thread parses, then copies the request into a port shared-memory buffer (`src/nxt_router.c:8345,8428`) | hyper parses; the parsed struct moves by pointer |
| Notify the app | port message over a Unix socketpair, or the app queue plus a wakeup | `std::mpsc` send plus a futex wake |
| Request fields to PHP | libunit reads the shm record; the module copies fields into `$_SERVER` | same superglobal copy in worker mode; dispatcher builds one object |
| Response from PHP | `ub_write` fills shm buffers; each full buffer is a port message to the router (`src/nxt_unit.c:2870,3032`) | `ub_write` appends into a heap `Vec`; dispatcher copies once into `Bytes` |
| Response to client | router copies from shm into the socket write | hyper writes the `Bytes` |
| Processes on the path | 2 (router, app) | 1 |

Both designs still cross threads twice per request (Rapira: tokio thread to PHP thread and
back). Rapira saves the inter-process message, the shm copy each way and the second process
wakeup. In classic mode this saving is invisible: Rapira classic (9,138 rps) is slower than
FreeUnit classic (11,445 rps) on the same application.

## 5. Operational features and gaps

| Feature | Rapira 0.9.0 | FreeUnit |
|---|---|---|
| Configuration | static TOML, read once at start; SIGHUP reloads workers, not config | JSON over a control socket, live changes |
| Apps per server | one `[http]` and one `[grpc]` section, one pool each | many apps, many languages, versions side by side |
| Routing | none; one entrypoint per pool | `routes` with matches, `share`, `fallback`, `return` |
| Proxy | none | `proxy` action |
| TLS | none (`tls: None`) | OpenSSL, SNI, certificate store |
| HTTP/2 | gRPC listener only | none |
| WebSocket | none | yes |
| Access log | none in the code | yes, format and conditions |
| Logs | `tracing` to stdout, text or JSON; PHP errors and `\Rapira\log()` go there | error log, access log |
| Metrics | Prometheus `/metrics` on a separate listener: workers per state, requests, failures, queue depth, exits, RSS and PSS (`crates/observability/src/text.rs:176-213`). No latency histogram | `/status` JSON |
| Health | `/livez`, `/readyz` | none built in |
| OpenTelemetry | none | `--otel` tracing |
| Body limit | `max_body_size_mb`, default 8 | `settings.http.max_body_size` |
| Timeouts | keepalive/header (60), write (30), request terminate (off), queue wait (30, fixed) | header, body, send, idle, app `limits.timeout` |
| Recycle | `max_requests` with jitter; no memory limit | `limits.requests`; no memory limit |
| Scaling | fixed `processes` | static or dynamic `processes` (max, spare, idle_timeout) |
| Isolation | none: no uid/gid switch, chroot, namespaces or cgroups in the code | user/group, namespaces, rootfs, cgroups |
| Uploads | dispatcher: Rust multipart parser with spool dir and limits; others: php-src | php-src |
| Persistent PHP app | worker and dispatcher modes | none for PHP (classic per-request only) |
| gRPC | unary, plugin | none |

## 6. Weaknesses and risks visible in code or data

### The socket-read errors in the benchmark

Every Rapira run had wrk `read` socket errors. FreeUnit, FPM, FrankenPHP, RoadRunner and
OxPHP had zero. The likely cause is `max_requests = 10000` in all three Rapira configs
(app-api `docker/runtimes/rapira*.toml`) combined with connection ownership by workers:

- A worker that reaches its quota stops its plugin. hyper's graceful shutdown closes every
  idle keep-alive connection at once and marks busy ones `Connection: close`
  (hyper `src/proto/h1/conn.rs:886-894`). A client that sends its next request on an idle
  connection while the server closes it reads EOF or a reset. wrk counts that as a read error.
- The data matches. In the dispatcher run, errors are 0 in the 2,500 rps stage, when the
  cumulative count per worker (warmup included) stayed below about 4,300, under the 10,001
  floor. Errors start in the 5,000 rps stage, which ends near 11,750 per worker. Below saturation the error count tracks the request
  count: about 20 to 25 errors per 12,500 requests (the mean quota), against about 13
  connections per worker. At saturation (100k and higher) errors fall to 3 or 4 per recycle,
  because busy connections get a clean `Connection: close` instead of a racing close.
  Classic shows the same shape: 149 errors in its last unsaturated stage (5,000 rps), then 21
  to 30 per saturated stage, about one per recycle.
- FreeUnit used the same `limits.requests: 10000` with zero errors, because the router owns
  client connections and an app process exit is invisible to clients.
- The site notes "Rapira worker application logs were empty when inspected after socket-read
  errors". That fits: the recycle message is `info` (`crates/sapi/src/quota.rs:53`), and the
  runs used `level = "warn"`.
- Not the backlog: it is 65535 and shared. Not the 60 s idle timeout: wrk keeps all 256
  connections busy at every stage. Not intake shedding: that answers 503, not a closed socket.
- Confirmation test: rerun with `max_requests = 0`. Errors should go to zero.

### Crash isolation

- One process holds about 13 client connections and up to 1024 queued requests. A segfault in
  PHP or an extension, an uncaught Rust panic outside `guard`, a `request_terminate_timeout`
  kill, or a second `max_execution_time` expiry on NTS (`_exit(124)`, noted at
  `crates/plugins/http/src/php/respond.rs:57`) drops all of them. FreeUnit loses only the
  requests inside the dead app process; the router answers 502 or 503 and keeps the connections.
- A PHP fatal (bailout) is cheaper: the worker re-runs its entrypoint in the same process and
  connections survive (`crates/sapi/src/rapira_worker.rs:78-136`). The requests queued behind
  it wait for the app to boot again.

### Head-of-line blocking per worker

- No work stealing. A slow request blocks every queued request on the same worker while other
  workers may be idle. The kernel spreads connections, not requests. Under keep-alive, a few
  heavy clients can pin one worker. FreeUnit's router can hand the next request to any free
  app process.

### NTS-only consequences

- One request at a time per process. Concurrency equals `processes`. Memory scales with
  processes. There is no thread mode. FreeUnit's PHP app process also runs one request at a time.
- The Zend signal-based timer is per process. `max_execution_time` is disarmed while the
  thread waits in `receive()` and re-armed per unit (`crates/sapi/module.c:102-124`).

### Memory growth

- Worker and dispatcher modes never reset the Zend heap between requests. Leaks accumulate
  until `memory_limit` triggers a fatal (then an in-process restart) or `max_requests` recycles.
  There is no RSS-based recycle; RoadRunner has `max_worker_memory`. 0.9.0 disables THP for
  workers to cut RSS (`src/main.rs:57-68`).
- `max_requests` is the only memory guard, and it causes the client errors described above.

### Other risks

- The 1 GiB response buffer cap per request in classic and worker modes
  (`crates/sapi/src/callbacks.rs:31`). A large download is held whole in memory.
- OPcache SHM is created in the master at MINIT and inherited by every generation, so with
  `validate_timestamps=0` a SIGHUP reload probably keeps old scripts. Inferred, not tested.
- Young project: 0.8.0 replaced Pingora with hyper on 2026-09-02.

## 7. What to measure in a fair FreeUnit-vs-Rapira rerun

1. Pin builds: Rapira `v0.9.0` image by digest (not `nightly`), FreeUnit 1.37.0 or later,
   same `php:8.5.11` NTS base, same `php-production.ini` (`opcache.validate_timestamps=0`,
   JIT off). Record `rapira --version` and the linked PHP version.
2. Equal recycling: run each runtime twice, with `max_requests`/`limits.requests` = 10000 and
   = 0. Report wrk read errors per stage for both. Expect Rapira errors at 0 with 0.
3. Same mode class: compare FreeUnit classic with Rapira classic (both per-request bootstrap),
   and Rapira worker/dispatcher with RoadRunner and FrankenPHP worker. Do not headline a
   classic-vs-persistent ratio as a server difference.
4. CPU per request, not only peak rps: container CPU (cores) divided by successful rps, at a
   fixed unsaturated stage (10k) and at saturation. Current numbers: FreeUnit classic 1.78 ms
   at 10k; Rapira classic about 1.7 ms once saturated (15k and 50k stages); Rapira worker
   0.171 ms and dispatcher 0.170 ms at 10k.
5. Split server overhead from PHP: run a hello-world PHP script (no framework) in classic mode
   on both, and a static file on both. This isolates router-hop cost from Yii bootstrap cost.
6. Latency below saturation: avg, p95, p99 at 10k and 50k targets. The "normal latency" on
   the report (Rapira dispatcher 1.07 ms, p95 1.81 ms) is the mean over unsaturated stages,
   not the latency at 111k rps (5,088 ms avg there).
7. Process and thread budget: host core count, `processes = 20` on both, FreeUnit router
   thread count, and Rapira's 2 tokio threads per worker. Record `top -H` CPU per thread
   type to see whether the router or tokio threads cost cores the PHP workers need.
8. Distribution: per-worker handled counts (Rapira scoreboard `/metrics`, FreeUnit `/status`)
   to check how even the EPOLLEXCLUSIVE rotation and FreeUnit's queue are.
9. Memory: RSS and PSS per worker after 1M requests in persistent mode, and FreeUnit app RSS.
10. Failure behaviour: kill -SEGV one PHP worker under load on both; count client errors.
11. PostgreSQL endpoint: every persistent runtime sat at 12.5k to 12.9k rps and FreeUnit at
    6.2k. Record PostgreSQL CPU and `max_connections` use; the database, not the server, is the
    likely limit there.
