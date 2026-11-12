#!/bin/sh
# bench-engines.sh — A/B benchmark harness comparing two unitd builds' event engines.
#
# WHAT IT DOES
#   Runs an identical request-hot-path matrix against two unitd build dirs
#   (e.g. an epoll-default build vs an io_uring-default build) and reports RPS,
#   p50/p99 latency, error counts and router CPU microseconds-per-request for
#   each. It is BUILD-AGNOSTIC: it only needs <builddir>/sbin/unitd (and, for the
#   PHP scenario, a php*.so under <builddir>/lib/unit/modules). It does not care how the
#   builds were configured, so pointing both slots at the same epoll build turns
#   this into a null A/A drift test.
#
# USAGE
#   bench-engines.sh <labelA> <builddirA> <labelB> <builddirB> [rounds]
#     rounds default 4. Builds are interleaved within every round, and the
#     order alternates per round (A/B, B/A, A/B, ...) so monotonic warmup or
#     thermal drift cannot stay correlated with one build label. Keep rounds
#     EVEN: with an odd count one build starts first more often than the
#     other, so a repeatable position effect would not cancel out of the means.
#
#   ENV OVERRIDES
#     RUNBASE=/path      base dir for per-run scratch (default under scratch/tmp)
#     RESULTS=file       machine-parsable results file (default ./bench-engines-results.txt)
#     PHPAPP=dir         PHP app root for the php scenario; must already contain
#                        index.php (validated up front; the harness never writes
#                        into it). Auto-created under RUNBASE only when unset.
#     ALLOW_SAME=1       permit both builds to report the SAME engine (A/A mode)
#     SKIP_AB=1          skip the secondary ApacheBench pass
#     MAX_FAILED=N       max failed requests a single measurement may record
#                        before it fails the round/run (default 0)
#     N_HIGH, N_LOW, N_CLOSE, N_PROXY, N_PHP   request counts (reproducibility knob)
#     OHA_TIMEOUT=dur    per-request oha timeout (default 30s; e.g. 5s, 500ms)
#     CURL_MAXTIME=secs  control-socket PUT transfer timeout (default 30)
#     SERVER_CORES=list  taskset CPU list for the server tree (default 0-3)
#     LOAD_CORES=list    taskset CPU list for the load generators (default 4-7)
#
#   FLAGS
#     --allow-same       same as ALLOW_SAME=1
#     --skip-ab          same as SKIP_AB=1
#
# ENGINE ASSERTION
#   After each build starts, the harness greps the unitd log for
#     using "<name>" event engine
#   and records the engine per build. If the two builds report the SAME engine
#   the whole run ABORTS unless --allow-same/ALLOW_SAME=1 is set. If the log line
#   is absent (older builds that predate it) the engine is recorded as "unknown"
#   with a warning, and the same-engine check is skipped (can't prove them equal).
#
# SCENARIOS (per round, per build)
#   ret200_ka_hi   return-200 keepalive, HIGH concurrency (-c 256)
#   ret200_ka_lo   return-200 keepalive, LOW  concurrency (-c 8)   <- herd detector
#   ret200_close   return-200, Connection: close                  (-c 16)
#   proxy_ka       proxy->self keepalive                          (-c 64)
#   php_ka         PHP hello app (SKIP unless php module + app available)
#
# OUTPUT
#   Appends `<label> <scenario> <tool> rps=.. p50ms=.. p99ms=.. failed=.. \
#   cpu_us_per_req=.. engine=..` lines to RESULTS, saves raw tool output per
#   measurement under the run dir, and prints a markdown summary table
#   (mean +/- stdev per scenario per build + delta% B vs A) at the end.
#
# NOTE: functional harness only. Real measurement runs are done serialized by the
# coordinator; the numbers from a shared/loaded box are not measurement-grade.
#
# LIFECYCLE (process-tree ownership)
#   Each unitd is spawned via `setsid` into its OWN session/process group, so the
#   whole family (main + controller + router + app workers) can be signalled as a
#   unit and can never be confused with the harness's own group or with unrelated
#   unitd daemons on a shared box. The real main pid is read back from the pidfile
#   (--pid <run>/unit.pid), NOT from $!, and the group id is written to
#   <run>/unitd.pgid.
#   Teardown is an escalation ladder keyed on the PGID, not the main pid: TERM the
#   group, poll ~10s for it to empty, then KILL the group (loudly). This reaps the
#   router/controller even when the main pid is already dead but children survive.
#   Concurrency: each round holds an exclusive flock on "<run>.lock" for its
#   whole lifetime. Two invocations sharing RUNBASE+labels compute the SAME
#   deterministic run dir, so the pre-run sweep must never mistake a live
#   sibling for a crash: it only runs once we hold the lock, and if a live
#   invocation already holds it we skip the round instead of sweeping/deleting
#   its tree. The kernel drops the lock when a process dies, so a CRASHED run
#   leaves the file with a FREE lock — still reapable by the next invocation or
#   an external sweeper.
#   Sweeps are FINGERPRINTED to run dirs: a pre-run sweep reaps a prior crashed
#   instance only if its pgid file lives in THIS run dir or a process cmdline
#   references THIS run dir — never a bare `unitd` pattern — and every kill
#   target must additionally have a unitd process image (/proc/<pid>/exe), so a
#   mere observer of the run files can never be signalled. Ports held by anything
#   else are still caught by the port_busy abort. A post-run leak assert scans
#   the <run>/unitd.pgid files of THIS invocation's run dirs only (a concurrent
#   invocation sharing RUNBASE owns its own groups), kills any survivor still
#   FINGERPRINTED to its run dir via the ladder — including orphaned children
#   whose argv unit rewrote to 'unit: <name>', recognized by unitd image +
#   session id == recorded pgid WITH a vacant/zombie session-leader slot (a live
#   pid==pgid means the id was recycled, so it is rejected) — and fails the run.
#   pgid/lock files are intentionally left on disk after teardown so an external
#   sweeper can reap orphans from a hard-killed harness.

set -u

# Force C locale: printf %f and float parsing must use '.' decimals, not the
# locale's ',' (otherwise result lines and the summarizer break).
export LC_ALL=C

# ---- args -------------------------------------------------------------------
ALLOW_SAME=${ALLOW_SAME:-0}
SKIP_AB=${SKIP_AB:-0}
# Positional arguments go into POS_0, POS_1, ...; NPOS counts them.
NPOS=0
for a in "$@"; do
    case "$a" in
        --allow-same) ALLOW_SAME=1 ;;
        --skip-ab)    SKIP_AB=1 ;;
        --*)          printf '%s\n' "unknown flag: $a" >&2; exit 2 ;;
        *)            eval "POS_$NPOS=\$a"; NPOS=$((NPOS + 1)) ;;   # NPOS is a number
    esac
done

if [ "$NPOS" -lt 4 ]; then
    echo "usage: bench-engines.sh <labelA> <builddirA> <labelB> <builddirB> [rounds]" >&2
    exit 2
fi

LABEL_A=$POS_0; BUILD_A=$POS_1
LABEL_B=$POS_2; BUILD_B=$POS_3
ROUNDS=${POS_4:-4}

# Labels become whitespace-delimited result-row fields; whitespace or glob chars
# silently break the summarizer (rows skipped, run still exits 0). Reject early.
for l in "$LABEL_A" "$LABEL_B"; do
    case "$l" in
        *[!A-Za-z0-9._-]*|'') printf '%s\n' "invalid label '$l' (allowed: A-Za-z0-9._-)" >&2; exit 2 ;;
    esac
done

# rounds feeds seq and arithmetic below; a bad value would silently run zero
# rounds (empty results, exit 0) instead of erroring.
case "$ROUNDS" in
    ''|*[!0-9]*) printf '%s\n' "rounds must be a positive integer, got '$ROUNDS'" >&2; exit 2 ;;
esac
[ "$ROUNDS" -gt 0 ] || { printf '%s\n' "rounds must be a positive integer, got '$ROUNDS'" >&2; exit 2; }
# An odd count leaves the A-first/B-first positions unbalanced (e.g. 5 rounds =
# A first 3x, B first 2x), so a repeatable position effect stays correlated
# with build identity. Allow it (quick smoke tests use 1), but say so.
[ $((ROUNDS % 2)) = 0 ] || \
    printf '%s\n' "NOTE: odd rounds=$ROUNDS leaves A/B start positions unbalanced; use an even count for measurement runs"

for b in "$BUILD_A" "$BUILD_B"; do
    [ -x "$b/sbin/unitd" ] || { printf '%s\n' "no executable $b/sbin/unitd" >&2; exit 2; }
done
[ "$LABEL_A" != "$LABEL_B" ] || { printf '%s\n' "labels must differ ($LABEL_A)" >&2; exit 2; }

# ---- config -----------------------------------------------------------------
# Keep RUNBASE short: the control socket path must fit AF_UNIX sun_path (~108
# bytes); deep scratch paths overflow it. Default to a shallow /tmp dir, so
# HARNESS_SCRATCH is not derived from TMPDIR here.
RUNBASE=${RUNBASE:-${HARNESS_SCRATCH:-/tmp/freeunit-harness}/bench-engines}
RESULTS=${RESULTS:-$PWD/bench-engines-results.txt}
PHPAPP=${PHPAPP:-}
# An explicit PHPAPP is caller-owned: require it to be usable NOW and never
# write into it (ensure_phpapp auto-creates an app only when PHPAPP is unset).
if [ -n "$PHPAPP" ] && [ ! -f "$PHPAPP/index.php" ]; then
    printf '%s\n' "PHPAPP=$PHPAPP has no index.php" >&2
    exit 2
