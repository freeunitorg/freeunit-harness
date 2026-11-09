# Lock-Free Per-Engine Shared Memory Slabs

In FreeUnit, shared memory buffer allocation currently funnels through a global mutex in `src/nxt_port_memory.c` (`mmaps->mutex`). The `tools/model/synthesis/` module sketches a lock-free per-engine SHM slab as a proposal. It is not a measurement of a live server, and it is not in FreeUnit today.

---

## 1. Architectural Comparison

```mermaid
flowchart TD
    subgraph LEGACY["Legacy Architecture: Global mmaps->mutex Contention"]
        E0_OLD["Router Engine 0"]
        E1_OLD["Router Engine 1"]
        WORKER_OLD["Worker Processes"]
        GLOBAL_MUTEX[("Global mmaps->mutex Lock")]
        GLOBAL_MEM["Shared Chunk Allocator"]

        E0_OLD --> GLOBAL_MUTEX
        E1_OLD --> GLOBAL_MUTEX
        WORKER_OLD --> GLOBAL_MUTEX
        GLOBAL_MUTEX --> GLOBAL_MEM
    end

    subgraph PROPOSED["Proposed: Per-Engine Lock-Free SHM Slabs"]
        E0_NEW["Router Engine 0"]
        E1_NEW["Router Engine 1"]
        SLAB0["Engine 0 Slab<br/>(Local Segments)"]
        SLAB1["Engine 1 Slab<br/>(Local Segments)"]

        CAS0["Atomic Bitmap CAS<br/>(__atomic_fetch_or)"]
        CAS1["Atomic Bitmap CAS<br/>(__atomic_fetch_or)"]

        E0_NEW --> SLAB0 --> CAS0
        E1_NEW --> SLAB1 --> CAS1

        WORKER_REMOTE["Worker Free Path"]
        REMOTE_FREE["Atomic Bit Clear<br/>(__atomic_fetch_and)<br/>ZERO MUTEX ACQUISITION"]

        WORKER_REMOTE --> REMOTE_FREE
        REMOTE_FREE -.->|Clear Bit| SLAB0
        REMOTE_FREE -.->|Clear Bit| SLAB1
    end

    classDef red fill:#450a0a,stroke:#ef4444,stroke-width:2px,color:#fecaca;
    classDef green fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#a7f3d0;
    class LEGACY,GLOBAL_MUTEX,GLOBAL_MEM red;
    class PROPOSED,SLAB0,SLAB1,CAS0,CAS1,REMOTE_FREE green;
```

---

## 2. Modelled invariants

The check is of this model. Each slab takes a `threading.Lock` to emulate
an atomic bit update. A global mutex counter exists in the code but is
never incremented.

1. **Local allocate, remote free**: Chunks allocated on Engine $A$ and freed by Engine $B$ or Worker $W$ clear the corresponding bit in Slab $A$'s bitmap.
2. **Double-free detection**: Clearing an already unset bit raises an error.
3. **Conservation**: The number of active chunks equals allocations minus frees after the modelled run.

---

## 3. Simulation & Validation API (`LockFreeShmPool`)

```python
from tools.model.synthesis import LockFreeShmPool

pool = LockFreeShmPool(num_engines=4)

# Engine 0 allocates a 16 KiB chunk locally (zero locks)
chunk = pool.alloc(engine_id=0)

# Worker on Engine 2 frees the chunk remotely (zero locks)
pool.free(chunk, calling_engine_or_worker_id=2)

assert pool.verify_invariants() is True
stats = pool.get_stats()
assert stats["global_mutex_locks"] == 0
assert stats["active_chunks"] == 0
```
