#!/usr/bin/env bash
# lib.sh -- the shared functions of the router-thread scaling kit. sweep.sh and
# profile.sh source it; tools/tests/test_perf_lab_scaling.py sources it alone.
#
# Bash, not POSIX sh: sweep.sh and profile.sh source it inside the lab container
# or natively on the perf box. The tests source it under bash on the developer
# host (docs/shell.md).
#
# Inputs (environment, defaults below): TREES OUT R WWW PORT CONNS APP_PROCS OHA
# SYSCPU (the cpu directory of sysfs; a test points it at a fake tree).
# Docs: docs/perf/scaling-kit.md.

# awk and perf print numbers in the locale; the tables are read back as C numbers.
export LC_ALL=C

: "${TREES:=/t}" "${OUT:=/out}" "${R:=/tmp/prof-run}" "${WWW:=/www}" "${PORT:=18080}"
: "${CONNS:=128}" "${APP_PROCS:=4}" "${OHA:=oha}" "${SYSCPU:=/sys/devices/system/cpu}"

die() { echo "scaling: $*" >&2; exit 1; }

# Root (the lab container) drops the workers to nobody; a native run as a normal
# user keeps its own user.
USER_ARGS=""
[ "$(id -u)" = 0 ] && USER_ARGS="--user nobody --group nogroup"

# --- topology -------------------------------------------------------------------
# CORE_T0[i] and CORE_T1[i] are the first and second thread of physical core i,
# ordered by the first thread. CORE_T1 is empty without SMT.
declare -a CORE_T0 CORE_T1
NCORES=0

expand_cpus() {  # "0,4" or "0-1" or "0-1,4-5" -> "0 4" / "0 1" / "0 1 4 5"
    local part lo hi out=""
    for part in ${1//,/ }; do
        if [[ $part == *-* ]]; then
            lo=${part%-*}; hi=${part#*-}
            out="$out $(seq "$lo" "$hi" | tr '\n' ' ')"
        else
            out="$out $part"
        fi
    done
    # shellcheck disable=SC2086  # word splitting trims the blanks
    echo $out
}

read_topology() {  # fills CORE_T0, CORE_T1, NCORES from $SYSCPU
    local s c i=0 j
    local -a ord
    CORE_T0=(); CORE_T1=()
    for s in $(for c in "$SYSCPU"/cpu[0-9]*/topology/thread_siblings_list; do
                   [ -r "$c" ] && cat "$c"
               done | sort -u); do
        # shellcheck disable=SC2046
        set -- $(expand_cpus "$s")
        CORE_T0[i]=$1; CORE_T1[i]=${2:-}
        i=$((i + 1))
    done
    mapfile -t ord < <(for ((j = 0; j < i; j++)); do echo "${CORE_T0[j]} ${CORE_T1[j]}"; done | sort -n)
    for ((j = 0; j < i; j++)); do
        # shellcheck disable=SC2086
        set -- ${ord[j]}
        CORE_T0[j]=$1; CORE_T1[j]=${2:-}
    done
    NCORES=$i
}

cpus_server() {  # n -> the first thread of cores 0..n-1
    local n=$1 j out=""
    for ((j = 0; j < n; j++)); do out="$out,${CORE_T0[j]}"; done
    echo "${out#,}"
}

cpus_load() {  # n -> both threads of cores n..; the server siblings when no core is left
    local n=$1 j out=""
    if [ "$n" -lt "$NCORES" ]; then
        for ((j = n; j < NCORES; j++)); do
            out="$out,${CORE_T0[j]}"
            [ -n "${CORE_T1[j]}" ] && out="$out,${CORE_T1[j]}"
        done
    else
        for ((j = 0; j < NCORES; j++)); do
            [ -n "${CORE_T1[j]}" ] && out="$out,${CORE_T1[j]}"
        done
        # No SMT: no sibling either. The load then shares every cpu with the
        # server; without this the cpu list is empty and taskset fails.
        if [ -z "$out" ]; then
            for ((j = 0; j < NCORES; j++)); do out="$out,${CORE_T0[j]}"; done
        fi
    fi
    echo "${out#,}"
}

# --- own processes ----------------------------------------------------------------
# Only the processes of this kit: their exe is under $TREES. A shared box can
# hold unitd processes of other sessions under the same user, so never kill by
# name (pkill -x unitd). One ls for all processes: a readlink per process costs
# seconds on a box with a thousand processes, and stop_unit polls this.
my_pids() {
    # shellcheck disable=SC2012  # /proc/N/exe names are fixed; the target follows " -> "
    ls -l /proc/[0-9]*/exe 2>/dev/null | awk -v t="$TREES/" '{
        i = index($0, " -> ")
        if (i && substr($0, i + 4, length(t)) == t) {
            s = substr($0, 1, i - 1); sub(/.*\/proc\//, "", s); sub(/\/exe$/, "", s); print s
        }
    }'
}

# The lab container runs as root, and the box user cannot remove root files
# from the results directory. Give them to the owner of $OUT.
own_out() {
    if [ "$(id -u)" = 0 ] && [ -d "$OUT" ]; then chown -R "$(stat -c %u:%g "$OUT")" "$OUT" 2>/dev/null; fi
    return 0
}

# --- cpu accounting from /proc/stat -------------------------------------------------
cpu_ticks() {  # "0,1,2" -> "busy total"
    awk -v list="$1" 'BEGIN{n=split(list,a,","); for(i=1;i<=n;i++) w["cpu" a[i]]=1}
        $1 in w {t=0; for(i=2;i<=9;i++) t+=$i; b=t-$5-$6; B+=b; T+=t} END{print B+0, T+0}' /proc/stat
}

# --- workloads ----------------------------------------------------------------------
setup_www() {
    rm -rf "$WWW"; mkdir -p "$WWW"
    head -c 1024 /dev/zero | tr '\0' 'x' > "$WWW/1k.txt"
    chmod -R a+rX "$WWW"
    if [ "$(id -u)" = 0 ]; then chmod a+x /; fi
}

conf_for() {  # variant workload n
    case "$2" in
    capp) cat <<EOF
{"settings": {"listen_threads": $3},
 "listeners": {"127.0.0.1:$PORT": {"pass": "applications/c"}},
 "applications": {"c": {"type": "external", "executable": "$TREES/$1/build/lab_capp", "processes": $APP_PROCS}}}
EOF
    ;;
    static) cat <<EOF
{"settings": {"listen_threads": $3},
 "listeners": {"127.0.0.1:$PORT": {"pass": "routes/s"}},
 "routes": {"s": [{"action": {"share": "$WWW\$uri"}}]}}
EOF
    ;;
    *) die "unknown workload $2" ;;
    esac
}

