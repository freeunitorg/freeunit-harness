# Task ledger: one file per task, the bus grammar for its history

Status: proposal, 2026-10-08. Companion of [agent-coordination.md](agent-coordination.md)
(bus, authority, learning loop) and [agent-messages.md](agent-messages.md) (line format).
Items 1 to 3 of section 12 are built (2026-10-08): `tools/task`, `inbox send --task`, the
`task` key, and the hooks in `tools/hooks/`. The maintainer enables the hooks with
`tools/hooks/install --apply`. Section 13 lists where they differ from this text. The rest of
section 12 is not built.

## 0. The recommendation

Keep the bus as the transport and add a second view of the same lines: one file per task
under `run/tasks/`, whose body is the task's own history in the bus line grammar, and whose
frontmatter holds the only mutable state that matters (owner, lease, status, gates, context
pack, links). A 300-line `tools/task` writes both views. Claude Code sessions feed the ledger
through hooks, so a Claude session records its claims, pushes, reviews and hand-offs without
remembering to; every other agent uses the CLI, and the nightly session extractors backfill
what an agent forgot. A task file replaces the session as the unit of memory: a reviewer or
a fresh session reads `task brief <id>` instead of a transcript, a resume note or a peer's
answer.

What changes for a Claude session on day one: it starts with its claimed tasks and the
ready queue already in context; it cannot push without a lease that matches the remote head;
each turn's first line lands in the task log; after compaction the brief comes back by
itself; its subagents get the task card in their prompt; it hands a task over with one
command and a "next" line, and the next session, Claude or not, continues from there.

## 1. The problem, in this week's incidents

- A peer session had to ask every live session whether anyone owned PR 588 before touching
  it, because ownership lived only in transcripts and in a memory file one session wrote.
- PR 588 was rebased twice by two sessions within a day. The extractor rule "two agents on
  one `pr` the same day" exists, but it runs after the fact and nobody reads it in time.
- Three review rounds on one plan re-reported items the previous round had already resolved,
  because each reviewer started from the files, not from the findings and their outcomes.
- Sessions end on context. The hand-written resume notes (a resume page, the memory
  "live state" entries, `RESUME-*.md` files in the server checkout) are stale within a day,
  readable by Claude only, and written by the session that is about to die.
- A fresh session spends its first 8 to 15 tool calls re-deriving state: the memory index, a
  resume note, `git ls-remote`, `git worktree list`, the peer list, a question on the bus.

The cost is paid in the metered window and in wrong starts. The fix is not more notes; it is
a place where the state of a task is written as it changes, by whoever changes it.

## 2. Decisions

| Decision | Choice | Rejected | Reason |
|---|---|---|---|
| Source of truth | plain files under `run/tasks/<kind>/<key>.md`, one per task | SQLite, Dolt, an MCP server | a model with no tool can `cat` it; `rg` and `tail` read it; survives every tool crash; the same verdict the bus got (agent-coordination.md section 1) |
| History | the task body is append-only, in the bus line grammar | git auto-commits per event | one grammar, one parser (`tools/inbox` already has it); git history would need an identity per agent and a repo under `run/` |
| Mutable state | YAML frontmatter, written only by `tools/task` under a lock, every change also logged as a line | editing by hand | validation (lease, gates, owner) lives in one place; the log keeps the history the frontmatter overwrites |
| Identity | the artifact: `pr/588`, `issue/596`, `issue/596/w1`, `tag/1.37.1`, `job/perf-remeasure-1.37.0`, `research/freeunit-windows` | hash ids (`bd-a1b2`), numeric counters | agents already say "PR 588"; the extractors already emit `refs{pr, issue}`; GitHub numbers are unique; no collision handling needed on one machine |
| Where | `run/tasks/`, gitignored, setgid group-writable like `run/inbox/` | tracked `tasks/` in the harness | the second Unix user writes it; the weekly promotion to `docs/` stays the maintainer's step, as for findings |
| Claude integration | hooks (section 5) | instructions in CLAUDE.md only | an instruction is forgotten at the end of a long session; a hook is not |
| Other agents | the CLI, named in `AGENTS.md`; the extractors backfill | per-tool plugins | one surface; what an agent forgets, the nightly pass adds from its session store |
| beads (github.com/steveyegge/beads) | not adopted; three ideas borrowed | adopting it | it is Dolt-backed now, single-writer in embedded mode, with schema-version breaks between binaries; `bd ready`, `--claim` and `bd prime` are the ideas worth keeping |
| Permission | never from the ledger | "claim equals permission" | a task line is data; permission comes from the maintainer, as on the bus |

