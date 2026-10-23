# A native aarch64 host for build, test and benchmark

**Use this when:** you need FreeUnit built or measured on arm64 (or armv7), or a bug
reproduces on one architecture only and you want a real CPU rather than an emulator.

Configured through the `alpine` role: `run/lab.env` sets `ALPINE_HOST` (see
`tools/perf-lab/lab.env.example`), `tools/hosts alpine` resolves it,
and `tools/unit-build -t alpine -H alpine -j 2 build` is the packaged path. The host
inventory itself is private and stays in `run/servers.md`.

## Why a real host, not qemu

`qemu-user` is enough to reproduce alignment faults (see
[emulated-repro.md](emulated-repro.md)), but it is not enough to measure or to trust a
syscall path:

| what | native | qemu-user, same box |
|---|---|---|
| 64-bit LCG loop, best of 3 | armv7 148 ms, aarch64 91 ms | armv7 344 ms |
| `/proc/PID/exe` provenance | the binary | the emulator |

So: **qemu for correctness, the host for timing and for kernel-facing features** (kTLS,
`io_uring`, `sendfile`, `SO_*`). `readlink /proc/self/exe` is useless under qemu — it reports
the emulator; read `/proc/PID/exe` from the host under `doas` instead.

Do not register `qemu-aarch64` in binfmt on an aarch64 host: it would emulate the whole
box. Register only `qemu-arm`, and only for the armv7 work. (`armenv binfmt on` in the
`dsh-d12` toolkit does this; binfmt_misc is not mounted at boot.)

## Bring-up (Alpine)

```sh
doas apk add --no-cache build-base linux-headers openssl-dev pcre2-dev zlib-dev zstd-dev brotli-dev
uname -m; uname -r; nproc; free -m | head -2; df -h / | tail -1     # record the baseline
```

Record the baseline in whatever you write: arch, kernel, cores, RAM, and the compiler
version. The host is small (2 OCPU) and shared, so it should be in the report.

## Build and test Unit

```sh
git clone --depth 1 https://github.com/freeunitorg/freeunit ~/fu && cd ~/fu
./configure --tests --openssl --debug && make -j2
make -j2 build/lib/libunit.a && make -j2 tests && ./build/tests; echo "exit=$?"
```

`make build/lib/libunit.a` first is not optional: `--tests` puts the hooks only in
the `-tests` archives, and the hook gate needs the plain archive that `make tests`
does not build (see `skills/freeunit-build/SKILL.md`). `tools/unit-build -t alpine
-H alpine -j 2 tests` runs the whole sequence for you, gate included.

Expect **exit 0**; at `4fef769d` that is 85 test notices and no failures. The
`test(0) failed (12: Out of memory)` alerts are the negative-path injection inside the
`conn close idle` test, which then reports passed — do not read them as failures.

## PHP module

```sh
doas apk add --no-cache php85 php85-dev php85-embed py3-pytest
./configure php --config=php-config85 --lib-path=/usr/lib/php85 && make
```

`--lib-path` is **required**: `php-config --ldflags` does not carry the directory of
`libphp.so` on this distribution, and without it configure stops at `no PHP embed SAPI
found`. The module name is the config basename without `-config`
(`auto/modules/php`), so `php-config85` gives `build/lib/unit/modules/php85.unit.so`.

Then prove it serves, because a passing C suite says nothing about the SAPI:

```sh
./build/sbin/unitd --no-daemon --statedir /tmp/ustate --control unix:/tmp/control.sock \
  --modulesdir "$PWD/build/lib/unit/modules" --log /tmp/unit.log &
curl -s --unix-socket /tmp/control.sock -X PUT --data-binary @app.json http://localhost/config
curl -s http://127.0.0.1:8443/
```

The project's own suite runs in Docker (`./test/run-local.sh php`); it builds a native
test image, so it works here without extra setup.

## Benchmark method

What a full three-variant run looked like (`native` / `docker arm64` / `docker armv7`),
worth copying because it is what makes the numbers defensible on a 2-core box:

* **Pin the server and the load generator to different cores**:
  `taskset -c 0` for native runs, `docker --cpuset-cpus=0` for containers, `taskset -c 1`
  for the generator. On 2 cores there is nothing else to give them.
* **`wrk -t2 -c8 -d30s --latency`**, 5 s warmup per cell, **one variant at a time**
  (never two servers or two generators at once).
* **Alternate and then swap**: per URL, 8 alternating rounds of the variants, then 4 rounds
  in the reverse order — 36 cells per URL. The swap is what separates a variant effect from
  a drift in the box.
* **Report transients.** A system-wide slowdown (all three variants down together) is not a
  variant result; the run that produced the published ARM figures had one, and only the
  main-phase medians were used.
* A null/A-A cell first, exactly as `skills/freeunit-benchmark` requires — on this host the
  noise floor is larger than on the 8-core boxes.

## Gotchas that cost time

* **`oha`, `ab` and cargo are not on the host; `wrk`, `taskset`, `strace` and
  `perf` are.** Use `wrk` for load and `/proc` for counters. A probe that shells out
  to a tool must check it is there first: the kTLS probe's OpenSSL arm skips itself with a
  clear message when the `openssl` CLI is missing.
* **`tls` ships unloaded**: `doas modprobe tls`; `/proc/net/tls_stat` appears only after.
* **A fresh Alpine `php85` ships without `fileinfo intl exif sodium`** — Drupal needs
  them. Install with `doas apk add php85-fileinfo php85-intl php85-exif php85-sodium`;
  check `php -m` first, because on a host another agent prepared they are already there.
* **The ini scan directory wins over the main php.ini.** `PHP_INI_SCAN_DIR` is parsed after
  `php_ini_path_override`, so a `conf.d` file silently overrides `options.file`. On the
  shared host a file another agent wrote (`99_dsh.ini`) sets `opcache.memory_consumption`;
  an experiment that disagrees with its own ini should read both.
* **Never `pkill -f <pattern>` when the pattern appears in the command you are running**:
  it matches the invoking shell and kills your own session. Use `pgrep -x unitd` and a pid
  list, or the `[b]racket` trick only when the literal path is not also in the command.
* `docker` is capped at the host's 2 CPUs; `-j2` is the maximum useful parallelism.

## Where the logs belong

Write the environment and the method down **in the harness**, not only on the host: a
`run/agent-findings/` note (the template is in `run/agent-findings/README.md`) plus a bus
line, so the next agent does not re-derive the box from scratch. A worked example is
`run/agent-findings/dsh-20261012-drupal12-arm-bench.md`, whose on-host companion lives in
`~/dsh-d12/{ENV-RUN,RUN,REPORT}.md` on the ARM box.
