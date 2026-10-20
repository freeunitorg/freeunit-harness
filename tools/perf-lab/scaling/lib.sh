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

# check_name WHAT VALUE: VALUE is one path component. sweep.sh builds paths from
# each variant and workload and runs rm -rf on them. run.sh has the same check.
check_name() {
    case $2 in
        ''|.|..|*[!A-Za-z0-9._-]*) die "$1 '$2': use letters, digits, '.', '_' and '-'; not '.' or '..'" ;;
    esac
}

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

default_n() {  # the profile default: 3 router threads, or the core count when it is smaller
    if [ "$NCORES" -lt 3 ]; then echo "$NCORES"; else echo 3; fi
}

usable_ns() {  # "1 2 3" -> the n that fit the cores; a warning on stderr for each other n
    local n out=""
    for n in $1; do
        if [ "$n" -le "$NCORES" ]; then out="$out $n"
        else echo "skipped n=$n: more than the $NCORES cores" >&2; fi
    done
    echo "${out# }"
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
                  "WARM=$WARM CONNS=$CONNS APP_PROCS=$APP_PROCS PROFILE='${PROFILE:-}' PERF_EVENT=${PERF_EVENT:-}" \
                  "PERF_SCOPE=${PERF_SCOPE:-}" ;;
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

# --- perf preflight -----------------------------------------------------------------------
# What perf can measure here: the sysctls, the PMU devices and the capabilities of
# this process. Tests point these paths at a fake tree. docs/perf/scaling-kit.md,
# "Perf privileges", says where each rule comes from and which ones are unverified.
: "${PROCSYS:=/proc/sys}" "${PROCSELF:=/proc/self}" "${EVSRC:=/sys/bus/event_source/devices}"
: "${TRACEFS:=/sys/kernel/tracing}" "${STRACE:=strace}"

cap_bit() {  # bit -> 1 when CapEff of $PROCSELF/status has it, else 0
    local hex
    hex=$(awk '/^CapEff:/ { print $2 }' "$PROCSELF/status" 2>/dev/null)
    [ -n "$hex" ] || { echo 0; return; }
    echo $(( (16#$hex >> $1) & 1 ))
}

init_userns() {  # true in the initial user namespace: there a capability counts for perf
    local a b c
    read -r a b c < "$PROCSELF/uid_map" 2>/dev/null || return 1
    [ "$a $b $c" = "0 0 4294967295" ]
}

# Sets PARANOID KPTR PTRACE_SCOPE HW_PMU MEM_PMU CAP_PERF CAP_SYSLOG CAP_PTRACE.
perf_facts() {
    local d
    PARANOID=$(cat "$PROCSYS/kernel/perf_event_paranoid" 2>/dev/null)
    KPTR=$(cat "$PROCSYS/kernel/kptr_restrict" 2>/dev/null) || KPTR=2
    PTRACE_SCOPE=$(cat "$PROCSYS/kernel/yama/ptrace_scope" 2>/dev/null) || PTRACE_SCOPE=0   # no Yama
    HW_PMU=0
    for d in "$EVSRC"/cpu "$EVSRC"/cpu_core "$EVSRC"/cpu_atom "$EVSRC"/armv[78]_*; do
        [ -e "$d" ] && HW_PMU=1
    done
    MEM_PMU=""
    if [ -e "$EVSRC/ibs_op" ]; then MEM_PMU=amd
    elif [ -e "$EVSRC/cpu/events/mem-loads" ] || [ -e "$EVSRC/cpu_core/events/mem-loads" ]; then MEM_PMU=intel
    else for d in "$EVSRC"/arm_spe*; do [ -e "$d" ] && MEM_PMU=arm; done
    fi
    # perf checks CAP_PERFMON and CAP_SYS_ADMIN in the initial user namespace only.
    # In a rootless container they are namespace capabilities, and perf ignores them.
    CAP_PERF=none CAP_SYSLOG=0
    if init_userns; then
        [ "$(cap_bit 38)" = 1 ] && CAP_PERF=perfmon
        [ "$(cap_bit 21)" = 1 ] && CAP_PERF=sys_admin
        CAP_SYSLOG=$(cap_bit 34)
    fi
    CAP_PTRACE=$(cap_bit 19)   # Yama accepts it in the user namespace of the target
}

_pf() {  # ok|unavailable metric text...: one report line; a requested metric that is unavailable fails
    local st=$1 m=$2
    shift 2
    echo "$m: $st${1:+ ($*)}"
    if [ "$st" = unavailable ] && [[ $PF_WANT == *" $m "* ]]; then PF_BAD=$((PF_BAD + 1)); fi
}

# perf_preflight METRIC...: METRIC is record, futex, c2c, c2cg or cpus (system-wide
# mode). Prints what works and what does not, with the sysctl or capability that
# is missing. Returns 1 when a requested metric cannot work. Sets PERF_LEVEL
# (full, task, user, software, none), PERF_USER_ONLY (0 or 1), C2C_FORM (full,
# user, amd-user or empty) and FUTEX_FORM (tracepoint, strace or empty).
# PERF_EVENT is the record event (cycles; cpu-clock is the clock, no PMU needed).
perf_preflight() {
    local why="" hwwhy="" c2cwhy="" ev=${PERF_EVENT:-cycles} kr seen
    PF_WANT=" $* " PF_BAD=0 PERF_USER_ONLY=0 C2C_FORM="" FUTEX_FORM=""
    perf_facts
    echo "facts: perf_event_paranoid=${PARANOID:-missing} kptr_restrict=$KPTR ptrace_scope=$PTRACE_SCOPE" \
         "hardware_pmu=$HW_PMU memory_sampling=${MEM_PMU:-none} perf_capability=$CAP_PERF" \
         "syslog_capability=$CAP_SYSLOG ptrace_capability=$CAP_PTRACE"
    if [ -z "$PARANOID" ]; then
        PERF_LEVEL=none why="no $PROCSYS/kernel/perf_event_paranoid: this kernel has no perf events"
    elif [ "$CAP_PERF" != none ]; then
        PERF_LEVEL=full
        if [ "$PARANOID" -ge 4 ] && [ "$CAP_PERF" = perfmon ]; then
            # KERNEL_RELEASE: uname -r; a test sets it.
            kr=${KERNEL_RELEASE:-$(uname -r)}
            case $kr in
                6.8.0-136-*) seen="Enough on this kernel, $kr (measured on pro4s-2)." ;;
                *) seen="Not measured on this kernel, $kr." ;;
            esac
            echo "note: perf_event_paranoid=$PARANOID and CAP_PERFMON only. $seen An Ubuntu noble kernel before" \
                 "6.8.0-98.98 needs CAP_SYS_ADMIN (read, not tested). profile.sh probes perf before it measures."
        fi
    elif [ "$PARANOID" -le 1 ]; then PERF_LEVEL=task
    elif [ "$PARANOID" -eq 2 ]; then PERF_LEVEL=user
    elif [ "$PARANOID" -eq 3 ]; then
        PERF_LEVEL=software
        why="perf_event_paranoid=3 allows software events only (reported for Debian and Ubuntu kernels, unverified)"
    else
        PERF_LEVEL=none
        why="perf_event_paranoid=$PARANOID: every perf call needs CAP_PERFMON (CAP_SYS_ADMIN on an older Ubuntu"
        why="$why kernel) in the initial user namespace, or the box admin sets kernel.perf_event_paranoid=2"
    fi
    echo "level: $PERF_LEVEL"
    case $PERF_LEVEL in user|software) PERF_USER_ONLY=1 ;; esac
    case $PERF_LEVEL in full|task|user) ;; *) hwwhy=$why ;; esac
    if [ "$HW_PMU" != 1 ] && [ -z "$hwwhy" ]; then
        hwwhy="no hardware PMU: no cpu, cpu_core, cpu_atom or armv*_ device in $EVSRC; no sysctl or capability adds one"
    fi

    # A. perf record
    case $ev in
        cpu-clock|task-clock)
            if [ "$PERF_LEVEL" = none ]; then _pf unavailable record "$why"
            else _pf ok record "$ev$([ "$PERF_USER_ONLY" = 1 ] && echo ', user space only')"; fi ;;
        *)
            if [ -z "$hwwhy" ]; then _pf ok record "$ev$([ "$PERF_USER_ONLY" = 1 ] && echo ', user space only')"
            else _pf unavailable record "$ev: $hwwhy. PERF_EVENT=cpu-clock samples the clock instead"; fi ;;
    esac

    # system-wide mode (PERF_SCOPE=cpus)
    if [ "$PERF_LEVEL" = full ] || { [ -n "$PARANOID" ] && [ "$PARANOID" -le 0 ]; }; then
        _pf ok cpus "perf -a -C on the server cpus"
    else
        _pf unavailable cpus "system-wide mode needs perf_event_paranoid <= 0 or CAP_PERFMON in the initial" \
            "user namespace"
    fi

    # C and D. perf c2c
    c2cwhy=$hwwhy
    if [ -z "$c2cwhy" ] && [ -z "$MEM_PMU" ]; then
        c2cwhy="no memory sampling PMU (Intel mem-loads, AMD ibs_op or arm_spe) in $EVSRC"
    fi
    # User space only on AMD needs the swfilt bit of IBS (kernel 6.14 or later). Without
    # it the event of amd-user does not parse: ibs_op alone proves nothing.
    if [ -z "$c2cwhy" ] && [ "$PERF_USER_ONLY" = 1 ] && [ "$MEM_PMU" = amd ] \
        && [ ! -e "$EVSRC/ibs_op/format/swfilt" ]; then
        c2cwhy="user space only on AMD needs ibs_op/swfilt=1/u, and $EVSRC/ibs_op/format has no swfilt"
        c2cwhy="$c2cwhy (kernel 6.14 or later); or CAP_PERFMON, or perf_event_paranoid <= 1"
    fi
    if [ -z "$c2cwhy" ]; then
        if [ "$PERF_USER_ONLY" = 0 ]; then C2C_FORM=full
        elif [ "$MEM_PMU" = amd ]; then C2C_FORM=amd-user   # ibs_op/swfilt=1/u, checked above
        else C2C_FORM=user; fi                               # perf c2c record --all-user, unverified
        _pf ok c2c "$C2C_FORM, $MEM_PMU"; _pf ok c2cg "$C2C_FORM, $MEM_PMU"
    else
        _pf unavailable c2c "$c2cwhy"; _pf unavailable c2cg "$c2cwhy"
    fi

    # B. futex calls: the tracepoint, or strace -p on the router
    if { [ "$PERF_LEVEL" = full ] || { [ -n "$PARANOID" ] && [ "$PARANOID" -le -1 ]; }; } \
        && [ -r "$TRACEFS/events/syscalls/sys_enter_futex/id" ]; then
        FUTEX_FORM=tracepoint
    elif command -v "$STRACE" >/dev/null 2>&1 \
        && { [ "$PTRACE_SCOPE" = 0 ] || { [ "$PTRACE_SCOPE" -le 2 ] && [ "$CAP_PTRACE" = 1 ]; }; }; then
        FUTEX_FORM=strace
    fi
    if [ -n "$FUTEX_FORM" ]; then _pf ok futex "$FUTEX_FORM"
    else
        _pf unavailable futex "the tracepoint needs perf_event_paranoid=-1 or CAP_PERFMON and a readable $TRACEFS;" \
            "strace -p needs $STRACE and ptrace_scope 0, or CAP_SYS_PTRACE at ptrace_scope 1 or 2"
    fi

    # not requested, only reported
    if [ "$PERF_LEVEL" = full ] || [ "$PERF_LEVEL" = task ]; then echo "kernel-samples: ok"
    else echo "kernel-samples: unavailable (perf_event_paranoid=${PARANOID:-missing}: user space only)"; fi
    if { [ "$KPTR" = 0 ] && [ -n "$PARANOID" ] && [ "$PARANOID" -le 1 ]; } \
        || { [ "$KPTR" -le 1 ] 2>/dev/null && [ "$CAP_SYSLOG" = 1 ]; }; then
        echo "kernel-symbols: ok"
    else
        echo "kernel-symbols: unavailable (kptr_restrict=$KPTR: needs kptr_restrict=0 with perf_event_paranoid <= 1," \
             "or CAP_SYSLOG in the initial user namespace at kptr_restrict 1)"
    fi
    if [ "$PF_BAD" != 0 ]; then
        echo "preflight: $PF_BAD requested metric(s) cannot work here; nothing was measured"
        return 1
    fi
    echo "preflight: ok"
}

