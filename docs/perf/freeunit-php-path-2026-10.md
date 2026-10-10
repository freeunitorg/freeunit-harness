# FreeUnit PHP path: cost per request and a worker mode (2026-10)

Code: freeunitorg/freeunit `origin/master` 47d2f933 ("changelog: open the 1.37.1 section").
Every `file:NNN` is a line at that commit. This is code reading only. Nothing was built or run for this page.
Numbers marked "measured" come from earlier runs on the reference bench host (2026-10-02/03). Numbers marked "estimate" are mine.

Benchmark: https://yii3-benchmarks.github.io/app-api/ (2026-10-05, PHP 8.5.11, 20 PHP workers, 256 connections,
host AMD Ryzen 9 7950X, 16 cores / 32 threads). Config and raw results: github.com/yii3-benchmarks/app-api
at 6e67cb52, `docker/runtimes/freeunit.json`, `results/samdark_2026-10-05/`.

| runtime | home rps | peak container CPU | CPU per request |
|---|---|---|---|
| Rapira worker | 102,703 | 20.49 cores | 0.20 ms |
| FrankenPHP worker | 66,624 | 21.07 cores | 0.29 ms (at 72.3k stage peak) |
| FreeUnit 1.37.0 (php module) | 11,445 | 20.46 cores | 1.79 ms |
| FrankenPHP classic | 10,562 | 21.67 cores | 2.05 ms |
| PHP-FPM + nginx | 10,264 | 19.23 cores (php only) | 1.87 ms |
| Rapira classic | 9,138 | 20.23 cores | 2.21 ms |

CPU per request = peak `cpu_percent` in `docker-stats.csv` / 100 / rps. Every runtime saturates its 20 workers.

Facts about the benchmark setup that matter below:
- FreeUnit is built from the 1.37.0 tarball on `php:8.5.11-cli-trixie`. That PHP is NTS. The build is a release build.
- php.ini is `php.ini-production` (so `output_buffering = 4096`) plus opcache on, `validate_timestamps = 0`, JIT off.
- `"processes": 20`, `"limits": {"requests": 10000}`. No access log.
- The route for `/` goes to `"share": "/app/public$uri"` first, with `"fallback"` to the `index` target (`"script": "index.php"`).
- The home response is about 209 bytes on the wire. The DB response is about 14 KiB.
- Valkey uses 64% of a core in every classic run and 3% in every worker run. The classic bootstrap talks to Valkey per request.

## 1. One PHP request, step by step

Case: HTTP/1.1 keep-alive, `GET /`, response under 1 KiB, `output_buffering = 4096`.
R = router process (one engine per thread, `nxt_ncpu` threads, src/nxt_router.c:3098). A = PHP app process.
"Low load" means idle workers are waiting in poll(). "Saturated" means the app queue is never empty.

