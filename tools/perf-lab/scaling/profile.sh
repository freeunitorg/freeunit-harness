#!/usr/bin/env bash
# profile.sh -- where the time goes at one point of the sweep. Runs inside the lab
# container (run.sh profile; it has --cap-add PERFMON and SYS_PTRACE), or natively.
#
#   PERF_SCOPE: pids (the default) attaches every perf call to the processes of
#   the kit with "-p" (unitd, router, controller, prototypes, applications).
#   cpus is the system-wide "-a -C <server cpus>" of the first kit; it also counts
#   other tasks on those cpus, and it needs perf_event_paranoid <= 0 or
#   CAP_PERFMON. At perf_event_paranoid 2 the preflight picks the user space
#   forms: perf record --all-user --user-callchains, and for c2c
#   "perf c2c record --all-user", or on AMD "perf record -W -d --sample-cpu
#   -e ibs_op/swfilt=1/u".
#
#   N (router threads; 3, or the core count when it is smaller), V (variant, master), WORKLOADS ("capp static"),
#   PERF_SEC (20), C2C_G (0; 1 adds a perf c2c record with call graphs),
#   LOAD_SEC (75, or 90 with C2C_G=1), PROFILE ("record futex c2c": the steps),
#   PERF_EVENT (cycles; cpu-clock needs no hardware PMU), PREFLIGHT_ONLY (0; 1
#   stops after the preflight).
#
# Preflight first: perf_preflight (lib.sh) reads perf_event_paranoid,
# kptr_restrict, ptrace_scope, the PMU devices and the capabilities, and writes
# preflight.txt. When a requested step cannot work, profile.sh writes FAILED and
# exits 1 before it starts unitd: no step produces an empty or zero result.
# Then a probe: "perf stat -e task-clock,PERF_EVENT -- true" must count.
#
# One unitd per workload under one oha load of LOAD_SEC seconds. Every record
# runs inside the load; the reports run after it:
#   A. perf record -g: top-<w>.txt, and the share of lock
#      symbols in lockshare-<w>.txt.
#   B. futex calls: the syscalls:sys_enter_futex tracepoint. A container
#      without tracefs has no tracepoint; then strace -c -f on the router.
#   C. perf c2c: c2c-<w>.txt holds the shared cache line tables, and
#      c2c-<w>-stats.txt the summary. "--stats" prints the summary only, so
#      it is a second report, never the only one.
#   D. with C2C_G=1: perf c2c record -g, and c2cg-<w>.txt with the callers of
#      each hot line.
# A step that passed the preflight may still fail; failures go to failures.txt.
# summary.txt is written last, then DONE.
#
# Lock counts are a separate stage (run.sh locks): the shim slows every lock call.
set -u
: "${N:=}" "${V:=master}" "${WORKLOADS:=capp static}" "${PERF_SEC:=20}" "${WARM:=3}" "${C2C_G:=0}"
: "${LOAD_SEC:=$((75 + 15 * C2C_G))}"   # covers every record step below; the reports run after the load
: "${PROFILE:=record futex c2c}" "${PERF_EVENT:=cycles}" "${PREFLIGHT_ONLY:=0}" "${PERF_SCOPE:=pids}"
case $PERF_SCOPE in pids|cpus) ;; *) die "PERF_SCOPE is pids or cpus, not '$PERF_SCOPE'" ;; esac
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=tools/perf-lab/scaling/lib.sh
. "$HERE/lib.sh"

# A misspelt step must not pass: no step would run, and DONE would follow.
read -r -a steps <<< "$PROFILE"
[ "${#steps[@]}" -gt 0 ] || die "PROFILE is empty; the steps are record, futex and c2c"
for s in "${steps[@]}"; do
    case $s in record|futex|c2c) ;; *) die "PROFILE: unknown step '$s'; the steps are record, futex and c2c" ;; esac
done

command -v "$OHA" >/dev/null || die "no oha ($OHA)"
command -v perf >/dev/null || die "no perf"
read_topology
[ -n "$N" ] || N=$(default_n)
[ "$N" -le "$NCORES" ] || die "N=$N is more than the $NCORES cores"
mkdir -p "$OUT"; chmod 1777 "$OUT"
write_meta profile "$V"
FAIL=$OUT/failures.txt; : > "$FAIL"

# --- preflight: stop before unitd starts when a requested step cannot work --------------
has() { [[ " $PROFILE " == *" $1 "* ]]; }
want=$PROFILE
[ "$PERF_SCOPE" = cpus ] && want="$want cpus"
[ "$C2C_G" = 1 ] && has c2c && want="$want c2cg"
# shellcheck disable=SC2086  # one word per metric
if ! perf_preflight $want > "$OUT/preflight.txt"; then
    cat "$OUT/preflight.txt"
    echo "preflight: a requested step cannot work; see preflight.txt" > "$OUT/FAILED"
    own_out; die "preflight failed: $(grep -c ': unavailable' "$OUT/preflight.txt") unavailable; see preflight.txt"
fi
cat "$OUT/preflight.txt"
# The files say what should work; this probe says whether perf_event_open agrees.
# "<not supported>" is exit 0 for perf stat, so read the output too.
u=""; [ "$PERF_USER_ONLY" = 1 ] && u=:u
if ! perf stat -x, -e "task-clock$u,$PERF_EVENT$u" -- true > "$OUT/probe.txt" 2>&1 \
    || grep -q -e '<not supported>' -e '<not counted>' "$OUT/probe.txt"; then
    echo "probe: perf stat -e task-clock$u,$PERF_EVENT$u failed; see probe.txt" \
        | tee -a "$OUT/preflight.txt" > "$OUT/FAILED"
    own_out; die "perf does not count here; see probe.txt"