## 3. The task file

Path: `run/tasks/pr/588.md`. Frontmatter keys are fixed; unknown keys are kept and ignored.
The body after `## Log` is append-only and uses exactly the bus grammar:
`<ts> <role>@<tool> <kind> <id> <text> [<json tail>]`.

    ---
    id: pr/588
    kind: pr
    title: "post-1.37.0 chores (SECURITY.md row, Dockerfile pins, version-sites gate)"
    repo: freeunitorg/freeunit
    url: "https://github.com/freeunitorg/freeunit/pull/588"
    status: claimed
    owner: orchestrator@claude
    session: 0b6c1d2e-5f00-4a1b-9c3d-7e8f90a1b2c3
    claimed_at: "2026-10-08T06:12:40Z"
    ttl_min: 720
    lease:
      ref: refs/heads/chore/post-1.37.0
      sha: 5a57ffce...
      checked_at: "2026-10-08T06:12:41Z"
    deps: [tag/1.37.1]
    links:
      pr: 588
      worktree: scratch/w588
      reviews: [run/agent-findings/dsh-20261007-review-588.md]
    gates:
      test_fails_prechange:
        state: pass
        evidence: "PR body, failing value quoted"
      hardening_strict:
        state: pass
        evidence: CI job strict-hardening run 1234
      review_gate:
        state: pending
      measured_number:
        state: n/a
        evidence: no number is claimed
      plain_english:
        state: pending
    pack:
      level: L0
      extra: [pkg/docker/template]
    embargo: false
    updated: "2026-10-08T06:40:02Z"
    ---
    ## Next
    Fix pkg/docker/local/Dockerfile.{minimal,php-8.5,wasm}: still 1.35.5, no version-sites
    row (review comment 6044176848). Then lease-push on 5a57ffce and re-request the review.

    ## Log
    2026-10-06T22:10:03Z orchestrator@claude.e0 claim 7a1c02 claimed pr/588 {"task":"pr/588","sha":"8d143a69...","ref":"refs/heads/chore/post-1.37.0"}
    2026-10-06T23:02:11Z orchestrator@claude.e0 fact 91be44 pushed 8d143a69, 7 commits, CI green {"task":"pr/588","pr":588,"sha":"8d143a69..."}
    2026-10-07T04:10:00Z orchestrator@claude.e0 fact c0d1e2 handoff: Codex and DeepSeek answered; waiting for the maintainer's lease push {"task":"pr/588","next":"docs/next"}
    2026-10-07T09:31:48Z maintainer@sh fact 5f6a7b rebased onto master 7d729582 by another session, head 5a57ffce {"task":"pr/588","sha":"5a57ffce..."}
    2026-10-08T06:12:40Z orchestrator@claude.6d claim e3f4a5 claimed pr/588 to fix review 6044176848 {"task":"pr/588","sha":"5a57ffce..."}

`status` is one of open, claimed, review, blocked, done or abandoned. `deps` lists the tasks that
must be done first; an empty list means ready. `links.worktree` is a class and a relative name,
never a home path.

Rules the file encodes:

- `## Next` is the hand-off: three lines at most, rewritten by the owner, the first thing a
  brief prints. A task with an empty `## Next` and no owner is not ready; it is abandoned.
- Every line carries `"task":"<id>"` in its tail. The same line is in `run/inbox/bus.log`.
  `rg '"task":"pr/588"' run/inbox/bus.log` and `task log pr/588` give the same lines.
