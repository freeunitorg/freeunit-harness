# Automated reviewers and `gh` mechanics

## Trusting a bot review

**Verify every bot or agent finding against the actual diff before acting on
it — and verify every applied fix the same way.** Both directions matter: a
confident wrong finding wastes a round, and a plausible wrong fix ships.

**Never relay a validation claim you did not re-run.** This is the same rule
one step further: a finding is checked against the diff, and a *claim that
something was verified* is checked by running it again. A regression test
reported by a subagent as "validated both ways" could not run at all under
`--debug` — an `nxt_assert` fired first — and the claim reached a public issue
comment before anyone re-ran it. Re-run the negative half especially: "fails
without the fix" carries the argument, and is the half that quietly fails for
an unrelated reason (see the regression-proof note under
[Commands](#commands), and the [verify
skill](../../freeunit-verify/SKILL.md)).

**Check what the reviewer actually diffed.** Tools that derive their diff from
"the current checkout's branch" review the wrong code when you work in `git
worktree`s, which is the normal way to work in a shared clone. The failure is
quiet and flattering: a clean report, on somebody else's changes. Confirm the
file list it reports matches the file list in your PR before trusting the
verdict.

**A clean bot review is bound to a commit, and a force-push voids it.** After
amending, re-request the review and say what changed — a review of the SHA you
replaced is not a review of what merges. Worth stating explicitly when the
amendment is comment-only, so the second pass is cheap.

## Triggering, and accidentally re-triggering

**Naming a review bot in a reply re-triggers it.** The workflow matches the
literal handle anywhere in the comment body, so writing "`@codex` said X" — or
quoting a bot's own text back at it, including to say a finding was wrong —
starts a fresh run. Refer to it without the `@`, or accept that every response
costs another review cycle.

The workflow fires on *every* comment and then filters on the handle, so
mentioning one bot spawns skipped runs for the others. A watcher that treats
"run completed" as "review finished" reports results that do not exist — check
the conclusion is not `skipped`.

**A bot that edits its comment in place will not trip a `createdAt` watcher.**
Poll the workflow run, or the comment body, not the comment list.

## Commands

**`--force-with-lease` needs the full 40-character SHA.** A hand-shortened one
is treated as stale information and the push is rejected — or worse, accepted
against the wrong expectation.

**`stale info` against a branch GitHub already deleted is not a lease
problem — there is no ref left to hold a lease on.** A squash/rebase merge
lands the content under a *new* SHA (#211 merged as `6757ce6b`; its
branch-tip `76ab7f94` is not an ancestor of master), and GitHub deletes the
source branch on merge. Do not escalate a rejected lease to plain `--force`:
on a deleted branch that resurrects a commit whose content already merged
under a different hash, attached to a closed PR, and the push still would
not land in master. Verify first: `git ls-remote origin refs/heads/<branch>`
after an explicit fetch — an empty result means the branch is gone.

**A local `git rev-parse origin/<branch>` reads a cached tracking ref, not
the remote's live state.** It answers instantly and confidently for a branch
that no longer exists upstream. Fetch that one branch (`git fetch origin
<branch>`) or use `git ls-remote` before trusting what `origin/<branch>`
says locally.

**When proving a regression test fails without its fix, confirm which check
actually produced the exit code.** Neutralizing one guard can trip an
earlier, unrelated check first — a "ready" test intercepting before the
"router" test you meant to exercise ever runs. The run reports `rc=1` and
looks like proof, but the assertion you wrote never fired (see #228 on
`freeunitorg/freeunit` for the full trace). Read the failure output, not
just the exit status, before citing a test as having demonstrated anything.

**`git add -A` stages whatever the tree happens to be carrying, including
files you decided were harmless in a different context.** A
`.claude/settings.local.json` — agent-local state, not repo content — was
swept into a docs commit because a `gh` warning about it had been assessed as
harmless *earlier in the session*, under a different operation, and the
assessment was never re-checked when the operation changed. A judgement about
a file is scoped to the operation it was made for.

Inspect the commit, not the working tree: `git show --stat HEAD` **before
pushing** answers "what did I commit", which is the question that matters;
`git status` before committing answers "what is lying around", which is not.
The durable fix for local agent state is a `.gitignore` entry, so no future
`add -A` can pick it up.

**`gh pr edit` can fail on repositories with classic projects enabled**, with a
GraphQL error about `projectCards` deprecation, without changing anything. The
REST path works:

```bash
jq -Rs '{body: .}' BODY.md > body.json
gh api -X PATCH repos/OWNER/REPO/pulls/NUMBER --input body.json
```

