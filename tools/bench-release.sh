#!/bin/bash
# bench-release.sh — A/B bench for a release cut: shipped build (A) vs a
# candidate (B), measured as router request-thread CPU per request.
#
# WHAT IT DOES
#   Unlike bench-engines.sh (RPS/latency from the load generator, N-scenario
#   matrix, N-round rotation), this is the narrow tool for the question a
#   release actually asks: "does the candidate cost more router CPU per
#   request than what shipped." It reads CPU straight from the kernel
#   (/proc/<router>/task/<tid>/stat fields 14+15, utime+stime in clock
#   ticks), summed over threads whose `comm` is "unitd" -- selecting by comm
#   is deliberate: a thread named "OpenTelemetry.T" or "reqwest-interna" is
#   the async exporter, and folding its CPU into the request-path number
#   silently charges the request cost for batch-exporter work it did not do.
#
# USAGE
#   bench-release.sh [--swap] <labelA> <builddirA> <labelB> <builddirB> [reps]
#     reps default 6. Rep 0 is a warm-up and is recorded but excluded from
#     the summary -- the first run of the whole sequence has no predecessor,
#     so it is not comparable to any other rep.
#
#   bench-release.sh --preflight-only
#     Run just the PREFLIGHT checks below (SMT siblings + CPU governor) and
#     exit 0/1 -- no builds, no reps. Use this to check a box is ready
#     before committing to a run.
#
#     A trustworthy A/B is THREE runs, not one:
#       1. null gate    ALLOW_SAME=1, same build under two labels -- must
#                        settle near 0%.
#       2. normal run    bench-release.sh A ... B ...
#       3. swapped run   bench-release.sh --swap A ... B ...   (or SWAP=1)
#     then combine (2) and (3) with tools/bench-crossover.py -- see
#     "SLOT-SET BIAS" below for why a single run, even an 8-cell one, is not
#     enough to trust on its own.
#
#   ENV OVERRIDES
#     MODE=keepalive|close   default keepalive
#     N=30000                requests per measured cell
#     RATE=4000               oha -q throttle
#     CONNS=16                oha -c concurrency
#     PORT=8901                listener port
#     ROUTER_CPU=2             taskset core for the router (must not overlap LOAD_CPUS)
#     LOAD_CPUS=4-7            taskset cores for oha
#     RUNBASE=/tmp/...bench-release   scratch dir (control socket, docroot, state)
#     RESULTS=file             output TSV (default $RUNBASE/release-<pair>[-swap]-<mode>.tsv)
#     ALLOW_SAME=1             permit A and B to be the same binary -- this is
#                              how you run the null pair (see below), not a
#                              flag for real comparisons
#     ALLOW_NOISY=1            skip the quiet-box check
#     SWAP=1                   same as --swap: run B on the slot set A would
#                              otherwise occupy, and vice versa
#     ALLOW_SMT_OVERLAP=1      permit LOAD_CPUS to include ROUTER_CPU's SMT
#                              sibling -- see "PREFLIGHT" below, not a flag
#                              for real comparisons
#     ALLOW_POWERSAVE=1        permit ROUTER_CPU's governor to be
#                              'powersave' -- see "PREFLIGHT" below, not a
#                              flag for real comparisons
#
# RUN THE NULL PAIR FIRST
#   Point A and B at the SAME build (ALLOW_SAME=1, two distinct labels). If
#   that run does not settle on ~0% delta, nothing else this script reports
#   means anything -- three separate methodology bugs were caught this way
#   before any real release number was trusted, including the ordering bug
#   below.
#
#     ALLOW_SAME=1 bench-release.sh same-1 /path/to/build same-2 /path/to/build
#
# ORDERING: 8-CELL COUNTERBALANCE, NOT ABBA, NOT ROTATION
#   With exactly two cells, per-repetition ROTATION (rep0: A,B | rep1: B,A |
#   rep2: A,B | ...) puts identical labels back-to-back across every
#   repetition boundary, and the second occurrence of a label inherits a
#   warm state (page cache, allocator arenas) the first does not -- this
#   produced a significant +3.7% difference between a binary and itself.
#
#   The fix is NOT the 4-cell ABBA block this script used to run
#   (CELLS=(X Y Y X)): that code puts X on BOTH outer slots and Y on BOTH
#   inner slots, no matter what the comment above it claimed ("each label
#   occupies one outer and one inner slot") -- with only 4 slots, "one
#   outer and one inner" is unavoidably ambiguous about which physical slot
#   a label lands on, and on a host with a non-linear (not just
#   linear-drift) per-slot effect that confounds slot position with label
#   identity. A null pair (same binary, two labels) on the reference bench host
#   (2026-08-28) failed at +/-2.8% with the 4-cell block.
#
#   The 8-cell sequence X Y Y X Y X X Y gives each label a full outer PAIR
#   (slots {0,3} or {4,7}) and a full inner PAIR (slots {1,2} or {5,6}),
#   which cancels both a linear drift effect and a constant per-slot offset.
#   With this sequence the same null pair passed (+0.14%, 95% CI [-0.97%,
#   +1.25%], sd 3.35 -> 1.14).
#
#   This distinction is specific to TWO cells: with SIX cells (e.g.
#   bench-engines.sh's scenario x engine matrix) straight rotation is a
#   proper Latin square and needs no special-casing.
#
#   bench-engines.sh's per-ROUND alternation (A/B, B/A, A/B, ...) has the
#   same two-cell rotation shape as the bug above -- it has not been shown to
#   suffer from it (its unit is a full scenario pass, not a single request
#   burst, which may dilute a cold-cache boundary effect enough not to
#   matter), but it has also not been checked. Treat that as open, not fine.
#
# SLOT-SET BIAS: WHY --swap AND tools/bench-crossover.py EXIST
#   An 8-cell block is still not enough on its own. On the reference bench host (2026-08-28)
#   a real A/B read -1.58% [-2.57%, -0.58%] on a normal run, and REVERSED
#   to +0.23% on a --swap run of the SAME two builds -- part of the -1.58%
#   was which slot set the candidate happened to occupy, not the candidate
#   itself. The protocol this script is built for is: null gate, then a
#   normal run, then a --swap run, then combine the two RESULTS files with
#   tools/bench-crossover.py. The swap term enters the two runs with
#   opposite sign, so it cancels in the average; half the two runs'
#   disagreement estimates the residual slot-set bias directly instead of
#   hiding it -- about +/-0.9% on the reference bench host. Treat that as this harness's
#   resolution floor: it cannot currently tell apart a real difference
#   smaller than that from slot-set noise.
#
# PREFLIGHT: SMT SIBLINGS AND THE CPU GOVERNOR
#   Before the first cell, the script prints ROUTER_CPU's SMT sibling list
#   (from /sys/.../topology/thread_siblings_list) and its scaling governor,
#   and refuses to run if LOAD_CPUS contains that sibling: pinning the
#   router to cpu2 while LOAD_CPUS includes cpu2's hyperthread sibling
#   (e.g. cpu6 on one layout of the reference bench host) is not a disjoint core, it's the
#   generator and the router fighting over the same physical core's
#   execution units -- exactly the confound the pinning exists to remove.
#   ALLOW_SMT_OVERLAP=1 overrides. The governor is enforced, not just
#   printed: `powersave` was observed on a run on the reference bench host, and this script
#   refuses to run on it (ALLOW_POWERSAVE=1 overrides) -- frequency scaling
#   mid-cell is non-stationarity this script cannot distinguish from a real
#   delta. Fix with `sudo cpupower frequency-set -g performance`, or write
#   `performance` to the cpu's scaling_governor as root -- on the reference bench host this
#   needs root, which agents don't have, so the user sets it before the
#   run. `bench-release.sh --preflight-only` checks this (and SMT) alone.
#
# GUARDS AND WHY
#   - refuses to run if A and B hash to the same binary, unless ALLOW_SAME=1
#     -- catches "compared a tree against itself"
#   - refuses to run if LOAD_CPUS overlaps ROUTER_CPU's SMT sibling, unless
#     ALLOW_SMT_OVERLAP=1 -- see PREFLIGHT above
#   - probes / for 200 before measuring -- a cell that 404s or 500s measures
#     the error path, not the workload
#   - hard-fails on a zero CPU delta -- the counter did not move, which means
#     the measurement is broken, not that the request cost nothing
#   - a cleanup trap kills the router on ANY exit path, including die(): a
#     server left holding PORT poisons every later cell with a config error
#     that looks like a new bug
#   - warns (does not silently proceed) if a compiler/linker is running or
#     load average is high -- a concurrent build was the actual root cause of
#     noise in one run that no amount of counterbalancing fixed; ALLOW_NOISY=1
#     to proceed anyway
#
# OUTPUT
#   Appends raw per-cell rows to RESULTS (label, rep, us_per_req, ticks,
#   threads, rps, ok-count), then prints a paired summary: for each rep>=1,
#   the per-rep difference between the two labels' cell means, and across
#   those a mean delta with a 95% CI half-width (normal approximation,
#   1.96*sd/sqrt(n)). REPORT THE HALF-WIDTH WITH THE NUMBER. A point estimate
#   whose interval covers zero is "no difference detected," not a percentage
#   -- a −8.7% figure with exactly that problem nearly went into a release
#   report before the interval was checked.
set -u

