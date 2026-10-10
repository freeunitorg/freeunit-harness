# Release version sites

Every file in https://github.com/freeunitorg/freeunit that carries the FreeUnit release version, how it
moves at a release, and what checks it. Audited 2026-10-06 on chore/post-1.37.0 (28f7f102) plus the
three commits of https://github.com/freeunitorg/freeunit/pull/588 that add the gate and the bump script.
Sources: the 1.37.0 release commit 61b297ac (38 files), the 1.36.1 bump commits 4484b540 (version),
bcf560a8 (unitctl crates) and 212ea040 (Dockerfiles), and
`git grep -nE '1\.37\.0|1\.36\.1|13700|13601'` over the tree.

## The source of truth

`version` holds `NXT_VERSION` and `NXT_VERNUM`. These read it directly, so a bump needs no edit there:
`configure` and `auto/make` (`build/include/nxt_version.h`, `unitd --version`, the `Server:` header),
`auto/modules/java` (jar names), `auto/modules/nodejs` (`unit-http` package.json from `%%VERSION%%`),
`pkg/Makefile` (tarball), `pkg/deb/Makefile`, `pkg/rpm/Makefile`, `pkg/npm/Makefile`,
`pkg/docker/Makefile`, `pkg/deb/build-local.sh`, and build-deb.yml (`grep '^NXT_VERSION='`).

## Sites that copy the version

"bump" = `tools/bump-version.sh X.Y.Z`. "gate" = `.github/scripts/check-version.sh`, a step of the
whitespace lint job on every pull request. Both read the one list, `.github/scripts/version-sites` (one row
per site, with its kind and the reason). A new site is one row there. Follow-up to the review at
https://github.com/freeunitorg/freeunit/pull/588#issuecomment-6021082311.

| file | today | consumer | bump | gate |
|---|---|---|---|---|
| `version` NXT_VERSION, NXT_VERNUM | by hand | everything above | yes | VERNUM = X*10000+Y*100+Z |
| `docs/unit-openapi.yaml` line 3 `title` | by hand | the spec and the unitctl client generated from it | yes | yes |
| `tools/unitctl/{unitctl,unit-client-rs,unit-openapi}/Cargo.toml` `[package] version` | by hand | crate metadata, `unitctl --version` | yes | yes |
| `tools/unitctl/Cargo.lock` (3 entries) | by hand or by cargo | `cargo build --locked` fails when stale (checked: rc 101) | yes | yes |
| `tools/unitctl/openapi-config.json` `packageVersion` | by hand | openapi-generator, writes the next line | yes | yes |
| `tools/unitctl/unit-openapi/README.md` "Package version" | generated, committed | readers | yes | yes |
| `pkg/docker/Dockerfile.*` (30) | `make dockerfiles` | release-docker.yml, security-seccomp.yml, build-local.sh | yes (make -B) | regenerates in a temp copy; stale, missing or extra files fail |
| `CHANGES` "Changes with FreeUnit X" block | by hand | release notes, unit-docs `source/CHANGES.txt` | no | block exists |
| `docs/changes.xml` two stanzas `ver="X"` | by hand | deb and rpm changelogs; a missing module stanza fails every module package | no | exactly 2 for NXT_VERSION and for the top CHANGES block |
| `tools/unitctl/CHANGELOG.md` `## [X]` and `[X]:` link | by hand | unitctl release notes | no | both exist |
| `SECURITY.md` supported-versions table | by hand, after the tag at 1.37.0 (5c376d26 in PR 588) | users | no | the X.Y.x row says Active |

Before the change in this PR, each Dockerfile carried the version twice (`git clone -b` and the
`image.version` label). release-docker.yml rewrote both with sed at build time, so a stale file still made a
correct image and nothing reported it. Now the template declares `ARG UNIT_VERSION=<NXT_VERSION>`, the
workflow passes `build-args: UNIT_VERSION=<tag>`, and the generated files differ only in that default.

