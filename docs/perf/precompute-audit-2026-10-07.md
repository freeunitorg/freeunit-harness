# Precompute audit: config-derived work on the request path (2026-10-07)

Code: freeunitorg/freeunit `origin/master` b2360a1dc. Every `file:NNN` is a line at that commit.
This is code reading only. Nothing was built, traced or measured for this page. All instruction counts are
estimates from the code (loop trips times body size; a byte hash 4 to 5 per byte; an lvlhsh find 60 to 120;
an `nxt_sprintf` 300 to 1,500; a syscall 1,000 to 3,000 with the kernel).

Question: which work runs per request, per message or per field although its inputs are fixed by the
configuration, the listener, the application or a static table? It is the larger cousin of
https://github.com/freeunitorg/freeunit/pull/614. Items already in
https://github.com/freeunitorg/freeunit/issues/597 are not proposed again; the overlap is named per item.

## 1. Result in one paragraph

The request path is already mostly precomputed. Route header, argument and cookie names are hashed at config
time (src/nxt_http_route.c:851). Patterns are decoded and case-folded at config time (src/nxt_http_route.c:1272-1285).
Variable names resolve to an index when a template compiles (src/nxt_var.c:103-160). Constant templates
short-circuit (src/nxt_tstr.c:325-328). The MIME table is an lvlhsh built at config apply (src/nxt_router.c:3787).
Date, `$time_local` and the error log use the per-thread, per-second time string cache. PHP targets, options and
the script realpath are set once at process start. What is left is small. On the lab hello (no routes) the
whole list is about 460 instructions per request, 0.3 percent of 138k server instructions. That is far under
the 2.5 percent null band. On the lab static1k it is about 1,900 router instructions, which should be measurable.
The larger items run only on configs the lab does not run yet: `$_SERVER` registration in PHP and the
access log. A review pass by Claude Fable corrected 9 claims in the first draft of this page. So the first step is two new lab workloads, not code.

Scale (perf lab, 1.37.0, run/perf-lab/remeasure-1.37.0-20261007T031948Z): hello c=6: router 64.6k, server 138k
instructions per request; c=32: router 57.1k, server 125.5k. The null band of 2.5 percent is about 1,500 router
or 3,400 server instructions. static1k has no measured baseline. My estimate is 20k to 30k router instructions
(about 6 syscalls plus the parse), so its band would be 500 to 750 instructions. Job 06 below measures it.

Lab workloads (tools/perf-lab/cells.sh): `hello` passes the listener straight to the PHP app (no routes),
`"root": "/lab/www"`, `GET /hello.php`, and hello.php is only `echo`. `static1k` is one route step with
`"share": "/lab/www$uri"` and no match. Neither has compression, response_headers, rewrite or an access log.
"Typical" below means: 5 route steps with uri, host and header matches, a share with `$uri`, 3 constant
response_headers, the default text access log (7 variables), compression off, a browser with
`Accept-Encoding: gzip, deflate, br, zstd` and about 10 request fields; for PHP a script or root target that
reads `$_SERVER`, with ext/filter loaded at its default.

## 2. Ranked table

Sorted by instructions per request on the two lab workloads, then by the typical config.
R = router, A = application process. "per req" counts only requests that reach the code.

