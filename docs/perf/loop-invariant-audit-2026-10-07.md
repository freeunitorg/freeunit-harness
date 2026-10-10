# Loop-invariant audit and plan, 2026-10-07

Source: FreeUnit `origin/master` b2360a1dc. Scope: 130 files `src/*.c` (core, `nxt_unit.c`, `nxt_php_sapi.c`).
`nxt_java.c`, the language module directories and `src/test/` are out of scope.
The pattern is the one in https://github.com/freeunitorg/freeunit/pull/614: work inside a loop that does not
depend on the loop and could run once before it.

## Result

- The 614 shape, `&static_table[CONST]` in a loop body, occurs once in the scope: the 614 site itself.
- No loop condition calls `strlen`, `nxt_strlen` or another non-trivial function on invariant data.
- 83 candidate rows survived hand review (appendix): 6 batch A, 21 batch B (8 PRs), 31 fold-in (F), 20 batch C, 5 none.
- 15 sites were measured with objdump. 3 are identical (clarity only). 6 are different but cost the same.
  6 are shorter per iteration, by 1 to 4 loads. None of the 15 is worth a PR on its own for speed.
- The real repeated work is algorithmic or a syscall or atomic per element, not a missed hoist.
  The compiler cannot fix any of it. Those are the 8 batch B items.

Why most hoists do not pay: `CFLAGS` has `-fno-strict-aliasing`, so any store through `u_char *` or any opaque
call forces the compiler to reload `ptr->field`. A manual hoist removes that reload, but at `-O` the extra live
value often spills, and the load moves from the struct to a stack slot. Static size says nothing; only the loop
body in the disassembly does.

## Method and build

- Loops: ast-grep `kind: for_statement|while_statement|do_statement` gave 398, 229 and 68 loops. Cross-check:
  `rg -c '^\s*for \('` gives 399, so 1 `for` is lost to a tree-sitter parse error. The 75 macro loops
  (`nxt_list_each`, `nxt_queue_each`, `nxt_http_fields_each`, `nxt_runtime_process_each`, `nxt_process_port_each`)
  are found by text. Total 770.
- Candidates: `tools/loop-invariant-scan.py` (harness). It attributes each hit to the innermost loop and marks a
  call `inv` when no identifier in its arguments is assigned or has its address taken in that loop.
- Hand review: every loop in the 63 HOT files (about 450 loops) and the scanner hits in the COLD files.
- objdump: gcc 13.3.0 on the build host, `./configure --openssl --zlib --hardening=default`.
  `auto/cc/test` sets `-O`. The deb and rpm packages append the distro flags through `--cc-opt` after it
  (`pkg/deb/debian/rules.in:65`, `pkg/rpm/unit.spec.in:92`), so packages build at `-O2`. 614 used `-O`.
  Each site was built before and after a local hoist at both levels (`make build/src/X.o EXTRA_CFLAGS=-O2`),
  then `objdump -d --no-show-raw-insn` per function, addresses stripped, and the loop body read with `-dr`.
- Calibration: the 614 patch gives identical code for `nxt_http_comp_check_acceptable()` (612 and 637 insns),
  where `nxt_http_comp_select_compressor()` is inlined. This reproduces the 614 claim.

## Queries and hit counts

| # | pattern | query | raw hits | rows kept |
|---|---|---|---|---|
| 1 | call in a loop condition | ast-grep rule `loop-cond-call` below | 80 | 2 (both COLD or non-Linux) |
| 1 | `strlen` family in a loop condition | `rg -n '(for\|while) \(.*\b(strlen\|nxt_strlen\|nxt_length)\(' $(cat scope.txt)` | 0 | 0 |
| 2 | `&nxt_table[CONST]` in a loop | ast-grep rule `loop-invariant-static-table-element` below | 1 | 1 (614) |
| 2 | pointer chain `a->b->c` with invariant base | scanner `P2chain`, regex `\b([a-z_]\w*)((?:->\w+){2,})` | 31 | 50 with hand review |
| 2 | `x[CONST].f` with invariant `x` | scanner `P2tab` | 27 | 1 (the 614 site; the rest are iovec, fd and byte elements) |
| 3 | listed calls (pure, clock, alloc, lock, copy) | scanner `P3`; 106 with all-invariant arguments | 332 | 15 |
| 4 | compare call in a nested loop | scanner `P4` | 1 | 9 with hand review |
| 5 | syscalls and `nxt_atomic_*` in loops | scanner `P5`; 72 with invariant arguments | 137 | 9 |