# Force a numeric-safe locale. On a box with e.g. LC_NUMERIC=ru_RU.UTF-8, awk's
# printf writes "108,70" instead of "108.70" -- a comma-decimal TSV that still
# LOOKS fine in a terminal and silently corrupts anything downstream that
# expects '.' (a spreadsheet import, a stricter awk/python parse, this script's
# own summary on a machine with a different locale). Caught by accident while
# testing this script, not by design -- worth having as a guard.
export LC_ALL=C

usage() { echo "usage: $0 [--swap] <labelA> <builddirA> <labelB> <builddirB> [reps]" >&2
    echo "       $0 --preflight-only" >&2; exit 2; }
PREFLIGHT_ONLY=0
if [ "${1:-}" = "--preflight-only" ]; then PREFLIGHT_ONLY=1; shift; fi
if [ "${1:-}" = "--swap" ]; then SWAP=1; shift; fi
if [ "$PREFLIGHT_ONLY" != 1 ]; then
    [ $# -ge 4 ] || usage
    LABEL_A=$1; BUILD_A=$2; LABEL_B=$3; BUILD_B=$4
    REPS=${5:-6}
else
    LABEL_A=""; BUILD_A=""; LABEL_B=""; BUILD_B=""; REPS=0
fi
SWAP=${SWAP:-0}

# $HOME/.cargo is not the registry root everywhere any more -- the build boxes
# export CARGO_HOME onto a data disk to keep their 40 G root filesystem from filling.
OHA=$(command -v oha || echo "${CARGO_HOME:-$HOME/.cargo}/bin/oha")
PORT=${PORT:-8901}
CLK=$(getconf CLK_TCK)

MODE=${MODE:-keepalive}      # keepalive | close
N=${N:-30000}
RATE=${RATE:-4000}
CONNS=${CONNS:-16}
RUNBASE=${RUNBASE:-/tmp/freeunit-bench-release}
PAIR="${LABEL_A}-vs-${LABEL_B}"
SWAPTAG=""; [ "$SWAP" = 1 ] && SWAPTAG="-swap"
OUT=${RESULTS:-"$RUNBASE/release-$PAIR$SWAPTAG-$MODE.tsv"}

mkdir -p "$RUNBASE"

# A die() that leaves its server running poisons the next run: the port
# stays held and every following cell fails to configure.
UNIT_PID=""
cleanup() { [ -n "$UNIT_PID" ] && kill "$UNIT_PID" 2>/dev/null; }
trap cleanup EXIT
die() { echo "FATAL: $*" >&2; cleanup; exit 1; }

if [ "$PREFLIGHT_ONLY" != 1 ]; then
    [ -x "$OHA" ] || die "no oha on PATH or in ${CARGO_HOME:-$HOME/.cargo}/bin"
    [ -x "$BUILD_A/sbin/unitd" ] || die "no unitd at $BUILD_A/sbin/unitd"
    [ -x "$BUILD_B/sbin/unitd" ] || die "no unitd at $BUILD_B/sbin/unitd"
fi

# Preflight: an SMT sibling of ROUTER_CPU is not a disjoint core. Pinning
# the router to cpu2 while LOAD_CPUS includes cpu2's hyperthread sibling
# means the generator and the router fight over the same physical core's
# execution units -- exactly the confound taskset pinning exists to
# remove. Refuses unless ALLOW_SMT_OVERLAP=1. The governor is printed for
# the record, not enforced: `powersave` lets the CPU change frequency
# mid-cell, which is non-stationarity this script cannot tell apart from a
# real difference.
preflight_cpu() {
    local rcpu=${ROUTER_CPU:-2} sibs="" gov="unknown"
    local sibpath="/sys/devices/system/cpu/cpu$rcpu/topology/thread_siblings_list"
    local govpath="/sys/devices/system/cpu/cpu$rcpu/cpufreq/scaling_governor"
    [ -r "$sibpath" ] && sibs=$(cat "$sibpath")
    if [ -r "$govpath" ]; then
        gov=$(cat "$govpath")
    else
        echo "WARNING: $govpath not readable -- cannot check the governor, continuing." >&2
    fi
    echo "router cpu=$rcpu  smt siblings=${sibs:-unknown}  governor=$gov"

    if [ "$gov" = "powersave" ] && [ "${ALLOW_POWERSAVE:-0}" != 1 ]; then
        die "router cpu$rcpu governor is 'powersave' -- frequency scaling mid-cell is non-stationarity this script cannot tell apart from a real delta. Fix: sudo cpupower frequency-set -g performance (or write 'performance' to $govpath as root -- on the reference bench host this needs root, which agents don't have, so set it before the run). ALLOW_POWERSAVE=1 to override."
    elif [ "$gov" != "performance" ] && [ "$gov" != "unknown" ]; then
        echo "WARNING: governor is '$gov', not 'performance' -- frequency scaling" >&2
        echo "  mid-cell looks like a real difference to this script. ALLOW_NOISY=1 to silence." >&2
    fi

    [ -n "$sibs" ] || return 0
    local sib_list load_list overlap
    sib_list=$(echo "$sibs" | tr ',' '\n' | while read -r r; do
        case "$r" in *-*) seq "${r%-*}" "${r#*-}";; *) echo "$r";; esac
    done)
    load_list=$(echo "${LOAD_CPUS:-4-7}" | tr ',' '\n' | while read -r r; do
        case "$r" in *-*) seq "${r%-*}" "${r#*-}";; *) echo "$r";; esac
    done)
    overlap=$(comm -12 <(echo "$sib_list" | sort -n) <(echo "$load_list" | sort -n))
    if [ -n "$overlap" ]; then
        echo "cpu topology: router cpu$rcpu's SMT sibling(s) [$overlap] are also in" >&2
        echo "  LOAD_CPUS=${LOAD_CPUS:-4-7} -- the generator would contend with the router" >&2
        echo "  for one physical core, not run on a disjoint one." >&2
        [ "${ALLOW_SMT_OVERLAP:-0}" = 1 ] || \
            die "router/load-gen share a physical core via SMT (cpu$rcpu sibling(s) $overlap in LOAD_CPUS) -- set ALLOW_SMT_OVERLAP=1 to override"
    fi
}
preflight_cpu
[ "$PREFLIGHT_ONLY" != 1 ] || exit 0