| # | P | step and function | syscalls | copies | allocations | port messages | shared memory |
|---|---|---|---|---|---|---|---|
| 1 | R | Accept, once per connection: `nxt_conn_accept` src/nxt_conn_accept.c:253, accept4 in src/nxt_epoll_engine.c:1059. The listen fd is in every engine with EPOLLEXCLUSIVE (src/nxt_epoll_engine.c:616-628). | accept4 1, epoll_ctl ADD 1 | peer address text | `nxt_mp_create(1024)` per connection (src/nxt_conn_accept.c:124) | - | - |
| 2 | R | Keep-alive wait: `nxt_h1p_keepalive` src/nxt_h1proto.c:2401. TCP_NODELAY is set here, at the first keep-alive (src/nxt_h1proto.c:2409-2410). | setsockopt once per connection | memzero of the h1p parser state (src/nxt_h1proto.c:2417) | header buffer from the engine cache (src/nxt_h1proto.c:477) | - | - |
| 3 | R | Read: `nxt_conn_io_recvbuf` src/nxt_conn_read.c:132. Edge-triggered epoll (src/nxt_epoll_engine.c:218-222). A short read clears read_ready, so there is no EAGAIN recv (src/nxt_conn_read.c:212-213). | epoll_wait 1, recv 1 | kernel to header buffer | idle timer added and disabled (rbtree only) | - | - |
| 4 | R | Request and parse: `nxt_h1p_conn_request_init` src/nxt_h1proto.c:527, `nxt_http_request_create` src/nxt_http_request.c:248, header processing src/nxt_h1proto.c:730. | 0 (cached time, src/nxt_http_request.c:280) | parser fields into `r` (src/nxt_h1proto.c:752) | `nxt_mp_create(4096, 128, 512, 32)` per request (src/nxt_http_request.c:254); `r` exceeds the 512-byte page, so it is a large alloc (src/nxt_mp.c:722) | - | - |
| 5 | R | Route. In the benchmark config `/` first runs the share: `nxt_http_static_send` src/nxt_http_static.c:487 opens `/app/public/index.html` (src/nxt_http_static.c:619-623), gets ENOENT and takes the fallback (src/nxt_http_static.c:682). | openat 1, fails | file name built | `nxt_mp_nget` for the name | - | - |
| 6 | R | App handler and RPC: `nxt_http_application_handler` src/nxt_http_request.c:642, `nxt_router_process_http_request` src/nxt_router.c:8126. The stream id is an atomic on a page shared by all processes (src/nxt_port_rpc.c:184). | 0 | - | RPC reg zalloc and lvlhsh insert | - | - |
| 7 | R | Port: `nxt_router_app_port_get` src/nxt_router.c:8030. Always `app->shared_port` (src/nxt_router.c:8041), under `app->mutex` (src/nxt_router.c:8039). Spawn check `nxt_router_app_need_start` src/nxt_router.c:2369. | 0 | - | - | - | - |
| 8 | R | Serialize: `nxt_router_prepare_msg` src/nxt_router.c:8345. PHP names get the `HTTP_` prefix, upper case and `-` to `_`, byte by byte, with an O(n^2) duplicate merge (src/nxt_router.c:8579). | 0 | request line, addresses, every header, and the body, into shm | `nxt_port_mmap_get_buf` src/nxt_port_memory.c:699 under `mmaps->mutex` (src/nxt_port_memory.c:574) | - | one 16 KiB chunk per request, even for 500 bytes |
| 9 | R | Enqueue: `nxt_router_app_prepare_request` src/nxt_router.c:8223, `nxt_app_queue_send` src/nxt_router.c:8291. The READ_QUEUE doorbell goes out only when the queue's `notified` flag goes 0 to 1 (src/nxt_app_queue.h:84). | sendmsg 1 at low load, 0 when saturated | 28-byte descriptor | - | REQ_HEADERS (queue), READ_QUEUE (socket) | app queue slot |
| 10 | A | Wait: `nxt_unit_run` src/nxt_unit.c:5811, `nxt_unit_read_buf` src/nxt_unit.c:5898. It checks its own queue (src/nxt_unit.c:5951) and the app queue (src/nxt_unit.c:5970) before `poll()` on its own port and the shared fd (src/nxt_unit.c:5975-5990). All idle workers poll the same fd. | low load: poll 1 in every idle worker (the herd); saturated: 0 | - | rbuf from a pool (src/nxt_unit.c:3251) | - | - |
| 11 | A | Dequeue: `nxt_unit_app_queue_recv` src/nxt_unit.c:7955. The doorbell datagram stays in the socket; a worker later drains it (src/nxt_unit.c:7805-7825). | 0, plus recvmsg READ_QUEUE and recvmsg EAGAIN in some worker | 28 bytes from the slot | - | - | CAS ops; `request_count` add if a limit is set (src/nxt_unit.c:7985) |
| 12 | A | Map and set up: `nxt_unit_process_msg` src/nxt_unit.c:1125, `nxt_unit_mmap_read` src/nxt_unit.c:5439, `nxt_unit_process_req_headers` src/nxt_unit.c:1549, `nxt_unit_request_info_get` src/nxt_unit.c:2168. | 0 | none: pointers into shm | pooled | - | `incoming.mutex` |
| 13 | A | ACK: `nxt_unit_send_req_headers_ack` src/nxt_unit.c:1975, called at src/nxt_unit.c:1751. A message with a stream and `last = 0` must use the socket (rule at src/nxt_unit.c:7437-7449). | sendmsg 1 | 16 bytes | - | REQ_HEADERS_ACK | READ_SOCKET marker |
| 14 | R | ACK: `nxt_router_req_headers_ack_handler` src/nxt_router.c:6398. | recvmsg 1 | - | lvlhsh insert (src/nxt_router.c:6412) | - | - |
| 15 | A | PHP setup: `nxt_php_request_handler` src/nxt_php_sapi.c:1122, `nxt_php_execute` src/nxt_php_sapi.c:1276: `fopen()` of the script (src/nxt_php_sapi.c:1290), SG fields point into shm (src/nxt_php_sapi.c:1301-1340). | openat 1, close 1 at handle destroy | none | glibc FILE | - | - |
| 16 | A | `php_request_startup()` src/nxt_php_sapi.c:1344. chdir only when the target changes (src/nxt_php_sapi.c:1148, :1356-1358). | PHP internal (timer for max_execution_time, unverified) | - | Zend heap reuse | - | - |
| 17 | A | `$_SERVER`: `nxt_php_register_variables` src/nxt_php_sapi.c:1600; one zval per field (src/nxt_php_sapi.c:1702). | 0 | every value into a zend string | about 25 strings | - | - |
| 18 | A | `php_execute_script()` src/nxt_php_sapi.c:1363: the Yii bootstrap and the handler. The SAPI name is `cli-server` (src/nxt_php_sapi.c:302), so the benchmark's `public/index.php` also runs its built-in-server branch: `parse_url` and `is_file()` per request. | stat 1, Valkey I/O | - | the whole app object graph | - | - |
| 19 | A | Headers: `nxt_php_send_headers` src/nxt_php_sapi.c:1473, `nxt_unit_response_init` (src/nxt_php_sapi.c:1516), `nxt_unit_response_add_field` src/nxt_unit.c:2705, `nxt_unit_response_send` (src/nxt_php_sapi.c:1544, src/nxt_unit.c:2814). With output buffering this runs when the buffer is flushed at shutdown. | sendmsg 1 | fields into the buffer, then kernel | `malloc(size + 16)` every request, because `local_buf` is NULL (src/nxt_unit.c:5076, from :2899) | DATA, last = 0 | - |
| 20 | R | Headers: `nxt_router_response_ready_handler` src/nxt_router.c:6104, parse at src/nxt_router.c:5985. | recvmsg 1 | none: pointers into the port buffer | field array | - | - |
| 21 | A | Body: `nxt_php_unbuffered_write` src/nxt_php_sapi.c:1412, `nxt_unit_response_write` (src/nxt_php_sapi.c:1430), `nxt_unit_response_write_nb` src/nxt_unit.c:3353. One call per output-buffer flush. | sendmsg 1 per call | 1 KiB or less: stack buffer (src/nxt_unit.c:3362), then kernel. More: into a new 16 KiB shm chunk per call | - | DATA, last = 0 | over 1 KiB: `nxt_unit_mmap_get` src/nxt_unit.c:4592 |
| 22 | R | Output: `nxt_h1p_request_header_send` src/nxt_h1proto.c:1560. PHP sends no Content-Length, so HTTP/1.1 gets chunked (src/nxt_h1proto.c:1644-1658). `nxt_h1p_request_send` src/nxt_h1proto.c:1838, chunk header src/nxt_h1proto.c:1883. | recvmsg 1; writev or send 1 to 2 (src/nxt_conn_write.c:323) | fields into the header buffer (src/nxt_h1proto.c:1696-1740) | chunk header buffer | - | large bodies: zero copy from shm |
| 23 | A | `php_request_shutdown()` src/nxt_php_sapi.c:1377 runs before the request ends (src/nxt_php_sapi.c:1380): shutdown functions, destructors, frees. The client waits for this, because the chunked tail needs the last message. | PHP internal | - | frees | - | - |
| 24 | A | Done: `nxt_unit_request_done` src/nxt_unit.c:4351, last message at src/nxt_unit.c:4396-4405. It is 16 bytes with `last = 1`, so it uses the router's port queue. | 0, or sendmsg 1 when the router queue was empty | 16 bytes into shm | pools released (src/nxt_unit.c:2213) | DATA, last = 1 | incoming chunks marked free (src/nxt_unit.c:5619) |
| 25 | R | Last: src/nxt_router.c:6164 adds the last buffer; `nxt_request_rpc_data_unlink` src/nxt_router.c:1423; tail `0\r\n\r\n` (src/nxt_h1proto.c:1918); `app->mutex` twice (src/nxt_router.c:7630, :1454). The worker goes idle in `nxt_router_app_port_idle` src/nxt_router.c:7366. | recvmsg 1 (the notice), send 0 to 1 | tail bytes | release timer 0 ms (src/nxt_router.c:8779) | - | - |
| 26 | R | Port drain: `nxt_port_queue_read_handler` src/nxt_port_socket.c:1963. Each drain ends in EAGAIN and then re-enables read (src/nxt_port_socket.c:2251), which issues EPOLL_CTL_MOD because the state is ACTIVE (src/nxt_epoll_engine.c:461-480). | recvmsg EAGAIN 1 and epoll_ctl MOD 1, per drain (1 to 3 drains) | - | - | - | - |
| 27 | R | Close: `nxt_http_request_close_handler` src/nxt_http_request.c:1188. Access log write (src/nxt_router_access_log.c:382) only if configured. | 0 here | - | `nxt_mp_destroy` | - | - |

