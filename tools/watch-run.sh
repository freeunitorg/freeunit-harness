#!/bin/bash
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

if [ "${BASH_VERSINFO[0]}" -lt 4 ] ||
   { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -lt 4 ]; }; then
    echo "this script needs bash >= 4.4 (found $BASH_VERSION)" >&2; exit 2
fi

REPO=""
RUN_ID=""
PR=""
BRANCH=""
INTERVAL=30
DEADLINE=3600

usage() { sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)     REPO="${2:-}"; shift 2 ;;
        --pr)       PR="${2:-}"; shift 2 ;;
        --branch)   BRANCH="${2:-}"; shift 2 ;;
        --interval) INTERVAL="${2:-}"; shift 2 ;;
        --deadline) DEADLINE="${2:-}"; shift 2 ;;
        -h|--help)  usage ;;
        -*)         echo "unknown option: $1" >&2; usage ;;
        *)          RUN_ID="$1"; shift ;;
    esac
done

command -v gh >/dev/null || { echo "gh is not installed" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is not installed" >&2; exit 2; }

R=()
[ -n "$REPO" ] && R=(--repo "$REPO")
gh_() { gh "$@" "${R[@]+"${R[@]}"}"; }

IDS=()

if [ -n "$RUN_ID" ]; then
    IDS=("$RUN_ID")
else
    SHA=""
    if [ -n "$PR" ]; then
        read -r BRANCH SHA < <(gh_ pr view "$PR" --json headRefName,headRefOid \
                                  -q '"\(.headRefName) \(.headRefOid)"') || {
            echo "cannot read PR $PR" >&2; exit 4; }
    fi
    [ -n "$BRANCH" ] || { echo "give a RUN_ID, --pr N or --branch NAME" >&2; usage; }

    runs=$(gh_ run list --branch "$BRANCH" --limit 50 \
              --json databaseId,headSha,workflowName) || {
        echo "gh run list failed" >&2; exit 4; }
    # For --branch, the head SHA is whatever the newest run was for.
    [ -n "$SHA" ] || SHA=$(jq -r '.[0].headSha // ""' <<<"$runs")
    [ -n "$SHA" ] || {
        echo "no run found on branch '$BRANCH' — a workflow whose paths filter" >&2
        echo "excluded this commit never starts one; do not wait for it." >&2
        exit 4; }
    mapfile -t IDS < <(jq -r --arg s "$SHA" \
                          '.[] | select(.headSha == $s) | .databaseId' <<<"$runs")
    [ "${#IDS[@]}" -gt 0 ] || {
        echo "no runs on $SHA (branch '$BRANCH')" >&2; exit 4; }
    echo "head $SHA: ${#IDS[@]} run(s)" >&2
fi

echo "watching ${#IDS[@]} run(s) (interval ${INTERVAL}s, deadline ${DEADLINE}s)" >&2

start=$(date +%s)
misses=0
failed=0
incomplete=0
declare -A DONE=()

while :; do
    pending=0
    for id in "${IDS[@]}"; do
        [ -n "${DONE[$id]:-}" ] && continue
        if ! json=$(gh_ run view "$id" \
                       --json status,conclusion,url,workflowName 2>/dev/null); then
            misses=$((misses + 1))
            echo "  ! gh run view $id failed ($misses/3)" >&2
            # Three in a row means the target is gone, not that the network
            # blinked. Keep asking a dead thing and you get two hours of
            # successful questions.
            [ "$misses" -ge 3 ] && { echo "runs unreachable" >&2; exit 4; }
            pending=$((pending + 1))
            continue
        fi
        misses=0
        status=$(jq -r .status <<<"$json")
        concl=$(jq -r '.conclusion // ""' <<<"$json")
        url=$(jq -r .url <<<"$json")
        name=$(jq -r .workflowName <<<"$json")

        if [ "$status" = "completed" ]; then
            DONE[$id]=1
            echo "RUN $id completed ${concl:-unknown} $url"
            if [ "$concl" != "success" ]; then
                failed=$((failed + 1))
                gh_ run view "$id" --json jobs \
                    -q '.jobs[] | select(.conclusion != "success" and .conclusion != "skipped")
                        | "  failed job: \(.name) (\(.conclusion))"' >&2 2>/dev/null
            fi
        else
            pending=$((pending + 1))
            echo "  $(date -u +%H:%M:%S) $name ($id): $status" >&2
        fi
    done

    if [ "$pending" -eq 0 ]; then
        if [ "$failed" -gt 0 ]; then
            echo "OVERALL failure ${#IDS[@]} run(s)"; exit 1
        fi
        echo "OVERALL success ${#IDS[@]} run(s)"; exit 0
    fi

    now=$(date +%s)
    if [ $((now - start)) -ge "$DEADLINE" ]; then
        for id in "${IDS[@]}"; do
            [ -n "${DONE[$id]:-}" ] || { echo "RUN $id in_progress deadline-reached"; incomplete=1; }
        done
        echo "OVERALL incomplete ${#IDS[@]} run(s)"
        echo "!! deadline of ${DEADLINE}s reached; $pending run(s) have NOT concluded." >&2
        exit 3
    fi
    sleep "$INTERVAL"
done
