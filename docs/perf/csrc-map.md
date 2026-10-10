# C-source map of router, libunit and port IPC

A "C-source map" is one text file that holds the router, libunit and port IPC
code in a form a long-context model can load whole. The model can then reason
about data structures and request flow across the three subsystems at once.

Built and measured at `origin/master` `47d2f933` (2026-10-06).
Generator: `tools/build-pack.py`. Existing prose maps it does not replace:
`docs/codemap.md` here, and `docs/internals/{http-router,libunit,port-ipc,memory-and-types}.md`
in the unit checkout.

## The set

Seven `.c` files: `nxt_router.c`, `nxt_unit.c`, `nxt_port.c`, `nxt_port_socket.c`,
`nxt_port_memory.c`, `nxt_port_rpc.c`, `nxt_port_hash.c`. Seventeen headers:
`nxt_port*.h`, `nxt_app_queue.h`, `nxt_application.h`, `nxt_router.h`,
`nxt_router_request.h`, `nxt_unit*.h` (`FILES` in the script has the list).

`--extra src/nxt_application.c` adds the prototype's port handlers.

## Measurements

Token counts in this page come in two kinds. "Measured" counts are what the
Read tool reported to a Sonnet agent for the pack file. "~" counts are
characters / 2.2, the ratio those measurements gave (L0 1.99, L1 2.24,
L2 2.25 characters per token). The common rule of 3.3 characters per token
under-counts this code by about a third. Removing alignment whitespace saves
only 3% of L0's tokens, so the cost is in the code, not in the spaces.

NCLOC counts lines with any non-comment code. "debug" counts lines of
debug-level log statements (`nxt_debug`, `nxt_unit_debug`,
`nxt_unit_req_debug`, `nxt_thread_log_debug`). "tests" counts lines inside
`#if (NXT_TESTS ...)` blocks, directives included.

| file | lines | NCLOC | comment | blank | ~tokens | debug | assert | license | tests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nxt_router.c | 9,284 | 5,607 | 1,533 | 2,144 | 130,363 | 157 | 49 | 6 | 140 |
| nxt_unit.c | 8,712 | 5,767 | 651 | 2,294 | 110,636 | 177 | 0 | 4 | 246 |
| nxt_port_socket.c | 2,938 | 1,586 | 706 | 646 | 42,535 | 37 | 4 | 5 | 60 |
| nxt_port.c | 1,572 | 685 | 604 | 283 | 25,813 | 22 | 10 | 5 | 3 |
| nxt_port_memory.c | 1,172 | 712 | 155 | 305 | 14,724 | 30 | 1 | 5 | 22 |
| nxt_port_rpc.c | 678 | 445 | 45 | 188 | 7,538 | 23 | 9 | 5 | 70 |
| nxt_port_hash.c | 142 | 98 | 5 | 39 | 1,348 | 2 | 0 | 5 | 0 |
| 17 headers | 2,994 | 1,490 | 813 | 691 | 45,052 | 0 | 0 | 76 | 117 |
| **total** | **27,492** | **16,390** | **4,512** | **6,590** | **378,009** | **448** | **73** | **111** | **658** |

The brief's line counts were stale. `nxt_router.c` has 9,284 lines, not
7,706. `nxt_unit.c` has 8,712, not 7,436. The five port `.c` files have
6,502, not about 5,500. The core is 16,390 NCLOC, not about 10k.
`nxt_thread_log_*` appears on only 9 lines.

So the strippable noise is small. Debug lines, asserts, license headers and
test hooks together are 1,290 lines, 4.7% of the set. Comments are 16% of
the lines. A full pack cannot get much smaller than the source.

## Pack levels

Each pack is one file. Each source file starts with a banner:
`=== FILE src/nxt_port.c (origin/master 47d2f933) ===`.

Every level drops the license comment, debug-level log statements that sit on
lines of their own, and runs of blank lines (kept as one).

Line numbers: `//@N` at a line end is original line N. A marker sits on every
function's name line, on the first line after any dropped line, and at least
every 10 lines. On a macro continuation line it is `/*@N*/ \` instead, so the
splice survives. A folded range reads `//@A-B`. `verify-markers.py` in the
scratch directory checks that every emitted line equals the source line its marker
implies: 0 mismatches over 37,350 lines in the three packs.

| level | content | lines | chars | measured tokens |
|---|---|---:|---:|---:|
| L0 skeleton | every type, macro, global and table in full; each body is one stub line with its line range and its calls into the set | 5,336 | 201,539 | 101,234 |
| L1 hot path | L0 plus full bodies of 265 request-path functions (12,816 source lines) | 17,583 | 572,937 | 256,290 |
| L2 full | all 481 functions whole | 26,159 | 833,637 | 371,091 |