fi

# Distinct port slots per build so an A and B never collide.
PORT_A=8910; PORT_A_PROXY=8911; PORT_A_PHP=8912
PORT_B=8920; PORT_B_PROXY=8921; PORT_B_PHP=8922

# Per-request timeout for every oha invocation (measurements AND warmups). oha's
# -t defaults to INFINITE, so a build that accepts a connection then stalls would
# hang the harness forever. A timed-out request is counted as an error (feeds
# `failed`), so a real stall surfaces as a failed measurement instead of a hang.
# Override via env; use a humantime duration (e.g. 5s, 500ms).
OHA_TIMEOUT=${OHA_TIMEOUT:-30s}

# Transfer timeout (seconds) for the control-socket PUTs in put_config_*. A wedged
# controller would otherwise block the script indefinitely before it can fail the
# round and clean up. Override via env.
CURL_MAXTIME=${CURL_MAXTIME:-30}

# Request counts. -n (not -z) for reproducibility. Scaled so each measurement is
# ~10-30s on a real run; override via env for the smoke test.
N_HIGH=${N_HIGH:-200000}
N_LOW=${N_LOW:-20000}
N_CLOSE=${N_CLOSE:-20000}
N_PROXY=${N_PROXY:-50000}
N_PHP=${N_PHP:-50000}

# Max failed requests a measurement may record before it fails the round/run.
# The summary tables don't surface `failed`, so without this an all-non-2xx run
# looks green as long as the tool exits 0.
MAX_FAILED=${MAX_FAILED:-0}
# A non-numeric MAX_FAILED makes the `-gt` comparison in oha_one/ab_one print an
# error and evaluate false, silently accepting a measurement that DID record
# failures. Require a non-negative integer up front, like rounds above.
case "$MAX_FAILED" in
    ''|*[!0-9]*) printf '%s\n' "MAX_FAILED must be a non-negative integer, got '$MAX_FAILED'" >&2; exit 2 ;;
esac

# CPU partition: server tree vs load generators.  The defaults encode the
# reference-box methodology (8 cores, 0-3/4-7); override via env on other
# hosts.  Validate up front: on a smaller box or a cpuset-limited runner an
# invalid list would otherwise fail every taskset invocation mid-run (or
# silently record empty measurements).
SERVER_CORES=${SERVER_CORES:-0-3}
LOAD_CORES=${LOAD_CORES:-4-7}
# taskset is not a busybox applet; on Alpine it is in util-linux-misc.
command -v taskset >/dev/null 2>&1 \
    || { echo "taskset not found (util-linux; util-linux-misc on Alpine)" >&2; exit 2; }
for cores in "$SERVER_CORES" "$LOAD_CORES"; do
    if ! taskset -c "$cores" true 2>/dev/null; then
        printf '%s\n' "cannot set CPU affinity '$cores' on this host (set SERVER_CORES/LOAD_CORES to available CPUs)" >&2
        exit 2
    fi
done
# The two masks must also be DISJOINT: any shared CPU makes the load generators
# contend directly with the measured server tree, silently invalidating both
# the RPS and the CPU-per-request comparison the partition exists to protect.
expand_cores() {  # taskset CPU list -> one cpu id per line
    # Handles every taskset list form: single "N", range "N-M", and strided
    # range "N-M:S" (taskset(1): 0-31:2 = every 2nd cpu). Missing the stride
    # form made a strided mask expand to NOTHING, so the disjoint check silently
    # passed two overlapping strided masks.
    printf '%s\n' "$1" | tr ',' '\n' | while IFS= read -r ec_part; do
        [ -n "$ec_part" ] || continue
        ec_stride=1; ec_range=$ec_part
        case "$ec_part" in *:*) ec_stride=${ec_part##*:}; ec_range=${ec_part%:*} ;; esac
        ec_lo=${ec_range%%-*}; ec_hi=${ec_range##*-}
        case "$ec_lo$ec_hi$ec_stride" in ''|*[!0-9]*) continue ;; esac
        [ "$ec_stride" -ge 1 ] || continue
        seq "$ec_lo" "$ec_stride" "$ec_hi"
    done
}
# comm reads the server list from fd 3 (a here-document) and the load list
# from stdin; this replaces bash's two <( ) process substitutions.
CORE_OVERLAP=$(expand_cores "$LOAD_CORES" | sort -u | comm -12 /dev/fd/3 - 3<<EOF | tr '\n' ' '
$(expand_cores "$SERVER_CORES" | sort -u)
EOF
)
if [ -n "${CORE_OVERLAP% }" ]; then
    printf '%s\n' "SERVER_CORES ($SERVER_CORES) and LOAD_CORES ($LOAD_CORES) overlap on CPU(s) ${CORE_OVERLAP% }; the masks must be disjoint" >&2
    exit 2
fi

mkdir -p "$RUNBASE"
# fail fast: an unwritable results file would otherwise only surface after
# the first measurement (or lose every tee -a silently under set +e)
: > "$RESULTS" || { printf '%s\n' "cannot write results file $RESULTS" >&2; exit 2; }

# ---- tool discovery ---------------------------------------------------------
HAVE_JQ=0; command -v jq >/dev/null 2>&1 && HAVE_JQ=1
HAVE_PY=0; command -v python3 >/dev/null 2>&1 && HAVE_PY=1
HAVE_AB=0; command -v ab >/dev/null 2>&1 && HAVE_AB=1
OHA=${OHA:-$(command -v oha 2>/dev/null || true)}
[ -z "$OHA" ] || command -v "$OHA" >/dev/null 2>&1 || OHA=$(command -v oha 2>/dev/null || true)
# Not on PATH: on the build boxes oha lives in the cargo
# bin dir, which CARGO_HOME may point off $HOME (see run/servers.md).
[ -n "$OHA" ] || { c="${CARGO_HOME:-$HOME/.cargo}/bin/oha"; [ -x "$c" ] && OHA=$c; }
[ -n "$OHA" ] || { echo "oha not found" >&2; exit 2; }
if [ "$HAVE_JQ" = 0 ] && [ "$HAVE_PY" = 0 ]; then
    echo "need jq or python3 to parse oha JSON" >&2; exit 2
fi
# Don't silently halve the matrix: a missing ab must abort unless the caller
# explicitly opted out via --skip-ab/SKIP_AB=1.
[ "$SKIP_AB" = 1 ] || [ "$HAVE_AB" = 1 ] || { echo "ab not found (use --skip-ab)" >&2; exit 2; }
# Every group-liveness and ownership check reads /proc/<pid>/stat, not ps or
# pgrep: busybox has no "ps -p" and its pgrep has no -g. Without /proc,
# group_alive() would report every live group as empty, so the kill ladder
# would never signal, the leak scan would see no survivors, and the whole
# unitd tree could be left running with a green exit. Require /proc, flock
# (per-run locking), setsid (own process group) and getconf (CLK_TCK) up
# front. busybox provides flock and setsid; getconf is in musl-utils on Alpine.
[ -r /proc/self/stat ] \
    || { echo "/proc is not mounted (needed for process-group cleanup)" >&2; exit 2; }
for t in flock setsid getconf; do
    command -v "$t" >/dev/null 2>&1 \
        || { printf '%s\n' "$t not found (needed for per-run locking, process-group cleanup and CPU accounting)" >&2; exit 2; }
done
# Clock ticks per second, for the CPU accounting; after the check that names a missing getconf.
CLK=$(getconf CLK_TCK)

# ---- state ------------------------------------------------------------------
UNITD_PID=""        # unitd main pid (read back from the pidfile, for child_pids)
PGID=""             # process-group id of the current unitd session (kill target)
RUN_CUR=""          # run dir of the currently-starting instance (orphan recovery)
LOAD_PID=""         # active load-generator pid (oha/ab run backgrounded + wait)
RUN_DIRS_N=0        # run dirs THIS invocation created, in RUN_DIR_1.. (scopes the leak scan)
RUN_LOCK_FD=""      # fd holding this round's per-run flock (live-vs-crashed marker); always 9
ROUTER=""
# Engine name per label: ENGINE_A/ENGINE_B for LABEL_A/LABEL_B, and the
# preflight-asserted EXPECT_A/EXPECT_B. Labels are not shell names, so the
# two maps are a slot per label, read and written through these helpers.
ENGINE_A=""; ENGINE_B=""; EXPECT_A=""; EXPECT_B=""
label_slot() {  # label -> A or B
    if [ "$1" = "$LABEL_A" ]; then printf A; else printf B; fi
}
engine_of() { eval "printf '%s' \"\$ENGINE_$(label_slot "$1")\""; }
expect_of() { eval "printf '%s' \"\$EXPECT_$(label_slot "$1")\""; }
TAB=$(printf '\t')

proc_listening() {  # port -> 0 if a TCP socket listens on it (no ss)
    # bash's /dev/tcp connect probe does not exist in POSIX sh. Read the
    # kernel's socket tables instead: state 0A is LISTEN, the local port is
    # the hex field after the colon of column 2.
    pl_files=""
    for pl_f in /proc/net/tcp /proc/net/tcp6; do
        [ -r "$pl_f" ] && pl_files="$pl_files $pl_f"
    done
    [ -n "$pl_files" ] || return 1
    # shellcheck disable=SC2086  # pl_files holds only the two fixed paths
    awk -v p="$(printf '%04X' "$1")" \
        '$4 == "0A" && substr($2, index($2, ":") + 1) == p { f = 1 } END { exit !f }' $pl_files
}

