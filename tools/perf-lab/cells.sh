#!/bin/bash
# cells.sh -- the measured part of a perf lab job. Runs INSIDE the lab
# container on the perf box, started by remote.sh. Do not run it by hand.
#
# Generalised from the bench-app-shm kit (PR 535): unitd on SERVER_CPUS, oha
# on LOAD_CPUS, a fresh unitd per cell, a warm-up, then a measured window.
# Each block is 8 cells in the order X Y Y X Y X X Y; odd blocks use the
# swapped order Y X X Y X Y Y X, so a normal and a swapped run alternate.
#
# CPU comes from /proc/<pid>/task/*/schedstat (on-CPU nanoseconds) summed
# over unitd and every descendant, so the app workers are included.
#
# Inputs (environment): JOB PHASES WORKLOAD CONNS DUR WARM REPS RATE
# APP_PROCS PERF STRACE STRACE_SEC STRACE_RATE SERVER_CPUS LOAD_CPUS AE
# AE: an Accept-Encoding value that oha sends with every request (empty: none).
# REV_A REV_B. Trees: /t/A and /t/B. Output: /out/results.tsv.
set -u
OUT=/out/results.tsv
PORT=8300
R=/tmp/lab
printf 'job\tphase\tside\trev\tcell\tblock\tslot\tmetric\tvalue\n' > "$OUT"

die() { echo "cells.sh: $*" >&2; exit 1; }
[ -x /t/A/build/sbin/unitd ] || die "no /t/A/build/sbin/unitd"
[ -x /t/B/build/sbin/unitd ] || die "no /t/B/build/sbin/unitd"
command -v oha >/dev/null || die "no oha in the container"
if [ "$PERF" = 1 ]; then
    perf stat -e cycles true >/dev/null 2>&1 || die "perf stat does not work in this container (needs CAP_PERFMON and a perf binary)"
fi
if [ "$STRACE" = 1 ]; then command -v strace >/dev/null || die "no strace in the container"; fi

emit() {  # phase side cell block slot metric value
    local rev=$REV_A
    [ "$1" = ab ] && [ "$2" = B ] && rev=$REV_B
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$JOB" "$1" "$2" "$rev" "$3" "$4" "$5" "$6" "$7" >> "$OUT"
}

# Every process descended from the unitd main process, the main included.
unit_pids() {
    local main
    main=$(cat $R/pid 2>/dev/null) || return 0
    ps -eo pid=,ppid= | awk -v m="$main" '
        { p[$1] = $2 } END {
            for (x in p) { y = x; while (y != "" && y != 0 && y != m) y = p[y]; if (y == m) print x }
        }'
}
router_pid() {
    local p
    for p in $(unit_pids); do
        tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q '^unit: router' && { echo "$p"; return; }
    done
}
app_pids() {
    local p c
    for p in $(unit_pids); do
        c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
        case "$c" in *application*) echo "$p" ;; "unit: "*) ;; "") ;; *) echo "$p" ;; esac
    done
}
oncpu_ns() {  # pids... -> summed on-CPU nanoseconds of all their threads
    local p t s=0 v
    for p in "$@"; do
        for t in /proc/$p/task/*/schedstat; do
            read -r v _ < "$t" 2>/dev/null && s=$((s + v))
        done
    done
    echo $s
}

conf_for() {  # tree
    case "$WORKLOAD" in
    hello|server) cat <<EOF
{"listeners": {"127.0.0.1:$PORT": {"pass": "applications/p"}},
 "applications": {"p": {"type": "php", "root": "/lab/www", "processes": $APP_PROCS,
   "options": {"admin": {"opcache.enable": "1", "opcache.validate_timestamps": "0"}}}}}
EOF
    ;;
    static1k) cat <<EOF
{"listeners": {"127.0.0.1:$PORT": {"pass": "routes/s"}},
 "routes": {"s": [{"action": {"share": "/lab/www\$uri"}}]}}
EOF
    ;;
    static1k-hdrs) cat <<EOF
{"listeners": {"127.0.0.1:$PORT": {"pass": "routes/s"}},
 "routes": {"s": [{"action": {"share": "/lab/www\$uri",
   "response_headers": {"X-Frame-Options": "DENY", "Cache-Control": "max-age=60",
                        "X-Content-Type-Options": "nosniff"}}}]}}
EOF
    ;;
    capp) cat <<EOF
{"listeners": {"127.0.0.1:$PORT": {"pass": "applications/c"}},
 "applications": {"c": {"type": "external", "executable": "$1/build/lab_capp", "processes": $APP_PROCS}}}
EOF
    ;;
    rx0|rx1|rx32|drupal-ci)
        # Router only, no application: the route table is a file in conf/.
        sed "s/@PORT@/$PORT/" "/lab/conf/$WORKLOAD.json" ;;
    *) die "unknown WORKLOAD $WORKLOAD" ;;
    esac
}
url_for() {
    case "$WORKLOAD" in hello) echo "http://127.0.0.1:$PORT/hello.php" ;;
    server) echo "http://127.0.0.1:$PORT/server.php" ;;
    static1k|static1k-hdrs) echo "http://127.0.0.1:$PORT/1k.txt" ;; capp) echo "http://127.0.0.1:$PORT/" ;;
    rx0|drupal-ci) echo "http://127.0.0.1:$PORT/node/42/edit" ;;
    rx1|rx32) echo "http://127.0.0.1:$PORT/catalog/section/item42" ;; esac
}

start_unit() {  # tree
    rm -rf $R; mkdir -p $R/state $R/tmp; chmod 1777 $R/tmp
    taskset -c "$SERVER_CPUS" "$1/build/sbin/unitd" --control unix:$R/ctl.sock --pid $R/pid \
        --log $R/log --statedir $R/state --tmpdir $R/tmp \
        --modulesdir "$1/build/lib/unit/modules" --user nobody --group nogroup || die "unitd did not start ($1)"
    local i
    for i in $(seq 50); do [ -S $R/ctl.sock ] && break; sleep 0.1; done
    conf_for "$1" > $R/conf.json
    curl -s --unix-socket $R/ctl.sock -X PUT --data-binary @$R/conf.json http://l/config | grep -q success \
        || { tail -5 $R/log >&2; die "config rejected ($1)"; }
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' "$(url_for)")
    [ "$code" = 200 ] || { tail -5 $R/log >&2; die "workload answers $code, not 200 ($1)"; }
}
stop_unit() {
    local p; p=$(cat $R/pid 2>/dev/null)
    [ -n "$p" ] && kill -QUIT "$p" 2>/dev/null
    local i
    for i in $(seq 50); do pgrep -x unitd >/dev/null || break; sleep 0.1; done
    pkill -9 -x unitd 2>/dev/null   # inside this container only unitd of this job exists
    sleep 0.3
}
oha_args() {  # conns -> OHA_A; an array, so that the AE value keeps its blanks
    OHA_A=(-c "$1" --no-tui)
    [ -n "$RATE" ] && [ "$RATE" != 0 ] && OHA_A+=(-q "$RATE")
    [ -n "${AE:-}" ] && OHA_A+=(-H "Accept-Encoding: $AE")
    return 0
}
perf_parse() {  # file prefix ok -> "metric value" lines, per request
    awk -F, -v p="$2" -v n="$3" '
        $3 ~ /^(cycles|instructions|context-switches|cpu-migrations)/ && $1 ~ /^[0-9.]+$/ {
            e = $3; sub(/:.*/, "", e); sub(/^context-switches$/, "cs", e); sub(/^cpu-migrations$/, "migrations", e)
            printf "%s_%s %.4f\n", p, e, (n > 0 ? $1 / n : 0)
        }' "$1"
}