A body stub reads `{ ... }  //@A-B calls: f, g`. The call list holds only
functions defined in the set, in order of first call. That is the call graph.

L0 and L1 also:

- drop forward declarations of functions defined later in the same file;
- fold each `#if (NXT_TESTS)` block to one line with its range (no such block
  has an `#else`; one that has would be kept whole);
- cut the comment above an elided function to its first sentence, marked
  `[...]`. L0 cuts every top-level comment of four or more lines in a `.c`
  file the same way. Header comments stay whole: they document the types.

L1 selects bodies by name (`HOT_PATTERNS` in the script): `nxt_router_app_*`,
`nxt_router_prepare_msg`, `nxt_router_response_*`, `nxt_router_http_request_*`,
`nxt_port_socket_write*`, `nxt_port_write_*`, `nxt_port_read_*`,
`nxt_port_*_handler`, `nxt_port_queue_*`, `nxt_port_mmap_*`, `nxt_app_queue_*`,
`nxt_unit_process_*`, `nxt_unit_request_*`, `nxt_unit_response_*`,
`nxt_unit_run*`, `nxt_unit_port_*`, `nxt_unit_read_buf*`, `nxt_unit_send_*`,
`nxt_unit_mmap_*`, `nxt_unit_buf_*` and a few single names. It adds each direct
callee in the same file of at most 80 lines. libunit is almost all hot: 124 of
182 functions.

The brief's targets (L0 under 60k, L1 under 200k) assumed 3.3 characters per
token. Measured, L0 is 101k and L1 is 256k. Cutting every comment from L0
brings it only to 82k, and the next cut would be the call lists, which are
the point of L0. So the targets are not met, and I kept the content. All
three levels fit a 1M window.

Packs at `47d2f933`, built in the session scratch directory (`csrc-map/`):

| file | size |
|---|---:|
| `pack-L0-47d2f933.txt` | 197 KiB |
| `pack-L1-47d2f933.txt` | 560 KiB |
| `pack-L2-47d2f933.txt` | 814 KiB |
| `pack-L1x-47d2f933.txt` (L1 with `--extra src/nxt_application.c`) | 569 KiB |
| `index-47d2f933.tsv` (`--index` output) | 44 KiB |
| `verify-markers.py` (marker check against `src/` extracted at 47d2f933) | 1 KiB |

The scratch directory is session storage: rebuild rather than rely on it.

## How to rebuild

```sh
uv run --with tree-sitter --with tree-sitter-c \
    tools/build-pack.py --repo "$UNIT_SRC" --rev origin/master --level 1 > pack-L1.txt
uv run --with tree-sitter --with tree-sitter-c \
    tools/build-pack.py --repo "$UNIT_SRC" --rev origin/master --stats
```

`--rev` takes any commit. Sources come from `git show REV:path`, so the
working tree is never read or changed. `--index` prints one line per function:
file:line, name, length, hot or not, calls. `--extra PATH` adds a file.
Two runs at the same revision give byte-identical packs.