There is no "ready" message per request. The router learns which worker took the request from the ACK (row 14),
and that it is idle again from the last message (row 25). The shm poison fill is debug-only on master:
src/nxt_unit.c:5627, src/nxt_port_memory.c:160-163 (commit 76c406b3 is an ancestor of 47d2f933).
The K3 chdir bug is fixed: `nxt_php_last_target` is set from the saved `app_target` (src/nxt_php_sapi.c:1153),
and CHANGES 1.37.0 says so. A target with only `"root"` still calls chdir on every request (src/nxt_php_sapi.c:1241).

Process lifecycle. The prototype runs `nxt_php_setup` (src/nxt_php_sapi.c:393) once: `php_module_startup`, opcache
shared memory, preload (src/nxt_application.c:569, call at :607). Each worker is forked from it and runs
`nxt_php_start` (src/nxt_php_sapi.c:476): `nxt_unit_init` (:530), `nxt_unit_run` (:537), `exit(0)` (:543).
`limits.requests` is enforced in libunit: at the limit it posts a graceful QUIT to itself (src/nxt_unit.c:7983-7997);
the worker quits when its last request is released (src/nxt_unit.c:2259-2261). In the benchmark each worker restarts
about every 17 s (10,000 requests at 572 rps per worker).

Syscalls per request (estimate from the code, small response):

