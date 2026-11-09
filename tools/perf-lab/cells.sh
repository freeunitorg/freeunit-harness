#!/bin/sh
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

die() { printf 'cells.sh: %s\n' "$*" >&2; exit 1; }
[ -x /t/A/build/sbin/unitd ] || die "no /t/A/build/sbin/unitd"
[ -x /t/B/build/sbin/unitd ] || die "no /t/B/build/sbin/unitd"
command -v oha >/dev/null || die "no oha in the container"
if [ "$PERF" = 1 ]; then
    perf stat -e cycles true >/dev/null 2>&1 || die "perf stat does not work in this container (needs CAP_PERFMON and a perf binary)"
fi
if [ "$STRACE" = 1 ]; then command -v strace >/dev/null || die "no strace in the container"; fi

emit() {  # phase side cell block slot metric value
    em_rev=$REV_A
    [ "$1" = ab ] && [ "$2" = B ] && em_rev=$REV_B
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$JOB" "$1" "$2" "$em_rev" "$3" "$4" "$5" "$6" "$7" >> "$OUT"
}

# Every process descended from the unitd main process, the main included.
unit_pids() {
    up_main=$(cat $R/pid 2>/dev/null) || return 0
    ps -eo pid=,ppid= | awk -v m="$up_main" '
        { p[$1] = $2 } END {
            for (x in p) { y = x; while (y != "" && y != 0 && y != m) y = p[y]; if (y == m) print x }
        }'
}
router_pid() {
    for rp_p in $(unit_pids); do
        tr '\0' ' ' < /proc/$rp_p/cmdline 2>/dev/null | grep -q '^unit: router' && { echo "$rp_p"; return; }
    done
}
app_pids() {
    for ap_p in $(unit_pids); do
        ap_c=$(tr '\0' ' ' < /proc/$ap_p/cmdline 2>/dev/null)
        case "$ap_c" in *application*) echo "$ap_p" ;; "unit: "*) ;; "") ;; *) echo "$ap_p" ;; esac
    done
}
oncpu_ns() {  # pids... -> summed on-CPU nanoseconds of all their threads
    on_s=0
    for on_p in "$@"; do
        for on_t in /proc/$on_p/task/*/schedstat; do
            read -r on_v _ < "$on_t" 2>/dev/null && on_s=$((on_s + on_v))
        done
    done
    echo $on_s
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
    py-wsgi-len) py_conf wsgi wsgi_len application 1 ;;
    py-wsgi-stream) py_conf wsgi wsgi_stream application 1 ;;
    py-wsgi-threads) py_conf wsgi wsgi_len application 4 ;;
    py-asgi-len) py_conf asgi asgi_len application 1 ;;
    py-asgi-stream) py_conf asgi asgi_stream application 1 ;;
    py-fastapi) py_conf asgi fastapi_app app 1 /opt/pyvenv ;;
    *) die "unknown WORKLOAD $WORKLOAD" ;;
    esac
}
py_conf() {  # protocol module callable threads [home]: a Python app from /lab/www/py
    pc_home=""
    [ -n "${5:-}" ] && pc_home=", \"home\": \"$5\""
    cat <<EOF
{"listeners": {"127.0.0.1:$PORT": {"pass": "applications/py"}},
 "applications": {"py": {"type": "python", "protocol": "$1", "path": "/lab/www/py",
   "module": "$2", "callable": "$3", "processes": $APP_PROCS, "threads": $4$pc_home}}}
EOF
}
# py-*: the exact body, and how the response must be framed. *-stream has no
# Content-Length, so it goes out chunked; every other py-* has a Content-Length.
body_for() {
    case "$WORKLOAD" in
    py-fastapi) printf '{"message":"hello"}' ;;
    py-*) printf 'hello\n' ;;
    esac
}
framing_for() {
    case "$WORKLOAD" in py-*-stream) echo chunked ;; py-*) echo length ;; esac
}
url_for() {
    case "$WORKLOAD" in hello) echo "http://127.0.0.1:$PORT/hello.php" ;;
    server) echo "http://127.0.0.1:$PORT/server.php" ;;
    static1k|static1k-hdrs) echo "http://127.0.0.1:$PORT/1k.txt" ;; capp) echo "http://127.0.0.1:$PORT/" ;;
    rx0|drupal-ci) echo "http://127.0.0.1:$PORT/node/42/edit" ;;
    rx1|rx32) echo "http://127.0.0.1:$PORT/catalog/section/item42" ;;
    py-*) echo "http://127.0.0.1:$PORT/" ;; esac
}

start_unit() {  # tree
    rm -rf $R; mkdir -p $R/state $R/tmp; chmod 1777 $R/tmp
    taskset -c "$SERVER_CPUS" "$1/build/sbin/unitd" --control unix:$R/ctl.sock --pid $R/pid \
        --log $R/log --statedir $R/state --tmpdir $R/tmp \
        --modulesdir "$1/build/lib/unit/modules" --user nobody --group nogroup || die "unitd did not start ($1)"
    for _ in $(seq 50); do [ -S $R/ctl.sock ] && break; sleep 0.1; done
    conf_for "$1" > $R/conf.json
    curl -s --unix-socket $R/ctl.sock -X PUT --data-binary @$R/conf.json http://l/config | grep -q success \
        || { tail -5 $R/log >&2; die "config rejected ($1)"; }
    su_code=$(curl -s -o /dev/null -w '%{http_code}' "$(url_for)")
    [ "$su_code" = 200 ] || { tail -5 $R/log >&2; die "workload answers $su_code, not 200 ($1)"; }
    check_body "$1"
}
check_body() {  # tree: the exact body and the framing, for workloads that define them
    cb_f=$(framing_for)
    [ -n "$cb_f" ] || return 0
    body_for > $R/want.b
    curl -s -D $R/got.h -o $R/got.b "$(url_for)" || die "cannot fetch $(url_for) ($1)"
    cmp -s $R/want.b $R/got.b || die "$WORKLOAD body is '$(head -c 80 $R/got.b)', not '$(cat $R/want.b)' ($1)"
    cb_te=0 cb_cl=0
    grep -qi '^transfer-encoding: *chunked' $R/got.h && cb_te=1
    grep -qi '^content-length:' $R/got.h && cb_cl=1
    case "$cb_f$cb_te$cb_cl" in chunked10|length01) ;;
    *) die "$WORKLOAD must be framed '$cb_f'; got chunked=$cb_te content-length=$cb_cl ($1)" ;; esac
}
stop_unit() {
    sp_p=$(cat $R/pid 2>/dev/null)
    [ -n "$sp_p" ] && kill -QUIT "$sp_p" 2>/dev/null
    for _ in $(seq 50); do pgrep -x unitd >/dev/null || break; sleep 0.1; done
    pkill -9 -x unitd 2>/dev/null   # inside this container only unitd of this job exists
    sleep 0.3
}
oha_run() {  # conns oha-args... -> oha on LOAD_CPUS with -c, --no-tui, -q RATE and the AE header
    # sh has no arrays: ${X:+"..."} adds one quoted word, so the AE value keeps its blanks.
    or_c=$1; shift
    or_q=""
    [ -n "$RATE" ] && [ "$RATE" != 0 ] && or_q=$RATE
    taskset -c "$LOAD_CPUS" oha "$@" -c "$or_c" --no-tui ${or_q:+-q} ${or_q:+"$or_q"} \
        ${AE:+-H} ${AE:+"Accept-Encoding: $AE"} "$(url_for)"
}
perf_parse() {  # file prefix ok -> "metric value" lines, per request
    awk -F, -v p="$2" -v n="$3" '
        $3 ~ /^(cycles|instructions|context-switches|cpu-migrations)/ && $1 ~ /^[0-9.]+$/ {
            e = $3; sub(/:.*/, "", e); sub(/^context-switches$/, "cs", e); sub(/^cpu-migrations$/, "migrations", e)
            printf "%s_%s %.4f\n", p, e, (n > 0 ? $1 / n : 0)
        }' "$1"
}