timed_cell() {  # phase side cell conns block slot tree
    local ph=$1 side=$2 cell=$3 c=$4 k=$5 s=$6 tree=$7
    start_unit "$tree"
    oha_args "$c"
    taskset -c "$LOAD_CPUS" oha -z "${WARM}s" "${OHA_A[@]}" "$(url_for)" >/dev/null 2>&1
    local rp all; rp=$(router_pid); all=$(unit_pids | tr '\n' ' ')
    [ -n "$rp" ] || die "no router process"
    local pr="" ps=""
    if [ "$PERF" = 1 ]; then
        perf stat -x, -e cycles,instructions,context-switches,cpu-migrations -o $R/perf.r -p "$rp" & pr=$!
        perf stat -x, -e cycles,instructions,context-switches,cpu-migrations -o $R/perf.s -p "$(echo $all | tr ' ' ',')" & ps=$!
        sleep 0.2
    fi
    local r0 a0 r1 a1
    r0=$(oncpu_ns $rp); a0=$(oncpu_ns $all)
    # -w: wait for in-flight requests at the deadline; without it oha counts
    # them as "aborted due to deadline" errors (one per connection).
    taskset -c "$LOAD_CPUS" oha -z "${DUR}s" -w "${OHA_A[@]}" --output-format json "$(url_for)" > $R/oha.json 2>/dev/null
    r1=$(oncpu_ns $rp); a1=$(oncpu_ns $all)
    if [ "$PERF" = 1 ]; then kill -INT $pr $ps 2>/dev/null; wait $pr $ps 2>/dev/null; fi
    local ok
    python3 - "$R/oha.json" > $R/oha.txt <<'PY' || die "cannot read oha json"
import json, sys
d = json.load(open(sys.argv[1]))
codes = d.get("statusCodeDistribution", {})
ok = int(codes.get("200", 0))
bad = sum(int(v) for k, v in codes.items() if k != "200") + sum(int(v) for v in d.get("errorDistribution", {}).values())
lp = d.get("latencyPercentiles", {})
print("ok", ok); print("bad", bad)
print("rps", round(d["summary"]["requestsPerSec"], 2))
print("p50_ms", round((lp.get("p50") or 0) * 1000, 4)); print("p99_ms", round((lp.get("p99") or 0) * 1000, 4))
PY
    ok=$(awk '$1 == "ok" {print $2}' $R/oha.txt)
    while read -r m v; do emit "$ph" "$side" "$cell" "$k" "$s" "$m" "$v"; done < $R/oha.txt
    local ru su
    ru=$(awk -v d=$((r1 - r0)) -v n="$ok" 'BEGIN{printf "%.3f", (n > 0 ? d / 1000 / n : 0)}')
    su=$(awk -v d=$((a1 - a0)) -v n="$ok" 'BEGIN{printf "%.3f", (n > 0 ? d / 1000 / n : 0)}')
    emit "$ph" "$side" "$cell" "$k" "$s" router_us "$ru"
    emit "$ph" "$side" "$cell" "$k" "$s" server_us "$su"
    if [ "$PERF" = 1 ]; then
        perf_parse $R/perf.r r "$ok" | while read -r m v; do emit "$ph" "$side" "$cell" "$k" "$s" "$m" "$v"; done
        perf_parse $R/perf.s s "$ok" | while read -r m v; do emit "$ph" "$side" "$cell" "$k" "$s" "$m" "$v"; done
    fi
    # Anonymous executable mappings in the router: PCRE2 JIT code lives in
    # them, so a JIT build shows more than a build without JIT.
    emit "$ph" "$side" "$cell" "$k" "$s" xmaps "$(awk '$2 ~ /x/ && NF == 5' /proc/$rp/maps 2>/dev/null | wc -l)"
    emit "$ph" "$side" "$cell" "$k" "$s" alerts "$(grep -cE '\[(alert|crit|emerg)\]' $R/log)"
    echo "  $ph $side $cell block $k slot $s: ok=$ok router_us=$ru server_us=$su"
    stop_unit
}