## What the 1.37.0 release got wrong

- b5bf3e67 (2026-10-04) added Dockerfile.go-1.27, Dockerfile.java-27 and Dockerfile.perl-5.44 with `-b 1.36.1`
  and `image.version="1.36.1"`. 61b297ac was rebased on top of it and touched only the 27 older Dockerfiles.
  The 1.37.0 tag ships the three stale files. The gate, run on the 1.37.0 tag, names exactly those three,
  plus SECURITY.md (no 1.37.x Active row). It passes on 1.36.1.
- SECURITY.md was not in 61b297ac; 5c376d26 in PR 588 updated it after the tag.

## Checklist corrections (release-plan-1.37.1.md section 6)

- All ten items exist and match 61b297ac. Item 9 said 30 Dockerfiles; 61b297ac changed 27, which is the bug.
- Item 9's check `grep -L -- '-b 1.37.1' ...` stops working after this PR: the line reads
  `-b "${UNIT_VERSION}"`. Use `sh .github/scripts/check-version.sh` instead.
- Missed: `SECURITY.md` (not in 61b297ac). Not missed but worth knowing: the unit-docs repo
  `source/CHANGES.txt` is a second copy of CHANGES outside this repo; no gate covers it.
- The gate replaces the "Before the merge" grep block for the version lines. The entry-count awk, xmllint and
  CHANGES/changes.xml pairing checks remain manual.

## Cannot be automated

- The CHANGES date (tag date, same column) and the `date=` of both changes.xml stanzas.
- The unitctl CHANGELOG.md section text, and whether unitctl changed at all.
- The SECURITY.md policy: which older minor lines become EOL.
- The commit author and trailers of the release commit (61b297ac was re-created without the AI trailer).

## Version strings that are NOT the release version

A bump script must never match these. bump-version.sh edits only anchored lines (`^NXT_VERSION=`,
the `[package]` version, the Cargo.lock line after `name = "<crate>"`), never a bare `s/1.37.0/1.37.1/`.
The Rust image pin (1.95.0) is a `pin-source` row (template.Dockerfile) plus a `pin` row in each of nine
other files; the gate checks they agree, and bump-version.sh skips pin rows.

| where | value (2026-10-06) | what it is |
|---|---|---|
| `rust-version` in unitctl crates, `src/otel/Cargo.toml` | 1.95, 1.86, 1.88 | Rust MSRV per crate |
| `pkg/docker/template.Dockerfile` RUST_VERSION, rustup archive | 1.95.0, 1.27.1 | image toolchain |
| build-deb.yml `RUST_TOOLCHAIN` | 1.88.0 | deb build toolchain |
| `auto/modules/java` NXT_JAR_VERSION; `pkg/eol.json` jetty and ecj rows | 9.4.58.v20250814, 3.26.0 | bundled Jetty and ECJ |
| `pkg/contrib/src/wasmtime/version`, `pkg/contrib/src/njs/version` | 48.0.5, 1.0.1 | contrib pins |
| `pkg/eol.json` runtimes and os | e.g. PHP 8.3 8.4 8.5 | the Dockerfile variant matrix |
| `docs/unit-openapi.yaml` `info.version` | 0.2.0 | API spec version |
| `src/otel`, `src/wasm-wasi-component`, `test/fake_otlp` Cargo.toml | 0.1.0 | internal crates |
| older blocks of CHANGES, changes.xml, unitctl CHANGELOG, SECURITY.md rows | 1.36.1 and earlier | history |
| `RELEASE-PROCESS.md` appendix example | 1.36.1 / 13601 | illustration of the file format |
| `pkg/docker/local/Dockerfile.*` (builder mode) | 1.35.5 | hand-written, stale; build-local.sh seds the -v value in at build time |
| TODO.md, pkg/community/*.md, workflow comments | 1.35.x | milestones and examples |
