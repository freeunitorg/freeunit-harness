---
name: freeunit-review
description: >-
  Prepare or review a FreeUnit pull request: lint, C tests, review rules and review bots. Use when you are about to open a pull request, or you are reviewing someone else's.
---

# Skill: review

**Trigger:** you are about to open a pull request, or you are reviewing someone
else's.

**Trailers:** `Co-Authored-By` for the model that wrote the code; `Reviewed-by`
only for a model whose finding changed the commit. Canonical spellings live in
`tools/reviewers.txt`; run `tools/check-trailers.py <commit>` before you push and
`tools/check-trailers.py -c comment.md` before you post a review. In a comment
no model line takes an address, and the trailers go last. The harness in
parentheses after the model is free text; only the model is checked. In a
comment the harness may hold an `@handle`, but `<...>` and an email-shaped
string still fail, and a Claude co-author in a commit still needs
`<noreply@anthropic.com>` or a `(claude X.Y.Z)` suffix.

```bash
make lint          # whitespace + convention gates
make ctest         # C unit tests
make test          # python suite
make ci-local      # the whole fast pipeline in the CI image
```

C style and commit format are in `docs/style.md` at the repository root.
Nothing in CI checks the commit subject, so that one is on you.

## Checklist

**Did you grep every caller of a signature you changed?** Including `src/test/`
and `fuzzing/` — PR CI does not compile the fuzz harnesses, so a shared parser
signature change can pass every check and still break the fuzz build
([build skill](../freeunit-build/SKILL.md)).

**Is the log format string in Unit's dialect?** `nxt_sprintf` is not `printf`.
The grammar is `%[0][width][u][x|X]<type>` — the hex `x`/`X` is a *modifier*
before a type character, not a conversion. Two-digit uppercase hex of an int is
`%02Xd`; plain `%02X` is an error. `-Werror` will not catch it; the clang-ast CI
leg will. Grep an existing usage before inventing one.

**Does the PR touch `docs/unit-openapi.yaml`?** Run
`tools/openapi-example-check.py --repo src` and compare the failing-label set
against the base commit's, not just the count. The 7 known `/config`
failures are pre-existing; any other FAIL is a new example that violates its
own schema. Add `--required-hints` to also flag property descriptions that
imply always-present ("always present"/"is always"/"every response"/etc.)
while the property is missing from `required` (informational only; add
`--strict-hints` to fail the run on a hit). Needs PyYAML + jsonschema
(`python3 -c 'import yaml, jsonschema'`; if missing, `uv pip install pyyaml
jsonschema`).

**Is new list-allocated memory zeroed?** `nxt_list_zero_add()`, not
`nxt_list_add()`. The failure shape is in
[`freeunit-test/references/failure-shapes.md`](../freeunit-test/references/failure-shapes.md), and
it will read as a race for days.

**Does the new test fail on the unpatched code?** Prove it in a worktree at the
base commit ([test skill](../freeunit-test/SKILL.md)).

**If it touches process lifecycle, what happens when the runner is killed?**
Anything that spawns must be reapable by process group, not by a single pid.

**If it claims a performance change, where is the A/A noise floor?**
([benchmark skill](../freeunit-benchmark/SKILL.md)). A single A/B run is not the
answer — it takes a null gate, a run, and a `--swap` run crossed over
([`../freeunit-benchmark/references/release-ab.md`](../freeunit-benchmark/references/release-ab.md)).

