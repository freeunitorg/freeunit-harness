# PHP lifecycle in an app worker

How your PHP actually runs. References are to `src/nxt_php_sapi.c`.

Unit embeds PHP as a **SAPI**, the same way `php-fpm` or `mod_php` do. It is not
CGI: there is no process fork per request, and the interpreter stays resident.

## Startup, once per worker process

`nxt_php_start` (`:690`) runs when an application worker starts:

1. **Targets are built.** An app can declare several `targets`; each gets a
   `root`, `script` and `index` (`:706-731`, `:831`). Without `targets` there is
   a single implicit target from the app configuration.
2. **php.ini is located and options applied.** `options.file` sets the ini path
   (`nxt_php_set_ini_path`); `options.admin` and `options.user` are applied as
   `ZEND_INI_SYSTEM` and `ZEND_INI_USER` respectively (`:467-470`). The
   distinction is PHP's: `SYSTEM` entries cannot be overridden at runtime by the
   script, `USER` entries can.
3. **The interpreter starts.** `nxt_php_startup` (`:1699`) calls
   `php_module_startup()` — PHP's MINIT phase. Extensions initialize here, once,
   for the life of the worker.
4. **Unit's own extension registers.** `PHP_MINIT_FUNCTION(nxt_php_ext)`
   (`:214`) installs the functions Unit adds to the PHP environment.

Everything in this phase happens **once per worker process**, not once per
request. That includes anything driven by php.ini — opcache warming and
`opcache.preload` included. Unit does not implement preload itself; it applies
the ini settings and PHP does the rest, at module startup, per worker.

## Per request

`nxt_php_request_handler` (`:1424`) is invoked by `src/nxt_unit.c` when a
request arrives over the port:

1. The target is selected and the `nxt_unit_request_t` is translated into PHP's
   server context — `$_SERVER`, headers, the request body stream.
2. `php_request_startup()` (`:1643`) — PHP's RINIT. Superglobals are populated,
   per-request extension state initializes, the output buffer opens.
3. The script runs.
4. `php_request_shutdown(NULL)` (`:1676`) — RSHUTDOWN. Output is flushed,
   shutdown functions and destructors run, per-request memory is released.
5. The response goes back to the router over the port.

Shutdown at step 4 is what makes the resident interpreter safe to reuse: PHP's
per-request arena is reset even though the process lives on.

## The async path

There is a second handler, `nxt_php_request_handler_async` (`:2235`), selected
at start when a request callback is available (`:749`, `:758-769`). It drives the
request through a PHP callback (`nxt_php_request_callback`) inside a coroutine
scope rather than the straight-line startup/run/shutdown sequence. This is the
integration point for PHP's async work; if you are not deliberately using it,
the synchronous handler is what runs.

## Consequences

**State survives between requests, deliberately.** Statics, singletons, opcache
contents and extension globals persist for the life of the worker. That is where
the performance comes from, and it is also the classic source of
"works on the first request" bugs.

**A worker is spawned on demand.** If an app is configured but idle, the first
request may arrive before a worker exists — the router answers 503 in that
window. Under a slow instrumented build this is wide enough to hit routinely,
and it presents as a spurious failure. Warm the app with requests until you get
a 200 before asserting anything (see `skills/freeunit-debug/SKILL.md`).

**php.ini changes need a worker restart, not a config reload of the app's
routes.** They are read at module startup.

**A fatal error takes out the worker, not the server.** Unit restarts it. This
is the process model doing its job, but it means a crash loop looks like
intermittent 5xx rather than a hard down.

**`options.admin` versus `options.user` is a security boundary**, not a
stylistic choice: settings a script must not be able to relax belong in `admin`.
