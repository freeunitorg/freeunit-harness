# Micro-Optimizations: K1 Doorbell & K2 Response Framing

`tools/model` can count modelled doorbells for two notify rules. Those counts are not context switches, syscalls, or CPU from a server run.

---

## K1: Single-Wake Doorbell (Herd Elimination)

### What the server does
`nxt_app_queue_t` has no `nitems` field. Notification uses
`nxt_app_queue_notify_decision(pos, depth_bound)` and a `READ_QUEUE` port
message. The one-wake flag and `depth_bound` are already in FreeUnit
1.37.2, so "every enqueue posts a doorbell" is not the baseline there.

### What the command counts
`python3 -m tools.model.cli benchmark-optimizations` counts modelled
notifications in two scenarios:

1. An artificial always-notify case, by clearing `notified` before every send.
2. The one-wake flag left set, so later enqueues in the same burst do not notify.

```mermaid
flowchart TD
    Enqueue["Router enqueues item into nxt_app_queue_t"]
    AtomicCheck{"Queue was empty (one-wake flag 0 -> 1)?"}
    SendDoorbell["Send READ_QUEUE port message"]
    SkipDoorbell["Skip doorbell (Worker already active)"]
    WorkerAwake["Worker awoken, drains items from queue"]
    CheckDepth{"Remaining items > 0?"}
    BatonPass["Worker re-signals peer or continues processing"]
    WorkerIdle["Worker clears notified flag & enters idle poll"]

    Enqueue --> AtomicCheck
    AtomicCheck -->|Yes: First item| SendDoorbell
    AtomicCheck -->|No: Queue already active| SkipDoorbell
    SendDoorbell --> WorkerAwake
    WorkerAwake --> CheckDepth
    CheckDepth -->|Yes| BatonPass
    CheckDepth -->|No| WorkerIdle

    classDef blue fill:#172554,stroke:#3b82f6,stroke-width:2px,color:#bfdbfe;
    classDef yellow fill:#451a03,stroke:#f59e0b,stroke-width:2px,color:#fde68a;
    classDef green fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#a7f3d0;
    class Enqueue,AtomicCheck blue;
    class SendDoorbell,SkipDoorbell yellow;
    class WorkerAwake,CheckDepth,BatonPass,WorkerIdle green;
```

### Modelled counts
Run `python3 -m tools.model.cli benchmark-optimizations` from the harness root.
The numbers it prints are doorbell counts from that process. It does not
measure context switches or CPU.

---

## K2: Single-Buffer Response Framing (proposal)

K2 is a proposal that is not in the server. The piggyback body budget
used here (`PHP_RESPONSE_ROOM`, 8192 bytes) is a model-only constant.

### The Problem in Baseline Wire Protocol
FreeUnit responses traditionally require:
1. Message 1: `nxt_unit_response_t` containing HTTP headers allocated in shared memory.
2. Message 2: Response body chunk allocated in a separate shared memory chunk.
3. Message 3: A bare `last = 1` EOF completion message.
This incurs **3 separate port messages and 2 shared-memory allocations** for even trivial 200 OK responses.

### The proposed change
FreeUnit's wire protocol already defines a `piggyback_content` pointer and `piggyback_content_length` in `nxt_unit_response_s` (`src/nxt_unit_response.h`). The proposal is a configurable piggyback reserve (`buf_size + reserve`):
- Small responses ($\le 16$ KiB) are packed directly into the same shared-memory buffer immediately trailing the response headers.
- The router parses headers and body content in a single pass.

```mermaid
flowchart LR
    subgraph Baseline["Baseline: 3 Port Messages"]
        direction TB
        B1["Msg 1: Response Headers (SHM Chunk 1)"]
        B2["Msg 2: Response Body (SHM Chunk 2)"]
        B3["Msg 3: EOF Socket Marker (last = 1)"]
        B1 --> B2 --> B3
    end

    subgraph OptimizedK2["Optimized K2: 1 Unified Port Message"]
        direction TB
        K1Msg["Unified Msg: [Headers + Body + last = 1] in Single SHM Chunk"]
    end

    classDef red fill:#450a0a,stroke:#ef4444,stroke-width:2px,color:#fecaca;
    classDef green fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#a7f3d0;
    class B1,B2,B3 red;
    class K1Msg green;
```

