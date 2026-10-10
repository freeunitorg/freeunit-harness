# OTLP export retries in FreeUnit after PR 577: what controls them, and a non-blocking design

Date: 2026-10-07. Tree: freeunitorg/freeunit origin/master at 872bf041 (PR 577 merged as 23074fa1).
Crates: opentelemetry-otlp 0.33.0, opentelemetry_sdk 0.33.0 (cargo registry copies; src/otel vendors nothing,
Cargo.lock pins them). Measurements: laptop build `./configure --openssl --otel --tests`, fake collector
`test/fake_otlp`, scripts and raw logs in the session scratchpad (`otelrun.sh`, `run/*/collector.log`).

## 1. What 0.33 changed and what 577 did

| Fact | Evidence |
|---|---|
| Retry is on by default: `RetryPolicy::recommended()` = 3 retries, 100 ms initial, 1600 ms cap, up to 100 ms jitter | opentelemetry-otlp:src/retry_policy.rs:36-42; `disabled()` = 0 retries, :26-30 |
| HTTP retries 429, 503 (Retry-After honoured, capped at 30 s), 502, 504 and status 0 (no response: refused, reset, timeout). Everything else is final | retry_classification.rs:32-46; retry.rs:45 |
| gRPC retries RESOURCE_EXHAUSTED and UNAVAILABLE (RetryInfo honoured), CANCELLED, DEADLINE_EXCEEDED, ABORTED, OUT_OF_RANGE, DATA_LOSS | retry_classification.rs:124-149 |
| Backoff: delay doubles from initial to cap, plus jitter; the exporter timeout (10 s) is the total deadline; a sleep that would pass the deadline fails the batch | retry.rs:117-173 |
| The retry loop runs inside `export()`. With `reqwest-blocking-client` it sleeps with `std::thread::sleep`; with tonic it sleeps on the tokio runtime, but the caller still blocks on `block_on` | retry.rs:19-27; http/mod.rs:450-460; tonic/trace.rs:70-76 |
| The batch processor is one thread. It calls `futures_executor::block_on(exporter.export(batch))` and takes the next batch only after that returns | opentelemetry_sdk:src/trace/span_processor.rs:789-790, :738-764 |
| While it blocks, finished spans go to a `sync_channel(max_queue_size)`; `try_send` on a full channel drops the span, counts it in a private field and logs once through the internal `tracing` logger (not wired in FreeUnit) | span_processor.rs:520, :818, :862-873 |
| `OTEL_BSP_EXPORT_TIMEOUT` / `max_export_timeout` is parsed but never used by this processor; only the async-runtime variant uses it | span_processor.rs:1218-1223 (parse); span_processor_with_async_runtime.rs:206,334 (use) |
| The processor's ForceFlush path never calls `exporter.force_flush()`; Shutdown calls `exporter.shutdown()` | span_processor.rs:617-629, :641 |
| PR 577: `with_retry_policy(RetryPolicy::disabled())` on both builders, nothing else; fake_otlp `--status`; test `test_otel_export_not_retried` | src/otel/src/lib.rs:230, :252; test/test_otel.py:939-969 |
| 1.37.1 CHANGES has no entry for 577 (only the "--otel no longer needs OpenSSL" change) | CHANGES lines 1-40 at origin/master |

Measured with the two lines of 577 removed (build `unitd-retryon`), batch_size 1, collector answering 503, two
requests sent 30 ms apart: 8 collector requests. Batch 1 at +0, +108, +278, +455 ms (4 attempts, 0.84 s); batch 2
started at the instant batch 1 gave up and took another 0.83 s. The second batch waited the whole time: this is the
serial blocking 577 removed. Master: 5 batches, 5 requests, `/status` failed 5.

## 2. Environment variables

The exporter is built with explicit builder calls (lib.rs:224-233, :239-261) and the processor with
`BatchConfigBuilder::default()` plus two setters (lib.rs:393-400). The crate rule is "builder value wins, else the
signal env var, else the generic one, else the default" (otlp exporter/mod.rs:420-439, http/mod.rs:770-795,
tonic/mod.rs:342-365; sdk span_processor.rs:1069-1089 "Programmatic configuration overrides"). Headers are the
exception: env headers are merged into the builder's (http/mod.rs:294-298, tonic/mod.rs:591-610).

The environment does reach the exporter. The router is a `fork()` child of main (nxt_process.c:704), nothing calls
`clearenv()`, and the process title code copies the strings before it reuses their memory (nxt_process_title.c:150-162).
The deb unit loads `EnvironmentFile=-/etc/default/unit` (pkg/deb/debian/freeunit.unit.service:9), the rpm unit sets
only `UNITD_OPTIONS` (pkg/rpm/rpmbuild/SOURCES/unit.service:19); an operator can add `Environment=` lines or
`docker -e`. Every row below was measured by starting unitd with the variable set (`env VAR=... unitd`).