**Did you run the validation you are about to quote?** A verification claim
from a subagent or a bot is a claim; re-run it — the negative half above all —
before it goes into a PR body or an issue comment
([`references/bots-and-gh.md`](references/bots-and-gh.md#trusting-a-bot-review)).

**What is actually in the commit?** `git show --stat HEAD` before pushing, not
`git status` before committing — `git add -A` sweeps up local agent state you
judged harmless under some earlier operation
([`references/bots-and-gh.md`](references/bots-and-gh.md#commands)).

## The layout and protocol surface

Four things are shared with a process or a module this tree did not build. When
a patch touches one of them, the check in the right column has to see it, and
`tools/perf/layout-structs.txt` is the manifest that turns a forgotten entry
into a failure instead of a warning.

| the change touches | what catches it |
|---|---|
| a struct or an enumerator in a header `make install` copies (the rule is in `auto/make`) | `Layout check (pahole)`; `tools/perf/layout-coverage.py` needs an entry for each |
| a struct in shared memory: `nxt_port*.h`, `nxt_app_queue.h`, `nxt_app_nncq.h`, `nxt_nncq.h` | the same job, as a `shm` entry: a layout change fails |
| a handler slot in `nxt_port_handlers_t`, or any `_NXT_PORT_MSG_*` value | the same job, as an `enum` entry: a number is the offset of a slot, so inserting or reordering renumbers the wire protocol, and appending is the only safe edit |
| `PORT_MMAP_*`, or a message payload both sides decode | the same job, as a `const` or `shm` entry |

The header comment of the list is the long form: what each kind means, why a
`local` entry is a warning rather than a failure, and the map from a file to
the entries it needs. Two review habits follow. A diff that inserts a slot in
the middle of `nxt_port_handlers_t` renumbers the protocol and is a protocol
change even when it looks like a local edit. A diff that changes a struct a
module lays out itself needs its list entry in the same commit, and CI will say
so if it is missing.

## Rebases and stacked branches

**Fold review fixes into the commit they correct.** Amend or squash and
force-push with `--force-with-lease`; do not stack "address review comments"
commits. The history that merges should read as though the code was right the
first time. Do not rewrite a branch that has already been approved — land it,
then rebase whatever was stacked on it.

**After any large rebase, re-audit against invariants the base added.** A branch
can replay without a single conflict and still violate something the base
hardened while it was away; the conflict view will not show you this, because
there was no conflict. This is a recurring class of bug, not a theoretical one.

**In a stacked branch, propagate a base fix explicitly, fix by fix.** The
dependent branch's own reworked code can contain the same bug in a different
shape that no rebase will surface. Enumerate each fix from the base and account
for it by name.

**Does the change reason about a platform CI does not build?** The workflows
run Linux only — no macOS, no FreeBSD, no Solaris leg — so anything inside a
platform `#if` is compiled by nobody and reviewed by nobody. Two ways this has
shipped from one PR (#291): a `static` helper defined unguarded while both its
call sites sat in the non-macOS arm (`-Wunused-function` under `-Werror`,
invisible here), and a test fixture gated `NXT_CRED_USECMSG` — Linux *and*
FreeBSD — whose whole premise was that the credential is enforced but not
attached, which is false where `nxt_socket_msg_oob_init()` attaches `SCM_CREDS`
itself. Check it mechanically rather than by reading: force the arm
(`sed 's/#if (NXT_MACOSX)/#if (1)/'`), compile for real — `-fsyntax-only` does
not emit `-Wunused-function` — and run the *guard-removed* control first to
confirm the check can fail at all. Separately, a Linux kernel behaviour
described as a general property is a portability bug in a comment: say which
platform you verified.

**When a review round adds a mechanism rather than a local correction, check it
against every mechanism already added — in both orders.** A dedupe suppressing a
later escalation, a retry reissuing the wrong cancel: interaction bugs between
successive fixes are how a converging review round reopens.

## References

- [`references/bots-and-gh.md`](references/bots-and-gh.md) — verifying bot
  findings, what an automated reviewer actually diffed, re-triggering, and the
  `gh` commands that fail.
- [`references/deepseek-review-prompt.md`](references/deepseek-review-prompt.md) —
  the system prompt for the DeepSeek reviewer (`dsh`). Pass it verbatim.
- [`references/freeunit-review-primer.md`](references/freeunit-review-primer.md) —
  context primer: layout, C style, project rules, review history, output
  format. Inject it before every review, after the prompt above.

### API width audits: read the emitter, not the struct

When checking OpenAPI integer widths against the server, trace each emitted member to its
expression in the emitter (`src/nxt_status.c` `nxt_conf_set_member_integer(...)` calls), not to a
same-named struct field: `connections.active` is `accepted - closed - idle` over `uint64_t` fields,
while the similarly named `active_requests` is `uint32_t` and feeds a different member. A name match
gave a wrong width on 2026-08-29 (#237 review); the expression audit found 7 × 64-bit, 4 × 32-bit.