port_busy() {  # port -> 0 if in use
    if command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN
    else
        proc_listening "$1"
    fi
}

wait_port() {  # host-port timeout_s -> 0 when listening
    wp_p=$1 wp_timeout=${2:-10} wp_i=0
    while [ "$wp_i" -lt $((wp_timeout*10)) ]; do
        if command -v ss >/dev/null 2>&1; then
            ss -ltn "sport = :$wp_p" 2>/dev/null | grep -q LISTEN && return 0
        else
            proc_listening "$wp_p" && return 0
        fi
        wp_i=$((wp_i+1)); sleep 0.1
    done
    return 1
}

# Process ids and states come from /proc/<pid>/stat, not from ps or pgrep:
# busybox has no "ps -p" and its pgrep has no -g, so on Alpine without procps
# they print nothing. The command name in field 2 can hold spaces, ")" and even
# a newline (prctl PR_SET_NAME), so a stat file is not always one line.
#
# proc_records FILE...: one line "pid state ppid pgrp session" per stat FILE.
# cat reads every file in one go and skips a file whose process has exited.
# awk joins the lines of a record until the text after its LAST ") " holds the
# remaining fields (about 50; a command name holds at most 15 bytes, so a
# fragment of it never does), then prints the fields after that ") ". A zombie
# still has a stat file, so it is listed with state Z. One scan costs one cat
# and one awk, not one read(2) per byte as the read builtin of dash and busybox
# ash does (about 150 ms per scan with 570 processes under dash).
proc_records() {
    cat "$@" 2>/dev/null | awk '
        { rec = (rec == "") ? $0 : rec "\n" $0
          n = split(rec, part, /[)] /)
          if (n < 2 || split(part[n], f, " ") < 20) next
          print substr(rec, 1, index(rec, " ") - 1), f[1], f[2], f[3], f[4]
          rec = "" }'
}

proc_stat_field() {  # pid n -> field n of proc_records (2 = state, 3 = ppid, 4 = pgrp, 5 = session); 1 if gone
    # Only a pid: "/proc//stat" would be /proc/stat, the CPU counters.
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    pf_n=$2
    pf_rec=$(proc_records "/proc/$1/stat")
    # shellcheck disable=SC2086  # split into pid, state, ppid, pgrp, session
    set -- $pf_rec
    case "$pf_n" in
        2) pf_v=${2:-} ;;
        3) pf_v=${3:-} ;;
        4) pf_v=${4:-} ;;
        5) pf_v=${5:-} ;;
        *) return 1 ;;
    esac
    [ -n "$pf_v" ] || return 1
    # Every field but the state is a number.
    [ "$pf_n" = 2 ] || case "$pf_v" in *[!0-9]*) return 1 ;; esac
    printf '%s\n' "$pf_v"
}
proc_state() { proc_stat_field "$1" 2; }  # pid -> state letter (R/S/D/T/Z/...); empty if gone
proc_pgid()  { proc_stat_field "$1" 4; }  # pid -> its process group (stat field 5)
proc_sid()   { proc_stat_field "$1" 5; }  # pid -> its session (stat field 6)

# group_pids PGID: the pids in group PGID, one per line, zombies included
# (as "pgrep -g PGID"). A process that exits during the scan is skipped.
group_pids() {
    proc_records /proc/[0-9]*/stat | awk -v g="$1" '$4 == g { print $1 }'
}

# child_pids PID...: the pids whose parent is one of PID..., one per line, in
# ascending order (as "pgrep -P"). No PID prints nothing.
child_pids() {
    [ "$#" -gt 0 ] || return 0
    proc_records /proc/[0-9]*/stat \
        | awk -v want=" $* " 'index(want, " " $3 " ") { print $1 }' | sort -n
}

# cmdline_has PID TEXT: 0 if one argument in the command line of PID holds
# TEXT as a fixed string. The NULs between the arguments become newlines
# first: the busybox grep of Ubuntu stops reading a line at its first NUL, so
# "grep -aF" on the raw file saw only argv[0] there.
cmdline_has() {
    # Only a pid: "/proc//cmdline" would be /proc/cmdline, the kernel one.
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    { tr '\0' '\n' < "/proc/$1/cmdline"; } 2>/dev/null | grep -qF -- "$2"
}

# unitd_pids: the pids whose process image is a unitd binary, as
# proc_is_unitd tests it, from one "ls -l" of the exe links (as my_pids in
# tools/perf-lab/scaling/lib.sh). A link that this user cannot read, or of a
# process that exits during the listing, is skipped.
unitd_pids() {
    ls -l /proc/[0-9]*/exe 2>/dev/null | awk '{
        i = index($0, " -> "); if (!i) next
        t = substr($0, i + 4)
        if (t !~ /\/unitd$/ && t !~ /\/unitd \(deleted\)$/) next
        s = substr($0, 1, i - 1); sub(/.*\/proc\//, "", s); sub(/\/exe$/, "", s); print s }'
}

reap_child() {  # non-blocking reap of our own group leader if it became a zombie
    # The setsid'd unitd is still our direct child, so after we kill it it lingers
    # as a zombie until reaped — and a zombie keeps group_pids listing the group as
    # non-empty. Reap it ONLY when it is gone or Z, never wait on a live/stopped
    # process (that would block the ladder, defeating the timeout budget).
    [ -n "$UNITD_PID" ] || return 0
    rc_st=$(proc_state "$UNITD_PID")
    if [ -z "$rc_st" ] || [ "$rc_st" = Z ]; then
        wait "$UNITD_PID" 2>/dev/null || true
    fi
    return 0
}

group_alive() {  # pgid -> 0 if any NON-zombie process remains in the group
    ga_pgid=$1
    for ga_p in $(group_pids "$ga_pgid"); do
        ga_st=$(proc_state "$ga_p")
        [ "$ga_st" = Z ] && continue   # a zombie is already dead; it holds nothing
        [ -z "$ga_st" ] && continue    # vanished between the listing and the read
        return 0
    done
    return 1
}

proc_is_unitd() {  # pid -> 0 if the process image is a unitd binary
    # Sweeps and ownership tests must only ever treat REAL unitd processes as
    # evidence/targets: a cmdline match alone also hits innocent observers —
    # e.g. `tail -f <run>/unitd.pgid` contains both "unitd" and the run path.
    # /proc/<pid>/exe names the true image regardless of unit's argv rewriting;
    # tolerate the "(deleted)" suffix from a rebuilt build dir.
    pu_exe=$(readlink "/proc/$1/exe" 2>/dev/null) || return 1
    case "$pu_exe" in
        */unitd|*/unitd\ \(deleted\)) return 0 ;;
    esac
    return 1
}

group_owns_run() {  # pgid run -> 0 if the group is provably OUR unitd tree
    # A numeric pgid read from a *.pgid file can name a group whose id was recycled
    # by an UNRELATED process after our clean teardown (pgid files are left on disk
    # on purpose, so a later run still finds them). Signalling on group_alive()
    # alone would then kill a stranger's group and report a false leak. Ownership
    # therefore needs BOTH a unitd process image and a tie to THIS run:
    #  - the main process keeps the run dir visible: unit rewrites its title to
    #    'unit: main vX [<full original argv>]' (src/nxt_main_process.c), so
    #    --pid <run>/unit.pid etc. stay in /proc cmdline;
    #  - an ORPHANED child (main died first) does not: unit rewrites child argv
    #    to 'unit: <router|controller|app>' (src/nxt_process.c) with no run-dir
    #    reference. But the child kept the session created by our setsid spawn,
    #    so SID == PGID == main pid. Accepting that alone is unsafe: the numeric
    #    pgid can be reused as the pid of an UNRELATED setsid'd unitd (also
    #    SID==PGID). Distinguish by the group/session-LEADER slot (pid == pgid):
    #    the kernel keeps a pgid reserved while the group is non-empty, so the
    #    number cannot become a new pid until our last member exits. A LIVE
    #    process at pid==pgid therefore means the id was recycled (reject); a
    #    vacant or zombie leader with surviving members is our crashed tree.
    go_pgid=$1 go_run=$2
    [ -n "$go_run" ] || return 1
    for go_p in $(group_pids "$go_pgid"); do
        proc_is_unitd "$go_p" || continue
        cmdline_has "$go_p" "$go_run" && return 0
        go_sid=$(proc_sid "$go_p") || continue     # vanished since the listing
        if [ "$go_sid" = "$go_pgid" ]; then
            go_lst=$(proc_state "$go_pgid")            # state of the session-leader pid
            { [ -z "$go_lst" ] || [ "$go_lst" = Z ]; } && return 0
        fi
    done
    return 1
}

