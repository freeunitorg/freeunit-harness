# Harness CI

This page is about the CI of this repository, the harness. It is not about
FreeUnit's CI; for that, see the other pages in the CI section of
[README.md](README.md).

The workflow is `.github/workflows/ci.yml`. It runs on every pull request and on
every push to `main`. Each job calls an entry point that also runs on your
machine, so you can run the same check before you push.

## Jobs

| Job | Command | What fails it |
|---|---|---|
| `tests` | `make tools-test` | a unit test of the harness tools fails |
| `public-text` | `tools/check-public.sh --all`, then `tools/check-public.sh --history` | a tracked file, or any line ever added in the history of the checked-out commit, matches a rule in `tools/public-deny.txt` |
| `agents` | `make agents-check` | the `.claude`/`.opencode` agent files or the skills links differ from a fresh `tools/gen-agents.py` run |
| `trailers` | `tools/check-trailers.py <commit>` for each new commit | a `Co-Authored-By` or `Reviewed-by` trailer breaks the rules in AGENTS.md, or a commit has a `Claude-Session` trailer |
| `shell` | `tools/check-shell.py` | shellcheck reports an error in a tracked shell script |

Notes:

- `tests` uses Python 3.13 and installs only pytest and PyYAML, at fixed versions.
  Python 3.13 is the first version that raises the `time.sleep` audit event.
  Some tests use that event, and Python 3.12 skips them.
- `public-text` checks out the full history (`fetch-depth: 0`). It uses the
  public rules only. The private deny file (`HARNESS_DENY_FILE`) is not in CI.
  Before `--history`, it deletes every ref except the checked-out branch, so
  the scan covers only the history of the checked-out commit, not other pull requests.
- `trailers` checks the commits of the pull request (base to head). On a push
  to `main` it checks the pushed commits. When the old tip is not known (a new
  branch or a forced push), it checks each pushed commit that no other branch
  of the repository has, up to the root commit. A commit that is already on
  another branch is not checked again.
- `trailers` fails with exit code 2 when `git rev-list` cannot list the
  commits, for example for a bad SHA. It also fails when a pull request has
  no commits in its range.
- `shell` picks every tracked file whose first line is a `sh`, `bash`, `dash`,
  `ksh` or `ash` shebang. It fails on the `error` level only. Run
  `tools/check-shell.py --severity warning` to see more. CI installs
  shellcheck 0.11.0 from its GitHub release and checks the sha256 first.
  Portability rules are in [shell.md](shell.md).

## Safety

- The workflow uses no secrets. The token has `contents: read` only, and
  checkout does not keep it on disk (`persist-credentials: false`).
- It triggers on `pull_request`, never on `pull_request_target`. Code from a
  pull request, also from a fork, runs only in a disposable GitHub-hosted
  runner, without write access to the repository.
- Every action is pinned to a full commit SHA, with the version in a comment.
- A new push to a pull request cancels the older run of that pull request.
  Runs on `main` are never cancelled.

## Branch protection

CI does not protect `main` by itself. The maintainer sets that in the GitHub
settings of the repository: require a pull request before merging, and require
these status checks to pass:

- `tests`
- `public-text`
- `agents`
- `trailers`
- `shell`

## Before you push

```sh
make tools-test
tools/check-public.sh --all
make agents-check
for c in $(git rev-list origin/main..HEAD); do tools/check-trailers.py "$c"; done
tools/check-shell.py
```

`tools/check-trailers.py origin/main..HEAD` checks only the newest commit of
that range, so use the loop above.
