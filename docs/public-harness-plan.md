# Public harness plan

Status: plan, 2026-10-07. Nothing here is done yet. Every count below was taken on
2026-10-07 from this repository (23 commits on `master`, 1 on `feat/code-registry`,
68 tracked files, 18 untracked files that are meant to be committed; `docs/review-pipeline.md`, added later the same day, is classified in the per-file table but not in the counts). Re-run the
commands in "Verification" before you trust a count.

Goal: publish this harness so that anyone can spend a token budget with any coding
agent to validate FreeUnit, reproduce an issue, review a PR, customize FreeUnit for
an application, or measure a change. The public tree holds no server names, no
private paths, no client names and no unpublished security material.

## 1. Scrub inventory

Search terms: build-host names, IPv4 literals, home and scratch paths, remote
filesystem paths, user names, e-mail addresses, project ids, client and
infrastructure names, GHSA and CVE ids, ssh and registry use, secret-shaped strings.
The literal private terms are in `run/public-deny.local` (see section 3). This page
names them only by kind, because this page will be public too.

| Kind | Tracked (lines/files) | Untracked (lines/files) | In history | Action |
|---|---|---|---|---|
| Build and bench host names | 32/7 | 10/5 | yes, same lines | scrub: "the build host", `$BUILD_HOST`, `$PERF_HOST` |
| Remote filesystem layout, `host:/path` evidence | 7/4 | 1/1 | yes | variable (`UNIT_REMOTE_ROOT`) or delete the evidence path |
| Laptop and per-session scratch paths | 6/3 | 8/5 | yes | `$UNIT_SRC`, `$HARNESS_SCRATCH`, `command -v` |
| Hosting org name (user name, Alpine build image) | 3/2 | 0 | yes | variable `ALPINE_BUILD_IMAGE`; drop the user name |
| Client names (maintainer's other work) | 0 | 1/1 | no | delete the row |
| Cloud provider instance name and dates | 0 (pending diff to AGENTS.md) | - | no | keep the rule, drop the instance and dates |
| Private-state pointers (memory notes, peer session names, "a private item") | 5/1 | 2/2 | yes | move `docs/session-start.md` to `run/`; reword the rest |
| Session links in commit messages (`Claude-Session:` URL) | 10 of 23 commits | - | yes | fresh repository (section 2) |
| GHSA ids | 4/2 | 4/2 | yes | keep: all 7 distinct ids are published (API check below) |
| CVE ids | 3/2 | 1/1 | yes | keep: public ids; add each to the allowlist after a check |
| Non-loopback IPv4 literals | 0 | 0 | 0 | none; the only private IP is in gitignored `run/` |
| Project ids, API keys, tokens, registry credentials | 0 | 0 | 0 | none; one page names CI secret *names* only |
| Model price table | 0 | 1/1 | no | ask the maintainer (section 8) |

GHSA check, 2026-10-07: `gh api 'repos/freeunitorg/freeunit/security-advisories?state=published'`
lists all seven ids cited here as published (five on 2026-10-04, two on 2026-07-17).
No tracked or untracked file holds advisory text that is not public. `run/ghsa/` is
gitignored (`git check-ignore -v run/ghsa` matches `/run/` in `.gitignore`).

History: no line with a private term was ever deleted (`git log -p` shows no removed
line that matches). So the history leaks exactly what HEAD leaks, plus the session
links in 10 commit messages. The side branch adds `tools/unit-registry` with no hit.

### Per-file actions

| File | Hits | Action |
|---|---|---|
| `tools/unit-build` | hosts in usage and comments, disk sizes, the remote data-root default, hosting-org image | read hosts, roots and image from the lab env file; move disk notes to `run/servers.md` |
| `tools/bench-engines.sh` | per-user `RUNBASE` default, a home-dir `oha` path | `${HARNESS_SCRATCH}/bench-engines`; `OHA=$(command -v oha)` |
| `tools/bench-release.sh`, `bench-startup.sh`, `bench-crossover.py` | host named as the evidence source | "the reference bench host" |
| `tools/capget-fallback-smoke.sh` | Docker host named | "a Docker-only host" |
| `skills/freeunit-benchmark/references/release-ab.md` | host x5 | "the reference bench host" |
| `skills/freeunit-release/references/packaging-deps.md` | a host-and-path evidence string, home paths | drop the path, keep the date and SHA; `$HARNESS_ROOT`, `$UNIT_SRC` |
| `skills/freeunit-release/references/cut-recipe.md` | a download-site host and path | "the download server" |
| `skills/freeunit-review/references/bots-and-gh.md` | maintainer checkout path x2 | `<checkout>/.codex` |
| `agents/remote-builder.md` | the remote data root | `$UNIT_REMOTE_ROOT` |
| `docs/session-start.md` | live state, session names, private pointer | move to `run/`; public "start here" is AGENTS.md |
| `docs/review-pipeline.md` (untracked, written 2026-10-07) | section "How the DeepSeek reviewer is driven": a laptop process, home-dir config and session paths, untracked helper scripts in the server checkout, posting under the maintainer login; one open-questions row on a past disclosure | move the operational detail and that row to `run/`; keep the stage table, costs, cheat sheet and the template rules (read hunks, trace claims, 15 lines, trailer pair). Its advice to move the DeepSeek prompt template into `skills/freeunit-review/references/` holds for the public tree only after the same scrub |
| `docs/model-routing.md` (untracked) | client names, prices | delete the client row; prices: ask |
| `docs/context-priming.md` (untracked) | host, checkout path | variables |
| `docs/perf/` (untracked, 13 files) | hosts, scratch paths, remote path | scrub `csrc-map`, `freeunit-php-path`, `perf-plan`, `php-worker-mode-api-survey`, `rapira-architecture`, `yii3-*`; move `release-plan-1.37.1`, `alpine-job-*-triage`, `review-452-fable-*` to `run/`; delete `pr452-body`, `pr588-body`, `pr588-reply` (already public on GitHub) |
| `AGENTS.md` (pending diff) | cloud instance name, provider, dates | keep the rule: prefix, owner tag, expiry, delete volumes and IPs |

### Configuration convention (maintainer decision, 2026-10-07)

The scrub is not only text replacement. Every tool that touches a server reads its
settings from one gitignored file, and private per-checkout instructions live in
`AGENTS.local.md` and `CLAUDE.local.md` (both gitignored). The perf-lab work chose
`run/lab.env` (override with `LAB_ENV=<path>`) and ships
`tools/perf-lab/lab.env.example` with placeholders. Reconciled with that file on
2026-10-07: reuse its names, and add only what the other tools need.

| Variable | Replaces | Status |
|---|---|---|
| `PERF_HOST` | bench host named in docs and bench scripts | in lab.env.example |
| `LAB_DOCKER_HOST` | host for `unit-build -t alpine` and gdb sessions | in lab.env.example |
| `REMOTE_WORKDIR` | the remote data root; `unit-build` keeps `UNIT_REMOTE_ROOT` and `UNIT_REMOTE_CACHE`, defaulting to it | in lab.env.example |
| `SSH_USER` | none today; empty means `~/.ssh/config` decides | in lab.env.example |
| `OHA` | home-dir binary path in `bench-engines.sh` | in lab.env.example; local default `command -v oha` |
| `BUILD_JOBS` | `JOBS=5` in `unit-build` | in lab.env.example |
| `SERVER_CPUS`, `LOAD_CPUS` | `SERVER_CORES`, `LOAD_CORES` (bench-engines), `UNIT_CPUS`, `POLL_CPUS` (bench-startup) | in lab.env.example; keep the old names as aliases for one release |
| `BUILD_HOST` | host in `unit-build -t remote` examples | add |
| `ALPINE_BUILD_IMAGE` | hard-coded third-party image in `unit-build` | add; default an image built from `ci/Dockerfile.alpine` |
| `HARNESS_SCRATCH` | per-user tmp and session scratch paths (`RUNBASE` defaults) | add; default `${TMPDIR:-/tmp}/freeunit-harness` |
| `PHP_TEST_IMAGE` | PHP pytest image named in docs | add |
| `UNIT_TEST_PORT`, `PORT`, `CTRL_PORT` | already variables | unchanged; local, not lab |

ssh port and key belong in `~/.ssh/config` under the alias, never in the harness.
One loader, `tools/lib/env.sh`, sources `${LAB_ENV:-run/lab.env}` if it exists; a
variable already set in the environment wins. `tools/perf-lab` and the other tools
share it.

## 2. History decision: a fresh repository

Recommendation: publish a fresh repository with one squashed, scrubbed commit. Do not
rewrite this history with `git filter-repo`.

Why: the history has 23 commits, and every private line in it is also in HEAD. A
rewrite needs `--replace-text` for the tree and `--message-callback` for 10 commit
messages, and one missed variant leaks forever once pushed. The old commits record
private working practice more than design; their value to a newcomer is small. A
fresh tree is checked once, by the same script that guards it later. This private
repository stays as it is, on this machine, with no remote.

Command sequence (the maintainer runs it; `$PRIV` is this repository, `$PUB` an empty
directory):

```sh
# 1. Scrub in the private repo as ordinary commits (section 1), then:
git -C "$PRIV" status --short          # must be empty
mkdir "$PUB" && git -C "$PRIV" archive HEAD | tar -x -C "$PUB"
cd "$PUB" && git init -b main
tools/check-public.sh --all             # generic patterns, exit 0 required
HARNESS_DENY_FILE="$PRIV/run/public-deny.local" tools/check-public.sh --all
git add -A && git commit -m "freeunit-harness: initial public import"
git log --format=%B | grep -c -i -E 'claude-session|session_'   # must print 0
git rev-list --count --all              # must print 1
gh repo create freeunitorg/freeunit-harness --public --source . --push
```

Verification grep that must return zero lines before the push, run in `$PUB`:

```sh
git grep -n -i -E -f "$PRIV/run/public-deny.local"; \
git grep -n -E '/home/[a-z_][a-z0-9_-]*/|/tmp/claude-[0-9]|/scratch[p]ad/|claude\.ai/code/session_'
```

## 3. `tools/check-public.sh`

Purpose: fail when a file that would be committed carries a private detail.
Runs as a pre-commit hook (`make hooks` installs it) and as a CI job on every push and
pull request. Exit 0 clean, 1 on a hit, 2 on a usage or setup error. Output: one TSV
line per hit (`file`, `line`, `rule id`), with the matched text masked, so a CI log
never repeats a secret.

Inputs:
- `tools/public-deny.txt` (public): one rule per line, `id<TAB>ERE`. Generic patterns only.
- `tools/public-allow.txt` (public): exact strings that a deny rule may match (loopback
  and documentation IPs, published GHSA and CVE ids, the anthropic noreply address).
- Private list (built): `run/public-deny.local`, gitignored, one term per line. Each line is
  a Python regex, or a literal when it does not compile; matching ignores case. Pass it with
  `--deny-file` or `HARNESS_DENY_FILE`; `make check-public` passes it when it exists. Create
  it with one command: `printf '%s\n' <host> <user> > run/public-deny.local`. A hit prints
  `private-<line number>` in place of the term. The matched line text is never printed; a
  file name is printed as is, so a file named after a term shows it. Without the file the
  private rules do not run and the exit code does not change, so CI and contributors run
  the generic rules only. The initial content, by kind: the two build-host names and their
  family prefix, the IP in `run/servers.md`, the packages DNS IP, the hosting-org name and
  the user name on the build hosts, three client names, the internal codenames of the
  maintainer's other projects, the cloud instance name.
- HMAC layer (not built in 0.1): `tools/public-deny.hmac` would hold HMAC-SHA256 values of
  the private terms, keyed by the CI secret `HARNESS_DENY_KEY`, so CI on pushes to `main`
  could check them too. Fork pull requests get no secret, so it would not cover them.
- Scope: `git ls-files` plus staged files; `--all` also scans untracked files that
  are not ignored; `--history` adds `git log -p --all` and commit messages.

Initial `tools/public-deny.txt`:

```text
# id	ERE	# why
home-path	/home/[A-Za-z_][A-Za-z0-9_-]*/	# absolute home paths
mac-home	/Users/[A-Za-z][A-Za-z0-9_-]*/	# same on macOS
tilde-checkout	~/(www|src|code)/	# a personal checkout layout
agent-scratch	/tmp/claude-[0-9]+|/scratch[p]ad/	# per-user agent scratch
session-link	claude\.ai/code/session_|^Claude-Session:	# session URLs
ipv4	\b([0-9]{1,3}\.){3}[0-9]{1,3}\b	# any IPv4 not in the allowlist
ssh-literal	\b(ssh|scp)( +-[A-Za-z]+)* +[a-z0-9][A-Za-z0-9._-]*( +['"]|[0-9]\b)	# ssh <literal-host>; ssh "$HOST", ssh HOST and ssh <host> pass
host-flag	(-H|--host) +[a-z][a-z0-9.-]*[0-9]\b	# a literal host passed to unit-build -H
host-path	\b[A-Za-z][A-Za-z0-9.-]*:/(home|data|srv|var|opt)/	# host:/path evidence
remote-root	/data/(projects|www)/	# a private remote layout
email	[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}	# any address not allowlisted
ghsa	GHSA(-[0-9a-z]{4}){3}	# only published ids pass
cve	CVE-[0-9]{4}-[0-9]{4,}	# only allowlisted ids pass
gh-token	gh[pousr]_[A-Za-z0-9]{36}	# GitHub tokens
api-key	sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-	# common key shapes
private-key	-----BEGIN [A-Z ]*PRIVATE KEY-----	# key material
memory-pointer	MEMORY\.md|resume-unit[-]	# private agent state
```

The list grows by appending a line. Every new rule comes with a test case in
`tools/tests/check-public/` (one file that must hit, one that must pass), and CI runs
those cases first.

## 4. Agent survey: one tree for every agent

Checked 2026-10-07 against official docs unless marked "secondary".

| Agent | Instruction file | Reads AGENTS.md | Skills or rules path | Local twin | Source |
|---|---|---|---|---|---|
| Claude Code | `CLAUDE.md`, `.claude/CLAUDE.md`, `@path` imports (4 hops) | yes (v2.1.277+), but only when no `CLAUDE.md` or `CLAUDE.local.md` exists, unless the setting is `claude-md-and-agents-md` | `.claude/skills/<n>/SKILL.md`, `.claude/agents/*.md`, `.claude/rules/` | `CLAUDE.local.md`, native, gitignore it | https://code.claude.com/docs/en/memory |
| Codex CLI | `AGENTS.override.md`, then `AGENTS.md`, root to cwd, concatenated; 32 KiB default cap | yes, primary | `.agents/skills/`, `~/.agents/skills/`; symlinked skill folders work | none; `AGENTS.override.md` replaces, `~/.codex/AGENTS.md` adds | https://learn.chatgpt.com/docs/agent-configuration/agents-md |
| MiniMax Code (`mcode`) | workspace `AGENTS.md` | yes, only file named | `/skills` in the TUI; path unconfirmed | none documented | https://agent.minimax.io/docs/cli/configuration.md |
| agy (Antigravity CLI) | workspace `GEMINI.md` and `AGENTS.md`, global `~/.gemini/GEMINI.md` | yes | `.agents/skills/` | none documented | https://antigravity.google/docs/cli/gcli-migration |
| dsh (DeepSeek Harness, `@deepseek-ai/dsh`) | `AGENTS.md` then `CLAUDE.md` in each directory from the git root to the cwd; global `$DSH_HOME/AGENTS.md` first; 64 KiB budget | yes, and `CLAUDE.md` | `.dsh/skills`, `.agents/skills`, `~/.dsh/skills`, `~/.agents/skills`; not `.claude/skills` | native: `AGENTS.local.md` and `CLAUDE.local.md` load after the base files | https://www.npmjs.com/package/@deepseek-ai/dsh-agent-instructions , https://www.npmjs.com/package/@deepseek-ai/dsh-skill-filesystem |
| Kilo Code | `AGENTS.md`; `kilo.jsonc` `instructions`; legacy `.kilocode/rules/` | yes | `.kilo/skills/`, `.agents/skills/` | none | https://kilo.ai/docs/customize/custom-rules |
| Pi (`pi`, earendil-works) | `AGENTS.md` or `CLAUDE.md`, walking up from cwd (secondary source) | yes | `.agents/skills/`, `~/.agents/skills/` | none; `AGENTS.override.md` (secondary) | https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md |
| Gemini CLI (retired for individual users 2026-06-18) | `GEMINI.md` hierarchy, `@file` imports | via `context.fileName` | `.gemini/skills/`, `.agents/skills/` | none; can list a local name in `context.fileName` | https://geminicli.com/docs/cli/gemini-md/ , https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/ |
| Cursor | `.cursor/rules/*.mdc` | yes, root and nested | `.agents/skills`, `.cursor/skills` | none | https://cursor.com/docs/context/rules |
| Aider | `CONVENTIONS.md` via `read:` in `.aider.conf.yml` | only via `read: AGENTS.md` | none | a gitignored `.aider.conf.yml` (inference) | https://aider.chat/docs/usage/conventions.html |
| OpenCode | `AGENTS.md`, else `CLAUDE.md`; `opencode.json` `instructions` (paths, globs, URLs) | yes, primary | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/`; agents `.opencode/agents/*.md`; commands `.opencode/commands/*.md` | none; `instructions` can name a file | https://opencode.ai/docs/rules/ , https://opencode.ai/docs/agents/ , https://opencode.ai/docs/commands/ |

Tool facts, lane rules and the full generated-file list for agy, dsh and OpenCode live in
[agent-tools.md](agent-tools.md), the single page for them. Verified 2026-10-07 with a `claude -p`
canary in scratch directories: Claude Code 2.1.292 loads a `CLAUDE.md` that is a symlink to
`AGENTS.md`, and resolves `@AGENTS.md` inside a `CLAUDE.md`; dsh prints identical sibling files once.
Start-up byte budgets: agy 24 KB per rules file, dsh 64 KiB, Codex 32 KiB. User-global skills need one
link per tool: `~/.agents/skills` (dsh, Codex, OpenCode), `~/.claude/skills` (Claude Code),
`~/.gemini/antigravity-cli/skills` (agy). A per-tool extension may point at shared content and add
tool-only behaviour; it may not restate a rule.

AGENTS.md is plain Markdown; the closest file to the edited file wins; it is stewarded
by the Agentic AI Foundation under the Linux Foundation (https://agents.md). Agent
Skills is one folder per skill with a `SKILL.md` that starts with `name` and
`description` frontmatter (https://agentskills.io). Unconfirmed: the `mcode` skills
path, Pi's official context-file page, Cursor's legacy `.cursorrules`, Aider reading
AGENTS.md without `read:`.

OpenCode with open-weights models: OpenRouter is a built-in provider (`/connect`,
then `/models`; ids are `provider/model`, https://opencode.ai/docs/providers/). The
FreeUnit checkout's `opencode.json` holds only `$schema` and `"lsp": true`. Custom
commands are Markdown files with `description`, `agent`, `model`, `subtask` frontmatter
and `$ARGUMENTS`; agents are Markdown files with `description`, `mode`
(`primary` or `subagent`), `model`, `permission`.

Finding (2026-10-07): none of the nine `skills/*/SKILL.md` files had frontmatter, so no agent
discovered them as skills. Fixed 2026-10-09: each now starts with `name` (the directory name) and
`description`. No agent has been checked to list them yet: the skills are not linked under
`.agents/skills` or `.claude/skills` until `make agents` exists.

### Proposed layout

```
AGENTS.md                 canonical text for every agent; ends with
                          "If AGENTS.local.md exists, read it first."
CLAUDE.md -> AGENTS.md    symlink (dsh does not expand "@AGENTS.md"); Claude-only notes go
                          in .claude/rules/*.md
skills/freeunit-<n>/SKILL.md   canonical skills; the index is docs/skills.md
.agents/skills -> ../skills    Codex, agy, Kilo, Pi, Cursor, OpenCode
.claude/skills -> ../skills    Claude Code
agents/*.md               canonical role briefs
.claude/agents/*.md       generated from agents/
.opencode/agents/*.md     generated from agents/
recipes/*.md              contribution recipes (section 5)
.opencode/commands/*.md   generated from recipes/ (/validate, /repro, /review ...)
opencode.json             instructions: ["AGENTS.local.md"] (test a missing file first)
.aider.conf.yml           read: [AGENTS.md]
tools/perf-lab/lab.env.example   placeholders (section 1)
.gitignore                adds AGENTS.local.md, CLAUDE.local.md (run/lab.env is under /run/)
```

A contributor's `CLAUDE.local.md` holds one line, `@AGENTS.local.md`. `make agents`
runs `tools/gen-agents.py`, which writes the generated files and recreates the
symlinks, or copies when `core.symlinks` is false (Windows). `make agents-check`
runs in CI and fails when the generated files differ from a fresh run.

## 5. Contribution recipes

Principle: every tool writes a unified machine format (TSV rows, one JSON verdict).
Arithmetic and judgement-free steps live in scripts with unit tests. The model reads
the verdict and writes the report; it never computes a mean, a delta or a pass rate.
The perf-lab work is building `perf-compare.py` with a JSON verdict on this pattern.

Budgets come from measured runs this month where they exist: the Fable review of
PR 452 took about 208k tokens and 18 minutes, its fix pass about 380k, the
benchmark-setup research about 220k, the release-state collection about 155k
(maintainer's figures); an Opus pack-primed perf question 561k and a Sonnet
grep-and-read question 83k (`docs/context-priming.md`); a scoped Codex review
39k-193k (`skills/freeunit-review/references/bots-and-gh.md`). Rows without a run are estimates.

| Recipe | Hands back | Budget band | Tier that suffices | Open model (Qwen, GLM, DeepSeek class) |
|---|---|---|---|---|
| Validate the codebase: build, C tests, one pytest group | `validate.json` + 10-line report | 60k-150k (upper bound: release-state run 155k) | small (Haiku, Sonnet) | needs a script: `tools/validate` that runs the steps and emits the verdict; then unaided |
| Reproduce an issue: build the revision, write the failing test | a test that fails before and passes after, with the failing value | 150k-400k (fix pass 380k) | frontier for C, lifetime or ordering bugs | HTTP-level pytest repro with the validate script; C-level needs frontier |
| Review a PR (adversarial brief + code rules) | findings with `file:line`, what was not verified | 200k-300k full; 40k-200k scoped (PR 452: 208k) | frontier | needs frontier; an open model may do a first pass that a frontier model verifies |
| Customize for an end user (Drupal, Laravel, static site) | a config, a smoke result as JSON, a short note | 50k-150k (estimate) | small | unaided, given a config-check script that PUTs the config and curls the routes |
| Measure a change (the perf-lab job, `tools/perf-lab/`) | perf-compare TSV and JSON verdict | 30k-80k per run (estimate); setup research 220k, once | small | needs a script: `perf-compare.py` verdict; then unaided |
| Public check of a contribution | `check-public.sh` exit code | under 20k | any | unaided |

Each recipe page states: inputs, the exact commands, the stop condition, the output
file, and "stop and report when the budget is 80% spent". A recipe never needs the
maintainer's hosts: it runs on the contributor's machine or a host named in their
lab env file. Benchmarks need dedicated cores and run one at a time.

## 6. Repository shape

Recommendation: a separate repository, `freeunitorg/freeunit-harness`.

| | Separate repo | Directory inside `freeunitorg/freeunit` |
|---|---|---|
| Discoverability | one link from FreeUnit's README and CONTRIBUTING | found by anyone who clones |
| CI cost | its own small CI (shellcheck, check-public, agents-check) | every harness edit triggers FreeUnit's 13 workflows |
| Design fit | the harness already clones the server into `./src` and must not build in an edited checkout | a harness inside the tree it builds conflicts with that rule |
| Release cadence | independent | tied to server PRs and reviews |

Add a three-line `AGENTS.md` pointer in the FreeUnit repo later, so agents working
there find the harness.

Licence: Apache-2.0. FreeUnit's `LICENSE` on `origin/master` is Apache License 2.0,
and this repository's `LICENSE` already is too.

README for humans: what the harness is, a 3-command start (`make doctor`, `make dev`,
`make validate`), the recipe table with budgets, which agents are tested, how to set
up the lab env file, and "security reports go to FreeUnit's SECURITY.md, never to
this repository's issues". Code of conduct: a pointer to FreeUnit's
`CODE_OF_CONDUCT.md` (Contributor Covenant).

First three "good first contribution" issues:

1. Prove that Claude Code, Codex and OpenCode list the nine skills (the `name` and
   `description` frontmatter is in place since 2026-10-09). 30k-60k tokens; open model unaided.
2. Run the validate recipe on your distribution and architecture and add a row to a
   platform matrix. 60k-150k tokens; open model with `tools/validate`.
3. Unit tests for the arithmetic in `tools/bench-crossover.py` and `tools/ci-cost.py`,
   with TSV fixtures. 80k-150k tokens; open model unaided.

Stays private forever: `run/` (server notes, `run/ghsa/`, raw bench data with host
names), `docs/session-start.md` and the other moved pages, the deny list in plain
text, the HMAC key, and the model price table unless the maintainer releases it.

## 7. Phases

| Phase | Dates | Done when |
|---|---|---|
| Scrub and verify | 2026-10-07 to 2026-10-11 | section 1 actions committed in this repo; lab env loader in tools; `check-public.sh --all --history` written and clean on the scrubbed tree |
| Fresh repo and CI | 2026-10-12 to 2026-10-18 | section 2 sequence run; CI runs check-public, agents-check, shellcheck; `make agents` output committed |
| Dry run | 2026-10-19 to 2026-10-23 | the maintainer runs the validate recipe with Codex and with MiniMax `mcode`, once with OpenCode on an open model via OpenRouter, and once with `dsh --profile headless`; each run stays inside its budget and returns a verdict file |
| Announce | from 2026-10-26 | dry-run fixes merged; the three issues filed; link added to FreeUnit README |

## 8. Questions for the maintainer

1. "agy": the Google Antigravity CLI (binary `agy`, the Gemini CLI successor)? It is the only tool found with that command name.
2. "Pi": the `pi` coding agent from earendil-works (formerly badlogic/pi-mono, pi.dev)? Inflection's Pi is a chat assistant, not a coding agent.
3. "MiniMax": the `mcode` CLI, or MiniMax models inside OpenCode? The dry run differs.
4. Publish the OpenRouter price table in `docs/model-routing.md`, or keep it in `run/`?
5. Keep the third-party Alpine build image as the default, or build ours from `ci/Dockerfile.alpine`?
6. Which `docs/perf/` analyses are public material (product comparisons, PHP path)?
7. Author identity and e-mail for the single public import commit.

## Verification

Commands behind the counts (run in this repository):

```sh
git grep -c -i -E '<kind pattern>'                         # tracked
git ls-files -o --exclude-standard | xargs grep -c -i -E '<kind pattern>'
git log -p --all | grep -E '^-[^-]' | grep -i -E '<pattern>'   # deleted lines: none
git log --all --format=%B | grep -c 'Claude-Session'          # 10
```

## Maintainer notes

Maintainer-specific notes for this page are kept outside the repository.
