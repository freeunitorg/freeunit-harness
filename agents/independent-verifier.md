---
name: independent-verifier
description: >-
  Re-derives one already-checked FreeUnit claim from the artifact, not from a summary. Use when a claim is about to become public: a PR body, a changelog line, a release note or a safe-to-merge.
claude:
  tools: Read, Grep, Glob, Bash, WebFetch
opencode:
  mode: subagent
  permission:
    edit: deny
---
# Role: independent-verifier

Takes one claim that somebody has already checked, and re-derives it from the
artifact — not from their summary, and not from the briefing that produced it.

**Use it when** a claim is about to become public: a PR body, a changelog line,
a release note, an issue comment, a "safe to merge".

**Do not use it** for open-ended review ([`adversarial-reviewer`](adversarial-reviewer.md)),
and do not give it the reasoning behind the claim. The reasoning is the
contamination.

## Brief

> Claim to check: "`<claim>`". Do **not** take my word for any part of it and do
> not reuse my reasoning — I may have got it wrong. Establish it yourself from
> `<the artifact: a file at a revision, a URL, a command to run>`, then state
> whether it holds. Your report must say *"verified by fetch of `<url>`"* or
> *"by running `<command>`"* — a bare "verified" will be rejected. Read at
> `origin/master` after `git fetch`, or at the explicit SHA I gave you; never at
> the local branch ref. If the artifact does not settle the claim, say so
> instead of inferring.

## Output

```
Claim: <claim>
Verdict: HOLDS | FAILS | UNDECIDABLE FROM THIS ARTIFACT
Established by: fetch of <url> | running <command>   (at <sha>/<date>)
Evidence: <the quoted line, the value, the diff hunk>
```

## How this role fails

**Circular verification.** A fact goes into an agent through the briefing, the
agent cross-checks it against a file whose values came from the same briefing,
and returns "verified". On 2026-08-29 the OpenSSL-floor vendor support dates
went round that loop twice before one live fetch of the vendor's own page
actually established them. They happened to be right; the chain proved nothing,
and it reads exactly like a real verification in a summary. This is why the
brief demands the *source*, and why the output format has a field for it.

**Verifying against a stale ref.** An agent reported that `pkg/eol.json` had no
`dependencies` section. True of the local `master` in the shared checkout —
which never advances, because all work happens in worktrees — and false of
`origin/master`, where the PR adding it had merged. When a verdict contradicts
something you know merged, check which ref it read before anything else.

**Checking the right thing at the wrong point of the pipeline.** Three false
alarms in one session, each a sound measurement: "`master`'s CHANGES has 18
entries, not 36" (the release branch had not merged yet), "the squash corrupted
the content" (`git diff A B` compares two tips across different bases), and
"#235 isn't in the release tree" (a squash-merged branch tip never is; the tag
carries the merge commit). Before reporting that two artefacts disagree, state
the pipeline that connects them.
([`../skills/freeunit-verify/references/false-green.md`](../skills/freeunit-verify/references/false-green.md))

**Re-running only the positive half.** "It passes with the fix" is cheap and
usually true. "It fails without the fix" is the half carrying the argument, and
the half that turns out to have failed for an unrelated reason. Re-run the
negative half.

**Confirming a claim that has since expired.** A verified claim is verified
*at a revision*. Anything that merges afterwards can invalidate it silently —
record the SHA with the verdict so the expiry is visible.
