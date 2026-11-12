#!/bin/sh
# lib.sh -- the shared functions of the router-thread scaling kit. sweep.sh and
# profile.sh source it; tools/tests/test_perf_lab_scaling.py sources it alone.
#
# POSIX sh (docs/shell.md): dash, busybox ash and bash run it. There is no
# local: the variables of each function have a prefix of their own.
#
# Inputs (environment, defaults below): TREES OUT R WWW PORT CONNS APP_PROCS OHA
# SYSCPU (the cpu directory of sysfs; a test points it at a fake tree).
# Docs: docs/perf/scaling-kit.md.

# awk and perf print numbers in the locale; the tables are read back as C numbers.
export LC_ALL=C

: "${TREES:=/t}" "${OUT:=/out}" "${R:=/tmp/prof-run}" "${WWW:=/www}" "${PORT:=18080}"
: "${CONNS:=128}" "${APP_PROCS:=4}" "${OHA:=oha}" "${SYSCPU:=/sys/devices/system/cpu}"

die() { printf 'scaling: %s\n' "$*" >&2; exit 1; }

# exit_traps CMD: run CMD when the shell exits. dash and busybox ash do not run
# the EXIT trap when a signal ends the shell; bash does. So HUP, INT and TERM
# run CMD too, and then the shell dies by that signal, as it does in bash. QUIT
# stays as it is: bash ignores it, and an ignored QUIT would pass to unitd.
# shellcheck disable=SC2064  # CMD and the signal go into the trap now, on purpose
exit_traps() {
    trap "$1" EXIT
    for et_sig in HUP INT TERM; do
        trap "trap - EXIT $et_sig; $1
kill -s $et_sig \$\$" "$et_sig"
    done
}

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
# CORES holds one line "T0 T1" for each physical core: its first and its second
# thread, ordered by the first thread. T1 is empty without SMT.
CORES=""
NCORES=0

expand_cpus() {  # "0,4" or "0-1" or "0-1,4-5" -> "0 4" / "0 1" / "0 1 4 5"
    ec_out=""
    for ec_part in $(printf '%s\n' "$1" | tr , ' '); do
        case $ec_part in
            *-*) ec_out="$ec_out $(seq "${ec_part%-*}" "${ec_part#*-}" | tr '\n' ' ')" ;;
            *) ec_out="$ec_out $ec_part" ;;
        esac
    done
    # shellcheck disable=SC2086  # word splitting trims the blanks
    echo $ec_out
}

read_topology() {  # sets CORES and NCORES from $SYSCPU
    CORES=$(for rt_c in "$SYSCPU"/cpu[0-9]*/topology/thread_siblings_list; do
                [ -r "$rt_c" ] && cat "$rt_c"
            done | sort -u | while read -r rt_s; do
                [ -n "$rt_s" ] || continue
                # shellcheck disable=SC2046  # one word per cpu
                set -- $(expand_cpus "$rt_s")
                echo "$1 ${2:-}"
            done | sort -n)
    NCORES=0
    [ -z "$CORES" ] || NCORES=$(($(printf '%s\n' "$CORES" | wc -l)))
}

# core_list FROM TO WHICH: ",T" for the threads WHICH (1, 2 or 12) of the cores
# FROM to TO-1. An empty second thread adds nothing. A core past the last one
# adds an empty first thread, so the list keeps one comma per requested core.
core_list() {
    cl_i=0 cl_out=""
    while read -r cl_t0 cl_t1; do
        [ -n "$cl_t0" ] || continue
        if [ "$cl_i" -ge "$1" ] && [ "$cl_i" -lt "$2" ]; then
            case $3 in *1*) cl_out="$cl_out,$cl_t0" ;; esac
            case $3 in *2*) [ -n "$cl_t1" ] && cl_out="$cl_out,$cl_t1" ;; esac
        fi
        cl_i=$((cl_i + 1))
    done <<EOF
$CORES
EOF
    case $3 in
        *1*) while [ "$cl_i" -lt "$2" ]; do
                 [ "$cl_i" -ge "$1" ] && cl_out="$cl_out,"
                 cl_i=$((cl_i + 1))
             done ;;
    esac
    printf '%s\n' "$cl_out"
}

