#!/bin/bash
# perf-job.sh -- run one perf lab job on the perf box and compare A with B.
#
# USAGE
#   tools/perf-job.sh JOBFILE
#
# One argument, the job file (KEY=VALUE lines, see tools/perf-lab/jobs/).
# The box comes from run/lab.env (gitignored; copy tools/perf-lab/lab.env.example),
# or from the file named by LAB_ENV, read by tools/lib/env.sh. A value set in
# the environment wins. Nothing host-specific is in tracked files.
#
# What it does: syncs tools/perf-lab to the box, starts remote.sh detached
# (claim, idle gate, fetch, build A and B in the lab image, run the cells,
# release), polls until it ends, copies the run directory back, and runs
# tools/perf-compare.py on it.
#
# OUTPUT
#   stderr: progress.  stdout: exactly one line, ready to paste:
#     PERF <job> <run>: <verdict line> | report <dir>/compare.txt | json <dir>/summary.json
#   exit 0 done (read the verdict), 75 box busy or outside the window (nothing
#   measured), 1 error, 2 bad job file or lab.env.
#   PERF_JOB_CHECK=1 tools/perf-job.sh JOBFILE  parses and validates only.
#
# Docs: docs/perf/perf-lab.md
set -u
HARNESS=$(cd "$(dirname "$0")/.." && pwd)
say() { echo "perf-job: $*" >&2; }
usage_die() { say "$*"; exit 2; }

[ $# -eq 1 ] || usage_die "usage: tools/perf-job.sh JOBFILE   (or a run directory, to resume)"
JOBFILE=$1
RESUME=0
if [ -d "$JOBFILE" ] && [ -f "$JOBFILE/job.env" ]; then RESUME=1
elif [ ! -f "$JOBFILE" ]; then usage_die "no job file $JOBFILE"; fi

# ---- lab.env --------------------------------------------------------------
LAB_HARNESS=$HARNESS
# shellcheck source=lib/env.sh
. "$HARNESS/tools/lib/env.sh"
[ -f "$LAB_ENV" ] || usage_die "no $LAB_ENV. Copy tools/perf-lab/lab.env.example to run/lab.env and fill it in."
# Strict here, unlike the other tools: a misspelt key such as SERVR_CPUS would
# leave a default in place and measure on the wrong cores, and nothing in the
# result would show it. Every key the template documents is accepted.
lab_env_load --strict --export \
    || usage_die "$LAB_ENV has errors (see above); the keys are in tools/perf-lab/lab.env.example"
read_kv() {  # file allowed-keys-regex -> exports KEY=VALUE pairs; refuses anything else
    local file=$1 allow=$2 line k v n=0
    while IFS= read -r line || [ -n "$line" ]; do
        n=$((n + 1))
        line=${line%%#*}; line=${line%"${line##*[![:space:]]}"}
        [ -z "$line" ] && continue
        [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]] || usage_die "$file:$n: not KEY=VALUE: $line"
        k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
        [[ "$k" =~ ^($allow)$ ]] || usage_die "$file:$n: unknown key $k"
        v=${v#\"}; v=${v%\"}; v=${v#\'}; v=${v%\'}
        printf -v "$k" '%s' "$v"; export "${k?}"
    done < "$file"
}
missing=""
for k in PERF_HOST REMOTE_WORKDIR CLAIM_DIR LAB_IMAGE OHA REPO_URL SERVER_CPUS LOAD_CPUS PERF_WINDOW GOVERNOR; do
    [ -n "${!k:-}" ] || missing="$missing $k"
done
[ -z "$missing" ] || usage_die "$LAB_ENV is missing:$missing (see tools/perf-lab/lab.env.example)"
case "$GOVERNOR" in record|require-performance) ;; *) usage_die "GOVERNOR must be record or require-performance" ;; esac

SSH_TARGET="${SSH_USER:+$SSH_USER@}$PERF_HOST"
W=$REMOTE_WORKDIR

if [ "$RESUME" = 1 ]; then
    LOCAL=$(cd "$JOBFILE" && pwd)
    # shellcheck disable=SC1091
    . "$LOCAL/job.env"
    RDIR=$W/runs/$RUN_ID
    : "${PRIMARY:=server_us}" "${NULL_LIMIT_PCT:=5}"
    say "resuming run $RUN_ID"