**A rebase can invalidate line numbers in a commit message.** If the message
cites `file.c:1126` as evidence, a base that moved the function makes the
citation point somewhere plausible and wrong. Cite the function name; put line
numbers only where you will re-check them.

## `codex review` is not read-only against a configured worktree

Local `codex review --base origin/master` runs its own `./configure --build-dir=/tmp/unit…` and
**overwrites the repo's root `Makefile`** to `include /tmp/unit…/Makefile`. Afterwards `make` says
`Nothing to be done for 'all'` with rc=0 while `build/sbin/unitd` does not exist — a make no-op that
reports success (see the verify skill). After every local codex run: check `head -3 Makefile` still
says `include build/Makefile`, else re-run `./configure …` before trusting any build or test result.
(Found 2026-08-28 on #213.)

## A local build against OpenSSL 3.x cannot validate OpenSSL 4.0 compatibility

3.x keeps removed-in-4.0 symbols as deprecated aliases (e.g. `SSL_R_SSLV3_ALERT_*` → 4.0 has
only `SSL_R_TLS_ALERT_*`), so a local build, `codex review`, and even `-DOPENSSL_NO_DEPRECATED`
against 3.5 all pass while the 4.0 CI leg fails. For 4.0 claims, the enforcing leg
(`build-test.yml` `openssl4`, real 4.0.x + `-DOPENSSL_NO_DEPRECATED`) is the only gate that can
fail; run it before calling a 4.0 PR reviewed. (2026-08-28, PR #213: `nxt_openssl.c:1731-1742`.)

## Local `codex`: resume is for follow-up, not for re-review

Measured 2026-08-29, `codex-cli 0.150.0`, `gpt-5.6-sol` at the config default
`model_reasoning_effort = "high"`, on `release/1.36.1` (`cae19520`). Wall seconds, two
reps each; token totals read from the rollout files, not from the CLI.

| variant | command | diff | wall (reps) | tokens | found the bug? |
|---|---|---|---|---|---|
| a. fresh review | `codex review --base <sha> --title …` | 46 files | 218 / 216 / 256 | 2.59M | n/a (clean diff) |
| a'. fresh review, exec | `codex exec review --base <sha> --json -o m.txt` | 46 files | 206 / 188 | 1.14M | n/a |
| b. free-form prompt | `codex exec "Act as a code reviewer. Review the diff from 'git diff <sha>' …"` | 46 files | 197 / 254 | 2.04M / 2.71M | n/a |
| c. resumed session | `codex exec resume <id> --json "…re-review; report only new findings"` | 46 files | 100 / **17** | 102k for the whole 5-turn thread | n/a |
| d. single commit | `codex review --commit HEAD` | 2 lines | 235 / **85** | 193k | raised the only P2 of the whole run |
| a. fresh, planted bug | `codex review --uncommitted` | 1 line | 41 / 33 | 71k / 39k | **yes, P1, both reps** |
| b. free-form, planted bug | `codex exec "…review the uncommitted diff…"` | 1 line | 60 | 272k | **yes, P1** |
| c. resumed, planted bug | `codex exec resume <id> "…report only new findings"` | 1 line | **20 / 11** | ~0 marginal | **yes, P1**; rep 2 said "previously reported … remains" |

The planted bug was `dst = nxt_mp_alloc(mp, src->length + 1)` → `src->length` in
`nxt_str_cstrz()` (`src/nxt_string.c:75`), a one-byte OOB write of the NUL. Every variant found
it, so on a small diff the forms differ in cost, not in detection.

**Recommendation: scope the diff, don't reuse the session.** `--commit <sha>` / `--uncommitted`
against a couple of changed lines costs 39-193k tokens and 33-85s; the same reviewer pointed at a
46-file `--base` range costs 1.1-2.6M tokens and 3-4 minutes and returned *fewer* findings, because
the signal drowns. Free-form `codex exec` "act as a reviewer" is the worst of both — the most
tokens (up to 2.7M) for prose that has no priority tags or structured `file:line` block. Use
`codex review`, one PR commit at a time.

Resume earns its place only *after* a review: `codex exec resume <id> "explain finding 2"` answers
in 9-20s at essentially zero marginal cost, because the thread carries the finding text. It is not
a cheaper re-review — the resumed turn re-derives the diff with its own `git` calls, and by the
second resume it stops looking altogether (17s, two commands, "no new findings").

### Exact mechanics in this version

- Resume forms that exist: `codex resume [ID|--last]` and `codex fork` (both interactive TUI),
  `codex exec resume [ID|--last] "prompt"` and `codex exec fork` (non-interactive). There is **no
  way to run the review harness inside a resumed session** — `review` is a subcommand, never a
  prompt, so a resumed "review this again" runs the ordinary agent, not the reviewer.
- `codex exec review --json` prints `{"type":"thread.started","thread_id":…}` as its first line;
  that id is what `codex exec resume` takes. Plain `codex review` prints the summary on stdout and
  its progress trace on stderr, and gives you no id — grab it from
  `~/.codex/sessions/YYYY/MM/DD/` by mtime instead.
- **`--json` reports zero tokens.** `turn.completed` carries
  `usage: {input_tokens: 0, …}` for every run. Real usage lives only in the rollout file, in
  `payload.type == "token_count"` records.
- **One review writes two rollout files under one session id**: a `source: "exec"` parent and a
  `source: {"subagent": "review"}` file holding the actual work. `codex exec resume <id>` attaches
  to the *parent*, whose entire record of the review is a single `<user_action>` user message
  containing the prose findings — not the diff, not the 19 tool calls. That is why resume is cheap
  and why it re-runs `git` to answer anything concrete.
- Sessions created by `codex exec` do not appear in the interactive `codex resume` picker without
  `--include-non-interactive`.

### Caveats

- **`--base <sha>` reviews `merge-base(sha, HEAD)..HEAD`, not `sha..HEAD`.** Passing a commit that
  is not an ancestor silently widens the review: `--base 5176907` on `release/1.36.1` reviewed
  `73eece04..HEAD` — 46 files, 882 insertions — instead of the intended 5-file, 17-line diff. Check
  with `git merge-base <sha> HEAD` before believing a review's scope, and prefer `--commit`.
- **A resumed session states things it no longer has.** Asked to restate its own finding without
  running commands, a resumed review thread reproduced the right file, line and diagnosis but quoted
  the code as `p = nxt_mp_alloc(…)` when the source says `dst = …`. It is reconstructing from the
  prose summary. Never take a code quote, a line number or a base SHA from a resumed session as
  evidence — re-read the file.
- The `workspace-write` sandbox and `approval_policy: never` apply to *every* form, `exec` and
  `resume` included, so the `Makefile` rewrite above is not specific to `codex review`. It did not
  fire in any of these runs (`git status` stayed clean) because a docs/one-line diff gave the
  reviewer no reason to build — which means the absence of the side effect on one run says nothing
  about the next.
- **A stray `.codex` *file* in the repo root breaks codex in the repo and in every linked
  worktree**: `Error loading config.toml: Failed to read project hooks config file
  <checkout>/.codex/config.toml: Not a directory (os error 20)`, exit 1 in ~0.06s, before
  any model call. Codex resolves the project root of a linked worktree to the *main* repo
  directory, so a throwaway worktree does not escape it, and `--ignore-user-config` does not skip
  the project hooks lookup. On 2026-10-07 `<checkout>/.codex` was a 0-byte read-only file.
  Until it is removed or made a directory, run codex from a `git clone --local` outside the repo.

## A codex review that never ran looks exactly like a clean one

Measured on `codex-cli 0.150.0`, 2026-08-29, against a one-line uncommitted diff:

| invocation | rc | what happened |
|---|---|---|
| `codex review --model gpt-5.6-sol --uncommitted` | 2 | `error: unexpected argument '--model' found` — `review` takes no `--model`; it is a **global** option and must precede the subcommand (`codex -m <model> review …`). Nothing was reviewed. |
| `codex -m 5.6-sol review --uncommitted` | 1 | the short name is *accepted* by the CLI with only a `warning: Model metadata for '5.6-sol' not found`, then every turn fails with a 400 (`The '5.6-sol' model is not supported…`) and the run prints `Review was interrupted. Please re-run`. Nothing was reviewed. |
| `codex exec --model 5.6-sol "…"` | 1 | same 400, same warning-not-error handling of the name. |

Two things to take from this. **The model name must be the full one** —
`gpt-5.6-sol`, not `5.6-sol`; an abbreviation is a warning, not an error, and
the failure surfaces one layer down as an API 400. And **an empty findings list
is not a clean review**: both failures above produced no findings, exactly like
a diff with no problems. Decide from the output, not from the shape of it —
`grep -E 'ERROR:|was interrupted' <output>` before recording a review as clean,
and treat any non-zero rc as "did not run" rather than "found nothing".

(The premise this section was written to record — that codex exits **0** on a
usage error or an unsupported model, so a status check reads a failed review as
clean — did **not** reproduce on 0.150.0: the statuses were 2 and 1. The
dangerous half is real regardless, because the empty output is identical.)
