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

The server's `Lint (shell)` workflow runs `.github/scripts/lint-shell.sh`
over every tracked shell script. It fails on shellcheck `warning`. The rules
and the pinned image are in the "Shell scripts" section of the server's
[CONTRIBUTING.md](https://github.com/freeunitorg/freeunit/blob/master/CONTRIBUTING.md#shell-scripts).
The script itself is the reference: it picks each file by the interpreter on
its `#!` line, also through `env`.

`tools/check-style` (`make lint`) runs the gate in the server checkout. It
needs `.github/scripts/lint-shell.sh` in that checkout, and `shellcheck`,
`dash` and `bash` on the host. It runs the local shellcheck. It reads the
version that the server pins from `.github/workflows/lint-shell.yml`, and
warns when the local shellcheck has a different version.

The workflow also runs test scripts, such as
`.github/scripts/lint-shell-test.sh` and `test/unitc/argv_test.sh`.
`tools/check-style` reads them from the workflow: each one-line `run:` that
runs one script, as `sh <path>` or `./<path>`. It runs each of them that is
in the checkout, with stdin closed. It lists, and does not run, any other
step that names a script: a `run:` with arguments or another interpreter, or
a multi-line `run:` that names a script other than `lint-shell.sh`. Run
those by hand. `argv_test.sh` needs the util-linux `script(1)`; without it,
that test is skipped.

## The harness gate

This repository's `shell` job runs `tools/check-shell.py`. It fails on
shellcheck `warning`, like the server gate; [docs/ci.md](ci.md) describes
the job. The two gates pick files and pin shellcheck differently, so a clean
run of one gate does not mean that the other gate is clean.

## What the lint cannot see

The server gate checks syntax and the known bashisms. The failures below
passed it. They come from the POSIX `sh` conversions in freeunit PRs
[743](https://github.com/freeunitorg/freeunit/pull/743) to
[748](https://github.com/freeunitorg/freeunit/pull/748) and from the signal
fix in [756](https://github.com/freeunitorg/freeunit/pull/756).

- **Quote a URL, not an option string.** For the local socket, and for the
  `docker://` and `ssh://` targets, the `unitc` control target is
  `--unix-socket <path> _`. `$UNIT_CTRL"$URI"` gives curl the option, the
  path and the `_/config` URL as separate arguments. Quoting the whole
  concatenation gives curl one argument, and curl fails with "is unknown".
  ShellCheck accepts both forms. A script that builds a command line needs a
  stub test that asserts the argv on every transport. `test/unitc/argv_test.sh`
  from [freeunit PR 763](https://github.com/freeunitorg/freeunit/pull/763) is
  the example.
- **`producer | grep -q` under `pipefail`.** grep exits at the first match,
  the producer takes SIGPIPE, and the pipeline status is 141, so the `if`
  takes the false branch. Drop `-q`, use a here-document, or keep the data
  off the pipe.
- **`for x in $list` performs pathname expansion.** Store the list
  newline-separated and read it with `IFS= read -r`, or disable globbing.
- **`set -u` and `"$@"` with no positional parameters.** POSIX exempts `@`
  and `*`; posh 0.14.3 treats them as unset. dash, ash, bash, ksh and yash
  accept the idiom; do not add a workaround for posh unless it is a target.
- **A signal does not run the EXIT trap in dash or ash.** Trap HUP, INT
  and TERM and exit 128 plus the signal number. QUIT needs a choice: bash
  ignores it in a script, so the harness scripts leave it alone (see
  "Traps found while porting"). After a hangup, stdout
  is gone and `echo` fails; under `set -e` that aborts the trap before the
  cleanup, so a log line must not fail.
- **dash's `echo` expands backslashes in user text.** Use `printf '%s\n'`.
- **A new `set -e` or `set -f` changes failure behaviour.** Say so in the
  commit message, so that the reviewer tests the new behaviour.

## Status in this harness

Every tracked shell script that runs on its own has a `#!/bin/sh` shebang and is written to run under dash,
busybox ash and `bash --posix`, with busybox applets in place of procps. The tests below run some of them; the
others are only parsed. `tools/lib/env.sh` has no shebang because it is only sourced (`. tools/lib/env.sh`) by
such scripts; it follows the same rules, but neither the parse test nor `tools/check-shell.py` picks it on
its own.

Exceptions and needs outside busybox:

- `tools/perf-lab/cells.sh` runs only inside the Debian lab image, which installs procps. It still calls
  `ps -eo pid=,ppid=`, `pgrep -x unitd` and `pkill -9 -x unitd`. Under busybox the last two would match
  nothing, because busybox compares `argv[0]` and unit rewrites it.
- `tools/bench-engines.sh` and `tools/bench-release.sh` need `taskset` and `getconf`. Neither is a busybox
  applet on Alpine: `taskset` is in util-linux-misc and `getconf` in musl-utils (Alpine 3.22). bench-engines.sh
  names a missing `taskset`, `flock`, `setsid` or `getconf` and stops.
- `tools/bench-startup.sh` uses `#!/usr/bin/env bash` by design. It times with `EPOCHREALTIME`, `/dev/tcp`
  and `read -t`, so no fork falls inside a measured interval; a fork costs about 5 ms and the script reports
  to 1 ms. Moving it to Python is the way to drop bash there.

The `shell` CI job runs shellcheck at the `warning` level (see "The harness gate"), because its portability
checks (SC3xxx) are warnings.

`test_entry_points_parse_as_posix_sh` in `tools/tests/test_cli_tools.py` parses every tracked `#!/bin/sh` script with
`sh -n`, and with dash, busybox ash and `bash --posix` when they are installed; a missing shell shows as a skipped
subtest. `UnitdCtlTest` in the same file starts and stops `tools/unitd-ctl` under each of these shells against a
fake unitd in its own process group. `BenchEnginesRunTest` runs one round of `tools/bench-engines.sh` against two
fake unitd builds, with stubs for `oha`, `ss`, `curl` and `taskset`. It runs under `sh`, dash, `bash --posix` and
busybox ash. For busybox, PATH holds only the stubs and a directory of busybox applets, so procps `ps` and `pgrep`
cannot leak in. When the busybox lacks `flock`, `comm`, `getconf`, `setsid` or `seq`, the test takes that tool from
the host. Ubuntu's busybox lacks `flock`, `comm` and `getconf`; Alpine's lacks only `getconf`, which is in
musl-utils there. Set `HARNESS_BUSYBOX` to test
another busybox binary, such as Alpine's `busybox.static`. The test checks the exit code, the result rows, and
that no fake unitd and no process group is left. A second test in the class runs the `/proc` helpers of
bench-engines.sh directly, under the same shells, against a process whose command name holds a newline and
against a fake unitd. The crash recovery paths of bench-engines.sh (an orphaned group found by its session, a
run dir found by its command line) are not run; only their helpers are. CI runs all four shells: the
ubuntu-latest image has dash and busybox. `tools/tests/test_perf_lab_scaling.py` runs the scaling kit under
`sh` (set `SCALING_SH` to pick another shell).

## Traps found while porting

- dash and busybox ash skip the EXIT trap when a signal ends the shell. Trap HUP, INT and TERM too (see
  `exit_traps` in `tools/perf-lab/scaling/lib.sh`). dash prints nothing for `$(trap)`, and `trap -p` is a fatal
  error in dash and busybox.
- `exec 9>file 2>/dev/null` with no command makes both redirections permanent: stderr is lost from then on.
  Write `{ command exec 9>file; } 2>/dev/null`. Without `command`, a failed open ends dash, busybox ash and
  `bash --posix`; only plain bash goes on.
- `${x:?msg}` exits 1 in bash but 2 in dash and ash. Where the exit code matters, test and exit explicitly.
- `shift 2` with one argument left fails, and an option loop around it never ends in bash. Check `$#` first.
- `kill -TERM -- -PGID`: the `kill` builtin of dash rejects the `--` ("Illegal number: -", exit 2) and sends no
  signal. busybox ash prints "invalid number '--'" and exits 1, but signals the group. Write `kill -TERM -PGID`
  after checking that PGID is a number.
- busybox has its own `ps`, `pgrep`, `ls`, `df`, `awk` and `grep`: no `ps -p`, no `pgrep -g`, no `grep --include`,
  no `ls --time-style`, and its `awk` may lack math functions such as `sqrt`, depending on the build (Alpine's
  has them, Ubuntu's does not). `pgrep -x` compares `argv[0]` as given, not the command name, so it misses a
  process started by its full path, such as the `cc1` that gcc starts. Some builds have no `pgrep` at all
  (Ubuntu's). The `grep` of Ubuntu's busybox stops reading a line at its first NUL, so `grep -aF TEXT
  /proc/PID/cmdline` sees only `argv[0]` there; convert the NULs first with `tr '\0' '\n'`. Prefer
  `find ... -exec grep` and read `/proc`.
- Read process ids from `/proc/PID/stat`. The command name in field 2 can hold spaces, `)` and a newline, so
  one record can take two lines. Take the fields after the last `) `: the state, then the parent (field 4),
  the process group (field 5) and the session (field 6). A zombie still has a stat file, with state `Z`.
  `proc_records` in `tools/bench-engines.sh` does this for many files at once: `cat /proc/[0-9]*/stat` skips a
  process that has exited, and one `awk` joins the lines of a record until the part after the last `) ` holds
  all its fields. Do not scan with the `read` builtin: dash and busybox ash read one byte per system call, and
  a scan of about 570 processes took 140 ms to 160 ms against about 25 ms for `cat` and `awk`. `group_pids`,
  `child_pids`, `proc_pgid`, `proc_sid` and `proc_state` in bench-engines.sh are built on it.
  `tools/unitd-ctl` still reads the first line with `read`, so a process whose command name holds a newline
  is invisible to it; unit's own processes are named `unitd`. Read the exact command name from
  `/proc/PID/comm`, as `tools/bench-release.sh` does.
- bash ignores SIGQUIT in a script; dash dies on it. Leave QUIT alone rather than trap or ignore it: an ignored
  QUIT passes to the children.
