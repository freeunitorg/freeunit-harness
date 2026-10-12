---
name: freeunit-cross-arch
description: >-
  Reproduce and debug FreeUnit bugs specific to one architecture. Use for a bug that reproduces on one architecture and not others, most often a SIGBUS on 32-bit ARM, or a downstream package with an architecture marked "allow fail".
---

# Skill: cross-arch

**Trigger:** a bug that reproduces on one architecture and not others — most
often a `SIGBUS` on 32-bit ARM — or a downstream package with an architecture
marked "allow fail".

Citations here were verified against `freeunitorg/freeunit` `master` at
`c9a7f48d`.

## What matters

**You do not need the hardware.** `qemu-user` since 6.0 enforces the ARM
alignment checks that matter, so a container reproduction of an alignment fault
is meaningful rather than a curiosity. Always pin `--platform`: on a multi-arch
host an unpinned pull resolves to the host architecture and you reproduce
nothing. **But a pinned pull — and a pinned run, which implicitly pulls that platform's manifest (measured on Docker 29.7.2) — repoints the tag for the whole box** — after
`docker pull --platform linux/s390x alpine:3.24`, every later *unqualified*
`docker run alpine:3.24` on that host runs s390x, which breaks other people's
builds on a shared machine (a pinned `run` does the same, it pulls). Fetch the
foreign variant by digest instead, and check with `docker image inspect <tag>
--format '{{.Architecture}}'` — never `docker info`, which describes the host,
not the tag. See [`references/emulated-repro.md`](references/emulated-repro.md).

**Do not infer which struct faults from its layout — capture the PC.** A
confident reading of struct sizes and alignments produces a plausible candidate
that can be the wrong one. In the `nxt_mp_get` alignment bug (fixed in
`c9a7f48d`) the predicted victim was an `nxt_file_t` reached through a list; the
actual faults were a 12-byte `nxt_http_static_mtype_t` and a 20-byte
`nxt_tstr_t`, both direct allocations. The mechanism was right and the victim
was wrong — exactly the shape of error a capture settles and an argument does
not.

**ARMv7 `ldrd`/`strd`/`ldm`/`stm` require only *word* alignment.** A 4-mod-8
address does not fault with `SCTLR.A=0`; only sub-word misalignment does. This
rules *in* allocator drift reaching odd or 2-mod-4 addresses and rules *out* a
whole class of alarming-but-harmless "8-aligned struct at +4" reasoning. Details
and the measurement in [`references/arm-alignment.md`](references/arm-alignment.md).

**An architecture marked "allow fail" downstream is an open bug, not a
setting.** FreeUnit's Alpine package carried `_allow_fail=yes # segfault` for
`armhf|armv7` from the day its test suite was enabled — the suite had never once
passed there, and the marker is why nobody noticed for years. When you see one,
ask what it is hiding. Removing it is the last step of the fix and belongs in
the same change as the proof that the suite passes with it gone; a green run
with the escape hatch still in place proves nothing.

**Alignment bugs are latent everywhere and fatal in one place.** The
`nxt_mp_get` defect was introduced in 2017 and is undefined behaviour on every
architecture; it only ever crashed on 32-bit ARM. Treat a one-architecture crash
as a whole-tree correctness bug that one architecture happens to detect, and fix
it in the shared code — a regression test asserting the invariant directly then
runs everywhere, and no one needs the hardware to catch it again.

## References

- [`references/emulated-repro.md`](references/emulated-repro.md) — the container
  invocation, the build inside it, and why the pytest teardown errors there.
- [`references/aarch64-host.md`](references/aarch64-host.md) — using a native aarch64
  host (the `alpine` role) for build, test and benchmark: the PHP `--lib-path`,
  `modprobe tls`, ini-scan-dir and 2-core gotchas, and the three-variant load method.
- [`references/arm-alignment.md`](references/arm-alignment.md) — capturing a
  faulting PC without a usable core, and the measured ARMv7 alignment rules.

## s390x is a real target — and it is big-endian with `--otel`

The Alpine package `community/freeunit` is `arch="all"` and its `build()` disables otel only for
`arm*|ppc64le|x86`, so **s390x builds and ships with `--otel`** — the only big-endian platform where
the `nxt_otel_attr_t` FFI struct exists at runtime. This repo's CI builds amd64+arm64 only, so any
change to that struct has an uncovered big-endian consumer.

Measured 2026-08-28 (PR #227): offsets 0/4/8/16 on all of x86-64 / armv7 / i686 / s390x; sizeof
32/24/24/32; alignof 8/8/4/8. C side via `clang -target <triple> -Xclang -fdump-record-layouts`,
Rust side via `rustup target add s390x-unknown-linux-gnu` + compile-time `const assert!` on the
`#[repr(C)]` mirror. Run a **negative control** first (assert a wrong value, confirm the build
fails) or the check can pass silently. Endianness is orthogonal to layout; it only matters where
memory is reinterpreted as bytes — audited: no punning in `nxt_otel.c`, hex round-trip is
value-based, prost varints are arithmetic — so OTLP bytes are identical on BE and LE.

Do not grep the `APKBUILD` at the root of the unit checkout for arch support: it is `pkgname=uv`,
an unrelated file. The FreeUnit APKBUILD lives in aports.
