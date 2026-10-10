# Suite-specific pytest options

Registered in `test/conftest.py::pytest_addoption`:

| Option | Effect |
|---|---|
| `--print-log` | dump `unit.log` when a test fails — turn this on |
| `--save-log` | keep `unit.log` after the run |
| `--detailed` | verbose per-test output |
| `--restart` | restart Unit after every test |
| `--unsafe` | include tests marked unsafe |
| `--user` | user for non-privileged processes |
| `--fds-threshold N` | fail if more than N descriptors are open |

There is no `--port` and no way to point the suite at a binary outside the
source tree; see the [build skill](../../freeunit-build/SKILL.md) for both.

`--restart` has a per-file interaction that decides which CI leg may pass it —
see [`../SKILL.md`](../SKILL.md).
