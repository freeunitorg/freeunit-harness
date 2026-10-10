# Glossary

The vocabulary, in the sense the codebase uses it. Several of these words mean
something else in other servers, which is the point of writing them down.

**action** — a step in the request dispatch chain. A handler that either
completes the request, fails, or returns the next action to run. Configuration
verbs (`pass`, `return`, `proxy`, `share`) become actions.

**app / application** — a configured unit of user code, run by a language
module in one or more worker processes.

**buf (`nxt_buf_t`)** — a buffer descriptor. May reference memory, a file, or a
shared-memory chunk; the descriptor and the storage are separate concerns.

**controller** — the process serving the control API. Validates configuration
and hands it to the router. Does not touch client traffic.

**control socket** — the Unix socket (or inet listener) serving the
configuration API. `--control` at startup. Access to it is equivalent to
arbitrary code execution as the app user, so its permissions are a security
boundary.

**edge-triggered** — readiness delivered once per transition rather than
continuously while data remains. The default Linux engine (`epoll_edge`) is
edge-triggered; code that stops reading without re-arming stalls.

**engine (event engine)** — the readiness abstraction, a vtable in
`nxt_event_engine.h` implemented per platform (`epoll`, `kqueue`, `eventport`,
`devpoll`, `poll`, `select`). Each router thread runs one. The server logs which
one it chose: `using "<name>" event engine`.

**field (`nxt_http_field_t`)** — one parsed HTTP header. Carries `skip` and
`hopbyhop` flags that serializers honour; both must be initialized, which is why
`nxt_list_zero_add()` exists.

**isolation** — the namespace/cgroup/rootfs/capability configuration applied to
app processes.

**listener** — a configured address Unit accepts connections on, bound to a
`pass` target.

**main process** — the supervisor. Starts the others, retains privileges,
performs privileged operations on their behalf.

**mmap / shared memory** — the transport for request and response bodies between
router and app workers, in chunks, with `OOSM`/`SHM_ACK` as backpressure.
Distinct from the port, which carries the messages.

**module (language module)** — the shared object embedding a language runtime
(`php.unit.so`, `python3.unit.so`, …), loaded by an app worker. Built by
`./configure <lang> && make <lang>`; lands in `build/lib/unit/modules/`.

**pass** — the configuration key naming where a request goes next, and by
extension the act of handing a request to another action. Bounded at 255 hops
per request.

**port (`nxt_port_t`)** — the IPC channel between two processes: a socket pair
plus typed messages. Not a TCP port. Ports can be closed from a thread other
than the one whose engine registered them.

**prototype** — the process app workers are forked from.

**router** — the data plane. Owns listen sockets and client connections, parses
HTTP, routes, proxies, serves static files, dispatches to apps. Multi-threaded.

**route** — an ordered list of match/action rules.

**share** — the configuration verb for serving files from disk.

**state directory** — where Unit persists configuration and certificates
(`--statedir`).

**target** — a named entry point within one application (its own `root`,
`script`, `index`). One app can expose several.

**task (`nxt_task_t`)** — the context object threaded through handlers, carrying
the log context and thread association. Which task a work item captures is
significant for lifetime: a work item that outlives the task it borrowed is a
use-after-free.

**unit-side vs app-side** — `src/nxt_unit.c` is the library linked into
application workers (the app side of the port protocol); `src/nxt_router.c` is
its counterpart in the router.

**worker** — one application process. Spawned on demand, restarted after a
fatal error, holds resident interpreter state between requests.