url_for() {
    case "$1" in
    capp) echo "http://127.0.0.1:$PORT/" ;;
    static) echo "http://127.0.0.1:$PORT/1k.txt" ;;
    esac
}

start_unit() {  # variant workload n -> sets SRV_PID; PRELOAD adds LD_PRELOAD
    local v=$1 w=$2 n=$3 srv i code
    srv=$(cpus_server "$n")
    rm -rf "$R"; mkdir -p "$R/state" "$R/tmp"; chmod 1777 "$R/tmp"; chmod a+rx "$R"
    # shellcheck disable=SC2086  # USER_ARGS is two words or none
    taskset -c "$srv" env ${PRELOAD:+LD_PRELOAD=$PRELOAD} "$TREES/$v/build/sbin/unitd" \
        --control "unix:$R/ctl.sock" --pid "$R/pid" --log "$R/log" --statedir "$R/state" \
        --tmpdir "$R/tmp" --modulesdir "$TREES/$v/build/lib/unit/modules" $USER_ARGS \
        || die "unitd does not start ($v)"
    for i in $(seq 100); do [ -S "$R/ctl.sock" ] && break; sleep 0.1; done
    conf_for "$v" "$w" "$n" > "$R/conf.json"
    curl -s --unix-socket "$R/ctl.sock" -X PUT --data-binary "@$R/conf.json" http://l/config | grep -q success \
        || { tail -5 "$R/log" >&2; die "config rejected ($v $w $n)"; }
    for i in $(seq 50); do
        code=$(curl -s -o /dev/null -w '%{http_code}' "$(url_for "$w")")
        [ "$code" = 200 ] && break
        sleep 0.1
    done
    [ "$code" = 200 ] || { tail -5 "$R/log" >&2; die "workload answers $code ($v $w $n)"; }
    SRV_PID=$(cat "$R/pid")
}

stop_unit() {  # QUIT to the main process, then SIGKILL to what is left of our own
    local i left
    [ -n "${SRV_PID:-}" ] && kill -QUIT "$SRV_PID" 2>/dev/null
    for i in $(seq 50); do [ -z "$(my_pids)" ] && break; sleep 0.1; done
    left=$(my_pids)
    # shellcheck disable=SC2086
    [ -n "$left" ] && kill -9 $left 2>/dev/null
    SRV_PID=""
    sleep 0.3
}