timed_cell() {  # phase side cell conns block slot tree
    ph=$1 side=$2 cell=$3 c=$4 k=$5 s=$6 tree=$7
    start_unit "$tree"
    oha_run "$c" -z "${WARM}s" >/dev/null 2>&1
    rp=$(router_pid); all=$(unit_pids | tr '\n' ' ')
    [ -n "$rp" ] || die "no router process"
    pr="" ps=""
    if [ "$PERF" = 1 ]; then
        perf stat -x, -e cycles,instructions,context-switches,cpu-migrations -o $R/perf.r -p "$rp" & pr=$!
        perf stat -x, -e cycles,instructions,context-switches,cpu-migrations -o $R/perf.s -p "$(echo $all | tr ' ' ',')" & ps=$!
        sleep 0.2
    fi
    r0=$(oncpu_ns $rp); a0=$(oncpu_ns $all)
    # -w: wait for in-flight requests at the deadline; without it oha counts
    # them as "aborted due to deadline" errors (one per connection).
    oha_run "$c" -z "${DUR}s" -w --output-format json > $R/oha.json 2>/dev/null
    r1=$(oncpu_ns $rp); a1=$(oncpu_ns $all)
    if [ "$PERF" = 1 ]; then kill -INT $pr $ps 2>/dev/null; wait $pr $ps 2>/dev/null; fi
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
    ph=$1 side=$2 cell=$3 c=$4 k=$5 s=$6 tree=$7
    start_unit "$tree"
    oha_run "$c" -z "${WARM}s" >/dev/null 2>&1
    rp=$(router_pid); apps=$(app_pids | sed 's/^/-p /' | tr '\n' ' ')
    strace -c -f -q -o $R/st.r -p "$rp" & sr=$!
    sa=""
    [ -n "$apps" ] && { strace -c -f -q -o $R/st.a $apps & sa=$!; }
    sleep 0.5
    taskset -c "$LOAD_CPUS" oha -z "${STRACE_SEC}s" -w -c "$c" -q "$STRACE_RATE" ${AE:+-H} ${AE:+"Accept-Encoding: $AE"} --no-tui --output-format json "$(url_for)" > $R/oha.json 2>/dev/null
    kill -INT $sr $sa 2>/dev/null; wait $sr $sa 2>/dev/null
    ok=$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["statusCodeDistribution"].get("200", 0)))' $R/oha.json)
    emit "$ph" "$side" "$cell" "$k" "$s" probe_ok "$ok"
    strace_parse $R/st.r rsys "$ok" | while read -r m v; do emit "$ph" "$side" "$cell" "$k" "$s" "$m" "$v"; done
    [ -n "$sa" ] && strace_parse $R/st.a asys "$ok" | while read -r m v; do emit "$ph" "$side" "$cell" "$k" "$s" "$m" "$v"; done
    echo "  $ph $side $cell block $k slot $s: strace probe ok=$ok"
    stop_unit
}