default_n() {  # the profile default: 3 router threads, or the core count when it is smaller
    if [ "$NCORES" -lt 3 ]; then echo "$NCORES"; else echo 3; fi
}

usable_ns() {  # "1 2 3" -> the n that fit the cores; a warning on stderr for each other n
    un_out=""
    for un_n in $1; do
        if [ "$un_n" -le "$NCORES" ]; then un_out="$un_out $un_n"
        else printf '%s\n' "skipped n=$un_n: more than the $NCORES cores" >&2; fi
    done
    printf '%s\n' "${un_out# }"
}

cpus_server() {  # n -> the first thread of cores 0..n-1
    cs_out=$(core_list 0 "$1" 1)
    echo "${cs_out#,}"
}

cpus_load() {  # n -> both threads of cores n..; the server siblings when no core is left
    if [ "$1" -lt "$NCORES" ]; then
        cd_out=$(core_list "$1" "$NCORES" 12)
    else
        cd_out=$(core_list 0 "$NCORES" 2)
        # No SMT: no sibling either. The load then shares every cpu with the
        # server; without this the cpu list is empty and taskset fails.
        [ -n "$cd_out" ] || cd_out=$(core_list 0 "$NCORES" 1)
    fi
    echo "${cd_out#,}"
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
    su_srv=$(cpus_server "$3")
    rm -rf "$R"; mkdir -p "$R/state" "$R/tmp"; chmod 1777 "$R/tmp"; chmod a+rx "$R"
    # shellcheck disable=SC2086  # USER_ARGS is two words or none
    taskset -c "$su_srv" env ${PRELOAD:+LD_PRELOAD=$PRELOAD} "$TREES/$1/build/sbin/unitd" \
        --control "unix:$R/ctl.sock" --pid "$R/pid" --log "$R/log" --statedir "$R/state" \
        --tmpdir "$R/tmp" --modulesdir "$TREES/$1/build/lib/unit/modules" $USER_ARGS \
        || die "unitd does not start ($1)"
    for _ in $(seq 100); do [ -S "$R/ctl.sock" ] && break; sleep 0.1; done
    conf_for "$1" "$2" "$3" > "$R/conf.json"
    curl -s --unix-socket "$R/ctl.sock" -X PUT --data-binary "@$R/conf.json" http://l/config | grep -q success \
        || { tail -5 "$R/log" >&2; die "config rejected ($1 $2 $3)"; }
    su_code=""
    for _ in $(seq 50); do
        su_code=$(curl -s -o /dev/null -w '%{http_code}' "$(url_for "$2")")
        [ "$su_code" = 200 ] && break
        sleep 0.1
    done
    [ "$su_code" = 200 ] || { tail -5 "$R/log" >&2; die "workload answers $su_code ($1 $2 $3)"; }
    SRV_PID=$(cat "$R/pid")
}

stop_unit() {  # QUIT to the main process, then SIGKILL to what is left of our own
    [ -n "${SRV_PID:-}" ] && kill -QUIT "$SRV_PID" 2>/dev/null
    for _ in $(seq 50); do [ -z "$(my_pids)" ] && break; sleep 0.1; done
    sp_left=$(my_pids)
    # shellcheck disable=SC2086
    [ -n "$sp_left" ] && kill -9 $sp_left 2>/dev/null
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
    fs_f=$1 fs_rc=0
    shift
    "$@" > "$fs_f.tmp" || fs_rc=$?
    if [ "$fs_rc" = 0 ]; then
        mv "$fs_f.tmp" "$fs_f"; cat "$fs_f"; : > "$OUT/DONE"; rm -f "$OUT/FAILED"
        return 0
    fi
    mv "$fs_f.tmp" "$fs_f.failed"; cat "$fs_f.failed"
    printf '%s\n' "${1##*/} ${2##*/} exited $fs_rc; the output is ${fs_f##*/}.failed" | tee "$OUT/FAILED" >&2
    rm -f "$OUT/DONE"
    return 1
}