else
# ---- job file -------------------------------------------------------------
JOB_KEYS='JOB|REV_A|REV_B|PATCH_A|PATCH_B|WORKLOAD|CONNS|DUR|WARM|REPS|RATE|APP_PROCS|PERF|STRACE|STRACE_SEC|STRACE_RATE|NULL_GATE|NULL_LIMIT_PCT|PRIMARY|FUNCS|CONFIGURE_ARGS|CONFIGURE_ARGS_A|CONFIGURE_ARGS_B|ISSUE|EXPECT|FALSIFY|AE'
WORKLOAD=hello CONNS=6 DUR=8 WARM=2 REPS=2 RATE="" APP_PROCS=4 PERF=0 STRACE=0
STRACE_SEC=3 STRACE_RATE=2000 NULL_GATE=1 NULL_LIMIT_PCT=5 PRIMARY=server_us FUNCS=""
CONFIGURE_ARGS="" CONFIGURE_ARGS_A="" CONFIGURE_ARGS_B="" PATCH_A="" PATCH_B="" JOB="" REV_A="" REV_B="" AE=""
read_kv "$JOBFILE" "$JOB_KEYS"
[ -n "$JOB" ] && [ -n "$REV_A" ] && [ -n "$REV_B" ] || usage_die "$JOBFILE needs JOB, REV_A and REV_B"
[[ "$JOB" =~ ^[A-Za-z0-9._-]+$ ]] || usage_die "JOB may hold only letters, digits, dot, dash, underscore"
case "$WORKLOAD" in
hello|server|static1k|static1k-hdrs|capp) ;;
rx0|rx1|rx32|drupal-ci)
    # Router-only workloads: the config is a file that cells.sh PUTs.
    conf=$HARNESS/tools/perf-lab/conf/$WORKLOAD.json
    python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$conf" 2>/dev/null \
        || usage_die "WORKLOAD $WORKLOAD: $conf is missing or not valid JSON" ;;
*) usage_die "WORKLOAD $WORKLOAD is not built yet (hello, server, static1k, static1k-hdrs, capp, rx0, rx1, rx32, drupal-ci)" ;;
esac
for v in CONFIGURE_ARGS CONFIGURE_ARGS_A CONFIGURE_ARGS_B; do
    [[ "${!v}" =~ ^[A-Za-z0-9\ ,.=/_+-]*$ ]] || usage_die "$v may hold only letters, digits, blanks and , . = / _ + -"
done
[[ "$AE" =~ ^[A-Za-z0-9\ ,.\;=*-]*$ ]] || usage_die "AE may hold only token characters, blanks, commas, dots, semicolons, = and *"
[[ "$REPS" =~ ^[0-9]+$ ]] && [ "$REPS" -ge 2 ] && [ $((REPS % 2)) = 0 ] \
    || usage_die "REPS must be even and at least 2: each normal block needs its swapped twin"
