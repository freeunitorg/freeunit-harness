#!/bin/sh
# run.sh -- drive the router-thread scaling kit on the perf box (PERF_HOST) from
# your machine. One stage per call, so a stage can be repeated alone.
#
#   run.sh claim [minutes]       claim the box (perflab-1 claim file, default 240 minutes)
#   run.sh build NAME=REF ...    export each REF of UNIT_REPO and build it as tree NAME
#   run.sh shim                  build mutexshim.so, the lock counter (in LAB_IMAGE, or natively)
#   run.sh sweep TAG             start sweep.sh detached; results in REMOTE_WORKDIR/scaling/results/TAG
#   run.sh locks TAG             the same sweep with LOCKS=1: mutex calls per request, no throughput
#   run.sh profile TAG           profile.sh: perf record, futex count, perf c2c at one point (N, V)
#   run.sh watch TAG [FILE]      wait for FILE (summary.txt; locks-summary.txt after locks); STOP and exit 3 on a foreign claim
#   run.sh stop TAG              touch the STOP file: the sweep ends before its next run
#   run.sh fetch TAG             copy the results to LAB_RESULTS/scaling/TAG (default run/perf-lab)
#   run.sh release               remove the claim and its holder process
#
# Sweep knobs (environment): VARIANTS (tree names, default master), WORKLOADS
# ("capp static"), NS ("1 2 3", router threads), RUNS (5), DUR (30 s), WARM (3 s),
# CONNS (128), APP_PROCS (4), PAIRS ("b:a,..." for the ratios in summary.txt).
# Lock knobs: LRUNS (2 runs per variant), LDUR (10 s measured window).
# Profile knobs: N (3 router threads), V (master), WORKLOADS, PERF_SEC (20), C2C_G (0).
#
# The box, its paths, the image and oha come from run/lab.env (tools/lib/env.sh):
# PERF_HOST, SSH_USER, REMOTE_WORKDIR, CLAIM_DIR, LAB_IMAGE, OHA, UNIT_REPO.
# NATIVE=1 builds and runs on the box without docker (a box without docker, or a
# run as a normal user). The kit pins its own cpus from the box topology;
# SERVER_CPUS and LOAD_CPUS are not used.
#
# Method, traps and the reading of the results: docs/perf/scaling-kit.md.
set -eu
case ${1:-} in
    claim|release|build|shim|sweep|locks|profile|stop|watch|fetch) ;;
    *) awk 'NR > 1 { if ($0 == "set -eu") exit; print }' "$0"; exit 2 ;;
esac
HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
HARNESS=$(CDPATH='' cd -- "$HERE/../../.." && pwd)
LAB_HARNESS=$HARNESS
# shellcheck source=tools/lib/env.sh
. "$HARNESS/tools/lib/env.sh"
# shellcheck disable=SC2119  # no options: the defaults
lab_env_load

die() { echo "run.sh: $*" >&2; exit 1; }
for k in PERF_HOST REMOTE_WORKDIR CLAIM_DIR OHA; do
    eval "[ -n \"\${$k:-}\" ]" || die "$k is not set (run/lab.env, see tools/perf-lab/lab.env.example)"
done
[ "${NATIVE:-0}" = 1 ] || [ -n "${LAB_IMAGE:-}" ] || die "LAB_IMAGE is not set and NATIVE is not 1"
TARGET=${SSH_USER:+$SSH_USER@}$PERF_HOST
RD=$REMOTE_WORKDIR/scaling
LOCAL=${LAB_RESULTS:-$HARNESS/run/perf-lab}/scaling
CLAIM_REC=$LOCAL/.claim

rsh() { ssh -o BatchMode=yes "$TARGET" "$@"; }
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }   # quote for the remote shell