# --- meta.txt -------------------------------------------------------------------------
# params_line sweep|locks|profile: the knobs that this kind of run uses, and only
# those. A locks run uses LRUNS and LDUR, not RUNS and DUR.
params_line() {
    case $1 in
    sweep) printf '%s %s\n' "params VARIANTS='$VARIANTS' WORKLOADS='$WORKLOADS' NS='$NS' RUNS=$RUNS DUR=$DUR WARM=$WARM" \
                "CONNS=$CONNS APP_PROCS=$APP_PROCS PAIRS='${PAIRS:-}'" ;;
    locks) printf '%s %s\n' "params LOCKS=1 VARIANTS='$VARIANTS' WORKLOADS='$WORKLOADS' NS='$NS' LRUNS=$LRUNS LDUR=$LDUR" \
                "WARM=$WARM CONNS=$CONNS APP_PROCS=$APP_PROCS SHIM='${SHIM:-}'" ;;
    profile) printf '%s %s %s\n' "params N=$N V=$V WORKLOADS='$WORKLOADS' PERF_SEC=$PERF_SEC C2C_G=$C2C_G LOAD_SEC=$LOAD_SEC" \
                  "WARM=$WARM CONNS=$CONNS APP_PROCS=$APP_PROCS PROFILE='${PROFILE:-}' PERF_EVENT=${PERF_EVENT:-}" \
                  "PERF_SCOPE=${PERF_SCOPE:-}" ;;
    *) die "params_line: unknown mode $1" ;;
    esac
}

# write_meta MODE VARIANT...: the box, the parameters and the sha of each tree, to $OUT/meta.txt.
write_meta() {
    wm_mode=$1
    shift
    {
        printf '%s\n' "date $(date -u +%FT%TZ)"
        printf '%s\n' "kernel $(uname -r)"
        printf '%s\n' "cpu $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2)"
        printf '%s %s %s\n' "governor $(cat "$SYSCPU/cpu0/cpufreq/scaling_governor" 2>/dev/null)" \
             "epp $(cat "$SYSCPU/cpu0/cpufreq/energy_performance_preference" 2>/dev/null)" \
             "no_turbo $(cat "$SYSCPU/intel_pstate/no_turbo" 2>/dev/null)"
        printf '%s\n' "smt $(cat "$SYSCPU/smt/active" 2>/dev/null)  cores $NCORES"
        wm_i=0
        while read -r wm_t0 wm_t1; do
            [ -n "$wm_t0" ] || continue
            echo "core$wm_i $wm_t0,$wm_t1"; wm_i=$((wm_i + 1))
        done <<EOF
$CORES
EOF
        printf '%s\n' "oha $("$OHA" --version 2>&1 | head -1)"
        params_line "$wm_mode"
        for wm_v in "$@"; do printf '%s\n' "variant $wm_v $(cat "$TREES/$wm_v/.sha" 2>/dev/null)"; done
    } > "$OUT/meta.txt"
}

# --- perf preflight -----------------------------------------------------------------------
# What perf can measure here: the sysctls, the PMU devices and the capabilities of
# this process. Tests point these paths at a fake tree. docs/perf/scaling-kit.md,
# "Perf privileges", says where each rule comes from and which ones are unverified.
: "${PROCSYS:=/proc/sys}" "${PROCSELF:=/proc/self}" "${EVSRC:=/sys/bus/event_source/devices}"
: "${TRACEFS:=/sys/kernel/tracing}" "${STRACE:=strace}"

cap_bit() {  # bit -> 1 when CapEff of $PROCSELF/status has it, else 0
    cb_hex=$(awk '/^CapEff:/ { print $2 }' "$PROCSELF/status" 2>/dev/null)
    [ -n "$cb_hex" ] || { echo 0; return; }
    echo $(( (0x$cb_hex >> $1) & 1 ))
}

init_userns() {  # true in the initial user namespace: there a capability counts for perf
    read -r iu_a iu_b iu_c < "$PROCSELF/uid_map" 2>/dev/null || return 1
    [ "$iu_a $iu_b $iu_c" = "0 0 4294967295" ]
}

