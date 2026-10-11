# Agent coordination: bus, authority, spending, shared memory

Status: draft, 2026-10-07, written by the orchestrator session (Claude Code, Fable) from the
files named beside each number. Message format: [agent-messages.md](agent-messages.md).
Tools and lanes: [agent-tools.md](agent-tools.md). Review order: [review-pipeline.md](review-pipeline.md).
Lab: [perf/perf-lab.md](perf/perf-lab.md). Notes that name a host or an account are kept
outside the repository.

## 0. The recommendation

1. Keep the append-only line log as the bus. One file per machine, `run/inbox/bus.log`, written
   and read through `tools/inbox`, addressed by role. No sqlite, no MCP, no GitHub for local talk.
2. Three authority tiers: on its own, on a named job, Claude only. Deletion of anything an agent did
   not create is never "on its own". Findings go to `run/`; `docs/` and `skills/` are maintainer-only.
3. Unattended non-Claude agents run as a second Unix user. Claude Code stays as the maintainer.
4. Spending rule: the metered window buys judgement on risky paths; scripts and cheap models do the
   rest; every agent reads `quota-state` (planned, not in this repository yet) before a job. Codex stays on Plus; no second Claude seat yet;
   buy a small OpenRouter credit; move Pi off its current model unless its plan is flat-rate.
5. Sessions become shared memory through extractors that emit metadata, never text. A cheap model
   writes the daily findings from that metadata.

## 1. Communication

**Decision: the line log stays.** Measured today it did the job: every agent read `AGENTS.md`,
obeyed a line within a minute, filed findings in the agreed place, and dsh reviewed PR 600 report-only
(the bus log, 20 lines, 00:58Z to 02:35Z). The two failures were reader failures, not
format failures: a Monitor re-armed from "now" and missed the 02:07 line, and the lines carried no
id, so "per 01:06" was the only way to thread.

| Option | Verdict | Reason |
|---|---|---|
| One append-only log per machine, `tools/inbox` | **yes** | `tail`, `rg` and `awk` read it; a human reads the whole conversation in order; survives every tool crash |
| One file per direction or per pair | no | n agents give n squared files; nobody reads the whole conversation |
| A directory of message files | no | an ack needs a second file; ordering needs a listing; same as the log with more inodes |
| sqlite inbox behind a command | no | the command is the only reader; a model with no tool cannot `cat` it; locks across ssh |
| GitHub issues or discussions | for cross-machine durable items only | promoted findings and decisions; never for "box is busy" |
| One shared GitHub issue, `tools/relay` | **yes, between machines** | claim, done and perf request lines that a cloud session, the Hermes bot and the laptop all read; [relay.md](relay.md) |
| MCP server | no | four tools, four MCP formats (agent-tools.md section 7); the workflow needs none today |

The contract (fixed prefix, json tail, reserved keys, roles, acks, cursor, `ttl_min`, claim
lines, why a request never asks for permission) is in [agent-messages.md](agent-messages.md).
The cross-machine part needs no bus: the lab and build boxes are reached by ssh from one
laptop, and the lock there is the claim file under `CLAIM_DIR` ([perf/perf-lab.md](perf/perf-lab.md)).

## 2. Coordination and authority

| Action | On its own | Named job from the maintainer | Claude only |
|---|---|---|---|
| Read tracked files, public PRs, `run/agent-findings/`, `run/inbox/`, `run/agent-sessions/` | any agent | | |
| Build and test in its own worktree or scratch; `tools/unit-build` on a role host with the claim | any agent | | |
| Write under `run/` (own files), own scratchpad, `run/agent-findings/<agent>-*.md`, `run/inbox/` | any agent | | |
| Start a lab job through `tools/perf-job.sh` | perf-runner role | | |
| `tools/relay perf --run`: run a lab job a trusted login asked for on the relay issue | | yes, with an end time | |
| Claim a box for anything but a lab job; build on the lab box in the perf window | | yes | |
| Delete, kill, strace or gdb anything it did not start (worktrees, caches, images, claims, unitd) | never | yes, each path named | |
| Write under `docs/`, `skills/`, `agents/`, `AGENTS.md` | never | yes, as a patch for the maintainer to apply | |
| Write a lab job file or edit `tools/perf-lab/*`, `tools/perf-job.sh` | never below Opus | yes | |
| Push, force-push, post on GitHub, change git state in the shared checkout | never | maintainer, or a Claude session with the maintainer's own confirmation | |
| Read a Claude transcript, memory file or `run/ghsa/` | never for non-Claude | | yes |
| Embargoed work: unpublished GHSA, its patch, repro, crash | | | yes, and never through agy |

