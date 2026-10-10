#!/bin/bash
# perf-bisect.sh -- find the first commit at which a perf lab metric rose.
#
# USAGE
#   tools/perf-bisect.sh [options] GOOD BAD
#
#   GOOD, BAD            tag, branch or SHA in the FreeUnit checkout; GOOD is the fast side.
#                        Candidates are `git rev-list --reverse GOOD..BAD`. BAD is the last
#                        candidate and is assumed slow (it is never measured).
#   --threshold PCT      a step is BAD when the delta of the metric (A = GOOD fixed, B = the
#                        candidate) is over PCT and the lab verdict is CLAIM. Default 5.
#   --metric NAME        lab metric, default s_instructions (also reported: r_instructions)
#   --conns N            one concurrency cell, default 32 (cell name hello-cN)
#   --workload NAME      lab workload, default hello
#   --null-limit PCT     NULL_LIMIT_PCT of each job, default 5
#   --repo DIR           FreeUnit checkout to list commits from (UNIT_REPO, else UNIT_SRC,
#                        else the harness checkout in ./src).
#                        Read only; no git state is changed. The clone may be shallow: pass tags
#                        or SHAs that resolve locally.
#   --log FILE           bisect log. Default: the newest run/perf-lab/bisect-GOOD-BAD-*.log, or a
#                        new one named with today's date. Re-running with the same log resumes.
#   --max-steps N        run at most N lab jobs in this invocation
#   --dry-run            run nothing: print the commit list facts, the next probe, the job file
#                        and the probe sequences if every step is GOOD and if every step is BAD
#   -h, --help
#
# Each step writes a job file (REV_A=GOOD, REV_B=candidate, one cell, WARM=2, DUR=8, REPS=2,
# PERF=1, STRACE=0) and runs `tools/perf-job.sh JOBFILE`, or the program named by $PERF_JOB (a
# test stub). It then reads that run's summary.json (phase "ab", cell <workload>-c<conns>,
# fields metric, delta_pct, ci_low, ci_high, null_band_pct, verdict).
#
#   GOOD   verdict CLAIM or NO CLAIM and delta_pct <= threshold
#   BAD    verdict CLAIM and delta_pct > threshold
#   RETRY  lab exit 75 (BUSY), verdict NOISY/INVALID/NO DATA/NO NULL GATE, delta over the
#          threshold but NO CLAIM. Nothing is guessed: the driver stops, exit 75, and a re-run
#          measures the same commit again.
#   SKIP   the build of the candidate failed. Logged; the next probe is the nearest untested
#          commit, as in git bisect skip.
#
# LOG: tab separated, one line per step:
#   time  index  sha  verdict  delta%  CI  band  r_instr_delta%  run-dir  subject
#
# EXIT: 0 culprit found (sha, subject, previous measured commit printed), 2 inconclusive (the
#       narrowest range is named; also --max-steps reached, bad arguments, inconsistent log),
#       75 box busy or a step needs a retry, 1 lab error.
#
# Docs: docs/perf/perf-lab.md, section "Bisecting a regression".
set -u
HARNESS=$(cd "$(dirname "$0")/.." && pwd)
say() { echo "perf-bisect: $*" >&2; }
die() { say "$*"; exit 2; }

THRESHOLD=5 METRIC=s_instructions CONNS=32 WORKLOAD=hello NULL_LIMIT=5
# UNIT_REPO and LAB_RESULTS may also come from the lab env file, read by
# tools/lib/env.sh; the environment wins.
LAB_HARNESS=$HARNESS
# shellcheck source=lib/env.sh
. "$HARNESS/tools/lib/env.sh"
UNIT_REPO=$(lab_env_get UNIT_REPO)
REPO=${UNIT_REPO:-${UNIT_SRC:-$(cd "$(dirname "$0")/.." && pwd)/src}} LOG="" MAXSTEPS=0 DRY=0
PERF_JOB=${PERF_JOB:-$HARNESS/tools/perf-job.sh}
usage() { sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

need2() { [ $# -ge 2 ] || die "$1 needs a value"; }

POS=()
while [ $# -gt 0 ]; do
    case "$1" in
    --threshold) need2 "$@"; THRESHOLD=$2; shift 2 ;;
    --metric) need2 "$@"; METRIC=$2; shift 2 ;;
    --conns) need2 "$@"; CONNS=$2; shift 2 ;;
    --workload) need2 "$@"; WORKLOAD=$2; shift 2 ;;
    --null-limit) need2 "$@"; NULL_LIMIT=$2; shift 2 ;;
    --repo) need2 "$@"; REPO=$2; shift 2 ;;
    --log) need2 "$@"; LOG=$2; shift 2 ;;
    --max-steps) need2 "$@"; MAXSTEPS=$2; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) POS+=("$1"); shift ;;
    esac
