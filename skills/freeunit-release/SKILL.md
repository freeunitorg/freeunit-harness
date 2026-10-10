---
name: freeunit-release
description: >-
  The FreeUnit release workflow and what a release expects of a change. Use when cutting a FreeUnit release, or preparing something that has to land in one.
---

# Skill: release

**Trigger:** cutting a FreeUnit release, or preparing something that has to land
in one.

> A maintainer workflow, documented so a contributor can see where their change
> is going and what a release expects of it — not so that anyone can cut one
> unilaterally.

Verified against `freeunitorg/freeunit`'s own `RELEASE-PROCESS.md`, the 1.36.0
cut (`542f28bd`..`dddafac4`), and the `1.34` LTS branch's 1.34.2 cut
(`c5aabd39`..`8ab74a8c`).

## Shape of a release

1. **Integration branch.** Work accumulates on a pre-release branch rather than
   landing directly on `master`, so the release content can be reviewed as a
   set. Dependency bumps target that branch too.
2. **Version.** Bumped in the source (`version`, `CHANGES`,
   `docs/changes.xml`, `docs/unit-openapi.yaml` — see point 4 for where the
   tag actually lands).
3. **Changelog.** `CHANGES` gets an entry per user-visible change, categorized
   (security / feature / bugfix / change), generated from `docs/changes.xml`.
   `xsltproc` may not be on your box — `tools/changes-gen.py` is a validated
   fallback; **run its `verify` mode before `write`**, see
   [`references/cut-recipe.md`](references/cut-recipe.md#python-fallback-generator).
4. **Merge and tag.** The integration branch merges to `master`. **The tag
   does not always point at that merge commit** — 1.36.0's tag landed one PR
   later, on a one-line contrib-checksum fix found while packaging. Check what
   the tag actually points at; see
   [`references/cut-recipe.md`](references/cut-recipe.md#the-tag-is-not-always-the-merge-commit).
5. **Advisories, atomically.** Any GitHub Security Advisory drafted privately
   during the cycle is published **at the tag**, so a fixed version exists the
   moment the issue becomes public. CVE requests follow. Lower-impact items ship
   as quiet hardening with no advisory, and security fixes generally land as
   focused single-purpose commits without "CVE"/"audit" wording in the title.
6. **Backports.** Fixes that apply to the maintained LTS line are backported to
   its branch and released separately.
7. **Artifacts.** Docker images and packages are built from the tag. Distro
   packaging carries its own dependency traps (a toolchain-version-gated
   `--otel` build, runtime `.so` deps that differ per distro, no TLS on the
   telemetry exporter) — see
   [`references/packaging-deps.md`](references/packaging-deps.md).

## What matters

**A release number is not always the next number.** A cycle can be renumbered
between planning and shipping — an integration branch called `pre-1.35.6` can
ship as `1.36.0`. Do not infer the version from the branch name; read the tag.

**Advisory publication and the tag must not drift apart.** Publishing early
discloses an issue with no fixed release available. Publishing late means
distributions ship the fix without knowing it was one, and scanners stay quiet.

**The LTS branch is not a subset of master.** It diverges, and a backport is a
real port with its own review and its own CI run. A newer compiler on an older
branch surfaces warnings that `-Werror` turns into failures; expect to deal with
that rather than treating it as a broken backport.

**Never sync the docs repo's `source/CHANGES.txt` wholesale from `CHANGES`.**
The two files have divergent history — the site carries a CVE mitigation note
and other lines the code repo never had. Prepend the new section only, and
prove the rest survived: `git diff origin/main -- source/CHANGES.txt | grep -c
'^-[^-]'` must be `0`. A "verbatim sync" checked only on the source side
deleted 16 lines on 2026-08-28 and looked correct doing it.

**Ask what the merge order is before asking why something is missing.** During
a cut the order is push → PR → merge → tag, so `master` lagging the release
content is expected, and a PR's *branch tip* is never in the tree after a
squash merge — the tag carries its **merge commit**. Three false alarms in one
session were sound measurements taken at the wrong point of that pipeline; the
checks that answer these questions properly are in
[`references/cut-recipe.md`](references/cut-recipe.md#is-pr-n-in-the-release-is-a-question-about-its-merge-commit).

**Check what CI actually ran on the release commit.** A merge can be green
because the workflows that matter did not trigger — path filters are per
workflow, and several of Unit's are path-filtered.

**`CHANGES` is not reliably a build of `docs/changes.xml` right now.** It has
been hand-edited directly, bypassing the generator, at least twice (1.35.6 and
1.36.0 cycles) — see the reference below before trusting either file, or
before regenerating `CHANGES` and clobbering a hand-added bullet.

**A PR body can carry its own hold on the cycle.** A PR can say, in its own
body, "Do not merge before `<version>` is tagged" — nothing about the PR
itself (label, title, CI state) flags this; only the prose does. Grep every
open PR you're about to fold into a merge list before building it:
`gh pr view <N> --json body`, or across all open PRs at once. This cycle it
was missed for #212 (caught before merge, not after — but only because
someone happened to reread the body).

**Don't combine perf figures from different bench runs into one
before/after claim.** A "before" number from one run and an "after" number
from a different run are not comparable — machine load, thermal state, and
background noise differ between runs even on the same box. Cite one run per
figure, and say which run each number came from, rather than presenting a
mixed pair as a single measured delta.

**`test_otel.py` cannot get otel coverage on a box with port 8080 taken.**
It wildcard-binds `*:8080`, like 53 other files in `test/` (at
`e21bc5bc`) — this is the same session-start capability-probe conflict
[the test skill already documents](../freeunit-test/SKILL.md#what-matters); it isn't
otel-specific, but it does mean an otel validation run on a shared box needs
the same `tools/test-isolated` treatment as any other test-suite run.

## The exact recipe

The maintainer workflow above is the shape; for the concrete file list, commit
order, commit-message conventions on `master` vs. the `1.34` LTS branch, which
CI workflow watches which tag pattern, and the post-cut checklist — all
verified against the 1.36.0 and 1.34.2 cuts — see
[`references/cut-recipe.md`](references/cut-recipe.md).
