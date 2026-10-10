#!/bin/bash
# bench-startup.sh -- startup/lifecycle latency for one unitd build, measured
# as wall-clock time across the four phases a change to the startup path can
# actually move.
#
# WHAT IT DOES
#   bench-release.sh answers "does the candidate cost more router CPU per
#   request" -- the steady-state request path. It is the wrong instrument for
#   a change that only runs before the first request: a capability probe at
#   runtime init, a credential check in the PROCESS_READY handshake, a new
#   syscall during fork. Those cost nothing per request and everything (if
#   anything) once per process. Benching them with bench-release.sh produces
#   an honest, uninformative 0%.
#
#   This script measures, per iteration, on a fresh state dir:
#
#     a_router_ms     exec unitd  ->  "router started" appears in the log
#     b_first200_ms   config PUT  ->  first HTTP 200 from a fresh app worker
#     c_reconf_ms     reconfigure ->  first HTTP 200 served by the NEW app
#     d_term_ms       SIGTERM     ->  main process has exited
#
#   Phase (a) covers runtime init, capability/credential probing, the fork of
#   main's children and the router's own start. Phase (b) covers the
#   controller applying a config plus the full app-worker handshake
#   (fork -> libunit init -> PROCESS_READY -> shm queue mapping -> first
#   request). Phase (c) is the same handshake with a router already warm,
#   which isolates worker start from process-tree start. Phase (d) covers
#   QUIT propagation and teardown.
#
# USAGE
#   bench-startup.sh [--label NAME] <builddir> [iters]
#     iters default 20. Iteration 0 is a warm-up: it is recorded in the TSV
#     with warmup=1 and excluded from the summary, because the first unitd
#     start of a session pays page-cache and dynamic-linker costs no later
#     iteration pays.
#
#   bench-startup.sh --preflight-only
#     Run the environment checks (SMT siblings, governor, quiet box) and exit.
#
#   <builddir> is a build or --prefix install tree: <builddir>/sbin/unitd must
#   exist, and <builddir>/lib/unit/modules must hold a python or a php module
#   -- phases (b) and (c) need a real application, because a "share" route
#   would skip the entire worker handshake, which is the part a startup-path
#   change actually touches. The module is auto-detected; APP=python|php
#   picks one explicitly. Which language it is does not matter to what is
#   being compared (the same app runs on both sides), but it does change the
#   absolute numbers, so never compare across two different APP values.
#
#   ENV OVERRIDES
#     ITERS=20            same as the positional argument
#     APP=python|php      which module to drive the worker handshake with;
#                         auto-detected from <builddir>/lib/unit/modules
#     PORT=8911           listener port for the app
#     CTRL_PORT=8912      control API port. TCP, not a unix socket, on
#                         purpose: the PUT that starts phase (b)'s clock is
#                         then issued from bash's own /dev/tcp with no fork,
#                         so curl's ~5 ms process spawn is not inside the
#                         measurement.
#     UNIT_CPUS=0,1,4,5   taskset set for the whole unit process tree
#     POLL_CPUS=2,3,6,7   taskset set for this script (it re-execs itself
#                         pinned). Must not share a physical core with
#                         UNIT_CPUS -- see PREFLIGHT.
#     TIMEOUT_MS=15000    per-phase timeout; a phase that hits it aborts the
#                         run rather than recording a censored sample
#     RUNBASE=/tmp/freeunit-bench-startup
#     RESULTS=file        output TSV, default
#                         $RUNBASE/bench-results-startup-<label>.tsv
#                         (the bench-results-*.tsv convention in unit/tools/)
#     ALLOW_NOISY=1       skip the quiet-box check
#     ALLOW_POWERSAVE=1   permit a 'powersave' governor -- see PREFLIGHT
#     ALLOW_SMT_OVERLAP=1 permit UNIT_CPUS/POLL_CPUS to share a core
#
# RESOLUTION, AND WHAT IT IS NOT
#   The clock is bash's EPOCHREALTIME (microseconds). Every poll loop is
#   fork-free -- `mapfile` for the log, `exec {fd}<>/dev/tcp/...` for HTTP,
#   `kill -0` for liveness, and a fifo read-timeout for the 1 ms nap between
#   attempts -- so a poll iteration costs ~1 ms, not the ~5 ms a curl or grep
#   subprocess would cost, and quantisation is 1 ms. (The nap is not
#   optional: a spin loop overruns the listen backlog of an app that is still
#   starting, and the resulting TCP retransmit backoff blocked one probe for
#   240 s against a 15 s deadline.) Do not read that as 0.1 ms
#   accuracy: these are WALL-CLOCK measurements of a process tree being
#   forked and scheduled, and the run-to-run spread on an idle 8-core box is
#   a few milliseconds even with pinning. Treat the IQR the summary prints as
#   the resolution of the run, not the clock's resolution, and do not claim a
#   difference smaller than the wider of the two IQRs being compared.
#
#   In particular: this measures wall-clock, so unlike bench-release.sh's
#   CPU-tick metric it IS sensitive to frequency scaling. A `powersave`
#   governor matters more here, not less. See PREFLIGHT.
#
#   Median and IQR rather than mean and sd: startup latency is bounded below
#   (there is a minimum amount of work) and has a long right tail (a stray
#   scheduling delay, a page fault storm). A mean chases the tail; the median
#   reports the typical start, and p90 reports the tail explicitly instead of
#   smearing it into the centre.
#
# COMPARING TWO BUILDS
#   Run it once per build, alternating -- not build A's 20 iterations
#   followed by build B's 20. Slot-position and thermal effects are exactly
#   as real here as they are in bench-release.sh (see release-ab.md), and a
#   block design is the only thing that keeps them from riding on the label:
#
#     for i in 1 2 3 4; do
#       bench-startup.sh --label A  /path/build-A 10
#       bench-startup.sh --label B  /path/build-B 10
#       bench-startup.sh --label B  /path/build-B 10
#       bench-startup.sh --label A  /path/build-A 10
#     done
#
#   with RESULTS pointing at one file per (label, block) so the blocks stay
#   distinguishable. This script deliberately does NOT implement the
#   two-build A/B itself: bench-release.sh's 8-cell counterbalance exists
#   because a two-cell design is easy to get subtly wrong, and duplicating
#   that machinery here without a null gate to validate it would be worse
#   than leaving the blocking to the caller.
#
# GUARDS AND WHY
#   - LC_ALL=C: EPOCHREALTIME uses the locale decimal separator. On a box
#     with LC_NUMERIC=ru_RU.UTF-8 (the reference bench host is one) it reads "1756...,123456"
#     and the microsecond arithmetic below silently produces garbage rather
#     than failing. Same class of bug as the awk printf one release-ab.md
#     documents.
#   - a cleanup trap kills unitd on every exit path including die(): a
#     leftover server holds PORT and every later iteration fails to bind,
#     which looks like a new bug rather than a stale process
#   - the body of every probe is checked, not just the status: phase (c)
#     polls for the NEW app's body, because the OLD worker keeps answering
#     200 across a reconfigure and a status-only check would measure zero
#   - a phase that times out aborts; a censored sample quietly pulls a
#     median towards "fast"
#   - iteration 0 is dropped from the summary
#
# PREFLIGHT: SMT SIBLINGS AND THE CPU GOVERNOR
#   UNIT_CPUS and POLL_CPUS being numerically disjoint is not the same as
#   being disjoint physical cores. This script busy-polls, so if the poller
#   lands on an SMT sibling of a core unitd is starting on, it steals
#   execution units from the thing being measured. Refuses unless
#   ALLOW_SMT_OVERLAP=1.
#
#   The governor is enforced the same way bench-release.sh enforces it, and
#   for a stronger reason: a wall-clock metric reads a CPU that has not yet
#   ramped up from 800 MHz as a slow startup. On the reference bench host the governor is
#   root-owned and sudo prompts, so an agent cannot set it -- either the user
#   runs `sudo cpupower frequency-set -g performance` first, or the run
#   carries ALLOW_POWERSAVE=1 and the report says so out loud.
set -u
export LC_ALL=C