| Variable | Read at | Effect in FreeUnit | Measured |
|---|---|---|---|
| OTEL_EXPORTER_OTLP_TIMEOUT, _TRACES_TIMEOUT | exporter/mod.rs:420 | none: `with_timeout(10 s)` wins | =1000, collector accepts and never answers: failed=0 after 3 s, failed=1 after 12 s (10 s timeout kept) |
| OTEL_EXPORTER_OTLP_ENDPOINT, _TRACES_ENDPOINT | http/mod.rs:775-783, tonic:355-358 | none: `with_endpoint` wins | both set to port 1: 5 of 5 spans at the configured collector |
| OTEL_EXPORTER_OTLP_PROTOCOL, _TRACES_PROTOCOL | exporter/mod.rs:99-113 | none for http (`with_protocol`); tonic ignores it | =grpc with protocol http: 5 of 5 exported |
| OTEL_EXPORTER_OTLP_HEADERS, _TRACES_HEADERS | http:294-298, tonic:591-610 | works: added to every export (auth tokens for a collector) | `x-test=hello`: header present in both dumped requests |
| OTEL_EXPORTER_OTLP_COMPRESSION, _TRACES_COMPRESSION | exporter/mod.rs:258-285 | ignored with a hidden warning: no gzip/zstd feature is compiled in | =gzip: 5 exported, no Content-Encoding, nothing in unit.log |
| OTEL_EXPORTER_OTLP_INSECURE | exporter/mod.rs:299 | gRPC scheme only; plaintext anyway | not measured |
| OTEL_BSP_SCHEDULE_DELAY | span_processor.rs:1198 | works: FreeUnit does not set the delay (default 5 s) | =500, batch_size 128, one request: exported 0.47 s later (control: at shutdown, 2 s later) |
| OTEL_BSP_EXPORT_TIMEOUT | span_processor.rs:1218 | none: unused by the thread processor | =1000 with a silent collector: failed=0 after 3 s |
| OTEL_BSP_MAX_QUEUE_SIZE | :1191 | none: `with_max_queue_size(4096)` wins | =1 with 5 quick spans: 5 exported |
| OTEL_BSP_MAX_EXPORT_BATCH_SIZE | :1205 | none: `with_max_export_batch_size` wins | =1 (same run) |
| OTEL_BSP_MAX_CONCURRENT_EXPORTS | :1184 | none: async variant only | not measured |
| OTEL_TRACES_SAMPLER, _ARG; OTEL_SPAN_*_LIMIT | sdk trace/config.rs:39-61 | sampler: none, `with_sampler` wins; span limits: default config, would apply | =always_off: 5 of 5 exported |
| OTEL_SDK_DISABLED | not read by opentelemetry_sdk 0.33 | none | =true: 5 of 5 exported |
| OTEL_EXPORTER_OTLP_RETRY_* or any retry variable | does not exist | the OTel exporter spec defines no retry variable and the SDK env page has none; neither crate greps a RETRY name | spec pages read 2026-10-07 |

Verdict on "some env vars can control it": no variable controls the retries. Retries are a builder-only setting
(`RetryPolicy`), 577 pins them to disabled, and no env var in either crate touches that field. Two env vars do
work in FreeUnit today, OTEL_EXPORTER_OTLP_HEADERS and OTEL_BSP_SCHEDULE_DELAY, and both are undocumented
behaviour that a future explicit setter would silently remove. A user who wants retries today has to patch
lib.rs:230 and :252 and rebuild.

## 3. Timeouts and visibility (question 5)

`with_timeout(10 s)` is the reqwest blocking client's total request timeout (http/mod.rs:247-248), so connect,
send and response are all inside the 10 s: measured 10 s against a listener that accepts and never answers, and
against an unroutable 10.255.255.1 (failed=0 at 3 s, failed=1 at 12 s). For gRPC the same 10 s is tonic's
per-request timeout on a lazy channel (tonic/mod.rs:328-336): measured 10 s against the silent listener. There is no
separate connect timeout; a dead collector costs one 10 s block per batch on the processor thread.

`/status/telemetry` reports `spans.exported` and `spans.failed` (nxt_status.c:167-190, lib.rs:166-199). The
September note "no counter" is about the other loss: spans the SDK drops when its 4096-span channel is full while
the processor blocks. Measured at 2000 rps with a silent collector for 15 s: `/status` said failed=128 (one batch
timed out) while about 29 800 spans were dropped unseen. unit.log gets nothing until exit, when nxt_otel.c logs one
line ("span export to the collector failed, exiting anyway"). The SDK's own warnings go to `tracing`, which has no
subscriber in this crate.