# perf_args SCOPE PIDS CPUS: the target of every perf call in PERF_T (an array):
# "-p PIDS" (SCOPE pids, the default) or "-a -C CPUS" (SCOPE cpus, system-wide).
# PERF_U holds the options of perf record for user space only (PERF_USER_ONLY=1).
perf_args() {
    if [ "$1" = cpus ]; then PERF_T=(-a -C "$3"); else PERF_T=(-p "$2"); fi
    PERF_U=()
    if [ "${PERF_USER_ONLY:-0}" = 1 ]; then PERF_U=(--all-user --user-callchains); fi
}

# strace_futex PID SEC OUT ERR: strace -c -f of the futex calls of PID for SEC
# seconds, then SIGINT: strace detaches and writes the -c table to OUT. A strace
# that is still there 5 s later gets SIGKILL. Success means strace ran the full
# SEC seconds and stopped on the SIGINT. An attach that ptrace refuses, or a PID
# that is gone, ends strace early: that is a failure.
# No timeout(1): GNU timeout exits 124 after the signal, busybox timeout passes
# on the status of strace, and uutils timeout 0.10.0 with -k sends SIGKILL at
# once, before strace writes its table.
strace_futex() {
    local sp i rc=0
    "$STRACE" -c -f -e trace=futex -p "$1" -o "$3" 2>> "$4" &
    sp=$!
    for ((i = 0; i < $2 * 10; i++)); do kill -0 "$sp" 2>/dev/null || break; sleep 0.1; done
    if ! kill -0 "$sp" 2>/dev/null; then
        wait "$sp" || rc=$?
        echo "strace_futex: $STRACE exited $rc before $2 s" >> "$4"
        return 1
    fi
    kill -INT "$sp"
    for ((i = 0; i < 50; i++)); do kill -0 "$sp" 2>/dev/null || break; sleep 0.1; done
    if kill -0 "$sp" 2>/dev/null; then
        kill -KILL "$sp"; wait "$sp" 2>/dev/null
        echo "strace_futex: $STRACE did not stop within 5 s of SIGINT; killed" >> "$4"
        return 1
    fi
    wait "$sp" 2>/dev/null
    return 0
}

# c2c_record DATA ERR ARG...: perf c2c record in the form of the preflight
# (C2C_FORM). ARG... ends with "-- sleep S". amd-user is the user-space-only form
# for AMD IBS, which "perf c2c record --all-user" refuses in per-process mode;
# perf c2c report reads its data the same way.
c2c_record() {
    local data=$1 err=$2
    shift 2
    case ${C2C_FORM:-} in
        full) perf c2c record "${PERF_T[@]}" -o "$data" "$@" ;;
        user) perf c2c record --all-user "${PERF_T[@]}" -o "$data" "$@" ;;
        amd-user) perf record -W -d --sample-cpu -e ibs_op/swfilt=1/u "${PERF_T[@]}" -o "$data" "$@" ;;
        *) echo "c2c_record: no c2c form (the preflight says c2c is unavailable)" >&2; false ;;
    esac > /dev/null 2> "$err"
}
