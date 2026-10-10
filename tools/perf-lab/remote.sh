#!/bin/bash
# remote.sh -- the perf-box side of one perf lab job. Started detached by
# tools/perf-job.sh as:  remote.sh <rundir>
# <rundir>/job.env holds every variable (job file plus lab.env, resolved).
#
# Steps: claim the box, idle gate (wait up to WAIT_MAX_MIN), fetch and build
# A and B in the lab image, gate again, run cells.sh in the container,
# release. Writes <rundir>/exit (0 done, 75 busy, 1 error) last.
set -u
RUN=$1
cd "$RUN" || exit 1
# shellcheck disable=SC1091
. ./job.env
LAB=$(cd "$(dirname "$0")" && pwd)
W=$REMOTE_WORKDIR
CLAIM="$CLAIM_DIR/CLAIMED-perflab-$RUN_ID"
STATUS=1
log() { echo "[$(date +%H:%M:%S)] $*"; }
finish() {
    [ -n "${CONTAINER:-}" ] && docker rm -f "$CONTAINER" >/dev/null 2>&1
    rm -f "$CLAIM"
    echo "$STATUS" > "$RUN/exit"
}
trap finish EXIT
busy_exit() { log "BUSY: $*"; echo "$*" > "$RUN/busy"; STATUS=75; exit 75; }
fail() { log "ERROR: $*"; echo "$*" > "$RUN/error"; STATUS=1; exit 1; }

# ---- claims -------------------------------------------------------------
# A lab claim is KEY=VALUE text: format, who, purpose, host, pid, started,
# expires (epoch). An empty file is a legacy claim from the older
# convention ("touch CLAIMED-<session>-<purpose>").
other_claims() {
    local f now age exp pid fmt
    now=$(date +%s)
    OTHER=""
    for f in "$CLAIM_DIR"/CLAIMED-*; do
        [ -e "$f" ] || continue
        [ "$f" = "$CLAIM" ] && continue
        if [ ! -s "$f" ]; then
            age=$(( (now - $(stat -c %Y "$f")) / 3600 ))
            if [ "$age" -ge "${LEGACY_CLAIM_MAX_H:-72}" ]; then
                log "warning: ignoring legacy claim $(basename "$f"), ${age} h old; ask its owner to remove it"
                continue
            fi
            OTHER="$OTHER $(basename "$f")"; continue
        fi
        fmt=$(sed -n 's/^format=//p' "$f"); exp=$(sed -n 's/^expires=//p' "$f"); pid=$(sed -n 's/^pid=//p' "$f")
        if [ "$fmt" = perflab-1 ] && [ -n "$exp" ] && [ "$now" -gt $((exp + 600)) ] && ! kill -0 "${pid:-0}" 2>/dev/null; then
            mkdir -p "$CLAIM_DIR/reaped"
            mv "$f" "$CLAIM_DIR/reaped/" && log "reaped stale claim $(basename "$f") (expired $(( (now - exp) / 60 )) min ago, pid gone)"
            continue
        fi
        OTHER="$OTHER $(basename "$f")"
    done
}

