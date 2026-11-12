#!/bin/sh
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

SRC=""; DEST=""; REV=""; ALLOW_DIFFER=0
PATHSPEC=""   # each --pathspec, quoted by shq, in the order given

usage() { sed -n '2,/^set -u/p' "$0" | sed 's/^# \{0,1\}//; $d'; exit 2; }

shq() {  # value -> one shell word that eval reads back as that value
    sq_r="" sq_s=$1
    while :; do
        case "$sq_s" in
        *\'*) sq_r="$sq_r${sq_s%%\'*}'\\''"; sq_s=${sq_s#*\'} ;;
        *) break ;;
        esac
    done
    printf "'%s'" "$sq_r$sq_s"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --rev)          [ $# -ge 2 ] || usage; REV="$2"; shift 2 ;;
        --pathspec)     [ $# -ge 2 ] || usage; PATHSPEC="$PATHSPEC $(shq "$2")"; shift 2 ;;
        --allow-differ) ALLOW_DIFFER=1; shift ;;
        -h|--help)      usage ;;
        -*)             printf '%s\n' "unknown option: $1" >&2; usage ;;
        *)              if [ -z "$SRC" ]; then SRC="$1"; else DEST="$1"; fi; shift ;;
    esac
done

[ -n "$SRC" ] && [ -n "$DEST" ] || usage
[ -d "$DEST" ] || { printf '%s\n' "destination is not a directory: $DEST" >&2; exit 2; }
git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1 || {
    printf '%s\n' "not a git tree: $SRC" >&2; exit 2; }

SRC=$(cd "$SRC" && pwd) || exit 2
DEST=$(cd "$DEST" && pwd) || exit 2

NL='
'
TAB=$(printf '\t')
BS=\\

# shellcheck disable=SC2059  # the format is \ooo or one of \a \b \f \r \v, checked by the case
# unquote PATH: git prints a path that holds a control character, '"' or '\'
# in double quotes, with C escapes: \n \t \" \\ and \ooo octal. Sets uq_out
# to the bytes of the path.
unquote() {
    uq_s=${1#\"}; uq_s=${uq_s%\"}; uq_out=""
    while [ -n "$uq_s" ]; do
        case $uq_s in
        "$BS"*)
            uq_s=${uq_s#?}
            case $uq_s in
            [0-7][0-7][0-7]*)
                uq_c=$(printf "$BS${uq_s%"${uq_s#???}"}x"); uq_c=${uq_c%x}; uq_s=${uq_s#???} ;;
            n*) uq_c=$NL; uq_s=${uq_s#?} ;;
            t*) uq_c=$TAB; uq_s=${uq_s#?} ;;
            [abfrv]*) uq_c=$(printf "$BS${uq_s%"${uq_s#?}"}x"); uq_c=${uq_c%x}; uq_s=${uq_s#?} ;;
            *) uq_c=${uq_s%"${uq_s#?}"}; uq_s=${uq_s#?} ;;   # \" and \\
            esac ;;
        *)
            uq_c=${uq_s%%"$BS"*}; uq_s=${uq_s#"$uq_c"} ;;
        esac
        uq_out=$uq_out$uq_c
    done
}

# One path per line. core.quotePath=false prints other bytes as they are; a
# path with a newline or a quote is still quoted, so a line is always one path.
# git quotes unusual paths in its default output; unquote reads them back.
eval "set -- $PATHSPEC"
if [ -n "$REV" ]; then
    FILES=$(git -C "$SRC" -c core.quotePath=false ls-tree -r --name-only "$REV" -- "$@")
else
    FILES=$(git -C "$SRC" -c core.quotePath=false ls-files -- "$@")
fi

[ -n "$FILES" ] || {
    echo "no tracked files matched — check SRC and --pathspec" >&2; exit 2; }

nfiles=0; missing=0; differ=0; unwritable=0
ndirs=0; SEEN_DIR=$NL   # the directories checked, one key per line

# dir_key DIR: sets dk_out to DIR with "\" written as "\\" and a newline as
# "\n". The key has no newline, so a whole-line match in SEEN_DIR is exact: a
# directory name that holds a newline cannot look like two entries and hide
# another directory from the writability check. A DIR without either
# character is its own key; it has no "\", so no encoded key can equal it.
dir_key() {
    case $1 in
    *["$BS$NL"]*) ;;
    *) dk_out=$1; return ;;
    esac
    dk_s=$1 dk_out=""
    while [ -n "$dk_s" ]; do
        dk_c=${dk_s%%["$BS$NL"]*}
        dk_out=$dk_out$dk_c; dk_s=${dk_s#"$dk_c"}
        case $dk_s in
        "$BS"*) dk_out="$dk_out$BS$BS" ;;
        "$NL"*) dk_out="$dk_out${BS}n" ;;
        esac
        dk_s=${dk_s#?}
    done
}

# A here-document, not a pipe: the loop sets the counts and exits in this shell.
while IFS= read -r f; do
    case $f in \"*) unquote "$f"; f=$uq_out ;; esac
    nfiles=$((nfiles + 1))
    d="$DEST/$f"

    # The directory is what rsync needs to write; check each one once.
    dir=$(dirname "$d")
    dir_key "$dir"
    case $SEEN_DIR in
    *"$NL$dk_out$NL"*) ;;
    *)
        SEEN_DIR="$SEEN_DIR$dk_out$NL"; ndirs=$((ndirs + 1))
        if [ -d "$dir" ] && [ ! -w "$dir" ]; then
            printf '%s\n' "UNWRITABLE ${dir#"$DEST"/}/"
            unwritable=$((unwritable + 1))
        fi ;;
    esac

    if [ ! -e "$d" ]; then
        printf '%s\n' "MISSING    $f"; missing=$((missing + 1)); continue
    fi

    if [ -n "$REV" ]; then
        src_hash=$(git -C "$SRC" rev-parse "$REV:$f" 2>/dev/null)
    else
        src_hash=$(git -C "$SRC" hash-object -- "$SRC/$f" 2>/dev/null)
    fi
    [ -n "$src_hash" ] || {
        printf '%s\n' "could not hash source file: $f (in $SRC)" >&2; exit 2; }

    dst_hash=$(git -C "$SRC" hash-object -- "$d" 2>/dev/null)
    if [ "$src_hash" != "$dst_hash" ]; then
        printf '%s\n' "DIFFER     $f"; differ=$((differ + 1))
    fi
done <<EOF
$FILES
EOF

echo
echo "checked $nfiles tracked files in $ndirs directories:" \
     "missing=$missing differ=$differ unwritable=$unwritable"
[ "$(id -u)" = 0 ] && echo "note: running as root — the writability check cannot fail" >&2

rc=0
[ "$missing" -gt 0 ] && rc=1
[ "$unwritable" -gt 0 ] && rc=1
[ "$differ" -gt 0 ] && [ "$ALLOW_DIFFER" = 0 ] && rc=1
exit $rc