kill_pgid() {  # pgid -> TERM/poll/KILL escalation ladder, scoped to that group
    kp_pgid=$1
    # Guard hard: never `kill -- -` with an empty/invalid group, and never a
    # low/reserved id. The setsid session id equals the leader pid, so this can
    # never be the harness's own group.
    case "$kp_pgid" in ''|*[!0-9]*) return 0 ;; esac
    [ "$kp_pgid" -gt 1 ] || return 0
    reap_child
    group_alive "$kp_pgid" || return 0
    # No "--": the kill builtin of dash and busybox ash rejects it. The pgid is
    # all digits and above 1 (checked above), so "-$kp_pgid" is a group.
    kill -TERM "-$kp_pgid" 2>/dev/null
    kp_i=0
    while [ "$kp_i" -lt 50 ]; do                 # ~10s at 0.2s steps, plus one /proc scan per step
        reap_child
        group_alive "$kp_pgid" || return 0
        kp_i=$((kp_i+1)); sleep 0.2
    done
    printf '%s\n' "cleanup: process group $kp_pgid survived TERM after ~10s; sending KILL" >&2
    kill -KILL "-$kp_pgid" 2>/dev/null
    kp_j=0
    while [ "$kp_j" -lt 25 ]; do                 # ~5s for the KILL to settle
        reap_child
        group_alive "$kp_pgid" || return 0
        kp_j=$((kp_j+1)); sleep 0.2
    done
    group_alive "$kp_pgid" \
        && printf '%s\n' "cleanup: process group $kp_pgid STILL has survivors after KILL" >&2
    return 0
}

resolve_pgid_from_run() {  # run -> sets UNITD_PID/PGID from pidfile, writes pgid
    rp_run=$1
    [ -n "$rp_run" ] || return 1
    [ -s "$rp_run/unit.pid" ] || return 1
    rp_main=$(tr -d ' \t\r\n' < "$rp_run/unit.pid" 2>/dev/null)
    case "$rp_main" in ''|*[!0-9]*) return 1 ;; esac
    rp_pgid=$(proc_pgid "$rp_main")
    case "$rp_pgid" in ''|*[!0-9]*) return 1 ;; esac
    UNITD_PID=$rp_main
    PGID=$rp_pgid
    printf '%s\n' "$rp_pgid" > "$rp_run/unitd.pgid" 2>/dev/null || true
    return 0
}

cleanup() {
    # First stop any load generator we may be mid-`wait` on: bash defers
    # INT/TERM traps while a FOREGROUND child runs, so oha/ab are backgrounded
    # and wait'ed (wait is interruptible). Without this kill, a signalled
    # harness would tear the server down while the generator keeps hammering
    # the dead ports for up to a full measurement of timeout waves.
    if [ -n "$LOAD_PID" ] && kill -0 "$LOAD_PID" 2>/dev/null; then
        kill "$LOAD_PID" 2>/dev/null
        wait "$LOAD_PID" 2>/dev/null
    fi
    LOAD_PID=""
    # Recover a pgid from the current run's pidfile if start_unitd failed before
    # recording one, so a half-started (orphaned) tree is still reaped. A stale
    # pidfile (KILL-escalated or crashed unitd never unlinks it) can name a
    # RECYCLED pid though, so never signal a group resolved this way without
    # proof it still owns the run dir.
    if [ -z "$PGID" ] && [ -n "$RUN_CUR" ]; then
        resolve_pgid_from_run "$RUN_CUR" 2>/dev/null || true
        if [ -n "$PGID" ] && ! group_owns_run "$PGID" "$RUN_CUR"; then
            UNITD_PID=""
            PGID=""
        fi
    fi
    if [ -n "$PGID" ]; then
        kill_pgid "$PGID"
    elif [ -n "$UNITD_PID" ] && kill -0 "$UNITD_PID" 2>/dev/null; then
        kill "$UNITD_PID" 2>/dev/null
    fi
    reap_child
    UNITD_PID=""
    PGID=""
    # This run is dealt with: a LATER cleanup invocation (the EXIT trap fires
    # after INT/TERM, or after the last round) must not re-resolve the same
    # pidfile into a possibly-recycled pid and signal a stranger's group.
    RUN_CUR=""
    # Release this round's run-dir lock (closing the fd drops the flock) so a
    # later invocation can reuse the dir and its own sweep sees the lock free.
    if [ -n "$RUN_LOCK_FD" ]; then
        { exec 9>&-; } 2>/dev/null
        RUN_LOCK_FD=""
    fi
}
# INT/TERM must EXIT, not just clean up: after the handler returns bash would
# otherwise resume the script into later measurements with unitd already killed.
# cleanup is idempotent (clears UNITD_PID/PGID), so the subsequent EXIT trap is a
# harmless no-op.
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
# bash also runs the EXIT trap when HUP ends it; dash and busybox ash do not.
# Exit through the trap for HUP too. QUIT stays as it is, as in exit_traps of
# tools/perf-lab/scaling/lib.sh: bash ignores it, and an ignored QUIT would
# pass to unitd.
trap 'cleanup; exit 129' HUP

acquire_run_lock() {  # run -> 0 if we now hold the exclusive lock; 1 if a LIVE invocation holds it
    # Per-run exclusive flock, SIBLING to the run dir (claim_run_dir rm -rf's the
    # dir itself, which would drop an in-dir lock and defeat the mutex). A live
    # invocation holds this fd for the whole round; the kernel frees the lock
    # when that process dies, so a CRASHED run leaves the file with a FREE lock
    # (still reapable). This gates sweep_stale_run: we only ever sweep/claim a
    # dir we could lock, so we can never TERM a concurrent invocation's live
    # tree or rm -rf its live run dir (two invocations sharing RUNBASE + labels
    # otherwise compute the same deterministic run dir).
    al_lock="$1.lock"
    mkdir -p "$(dirname "$al_lock")" 2>/dev/null || true
    # A fixed fd 9 replaces bash's {RUN_LOCK_FD}: one lock is held at a time,
    # and cleanup closes it before the next round takes it. "command" keeps a
    # failed open from ending the shell, as a failed redirection on the exec
    # special builtin does in POSIX sh. The 2>/dev/null is on the compound
    # command, so it hides only the open or close error and does not leave
    # the shell's stderr pointing at /dev/null.
    { command exec 9>"$al_lock"; } 2>/dev/null || { RUN_LOCK_FD=""; return 1; }
    RUN_LOCK_FD=9
    if ! flock -n 9; then
        { exec 9>&-; } 2>/dev/null
        RUN_LOCK_FD=""
        return 1
    fi
    return 0
}

sweep_stale_run() {  # run -> reap a prior crashed instance tied to THIS run dir
    ss_run=$1
    # Defensive: an empty run would turn the pattern kill into a bare `unitd`
    # match against every daemon on the box.
    [ -n "$ss_run" ] || return 0
    ss_swept=0

    # 1) pgid file left behind by a previous (crashed/hard-killed) run
    if [ -f "$ss_run/unitd.pgid" ]; then
        ss_oldpgid=$(tr -d ' \t\r\n' < "$ss_run/unitd.pgid" 2>/dev/null)
        case "$ss_oldpgid" in
            ''|*[!0-9]*) ;;
            *)
                if group_alive "$ss_oldpgid" && group_owns_run "$ss_oldpgid" "$ss_run"; then
                    printf '%s\n' "sweep: reaping stale process group $ss_oldpgid from prior run $ss_run" >&2
                    kill_pgid "$ss_oldpgid"
                    ss_swept=1
                fi
                ;;
        esac
    fi

    # 2) processes whose cmdline still references THIS run dir (pgid file lost).
    #    Enumerate unitd candidates with a FIXED-string cmdline confirm, never a
    #    regex on $run: labels permit '.' and RUNBASE is caller-supplied, so
    #    feeding the run path to a regex (as `pgrep -f` takes) could match an unrelated
    #    run (e.g. 'A.B-r1' matches 'AxB-r1') and this branch would kill a
    #    stranger's group WITHOUT the group_owns_run check — or a stray metachar
    #    in RUNBASE could break the pattern and miss a real leak. Confirm each
    #    candidate literally, exactly the way group_owns_run does, AND require a
    #    unitd process image: the cmdline of an innocent observer can contain
    #    both "unitd" and the run path (e.g. `tail -f <run>/unitd.pgid`), and
    #    signalling its group would kill an unrelated process tree.
    ss_pids=""
    # The candidates are the unitd images whose command line holds "unitd", as
    # "pgrep -f unitd" with the image check gave them.
    for ss_p in $(unitd_pids); do
        proc_is_unitd "$ss_p" || continue
        cmdline_has "$ss_p" unitd || continue
        cmdline_has "$ss_p" "$ss_run" && ss_pids="$ss_pids $ss_p"
    done
    ss_pids=${ss_pids# }
    if [ -n "$ss_pids" ]; then
        printf '%s\n' "sweep: reaping stale unitd process(es) [$ss_pids] matching run dir $ss_run" >&2
        for ss_p in $ss_pids; do
            ss_g=$(proc_pgid "$ss_p")
            case "$ss_g" in
                ''|*[!0-9]*) kill -TERM "$ss_p" 2>/dev/null ;;
                *)           kill_pgid "$ss_g" ;;
            esac
        done
        ss_swept=1
    fi
    [ "$ss_swept" = 0 ] || printf '%s\n' "sweep: run dir $ss_run cleared" >&2
    return 0
}

router_cpu_ticks() {  # utime+stime of the router process; comm has a space
    # Default to 0 if the process vanished between checks: an empty value
    # would blow up the callers' $((...)) arithmetic.
    rt_ticks=$(sed 's/.*) //' "/proc/$ROUTER/stat" 2>/dev/null | awk '{print $12+$13}')
    printf '%s\n' "${rt_ticks:-0}"
}

