#!/usr/bin/env bash
# profile.sh -- where the time goes at one point of the sweep. Runs inside the lab
# container (run.sh profile; it has --cap-add PERFMON and SYS_PTRACE), or natively.
#
#   N (router threads, 3), V (variant, master), WORKLOADS ("capp static"),
#   PERF_SEC (20), C2C_G (0; 1 adds a perf c2c record with call graphs),
#   LOAD_SEC (75, or 90 with C2C_G=1).
#
# One unitd per workload under one oha load of LOAD_SEC seconds. Every record
# runs inside the load; the reports run after it:
#   A. perf record -g on the server cpus: top-<w>.txt, and the share of lock
#      symbols in lockshare-<w>.txt.
#   B. futex calls: the syscalls:sys_enter_futex tracepoint. A container
#      without tracefs has no tracepoint; then strace -c -f on the router.
#   C. perf c2c: c2c-<w>.txt holds the shared cache line tables, and
#      c2c-<w>-stats.txt the summary. "--stats" prints the summary only, so
#      it is a second report, never the only one.
#   D. with C2C_G=1: perf c2c record -g, and c2cg-<w>.txt with the callers of
#      each hot line.
# Each step may fail; failures go to failures.txt. summary.txt is written last.
#
# Lock counts are a separate stage (run.sh locks): the shim slows every lock call.
set -u
: "${N:=3}" "${V:=master}" "${WORKLOADS:=capp static}" "${PERF_SEC:=20}" "${WARM:=3}" "${C2C_G:=0}"
: "${LOAD_SEC:=$((75 + 15 * C2C_G))}"   # covers every record step below; the reports run after the load
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=tools/perf-lab/scaling/lib.sh
. "$HERE/lib.sh"

command -v "$OHA" >/dev/null || die "no oha ($OHA)"
command -v perf >/dev/null || die "no perf"
read_topology
[ "$N" -le "$NCORES" ] || die "N=$N is more than the $NCORES cores"
mkdir -p "$OUT"; chmod 1777 "$OUT"
write_meta profile "$V"
FAIL=$OUT/failures.txt; : > "$FAIL"
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

    # A. cycles on the server cpus
    try "perf record $w" perf record -a -C "$srv" -g -F 499 -o "$OUT/perf-$w.data" -- sleep "$PERF_SEC" \
        2> "$OUT/perf-$w.err" >/dev/null

    # B. futex calls: the kernel side of a contended pthread mutex
    if ! perf stat -a -C "$srv" -e 'syscalls:sys_enter_futex' -o "$OUT/futex-$w.txt" -- sleep 10 2> "$OUT/futex-$w.err"; then
        echo "FAILED: futex tracepoint $w (no tracefs?); strace on the router instead" >> "$FAIL"
        rp=""
        for p in $(my_pids); do
            tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q '^unit: router' && { rp=$p; break; }
        done
        if [ -n "$rp" ] && command -v strace >/dev/null; then
            try "strace futex $w" bash -c "timeout -s INT 10 strace -c -f -e trace=futex -p $rp \
                -o '$OUT/futex-strace-$w.txt' 2>> '$OUT/futex-$w.err'; true"
        else
            echo "FAILED: strace futex $w (router pid '$rp', strace $(command -v strace || echo missing))" >> "$FAIL"
        fi
    fi

    # C. cache line contention
    try "perf c2c record $w" bash -c "perf c2c record -a -C '$srv' -o '$OUT/c2c-$w.data' -- sleep 15 \
        > /dev/null 2> '$OUT/c2c-$w.err'"

    # D. callers of the hot lines
    if [ "$C2C_G" = 1 ]; then
        try "perf c2c record -g $w" bash -c "perf c2c record -a -C '$srv' -g -o '$OUT/c2cg-$w.data' -- sleep 12 \
            > /dev/null 2> '$OUT/c2cg-$w.err'"
    fi

    wait "$LP" 2>/dev/null
    stop_unit

    # the reports, with the load gone
    try "perf report $w" bash -c "perf report -i '$OUT/perf-$w.data' --no-children --stdio -g none \
        --percent-limit 0.5 --sort comm,symbol > '$OUT/top-$w.txt' 2>> '$OUT/perf-$w.err'"
    perf report -i "$OUT/perf-$w.data" --no-children --stdio -g none --sort symbol 2>/dev/null \
        | awk '/^ +[0-9.]+%/ { p = $1; sub("%", "", p); tot += p
                if ($0 ~ /futex|pthread_mutex|nxt_thread_mutex|lll_lock|lll_unlock|__lll_/) lock += p }
               END { printf "lock-related symbols: %.2f%% of %.2f%% sampled\n", lock, tot }' > "$OUT/lockshare-$w.txt"
    try "perf c2c report $w" bash -c "perf c2c report -i '$OUT/c2c-$w.data' --stdio -c pid,iaddr --full-symbols \
        > '$OUT/c2c-$w.txt' 2>> '$OUT/c2c-$w.err'"
    try "perf c2c stats $w" bash -c "perf c2c report -i '$OUT/c2c-$w.data' --stats > '$OUT/c2c-$w-stats.txt' \
        2>> '$OUT/c2c-$w.err'"
    if [ "$C2C_G" = 1 ]; then
        try "perf c2c report -g $w" bash -c "perf c2c report -i '$OUT/c2cg-$w.data' --stdio -d lcl \
            --call-graph=caller,0.5 --percent-limit 0.5 > '$OUT/c2cg-$w.txt' 2>> '$OUT/c2cg-$w.err'"
    fi
done
chmod -R a+r "$OUT" 2>/dev/null
{
    echo "profile V=$V N=$N WORKLOADS='$WORKLOADS' server cpus $(cpus_server "$N") load cpus $(cpus_load "$N")"
    for w in $WORKLOADS; do
        echo "$w: $(cat "$OUT/lockshare-$w.txt" 2>/dev/null); load $(read_oha "$OUT/load-$w.json" 2>/dev/null \
            || echo "FAILED: no oha result")"
    done
    echo "failures: $(wc -l < "$FAIL")"
    cat "$FAIL"
} > "$OUT/summary.txt"
cat "$OUT/summary.txt"
: > "$OUT/DONE"