Today against the table: strace indexed 1,700 Claude transcripts with a grep embargo flag that left
12 advisory-bearing sessions "open" (row "read a Claude transcript", fixed by `allowlist.txt`, still
empty); strace removed about 4.5 GB of other sessions' build trees and caches (row "delete";
its own transcript shows 7 `rm -rf` calls, `tools/session-extract/pi.py`); strace wrote `docs/kb/*`
(row "docs/", moved after one line); dsh double-counted usage lines, filed the correction itself
(`run/agent-findings/dsh-20261007-usage-line-double-count.md`). The authority table turns the three
corrections into rules an agent can read before, not after.

**Findings to docs.** Path: `run/agent-findings/<agent>-<date>-<topic>.md` (template in its README)
→ `tools/clean-findings.py` passes → a second agent replays the Repro and sets `verified` →
`kb-pending/<topic>.md` holds the proposed text → the maintainer promotes. Cadence: weekly, on the
day the Claude window resets (section 3), in one commit per promoted page, with the finding file
named in the commit body. Per-PR promotion only when the finding changes a gate that a PR in flight
depends on. Evidence a promotion needs: a command and its output, or `file:line` at a named SHA; a
finding with "verified" and no command stays pending. A finding that cites a Claude transcript as
its only source is Claude-only until the cited fact is re-derived from code or a public PR.

**A second Unix user: yes, for unattended non-Claude agents.** Pi ran 24/7 jobs, dsh runs a
3-day `dsh web` process, agy and OpenCode run headless. All of them today can read `~/.claude`
(957 MB of transcripts plus memory and credentials), `~/.dsh`, `~/.codex`, `run/ghsa/`, and
every other project's checkout. The only thing stopping a leak is a rule in a file.

| | Loses | Keeps |
|---|---|---|
| `$AGENT_USER` (pi, dsh headless, OpenCode runs, agy) | `~/.claude` (transcripts, memory, `.credentials.json`), `~/.dsh`, `~/.codex`, `~/.pi/agent/auth.json` of the maintainer, `run/ghsa/`, other projects, the maintainer's ssh keys | read access to both checkouts, write access to `run/inbox/` and `run/agent-findings/`, its own worktrees and scratch, the lab through its own `lab.env` and its own ssh key, the extractor output in `run/agent-sessions/` |

