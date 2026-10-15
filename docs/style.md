# Style and conventions

What "follow the existing style" actually means, measured from the tree rather
than asserted.

Counts verified against `freeunitorg/freeunit` `master` at `c9a7f48d`, over the
~111,700 lines of `src/*.c`. The server's own `CONTRIBUTING.md` says only
"Follow the existing C code style in the project", so this page exists to make
that instruction actionable. Where a rule here and the code disagree, the code
wins.

## C formatting

Unit inherits nginx's house style. There is **no `.clang-format`** and no
formatter that can be run over the tree, so none of this is auto-fixable —
which is why it is worth reading once.

| Rule | Evidence in the tree |
|---|---|
| 4-space indent, no tabs | exactly one `src/*.c` file contains a tab |
| 80-column limit | 111 lines exceed it out of ~111,700 — 0.1% |
| `/* */` comments, not `//` | 1775 block-comment openers vs 33 `//` lines |
| header guard `_NXT_<NAME>_H_INCLUDED_` | e.g. `src/nxt_mp.h` |
| `struct nxt_foo_s` + `} nxt_foo_t;` | 76 struct tags, 171 typedefs |

Function definitions put the return type on **its own line**, so the function
name starts at column 1 and `git grep -n '^nxt_mp_get'` finds definitions
rather than call sites. Top-level functions are separated by **two** blank
lines:

```c
static void *
nxt_mp_get_small(nxt_mp_t *mp, nxt_queue_t *pages, size_t size)
{
```

### Declaration blocks are aligned, and ordered by type-name length

This is the most distinctive local convention and the one most often missed.
Locals are declared in a block at the top of the function, sorted by
*increasing length of the type name*, with the variable names aligned in a
single column and `*` bound to the name:

```c
    u_char            *p;
    uint32_t          available;
    nxt_mp_page_t     *page;
    nxt_queue_link_t  *link, *next;
```

Not alphabetical, not grouped by meaning — by type-name width, shortest first.
The alignment column sits two spaces past the longest type. The ordering is a
strong habit, not a law: of 727 top-of-function blocks with three or more
declarations at `82e17fbb`, 670 (92%) are in non-decreasing type-name order
(`nxt_router_conf_create()` is one that is not). New code follows the rule;
when editing a function, match its existing block rather than reorder it.
There is no blank line between groups inside the block.

## Idioms

**Branch hints on every fallible call.** `nxt_slow_path()` appears 2018 times
and `nxt_fast_path()` 337. The convention is `if (nxt_slow_path(p == NULL))`
for the error branch, not a bare `if`. Matching it matters less for the
optimiser than for the reader: an unhinted error check reads as a normal
branch.

**Return codes are a small fixed vocabulary.** `NXT_ERROR` (829) and `NXT_OK`
(529) dominate, with `NXT_AGAIN` (34) and `NXT_DECLINED` (31) where the
distinction is real. Return one of these from anything returning `nxt_int_t`;
do not invent a truth value or a negative errno.

**Cleanup goes through a `goto` label at the end of the function,** because
there is no RAII and the pool does not own everything acquired. `fail` is the
overwhelmingly common name (315 uses), then `error` (63), `done` (51) and
`alloc_fail` (25). Prefer `fail` unless the function already uses another.

**`nxt_sprintf` is not `printf`.** The grammar is
`%[0][width][u][x|X]<type>` — the hex `x`/`X` is a modifier *before* a type
character, not a conversion. Two-digit uppercase hex of an int is `%02Xd`;
plain `%02X` is wrong. `-Werror` will not catch it, the `analysis-clang-ast`
CI leg will. Grep an existing use before inventing one.

**Allocator choice is semantic, not stylistic.** `nxt_mp_get` returns aligned
non-freeable memory, `nxt_mp_nget` returns explicitly **non-aligned**
byte-packed memory (`src/nxt_mp.h`), `nxt_mp_alloc` is freeable. Picking the
wrong one is a correctness bug, not a preference — storing a struct through
`nget` is how you get a `SIGBUS` on 32-bit ARM. See `skills/freeunit-cross-arch/references/arm-alignment.md`.

**Prefer `nxt_list_zero_add()` over `nxt_list_add()`.** The latter returns
non-zeroed pool memory and the field setters do not initialise every flag bit.

## Code rules since #413 and #444

Checked against `freeunitorg/freeunit` `master` at `37fe59b9`. #444
(`1e73d92d`) added the helpers and `--hardening`. #413 (`96f88f76`) moved
the test hooks out of the shipped build. The hardening PRs #445–#448 build on
both.

- **Length sums and products use `nxt_size_add()` / `nxt_size_mul()`**
  (`src/nxt_checked.h`). Each returns 1 on overflow, and then `*out` holds
  the wrapped value, which the caller must not use.
- **Untrusted records are read through `nxt_span_t`** (`src/nxt_span.h`):
  `nxt_span_init()`, then `nxt_span_take()` / `nxt_span_copy()`. They fail on
  a short read or a partial tail and never return a pointer past `end`. Do not
  trust a length field that comes from the peer.
- **A deliberate case fall-through is `nxt_fallthrough;`** (`src/nxt_clang.h`),
  not a `/* Fall through. */` comment. Under `-Wimplicit-fallthrough` GCC
  accepts the comment and Clang does not; both accept the statement.
- **No VLAs.** `--hardening=strict` adds `-Wvla`, and gcc builds with
  `-Werror` (`auto/cc/test`).
