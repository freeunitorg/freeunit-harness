#!/bin/sh
# watch-run.sh — block until the GitHub Actions runs for a commit reach a
# terminal state, then exit with their combined conclusion.
#
# This script exists because of two failures, both from the 1.36.1 cut
# (2026-08-29):
#
#  1. An agent asked to "watch the pipeline" replied within seconds that
#     "monitoring is running" and stopped. Nothing was monitoring anything. A
#     watch is not a thing you start, it is a thing you finish — this script
#     blocks, and the only output that matters is its last lines.
#
#  2. A poller was left looping for two hours against a container that had died
#     in the first minute. Every iteration succeeded at asking, so nothing ever
#     looked wrong. Hence: a mandatory deadline, and an abort after three
#     consecutive failures to reach the API rather than an infinite retry.
#
# `gh run watch` alone is not enough: without --exit-status it exits 0 for a
# failed run (measured on gh 2.46.0: rc 0 without the flag, rc 1 with it), so a
# caller checking $? reads a red pipeline as green. This script always reports
# each conclusion explicitly and never infers.
#
# It watches ALL runs on the commit, not one. `gh run list --limit 1` returns
# the newest run of *any* workflow — on a FreeUnit PR head that is 1 of 6 — so a
# single-run watch reports success while five workflows are unwatched. That is
# the same false-green in a different costume.
#
# Usage:
#   watch-run.sh [--repo OWNER/REPO] [--interval SEC] [--deadline SEC] RUN_ID
#   watch-run.sh [--repo OWNER/REPO] --pr N        # every run on the PR head SHA
#   watch-run.sh [--repo OWNER/REPO] --branch NAME # every run on the branch tip
#
# Defaults: --interval 30, --deadline 3600, --repo from the CWD's remote.
#
# Output: progress to stderr; to stdout, one line per run watched —
#   RUN <id> <status> <conclusion> <url>
# followed by a final line
#   OVERALL <success|failure|incomplete> <n> run(s)
# Non-success runs also list their failing jobs on stderr. The exit paths that
# produce no RUN line (usage, unreachable) print nothing to stdout at all, so
# parse the OVERALL line, not the last line.
#
# Exit: 0 every run concluded success · 1 any other terminal conclusion · 2 usage
#       · 3 deadline reached with runs still in progress (state printed, nothing
#       assumed) · 4 the runs could not be reached.
#
# Report the conclusion, never the fact that the watch finished. "The workflow
# completed" is not a result; "failure — build (ubuntu-22.04)" is.
#
# Note for a caller that wants PR check state rather than run state: on gh
# 2.46.0 `gh pr checks --json` does not exist ("unknown flag: --json", exit 1) —
# and since `gh pr checks` also exits 1 to mean "checks failed", a wrapper
# cannot tell the two apart. `gh pr view --json statusCheckRollup` works on that
# version. Newer gh does document `--json` for `pr checks`; check yours.

set -u

REPO=""
RUN_ID=""
PR=""
BRANCH=""
INTERVAL=30
DEADLINE=3600

usage() { sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)     [ $# -ge 2 ] || usage; REPO="$2"; shift 2 ;;
        --pr)       [ $# -ge 2 ] || usage; PR="$2"; shift 2 ;;
        --branch)   [ $# -ge 2 ] || usage; BRANCH="$2"; shift 2 ;;
        --interval) [ $# -ge 2 ] || usage; INTERVAL="$2"; shift 2 ;;
        --deadline) [ $# -ge 2 ] || usage; DEADLINE="$2"; shift 2 ;;
        -h|--help)  usage ;;
        -*)         printf '%s\n' "unknown option: $1" >&2; usage ;;
        *)          RUN_ID="$1"; shift ;;
    esac
done

