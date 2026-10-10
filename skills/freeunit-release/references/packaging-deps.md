# Distro packaging dependencies

Verified 2026-08-28 on the build host, distro-validating `master` at `e21bc5bc` plus
PR #226, in pinned `--platform linux/amd64` containers (Alpine and Debian).
Evidence: the `validate-226` run directory on the build host. **The build host's
root filesystem fills up during container builds — `df` before starting
another one, not after it fails.**

## `--otel` needs a newer rustc than most distros ship

**The constraint, stated so it survives version churn:** `--otel` compiles
`src/otel`, whose MSRV floor is the highest `rust-version` among its *locked*
dependencies — today that is `tonic` (pulled in transitively via `tonic-prost`
and `tonic-types`). Never quote a remembered number; read it from the lock:

```sh
cargo metadata --format-version 1 --locked --manifest-path src/otel/Cargo.toml \
  | python3 -c "import json,sys; p=[x for x in json.load(sys.stdin)['packages'] if x.get('rust_version')]; \
p.sort(key=lambda x:[int(n) for n in x['rust_version'].split('.')]); \
print('\n'.join(f\"{x['name']} {x['version']} -> {x['rust_version']}\" for x in p[-5:]))"
```

**The floor is per-crate, and `src/otel` is not the highest one.** Measured
2026-08-29 with the command above: `src/otel` = **1.88** (`tonic` 0.14.6, 134
locked crates), `tools/unitctl` = **1.95** (`sysinfo` 0.39.6, 254 locked
crates). They diverge because nothing builds both — the packaged daemon
(Docker images, `.deb`) never compiles `unitctl`, which is built only by
`.github/workflows/unitctl.yml` on floating `rustup update stable`. So a
toolchain pin chosen for the daemon says nothing about whether `unitctl`
still compiles; check each crate separately.

When the toolchain is below that floor, cargo says so precisely — this is what
a too-old distro rustc looks like, it is *not* a musl/Alpine or glibc problem:

```
rustc 1.87.0 is not supported by the following packages:
  tonic@0.14.6 requires rustc 1.88
```

Two ways to fix it, in order of preference:

1. Install a current toolchain via `rustup` in the build environment instead
   of relying on the distro's `rustc` package. This is what the repo already
   does for `.deb`s (`.github/workflows/build-deb.yml` and
   `pkg/deb/build-local.sh` both pin `RUST_TOOLCHAIN`).
2. `cargo update tonic --precise <an older, compatible version>` — changes
   `Cargo.lock`, needs review before landing.

### What each distro actually ships (checked 2026-08-29 — re-check, don't trust)

| Toolchain source | rustc | Clears a 1.88 floor? |
|---|---|---|
| Rust upstream stable | 1.98.0 (2026-08-20; next 1.99.0 on 2026-10-01) | yes |
| Alpine 3.22 (`main/rust`) | 1.87.0-r1 | **no** |
| Alpine 3.24 (`main/rust`) | 1.96.1-r0 | yes |
| Alpine edge (`main/rust`) | 1.97.0-r0 | yes |
| Debian bookworm (`rustc`) | 1.63.0+dfsg1-2 | **no** |
| Debian trixie (`rustc`) | 1.85.0+dfsg3-1 | **no** |
| Debian bookworm-backports | 1.94.1+dfsg1-1~bpo13+3 | yes |
| Debian sid/testing | 1.95.0+dfsg1-2 | yes |
| Ubuntu 24.04 noble (`rustc`) | 1.75.0+dfsg0ubuntu1-0ubuntu7.4 | **no** |

Sources: <https://pkgs.alpinelinux.org/packages?name=rust>,
<https://packages.debian.org/{bookworm,trixie}/rustc>,
<https://tracker.debian.org/pkg/rustc>, <https://releases.rs/>.

