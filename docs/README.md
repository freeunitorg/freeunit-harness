# Base knowledge

How FreeUnit works, written against the source tree, and how this harness is run.

## The server

| Document | What it covers |
|---|---|
| [codemap.md](codemap.md) | where things live in `src/`, and how to find the file you want |
| [style.md](style.md) | what "follow the existing style" means: C conventions, commit format, approach |
| [architecture.md](architecture.md) | processes, ports, shared memory, threads and event engines; where to put a change |
| [http-flow.md](http-flow.md) | one request from accept to response, with the symbols to follow |
| [php-lifecycle.md](php-lifecycle.md) | how an app worker runs your PHP: MINIT once, RINIT/RSHUTDOWN per request |
| [glossary.md](glossary.md) | the vocabulary, including the words that mean something else elsewhere |
| [release-version-sites.md](release-version-sites.md) | every file that carries the release version, how a bump updates it, what check-version.sh covers, and the version strings a bump must never touch |
| [otel-retry-config-2026-10-07.md](otel-retry-config-2026-10-07.md) | OTLP export retries after PR 577: what controls them, and a non-blocking design |
| [reproducible-builds-plan-2026-10.md](reproducible-builds-plan-2026-10.md) | reproducible builds of FreeUnit: findings and plan. Plan, 2026-10-08. |

## CI

| Document | What it covers |
|---|---|
| [ci-rebalance-2026-10-08.md](ci-rebalance-2026-10-08.md) | current: CI cost by workflow, what runs on which event, the recommendations with their status |
| [ci-matrix-audit-2026-10-07.md](ci-matrix-audit-2026-10-07.md) | the earlier audit of the CI matrix: toolchains, runner images, pins; recommendations with their status |

## Agents and the harness

| Document | What it covers |
|---|---|
| [host-roles.md](host-roles.md) | which machine does which job: `tools/hosts`, roles from `run/lab.env`, and why host names never live in tracked files |
| [task-ledger.md](task-ledger.md) | the task ledger: one file per task, `tools/task`, claims, leases, gates, briefs and the Claude Code hooks |
| [agent-messages.md](agent-messages.md) | the line format of the agent message bus (`tools/inbox`) |
| [relay.md](relay.md) | the cross-machine channel: one GitHub issue, `tools/relay`, claim lines and perf requests from machines with no lab |
| [agent-coordination.md](agent-coordination.md) | how agents coordinate: bus decisions, authority, spending, the harness as shared memory, learning from sessions. Draft. |
| [agent-tools.md](agent-tools.md) | agy, dsh and OpenCode next to Claude Code and Codex: install state, instruction and skill discovery, scripted use, models and cost, which tool for which job. Draft. |
| [model-routing.md](model-routing.md) | which LLM gets which job; what may leave Claude (embargoed GHSA, client code). Draft. |
| [review-pipeline.md](review-pipeline.md) | who reviews a change (DeepSeek, Codex, Fable, Sonnet), in what order, cost and skip rules per stage; a request-a-review cheat sheet. Draft. |
| [context-priming.md](context-priming.md) | when to load router + libunit + port whole into one 1M subagent, which pack level for which task, how to brief it; built by `tools/build-pack.py` |
| [public-harness-plan.md](public-harness-plan.md) | plan to publish this harness: scrub inventory, `check-public.sh` spec, agent survey, contribution recipes with token budgets, repository shape, phases. Plan, 2026-10-07. |

## Performance (`perf/`)

| Document | What it covers |
|---|---|
| [perf/perf-lab.md](perf/perf-lab.md) | the perf lab: one command per job (`tools/perf-job.sh`), the box schedule, claim and idle gate, job and TSV format, the verdict from `tools/perf-compare.py`, measured noise floors |
| [perf/perf-plan-2026-10.md](perf/perf-plan-2026-10.md) | the performance plan for October 2026, from the research pages below |
| [perf/freeunit-php-path-2026-10.md](perf/freeunit-php-path-2026-10.md) | the PHP request path: cost per request, and a worker mode |
| [perf/php-worker-mode-api-survey-2026-10.md](perf/php-worker-mode-api-survey-2026-10.md) | worker-mode APIs of other PHP application servers |
| [perf/pr601-chdir-vs-stat-2026-10.md](perf/pr601-chdir-vs-stat-2026-10.md) | PR 601, chdir() versus stat() per request. Rejected. |
| [perf/libunit-message-cost-2026-10-08.md](perf/libunit-message-cost-2026-10-08.md) | libunit message cost: recovering the CPU of the 1.37.0 order fix |
| [perf/otel-unconfigured-overhead-2026-10-08.md](perf/otel-unconfigured-overhead-2026-10-08.md) | the per-request cost of OTel compiled in but not configured |
| [perf/precompute-audit-2026-10-07.md](perf/precompute-audit-2026-10-07.md) | config-derived work done on the request path |
| [perf/loop-invariant-audit-2026-10-07.md](perf/loop-invariant-audit-2026-10-07.md) | loop-invariant audit and plan (`tools/loop-invariant-scan.py`) |
| [perf/lto-pgo-adoption-2026-10-08.md](perf/lto-pgo-adoption-2026-10-08.md) | LTO and PGO: findings, prototype, plan. Parked. Proposed runner change: [perf/lto-pgo-runner-configure-args-ab.diff](perf/lto-pgo-runner-configure-args-ab.diff) |
| [perf/csrc-map.md](perf/csrc-map.md) | the C-source map of router, libunit and port IPC that `tools/build-pack.py` builds |
| [perf/rapira-architecture-2026-10.md](perf/rapira-architecture-2026-10.md) | Rapira 0.9.0 architecture, for comparison with FreeUnit |
| [perf/yii3-benchmark-setup-2026-10-05.md](perf/yii3-benchmark-setup-2026-10-05.md) | how FreeUnit was run in the Yii3 application-server benchmark; stage data in [perf/yii3-benchmark-stages-2026-10-05.csv](perf/yii3-benchmark-stages-2026-10-05.csv) |
| [perf/drupal-benchmark-plan-2026-10.md](perf/drupal-benchmark-plan-2026-10.md) | plan for a Drupal application-server benchmark page: Apache mod_php, nginx+FPM and FreeUnit; workloads, protocol, phases. Plan, 2026-10-07. |
| [perf/review-613-2026-10-08.md](perf/review-613-2026-10-08.md) | review of PR 613, OpenSSL 3.6.5 to 3.5.9 LTS in CI |

**These pages cite file and symbol locations on purpose.** They are meant to be
checked, not believed. Documentation about a moving codebase decays, and the
only defence is making verification cheap — if a claim here disagrees with the
source, the source wins and this should be corrected.

Line numbers drift fastest; symbol names and file paths are the durable part of
a citation. If a line reference is stale but the symbol is right, fix the number
rather than removing the reference.

**Citations here were verified against `freeunitorg/freeunit` `master` at
`02263309`** (2026-07-27). To re-check them after the tree moves, confirm each
cited line still contains the symbol it names:

```bash
sed -n '608p' src/nxt_http_request.c   # expect nxt_http_request_action
```

Two things this check has already caught, so it is worth actually running:

- Over 18 commits of `master`, every *function* citation here held, but a
  citation to a **call site** inside a function drifted by 40 lines. Function
  definitions are stable because code is added between them; a line in the
  middle of a function is not. Prefer citing the function.
- Line numbers taken from a feature branch differ from `master` by enough to
  send a reader to the wrong function while still looking plausible.

Where a fact spans a few lines, cite the range and quote the part that matters —
`conftest.py:760-763` survives edits that a single-line citation would not.