- Paths in links and evidence are repository-relative or class-relative (`scratch/w588`),
  never a home path or a host, so a brief built from the file is publishable after one scrub.
- `embargo: true` sets the file mode to 0600, keeps the task out of `task ready`, out of the
  backfill and out of every brief a non-Claude agent can read (agent-coordination.md section 5).

## 4. The CLI: `tools/task`

python3 standard library, about 300 lines, shares `tools/inbox`'s line parser. Sender comes
from `INBOX_SENDER`; the session from `TASK_SESSION` (a Claude Code session id, a Codex
rollout id, a dsh session id). Every write takes `flock` on the task file and replaces it
once, with the new frontmatter and the new log line together (section 13); the frontmatter is rewritten under the same
lock. One task per file keeps writers from colliding.

| Command | Does | Also posts on the bus |
|---|---|---|
| `task new <id> --title T [--url U] [--dep id]...` | creates the file with `status: open` | `fact` |
| `task claim <id> [--ref R]` | refuses if owned and the TTL is live; runs `git ls-remote` on the ref, records the lease, sets the owner and session | `claim` |
| `task note <id> <text> [--json T]` | appends a fact line | `fact` |
| `task link <id> k=v ...` | sets a link (`pr`, `issue`, `worktree`, `review`) | `fact` |
| `task gate <id> <gate> pass\|fail\|n/a --evidence E` | records a gate with its evidence; refuses `pass` with no evidence | `fact` |
| `task next <id> <text>` | rewrites `## Next` | `fact` with `"next":"..."` |
| `task handoff <id> [--to role@tool]` | clears the owner, keeps the lease, requires a non-empty `## Next` | `request` to the role, or `fact` |
| `task done <id>` | refuses while a gate is `pending`; `--maintainer` overrides and logs it | `fact` |
| `task show <id>` / `task log <id> [--since cursor]` | prints the file / the lines | |
| `task who pr 588` / `task who worktree scratch/w588` | finds the task by a link | |
| `task mine` / `task ready [--role R]` | my claims / open tasks with all deps done and no live owner | |
| `task lease-check <id\|--cwd>` | exit 0 when the lease SHA equals the remote head of the lease ref, exit 1 with both SHAs otherwise | |
| `task reap` | marks claims whose TTL expired and whose session is not live as `open`, with a line that names the stale owner | `fact` |
| `task brief <id> [--for resume\|implement\|review] [--short] [--public]` | section 7 | |
| `task backfill <date>` | section 6 | `fact` per session |

Validation that is not negotiable: a `claim` on a task whose `repo` is the shared checkout
path is refused unless `links.worktree` is set (the shared checkout rule from the server
`CLAUDE.md`, enforced mechanically); a `done` with `gates.review_gate` pending is refused;
`--evidence` must be a URL, a `path:line@sha`, or a command with its output file.

Reserved bus key to add to agent-messages.md: `task` (string, the task id). `tools/inbox send
--task <id>` tees the line into the task file; `tools/task` calls the same code.

## 5. Claude Code integration: hooks

Hooks are configured in `~/.claude/settings.json` (none exist today) and run on this
machine for every Claude session. Each one is a short script under `tools/hooks/` that calls
`tools/task`. Event names and fields are from the Claude Code hooks reference.

