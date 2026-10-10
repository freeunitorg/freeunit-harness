# Architecture

What the processes are, how they talk, and where your change is going to run.

Everything here is stated against the source tree; file and symbol references
are given so you can check rather than trust. If something below disagrees with
the code, the code is right — please fix this page.

## Process model

Unit is not one process. `src/nxt_process_type.h` enumerates the kinds:

| Type | Role |
|---|---|
| `NXT_PROCESS_MAIN` | supervisor: starts everything else, owns privileged operations |
| `NXT_PROCESS_DISCOVERY` | transient; probes available language modules at startup |
| `NXT_PROCESS_CONTROLLER` | serves the control API, holds and validates configuration |
| `NXT_PROCESS_ROUTER` | the data plane: listens, parses HTTP, routes, proxies, serves files |
| `NXT_PROCESS_PROTOTYPE` | template process from which app workers are forked |
| `NXT_PROCESS_APP` | an application worker running your code via a language module |

A running server shows up as:

```
unit: main v1.36.0 [...]
unit: controller
unit: router
```

plus one process per application worker once an app is configured.

This split is the reason for most of the codebase's shape. The main process is
the only one that keeps privileges; the router is the only one that touches
client sockets; application code never shares an address space with either.

**Consequences worth internalizing:**

- A crash in an app worker does not take down the server; a crash in the router
  drops every in-flight connection on that thread.
- Configuration does not arrive by function call. The controller validates it
  and hands it to the router over IPC.
- Killing "the server" means killing a process *tree*. The main process
  re-parents nothing on its way out — if you SIGKILL only the main pid, the
  router and workers keep running and keep holding the listen sockets. This is
  why every tool in this repository operates on process groups.

## Ports: the IPC layer

Processes communicate over **ports** — `nxt_port_t`, backed by socket pairs,
with a typed message on top. The message types are enumerated in
`src/nxt_port.h`; the shape of the system is visible directly in that list:

- process lifecycle — `PROCESS_READY`, `PROCESS_CREATED`, `WHOAMI`,
  `REMOVE_PID`, `QUIT`, `START_PROCESS`
- port plumbing — `NEW_PORT`, `GET_PORT`, `PORT_ACK`
- shared memory — `MMAP`, `GET_MMAP`, `OOSM` (out of shared memory), `SHM_ACK`
- configuration and assets — `CONF_STORE`, `MODULES`, `CERT_GET`,
  `SCRIPT_GET`, `ACCESS_LOG`
- the request path — `REQ_HEADERS`, `REQ_HEADERS_ACK`, `REQ_BODY`, `DATA`,
  `WEBSOCKET`

Flags ride in the high bits (`NXT_PORT_MSG_LAST`, `NXT_PORT_MSG_CLOSE_FD`,
`NXT_PORT_MSG_SYNC`) with `NXT_PORT_MSG_MASK` selecting the type.

Which processes may talk to which is not implicit: `nxt_process.h` declares
`nxt_proc_keep_matrix`, `nxt_proc_send_matrix` and
`nxt_proc_remove_notify_matrix`, indexed by process type. If you are adding a
message between two process kinds, those matrices are part of the change.

**Ports close cross-thread.** That fact is load-bearing for anything touching
port teardown or event registration: a port belonging to one thread's engine can
be closed from another, so cleanup that needs to reach the event engine cannot
simply call it inline.

## Shared memory

Request and response bodies do not travel through the socket pair. They go
through shared memory segments (`src/nxt_port_memory.c`,
`src/nxt_port_memory.h`) allocated in chunks, with the port message carrying a
reference. `nxt_port_mmap_get_buf` / `nxt_port_mmap_write` /
`nxt_port_mmap_read` are the entry points, and `OOSM`/`SHM_ACK` are the
backpressure signals for when a peer has run out.

This is why bounds arithmetic in that layer matters more than in most places: an
offset is validated against a mapped segment shared with a less-trusted process.

## Threads and event engines

The router runs multiple threads, each with its own **event engine** — an
abstraction over the platform's readiness mechanism. `src/nxt_service.c`
registers the available implementations:

| Platform | Engines |
|---|---|
| Linux | `epoll` (= `epoll_edge`), `epoll_edge`, `epoll_level` |
| BSD/macOS | `kqueue` |
| Solaris/illumos | `eventport` |
| AIX | `pollset` |
| others | `devpoll`, `poll`, `select` |

The engine is a vtable (`nxt_event_engine.h`): `create`, `free`, `enable`,
`disable`, `delete`, `close`, `enable_read`, `enable_write`, and so on. Anything
implementing that contract can be an engine, which is how an alternative
readiness backend can be added without touching call sites.

The server announces its choice at startup:

```
using "epoll_edge" event engine
```

Grep for that line when you need to know what a build is actually running —
guessing from configure flags is how A/B benchmarks end up comparing a build
against itself.

**Edge versus level matters.** The default Linux engine is edge-triggered, which
means a readiness notification is delivered once per transition, not repeatedly
while data remains. Code that stops reading early without re-arming will hang
rather than fail loudly.

## Where to put a change

| You are changing | Look at |
|---|---|
| HTTP parsing | `src/nxt_http_parse.c`, `src/nxt_h1proto.c` |
| routing / matching | `src/nxt_http_route.c` |
| static file serving | `src/nxt_http_static.c` |
| proxying | `src/nxt_http_proxy.c` |
| the control API | `src/nxt_controller.c` |
| app dispatch | `src/nxt_router.c`, `src/nxt_unit.c` |
| a language module | `src/nxt_php_sapi.c` and siblings |
| IPC / shared memory | `src/nxt_port*.c` |
| readiness / event loop | `src/nxt_event_engine.c`, `src/nxt_*_engine.c` |
| build system | `configure`, `auto/` |

## Further reading

- [http-flow.md](http-flow.md) — a request from accept to response
- [php-lifecycle.md](php-lifecycle.md) — how an app worker runs your code
- [glossary.md](glossary.md) — the vocabulary