## 4. Where a retry sleeps today, and the non-blocking design

Today: inside `export()`, on the processor thread (retry.rs:172 `sleep_for` under `block_on`, span_processor.rs:790).
The processor does not take the next batch until `export()` returns; new spans queue in the 4096 channel and are
dropped past that. The crate has no detach option: `BatchSpanProcessor` is one worker and the async-runtime variant
needs a tokio runtime for the whole provider and is experimental.

Design (prototyped): a wrapper exporter in src/otel/src/retry.rs that implements `SpanExporter` around the OTLP
exporter, installed only when a retry policy is configured.

- `export()` pushes the batch into a bounded `VecDeque` under a mutex and returns `Ok` at once. The processor never
  blocks, not even for attempt 1 and not for the 10 s timeout of a dead collector.
- One worker thread (`otel-retry`) sends the earliest due batch. On failure it requeues the batch with
  `due = now + min(initial * 2^attempt, max_delay) + jitter` until `max_attempts`; while a batch waits, newer batches
  are sent. The OTLP exporter keeps `RetryPolicy::disabled()`, so one request per attempt.
- Bound: `max_queued_batches`; when full the oldest batch is dropped, counted in `spans.failed`, and logged at most
  once per second with the counts since the last line. Memory is at most `max_queued_batches * batch_size` spans.
- Counters for `/status`: retry attempts, batches dropped, current and max queue depth; the prototype logs them at
  worker stop. A `/status` field needs one more FFI getter next to `nxt_otel_rs_export_stats` (lib.rs:317).
- `force_flush` waits until the queue drains (bounded by 10 s); `shutdown` stops the backoff, makes one last attempt
  per queued batch and joins the worker within the processor's timeout.
- Clone cost: the trait consumes the batch, so a batch that may be retried is cloned once per attempt except the
  last. That is on the worker thread, not on the processor or the router.

Prototype: worktree `wotel` in the session scratch directory
(detached at 872bf041, uncommitted). Files: src/otel/src/retry.rs (265 lines, with comments), src/otel/src/lib.rs
(+18 -7: `mod retry`, processor selection), src/otel/Cargo.toml (+futures-executor), src/nxt_otel.c (log callback,
see below). Policy from env in the prototype only: NXT_OTEL_RETRY_MAX_ATTEMPTS (off below 2), _INITIAL_DELAY_MS,
_MAX_DELAY_MS, _MAX_QUEUED_BATCHES.

Two findings from the prototype that the real change must handle:

1. The Rust side may only call `nxt_otel_log_callback` from the router thread. It used `nxt_thread()->task`
   (nxt_otel.c:843-845); called from the SDK thread the router died with SIGSEGV twice in the first run
   ("process 1720938 exited on signal 11"). The prototype switches it to `nxt_main_log_error()`, which formats and
   write()s without a task. The timestamp still comes from the thread time cache
   (nxt_app_log.c:27-36 `nxt_log_time_handler`), so lines from foreign threads print "1900/01/00 00:00:00". The
   change needs either a time source that does not use `nxt_thread()` or a hand-off of log events to the router thread.
2. A retried batch is a 400 as well as a 503: the inner exporter returns `OTelSdkError::InternalFailure(String)`
   and the status code is only in the text. The real change should classify like the crate does (429/502/503/504
   and no-response retry; 4xx final), by parsing the message or by owning the HTTP call.

### Measurements, prototype versus master

Policy for all prototype runs: 6 attempts, 1 s initial, 8 s cap; queue 8 batches (a, b) or 32 (c).

| Scenario | Master (577) | Prototype, retries on |
|---|---|---|
| (a) collector 503 for 10 s then 200, batch_size 1, 12 requests at 1/s | 12 requests, exported 2, failed 10; the outage spans are gone | 36 requests (12 first attempts + 24 retries), exported 9, failed 3 (queue of 8 overflowed, 3 oldest dropped and logged); first-attempt timestamps 1 s apart throughout, no batch waited for another |
| (b) collector accepts and never answers, batch_size 1, 30 requests at 5/s, 21 s | 3 connections, failed 2, 28 spans still in the SDK queue with no counter | 3 connections, failed 21 (all drops counted), 5 log lines "retry queue full (8 batches): dropped 5 batches, 5 spans", max depth 8 |
| (c) 2000 rps for 15 s, batch_size 128, collector 503 | p50 0.12 ms, p99 0.43 ms, p99.9 1.93 ms; 360 collector requests; failed 29 879 | p50 0.14 ms, p99 1.42 ms, p99.9 4.31 ms; 495 collector requests; failed 26 847 (queue of 32 bounded); 14 log lines |
| (c) same, collector silent | p50 0.13 ms, p99 1.49 ms, p99.9 4.50 ms; failed 128, about 29 800 dropped unseen | p50 0.12 ms, p99 0.87 ms, p99.9 4.86 ms; failed 26 323, all counted |

