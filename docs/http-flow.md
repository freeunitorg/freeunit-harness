# A request, end to end

From the listen socket to the last byte written. Symbols are given so you can
follow along in the source.

## 1. Accept

A configured listener installs `nxt_http_conn_init` as its connection handler
(`src/nxt_router.c`, where `skcf->listen->handler` is set). That is the entry
point for a newly accepted client connection, in a router thread, on that
thread's event engine.

## 2. Protocol setup

`nxt_http_conn_init` (`src/nxt_h1proto.c:218`) prepares the connection and hands
off to `nxt_h1p_conn_proto_init`, which initializes the HTTP/1 protocol state
and calls `nxt_h1p_conn_request_init`. For a TLS listener the handshake happens
first — protocol init is what runs once the connection can carry HTTP.

`nxt_h1p_conn_request_init` is also the re-entry point for the *next* request on
a keep-alive connection (`src/nxt_h1proto.c:2029`). Anything that must not leak
between requests on one connection has to be reset here or on the request object
it creates — this is the seam where per-request state and per-connection state
are easiest to confuse, and where a bug shows up only on the second request.

## 3. Parse

The request line and headers are parsed by `src/nxt_http_parse.c` into an
`nxt_http_request_t` (created via `nxt_http_request_create`,
`src/nxt_http_request.c:245`). Header fields land in a list of
`nxt_http_field_t`.

Well-known headers are recognized by table-driven callbacks rather than
re-scanned later — `nxt_http_request_host`, `nxt_http_request_content_length`
and friends run as fields are parsed.

> Field structures come from pool memory that is **not** zeroed by default.
> `nxt_list_add()` returns uninitialized memory, and the field setters do not
> clear `skip`/`hopbyhop`, which serializers test. Use `nxt_list_zero_add()`.
> The failure mode is a header that intermittently disappears depending on
> allocation history — see `skills/freeunit-test/references/failure-shapes.md`.

## 4. Ready

`nxt_http_request_ready` (`:584`) marks the point where a complete request head
exists and routing can begin. If the request has a body, body handling is set up
around here (`nxt_http_request_read_body`, `:673`); chunked bodies get
`nxt_http_request_chunked_transform` (`:550`).

## 5. Route and act

`nxt_http_request_action` (`:608`) is the dispatch loop, and it is worth reading
in full because it is short and it explains a lot:

```c
do {
    ret = nxt_http_rewrite(task, r);
    if (ret != NXT_OK) break;

    action = action->handler(task, r, action);

    if (action == NULL)              return;   /* handler owns the request now */
    if (action == NXT_HTTP_ACTION_ERROR) break;

} while (r->pass_count++ < 255);
```

Actions form a **chain**, not a single decision. A handler either finishes the
request (returns `NULL`), fails (`NXT_HTTP_ACTION_ERROR`), or returns the next
action to run — which is how `pass` targets can hop between routes. The
`pass_count < 255` bound is loop protection: a configuration that passes in a
cycle terminates with a 500 rather than spinning. Falling out of the loop for
any reason lands on `nxt_http_request_error(..., NXT_HTTP_INTERNAL_SERVER_ERROR)`.

The action kinds correspond to what configuration can express
(`src/nxt_http_route.c`): `pass`, `return`, `proxy`, `share`. Route matching
itself is `nxt_http_route_handler` / `nxt_http_route_match`; a `pass` whose value
contains a variable resolves through `nxt_http_pass_var`.

## 6a. To an application

`nxt_http_application_handler` (`src/nxt_http_request.c:639`) fills in protocol
info, sets `server_name` from the `Host` header (defaulting to `localhost` when
absent), and calls `nxt_router_process_http_request`. It returns `NULL` — the
router owns the request from here.

The router serializes the request to the app worker over a port as
`REQ_HEADERS`, with the body following in shared memory
(`REQ_BODY`). On the worker side `src/nxt_unit.c` receives it —
`nxt_unit_process_req_headers` (`:1382`) — reconstructs a request object, and
invokes the language module's handler. `REQ_HEADERS_ACK` (`:1803`) is the flow
control signal back to the router.

The application's response travels back the same way and is written to the
client by the router.

## 6b. To a proxy, a file, or a fixed response

`proxy` (`src/nxt_http_proxy.c`) opens an upstream connection and relays;
`share` (`src/nxt_http_static.c`) serves from the filesystem; `return` answers
immediately with a status. All three are actions in the same chain, and all
three end at the same response path.

## 7. Respond

`nxt_http_request_header_send` (`:682`) serializes the status line and headers;
`nxt_http_request_send` (`:778`) writes body buffers. The serializers skip
fields flagged `skip` (and, for peer/proxy paths, `hopbyhop`).

## 8. Finish

The request is closed and freed. On a keep-alive connection the flow returns to
step 2's `nxt_h1p_conn_request_init` for the next request; otherwise the
connection is closed.

## Things that surprise people

**A request can outlive the function that started it.** Handlers return `NULL`
to mean "I have taken ownership and will complete this asynchronously". Reading
the dispatch loop as though it were synchronous will mislead you.

**Errors are a path, not an exception.** `NXT_HTTP_ACTION_ERROR` unwinds to a
single error entry point; there is no unwinding of partial work for free.

**Keep-alive is where per-request state bugs live.** A single-request test will
not find them. If you touch anything reset in `nxt_h1p_conn_request_init`, test
two requests on one connection.

**The parse path is reachable by anyone who can open a socket.** Length
arithmetic, terminator budgeting and CR/LF/NUL handling there are security
surface, not style questions. See `skills/freeunit-security/SKILL.md`.
