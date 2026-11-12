#!/bin/sh
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
say() { printf 'perf-bisect: %s\n' "$*" >&2; }
die() { say "$*"; exit 2; }
TAB=$(printf '\t')
# ASCII classes spelt out: a range such as [a-z] follows the locale in some shells.
DIGIT=0123456789
LOWER=abcdefghijklmnopqrstuvwxyz

THRESHOLD=5 METRIC=s_instructions CONNS=32 WORKLOAD=hello NULL_LIMIT=5
# UNIT_REPO and LAB_RESULTS may also come from the lab env file, read by
# tools/lib/env.sh; the environment wins.
# shellcheck disable=SC2034  # read by tools/lib/env.sh
LAB_HARNESS=$HARNESS
# shellcheck source=lib/env.sh
. "$HARNESS/tools/lib/env.sh"
UNIT_REPO=$(lab_env_get UNIT_REPO)
REPO=${UNIT_REPO:-${UNIT_SRC:-$(cd "$(dirname "$0")/.." && pwd)/src}} LOG="" MAXSTEPS=0 DRY=0
PERF_JOB=${PERF_JOB:-$HARNESS/tools/perf-job.sh}
usage() { sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

need2() { [ $# -ge 2 ] || die "$1 needs a value"; }

# The positional arguments, counted; only the first two are kept.
NPOS=0 GOOD_ARG="" BAD_ARG=""
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
    *) NPOS=$((NPOS + 1))
       case $NPOS in 1) GOOD_ARG=$1 ;; 2) BAD_ARG=$1 ;; esac
       shift ;;
    esac
done
[ "$NPOS" -eq 2 ] || die "usage: tools/perf-bisect.sh [options] GOOD BAD   (see --help)"
is_int() { case $1 in ''|*[!$DIGIT]*) return 1 ;; esac; return 0; }
is_num() {  # digits, or digits '.' digits
    case $1 in *.*) is_int "${1%%.*}" && is_int "${1#*.}" ;; *) is_int "$1" ;; esac
}
is_num "$THRESHOLD" || die "--threshold must be a number"
is_num "$NULL_LIMIT" || die "--null-limit must be a number"
is_int "$CONNS" || die "--conns must be one integer"
is_int "$MAXSTEPS" || die "--max-steps must be an integer"
case $METRIC in ''|*[!${LOWER}${DIGIT}_]*) die "--metric must look like s_instructions" ;; esac
case $WORKLOAD in ''|*[!${LOWER}${DIGIT}]*) die "--workload must look like hello" ;; esac
CELL=$WORKLOAD-c$CONNS

# ---- the candidate list ----------------------------------------------------
GOOD=$(git -C "$REPO" rev-parse --verify --quiet "$GOOD_ARG^{commit}") || die "cannot resolve GOOD '$GOOD_ARG' in $REPO"
BAD=$(git -C "$REPO" rev-parse --verify --quiet "$BAD_ARG^{commit}") || die "cannot resolve BAD '$BAD_ARG' in $REPO"
# SHAS holds one sha per line; index i (from 0) is line i + 1.
SHAS=$(git -C "$REPO" rev-list --reverse "$GOOD..$BAD")
N=$(printf '%s\n' "$SHAS" | grep -c .)
[ "$N" -ge 1 ] || die "no commits in $GOOD_ARG..$BAD_ARG"
sha_at() { printf '%s\n' "$SHAS" | sed -n "$(($1 + 1))p"; }
idx_of() {  # sha -> its index, empty when it is not in the list
    [ -n "$1" ] || return 0
    printf '%s\n' "$SHAS" | awk -v s="$1" '$0 == s { print NR - 1; exit }'
}
short() {  # sha -> its first 10 characters
    case $1 in ??????????*) printf '%s' "${1%"${1#??????????}"}" ;; *) printf '%s' "$1" ;; esac
}
[ "$(sha_at $((N - 1)))" = "$BAD" ] || die "BAD is not the last of GOOD..BAD (is BAD a descendant of GOOD?)"
NMERGE=$(git -C "$REPO" rev-list --merges "$GOOD..$BAD" | wc -l)
subject() { git -C "$REPO" log -1 --format=%s "$1" 2>/dev/null | tr '\t\r\n' '   '; }

# ---- paths -----------------------------------------------------------------
LABDIR=$(lab_env_get LAB_RESULTS)
LABDIR=${LABDIR:-$HARNESS/run/perf-lab}
san() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }
if [ -z "$LOG" ]; then
    # shellcheck disable=SC2012  # the names are ours and have no newlines
    LOG=$(ls -1 "$LABDIR/bisect-$(san "$GOOD_ARG")-$(san "$BAD_ARG")"-*.log 2>/dev/null | tail -1)
    [ -n "$LOG" ] || LOG=$LABDIR/bisect-$(san "$GOOD_ARG")-$(san "$BAD_ARG")-$(date -u +%Y%m%d).log