**Rust has no LTS and no EOL in the distro sense**: a new stable ships every
six weeks and only the newest stable is supported upstream — there are no
maintained older branches and no backports. (`endoflife.date/api/rust.json`
sets each cycle's `eol` to the *next* release's date, i.e. "superseded", not
"security-dead".) So there is nothing to add to `EOL.md`'s OS/runtime
matrices for Rust itself; the only durable fact is the MSRV floor, which is a
property of `Cargo.lock`, not of a calendar.

**Alpine ≥ 3.24 needs no rustup (verified 2026-08-28 on the build host, pinned
linux/amd64, `alpine:3.24` = 3.24.1):** distro `rust`/`cargo` are 1.96.1-r0,
above the floor; `./configure --openssl --otel && make` on master `defd7e26`
builds clean. So for Alpine the fix is "build on ≥ 3.24", not a `Cargo.lock`
change. Runtime deps on 3.24: libssl3, libcrypto3 (3.5.8), pcre2 (10.47),
libgcc (15.2), musl — same shape as 3.22 plus nothing new.

**Debian is not exempt.** Both bookworm (1.63) and trixie (1.85) ship a rustc
*below* the floor, so a Debian `--otel` build only succeeds because the repo
installs a pinned rustup toolchain and puts it ahead of the apt one on `PATH`.
Do not read a green `.deb` build as "Debian's rustc is new enough".

### On each new Rust stable release, re-run this inventory

```sh
rg -n -i 'rustc|rust 1\.|1\.[89][0-9]|rustup|tonic' "$HARNESS_ROOT" --glob '!.git' -l
rg -n 'rust-version|MSRV|rustc [0-9]|RUST_VERSION|RUST_TOOLCHAIN' \
   "$UNIT_SRC"/{src/otel,tools/unitctl,pkg,.github,auto,EOL.md,docs} --glob '!*.lock'
```

Then refresh the table above and confirm the repo's pins (Docker
`RUST_VERSION`, `build-deb.yml` `RUST_TOOLCHAIN`) are still ≥ the lock's floor
and ≤ what CI can install.

## Runtime package dependencies

| Distro | Always needed | New with `--otel` |
|---|---|---|
| Debian bookworm | `libssl3`, `libpcre2-8-0` | — |
| Alpine 3.22 / 3.24 | `pcre2`, `libgcc` | `libgcc` |

`libgcc` is not a general Alpine runtime dependency for this daemon — it is
pulled in specifically by an `--otel` build. The Rust staticlib
(`src/otel/`) references `_Unwind_*` symbols, and without `libgcc` present at
runtime that surfaces as 20+ dynamic-linker relocation errors, not a clean
"library not found."

## The otel exporter has no TLS — the collector endpoint must be plain `http://`

`src/otel/Cargo.toml` links no TLS stack at all, by design (comment in that
file, verified by reading it directly):

> No TLS stack is linked (reqwest 0.13 dropped the ring rustls provider;
> rustls would pull aws-lc-sys and native-tls would link a second OpenSSL
> into this staticlib), so both transports are plaintext — the endpoint must
> be plain `http://` (h2c for grpc).

This is true for both the HTTP and gRPC OTLP transports — there is no build
flag that adds TLS back. Document any `--otel` telemetry endpoint as
`http://`, never `https://`; a TLS collector URL will not work regardless of
distro.

## A new Rust stable cannot break this repo through lints — and that is load-bearing

Verified 2026-08-29 by grepping every `.rs`, `.toml`, `.yml` and `.sh` in the
repo: there is **no `#![deny(warnings)]`, no `#![forbid]`, no `-D warnings`, no
`RUSTFLAGS` lint setting, and no `cargo clippy` step in any workflow.** The only
attribute of that family is an `#![allow(...)]` in `unit-openapi/src/lib.rs`.

This is the reason a Rust stable bump is a non-event here, and it is worth
stating explicitly at tag time rather than re-deriving it: clippy gains lints
every release, and in a repo with a lint gate that is exactly how a new compiler
breaks a green build. Here the new lints fire into nothing.

Two consequences, in opposite directions:

- **For a release decision:** "new Rust stable is out" does not, by itself,
  threaten the tag. Check that the crates *compile* on it; do not spend time
  hunting for lint regressions that have nowhere to land.
- **For the backlog:** the debt accrues invisibly. Running `cargo clippy
  --all-targets -- -D warnings` on 1.98 fires 11 lints in `src/otel`, 2 in
  `test/fake_otlp`, 3 in `pkg/eol` — and **none of them are new in 1.98**; each
  was checked against clippy's changelog and has existed for several releases.
  Nine of the eleven are `missing_safety_doc` on otel's `pub unsafe extern "C"`
  FFI surface. So whenever clippy is switched on in CI, it has to land with the
  backlog fixed or explicitly allowed *in the same change*, or the first run can
  never be green.

Do not report a clippy failure on a new toolchain as a regression without
checking the lint's introduction version first. The default assumption should be
"pre-existing debt, newly measured", because that is what it has been every time
so far.

## What floats on `stable`, and therefore moved by itself

Most Rust in this repo is pinned; these are the places that are not, and that
silently pick up each new stable. Re-read this list on every Rust release —
file paths are stable, line numbers are not.

| Floats | Where | What it builds |
|---|---|---|
| `channel = "stable"` | `test/fake_otlp/rust-toolchain.toml`, `test/fake_upstream/rust-toolchain.toml` | test harness binaries, built by the `unit`/`python` matrix legs |
| `rustup update stable` | `.github/workflows/unitctl.yml` (two jobs: test, and the release build) | the shipped `unitctl` binaries |
| `rustup toolchain install stable` | `.github/workflows/build-test.yml` (unit/python legs) | fake_otlp / fake_upstream |
| bare `curl sh.rustup.rs \| sh -s -- -y`, no `--default-toolchain` | `build-test.yml`, wasm-wasi-component leg | the wasm component |
| ambient runner toolchain, no install step at all | `build-test.yml` main `--otel` build; `eol-check.yml` | **the otel crate on every matrix leg** — the primary CI signal |
| `FROM rust:slim-trixie` | `tools/unitctl/Dockerfile` | not wired into CI; a local packaging landmine |

The last row of the pinned side is worth knowing too: the `.deb` builds pin
`RUST_TOOLCHAIN` at exactly `1.88.0`, which **is** otel's floor — zero headroom.
Any dependency bump that moves the floor breaks `.deb` CI with no earlier
warning. Raising that pin to match the Docker `RUST_VERSION` is cheap insurance.

## Tagging a release compiles unitctl with whatever stable ships that day

`.github/workflows/unitctl.yml` triggers on `push:` `tags: '[0-9]+.[0-9]+.[0-9]+'`,
and its toolchain step is `rustup update stable`. So **the act of pushing the
release tag builds and publishes the release `unitctl` binaries on the current
stable compiler**, whatever that happens to be that morning — not on any pinned
version, and not on anything CI exercised beforehand.

This is benign as long as stable ≥ the unitctl workspace's floor, which it has
always been. It is listed here because it is the one place the tag itself pulls
in a new compiler, so a Rust release landing shortly before a tag deserves one
compile check of that workspace before pushing the tag, not after.

## No crate declares `rust-version`, so no floor is enforced

All nine `Cargo.toml` files in the repo declare `edition` and none declare
`rust-version` (`tools/unitctl/unit-openapi` is edition 2018; every other crate
is 2021). Every MSRV floor is therefore a derived property of `Cargo.lock` —
discoverable only by the `cargo metadata` walk above — and nothing fails loudly
when one moves. Adding `rust-version` to `src/otel` and to the `tools/unitctl`
workspace would make cargo itself refuse a too-old toolchain with a clear
message instead of a confusing mid-dependency compile error.

Related: no build anywhere — CI, Docker, or packaging — passes `--locked` or
`--frozen`. The checked-in lockfiles do pin the graph in practice, but nothing
enforces it, so a `Cargo.toml` edit unmatched by a lock update will re-resolve
freely against the ambient toolchain.