# Other claims on the box, as tools/perf-lab/remote.sh reads them: an empty file
# is a legacy claim, ignored once older than LEGACY_CLAIM_MAX_H hours; a
# perflab-1 claim whose expiry is 10 minutes past and whose pid is gone is stale.
# Prints the names of the live ones. $1 is our own claim file, or empty.
# shellcheck disable=SC2016  # the remote shell expands it
OTHER_CLAIMS='other_claims() {
    now=$(date +%s)
    for f in "$CLAIM_DIR"/CLAIMED-*; do
        [ -e "$f" ] || continue
        [ "$f" = "$1" ] && continue
        if [ ! -s "$f" ]; then
            [ $(( (now - $(stat -c %Y "$f")) / 3600 )) -ge "${LEGACY_CLAIM_MAX_H:-72}" ] && continue
        else
            exp=$(sed -n "s/^expires=//p" "$f"); pid=$(sed -n "s/^pid=//p" "$f")
            [ -n "$exp" ] && [ "$now" -gt $((exp + 600)) ] && ! kill -0 "${pid:-0}" 2>/dev/null && continue
        fi
        printf " %s" "${f##*/}"
    done
}'

# A tree name or a tag is one path component of a remote path that build and
# start remove with rm -rf: "../trees" would name the trees directory itself.
check_name() {  # what value
    case $2 in
        ''|.|..|*[!A-Za-z0-9._-]*) die "$1 '$2': use letters, digits, '.', '_' and '-'; not '.' or '..'" ;;
    esac
}

claim_file() { sed -n 's/^claim=//p' "$CLAIM_REC" 2>/dev/null; }

stage_claim() {
    min=${1:-240}
    [ -s "$CLAIM_REC" ] && die "already claimed: $(claim_file) (run.sh release first)"
    claim="$CLAIM_DIR/CLAIMED-perflab-scaling-$(date +%Y%m%dT%H%M%S)"
    out=$(rsh "CLAIM_DIR=$(q "$CLAIM_DIR") LEGACY_CLAIM_MAX_H=$(q "${LEGACY_CLAIM_MAX_H:-72}") sh -s" <<EOF
set -eu
$OTHER_CLAIMS
mkdir -p "\$CLAIM_DIR"
exec 9> "\$CLAIM_DIR/.perflab.lock"
flock -w 60 9 || { echo "BUSY: the claim lock is held"; exit 75; }
o=\$(other_claims "")
[ -z "\$o" ] || { echo "BUSY: claimed by:\$o"; exit 75; }
# 9>&-: the holder must not inherit the lock. It lives for the whole claim, and
# tools/perf-lab/remote.sh takes the same lock before it reads the claims.
nohup sleep $((min * 60)) >/dev/null 2>&1 9>&- &
pid=\$!
now=\$(date +%s)
printf 'format=perflab-1\nwho=%s\npurpose=scaling kit\nhost=%s\npid=%s\nstarted=%s\nexpires=%s\n' \
    "\$(id -un)@\$(uname -n)" "\$(uname -n)" "\$pid" "\$now" "\$((now + $min * 60))" > $(q "$claim")
echo "pid=\$pid"
EOF
    ) || { echo "$out"; exit 75; }
    mkdir -p "$LOCAL"
    printf 'claim=%s\n%s\n' "$claim" "$out" > "$CLAIM_REC"
    echo "claimed: ${claim##*/} for $min minutes"
}

stage_release() {
    [ -s "$CLAIM_REC" ] || die "no claim record at $CLAIM_REC"
    claim=$(claim_file); pid=$(sed -n 's/^pid=//p' "$CLAIM_REC")
    rsh "kill $(q "$pid") 2>/dev/null; rm -f $(q "$claim")"
    rm -f "$CLAIM_REC"
    echo "released: ${claim##*/}"
}

push_scripts() {
    rsh "mkdir -p $(q "$RD/scripts") $(q "$RD/trees") $(q "$RD/results")"
    rsync -a "$HERE/lib.sh" "$HERE/sweep.sh" "$HERE/build-tree.sh" "$HERE/capp.c" "$HERE/analyze.py" \
        "$HERE/mutexshim.c" "$HERE/locks.py" "$HERE/profile.sh" "$TARGET:$RD/scripts/"
}