# Sets PARANOID KPTR PTRACE_SCOPE HW_PMU MEM_PMU CAP_PERF CAP_SYSLOG CAP_PTRACE.
perf_facts() {
    PARANOID=$(cat "$PROCSYS/kernel/perf_event_paranoid" 2>/dev/null)
    KPTR=$(cat "$PROCSYS/kernel/kptr_restrict" 2>/dev/null) || KPTR=2
    PTRACE_SCOPE=$(cat "$PROCSYS/kernel/yama/ptrace_scope" 2>/dev/null) || PTRACE_SCOPE=0   # no Yama
    HW_PMU=0
    for pf_d in "$EVSRC"/cpu "$EVSRC"/cpu_core "$EVSRC"/cpu_atom "$EVSRC"/armv[78]_*; do
        [ -e "$pf_d" ] && HW_PMU=1
    done
    MEM_PMU=""
    if [ -e "$EVSRC/ibs_op" ]; then MEM_PMU=amd
    elif [ -e "$EVSRC/cpu/events/mem-loads" ] || [ -e "$EVSRC/cpu_core/events/mem-loads" ]; then MEM_PMU=intel
    else for pf_d in "$EVSRC"/arm_spe*; do [ -e "$pf_d" ] && MEM_PMU=arm; done
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
    _pf_st=$1 _pf_m=$2
    shift 2
    printf '%s\n' "$_pf_m: $_pf_st${1:+ ($*)}"
    if [ "$_pf_st" = unavailable ]; then
        case $PF_WANT in *" $_pf_m "*) PF_BAD=$((PF_BAD + 1)) ;; esac
    fi
}

