# freeunit-harness — contributor toolkit for FreeUnit.
#
# `make help` lists everything. The short version:
#
#   make dev     source + build + a running unitd on :8080
#   make test    the pytest suite against that build
#   make bench   A/B benchmark two builds
#   make lint    the style gates CI will run
#   make tools-test  the unit tests of the harness tools
#   make ci-local  the CI pipeline, in Docker, before you push

SHELL := /bin/bash
.DEFAULT_GOAL := help

# ---- knobs ------------------------------------------------------------------
# Source. By default the harness manages its OWN checkout under ./src, because
# ./configure rewrites the root Makefile and build/ of whatever tree it runs in
# — pointing this at a checkout you are working in would clobber its configured
# state. To build from a local repo without touching it, set UNIT_GIT to that
# path: the clone is local and cheap. UNIT_SRC is the opt-out; see `make doctor`.
UNIT_GIT        ?= https://github.com/freeunitorg/freeunit.git
UNIT_REF        ?= master
UNIT_SRC        ?= $(CURDIR)/src

# NOT a free choice: test/conftest.py resolves unitd as
#   <source-root>/build/sbin/unitd
# with no command-line override (conftest.py:466, `builddir =
# f'{option.current_dir}/build'`). So the build must live inside the source
# tree for `make test` to find it at all. This is also why the harness manages
# its own checkout by default — an in-tree build is unavoidable, so it should
# not be in a tree you are editing.
BUILD_DIR       ?= $(UNIT_SRC)/build
# The dev unitd keeps its pid, log, socket and state in run/dev. The parent run/
# holds private state (lab env, task ledger, bus, deny list): clean must never
# touch it, so RUN_DIR is always a subdirectory.
RUN_DIR         ?= $(CURDIR)/run/dev
PORT            ?= 8080
JOBS            ?= $(shell nproc 2>/dev/null || echo 4)

# Language modules to build. Space separated, e.g. MODULES="php python3".
# Each needs its own dev headers present; `make doctor` reports what is missing.
MODULES         ?= php

# --tests builds the C unit-test runner as well (build/tests).
CONFIGURE_FLAGS ?= --debug --openssl --tests

UNITD           := $(BUILD_DIR)/sbin/unitd
CONTROL         := $(RUN_DIR)/control.sock
CTL             := $(CURDIR)/tools/unitd-ctl

export BUILD_DIR RUN_DIR PORT UNIT_SRC CONTROL

# ---- help -------------------------------------------------------------------
.PHONY: help
help:
	@echo "freeunit-harness — contributor toolkit for FreeUnit"
	@echo
	@echo "Getting started"
	@echo "  make dev            source + build + running unitd on :$(PORT)"
	@echo "  make doctor         report toolchain/deps status, no changes made"
	@echo
	@echo "Build"
	@echo "  make src            clone the FreeUnit source into UNIT_SRC (once; later runs keep it)"
	@echo "  make configure      run ./configure into BUILD_DIR"
	@echo "  make build          build unitd + the modules in MODULES"
	@echo "  make rebuild        build without re-running configure"
	@echo
	@echo "Run"
	@echo "  make start|stop|restart|status   manage the dev unitd"
	@echo "  make logs           tail the dev unitd log"
	@echo "  make config         PUT a hello-world config onto :$(PORT)"
	@echo
	@echo "Verify"
	@echo "  make test           pytest suite (TESTS=path/to/test_x.py to narrow)"
	@echo "  make ctest          the C unit-test runner (build/tests)"
	@echo "  make lint           whitespace + style gates, as CI runs them"
	@echo "  make tools-test     unit tests of the harness tools (tools/tests, tools/model/tests)"
	@echo "  make check-public   scan for private details (--all, plus run/public-deny.local if present)"
	@echo "  make agents         write the skills links and the .claude/.opencode agent files"
	@echo "  make agents-check   fail when those differ from a fresh tools/gen-agents.py run"
	@echo "  make hooks-install  show the Claude Code hook settings diff (dry run)"
	@echo "  make ci-local       full CI pipeline in Docker"
	@echo "  make bench A=<dir> B=<dir>   A/B benchmark two builds"
	@echo
	@echo "Clean"
	@echo "  make clean          drop BUILD_DIR and RUN_DIR (run/dev only, not run/)"
	@echo "  make distclean      also drop the managed source checkout"
	@echo
	@echo "Knobs: UNIT_GIT UNIT_REF UNIT_SRC BUILD_DIR RUN_DIR PORT MODULES JOBS"
	@echo "       CONFIGURE_FLAGS  (current: $(CONFIGURE_FLAGS))"

# ---- doctor -----------------------------------------------------------------
.PHONY: doctor
doctor:
	@tools/doctor

# ---- source -----------------------------------------------------------------
.PHONY: src
src: $(UNIT_SRC)/configure

$(UNIT_SRC)/configure:
	@if [ "$(UNIT_SRC)" != "$(CURDIR)/src" ]; then \
	    echo "UNIT_SRC=$(UNIT_SRC) does not look like a FreeUnit checkout"; \
	    echo "(no ./configure found). Refusing to clone over it."; \
	    exit 1; \
	fi
	@echo "==> cloning $(UNIT_GIT) ($(UNIT_REF)) into $(UNIT_SRC)"
	@git clone --branch "$(UNIT_REF)" "$(UNIT_GIT)" "$(UNIT_SRC)"