stage_build() {
    [ $# -gt 0 ] || die "usage: run.sh build NAME=REF ..."
    repo=${UNIT_REPO:-${UNIT_SRC:-$HARNESS/src}}
    for nr in "$@"; do   # every name before the first remote command
        v=${nr%%=*}; ref=${nr#*=}
        if [ "$v" = "$nr" ] || [ -z "$v" ] || [ -z "$ref" ]; then die "not NAME=REF: $nr"; fi
        check_name "tree name" "$v"
    done
    push_scripts
    for nr in "$@"; do
        v=${nr%%=*}; ref=${nr#*=}
        sha=$(git -C "$repo" rev-parse --verify "$ref^{commit}") || die "no commit $ref in $repo"
        t=$RD/trees/$v
        if [ "$(rsh "cat $(q "$t/.sha") 2>/dev/null || true")" = "$sha" ] && rsh "test -x $(q "$t/build/lab_capp")"; then
            echo "build: $v is current ($sha)"; continue
        fi
        echo "build: $v = $sha"
        rsh "rm -rf $(q "$t") && mkdir -p $(q "$t")"
        git -C "$repo" archive "$sha" | rsh "tar -x -C $(q "$t")"
        rsh "echo $sha > $(q "$t/.sha")"
        if [ "${NATIVE:-0}" = 1 ]; then
            rsh "SRC=$(q "$t") CAPP=$(q "$RD/scripts/capp.c") LOGDIR=$(q "$t") JOBS=${BUILD_JOBS:-4} sh $(q "$RD/scripts/build-tree.sh")" \
                || die "build of $v failed; logs: $t/conf.log, $t/make.log"
        else
            rsh "docker run --rm --user \$(id -u):\$(id -g) -e JOBS=${BUILD_JOBS:-4} -e LOGDIR=/src \
                -v $(q "$t"):/src -v $(q "$RD/scripts/build-tree.sh"):/build-tree.sh:ro \
                -v $(q "$RD/scripts/capp.c"):/capp.c:ro $(q "$LAB_IMAGE") sh /build-tree.sh" \
                || die "build of $v failed; logs: $t/conf.log, $t/make.log"
        fi
    done
}

stage_shim() {
    push_scripts
    cc='cc -O2 -fno-omit-frame-pointer -shared -fPIC -o mutexshim.so mutexshim.c -ldl -lpthread'
    if [ "${NATIVE:-0}" = 1 ]; then
        rsh "cd $(q "$RD/scripts") && $cc" || die "shim build failed"
    else
        rsh "docker run --rm --user \$(id -u):\$(id -g) -v $(q "$RD/scripts"):/s -w /s $(q "$LAB_IMAGE") $cc" \
            || die "shim build failed"
    fi
    echo "shim built: $RD/scripts/mutexshim.so"
}

# start TAG SCRIPT KEY=VALUE...: run SCRIPT detached, so an ssh drop cannot
# signal it. The output goes to results/TAG/run.log.
start() {
    tag=$1 script=$2; shift 2
    check_name tag "$tag"
    push_scripts
    out=$RD/results/$tag
    envs=""
    for kv in "$@"; do envs="$envs $(q "$kv")"; done
    rsh "rm -rf $(q "$out") && mkdir -p $(q "$out") && chmod 1777 $(q "$out")"
    if [ "${NATIVE:-0}" = 1 ]; then
        # R holds the control socket: keep it short, a unix socket path has at most 107 bytes
        rsh "cd $(q "$RD") && env $envs OUT=$(q "$out") TREES=$(q "$RD/trees") R=$(q "/tmp/scaling-$tag") \
            WWW=$(q "$RD/www-$tag") OHA=$(q "$OHA") setsid nohup bash $(q "$RD/scripts/$script") \
            > $(q "$out/run.log") 2>&1 < /dev/null & echo \$! > $(q "$out/pid")"
    else
        e=""
        for kv in "$@"; do e="$e -e $(q "$kv")"; done
        rsh "docker run -d --rm --name $(q "scaling-$tag") --cap-add PERFMON --cap-add SYS_PTRACE \
            -v $(q "$RD/trees"):/t:ro -v $(q "$RD/scripts"):/lab:ro -v $(q "$out"):/out \
            -v $(q "$OHA"):/usr/local/bin/oha:ro $e -e OUT=/out $(q "$LAB_IMAGE") \
            bash -c $(q "bash /lab/$script > /out/run.log 2>&1")" >/dev/null
    fi
    echo "started $script as $tag; log $out/run.log"
}

stage_sweep() {
    start "${1:?usage: run.sh sweep TAG}" sweep.sh \
        "VARIANTS=${VARIANTS:-master}" "WORKLOADS=${WORKLOADS:-capp static}" "NS=${NS:-1 2 3}" \
        "RUNS=${RUNS:-5}" "DUR=${DUR:-30}" "WARM=${WARM:-3}" "CONNS=${CONNS:-128}" \
        "APP_PROCS=${APP_PROCS:-4}" "PAIRS=${PAIRS:-}"
}

stage_locks() {
    shim=/lab/mutexshim.so
    [ "${NATIVE:-0}" = 1 ] && shim=$RD/scripts/mutexshim.so
    start "${1:?usage: run.sh locks TAG}" sweep.sh LOCKS=1 "SHIM=$shim" \
        "VARIANTS=${VARIANTS:-master}" "WORKLOADS=${WORKLOADS:-capp}" "NS=${NS:-1 2 3}" \
        "LRUNS=${LRUNS:-2}" "LDUR=${LDUR:-10}" "WARM=${WARM:-3}" "CONNS=${CONNS:-128}" \
        "APP_PROCS=${APP_PROCS:-4}"
}

stage_profile() {
    start "${1:?usage: run.sh profile TAG}" profile.sh \
        "N=${N:-3}" "V=${V:-master}" "WORKLOADS=${WORKLOADS:-capp static}" "PERF_SEC=${PERF_SEC:-20}" \
        "C2C_G=${C2C_G:-0}" "LOAD_SEC=${LOAD_SEC:-}" "WARM=${WARM:-3}" "CONNS=${CONNS:-128}" \
        "APP_PROCS=${APP_PROCS:-4}"
}

stage_stop() {
    check_name tag "${1:?usage: run.sh stop TAG}"
    rsh "touch $(q "$RD/results/$1/STOP")"
    echo "STOP written; the run ends before its next cell"
}

# Poll every 60 s. FILE is summary.txt for a sweep or a profile, and
# locks-summary.txt for a locks run. Exit 0 when FILE is there, 3 after touching STOP because a
# foreign claim showed up, 4 when the run is gone without FILE.
stage_watch() {
    tag=${1:?usage: run.sh watch TAG [FILE]}; file=${2:-summary.txt}
    check_name tag "$tag"; check_name file "$file"
    out=$RD/results/$tag
    while :; do
        st=$(rsh "CLAIM_DIR=$(q "$CLAIM_DIR") LEGACY_CLAIM_MAX_H=$(q "${LEGACY_CLAIM_MAX_H:-72}") sh -s" <<EOF
$OTHER_CLAIMS
if [ -s $(q "$out/$file") ]; then echo DONE; exit 0; fi
if [ -f $(q "$out/pid") ]; then kill -0 "\$(cat $(q "$out/pid"))" 2>/dev/null && echo RUNNING || echo GONE
else docker ps -q --filter name=$(q "^scaling-$tag\$") | grep -q . && echo RUNNING || echo GONE; fi
echo "claims:\$(other_claims $(q "$(claim_file)"))"
EOF
        ) || { echo "watch: ssh failed, retrying"; sleep 60; continue; }
        case $st in DONE*) echo "done: $out/$file"; return 0 ;; esac
        foreign=$(printf '%s\n' "$st" | sed -n 's/^claims://p')
        if [ -n "$foreign" ]; then
            rsh "touch $(q "$out/STOP")"
            echo "foreign claim:$foreign; STOP written at $(date -u +%FT%TZ)"; return 3
        fi
        case $st in GONE*) echo "the run is gone without $file; see $out/run.log"; return 4 ;; esac
        sleep 60
    done
}

stage_fetch() {
    tag=${1:?usage: run.sh fetch TAG}
    check_name tag "$tag"
    mkdir -p "$LOCAL/$tag"
    rsync -a "$TARGET:$RD/results/$tag/" "$LOCAL/$tag/"
    echo "fetched to $LOCAL/$tag"
}

stage=$1
shift
case $stage in
    claim) stage_claim "$@" ;;
    release) stage_release ;;
    build) stage_build "$@" ;;
    shim) stage_shim ;;
    sweep) stage_sweep "$@" ;;
    locks) stage_locks "$@" ;;
    profile) stage_profile "$@" ;;
    stop) stage_stop "$@" ;;
    watch) stage_watch "$@" ;;
    fetch) stage_fetch "$@" ;;
esac
