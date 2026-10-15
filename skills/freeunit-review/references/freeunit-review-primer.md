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

C style (summary of docs/style.md, which wins on any conflict;
the code wins over both)
- 4 spaces, no tabs, 80 columns, /* */ comments only.
- Return type on its own line, name at column 1; two blank lines
  between top-level functions.
- Locals in one block at the top, names aligned in one column, `*` on
  the name, ordered by type-name length, shortest first (92% of
  blocks). No blank lines inside the block. Match an existing block.
- Error branches use nxt_slow_path(); likely ones nxt_fast_path().
- Cleanup via goto fail (or the label the function already uses).
  Return NXT_OK / NXT_ERROR, NXT_AGAIN / NXT_DECLINED only where the
  distinction is real.
- nxt_sprintf grammar is not printf: hex is %Xd, not %X.
- nxt_mp_get aligned, nxt_mp_nget unaligned, nxt_mp_alloc freeable:
  the wrong one is a bug (SIGBUS on 32-bit ARM). Prefer
  nxt_list_zero_add().
- Since #444/#413: nxt_size_add()/nxt_size_mul() for length maths,
  nxt_span_t for untrusted records, nxt_fallthrough; not a comment,
  no VLAs, clean under --hardening=strict, test hooks under
  #if (NXT_TESTS) in NXT_LIB_SRCS / NXT_LIB_UNIT_SRCS only.
- Watch integer widths: nxt_conf_map types write their own size (32-bit bug
  class from #581); check 32-bit, big-endian (s390x), musl and weak-memory
  (riscv64, loongarch64, ppc64le) behaviour.

Early feedback (docs/style.md, "Close the feedback loop early")
- State the expected result before each run or check.
- Read each result as soon as it lands: build, first test, each CI
  job. Report a failure, red job or broken assumption in the next
  status line with the quoted log line, not only in the final review.
- Try the narrowest fix on the spot and re-run the failed step. Stop
  and report after two failed attempts or when it needs a decision.

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