usage() {
    echo "usage: $0 [--label NAME] <builddir> [iters]" >&2
    echo "       $0 --preflight-only" >&2
    exit 2
}

LABEL=""
PREFLIGHT_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --preflight-only) PREFLIGHT_ONLY=1; shift ;;
        --label) LABEL=${2:-}; shift 2 ;;
        --label=*) LABEL=${1#--label=}; shift ;;
        -*) usage ;;
        *) break ;;
    esac
done

BUILD=""
if [ "$PREFLIGHT_ONLY" != 1 ]; then
    [ $# -ge 1 ] || usage
    BUILD=$1
    ITERS=${2:-${ITERS:-20}}
else
    ITERS=0
fi

PORT=${PORT:-8911}
CTRL_PORT=${CTRL_PORT:-8912}
UNIT_CPUS=${UNIT_CPUS:-0,1,4,5}
POLL_CPUS=${POLL_CPUS:-2,3,6,7}
TIMEOUT_MS=${TIMEOUT_MS:-15000}
RUNBASE=${RUNBASE:-/tmp/freeunit-bench-startup}
[ -n "$LABEL" ] || LABEL=$(basename "${BUILD:-preflight}")
OUT=${RESULTS:-"$RUNBASE/bench-results-startup-$LABEL.tsv"}

UNIT_PID=""
RUN=""
cleanup() {
    # TERM first so unit tears its own children down, then KILL. Sending
    # both back to back (as this used to) kills main before it can forward
    # QUIT, orphaning the router and every app process -- which then keep
    # PORT bound and make the NEXT run fail at bind() instead.
    local i
    if [ -n "$UNIT_PID" ]; then
        kill -TERM "$UNIT_PID" 2>/dev/null
        for i in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$UNIT_PID" 2>/dev/null || return 0
            sleep 0.2
        done
        kill -KILL "$UNIT_PID" 2>/dev/null
    fi
}
trap cleanup EXIT
die() { echo "FATAL: $*" >&2; cleanup; exit 1; }

# ---------------------------------------------------------------- preflight

expand_cpus() {
    echo "$1" | tr ',' '\n' | while read -r r; do
        case "$r" in *-*) seq "${r%-*}" "${r#*-}" ;; *) echo "$r" ;; esac
    done
}

