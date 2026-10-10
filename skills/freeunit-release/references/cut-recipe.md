# The cut recipe: files, order, commands

Verified against two real cuts in `freeunitorg/freeunit`:

- **1.36.0** — integration branch `pre-1.35.6`, PR #132 for the version bump,
  merged to `master` via PR #139 (`7a266dc5`), tagged at `dddafac4` (one PR
  later — see "The tag is not always the merge commit" below).
- **1.34.2** — the maintained `1.34` LTS branch, `c5aabd39` (version bump) through
  `8ab74a8c` (tag).

The primary source is **`RELEASE-PROCESS.md`** at the repo root — read it
first. This page cross-checks it against both cuts' actual history and records
where the two diverge or where the doc is silent.

## Pre-cut checklist

- Confirm the version number by reading `git tag -l`, not the branch name. A
  branch called `pre-1.35.6` shipped as `1.36.0` — `RELEASE-PROCESS.md`'s own
  appendix example (`NXT_VERSION=1.36.0`) has to be updated on every cut too
  (`7c74a915` touched it as a drive-by).
- Run the consistency check `RELEASE-PROCESS.md` gives before touching
  anything:
  ```
  grep -nE 'NXT_VERSION=|NXT_VERNUM=' version
  grep -m1 'Changes with FreeUnit' CHANGES
  grep -m2 'ver=' docs/changes.xml
  sed -n '3p' docs/unit-openapi.yaml
  ```
- **Run the example checker** before touching `docs/unit-openapi.yaml`:
  `tools/openapi-example-check.py --repo src`. It validates every committed
  example against the schema it's attached to. The 7 known `/config`
  failures (mime-types `location` example under a string-only schema) are
  pre-existing — anything else in the FAIL list is new and blocks the cut.
  Add `--required-hints` to also flag property descriptions implying
  always-present wording ("always present", "is always", "every response",
  etc.) that isn't backed by `required` (informational; `--strict-hints`
  turns a hit into a failure). Needs PyYAML + jsonschema (`python3 -c
  'import yaml, jsonschema'`; if missing, `uv pip install pyyaml
  jsonschema`).
