# Reproducing in an emulated container

```bash
# armv7 = linux/arm/v7, armhf = linux/arm/v6
docker run --rm --platform=linux/arm/v7 --cpus=4 --user root \
  --ulimit nofile=65536:65536 -v "$PWD":/src \
  registry.alpinelinux.org/alpine/infra/docker/alpine-gitlab-ci:latest-armv7 \
  sh /src/repro.sh
```

Inside the container, per run (it is `--rm`, so nothing persists):

```bash
apk add --no-cache build-base clang22 openssl-dev pcre2-dev linux-headers \
    python3 py3-pytest procps-ng binutils
./configure --cc=clang-22 --openssl --tests --cc-opt='-g'
make -j4                 # ~20-25 min under emulation
make tests               # plain `make` does not build ./build/tests
```

`pcre2-dev` is not optional — `./configure` fails without it. Match `--cpus=4`
and `make -j4`; emulation is slow enough that the build dominates every
iteration, so get it right the first time.

## The pytest teardown errors, and it is not your bug

`conftest.py` classifies processes by the `unit: …` titles that
`nxt_process_title()` installs (`src/nxt_process.c:647`), filtering with
`'unit: main' not in l` and asserting the remainder is 2
(`test/conftest.py:760-763`). Under `qemu-user` the visible command line is the
emulator's (`/usr/bin/qemu-arm .../unitd …`), so the title filter matches
nothing and every test errors at teardown with `assert 3 == 2, 'one main'`.

A run can be `19 passed, 19 errors` and be entirely healthy. Judge emulated runs
by the passes and by your own crash counter, not by the teardown errors.

## Alpine's 32-bit ARM builders have no kernel fixup path

Alpine's armv7/armhf builders are native AArch32-on-aarch64, and the arm64
kernels do not enable `COMPAT_ALIGNMENT_FIXUPS`. Nothing will fix up a
misaligned access, and NEON is never fixable regardless. An alignment bug there
is a hard crash, not a slow path.

## A `--platform` pull — or run — repoints the shared tag for everyone on the box

A pinned `docker run --platform` implicitly pulls that platform's manifest and repoints the tag
too (measured on Docker 29.7.2 on 2026-08-28 after an earlier "only pull does" claim failed on a
second host). Treat every `--platform` operation as able to repoint; pull by digest or re-tag per arch.

`--platform` is not a per-invocation flag on the pull side: it changes what
the *tag* resolves to for every later command, including everyone else's.
Measured on this workstation (Docker 29.7.2, x86_64, 2026-08-28) with a
throwaway tag:

```
$ docker pull --platform linux/s390x alpine:3.21
$ docker image inspect alpine:3.21 --format '{{.Architecture}}'
s390x
$ docker run --rm alpine:3.21 uname -m          # no --platform at all
WARNING: The requested image's platform (linux/s390x) does not match the
detected host platform (linux/amd64/v3) and no specific platform was requested
exec /bin/uname: exec format error
```

On a shared build box that breaks other people's builds — an image `pull`ed
for a five-minute architecture check silently repoints a base image half the
containers on the host are built from, and the breakage surfaces in someone
else's build, not in yours.

**A `--platform` *run* does it too** — it implicitly pulls that platform's
manifest and moves the tag with it. Measured here twice, including on a
second run after the foreign variant was already in the local store: the tag
read `s390x` again afterwards. Do not assume the damage is confined to
explicit `pull` commands.

**How to check, and how to undo it.** `docker image inspect <tag> --format
'{{.Architecture}}'`, or `docker run --rm <tag> uname -m`. Not `docker info`
— that reports the daemon and the host, and says nothing about what a tag
resolves to. A plain `docker pull <tag>` (no `--platform`) restores the host
variant; both directions were measured.

**The habit that avoids it: pull the foreign variant by digest,** which never
touches the tag (measured: tag still `amd64` after
`docker pull alpine@sha256:9333f5f4…`, the s390x manifest of the same tag):

```bash
D=$(docker manifest inspect alpine:3.21 \
    | jq -r '.manifests[] | select(.platform.architecture=="s390x") | .digest')
docker run --rm "alpine@$D" uname -m
```

Or re-tag the foreign image under an arch-suffixed name of your own
immediately after pulling, and never leave a shared tag pointing at it.

**How loudly this fails depends on the box.** With no `qemu-user` binfmt
handlers registered (this workstation has none — check
`/proc/sys/fs/binfmt_misc/`), a poisoned tag dies immediately with `exec
format error`, which at least points at the problem. On a box *with* binfmt
registered — any box set up for the emulated reproduction above — the
container runs, correctly, under emulation, an order of magnitude slower, and
nothing in the output says the architecture is wrong.