JOBDIR=$(cd "$(dirname "$JOBFILE")" && pwd)
for p in PATCH_A PATCH_B; do
    v=${!p}; [ -z "$v" ] && continue
    case "$v" in /*) ;; *) v=$JOBDIR/$v ;; esac
    [ -f "$v" ] || usage_die "$p file $v not found"
    printf -v "$p" '%s' "$v"
done

ncells=$(echo $CONNS | wc -w)
# One phase (null only) when A and B would be the same build.
nph=2
[ "$REV_A" = "$REV_B" ] && [ -z "$PATCH_A$PATCH_B" ] && [ "$CONFIGURE_ARGS_A" = "$CONFIGURE_ARGS_B" ] && nph=1
per_block=$(( 8 * (WARM + DUR + 2) + (STRACE == 1 ? 2 * (WARM + STRACE_SEC + 3) : 0) ))
EST_MIN=$(( (REPS * ncells * nph * per_block + 59) / 60 ))
RUN_ID="$JOB-$(date -u +%Y%m%dT%H%M%SZ)"
LAB_WHO="${LAB_WHO:-${USER:-agent}@$(hostname -s)}"
RDIR=$W/runs/$RUN_ID
LOCAL=${LAB_RESULTS:-$HARNESS/run/perf-lab}/$RUN_ID
say "job $JOB, run $RUN_ID, about $EST_MIN min of cells on $PERF_HOST"
say "if this command is interrupted, resume with: tools/perf-job.sh $LOCAL"
if [ "${PERF_JOB_CHECK:-0}" = 1 ]; then
    echo "CHECK $JOB: ok, phases $nph, cells '$CONNS', $REPS blocks, about $EST_MIN min of cells," \
        "configure A '$(echo $CONFIGURE_ARGS $CONFIGURE_ARGS_A)' B '$(echo $CONFIGURE_ARGS $CONFIGURE_ARGS_B)'"; exit 0
fi

mkdir -p "$LOCAL" || usage_die "cannot create $LOCAL"

# ---- resolved env for the box ---------------------------------------------
: "${BUILD_JOBS:=8}" "${SHORT_JOB_MIN:=15}" "${IDLE_MAX_PCT:=5}" "${WAIT_MAX_MIN:=30}" "${LEGACY_CLAIM_MAX_H:=72}"
{
    for k in JOB RUN_ID LAB_WHO EST_MIN REV_A REV_B WORKLOAD CONNS DUR WARM REPS RATE APP_PROCS \
             PERF STRACE STRACE_SEC STRACE_RATE NULL_GATE NULL_LIMIT_PCT PRIMARY FUNCS CONFIGURE_ARGS CONFIGURE_ARGS_A CONFIGURE_ARGS_B AE \
             REMOTE_WORKDIR CLAIM_DIR LAB_IMAGE OHA REPO_URL SERVER_CPUS LOAD_CPUS PERF_WINDOW \
             GOVERNOR SHORT_JOB_MIN IDLE_MAX_PCT WAIT_MAX_MIN LEGACY_CLAIM_MAX_H BUILD_JOBS; do
        printf '%s=%q\n' "$k" "${!k:-}"
    done
    printf 'PATCH_A=%q\nPATCH_B=%q\n' "${PATCH_A:+patch-A.diff}" "${PATCH_B:+patch-B.diff}"
} > "$LOCAL/job.env"
cp "$JOBFILE" "$LOCAL/job.txt"
[ -n "$PATCH_A" ] && cp "$PATCH_A" "$LOCAL/patch-A.diff"
[ -n "$PATCH_B" ] && cp "$PATCH_B" "$LOCAL/patch-B.diff"

# Each run gets its own copy of the lab scripts: a later job's sync must
# never rewrite a script that a running job is still reading.
ssh -o BatchMode=yes -o ConnectTimeout=15 "$SSH_TARGET" "mkdir -p '$RDIR/lab'" \
    || { say "cannot reach $SSH_TARGET"; exit 1; }
rsync -a "$LOCAL/" "$SSH_TARGET:$RDIR/" \
    && rsync -a --exclude testdata --exclude jobs "$HARNESS/tools/perf-lab/" "$SSH_TARGET:$RDIR/lab/" || { say "rsync to the box failed"; exit 1; }
ssh -o BatchMode=yes "$SSH_TARGET" \
    "cd '$RDIR' && setsid nohup bash '$RDIR/lab/remote.sh' '$RDIR' > remote.log 2>&1 < /dev/null &" \
    || { say "cannot start remote.sh"; exit 1; }
fi

# ---- poll ------------------------------------------------------------------
deadline=$(( $(date +%s) + (EST_MIN + ${WAIT_MAX_MIN:-30} + 30) * 60 ))
last=""
while :; do
    sleep 20
    st=$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$SSH_TARGET" \
        "cat '$RDIR/exit' 2>/dev/null; echo; tail -n 1 '$RDIR/remote.log' 2>/dev/null" 2>/dev/null) || continue
    code=$(echo "$st" | head -1); msg=$(echo "$st" | tail -n 1)
    [ "$msg" != "$last" ] && say "$msg" && last=$msg
    [ -n "$code" ] && break
    [ "$(date +%s)" -gt "$deadline" ] && { say "no result by the deadline; the run may still be going: $SSH_TARGET:$RDIR"; exit 1; }
done
rsync -a "$SSH_TARGET:$RDIR/" "$LOCAL/" || say "warning: copying the run back failed"

case "$code" in
75) echo "PERF $JOB $RUN_ID: BUSY, nothing measured: $(cat "$LOCAL/busy" 2>/dev/null) | log $LOCAL/remote.log"; exit 75 ;;
0) ;;
*) echo "PERF $JOB $RUN_ID: ERROR: $(cat "$LOCAL/error" 2>/dev/null) | log $LOCAL/remote.log"; exit 1 ;;
esac

python3 "$HARNESS/tools/perf-compare.py" "$LOCAL/results.tsv" --meta "$LOCAL/meta.txt" \
    --json "$LOCAL/summary.json" --primary "$PRIMARY" --null-limit "$NULL_LIMIT_PCT" > "$LOCAL/compare.txt" \
    || { echo "PERF $JOB $RUN_ID: ERROR: perf-compare failed | $LOCAL"; exit 1; }
cat "$LOCAL/compare.txt" >&2
echo "PERF $JOB $RUN_ID: $(head -1 "$LOCAL/compare.txt") | report $LOCAL/compare.txt | json $LOCAL/summary.json"