tree_cpu_ticks() {  # unitd main + all descendants (router + app workers)
    # utime+stime of each live member PLUS its cutime+cstime: an app worker
    # that exits mid-measurement is reaped by its parent, which folds the dead
    # child's ticks into the parent's cutime/cstime. Without those fields a
    # worker exit (or churn/replacement) removes its CPU from the t1 snapshot
    # and the interval delta understates — or even goes negative. Live children
    # are never in a parent's cutime, so nothing is double-counted.
    tc_total=0
    tc_kids=$(child_pids "$UNITD_PID")
    # shellcheck disable=SC2086  # tc_kids holds pids, one per line
    for tc_pid in $UNITD_PID $tc_kids $(child_pids $tc_kids); do
        [ -r "/proc/$tc_pid/stat" ] || continue
        tc_ticks=$(sed 's/.*) //' "/proc/$tc_pid/stat" 2>/dev/null | awk '{print $12+$13+$14+$15}')
        tc_total=$((tc_total + ${tc_ticks:-0}))
    done
    printf '%s\n' "$tc_total"
}

# ---- oha JSON parsing -------------------------------------------------------
# oha --no-tui --output-format json emits: .summary.requestsPerSec, .summary.successRate,
# .latencyPercentiles.p50 / .p99 (seconds), .statusCodeDistribution.
# failed counts every non-2xx status (3xx included) plus transport errors, so
# it matches ab's "Non-2xx responses" accounting below.
# The REQUIRED fields (requestsPerSec, p50, p99, statusCodeDistribution) must be
# present and numeric/object: a valid-JSON document without them (e.g. after an
# oha output-schema change) prints NOTHING, so the caller's empty-rps check
# fails the measurement instead of recording an all-zero row.
parse_oha() {  # jsonfile -> "rps p50ms p99ms failed"
    po_f=$1
    if [ "$HAVE_JQ" = 1 ]; then
        jq -r '
          def ms(x): (x*1000);
          if   (.summary.requestsPerSec  | type) != "number"
            or (.latencyPercentiles."p50" | type) != "number"
            or (.latencyPercentiles."p99" | type) != "number"
            or (.statusCodeDistribution   | type) != "object"
          then empty
          else
          [ .summary.requestsPerSec,
            (ms(.latencyPercentiles."p50")),
            (ms(.latencyPercentiles."p99")),
            ( (( [ .statusCodeDistribution | to_entries[]
                   | select((.key|tonumber) >= 300 or (.key|tonumber) < 200) | .value ]
                 | add) // 0)
              + (( [ .errorDistribution // {} | to_entries[] | .value ] | add) // 0) )
          ] | @tsv
          end' "$po_f"
    else
        python3 - "$po_f" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
s=d.get("summary",{})
lp=d.get("latencyPercentiles",{})
rps=s.get("requestsPerSec")
p50=lp.get("p50")
p99=lp.get("p99")
scd=d.get("statusCodeDistribution")
def num(x): return isinstance(x,(int,float)) and not isinstance(x,bool)
if not (num(rps) and num(p50) and num(p99) and isinstance(scd,dict)):
    sys.exit(0)   # print nothing: required fields missing -> caller fails
failed=sum(v for k,v in scd.items() if str(k).isdigit() and (int(k)>=300 or int(k)<200))
failed+=sum((d.get("errorDistribution",{}) or {}).values())
print(f"{rps}\t{p50*1000}\t{p99*1000}\t{failed}")
PY
    fi
}

# ---- config payloads --------------------------------------------------------
json_string() {  # value -> JSON string literal (with surrounding quotes)
    # The php app root comes from PHPAPP or a RUNBASE-derived path; a '"' or '\'
    # (or any control char) in it would break the raw-interpolated control-socket
    # payload. Encode via the JSON tool we already require (jq or python3).
    if [ "$HAVE_JQ" = 1 ]; then
        jq -n --arg s "$1" '$s'
    else
        python3 -c 'import json,sys; sys.stdout.write(json.dumps(sys.argv[1]))' "$1"
    fi
}

put_config_http() {  # runsock port proxyport -> writes conf result, returns rc
    ph_sock=$1 ph_port=$2 ph_pport=$3 ph_out=$4
    curl -s --max-time "$CURL_MAXTIME" --unix-socket "$ph_sock" -X PUT -d '{
      "listeners": {
        "*:'"$ph_port"'":  {"pass": "routes/direct"},
        "*:'"$ph_pport"'": {"pass": "routes/proxy"}
      },
      "routes": {
        "direct": [{"action": {"return": 200}}],
        "proxy":  [{"action": {"proxy": "http://127.0.0.1:'"$ph_port"'"}}]
      }
    }' http://localhost/config > "$ph_out"
    grep -q '"success"' "$ph_out"
}

put_config_php() {  # runsock port phproot -> writes conf result, returns rc
    pp_sock=$1 pp_port=$2 pp_root=$3 pp_out=$4
    pp_rootjson=$(json_string "$pp_root")
    curl -s --max-time "$CURL_MAXTIME" --unix-socket "$pp_sock" -X PUT -d '{
      "listeners": {"*:'"$pp_port"'": {"pass": "applications/hello"}},
      "applications": {"hello": {
          "type": "php", "root": '"$pp_rootjson"', "script": "index.php",
          "processes": 2
      }}
    }' http://localhost/config > "$pp_out"
    grep -q '"success"' "$pp_out"
}

# ---- engine assertion -------------------------------------------------------
detect_engine() {  # rundir -> engine name (or "unknown")
    de_run=$1
    # implementation branch logs:  using "epoll" event engine
    # The main process emits it on stderr (before the log file opens); router
    # workers emit it into unit.log later. Check both.
    de_name=$(cat "$de_run/stderr.log" "$de_run/unit.log" 2>/dev/null \
           | grep -oE 'using "[^"]+" event engine' | head -1 \
           | sed -E 's/using "([^"]+)".*/\1/')
    if [ -n "$de_name" ]; then printf '%s\n' "$de_name"; else echo "unknown"; fi
}

# ---- lifecycle --------------------------------------------------------------
claim_run_dir() {  # run -> (re)create run dir; refuse to delete a foreign one
    # RUNBASE is caller-supplied, so "$RUNBASE/<label>-r<n>" can collide with a
    # pre-existing unrelated path; blindly rm -rf'ing it would destroy data the
    # harness does not own. Only delete a directory that a bench-engines run
    # created earlier, proven by its marker file (or an empty/absent path).
    cr_run=$1
    if [ -e "$cr_run" ] && [ ! -f "$cr_run/.bench-engines-run" ]; then
        printf '%s\n' "run dir $cr_run exists but has no bench-engines marker; refusing to delete it (point RUNBASE at a dedicated dir)" >&2
        return 1
    fi
    rm -rf "$cr_run"
    mkdir -p "$cr_run" || return 1
    : > "$cr_run/.bench-engines-run" || return 1
    return 0
}

start_unitd() {  # label build run -> sets UNITD_PID, ROUTER, ENGINE[label]
    su_label=$1 su_build=$2 su_run=$3 su_mods=$4
    claim_run_dir "$su_run" || return 1
    mkdir -p "$su_run/state" "$su_run/modules"
    UNITD_PID=""; PGID=""; RUN_CUR="$su_run"
    RUN_DIRS_N=$((RUN_DIRS_N + 1))
    eval "RUN_DIR_$RUN_DIRS_N=\$su_run"   # RUN_DIRS_N is a number
    # AF_UNIX sun_path is ~108 bytes; unitd binds "<run>/control.sock.tmp" (+17).
    if [ "$(( ${#su_run} + 17 ))" -ge 108 ]; then
        printf '%s\n' "run path too long for AF_UNIX socket ($su_run); set a shorter RUNBASE" >&2
        return 1
    fi
    # setsid: give unitd its OWN session/process group so cleanup can signal the
    # whole tree (main+controller+router+workers) as a unit, isolated from the
    # harness's group and from unrelated daemons.
    # Pin the whole server tree at spawn (children inherit affinity); the later
    # router re-pin is then just a no-op safety net.
    # stderr must be captured: the main process logs the engine line before the
    # log file is opened, so it only ever appears on stderr.
    setsid taskset -c "$SERVER_CORES" "$su_build/sbin/unitd" --no-daemon \
        --control "unix:$su_run/control.sock" \
        --pid "$su_run/unit.pid" --log "$su_run/unit.log" \
        --statedir "$su_run/state" --tmpdir "$su_run" \
        --modulesdir "$su_mods" \
        >/dev/null 2>"$su_run/stderr.log" &
    # $! may be the setsid wrapper (which forks-or-execs depending on group-leader
    # status), so it is only trusted as a start-failure hint until the pidfile
    # appears; the real main pid is read from the pidfile below.
    su_spawn_pid=$! su_main=""
    # Fallback cleanup target BEFORE the pidfile exists. setsid runs the child in
    # its OWN session with PID==PGID==SID (this is a non-job-control shell, so the
    # child is not a group leader, setsid() succeeds and execs in place), so
    # spawn_pid names the new group. Record it as the kill target NOW: if unitd
    # hangs and never writes unit.pid or opens the control socket, PGID/UNITD_PID
    # would otherwise both stay empty and neither cleanup nor the post-run leak
    # scan (both key off <run>/unitd.pgid) could reap the leaked group. This can
    # never be the harness's own group (spawn_pid is a child pid, != the shell's
    # pid, and no group has id==spawn_pid until setsid creates it); a query before
    # setsid runs simply finds no such group. resolve_pgid_from_run overwrites
    # this with the authoritative pidfile-derived pgid once unitd is up.
    PGID=$su_spawn_pid
    printf '%s\n' "$su_spawn_pid" > "$su_run/unitd.pgid" 2>/dev/null || true
    # wait for the control socket instead of a bare sleep
    su_i=0
    while [ ! -S "$su_run/control.sock" ] && [ "$su_i" -lt 100 ]; do
        # Prefer the real pid the moment unitd writes its pidfile.
        if [ -z "$su_main" ] && [ -s "$su_run/unit.pid" ]; then
            su_main=$(tr -d ' \t\r\n' < "$su_run/unit.pid" 2>/dev/null)
            case "$su_main" in *[!0-9]*) su_main="" ;; esac
        fi
        if [ -n "$su_main" ]; then
            kill -0 "$su_main" 2>/dev/null || { printf '%s\n' "unitd died on start ($su_label)"; return 1; }
        elif ! kill -0 "$su_spawn_pid" 2>/dev/null && [ ! -s "$su_run/unit.pid" ]; then
            # spawn gone before any pidfile: a real early death (exec case). In the
            # fork case the wrapper exits normally, so only trust this pre-pidfile.
            printf '%s\n' "unitd died on start ($su_label)"; return 1
        fi
        su_i=$((su_i+1)); sleep 0.1
    done
    [ -S "$su_run/control.sock" ] || { printf '%s\n' "no control socket ($su_label)"; return 1; }
    # Resolve the authoritative main pid + process group from the pidfile and
    # record the pgid for the cleanup ladder / external sweepers.
    resolve_pgid_from_run "$su_run" \
        || { printf '%s\n' "no valid main pid/pgid in pidfile ($su_label)"; return 1; }
    # shellcheck disable=SC2034  # read by the eval below
    su_engine=$(detect_engine "$su_run")
    eval "ENGINE_$(label_slot "$su_label")=\$su_engine"   # the slot is A or B
    return 0
}

pin_router() {  # run -> sets ROUTER, pins to server cores
    # As "pgrep -P UNITD_PID -f router | head -1": the lowest child pid whose
    # command line contains "router".
    ROUTER=""
    for pr_p in $(child_pids "$UNITD_PID"); do
        cmdline_has "$pr_p" router && { ROUTER=$pr_p; break; }
    done
    [ -n "$ROUTER" ] || { echo "no router pid"; return 1; }
    taskset -pc "$SERVER_CORES" "$ROUTER" >/dev/null 2>&1
    return 0
}

# ---- measurement ------------------------------------------------------------
# oha_one: label scenario url nreq conc keepalive_flag mode
#   mode = router | tree   (which CPU accounting)
oha_one() {
    oo_run=$1 oo_label=$2 oo_scen=$3 oo_url=$4 oo_n=$5 oo_c=$6 oo_ka=$7 oo_mode=$8
    oo_js="$oo_run/oha-$oo_scen.json"
    oo_ohaka=""
    [ "$oo_ka" = 1 ] || oo_ohaka=--disable-keepalive
    if [ "$oo_mode" = tree ]; then oo_t0=$(tree_cpu_ticks); else oo_t0=$(router_cpu_ticks); fi
    # Backgrounded + wait, NOT foreground: bash defers INT/TERM traps while a
    # foreground child runs, so a signalled harness would otherwise let the
    # generator run out its full measurement before cleanup can start. wait is
    # interruptible and cleanup kills $LOAD_PID.
    taskset -c "$LOAD_CORES" "$OHA" --no-tui --output-format json -t "$OHA_TIMEOUT" \
        -n "$oo_n" -c "$oo_c" ${oo_ohaka:+"$oo_ohaka"} "$oo_url" \
        > "$oo_js" 2>"$oo_run/oha-$oo_scen.err" &
    LOAD_PID=$!
    wait "$LOAD_PID"
    oo_rc=$?
    LOAD_PID=""
    if [ "$oo_mode" = tree ]; then oo_t1=$(tree_cpu_ticks); else oo_t1=$(router_cpu_ticks); fi
    # Router mode: a crash/restart mid-measurement makes router_cpu_ticks default
    # to 0, yielding a bogus 0/negative CPU sample that would still pass. Fail if
    # the router is gone. (Tree mode: app worker churn is the daemon's business.)
    if [ "$oo_mode" = router ] && [ ! -r "/proc/$ROUTER/stat" ]; then
        printf '%s\n' "router pid $ROUTER vanished mid-measurement: $oo_label $oo_scen" >&2
        return 1
    fi
    # A here-document of the parsed line replaces bash's < <( ) process
    # substitution; read takes its first line as before.
    oo_parsed=$(parse_oha "$oo_js")
    IFS=$TAB read -r oo_rps oo_p50 oo_p99 oo_failed <<EOF
$oo_parsed
EOF
    # A dead/unstartable oha (or unparsable JSON) must fail the measurement:
    # otherwise printf records an all-zero row and the round looks green.
    if [ "$oo_rc" -ne 0 ] || [ -z "$oo_rps" ]; then
        printf '%s\n' "oha measurement failed: $oo_label $oo_scen (rc=$oo_rc, see $oo_run/oha-$oo_scen.err)" >&2
        return 1
    fi
    oo_us=$(LC_ALL=C awk -v a="$oo_t0" -v b="$oo_t1" -v clk="$CLK" -v n="$oo_n" \
        'BEGIN{printf "%.2f", (b-a)*1000000/clk/n}')
    # A failing append (e.g. RESULTS on a full disk) must fail the measurement:
    # the trailing failed-check below would otherwise overwrite the pipeline
    # status and the row would be lost while oha_one still returned success.
    if ! printf '%s %s oha rps=%.0f p50ms=%.3f p99ms=%.3f failed=%s cpu_us_per_req=%s engine=%s\n' \
            "$oo_label" "$oo_scen" "$oo_rps" "$oo_p50" "$oo_p99" "${oo_failed:-0}" "$oo_us" "$(engine_of "$oo_label")" \
            | tee -a "$RESULTS"; then
        printf '%s\n' "failed to append measurement row to $RESULTS: $oo_label $oo_scen" >&2
        return 1
    fi
    # Recorded the row for forensics; now fail the measurement if it saw request
    # failures (non-2xx/transport) — the summary tables don't surface `failed`.
    if [ "${oo_failed:-0}" -gt "$MAX_FAILED" ]; then
        printf '%s\n' "oha measurement recorded ${oo_failed:-0} failed request(s) (>MAX_FAILED=$MAX_FAILED): $oo_label $oo_scen" >&2
        return 1
    fi
}

# ab_one: secondary continuity metric (keepalive matching bench-ab.sh numbers)
ab_one() {
    ab_run=$1 ab_label=$2 ab_scen=$3 ab_url=$4 ab_n=$5 ab_c=$6 ab_ka=$7 ab_mode=$8
    [ "$SKIP_AB" = 1 ] && return 0
    ab_out="$ab_run/ab-$ab_scen.txt" ab_extra=""
    [ "$ab_ka" = 1 ] && ab_extra="-k"
    if [ "$ab_mode" = tree ]; then ab_t0=$(tree_cpu_ticks); else ab_t0=$(router_cpu_ticks); fi
    # Backgrounded + wait for signal responsiveness; see oha_one.
    # shellcheck disable=SC2086
    taskset -c "$LOAD_CORES" ab $ab_extra -n "$ab_n" -c "$ab_c" -q "$ab_url" > "$ab_out" 2>&1 &
    LOAD_PID=$!
    wait "$LOAD_PID"
    ab_rc=$?
    LOAD_PID=""
    if [ "$ab_mode" = tree ]; then ab_t1=$(tree_cpu_ticks); else ab_t1=$(router_cpu_ticks); fi
    # Router mode: a crash/restart mid-measurement makes router_cpu_ticks default
    # to 0, yielding a bogus 0/negative CPU sample that would still pass. Fail if
    # the router is gone. (Tree mode: app worker churn is the daemon's business.)
    if [ "$ab_mode" = router ] && [ ! -r "/proc/$ROUTER/stat" ]; then
        printf '%s\n' "router pid $ROUTER vanished mid-measurement: $ab_label $ab_scen" >&2
        return 1
    fi
    ab_rps=$(awk '/Requests per second/{print $4}' "$ab_out")
    # Same rationale as oha_one: a failed ab must not record a zero-value row.
    if [ "$ab_rc" -ne 0 ] || [ -z "$ab_rps" ]; then
        printf '%s\n' "ab measurement failed: $ab_label $ab_scen (rc=$ab_rc, see $ab_out)" >&2
        return 1
    fi
    ab_p99=$(awk '/ 99%/{print $2}' "$ab_out")
    # ab reports HTTP-status failures on a separate "Non-2xx responses" line,
    # NOT in "Failed requests"; count both or 5xx regressions record failed=0.
    ab_failed=$(awk '/Failed requests/{print $3}' "$ab_out")
    ab_non2xx=$(awk '/Non-2xx responses/{print $3}' "$ab_out")
    ab_failed=$(( ${ab_failed:-0} + ${ab_non2xx:-0} ))
    ab_us=$(LC_ALL=C awk -v a="$ab_t0" -v b="$ab_t1" -v clk="$CLK" -v n="$ab_n" \
        'BEGIN{printf "%.2f", (b-a)*1000000/clk/n}')
    # Same rationale as oha_one: a failing append must fail the measurement, or
    # the trailing failed-check overwrites the pipeline status and the row is lost.
    if ! printf '%s %s ab rps=%s p50ms=NA p99ms=%s failed=%s cpu_us_per_req=%s engine=%s\n' \
            "$ab_label" "$ab_scen" "${ab_rps:-0}" "${ab_p99:-NA}" "${ab_failed:-0}" "$ab_us" "$(engine_of "$ab_label")" \
            | tee -a "$RESULTS"; then
        printf '%s\n' "failed to append measurement row to $RESULTS: $ab_label $ab_scen" >&2
        return 1
    fi
    # Recorded the row for forensics; now fail the measurement if it saw request
    # failures (Failed requests + Non-2xx) — the summary tables don't surface it.
    if [ "${ab_failed:-0}" -gt "$MAX_FAILED" ]; then
        printf '%s\n' "ab measurement recorded ${ab_failed:-0} failed request(s) (>MAX_FAILED=$MAX_FAILED): $ab_label $ab_scen" >&2
        return 1
    fi
}

# warmup_one: a VALIDATED warmup pass. oha's default discards status and JSON, so
# a warmup that errors or returns non-2xx would silently precede the first
# measurement, leaving it to run against a cold or still-recovering build. Apply
# the same request-failure policy as a measurement (exit status + failed-count vs
# MAX_FAILED); record no result row. Non-zero return fails the build-round.
warmup_one() {  # run label tag url nreq conc
    wu_run=$1 wu_label=$2 wu_tag=$3 wu_url=$4 wu_n=$5 wu_c=$6
    wu_js="$wu_run/warmup-$wu_tag.json"
    # Backgrounded + wait for signal responsiveness; see oha_one.
    taskset -c "$LOAD_CORES" "$OHA" --no-tui --output-format json -t "$OHA_TIMEOUT" \
        -n "$wu_n" -c "$wu_c" "$wu_url" > "$wu_js" 2>"$wu_run/warmup-$wu_tag.err" &
    LOAD_PID=$!
    wait "$LOAD_PID"
    wu_rc=$?
    LOAD_PID=""
    wu_parsed=$(parse_oha "$wu_js")   # as in oha_one
    # shellcheck disable=SC2034  # wu_p50 and wu_p99 are read only to split the line
    IFS=$TAB read -r wu_rps wu_p50 wu_p99 wu_failed <<EOF
$wu_parsed
EOF
    if [ "$wu_rc" -ne 0 ] || [ -z "$wu_rps" ]; then
        printf '%s\n' "warmup failed: $wu_label $wu_tag (rc=$wu_rc, see $wu_run/warmup-$wu_tag.err)" >&2
        return 1
    fi
    if [ "${wu_failed:-0}" -gt "$MAX_FAILED" ]; then
        printf '%s\n' "warmup recorded ${wu_failed:-0} failed request(s) (>MAX_FAILED=$MAX_FAILED): $wu_label $wu_tag" >&2
        return 1
    fi
    return 0
}

# ---- per-build round --------------------------------------------------------
run_build_round() {
    rb_label=$1 rb_build=$2 rb_slot=$3 rb_rnd=$4
    rb_meas_failed=0
    if [ "$rb_slot" = A ]; then
        rb_port=$PORT_A; rb_pport=$PORT_A_PROXY; rb_pphp=$PORT_A_PHP
    else
        rb_port=$PORT_B; rb_pport=$PORT_B_PROXY; rb_pphp=$PORT_B_PHP
    fi
    rb_run="$RUNBASE/$rb_label-r$rb_rnd"
    rb_mods="$rb_build/lib/unit/modules"
    [ -d "$rb_mods" ] || rb_mods="$rb_run/modules"

    # Serialize against a CONCURRENT invocation using the same RUNBASE+labels
    # (=> the same deterministic run dir): hold the exclusive per-run lock so the
    # sweep below can only ever reap a CRASHED prior run, never a live sibling.
    acquire_run_lock "$rb_run" \
        || { printf '%s\n' "run dir $rb_run is locked by another live invocation; skipping round" >&2; return 1; }

    # Reap any instance left in THIS run dir by a previously crashed/hard-killed
    # harness before we probe the ports (an orphan holding them would otherwise
    # trip the port_busy abort below on a run we can legitimately recover).
    sweep_stale_run "$rb_run"

    # refuse if ports busy (php port too: a bound $pphp would otherwise
    # degrade to the php SKIP path instead of aborting the round)
    for rb_p in "$rb_port" "$rb_pport" "$rb_pphp"; do
        if port_busy "$rb_p"; then printf '%s\n' "port $rb_p busy, aborting" >&2; cleanup; return 1; fi
    done

    printf '%s\n' "== round $rb_rnd  build=$rb_label ($rb_build) engine-slot=$rb_slot =="
    # Every early-failure return must run cleanup first, or the half-started
    # unitd leaks: it holds the ports (aborting later rounds) and the EXIT
    # trap only knows the LAST spawned pid.
    start_unitd "$rb_label" "$rb_build" "$rb_run" "$rb_mods" || { cleanup; return 1; }

    # Engine revalidation: the preflight same-engine assertion ran ONCE, but
    # the engine of $label is re-detected on every start. A build that transiently
    # falls back to another engine (or a build dir swapped mid-run) must fail
    # the round instead of pooling foreign-engine samples under this label.
    if [ -n "$(expect_of "$rb_label")" ] \
       && [ "$(engine_of "$rb_label")" != "$(expect_of "$rb_label")" ]; then
        printf '%s\n' "engine changed for $rb_label: preflight='$(expect_of "$rb_label")' this round='$(engine_of "$rb_label")'" >&2
        cleanup; return 1
    fi

    # --- HTTP scenarios ---
    put_config_http "$rb_run/control.sock" "$rb_port" "$rb_pport" "$rb_run/conf-http.json" \
        || { printf '%s\n' "CONF FAILED $rb_label"; cat "$rb_run/conf-http.json"; cleanup; return 1; }
    wait_port "$rb_port" 10 \
        || { printf '%s\n' "listener $rb_port never came up"; cleanup; return 1; }
    pin_router "$rb_run" || { cleanup; return 1; }

    # warmup (validated): a failed warmup means the warmed-state precondition
    # for EVERY sample of this round does not hold — abort the round now
    # instead of recording a full matrix of cold/erroring samples that sit in
    # RESULTS looking like valid measurements.
    warmup_one "$rb_run" "$rb_label" http "http://127.0.0.1:$rb_port/" 2000 16 \
        || { printf '%s\n' "warmup failed; aborting round for $rb_label" >&2; cleanup; return 1; }

    # Collect measurement failures instead of aborting mid-round: the round
    # must still tear down cleanly, then report failure via FAILED_ROUNDS.
    oha_one "$rb_run" "$rb_label" ret200_ka_hi "http://127.0.0.1:$rb_port/"  "$N_HIGH"  256 1 router || rb_meas_failed=1
    ab_one  "$rb_run" "$rb_label" ret200_ka_hi "http://127.0.0.1:$rb_port/"  "$N_HIGH"  256 1 router || rb_meas_failed=1
    oha_one "$rb_run" "$rb_label" ret200_ka_lo "http://127.0.0.1:$rb_port/"  "$N_LOW"     8 1 router || rb_meas_failed=1
    ab_one  "$rb_run" "$rb_label" ret200_ka_lo "http://127.0.0.1:$rb_port/"  "$N_LOW"     8 1 router || rb_meas_failed=1
    oha_one "$rb_run" "$rb_label" ret200_close "http://127.0.0.1:$rb_port/"  "$N_CLOSE"  16 0 router || rb_meas_failed=1
    ab_one  "$rb_run" "$rb_label" ret200_close "http://127.0.0.1:$rb_port/"  "$N_CLOSE"  16 0 router || rb_meas_failed=1
    oha_one "$rb_run" "$rb_label" proxy_ka     "http://127.0.0.1:$rb_pport/" "$N_PROXY"  64 1 router || rb_meas_failed=1
    ab_one  "$rb_run" "$rb_label" proxy_ka     "http://127.0.0.1:$rb_pport/" "$N_PROXY"  64 1 router || rb_meas_failed=1

    # --- PHP scenario (optional) ---
    rb_php_mod=""
    [ -d "$rb_build/lib/unit/modules" ] && \
        rb_php_mod=$(find "$rb_build/lib/unit/modules" -maxdepth 1 -name 'php*.so' 2>/dev/null | head -1)
    if [ -n "$rb_php_mod" ]; then
        rb_root=$(ensure_phpapp) || rb_root=""
        if [ -n "$rb_root" ]; then
            if cleanup_http_reconfig "$rb_run" "$rb_pphp" "$rb_root"; then
                # An unwarmed php scenario must not be measured either: skip
                # its samples and fail the round (http samples above stand).
                if warmup_one "$rb_run" "$rb_label" php "http://127.0.0.1:$rb_pphp/" 5000 8; then
                    oha_one "$rb_run" "$rb_label" php_ka "http://127.0.0.1:$rb_pphp/" "$N_PHP" 8 1 tree || rb_meas_failed=1
                    ab_one  "$rb_run" "$rb_label" php_ka "http://127.0.0.1:$rb_pphp/" "$N_PHP" 8 1 tree || rb_meas_failed=1
                else
                    printf '%s\n' "php warmup failed for $rb_label; skipping php measurements" >&2
                    rb_meas_failed=1
                fi
            else
                # module + app dir are BOTH present, so a config/listener
                # failure is a real regression in this build, not a missing
                # optional dependency: fail the round, never degrade to a
                # SKIP that leaves one side without PHP samples at exit 0.
                printf '%s\n' "php config/listener FAILED for $rb_label (see $rb_run/conf-php.json)" >&2
                rb_meas_failed=1
            fi
        else
            printf '%s\n' "$rb_label php_ka SKIP php (no app dir)" | tee -a "$RESULTS"
        fi
    else
        printf '%s\n' "$rb_label php_ka SKIP php (no php module in $rb_build)" | tee -a "$RESULTS"
    fi

    cleanup
    # Fail the round if any measurement failed, so the caller's FAILED_ROUNDS
    # accounting (and the final non-zero exit) catches incomplete data.
    [ "$rb_meas_failed" -eq 0 ]
}

cleanup_http_reconfig() {  # reuse the running unitd, swap to php config on pphp
    ch_run=$1 ch_pphp=$2 ch_root=$3
    put_config_php "$ch_run/control.sock" "$ch_pphp" "$ch_root" "$ch_run/conf-php.json" || return 1
    wait_port "$ch_pphp" 10 || return 1
    return 0
}

ensure_phpapp() {  # -> prints app root, or empty on failure
    ep_root=$PHPAPP
    if [ -n "$ep_root" ]; then
        # explicit app root: validated at startup, caller-owned — NEVER write
        # into it (auto-creation applies only to the harness's own dir below)
        printf '%s\n' "$ep_root"
        return 0
    fi
    ep_root="$RUNBASE/phpapp"
    if [ ! -f "$ep_root/index.php" ]; then
        mkdir -p "$ep_root" 2>/dev/null || { return 1; }
        printf '%s\n' '<?php echo "hello\n";' > "$ep_root/index.php" 2>/dev/null || return 1
    fi
    printf '%s\n' "$ep_root"
}

# ---- engine preflight + assertion (before any measurement) ------------------
preflight_engine() {  # label build -> detects ENGINE[label], then kills
    pe_label=$1 pe_build=$2
    pe_run="$RUNBASE/$pe_label-preflight"
    pe_mods="$pe_build/lib/unit/modules"
    [ -d "$pe_mods" ] || pe_mods="$pe_run/modules"
    acquire_run_lock "$pe_run" \
        || { printf '%s\n' "preflight: run dir $pe_run is locked by another live invocation" >&2; return 1; }
    sweep_stale_run "$pe_run"
    start_unitd "$pe_label" "$pe_build" "$pe_run" "$pe_mods" || {
        printf '%s\n' "preflight: $pe_label failed to start" >&2; cleanup; return 1; }
    cleanup
}

printf '%s\n' "bench-engines: A=$LABEL_A ($BUILD_A)  B=$LABEL_B ($BUILD_B)  rounds=$ROUNDS"
printf '%s\n' "results -> $RESULTS   run dirs -> $RUNBASE"

preflight_engine "$LABEL_A" "$BUILD_A" || exit 1
preflight_engine "$LABEL_B" "$BUILD_B" || exit 1
EA=${ENGINE_A:-unknown}
EB=${ENGINE_B:-unknown}
printf '%s\n' "engines: $LABEL_A=$EA  $LABEL_B=$EB"
if [ "$EA" = unknown ] || [ "$EB" = unknown ]; then
    echo "WARNING: could not detect engine for at least one build (old build without the"
    echo "         'using \"<name>\" event engine' log line). Same-engine check skipped."
elif [ "$EA" = "$EB" ]; then
    if [ "$ALLOW_SAME" = 1 ]; then
        printf '%s\n' "NOTE: both builds report engine=$EA (A/A mode, --allow-same set)."
    else
        printf '%s\n' "ERROR: both builds report the SAME engine ($EA). This is not an A/B" >&2
        echo "       comparison. Re-run with --allow-same for an intentional A/A test." >&2
        exit 1
    fi
fi
# Pin each label to its preflight-asserted engine: run_build_round compares the
# per-start re-detection against this and fails the round on any mismatch.
# shellcheck disable=SC2034  # read through expect_of
EXPECT_A=$EA
# shellcheck disable=SC2034  # as above
EXPECT_B=$EB

# ---- main loop --------------------------------------------------------------
FAILED_ROUNDS=0
for r in $(seq 1 "$ROUNDS"); do
    # Interleave within the round AND alternate the order per round: with a
    # fixed A-then-B order any monotonic warmup/thermal drift stays correlated
    # with the build label; alternating puts each build in both positions.
    if [ $((r % 2)) = 1 ]; then
        run_build_round "$LABEL_A" "$BUILD_A" A "$r" \
            || { printf '%s\n' "round $r A failed" >&2; FAILED_ROUNDS=$((FAILED_ROUNDS+1)); }
        run_build_round "$LABEL_B" "$BUILD_B" B "$r" \
            || { printf '%s\n' "round $r B failed" >&2; FAILED_ROUNDS=$((FAILED_ROUNDS+1)); }
    else
        run_build_round "$LABEL_B" "$BUILD_B" B "$r" \
            || { printf '%s\n' "round $r B failed" >&2; FAILED_ROUNDS=$((FAILED_ROUNDS+1)); }
        run_build_round "$LABEL_A" "$BUILD_A" A "$r" \
            || { printf '%s\n' "round $r A failed" >&2; FAILED_ROUNDS=$((FAILED_ROUNDS+1)); }
    fi
done

# ---- post-run leak assert ---------------------------------------------------
# Every round tears its instance down via the pgid ladder, but assert it: scan
# the pgid files left on disk and treat any surviving group as a run failure even
# if all measurements succeeded. Kill survivors (again, via the ladder) so we do
# not leave orphans behind, and force a non-zero exit below.
# Scan ONLY the run dirs THIS invocation created: a concurrent invocation
# sharing RUNBASE (different labels) has live, legitimately-owned unitd groups
# under other dirs, and a glob over all of RUNBASE would report the first one
# found as our leak and kill it mid-measurement.
LEAKED=0
UNITD_PID=""   # leak survivors are not our children; keep reap_child a no-op
rd_i=0
while [ "$rd_i" -lt "$RUN_DIRS_N" ]; do
    rd_i=$((rd_i + 1))
    eval "rd=\$RUN_DIR_$rd_i"   # rd_i is a number
    # shellcheck disable=SC2154  # rd is set by the eval above
    pf="$rd/unitd.pgid"
    [ -f "$pf" ] || continue
    pg=$(tr -d ' \t\r\n' < "$pf" 2>/dev/null)
    case "$pg" in ''|*[!0-9]*) continue ;; esac
    if group_alive "$pg" && group_owns_run "$pg" "${pf%/unitd.pgid}"; then
        printf '%s\n' "LEAK: process group $pg from ${pf%/unitd.pgid} still has survivors:" >&2
        # As "ps -o pid,pgid,cmd": the pid, the group and the command line.
        for lk_p in $(group_pids "$pg"); do
            lk_cmd=$({ tr '\0' ' ' < "/proc/$lk_p/cmdline"; } 2>/dev/null) || continue
            printf '    %s %s %s\n' "$lk_p" "$pg" "${lk_cmd% }" >&2
        done
        kill_pgid "$pg"
        LEAKED=1
    fi
done

# ---- summary ----------------------------------------------------------------
echo
SUMMARIZER="$(dirname "$0")/bench-engines-report.py"
# A summarizer failure must fail the run (below): automation would otherwise
# read a clean exit off a benchmark whose reporting stage broke.
SUMMARY_FAILED=0
if [ "$HAVE_PY" = 1 ] && [ -f "$SUMMARIZER" ]; then
    python3 "$SUMMARIZER" "$RESULTS" "$LABEL_A" "$LABEL_B" || SUMMARY_FAILED=1
else
    # inline awk fallback summary (mean only)
    echo "### summary (mean RPS, oha)"
    # Both the awk and the sort status count. POSIX sh has no PIPESTATUS, so
    # each side writes its status to fd 3, which the command substitution
    # reads; sort writes its output to fd 4, the script's stdout.
    { sumstat=$( { { awk '$3=="oha"{
        split($4,a,"="); rps=a[2];
        key=$1" "$2; sum[key]+=rps; cnt[key]++
    } END{ for(k in sum) printf "  %s  mean_rps=%.0f (n=%d)\n", k, sum[k]/cnt[k], cnt[k] }' \
        "$RESULTS"; printf '%s ' "$?" >&3; } | sort >&4; printf '%s\n' "$?" >&3; } 3>&1 ); } 4>&1
    [ "$sumstat" = "0 0" ] || SUMMARY_FAILED=1
fi

# A failed round means incomplete/contaminated results: still print the
# summary above for whatever data exists, but exit non-zero so automation
# never mistakes this for a good A/B run.
if [ "$FAILED_ROUNDS" -gt 0 ]; then
    printf '%s\n' "ERROR: $FAILED_ROUNDS build-round(s) failed; results are incomplete" >&2
    exit 1
fi

# A leaked process group is a run failure even if every measurement passed.
if [ "$LEAKED" -ne 0 ]; then
    echo "ERROR: leaked unitd process group(s) survived teardown (see LEAK lines above)" >&2
    exit 1
fi

# Nobody validated/printed the aggregate: the raw rows exist, but the run must
# not look green when its summary stage failed.
if [ "$SUMMARY_FAILED" -ne 0 ]; then
    printf '%s\n' "ERROR: summary generation failed (raw rows remain in $RESULTS)" >&2
    exit 1
fi

exit 0
