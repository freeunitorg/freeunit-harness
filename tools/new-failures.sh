#!/bin/bash
# new-failures.sh — run a checker at a base revision and at your tree, and fail
# only on failures your change INTRODUCED.
#
# Most useful checkers on an old codebase are not green. `openapi-example-check`
# reports 7 pre-existing FAILs under /config; a lint over 111k lines of C reports
# hundreds. A gate that demands zero is ignored within a week; a gate that
# demands "no NEW ones" is one you can actually keep. This generalises the
# before/after pattern documented in tools/openapi-example-check.py:
#
#     the number that matters is (sites failing after) - (sites failing before),
#     and it must be zero.
#
# Usage:
#   new-failures.sh --base REV [--repo DIR] [--match REGEX] [--key-sed EXPR]
#                   [--ok-status LIST] [--keep] -- CMD [ARGS...]
#
#   --base REV     revision to compare against (e.g. origin/master, a merge-base).
#   --repo DIR     source tree the checker inspects; default $UNIT_SRC or ./src.
#   --match REGEX  ERE selecting failure lines from CMD's stdout. Default '^FAIL '.
#   --key-sed EXPR sed -E expression reducing each matched line to a stable key,
#                  for checkers whose messages carry line numbers or counts that
#                  move for unrelated reasons. Default: compare the whole line.
#   --ok-status L  comma-separated exit statuses that mean "the checker ran".
#                  Default 0,1 — 0 clean, 1 found failures. Anything else aborts.
#   --keep         leave the base worktree in place for inspection.
#
# CMD runs with the CWD you invoked this from (so tools/… paths resolve), and
# every literal `{}` in CMD is replaced by the tree being checked. Typical:
#
#   tools/new-failures.sh --base origin/master -- \
#       tools/openapi-example-check.py --repo {}
#
# Exit: 0 no new failures · 1 new failures (each printed as `NEW <key>`)
#       2 usage · 3 the checker did not run (see below).
#
# Why 3 exists, and why --ok-status is not a formality: a checker that fails to
# START — missing PyYAML, a renamed path, a typo in the command — prints nothing,
# matches nothing, and looks exactly like a clean base run. The delta then comes
# out as "no new failures" for a checker that never executed. Every status
# outside --ok-status aborts, and an empty base run with a non-zero status aborts
# too, and so does an empty run on the working tree — the guard is symmetric,
# because a --match typo produces "no new failures" just as convincingly as a
# clean change does. Related: the `tee` trap in
# skills/freeunit-verify/references/false-green.md — this script never pipes CMD, it
# captures to a file and reads $? directly.
#
# The whole script runs under LC_ALL=C. GNU `comm` compares with the collating
# sequence (LC_COLLATE), not byte-wise, so sorting under C while comm runs under
# a UTF-8 locale makes it emit `comm: file 1 is not in sorted order` and pair the
# wrong lines — inventing NEW failures for a clean change. uutils' comm, which is
# what /usr/bin/comm is on some boxes here, *is* byte-wise and hides the bug
# locally; see skills/freeunit-verify/references/false-green.md on tool-dialect mismatch.

set -u
export LC_ALL=C          # must cover sort AND comm; see the header

if [ "${BASH_VERSINFO[0]}" -lt 4 ] ||
   { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -lt 4 ]; }; then
    echo "this script needs bash >= 4.4 (found $BASH_VERSION)" >&2; exit 2
fi

BASE=""
REPO="${UNIT_SRC:-$(cd "$(dirname "$0")/.." && pwd)/src}"
MATCH='^FAIL '
KEY_SED=""
OK_STATUS="0,1"
KEEP=0

usage() { sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --base)       BASE="${2:-}"; shift 2 ;;
        --repo)       REPO="${2:-}"; shift 2 ;;
        --match)      MATCH="${2:-}"; shift 2 ;;
        --key-sed)    KEY_SED="${2:-}"; shift 2 ;;
        --ok-status)  OK_STATUS="${2:-}"; shift 2 ;;
        --keep)       KEEP=1; shift ;;
        --)           shift; break ;;
        -h|--help)    usage ;;
        *)            echo "unknown option: $1" >&2; usage ;;
    esac
done

