# C Schema & ABI Mirroring

To ensure that `tools/model` accurately reflects the exact memory behavior of the production C codebase, it includes a tree-sitter header extractor and `ctypes` memory layout mirrors.

---

## Architecture of the AST Extractor

The extractor (`tools/model/ast_extractor.py`) parses FreeUnit C source files using `tree-sitter-c`:

```mermaid
flowchart LR
    Headers["FreeUnit C Headers<br/>(nxt_port.h, nxt_app_queue.h,<br/>nxt_unit.h, nxt_main.h)"]
    Shims["Macro Shimming &amp; Preprocessing<br/>(Blanks nxt_inline, nxt_packed,<br/>expands loop macros)"]
    TS["Tree-sitter C AST Parser"]
    Extract["SchemaExtractor<br/>- Enums &amp; Constants<br/>- Struct fields &amp; bitfields<br/>- Port message handlers"]
    CTypes["ctypes Layout Mirrors<br/>(c_types.py)"]
    Compare["Compare ctypes to extracted layout<br/>(size and field offsets)"]

    Headers --> Shims --> TS --> Extract
    Extract --> Compare
    CTypes --> Compare

    classDef blue fill:#172554,stroke:#3b82f6,stroke-width:2px,color:#bfdbfe;
    classDef yellow fill:#451a03,stroke:#f59e0b,stroke-width:2px,color:#fde68a;
    classDef green fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#a7f3d0;
    class Headers,TS blue;
    class Shims,Extract yellow;
    class CTypes,Compare green;
```

---

## Struct layouts

All data structures exchanged between the router and workers are modeled with byte-level fidelity matching the Linux x86_64 System V ABI.

### 1. `nxt_port_msg_t` (Port Message Header)
Size: 16 bytes. Alignment: 4 bytes.

| Offset | Field | Type | Size | Notes |
|---|---|---|---|---|
| `+0x00` | `stream` | `uint32_t` | 4B | Unique request stream identifier |
| `+0x04` | `pid` | `int32_t` | 4B | Process ID of sender |
| `+0x08` | `reply_port` | `uint16_t` | 2B | Return port ID |
| `+0x0A` | `type` | `uint8_t` | 1B | Message type (`REQ_HEADERS`, `DATA`, `ACK`, etc.) |
| `+0x0B` | `last` | `uint8_t` | 1B | Flag: 1 if final frame of stream |
| `+0x0C` | `mmap` | `uint8_t` | 1B | Flag: 1 if payload references shared memory |
| `+0x0D` | `nf` | `uint8_t` | 1B | Non-First fragment in a fragmented message |
| `+0x0E` | `mf` | `uint8_t` | 1B | More fragments flag |

### 2. `nxt_app_queue_item_t` (Ring Buffer Item)
Size: 36 bytes. Alignment: 4 bytes.

| Offset | Field | Type | Size | Notes |
|---|---|---|---|---|
| `+0x00` | `size` | `uint8_t` | 1B | Valid payload byte count ($\le 31$) |
| `+0x01` | `data[31]` | `uint8_t[31]` | 31B | Inlined message payload |
| `+0x20` | `tracking` | `uint32_t` | 4B | Stream of the request; claim and cancel compare-and-swap run on it |

### 3. `nxt_unit_request_t` (Worker Request Header)
Size: 96 bytes. Alignment: 8 bytes.

Contains packed length bytes, string offset pointers (`nxt_unit_sptr_t`), query/path indices, and HTTP field array pointers.

---

## Layout check

To compare the Python `ctypes` mirrors with the structs extracted from the headers, run:

```bash
uv run --with tree-sitter --with tree-sitter-c python3 -m tools.model.cli schema --src ./src
```

The command compares each ctypes size, field offset, and field size with the tree-sitter layout. It does not run a C compiler. A difference exits 1.
