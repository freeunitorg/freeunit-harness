#!/bin/sh
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
log() { printf '%s\n' "[$(date +%H:%M:%S)] $*"; }
finish() {
    [ -n "${CONTAINER:-}" ] && docker rm -f "$CONTAINER" >/dev/null 2>&1
    rm -f "$CLAIM"
    echo "$STATUS" > "$RUN/exit"
}
trap finish EXIT
# dash and busybox ash skip the EXIT trap when a signal ends the shell; bash
# runs it. Exit through the trap, so the claim is released and exit written.
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 131' QUIT
trap 'exit 143' TERM
busy_exit() { log "BUSY: $*"; printf '%s\n' "$*" > "$RUN/busy"; STATUS=75; exit 75; }
fail() { log "ERROR: $*"; printf '%s\n' "$*" > "$RUN/error"; STATUS=1; exit 1; }

# ---- claims -------------------------------------------------------------
# A lab claim is KEY=VALUE text: format, who, purpose, host, pid, started,
# expires (epoch). An empty file is a legacy claim from the older
# convention ("touch CLAIMED-<session>-<purpose>").
other_claims() {
    oc_now=$(date +%s)
    OTHER=""
    for oc_f in "$CLAIM_DIR"/CLAIMED-*; do
        [ -e "$oc_f" ] || continue
        [ "$oc_f" = "$CLAIM" ] && continue
        if [ ! -s "$oc_f" ]; then
            oc_age=$(( (oc_now - $(stat -c %Y "$oc_f")) / 3600 ))
            if [ "$oc_age" -ge "${LEGACY_CLAIM_MAX_H:-72}" ]; then
                log "warning: ignoring legacy claim $(basename "$oc_f"), ${oc_age} h old; ask its owner to remove it"
                continue
            fi
            OTHER="$OTHER $(basename "$oc_f")"; continue
        fi
        oc_fmt=$(sed -n 's/^format=//p' "$oc_f"); oc_exp=$(sed -n 's/^expires=//p' "$oc_f"); oc_pid=$(sed -n 's/^pid=//p' "$oc_f")
        if [ "$oc_fmt" = perflab-1 ] && [ -n "$oc_exp" ] && [ "$oc_now" -gt $((oc_exp + 600)) ] && ! kill -0 "${oc_pid:-0}" 2>/dev/null; then
            mkdir -p "$CLAIM_DIR/reaped"
            mv "$oc_f" "$CLAIM_DIR/reaped/" && log "reaped stale claim $(basename "$oc_f") (expired $(( (oc_now - oc_exp) / 60 )) min ago, pid gone)"
            continue
        fi
        OTHER="$OTHER $(basename "$oc_f")"
    done
}

