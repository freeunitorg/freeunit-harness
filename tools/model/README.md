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
`src/nxt_app_queue.h`; `schema` below checks the struct layouts against the
headers.

## Files

| Path | What |
|---|---|
| `engine/` | event loop, listener and connection state machine, router, RPC streams, app queue, socket pair, shared-memory pool, worker, schedules |
| `analyzer/` | interleaver, fault injector, invariant checker, state-space explorer |
| `c_types.py` | byte-exact `ctypes` mirrors of `nxt_port_msg_t`, `nxt_port_mmap_msg_t`, the app queue and the libunit request and response headers |
| `ast_extractor.py` | tree-sitter parser for the C headers: enums, defines, struct layouts, x86_64 offsets |
| `cli.py` | the commands below |
| `tests/` | nine pytest tests: abrupt worker death, dual-channel inversion, framework lifecycles, schedules, shared-memory exhaustion |

## Commands

Run from the harness root. The model and the tests need only the standard
library and pytest.

```sh
python3 -m tools.model.cli simulate --requests 50 --workers 2
python3 -m tools.model.cli verify --chaos --seed 1
python3 -m tools.model.cli benchmark-optimizations
python3 -m pytest -q tools/model/tests
```

`schema` needs the `tree_sitter` and `tree_sitter_c` packages and a server
checkout. It reads `$UNIT_SRC`, else `<harness>/src`:

```sh
uv run --with tree-sitter --with tree-sitter-c python3 -m tools.model.cli schema --src ./src
```

## Status

First cut, 2026-10-10. `verify --chaos --seed 1` reports two violations
(dangling streams and five unfreed chunks). Whether they are server defects or
model gaps has not been traced. Treat the output as leads, not findings.