| Event (matcher) | Runs | Effect on the session |
|---|---|---|
| `SessionStart` (`startup`, `resume`, `compact`, `fork`) | `task brief --mine --short` plus `inbox since <cursor>`; returns `hookSpecificOutput.additionalContext` and `sessionTitle: <first claimed task id>` | the session begins with its tasks, the ready queue and the bus delta in context; after compaction the same brief comes back, so a compacted session does not lose the task; the session title shows the task id to peers in the agent list |
| `UserPromptSubmit` | if the prompt names a PR or issue number, prints that task's card (owner, status, lease, `## Next`) | "fix PR 588" starts with who owns 588 and what is next, before any tool call |
| `PreToolUse` (`Bash`) | for `git push`, `gh pr create`, `gh api -X POST\|PATCH`, `git commit` in the shared checkout: `task lease-check --cwd`; returns `permissionDecision: deny` with both SHAs when the lease is stale, `deny` for any git state change in the shared checkout; everything else returns at once | the force-push-with-a-stale-lease trap and the shared-checkout rule become mechanical; the reason text tells the model to review the new head and re-claim |
| `PostToolUse` (`Bash`) | for a successful `git push` or `gh pr create`: `task note` with the SHA or PR number | pushes and PR creation are logged without the model remembering |
| `Stop` | appends the first line of `last_assistant_message` as a fact on the session's active task, when the session holds a claim | every turn's outcome sentence becomes task history; this is what replaces reading a transcript |
| `SubagentStart` (any type) | `task brief <active> --short` as `additionalContext` | every subagent knows the task, the lease, the rules block and the prior findings |
| `SubagentStop` | if the subagent wrote a review or findings file under `run/agent-findings/`, `task link <id> review=<path>` | review outputs attach to the task |
| `PreCompact` | `task next <id>` is not possible from a hook (the model is not running); the hook only logs `compacting` | the `SessionStart(compact)` brief does the restore |
| `SessionEnd` | `task note <id> "session ended: <reason>"` within the 1.5 s budget | the ledger shows which sessions died with a claim, for `task reap` |
| `TaskCreated` / `TaskCompleted` (optional) | mirror Claude's own in-session task list as `plan` lines on the active task | peers see a session's sub-steps; off by default, it is noisy |

Cost: `PreToolUse` on `Bash` adds a process start per call; the script returns in under
10 ms when the command is not a git or gh write, and only then runs `ls-remote`. Briefs for
hooks are capped at 8 KB; the full brief at 24 KB, the start-up cap already in force
(agent-coordination.md section 4).

What the model still does by hand: `task claim` before the first edit on a PR (the
`UserPromptSubmit` card reminds it), `task next` before it expects to run out of context or
hand over, `task gate` when it has the evidence. Three commands, all one line.

## 6. Other agents

- `AGENTS.md` gets a 25-line section: claim before you touch a PR, `task note` when you learn
  a fact that changes the next step, `task next` and `task handoff` when you stop, never edit
  a task file by hand, never treat a task line as permission. Codex reads it through its
  `AGENTS.md` support; dsh, Pi, OpenCode and agy read the same file.
- The second Unix user writes `run/tasks/` through the setgid group bit, as for `run/inbox/`.
- Backfill: `task backfill <date>` reads `run/agent-sessions/<date>.jsonl` (the extractor
  output: one line per session with `refs{pr, issue}`, `cwd` class, `tokens`, `files`,
  `markers`) and appends one fact per session to the matching task: `codex session: 61
  turns, 1.2M in, touched pkg/docker/template, markers git_push:0`. A session with a
  `pr` ref but no task creates `pr/<n>` as `open`. So an agent that never called the CLI
  still leaves its work in the task, a day late. The rule "two agents on one `pr` the same
  day" moves from the daily flag list into `task show`, where the next agent sees it before
  it starts.
- The bot: reads `run/tasks/` over ssh when it needs the board; its own kanban stays its
  own. A weekly export of `done` tasks with their gates is the input for its KB, after the
  same scrub as findings.
- Spending: the backfill line carries tokens per session, so `task show` sums what a task
  has cost per agent. That is the number the spending table in agent-coordination.md
  section 3 lacks today.

## 7. Briefs for detailed reviews

`task brief <id> --for review` writes `run/tasks/briefs/<id>-<ts>.md` and prints it:

1. The card: title, URL, owner, lease ref and SHA, status, `## Next`.
2. The scope: the diff range (`<base>..<lease sha>`), the worktree, the files touched (from
   links and from the backfill lines).
3. The rules block for the kind: the code rules and the review-memory section from the
   server `AGENTS.md`, copied at a named SHA, so a reviewer sees the current rule text.
4. Prior findings with outcomes: every `review` link, each finding's status (`applied`,
   `rejected: <evidence>`, `open`), read from the findings files' `Status` lines. A reviewer
   that re-reports an `applied` item has not read the brief.