The p99 spread (0.4 to 1.5 ms) is the same in both directions and is laptop noise: the request path ends a span
with a non-blocking `try_send` in both builds, so a stuck exporter never touches it. What the prototype changes
is span loss during a short outage (a: 9 delivered instead of 2), the bound on memory, and that every loss is
counted and logged. Under a long outage both lose almost everything; the prototype says so in `/status` and unit.log.

## 5. Recommendation

Do (c) for 1.37.1: leave retries off, add a CHANGES line for 577 (it has none), and document in the telemetry
page that a failed batch is sent once and why (the measured serial block). Do (a) for 1.38.0 with the detached
worker, not the crate's in-call retry, which would reintroduce the block that 577 removed. (b), honouring env vars,
is not possible: no retry env var exists, and inventing a vendor one hides a behaviour change from the config API.

Config, 1.38.0:

```json
"settings": {"telemetry": {"endpoint": "http://127.0.0.1:4318/v1/traces", "protocol": "http",
  "retry": {"max_attempts": 4, "initial_delay": 1000, "max_delay": 8000, "max_queued_batches": 32}}}
```

`retry` absent = off (today). `max_attempts` 1..16 (1 means off), delays in ms, `max_queued_batches` 1..1024.
Files: src/nxt_conf_validation.c (a `retry` object in `nxt_conf_vldt_otel_members` :462-483 with four validators,
about 60 lines); src/nxt_router.c (read the four paths next to :3600-3631, add them to `nxt_otel_applied_conf_t`
:2920-2994 so a change rebuilds the exporter, pass a `nxt_otel_retry_conf_t` pointer to `nxt_otel_rs_init`, about
50 lines); src/nxt_otel.h/.c (the struct, the thread-safe log callback, about 30 lines); src/otel/src/lib.rs and
retry.rs (the prototype minus env parsing, plus the error classification and a `nxt_otel_rs_retry_stats` getter,
about 250 lines); src/nxt_status.c (`telemetry.retry: {attempts, dropped_batches, queued_batches}`, about 25 lines;
grep test/ for `telemetry` asserts first, see the extending-an-object trap); docs/unit-openapi.yaml. Total about
450 lines plus tests.

Tests (test/test_otel.py, fake_otlp): (1) `--status 503`, batch_size 1, `max_attempts` 3: requests per failed
batch == 3 and `/status` failed == spans (fails on 577 with 1 request per batch); (2) 503 for 3 s then 200 (a
fake_otlp `--fail-for SECS` option, 15 lines): every span exported, `retry.attempts` > 0; (3) retry absent:
exactly 1 request per batch (the existing test); (4) `max_queued_batches` 2 with a silent collector: `/status`
`dropped_batches` grows and unit.log has the rate-limited line; (5) validation: `max_attempts` 0 and 17 rejected.

CHANGES, 1.37.1 (for 577): "Change: a span batch the collector rejects is sent once. opentelemetry-otlp 0.33
retried it up to 3 times inside the export call and held the next batches back; 1.37.0 sent it once."
CHANGES, 1.38.0: "Feature: the "retry" object in "settings/telemetry". A failed export is retried by a separate
thread with exponential backoff; the batch processor is never held. Dropped batches are counted in "/status".
Off by default."

What the user gains: spans from an outage shorter than the backoff budget (about 15 s with the example policy)
arrive instead of being lost; loss during a longer outage is bounded, counted and logged instead of silent; the
request path is unchanged either way. What it does not give: delivery through a long outage. That needs a
collector-side queue (the OTel collector's `sending_queue` with file storage), not an exporter retry.

## Not verified

- gRPC was measured only for the 10 s block and the 1-request-per-batch case; the prototype runs used HTTP.
- OTEL_EXPORTER_OTLP_INSECURE and OTEL_BSP_MAX_CONCURRENT_EXPORTS were read from source only.
- Latency numbers are from one laptop run per cell; they show no effect, not a precise cost. An ABBA run on the reference bench host
  would be needed before quoting a number.
- The prototype's shutdown path was exercised only by SIGTERM in the test runs, not by a telemetry reconfigure
  while batches are queued.