# Quiet box: a concurrent build was the actual root cause of a noisy run
# that survived every ordering fix. Best-effort and non-fatal by default --
# process-name matching is inherently approximate -- so it warns instead of
# aborting; ALLOW_NOISY=1 silences the warning once you've checked by hand.
if [ "${ALLOW_NOISY:-0}" != 1 ]; then
    busy=$(pgrep -x 'cc1|cc1plus|ld|ld.gold|ld.lld|make|gcc|g\+\+|clang|clang\+\+|cargo|rustc' 2>/dev/null | tr '\n' ' ')
    if [ -n "$busy" ]; then
        echo "WARNING: compiler/linker process(es) running (pid $busy) -- benchmarks are" >&2
        echo "  serial, foreground work; this run's noise floor is suspect. ALLOW_NOISY=1 to silence." >&2
    fi
    load1=$(awk '{print $1}' /proc/loadavg)
    nc=$(nproc)
    over=$(awk -v l="$load1" -v n="$nc" 'BEGIN{print (l > n*0.5) ? 1 : 0}')
    if [ "$over" = 1 ]; then
        echo "WARNING: load average $load1 on $nc cores -- box may not be quiet. ALLOW_NOISY=1 to silence." >&2
    fi
fi

# Identity: the two sides must not be the same binary (except deliberately,
# for the null pair). A silent same-tree comparison is the failure this
# guards against.
SA=$(sha256sum "$BUILD_A/sbin/unitd" | cut -c1-16)
SB=$(sha256sum "$BUILD_B/sbin/unitd" | cut -c1-16)
echo "$LABEL_A=$SA  $LABEL_B=$SB"
if [ "$SA" = "$SB" ] && [ "${ALLOW_SAME:-0}" != 1 ]; then
    die "$LABEL_A and $LABEL_B are the same binary -- the comparison would be meaningless (set ALLOW_SAME=1 to run this deliberately as the null pair)"
