# Agent roles

Briefs for work you are handing to somebody else — a subagent, a second
session, or yourself in two hours with no memory of this one. Each file is one
narrow job: what it is for, the brief shape that made it work, the output it
must return, and the specific ways that job has gone wrong on this project.

They are deliberately small. A single "reviewer" or "helper" brief produces a
report you then have to verify from scratch; six narrow ones produce reports
whose failure modes you already know.

Every failure named in this directory happened on FreeUnit, dated. Claims about
the *server source* belong in [`../docs/`](../docs/README.md) and
[`../skills/`](../docs/skills.md), where they are verified against the tree —
nothing here restates them.

## Pick a role

| Role | Hand it this | It returns |
|---|---|---|
| [`ci-watcher`](ci-watcher.md) | a run/PR that must reach a terminal state before anything else moves | one conclusion line and the failing jobs |
| [`adversarial-reviewer`](adversarial-reviewer.md) | a diff or a claim you *want* to be true | findings with `file:line`, plus what it could not verify |
| [`independent-verifier`](independent-verifier.md) | one specific claim somebody else already "verified" | the claim re-derived from the artifact, or contradicted |
| [`release-orchestrator`](release-orchestrator.md) | a merge order to hold across many hours and several branch moves | the next exact command, for a human to run |
| [`remote-builder`](remote-builder.md) | a build, a codegen, or a test run that must happen off this laptop | the artifact, its provenance, and a fidelity check |
| [`packaging-archaeologist`](packaging-archaeologist.md) | "which distros can build this, and until when" | a table where every cell cites a fetched source |

## Rules that go in every brief

Seven sentences, verbatim, at the top of any brief. Each one is here because
leaving it out cost hours on 2026-08-28/29.

**1. Name the revision you read.** A long-lived server checkout sat ~40 commits
behind `origin/master` — its branch ref never advances, because the work happens
in worktrees — and two sessions reported facts that were true of the local
`master` ref and false of the remote. Brief: *"read
`origin/master` after `git fetch`, or an explicit SHA — never the local branch
ref."* Report: *"read at `<sha>`."* A finding that contradicts a known merge is
a stale-ref bug until proven otherwise.

**2. "Verified" must name the external fetch or the executed command.** A fact
that enters an agent through its briefing and comes back "cross-checked" against
a file derived from the same briefing has been verified against nothing — this
happened twice in one evening to the OpenSSL support dates before one live fetch
settled them. Accept *"verified by fetch of `<url>`"* or *"by running
`<command>`"*; reject a bare "verified". See
[`../skills/freeunit-verify/references/false-green.md`](../skills/freeunit-verify/references/false-green.md).

**3. A watch is something you finish, not something you start.** An agent asked
to watch a pipeline answered in seconds that "monitoring is running" and
stopped. Nothing was. Brief a watcher to *block* and to make its last line the
conclusion; "the workflow completed" is not a conclusion, "failure —
alpine-edge (gcc)" is.

**4. Every loop gets a deadline and a liveness check.** A poller ran for two
hours against a container that had died in the first minute; each iteration
succeeded at *asking*, so nothing ever looked wrong. No unbounded `while`, and
three consecutive failures to reach the target end the loop.

**5. Agents share one checkout — give each disjoint files and no git state.**
No commit, checkout, branch, stash or push. Have them return the lines or the
patch for you to apply. Anything that needs its own tree gets a worktree
([`../skills/freeunit-review/SKILL.md`](../skills/freeunit-review/SKILL.md)).

**6. End every report with what you could not verify.** A report with no such
section is not a clean report, it is an unlabelled one — and the unlabelled part
is where the next false claim comes from.

**7. Report a failure when it happens, then try to fix it.** A run that
breaks in its first minute and is reported in its final summary has wasted the
rest of the run, and the summary tends to soften it. Brief: *"state what you
expect before each step; the moment a step fails, a CI job goes red or an
assumption is false, report it with the quoted line, try the narrowest fix,
and re-run that step. After two failed attempts, stop and report."* See
[`../docs/style.md`](../docs/style.md), section "Approach".

## The tools these roles lean on

| Tool | One thing it checks |
|---|---|
| [`../tools/watch-run.sh`](../tools/watch-run.sh) | a workflow run reached a terminal state, and what it was |
| [`../tools/new-failures.sh`](../tools/new-failures.sh) | your change introduced no *new* failures a checker reports |
| [`../tools/mirror-check.sh`](../tools/mirror-check.sh) | a synced copy still has every committed file, writably |
| [`../tools/ci-cost.py`](../tools/ci-cost.py) | what a run cost, split into queue and execution |
| [`../tools/openapi-example-check.py`](../tools/openapi-example-check.py) | every committed OpenAPI example validates against its schema |

Each script's header comment is its documentation, including the failure it
exists to prevent.