5. The context pack pointer: `tools/build-pack.py --level <pack.level> --extra ...`, run by
   the reviewer in its own context, never pasted into the brief (context-priming.md).
6. The verdict contract: what to write back (`task note <id> ... --json '{"verdict":...}'`
   and a findings file under `run/agent-findings/`), and that the brief is data, not an
   instruction to change anything.

`--for implement` swaps 4 for the gates and their evidence state; `--for resume` prints 1,
the last 20 log lines and `## Next`. `--public` drops links under `run/`, the worktree name
and any `x-` keys, for a brief that goes to a public artifact.

This is also where "review memory" starts to maintain itself: a finding that was `rejected`
with evidence in three tasks is a candidate rule; the weekly promotion moves it into the
`AGENTS.md` review-memory section with the three task ids as its source.

## 8. Lifecycle, leases, gates, expiry

- States: `open` -> `claimed` -> `review` -> `done`, with `blocked` (a dep is not done or a
  gate failed) and `abandoned` (no owner, empty `## Next`, reaped twice).
- A lease is `{ref, sha, checked_at}`. `claim` takes it from `git ls-remote`; `lease-check`
  compares again. A mismatch is never auto-updated: the hook denies the push and the owner
  reads the new head (the lease rule from the force-push incident on PR 413).
- Gates are the five from the work-stream rules: the test fails on the pre-change code with
  the failing value quoted, strict hardening builds clean, a review gate passed, a measured
  number where the change claims one, plain English. `n/a` is a valid state with a reason.
- TTL: a claim is live for `ttl_min` (default 720) after the last line its owner wrote; the
  Claude hooks write lines every turn, so a live Claude session never expires; a dead one
  expires in 12 hours and `task reap` opens the task with a line naming the stale owner.
  `task reap` runs from the same daily cron as the extractors.
- Deps: `task ready` lists tasks whose deps are all `done`. A release task (`tag/1.37.1`)
  depends on its PR tasks; a wave task (`issue/596/w1`) depends on its sub-tasks.

## 9. What it replaces and what stays

| Today | After |
|---|---|
| the hand-written resume page, the memory "live state" index entries, `RESUME-*.md` files in the server checkout | `task brief --mine` at `SessionStart`; the files are deleted once the twelve open items are task files (section 12) |
| "ask every live session who owns PR N" | `task who pr N`, and the owner's session title |
| hand-written subagent briefs that restate the rules and the history | `task brief --for review\|implement`, plus the task-specific question |
| the extractor's daily flag "two agents on one pr" read by nobody in time | the same fact in `task show`, read by the next agent before it starts |
| per-session transcripts as the only history of a PR | the task log, one line per outcome, written by hooks |

What stays: the memory directory for lessons, corrections and references (it is Claude-only
and about how to work, not about what is in flight); the bus as transport and as the
by-time view; `run/agent-findings/` as the evidence store; the weekly promotion into
`docs/` by the maintainer.

## 10. Metrics, measured after one week

| # | Metric | Today (estimate) | Target | How |
|---|---|---|---|---|
| 1 | tool calls from session start to the first edit on an owned task | 8 to 15 | at most 3 | Claude extractor: position of the first `Edit`/`Write` in the transcript |
| 2 | incidents per week of two agents working one PR without a hand-off line | 1 to 2 | 0 | backfill: two sessions with the same `pr` ref and no `handoff` between them |
| 3 | review re-finding rate: findings in round N already `applied` or `rejected` in round N-1 | about 20 percent on this week's plan reviews | under 10 percent | findings files' `Status` lines, matched by file and claim |
| 4 | brief size | hand-written, unbounded | 8 KB short, 24 KB full | `task brief` drops the oldest log lines, then truncates, to stay under the cap |
| 5 | resume fidelity: the first action of a resumed or compacted session matches `## Next` | not measured | 9 of 10 | weekly spot check of ten sessions, Haiku |

## 11. Risks

- A hook denies a legitimate push because a peer pushed a newer head. Intended: the reason
  names both SHAs; the owner reviews the new head and re-claims. The cost is one `ls-remote`.
