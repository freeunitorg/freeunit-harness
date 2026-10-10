# Agent tools: agy, dsh and OpenCode next to Claude Code and Codex

Status: draft, 2026-10-07. The single page for agent tools; two session plans were merged into it.
Facts are marked "run here", carry a vendor URL, or say "unverified". Re-check a row before use.
Related: [review-pipeline.md](review-pipeline.md), [model-routing.md](model-routing.md),
[public-harness-plan.md](public-harness-plan.md), [perf/perf-lab.md](perf/perf-lab.md).

**agy** is the Google Antigravity CLI, successor of Gemini CLI for individual users since 2026-06-18
([migration](https://antigravity.google/docs/cli/gcli-migration)). **dsh** is DeepSeek Harness, npm
`@deepseek-ai/dsh` ([repo](https://github.com/deepseek-ai/deepseek-harness)). **OpenCode** is at
https://opencode.ai.

## Recommendation

1. One canonical `AGENTS.md` and one set of skills; every tool here reads both.
2. The lane rule is in section 9: Claude pays for one adversarial pass, open models do the cheap
   passes and scripted jobs, Codex reviews one scoped commit second, agy cross-checks.
3. Generate the thin per-tool files with `make agents`. Review in VS Code with clangd; PhpStorm for PHP.
4. Embargoed work stays in Claude Code; `agy` offers Claude models, but through Google.

## 2. Install and update

| | Install | Update | Source |
|---|---|---|---|
| agy | `curl -fsSL https://antigravity.google/cli/install.sh \| bash`; sign in with a Google account | `agy update` | https://github.com/google-antigravity/antigravity-cli |
| dsh | `npx @deepseek-ai/dsh web` (needs Node); not on PATH after that | `npx -y @deepseek-ai/dsh@latest ...`; the npx cache path changes per version, so call it through `npx`, never by path | https://github.com/deepseek-ai/deepseek-harness |
| OpenCode | installer from https://opencode.ai; `/connect` stores provider keys | `opencode upgrade` (Claude Code and Codex: `claude update`, `codex update`) | https://opencode.ai/docs/providers/ |

## 3. Project instructions, and a local twin

| Tool | Reads | Order and limits | Local twin | Source |
|---|---|---|---|---|
| agy | `AGENTS.md`, `GEMINI.md`, `.agents/rules/*.md`; global `~/.gemini/GEMINI.md`, `~/.gemini/AGENTS.md` | walks from the cwd up to the repo root; 24 KB per file; 20k-token rules budget; relative order of the two names not documented | none documented; a gitignored `.agents/rules/local.md` should work (inference) | https://antigravity.google/docs/cli/gcli-migration , bundled `agy-customizations` guide (run here) |
| dsh | `AGENTS.md` then `CLAUDE.md` in each directory from the git root to the cwd; global `AGENTS.md` in `$DSH_HOME` first | identical siblings print once; 65,536-byte budget; broad files dropped before narrow ones | native: `AGENTS.local.md` and `CLAUDE.local.md` load after the base files | https://www.npmjs.com/package/@deepseek-ai/dsh-agent-instructions |
| OpenCode | `AGENTS.md`; `CLAUDE.md` only if no `AGENTS.md`; global `~/.config/opencode/AGENTS.md`, then `~/.claude/CLAUDE.md` | first match wins per category; `instructions` in `opencode.json` adds files and globs | none; list `AGENTS.local.md` under `instructions` (a missing file is untested) | https://opencode.ai/docs/rules/ |

Trap: text only in `CLAUDE.md` is invisible to OpenCode and Codex. Put every shared rule in `AGENTS.md`;
keep `CLAUDE.md` to Claude-only notes plus an `@AGENTS.md` import (https://code.claude.com/docs/en/memory).

## 4. Commands, agents, skills

| | agy | dsh | OpenCode |
|---|---|---|---|
| Skills | `.agents/skills/<n>/SKILL.md`; `.gemini/skills` no longer read. Source: gcli-migration page above | `<root>/.dsh/skills`, `.agents/skills`, `~/.dsh/skills`, `~/.agents/skills`, extra dirs; `<n>/SKILL.md` or flat `<n>.md`, one level deep. Not `.claude/skills`. Source: npm `dsh-skill-filesystem` | `.opencode/skills`, `.claude/skills`, `.agents/skills` and the same three under `~/`. https://opencode.ai/docs/skills/ |
| agentskills.io | core fields `name`, `description` honoured; `metadata.icon` used (`agy changelog`, run here); no compliance statement | `name`, `description`, `metadata` honoured; adds `whenToUse`, `disable-model-invocation`, `user-invocable` | spec fields `name`, `description`, `license`, `compatibility`, `metadata` ([spec](https://agentskills.io/specification)) |
| Commands | skills double as slash commands; no separate command file | skills with `user-invocable` (default on) | `.opencode/commands/*.md`; frontmatter `description`, `agent`, `model`, `subtask`; `$ARGUMENTS`, `$1`, `` !`cmd` ``, `@file`. https://opencode.ai/docs/commands/ |
| Agents | `.agents/agents/<n>/agent.md`, frontmatter `name`, `description`; `--agent <n>`. https://antigravity.google/docs/cli/commands/agents | agent presets `standard` and `ptc` ship with the web app; `ptc` switches the tool presentation to programmatic tool calls; delegation `maxDepth` default 1, `maxActiveSubagents` default 8. Source: npm `dsh-subagent` | `.opencode/agents/*.md`; `description`, `mode`, `model`, `permission`, `steps`, `temperature`. https://opencode.ai/docs/agents/ |

Checked here: `opencode agent list` in the harness shows `perf-runner`; `opencode debug skill` in the
FreeUnit checkout lists its `.agents/skills` and `~/.claude/skills`. That checkout has no `.opencode/`,
so the perf-lab note to copy the two files to `~/.config/opencode/` is needed.

All three read `.agents/skills`, so one `skills/` directory linked there serves them and Codex. dsh does not read `.claude/skills`.

## 5. Scripted use

| | agy | dsh | OpenCode |
|---|---|---|---|
| One shot | `agy -p "$(cat prompt.md)" --output-format json --model gemini-3.8-flash-medium` from the project directory; `--add-dir` for more | `dsh --profile headless --json - < prompt.md` from the project directory; run `npx @deepseek-ai/dsh` while it is not on PATH | `opencode run --dir <dir> --agent <a> -m <provider/model> --format json -f prompt.md "<message>"` |
| Output | `text`, `json`, `stream-json`; `--json-schema` forces a result shape | final text, or `--json` events (`session`, `tool_call`, `final`); strings capped at 8 KiB; reasoning goes to stderr | formatted text, or `--format json` raw events |
| Exit codes | docs: 0 ok, 1 error, 2 unsupported streaming. The installed changelog says a mid-run model error exits 3 with an `AGY_ERROR` line on stderr, so the docs are stale | 0 only for a completed turn, else 1 | not documented (unverified); parse the JSON events and wrap in `timeout` |
| Caps | `--print-timeout`, `--effort`; no turn or token flag in `agy --help` (run here) | no turn budget (docs list it as a limit); per-request `maxTokens` in config; wrap in `timeout` | agent `steps`; no token flag; `opencode stats` reports cost afterwards |
| Unattended permissions | unapproved tools are soft-denied; allow list in `settings.json`; `--dangerously-skip-permissions` | preset sets sandbox and approval together; what headless does with an `ask` approval is unverified | `--auto` approves all but denied rules; prefer deny rules in the agent |
| Sources | https://antigravity.google/docs/cli/headless | https://www.npmjs.com/package/@deepseek-ai/dsh-headless | https://opencode.ai/docs/cli/ |

Known risk: agy 1.0.0 dropped stdout when it was not a terminal and still exited 0
(https://github.com/google-antigravity/antigravity-cli/issues/76, open). The 1.2.16 changelog does not
mention a fix, so smoke-test `agy -p "say ok" | cat` before a loop, and fail on an empty result.

Handoff command lines, one process each (flags confirmed with `--help` here; sources above, and
https://code.claude.com/docs/en/headless for Claude Code):

| From the harness | Command |
|---|---|
| Claude Code | `claude -p --model sonnet "<task>"`; in scripts add `--bare` and `--max-budget-usd N` |
| Codex, structured | `codex exec --json --output-schema findings.json -C <dir> -o out.txt "<task>"` |
| Codex, review | `codex review --commit <sha>` or `--uncommitted`, in a clone outside the repo. Not `--base` on a configured checkout: it rewrites the root `Makefile`, and on 46 files cost 1.1M to 2.6M tokens against 39k to 193k for one commit ([bots-and-gh.md](../skills/freeunit-review/references/bots-and-gh.md)) |
| agy | `agy -p "<task>" --output-format json --effort high`; keep the prompt right after `-p`, since flags before it are untested |
| dsh | `dsh --profile headless "<task>"` |

A handoff starts a new process with an empty context, so every rule it obeys must come from its own
instruction files. One canonical `AGENTS.md` keeps that cheap; a rule block copied into three files is
paid again by every call, subagent and fork.

Loops. Each job is one process. A shell `for` over job files calls `tools/agent-run <tool>
<prompt-file> <dir>` (planned, not written yet), which adds `timeout`, saves raw output under `run/agent-runs/`, prints one line.

## 6. Models and cost

| | Models | Cost shape | Tool use and long context |
|---|---|---|---|
| agy | `agy models` lists Gemini 3.8, 3.7 and 3.6 Flash, Gemini 3.1 Pro, Claude Opus 5.5, Claude Sonnet 5.5 and GPT-OSS 120B; custom models in `settings.json` | plan quota and AI credits; no per-token price in the docs read (https://antigravity.google/docs/cli/credits) | no reliability data in the docs; unmeasured here |
| dsh | `deepseek-flash` by default; the `pi-ai` adapter routes other providers and OpenAI-compatible gateways (npm `dsh-llm-pi-ai`) | $0.023 in, $0.471 out per 1M tokens at the OpenRouter list ([model-routing.md](model-routing.md)); dsh bills the official API, so it can differ. Anchored review: 0.26M cache-read tokens, about $0.003 ([review-pipeline.md](review-pipeline.md)) | the harness's own measure: a verbose prompt costs 31 times the anchored one; some claims are wrong, verify each |
| OpenCode | 75+ providers, OpenRouter, DeepSeek and Z.AI sections (https://opencode.ai/docs/providers/); OpenCode Zen free models | per provider; `opencode stats` reports it afterwards; free lane below | the docs say only a few models are good at both code and tool calls (https://opencode.ai/docs/models/); no per-model figures |

The OpenCode free lane, catalog prices per 1M tokens (read here 2026-10-07; they move):

| Route | Input / output | Context, output limit | Note |
|---|---|---|---|
| `zai-coding-plan` GLM 5.3, 5.2, 5.3-flash | $0 / $0 | 1M, 131k | $0 per token under the Z.AI plan; plan limits unverified |
| OpenCode Zen free models (`deepseek-v4-flash-free`, `glm-5-free`, `glm-4.7-free`, `kimi-k2.5-free`, `minimax-m2.x-free`, `nemotron-3.5-lightning-free`, `grok-code`, and about 30 more) | $0 / $0 | varies | whether free tiers keep or train on prompts: unverified, so public code only |
| `deepseek` direct, flash / pro | $0.15 / $0.60 and $0.66 / $1.98 | 1M, 393k | differs from the OpenRouter list in [model-routing.md](model-routing.md); trust the bill |
| `google` gemini-3.1-flash-lite / gemini-3.8-flash | $0.25 / $1.50 and $0.75 / $3.75 | 1M, 65k | cross-check lane |

Embargo rule ([model-routing.md](model-routing.md)): embargoed work is Claude only. agy can select a
Claude model, but the request goes through Google, so agy is "not Claude" here. Its data terms: unverified.
No per-model tool-call figures exist for any open model.

## 7. MCP, tools, sandbox

| | MCP | Shell, files, web | Sandbox and permissions |
|---|---|---|---|
| agy | `agy mcp add/list`; `~/.gemini/config/mcp_config.json`, workspace `.agents/mcp_config.json`; key `serverUrl`, not `url` (gcli-migration page) | shell, files; browser through the chrome-devtools-mcp plugin | `--sandbox`, `nsjail` on Linux; levels `request-review`, `always-proceed`, `strict`; allow and deny rules (https://antigravity.google/docs/cli/features) |
| dsh | `dsh-mcp-client`, stdio or Streamable HTTP, none on by default, tools named `mcp__<server>__<tool>`; reads Claude Code `hooks.json` via `dsh-hooks-claude-code` (npm pages) | bash, file tools, web search and fetch, subagents | Linux `bwrap` or Landlock; modes `read-only`, `workspace-write`, `danger-full-access`; fails with `SANDBOX_UNAVAILABLE` rather than run open (npm `dsh-sandbox-local`) |
| OpenCode | `mcp` key in `opencode.json`, `local` or `remote`; no `.mcp.json` import (https://opencode.ai/docs/mcp-servers/) | bash, edit, read, webfetch, websearch | allow, ask, deny per tool, last matching pattern wins; no OS sandbox in the docs read (https://opencode.ai/docs/permissions/) |

No MCP file is shared (Claude Code `.mcp.json`, OpenCode `opencode.json`, agy `mcp_config.json`,
dsh a YAML patch). The workflow needs none today: `gh`, `rg` and harness scripts cover it.

## 8. IDE fit

| Tool | PhpStorm | VS Code |
|---|---|---|
| Claude Code | JetBrains plugin; diffs open in the IDE diff viewer; selection and diagnostics shared; `/ide` from an outside terminal (https://code.claude.com/docs/en/jetbrains) | extension with inline diffs, `@` mentions, plan review (https://code.claude.com/docs/en/vs-code) |
| Codex | no own plugin; pick Codex in the AI chat (https://learn.chatgpt.com/docs/codex/ide) | official extension, same page |
| OpenCode | through ACP: add `opencode acp` to `acp.json` (https://opencode.ai/docs/acp/); JetBrains is not in its IDE page | extension, auto-installs from the integrated terminal, shares selection and tab (https://opencode.ai/docs/ide/) |
| agy | Antigravity extension for JetBrains 2026.2.1 and later; source https://antigravity.google/blog/antigravity-ide-extensions . A third-party "Antigravity Companion" plugin exposes editor state to agy over MCP (https://github.com/matasarei/antigravity-companion) | Antigravity extension, same page |
| dsh | none found; the CLI package lists ACP (`dsh --profile acp`) but I did not test it in an IDE | none found |

PhpStorm lists any ACP agent on its AI Assistant agents page, with diff review and rollback
(https://www.jetbrains.com/help/phpstorm/agents-in-phpstorm.html); whether that plugin is enabled here: unverified.

What a reviewer gains, as "IDE action replaces agent action":

| Agent action | IDE action instead |
|---|---|
| `git diff` read through hunks | the IDE diff view of the agent's worktree; use the branch diff, not the working tree |
| `rg` and `sed -n` to follow a symbol | clangd go to definition and find references; `ast-grep` misses macro-heavy C (see `AGENTS.md`) |
| "does it build, do the C tests pass" | a run configuration that calls the harness (`tools/unit-build -t remote ... tests`); build and tests run on the build host, not the laptop |
| "explain this selection" | the `@file#L1-99` shortcut (`Alt+Ctrl+K` in Claude Code and OpenCode) sends the range |
| agent says "no functional change" | `git patch-id --stable` in the terminal; no IDE feature proves it |

clangd needs compile flags. A `compile_commands.json` goes stale when the tree moves, and FreeUnit
has no generator target; `bear` can make one. Proposed and untested: in the PR worktree run
`./configure` (no build) and add a `.clangd` file with `CompileFlags: Add: [-Isrc, -Ibuild/include]`.
Never run `./configure` in the shared checkout. PhpStorm ships no C or C++ support; CLion is the
JetBrains C IDE (not confirmed in a vendor page; the local plugin list agrees). LSP4IJ
(https://github.com/redhat-developer/lsp4ij) can attach clangd to JetBrains; untested.

IDE recommendation. Review the agent's branch in VS Code with clangd in a PR worktree, and ask about a
selection with the Claude Code or OpenCode extension. Keep PhpStorm for the PHP module, Drupal and PHP
test apps; an Antigravity IDE adds nothing a reviewer needs.

## 9. Which tool for which job

**The lane rule.** One rule replaces the lane tables of the two earlier session plans. Pick by what
the job risks, then by price.
1. Correctness of a risky path, a release, anything embargoed: Claude Code, one Fable pass on the
   folded head. 2. Review passes in the pipeline's order: local gates, dsh read-only, Codex on one
   scoped commit, fold, Fable once ([review-pipeline.md](review-pipeline.md)). 3. Scripted and
   mechanical work with no judgement: an OpenCode agent with deny rules, an open model, a worktree.
   4. A second vendor's view of a design: agy. 5. When Claude weekly use is above 80 percent,
   Claude stops executing and only reviews.

This replaces "Claude decides, DeepSeek builds, Codex reviews, Antigravity cross-checks". Codex reviews
second, and its quota is finite (15 limit comments on PR 452 in 8 days). DeepSeek reviews first and builds
only mechanical work, through OpenCode (permission file, JSON events); no recorded run shows dsh
implementing. Codex and agy are plan-limited, so "protect the one metered window" is wrong.

| Job | Tool | Why | Entry point (generated by `make agents`) | Band | Must not |
|---|---|---|---|---|---|
| Adversarial review | Claude Code, Fable | found what three Codex rounds missed on PR 452 | `agents/adversarial-reviewer.md` to `.claude/agents/adversarial-reviewer.md`; run with `claude -p` | 208k tokens, 18 min (maintainer's figure, unverified) | be given the fix, or run twice on one SHA |
| Cheap first-pass review | dsh headless, `deepseek-flash` | PR 588: 6 findings in 2 comments for cents | new `skills/freeunit-review-first-pass/SKILL.md` (the `.pr536` template moved in), started by `tools/agent-run dsh` | about $0.003 anchored, $0.08 verbose | post under the maintainer's name by itself, see embargoed work, or have a finding trusted unverified |
| Bulk and scripted: perf-lab runner | OpenCode, open model | one command, one line, a JSON verdict to copy | `.opencode/commands/perf-job.md`, `.opencode/agents/perf-runner.md` (exist today) | 30k to 80k tokens per run (estimate) | compute a number, edit a job, or explain a surprising result |
| Bulk: CI log triage | OpenCode, free-lane model; agy as fallback | log reading is cheap and judgement-free; keeps Codex quota for reviews | new `.opencode/commands/ci-triage.md` and agent `ci-triage` (read-only, runs `tools/new-failures.sh`, `gh run view --log-failed`) | 20k to 60k (estimate, unmeasured) | decide a failure is flaky, or rerun a workflow |
| Second review pass | Codex app on ready, or `codex review --commit <sha>` | reads local state and control flow; found a P2 each round on PR 452 | GitHub app; the CLI as in section 5 | 39k to 193k tokens per commit | review a many-file `--base` range; retry after a limit comment the same day |
| Code edits with judgement | Claude Code (Opus, Sonnet for routine) | lifetime and ordering bugs need the strongest C reasoning | `CLAUDE.md` with `@AGENTS.md`, `.claude/skills` | the scarce quota; the heaviest line item | do bulk or mechanical edits; touch git |
| Fix from a review list; mechanical bulk edits, test scaffolding | OpenCode `/fold`, or Codex (`codex exec`) | mechanical once each finding has an input that fails | new `.opencode/commands/fold.md`, agent `folder` (edit allowed, git denied) | 40k to 200k per list (scoped Codex review range; Opus fix pass was 380k) | judge a finding real without running its input; fold into a risky path; amend or push |
| Cross-check of a design | agy | a second vendor; large reads | `.agents/agents/<n>/agent.md`, `agy -p --agent <n>` | plan quota | embargoed work; heavy refactors until the exit code and stdout issue is cleared |
| Human review and merge | VS Code (clangd) or PhpStorm, plus `gh` | the maintainer owns git and the merge | proposed `tools/open-pr N`: worktree, `.clangd`, `code <dir>` | no tokens | be replaced by "looks good" from an agent; merge when the reviewed SHA is not the head |

The order is the pipeline's. Claude quota is the scarce resource (the numbers are kept outside the repository);
the cheap-pass, triage and fold rows move work off it.

## 10. Shared configuration design

The agent survey, the proposed layout, the per-tool skill roots, the start-up byte budgets and the
symlink check are in [public-harness-plan.md](public-harness-plan.md) section 4, the one source for
them. This section adds the layers and the files `make agents` must generate.

Layers, from canonical to local:

| Layer | Files | Tracked | Written by |
|---|---|---|---|
| Canonical | `AGENTS.md`, `skills/<n>/SKILL.md` with `name` and `description` frontmatter, `agents/*.md` role briefs, `agents/roles.yaml` (new: per role mode, model hint, permissions), `recipes/*.md` | yes | hand |
| Generated, thin | the list below, each with a `GENERATED by tools/gen-agents.py` header | yes | `make agents` |
| Local, private | `AGENTS.local.md`, `CLAUDE.local.md`, `.claude/settings.local.json`, `.agents/rules/local.md`, `~/.config/opencode/`, `run/lab.env`, `run/` | no, gitignored | hand |

Last line of `AGENTS.md`: "If AGENTS.local.md exists, read it first." Every tool above can read a
file, so this works where no native twin exists. dsh and Claude Code also load their own local files.

Rules for the split. A fact true for every tool lives in `AGENTS.md`, once. A per-tool file holds only
that tool's mechanics (permissions, sandbox, model, plugin ids, local paths), never rule text. A note
not needed at start goes to a notes directory that `AGENTS.md` links. Start-up text has a byte budget per tool
(public-harness-plan.md section 4). A per-tool extension (a dsh profile bundle, a Claude Code
plugin, a Codex `config.toml`) may point at shared content and add tool-only behaviour; it may not
restate a rule. Shared and written once: skills, notes, and the headless review contract (same task
text, flags and output parsing, through `tools/agent-run`).

Whether dsh expands an `@` import is unverified. Use the symlink when there is no Claude-only text,
and the import file when there is.

Files `make agents` must produce (the four marked "new" do not exist today):

| File | For | From |
|---|---|---|
| `CLAUDE.md` (`@AGENTS.md` plus Claude-only notes) | Claude Code; dsh and OpenCode see it as fallback | template |
| `.claude/skills` (symlink to `../skills`) | Claude Code | link |
| `.claude/agents/<n>.md` | Claude Code | `agents/`, `roles.yaml` |
| `.agents/skills` (symlink to `../skills`) | Codex, agy, dsh, OpenCode | link |
| `.agents/agents/<n>/agent.md` (new) | agy | `agents/`, `roles.yaml` |
| `.opencode/agents/<n>.md` | OpenCode | `agents/`, `roles.yaml` (`permission`, `steps`) |
| `.opencode/commands/<n>.md` | OpenCode | `recipes/` frontmatter |
| `opencode.json` (`instructions`, permission floor, `lsp`) | OpenCode | template |
| `.clangd` (new) | clangd in VS Code and PhpStorm | template |
| `.gitignore` lines (new) | all | template |

Not generated, on purpose: `GEMINI.md` (agy reads `AGENTS.md`; a second rule file drifts), anything
for dsh or Codex (they read `AGENTS.md`, `.agents/skills` and the local twins), any MCP file.
`make agents-check` regenerates into a scratch directory and fails on a difference. Where
`core.symlinks` is false, copy. `tools/agent-run`, when written, is hand-written.

## 11. Not verified

- agy `-p` stdout when piped, on 1.2.16 (issue 76 is against 1.0.0); I made no agy or dsh model call.
- dsh headless with an `ask` approval; `DSH_TOOLS_MODE` selecting `ptc`; OpenCode `run` exit codes; a
  missing file under `instructions`.
- agy's precedence between `GEMINI.md` and `AGENTS.md`; a gitignored `.agents/rules/local.md`.
- Google's data terms and agy quotas; agy tool-call reliability; whether PhpStorm's AI Assistant is
  enabled here; dsh over ACP in an IDE; the `.clangd` flags.
- The Claude transcript counts in the private measurement note (deduplicated by message id; the last streamed
  `output_tokens` may be partial). `codex review` with `--base` and `--uncommitted` together; Z.AI plan
  limits; Zen free-tier data terms.
- Token bands marked "estimate", and the 208k and 380k figures (the maintainer's, no public trace).

## 12. Maintainer notes

Maintainer-specific notes for this page are kept outside the repository.