fi
if [ "$PREFLIGHT_ONLY" = 1 ]; then
    cp "$OUT/preflight.txt" "$OUT/summary.txt"; : > "$OUT/DONE"; own_out; exit 0
fi
trap 'stop_unit; own_out' EXIT
setup_www
try() { local what=$1; shift; "$@" || echo "FAILED: $what ($*)" >> "$FAIL"; }

for w in $WORKLOADS; do
    srv=$(cpus_server "$N"); load=$(cpus_load "$N")
    start_unit "$V" "$w" "$N"
    oha_run "$WARM" "$load" "$(url_for "$w")"
    taskset -c "$load" "$OHA" -z "${LOAD_SEC}s" -c "$CONNS" --no-tui --output-format json \
        "$(url_for "$w")" > "$OUT/load-$w.json" 2>/dev/null &
    LP=$!
    sleep 3
    pids=$(my_pids | paste -s -d, -)
    [ -n "$pids" ] || echo "FAILED: no process of the kit is running for $w" >> "$FAIL"
    echo "$w: pids $pids" >> "$OUT/pids.txt"
    perf_args "$PERF_SCOPE" "$pids" "$srv"

    # A. samples of the kit's processes, or of the server cpus
    has record && try "perf record $w" perf record -e "$PERF_EVENT" "${PERF_T[@]}" ${PERF_U[@]+"${PERF_U[@]}"} \
        -g -F 499 -o "$OUT/perf-$w.data" -- sleep "$PERF_SEC" 2> "$OUT/perf-$w.err" >/dev/null

    # B. futex calls: the kernel side of a contended pthread mutex
    if has futex && [ "$FUTEX_FORM" = tracepoint ]; then
        try "futex tracepoint $w" perf stat "${PERF_T[@]}" -e 'syscalls:sys_enter_futex' -o "$OUT/futex-$w.txt" \
            -- sleep 10 2> "$OUT/futex-$w.err"
    elif has futex; then
        rp=""
        for p in $(my_pids); do
            tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q '^unit: router' && { rp=$p; break; }
        done
        if [ -n "$rp" ] && command -v "$STRACE" >/dev/null; then
            try "strace futex $w" strace_futex "$rp" 10 "$OUT/futex-strace-$w.txt" "$OUT/futex-$w.err"
        else
            echo "FAILED: strace futex $w (router pid '$rp', strace $(command -v "$STRACE" || echo missing))" >> "$FAIL"
        fi
    fi

    # C. cache line contention
    has c2c && try "perf c2c record $w ($C2C_FORM)" c2c_record "$OUT/c2c-$w.data" "$OUT/c2c-$w.err" -- sleep 15

    # D. callers of the hot lines
    if [ "$C2C_G" = 1 ] && has c2c; then
        try "perf c2c record -g $w ($C2C_FORM)" c2c_record "$OUT/c2cg-$w.data" "$OUT/c2cg-$w.err" -g -- sleep 12
    fi

    wait "$LP" 2>/dev/null
    stop_unit

    # the reports, with the load gone
    has record && try "perf report $w" bash -c "perf report -i '$OUT/perf-$w.data' --no-children --stdio -g none \
        --percent-limit 0.5 --sort comm,symbol > '$OUT/top-$w.txt' 2>> '$OUT/perf-$w.err'"
    has record && perf report -i "$OUT/perf-$w.data" --no-children --stdio -g none --sort symbol 2>/dev/null \
        | awk '/^ +[0-9.]+%/ { p = $1; sub("%", "", p); tot += p
                if ($0 ~ /futex|pthread_mutex|nxt_thread_mutex|lll_lock|lll_unlock|__lll_/) lock += p }
               END { printf "lock-related symbols: %.2f%% of %.2f%% sampled\n", lock, tot }' > "$OUT/lockshare-$w.txt"
    has c2c && try "perf c2c report $w" bash -c "perf c2c report -i '$OUT/c2c-$w.data' --stdio -c pid,iaddr --full-symbols \
        > '$OUT/c2c-$w.txt' 2>> '$OUT/c2c-$w.err'"
    has c2c && try "perf c2c stats $w" bash -c "perf c2c report -i '$OUT/c2c-$w.data' --stats > '$OUT/c2c-$w-stats.txt' \
        2>> '$OUT/c2c-$w.err'"
    if [ "$C2C_G" = 1 ] && has c2c; then
        try "perf c2c report -g $w" bash -c "perf c2c report -i '$OUT/c2cg-$w.data' --stdio -d lcl \
            --call-graph=caller,0.5 --percent-limit 0.5 > '$OUT/c2cg-$w.txt' 2>> '$OUT/c2cg-$w.err'"
    fi
done
chmod -R a+r "$OUT" 2>/dev/null
{
    echo "profile V=$V N=$N WORKLOADS='$WORKLOADS' server cpus $(cpus_server "$N") load cpus $(cpus_load "$N")"
    echo "perf: scope $PERF_SCOPE, level $PERF_LEVEL, user space only $PERF_USER_ONLY, event $PERF_EVENT," \
         "c2c ${C2C_FORM:-none}, futex ${FUTEX_FORM:-none}"
    cat "$OUT/pids.txt" 2>/dev/null
    for w in $WORKLOADS; do
        echo "$w: $(cat "$OUT/lockshare-$w.txt" 2>/dev/null); load $(read_oha "$OUT/load-$w.json" 2>/dev/null \
            || echo "FAILED: no oha result")"
    done
    echo "failures: $(wc -l < "$FAIL")"
    cat "$FAIL"
} > "$OUT/summary.txt"
cat "$OUT/summary.txt"
: > "$OUT/DONE"