### Modelled counts
This command does not count K2 messages. Do not treat the figures above as a
measurement.

---

## Single-Worker Queue Notification Coalescing (Commit `edd1283ca`)

### The Problem with Uncoalesced Single-Worker Signals
In FreeUnit with a single worker process (`depth_bound = 1`), position-based notification checks signaled every request arriving after the queue was drained. The message of FreeUnit commit `edd1283ca` mentions a CPU saving on a PHP hello run. This command does not measure that.

### The Solution: 0->1 Flag Coalescing & Drain Re-arming
Commit `edd1283ca` re-introduced coalescing on the `q->notified` flag specifically when `depth_bound == 1`:
1. **Producer Side**: The router signals the application port only on the atomic `0 -> 1` transition of `q->notified`. Subsequent enqueues while `notified == 1` suppress the doorbell.
2. **Consumer Side & Re-arming**: `nxt_app_queue_recv()` re-arms the doorbell by resetting `notified = 0` whenever the queue depth reaches zero (`nxt_app_queue_depth(q) == 0`).
3. **Deadlock Immunity**: If a worker dequeues without reading the doorbell (e.g. already awake from a previous task or crash recovery), the flag is cleared as soon as the queue drains, preventing permanent queue stalls.

```mermaid
flowchart TD
    Enq["Router enqueues request (depth_bound == 1)"]
    FlagTest{"cmp_set(&q->notified, 0, 1)?"}
    RingDoorbell["Send READ_QUEUE doorbell datagram"]
    Coalesce["Coalesce: Suppress doorbell (Worker already signaled)"]
    WorkerDrain["Worker drains queue items in batch"]
    QueueEmpty{"nxt_app_queue_depth(q) == 0?"}
    RearmDoorbell["Reset notified = 0 (Doorbell re-armed)"]
    KeepFlag["Keep notified = 1 (Items remain)"]

    Enq --> FlagTest
    FlagTest -->|0 -> 1 Transition| RingDoorbell
    FlagTest -->|Already 1| Coalesce
    RingDoorbell --> WorkerDrain
    WorkerDrain --> QueueEmpty
    QueueEmpty -->|Yes: Drained| RearmDoorbell
    QueueEmpty -->|No: More items| KeepFlag

    classDef blue fill:#172554,stroke:#3b82f6,stroke-width:2px,color:#bfdbfe;
    classDef yellow fill:#451a03,stroke:#f59e0b,stroke-width:2px,color:#fde68a;
    classDef green fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#a7f3d0;
    class Enq,FlagTest blue;
    class RingDoorbell,Coalesce yellow;
    class WorkerDrain,QueueEmpty,RearmDoorbell,KeepFlag green;
```

---

## Multi-engine epoll signalfd spurious wakeup classification (https://github.com/freeunitorg/freeunit/pull/776)

### Multi-Threaded signalfd Race
In multi-threaded FreeUnit router engines, `signalfd` is non-blocking (`O_NONBLOCK`). When a process-directed signal (`SIGCHLD`, `SIGHUP`) arrives:
- Epoll reports the descriptor readable across multiple engine threads.
- Thread 1 wins the race, reads 128 bytes (`struct signalfd_siginfo`), and consumes the signal.
- Thread 2 attempts `read()`, but the kernel signal queue is now empty, returning `n = -1` with `errno = EAGAIN`.

### The Fix (`14c69bbce`)
FreeUnit introduced `nxt_epoll_signalfd_eagain(n, err)` to explicitly classify `(n == -1 && err == NXT_EAGAIN)` as a benign spurious wakeup rather than an error:
- Erroneous reads (e.g. `EINTR` or short reads $< 128$ bytes) still log `nxt_alert()`.
- Spurious `EAGAIN` wakeups return immediately without false-positive alerts, keeping CI and production logs clean.