- Agents that do not call the CLI. The hooks cover Claude; the backfill covers everyone else a
  day late; `task ready` shows an `open` task that a session is in fact working on only until
  the backfill runs. Acceptable for a week; then measure metric 2.
- Hook latency on every `Bash` call. Mitigated by the early return; measure with ten calls
  before enabling for all sessions.
- Two writers on one task file. `flock` plus `O_APPEND`; one task per file; the bus line is
  the backup copy of every log line.
- Briefs that grow. Hard caps, and `pack` pointers instead of pasted code.
- A task that leaks. `run/` is private; `embargo` tasks are 0600 and never backfilled or
  briefed to non-Claude agents; `--public` scrubs; `check-public.sh` runs on the briefs
  directory before anything under it is copied out.
- The ledger drifts from GitHub. `task claim` and `lease-check` read GitHub each time; the
  nightly backfill re-reads PR state (`gh pr view --json state,headRefOid`) for every
  `claimed` or `review` task and logs a line when it changed.

## 12. First week

| # | Change | File | Tier |
|---|---|---|---|
| 1 | `tools/task` with the commands of section 4, sharing `tools/inbox`'s parser; `inbox send --task` | `tools/task`, `tools/inbox` | Sonnet writes, maintainer applies |
| 2 | the `task` key in the reserved table | `docs/agent-messages.md` | Sonnet |
| 3 | hook scripts and the settings block of section 5; `PreToolUse` first, then `SessionStart`, `Stop`, `SubagentStart` | `tools/hooks/*.sh`, `~/.claude/settings.json` | Sonnet writes, maintainer enables |
| 4 | the twelve open items become task files: PR 452, PR 588, PR 600, the 1.37.1 release and its ready set, issue 596 waves 1 to 3, issue 597 wave 0 and the perf-regression lead, the Alpine aport, freeunit4drupal Phase 0, freeunit-windows Phase 0, the OTel line | `run/tasks/` | Opus, from the private notes and the trackers |
| 5 | `task backfill` from `run/agent-sessions/` and `task reap` in the daily cron | `tools/task`, `Makefile` | Sonnet |
| 6 | the `AGENTS.md` section and the `CLAUDE.md` pointer in both checkouts | `AGENTS.md`, `AGENTS.local.md` | Sonnet writes, maintainer applies |
| 7 | retire the resume page and the memory live-state entries | `docs/`, memory | maintainer |
| 8 | measure metrics 1 to 5 | `tools/session-extract/`, a 30-line report | Haiku |

## 13. Implementation notes

Items 1 to 3 of section 12, built 2026-10-08. Tests: `make tools-test`. Where the tool and
the hooks differ from sections 3, 4, 5 and 8:

Section 3, the file:

- `lease` and `pack` are nested blocks, not flow maps. The tool writes and parses only its own YAML subset: block maps, flow lists of scalars, `{}` for an empty map, `null`, `true`, `false`. A flow map such as `{state: pending}` in a hand-edited file is an error (exit 2). A gate that is not a map counts as pending for `done`.
- A string that a YAML reader could take for something else is written as a JSON string. That covers timestamps (`claimed_at: "2026-10-08T06:12:40Z"`), number-like and boolean-like strings, and strings with a comma.
- No inline comments are written. A full-line comment in a hand-edited file is dropped on the next write.
- `owner` is the sender alone, for example `orchestrator@claude`. The session id is in its own `session` key.
- New keys: `session`; `remote` (the lease remote when it is not `https://github.com/<repo>.git`; a remote or ref that starts with `-` is an error); `role` (set by `handoff --to`, read by `ready --role`); `base` (optional, the start of the diff range in a brief).
- `task new` gives `pr` and `issue` tasks the five gates as `pending` and other kinds no gates. An id `pr/N` or `issue/N...` sets `links.pr` or `links.issue`.
- Review links are the list `links.reviews`. `task link <id> review=<path>` appends to it.
- An embargoed task's lines are not sent on the bus; its 0600 file is the only copy. When the bus write fails after the task file write, the tool exits 2 and says which copy has the line.

