---
name: freeunit-security
description: >-
  Audit and harden FreeUnit code paths, and report vulnerabilities privately. Use when you are auditing a code path, hardening one, or you think you have found a vulnerability.
---

# Skill: security

**Trigger:** you are auditing a code path, hardening one, or you think you have
found a vulnerability.

## If you think you found a vulnerability

**Do not open a public issue or PR.** Report it privately through the FreeUnit
repository's security advisory process
(<https://github.com/freeunitorg/freeunit/security/advisories>).

Writing, bundling and filing an advisory, field by field: [references/ghsa-advisory.md](references/ghsa-advisory.md).

A public report starts a clock nobody chose — and that includes a "just a
hardening cleanup" PR whose diff makes the bug obvious, and a regression test
that encodes a working exploit. Judge a change by what its diff reveals, not by
the words in its title. Do not publish reproducers for unfixed issues, including
in a fork, a gist, or a branch you believe is obscure.

**"It needs an unusual configuration" is not an answer to whether it is
exploitable** — it is one input to severity. Report it and let the assessment
happen. Which issues get an advisory, a quiet hardening commit, or a backport is
a maintainer call that depends on reachability in shipped configurations and on
downstream packaging, neither of which is visible from the diff; the
[release skill](../freeunit-release/SKILL.md) describes the resulting shape.

## What matters

**The fuzz harnesses in `fuzzing/` are not built by PR CI.** Build them
explicitly whenever you touch parsing code — see the
[build skill](../freeunit-build/SKILL.md).

**Run the ASan+UBSan gate** against any change to parsing, buffer handling or
the port/IPC layer:
[`freeunit-debug/references/sanitizers.md`](../freeunit-debug/references/sanitizers.md).

**Review the isolation surface against a hostile configuration.** Namespaces,
cgroups, rootfs, `openat2` and capabilities are all configuration-driven, so the
documented configuration is not the threat model.

**Recurring shapes in a parser change**, worth checking by name:

- length arithmetic that can wrap, or that trusts a client-supplied length;
- a field copied into a fixed buffer without the terminator budgeted;
- CR/LF/NUL reaching a header value or a log line (header injection, log
  forging);
- an offset validated against the wrong length (mapped size versus claimed
  size);
- state that survives a connection reset into the next request on a keepalive
  connection.