| # | file:line | recomputed per request | input | hello | static1k | typical | wave | 597 overlap |
|---|---|---|---|---|---|---|---|---|
| P1 | src/nxt_http_static.c:711-721, :817 | Last-Modified: `nxt_gmtime` plus a 7-conversion `nxt_http_date`; ETag: `nxt_sprintf("\"%xT-%xO%s\"")` | file mtime, size, svgz (not config, but a pure function) | 0 | R 1,150 | R 0 to 1,150 | 2 | none |
| P2 | src/nxt_http_compression.c:786-822, reached from :1364 | with compression off (`rtcf->compression == NULL`, set only at src/nxt_router.c:3382) the full Accept-Encoding parse still runs: `nxt_str_cstrz` alloc, whitespace compaction, `strsep` per token, identity compare | config: compression absent | 0 today; R 600 after the 597 Content-Length item | R 600 | R 600 | 1 | 597 Content-Length makes hello reach it; PR 614 edits the same function |
| P3 | src/nxt_php_sapi.c:1206, :1229 (dirname malloc at :990), :1239-1240 | root-only target: 2 `malloc`, 2 `free`, a copy of `target->root` and a dirname scan per request | target root | A 250 | 0 | A 250 (root-only) | fold into 597 | 597 root-only chdir: same function, same "last dirname" buffer |
| P4 | src/nxt_var.c:546, :560 | `nxt_var_interpreter` builds a heap parts array per query; a 5th variable grows it (alloc, memcpy, free) | compiled template: `var->vars` is fixed | 0 | R 120 | R 460 | 1 | 597 pool reuse (cheaper allocs; this removes them) |
| P5 | src/nxt_http_request.c:843-847 | `nxt_strlen` of `NXT_SERVER` or `NXT_NAME` for the Server field | skcf->server_version, literals | R 0 to 30 | R 0 to 30 | R 0 to 30 | 3 | none |
| P6 | src/nxt_router.c:8553-8566 | PHP header name transform (`HTTP_`, upper case, `-` to `_`) by a branchy byte loop, about 5 per byte | static | R 180 (oha: 4 names, 35 bytes) | 0 | R 400 | 3, or ride in 597 | 597 small request-header buffer rewrites this function |
| P7 | src/nxt_php_sapi.c:1727-1730 (`nxt_php_set_sptr`), callers :1610-1704 | 8 fixed entries, every request header (:1689-1693) and CONTENT_LENGTH/TYPE (:1696-1704) go through `sapi_module.input_filter`; ext/filter registers each twice (about 730 per entry) | static names | 0 (lab: unverified, see 6) | 0 | A 9,000 to 11,000 (18 to 20 entries) | 3 (behaviour) | none |
| P8 | src/nxt_php_sapi.c:1610, :1664, :1790 | `php_register_variable_safe` for fixed names and fixed values: strlen, name scan, hash, emalloc of key and value; `$_SERVER` rehashes 8 to 16 to 32. Fixed values: SERVER_SOFTWARE, DOCUMENT_ROOT; SCRIPT_NAME and SCRIPT_FILENAME only for a script target (a root target builds them per request, :1202-1216) | static names, `nxt_server`, `target->root`, script target | 0 (lab: unverified) | 0 | A 1,500 script target; about 800 root target | 2 | none |
| P9 | src/nxt_router_access_log.c:312-371 | JSON log format: builds an `nxt_conf` object per request, runs `nxt_utf8_sanitize` on every member value, constant members included (:352), then length and print escape the constant member names twice | rtcf log format | 0 | 0 | R 1,200 (JSON format, 8 members) | 2 | none |
| P10 | src/nxt_var.c:239-245 | each cacheable variable: murmur hash of a dense index and `nxt_lvlhsh_insert` into `r->tstr_cache`; first insert allocates a 128-byte bucket | `rtcf->tstr_state->var_refs`, count fixed at src/nxt_router.c:2444 | 0 | 0 (`$uri` is not cacheable) | R 600 | 2 | 597 pool reuse (allocs only) |
| P11 | src/nxt_http_set_headers.c:258-271 | a ctx with 3 pool allocations per request even when every value is constant | action->set_headers | 0 | 0 | R 330 | 2 | 597 pool reuse |
| P12 | src/nxt_http_set_headers.c:356-363 | constant response_headers values scanned for control bytes per response; the validator already rejects them at load (src/nxt_conf_validation.c:3502-3510) | action->set_headers | 0 | 0 | R 160 to 320 | 1 | none |
| P13 | src/nxt_http_variables.c:467 | `$status` formatted by `nxt_sprintf("%03d")` into 3 bytes | static format; the value is an unchecked uint16 from the app (src/nxt_router.c:6066) | 0 | 0 | R 290 | 1 | none |
| P14 | src/nxt_http_rewrite.c:43-80 | a constant `rewrite` is memzeroed, parsed by `nxt_http_parse_complex_target` and copied into a new `nxt_str_t` per request | action->rewrite | 0 | 0 | R 280 (10-byte target) | 1 | none |
| P15 | src/nxt_http_static.c:523-535, :728 | directory request: extension of the configured index and its MIME lookup (extract, `djb_hash_lowcase`, lvlhsh find) | conf->index, rtcf->mtypes_hash | 0 | 0 | R 170 per `/` request | 1 | none |

Not proposed (wave 3), with the reason:

| # | file:line | what | instr | reason |
|---|---|---|---|---|
| X1 | src/nxt_http_static.c:617 | open, fstat, close per static request; no fd cache | R 3,000 to 9,000 | the input is filesystem state; needs an invalidation design like nginx `open_file_cache`; its own issue |
| X2 | src/nxt_router_access_log.c:382 | one unbuffered `write()` per logged request | R 2,000 to 5,000 | a buffering design, not a precompute; related to https://github.com/freeunitorg/freeunit/issues/541 |
| X3 | src/nxt_port_memory.c:659, :664; src/nxt_runtime.c:1883 | pid to process lookup under `rt->processes_mutex` per incoming mmap message | R 250 | keyed by pid, not config; hardening site (#445 wave) with a use-after-free guard |
| X4 | src/nxt_router.c:6412, :6429; src/nxt_port_rpc.c:372 | ack: port hash find under `app->mutex`, rpc peer insert and delete | R 400 | keyed by worker pid and port; a per-worker slot array is a redesign |
| X5 | src/nxt_router.c:8041 | `app->shared_port` read under `app->mutex` | 0 extra | no hash lookup today. A lock-free cached pointer is NOT safe: restart swaps the port under the mutex and drops the old one (src/nxt_router.c:2085-2093), `nxt_router_free_app` releases it (:8006-8011). The mutex is held for `active_requests` anyway |
| X6 | src/nxt_http_static.c:1311, :1612; src/nxt_http_compression.c:655, :1326 | three walks of the request fields and one of the response fields per static response | R 250 | request data; multi-line fields need a list, not one pointer |
| X7 | src/nxt_http_route.c:2255, :2285 | header-value patterns: `nxt_memcasecmp` folds both sides per byte | R about 120 per match | 1 instruction per byte saved by folding the pattern at config |
| X8 | src/nxt_http_request.c:862 | Date: 29-byte copy from the per-second cache | R 100 | pointing the field at the thread cache is fragile |
| X9 | src/nxt_router.c:6514, :8108 | app timeout timer insert and delete per request | R 400 | only with `limits.timeout`; a real per-request deadline |

## 3. Plan

### Wave 0: lab first (Opus writes jobs, no product code)

Without these, most items below cannot be shown at all.
- Job `06-null-static1k`: `REV_A=REV_B=origin/master`, `WORKLOAD=static1k`, `CONNS=6 32`, `PERF=1`, `STRACE=1`,
  `PRIMARY=r_instructions`. It gives the static router baseline and its band. FALSIFY: band over 3 percent.
- Workload `static1k-full` in cells.sh: the static1k route plus 3 constant `response_headers`, the default
  access log to a file on tmpfs, and `-H 'Accept-Encoding: gzip, deflate, br, zstd'` on oha. Check what the lab
  oha sends by default; oha 1.15.0 sends `accept-encoding: gzip, compress, deflate, br`.
- Workload `hello-server`: hello.php plus `$_SERVER['REQUEST_URI']`, so `nxt_php_register_variables` runs.
  Before using it, prove it runs: count calls with a uprobe or a debug log line (see section 6).

### Wave 1: a new field or a condition plus one assignment at config apply (Sonnet, one PR per file)

Most are under the band alone. Each PR carries its job file. A single PR passes when its verdict is not a CLAIM
in the wrong direction and the `FUNCS` size moves as expected. The batch job W1-ALL, on a branch that stacks all
six, must CLAIM on `static1k-full`. Tests prove unchanged behaviour; the mutation check is to break the
precompute (for example drop the `;` guard of P2) and watch the named test fail.

| PR | item | file | change | job | test | lines |
|---|---|---|---|---|---|---|
| W1-a | P2 | src/nxt_http_compression.c | at the top of `nxt_http_comp_select_compressor`: `conf == NULL` and no `;` in the value returns identity, `identity_refused = 0`. `identity;q=0`, `*;q=0` and `identity;Q=0` all contain `;` and keep the old path. After PR 614 | `07-comp-off-ae`: static1k, `PRIMARY=r_instructions`, `FUNCS=unitd:nxt_http_comp_select_compressor`, EXPECT 600 fewer | test/test_static.py:166-199 (identity;q=0 with compression off), test/test_static_compression.py; new: `gzip, br` with compression off gives 200 identity | 10 |
| W1-b | P4 | src/nxt_var.c | an 8-slot `nxt_str_t *` array on the stack when `var->vars <= 8` (not a VLA); the pool array only above 8 | `08-var-parts`: static1k, EXPECT 120 fewer (NO CLAIM alone) | test/test_variables.py, test/test_static_variables.py, test/test_access_log.py | 15 |
| W1-c | P12 | src/nxt_http_set_headers.c | skip the control-byte scan when `nxt_tstr_is_const(hv->value)`; the validator guarantees it | `09-set-headers`: static1k-full | test/test_response_headers.py | 3 |
| W1-d | P13 | src/nxt_http_variables.c | write the 3 status digits with div and mod. Keep today's output exactly: 42 prints `042`, 65535 is cut to `655` | `10-status-var`: static1k-full | test/test_access_log.py; add statuses 0, 42 and 1000 from an app | 8 |
| W1-e | P14 | src/nxt_http_rewrite.c (+ 2 fields in `nxt_http_action_s`, src/nxt_http.h:373) | in `nxt_http_rewrite_init` (src/nxt_http_rewrite.c:11-14), parse a constant once into `action->rewrite_path` and `rewrite_quoted`, allocated from the rtcf pool. A constant that fails to parse keeps today's per-request 500 (store a flag), it does not fail the config | `11-rewrite-const`: new cell with `"rewrite": "/1k.txt"` | test/test_rewrite.py (test_rewrite, test_rewrite_encoded) | 30 |
| W1-f | P15 | src/nxt_http_static.c | `conf->index_exten`, `conf->index_mtype` set in `nxt_http_static_init` after src/nxt_http_static.c:241-249; the mtypes hash exists then (src/nxt_router.c:3104 runs before routes at :3350) | `12-static-index`: GET `/` on a share with index | test/test_static.py:61, test/test_static_types.py:147 | 12 |

Lifetime for all six: the value lives in a struct that reconfigure rebuilds (`rtcf`, the action, the static
conf). No invalidation is needed. W1-e: every reader of `*r->path` only reads it (src/nxt_h1proto.c:3039-3057,
src/nxt_router.c:8391-8492, src/nxt_http_static.c:1199-1218, src/nxt_http_js.c:127), so sharing one config
string across requests is safe; Opus checks that before Sonnet writes it.

### Wave 2: a data structure changes (Opus writes, Fable gates)

| item | file | change | lifetime | job | test | lines |
|---|---|---|---|---|---|---|
| P1 | src/nxt_http_static.c | a per-thread one-entry memo: {mtime} to the 29-byte date, {mtime, size, svgz} to the quoted ETag body; `W/` stays outside (etag_weak changes per request, :781). Use the thread data pattern at :62 | a pure function of its key, so no invalidation and no file identity | `13-static-memo`: static1k, `FUNCS=unitd:nxt_http_static_send`, EXPECT 1,000 to 1,150 fewer, a CLAIM | test/test_static.py:139 (etag), :615 and :638 (Last-Modified); new: `os.utime` between two requests on one keep-alive connection must change both fields (fails on a memo keyed without mtime) | 45 |
| P8 | src/nxt_php_sapi.c | at `nxt_php_setup` (:391) build interned `zend_string` names; at `nxt_php_set_target` (:557) intern root, and script_filename and script_name for a script target only. Register with `zend_hash_update` and interned values; `zend_hash_extend` once to 15 plus the field count. PHP 8.2+ has `php_register_known_variable`. Skip-filter entries only: P7 entries keep the filter | process lifetime; a reconfigure restarts PHP workers. Interned strings created after opcache startup and under ZTS need care | `14-php-server`: hello-server (root target, so about 800; a script-target cell shows the 1,500), `PRIMARY=s_instructions` | new: dump every fixed `$_SERVER` key and value and compare with a capture from master; test/test_php_application.py:790-854 | 70 |
| P9 | src/nxt_router_access_log.c | store each member's escaped `"name":` prefix and length in `nxt_router_access_log_format_create` (:158); sanitise and escape constant member values there too (:352 does it per request now); size and write values directly; keep sanitising and escaping of variable values | rtcf | `15-log-json`: static1k-full with the object format | test/test_access_log.py object format tests; byte diff of log lines before and after | 80 |
| P10 | src/nxt_var.c, src/nxt_router.c:2444 | `tstr_state->nrefs` set after `nxt_tstr_state_done`; the cache becomes a `nxt_str_t **` array of `nrefs`, allocated on the first cacheable lookup, indexed by `ref->index` | rtcf; refs from njs `nxt_var_get` are not cacheable (src/nxt_var.c:120-142) and never index the array. Assert that no ref is added after state_done | `16-var-cache`: static1k-full | test/test_variables.py, test/test_access_log.py, test/test_njs.py | 50 |
| P11 | src/nxt_http_set_headers.c | `action->set_headers_all_const` lets `nxt_http_set_headers` and `_field_op` skip the ctx | action; the compression path calls `_field_op` at src/nxt_http_compression.c:1353-1354 before the header is sent | with P12 in `09-set-headers` | test/test_response_headers.py, test/test_static_compression.py | 40 |

Expected sums: static1k about 1,870 router instructions (P1 1,150, P2 600, P4 120). static1k-full about 1,900
more with the text log and 3 headers (P4 340 more, P10 600, P11 330, P12 320, P13 290). hello about 460 (P3 250, P6 180, P5 30): not measurable; it moves
only with P8 on `hello-server` (1,500 app) and after the 597 Content-Length item (P2 then adds 600).

### Wave 3: not worth it

| item | reason |
|---|---|
| P5 Server strlen | GCC probably folds `strlen` of a ternary of two literals; check `objdump` first. At most 30 |
| P6 name transform table | 60 on hello, under every band. Ride along when the 597 small-buffer item rewrites `nxt_router_prepare_msg`, not alone |
| P7 `$_SERVER` filter bypass | the biggest PHP item (9,000 to 11,000) but a behaviour change: `filter_input(INPUT_SERVER, ...)` would return NULL and `filter.default` would stop applying. php-src's CLI server bypasses the filter only for its known variables (`register_known_var_char/_str`, sapi/cli/php_cli_server.c:618-640); its headers still go through the filter (:612). The maintainer decides. A no-change variant only helps builds without ext/filter |
| X1 to X9 | see the table in section 2 |

## 4. Overlap with 597

| item | 597 item | relation |
|---|---|---|
| P2 | Content-Length instead of chunked | after it, PHP responses reach the compression path, so P2 also saves 600 on hello |
| P3 | root-only chdir per request | same function; one persistent "last dirname" buffer serves both. Put P3 into that PR |
| P4, P10, P11 | reuse the request pool | pool reuse makes these allocations cheaper; P4, P10, P11 remove them. Independent |
| P6 | request headers in a small buffer, O(n^2) merge | same function; a static map rides along |
| X3, X4 | ACK in the first message; hardening #445 | X4 disappears with the ACK change; X3 is in the hardening file |
| all others | none | |

## 5. Five lines for the 597 tracker

- [ ] Static: memo Last-Modified and the ETag body per thread, keyed by mtime, size and svgz (src/nxt_http_static.c:718, :817). Estimate 1,150 router instructions per static response. Test: `os.utime` between two requests on one connection.
- [ ] Compression off: skip the Accept-Encoding parse when the value has no `;` (src/nxt_http_compression.c:786). Estimate 600 per static response, and for PHP after the Content-Length item.
- [ ] PHP: register the fixed `$_SERVER` names and values as interned strings, presize the array (src/nxt_php_sapi.c:1610, :1790). Estimate 1,500 app instructions for a script target and about 800 for a root target, when a script reads `$_SERVER`. Fold the root-only malloc pair (src/nxt_php_sapi.c:1206) into the chdir item.
- [ ] Templates and access log: dense array for the variable cache, parts array on the stack, `$status` without sprintf, JSON log names escaped once, constant rewrite parsed once (src/nxt_var.c:239, :546; src/nxt_router_access_log.c:312; src/nxt_http_rewrite.c:61). Estimate 1,500 to 2,500 router instructions with an access log.
- [ ] Lab: a null job on static1k for its band, a `static1k-full` workload (response_headers, access log, browser Accept-Encoding) and a `hello-server` workload that reads `$_SERVER`. Plan: docs/perf/precompute-audit-2026-10-07.md in freeunit-harness.

## 6. Not verified

- No instruction count here is measured. static1k has no lab baseline yet; the 20k to 30k router estimate is mine.
- Whether the lab hello builds `$_SERVER`. With `auto_globals_jit` on and hello.php not naming `$_SERVER`, PHP
  should skip it, which is why P7 and P8 show 0. The PHP 8.5 default of `register_argc_argv` and the lab
  image's php.ini also decide it. Check with a uprobe on `nxt_php_register_variables` during one job.
- What the lab's oha sends as Accept-Encoding (P2 applies only if it sends one with no `;`).
- Whether GCC folds the Server `strlen` (P5).
- The JSON access log estimate (P9) assumes 8 members of about 10 bytes.
