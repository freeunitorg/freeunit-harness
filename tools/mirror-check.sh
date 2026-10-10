#!/bin/bash
# mirror-check.sh — prove that a copy of a repo still carries every committed
# file, byte for byte, and that the next sync can write into it.
#
# Run it after any rsync, docker cp, tarball unpack or container-produced tree
# that is supposed to mirror a git checkout. It answers one question: is
# anything the source has under version control missing, different, or sitting
# in a directory this user cannot write?
#
# Two real failures, both from the 1.36.1 cut (2026-08-29):
#
#   rsync --exclude 'build/'   — an unanchored pattern matches at EVERY depth,
#   so it silently dropped tools/unitctl/build/package.mk and (via a similar
#   pattern) unit-openapi/src/apis/error.rs. Those are committed sources whose
#   directory happens to be named like an artifact directory. The remote build
#   then failed for a reason that had nothing to do with the code. Anchor
#   artifact excludes to the tree root: --exclude '/build/', not 'build/'.
#
#   A container running as root wrote into the synced tree, and the next rsync
#   from the non-root user could not replace what it found. Note the mechanism,
#   because the obvious guess is wrong and was measured to be wrong: a
#   root-owned 0644 *file* does not block anything — rsync writes a temp file in
#   the containing directory and renames over the target, so it succeeds and
#   even takes ownership. What blocks it is a *directory* owned by the other
#   user: `mkstemp ".../sub/.f.txt.XXXXXX" failed: Permission denied (13)`.
#   Ownership is the operative half, not the mode — a directory you own you can
#   always chmod your way into, and rsync -a will do exactly that for you. So
#   this script checks directory writability, which is the question rsync
#   actually asks. Run containers as --user "$(id -u):$(id -g)" and neither
#   case arises.
#
# Usage:
#   mirror-check.sh SRC_REPO DEST [--rev REV] [--pathspec PATH...] [--allow-differ]
#
#   --rev REV        compare against that revision's tracked files rather than
#                    the index (use when DEST was built from a known commit).
#   --pathspec P     limit to a subtree; repeatable.
#   --allow-differ   content differences are reported but do not fail the run
#                    (for a destination that is deliberately patched).
#
# Output: one line per problem —
#   MISSING     <path>    tracked in SRC, absent in DEST
#   DIFFER      <path>    present but the contents are not identical
#   UNWRITABLE  <dir>/    a destination directory this user cannot write into
# then a count line.
#
# Exit: 0 clean · 1 problems found · 2 usage or a source file that could not be
# hashed (that last one used to be swallowed — see below).
#
# Both paths are resolved to absolute before use. They did not used to be, and a
# relative SRC made `git -C "$SRC" hash-object -- "$SRC/$f"` resolve twice: every
# hash came back empty, an emptiness the comparison quietly treated as "fine",
# and the tool certified a corrupted mirror as clean. A checker that cannot fail
# is the thing this whole directory exists to prevent.
#
# Running as root makes the writability half meaningless: uid 0 passes `[ -w ]`
# on any mode. The script says so rather than reporting a clean tree.

set -u

if [ "${BASH_VERSINFO[0]}" -lt 4 ] ||
   { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -lt 4 ]; }; then
    echo "this script needs bash >= 4.4 (found $BASH_VERSION)" >&2; exit 2
fi

SRC=""; DEST=""; REV=""; ALLOW_DIFFER=0
PATHSPEC=()

usage() { sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --rev)          REV="${2:-}"; shift 2 ;;
        --pathspec)     PATHSPEC+=("${2:-}"); shift 2 ;;
        --allow-differ) ALLOW_DIFFER=1; shift ;;
        -h|--help)      usage ;;
        -*)             echo "unknown option: $1" >&2; usage ;;
        *)              if [ -z "$SRC" ]; then SRC="$1"; else DEST="$1"; fi; shift ;;
    esac
done

[ -n "$SRC" ] && [ -n "$DEST" ] || usage
[ -d "$DEST" ] || { echo "destination is not a directory: $DEST" >&2; exit 2; }
git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1 || {
    echo "not a git tree: $SRC" >&2; exit 2; }

SRC=$(cd "$SRC" && pwd) || exit 2
DEST=$(cd "$DEST" && pwd) || exit 2

# -z / mapfile -d '': git quotes unusual paths in its default output, and a
# quoted path never matches the file it names.
if [ -n "$REV" ]; then
    mapfile -d '' -t FILES < <(git -C "$SRC" ls-tree -r -z --name-only "$REV" \
                                   -- "${PATHSPEC[@]+"${PATHSPEC[@]}"}")
else
    mapfile -d '' -t FILES < <(git -C "$SRC" ls-files -z \
                                   -- "${PATHSPEC[@]+"${PATHSPEC[@]}"}")
fi

[ "${#FILES[@]}" -gt 0 ] || {
    echo "no tracked files matched — check SRC and --pathspec" >&2; exit 2; }

missing=0; differ=0; unwritable=0
declare -A SEEN_DIR=()

for f in "${FILES[@]}"; do
    d="$DEST/$f"

    # The directory is what rsync needs to write; check each one once.
    dir=$(dirname "$d")
    if [ -z "${SEEN_DIR[$dir]:-}" ]; then
        SEEN_DIR[$dir]=1
        if [ -d "$dir" ] && [ ! -w "$dir" ]; then
            echo "UNWRITABLE ${dir#$DEST/}/"
            unwritable=$((unwritable + 1))
        fi
    fi

    if [ ! -e "$d" ]; then
        echo "MISSING    $f"; missing=$((missing + 1)); continue
    fi

    if [ -n "$REV" ]; then
        src_hash=$(git -C "$SRC" rev-parse "$REV:$f" 2>/dev/null)
    else
        src_hash=$(git -C "$SRC" hash-object -- "$SRC/$f" 2>/dev/null)
    fi
    [ -n "$src_hash" ] || {
        echo "could not hash source file: $f (in $SRC)" >&2; exit 2; }

    dst_hash=$(git -C "$SRC" hash-object -- "$d" 2>/dev/null)
    if [ "$src_hash" != "$dst_hash" ]; then
        echo "DIFFER     $f"; differ=$((differ + 1))
    fi
done

echo
echo "checked ${#FILES[@]} tracked files in ${#SEEN_DIR[@]} directories:" \
     "missing=$missing differ=$differ unwritable=$unwritable"
[ "$(id -u)" = 0 ] && echo "note: running as root — the writability check cannot fail" >&2

rc=0
[ "$missing" -gt 0 ] && rc=1
[ "$unwritable" -gt 0 ] && rc=1
[ "$differ" -gt 0 ] && [ "$ALLOW_DIFFER" = 0 ] && rc=1
exit $rc