| side | low load | saturated |
|---|---|---|
| router | 10 to 16: epoll_wait 2-4, recv 1, openat 1 (benchmark share), sendmsg 1, recvmsg 3-4, recvmsg EAGAIN 1-3, epoll_ctl MOD 1-3, writev 1-2 | about 8 |
| app (libunit) | about 8: poll 2, recvmsg 2, sendmsg 4; plus 2-3 for every other idle worker woken | about 3: ACK, headers, body |
| app (PHP) | openat, close, stat (index.php), timer, Valkey send and recv | same |

Measured on the reference bench host (hello.php, HTTP/1.1 keep-alive, 2026-10-03): router about 58 us CPU per request, PHP process
about 105 us; 4.9 voluntary context switches per request at c=6 and 2.76 at c=32; 2.46 TCP segments per response.

## 2. Where the 10x goes

FreeUnit classic spends about 1.79 ms of CPU per request (20.46 cores / 11,445 rps). Rapira worker spends 0.20 ms
for the same app and response. The split, best estimate:

| part | ms per request | share | reasoning | removable by FreeUnit |
|---|---|---|---|---|
| (a) Yii bootstrap per request, PHP side | 1.45 to 1.55 | 81-87% | 1.79 total, minus the handler (about 0.15, from Rapira worker 0.20 all-in), minus (b). Includes `php_request_startup`/`shutdown` with a full app graph, class linking from opcache, DI and config build, and the Valkey round trip (Valkey CPU 64% classic vs 3% worker). | Only by worker mode (section 3). opcache.preload helps class linking, not the object graph. |
| (b) router + IPC | 0.08 to 0.11 | 5-6% | router 58 us (measured, on other cores) plus about 25-50 us of libunit and syscalls inside the worker | About half, with K2, the ACK and the epoll fix (section 4) |
| (c) scheduling | 0.01 to 0.03 | about 1% | about 5 context switches per request at low load; near 0 at saturation, where workers find the queue non-empty | K1 |

Consequences:
- In classic mode the 20 workers are the bottleneck. Router work runs on other cores. So router savings give close to
  0 rps in this benchmark. App-side savings (rows 13, 19, 21) give at most 2%.
- FreeUnit is already the fastest classic runtime in the table. Its server overhead is not what separates it from
  the worker runtimes.