fi

DOC="$RUNBASE/www"
mkdir -p "$DOC"
head -c 1024 /dev/urandom | base64 | head -c 1024 > "$DOC/1k.txt"

router_thread_ticks() {
    local rpid=$1 sum=0 t comm rest
    for t in /proc/"$rpid"/task/*/stat; do
        [ -r "$t" ] || continue
        comm=$(sed 's/^[0-9]* (\(.*\)) .*/\1/' "$t")
        [ "$comm" = "unitd" ] || continue
        rest=$(sed 's/.*) //' "$t")
        sum=$(( sum + $(echo "$rest" | awk '{print $12+$13}') ))
    done
    echo "$sum"
}

router_thread_count() {
    local rpid=$1 n=0 t comm
    for t in /proc/"$rpid"/task/*/stat; do
        [ -r "$t" ] || continue
        comm=$(sed 's/^[0-9]* (\(.*\)) .*/\1/' "$t")
        [ "$comm" = "unitd" ] && n=$((n+1))
    done
    echo "$n"
}

run_cell() {
    local build=$1 label=$2 rep=$3
    local run="$RUNBASE/rrun"
    rm -rf "$run"; mkdir -p "$run/state" "$run/modules"

    "$build/sbin/unitd" --no-daemon \
        --control "unix:$run/control.sock" \
        --pid "$run/unit.pid" --log "$run/unit.log" \
        --statedir "$run/state" --tmpdir "$run" --modulesdir "$run/modules" \
        >/dev/null 2>&1 &
    local unit_pid=$!
    UNIT_PID=$unit_pid
    sleep 1.2
    kill -0 "$unit_pid" 2>/dev/null || die "unitd ($label) died on start"

    local conf
    conf=$(curl -s --unix-socket "$run/control.sock" -X PUT -d '{
      "settings": {"listen_threads": 1},
      "listeners": {"127.0.0.1:'"$PORT"'": {"pass": "routes/s"}},
      "routes": {"s": [{"action": {"share": "'"$DOC"'$uri"}}]}
    }' http://localhost/config)
    case "$conf" in
        *success*) ;;
        *) tail -15 "$run/unit.log" >&2; kill "$unit_pid" 2>/dev/null
           die "config rejected ($label rep$rep): [$conf]" ;;
    esac

    sleep 0.5
    local router
    router=$(pgrep -P "$unit_pid" -f router | head -1)
    [ -n "$router" ] || { kill "$unit_pid" 2>/dev/null; die "no router pid"; }

    # Serve check: a cell that 404s would measure the error path, not static.
    # Pin the router to one dedicated core and the generator to a disjoint
    # set. Unpinned, the two migrate across cores between repetitions and
    # that placement noise dominates the difference being measured.
    taskset -pc "${ROUTER_CPU:-2}" "$router" >/dev/null 2>&1 \
        || die "taskset failed on router $router"

    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/1k.txt")
    [ "$code" = "200" ] || { kill "$unit_pid" 2>/dev/null
        die "$label serves $code, not 200 -- wrong workload"; }

    local ka=()
    [ "$MODE" = "close" ] && ka=(--disable-keepalive)

    taskset -c "${LOAD_CPUS:-4-7}" "$OHA" -n 3000 -c "$CONNS" "${ka[@]}" --no-tui \
        "http://127.0.0.1:$PORT/1k.txt" >/dev/null 2>&1

    local nthreads before after ticks ohaout rps ok us
    nthreads=$(router_thread_count "$router")
    [ "$nthreads" -gt 0 ] || die "no request threads matched comm=unitd"

    before=$(router_thread_ticks "$router")
    ohaout=$(taskset -c "${LOAD_CPUS:-4-7}" "$OHA" -n "$N" -c "$CONNS" -q "$RATE" "${ka[@]}" --no-tui \
             "http://127.0.0.1:$PORT/1k.txt" 2>&1)
    after=$(router_thread_ticks "$router")
    ticks=$(( after - before ))

    # First match only, digits only: oha prints more than one line matching
    # [200], and a multi-line value turns the gate below into a shell error
    # rather than a check.
    rps=$(echo "$ohaout" | awk '/Requests\/sec/{print $2; exit}' | tr -cd '0-9.')
    ok=$(echo "$ohaout" | awk '/\[200\]/{print $2; exit}' | tr -cd '0-9')

    [ "$ticks" -gt 0 ] || die "zero CPU delta ($label rep$rep) -- broken"
    [ "${ok:-0}" -ge $(( N * 99 / 100 )) ] || die "$label rep$rep: only ${ok:-0}/$N were 200"

    us=$(awk -v t="$ticks" -v n="$N" -v c="$CLK" \
         'BEGIN{printf "%.2f", (t*1000000/c)/n}')

    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
        "$label" "$rep" "$us" "$ticks" "$nthreads" "${rps:-NA}" "${ok:-NA}" >> "$OUT"
    echo "  rep$rep $label: $us us/req (${ticks}t, ok=$ok)"

    kill "$unit_pid" 2>/dev/null; wait "$unit_pid" 2>/dev/null
    UNIT_PID=""
    sleep 0.5
}

