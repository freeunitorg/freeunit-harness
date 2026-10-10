# Sanitizer builds

```bash
cd src
ASAN_OPTIONS=detect_leaks=0 ./configure --debug --openssl --tests \
    --cc-opt="-fsanitize=address,undefined -fno-omit-frame-pointer -O1" \
    --ld-opt="-fsanitize=address,undefined"
ASAN_OPTIONS=detect_leaks=0 ./configure php
make -j$(nproc) unitd && make -j$(nproc) php

ASAN_OPTIONS=log_path=/tmp/asan/asan \
UBSAN_OPTIONS=print_stacktrace=1:log_path=/tmp/asan/ubsan \
    python3 -m pytest --print-log test/test_php_application.py
```

Worth running against any change to parsing, buffer handling or the port/IPC
layer. It earns its keep: the first run of this gate caught a controller crash
every other CI leg missed — a debug-build use-after-remove that release builds
survived only by accident.

## `detect_leaks=0` belongs on the `./configure` steps, not just the test run

`auto/feature` compiles **and runs** its probes, and some of them leak by design
— `auto/malloc`'s `malloc_usable_size()` probe mallocs and returns. With leak
detection on, the probe exits non-zero, configure records the feature as "found
but is not working", and silently drops it. The sanitized build then configures
*different code paths* than a normal build, which is the worst possible outcome:
you debug a binary that is not the one you shipped.

## UBSan needs its own `log_path`

`ASAN_OPTIONS=log_path=` does not capture UBSan-only reports. A UBSan
`runtime error` that does not escalate to a fatal signal lands nowhere the test
harness looks: `test/unit/log.py::check_alerts` greps `unit.log` for
`Sanitizer`, not for `runtime error`. Set `UBSAN_OPTIONS=...:log_path=` too, and
read the files afterwards — a green pytest run does not mean no reports, because
a crashing forked child does not reliably fail the suite.

## Flags must ride `--cc-opt`/`--ld-opt`, never `CFLAGS`

Only the recorded flags propagate to a module configured afterwards (see the
[build skill](../../freeunit-build/SKILL.md)). Without that you get an ASan interceptor
or ODR mismatch between `unitd` and the module, which presents as nonsense
stacks.
