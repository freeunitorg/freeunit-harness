# Checks that pass for the wrong reason

Every item here is a real failure from actual work on this toolkit and the
server it wraps, not a hypothetical. The shape repeats: a check reports
success, and the success is true of *something* — just not the thing you
meant to ask about. Grouped by mechanism, because the fix is different for
each.

## Exit-status laundering

**`$?` after a pipe is the last command's status, not the pipeline's.**
`./build/tests | tail` returned `tail`'s `0` while the C suite segfaulted
underneath it. `make ... | grep something` reported success for a build that
failed to link, because `grep`'s exit status (found a match, or didn't) is
what `$?` sees, never `make`'s. Fix: `set -o pipefail` before the pipe, check
`${PIPESTATUS[0]}` (bash) explicitly, or restructure so the command whose
status you need is not the one piped into something else.

**A stale test binary answers questions your source can no longer ask.**
When `make tests` fails to *link* (a missing symbol, a header change that
broke a translation unit), the *previous* `./build/tests` is often still
sitting on disk, still executable. Run it and it reports "35 passed" — for
code that was never recompiled, testing nothing about your change. Check the
build step's own exit status before trusting the run that follows it; don't
infer "the code compiled" from "a test binary ran."

**Release builds compile out the assertions debug builds rely on.**
`nxt_assert` is a no-op outside `--debug`. A test fixture that violated a
port's teardown invariant passed locally against a default (non-debug) build
and only aborted once it hit CI's debug leg. The general form: any check
built on an assertion firing is only as strong as the build flags under it.
Verify anything touching runtime invariants with `./configure --tests
--debug` (see the [debug skill](../../freeunit-debug/SKILL.md) and
[`../../freeunit-debug/references/sanitizers.md`](../../freeunit-debug/references/sanitizers.md)
for the two ways ASan+UBSan builds silently lie too).

## Stale or wrong artifacts