[ -n "$BASE" ] || { echo "--base is required" >&2; usage; }
[ $# -gt 0 ]   || { echo "no CMD given after --" >&2; usage; }
[ -d "$REPO/.git" ] || [ -f "$REPO/.git" ] || {
    echo "not a git tree: $REPO (pass --repo, or run 'make src')" >&2; exit 2; }

REPO=$(cd "$REPO" && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/new-failures.XXXXXX")
BASE_TREE="$WORK/base"

cleanup() {
    if [ "$KEEP" = 1 ]; then
        echo "kept: $WORK" >&2
        return
    fi
    [ -d "$BASE_TREE" ] && git -C "$REPO" worktree remove --force "$BASE_TREE" >/dev/null 2>&1
    rm -rf "$WORK"
}
CHILD=""

# Ctrl-C is the expected way to abort a long checker run, and bash does NOT run
# an EXIT trap when it dies of SIGINT — verified: the base worktree stayed
# registered in the user's repo, recoverable only with `git worktree prune`,
# which nothing would have told them to run. So: handle the signal, kill the
# checker, clean up, then die of the signal properly.
on_signal() {
    [ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null
    cleanup
    trap - INT TERM HUP EXIT
    exit 130
}
trap cleanup EXIT
trap on_signal INT TERM HUP

status_ok() {
    local s="$1" w
    local -a want
    IFS=',' read -ra want <<< "$OK_STATUS"
    for w in "${want[@]}"; do [ "$s" = "$w" ] && return 0; done
    return 1
}

# Run CMD against $1, capturing stdout+stderr to $2; sets RC.
# Backgrounded and waited on rather than run in the foreground: `wait` is
# interruptible, so a Ctrl-C during a ten-minute checker is handled now instead
# of whenever the checker happens to finish.
run_checker() {
    local tree="$1" out="$2" arg
    local -a cmd=()
    for arg in "${CMD[@]}"; do cmd+=("${arg//\{\}/$tree}"); done
    "${cmd[@]}" > "$out" 2>&1 &
    CHILD=$!
    wait "$CHILD"
    RC=$?                      # the child's status, never a pipeline's: after a
    CHILD=""                   # `tee`, PIPESTATUS/$? report tee, which always wins
}

keys_from() {
    if [ -n "$KEY_SED" ]; then
        grep -E -e "$MATCH" -- "$1" | sed -E "$KEY_SED"
    else
        grep -E -e "$MATCH" -- "$1"
    fi | LC_ALL=C sort -u
}

CMD=("$@")

BASE_SHA=$(git -C "$REPO" rev-parse --verify --quiet "$BASE^{commit}") || {
    echo "base revision not found in $REPO: $BASE" >&2; exit 2; }

echo "==> base worktree: $BASE ($BASE_SHA)"
git -C "$REPO" worktree add --detach "$BASE_TREE" "$BASE_SHA" >/dev/null 2>&1 || {
    echo "could not create a worktree at $BASE_SHA" >&2; exit 3; }

run_checker "$BASE_TREE" "$WORK/before.txt"; before_rc=$RC
echo "    checker exit $before_rc"
if ! status_ok "$before_rc"; then
    echo "!! checker exited $before_rc at the base (not in --ok-status=$OK_STATUS)." >&2
    echo "   The base run did not execute; a delta against it means nothing." >&2
    sed -n '1,20p' "$WORK/before.txt" >&2
    exit 3
fi

echo "==> working tree: $REPO"
run_checker "$REPO" "$WORK/after.txt"; after_rc=$RC
echo "    checker exit $after_rc"
if ! status_ok "$after_rc"; then
    echo "!! checker exited $after_rc on the working tree." >&2
    sed -n '1,20p' "$WORK/after.txt" >&2
    exit 3
fi

keys_from "$WORK/before.txt" > "$WORK/before.keys"
keys_from "$WORK/after.txt"  > "$WORK/after.keys"
nb=$(wc -l < "$WORK/before.keys"); na=$(wc -l < "$WORK/after.keys")

for side in before after; do
    eval "n=\$n${side:0:1} rc=\$${side}_rc"
    if [ "$n" -eq 0 ] && [ "$rc" -ne 0 ]; then
        echo "!! $side run matched no '$MATCH' lines but exited $rc — the" >&2
        echo "   --match pattern probably does not fit this checker's output." >&2
        exit 3
    fi
done

comm -13 "$WORK/before.keys" "$WORK/after.keys" | sed 's/^/NEW   /'
comm -23 "$WORK/before.keys" "$WORK/after.keys" | sed 's/^/FIXED /'

new=$(comm -13 "$WORK/before.keys" "$WORK/after.keys" | wc -l)
fixed=$(comm -23 "$WORK/before.keys" "$WORK/after.keys" | wc -l)
echo
echo "before: $nb  after: $na  new: $new  fixed: $fixed"

[ "$new" -eq 0 ] || exit 1
exit 0
