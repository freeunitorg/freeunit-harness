# Review pipeline: who reviews a FreeUnit change, in what order, at what cost

**Status: draft (07.10.2026).** Built from the PRs, workflow runs and session logs
named below. A number marked "brief" came from the maintainer and has no public
trace; it is not verified. Model prices are in [model-routing.md](model-routing.md).
Embargoed security work never enters this pipeline except through Claude (hard rule there).

## The rule in five lines

1. Run the free checks first: local gates, then DeepSeek, then Codex.
2. Fold every cheap finding into one push. Then pay for Fable once, on that head.
3. Mark the PR ready only after the Fable fold. Codex then runs once on the final head.
4. Re-review after a fold only if the fold touched a risky path (list below).
5. A push costs a full CI run and may cost a Codex review. Batch changes into one push.

## Pipeline

| # | Stage | Who | Trigger | Good at (evidence) | Cost per review | Skip when |
|---|---|---|---|---|---|---|
| 0 | Local gates | scripts, run by Haiku or Sonnet | before every push | whitespace, `-Werror` build, C tests, `--hardening=strict`, `check-test-hooks.sh`; catches what CI would catch | no tokens; minutes on the build host | never; a docs-only change skips the build steps |
| 1 | DeepSeek V4.1 Flash | a `dsh` session, started by the maintainer or a peer | by hand today; run after each fold, on the head about to be pushed | [PR 588](https://github.com/freeunitorg/freeunit/pull/588): 2 comments, 6 findings, several shown by planted-error runs; the author confirmed the 3 in the second comment. Weak spot: some claims are wrong ([PR 508](https://github.com/freeunitorg/freeunit/pull/508) item 1 was disproved by measurement) | anchored prompt: 14 turns, 0.26M cache-read tokens (about $0.003 at $0.010 per 1M); verbose prompt: 8.11M (about $0.08). Fresh input and output not measured | docs-only or changelog-only; embargoed work |
| 2 | Codex app | GitHub app `chatgpt-codex-connector` | open for review, mark ready, `@codex review`; the status table also shows "New commits" (see open questions) | [PR 452](https://github.com/freeunitorg/freeunit/pull/452): 3 rounds, one P2 each, all three confirmed and fixed by the author. PR 588: one inline finding (file mode 100755), fixed. Reads local state and control flow | plan quota, no price visible. PR 452 got 15 "usage limits" comments in 8 days | the last bot comment on this head is a limit comment; do not retry the same day |
| 2b | Codex CLI | `codex review --commit <sha>` in a clone outside the repo | only when stage 2 is silent for another reason than quota | 39k to 193k tokens, 33 to 85 s per commit ([bots-and-gh.md](../skills/freeunit-review/references/bots-and-gh.md)) | the same account quota as stage 2, shown on [PR 316](https://github.com/freeunitorg/freeunit/pull/316) on 2026-09-10 | the app hit its limit: the CLI will too |
| 3 | Claude Action | `claude.yml`, Opus with 1M context | comment `@claude review` (owner, member, collaborator only) | PR 452: 3 min 4 s, "no blocking bugs", could not build or run tests. One data point | repo OAuth token quota; 30 minute job limit | when stages 1 and 2 already ran; optional |
| 4 | Fold | Sonnet for mechanical findings, Opus for judgement | after stages 1 to 3 | see "Sonnet and Haiku" below | tokens of one session; Opus fix pass on PR 452 was 380k (brief) | no findings held |
| 5 | Fable gate | Fable, brief from [adversarial-reviewer](../agents/adversarial-reviewer.md) | once per PR, on the folded head | PR 452: found what 3 Codex rounds missed, 208k tokens, 18 min (brief). On 2026-09-05 Codex found 4 P1 in 10 branches that had passed Fable, so the two miss different things | 208k tokens and 18 min on PR 452 (brief) | change touches no risky path (see below), or it is docs, changelog, version bump, CI config |
| 6 | Delta check | Opus with the adversarial-reviewer brief, on the delta only | after the Fable fold, if the fold touched a risky path | disproves "the fold changed only X" | a fraction of stage 5; not measured | the delta is outside the risky paths: rerun stage 0 only |
| 7 | Merge | the maintainer | CI green, no open finding | owns git: commits, rebases, force-push | | |

Risky paths (a proposal, from the bugs listed in [model-routing.md](model-routing.md)):
`nxt_port_*`, `nxt_unit.c`, `nxt_router*`, `nxt_http_*` parsers, shared-memory records,
anything that frees, retries, orders or tears down, and any text about a vulnerability.

## Order, and how not to pay twice

Both orders have evidence. Fable then Codex found four real P1 on 2026-09-05. On PR 452
Fable found what Codex missed. Neither order is proven better, so choose by cost: cheap
and automatic first, then the one expensive pass on a head that no longer moves.

**Pay Fable once.**
- Fold stages 0 to 3 first, so Fable reads one stable head.
- Note the 40-character SHA that Fable reviewed. Keep it in the PR notes.
- After the fold run `git diff --stat <gated-sha>..HEAD`. If a risky path is in it, run stage 6.
  If not, run stage 0 only.
- After a rebase, prove the code is unchanged with `git patch-id --stable`, not by reading.
- Never ask Fable "does it hold" twice for one SHA. The second opinion on one SHA is Codex.

**Keep Codex under its limit.**
- Open as draft. Run stages 0, 1, 5 on the draft. Mark ready once.
- One push per review round. Put DeepSeek, Fable and CI findings in one amend.
- Never push only for trailers. A comment trailer is a body PATCH and runs no CI.
- Never write `@codex` in a reply. The workflow matches the handle and starts a new review.
- Read the last bot comment before asking. A limit comment is not a review.

## Sonnet and Haiku, without judgement

| Job | Tool | Model |
|---|---|---|
| Run the gates and report exit codes | `tools/check-style`, `tools/ci-local`, `.github/scripts/check-test-hooks.sh`, `tools/openapi-example-check.py`, `tools/new-failures.sh` (new failures only) | Haiku, with a fixed command list |
| Build and test off the laptop | `tools/unit-build -t remote ... tests`, `tools/test-isolated --src <worktree>`, `unit-build verify` | Haiku or Sonnet |
| Watch CI to a conclusion | `tools/watch-run.sh`, agent [ci-watcher](../agents/ci-watcher.md) | Haiku |
| Triage a red run | `tools/ci-cost.py`, `gh run view --log-failed`, `tools/new-failures.sh` against the base; return the first error line per job | Sonnet |
| Fold a mechanical finding | a typo, a comment, a file mode, a table row in `src/test/nxt_tests.c`, a doc anchor | Sonnet |
| Write the measurement section | from command output the author supplies; quote the number and the command | Sonnet, `EFFORT: MEDIUM` |
| Tabulate bot findings | one row per finding: source, `file:line`, claim | Sonnet |

They must not: decide that a finding is real without running its input; fold a
finding in a risky path; write or post security text; amend, rebase or force-push (the
maintainer owns git); post to GitHub under the maintainer's name; call a change "no
functional change"; or relay a validation claim they did not re-run. Haiku ignores an
`EFFORT` level upward, so bound its work with a fixed file list and command list.

## CI

CI cost per workflow, what runs on which event, and the open and done changes are in
[ci-rebalance-2026-10-08.md](ci-rebalance-2026-10-08.md). The earlier audit is
[ci-matrix-audit-2026-10-07.md](ci-matrix-audit-2026-10-07.md). For review planning one
rule holds: every push runs a full CI, so batch the findings of one review round into one push.

## Requesting a review: cheat sheet for agents

| Bot | Request | Wait | Reads as |
|---|---|---|---|
| Codex app | post `@codex review` alone, once per head | 6 to 9 minutes on PR 452 | inline comments with a `P1` or `P2` badge, review body "Codex Review ... Reviewed commit: `<sha10>`". No findings: a thumbs-up reaction, or "Didn't find any major issues". Newer runs edit one "Codex Review Summary" comment in place (a table with Status, Commit, trigger). A limit comment ("You have reached your Codex usage limits for code reviews") is not a review |
| Claude Action | `@claude review` | about 3 minutes | `claude[bot]` comment: "Claude finished ... in 3m 4s", checklist, then a Verdict |
| DeepSeek | no trigger comment; ask the maintainer to start the session | minutes | a plain comment from the maintainer's account: "Reviewed at `<sha>`", bullets of `path:line` with the mechanism and the input, a "Not verified:" line, and a closing `Reviewed-by` and `Harness` pair |

Then:
1. Poll the comment body or the workflow run, not the comment list: bots edit in place.
   Check that a workflow conclusion is not `skipped`, and that the reviewed SHA is the head.
2. Verify every finding by running the input it names. A finding is a claim.
3. Fold each held finding into the commit it corrects. Hand the maintainer the amend
   command. Push once with `--force-with-lease=<ref>:<reviewed 40-char SHA>`.
4. Reply once: how many findings held, one line each, evidence for each decline. No bot handles.
5. Trailer only if a finding changed the commit: `Reviewed-by: DeepSeek V4.1 Flash` plus
   `Harness: DeepSeek Harness`; `Reviewed-by: Codex`; for Claude,
   `Reviewed-by: <full model name> <noreply@anthropic.com>`. PR 588 commit `547a854a`
   carries two. A review with no applied finding gets none.

## How the DeepSeek reviewer is driven

It is not a bot. It is `dsh`, the npm package `@deepseek-ai/dsh`, run by hand in a
terminal. Its default model is `deepseek-flash` on the `deepseek-official` provider,
reasoning effort `max`. Sessions are logged as zstd JSONL.

The prompt is a template. It tells the model to read hunks, trace each claim, post at
most 15 lines, and end with the `Reviewed-by` and `Harness` pair, because the comment
appears under a human account. I found no cron job, script or CI step that starts it.
A human or a peer session starts each review. The template's measured basis: the
anchored form reached the same conclusions with 0.26M cache-read tokens against 8.11M
for the verbose form.

## Open questions for the maintainer

| Question | Evidence | Recommendation |
|---|---|---|
| Pay for Codex? | 15 limit comments on PR 452 in 8 days; 3 of 3 rounds gave a confirmed P2; the CLI shares the quota | No, not yet. Batch pushes for two weeks and count limit comments with `gh api ... \| grep -c "usage limits"`. Buy only if they continue |
| Should DeepSeek post under a bot account? | Today the only marker of a DeepSeek comment is a trailer under a human login | Yes. Use a machine user or GitHub App with write on issues and pull requests of this repo only |
| Keep review prices out of the public harness? | OpenRouter list prices are public; plan quotas and spend are not | Public: stage rules, tokens, minutes, list prices. In gitignored `run/`: plan tier, quota, host names |
| Does Codex review drafts and each new commit? | The status table says "New commits" on PR 452 and 588; the app text lists three triggers | Test once on a throwaway draft before relying on "drafts until ready" |
| Keep `claude.yml` as a reviewer? | One data point, no findings, no build | Keep as an optional sanity pass, not a gate |
| Move the DeepSeek prompt into the harness? | It is untracked in the server checkout | Yes: `skills/freeunit-review/references/`, so every session can find it |

Sources: PR 452, 588, 316, 508 on https://github.com/freeunitorg/freeunit; workflows at
`origin/master` in `.github/workflows/`; run totals from
`https://api.github.com/repos/freeunitorg/freeunit/actions/runs`; prices from
https://openrouter.ai/api/v1/models (07.10.2026).