# ---- idle gate ----------------------------------------------------------
cpu_busy_pct() {  # sample /proc/stat over $1 seconds
    local a b
    a=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9, $5+$6}' /proc/stat)
    sleep "$1"
    b=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9, $5+$6}' /proc/stat)
    echo "$a $b" | awk '{t = $3 - $1; i = $4 - $2; printf "%.1f", (t > 0 ? (t - i) * 100 / t : 0)}'
}
top_consumers() {  # processes over 2% of one CPU in a 3 s sample, own session excluded
    local mysid
    mysid=$(awk '{sub(/.*\) /, ""); print $4}' /proc/self/stat)
    snap() { for f in /proc/[0-9]*/stat; do read -r l < "$f" 2>/dev/null || continue
        r=${l##*) }; set -- $r; echo "${l%% *} $4 $(( ${12} + ${13} )) $(echo "$l" | sed 's/^[0-9]* (\(.*\)) .*/\1/')"; done; }
    snap > "$RUN/.p0"; sleep 3; snap > "$RUN/.p1"
    awk -v sid="$mysid" -v hz="$(getconf CLK_TCK)" 'NR == FNR { t[$1] = $3; next }
        ($1 in t) && $2 != sid { d = ($3 - t[$1]) * 100 / hz / 3; if (d > 2) printf "%s(%s) %.0f%%\n", $4, $1, d }' \
        "$RUN/.p0" "$RUN/.p1" | sort -t' ' -k2 -rn | head -5 | tr '\n' ' '
    rm -f "$RUN/.p0" "$RUN/.p1"
}
in_window() {
    [ "${PERF_WINDOW:-any}" = any ] && return 0
    [ "$EST_MIN" -le "${SHORT_JOB_MIN:-15}" ] && return 0
    local now s e
    now=$(date +%H%M); s=${PERF_WINDOW%-*}; e=${PERF_WINDOW#*-}; s=${s/:/}; e=${e/:/}
    if [ "$s" -le "$e" ]; then [ "$now" -ge "$s" ] && [ "$now" -lt "$e" ]
    else [ "$now" -ge "$s" ] || [ "$now" -lt "$e" ]; fi
}
gate() {  # sets GATE_MSG; 0 idle, 1 busy
    other_claims
    if [ -n "$OTHER" ]; then GATE_MSG="claimed by:$OTHER"; return 1; fi
    if ! in_window; then GATE_MSG="outside PERF_WINDOW $PERF_WINDOW for a ${EST_MIN} min job"; return 1; fi
    local b; b=$(cpu_busy_pct 10)
    if awk -v b="$b" -v m="${IDLE_MAX_PCT:-5}" 'BEGIN{exit !(b > m)}'; then
        local top; top=$(top_consumers)
        GATE_MSG="cpu ${b}% busy over 10 s (limit ${IDLE_MAX_PCT:-5}%); top: ${top:-no single process over 2% of a CPU}"; return 1
    fi
    GATE_MSG="cpu ${b}% busy"; return 0
}
wait_idle() {  # $1 = minutes to wait
    local until=$(( $(date +%s) + $1 * 60 ))
    while ! gate; do
        [ "$(date +%s)" -ge "$until" ] && busy_exit "$GATE_MSG"
        log "waiting: $GATE_MSG"; sleep 20
    done
    log "idle gate passed: $GATE_MSG"
}

# ---- claim, with a lock so two lab jobs cannot both pass ---------------
mkdir -p "$CLAIM_DIR" 2>/dev/null
exec 9> "$CLAIM_DIR/.perflab.lock" || fail "cannot open $CLAIM_DIR/.perflab.lock"
wait_idle "${WAIT_MAX_MIN:-30}"
flock -w 60 9 || busy_exit "another lab job holds the claim lock"
other_claims   # under the lock only claims are rechecked; CPU was checked just above
[ -z "$OTHER" ] || busy_exit "claimed by:$OTHER"
cat > "$CLAIM" <<EOF
format=perflab-1
who=$LAB_WHO
purpose=perf-job $JOB
host=$(uname -n)
pid=$$
started=$(date +%s)
expires=$(( $(date +%s) + (EST_MIN * 2 + 30) * 60 ))
EOF
flock -u 9
log "claimed: $(basename "$CLAIM")"

# ---- sources and builds -------------------------------------------------
mkdir -p "$W/trees"
BARE=$W/freeunit.git
if [ ! -d "$BARE" ]; then
    git init -q --bare "$BARE" && git -C "$BARE" remote add origin "$REPO_URL" || fail "cannot init $BARE"
fi
git -C "$BARE" fetch -q origin '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*' || fail "git fetch failed"
resolve() {
    local r=$1
    case "$r" in
    pr/*) git -C "$BARE" fetch -q origin "+refs/pull/${r#pr/}/head:refs/pr/${r#pr/}" || return 1; r=refs/pr/${r#pr/} ;;
    origin/*) r=refs/heads/${r#origin/} ;;
    esac
    git -C "$BARE" rev-parse -q --verify "$r^{commit}" 2>/dev/null && return 0
    git -C "$BARE" fetch -q origin "$r" 2>/dev/null && git -C "$BARE" rev-parse -q --verify "$r^{commit}"
}
SHA_A=$(resolve "$REV_A") || fail "cannot resolve REV_A=$REV_A"
SHA_B=$(resolve "$REV_B") || fail "cannot resolve REV_B=$REV_B"
IMG=$(docker image inspect "$LAB_IMAGE" --format '{{.Id}}' 2>/dev/null) || fail "no image $LAB_IMAGE on this box; build it from tools/perf-lab/Dockerfile"

prepare() {  # sha patchfile configure-args -> prints tree path
    local sha=$1 patch=$2 cargs=$3 name t
    name=${sha:0:12}
    [ -n "$patch" ] && name="$name-p$(sha256sum "$patch" | cut -c1-8)"
    # Per-side configure arguments get their own tree: one SHA can be built
    # two ways in one job (CONFIGURE_ARGS_A and CONFIGURE_ARGS_B).
    [ -n "${cargs// /}" ] && name="$name-c$(echo "$cargs" | sha256sum | cut -c1-8)"
    t=$W/trees/$name
    if [ ! -f "$t/.lab-src-ok" ]; then
        rm -rf "$t"; mkdir -p "$t"
        git -C "$BARE" archive "$sha" | tar -x -C "$t" || return 1
        if [ -n "$patch" ]; then (cd "$t" && patch -p1 -s < "$patch") || return 1; fi
        touch "$t/.lab-src-ok"
    fi
    local extra="" php=""
    case "$WORKLOAD" in hello|server) php="./configure php >/dev/null && make -j$BUILD_JOBS php >/dev/null &&" ;; esac
    [ "$WORKLOAD" = capp ] && extra="make -j$BUILD_JOBS build/lib/libunit.a >/dev/null && cc -O2 -I src -I build/include -o build/lab_capp src/test/nxt_unit_app_test.c build/lib/libunit.a -lpthread &&"
    local stamp; stamp=$(echo "$IMG $CONFIGURE_ARGS $cargs $WORKLOAD" | sha256sum | cut -c1-16)
    if [ "$(cat "$t/.lab-build" 2>/dev/null)" != "$stamp" ] || [ ! -x "$t/build/sbin/unitd" ]; then
        log "building $name ($WORKLOAD) in $LAB_IMAGE" >&2
        docker run --rm --user "$(id -u):$(id -g)" -v "$t:/src" -w /src "$LAB_IMAGE" sh -c \
            "./configure $CONFIGURE_ARGS $cargs && make -j$BUILD_JOBS unitd >/dev/null && $php $extra true" \
            > "$RUN/build-$name.log" 2>&1 || { tail -20 "$RUN/build-$name.log" >&2; return 1; }
        echo "$stamp" > "$t/.lab-build"
    fi
    echo "$t"
}
PA=""; PB=""
[ -n "${PATCH_A:-}" ] && PA=$RUN/patch-A.diff
[ -n "${PATCH_B:-}" ] && PB=$RUN/patch-B.diff
TA=$(prepare "$SHA_A" "$PA" "${CONFIGURE_ARGS_A:-}") || fail "build A failed"
TB=$(prepare "$SHA_B" "$PB" "${CONFIGURE_ARGS_B:-}") || fail "build B failed"
# Build facts for the report: does each side define NXT_REGEX_JIT, and does
# the image's PCRE2 have JIT (PCRE2_CONFIG_JIT is 1)? Read-only probes.
has_jit() { grep -q '^#define NXT_REGEX_JIT ' "$1/build/include/nxt_auto_config.h" 2>/dev/null && echo yes || echo no; }
JIT_A=$(has_jit "$TA"); JIT_B=$(has_jit "$TB")
PCRE2_JIT=$(docker run --rm --entrypoint python3 "$LAB_IMAGE" -c '
import ctypes, ctypes.util
lib = ctypes.CDLL(ctypes.util.find_library("pcre2-8") or "libpcre2-8.so.0")
v = ctypes.c_uint32(0); lib.pcre2_config_8(1, ctypes.byref(v)); print("yes" if v.value else "no")' 2>/dev/null || echo unknown)
UA=$(sha256sum "$TA/build/sbin/unitd" | cut -c1-16)
UB=$(sha256sum "$TB/build/sbin/unitd" | cut -c1-16)
PHASES="null ab"
if [ "$TA" = "$TB" ]; then PHASES=null; log "A and B are the same tree: this job is a null gate only"; fi
[ "$NULL_GATE" = 0 ] && [ "$PHASES" != null ] && PHASES=ab

# Function sizes in bytes, for machine-code iterations. FUNCS="bin:symbol ..."
: > "$RUN/sizes.tsv"
for fs in ${FUNCS:-}; do
    bin=${fs%%:*}; sym=${fs#*:}
    for side in A B; do
        t=$TA; [ "$side" = B ] && t=$TB
        f=$t/build/sbin/$bin; [ -f "$f" ] || f=$t/build/lib/unit/modules/$bin
        sz=$(nm -S --defined-only "$f" 2>/dev/null | awk -v s="$sym" '$4 == s {print $2; exit}')
        [ -n "$sz" ] && sz=$((16#$sz))   # mawk has no strtonum
        printf '%s\tbuild\t%s\t%s\t%s\t0\t0\tfsize_bytes\t%s\n' "$JOB" "$side" \
            "$([ $side = A ] && echo "$REV_A" || echo "$REV_B")" "$sym" "${sz:-0}" >> "$RUN/sizes.tsv"
    done
done

# Builds are ours; the gate checks again for anyone who started meanwhile.
wait_idle 5

GOV=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo unknown)
EPP=$(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null || echo unknown)
[ "$GOVERNOR" = require-performance ] && [ "$GOV" != performance ] && fail "governor is $GOV, job requires performance"
cat > "$RUN/meta.txt" <<EOF
job=$JOB
run_id=$RUN_ID
rev_a=$REV_A
rev_b=$REV_B
sha_a=$SHA_A
sha_b=$SHA_B
patch_a=${PATCH_A:-}
patch_b=${PATCH_B:-}
unitd_a=$UA
unitd_b=$UB
configure_a=$CONFIGURE_ARGS ${CONFIGURE_ARGS_A:-}
configure_b=$CONFIGURE_ARGS ${CONFIGURE_ARGS_B:-}
regex_jit_a=$JIT_A
regex_jit_b=$JIT_B
pcre2_jit=$PCRE2_JIT
phases=$PHASES
workload=$WORKLOAD
ae=${AE:-}
conns=$CONNS
dur_s=$DUR
warm_s=$WARM
reps=$REPS
rate=${RATE:-}
app_procs=$APP_PROCS
perf=$PERF
strace=$STRACE
server_cpus=$SERVER_CPUS
load_cpus=$LOAD_CPUS
cpu=$(awk -F': ' '/model name/{print $2; exit}' /proc/cpuinfo)
kernel=$(uname -r)
governor=$GOV
epp=$EPP
image=$LAB_IMAGE ${IMG:7:12}
oha=$("$OHA" --version 2>/dev/null)
gate=$GATE_MSG
started=$(date -Is)
EOF

# ---- measure ------------------------------------------------------------
CONTAINER=perflab-$RUN_ID
log "measuring: phases '$PHASES', cells '$CONNS', $REPS blocks"
docker run --rm --name "$CONTAINER" --cap-add PERFMON --cap-add SYS_PTRACE \
    -v "$TA:/t/A:ro" -v "$TB:/t/B:ro" -v "$LAB:/lab:ro" -v "$RUN:/out" -v "$OHA:/usr/local/bin/oha:ro" \
    -e JOB="$JOB" -e PHASES="$PHASES" -e WORKLOAD="$WORKLOAD" -e CONNS="$CONNS" -e DUR="$DUR" \
    -e WARM="$WARM" -e REPS="$REPS" -e RATE="${RATE:-}" -e APP_PROCS="$APP_PROCS" -e PERF="$PERF" \
    -e STRACE="$STRACE" -e STRACE_SEC="$STRACE_SEC" -e STRACE_RATE="$STRACE_RATE" -e AE="${AE:-}" \
    -e SERVER_CPUS="$SERVER_CPUS" -e LOAD_CPUS="$LOAD_CPUS" -e DB_CPUS="${DB_CPUS:-}" \
    -e REV_A="$REV_A" -e REV_B="$REV_B" \
    --entrypoint bash "$LAB_IMAGE" -c "bash /lab/cells.sh; s=\$?; chown $(id -u):$(id -g) /out/results.tsv; exit \$s" \
    > "$RUN/cells.log" 2>&1 || { tail -20 "$RUN/cells.log"; fail "cells failed, see cells.log"; }
CONTAINER=""
cat "$RUN/sizes.tsv" >> "$RUN/results.tsv"
echo "ended=$(date -Is)" >> "$RUN/meta.txt"
log "done"
STATUS=0