fi
JOBDIR=${LOG%.log}.jobs

# ---- state: lo = last GOOD index (-1 = GOOD itself), hi = first BAD index ---
LO=-1 HI=$((N - 1))
SKIP=" "        # the skipped indices, each with a blank on both sides
NSKIP=0
INCONSISTENT=""
is_skipped() { case $SKIP in *" $1 "*) return 0 ;; esac; return 1; }
apply() {  # index verdict
    ap_i=$1 ap_v=$2
    case "$ap_v" in
    GOOD) if [ "$ap_i" -ge "$HI" ]; then INCONSISTENT="GOOD at index $ap_i ($(short "$(sha_at "$ap_i")")) is not before the first BAD at index $HI"; return; fi
          [ "$ap_i" -gt "$LO" ] && LO=$ap_i ;;
    BAD)  if [ "$ap_i" -le "$LO" ]; then INCONSISTENT="BAD at index $ap_i ($(short "$(sha_at "$ap_i")")) is not after the last GOOD at index $LO"; return; fi
          [ "$ap_i" -lt "$HI" ] && HI=$ap_i ;;
    SKIP) is_skipped "$ap_i" || SKIP="$SKIP$ap_i "; NSKIP=$((NSKIP + 1)) ;;
    esac
}
PICK=""
pick() {  # sets PICK to the untested index nearest the midpoint of (LO, HI); returns 1 if none
    pk_mid=$(((LO + HI) / 2)) pk_d=0
    while [ "$pk_d" -lt "$N" ]; do
        for pk_c in $((pk_mid - pk_d)) $((pk_mid + pk_d)); do
            if [ "$pk_c" -gt "$LO" ] && [ "$pk_c" -lt "$HI" ] && ! is_skipped "$pk_c"; then PICK=$pk_c; return 0; fi
        done
        [ $((pk_mid - pk_d)) -le "$LO" ] && [ $((pk_mid + pk_d)) -ge "$HI" ] && break
        pk_d=$((pk_d + 1))
    done
    return 1
}

LOGHEAD="#meta good=$GOOD bad=$BAD metric=$METRIC cell=$CELL threshold=$THRESHOLD"
if [ -f "$LOG" ]; then
    m=$(grep -m1 '^#meta ' "$LOG")
    [ "$m" = "$LOGHEAD" ] || [ "${m%% threshold=*}" = "${LOGHEAD%% threshold=*}" ] \
        || die "$LOG was written for a different bisect: $m"
    # A here-document, not a pipe: apply must run in this shell to keep LO and HI.
    while IFS=$TAB read -r _ i sha v _; do
        case "$v" in GOOD|BAD|SKIP) ;; *) continue ;; esac
        [ -n "$sha" ] && [ "$(idx_of "$sha")" = "$i" ] || die "$LOG: $sha is not index $i of this list"
        apply "$i" "$v"
    done <<EOF
$(grep -v '^#' "$LOG")
EOF
fi

report_range() {
    rr_first=$((LO + 1)) rr_last=$((HI))
    say "narrowest range: the culprit is one of $(short "$(sha_at "$rr_first")") .. $(short "$(sha_at "$rr_last")") (indices $rr_first..$rr_last, $((rr_last - rr_first + 1)) commits; the last is the first known BAD)"
    rr_i=$rr_first
    while [ "$rr_i" -le "$rr_last" ]; do
        rr_sha=$(sha_at "$rr_i")
        printf '  %3d %s %s%s\n' "$rr_i" "$(short "$rr_sha")" "$(subject "$rr_sha")" "$(is_skipped "$rr_i" && echo ' [SKIPPED]')" >&2
        rr_i=$((rr_i + 1))
    done
}
conclude() {  # prints the result; exit code 0, 2
    if [ -n "$INCONSISTENT" ]; then
        say "INCONSISTENT: $INCONSISTENT. The regression is not monotonic in list order (merge commits, a flaky step or a fix then a break). Not guessing."
        exit 2
    fi
    if [ "$HI" -eq $((LO + 1)) ]; then
        cc_prev=$GOOD
        [ "$LO" -ge 0 ] && cc_prev=$(sha_at "$LO")
        cc_hi=$(sha_at "$HI")
        printf '%s\n' "CULPRIT $cc_hi $(subject "$cc_hi")"
        printf '%s\n' "PREVIOUS ${cc_prev} $(subject "$cc_prev") ($([ "$LO" -ge 0 ] && echo measured GOOD || echo the GOOD end))"
        [ "$HI" -eq $((N - 1)) ] && say "note: the culprit is BAD itself, which was assumed and never measured"
        exit 0
    fi
    report_range
    exit 2
}