oha_run() {  # seconds cpus url [json-file]
    if [ -n "${4:-}" ]; then
        taskset -c "$2" "$OHA" -z "${1}s" -w -c "$CONNS" --no-tui --output-format json "$3" > "$4" 2>/dev/null
    else
        taskset -c "$2" "$OHA" -z "${1}s" -c "$CONNS" --no-tui "$3" >/dev/null 2>&1
    fi
}

# A missing or unreadable result is a failed load: read_oha prints nothing and
# exits 1. It never turns it into a zero-throughput row.
read_oha() {  # json -> "ok bad rps p50_ms p99_ms"
    python3 - "$1" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    rps = float(d["summary"]["requestsPerSec"])
except Exception as e:
    sys.exit("read_oha: no oha result in %s: %s" % (sys.argv[1], e))
codes = d.get("statusCodeDistribution", {})
ok = int(codes.get("200", 0))
bad = sum(int(v) for k, v in codes.items() if k != "200") + sum(int(v) for v in d.get("errorDistribution", {}).values())
lp = d.get("latencyPercentiles", {})
print(ok, bad, round(rps, 1), round((lp.get("p50") or 0) * 1000, 4), round((lp.get("p99") or 0) * 1000, 4))
PY
}

# finish_summary FILE CMD...: run CMD into FILE.tmp. On success move it to FILE
# and write $OUT/DONE. On failure keep the output as FILE.failed, write the exit
# status to $OUT/FAILED and return 1: a partial table is never FILE, and
# run.sh watch keys off DONE and FAILED, not off a non-empty FILE.
finish_summary() {
    local f=$1 rc=0
    shift
    "$@" > "$f.tmp" || rc=$?
    if [ "$rc" = 0 ]; then
        mv "$f.tmp" "$f"; cat "$f"; : > "$OUT/DONE"; rm -f "$OUT/FAILED"
        return 0
    fi
    mv "$f.tmp" "$f.failed"; cat "$f.failed"
    echo "${1##*/} ${2##*/} exited $rc; the output is ${f##*/}.failed" | tee "$OUT/FAILED" >&2
    rm -f "$OUT/DONE"
    return 1
}

# --- meta.txt -------------------------------------------------------------------------
# params_line sweep|locks|profile: the knobs that this kind of run uses, and only
# those. A locks run uses LRUNS and LDUR, not RUNS and DUR.
params_line() {
    case $1 in
    sweep) echo "params VARIANTS='$VARIANTS' WORKLOADS='$WORKLOADS' NS='$NS' RUNS=$RUNS DUR=$DUR WARM=$WARM" \
                "CONNS=$CONNS APP_PROCS=$APP_PROCS PAIRS='${PAIRS:-}'" ;;
    locks) echo "params LOCKS=1 VARIANTS='$VARIANTS' WORKLOADS='$WORKLOADS' NS='$NS' LRUNS=$LRUNS LDUR=$LDUR" \
                "WARM=$WARM CONNS=$CONNS APP_PROCS=$APP_PROCS SHIM='${SHIM:-}'" ;;
    profile) echo "params N=$N V=$V WORKLOADS='$WORKLOADS' PERF_SEC=$PERF_SEC C2C_G=$C2C_G LOAD_SEC=$LOAD_SEC" \
                  "WARM=$WARM CONNS=$CONNS APP_PROCS=$APP_PROCS" ;;
    *) die "params_line: unknown mode $1" ;;
    esac
}

# write_meta MODE VARIANT...: the box, the parameters and the sha of each tree, to $OUT/meta.txt.
write_meta() {
    local mode=$1 v i
    shift
    {
        echo "date $(date -u +%FT%TZ)"
        echo "kernel $(uname -r)"
        echo "cpu $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2)"
        echo "governor $(cat "$SYSCPU/cpu0/cpufreq/scaling_governor" 2>/dev/null)" \
             "epp $(cat "$SYSCPU/cpu0/cpufreq/energy_performance_preference" 2>/dev/null)" \
             "no_turbo $(cat "$SYSCPU/intel_pstate/no_turbo" 2>/dev/null)"
        echo "smt $(cat "$SYSCPU/smt/active" 2>/dev/null)  cores $NCORES"
        for ((i = 0; i < NCORES; i++)); do echo "core$i ${CORE_T0[i]},${CORE_T1[i]}"; done
        echo "oha $("$OHA" --version 2>&1 | head -1)"
        params_line "$mode"
        for v in "$@"; do echo "variant $v $(cat "$TREES/$v/.sha" 2>/dev/null)"; done
    } > "$OUT/meta.txt"
}
