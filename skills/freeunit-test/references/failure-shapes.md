# Test failures that are not what they look like

## The suite leaks daemons when it dies abnormally

`unit_stop()` escalates to SIGKILL on the *main pid only*, after a fixed grace
period; router, controller and application workers re-parent to init and
survive. If pytest itself dies hard, `pytest_sessionfinish` never runs and the
whole tree is orphaned.

Symptoms: the next run fails with a busy port, or a build machine slowly fills
with `unitd` processes. Run `pgrep -a unitd` before blaming your change — and
before every reproduction attempt while debugging, since a survivor holding the
port produces failures that look exactly like the bug you are chasing.

The long-standing trigger was the 32-bit ARM `SIGBUS` fixed in `c9a7f48d`, but
the orphaning is a property of the teardown, not of that crash. Any hard death
still reproduces it; do not treat the fix as having closed this.

## A header that vanishes only on CI is probably not a race

`nxt_list_add()` returns non-zeroed pool memory, and the field setters do not
initialize `skip`/`hopbyhop`. The serializers test those bits, so garbage drops
the field in a way that depends on allocation history — which is why it shows up
on CI and not locally, and why it reads as a race for days.

Check the append site for `nxt_list_add(` versus the canonical
`nxt_list_zero_add(` (`src/nxt_list.h`) before going near the event loop.

## To reproduce a CI-only flake locally, slow the box down

`taskset -c 0` around pytest emulates a loaded single-core runner and surfaces
ordering bugs that never appear on an idle multi-core machine.

## Duplicate response headers parse as a list

`test/unit/http.py` collects repeated headers, so assert
`resp['headers']['X'] == ['a', 'b']` — not a string.
