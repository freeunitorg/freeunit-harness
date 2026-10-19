#!/usr/bin/env bash
# sweep.sh -- router-thread scaling sweep. Runs inside the lab container (run.sh
# sweep) or natively on the perf box (NATIVE=1 run.sh sweep). Never on a laptop
# that holds the load generator for another job.
#
#   $TREES/<variant>/build/sbin/unitd and build/lab_capp are built before (run.sh build).
#   Writes $OUT/sweep.tsv (one row per run), $OUT/meta.txt, $OUT/freq.log and
#   $OUT/summary.txt.
#
# Layout: the server (router threads, and the application processes, which
# inherit the cpuset) runs on the first thread of the first n physical cores.
# Their SMT siblings stay idle. oha runs on both threads of the remaining cores.
# With n equal to the core count no core is left: oha then gets the SMT siblings
# of the server cores, and the row is marked "shared". Without SMT it shares
# every server cpu.
#
# Runs are interleaved: round r runs every variant once, each with a fresh unitd,
# and the variant order rotates by one each round. A slow drift of the box (heat,
# frequency, a neighbour) then spreads over all variants instead of landing on one.
#
# LOCKS=1 counts mutex calls per request instead (SHIM: the path of
# mutexshim.so). It writes $OUT/locks.tsv, locks-summary.txt and one
# sites-<variant>-<workload>-<n>-<run>.txt per run; see locks.py.
#
# Stop early: touch $OUT/STOP. The sweep stops before the next run, so every row
# in sweep.tsv is a complete run.
set -u
: "${VARIANTS:=master}" "${WORKLOADS:=capp static}" "${NS:=1 2 3}" "${RUNS:=5}" "${DUR:=30}" "${WARM:=3}"
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=tools/perf-lab/scaling/lib.sh
. "$HERE/lib.sh"

command -v "$OHA" >/dev/null || die "no oha ($OHA)"
command -v taskset >/dev/null || die "no taskset"
read_topology
[ "$NCORES" -ge 2 ] || die "cannot read the topology from $SYSCPU"
mkdir -p "$OUT"; chmod 1777 "$OUT"   # the router and the apps run as nobody in the container
TSV=$OUT/sweep.tsv

# --- meta -------------------------------------------------------------------------
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
    echo "params VARIANTS='$VARIANTS' WORKLOADS='$WORKLOADS' NS='$NS' RUNS=$RUNS DUR=$DUR WARM=$WARM CONNS=$CONNS APP_PROCS=$APP_PROCS"
    for v in $VARIANTS; do echo "variant $v $(cat "$TREES/$v/.sha" 2>/dev/null)"; done
} > "$OUT/meta.txt"

# --- frequency samples: "epoch kHz(cpu0) kHz(cpu1) ...", every 2 s (freq.py reads them)
freq_sampler() {
    local i line
    while :; do
        line=$(date +%s); i=0
        while [ -r "$SYSCPU/cpu$i/cpufreq/scaling_cur_freq" ]; do
            line="$line $(cat "$SYSCPU/cpu$i/cpufreq/scaling_cur_freq")"; i=$((i + 1))
        done
        echo "$line"
        sleep 2
    done
}
FREQ_PID=""
if [ -r "$SYSCPU/cpu0/cpufreq/scaling_cur_freq" ]; then
    freq_sampler > "$OUT/freq.log" & FREQ_PID=$!
fi
trap '[ -n "$FREQ_PID" ] && kill "$FREQ_PID" 2>/dev/null; stop_unit; own_out' EXIT

setup_www

