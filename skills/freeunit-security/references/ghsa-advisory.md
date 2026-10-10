# Writing and filing a GitHub security advisory (GHSA)

Recorded 2026-10-04, from the 1.37.0 release. The maintainer files advisories by hand in the GitHub UI: the
auto-mode classifier blocks creating an advisory or its private fork through the API.

## Where drafts live

Drafts are private and stay local: keep them in a gitignored directory of your own checkout,
for example under `run/`, never in a tracked file, an issue or a pull request. The layout
the maintainer uses:

- an index file that lists every advisory and its state;
- one paste-ready draft per advisory, in form layout, named
  `<release>-<GHSA id>-<NN>-<slug>.md`. Use `GHSA-TBD` until filed, then rename the file
  to the real ID;
- a long version of each, with all the reasoning;
- one file per issue, with the tag-by-tag range derivation.

Never keep a draft only in a session scratchpad: it is lost when the session ends.

## The form, field by field (in this order)

1. **Title**: one line, at most about 100 characters.
2. **CVE identifier**: "Request CVE ID later", then request it from the draft. GitHub's review can take a day or
   more, so request it before the release.
3. **Summary**: 2 to 4 sentences. Say who is affected, under which settings, and the worst effect.
4. **Details**: one short subsection per issue. Give the cause, the function and file as a full GitHub URL at the
   last affected tag, the affected range and the fix commit URL.
5. **Proof of concept**: the minimal configuration and the request shape in words, plus the regression test that
   the fix commit added (`file::test`). Never a byte-exact payload or an exploit script.
6. **Impact**: the class of weakness and who is impacted, plus a workaround in one sentence where one exists.
7. **Affected products**: `Ecosystem: other`, `Package name: freeunit` (the GHSA-768w-qrh2-jjvh precedent),
   `Affected versions: >= X, < Y`, `Patched versions: <release>`. If nginx/unit shares the code, add a second
   row (`nginx-unit`, with no patched version, because the repository is archived).
8. **Severity**: the rating plus the exact CVSS 3.1 vector string.
9. **CWE**: every CWE involved.
10. **Credits**: the reporter's GitHub login.

End the draft file with a "Before filing" checklist that is not pasted: the
`git merge-base --is-ancestor <fix> <release tag>` command for every fix commit, to run after the tag.

## Ranges

- Derive each affected range by reading the code at EACH tag (`git show <tag>:<path>`), never from commit
  messages or old notes. Drafts made in 2026-09 had seven range errors.
- List the tags with `git tag -l | sort -V`. A prefix filter drops `v1.35.1`.
- The clone is shallow, so `git log -S` and `git tag --contains` give wrong origins.
- Check upstream nginx/unit (remote `o`) for the same code and name it if it is affected.

## Bundling

Group issues by component and attacker, as GHSA-768w-qrh2-jjvh does. For 1.37.0 the bundles were: router remote
DoS; access-log injection; wasm memory bounds; app isolation. A bundle has one vector, the worst issue's, and one
section per issue. Keep the per-issue files in `parts/`, in case a reviewer or a CNA wants one CVE per issue.

## Timing and the fix commit

- A fix commit for an unpublished issue carries NO advisory ID and no exploit detail; the commit message is
  immutable. The release commit adds the GHSA IDs to CHANGES and docs/changes.xml.
- File the advisories as drafts BEFORE the release commit, so their IDs can go into the release notes. Drafts are
  private until published.
- A fix that is still private goes in through the advisory's temporary private fork: push the fix there, open the
  PR inside the fork, and merge it from the advisory page. CI does not run in a private fork, so verify the fix on
  the build box first (the C suite, the new tests run 10 times, ASan, and a control run on master that fails).
- Publish after the tag and the packages, once the merge-base checks pass.

## Precedent: how 1.36.0 published (checked 2026-10-04)

GHSA-768w-qrh2-jjvh and GHSA-g6v8-r577-76jm were drafted on 2026-07-06, 11 days before the release. The GitHub Release
1.36.0 came out on 2026-07-17 at 05:53Z, and both advisories were published at 07:50Z, about 2 hours later. Fields:
`other` / `freeunit` / `< 1.36.0` / patched `1.36.0`, with no credits. CHANGES carries no GHSA IDs. The release body
lists them after "Full details in CHANGES. The ... fixes are covered by N advisories:", one linked line each. Only
g6v8 got a CVE (CVE-2026-84300), and it was assigned weeks later, so request CVEs from the drafts before the release.