The P1 rule: a `call_expression` with `inside: {kind: for_statement, field: condition|update, stopBy: end}`, or
inside a `parenthesized_expression` whose parent is a `while_statement` or `do_statement`. Its 80 hits are branch
hints (`nxt_fast_path` 13, `nxt_slow_path` 3), address macros (`nxt_queue_tail` 11, `nxt_queue_head` 1,
`nxt_rbtree_sentinel` 1), compile-time `nxt_nitems` 6, iterators (`nxt_http_fields_next` 8, `nxt_queue_next` 7,
`getline` 3, `strsep`, `strchr`, `strstr`) and spin or CAS loops (`nxt_atomic_try_lock` 4). The one `strlen`
loop the pattern targets, `nxt_cgroup.c:72`, already caches the length. Of the 106 invariant P3 hits, 64 are
`nxt_length()` of a literal (a compile-time constant), 26 are allocations that grow an array or pool, 12 are
compares, 3 are copies and 1 is a clock read. A row with two patterns counts under both in the table.

## Top 15, measured

Static instruction counts are for the function that contains the loop after inlining. "shorter" means fewer
loads or instructions in the loop body; the reason is why the compiler could not hoist the value itself.

| # | site | function (inlined into) | -O before/after | -O2 before/after | loop body verdict |
|---|---|---|---|---|---|
| 1 | `nxt_http_compression.c:862` | `select_compressor` (`check_acceptable`) | 612/612 | 637/637 | identical |
| 2 | `nxt_http_parse.c:889`, `:1132` | `nxt_http_parse_complex_target` | 1662/1662 obj | 1735/1735 obj | identical: `rp->target_end` was already in a register |
| 3 | `nxt_http_set_headers.c:357` | `nxt_http_set_headers_value` | 592/592 obj | 621/621 obj | identical |
| 4 | `nxt_router.c:8405` | `prepare_msg` (`process_http_request`) | 1198/1199 | 1197/1198 | same cost: `add (%r11),%r10` becomes a register add |
| 5 | `nxt_router.c:8585` | same, duplicate inner loop | 1198/1208 | 1197/1221 | same cost: 2 of 3 `*field` reloads become stack loads |
| 6 | `nxt_router.c:8526`, `:8529` | same, copy loop | 1198/1224 | 1197/1222 | shorter: per field 10 loads and 12 insns become 6 and 6; stores via `dst_field` and `p` may alias `*r`, `*prefix` |
| 7 | `nxt_router.c:6036` | `response_header_parse` (`response_ready_handler`) | 1034/1036 | 1106/1091 | shorter: about 3 loads per response field; `nxt_list_add()` is opaque |
| 8 | `nxt_http_route.c:1989` | `nxt_http_route_header` (`route_rule`) | 231/236 | 231/264 | same cost: one compare gains a register, the field count spills |
| 9 | `nxt_http_request.c:384` | `nxt_http_request_forward` | 322/339 | 356/361 | same cost: the `header->` chain becomes stack loads |
| 10 | `nxt_epoll_engine.c:966` | `nxt_epoll_poll` | 174/173 | 314/312 | shorter: 1 load and 1 insn per event; `nxt_work_queue_add()` is opaque |
| 11 | `nxt_sendbuf.c:99` | `nxt_sendbuf_mem_coalesce` | 66/79 | 75/82 | shorter: 1 load per buffer, 3 per new iovec, plus 2 push/pop per call; net 0 for 1 to 3 buffers |
| 12 | `nxt_h1proto_websocket.c:576` | `nxt_h1p_ws_utf8_validate` | 251/154 | 282/189 | same cost per byte: `state->len` becomes a stack load of the bound; the size drop is in the slow path |
| 13 | `nxt_http_compression.c:345` | `compress_app_response` | 120/124 | 118/127 | same cost: 2 dependent loads become 2 stack loads |
| 14 | `nxt_lvlhsh.c:243` | `nxt_lvlhsh_bucket_find` | 52/56 | 58/64 | shorter: 1 load per entry, plus 1 push/pop per call; the indirect `proto->test()` blocks the hoist |
| 15 | `nxt_router_access_log.c:324` | `access_log_json` (`access_log_writer`) | 160/165 | 167/173 | shorter: one call of `nxt_tstr_query_init()` (about 15 insns) per variable member after the first |