# The cell functions use plain (global) variables, as sh has no "local";
# the loop below uses its own names (m_*), so a call cannot change them.
ORDER="X Y Y X Y X X Y"
SWAP="Y X X Y X Y Y X"
for m_k in $(seq 0 $((REPS - 1))); do
    if [ $((m_k % 2)) = 0 ]; then m_ord=$ORDER; else m_ord=$SWAP; fi
    for m_c in $CONNS; do
        m_cell="$WORKLOAD-c$m_c"
        for m_ph in $PHASES; do
            m_ty=/t/A; [ "$m_ph" = ab ] && m_ty=/t/B
            m_s=0
            for m_x in $m_ord; do
                if [ "$m_x" = X ]; then timed_cell "$m_ph" A "$m_cell" "$m_c" "$m_k" $m_s /t/A
                else timed_cell "$m_ph" B "$m_cell" "$m_c" "$m_k" $m_s "$m_ty"; fi
                m_s=$((m_s + 1))
            done
            if [ "$STRACE" = 1 ]; then
                if [ "${m_ord%% *}" = X ]; then
                    probe_cell "$m_ph" A "$m_cell" "$m_c" "$m_k" 8 /t/A; probe_cell "$m_ph" B "$m_cell" "$m_c" "$m_k" 9 "$m_ty"
                else
                    probe_cell "$m_ph" B "$m_cell" "$m_c" "$m_k" 8 "$m_ty"; probe_cell "$m_ph" A "$m_cell" "$m_c" "$m_k" 9 /t/A
                fi
            fi
        done
    done
done
echo CELLS-DONE
