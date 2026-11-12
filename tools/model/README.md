# tools/model: a behavioural model of the server

A Python model of the FreeUnit data path: router engines, the port protocol
over Unix datagram sockets, the shared-memory app queue and chunk allocator,
and application workers running libunit. It has two parts.

- **Model A** (`engine/`): a deterministic discrete-event simulation. It
  replays requests through the modelled components and records a trace.
- **Model B** (`analyzer/`): adversarial exploration on top of Model A. It
  interleaves the two message channels (app queue and socket), injects faults
  (worker SIGKILL mid-transfer, shared-memory exhaustion, client RST, process
  churn) and checks six invariants: stream FIFO order, refcount safety on
  worker teardown, no shared-memory leak, progress under backpressure,
  connection lifecycle, and framework state isolation.

The model is not the server. A violation it reports is a claim about the C
code, to be traced in `src/` and reproduced with a test before anyone acts on
it. The constants it uses (16 KiB chunks, 10 MiB segments, 131,072 queue
entries, 31-byte queue payloads) mirror `src/nxt_port_memory.c` and
`src/nxt_app_queue.h`. The `schema` command compares the ctypes layouts with
the headers extracted by tree-sitter.

Longer docs are under [docs/model/](../../docs/model/).

## Files

| Path | What |
|---|---|
| `engine/` | event loop, listener, router, RPC streams, app queue, socket pair, shared-memory pool, worker, schedules, protocol FSMs |
| `analyzer/` | interleaver, fault injector, invariant checker, state-space explorer |
| `conformance/` | wire comparator and tandem runs |
| `replay/` | telemetry ingest and replay |
| `verify/` | optional Hypothesis state machine |
| `synthesis/` | modelled doorbell-count and lock-free SHM experiments |
| `c_types.py` | ctypes mirrors of `nxt_port_msg_t`, `nxt_port_mmap_msg_t`, the app queue and the libunit request and response headers |
| `ast_extractor.py` | tree-sitter parser for the C headers: enums, defines, struct layouts, x86_64 offsets |
| `cli.py` | the commands below |
| `tests/` | pytest tests for the model |
| `tui/` | a Textual terminal UI for `cli.py`; start it with `tools/model/tui/model-tui` (see `tui/README.md`) |

## Commands

Run from the harness root. The model and the tests need only the standard
library and pytest. Hypothesis is optional (stateful tests skip without it).
`schema` also needs tree-sitter.

```sh
python3 -m tools.model.cli simulate --requests 50 --workers 2
python3 -m tools.model.cli verify --chaos --seed 1
python3 -m tools.model.cli benchmark-optimizations
python3 -m tools.model.cli replay
python3 -m tools.model.cli conformance
python3 -m pytest -q tools/model/tests
```

`schema` looks for a FreeUnit tree in this order: `$UNIT_SRC` if set, then
`<harness>/../unit`, `<harness>/../freeunit`, `<harness>/src`, `<cwd>/src`,
and `<cwd>/../unit`. The first path that contains `src/nxt_main.c` (or
`nxt_main.c` if the candidate is already a `src` directory) is used.

```sh
uv run --with tree-sitter --with tree-sitter-c python3 -m tools.model.cli schema --src ./src
```

`schema` compares each ctypes mirror with the extracted struct size, field
offsets, and field sizes. It does not run a C compiler. A difference exits 1.
Signedness, bit widths inside one byte and the element type of a trailing `[]`
are not compared.

`conformance` without `--url` is a self-check against the model's default
handler. It does not compare a live server.

## Status

Checked on 2026-11-11. `python3 -m tools.model.cli verify --chaos --seed 1`
exits 1 and reports two invariant violations: INV-4-PROGRESS (dangling
streams) and INV-3-NO-LEAK (five unfreed chunks). The same two appear with
`--seed 42`. Whether they are server defects or model gaps has not been
traced. Treat the output as leads, not findings.

Known differences from the server, left for a later change:

- The server always places `REQ_HEADERS` on the shared queue as a 28-byte
  item (`nxt_port_msg_t` 16 + `nxt_port_mmap_msg_t` 12) with the headers in
  shared memory. A failed queue send is HTTP 500. The model sends a 16-byte
  item and falls back to the socket, which the server does not do.
- Queue and socket order is kept by the `READ_SOCKET` marker in the queue.
  The model does not model that marker.
- A worker takes a queue item with `nxt_app_queue_recv_claim()`, and the
  router uses `nxt_app_queue_cancel()` if the worker dies after the claim.
  That window is https://github.com/freeunitorg/freeunit/issues/665. The
  model has no claim or cancel step, so it cannot show a request lost
  between dequeue and answer.
- The wake message is `READ_QUEUE`. Chunks are
  freed by the shared free map. `SHM_ACK` is sent only when chunks were
  freed while the header `oosm` flag was set.
- `nxt_app_queue_t` has no `nitems`. Notification uses
  `nxt_app_queue_notify_decision(pos, depth_bound)`. The one-wake flag and
  `depth_bound` are already in FreeUnit 1.37.2, so "every enqueue posts a
  doorbell" is not the baseline there.