preflight() {
    local first gov govpath sibs sib_all c overlap
    first=$(expand_cpus "$UNIT_CPUS" | head -1)
    govpath="/sys/devices/system/cpu/cpu$first/cpufreq/scaling_governor"
    gov="unknown"
    if [ -r "$govpath" ]; then
        gov=$(cat "$govpath")
    else
        echo "WARNING: $govpath not readable -- cannot check the governor." >&2
    fi

    sib_all=""
    for c in $(expand_cpus "$UNIT_CPUS"); do
        local p="/sys/devices/system/cpu/cpu$c/topology/thread_siblings_list"
        [ -r "$p" ] && sib_all="$sib_all $(expand_cpus "$(cat "$p")")"
    done
    sibs=$(echo "$sib_all" | tr ' ' '\n' | grep -v '^$' | sort -n -u)

    echo "unit cpus=$UNIT_CPUS  poll cpus=$POLL_CPUS  governor(cpu$first)=$gov"

    if [ "$gov" = "powersave" ] && [ "${ALLOW_POWERSAVE:-0}" != 1 ]; then
        die "cpu$first governor is 'powersave' -- this script measures WALL CLOCK, so a core still at its minimum frequency reads as a slow startup. Fix: sudo cpupower frequency-set -g performance. ALLOW_POWERSAVE=1 to override (and say so in the report)."
    elif [ "$gov" != "performance" ] && [ "$gov" != "unknown" ]; then
        echo "WARNING: governor is '$gov', not 'performance'." >&2
    fi

    if [ -n "$sibs" ]; then
        overlap=$(comm -12 <(echo "$sibs") <(expand_cpus "$POLL_CPUS" | sort -n -u))
        if [ -n "$overlap" ]; then
            echo "cpu topology: POLL_CPUS overlaps the SMT sibling set of UNIT_CPUS [$overlap]." >&2
            echo "  This script busy-polls; on a shared physical core the poller steals" >&2
            echo "  execution units from the startup it is timing." >&2
            [ "${ALLOW_SMT_OVERLAP:-0}" = 1 ] || \
                die "UNIT_CPUS and POLL_CPUS share physical core(s) via SMT ($overlap) -- ALLOW_SMT_OVERLAP=1 to override"
        fi
    fi

    if [ "${ALLOW_NOISY:-0}" != 1 ]; then
        local busy load1 nc over
        busy=$(pgrep -x 'cc1|cc1plus|ld|ld.gold|ld.lld|make|gcc|g\+\+|clang|clang\+\+|cargo|rustc' 2>/dev/null | tr '\n' ' ')
        [ -n "$busy" ] && echo "WARNING: compiler/linker running (pid $busy) -- startup timing is wall-clock and will absorb it. ALLOW_NOISY=1 to silence." >&2
        load1=$(awk '{print $1}' /proc/loadavg); nc=$(nproc)
        over=$(awk -v l="$load1" -v n="$nc" 'BEGIN{print (l > n*0.5) ? 1 : 0}')
        [ "$over" = 1 ] && echo "WARNING: load average $load1 on $nc cores. ALLOW_NOISY=1 to silence." >&2
    fi
}