# ---- build ------------------------------------------------------------------
# configure runs from the source dir and writes artifacts into <src>/build:
# sbin/unitd and lib/unit/modules/*.so appear there directly, with no
# `make install` step. That is both what test/conftest.py requires and the
# layout tools/bench-engines.sh expects, so the two agree for free.
.PHONY: configure
configure: src
	@echo "==> configure $(CONFIGURE_FLAGS) --prefix=$(BUILD_DIR)"
	@cd "$(UNIT_SRC)" && ./configure $(CONFIGURE_FLAGS) --prefix="$(BUILD_DIR)"
	@for m in $(MODULES); do \
	    echo "==> configure module: $$m"; \
	    ( cd "$(UNIT_SRC)" && ./configure $$m ) || exit 1; \
	done

.PHONY: build
build: configure rebuild

.PHONY: rebuild
rebuild:
	@test -f "$(BUILD_DIR)/Makefile" || { echo "not configured yet — run 'make build'"; exit 1; }
	@echo "==> make -j$(JOBS) unitd"
	@$(MAKE) --no-print-directory -C "$(UNIT_SRC)" -j$(JOBS) unitd
	@for m in $(MODULES); do \
	    echo "==> make $$m"; \
	    $(MAKE) --no-print-directory -C "$(UNIT_SRC)" -j$(JOBS) $$m || exit 1; \
	done
	@echo "==> built $(UNITD)"

.PHONY: ctest
ctest:
	@test -x "$(BUILD_DIR)/tests" || $(MAKE) --no-print-directory -C "$(UNIT_SRC)" -j$(JOBS) tests
	@"$(BUILD_DIR)/tests"

# ---- run --------------------------------------------------------------------
.PHONY: start stop restart status logs config
start:
	@$(CTL) start
stop:
	@$(CTL) stop
restart:
	@$(CTL) restart
# `|| true`: "stopped" is a legitimate answer, not a build failure. unitd-ctl
# still exits non-zero so scripts can branch on it.
status:
	@$(CTL) status || true
logs:
	@$(CTL) logs
config:
	@$(CTL) config

.PHONY: dev
dev: build start config
	@echo
	@echo "Ready.  http://localhost:$(PORT)/"
	@echo "  control socket : $(CONTROL)"
	@echo "  log            : $(RUN_DIR)/unit.log"
	@echo "  config         : curl -s --unix-socket $(CONTROL) http://localhost/config"
	@echo "  stop           : make stop"

# ---- agent discovery ---------------------------------------------------------
# tools/gen-agents.py writes the skills links and the .claude/.opencode agent
# files from agents/ and recipes/. Run it after editing either. validate: planned.
.PHONY: agents agents-check
agents:
	@python3 tools/gen-agents.py

agents-check:
	@python3 tools/gen-agents.py --check

# ---- verify -----------------------------------------------------------------
# The suite has no way to be told where unitd is (see BUILD_DIR above), so it
# must run from the source root with the build in place. TESTS narrows the
# selection: TESTS=test/test_static.py, or TESTS="test -k keepalive".
#
# SUDO=sudo is needed for the privilege-sensitive cases (isolation, clone
# credentials, user switching); CI runs the whole suite under `sudo -E`. Left
# empty by default so `make test` never silently escalates.
TESTS ?= test
SUDO  ?=
.PHONY: test
test:
	@if [ -f "$(RUN_DIR)/unit.pid" ] && kill -0 "$$(cat "$(RUN_DIR)/unit.pid" 2>/dev/null)" 2>/dev/null; then \
	    echo "the dev unitd is running (pid file $(RUN_DIR)/unit.pid)."; \
	    echo "Any unitd on :8080 breaks pytest at startup. Run 'make stop' first."; \
	    exit 1; \
	fi
	@test -x "$(UNITD)" || { echo "no $(UNITD) — run 'make build' first"; exit 1; }
	@echo "==> pytest $(TESTS)"
	@cd "$(UNIT_SRC)" && $(SUDO) python3 -m pytest --print-log $(TESTS)

.PHONY: lint
lint:
	@tools/check-style

.PHONY: tools-test
tools-test:
	@python3 -m unittest discover -s tools/tests -v
	@python3 tools/perf-lab/test_perf_compare.py
	@python3 -m pytest -q tools/model/tests

.PHONY: check-public
check-public:
	@tools/check-public.sh --all
	@if [ -f run/public-deny.local ]; then tools/check-public.sh --all --deny-file run/public-deny.local; fi

.PHONY: hooks-install
hooks-install:
	@tools/hooks/install
	@echo "To take them out again later: tools/hooks/install --remove --apply"

.PHONY: ci-local
ci-local:
	@tools/ci-local

# ---- bench ------------------------------------------------------------------
# make bench A=/path/to/epoll-build B=/path/to/io_uring-build [ROUNDS=4]
ROUNDS ?= 4
A ?=
B ?=
.PHONY: bench
bench:
	@if [ -z "$(A)" ] || [ -z "$(B)" ]; then \
	    echo "usage: make bench A=<builddir> B=<builddir> [ROUNDS=$(ROUNDS)]"; \
	    echo "       (point both at the same dir plus ALLOW_SAME=1 for a null A/A run)"; \
	    exit 2; \
	fi
	@tools/bench-engines.sh "$$(basename $(A))" "$(A)" "$$(basename $(B))" "$(B)" $(ROUNDS)

# ---- clean ------------------------------------------------------------------
.PHONY: clean distclean
clean:
	@$(CTL) stop || true
	@rm -rf "$(BUILD_DIR)" "$(RUN_DIR)"
	@echo "==> removed $(BUILD_DIR) and $(RUN_DIR)"

distclean: clean
	@if [ "$(UNIT_SRC)" = "$(CURDIR)/src" ]; then \
	    rm -rf "$(UNIT_SRC)"; echo "==> removed $(UNIT_SRC)"; \
	else \
	    echo "==> UNIT_SRC is not harness-managed, leaving $(UNIT_SRC) alone"; \
	fi
