# Model routing — which LLM gets which FreeUnit job

**Status: draft (06.10.2026), to be refined.** Prices and model IDs come from the
OpenRouter models API on that date (two rows re-fetched 07.10.2026, see the price table) (`https://openrouter.ai/api/v1/models`);
benchmark claims are vendor-reported and unverified. Re-check before relying on
a number — this page decays faster than the rest of `docs/`.

## Hard rule: what may leave Claude

| Material | Allowed models |
|---|---|
| **Embargoed security work** — any GHSA before its advisory is published, its patch, repro and fuzz crash | **Claude only** |
| Client code (the maintainer's other, non-public projects) | **Claude only** |
| Public FreeUnit / Drupal code, public PRs and issues, published advisories | any model below |

The rule covers reviewers too. The Codex app and the Claude Action read every public
PR on open, so an embargoed fix must not open as a public PR. DeepSeek sees only what a
session sends it: send it nothing embargoed.

An unpublished vulnerability leaking is worse than leaking client code. "The
project is open source" does not cover a fix that is not public yet. 1.37.0
alone shipped five GHSAs, so this is the normal case, not an edge case.

## What the work actually looks like (1.36.0 → 1.37.0)

Measured on a fresh clone, 413 commits, +88k / −14k lines:

- ~45 % tests, fuzzing, CI — sanitizer runs (ASan/LSan/UBSan), flaky tests,
  libFuzzer targets.
- ~20–25 % memory-safety hardening — bounds checks on shared-memory records,
  sender authentication of port messages.
- ~15 % HTTP — chunked bodies, ranges, conditional requests, 100-continue,
  compression negotiation.
- the rest: features, concurrency/ordering, build and packaging. PHP is small.

Percentages are keyword estimates over commit subjects, not a classification.

The hard bugs are root-cause analysis of lifetime and ordering across
processes — e.g. `1453d33c` (per-thread compressor reused across responses),
`39700f29` (port messages split between shm queue and socket arrive out of
order), `b12fb677` (`fsync` in the main event loop stalled forks), `7db3eb34`
(raw-pointer walk over untrusted shm records).

`src/` is ~134k lines of C (~1.5–2M tokens): the whole tree does not fit any
context window. For the size of one subsystem pack (router + libunit + port) and
which window it fits, see [context-priming.md](context-priming.md).

## Routing table

| Job | Model | Why |
|---|---|---|
| Embargoed GHSA, client code | Claude (Max) | hard rule above |
| Hard root cause: races, lifetimes, IPC, shm | Claude; second opinion `moonshotai/kimi-k3` or `qwen/qwen3.8-max-0902` | needs the strongest C-systems reasoning |
| Agent loop: build → run under ASan → fix | `z-ai/glm-5.3` via Claude Code (Anthropic-compatible endpoint) | long sessions with huge test output; input $0.07/1M |
| Fuzz targets, test generation, sanitizer-log triage | `qwen/qwen3.8-flash` or `xiaomi/mimo-v2.6-pro` | bulk work; ~$0.01 per review-sized call |
| HTTP/RFC review of a diff | `moonshotai/kimi-k3` or `qwen/qwen3.8-max-0902`, Claude for the final pass | spec-reading accuracy |
| Automated review of public PRs in CI | `qwen/qwen3.8-27b`, open weights, self-hosted | no API key or cost; dense 27B fits one GPU; untested, see the next row for what runs today |
| First review of a public PR diff (every fold) | `deepseek/deepseek-v4.1-flash` via `dsh` | PR 588: 6 findings in 2 comments; under $0.01 of cache read per anchored review; some claims wrong, so verify each |
| Review gate before ready, risky paths | Fable once per PR; Codex app on ready | pipeline, order and skip rules: [review-pipeline.md](review-pipeline.md) |
| Folding findings, running gates, triaging CI logs | Sonnet or Haiku with a fixed command list | no judgement needed; limits in [review-pipeline.md](review-pipeline.md) |
| Cross-subsystem question (router + libunit + port at once): ordering, ownership, teardown | one 1M-window subagent primed with a source pack: Sonnet for flow and invariants, Fable or Opus when the verdict is acted on; never the main thread | measured 2026-10-06: L1 (256k tokens) answered three known-answer invariants correctly; see [context-priming.md](context-priming.md) |

Not used for now: `minimax/minimax-m3` (older and pricier than MiMo for bulk);
MiniMax M3.1-Flash-Preview (27.09.2026) runs only inside MiniMax Code, no API.

## Reference prices (OpenRouter, 06.10.2026, $ per 1M tokens)

| Model | Released | Input | Output | Cache read | Open weights |
|---|---|---:|---:|---:|---|
| `moonshotai/kimi-k3` | 2026-07-16 | 0.69 | 15.00 | 0.23 | yes (2.8T) |
| `qwen/qwen3.8-max-0902` | 2026-09-03 | 2.00 | 6.00 | 0.25 | via `qwen3.8-2.4t-a95b` |
| `qwen/qwen3.8-27b` | 2026-08-14 | 0.43 | 2.55 | 0.085 | yes |
| `qwen/qwen3.8-flash` | 2026-08-26 | 0.15 | 0.47 | 0.016 | no |
| `z-ai/glm-5.3` | 2026-08-18 | 0.07 | 7.00 | 0.065 | — |
| `xiaomi/mimo-v2.6-pro` | 2026-09-21 | 0.43 | 0.87 | 0.0036 | — |
| `deepseek/deepseek-v4.1-flash` | 2026-09-10 | 0.023 | 0.471 | 0.010 | — |

The Kimi K3 and DeepSeek V4.1 Flash rows were re-fetched on 07.10.2026 and had moved
within one day; the other rows matched. `dsh` calls the official DeepSeek API, so its
bill can differ from the OpenRouter price.

Rough cost of one agent session (5M input, 80 % cached, 200k output), at the 06.10
prices: MiMo $0.62, GLM-5.3 $1.73, Qwen3.8 Max $4.20, Kimi K3 $4.71 ($4.61 at the
07.10 price). Claude tiers are on a plan, so [review-pipeline.md](review-pipeline.md)
counts them in tokens and minutes, not dollars.

## Open questions

- Bake-off: run the same real FreeUnit bug through Claude, Kimi K3, Qwen3.8 Max
  and GLM-5.3; record which found the root cause and at what cost.
- Tool-calling reliability over long sessions is unmeasured for every model.
- Where does a self-hosted Qwen3.8 27B run, and on whose hardware?
- How is "embargoed" marked in practice — branch naming, a label, a separate
  worktree — so an agent can tell before it sends code out?