preflight
[ "$PREFLIGHT_ONLY" != 1 ] || exit 0

# Re-exec pinned. Doing this here rather than asking the caller to remember
# `taskset` keeps the poller off unitd's cores by construction.
if [ "${_BENCH_STARTUP_PINNED:-0}" != 1 ]; then
    export _BENCH_STARTUP_PINNED=1
    exec taskset -c "$POLL_CPUS" "$0" --label "$LABEL" "$BUILD" "$ITERS"
fi

[ -x "$BUILD/sbin/unitd" ] || die "no unitd at $BUILD/sbin/unitd"
MODULES="$BUILD/lib/unit/modules"
APP=${APP:-}
if [ -z "$APP" ]; then
    if ls "$MODULES"/python*.unit.so >/dev/null 2>&1; then APP=python
    elif ls "$MODULES"/php*.unit.so >/dev/null 2>&1; then APP=php
    fi
fi
case "$APP" in
    python) ls "$MODULES"/python*.unit.so >/dev/null 2>&1 || die "APP=python but no python module in $MODULES" ;;
    php)    ls "$MODULES"/php*.unit.so    >/dev/null 2>&1 || die "APP=php but no php module in $MODULES" ;;
    *) die "no python or php module in $MODULES -- phases (b) and (c) need a real app worker, not a share route (a share route would skip the whole handshake and measure nothing)" ;;
esac

# A previous run that was SIGKILLed mid-phase (or any other unitd on the
# box) leaves PORT/CTRL_PORT held, and unitd then reports only
# "bind(...) failed (98: Address already in use)" from inside a log this
# script is polling -- which surfaces as "unitd exited during start" and
# reads like a broken build rather than a stale process. Name it up front.
for prt in "$PORT" "$CTRL_PORT"; do
    if ss -ltnH "sport = :$prt" 2>/dev/null | grep -q .; then
        echo "port $prt is already listening:" >&2
        ss -ltnpH "sport = :$prt" 2>/dev/null >&2
        die "port $prt in use -- a leaked unitd from an earlier run, or another server. Kill it, or set PORT/CTRL_PORT."
    fi
done

SHA=$(sha256sum "$BUILD/sbin/unitd" | cut -c1-16)
echo "label=$LABEL build=$BUILD unitd=$SHA app=$APP iters=$ITERS -> $OUT"

# ------------------------------------------------------------------- timing