The fork reviewers predicted "shorter" for row 2 and "identical" for row 4. Both predictions were wrong. A
prediction is not a verdict; batch A accepts a site only on an identical objdump.

## Plan

Order: B1, B2, A, B5, B6, B3, B4, B7, then B8 only if its count says so. Batch C and F get no PR of their own.

### Batch A: clarity-only hoists on HOT paths (one PR, Sonnet)

After https://github.com/freeunitorg/freeunit/pull/614 merges, one PR, grouped by file, about 15 changed lines:

| file | sites | change |
|---|---|---|
| `src/nxt_http_parse.c` | 889, 1132 | `end = rp->target_end;` before the first loop, used by both |
| `src/nxt_http_set_headers.c` | 356-357 | `vs = value->start; vl = value->length;` inside `if (value->start != NULL)` |
| `src/nxt_http_compression.c` | 1197-1200 | `v = vary->value;` before `keep = vary->value_length;` |
| `src/nxt_http_static.c` | 1858 | `start = path->start;` before the loop |

Acceptance: for each object, `objdump -d --no-show-raw-insn` of the containing function is identical at `-O` and
`-O2`, as in 614. A site that is not identical leaves the PR. No measurement, no new test. The last two sites are
predicted identical but not yet measured.

### Batch B: real repeated work on HOT paths (one PR each)

Metrics come from `docs/perf/perf-lab.md`: `r_instructions` or `s_instructions` per request (null band about
2.5 percent) and exact `strace -c` syscall counts. CPU time does not resolve these deltas. A PR that measures
inside the null band reports NO CLAIM and is judged as code, not as speed.