- **New code builds clean under `./configure --hardening=strict`.** Strict
  adds `-Wformat=2`, `-Wvla`, `-Wimplicit-fallthrough` and
  `-ftrivial-auto-var-init=pattern` on top of `default`. Each flag is probed,
  so an old compiler drops it without error: a clean build on an old
  compiler proves less. `tools/unit-build --hardening tests` runs this check.
- **New C tests go in the `nxt_security_tests[]` table** in
  `src/test/nxt_tests.c`. Use `nxt_test_in_child()` for a test that can crash
  or must not change the parent, and `NXT_TEST_CHECK(log, cond, fmt, ...)`
  for a check that logs and returns `NXT_ERROR`.
- **Test hooks stay under `#if (NXT_TESTS)`, in a source of `NXT_LIB_SRCS` or
  `NXT_LIB_UNIT_SRCS`** (`auto/sources`). Only those are compiled twice. A
  hook in any other source ships or never links.
  `.github/scripts/check-test-hooks.sh` proves that no hook reaches a shipped
  artifact. Run it after `make`, `make build/lib/libunit.a` and `make tests`.
- **Every new test must fail on the code before the change.** Run it on the
  parent commit and record the failure. A test that passes on both proves
  nothing about the fix.

## Commit messages

The repository documents Conventional Commits, and mostly practises them — but
the tree carries a visible seam from upstream, and knowing which is which saves
an argument in review.

**What `CONTRIBUTING.md` requires:** Conventional Commits (`fix:`, `feat:`,
`docs:`), and PR titles must follow the same form rather than the branch name.

**What the last 200 commits on `master` actually use:**

| Prefix | Count |
|---|---|
| `fix` | 52 |
| `test` | 22 |
| `feat` | 14 |
| `ci` | 12 |
| `chore` | 7 |
| `docs` | 6 |
| `build` | 5 |
| `refactor` | 3 |
| bare `area:` (`http`, `router`, `var`, `mp`) | 8 |

Two things to notice. The vocabulary is occasionally extended beyond the
standard set (`diag(libunit):`, `data(eol):`), and a residue of commits use a
bare nginx-style `area:` prefix — `c9a7f48d "mp: keep the nxt_mp_get() cursor
aligned by rounding, not flooring"` is a recent example. That form is inherited
habit, not the documented convention.

**What upstream `nginx/unit` used,** for contrast, since much of the history
predates the fork: of its last 200 subjects, 102 use a capitalised `Area: ...`
prefix and 98 are plain imperative sentences (`Remove SUPPORT.md`). Upstream
never used Conventional Commits. Do not take commit style from `git log` alone
— you will sample the old world.

**So: use Conventional Commits for new work.** `type(scope): imperative
summary`, scope being the subsystem (`http`, `router`, `mp`, `php`).

**Nothing enforces any of this mechanically.** There is no commitlint or
PR-title workflow in `.github/workflows/`; the only commit-level gate is
`lint-whitespace.yml` running `git log --check`, which catches whitespace
errors in the diff and says nothing about the subject line.

### The body is where the value is

The subject is a label; the body is the argument. The convention that has held
up best in this tree is: what was wrong, why it was wrong, what the fix does,
and how it was verified — in prose, wrapped at 72–75 columns, with the
mechanism spelled out rather than gestured at. A reader hitting the commit from
`git blame` in two years has only this.

If a fix is architecture- or environment-specific, put the *evidence* in the
message — the faulting instruction, the captured address, the failing
assertion. Cite function names rather than line numbers: a later rebase moves
the lines and leaves the citation pointing somewhere plausible and wrong.

## Approach

**Prove the bug before fixing it, and prove the fix after.** A regression test
that does not fail on the unpatched tree is testing nothing — see
`skills/freeunit-test/SKILL.md`.

**Prefer the narrowest correct fix,** and say in the message what you
deliberately did not fix. A one-line change with a paragraph of reasoning lands
faster than a refactor that also fixes it.

**Fix the shared code, not the architecture that noticed.** A crash on one
platform is usually undefined behaviour everywhere that one platform happens to
detect. Where you can, land the invariant as an assertion or unit test that
runs on every architecture, so the next regression is caught without the
hardware.

**Close the feedback loop early, not at the end.** An experiment, a review
round or a fix is a sequence of checkpoints, and each one is a chance to find
out that you are wrong while it is still cheap:

1. Before the run, write down what you expect: the test that should fail on
   the parent, the number that should move, the CI job that should go green.
2. After each step (configure, build, first test, first benchmark cell, first
   CI job to finish), compare against that expectation at once. Do not queue
   the next step behind an unread result.
3. When something fails, a CI job goes red, or an assumption turns out false,
   say so in the next status line, with the failing line quoted, before you
   do anything else. A failure first reported in the final summary is a late
   report, even if the summary is correct.
4. Then try the fix on the spot: the narrowest change, re-run only the step
   that failed, and confirm it now passes. Carry on only when it does.
5. If the fix is out of scope, needs a decision, or fails twice, stop and
   report what failed, what you tried and what is left. Do not route around
   it or finish the remaining steps as if it had passed.

A red CI job counts the same as a local failure: read its log as soon as it
fails (`tools/watch-run.sh`), not when the whole run ends, and decide whether
it is this change or infrastructure, with the log line as evidence.

**Separate a latent finding from the fix in hand.** If you find a second bug
while proving the first, document it in the PR and file it — do not grow the
diff. Reviewers can only verify what they can hold in their head.