- The PostgreSQL case (classic about 6.1-6.4k for every runtime, worker 12.5-13.6k) is the same split: the classic
  request opens a new database connection and rebuilds the app. Worker mode keeps the PDO connection.
- In worker mode the router does matter. At 100k rps a 58 us router needs 5.8 cores, on a host with 16 physical cores
  that also runs the load generator and 20 busy workers. Rapira's whole budget fits in 20.5 cores. So the section 4
  items decide whether FreeUnit worker mode lands near FrankenPHP (67k) or near Rapira (103k).

Worker-mode estimate for FreeUnit: handler 150 us + per-request reset 10-20 us + libunit 20-30 us = 180-200 us of
worker time, plus 58 us of router time. On about 21 usable cores that is 80-90k rps. With K2, the ACK cut and the
epoll fix (router about 40 us, libunit about 12 us), 90-100k rps. This needs a measurement (section 6, Q5 and Q9).

## 3. Worker mode for PHP

### What exists today

- One request = one PHP request: `php_request_startup` (src/nxt_php_sapi.c:1344), `php_execute_script` (:1363),
  `php_request_shutdown` (:1377). Shutdown destroys every user variable, object, static property and declared
  class or function. opcache keeps compiled scripts and immutable classes in shm. Persistent resources and
  extension globals survive. So no application state survives between requests today.
- The harness page docs/php-lifecycle.md says "Statics, singletons ... persist for the life of the worker". That is
  wrong for user-level PHP state, and its async section and line numbers do not match master. Fix it or point it here.
- `nxt_php_unit_module` (src/nxt_php_sapi.c:185) registers one function: `fastcgi_finish_request`
  (src/nxt_php_sapi.c:177, body at :231). It ends output, re-arms `$_SERVER` (:262-270), and calls
  `nxt_unit_request_done_detached` (:282), so the router keeps the worker busy until the handler returns.
- `PHP_MINIT_FUNCTION(nxt_php_ext)` (src/nxt_php_sapi.c:199) wraps `chdir()` to track the directory.
- libunit already supports what a loop needs: `nxt_unit_run_once` (src/nxt_unit.c:5849) waits for and processes one
  message and calls the request handler; a handler may return without `nxt_unit_request_done` (ASGI and Node.js
  do this). `nxt_unit_dequeue_request` (src/nxt_unit.c:6425) exists but does not wait, and no module uses it.
- FrankenPHP's model (php/frankenphp `frankenphp.c`, main, read 2026-10-06): the worker script runs inside one
  long PHP request. `frankenphp_handle_request(callable)` waits for a request, then does a partial request startup
  (`php_output_activate`, `sapi_activate`, re-import of superglobals), calls the callable, and does a partial
  shutdown (`php_output_end_all`, `php_output_deactivate`, `sapi_deactivate`).

### The smallest change

Config: a new PHP target member `"worker": true` (or an app-level `"worker"` script path). Files:
src/nxt_conf_validation.c (php members at :1062-1159, about 10 lines), src/nxt_application.h `nxt_php_app_conf_t`
(:63-65, 1 line), src/nxt_main_process.c `nxt_php_app_conf` (:325, about 5 lines). First version: one worker
target per app; requests for other targets of that app answer 500 with an alert.

PHP function, registered next to `fastcgi_finish_request` in `nxt_php_ext_functions` (src/nxt_php_sapi.c:176-178):
`unit_handle_request(callable $handler): bool`. FrankenPHP-compatible semantics, so Yii's FrankenPHP runner can be
copied with a name change.

Start path, in `nxt_php_start` (src/nxt_php_sapi.c:476):
1. Install a second request handler, `nxt_php_worker_park(req)`. It stores `req` in a static slot and returns.
2. Instead of `nxt_unit_run`, run the worker script once: the existing `nxt_php_execute` path with a NULL request.
   SG gets an empty request (no URI, method GET). `nxt_php_register_variables` (:1600) already returns early when
   `ctx->req == NULL`.
3. When the script returns (loop ended, or a fatal error), call `php_request_shutdown`. If the slot still holds a
   request, end it with `nxt_unit_request_done(req, NXT_UNIT_ERROR)`. Then `nxt_unit_done` and `exit(0)`.

