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