Section 4, the CLI:

- The shared-checkout rule checks the kind: `claim` on a `pr` task needs `links.worktree` or `--worktree class/name`. The `repo` value is not compared with a path.
- `done` is refused while any gate is `pending`, not only `review_gate`.
- A gate name is one of the five or starts with `x-`. `gate pass` and `gate fail` need evidence in one of the three shapes. `gate n/a` needs a reason in free text. A path in evidence must not start with `/` or `~`.
- In the shape `cmd > out` (or `cmd -> out`), `out` is one relative path with no `..`. It must exist (in the current directory or the harness root) or be under `run/`. `it is fine > trust me` is refused.
- `claim` with no `--ref` and no recorded lease ref records no lease, and its line says so.
- A claim made with a session belongs to that session. The same sender with no session or another session is another owner for `claim` and `handoff`; `mine` with no session lists every session. `claim --ttl` takes 0 or more minutes.
- `task mine` lists the open tasks whose owner is the sender and whose session is the caller's session (any session when the caller has none). The hooks do not use it to find the active task (section 5 below).
- With no `INBOX_SENDER`, `tools/task` and `tools/inbox` use `agent@claude` when `CLAUDE_CODE_SESSION_ID` is set, else `<USER>@sh`. A Claude Code shell so writes no user name on the bus.
- `handoff` is refused while another owner's claim is live. It sets `status: open`. `--to role@tool` puts only the role in the `to` key.
- `next` writes the full text into the `next` key of the tail. The free text holds its first line.
- `lease-check` only reads. It does not update `checked_at` and writes no line.
- `lease-check --cwd` takes the branch from `git rev-parse --abbrev-ref HEAD` and the remote from `git remote get-url origin`. If several tasks hold that ref, it prefers the one the sender owns.
- `reap` opens every claimed task whose TTL expired. It does not check whether the owner's session is still running.
- `who` takes `pr`, `issue`, `worktree` or `review` and prints nothing when no task matches.
- `brief` defaults to `--for resume`. Only `--for review` also writes a file, at `briefs/<id>-<ts>.md`.
- `brief --for resume` prints the card, the scope, the pack pointer and the last 20 log lines. It adds the rules and the findings only when their inputs exist, and has no verdict contract.
- `brief --short` prints the card, the gates and the last 20 log lines, for any `--for`. The log heading counts every line a brief leaves out.
- The rules block is the file named by `TASK_RULES_FILE`, printed with its file name. The tool does not name a SHA; the file must.
- `--public` also drops the `remote` key, drops the json tail of every log line and the session suffix of every sender. An embargoed task gets no public brief and no brief for a sender whose tool is not `claude`.
- `backfill` is a stub that exits 2 until `run/agent-sessions/` exists (section 12 item 5).

Section 5, the hooks:

- The scripts are python3 files named after the event (`tools/hooks/pre-bash`, `session-start` and so on), not `*.sh`. Shared code is in `tools/hooks/_ledger.py`. `tools/hooks/settings.json` is the settings block; `tools/hooks/install` merges it into `~/.claude/settings.json`, as a dry run unless `--apply` is given.
- Configuration comes from `run/hooks.env` (or the file named by `HOOKS_ENV`), else `tools/hooks/hooks.env.example`. A variable set in the environment wins. The keys are `TASK_ROOT`, `INBOX_LOG`, `INBOX_SENDER` and `TASK_SHARED_CHECKOUTS`.
- Every hook does nothing when `TASK_ROOT` does not exist. That includes the shared-checkout rule.
- The session id is `session_id` from stdin. The hooks pass it to the CLI as `TASK_SESSION`.
- The hooks find the active task by session id alone: the first open task whose `session` key equals `session_id`, whatever its owner. `task claim` records that key from `CLAUDE_CODE_SESSION_ID`, so a claim made in the model's shell matches even when its sender differs from `INBOX_SENDER` in the hooks env.
- A hook writes its line on a task (Stop, PostToolUse, PreCompact, SessionEnd, SubagentStop) with the owner recorded in the task as the sender. The line so refreshes the TTL.
- `SessionStart` has no matcher, so it also runs on `clear`. There is no `task brief --mine`. The hook finds the tasks of this session id as above, then runs `task brief <id> --short` for each task, each cut to 5 KB divided by the number of tasks (500 bytes at least). The cut keeps the card and `## Next`.
- When this session id owns no task, `SessionStart` lists the `task mine` lines of every session of the sender, because a resumed session can get a new id. It prints no briefs and sets no session title then.
- The bus cursor is a timestamp in `<TASK_ROOT>/.cursor/<session_id>`. With no cursor the hook shows the last 24 hours. It leaves out the lines of tasks it already briefed, and shows 40 lines at most.
- After compaction the context starts with the line "context after compaction: re-read the task files before acting".
- `UserPromptSubmit` also reads `#588` (a PR task, else an issue task) and GitHub pull and issue URLs. `issue 596` with no `issue/596` file shows up to three tasks under `issue/596/`. The card is the short brief up to `## Gates`, plus a claim reminder when this session does not own the task. The cap is 2 KB.
- The `PreToolUse` fast path is one regex on the command. It takes 25 ms at best and 35 ms at the median with the interpreter start (30 runs, 2026-10-09), not 10 ms.
- The shared-checkout rule compares the git top level of the command's directory (the hook `cwd`, then `cd <path>` and `git -C <path>`) with `TASK_SHARED_CHECKOUTS`. A worktree nested in a shared checkout is not denied. Denied: commit, checkout, switch, reset, stash (not `list` or `show`), rebase, merge, cherry-pick, push, pull, restore, clean (not `-n`), am, revert, update-ref, rm, mv, add, apply (not `--check` or `--stat` alone), `tag` with a name or `-d` (not a listing form such as `-l` or `--contains`), `branch -d/-D/-m/-M/-c/-C/-f/-u` and their long forms, `worktree remove/move/prune`. The string of `bash -c`, `sh -c` and `eval` is checked the same way. Allowed: status, log, diff, show, fetch, ls-remote, `worktree list`, `worktree add`, and a plain `git branch <name>`.
- The lease check does not use `task lease-check --cwd`. The hook takes the ref from the push refspec (so `git push origin HEAD:refs/heads/x` from a detached worktree is checked) or from the current branch, and the remote URL from the command or `origin`. It finds the task whose lease ref and remote match, then runs `task lease-check <id> --remote <url>`. When no task matches, it adds one line of context and does not deny.
- `gh pr create` is checked for `--head` (or the current branch) on `--repo` (or `origin`). `gh api` is checked only for a write on `repos/<owner>/<repo>/pulls/N` or `issues/N`, when a task linked to N holds a lease. A write is `-X` with a method other than GET, or a field flag, which makes gh send a POST. There is no reminder for `gh api`.
- A lease-check exit 2, a timeout or a crash gives no output. Only a "lease mismatch" denies.
- `PostToolUse` counts a push as successful when stdout and stderr hold no "error", "rejected" or "fatal" and the call was not interrupted. The SHA is `git rev-parse` of the refspec source (HEAD by default), one note per pushed ref. `gh pr create` writes "created PR <url>" with `{"pr": N}`.
- `Stop` writes the tail `{"hook":"stop"}` and skips a first line shorter than 12 characters.
- `SubagentStop` uses `task link <id> review=<path>`, which already appends to `links.reviews`; `tools/task` was not changed. A path must exist under the harness root (the parent of `run/`).
- `SessionEnd` has a hook timeout of 1 second. It reads the ledger in process and makes one CLI call with 0.45 seconds.
- `TaskCreated` and `TaskCompleted` are not built.

Section 8, lifecycle:

- No command sets `review`, `blocked` or `abandoned`. A failed gate does not block the task; `ready` checks the deps itself.
- The TTL counts from the last log line whose sender is the owner, else from `claimed_at`. `ttl_min: 0` makes a claim expire at once.
