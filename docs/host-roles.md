# Host roles — which machine does which job

A contributor's kit is a laptop, one or two build boxes, maybe a musl builder, maybe a
box with a GPU. Tools that hardcode one person's host name work for exactly one person,
and the failure is quiet: a run meant for the build box happens on the laptop and
measures the laptop.

Roles fix that. They are names for machines, and the values live in the one settings file
the configuration convention already chose: `run/lab.env` (gitignored; template
[tools/perf-lab/lab.env.example](../tools/perf-lab/lab.env.example)). `tools/hosts`
resolves them, and `tools/unit-build -H` accepts a role or a literal ssh name.

```console
$ tools/hosts list
build    build-box                ok
alpine   docker-box               ok
lab      build-box                ok
infer    <unset>                  unset (optional)
$ tools/hosts check
note    infer unset (optional)
note    build-box used by more than one role
ok      host roles consistent ($HARNESS_ROOT/run/lab.env)
```

## The roles

| Role | Variable in `run/lab.env` | Falls back to | Consumed by |
|---|---|---|---|
| `build` | `BUILD_HOST` | none: unset is an error, so a build never lands on the bench box | `tools/unit-build -t remote -H build` |
| `alpine` | `ALPINE_HOST` | `LAB_DOCKER_HOST` | `tools/unit-build -t alpine -H alpine` |
| `lab` | `PERF_HOST` | — | `tools/perf-job.sh` |
| `infer` | `INFER_HOST` | — | unset by default; see [model-routing.md](model-routing.md) |
| `bot` | `BOT_HOST` | — | unset by default |

Paths follow the same file: `REMOTE_WORKDIR` for the remote tree, `BUILD_ROOT` and
`BUILD_CACHE` when a build box differs. ssh port and key belong in `~/.ssh/config` under
the alias, never here.

## Setting it up

```bash
cp tools/perf-lab/lab.env.example run/lab.env   # then edit
tools/hosts check                               # fails loudly on an empty role
tools/unit-build -t remote -H build --debug tests
```

Literal ssh names keep working, so an existing script needs no change:

```bash
tools/unit-build -t remote -H build-box --debug tests
```

`LAB_ENV=/path/to/file tools/hosts list` reads a different file, which is how a second
checkout or a CI runner points at its own machines.

## Rules

- **Roles are local.** `run/` is gitignored and no tool prints a value from `run/lab.env`
  into a model prompt. Private host notes belong in `CLAUDE.local.md`, `AGENTS.local.md`,
  or `run/`, never in a tracked file.
- **One file, not two.** There is deliberately no `hosts.env`. `PERF_HOST` is the lab's
  name for the same box `BUILD_HOST` names; two files holding that fact is how they drift
  apart. `tools/lib/env.sh` is the intended single loader.
- **An empty required role is a problem, not a default.** `tools/hosts check` exits 1 when
  `build` or `lab` has no host, so nobody silently builds on the laptop. `alpine` and
  `infer` are optional and only produce a note.
- **One box may hold several roles.** A duplicate ssh target is a note, not an error.

## Still to do

`tools/perf-job.sh`, `tools/unit-build` and `tools/test-isolated` each read their settings
their own way today, so a fact can still live in two places until `tools/lib/env.sh`
exists. That loader is the next step, not a change to make in three tools at once.