# perf_preflight METRIC...: METRIC is record, futex, c2c, c2cg or cpus (system-wide
# mode). Prints what works and what does not, with the sysctl or capability that
# is missing. Returns 1 when a requested metric cannot work. Sets PERF_LEVEL
# (full, task, user, software, none), PERF_USER_ONLY (0 or 1), C2C_FORM (full,
# user, amd-user or empty) and FUTEX_FORM (tracepoint, strace or empty).
# PERF_EVENT is the record event (cycles; cpu-clock is the clock, no PMU needed).
perf_preflight() {
    pp_why="" pp_hwwhy="" pp_c2cwhy="" pp_ev=${PERF_EVENT:-cycles}
    PF_WANT=" $* " PF_BAD=0 PERF_USER_ONLY=0 C2C_FORM="" FUTEX_FORM=""
    perf_facts
    printf '%s %s %s\n' "facts: perf_event_paranoid=${PARANOID:-missing} kptr_restrict=$KPTR ptrace_scope=$PTRACE_SCOPE" \
         "hardware_pmu=$HW_PMU memory_sampling=${MEM_PMU:-none} perf_capability=$CAP_PERF" \
         "syslog_capability=$CAP_SYSLOG ptrace_capability=$CAP_PTRACE"
    if [ -z "$PARANOID" ]; then
        PERF_LEVEL=none pp_why="no $PROCSYS/kernel/perf_event_paranoid: this kernel has no perf events"
    elif [ "$CAP_PERF" != none ]; then
        PERF_LEVEL=full
        if [ "$PARANOID" -ge 4 ] && [ "$CAP_PERF" = perfmon ]; then
            # The release as uname -r prints it; a test fakes PROCSYS.
            pp_kr=$(cat "$PROCSYS/kernel/osrelease" 2>/dev/null) || pp_kr=unknown
            case $pp_kr in
                6.8.0-136-generic) pp_seen="Enough on this kernel, $pp_kr (measured on the lab box)." ;;
                *) pp_seen="Not measured on this kernel, $pp_kr." ;;
            esac
            printf '%s %s\n' "note: perf_event_paranoid=$PARANOID and CAP_PERFMON only. $pp_seen An Ubuntu noble kernel before" \
                 "6.8.0-98.98 needs CAP_SYS_ADMIN (read, not tested). profile.sh probes perf before it measures."
        fi
    elif [ "$PARANOID" -le 1 ]; then PERF_LEVEL=task
    elif [ "$PARANOID" -eq 2 ]; then PERF_LEVEL=user
    elif [ "$PARANOID" -eq 3 ]; then
        PERF_LEVEL=software
        pp_why="perf_event_paranoid=3 allows software events only (reported for Debian and Ubuntu kernels, unverified)"
    else
        PERF_LEVEL=none
        pp_why="perf_event_paranoid=$PARANOID: every perf call needs CAP_PERFMON (CAP_SYS_ADMIN on an older Ubuntu"
        pp_why="$pp_why kernel) in the initial user namespace, or the box admin sets kernel.perf_event_paranoid=2"
    fi
    echo "level: $PERF_LEVEL"
    case $PERF_LEVEL in user|software) PERF_USER_ONLY=1 ;; esac
    case $PERF_LEVEL in full|task|user) ;; *) pp_hwwhy=$pp_why ;; esac
    if [ "$HW_PMU" != 1 ] && [ -z "$pp_hwwhy" ]; then
        pp_hwwhy="no hardware PMU: no cpu, cpu_core, cpu_atom or armv*_ device in $EVSRC; no sysctl or capability adds one"
    fi

    # A. perf record
    case $pp_ev in
        cpu-clock|task-clock)
            if [ "$PERF_LEVEL" = none ]; then _pf unavailable record "$pp_why"
            else _pf ok record "$pp_ev$([ "$PERF_USER_ONLY" = 1 ] && echo ', user space only')"; fi ;;
        *)
            if [ -z "$pp_hwwhy" ]; then _pf ok record "$pp_ev$([ "$PERF_USER_ONLY" = 1 ] && echo ', user space only')"
            else _pf unavailable record "$pp_ev: $pp_hwwhy. PERF_EVENT=cpu-clock samples the clock instead"; fi ;;
    esac

    # system-wide mode (PERF_SCOPE=cpus)
    if [ "$PERF_LEVEL" = full ] || { [ -n "$PARANOID" ] && [ "$PARANOID" -le 0 ]; }; then
        _pf ok cpus "perf -a -C on the server cpus"
    else
        _pf unavailable cpus "system-wide mode needs perf_event_paranoid <= 0 or CAP_PERFMON in the initial" \
            "user namespace"
    fi

    # C and D. perf c2c
    pp_c2cwhy=$pp_hwwhy
    if [ -z "$pp_c2cwhy" ] && [ -z "$MEM_PMU" ]; then
        pp_c2cwhy="no memory sampling PMU (Intel mem-loads, AMD ibs_op or arm_spe) in $EVSRC"
    fi
    # User space only on AMD needs the swfilt bit of IBS (kernel 6.14 or later). Without
    # it the event of amd-user does not parse: ibs_op alone proves nothing.
    if [ -z "$pp_c2cwhy" ] && [ "$PERF_USER_ONLY" = 1 ] && [ "$MEM_PMU" = amd ] \
        && [ ! -e "$EVSRC/ibs_op/format/swfilt" ]; then
        pp_c2cwhy="user space only on AMD needs ibs_op/swfilt=1/u, and $EVSRC/ibs_op/format has no swfilt"
        pp_c2cwhy="$pp_c2cwhy (kernel 6.14 or later); or CAP_PERFMON, or perf_event_paranoid <= 1"
    fi
    if [ -z "$pp_c2cwhy" ]; then
        if [ "$PERF_USER_ONLY" = 0 ]; then C2C_FORM=full
        elif [ "$MEM_PMU" = amd ]; then C2C_FORM=amd-user   # ibs_op/swfilt=1/u, checked above
        else C2C_FORM=user; fi                               # perf c2c record --all-user, unverified
        _pf ok c2c "$C2C_FORM, $MEM_PMU"; _pf ok c2cg "$C2C_FORM, $MEM_PMU"
    else
        _pf unavailable c2c "$pp_c2cwhy"; _pf unavailable c2cg "$pp_c2cwhy"
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
        printf '%s %s\n' "kernel-symbols: unavailable (kptr_restrict=$KPTR: needs kptr_restrict=0 with perf_event_paranoid <= 1," \
             "or CAP_SYSLOG in the initial user namespace at kptr_restrict 1)"
    fi
    if [ "$PF_BAD" != 0 ]; then
        echo "preflight: $PF_BAD requested metric(s) cannot work here; nothing was measured"
        return 1
    fi
    echo "preflight: ok"
}