# Integer microseconds from bash's own clock, no fork. EPOCHREALTIME is
# "seconds.microseconds"; stripping the separator yields microseconds
# directly. LC_ALL=C above is what makes that separator a '.'.
# Sets NOW_US rather than echoing it. Capturing the value with a command
# substitution instead would fork a subshell on every poll iteration, which
# is precisely the cost this script goes out of its way to keep out of the
# measured intervals.
NOW_US=0
now_us() { local t=${EPOCHREALTIME}; NOW_US=$(( 10#${t/./} )); }

# Microseconds -> milliseconds with three decimals, zero-padded. Writing
# this as "$(( us / 1000 )).$(( us % 1000 ))" loses the leading zeros of the
# remainder: 123045 us would print as "123.45", i.e. 123.45 ms instead of
# 123.045 ms, and only for the ~10% of samples whose remainder is under 100.
ms_of() { local us=$1; printf '%d.%03d' $(( us / 1000 )) $(( us % 1000 )); }

# Fork-free read of a whole (small) file into READ_BUF. `mapfile` is a
# builtin and its input redirection does not fork; `$(<file)` does.
READ_BUF=""
slurp() {
    local -a lines=()
    mapfile -t lines < "$1" 2>/dev/null
    READ_BUF="${lines[*]}"
}

APPDIR="$RUNBASE/app"
mkdir -p "$APPDIR/one" "$APPDIR/two"
if [ "$APP" = python ]; then
    for a in one two; do
        u=$(echo "$a" | tr '[:lower:]' '[:upper:]')
        cat > "$APPDIR/$a.py" <<PY
def application(environ, start_response):
    start_response('200 OK', [('Content-Type', 'text/plain'),
                              ('Content-Length', '3')])
    return [b'$u']
PY
    done
else
    for a in one two; do
        u=$(echo "$a" | tr '[:lower:]' '[:upper:]')
        printf '<?php echo "%s";' "$u" > "$APPDIR/$a/index.php"
    done
fi

# The two apps differ only in the three bytes they return. Phase (c) polls
# for the new body rather than for a 200, because the OLD worker keeps
# answering 200 right across the reconfigure -- a status-only check there
# reports ~0 ms and looks like a wonderfully fast reconfigure.
app_config() {   # $1 = one|two
    if [ "$APP" = python ]; then
        printf '{"listeners":{"127.0.0.1:%s":{"pass":"applications/%s"}},"applications":{"%s":{"type":"python","path":"%s","module":"%s","processes":1}}}' \
            "$PORT" "$1" "$1" "$APPDIR" "$1"
    else
        printf '{"listeners":{"127.0.0.1:%s":{"pass":"applications/%s"}},"applications":{"%s":{"type":"php","root":"%s/%s","script":"index.php","processes":1}}}' \
            "$PORT" "$1" "$1" "$APPDIR" "$1"
    fi
}

# Fork-free HTTP over bash's /dev/tcp. Sets HTTP_BODY on success.
# Returns 1 on connect failure, 2 on a non-200, 0 on 200.
HTTP_BODY=""
http_get() {   # $1 = port, $2 = path
    local fd line status
    HTTP_BODY=""
    exec {fd}<>"/dev/tcp/127.0.0.1/$1" 2>/dev/null || return 1
    # HTTP/1.0, not 1.1: a PHP app that sets no Content-Length makes Unit
    # answer an HTTP/1.1 request with chunked transfer encoding, and the
    # first body line is then the chunk LENGTH ("3"), not the payload. That
    # reads as "the app is up but returning the wrong thing" and the poll
    # below never terminates. HTTP/1.0 gets the body delimited by close.
    printf 'GET %s HTTP/1.0\r\nHost: b\r\n\r\n' "$2" >&"$fd" 2>/dev/null || {
        exec {fd}>&-; return 1; }
    IFS= read -r -t 5 -u "$fd" line || { exec {fd}>&-; return 1; }
    status=${line#* }; status=${status%% *}
    while IFS= read -r -t 5 -u "$fd" line; do
        case "$line" in ''|$'\r') break ;; esac
    done
    # Accumulate everything left rather than one line: belt and braces
    # against a framing that still splits the payload. The final `read`
    # returns non-zero at EOF while still assigning, so its status is
    # deliberately not checked.
    while IFS= read -r -t 5 -u "$fd" line; do
        HTTP_BODY="$HTTP_BODY${line%$'\r'}"
    done
    HTTP_BODY="$HTTP_BODY${line%$'\r'}"
    exec {fd}>&-
    [ "$status" = "200" ] || return 2
    return 0
}

# A fork-free sleep, via a read timeout on a fifo nothing ever writes to.
# The poll loops need one: hammering connect() in a spin loop overruns the
# listen backlog of an app that is still starting, and once the SYN queue
# fills the kernel drops SYNs and `exec {fd}<>/dev/tcp` blocks through
# multi-second TCP retransmit backoff -- long past the phase deadline, which
# is only tested between attempts. Observed as a 240 s hang on a 15 s
# timeout. 1 ms also quantises every measurement below, which is well inside
# the few-millisecond spread these numbers have anyway.
NAPFD=""
nap_setup() {
    local fifo="$RUNBASE/nap.fifo"
    rm -f "$fifo"; mkfifo "$fifo" || die "cannot create $fifo"
    exec {NAPFD}<>"$fifo"
    rm -f "$fifo"
}
nap() { read -r -t "${NAP_S:-0.001}" -u "$NAPFD" _; return 0; }

# Fork-free control-API PUT. Sets CTRL_REPLY.
CTRL_REPLY=""
ctrl_put() {   # $1 = path, $2 = body
    local fd line
    CTRL_REPLY=""
    exec {fd}<>"/dev/tcp/127.0.0.1/$CTRL_PORT" 2>/dev/null || return 1
    printf 'PUT %s HTTP/1.1\r\nHost: b\r\nContent-Length: %s\r\nConnection: close\r\n\r\n%s' \
        "$1" "${#2}" "$2" >&"$fd" 2>/dev/null || { exec {fd}>&-; return 1; }
    while IFS= read -r -t 10 -u "$fd" line; do
        CTRL_REPLY="$CTRL_REPLY$line"
    done
    exec {fd}>&-
    case "$CTRL_REPLY" in *success*) return 0 ;; *) return 1 ;; esac
}

# ---------------------------------------------------------------- iteration

mkdir -p "$RUNBASE"
nap_setup
: > "$OUT"
printf 'label\titer\twarmup\ta_router_ms\tb_first200_ms\tc_reconf_ms\td_term_ms\tsha\tapp\n' >> "$OUT"

run_iter() {   # $1 = iteration number
    local iter=$1 warm=0 t0 t1 deadline log stderrlog buf
    [ "$iter" = 0 ] && warm=1

    RUN="$RUNBASE/it"
    rm -rf "$RUN"; mkdir -p "$RUN/state"
    log="$RUN/unit.log"; stderrlog="$RUN/stderr.log"
    : > "$log"; : > "$stderrlog"

    # ---- phase (a): exec -> "router started"
    now_us; t0=$NOW_US
    taskset -c "$UNIT_CPUS" "$BUILD/sbin/unitd" --no-daemon \
        --control "127.0.0.1:$CTRL_PORT" \
        --pid "$RUN/unit.pid" --log "$log" \
        --statedir "$RUN/state" --tmpdir "$RUN" --modulesdir "$MODULES" \
        >/dev/null 2>"$stderrlog" &
    UNIT_PID=$!

    deadline=$(( t0 + TIMEOUT_MS * 1000 ))
    while :; do
        slurp "$log";       buf=$READ_BUF
        slurp "$stderrlog"; buf="$buf$READ_BUF"
        case "$buf" in *"router started"*) break ;; esac
        kill -0 "$UNIT_PID" 2>/dev/null || die "iter$iter: unitd exited during start; log: $buf"
        now_us; [ "$NOW_US" -lt "$deadline" ] || die "iter$iter: timeout waiting for 'router started'"
        nap
    done
    now_us; t1=$NOW_US
    local a_ms; a_ms=$(ms_of $(( t1 - t0 )))

    # ---- phase (b): config PUT -> first 200 from a fresh worker
    now_us; t0=$NOW_US
    ctrl_put /config "$(app_config one)" || die "iter$iter: config PUT rejected: $CTRL_REPLY"
    deadline=$(( t0 + TIMEOUT_MS * 1000 ))
    while :; do
        if http_get "$PORT" / ; then
            case "$HTTP_BODY" in *ONE*) break ;; esac
        fi
        kill -0 "$UNIT_PID" 2>/dev/null || die "iter$iter: unitd exited during phase b"
        now_us; [ "$NOW_US" -lt "$deadline" ] || die "iter$iter: timeout waiting for first 200 (last body: [$HTTP_BODY])"
        nap
    done
    now_us; t1=$NOW_US
    local b_ms; b_ms=$(ms_of $(( t1 - t0 )))

    # ---- phase (c): reconfigure -> first 200 from the NEW app
    # The old worker keeps answering 200 throughout, so this polls the body.
    # A status-only check here measures nothing and reports ~0 ms.
    now_us; t0=$NOW_US
    ctrl_put /config "$(app_config two)" || die "iter$iter: reconfigure PUT rejected: $CTRL_REPLY"
    deadline=$(( t0 + TIMEOUT_MS * 1000 ))
    while :; do
        if http_get "$PORT" / ; then
            case "$HTTP_BODY" in *TWO*) break ;; esac
        fi
        kill -0 "$UNIT_PID" 2>/dev/null || die "iter$iter: unitd exited during phase c"
        now_us; [ "$NOW_US" -lt "$deadline" ] || die "iter$iter: timeout waiting for the new app (last body: [$HTTP_BODY])"
        nap
    done
    now_us; t1=$NOW_US
    local c_ms; c_ms=$(ms_of $(( t1 - t0 )))

    # ---- phase (d): SIGTERM -> exit
    now_us; t0=$NOW_US
    kill -TERM "$UNIT_PID" 2>/dev/null
    deadline=$(( t0 + TIMEOUT_MS * 1000 ))
    while kill -0 "$UNIT_PID" 2>/dev/null; do
        now_us; [ "$NOW_US" -lt "$deadline" ] || die "iter$iter: timeout waiting for exit"
        nap
    done
    now_us; t1=$NOW_US
    wait "$UNIT_PID" 2>/dev/null
    UNIT_PID=""
    local d_ms; d_ms=$(ms_of $(( t1 - t0 )))

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$LABEL" "$iter" "$warm" "$a_ms" "$b_ms" "$c_ms" "$d_ms" "$SHA" "$APP" >> "$OUT"
    local tag=""; [ "$warm" = 1 ] && tag=" (warmup)"
    echo "  iter$iter$tag: a=${a_ms}ms b=${b_ms}ms c=${c_ms}ms d=${d_ms}ms"
}