say "GOOD $(short "$GOOD") ($GOOD_ARG)  BAD $(short "$BAD") ($BAD_ARG)  $N commits, metric $METRIC, cell $CELL, threshold $THRESHOLD percent"
if [ "$NMERGE" -gt 0 ]; then
    say "NOTE: the list holds $NMERGE merge commit(s); rev-list order is a linear walk, not history. A merge can hide a culprit that sits on a side branch, and a step is the tree at that commit."
else
    say "the list holds no merge commits"
fi
say "log $LOG"

write_job() {  # index -> prints the job file text
    wj_sha=$(sha_at "$1")
    cat <<EOF
# perf-bisect step: $(short "$wj_sha") $(subject "$wj_sha")
JOB=bisect-$(short "$wj_sha")
REV_A=$GOOD
REV_B=$wj_sha
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
    printf '%s\n' "state: LO=$LO ($([ "$LO" -ge 0 ] && short "$(sha_at "$LO")" || echo GOOD)) HI=$HI ($(short "$(sha_at "$HI")")) skipped=$NSKIP"
    [ -n "$INCONSISTENT" ] && printf '%s\n' "INCONSISTENT: $INCONSISTENT"
    if pick; then
        dr_sha=$(sha_at "$PICK")
        printf '%s\n' "next probe: index $PICK $(short "$dr_sha") $(subject "$dr_sha")"
        printf '%s\n' "--- job file $JOBDIR/$(printf '%03d' "$PICK")-$(short "$dr_sha").job"
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
                dr_sha=$(sha_at "$PICK")
                s=$((s + 1)); printf '  step %d: index %3d %s %s\n' "$s" "$PICK" "$(short "$dr_sha")" "$(subject "$dr_sha")"
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
    i=$PICK sha=$(sha_at "$PICK")
    subj=$(subject "$sha") ssha=$(short "$sha")
    jf=$JOBDIR/$(printf '%03d' "$i")-$ssha.job
    write_job "$i" > "$jf"
    STEPS=$((STEPS + 1))
    say "step $STEPS: index $i $ssha $subj  (range LO=$LO HI=$HI)"
    out=$("$PERF_JOB" "$jf"); rc=$?
    line=$(printf '%s\n' "$out" | tail -n 1)
    dir=$(printf '%s\n' "$line" | sed -n 's#.*json \(.*\)/summary\.json.*#\1#p; s#.*log \(.*\)/remote\.log.*#\1#p' | head -1)
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    logline() { printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "$i" "$sha" "$1" "$2" "$3" "$4" "$5" "${dir:-NA}" "$subj" >> "$LOG"; }
    case "$rc" in
    75)
        logline RETRY NA NA NA NA
        say "RETRY $ssha: box busy, nothing measured: $line"
        exit 75 ;;
    0)
        if [ -z "$dir" ] || [ ! -f "$dir/summary.json" ]; then
            logline RETRY NA NA NA NA
            say "RETRY $ssha: no summary.json in the lab output: $line"; exit 75
        fi
        res=$(classify "$dir/summary.json") || { logline RETRY NA NA NA NA; say "RETRY $ssha: cannot read $dir/summary.json"; exit 75; }
        IFS=$TAB read -r cls why d ci band rd <<EOF
$res
EOF
        logline "$cls" "$d" "$ci" "$band" "$rd"
        say "$cls $ssha: $METRIC delta $d percent, CI $ci, null band $band, r_instructions $rd ($why)"
        if [ "$cls" = RETRY ]; then say "RETRY: no guess made; re-run to measure this commit again"; exit 75; fi
        apply "$i" "$cls" ;;
    1)
        err=$(cat "${dir:-/nonexistent}/error" 2>/dev/null)
        case "$err" in
        "build B failed"*)
            logline SKIP NA NA NA NA
            say "SKIP $ssha: the candidate does not build ($err); trying the nearest untested commit"
            apply "$i" SKIP ;;
        *)
            logline RETRY NA NA NA NA
            say "ERROR $ssha: lab error: ${err:-$line}"; exit 1 ;;
        esac ;;
    *)
        say "perf job exited $rc for $jf: $line"; exit 1 ;;
    esac
done