Inside `unit_handle_request`:
1. `zend_unset_timeout()`. Loop on `nxt_unit_run_once(ctx)` until the slot is set or the context goes offline.
   Offline (QUIT from the router, or the `limits.requests` QUIT from src/nxt_unit.c:7983-7997) returns `false`.
2. Partial startup, as FrankenPHP does: `php_output_activate`, `PG(connection_status) = NORMAL`, fill
   `SG(request_info)` exactly as src/nxt_php_sapi.c:1301-1340 does (move that block into a helper),
   `sapi_activate`, start the ini output buffer (`output_buffering`), reset `$_GET`, `$_POST`, `$_COOKIE`,
   `$_FILES` and re-import `$_SERVER` through the auto-global callbacks (`nxt_php_server_ag` already exists, :182),
   `zend_set_timeout(max_execution_time)`.
3. `zend_call_function` on the callable. An uncaught exception is reported with `zend_exception_error`, and the
   request ends with 500 if no header was sent.
4. Partial shutdown: `php_output_end_all` (this calls `nxt_php_send_headers` and `nxt_php_unbuffered_write`), then
   `nxt_unit_request_done(req, NXT_UNIT_OK)` if `ctx->req` is still set (it is NULL after
   `fastcgi_finish_request`), then `php_output_deactivate`, `sapi_deactivate`, clear the slot and
   `SG(server_context)`. Return `true`.

Errors and recycling:
- A fatal error or `exit()` inside the callable bails out of `php_execute_script`. Step 3 of the start path ends the
  request with an error and the process exits. The router replaces it as it does after the `limits.requests` QUIT.
  Respawn is a fork of the prototype, so it is cheap.
- `limits.requests` works without change: libunit counts the dequeue and quits gracefully. The PHP side may also
  stop after N calls, as Yii's FrankenPHP runner does.
- `limits.timeout` (router side) and `max_execution_time` (re-armed per request) both still apply.
- Memory: user code must not keep request state. Add `gc_collect_cycles()` advice in the docs, as FrankenPHP does.

Router interaction: none needs to change. The worker is busy from the ACK (row 13) to the last message (row 24).
Work the script does between requests (GC, resets) happens while the router counts the worker idle. That is
correct: no request is lost, because the next one waits in the app queue. Requests that arrive while the worker
script is still booting also wait in the queue.

Size: src/nxt_php_sapi.c +250 to 350 lines (park handler, `unit_handle_request`, reset helpers, start path, error
path); config 3 files, about 20 lines; tests: test/php/worker/ scripts and test/test_php_worker.py, about 150 lines
(superglobals do not leak between requests, `header()` resets, exception gives 500, fatal error respawns,
`limits.requests` recycles, `fastcgi_finish_request` inside the loop, output buffering on and off). libunit: no change.

### The alternative: a libunit loop outside the php module

A `type: "external"` app could run `php worker.php` with a small extension that links libunit and exposes the same
loop. The CLI SAPI does not build HTTP superglobals, so the extension would pass a request array and FreeUnit would
lose `targets`, `options` and `fastcgi_finish_request`. This is what RoadRunner does, and RoadRunner is at 45k.
A pure C app on libunit would measure only the server, not Yii, so it is not a fair entry for this benchmark.
It is useful as a lab tool: it gives the floor of FreeUnit's own per-request cost (section 6, Q3).

## 4. Router and IPC changes, independent of PHP mode

Ranked by expected CPU saving per request. "us" is router plus app CPU, estimate. All help worker mode much more than
classic mode (section 2).