: > "$OUT"
printf "cell\trep\tus_per_req\tticks\tthreads\trps\tok\n" >> "$OUT"

# 8-cell counterbalanced block. See "ORDERING" and "SLOT-SET BIAS" above:
# the 4-cell ABBA form this script used to run (X Y Y X) puts X on BOTH
# outer slots and Y on BOTH inner slots -- not "one outer and one inner
# slot each" as a comment here used to claim -- and a null pair failed at
# +/-2.8% on a host with a non-linear per-slot effect. X Y Y X Y X X Y
# gives each label a full outer pair (slots {0,3} or {4,7}) and a full
# inner pair (slots {1,2} or {5,6}), which passed the same null at +0.14%.
# --swap / SWAP=1 flips which build is X and which is Y, so the two
# builds trade slot sets between a normal run and its swap run; combine
# the two with tools/bench-crossover.py rather than trusting either alone.
X="$BUILD_A $LABEL_A"; Y="$BUILD_B $LABEL_B"
[ "$SWAP" = 1 ] && { X="$BUILD_B $LABEL_B"; Y="$BUILD_A $LABEL_A"; }
CELLS=("$X" "$Y" "$Y" "$X" "$Y" "$X" "$X" "$Y")
NC=${#CELLS[@]}

echo "A=$LABEL_A B=$LABEL_B mode=$MODE swap=$SWAP N=$N rate=$RATE reps=$REPS -> $OUT"
# Repetition 0 is a warm-up and is recorded but dropped from the summary
# below: the very first run of the sequence has no predecessor.
for rep in $(seq 0 "$REPS"); do
    for i in $(seq 0 $((NC - 1))); do
        # shellcheck disable=SC2086
        set -- ${CELLS[$i]}
        run_cell "$1" "$2" "$rep"
    done
done
echo "done -> $OUT"

# Paired summary: per rep (excluding warm-up rep 0), the difference between
# the two labels' cell means, then the mean of those differences with a 95%
# CI half-width. This is the number to report -- not a lone point estimate.
awk -F'\t' -v la="$LABEL_A" -v lb="$LABEL_B" '
    NR==1 { next }
    { sum[$2","$1] += $3; cnt[$2","$1]++ }
    END {
        for (k in cnt) {
            split(k, parts, ",")
            rep = parts[1]; label = parts[2]
            mean[rep","label] = sum[k] / cnt[k]
            seen[rep] = 1
        }
        n = 0
        for (rep in seen) {
            if (rep == "0") continue                     # warm-up, dropped
            ma = mean[rep","la]; mb = mean[rep","lb]
            if (ma == "" || mb == "") continue
            n++
            diff[n] = mb - ma
            pct[n] = 100.0 * (mb - ma) / ma
            baseline_sum += ma
        }
        if (n == 0) { print "no paired reps to summarize"; exit 1 }
        for (i = 1; i <= n; i++) { s += pct[i] }
        meanpct = s / n
        for (i = 1; i <= n; i++) { v += (pct[i]-meanpct)^2 }
        sd = (n > 1) ? sqrt(v/(n-1)) : 0
        hw = (n > 1) ? 1.96 * sd / sqrt(n) : 0
        printf "\n%s vs %s: %+.2f%% CPU/req, 95%% CI +/- %.2f%% (n=%d paired reps)\n", lb, la, meanpct, hw, n
        if (hw >= (meanpct < 0 ? -meanpct : meanpct)) {
            print "  -> CI crosses zero: NO DIFFERENCE DETECTED, not a percentage to report."
        }
    }
' "$OUT"
