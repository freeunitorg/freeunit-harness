# Benchmark methodology

## What it measures

Per build, per scenario: RPS, p50/p99 latency, failed requests, and router
CPU-microseconds per request. `oha` is primary; ApacheBench runs as a secondary
for continuity with older results (`SKIP_AB=1` to drop it).

| Scenario | What it is for |
|---|---|
| `ret200_ka_hi` | keepalive at high concurrency (c=256) — throughput ceiling |
| `ret200_ka_lo` | keepalive at low concurrency (c=8) — listener wake distribution / herd effects |
| `ret200_close` | `Connection: close` (c=16) — accept and teardown path |
| `proxy_ka` | proxy to self (c=64) — the client side of the server |
| `php_ka` | PHP hello app (c=8) — process-tree CPU accounting |

## Knobs

`SERVER_CORES` / `LOAD_CORES` (default `0-3` and `4-7`), `N_HIGH`/`N_LOW`/etc
for request counts, `MAX_FAILED`, `RESULTS` for the output file, `ALLOW_SAME=1`
to permit an A/A run. The full list is in the header of
`tools/bench-engines.sh`.

## Reading the output

**CPU per request is the tighter signal.** RPS on a keepalive scenario is noisy
— the A/A floor is typically several percent — while router CPU-microseconds per
request is stable to about a percent on the close and high-concurrency
scenarios. A change that moves CPU/req but not RPS is still a real change.

**Interleave, and keep the round count even.** The harness alternates A/B and
B/A per round so thermal drift and warmup cannot stay correlated with one label.
With an odd count one build starts first more often, so a position effect does
not cancel.

**Results are only comparable within one methodology.** Kernel version,
liburing version, load generator version and core layout all move the numbers.
The harness header and run directory capture most of it; the kernel and library
versions are on you to record.
