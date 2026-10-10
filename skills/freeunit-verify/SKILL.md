---
name: freeunit-verify
description: >-
  Prove a check can fail before trusting it: break the thing it should catch and see it turn red. Use when a check just went green (a test run, a build, an exit code, an assertion) and you are about to act on that as fact.
---

# Skill: verify

**Trigger:** a check just went green — a test run, a build, a script's exit
code, an assertion — and you are about to act on that as fact.

**The rule:** an assertion you have never seen fail is not evidence. Before
relying on a check for anything that matters, break the thing it's supposed
to catch, once, and confirm the check actually turns red. A check only ever
observed passing has not been shown able to fail — it has been observed
being silent, which is a different thing.

## What matters

**`$?` after a pipe is the last command's exit status, not the pipeline's.**
`./build/tests | tail` reported `0` while the suite segfaulted underneath it;
`make ... | grep` reported success for a build that failed to link. `set -o
pipefail`, check `${PIPESTATUS[0]}`, or don't pipe the command whose status
you actually need.

**A stale binary answers questions your source can no longer ask.** When
`make tests` fails to *link*, the previous `./build/tests` is still sitting
there, executable, and reports "35 passed" for code that was never
recompiled. The test run's exit status is not the build's exit status —
check both.

**Release builds compile out the assertions debug builds rely on.**
`nxt_assert` is a no-op outside `--debug`. A fixture that violated a port's
teardown invariant passed locally and aborted only in CI's debug leg.
Anything touching runtime invariants: verify with `./configure --tests
--debug` (see the [debug skill](../freeunit-debug/SKILL.md)), not just the default
build.

**A sound check pointed at the wrong end of a pipeline is a false alarm, not
a finding.** "`master`'s `CHANGES` has 18 entries, not 36" was a correct count
of a tree the release branch had not been merged into yet. Before reporting
that two artefacts disagree, state the pipeline that connects them and confirm
it — ask "what is the merge order?" before "why doesn't `master` have this?".
Three of these in one session; the shapes are in
[`references/false-green.md`](references/false-green.md#the-mirror-image-a-check-that-fails-for-the-wrong-reason).

**Check what an operation removed, not only what it brought in.** A
"verbatim sync" of `CHANGES` into the docs repo was verified hard on the
source side — header date, entry count, byte-compare — and silently deleted
16 lines the destination carried and the source never had, one of them a CVE
mitigation note. After any sync, copy or regenerate:
`git diff <base> -- <file> | grep -c '^-[^-]'` must be `0` unless you meant to
delete something.

**A validation someone else ran is a claim until you re-run it.** Re-run the
proof yourself, and especially its negative half — the run that is supposed to
fail. A test reported as "validated both ways" turned out not to run at all
under `--debug`.

**A tool's own docstring is a claim, not a check — run it against real data
before trusting either.** `tools/changes-gen.py`'s first draft asserted (in
its own comments) that multiple `<changes>` blocks per version was a rare,
named edge case. Running it against this repo's real `docs/changes.xml`
found it "ambiguous" on **52 of 53 versions** — the filter was matching every
per-language-module block, not just the one that feeds `CHANGES`. The
docstring was confident and wrong; the fix came from running the tool, not
from rereading it. See
[`../freeunit-release/references/cut-recipe.md`](../freeunit-release/references/cut-recipe.md#python-fallback-generator).

**On a codebase that was never green, the gate is "no NEW failures", not
"zero".** Any checker worth running over an old tree reports pre-existing
failures — `openapi-example-check.py` finds 7 under `/config`, and a lint over
111k lines of C finds hundreds. A gate demanding zero gets disabled; a gate
demanding that your change adds none is one you can keep. Run the checker at the
base revision *and* at your tree, compare the failing **labels** rather than the
counts (a count can stay flat while one failure is traded for another), and
require the new-failure set to be empty. `tools/new-failures.sh --base <rev> --
<checker>` does this for any checker that prints one line per failure. Two traps
it exists to close: a checker that fails to *start* prints nothing, matches
nothing and reads as a clean base — so treat every exit status outside "0 clean /
1 found failures" as "did not run"; and GNU `comm` compares by *collating
sequence*, so sorting under `LC_ALL=C` while `comm` itself runs under a UTF-8
locale makes it mis-pair lines and invent new failures — export `LC_ALL=C` for
the whole script, not just the sort. (uutils' `comm`, which is `/usr/bin/comm`
on some boxes here, is byte-wise and hides this locally.)

## References

- [`references/false-green.md`](references/false-green.md) — exit-status
  laundering, stale artifacts, dialect/tool mismatches, and silent no-ops,
  each with the evidence that caught it.