# perf_args SCOPE PIDS CPUS: the target of every perf call, in two variables:
# PERF_T holds the options and PERF_TA their value. SCOPE pids (the default)
# gives "-p PIDS"; SCOPE cpus gives "-a -C CPUS" (system-wide). PERF_U holds the
# options of perf record for user space only (PERF_USER_ONLY=1), or nothing.
# Use them as $PERF_T ${PERF_TA+"$PERF_TA"} $PERF_U: no word of PERF_T or
# PERF_U holds a blank or a pattern character, and an unset PERF_TA with an
# empty PERF_T is no target.
# shellcheck disable=SC2034  # PERF_U is read by profile.sh
perf_args() {
    if [ "$1" = cpus ]; then PERF_T="-a -C" PERF_TA=$3; else PERF_T=-p PERF_TA=$2; fi
    PERF_U=""
    if [ "${PERF_USER_ONLY:-0}" = 1 ]; then PERF_U="--all-user --user-callchains"; fi
}

# strace_futex PID SEC OUT ERR: strace -c -f of the futex calls of PID for SEC
# seconds, then SIGINT: strace detaches and writes the -c table to OUT. A strace
# that is still there 5 s later gets SIGKILL. Success means: strace ran the full
# SEC seconds, stopped on the SIGINT with status 0 or 130 (128 + SIGINT, as
# strace 6.19 does), and OUT exists. An empty OUT is a success: strace -c writes
# nothing when PID made no futex call. An attach that ptrace refuses, or a PID
# that is gone, ends strace early: that is a failure.
# No timeout(1): GNU timeout exits 124 after the signal, busybox timeout passes
# on the status of strace, and uutils timeout 0.10.0 with -k sends SIGKILL at
# once, before strace writes its table.
# While strace runs, EXIT, HUP, INT and TERM of the caller kill it first. The
# caller's own traps are kept and run after that. SIGKILL of the caller cannot
# be trapped: then strace stays until the traced process ends. A caller under
# dash or busybox ash that wants its EXIT trap to run on a signal sets it with
# exit_traps.
# "trap" prints the traps that can be set again. dash prints nothing inside $(),
# so the list goes through a file.
strace_futex() {
    sf_rc=0
    case $2 in
        ''|0*|*[!0-9]*) printf '%s\n' "strace_futex: SEC is a positive integer, not '$2'" >> "$4"; return 1 ;;
    esac
    sf_tf=$(mktemp "${TMPDIR:-/tmp}/strace_futex.XXXXXX") \
        || { echo "strace_futex: cannot make a temporary file" >> "$4"; return 1; }
    trap > "$sf_tf"; _SF_TRAPS=$(cat "$sf_tf"); rm -f "$sf_tf"
    _sf_exit_action "$_SF_TRAPS"
    # The pid of this shell, also in a subshell, where $$ is the pid of the parent.
    _SF_PID="" _SF_SELF=$(exec sh -c 'echo "$PPID"')
    trap '_sf_trap EXIT' EXIT; trap '_sf_trap HUP' HUP; trap '_sf_trap INT' INT; trap '_sf_trap TERM' TERM
    "$STRACE" -c -f -e trace=futex -p "$1" -o "$3" 2>> "$4" &
    sf_sp=$! _SF_PID=$!
    sf_i=0
    while [ "$sf_i" -lt $(($2 * 10)) ]; do
        kill -0 "$sf_sp" 2>/dev/null || break; sleep 0.1; sf_i=$((sf_i + 1))
    done
    if ! kill -0 "$sf_sp" 2>/dev/null; then
        wait "$sf_sp" || sf_rc=$?
        _sf_untrap
        printf '%s\n' "strace_futex: $STRACE exited $sf_rc before $2 s" >> "$4"
        return 1
    fi
    kill -INT "$sf_sp"
    sf_i=0
    while [ "$sf_i" -lt 50 ]; do
        kill -0 "$sf_sp" 2>/dev/null || break; sleep 0.1; sf_i=$((sf_i + 1))
    done
    if kill -0 "$sf_sp" 2>/dev/null; then
        kill -KILL "$sf_sp"; wait "$sf_sp" 2>/dev/null
        _sf_untrap
        printf '%s\n' "strace_futex: $STRACE did not stop within 5 s of SIGINT; killed" >> "$4"
        return 1
    fi
    wait "$sf_sp" || sf_rc=$?
    _sf_untrap
    if [ "$sf_rc" != 0 ] && [ "$sf_rc" != 130 ]; then
        printf '%s\n' "strace_futex: $STRACE exited $sf_rc after the SIGINT" >> "$4"; return 1
    fi
    [ -e "$3" ] || { printf '%s\n' "strace_futex: $STRACE wrote no $3" >> "$4"; return 1; }
}

