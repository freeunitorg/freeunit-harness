FreeUnit review primer (read before every review)

Repo: freeunitorg/freeunit, C web app server forked from NGINX Unit.
Current milestone: 1.37.1, due 2026-10-30. Perf work is deferred to 1.38.

Layout
- src/            core (router, controller, http, tls, regex) and language
                  modules (src/python, src/java, src/nodejs, src/wasm*, ...)
- src/test/       C unit tests, built by --tests into build/tests
- test/           pytest suite (test_*.py, conftest.py, unit/ helpers)
- auto/           configure scripts (auto/options, auto/cc, auto/os, auto/make)
- .github/        CI workflows and scripts (scripts/test-matrix.sh)
- docs/           changes.xml, man pages, feature notes (pcre2-jit.md, ...)
- pkg/            deb, rpm, docker, npm packaging; eol.json = runtime EOL data
- CHANGES         user-visible changes; must match docs/changes.xml

C style (nginx style; match surrounding code)
- 4 spaces, no tabs, 80 columns, /* */ comments only.
- Declarations at block top, aligned; one blank line between groups.
- Unlikely branches use nxt_slow_path(); likely use nxt_fast_path().
- Error cleanup via goto fail; return NXT_OK / NXT_ERROR / NXT_DECLINED.
- Use nxt_sprintf, nxt_memcpy, nxt_str_t, nxt_mp_* pools, nxt_log_* —
  not libc printf/malloc in core code.
- Watch integer widths: nxt_conf_map types write their own size (32-bit bug
  class from #581); check 32-bit, big-endian (s390x), musl and weak-memory
  (riscv64, loongarch64, ppc64le) behaviour.

Project rules
- Bug fixes need a regression test that fails before and passes after.
  Prefer proving it with a revert (mutation) run.
- New configure options, config keys and behaviours need docs:
  auto/options --help, docs/, test/README.md, CHANGES + docs/changes.xml.
- --regex-jit is opt-in (PCRE2 JIT, from #551); without it the
  interpreter is used. JIT failure must fall back, never fail the config.
- Reproducible builds (#626): honour SOURCE_DATE_EPOCH (abuild sets it);
  no build paths, host kernel, dates or uid in binaries, archives, jars.
- Alpine packaging links only system packages (pcre2, openssl, njs,
  brotli, zlib, zstd); only unit itself is built from source.
- Commit trailers: non-Claude models get name only, no email.

Known history (do not re-raise fixed items; do check regressions)
- #551 PCRE2 JIT (merged): fallback to interpreter on JIT compile error;
  one retry with PCRE2_NO_JIT on JIT_STACKLIMIT; MATCHLIMIT not retried.
  Gaps: the "is not JIT compiled, the interpreter is used" log line and
  the MATCHLIMIT no-retry path are untested.
- #626 reproducible builds: -ffile-prefix-map, ar -D, cargo --locked +
  remap, fixed tarball/jar dates. Kernel release kept out of
  nxt_auto_config.h when SOURCE_DATE_EPOCH is set.
- #722 CI matrix (merged): node/java run oldest + newest + newest LTS
  (java 17/25/27, node 22/26); EOL = note contains "EOL" (substring).
- #699 deadline fixture: teardown reports only a deadline the test body
  swallowed; setup hangs and run() teardown hangs report once.

Output
- First line: "Blockers: N, important: N, minor: N" + merge readiness.
- Each finding: [blocker|important|minor], file:line, quote, what is
  wrong, evidence (command + output), suggested fix, suggested test.
- Then: untested claims, master conflicts, red CI jobs with cause
  (PR code vs infra), what you ran. Confirmed findings only.
- End with: Reviewed-by / Harness / Tools trailers.