strace_parse() {  # file prefix ok
    awk -v p="$2" -v n="$3" '
        $1 ~ /^[0-9.]+$/ && NF >= 5 { c = $4; name = $NF
            if (name == "total") name = "total"
            if (n > 0 && c / n >= 0.01) printf "%s_%s %.4f\n", p, name, c / n }' "$1"
}

probe_cell() {  # phase side cell conns block slot tree
    local ph=$1 side=$2 cell=$3 c=$4 k=$5 s=$6 tree=$7
    start_unit "$tree"
    oha_args "$c"
    taskset -c "$LOAD_CPUS" oha -z "${WARM}s" "${OHA_A[@]}" "$(url_for)" >/dev/null 2>&1
    local rp apps; rp=$(router_pid); apps=$(app_pids | sed 's/^/-p /' | tr '\n' ' ')
    strace -c -f -q -o $R/st.r -p "$rp" & local sr=$!
    local sa=""
    [ -n "$apps" ] && { strace -c -f -q -o $R/st.a $apps & sa=$!; }
    sleep 0.5
    taskset -c "$LOAD_CPUS" oha -z "${STRACE_SEC}s" -w -c "$c" -q "$STRACE_RATE" ${AE:+-H} ${AE:+"Accept-Encoding: $AE"} --no-tui --output-format json "$(url_for)" > $R/oha.json 2>/dev/null
    kill -INT $sr $sa 2>/dev/null; wait $sr $sa 2>/dev/null
    local ok
    ok=$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["statusCodeDistribution"].get("200", 0)))' $R/oha.json)
    emit "$ph" "$side" "$cell" "$k" "$s" probe_ok "$ok"
    strace_parse $R/st.r rsys "$ok" | while read -r m v; do emit "$ph" "$side" "$cell" "$k" "$s" "$m" "$v"; done
    [ -n "$sa" ] && strace_parse $R/st.a asys "$ok" | while read -r m v; do emit "$ph" "$side" "$cell" "$k" "$s" "$m" "$v"; done
    echo "  $ph $side $cell block $k slot $s: strace probe ok=$ok"
    stop_unit
}

ORDER=(X Y Y X Y X X Y)
SWAP=(Y X X Y X Y Y X)
for k in $(seq 0 $((REPS - 1))); do
    if [ $((k % 2)) = 0 ]; then ord=("${ORDER[@]}"); else ord=("${SWAP[@]}"); fi
    for c in $CONNS; do
        cell="$WORKLOAD-c$c"
        for ph in $PHASES; do
            ty=/t/A; [ "$ph" = ab ] && ty=/t/B
            s=0
            for x in "${ord[@]}"; do
                if [ "$x" = X ]; then timed_cell "$ph" A "$cell" "$c" "$k" $s /t/A
                else timed_cell "$ph" B "$cell" "$c" "$k" $s "$ty"; fi
                s=$((s + 1))
            done
            if [ "$STRACE" = 1 ]; then
                if [ "${ord[0]}" = X ]; then
                    probe_cell "$ph" A "$cell" "$c" "$k" 8 /t/A; probe_cell "$ph" B "$cell" "$c" "$k" 9 "$ty"
                else
                    probe_cell "$ph" B "$cell" "$c" "$k" 8 "$ty"; probe_cell "$ph" A "$cell" "$c" "$k" 9 /t/A
                fi
            fi
        done
    done
done
echo CELLS-DONE