| # | change | where | mechanism | us per request | size | risk | test |
|---|---|---|---|---|---|---|---|
| 1 | K1: one wake per request | src/nxt_unit.c:5975-5990, src/nxt_router.c:8293-8299 | Today every idle worker polls the shared fd and wakes on each doorbell. Either the router rings the own port of one process from its idle list (src/nxt_router.c:7366), or libunit waits with epoll and EPOLLEXCLUSIVE. Also let the winner drain the doorbell datagram. | 25-75 at low and mid load (from 4.9 cs per request); near 0 when saturated | 80-150 lines | medium: a lost wake stalls a request until the next one | perf stat context-switches per request at c=1, 8, 32, 256 with 20 workers |
| 2 | K2: one message per small PHP response | src/nxt_php_sapi.c:1516, :1544, :1430; src/nxt_unit.c:3381-3399, :4387 | Do not send the header at once. Reserve body space in `nxt_unit_response_init`, add output with `nxt_unit_response_add_content`, and let `nxt_unit_request_done` send header, body and `last = 1` in one message (`nxt_unit_buf_send_done`). `flush()` and outputs over the reserve still send at once. | 8-15 (app: 2 sendmsg; router: 2 recvmsg, 1 EAGAIN, 1 epoll_ctl, often 1 epoll_wait); 1 writev to the client | 60-90 lines | low to medium: keep the 1.37.0 flush() and headers_sent() behaviour | strace -c per request; test_php_application.py flush tests |
| 3 | Content-Length when the whole response is in one message | src/nxt_h1proto.c:1644-1658, src/nxt_router.c:6164 | With K2 the first message is also the last. Set `content_length_n` from the body size instead of chunked. Skip when compression is on. | 1-3, and 12 to 20 bytes less on the wire; no separate tail write | 20-40 lines | low | curl -sI shows Content-Length; HEAD and 204 tests |
| 4 | ACK without a socket message | src/nxt_unit.c:1751, :1975, :7437-7449; src/nxt_router.c:6398 | Carry the ACK in the first response message, or allow it through the queue with an ordering rule. Body forwarding and cancel depend on it. | 3-6 | 50-100 lines | medium to high: message order (see the 1.37.0 order fix) | test_python_procman, test_asgi_websockets, request body tests |
| 5 | No epoll_ctl MOD on each port drain | src/nxt_port_socket.c:2251; src/nxt_epoll_engine.c:461-480 | Call `nxt_fd_event_enable_read` only when the read is disabled, as src/nxt_conn_read.c:121 does. Edge mode keeps the registration. | 1-3 | 3 lines | low | strace -c on the router; full suite |
| 6 | Early end of response before PHP shutdown | src/nxt_php_sapi.c:1377-1380 | Run shutdown functions and `php_output_end_all` first, then send the last message, then `php_request_shutdown`. | 0 CPU; latency by the destructor and free time (unmeasured) | 20-30 lines | medium: shutdown-function output must still reach the client | latency at c=1 with wrk |
| 7 | Request headers into a small buffer, not a 16 KiB chunk | src/nxt_router.c:8345, src/nxt_port_memory.c:574, :699 | A per-engine slab, or plain socket data under 1 KiB. Also drop the O(n^2) duplicate merge (src/nxt_router.c:8579). | 1-3, less mutex contention | 60-120 lines | medium | test_php_application.py header tests |
| 8 | Reuse the request pool | src/nxt_http_request.c:254, src/nxt_mp.c:722 | Cache one `nxt_mp_t` and `r` per engine, or raise the page size so `r` fits. | 1-2 | 40-60 lines | medium: lifetime bugs | ASan suite |
| 9 | No script fopen per request | src/nxt_php_sapi.c:1290 | Use a filename handle and let opcache hit without opening (unverified that opcache skips the open). Keep the 404 path. | 2-4 (openat, close, FILE) | 20 lines | low | strace; test_php_application.py 404 tests |
| 10 | Header buffer without malloc | src/nxt_unit.c:5076, :2899 | Keep one plain buffer per context. | under 0.5 | 15 lines | low | suite |
| 11 | One shm chunk per response, not per write | src/nxt_unit.c:3405-3420 | Keep the open chunk across `ub_write` calls. Matters for the 14 KiB DB response. | 2-5 for large responses | 40 lines | medium | large-body tests |
| 12 | Benchmark config: no share probe for `/` | freeunit.json, row 5 | Route `/` (or every non-asset URI) straight to the app. Config, not code. | 2-4 | config | none | strace shows no ENOENT openat |
| 13 | Debug-only pool junk fill | src/nxt_mp.c:144, :924, :945 | `memset(0x5A)` on every small `nxt_mp_free` in release builds. Same idea as 76c406b3. | under 0.5 | 4 lines | low | suite |

Checked and not worth a change: timers make no syscall per request (rbtree only); the clock is the vDSO after each
epoll_wait (src/nxt_epoll_engine.c:950); no cross-thread post on the hot path (src/nxt_router.c:7648 fires only when
`idle == spare`); the listen socket already uses EPOLLEXCLUSIVE; the access log is off in the benchmark (it is one
unbuffered `write()` per request when on, src/nxt_router_access_log.c:382); TCP_NODELAY is set once per connection
at the first keep-alive (src/nxt_h1proto.c:2409), which only affects the first response of a connection; the shm
poison fill is debug-only. The stream counter atomic on a shared page (src/nxt_port_rpc.c:184) bounces one cache
line between router threads; it is under 0.5 us.

