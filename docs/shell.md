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

For a sed expression, compare outputs on the same input:

```sh
printf '%s\n' "$input" | sed --posix -e "$expr" > gnu.out
printf '%s\n' "$input" | busybox sed -e "$expr" > bb.out
cmp gnu.out bb.out
```

None of these tests Solaris or BSD `sh` directly. When a script is on the
`configure` path, say in the PR which shells it was run under.

## The server gate

The server repo has a full-tree gate, `.github/scripts/lint-shell.sh`, run by
`Lint (shell)` on every pull request and on every push to master. A
`#!/bin/sh` file gets `shellcheck -s sh -S warning` and `dash -n`; a
`#!/bin/bash` file gets `shellcheck -s bash -S warning` and `bash -n`.
`configure` and the generated `*.in` files are skipped. A `#!/bin/sh` file
may not run a bare `set -o pipefail`; the guarded form is allowed.

`tools/check-style` (`make lint`) runs the gate when shellcheck and dash are
installed. The CI image is pinned by digest in the server's
`.github/workflows/lint-shell.yml`; from the server checkout:

```sh
sh .github/scripts/lint-shell.sh \
    "docker run --rm -v $PWD:/mnt -w /mnt \
     koalaman/shellcheck:v0.11.0@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d"
```

## What the lint cannot see

The gate checks syntax and the known bashisms. These failures passed it; they
come from the server's 743-748 and 756 conversions.

- **Quote a URL, not an option string.** The `unitc` control target is
  `--unix-socket <path> _` for the local socket, `docker://` and `ssh://`.
  `$UNIT_CTRL"$URI"` keeps the option words and the `_/config` URL; quoting
  the concatenation hands curl one argument and it fails with "is unknown".
  ShellCheck accepts both. A script that builds a command line needs a stub
  test that asserts the argv on every transport; `test/unitc/argv_test.sh`
  ([freeunit#763](https://github.com/freeunitorg/freeunit/pull/763)) is the
  example.
- **`producer | grep -q` under `pipefail`.** grep exits at the first match,
  the producer takes SIGPIPE, and the pipeline status is 141, so the `if`
  takes the false branch. Drop `-q`, use a here-document, or keep the data
  off the pipe.
- **`for x in $list` performs pathname expansion.** Store the list
  newline-separated and read it with `IFS= read -r`, or disable globbing.
- **`set -u` and `"$@"` with no positional parameters.** POSIX exempts `@`
  and `*`; posh 0.14.3 treats them as unset. dash, ash, bash, ksh and yash
  accept the idiom; do not add a workaround for posh unless it is a target.
- **A signal does not run the EXIT trap in dash or ash.** Trap HUP, INT,
  QUIT and TERM and exit 128 plus the signal number. After a hangup, stdout
  is gone and `echo` fails; under `set -e` that aborts the trap before the
  cleanup, so a log line must not fail.
- **dash's `echo` expands backslashes in user text.** Use `printf '%s\n'`.
- **A new `set -e` or `set -f` changes failure behaviour.** Say so in the
  commit, or the next reader tests the wrong contract.

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

`tools/check-public.sh` is the one tracked script with a `#!/bin/sh` shebang.