Parsing uses tree-sitter-c with the length-preserving macro shim from `fidx`
(`tools/fidx/shim.h` on the `proto/factory` branch of the maintainer's fork),
plus `nxt_packed`, which broke two structs in `nxt_unit.c`. The script asserts
coverage: every column-0 `name(` line followed by a `{` must be a parsed
function, else it exits non-zero. All 481 definitions parse at `47d2f933`.

## Validation with Sonnet

One Sonnet agent got the L1 pack and four questions. It could use only the
Read tool, only on that file. It read all 17,583 lines in 14 chunks of 1,300
lines (a 1,500-line chunk hit the Read tool's 25k-token cap). It reported no
truncation and no compaction, so its window held more than 256k tokens. The
harness counted 386,123 subagent tokens over 16 tool calls in 252 seconds.
L1 was enough. L2 was not needed.

**(a) Can a prototype's write to the router port return NXT_AGAIN?**
Grade: correct.

- Code: `nxt_port_socket_write2()` returns NXT_AGAIN only on the shared-queue
  branch, which needs `port->queue != NULL` (src/nxt_port_socket.c:347). It
  happens when the ring is full for the payload with no wake-up owed, or for
  the 1-byte READ_SOCKET marker (:415). Every queueless exit gives NXT_OK or
  NXT_ERROR: `nxt_port_msg_chk_insert()` (:553) and `nxt_port_write_msgs()`.
- The prototype writes to `rt->port_by_type[NXT_PROCESS_ROUTER]`
  (src/nxt_application.c:1779), the router's main port. That port has no
  shared queue (src/nxt_router.c:473). Also, `nxt_app_new_port_handler()`
  (src/nxt_application.c:185) closes every queue descriptor it receives, so
  no port in a prototype has a queue.
- The brief's reason, "the router consumes directly", is not what the code
  says. The reason is that the port has no queue.
- Sonnet named both NXT_AGAIN exits and every other exit correctly. It cited
  src/nxt_router.c:473 for the main port. It said, correctly, that the map
  does not hold `nxt_application.c`, so it could not show the prototype side.
  `--extra src/nxt_application.c` closes that gap.

**(b) What does the port error handler tear down?** Grade: correct and complete.

- `nxt_port_error_handler()` (src/nxt_port_socket.c:2880) drains
  `port->messages` under `write_mutex`. For each message it uncounts the
  descriptor bound, closes owned descriptors (`close_fd`), queues the
  completion of each non-sync buffer, and frees the message. It drops one
  port reference per message, plus the one the failing writer took.
- It closes no socket, disables no event, leaves `port->queue`, and does not
  remove the port from `app->port_hash`. The comment at src/nxt_port_socket.c:1117
  says it "closes nothing". Sonnet listed all of this, with the hash removal
  sites in src/nxt_router.c.

**(c) Marker ordering and the 1.37.0 fix.** Grade: correct.

- Sonnet named the writers and readers of both markers, the reader's FIFO
  drain with `from_socket`, the one-slot parking of an early datagram, and
  the guarantee in the comment at src/nxt_unit.c:7438: a datagram can be read
  before a queue message its sender sent earlier, never after one it sent
  later.
- It named the rule and its function: `nxt_unit_port_send()`
  (src/nxt_unit.c:7423) queues a message only if the port has a queue, there
  is no out-of-band data, the size is 16 to 31 bytes, and
  `pm->stream == 0 || pm->last`.
- The map holds no history, so it could not name the commit. I checked it:
  66048b7b "libunit: keep the messages of a request in order on a queue port"
  added exactly that condition. The headers ack, response header and body
  parts go to the socket; only the last message and stream-0 messages use the
  queue. The router-to-application direction is unchanged. The bug dates from
  1.19.0. Its test load gave 662 503s in 300,000 requests before and 0 after.

**(d) Two data-structure optimizations.**

1. Per-request libunit objects and locks. Grade: B-.
   - Evidence is right: `ctx_impl->mutex` is taken in
     `nxt_unit_request_info_get()`, `nxt_unit_read_buf_get()` and
     `nxt_unit_mmap_buf_get()` and their releases; `mmaps->mutex` in
     `nxt_unit_mmap_read()`; a `malloc()` of the plain response buffer in
     `nxt_unit_get_outgoing_buf()` (src/nxt_unit.c:5076) when no local buffer
     is given.
   - The proposal is to embed the mmap bufs in the request slab and take
     them with one free-list pop. It estimates 100 to 150 ns per request
     against about three `sendmsg()` calls.
   - Weak points: these objects are already recycled through free lists, and
     the context mutex is uncontended when one thread owns the context. The
     gain is under 5% of the IPC cost of a request. Two cited lines were off
     by about 70 (5148 for 5076, 5534 for 5502).
2. Response-port lookup. Grade: C+.
   - The libunit half is real: `nxt_unit_request_check_response_port()`
     (src/nxt_unit.c:1851) takes `lib->mutex`, which all contexts share, and
     does an lvlhsh find per request. A per-context cache would help
     multi-threaded applications.
   - The router half is unsafe. It moves `nxt_port_hash_find(&app->port_hash)`
     (src/nxt_router.c:6429) before `app->mutex`. But `app->port_hash` is
     changed under that mutex (src/nxt_router.c:7228, 7710, 7873), so the find
     would race with a removal.
   - The effect is estimated, not measured.

Lines with a marker were cited exactly; counted lines were mostly within a few.

## Which level for which question

| question | level | why |
|---|---|---|
| struct layout, field meaning, message types, who calls what | L0 | types and the call graph in 101k tokens; a cheap cached prefix |
| request path, ordering, ownership, port IPC behaviour | L1 | answered (a) to (c) correctly at 256k |
| config apply, start and stop, error paths, anything one helper deep | L2 | L1 elides those bodies; L2 fits a 1M window at 371k |
| prototype or main-process side of a port | L1 or L2 with `--extra src/nxt_application.c` | not in the default set |
| why the code is this way | none | packs hold no history; use `git log` |

With a 1M window, L2 is the safe default. L1 saves 115k tokens and gave the
same answers here. Use L1 when the window is shared with other material.

## What I would change next

- Add `src/nxt_application.c` to the default set. Question (a) needed it.
- Put markers every 5 lines in L1 and L2. It costs about 3% and removes the
  counting errors.
- Add an optional history section: `git log --format='%h %s'` for the set
  since the last release, about 2k tokens. Question (c) asked for a commit.
- Do not chase the 60k L0 target. The tokens are in the code.