done
[ ${#POS[@]} -eq 2 ] || die "usage: tools/perf-bisect.sh [options] GOOD BAD   (see --help)"
GOOD_ARG=${POS[0]} BAD_ARG=${POS[1]}
[[ "$THRESHOLD" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "--threshold must be a number"
[[ "$NULL_LIMIT" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "--null-limit must be a number"
[[ "$CONNS" =~ ^[0-9]+$ ]] || die "--conns must be one integer"
[[ "$MAXSTEPS" =~ ^[0-9]+$ ]] || die "--max-steps must be an integer"
[[ "$METRIC" =~ ^[a-z0-9_]+$ ]] || die "--metric must look like s_instructions"
[[ "$WORKLOAD" =~ ^[a-z0-9]+$ ]] || die "--workload must look like hello"
CELL=$WORKLOAD-c$CONNS

# ---- the candidate list ----------------------------------------------------
GOOD=$(git -C "$REPO" rev-parse --verify --quiet "$GOOD_ARG^{commit}") || die "cannot resolve GOOD '$GOOD_ARG' in $REPO"
BAD=$(git -C "$REPO" rev-parse --verify --quiet "$BAD_ARG^{commit}") || die "cannot resolve BAD '$BAD_ARG' in $REPO"
mapfile -t SHAS < <(git -C "$REPO" rev-list --reverse "$GOOD..$BAD")
N=${#SHAS[@]}
[ "$N" -ge 1 ] || die "no commits in $GOOD_ARG..$BAD_ARG"
[ "${SHAS[$((N - 1))]}" = "$BAD" ] || die "BAD is not the last of GOOD..BAD (is BAD a descendant of GOOD?)"
declare -A IDX
for ((i = 0; i < N; i++)); do IDX[${SHAS[$i]}]=$i; done
NMERGE=$(git -C "$REPO" rev-list --merges "$GOOD..$BAD" | wc -l)
subject() { git -C "$REPO" log -1 --format=%s "$1" 2>/dev/null | tr '\t\r\n' '   '; }

# ---- paths -----------------------------------------------------------------
LABDIR=$(lab_env_get LAB_RESULTS)
LABDIR=${LABDIR:-$HARNESS/run/perf-lab}
san() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }
if [ -z "$LOG" ]; then
    LOG=$(ls -1 "$LABDIR/bisect-$(san "$GOOD_ARG")-$(san "$BAD_ARG")"-*.log 2>/dev/null | tail -1)
    [ -n "$LOG" ] || LOG=$LABDIR/bisect-$(san "$GOOD_ARG")-$(san "$BAD_ARG")-$(date -u +%Y%m%d).log
fi
JOBDIR=${LOG%.log}.jobs

# ---- state: lo = last GOOD index (-1 = GOOD itself), hi = first BAD index ---
LO=-1 HI=$((N - 1))
declare -A SKIP
NSKIP=0
INCONSISTENT=""
apply() {  # index verdict
    local i=$1 v=$2
    case "$v" in
    GOOD) if [ "$i" -ge "$HI" ]; then INCONSISTENT="GOOD at index $i (${SHAS[$i]:0:10}) is not before the first BAD at index $HI"; return; fi
          [ "$i" -gt "$LO" ] && LO=$i ;;
    BAD)  if [ "$i" -le "$LO" ]; then INCONSISTENT="BAD at index $i (${SHAS[$i]:0:10}) is not after the last GOOD at index $LO"; return; fi
          [ "$i" -lt "$HI" ] && HI=$i ;;
    SKIP) SKIP[$i]=1; NSKIP=$((NSKIP + 1)) ;;
    esac
}
PICK=""
pick() {  # sets PICK to the untested index nearest the midpoint of (LO, HI); returns 1 if none
    local mid=$(((LO + HI) / 2)) d c
    for ((d = 0; d < N; d++)); do
        for c in $((mid - d)) $((mid + d)); do
            if [ "$c" -gt "$LO" ] && [ "$c" -lt "$HI" ] && [ -z "${SKIP[$c]:-}" ]; then PICK=$c; return 0; fi
        done
        [ $((mid - d)) -le "$LO" ] && [ $((mid + d)) -ge "$HI" ] && break
    done
    return 1
}

LOGHEAD="#meta good=$GOOD bad=$BAD metric=$METRIC cell=$CELL threshold=$THRESHOLD"
if [ -f "$LOG" ]; then
    m=$(grep -m1 '^#meta ' "$LOG")
    [ "$m" = "$LOGHEAD" ] || [ "${m%% threshold=*}" = "${LOGHEAD%% threshold=*}" ] \
        || die "$LOG was written for a different bisect: $m"
    while IFS=$'\t' read -r _ i sha v _; do
        case "$v" in GOOD|BAD|SKIP) ;; *) continue ;; esac
        [[ "${IDX[$sha]:-}" = "$i" ]] || die "$LOG: $sha is not index $i of this list"
        apply "$i" "$v"
    done < <(grep -v '^#' "$LOG")
fi

report_range() {
    local first=$((LO + 1)) last=$((HI)) i
    say "narrowest range: the culprit is one of ${SHAS[$first]:0:10} .. ${SHAS[$last]:0:10} (indices $first..$last, $((last - first + 1)) commits; the last is the first known BAD)"
    for ((i = first; i <= last; i++)); do
        printf '  %3d %s %s%s\n' "$i" "${SHAS[$i]:0:10}" "$(subject "${SHAS[$i]}")" "$([ -n "${SKIP[$i]:-}" ] && echo ' [SKIPPED]')" >&2
    done
}
conclude() {  # prints the result; exit code 0, 2
    if [ -n "$INCONSISTENT" ]; then
        say "INCONSISTENT: $INCONSISTENT. The regression is not monotonic in list order (merge commits, a flaky step or a fix then a break). Not guessing."
        exit 2
    fi
    if [ "$HI" -eq $((LO + 1)) ]; then
        local prev=$GOOD
        [ "$LO" -ge 0 ] && prev=${SHAS[$LO]}
        echo "CULPRIT ${SHAS[$HI]} $(subject "${SHAS[$HI]}")"
        echo "PREVIOUS ${prev} $(subject "$prev") ($([ "$LO" -ge 0 ] && echo measured GOOD || echo the GOOD end))"
        [ "$HI" -eq $((N - 1)) ] && say "note: the culprit is BAD itself, which was assumed and never measured"
        exit 0
    fi
    report_range
    exit 2
}

say "GOOD ${GOOD:0:10} ($GOOD_ARG)  BAD ${BAD:0:10} ($BAD_ARG)  $N commits, metric $METRIC, cell $CELL, threshold $THRESHOLD percent"
if [ "$NMERGE" -gt 0 ]; then
    say "NOTE: the list holds $NMERGE merge commit(s); rev-list order is a linear walk, not history. A merge can hide a culprit that sits on a side branch, and a step is the tree at that commit."
else
    say "the list holds no merge commits"
fi
say "log $LOG"

write_job() {  # index -> prints the job file text
    local i=$1 sha=${SHAS[$1]}
    cat <<EOF
# perf-bisect step: ${sha:0:10} $(subject "$sha")
JOB=bisect-${sha:0:10}
REV_A=$GOOD
REV_B=$sha
WORKLOAD=$WORKLOAD
CONNS=$CONNS
WARM=2
DUR=8
REPS=2
PERF=1
STRACE=0
PRIMARY=$METRIC
NULL_LIMIT_PCT=$NULL_LIMIT
EXPECT=delta over the threshold of $THRESHOLD percent marks the commit BAD
FALSIFY=NOISY or BUSY: no verdict for this commit
EOF
}

dry_run() {
    echo "dry run: nothing is measured, nothing is written"
    echo "state: LO=$LO ($([ "$LO" -ge 0 ] && echo "${SHAS[$LO]:0:10}" || echo GOOD)) HI=$HI (${SHAS[$HI]:0:10}) skipped=$NSKIP"
    [ -n "$INCONSISTENT" ] && echo "INCONSISTENT: $INCONSISTENT"
    if pick; then
        echo "next probe: index $PICK ${SHAS[$PICK]:0:10} $(subject "${SHAS[$PICK]}")"
        echo "--- job file $JOBDIR/$(printf '%03d' "$PICK")-${SHAS[$PICK]:0:10}.job"
        write_job "$PICK"
        echo "---"
    else
        echo "no untested commit left in the range"
    fi
    for path in GOOD BAD; do
        echo "probe sequence if every step is $path:"
        (
            s=0
            while pick; do
                s=$((s + 1)); printf '  step %d: index %3d %s %s\n' "$s" "$PICK" "${SHAS[$PICK]:0:10}" "$(subject "${SHAS[$PICK]}")"
                apply "$PICK" "$path"
            done
            echo "  ends at LO=$LO HI=$HI after $s steps"
        )
    done
}
if [ "$DRY" = 1 ]; then dry_run; exit 0; fi

# ---- the lab ---------------------------------------------------------------
[ -x "$PERF_JOB" ] || [ -f "$PERF_JOB" ] || die "no perf job program $PERF_JOB"
mkdir -p "$LABDIR" "$JOBDIR" || die "cannot create $LABDIR"
[ -f "$LOG" ] || printf '%s\n' "$LOGHEAD" > "$LOG"

classify() {  # summary.json -> one TSV line: CLASS reason delta ci band rdelta
    python3 -I - "$1" "$CELL" "$METRIC" "$THRESHOLD" <<'PY'
import json, sys
path, cell, metric, thr = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
def row(m):
    for r in json.load(open(path))["results"]:
        if r["phase"] == "ab" and r["cell"] == cell and r["metric"] == m:
            return r
    return None
def f(x, nd=2):
    return "NA" if x is None else ("%+.*f" % (nd, x))
r = row(metric)
rr = row("r_instructions")
rd = f(rr["delta_pct"]) if rr else "NA"
if r is None:
    print("RETRY\tno ab row for %s in %s\tNA\tNA\tNA\t%s" % (metric, cell, rd)); sys.exit(0)
d, v = r["delta_pct"], r["verdict"]
ci = "[%s,%s]" % (f(r["ci_low"]), f(r["ci_high"]))
band = "NA" if r["null_band_pct"] is None else "%.2f" % r["null_band_pct"]
if d is None or v not in ("CLAIM", "NO CLAIM"):
    cls, why = "RETRY", "lab verdict %s" % v
elif d <= thr:
    cls, why = "GOOD", "delta at or under the threshold (%s)" % v
elif v == "CLAIM":
    cls, why = "BAD", "delta over the threshold, CLAIM"
else:
    cls, why = "RETRY", "delta over the threshold but NO CLAIM"
print("\t".join([cls, why, f(d), ci, band, rd]))
PY
}

STEPS=0
while :; do
    [ -z "$INCONSISTENT" ] || conclude
    pick || conclude
    if [ "$MAXSTEPS" -gt 0 ] && [ "$STEPS" -ge "$MAXSTEPS" ]; then
        say "--max-steps $MAXSTEPS reached; re-run with the same log to continue"
        report_range; exit 2
    fi
    i=$PICK sha=${SHAS[$PICK]} subj=$(subject "${SHAS[$PICK]}")
    jf=$JOBDIR/$(printf '%03d' "$i")-${sha:0:10}.job
    write_job "$i" > "$jf"
    STEPS=$((STEPS + 1))
    say "step $STEPS: index $i ${sha:0:10} $subj  (range LO=$LO HI=$HI)"
    out=$("$PERF_JOB" "$jf"); rc=$?
    line=$(printf '%s\n' "$out" | tail -n 1)
    dir=$(printf '%s\n' "$line" | sed -n 's#.*json \(.*\)/summary\.json.*#\1#p; s#.*log \(.*\)/remote\.log.*#\1#p' | head -1)
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    logline() { printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "$i" "$sha" "$1" "$2" "$3" "$4" "$5" "${dir:-NA}" "$subj" >> "$LOG"; }
    case "$rc" in
    75)
        logline RETRY NA NA NA NA
        say "RETRY ${sha:0:10}: box busy, nothing measured: $line"
        exit 75 ;;
    0)
        if [ -z "$dir" ] || [ ! -f "$dir/summary.json" ]; then
            logline RETRY NA NA NA NA
            say "RETRY ${sha:0:10}: no summary.json in the lab output: $line"; exit 75
        fi
        res=$(classify "$dir/summary.json") || { logline RETRY NA NA NA NA; say "RETRY ${sha:0:10}: cannot read $dir/summary.json"; exit 75; }
        IFS=$'\t' read -r cls why d ci band rd <<< "$res"
        logline "$cls" "$d" "$ci" "$band" "$rd"
        say "$cls ${sha:0:10}: $METRIC delta $d percent, CI $ci, null band $band, r_instructions $rd ($why)"
        if [ "$cls" = RETRY ]; then say "RETRY: no guess made; re-run to measure this commit again"; exit 75; fi
        apply "$i" "$cls" ;;
    1)
        err=$(cat "${dir:-/nonexistent}/error" 2>/dev/null)
        case "$err" in
        "build B failed"*)
            logline SKIP NA NA NA NA
            say "SKIP ${sha:0:10}: the candidate does not build ($err); trying the nearest untested commit"
            apply "$i" SKIP ;;
        *)
            logline RETRY NA NA NA NA
            say "ERROR ${sha:0:10}: lab error: ${err:-$line}"; exit 1 ;;
        esac ;;
    *)
        say "perf job exited $rc for $jf: $line"; exit 1 ;;
    esac
done
