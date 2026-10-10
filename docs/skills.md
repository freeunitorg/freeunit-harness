# Skills

The skills live in `skills/`, one directory per procedure. This index sits in
`docs/` because some agents read every `.md` file at the top of `skills/` as a
skill. Each directory name equals the skill's `name` and starts with `freeunit-`,
so the skills do not collide with other skill sets of the same name.

Read a skill's `SKILL.md` first; it is the short version. Each one links into its own `references/` for the long detail; open those only
when the `SKILL.md` sends you there.

| Skill | Read it when |
|---|---|
| [`freeunit-build`](../skills/freeunit-build/SKILL.md) | you need a `unitd` binary, a language module, or different build options |
| [`freeunit-test`](../skills/freeunit-test/SKILL.md) | you changed something, or you are writing a regression test |
| [`freeunit-benchmark`](../skills/freeunit-benchmark/SKILL.md) | you want to claim something got faster or slower |
| [`freeunit-debug`](../skills/freeunit-debug/SKILL.md) | a crash, a hang, a leak, or a test failure the log does not explain |
| [`freeunit-cross-arch`](../skills/freeunit-cross-arch/SKILL.md) | it only breaks on one architecture, or an arch is allow-failed downstream |
| [`freeunit-security`](../skills/freeunit-security/SKILL.md) | auditing a path, hardening one, or you think you found something |
| [`freeunit-release`](../skills/freeunit-release/SKILL.md) | cutting a release, or preparing a change for one |
| [`freeunit-review`](../skills/freeunit-review/SKILL.md) | opening a pull request, or reviewing one |
| [`freeunit-verify`](../skills/freeunit-verify/SKILL.md) | a check just went green and you're about to trust it |

Handing one of these procedures to another agent instead of running it
yourself? [`agents/README.md`](../agents/README.md) has the brief shapes —
what to say, what to demand back, and how each role has already gone wrong here.

The pitfalls are the point: most of them cost someone a day and none are
discoverable from the code you are looking at when they bite. If you hit one
that belongs here, add it — verified, with the file and symbol that proves it.