## 5. Threads

- The PHP app has no `threads` or `thread_stack_size` option. Only python, perl, ruby and java list them
  (src/nxt_conf_validation.c:976, :1167, :1186, :1221; `nxt_php_app_conf_t` at src/nxt_application.h:63-65).
  The PHP module runs one libunit context per process (src/nxt_php_sapi.c:537).
- ZTS PHP builds are accepted: configure tests for ZTS (auto/modules/php:170-185) and `nxt_php_setup` starts TSRM
  (src/nxt_php_sapi.c:407-415). They still run one thread per process. The benchmark's FreeUnit is NTS.
- 20 workers as processes: each has its own heap. In worker mode each keeps its own Yii object graph (tens of MB
  each, unmeasured). opcache shm is shared, because module startup runs in the prototype before fork.
- Threads would need ZTS (slower Zend globals access, unmeasured here) and give no CPU gain for CPU-bound PHP.
  With libunit, each thread context also polls the shared fd, so it joins the herd (src/nxt_unit.c:5973-5984).
  FrankenPHP is ZTS and threaded and is at 67k; Rapira uses processes and is at 103k. Keep processes.

## 6. Open questions that need a measurement

Run on the reference bench host in the main session, one at a time. Use oha or wrk with HTTP/1.1,
never ab. App: clone github.com/yii3-benchmarks/app-api at 6e67cb52, `composer install --no-dev`, the same php.ini.
`R` is the router pid (`pgrep -f 'unit: router'`), `W` the worker pids (`pgrep -d, -f 'application'`).

| # | question | command |
|---|---|---|
| Q1 | How the 1.79 ms splits in the worker: Zend startup and shutdown, autoload, DI, Valkey | `perf record -F 999 -g -p $W -- sleep 20` during `wrk -t8 -c256 -d30s http://127.0.0.1:8080/`; `perf report --no-children --sort symbol` and sum `php_request_startup`, `php_request_shutdown`, `zend_deactivate`, `execute_ex` |
| Q2 | Syscalls per request, router and app | `perf trace -s -p $R -- sleep 10` and `perf trace -s -p $W -- sleep 10` under a fixed-rate load (`wrk2 -R5000`), divided by the request count |
| Q3 | Context switches per request vs concurrency (K1) | `perf stat -e context-switches,cpu-migrations -p $W -- sleep 10` at c=1, 8, 32, 256 with 20 workers; repeat with the C libunit example app as the floor |
| Q4 | Doorbells that find nothing (herd waste) | `bpftrace -e 'tracepoint:syscalls:sys_exit_recvmsg /args->ret == -11/ { @[pid] = count(); }'` during the load, divided by requests |
| Q5 | Yii handler cost in worker mode | run the benchmark's `frankenphp-worker` and `rapira` images on the reference bench host; CPU per request from `docker stats` at a fixed rate below saturation |
| Q6 | Valkey commands per classic request | `valkey-cli INFO commandstats` before and after 10,000 requests |
| Q7 | Does Yii send Content-Length; how many `ub_write` calls per response | `curl -sI http://127.0.0.1:8080/`; `strace -f -e trace=sendmsg -p $W` for 100 requests |
| Q8 | Router CPU per request at high rate (the worker-mode limit) | `pidstat -u -p $R 1` during `wrk2 -R50000` against a static hello.php, before and after items 2 and 5 of section 4 |
| Q9 | Host headroom with SMT: router plus 20 workers plus load generator | `mpstat -P ALL 1` during the Q8 run; look for busy sibling threads |
| Q10 | Latency won by item 6 | `wrk -t1 -c1 -d30s --latency` with and without the change |

## 7. Summary of decisions

- The gap is the classic bootstrap, about 85% of the CPU per request. Only a worker mode removes it.
- The smallest worker mode is a FrankenPHP-style loop in the php module: one long PHP request, a partial reset per
  request, `unit_handle_request(callable)`, no libunit change. About 300 lines plus tests.
- Router work does not limit classic mode. It limits worker mode near 100k rps on this host, so do K2 (with
  Content-Length) and the epoll fix first, then K1, then the ACK.
