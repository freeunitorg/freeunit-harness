# Portable shell

Rules for shell scripts in FreeUnit and in this harness, and how to check them.

## Python first, POSIX sh for entry points

Python is the primary language for tooling here (`tools/*.py`). Write new
logic in Python. Shell stays only where it earns its place: small entry points
that must run on any host before anything else is installed, `configure`-style
probes under `auto/`, and CI glue.

Every such script is **POSIX `sh`**, not bash:

- the shebang is `#!/bin/sh`;
- it must run unchanged under Alpine `ash` (busybox), Debian/Ubuntu `dash`,
  RHEL `sh` (bash in POSIX mode), Solaris `/bin/sh` and the BSD `sh`;
- no bashisms: no `[[ ]]`, arrays, `function f`, `==` in `test`,
  `$'...'`, `<<<`, `<( )`, `{a,b}` brace expansion, `${var//x/y}`,
  `${var:0:3}`, `source`, `echo -e`/`echo -n` (use `printf`), `set -o pipefail`
  (not in every target shell), `$RANDOM`, `&>`. `local` is not POSIX either; most target shells accept it,
  but keep it out of `auto/`.

If a script genuinely needs bash, say so in the shebang (`#!/usr/bin/env bash`)
and keep it off every path that a user or a non-Linux build runs.

## Portable sed

`sed` differs more across systems than the shell does. Use only what POSIX
specifies:

| Avoid | Why | Use instead |
|---|---|---|
| `sed -E`, `sed -r` | extended regex flag; missing in older Solaris `/usr/bin/sed` | POSIX BRE |
| `sed -i` | GNU and BSD take different arguments (`-i` vs `-i ''`), POSIX has none | write to a temp file, then `mv` |
| `\+`, `\?`, `\|` in BRE | GNU extensions | `\{1,\}`, `\{0,1\}`, two expressions or two `-e` |
| `\t`, `\n` in the pattern or replacement | not portable | a literal tab, or `tr` |
| `I` / `M` flags, `0~2` addresses | GNU only | rewrite, or do it in Python |

Real case: [freeunit#626](https://github.com/freeunitorg/freeunit/pull/626)
added `sed -E` to `auto/options`. Under `set -e`, `configure` with any option
would fail on Solaris, whose stock sed accepts only `-n`, `-e` and `-f`. The fix
in [freeunit#726](https://github.com/freeunitorg/freeunit/pull/726) rewrote the
expression in BRE and checked that output matched on the test inputs under
`sed --posix` and busybox sed.

If an ERE is really needed, detect it once rather than assuming:

```sh
if echo a | sed -E 's/a+/b/' >/dev/null 2>&1; then
    SED_ERE='sed -E'
else
    SED_ERE=''   # fall back to a BRE code path
fi
```

The same caution applies to `grep -P`, `grep -o`, `readlink -f`, `date -d`,
`stat -c`, `mktemp` without a template, `xargs -r` and `find -maxdepth`: check
the POSIX page before using a flag.

## Multishell testing

Run a changed script under several shells before pushing. `bash --posix` is the
closest a Linux host gets to RHEL `sh`; `dash` and busybox `ash` catch most
bashisms that `bash` silently accepts.

```sh
f=path/to/script.sh

dash -n "$f" && dash "$f" --help
bash --posix --norc "$f" --help
busybox ash "$f" --help                  # if busybox is installed

# Alpine ash in a container; pass --platform explicitly
docker run --rm --platform linux/amd64 -v "$PWD:/src:ro" -w /src \
    alpine:edge sh "$f" --help
```

Static checks, where available:

```sh
shellcheck -s sh "$f"
checkbashisms "$f"          # Debian devscripts
```

`tools/gates/run-gates -s <checkout> -g 8` (G8) runs `dash -n` and busybox
`ash -n` on each tracked `#!/bin/sh` file of a FreeUnit checkout. It also
greps for traps that `shellcheck -s sh` misses: `echo "$x" |` (stronger
when piped to `jq`, `python` or `base64`), `| grep -q` in a file that sets
`pipefail`, and an `EXIT` trap without `HUP`/`INT`/`TERM` traps. It prints
`WARN:` lines (`WARN: file:line:` for grep hits, or the shell's diagnostic
for parse errors) and does not fail. Comment lines are skipped; a heredoc
body is not, so read each hit. The failing checks are in the server repo's
CI: `.github/workflows/lint-shell.yml` runs `.github/scripts/lint-shell.sh`
(`shellcheck -s sh`, `dash -n` and a bare `set -o pipefail` check on
non-generated `sh` scripts). G8 adds what that job does not run: `ash -n`,
the three greps, and checks on `configure` and `*.in`.

For a sed expression, compare outputs on the same input:

```sh
printf '%s\n' "$input" | sed --posix -e "$expr" > gnu.out
printf '%s\n' "$input" | busybox sed -e "$expr" > bb.out
cmp gnu.out bb.out
```

None of these tests Solaris or BSD `sh` directly. When a script is on the
`configure` path, say in the PR which shells it was run under.

## Status in this harness

These tracked scripts use a bash shebang today and are **not yet compliant**.
They are listed, not rewritten; convert one when you touch it, or move its
logic to Python:

- `tools/bench-engines.sh`, `tools/bench-release.sh`, `tools/bench-startup.sh`
- `tools/capget-fallback-smoke.sh`
- `tools/check-style`
- `tools/ci-local`
- `tools/doctor`
- `tools/gates/run-gates`
- `tools/hosts`
- `tools/mirror-check.sh`
- `tools/new-failures.sh`
- `tools/perf-bisect.sh`, `tools/perf-job.sh`
- `tools/perf-lab/cells.sh`, `tools/perf-lab/remote.sh`
- `tools/test-isolated`
- `tools/unit-build`
- `tools/unitd-ctl`
- `tools/watch-run.sh`

`tools/check-public.sh` and `tools/gates/shell_check` are the tracked scripts with a `#!/bin/sh` shebang.