for i in $(seq 0 "$ITERS"); do
    run_iter "$i"
done
echo "done -> $OUT"

# -------------------------------------------------------------------- stats

# mawk is the default awk on Debian/Ubuntu and has no arrays-of-arrays and no
# asort(), so the samples go into a flat "col,index" keyed array and are
# sorted by hand. Written for gawk it would run here and silently summarise
# nothing.
awk -F'\t' '
    function pct(col, m, p,   idx, lo, hi, frac) {
        if (m == 1) return v[col ",1"]
        idx = 1 + (m - 1) * p
        lo = int(idx); hi = lo + 1; frac = idx - lo
        if (hi > m) return v[col "," m]
        return v[col "," lo] + frac * (v[col "," hi] - v[col "," lo])
    }
    NR == 1 { next }
    $3 == 1 { next }                     # warm-up iteration
    {
        for (c = 4; c <= 7; c++) { n[c]++; v[c "," n[c]] = $c + 0 }
    }
    END {
        split("a_router b_first200 c_reconf d_term", name, " ")
        printf "\n%-14s %8s %8s %8s %8s %8s %6s\n", \
               "phase", "median", "p25", "p75", "IQR", "p90", "n"
        for (c = 4; c <= 7; c++) {
            m = n[c] + 0
            if (m == 0) continue
            # insertion sort; m is ~20 and asort() is a gawk extension
            for (i = 2; i <= m; i++) {
                key = v[c "," i]
                for (j = i - 1; j >= 1 && v[c "," j] > key; j--)
                    v[c "," (j + 1)] = v[c "," j]
                v[c "," (j + 1)] = key
            }
            med = pct(c, m, 0.5);  q1  = pct(c, m, 0.25)
            q3  = pct(c, m, 0.75); p90 = pct(c, m, 0.90)
            printf "%-14s %8.1f %8.1f %8.1f %8.1f %8.1f %6d\n", \
                   name[c - 3] "_ms", med, q1, q3, q3 - q1, p90, m
        }
        print "\nmilliseconds, wall clock. Do not claim a between-build"
        print "difference smaller than the wider of the two IQRs."
    }
' "$OUT"