**A tool's own docstring is a claim, not a check.** `tools/changes-gen.py`'s
first draft matched every `<changes>` element in `docs/changes.xml` and
refused to act when more than one shared a `ver` attribute, with a comment
claiming this was a named, rare case (one specific version). Running `verify`
against this repo's *actual* `docs/changes.xml` found the block-matching
unfiltered by the `apply` attribute, so it also matched every per-language
module's placeholder block (`apply="unit-php"`, `apply="unit-python..."`,
etc.) — the tool reported "ambiguous" on **52 of 53 versions** in the real
file, which would have made `write` fail almost always instead of almost
never. The comment was written with confidence and was wrong; only running
the tool against real data caught it. Fixed by filtering to `apply="unit"`
specifically — see
[`../../freeunit-release/references/cut-recipe.md`](../../freeunit-release/references/cut-recipe.md#python-fallback-generator).
The general form: a script's own comments describing its edge cases are as
unverified as any other doc until you've run it against the real data those
comments claim to describe.

## Shell and tool-dialect mismatch

These are per-box facts, not per-project ones — check them on whichever
machine is actually running the script, they are exactly the kind of thing
that's different on the next box.

**`sh -n script.sh` validates against whichever shell `/bin/sh` is symlinked
to on this box (dash, here), not against bash.** It will pass a script that
uses bash-only syntax if dash happens to accept the specific construct
silently, or reject one that bash accepts — either direction is possible
depending on what's in the script. `sh -n` tells you the script parses for
the interpreter it happened to check, not the one that will actually run it.
If the shebang says `#!/bin/bash`, validate with `bash -n`, not `sh -n`.

**Locale changes what a number *is*, not just how it prints.** On a box with
`LC_NUMERIC=ru_RU.UTF-8` (this one), `awk`'s `printf "%.2f", 108.7` prints
`108,70` — comma decimal. That still looks fine scrolling past in a
terminal, and it silently corrupts a TSV (or any format) that something
downstream — a stricter parser, a different locale, a spreadsheet import —
expects to be able to read as `.`-decimal. `tools/bench-release.sh` forces
`export LC_ALL=C` at the top for exactly this reason, found by accident while
testing the script's own summary output, not by anticipating it.

**`/usr/bin/timeout` may be uutils (Rust), not GNU coreutils, on this box —
same name, different implementation, different error text.** A GNU-flavored
mental model of what a `timeout` failure looks like ("timeout: failed to run
command") doesn't match uutils' phrasing ("os error 2"). Don't infer the
failure mode from memory of the other implementation; check `timeout
--version`.

**`grep` here is a ugrep wrapper, not GNU grep.** When a script's *exact*
matching behavior matters — an edge case in regex dialect, a flag that
changed meaning — verify with `command grep` or `rg` (both installed), not
bare `grep`, which may not be the grep you think it is. This applies to any
interactive shell session, not just scripts: a `grep` command run to sanity
check something can itself be the thing lying to you.

## Process matching that hits the wrong process

**`pkill -f <pattern>` matches the *entire command line*, including the
shell/ssh wrapper that invoked the check itself, if that pattern happens to
appear in it.** A pattern meant to find and kill a leftover `unitd` can match
an ssh session whose command line contains that same string, and kill its
own shell out from under itself. This surfaces as a bare `exit 255` with no
further output — which reads exactly like a flaky host or a network blip,
not like the script committing suicide. Prefer the narrowest identifier that
actually names the target: a pidfile written by the process itself, `pgrep
-x <exact-comm>` (name match, not full-command-line substring), or a marker
string you control and that nothing else's command line could contain.

## Silent no-ops

**A string `.replace()` call with no assertion silently does nothing when
the pattern isn't present — in Python or any language with the same
semantics.** A benchmark script that substituted a cell label into a request
template did this: the substitution silently no-op'd, the run completed
end-to-end, produced a full table of numbers, and every row was labeled
wrong. Nothing about the run *looked* broken — it looked exactly like a
successful benchmark. Guard any load-bearing string transformation with
`assert old in text` before replacing (or a form that raises when nothing
matched, like `str.format` on an unset key, rather than silently emitting a
literal `{missing}`).

## The mirror image: a check that fails for the wrong reason

The same defect produces false *alarms*, and they are more expensive because
they get reported. All three below happened in one release session
(2026-08-28); each measurement was sound, and each was aimed at a point in a
pipeline whose shape had not been confirmed first.

**"`master`'s `CHANGES` has 18 entries, not 36."** True, and irrelevant: the
36 were on the release branch, which had not been merged yet. The order was
push → PR → merge → tag, so `master` was *expected* to lag. Establish the
merge order before treating a difference between two refs as a defect.

**"`git diff backup HEAD` is huge — the squash corrupted the content."** The
diff was huge because `origin/master` had moved under the rewrite: `git diff
A B` compares two *tips*, so when the branches sit on different bases it
reports every commit the base gained as well as whatever the rewrite changed.
A rewrite is verified by comparing each branch's own patch against **its own
base** — `git diff <old-base>..<old>` against `git diff <new-base>..<new>`,
or `git range-diff <old-base>..<old> <new-base>..<new>`, which does exactly
this pairing for you.

**"#235 isn't in the release tree."** True of the PR's *branch tip*, which a
squash merge never puts in the tree at all (see [the review
skill](../../freeunit-review/references/bots-and-gh.md#commands) on merged branch tips
being non-ancestors). The tag sits on the **merge commit into `master`**,
which carries the change. Ask the question about the merge commit:
`git log --oneline <prev-tag>..<tag> | grep -F '(#235)'`, or
`git branch --contains <merge-sha>`.

The general form: a comparison is only as meaningful as the pipeline you
believe connects its two sides. State that pipeline out loud — "this lands on
the branch, merges here, is tagged there" — and confirm it, before reporting
that two artefacts disagree.

## One-sided verification: what the operation destroyed

**A sync verified only on the source side is half-checked.** Copying
`CHANGES` into the docs repo's `source/CHANGES.txt` was checked hard — header
date, entry count, byte-comparison of the new section against the source —
and every one of those checks looked at what arrived. It silently removed 16
lines the docs site carried and the code repo never had, including a
CVE-mitigation note, because the two files have **divergent history** and the
sync assumed they did not (see
[`../../freeunit-release/references/cut-recipe.md`](../../freeunit-release/references/cut-recipe.md#docs-repo-sourcechangestxt-prepend-the-new-section-never-sync-the-whole-file)).

After any sync, copy, regeneration or reformat, count the removals before
looking at the additions:

```bash
git diff <base> -- <file> | grep -c '^-[^-]'   # must be 0 unless you meant it
```

This generalises past changelogs: a regenerated config, a reformatted file, a
templated Dockerfile. "The new content is correct" and "nothing else was lost"
are two different questions, and only the first one is the one you naturally
ask.

## Checks you did not run yourself

**A validation reported by a subagent, a colleague or a bot is a claim, not a
result — re-run it before relaying it.** A regression test was reported as
"validated both ways" (fails without the fix, passes with it). It could not
run at all under `--debug`: an `nxt_assert` in the fixture fired before the
assertion under test was reached, so the negative half had never executed. The
claim was relayed into a public issue comment before anyone re-ran it.

The negative half is the half to re-run. "It passes" is cheap to reproduce and
usually true; "it fails without the fix" is the half that carries the whole
argument and the half that quietly turns out to have failed for another reason
— see [the bots reference](../../freeunit-review/references/bots-and-gh.md) on a
regression proof where an unrelated earlier check produced the `rc=1`.

## A build that skipped your file

**A cross-compile that never compiled the code under test.** Checking whether
`syscall.MSG_CTRUNC` resolves on every supported `GOOS`, `CGO_ENABLED=0
GOOS=solaris go build ./...` exited 0 for all eight — and also exited 0 after
the symbol was replaced with `syscall.MSG_CTRUNC_BOGUS`. `go/port.go` is cgo
(`import "C"`), so `CGO_ENABLED=0` excluded it from the build entirely; the
green was true of the package minus the only file in question. The control —
mutate the symbol, expect a failure — is what exposed it. Turning cgo back on
did not rescue the check either: the module needs the C build's generated
headers, so it fails at baseline, and a check that is red before and after
your change measures nothing.

**A filtered compile reads clean on any failure.** `go build ... 2>&1 | grep
-iE 'MSG_CTRUNC|undefined'` printed nothing and was reported as "no problem"
for a target that had failed with `unsupported GOOS/GOARCH pair`. Grepping the
output for the error you expect hides every error you did not. Capture the
exit status separately from the text you filter.

**`gcc -fsyntax-only` does not emit every warning.** Testing whether an
unguarded `static` would trip `-Wunused-function` on macOS, `-fsyntax-only`
reported nothing for both the guarded and the unguarded file — the diagnostic
needs a real compile (`-c -o /dev/null`). A second attempt failed for an
unrelated reason: `-Wall -Wextra -Werror` trips Unit's own headers
(`nxt_timer.h` unused parameter), so both arms errored out and neither said
anything about the function under test. Narrow the flags to the one warning
you are asking about, and confirm the control arm produces it.

## The general rule, restated

Every item above is a check that returned a truthful answer to a question
nobody meant to ask. The fix is never "trust it a little more carefully" —
it's proving the check can say no. Before you rely on a check for something
that matters: break the thing it's supposed to catch, once, on purpose, and
confirm it goes red. If you can't make it fail deliberately, you don't know
what it's actually checking.

## "Verified" must name the source or the command

A fact that enters an agent via its briefing and comes back "cross-checked against pkg/eol.json"
(whose values came from the same briefing) has been verified against nothing. On 2026-08-29 the
OpenSSL-floor vendor dates were "verified" twice this way before one live fetch of endoflife.date
actually established them (they were right; the chain still proved nothing). Reports must say
"verified by fetch of <URL>" or "by running <command>"; a bare "verified" is a claim, not evidence.