| id | sites | work removed | fix | metric and lab job | test | size |
|---|---|---|---|---|---|---|
| B1 | `nxt_router.c:8581` (plus 8405, 8526, 8529, 8585) | n(n-1)/2 duplicate scans per request for PHP, Perl, Ruby; up to 4 reloads per field | 64-bit bitmap of `field->hash & 63` in the counting loop at 8396; scan for duplicates only on a collision; fold the same-function hoists | `r_instructions`, hello.php with 20 and 50 request headers, no duplicates | duplicate names, case-different names, join order ", ", `dup->skip` | 40 lines + test, Opus |
| B2 | `nxt_h1proto.c:1061`, `:1319` | one `write()` per decoded chunk of a request body spilled to the temp file | one `writev()` per buffer chain, one helper for both | `strace -c` `write`/`writev` per request, chunked upload of 200 chunks over `body_buffer_size` | body hash for 1, 2, 200 chunks and a short write | 60 lines + test, Opus |
| B5 | `nxt_http_set_headers.c:71`, `:147`, `:467`; `nxt_http_compression.c:968` | name matches on config data on every request | flags and indexes computed in `nxt_http_set_headers_init()` (198) and at compression config apply | `r_instructions`, static file with `response_headers` and compression | existing set_headers and compression tests | 40 lines, Sonnet |
| B6 | `nxt_http_compression.c:654`, `699`, `1035`, `1092`, `1323`, `1486`; `nxt_http_static.c:1311`, `1612` | 4 passes over response fields and 2 over request fields per compressed response; no `f->hash` precheck | one pass per list, or test `f->hash` and `f->name_length` first; length check before compare at 1326 | `r_instructions`, compressed static and app responses | `test/test_static_compression.py` and the app compression tests | 50 lines, Sonnet |
| B3 | `nxt_unit.c:4612`, `5619`; `nxt_port_memory.c:607`, `234` | one locked CAS or OR per 16 KiB chunk: 64 per MiB on each side | one atomic per 64-bit word with a run mask | `s_instructions`, `r_instructions`, 1 MiB response | C test in `src/test/nxt_tests.c`: runs across a word boundary, release of a partial run | 80 lines + test, Opus; after the hardening wave (#445-#448 touch these files) |
| B4 | `nxt_unit.c:6192` (and 5851, 5458) | 4 or more mutex pairs per message, 2 of them only to find an empty queue | check emptiness without the lock (atomic counter) before locking | `s_instructions` at c=1 and c=32, app with 4 threads | threaded Python app under load; a lost wake must fail the test | 30 lines + test, Opus, Fable review |
| B7 | `nxt_unit.c:2451` | O(n^2) duplicate grouping per request (Python WSGI and Java only) | bucket by hash once | `s_instructions`, WSGI app with 50 headers | `src/test/nxt_unit_msg_test.c` cases | 50 lines, Sonnet |
| B8 | `nxt_port_memory.c:987` | process lookup and `incoming.mutex` per shm record of one message | look the process up once per message | count records per message first; if most messages carry 1 or 2, drop | existing port tests | 20 lines |

Lines for https://github.com/freeunitorg/freeunit/issues/597:
- Wave 4, existing item "drop the O(n^2) duplicate-header merge": link the B1 PR.
- Wave 4, small: "Request body temp file: one writev per buffer chain instead of one write per chunk (B2)".
- Wave 4, small: "Config-derived set_headers and compression decisions computed at config apply (B5)".
- Wave 4, small: "One pass over the field lists in compression and static (B6)".
- Wave 4: "Shared-memory chunk bitmap: one atomic per word, not per chunk (B3)".
- Wave 4, after K1: "libunit: no mutex pair to find an empty queue (B4)".
- Wave 4, small: "libunit duplicate field grouping without O(n^2) (B7)". B8 gets a line only if its count justifies it.

### F: fold into the next change of the function (31 rows)

These are HOT reloads of one to three fields per iteration. Rows 7 to 15 above show the outcome: same cost
or a load or two per iteration. No standalone PR. When a PR changes one of these functions for another reason,
it may hoist the value and quote the loop body from `objdump -dr`.

### Batch C: COLD paths (20 rows)

Config apply, startup, shutdown and process start. No effect at run time. Do nothing, except `nxt_router.c:3938`
and `nxt_var.c:113` when a PR touches those functions anyway.

## The reviewer rule

For the ast-grep gate of https://github.com/freeunitorg/freeunit/pull/449 (`tools/ast-grep/rules/`). It gives
1 hit on master (the 614 site) and 0 after 614. Matching only upper-case index names keeps `nxt_process_argv[0]`
(`nxt_runtime.c:1359`, a global `char **` on an error path) out.

```yaml
id: loop-invariant-static-table-element
language: c
severity: warning
message: >-
  A file-scope table element at a constant index is read inside a loop.
  It does not change between iterations. Read it once before the loop.
note: >-
  freeunit PR 614. The compiler usually folds the address, so the fix is for
  the reader. A pointer chain through a struct is not caught here.
rule:
  kind: subscript_expression
  all:
    - has: {field: argument, kind: identifier, regex: '^nxt_'}
    - has: {field: index, regex: '^[A-Z][A-Z0-9_]*$'}
    - inside:
        stopBy: end
        any: [{kind: for_statement}, {kind: while_statement}, {kind: do_statement}]
    - not:
        inside: {stopBy: end, kind: for_statement, field: initializer}
```

Test file `tools/ast-grep/tests/loop-invariant-static-table-element-test.yml` (passes `ast-grep test`):
valid: the element read before the loop, `&nxt_tab[0]` in a `for` initializer, `&nxt_tab[i]`, and
`nxt_process_argv[0]` in a loop. Invalid: `&nxt_tab[NXT_ID]` in a `while` body, `nxt_tab[NXT_TAB_LAST].len` in a
`for` body.

The rule for reviewers in one line: a value read from a `static const` table or an unchanging struct inside a
loop is read once before the loop; for a struct field, ask for `objdump -dr` of the loop only when the PR claims
speed.

## Not verified

- No lab run. Every batch B effect is an estimate until its job runs.
- `nxt_php_sapi.c` was not compiled: the build host has no `php-config` outside the lab images. Hand review
  found no loop-invariant work in its request path.
- The 68 rows outside the top 15 are hand-review judgments. Their "compiler hoists" column is a prediction.
- Only gcc 13.3.0 on x86-64. Not clang, not arm64.
- Non-Linux engines and sendfile variants were reviewed by loop header only.
- B3 overlap with #445 to #448: run `git merge-tree --write-tree origin/master <head>` before starting.

## Side findings, outside the audit

- `nxt_http_compression.c:1326` compares `f->name` with `nxt_strcasecmp()` and no length. It works because
  libunit writes a NUL after each name (`nxt_unit.c:2749`) and static fields use literals. B6 replaces it.
- `nxt_unit.c:4612` grabs chunks without the `*c + nchunks < PORT_MMAP_CHUNK_COUNT` bound of the router twin
  (`nxt_port_memory.c:606`). Not checked whether the sentinel bit makes it safe. Check before B3.
- `nxt_http_js.c:222` finds request headers with a case-sensitive `memcmp()`.

## Appendix: all rows

Batch: A clarity PR; B1 to B8 own PRs; F fold into the next change; C COLD, no PR; none inherent.
| batch | file:line | invariant | pat | path | fix | effect | objdump -O/-O2 |
|---|---|---|---|---|---|---|---|
| A (PR 614) | nxt_http_compression.c:862 | &nxt_http_comp_compressors[NXT_HTTP_COMP_SCHEME_IDENTITY]. | P2 | HOT | hoist (PR 614) | clarity | identical/identical |
| A | nxt_http_compression.c:1198 | vary->value | P2 | HOT | hoist | clarity |  |
| A | nxt_http_parse.c:1132 | rp->target_end | P2 | HOT | hoist (same local as 889) | clarity | identical (with 889) |
| A | nxt_http_parse.c:889 | rp->target_end (also rp->encoded_slashes at 1072/1104, rp. | P2 | HOT | hoist to locals | ns/byte: 1 load per target by. | identical/identical |
| A | nxt_http_set_headers.c:357 | value->start, value->length | P2 | HOT | hoist | clarity | identical/identical |
| A | nxt_http_static.c:1858 | path->start | P2 | HOT | hoist | clarity |  |
| B1 | nxt_router.c:8405 | prefix->length (8405, 8415) | P2 | HOT | hoist prefix_len | clarity | same cost |
| B1 | nxt_router.c:8526 | prefix->length (8526, 8547, 8549, 8578), prefix->start (8. | P2 | HOT | hoist: prefix_len / prefix_start. | ns/field (up to 4 loads) | shorter |
| B1 | nxt_router.c:8529 | r->content_length, r->content_type, r->cookie, r->authori. | P2 | HOT | hoist into four locals | ns/field (up to 4 loads) | shorter (with 8526) |
| B1 | nxt_router.c:8581 | the outer field's duplicates: every field rescans all lat. | P4 | HOT | precompute: in the counting loop. | ns/field: n(n-1)/2 inner step. |  |
| B1 | nxt_router.c:8585 | field->name_length, field->hash, field->name | P2 | HOT | hoist into locals before the inne. | ns/iter (3 loads per inner st. | same cost |
| B2 | nxt_h1proto.c:1061 | b->file->fd, skcf->max_body_size; one nxt_fd_write() per. | P5 | HOT | batch: one writev() of the chain. | syscall/iter |  |
| B2 | nxt_h1proto.c:1319 | same as 1061 | P5 | HOT | batch (share one helper with 1061) | syscall/iter |  |
| B3 | nxt_unit.c:4612 | one locked CAS per 16 KiB chunk on the shared free_map wo. | P5 | HOT | batch: one CAS per 64-bit word wi. | 1 locked CAS per chunk: 64 pe. |  |
| B3 | nxt_unit.c:5619 | one atomic OR per chunk on the same word | P5 | HOT | batch: one atomic OR per word (ma. | 1 lock-prefixed op per chunk. |  |
| B4 | nxt_unit.c:5458 | ctx_impl->mutex taken per record inside mmap_buf_get | P3 | HOT | batch: take n buffers under one l. | 1 mutex pair per extra record |  |
| B4 | nxt_unit.c:6192 | ctx_impl->mutex taken 4+ times per message: read_buf_get. | P3 | HOT | batch: unlocked emptiness peek (a. | 2 of 4 uncontended mutex pair. |  |
| B5 | nxt_http_compression.c:968 | whether enabled[i] is the first entry with its token: con. | P4 | HOT | precompute at config apply (first. | ns, O(n^2) in configured comp. |  |
| B5 | nxt_http_set_headers.c:147 | index of the last key named Content-Encoding is config-in. | P3 | HOT | precompute at config apply: store. | ns/iter, 1 memcasecmp per key. |  |
| B5 | nxt_http_set_headers.c:467 | key name/length are config; the field list is rescanned o. | P4 | HOT | one pass over response fields mat. | ns per key x field (O(keys x. |  |
| B5 | nxt_http_set_headers.c:71 | result depends only on config (action->set_headers names) | P3 | HOT | precompute at config apply: flag. | ns/iter: 2 strncasecmp per ke. |  |
| B6 | nxt_http_compression.c:1097 | same list rescanned per name | P4 | HOT | one pass collecting the fields, o. | ns/field x 4 passes |  |
| B6 | nxt_http_compression.c:1326 | constant name "Content-Encoding"; unbounded nxt_strcasecm. | P3 | HOT | test f->name_length == 16 first,. | ns/field |  |
| B6 | nxt_http_compression.c:659 | constant name; parser already hashed every field (f->hash) | P3/P4 | HOT | add Accept-Encoding to nxt_h1p_fi. | ns/field x 2 passes |  |
| B6 | nxt_http_static.c:1311 | same list rescanned | P4 | HOT | one pass, or f->hash precheck | ns/field x 2 passes |  |
| B7 | nxt_unit.c:2451 | none (algorithmic) | P4 | HOT | precompute: sort or bucket by has. | O(n^2) field pairs, O(n^3) wo. |  |
| B8 | nxt_port_memory.c:987 | spid invariant: each record repeats nxt_runtime_process_r. | P3 | HOT | hoist: ref the process once per m. | per extra record: 2 mutex pai. |  |
| F | nxt_epoll_engine.c:966 | engine->u.epoll.events pointer | P2 | HOT | hoist: events = engine->u.epoll.e. | 1 load per event | shorter |
| F | nxt_h1proto_websocket.c:1005 | &task->thread->engine->fast_work_queue | P2 | HOT | hoist wq | none (rare inner path) |  |
| F | nxt_h1proto_websocket.c:576 | state->len (changes only on the non-ASCII path) | P2 | HOT | keep len in a local, store back a. | ns/byte: 1 load per text-fram. | same cost |
| F | nxt_http_chunk_parse.c:111 | b->mem.pos/free load and store per byte; hcp->chunk_size. | P2 | HOT | local pos, store back at exits | ns/byte on chunk-size lines a. |  |
| F | nxt_http_chunk_parse.c:335 | &task->thread->engine->fast_work_queue | P2 | HOT | hoist wq | ns/iter |  |
| F | nxt_http_compression.c:345 | &task->thread->engine->fast_work_queue | P2 | HOT | hoist wq | ns/iter: 2 dependent loads | same cost |
| F | nxt_http_compression.c:600 | conf->nr_enabled, conf->enabled | P2 | HOT | hoist to locals | ns/iter: 2 loads |  |
| F | nxt_http_proxy.c:269 | field count is known before the loop | P3 | HOT | size response fields once | ns/field |  |
| F | nxt_http_request.c:384 | client_ip->header_hash, client_ip->header->start/length,. | P2 | HOT | hoist to locals | ns/field: up to 6 loads | same cost |
| F | nxt_http_route.c:1628 | route->items, r->log_route | P2 | HOT | hoist n | clarity/ns |  |
| F | nxt_http_route.c:1989 | rule->u.name.hash, .length, .start | P2/P4 | HOT | hoist to locals | ns/field: 3 loads | same cost |
| F | nxt_http_route.c:2040 | rule->u.name.hash, .length, .start | P2 | HOT | hoist | ns/arg |  |
| F | nxt_http_route.c:2118 | rule->u.name.hash, .length, .start | P2 | HOT | hoist | ns/cookie |  |
| F | nxt_http_route.c:2218 | pattern_slices->nelts, pattern->case_sensitive, pattern->. | P2 | HOT | hoist | ns/slice |  |
| F | nxt_http_static.c:1513 | etag->length, etag->start | P2 | HOT | hoist | ns/tag |  |
| F | nxt_http_variables.c:631 | vf->hash, vf->name.length, vf->name.start | P2 | HOT | hoist | ns/entry |  |
| F | nxt_http_variables.c:662 | vf->hash, vf->name.length, vf->name.start | P2 | HOT | hoist | ns/field |  |
| F | nxt_http_websocket.c:104 | &task->thread->engine->fast_work_queue | P2 | HOT | hoist wq | ns/iter |  |
| F | nxt_lvlhsh.c:243 | lhq->key_hash, lhq->proto | P2 | HOT | hoist key = lhq->key_hash | 1 load per bucket entry | shorter |
| F | nxt_mp.c:622 | mp->page_size | P2 | HOT | hoist | 1 load per page scanned |  |
| F | nxt_poll_engine.c:578 | engine->u.poll.nfds | P1/P2 | HOT | hoist | none |  |
| F | nxt_port_socket.c:2820 | port->socket.read_work_queue | P2 | HOT | hoist | 1 load per completed buffer |  |
| F | nxt_router.c:6036 | b->mem.pos (6036, 6038; equals resp), r->mem_pool and &r-. | P2 | HOT | hoist: start = b->mem.pos (or use. | ns/field (2-3 loads) | shorter |
| F | nxt_router.c:6501 | &task->thread->engine->fast_work_queue | P2 | HOT | hoist | none (error path) |  |
| F | nxt_router.c:9143 | ack already found: the outer loop keeps scanning every re. | P4 | HOT | stop the outer loop once ack is s. | ns per remaining segment, und. |  |
| F | nxt_router_access_log.c:324 | nxt_tstr_query_init(&r->tstr_query, rtcf->tstr_state, &r-. | P3 | HOT | hoist: init once on the first non. | ns/member: one call plus 4 st. | shorter (call) |
| F | nxt_sendbuf.c:99 | sb->limit, sb->nmax, sb->iobuf | P2 | HOT | hoist into locals | 3 loads per buffer | shorter |
| F | nxt_unit.c:2621 | req->response, ->fields_count, ->fields | P2 | HOT | hoist: old = req->response; n = o. | 3 loads per field |  |
| F | nxt_unit.c:4601 | pthread_self() and port->id.id per segment | P3 | HOT | hoist: self = pthread_self(); por. | 1 call + 1 load per segment;. |  |
| F | nxt_unit.c:6121 | ctx_impl->ctx.unit | P2 | HOT | hoist above the loop | 1 load per ready request |  |
| F | nxt_var.c:560 | parts array grows by nxt_array_add() from 0; var->vars kn. | P3 | HOT | allocate var->vars pointers once. | none for <=4 variables; one e. |  |
| C | nxt_http_request.c:1533 | name->start, name->length, case_sensitive branch | P2 | COLD | hoist; unswitch case_sensitive | none |  |
| C | nxt_http_request.c:1618 | name->start, name->length | P2 | COLD | hoist | none |  |
| C | nxt_http_static.c:2132 | nxt_strlen of a literal extension | P3 | COLD | nxt_string() in the table | none |  |
| C | nxt_http_static.c:268 | nxt_tstr_str(conf->chroot, &chr) | P3 | COLD | hoist | none |  |
| C | nxt_js.c:147 | jcf->modules->nelts, jcf->funcs->nelts | P2 | COLD | hoist n | clarity |  |
| C | nxt_main_process.c:1930 | rt->listen_sockets->elts | P2 | COLD | hoist | none |  |
| C | nxt_openssl.c:758 | tls->conf->tickets->count | P2 | COLD | hoist | none (per resumed TLS handsha. |  |
| C | nxt_port.c:1316 | task->thread->engine->mem_pool, &task->thread->engine->fa. | P2 | COLD | hoist | 3 loads per process |  |
| C | nxt_port.c:485 | new_port->pid, new_port->type | P2 | COLD | hoist | 2 loads per process |  |
| C | nxt_router.c:2160 | report pointer fields (report->accepted_conns etc.) | P2 | COLD | sum into locals, store once | clarity |  |
| C | nxt_router.c:2662 | task->thread->runtime->tls->server_free | P2 | COLD | hoist | none |  |
| C | nxt_router.c:3470 | task->thread->runtime->tmp | P2 | COLD | hoist | none |  |
| C | nxt_router.c:3938 | fh->header->length, fh->header->start | P2 | COLD | hoist | clarity |  |
| C | nxt_router.c:4943 | tmcf->router_conf->mem_pool, tmcf->engine->task (4935) | P2 | COLD | hoist | clarity |  |
| C | nxt_router.c:5020 | tmcf->engine->task | P2 | COLD | hoist | clarity |  |
| C | nxt_runtime.c:715 | rt->listen_sockets->elts | P2 | COLD | hoist | none |  |
| C | nxt_unit.c:4904 | segment not yet shared; atomic AND per chunk | P5 | COLD | precompute: plain bit clear / mem. | n atomics per new segment |  |
| C | nxt_unit.c:7147 | the target ctx_impl repeats across requests; nxt_unit_awa. | P5 | HOT | batch: group by ctx_impl, one loc. | 1 sendmsg per request to the. |  |
| C | nxt_unit.c:923 | lock per process | P3 | COLD | none | n/a |  |
| C | nxt_var.c:113 | state->var_refs->nelts | P1/P2 | COLD | hoist n | clarity |  |
| none | nxt_epoll_engine.c:680 | engine->u.epoll.fd (load); one epoll_ctl per change | P5 | HOT | none for the syscall (Linux has n. | 1 syscall per change (inheren. |  |
| none | nxt_event_engine.c:554 | none: once per poll, a cached field read | P3 | HOT | none | none |  |
| none | nxt_port_socket.c:1209 | none: one sendmsg per message | P5 | HOT | none (batch with sendmmsg is poss. | 1 syscall per message, inhere. |  |
| none | nxt_runtime.c:1359 | nxt_process_argv[0] | P2 | COLD | none | none |  |
| none | nxt_unit.c:3418 | none: one message per shm buffer (up to PORT_MMAP_DATA_SI. | P5 | HOT | none | 1 sendmsg per <=10 MiB part,. |  |