command -v gh >/dev/null || { echo "gh is not installed" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is not installed" >&2; exit 2; }

# stdin from /dev/null: watch_one runs inside a "while read" loop that reads a
# here-document, and nothing it calls may eat the lines of that loop.
gh_() { gh "$@" ${REPO:+--repo "$REPO"} </dev/null; }

# IDS: the run ids, one per line. A RUN_ID is taken as it is, as one id.
IDS=""
NL='
'

if [ -n "$RUN_ID" ]; then
    IDS=$RUN_ID
else
    SHA=""
    if [ -n "$PR" ]; then
        pr_line=$(gh_ pr view "$PR" --json headRefName,headRefOid \
                      -q '"\(.headRefName) \(.headRefOid)"')
        # As "read" of the first line: no line at all is a failure.
        [ -n "$pr_line" ] || { printf '%s\n' "cannot read PR $PR" >&2; exit 4; }
        read -r BRANCH SHA <<EOF
$pr_line
EOF
    fi
    [ -n "$BRANCH" ] || { echo "give a RUN_ID, --pr N or --branch NAME" >&2; usage; }

    runs=$(gh_ run list --branch "$BRANCH" --limit 50 \
              --json databaseId,headSha,workflowName) || {
        echo "gh run list failed" >&2; exit 4; }
    # For --branch, the head SHA is whatever the newest run was for.
    [ -n "$SHA" ] || SHA=$(printf '%s\n' "$runs" | jq -r '.[0].headSha // ""')
    [ -n "$SHA" ] || {
        printf '%s\n' "no run found on branch '$BRANCH' — a workflow whose paths filter" >&2
        echo "excluded this commit never starts one; do not wait for it." >&2
        exit 4; }
    IDS=$(printf '%s\n' "$runs" | jq -r --arg s "$SHA" \
              '.[] | select(.headSha == $s) | .databaseId')
    [ -n "$IDS" ] || {
        printf '%s\n' "no runs on $SHA (branch '$BRANCH')" >&2; exit 4; }
fi

nids=0
while IFS= read -r id; do nids=$((nids + 1)); done <<EOF
$IDS
EOF
[ -n "$RUN_ID" ] || printf '%s\n' "head $SHA: $nids run(s)" >&2

printf '%s\n' "watching $nids run(s) (interval ${INTERVAL}s, deadline ${DEADLINE}s)" >&2

start=$(date +%s)
misses=0
failed=0
FINISHED=$NL   # the ids that completed, one per line

# watch_one ID: one look at run ID; counts in pending, misses and failed.
watch_one() {
    id=$1
    case $FINISHED in *"$NL$id$NL"*) return 0 ;; esac
    if ! json=$(gh_ run view "$id" \
                   --json status,conclusion,url,workflowName 2>/dev/null); then
        misses=$((misses + 1))
        printf '%s\n' "  ! gh run view $id failed ($misses/3)" >&2
        # Three in a row means the target is gone, not that the network
        # blinked. Keep asking a dead thing and you get two hours of
        # successful questions.
        [ "$misses" -ge 3 ] && { echo "runs unreachable" >&2; exit 4; }
        pending=$((pending + 1))
        return 0
    fi
    misses=0
    status=$(printf '%s\n' "$json" | jq -r .status)
    concl=$(printf '%s\n' "$json" | jq -r '.conclusion // ""')
    url=$(printf '%s\n' "$json" | jq -r .url)
    name=$(printf '%s\n' "$json" | jq -r .workflowName)

    if [ "$status" = "completed" ]; then
        FINISHED="$FINISHED$id$NL"
        printf '%s\n' "RUN $id completed ${concl:-unknown} $url"
        if [ "$concl" != "success" ]; then
            failed=$((failed + 1))
            gh_ run view "$id" --json jobs \
                -q '.jobs[] | select(.conclusion != "success" and .conclusion != "skipped")
                        | "  failed job: \(.name) (\(.conclusion))"' >&2 2>/dev/null
        fi
    else
        pending=$((pending + 1))
        printf '%s\n' "  $(date -u +%H:%M:%S) $name ($id): $status" >&2
    fi
}

while :; do
    pending=0
    # A here-document, not a pipe: watch_one sets the counts and may exit.
    while IFS= read -r wid; do
        watch_one "$wid"
    done <<EOF
$IDS
EOF

    if [ "$pending" -eq 0 ]; then
        if [ "$failed" -gt 0 ]; then
            echo "OVERALL failure $nids run(s)"; exit 1
        fi
        echo "OVERALL success $nids run(s)"; exit 0
    fi

    now=$(date +%s)
    if [ $((now - start)) -ge "$DEADLINE" ]; then
        while IFS= read -r wid; do
            case $FINISHED in
            *"$NL$wid$NL"*) ;;
            *) printf '%s\n' "RUN $wid in_progress deadline-reached" ;;
            esac
        done <<EOF
$IDS
EOF
        echo "OVERALL incomplete $nids run(s)"
        printf '%s\n' "!! deadline of ${DEADLINE}s reached; $pending run(s) have NOT concluded." >&2
        exit 3
    fi
    sleep "$INTERVAL"
done
