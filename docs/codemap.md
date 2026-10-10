# Codemap

Where things live in `src/`, and how to find the file you actually want.

Citations verified against `freeunitorg/freeunit` `master` at `c9a7f48d`.

`architecture.md` explains the concepts and processes; this page is the file
inventory. For a single request end to end, read `http-flow.md`.

## The naming convention is the index

Almost every file is `src/nxt_<subsystem>_<thing>.c`, and almost every symbol
in it is `nxt_<subsystem>_<action>()`. That is more reliable than any directory
structure here, because `src/` is nearly flat — ~200 `.c` files at the top
level, with subdirectories only for language modules.

So the fastest lookup is usually a symbol grep, not a directory walk:

```bash
git grep -n 'nxt_http_request_create' -- 'src/*.c'   # definition + all callers
git grep -ln 'nxt_router_' -- 'src/*.c'              # everything router-ish
```

| Prefix | Subsystem |
|---|---|
| `nxt_main_process_`, `nxt_process_`, `nxt_runtime_` | process model, startup, the process/port registry |
| `nxt_port_` | the IPC layer between processes |
| `nxt_conn_`, `nxt_event_`, `nxt_epoll_`, `nxt_kqueue_` | connections and the event engines |
| `nxt_http_`, `nxt_h1proto_`, `nxt_websocket_` | the HTTP layer and protocols |
| `nxt_router_` | the router process: applying config, listeners, apps |
| `nxt_controller_`, `nxt_conf_` | the control API and the configuration model |
| `nxt_app_`, `nxt_unit_`, `nxt_<lang>_` | application side: libunit and language modules |
| `nxt_mp_`, `nxt_buf_`, `nxt_array_`, `nxt_list_`, `nxt_lvlhsh_`, `nxt_rbtree_` | memory pools and data structures |
| `nxt_openssl_`, `nxt_tls_`, `nxt_cert_` | TLS and certificate storage |
| `nxt_var_`, `nxt_tstr_`, `nxt_js_` | variables and templated strings |

## The large files, and what each one owns

Size is a decent proxy for "where the complexity is". Line counts at
`c9a7f48d`:

| File | Lines | Owns |
|---|---|---|
| `nxt_unit.c` | 7308 | **libunit — the app-side library**, not the server. See the trap below. |
| `nxt_router.c` | 6069 | the router process: config application, listener and app lifecycle |
| `nxt_conf_validation.c` | 4075 | the configuration schema — what the control API will accept |
| `nxt_h1proto.c` | 3297 | HTTP/1 protocol: request/response framing, keep-alive, upgrades |
| `nxt_controller.c` | 2801 | the control API process serving the REST config interface |
| `nxt_conf.c` | 2717 | the config value model plus JSON parse and serialise |
| `nxt_php_sapi.c` | 2679 | the PHP module's SAPI — see `php-lifecycle.md` |
| `nxt_http_route.c` | 2264 | routing: match conditions and actions |
| `nxt_runtime.c` | 1895 | runtime creation, inherited/systemd sockets, event engines |
| `nxt_main_process.c` | 1887 | the main process: forking and supervising every child |
| `nxt_openssl.c` | 1852 | TLS via OpenSSL |
| `nxt_port_socket.c` | 1500 | port message send/receive, including fd passing |
| `nxt_http_request.c` | 1371 | request lifecycle and state machine |
| `nxt_isolation.c` | 1367 | namespaces, `pivot_root`, cgroups |
| `nxt_http_static.c` | 1346 | the static file handler |
| `nxt_application.c` | 1330 | module discovery and application process setup |
| `nxt_http_parse.c` | 1281 | the HTTP parser |
| `nxt_mp.c` | 1109 | memory pools — the allocator behind almost every struct |

## Start here for a given question

| Question | Start at |
|---|---|
| how does a request become a response? | `nxt_h1proto.c`, then `http-flow.md` |
| why was my config rejected? | `nxt_conf_validation.c` |
| what happens when config is applied? | `nxt_router.c`, `nxt_router_conf_apply` |
| how does a process get spawned? | `nxt_main_process.c` |
| how does the app talk to the server? | `nxt_unit.c` plus `src/<lang>/` |
| where did this allocation come from? | `nxt_mp.c` — `nxt_mp_get`/`_zget`/`_nget`/`_alloc` differ in real ways |
| how are processes addressed? | `nxt_port_*.c`, and `architecture.md` |

## Subdirectories

`src/` is otherwise flat. The subdirectories are `java`, `nodejs`, `otel`,
`perl`, `python`, `ruby`, `wasm`, `wasm-wasi-component`, and `test` — the last
being the **C** unit tests (`./build/tests`), not the Python suite, which lives
in `test/` at the repository root. Two different test suites, similar paths;
`skills/freeunit-test/SKILL.md` covers running both.

## Navigation traps

**`src/nxt_unit.c` is not part of the server.** It is the largest file in the
tree and it is libunit — the library linked *into application processes*. A
symbol grep lands there constantly. If you are debugging the router and you
find yourself reading `nxt_unit_*`, you are on the app side of the port
boundary.

**`src/test/` and `test/` are different suites.** `src/test/` is the C runner;
`test/` at the root is pytest. A "test" file path alone does not tell you
which.

**Language modules are two pieces, not one.** `src/nxt_php_sapi.c` is the
module; `src/python/`, `src/ruby/` and friends hold the rest. The module is
built by a *separate* `./configure <lang>` step — see `skills/freeunit-build/SKILL.md`.

**A flat `src/` means the prefix carries the layering.** Nothing in the
filesystem stops `nxt_http_*` calling into `nxt_router_*`; the naming is the
only signal of which layer you are in. When adding a file, the prefix you pick
is a design statement, not a label.
