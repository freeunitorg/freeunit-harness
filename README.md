# freeunit-harness

A contributor toolkit for [FreeUnit](https://github.com/freeunitorg/freeunit), the
community LTS fork of the Unit application server.

It clones the server source into `./src` and gives you a build, a running `unitd`,
the CI gates, benchmark tools, and written procedures for the things that go wrong.
It is for people and for coding agents. Nothing here is required to contribute to
FreeUnit. It lives outside the server repository so it can change on its own
schedule, and you can use one piece of it without the rest.

## Start

```bash
git clone https://github.com/freeunitorg/freeunit-harness.git
cd freeunit-harness
make doctor    # what this machine is missing; changes nothing
make dev       # source + configure + build + a running unitd on :8080
make stop      # stop that unitd; any unitd on :8080 breaks pytest at startup
make test      # the pytest suite against that build
make help      # every target, with its options
```

Start a coding agent in this directory, not in `./src`: the server clone has its own
git root, so an agent started there gets no harness context.

`make ci-local` runs what CI will run, before you push. Planned, not written yet:
a `make validate` target and `tools/validate`, which build, run the C tests and one
pytest group, and write one JSON verdict.

## Layout

| Path | What it is |
|------|------------|
| `Makefile` | The entry point. Every workflow is a target. |
| `AGENTS.md` | The entry point for coding agents: the traps worth knowing first, and links into `skills/` and `docs/`. `CLAUDE.md` is a symlink to it. |
| `skills/` | Verified procedures in the Agent Skills format (https://agentskills.io): a short `SKILL.md` plus `references/`. Start at [docs/skills.md](docs/skills.md). Skill names start with `freeunit-`. |
| `docs/` | How the server works, and how the harness is run. Index: [docs/README.md](docs/README.md). |
| `agents/` | Briefs for delegated work, one file per narrow job. Start at [agents/README.md](agents/README.md). |
| `ci/` | Build environments (`Dockerfile.dev`, `Dockerfile.alpine`) for `make ci-local` and `tools/test-isolated`. |
| `tools/` | Runnable tools. Each script's header comment is its documentation. |

## Tools

`tools/test-isolated` runs the pytest suite without colliding with another run. Since
FreeUnit commit `2149f582` the suite takes `--port N`, which shifts every port it uses,
so the tool passes `--port` and runs pytest directly. That needs no root, no docker and
no namespace. Pass `-s <checkout>` and either `--port N` (8080, 9080, ... 32080) or
`--port auto`, which picks the first free band and locks it. Two checkouts on one host
then run at the same time. The namespace ladder is still there, and the tool falls back
to it on its own for the two cases `--port` does not cover
(`test/test_unix_abstract.py` and `test/test_asgi_application_unix_abstract.py` bind the
abstract socket `unix:@sock` by that fixed name) and for a checkout older than
`2149f582`. The default pytest selection is the whole `test` tree, which contains those
two files, so the default still takes the ladder. Two runs against **one** checkout are
a different problem: `test/test_asgi_lifespan.py` writes cookies into
`<src>/test/python/lifespan/empty` and the Go tests share `GOPATH=<src>/build/go`. Use
two checkouts. `tools/unit-build` builds and tests
on a remote machine. `tools/check-style`, `tools/ci-local` and `tools/gates/run-gates` mirror the server's CI
gates; `tools/doctor` reports what the machine is missing. `tools/hosts` resolves a
machine role (build, alpine, lab) to the host name in your lab env file. `tools/task`
is the task ledger and `tools/inbox` the message bus that agents share
([docs/task-ledger.md](docs/task-ledger.md), [docs/agent-messages.md](docs/agent-messages.md));
`tools/hooks/` installs the Claude Code hooks that feed them (`make hooks-install`).
`tools/check-public.sh` fails when a file to be committed carries a private detail
(`make check-public`). `tools/build-pack.py` builds one text file of the router,
libunit and port IPC source for a long-context model ([docs/context-priming.md](docs/context-priming.md)).
Performance: `tools/bench-engines.sh` (A/B of two builds, noise floor first),
`tools/bench-release.sh` and `tools/bench-crossover.py` (release A/B with swapped slots),
`tools/bench-startup.sh` (startup latency), and the perf lab: `tools/perf-job.sh` runs
one job on the lab machine, `tools/perf-compare.py` writes the verdict,
`tools/perf-bisect.sh` finds the first slower commit ([docs/perf/perf-lab.md](docs/perf/perf-lab.md)).

### Code registry

`tools/unit-registry` indexes the C tree into flat TSV under `run/registry/`
(`make registry`, a few seconds, no build needed; the cross-check needs
[ast-grep](https://ast-grep.github.io/), and `REGISTRY_ARGS=--no-crosscheck` skips it)
and answers from it:
`uses <sym>` splits every reference into production, test, fuzzing, module and tool;
`callers <sym> [--transitive]`, `dead`, `abi`, `const <name>`, `includes <file>` and
`defs <sym>` do what they say. A name the registry does not know exits 2, so a typo
cannot read as "no callers". `make registry REGISTRY_REF=<ref>` indexes a git ref
without touching the checkout; it then reads no binary for the ABI half, because a
binary built from the working tree says nothing about another ref. An index whose
gates failed keeps its artifacts for inspection, but every query except `stat`
refuses to answer from it, and so does an index with a source file, a directory or an
`auto/` build script it could not read. `--build` on a directory with no artifacts
exits 2; `make registry` passes `--no-build` when `BUILD_DIR` does not exist and says so.
`make registry-verify` runs the registry's own checks: invariants that hold on any
tree, plus pinned FreeUnit facts that skip when their symbol is absent at the indexed
ref. It fails an index built without the cross-check or missing an artifact.

References resolve by name. `dead` scopes a `static` in a `.c` file to that file, but
`uses` and `callers` merge every function that shares a name, and calls through
function pointers or token pasting are not seen. Only file-scope names are symbols:
struct members, parameters and locals are not indexed, and inside its own scope a
parameter or local that shares a file-scope name is not counted as a use of it.
Where an `#if`/`#else` around a brace hides a definition, the other branch is read
too; a definition that is still hidden is listed in `disagreements.tsv` and the index
still builds. A function whose braces come from a whole-body macro is invisible to
the scanner and to ast-grep. A K&R (old-style) definition is not indexed; FreeUnit
has none. Generated headers (`build/include/nxt_auto_config.h`, `nxt_version.h`) are
not read, so `NXT_VERSION` and the `NXT_HAVE_*` values of configure are unknown names.
A constant tested in `#if`, `#elif`, `#ifdef`, `#ifndef` or `defined()` is a
use of kind `pp`: `uses`, `const` and `dead` count it, `callers` does not; a function
or object named there is not a use. The first `#ifndef` of a file followed by its
`#define` is an include guard and is not a use, so a guard reports 0 uses unless another
file tests it; a guard in any other position counts. A branch whose `#if` is constant
false is dropped; a hex or unsigned literal or `?:` makes the condition unknown, and
the branch is kept. Known limits the tree does not hit today: a definition returning a
function pointer shows a cut signature, a parameter is excluded by line, and a
block-scope `extern` reads as a new local. The module list is read from the source lists of `auto/modules/*`,
not from their comments, and a file that `auto/sources` lists is never module code.
Go code calls libunit
as `C.name` from `go/*.go`; those calls are read as module references, but nothing
else in Go is. `callers` lists a function stored in a pointer (`rp->handler = &fn;`)
under "address-taken (not a direct call)", and `--transitive` does not follow those
references; it prints how many it skipped. `abi` compares the declared exports with
the global symbols of `sbin/unitd`, `lib/libunit.a`, `lib/libnxt.a` and the module
`.so` files. Treat `dead` as a candidate list to read, not as proof.

### Local gates before a push

`tools/gates/run-gates` runs seven gates against a FreeUnit checkout. Each gate prints
`G<n>: PASS` or `G<n>: FAIL`. The exit status is not zero when a gate failed. Each gate
copies a CI job. When that CI job changes, the gate must follow.

| Gate | What it does | CI job it mirrors |
|---|---|---|
| G1 | `--hardening=strict` build with gcc and with clang | `hardening-strict` in `.github/workflows/build-test.yml` |
| G2 | a build and a pytest slice, set with `-t` | `.github/workflows/build-test.yml` |
| G3 | an ASan and UBSan build, `test_static.py`, then a scan of the logs with `.github/scripts/check-sanitizer-reports.sh` | `.github/workflows/sanitize.yml` |
| G4 | a short libFuzzer run on `fuzz_basic` | `.github/workflows/fuzzing.yml` |
| G5 | the ast-grep rule tests and the baseline check | `ast-grep` in `.github/workflows/lint-astgrep.yml` |
| G6 | the `@contract` comment check | none; this gate is local only |
| G7 | mixed atomic/plain accesses: `clang-query` over `src/*.c`, compared with a baseline | none; this gate is local only |

Pass the checkout with `-s`, the same option `tools/unit-build` takes. Without `-s` the
script uses `$UNIT_SRC`, else the harness `./src`. A tree that holds no `./configure` is
refused with exit code 2 before any gate runs.

```bash
tools/gates/run-gates -s src                  # the default set: G5, G6 and G7
tools/gates/run-gates -s /path/to/worktree -g 5
tools/gates/run-gates -g 6
```

G5 reads `tools/ast-grep/sgconfig.yml`, `tools/ast-grep/baseline.json` and
`tools/ast-grep/check_baseline.py` from the checkout, not from this harness. Those files
live in the FreeUnit repository. A checkout without `tools/ast-grep/` fails G5 with a
message that names the missing file. It does not pass the gate. G5 also needs `ast-grep`
on the PATH, in the version that `AST_GREP_VERSION` pins in `lint-astgrep.yml`, which is
0.45.3 today. A different version can change the rule snapshots and the baseline text.

G6 reads the function names in `tools/gates/contract_functions.txt`, next to the script.
For each name it checks that the comment above the definition of the function under
`src/` has an `@contract` line. **That list is empty, so G6 checks nothing today.** It
prints `no names in ...: nothing to check` and passes. Add a name when the
pre-conditions and post-conditions of a function matter enough that a reviewer must read
them in one place.

G7 needs `clang-query` and `clang`. The distro package is `clang-tools-21` on Debian and
Ubuntu, and it does not put the binary on the PATH, so G7 also looks in
`/usr/lib/llvm-*/bin` and fails rather than skips when it finds nothing. G7 copies the
checkout, configures the copy, and runs the matcher `tools/gates/atomic_query.txt` over
every `src/*.c`. It fails on a reference to a value declared with one of the atomic
typedefs in `src/nxt_atomic.h` that is not made through the `nxt_atomic_*` macros or a
`__sync_*` builtin, unless the reference is in `tools/gates/atomic_baseline.txt`. New
matches and stale entries both fail. The baseline holds only the by-design sites
(initialization stores before the object is shared, value-type uses through a private
local, a global initializer and the next-port-id reset in the child); it was generated
on freeunit master `4a0271cc5` with `661c0d5ea` (#730, #736, #742, #749 and #752 merged,
plus the `src/nxt_unit.c` `parked` and `quit_pending` fix), so a checkout without them
reports the sites they fix, which is correct until they merge. A run takes about 30
seconds, most of it the configure. `-g 5,6` leaves G7 out on a machine without
`clang-query`.

G1 to G4 build. They refuse to run unless you pass `--allow-local-build`, and the
default gate set, `5,6,7`, contains no building gate for the same reason. Two harness
rules say that build does not belong here. A build belongs on the build host, not on
this machine:
`tools/unit-build -t remote -H build --hardening tests`. And `./configure` rewrites the
root `Makefile` and `build/` of the tree it runs in, so a gate that builds would clobber
the configured state of a worktree you are editing. Use `--allow-local-build` only on a
checkout you can throw away.

### 32-bit test runs (opt-in)

`tools/unit-build --m32` builds FreeUnit with
`--cc-opt='-m32 -D_FILE_OFFSET_BITS=64' --ld-opt=-m32` and runs the C tests or the
Python suite on that build. It is off by default. Turn it on per run with `--m32`, or for
every run with `UNIT_BUILD_M32=1` in the environment or in the lab env file.

```bash
tools/unit-build -t alpine -H alpine --m32 tests     # build/tests and the hook gate
tools/unit-build -t alpine -H alpine --m32 pytest    # the Python suite
tools/unit-build -t remote -H build --m32 tests      # needs gcc-multilib on the host
```

The alpine target is the one to use. It runs in a 32-bit x86 Alpine edge container
(`--platform linux/386`, image `ALPINE_M32_IMAGE`, default `i386/alpine:edge`) with
Alpine edge apk packages only, so OpenSSL and PCRE are real 32-bit libraries and only
FreeUnit is built. The Docker host must run 386 binaries: any x86_64 host does natively,
an arm64 host only under qemu. The remote target has no 32-bit OpenSSL or PCRE, so it
builds without `--openssl` and with `--no-regex`, as the CI job does. A 32-bit run uses
its own synced tree and tmux session, with a `-m32` suffix, and never touches the 64-bit
`build/`. `--otel` and the `debug` verb are refused with `--m32`, and so is `-t local`,
which would build in place in your checkout.

FreeUnit CI only compiles this configuration, in the `build-m32` job
([freeunitorg/freeunit#725](https://github.com/freeunitorg/freeunit/pull/725), for
[#632](https://github.com/freeunitorg/freeunit/issues/632)). This option runs the tests on it.

## Lab env file

Host names never go into a tracked file. Copy the template and fill in your own hosts:

```bash
cp tools/perf-lab/lab.env.example run/lab.env   # run/ is gitignored
tools/hosts check                               # fails on an empty required role
```

The file is optional: the local build and tests work without it. A remote build host
and the perf lab need it. The `build` role never falls back to the bench host. Pass
the tree to `tools/unit-build` with `-s src`. Set `LAB_ENV=/path/to/file` to use another file. The roles are described in
[docs/host-roles.md](docs/host-roles.md).

## Behavioural model

`tools/model` is a Python model of the FreeUnit data path: router engines, the
port protocol, the shared-memory app queue, and application workers. Model A
replays requests; Model B interleaves the two message channels, injects faults,
and checks invariants. A violation is a claim about the C code, not a finding
by itself. Docs: [docs/model/](docs/model/).

```bash
python3 -m tools.model.cli simulate --requests 50 --workers 2
python3 -m tools.model.cli verify --chaos --seed 1
python3 -m pytest -q tools/model/tests
```

## Agents

`AGENTS.md` is read directly by Codex CLI, OpenCode, dsh, agy, Kilo Code, Cursor, Pi and
MiniMax Code. Claude Code reads `CLAUDE.md`, a symlink to `AGENTS.md`. After you edit `agents/` or `recipes/`, run `make agents`. The links
`.agents/skills` and `.claude/skills` point to `skills/`, so these agents find the
skills without being told. Pi asks you to trust the project first (`--approve`). Aider reads it
only when `.aider.conf.yml` lists it under `read:`. A private `AGENTS.local.md` is
gitignored. The survey with sources is in
[docs/public-harness-plan.md](docs/public-harness-plan.md) section 4.

## Rules for the content

1. The code is the territory. Every claim in `docs/` and every command in `skills/`
   is checked against the FreeUnit source. Anything unverified is labelled inline.
2. Mirror CI, do not reinvent it. `check-style` and `ci-local` follow the server's
   `.github/workflows/`.
3. Own your processes. Anything that starts a `unitd` also makes sure it stops.
4. Pin nothing silently. A tool that depends on an external version reads the pin
   from the server repository.

## Security

Security reports go to FreeUnit's
[SECURITY.md](https://github.com/freeunitorg/freeunit/blob/master/SECURITY.md), never to
this repository's issues.

## Code of conduct

This project follows FreeUnit's
[CODE_OF_CONDUCT.md](https://github.com/freeunitorg/freeunit/blob/master/CODE_OF_CONDUCT.md).

## Licence

Apache-2.0, the same as FreeUnit. See [LICENSE](LICENSE).