_SF_NL='
'

# _sf_exit_action LIST: the action of the EXIT trap in LIST, the output of
# "trap", into _SF_EXIT; empty when LIST has no EXIT trap. Every shell lists
# EXIT first, as "trap -- WORD EXIT", where WORD is the action quoted. WORD can
# hold " EXIT" and newlines, so each place where " EXIT" ends a line is tried:
# a cut inside WORD leaves a quote open, and only the right cut gives one word.
_sf_exit_action() {
    _SF_EXIT=""
    case $1 in "trap -- "*) ;; *) return 0 ;; esac
    se_rest=${1#"trap -- "} se_word=""
    while :; do
        case $se_rest in *" EXIT"*) ;; *) return 0 ;; esac
        se_word=$se_word${se_rest%%" EXIT"*}
        se_rest=${se_rest#*" EXIT"}
        case $se_rest in
            ""|"$_SF_NL"*)
                # shellcheck disable=SC2016  # $# is of the subshell, after the eval
                if (eval "set -- $se_word" && [ $# = 1 ]) 2>/dev/null; then
                    eval "_SF_EXIT=$se_word"
                    return 0
                fi ;;
        esac
        se_word="$se_word EXIT"
    done
}

_sf_untrap() {  # the caller's traps again
    trap - EXIT HUP INT TERM
    eval "$_SF_TRAPS"
    _SF_PID=""
}

_sf_trap() {  # SIG: kill the strace, give the caller its traps back, then let SIG act as before
    [ -n "$_SF_PID" ] && kill -KILL "$_SF_PID" 2>/dev/null
    _sf_untrap
    if [ "$1" = EXIT ]; then eval "$_SF_EXIT"; else kill -s "$1" "$_SF_SELF"; fi
}

# c2c_record DATA ERR ARG...: perf c2c record in the form of the preflight
# (C2C_FORM). ARG... ends with "-- sleep S". amd-user is the user-space-only form
# for AMD IBS, which "perf c2c record --all-user" refuses in per-process mode;
# perf c2c report reads its data the same way.
c2c_record() {
    cr_data=$1 cr_err=$2
    shift 2
    # shellcheck disable=SC2086  # PERF_T is one or two option words (perf_args)
    case ${C2C_FORM:-} in
        full) perf c2c record ${PERF_T:-} ${PERF_TA+"$PERF_TA"} -o "$cr_data" "$@" ;;
        user) perf c2c record --all-user ${PERF_T:-} ${PERF_TA+"$PERF_TA"} -o "$cr_data" "$@" ;;
        amd-user) perf record -W -d --sample-cpu -e ibs_op/swfilt=1/u ${PERF_T:-} ${PERF_TA+"$PERF_TA"} -o "$cr_data" "$@" ;;
        *) echo "c2c_record: no c2c form (the preflight says c2c is unavailable)" >&2; false ;;
    esac > /dev/null 2> "$cr_err"
}