# --- lock counts per request (LOCKS=1): shim snapshots around the measured window -------
# The shim slows every lock call, so this stage writes no throughput rows.
if [ "${LOCKS:-0}" = 1 ]; then
    [ -f "${SHIM:-}" ] || die "LOCKS=1 needs SHIM, the path of mutexshim.so (run.sh shim)"
    LT=$OUT/locks.tsv
    printf 'variant\tworkload\tn\trun\tok\trole\tpid\tcalls\tcontended\twait_ns\n' > "$LT"
    snap() {  # dir: two copies of the shim files 0.3 s apart (locks.py takes the larger), and the roles
        local f p
        mkdir -p "$1/x"
        cp "$SD"/mutex-*.tsv "$1"/ 2>/dev/null; sleep 0.3; cp "$SD"/mutex-*.tsv "$1/x"/ 2>/dev/null
        for f in "$1"/mutex-*.tsv; do
            [ -e "$f" ] || continue
            p=${f##*/mutex-}; p=${p%%-*}
            echo "$p $(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | cut -c1-60)"
        done > "$1/roles.txt"
    }
    for w in $WORKLOADS; do
      for n in $NS; do
        [ "$n" -le "$NCORES" ] || continue
        load=$(cpus_load "$n")
        for ((r = 1; r <= ${LRUNS:-2}; r++)); do
          for v in $VARIANTS; do
            [ -e "$OUT/STOP" ] && { echo "STOP file: stopping before locks $v $w n=$n run $r"; break 4; }
            SD=$OUT/shim/$v-$w-$n-$r; rm -rf "$SD"; mkdir -p "$SD"; chmod 1777 "$SD"
            PRELOAD=$SHIM MUTEXSHIM_OUT=$SD start_unit "$v" "$w" "$n"
            oha_run "$WARM" "$load" "$(url_for "$w")"
            # 2.5 s idle: the dumper writes once a second, so the files hold the warm-up
            sleep 2.5; snap "$SD/a"
            oha_run "${LDUR:-10}" "$load" "$(url_for "$w")" "$SD/oha.json"
            sleep 2.5; snap "$SD/b"
            read -r ok bad rps p50 p99 < <(read_oha "$SD/oha.json")
            stop_unit
            python3 "$HERE/locks.py" diff "$SD" "$v" "$w" "$n" "$r" "$ok" >> "$LT"
            python3 "$HERE/locks.py" sites "$SD" > "$OUT/sites-$v-$w-$n-$r.txt" 2>&1
            echo "  locks $v $w n=$n run $r: ok=$ok bad=$bad rps(shim)=$rps"
          done
        done
      done
    done
    python3 "$HERE/locks.py" summary "$LT" > "$OUT/locks-summary.txt"
    cat "$OUT/locks-summary.txt"
    echo "locks done"
    exit 0
fi

printf 'variant\tworkload\tn\tlayout\trun\tok\tbad\trps\tp50_ms\tp99_ms\tsrv_cpu_pct\tload_cpu_pct\tts\n' > "$TSV"

# --- throughput: interleaved rounds, a fresh server per run, the order rotates per round
read -r -a VA <<< "$VARIANTS"; nv=${#VA[@]}
for w in $WORKLOADS; do
  for n in $NS; do
    [ "$n" -le "$NCORES" ] || continue
    srv=$(cpus_server "$n"); load=$(cpus_load "$n")
    layout=clean; [ "$n" -ge "$NCORES" ] && layout=shared
    for ((r = 1; r <= RUNS; r++)); do
      for ((k = 0; k < nv; k++)); do
        v=${VA[(k + r - 1) % nv]}
        [ -e "$OUT/STOP" ] && { echo "STOP file: stopping before $v $w n=$n run $r"; break 4; }
        start_unit "$v" "$w" "$n"
        oha_run "$WARM" "$load" "$(url_for "$w")"
        read -r sb st < <(cpu_ticks "$srv"); read -r lb lt < <(cpu_ticks "$load")
        oha_run "$DUR" "$load" "$(url_for "$w")" "$R/oha.json"
        read -r sb2 st2 < <(cpu_ticks "$srv"); read -r lb2 lt2 < <(cpu_ticks "$load")
        read -r ok bad rps p50 p99 < <(read_oha "$R/oha.json")
        sc=$(awk -v b=$((sb2 - sb)) -v t=$((st2 - st)) 'BEGIN{printf "%.1f", (t > 0 ? 100 * b / t : 0)}')
        lc=$(awk -v b=$((lb2 - lb)) -v t=$((lt2 - lt)) 'BEGIN{printf "%.1f", (t > 0 ? 100 * b / t : 0)}')
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$v" "$w" "$n" "$layout" "$r" \
            "$ok" "$bad" "$rps" "$p50" "$p99" "$sc" "$lc" "$(date +%s)" >> "$TSV"
        echo "  $v $w n=$n run $r: rps=$rps p50=${p50}ms p99=${p99}ms srv=${sc}% load=${lc}% bad=$bad"
        stop_unit
      done
    done
  done
done

python3 "$HERE/analyze.py" "$TSV" "${PAIRS:-}" > "$OUT/summary.txt"
cat "$OUT/summary.txt"
echo "sweep done"