- **Diff `CHANGES` against a freshly generated one before trusting either.**
  `CHANGES` is supposed to be *generated from* `docs/changes.xml`, but it has
  been hand-edited directly at least twice: `77c3eaaf` (1.35.6 hardening
  bullets) and `2ab11653` (a 1.36.0 wording tweak — "note control-socket perms
  are defense-in-depth"). Neither touched `docs/changes.xml`. The result: as
  of the 1.36.0 tag, the isolation-hardening bugfix bullet exists in `CHANGES`
  in wording that **does not appear anywhere in `docs/changes.xml`** — running
  `make -C docs changes` today would silently drop it. Check before you rely
  on either file as the source of truth.

## The cut sequence

Both cuts touch the same file set, but the two branches order and group the
commits differently — match whichever pattern the target branch already uses
rather than inventing a third.

**Master style (1.36.0), one combined commit then two follow-ups:**

1. **`542f28bd` — bare version number as the subject (`1.36.0`).** Bumps, in
   one commit: `version` (`NXT_VERSION` + `NXT_VERNUM`), `CHANGES`,
   `docs/changes.xml` (both the `ver=`/`date=` header and any new changelog
   entries), and `docs/unit-openapi.yaml` line 3 (`title:`).
2. **`7c74a915` — `chore(unitctl): bump crate versions to X.Y.Z`.** Bumps
   `tools/unitctl/{unitctl,unit-client-rs,unit-openapi}/Cargo.toml`,
   `tools/unitctl/Cargo.lock`, `tools/unitctl/openapi-config.json`, and (as a
   drive-by) `RELEASE-PROCESS.md`'s own version-appendix example and
   `tools/unitctl/CHANGELOG.md`'s tag links, plus
   `tools/unitctl/unit-openapi/README.md`.
3. **`05e89f0b` — `chore(docker): regenerate Dockerfiles for X.Y.Z`.**
   Regenerates every `pkg/docker/Dockerfile.*` from `template.Dockerfile`:
   ```
   cd pkg/docker
   make clean       # only needed if the module/version matrix changed
   make dockerfiles
   git rm/add as required
   ```
   (`make dockerfiles` invokes the `Dockerfile.%` rule, which `sed`s
   `@@VERSION@@` etc. from `template.Dockerfile`; see
   `pkg/docker/Makefile:108-121`.)

Everything after that on the 1.36.0 branch (`7bb33ea8`, `2ab11653`,
`7eb0f960`, `62745d9f`, `dea537f8`) was CI fixups and a changelog wording
tweak surfaced by review on PR #139, not steps of the recipe — but they show
the pattern: **expect at least one more `CHANGES`/`docs/changes.xml` touch
after the initial bump**, as review comments land.

**LTS branch style (1.34.2), one commit per concern, changelog last:**

1. `c5aabd39` "Version bump" — `version` + the `docs/changes.xml` header
   (`ver=`/`date=`) only.
2. (security fix commits land here — `c1372d1e`, `e857293f`)
3. `75208910` "tools/unitctl: Update for version X.Y.Z" — same unitctl file
   set as above, minus the `RELEASE-PROCESS.md`/`CHANGELOG.md` drive-bys
   (those didn't exist yet on this older branch style).
4. `3890466f` "docs/unit-openapi.yaml: Update version for X.Y.Z" — line 3
   only, its own commit.
5. `60bb8c48` "pkg/docker: Update dockerfiles for X.Y.Z" — same
   `make dockerfiles` regen, its own commit.
6. `f5aa2fde` "docs/changes.xml: Add X.Y.Z changelog entries" — the actual
   changelog prose, added *last*, once every fix that will ship is known.
7. `8ab74a8c` "Add X.Y.Z CHANGES" — generated, **commit message says so
   verbatim**:
   ```
   make -C docs/ changes && mv build/CHANGES .
   ```
   This is the one place in either branch's history where the exact
   generation command is spelled out in a commit message — it matches
   `RELEASE-PROCESS.md`'s own instructions and `docs/Makefile:25-41`
   (`$(DEST)/CHANGES` target: `xmllint --noout --valid changes.xml` then
   `xsltproc --stringparam format generic ... -o $@ changes.xslt changes.xml`).

Commit-message convention split: master uses Conventional Commits
(`chore(unitctl): ...`, `chore(docker): ...`) plus one bare-version subject
(`1.36.0`); the `1.34` branch (authored by the pre-fork nginx/unit maintainer)
uses plain imperative subjects with a file-path prefix (`tools/unitctl:
Update for version X.Y.Z`). Match the branch you're committing to.

## The tag is not always the merge commit

`RELEASE-PROCESS.md` says the tag "should" point at the CHANGES commit right
after merge. In practice, for 1.36.0: PR #139 merged the `pre-1.35.6`
integration branch to `master` at `7a266dc5`, and the tag (`dddafac4`) landed
one PR *later* — `fix(contrib): correct wasi-sysroot-27.0 SHA512SUM` (PR
#142), a one-line fix to `pkg/contrib/src/wasi-sysroot/SHA512SUMS` found
because the Debian `.deb` build failed on the mismatched checksum. **Verify
what the tag actually points at and what shipped in that gap** — don't assume
tag == merge commit. `1.34.2`'s tag does sit on the last commit of its
sequence (`8ab74a8c`), so this drift is not universal, just not guaranteed.

### "Is PR #N in the release?" is a question about its merge commit

Not about its branch tip. A squash merge lands the content under a **new** SHA and GitHub then
deletes the source branch, so the tip is an ancestor of nothing (see
[`../../freeunit-review/references/bots-and-gh.md`](../../freeunit-review/references/bots-and-gh.md#commands)) — a tip
that is "not in the release tree" is the normal state of a merged PR, not a missing change. Ask
about the merge commit instead:

```bash
git log --oneline <prev-tag>..<tag> | grep -F '(#235)'   # the merge/squash subject
git branch --contains <merge-sha>
```

For the same reason `master` legitimately lags the release content while a cut is in flight: the
order is push → PR → merge → tag, so "`master`'s `CHANGES` has 18 entries, not 36" is the expected
state of an unmerged release branch, not a lost changelog. Establish where in that order you are
before reporting a discrepancy — see the [verify skill](../../freeunit-verify/SKILL.md).

## Tag triggers, by workflow

| Workflow | Tag pattern | What it does |
|---|---|---|
| `.github/workflows/release-docker.yml` | `[0-9]+.[0-9]+.[0-9]+` (push) | Builds the `pkg/docker/Dockerfile.<variant>` matrix (variants from `pkg/eol.json`), pushes `amd64`+`arm64` to both `ghcr.io/freeunitorg/freeunit` and `docker.io/freeunitorg/freeunit`. Fixed by `53e781ec`/`d779cb00` (`fix(ci): restrict docker build trigger to semver tags`) after an unpinned `'*'` trigger picked up `unitctl/*` tags and broke the checkout. |
| `.github/workflows/build-deb.yml` | `[0-9]+.[0-9]+.[0-9]+` and `[0-9]+.[0-9]+.[0-9]+-build[0-9]+` (push) | Builds Debian trixie `.deb`s, then its `release` job (`if: startsWith(github.ref, 'refs/tags/')`) computes `SHA256SUMS` and attaches the `.deb`s + `SHA256SUMS` to the GitHub Release via `softprops/action-gh-release`. |
| `.github/workflows/unitctl.yml` | `[0-9]+.[0-9]+.[0-9]+` (push, also `master`) | Builds `unitctl` for linux/macOS, then (`if: startsWith(github.ref, 'refs/tags/')`) creates a **separate** GitHub Release tagged `unitctl/<ref_name>` — not the daemon's tag. Isolated from the daemon release by `bf3f716e`/`42de500a` (`fix(ci): isolate unitctl releases under their own tag`, #69) after `allowUpdates: true` on the shared tag let a unitctl release overwrite the main release body. |

So one `git tag -a 1.36.0 && git push` fans out to three workflows and one
extra CI-created tag (`unitctl/1.36.0`) — you never push that second tag
yourself.

## CHANGES generation

```
cd docs
make changes         # -> ../build/CHANGES
mv ../build/CHANGES ../CHANGES   # or: make -C docs changes && mv build/CHANGES .
```

Requires `xmllint` and `xsltproc` (`docs/Makefile:36-41`, using
`docs/changes.xslt`, itself generated from `changes.xsls` via `xslscript.pl`).
**`xsltproc` is not installed on this dev machine** (`command -v xsltproc`
exits 1) — you cannot run this step here without installing it first.

### Python fallback generator

`tools/changes-gen.py` (from the repository root) reproduces the
`xsltproc` transform without needing it installed. The three formatting
settings came from bisecting against the real file, not from reading
`changes.xslt`: width 76, `break_on_hyphens=False` (or `use-after-free`
splits mid-word), `break_long_words=False`, and whitespace collapsed
(`changes.xml` uses two spaces after a sentence, `CHANGES` uses one).

```
tools/changes-gen.py verify 1.36.0 --repo src   # prove it reproduces CHANGES
tools/changes-gen.py print  1.36.0 --repo src   # emit the section, don't write it
tools/changes-gen.py write  1.36.0 --repo src   # rewrite that section in place
```

`write` derives the header's date from the matching `<changes ver=...>`
block's `date=` attribute in `changes.xml` — it does not copy the existing
`CHANGES` header line verbatim, so a date bump in `changes.xml` reaches
`CHANGES` without a hand edit. `verify` checks the header too, not just the
body.

**Always run `verify` before trusting `write` on a version you haven't
generated before.** It regenerates a section that already exists in
`CHANGES` and diffs it byte-for-byte; skipping straight to `write` risks
reflowing untouched entries into a noisy diff, or — see below — silently
regenerating the wrong content entirely.

**Only the `apply="unit"` block feeds `CHANGES`.** `changes.xml` carries one
`<changes>` element per released *package* per version — `apply="unit"` (the
core, what `CHANGES` documents) plus one per language module
(`apply="unit-php"`, `apply="unit-python..."`, etc.), almost all of which are
just "NGINX Unit updated to X.Y.Z". **This was a real bug in the tool's first
draft**, caught by actually running it against this repo's own `src/`
checkout rather than trusting the docstring that shipped with it: an
unfiltered `<changes` match sees every one of those per-module blocks too, so
it found **52 of 54 versions "ambiguous"** and would have refused to `write`
almost always instead of almost never. The fix filters to `apply="unit"`
exactly; only one real case remains ambiguous after that (`1.4`, which
genuinely has two `apply="unit"` blocks for the same `ver`), and `write`
still correctly refuses on it. If you ever see "ambiguous, refusing to
guess" for anything other than `1.4`, treat it as a regression in the filter,
not a normal outcome.

Not every version in `docs/changes.xml` has a matching `CHANGES` section —
some patch releases were never written up (npm/Docker-only, no user-visible
change). `verify`/`write` say so explicitly rather than raising a raw
`ValueError` from an unhandled `str.index()`; use `print` to see what a
missing version would generate.

## Post-cut checklist

- **GitHub Release** exists at the tag (created/updated by `build-deb.yml`'s
  `release` job — see table above). Its `.deb`/`SHA256SUMS` assets are
  automatic; the human-written top section (security/feature/change summary,
  GHSA links, "Full details in CHANGES" link) is not generated by any
  workflow in `.github/workflows/` — write and attach it yourself, on top of
  whatever GitHub's own "What's Changed"/"New Contributors"/"Full Changelog"
  auto-notes add.
- **Advisories, at the tag.** GHSAs already drafted privately are published
  the moment a fixed version exists — see SKILL.md's existing note. 1.36.0
  shipped with `GHSA-768w-qrh2-jjvh` and `GHSA-g6v8-r577-76jm` referenced
  directly in the release body.
- **`SECURITY.md` supported-versions matrix.** Updated *after* the tag, as its
  own chore commit — `349fde84` "chore(security): supported-versions matrix
  for 1.36.0" touched only `SECURITY.md`, marking the new line Active and
  retiring the one it superseded (not a subset relationship — the advisories
  in that commit backported to `1.34.x` LTS, not `1.35.x`, so `1.35.x` went
  straight to EOL).
- **Docker images** land on GHCR and Docker Hub automatically (see table
  above; Docker Hub push added in `291697cd`
  `ci(release-docker): push images to Docker Hub`).
- **`packages.freeunit.org`** mirrors contrib source tarballs (njs, wasmtime,
  wasi-sysroot, libunit-wasm — see `TODO.md:508-528`) and is referenced as an
  RPM/DEB package host in `pkg/community/REMI.md`; nothing in this checkout's
  CI publishes there automatically — treat it as an external, manually
  maintained mirror, not a step this repo's workflows perform.
- **Alpine.** There is no FreeUnit `APKBUILD` or Alpine packaging anywhere
  under `pkg/` in this checkout (checked `pkg/community/`, `pkg/eol.json`,
  and a repo-wide search for `apkbuild`/`aports`) — an `APKBUILD` sitting in
  the checkout root is an unrelated, untracked file for an unrelated package
  and is not evidence of in-tree Alpine support. If Alpine packaging is
  wanted, it's an external `aports` contribution with no existing hook in
  this repo; don't assume one exists.
- **Backports.** A maintained LTS branch (`1.34`) gets the same fixes ported
  and released under its own tag, on its own schedule — see the LTS-style
  sequence above. It is not a merge of `master`; it's a separate cut with its
  own review.
- `RELEASE-PROCESS.md`'s own "Tarball" (`pkg/ && make dist`, then an rsync
  to the download server) and "Docs" (copy `CHANGES` into the
  `unit-docs` repo as `source/CHANGES.txt`) sections describe manual,
  pre-fork-era steps. Nothing in `.github/workflows/` references
  `freeunit.org/download` or a docs-repo sync — treat these two sections as
  unverified for the current fork rather than as confirmed practice.

## `docs/changes.xml` has TWO stanzas per release — finalise both

Each release carries an `apply="unit"` `<changes>` block (→ `CHANGES`) **and** a per-module block
(`apply="unit-php unit-php8.3 … unit-python3.14 …"`, → the Debian/RPM language-module changelogs via
`docs/changes.xslt`'s `string_in_list` filter). `RELEASE-PROCESS.md` ("Files that must move in
lockstep", item 3) requires both with matching `ver=`/`date=`. The 1.36.1 cut (PR #237) finalised
only the `unit` block and Codex caught it: every module changelog would have started at 1.36.0
while the packages built as 1.36.1. Mirror the previous release's module stanza (same module list,
new ver/date, entries per its convention). `tools/changes-gen.py` filters `apply="unit"`, so it will
NOT notice a missing module stanza — check it by hand: `grep -c 'ver="X.Y.Z"' docs/changes.xml` must be 2.

## `pkg/eol.json` feeds four matrices — do not park dependency floors there

`.os`/`.runtimes` in `pkg/eol.json` drive `build-test.yml`, `release-docker.yml`,
`pkg/docker/Makefile` and `build-local.sh`. Adding OpenSSL or bundled jars under `runtimes` would
conjure phantom Docker variants and, since OpenSSL 1.1.1 is EOL (2023), hard-fail every PR touching
those paths. Record such floors under a separate top-level key (e.g. `dependencies`), which none of
the four consumers nor the validator's parser reads. (Found 2026-08-28 while placing the OpenSSL 1.1.1
floor from #224.)

## Docs repo `source/CHANGES.txt`: prepend the new section, never sync the whole file

The docs site's `CHANGES.txt` and the code repo's `CHANGES` have diverged historically: the site
carries lines the code repo does not (a seccomp/CVE-2026-31431 note, PHP 8.5 `pre_request_init`,
parts of 1.34.x/1.32.1). Copying `CHANGES` over `CHANGES.txt` silently deletes them (16 lines on
2026-08-28). The release commit must prepend only the new `Changes with FreeUnit X.Y.Z` section and
leave everything from the previous header down byte-identical to `main` (expect N additions, 0
deletions in the diff).

Check the destructive half explicitly — the sync was verified hard on the *source* side (header
date, entry count, byte-comparison of the new section) and every one of those checks looked only at
what arrived:

```bash
git diff origin/main -- source/CHANGES.txt | grep -c '^-[^-]'    # must be 0
```

The 16 deleted lines included a CVE-2026-31431 seccomp mitigation note that exists nowhere in the
code repo, so no comparison against `CHANGES` could have surfaced it. See the [verify
skill](../../freeunit-verify/SKILL.md) for the general rule.

## Never regenerate `CHANGES` wholesale — splice the one section

`make -C docs changes` renders the whole file: `changes.xslt:244` hardcodes "Unit" (every
"Changes with FreeUnit" header would be rebranded and shortened by four columns) and
`docs/changes.xml` carries only 14 of 1.36.0's 42 entries, so 28 entries would vanish — and a clean
build plus green CI would not notice. Use `tools/changes-gen.py write X.Y.Z` (section-only,
byte-for-byte `verify`) and guard with per-version bullet counts before/after:
`awk '/^Changes with/{v=$4} /^    \*\)/{c[v]++} END{for(k in c) print k, c[k]}' CHANGES | sort -V`
— only the section being cut may change.
`changes-gen.py` wraps with Python `textwrap`, not the XSLT; `verify` proves agreement only for
text that already exists in both files. For NEW prose, cross-render the section through
`docs/changes.xslt` (lxml `etree.XSLT`, params `format='generic'`, `pkgname='unit'`, DTD-validate)
and diff the one section against the written `CHANGES` (normalise the "Changes with Unit" header
the XSLT emits). A long token, a double space mid-sentence or an em-dash can wrap differently.
Post-release cleanup owed: reconcile CHANGES ↔ changes.xml
history both ways (CHANGES also lacks three entries the XML has: 1.35.5 chunk extensions, 1.35.5
isolation mount comparator, 1.35.4 Docker images note).

## Re-read "no changes" claims after every merge into the release branch

`tools/unitctl/CHANGELOG.md`'s 1.36.1 entry said "no functional changes to unitctl" — true when the
crate bump was written, false after #241 (error reporting) and #247 (int64 counters) merged into the
same release branch later. Nothing re-checks such sentences. Before merging the release PR, grep the
tagged tree for "no functional changes" / "no changes" / "unchanged" in changelogs and release notes
and re-verify each against `git log <branch> -- <path>` since the sentence was written.