# ---- idle gate ----------------------------------------------------------
cpu_busy_pct() {  # sample /proc/stat over $1 seconds
    cb_a=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9, $5+$6}' /proc/stat)
    sleep "$1"
    cb_b=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9, $5+$6}' /proc/stat)
    printf '%s\n' "$cb_a $cb_b" | awk '{t = $3 - $1; i = $4 - $2; printf "%.1f", (t > 0 ? (t - i) * 100 / t : 0)}'
}
top_consumers() {  # processes over 2% of one CPU in a 3 s sample, own session excluded
    tc_sid=$(awk '{sub(/.*\) /, ""); print $4}' /proc/self/stat)
    snap() { for sn_f in /proc/[0-9]*/stat; do read -r sn_l < "$sn_f" 2>/dev/null || continue
        sn_r=${sn_l##*) }; set -- $sn_r
        printf '%s\n' "${sn_l%% *} $4 $(( ${12} + ${13} )) $(printf '%s\n' "$sn_l" | sed 's/^[0-9]* (\(.*\)) .*/\1/')"; done; }
    snap > "$RUN/.p0"; sleep 3; snap > "$RUN/.p1"
    awk -v sid="$tc_sid" -v hz="$(getconf CLK_TCK)" 'NR == FNR { t[$1] = $3; next }
        ($1 in t) && $2 != sid { d = ($3 - t[$1]) * 100 / hz / 3; if (d > 2) printf "%s(%s) %.0f%%\n", $4, $1, d }' \
        "$RUN/.p0" "$RUN/.p1" | sort -t' ' -k2 -rn | head -5 | tr '\n' ' '
    rm -f "$RUN/.p0" "$RUN/.p1"
}
no_colon() {  # string -> the string without its first colon
    case "$1" in *:*) printf '%s' "${1%%:*}${1#*:}" ;; *) printf '%s' "$1" ;; esac
}
in_window() {
    [ "${PERF_WINDOW:-any}" = any ] && return 0
    [ "$EST_MIN" -le "${SHORT_JOB_MIN:-15}" ] && return 0
    iw_now=$(date +%H%M); iw_s=${PERF_WINDOW%-*}; iw_e=${PERF_WINDOW#*-}
    iw_s=$(no_colon "$iw_s"); iw_e=$(no_colon "$iw_e")
    if [ "$iw_s" -le "$iw_e" ]; then [ "$iw_now" -ge "$iw_s" ] && [ "$iw_now" -lt "$iw_e" ]
    else [ "$iw_now" -ge "$iw_s" ] || [ "$iw_now" -lt "$iw_e" ]; fi
}
gate() {  # sets GATE_MSG; 0 idle, 1 busy
    other_claims
    if [ -n "$OTHER" ]; then GATE_MSG="claimed by:$OTHER"; return 1; fi
    if ! in_window; then GATE_MSG="outside PERF_WINDOW $PERF_WINDOW for a ${EST_MIN} min job"; return 1; fi
    gt_b=$(cpu_busy_pct 10)
    if awk -v b="$gt_b" -v m="${IDLE_MAX_PCT:-5}" 'BEGIN{exit !(b > m)}'; then
        gt_top=$(top_consumers)
        GATE_MSG="cpu ${gt_b}% busy over 10 s (limit ${IDLE_MAX_PCT:-5}%); top: ${gt_top:-no single process over 2% of a CPU}"; return 1
    fi
    GATE_MSG="cpu ${gt_b}% busy"; return 0
}
wait_idle() {  # $1 = minutes to wait
    wi_until=$(( $(date +%s) + $1 * 60 ))
    while ! gate; do
        [ "$(date +%s)" -ge "$wi_until" ] && busy_exit "$GATE_MSG"
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
    rs_r=$1
    case "$rs_r" in
    pr/*) git -C "$BARE" fetch -q origin "+refs/pull/${rs_r#pr/}/head:refs/pr/${rs_r#pr/}" || return 1; rs_r=refs/pr/${rs_r#pr/} ;;
    origin/*) rs_r=refs/heads/${rs_r#origin/} ;;
    esac
    git -C "$BARE" rev-parse -q --verify "$rs_r^{commit}" 2>/dev/null && return 0
    git -C "$BARE" fetch -q origin "$rs_r" 2>/dev/null && git -C "$BARE" rev-parse -q --verify "$rs_r^{commit}"
}
SHA_A=$(resolve "$REV_A") || fail "cannot resolve REV_A=$REV_A"
SHA_B=$(resolve "$REV_B") || fail "cannot resolve REV_B=$REV_B"
IMG=$(docker image inspect "$LAB_IMAGE" --format '{{.Id}}' 2>/dev/null) || fail "no image $LAB_IMAGE on this box; build it from tools/perf-lab/Dockerfile"
# py-*: check the image before any build.  An image built before the Python
# workloads has no python3-config and no /opt/pyvenv, and the build would
# fail with only "build A failed".  py-fastapi runs from /opt/pyvenv.
PY_VER=""; PY_PKGS=""
PY_IMAGE_FIX="rebuild the base (tools/perf-lab/Dockerfile.base) and then the lab image (tools/perf-lab/Dockerfile) on it, see docs/perf/perf-lab.md"
case "$WORKLOAD" in py-*)
    docker run --rm --entrypoint sh "$LAB_IMAGE" -c 'command -v python3-config' >/dev/null 2>&1 \
        || fail "no python3-config in $LAB_IMAGE; $PY_IMAGE_FIX"
    PY_VER=$(docker run --rm --entrypoint python3 "$LAB_IMAGE" --version 2>&1)
    PY_PKGS=$(docker run --rm --entrypoint /opt/pyvenv/bin/pip "$LAB_IMAGE" freeze --disable-pip-version-check 2>/dev/null | tr '\n' ' ')
    [ -n "$PY_PKGS" ] || fail "no /opt/pyvenv in $LAB_IMAGE; $PY_IMAGE_FIX" ;;
esac

prepare() {  # sha patchfile configure-args -> prints tree path
    pp_sha=$1 pp_patch=$2 pp_cargs=$3
    pp_name=$(printf '%s\n' "$pp_sha" | cut -c1-12)
    [ -n "$pp_patch" ] && pp_name="$pp_name-p$(sha256sum "$pp_patch" | cut -c1-8)"
    # Per-side configure arguments get their own tree: one SHA can be built
    # two ways in one job (CONFIGURE_ARGS_A and CONFIGURE_ARGS_B).
    case "$pp_cargs" in *[!\ ]*) pp_name="$pp_name-c$(printf '%s\n' "$pp_cargs" | sha256sum | cut -c1-8)" ;; esac
    pp_t=$W/trees/$pp_name
    if [ ! -f "$pp_t/.lab-src-ok" ]; then
        rm -rf "$pp_t"; mkdir -p "$pp_t"
        git -C "$BARE" archive "$pp_sha" | tar -x -C "$pp_t" || return 1
        if [ -n "$pp_patch" ]; then (cd "$pp_t" && patch -p1 -s < "$pp_patch") || return 1; fi
        touch "$pp_t/.lab-src-ok"
    fi
    pp_extra="" pp_php=""
    case "$WORKLOAD" in hello|server) pp_php="./configure php >/dev/null && make -j$BUILD_JOBS php >/dev/null &&" ;; esac
    # py-*: the Unit python3 module (build/lib/unit/modules/python3.unit.so) for the image's python3.
    case "$WORKLOAD" in py-*) pp_php="./configure python --config=python3-config --module=python3 >/dev/null && make -j$BUILD_JOBS python3 >/dev/null &&" ;; esac
    [ "$WORKLOAD" = capp ] && pp_extra="make -j$BUILD_JOBS build/lib/libunit.a >/dev/null && cc -O2 -I src -I build/include -o build/lab_capp src/test/nxt_unit_app_test.c build/lib/libunit.a -lpthread &&"
    pp_stamp=$(printf '%s\n' "$IMG $CONFIGURE_ARGS $pp_cargs $WORKLOAD" | sha256sum | cut -c1-16)
    if [ "$(cat "$pp_t/.lab-build" 2>/dev/null)" != "$pp_stamp" ] || [ ! -x "$pp_t/build/sbin/unitd" ]; then
        log "building $pp_name ($WORKLOAD) in $LAB_IMAGE" >&2
        docker run --rm --user "$(id -u):$(id -g)" -v "$pp_t:/src" -w /src "$LAB_IMAGE" sh -c \
            "./configure $CONFIGURE_ARGS $pp_cargs && make -j$BUILD_JOBS unitd >/dev/null && $pp_php $pp_extra true" \
            > "$RUN/build-$pp_name.log" 2>&1 || { tail -20 "$RUN/build-$pp_name.log" >&2; return 1; }
        echo "$pp_stamp" > "$pp_t/.lab-build"
    fi
    printf '%s\n' "$pp_t"
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
        [ -n "$sz" ] && sz=$((0x$sz))   # mawk has no strtonum
        printf '%s\tbuild\t%s\t%s\t%s\t0\t0\tfsize_bytes\t%s\n' "$JOB" "$side" \
            "$([ $side = A ] && printf '%s\n' "$REV_A" || printf '%s\n' "$REV_B")" "$sym" "${sz:-0}" >> "$RUN/sizes.tsv"
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
image=$LAB_IMAGE $(printf '%s\n' "$IMG" | cut -c8-19)
oha=$("$OHA" --version 2>/dev/null)
python=$PY_VER
pyvenv=$PY_PKGS
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
    --entrypoint sh "$LAB_IMAGE" -c "sh /lab/cells.sh; s=\$?; chown $(id -u):$(id -g) /out/results.tsv; exit \$s" \
    > "$RUN/cells.log" 2>&1 || { tail -20 "$RUN/cells.log"; fail "cells failed, see cells.log"; }
CONTAINER=""
cat "$RUN/sizes.tsv" >> "$RUN/results.tsv"
printf '%s\n' "ended=$(date -Is)" >> "$RUN/meta.txt"
log "done"
STATUS=0