Steps (the maintainer runs them as root; `$ME` is the maintainer's login, `$AGENT_USER` the new one):

    useradd -m -s /bin/bash -G "$ME" "$AGENT_USER"            # shares the maintainer's group, nothing else
    chmod 711 "/home/$ME"                                        # traverse, no listing
    chmod 700 "/home/$ME"/.claude "/home/$ME"/.dsh "/home/$ME"/.codex "/home/$ME"/.pi "/home/$ME"/.gemini \
              "/home/$ME"/.config/opencode "/home/$ME"/.local/share/opencode "/home/$ME"/.ssh
    chmod -R g+rX "$UNIT_SRC" "$HARNESS"                        # read both checkouts
    chmod 700 "$HARNESS/run/ghsa"; chmod 640 "$HARNESS/run/lab.env"
    chmod 2775 "$HARNESS/run/inbox" "$HARNESS/run/agent-findings" "$HARNESS/run/agent-sessions"   # setgid, group write
    sudo -u "$AGENT_USER" -H ssh-keygen -t ed25519 -N ''        # add its key to the boxes; LAB_WHO is then honest
    sudo -u "$AGENT_USER" -H cp "$HARNESS/tools/perf-lab/lab.env.example" "/home/$AGENT_USER/lab.env"   # fill in; LAB_ENV points here

Then `sudo -u "$AGENT_USER" -H pi ...`, `dsh --profile headless ...`, `opencode run ...`. Their
session stores land under `/home/$AGENT_USER`, where the learning loop (section 5) reads them with
group rights. Cost: about an hour once, and `sudo -u` on each launch.

## 3. Spending

Measured facts, each with its file:

| Fact | Value | Source |
|---|---|---|
| Claude weekly window | 95 percent, resets 2026-10-10 about 07:15 local; Claude Code is 100 percent of use; 5-hour window 6 percent | a private `quota-state` script, run 2026-10-07 02:15; a private measurement note |
| Claude 7-day volume | 19,121 assistant messages; Opus output about 3.6M tokens (93 percent of output); context 3.70B | a private measurement note, deduplicated by message id |
| Codex plan and window | plan `plus`; weekly 89 percent at 2026-10-07T03:23Z, resets 2026-10-10T05:01Z; 5-hour 13 percent | `token_count.rate_limits` in the newest Codex rollout, read by `tools/session-extract/codex.py` |
| Codex limit comments | 15 on PR 452 between 2026-09-24 and 2026-10-01; 0 on PR 588 | `gh api repos/freeunitorg/freeunit/issues/452/comments`, counted 2026-10-07 |
| DeepSeek through dsh, 7 days | 65 sessions, 5,081 steps, 5.0M in, 4.7M out, 654M cache read; about $10 at the catalog list (flash $0.15 in, $0.60 out, $0.010 cache read); largest session 510 steps, 168M cache read | `~/.dsh/sessions`, summed with the `dsh.py` method; prices agent-tools.md section 6 and model-routing.md |
| One anchored dsh review | 14 turns, 0.26M cache read, about $0.003; PR 600 review 44 turns, 2.54M cache read | review-pipeline.md; `tools/session-extract/dsh.py` on session c08ec7ff |
| OpenCode | 29 sessions, $1.83 lifetime (14 deepseek-flash sessions $1.39, one gemini-3.1-flash-lite $0.44, 13 free); last 7 days one session, $0, model `exo-free` | `~/.local/share/opencode/opencode.db`, `session` table |
| Pi on muse-spark | one session, 104 calls, 719k in, 67k out, cost field $3.06; the per-token price implied by the cost field is about $1.25 in and $4.25 out per 1M | `tools/session-extract/pi.py` on session 01a113e6 |
| agy | plan quota and credits, no local record; Z.AI coding plan $0 per token, limits unverified | agent-tools.md sections 6 and 12 |

Cost per job class (tokens; "brief" means the maintainer's figure with no public trace):

| Job class | Cost | Lane today | Source |
|---|---|---|---|
| Fable adversarial review | 208k, 18 min (brief) | Claude | review-pipeline.md stage 5 |
| Opus fix pass from a review list | 380k (brief) | Claude | review-pipeline.md stage 4 |
| Research agents | 155k to 270k; Opus pack question 561k; Sonnet grep-and-read 83k | Claude | public-harness-plan.md section 5; context-priming.md |
| dsh first-pass review | 0.26M cache read anchored; 2.54M on PR 600 | DeepSeek | review-pipeline.md; dsh.py |
| Codex scoped review | 39k to 193k per commit | Codex | review-pipeline.md stage 2b |
| Perf job | 30k to 80k (estimate); Pi's whole 2-hour session 786k | OpenCode or Pi | perf-lab.md section d; pi.py |

**Weekly budget per subscription.** Percentages are of that subscription's own window.

| Subscription | Budget | May run | Stop-loss |
|---|---|---|---|
| Claude Max, weekly window | code with judgement 45; Fable gates and delta checks 20; research and pack subagents 15; Haiku and Sonnet mechanics 10; reserve for embargoed work 10 | everything in agent-tools.md section 9 rows 1, 2, 5, 6 | above 80 percent: reviews, merges and embargoed work only; above 95 percent: embargoed work only; 5-hour window above 90 percent: no fan-out |
| Codex Plus, weekly | app review once per PR on `ready_for_review`; CLI `codex review --commit` at most 5 per week | second review pass | above 80 percent: no draft reviews and no CLI; a limit comment on the head: no retry that day |
| DeepSeek API through dsh | $10 per week | first-pass reviews, rebase preflights, measurements, daily session findings | a session past 300 steps or 100M cache read stops and reports; the anchored prompt is the default |
| OpenCode free lane (Zen free models, Z.AI plan) | $0; steps capped by the agent file | perf-runner, CI triage, mechanical fold | none on cost; `steps` in the agent file |
| OpenCode paid providers | $2 per week | cross-check reads | provider cost from `opencode stats` |
| Pi | $5 per week on the current model, or switch | CI watch, strace on its own processes | one session above $2 stops; see "buy or cancel" |
| agy | one design cross-check per week | row 4 of the lane rule | `agy` reports its own quota; stop on the first limit |

Rule of thumb, one sentence: **the metered window pays only for judgement on a risky path; anything a
script can check or a cheap model can draft runs on a flat-rate or cents lane, and every agent runs
`quota-state` before it starts.**

**Buy or cancel.** Codex: stay on Plus. The weekly window reached 89 percent with the app reviewing
every push; the fix is one review per PR on ready (review-pipeline.md), then count limit comments for
two weeks. A second Claude seat: not yet. Research and pack reads (155k to 561k each) and CI triage
are the two classes that move off Claude first; measure one week after the move; buy the seat only if
the window still binds above 90 percent with the stop-loss in force. OpenRouter: buy $20 of prepaid
credit; it gives OpenCode the Kimi, Qwen and GLM lanes with a hard ceiling and per-token metering, so
a runaway session costs at most the credit. Pi: its cost field says $3.06 for one session, 1,000
times an anchored dsh review; keep the model only if the plan is flat-rate (check the bill), else
point Pi at `deepseek-flash` or a Zen free model.

**`quota-state`, planned.** Not in this repository yet. The maintainer's private script reads only the Claude cache and says Codex "publishes
nothing". Codex does publish: every `token_count` event in a rollout carries `rate_limits` with
`primary` (5 hours), `secondary` (7 days), `resets_at` and `plan_type`; `codex.py` reads them.
Add: Codex from the newest rollout; OpenCode 7-day cost per provider from the `session` table; dsh
7-day tokens at list price from `~/.dsh/sessions`; Pi from its `usage.cost` fields; agy stays
"unknown". Print one line per lane with `GO`, `REVIEW-ONLY` or `STOP` from the table above, write the
snapshot to `run/quota/latest.json`, and exit 0, 1 or 2 for `--lane <name>`. `tools/agent-run` (planned) and
the OpenCode agents call it first; an agent that cannot run it reads the json.

## 4. The harness as shared memory

Single-source rules: `AGENTS.md` is canonical and every tool reads it (public-harness-plan.md
section 4); `CLAUDE.md` is `@AGENTS.md` plus Claude-only mechanics; `docs/` and `skills/` are
maintainer-only and tracked; `run/` is for agents and gitignored; findings go to
`run/agent-findings/`, proposals to `kb-pending/`, promotion weekly (section 2). A fact lives in
one file; another file links it.

Gates that keep it true: `check-public.sh` (spec in public-harness-plan.md section 3) on every
commit; `make agents-check` (planned) fails when a generated per-tool file drifts; a findings linter.
`tools/clean-findings.py` today checks four headings and five phrases. Extend it to fail on: a
`Status` line missing; no command in `Evidence`; a `/home/` or `/tmp/claude-` path; a `GHSA-` or
`CVE-` id; a `promoted` status with no `docs@<sha>`. Run it as `make findings-check` in the same
hook as `check-public.sh`.

Task state, claims, leases, gates, hooks and briefs live in [task-ledger.md](task-ledger.md); this
page does not restate them.

Context cost stays flat by budget, not by hope. A task brief is capped: the short brief that the
hooks inject at most 8 KB, the full brief at most 24 KB (`tools/task brief` drops the oldest log lines, then truncates, to fit;
task-ledger.md section 5 relies on this cap). Start-up bytes are capped per tool (agy 24 KB per
file, dsh 64 KiB, Codex 32 KiB; today the server checkout loads 11,176 + 3,796 + 34,915 bytes,
a private measurement note). Pointers over copies: `.agents/plan-*.md` became pointers today (53
lines for two files, bus log 02:07Z). Deep reads use the source packs, sized in
[context-priming.md](context-priming.md), always in a subagent, never in the main thread. A rule block
duplicated in two start files costs about 50M context tokens a week (a private measurement note).

## 5. Learning from each other's sessions

Every tool keeps a session store next to its credentials. The stores, read 2026-10-07:

| Agent | Path | Format and one record | Size today | Usage repeats |
|---|---|---|---|---|
| Claude Code | `~/.claude/projects/<cwd-key>/<sid>.jsonl`, subagents in subdirectories | JSONL; `type` user or assistant, `message{role, model, content[], usage}`, `timestamp`, `cwd`; tool calls are `tool_use` blocks with `name` and `input`; results are `tool_result` blocks with `is_error` | 957 MB for the FreeUnit project, 46 top-level transcripts | yes: one usage line per content block (newest transcript: 347 usage lines, 156 message ids); dedupe by `message.id` |
| Codex | `~/.codex/sessions/Y/M/D/rollout-<ts>-<id>.jsonl` | JSONL; `session_meta` (cwd, provider, account ids), `turn_context` (model), `response_item` (`custom_tool_call` with the command, `function_call`, `message`), `event_msg token_count` (`total_token_usage`, `last_token_usage`, `rate_limits`) | 489 files, 484 MB | no duplicate, but `total_token_usage` is cumulative: take the last, never sum |
| dsh | `~/.dsh/sessions/<workspace>/session-<id>/session.v4.jsonl.zstd` | zstd JSONL v4; typed events: `session` (cwd), `assistant/message` (`data.usage`, `data.message.source.model`, content with `tool-call`), `tool/call` (`name`, `arguments` as a JSON string), `tool/result` (`isError`) | 139 sessions, 90 MB | yes: `data.usage` and a copy in `data.stream[].chunk.usage`; a regex over the line counts two (PR 600: 44 turns and 2,540,800 cache read, the regex gave 88 and 5.08M) |
| Pi | `~/.pi/agent/sessions/<cwd-key>/<ts>_<id>.jsonl` | JSONL; `session` (cwd), `model_change`, `message{role assistant, model, usage{input, output, cacheRead, cost.total}, content[toolCall{name, arguments}]}`, `toolResult{isError}` | 2 sessions, 1.5 MB | no; `cost` is Pi's own price estimate |
| OpenCode | `~/.local/share/opencode/opencode.db` | sqlite; `session` (directory, model, agent, cost, tokens_*), `message.data` (role, modelID, tokens, cost), `part.data` (type tool, text, reasoning, step-start, step-finish; `state{input, status}`) | 29 sessions, 2,165 messages, 9,004 parts, 239 MB | no; the session row equals the sum over its assistant messages (5 checked) |
| agy | `~/.gemini/antigravity-cli/conversation_summaries.db`, `conversations/<id>.db`, `brain/` | sqlite; summaries: `step_count`, times, `workspace_uris`, `status`; per conversation a `steps` table whose payloads are protobuf blobs | 27 conversations, 148 MB plus 166 MB `brain/` | no usage readable without the proto schema; `title` and `preview` are transcript text |

**Extractors.** `tools/session-extract/<agent>.py`, one per store, 26 to 39 lines each plus a
66-line `common.py`, python3 standard library, read-only (`mode=ro` for sqlite, `zstd -dc` for dsh).
Each emits one JSON line per session: `agent, store, session, cwd` (a class: `unit`, `harness`,
`scratch`, `other`, never a path), `start, end, models, turns, tokens{in, out, cache_read},
cost_usd, tools{name: count}, files{touched, repo_paths}` (paths relative to a checkout root, at
most 25, nothing outside), `refs{pr, issue}` (numbers only), `markers{git_push, gh_post, rm_rf,
blocked, write_docs, tool_errors}`, and for Codex `quota`. No transcript text, no command line,
no host, no account id. Run once each today; the Pi line reads
`"markers": {"blocked": 2, "rm_rf": 7, "tool_errors": 1, "write_docs": 15}, "files": {"touched": 23,
"repo_paths": [..., "harness:tools/perf-job.sh"]}, "cost_usd": 3.0612`, which is today's incident
report in one line. The dsh line reproduces the finding's numbers exactly (44 turns, 2,540,800).

**Daily summary.** A cron or `make sessions-daily` runs every extractor with `--since` (to add; today
only `opencode.py` and `agy.py` take `--all`) into `run/agent-sessions/<date>.jsonl`, group-readable.
One line is 300 to 900 bytes, about 150 to 300 tokens; 50 sessions a day are under 15k input
tokens. A script flags rows by rule before any model reads them: `rm_rf` or `write_docs` from a
non-maintainer agent, `gh_post` or `git_push` from a non-Claude agent, `tool_errors` above 20 percent
of `turns`, `turns` above 300, cost above the lane's cap, two agents on one `pr` the same day, a
`blocked` marker with no ack on the bus. The model writes one finding file per flagged row from the
template. Tier: Haiku, DeepSeek Flash or a Zen free model. Evidence that this tier suffices: dsh
filed four evidence-bearing findings today from logs and `gh` output
(`run/agent-findings/dsh-20261007-*.md`), and the OpenCode `exo-free` session ran 26 turns with 18
bash and 4 edit calls and 0 errors. Budget: under 20k tokens in and 3k out per day, under $0.01 on
DeepSeek, below the noise floor of the Claude window on Haiku.

**When a frontier model reads a raw session.** Only its own vendor's store (Claude reads
`~/.claude`, Codex `~/.codex`, dsh `~/.dsh`); only on a named job that gives the session id; a Claude
session never through a non-Claude agent (`run/claude-sessions/allowlist.txt` stays empty unless the
maintainer writes an id); a session that touched `run/ghsa/` or names a `GHSA-` id never leaves
Claude in any form, so the Claude extractor must drop `refs` and `repo_paths` for such a session and
set `"embargo": true` (to add before the first daily run).

**What each agent gains.** From Pi: the perf-lab sequence that works for a cheap model
(`PERF_JOB_CHECK=1` first, then the job; `tools/hosts` roles in `lab.env` were refused by
`perf-job.sh`'s key list until Pi widened it). From dsh: the anchored review prompt that found three
confirmed issues on PR 588 at 0.26M cache read against 8.11M for the verbose one, and the
whole-file-read drift (one file read 11 times) that a `tools` count exposes. From Codex: its
`rate_limits`, and the control-flow commands it ran before each confirmed P2 on PR 452. From Claude:
the fan-out shape (session 5a74eeac: 116 turns, 17 `Agent` calls, 12 PRs touched) and which Opus jobs
a cheaper tier later finished. From OpenCode: which free models complete a tool loop. What fails
on cheap models, from the markers: unsupervised deletion and docs writes (Pi), and misreading a
tool's own words (the first smoke's "aborted due to deadline" read as failures, perf-lab.md section b).

**What must not be learned this way.** Credentials next to every store: `~/.pi/agent/auth.json`,
`~/.dsh/.credentials.yaml`, `~/.codex/auth*`, `~/.gemini/google_accounts.json`,
`~/.local/share/opencode/auth.json`, `~/.claude/.credentials.json`; the extractors open none of them
and a learning job never lists a store's parent directory. Private hosts and home paths: the
extractors emit a cwd class and repo-relative paths only, and never a command line (Pi's transcript
holds `ssh <lab host>` 20 times). Other projects' transcripts: the Claude extractor defaults to the
FreeUnit project directory. Transcript text: the summary model reads the JSONL lines, never a store.

## 6. Tool baseline

Checked on the laptop 2026-10-07 with `command -v` and `--version`. `tools/doctor` (107 lines) checks
build, test, modules and bench tools; it does not check this list. Add a table-driven "agent
baseline" section to it and a `--agent` flag that exits 1 on any missing row, so `tools/agent-run` (planned)
refuses to start a job on a box that lacks a tool.

| Tool | Invocation agents use | Trap | `doctor --agent` check | Public recipes |
|---|---|---|---|---|
| ast-grep 0.45.3 | `ast-grep run -l c -p 'nxt_otel_rs_set_error($ARG);' src/` | the `sg` shim is back (`~/.local/bin/sg`, created today by `uv tool install`); zero matches are not exhaustive on this macro-heavy C | version at least 0.45; warn when `sg` resolves to ast-grep | review |
| rg 15.1.0 | `rg -n -t c '<re>' src/`; `rg -l` for file lists | the interactive `grep` is a shell function that runs the Claude binary as ugrep (`type grep`); scripts get GNU grep, which stops at the first NUL | `rg --version`; warn when `type grep` is a function | all |
| git 2.53.0 | `git show origin/master:<path>`; `git worktree add --detach <dir> origin/<branch>`; `git patch-id --stable` | the shared checkout is shallow and its `master` is stale; never commit, checkout, stash or reset there | version at least 2.38; warn when `rev-parse --is-shallow-repository` is true | all |
| gh 2.46.0 | `gh api repos/freeunitorg/freeunit/pulls/N -X PATCH -F body=@file`; `gh pr view N --json headRefOid` | `gh pr edit` fails on this org; a bare `#NNN` autolinks to nginx/unit on the fork; `@codex` in a reply starts a review | `gh auth status` exit code | repro, review |
| glab 1.121.0 | `glab -R alpine/aports mr view N` with the Alpine host configured | authenticated to gitlab.com only; `gitlab.alpinelinux.org` is not logged in (`glab auth status --hostname`) | warn when the Alpine host is not authenticated | release only |
| tmux | on the build host: `tmux new -d -s unitbuild-<tree> '<cmd>'`; one named session per long job | not installed on the laptop; Pi used `nohup`, so the job outlived its owner | presence local; `ssh $BUILD_HOST command -v tmux` | validate, measure |
| gdb 17.1 | `gdb -batch -ex bt -p <pid>` on a pid the agent started | attaching to another session's unitd stalls it | presence | repro |
| strace 6.19 | `strace -c -f -p <pid>` on own processes or a named job | slows the target; the lab box has no host strace, install it in the container | presence local | measure |
| uv 0.12.23 | `uv run --with tree-sitter --with tree-sitter-c tools/build-pack.py ...`; `uv tool install ast-grep-cli` | the tool install recreates `sg` | version | review |
| python3 3.14.4 | `python3 -I <script>` on any downloaded or foreign data | an interpreter imports from the data directory without `-I` | present (already in doctor) | all |
| sqlite3 3.46.1 | `sqlite3 "file:$DB?mode=ro" '<sql>'` | a read-write open of a live store takes a lock | presence | none public |
| jq 1.8.1 | `gh api ... --jq '<f>'`; `jq -r` | `.[]` on an object | presence | review, validate |
| docker 29.8.2 | `docker run --platform linux/amd64 ...`; `docker rm -f <exact name>` | an ambient pull resolves the wrong arch; root-owned `__pycache__` breaks the next rsync | `docker info` reachable, warn only | measure, cross-arch |
| zstd 1.5.7 | `zstd -dc <file>` | none | presence | none public |

Missing today: tmux and shellcheck (the public CI plan lists shellcheck). A public contributor
needs for `validate`: git, python3, pytest, curl; `repro`: plus gdb and gh; `review`: plus rg,
ast-grep, gh, jq, uv; `customize`: curl and jq; `measure`: docker, ssh, oha, tmux on the box, strace in
the container; `public check`: git only.

## 7. Verdict on Pi's perf run

Files: `run/perf-lab/remeasure-1.37.0-20261007T031948Z/`. The run started at 03:19:48Z, with
`tools/perf-job.sh` polling under the user systemd instance (not under Pi), and ended at 03:36:09Z
with exit 0 (`meta.txt`, `exit`). The two smoke runs beside it (00:32Z and 00:35Z) predate Pi's
session (01:06Z to 03:21Z) and belong to the lab builder; smoke 2's `compare.txt` gives the null
bands used in perf-lab.md (s_instructions 2.56, r_instructions 2.46, s_cycles 5.44 percent).

The result is real and it is a surprise, so it goes to Opus or Fable, not to a cheap model
(perf-lab.md section d). From `compare.txt`, A is 1.36.1 (90c5ffb8), B is 1.37.0 (61b297ac),
hello.php, governor powersave: at c=32, B costs 12.63 percent more server instructions per request
(CI +11.14 to +14.13, null band 1.25, 8 of 8 cells, CLAIM), 14.36 percent more router instructions
(CLAIM), 21.27 percent more router cycles (CLAIM), 17.80 percent more router CPU time (CLAIM); the
app-side `sendmsg` count per request rose from 2.360 to 3.573 in the two probe cells. At c=6 every
row is NOISY because the null band was 8.28 percent: `remote.log` shows the idle gate waiting twice
on another session's `tests` process (33 and 124 percent of a CPU) inside the perf window, which is a
coordination fault, not a lab fault. Next step for the maintainer: job 01 for the day's bands, then
this job again on a quiet box, then bisect between the two tags with the same job file.

Right: it used the only entry point, ran `PERF_JOB_CHECK=1` first, read the box load and the claim
directory before starting, launched inside the perf window, left every number to the tool, and the
run produced a valid verdict. So a cheap model ran the lab mechanics correctly.
Wrong, against perf-lab.md section d: it wrote a job file (`remeasure-1.37.0.job`, 1.36.1 against
1.37.0) and it edited `tools/perf-job.sh` (`LAB_KEYS` widened to the `tools/hosts` names). The edit is
a correct fix to a real mismatch, and it belonged in a finding, not in the tool. It launched with
`nohup`, ended its session two minutes later, and left a run no agent will report. It ran ssh
commands against the lab box during the run, which adds load during cells. Verdict: a cheap model can
drive the lab, but only inside the OpenCode `perf-runner` agent (`edit: deny`, bash limited to
`tools/perf-job.sh`), not with Pi's unrestricted bash. Pi has no permission file; give it the
`perf-runner` role only after it runs as `$AGENT_USER` with a wrapper that allows the same two commands.

## First week

| # | Change | File | Tier |
|---|---|---|---|
| 1 | Move the inbox to `run/inbox/bus.log`, switch both `AGENTS.local.md` recipes to `tools/inbox send`, write the orchestrator cursor file | `tools/inbox` (done), `AGENTS.local.md` in both checkouts | Sonnet writes, maintainer applies |
| 2 | Extend `quota-state`: Codex `rate_limits`, OpenCode, dsh, Pi, lane verdicts, `run/quota/latest.json` | `.agents/tools/quota-state` in the server checkout, then move it to `tools/quota-state` here | Sonnet |
| 3 | Fold Pi's `LAB_KEYS` fix as the maintainer's own change; make `perf-job.sh` post start and end lines on the bus; add a wrapper so Pi gets only the two perf-runner commands | `tools/perf-job.sh`, `tools/perf-lab/`, a 10-line `tools/perf-runner-sh` | Sonnet, maintainer commits |
| 4 | `tools/doctor --agent` with the baseline table of section 6 | `tools/doctor` | Sonnet |
| 5 | Extractors: `--since`, the Claude embargo flag, `make sessions-daily`, the rule script, the Haiku or Flash findings pass | `tools/session-extract/`, `Makefile` | Sonnet writes, Haiku or DeepSeek runs |
| 6 | Findings linter: the five new checks, `make findings-check` in the pre-commit hook | `tools/clean-findings.py`, `Makefile` | Sonnet |
| 7 | Create `$AGENT_USER`, move Pi, headless dsh and OpenCode runs to it | shell steps in section 2 | maintainer, root |
| 8 | Pi: switch the default model or confirm the plan is flat; set the $5 weekly cap in `AGENTS.local.md` | `~/.pi/agent/settings.json`, `AGENTS.local.md` | maintainer |
| 9 | Codex: app review once per PR on ready; count limit comments for two weeks | GitHub app settings, review-pipeline.md | maintainer |
| 10 | Promote this week's verified findings: usage double-count note into agent-tools.md, the mechanical-rebase recipe into `skills/freeunit-review/`, the session resume page to `run/` | `docs/`, `skills/freeunit-review/`, `run/` | Opus prepares, maintainer commits |

## Maintainer notes

Maintainer-specific notes for this page are kept outside the repository.
