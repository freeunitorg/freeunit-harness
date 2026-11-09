# FreeUnit Subsystem Architecture & Lifecycle

This document provides a detailed breakdown of FreeUnit's runtime architecture, data plane mechanics, and how each subsystem is modeled in `tools/model`.

---

## Process Topology

FreeUnit operates as a process hierarchy designed for fault tolerance and zero-downtime reconfiguration:

```mermaid
graph TD
    Main["Main Process (Supervisor)<br/>Privileged, sets up ports, handles signals"]
    Controller["Controller Process<br/>REST API, JSON configuration, validation"]
    Router["Router Process<br/>Multi-threaded event loop, TLS, routing, proxy"]

    subgraph Workers["Worker Process Pools"]
        direction TB
        AppPoolA["Application Pool: 'web'<br/>Worker processes (PHP / Python / Go)"]
        AppPoolB["Application Pool: 'jobs'<br/>Isolated worker pool (Drupal cron, etc.)"]
    end

    Main -->|Spawns &amp; Monitors| Controller
    Main -->|Spawns &amp; Monitors| Router
    Main -->|Spawns &amp; Monitors| AppPoolA
    Main -->|Spawns &amp; Monitors| AppPoolB
    Controller -.->|Applies Config via IPC| Router

    classDef proc fill:#172554,stroke:#3b82f6,stroke-width:2px,color:#bfdbfe;
    classDef pool fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#a7f3d0;
    class Main,Controller,Router proc;
    class AppPoolA,AppPoolB pool;
```

---

## Request Lifecycle Sequence

When an external client initiates an HTTP request, the data travels through the following sequence across threads and processes:

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant Listener as Listener (*:8080)
    participant Engine as Router Engine (Thread)
    participant SHM as Shared Memory Pool
    participant AppQueue as App Queue (Ring Buffer)
    participant Worker as App Worker (libunit)
    participant SAPI as App Runtime (PHP/Python)

    Client->>Listener: TCP SYN + Handshake
    Listener->>Engine: EPOLLEXCLUSIVE Accept
    Client->>Engine: HTTP Request Bytes
    Engine->>Engine: Parse HTTP/1.1 or H2 Headers
    Engine->>SHM: Allocate 16 KiB chunk (under mmaps->mutex)
    SHM-->>Engine: Chunk Pointer & mmap_id
    Engine->>SHM: Serialize nxt_unit_request_t and headers
    Engine->>AppQueue: Push 28-byte REQ_HEADERS item (port msg + mmap msg)
    Note over Engine,AppQueue: Headers live in shared memory. A failed queue send is HTTP 500. There is no socket path for a large request.
    Engine->>AppQueue: Optional READ_SOCKET marker when a later message uses the socket

    Worker->>AppQueue: Claim and drain items from the queue
    Worker->>SHM: Read nxt_unit_request_t
    Worker->>SAPI: Invoke request handler
    SAPI->>SAPI: Execute user application logic
    SAPI-->>Worker: Emit HTTP Response (status, headers, body)

    Worker->>SHM: Write nxt_unit_response_t
    Worker->>Engine: Send RESP_HEADERS via Unix Datagram Socket
    Engine->>Client: Send HTTP response to client socket
    Worker->>SHM: Free the chunk in the shared free map
    Worker->>Engine: SHM_ACK only if chunks were freed while hdr->oosm was set
```

---

## Subsystem Details

### 1. Listener (`tools/model/engine/listener.py`)
- FreeUnit uses `EPOLLEXCLUSIVE` on Linux to avoid the thundering herd problem when multiple router threads wait on the listening socket.
- Connections are tracked via explicit state machines (`INIT`, `ACCEPTED`, `HANDSHAKE`, `READING`, `PROCESSING`, `SENDING`, `KEEPALIVE_IDLE`, `CLOSING`, `CLOSED`).
- Idle connections in keep-alive state are managed with connection-scoped tasks (`c->task`) and deferred release timers (`r->timer`), ensuring no dangling pointer dereferences occur when an application timeout fires.

### 2. Router Engine (`tools/model/engine/router.py`)
- Operates $N$ event engines (`nxt_event_engine_t`), each running a non-blocking event loop.
- Manages an RPC stream hash table (`RpcStream`), tracking in-flight requests from creation until client response completion.
- Dispatches `REQ_HEADERS` on the shared queue as a 28-byte item (`nxt_port_msg_t` 16 bytes plus `nxt_port_mmap_msg_t` 12 bytes). The headers themselves are in shared memory. A failed queue send is HTTP 500. The server has no socket fallback for a large request. The model still sends a 16-byte item and falls back to the socket; that difference is listed in [tools/model/README.md](../../../tools/model/README.md).

### 3. Shared Memory Pool (`tools/model/engine/shm_pool.py`)
- Request and response bodies are transferred via pre-allocated shared memory segments partitioned into 16 KiB chunks.
- Allocations are protected by `mmaps->mutex`.
- Implements Out-Of-Shared-Memory (OOSM) backpressure: a chunk is freed by setting its bit in the shared free map. `SHM_ACK` is sent only when chunks were freed while the header `oosm` flag was set, not on every release.

### 4. App Worker Runtime (`tools/model/engine/worker.py`)
- Built on `libunit` C client library.
- Workers poll both the private socket pair and the shared-memory queue ring buffer.
- Order between the queue and the socket is kept by a `READ_SOCKET` marker in the queue. The worker drains the queue, including that marker, before reading the matching socket message. The model does not yet model `READ_SOCKET`.
