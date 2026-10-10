#!/bin/bash
# capget-fallback-smoke.sh — start build/sbin/unitd unprivileged under
# tools/secexec with SYS_capget filtered, and prove the plan-capget-fallback.md
# (S3) fallback actually took effect: the new [warn] line fired, the router
# came up, and an app it then loads is running as the SAME uid unitd itself
# is -- not just that the control socket answered 200.
#
# Why "control socket returned 200" is not enough: unit's user/group
# switching for an app happens in the router/process machinery, not in the
# HTTP response -- a 200 only proves the app process exists and speaks HTTP,
# not which uid it runs as. This script's real assertion is the app's own
# /proc/self/status, read from inside the app process by a one-shot WSGI
# script this script writes and unit loads -- assert on the artifact, not on
# a log line about the artifact.
#
# Four variants exercise the plan's acceptance criteria:
#   --caps=none     the common case: no capabilities, filter -> must start,
#                    warn, and the app runs as unitd's own uid.
#   --caps=ambient   CAP_SETUID/CAP_SETGID granted via `setpriv --ambient-caps`
#                    before the filter is installed (the non-root analogue of
#                    systemd AmbientCapabilities=) -- the regression test for
#                    the child-side capability drop in
#                    nxt_capability_drop(). unitd itself HOLDS the two caps;
#                    the application it forks must end up with NONE. Before
#                    that drop landed the app inherited CapEff=...c0 straight
#                    through fork(), which is exactly what this asserts is
#                    gone. This variant is only as strong as its own
#                    precondition, so the precondition is checked rather than
#                    assumed: a pre-flight reads unitd's own
#                    /proc/<pid>/status from OUTSIDE the filter (procfs is not
#                    filtered; only unitd's own capget() call is) and fails
#                    the run if CapEff/CapAmb do not both carry the
#                    SETUID|SETGID bits (mask 0xc0) -- if setpriv silently
#                    dropped them across --reuid, this variant would
#                    otherwise pass while testing the exact same thing as
#                    caps=none. Together the two reads are the whole point:
#                    unitd HAD the capabilities, the application does NOT.
#   --caps=capset    the refusal path: CAP_SETUID/CAP_SETGID granted exactly
#                    as in --caps=ambient, but the filter denies capset(2)
#                    instead of capget(2), so the drop cannot happen at all.
#                    unitd must NOT then run the application with those
#                    capabilities intact: the prototype refuses to start and
#                    the request is answered 503. The router, controller and
#                    discovery processes get the same denial and must
#                    survive it -- refusing there would be an unbounded
#                    respawn loop (both carry .restart = 1) or, for
#                    discovery, a silent death, so this variant asserts that
#                    unitd comes up and stays up while only the application
#                    fails.
#   --caps=capset-none  the control for the variant above: capset(2) denied
#                    exactly the same way, but with NO capabilities granted,
#                    so there is nothing to refuse over. unitd must serve
#                    normally. Refusing here would be the regression -- a
#                    root unitd empties its sets by switching away from uid
#                    0, and an unprivileged one never had any, so in both
#                    the denied call would have done nothing and taking the
#                    application down buys nothing at all.
#   (default: all four, in that order)
#
# REQUIRES A PATCHED unitd. --unitd must point at a build/sbin/unitd from the
# fix/capget-fallback branch (a peer session builds that; this script does
# not build unit). Point it at your build with --unitd; everything else
# (temp layout, config, assertions) is ready to go against it as-is.
#
# Also requires tools/secexec + tools/capget-probe for the runner -- built
# automatically below (via cc) if the compiled binaries are not next to this
# script or given via --secexec/--capget-probe. capget-probe is not just
# built: it is run under secexec as a pre-flight, once per invocation, to
# prove the filter with this --errno actually survives execve on this host
# BEFORE either unitd variant is trusted to be running under it.
#
# BUILD NOTE: the host this was developed against (a Docker-only host) has no usable C
# compiler outside a container -- gcc does not exist there and /usr/bin/cc is
# tcc 0.9.27. A direct EPERM-path check against a tcc build of secexec.c on
# that host did not show a miscompile (correct errno, other syscalls still
# allowed) -- but that check could not exercise the compat-ABI kill path
# (needs a 32-bit cross build; tcc's own multilib story is thin), which is
# exactly the kind of struct-layout/initializer-order bug a non-optimizing,
# non-mainstream compiler is more likely to get wrong AND a quick smoke is
# least likely to catch. Treat tcc as unproven, not proven-fine: run this
# script inside a container with a real gcc/clang -- the same one unitd was
# built in. This script refuses to build with a `cc` that reports itself as
# tcc; override that guard (you should not need to, and doing so does not
# re-run the compat-ABI check above) with CC_ALLOW_TCC=1.
#
# CONTAINER NOTE: measured on Docker 29.7.2, alpine:3.22 and fedora:rawhide,
# 2026-08-29 -- prctl(PR_SET_SECCOMP) needed NO extra --security-opt under
# Docker's *default* seccomp profile (it mediates the modern seccomp(2)
# syscall and some prctl subcommands, but not PR_SET_SECCOMP filter
# installation, on that measured version). Don't pre-emptively add
# --security-opt seccomp=unconfined -- if your runner's profile differs and
# installation fails, secexec's own EACCES message tells you to add it.
#
# This script itself needs to start as root (container default is fine): it
# creates the temp tree, then drops to --uid/--gid via `setpriv` for the
# unitd invocation itself, which is the only process the assertions are
# about.
#
# Usage:
#   capget-fallback-smoke.sh --unitd PATH [options]
#
#   --unitd PATH        build/sbin/unitd from fix/capget-fallback. Required.
#   --modulesdir DIR     default: <dir of --unitd>/../lib/unit/modules
#                        (unit's own layout, same as test/conftest.py).
#   --app-type TYPE      unit application "type" for the python app config.
#                        Default "python" (bare -- works if the build has
#                        exactly one python module; pass "python 3.12" etc.
#                        if it has more than one).
#   --caps MODE          none | ambient | capset | capset-none | both | all
#                        (default: all; "both" is the old none+ambient pair).
#   --uid UID             --gid GID    target uid/gid to run unitd as.
#                        Default 1000:1000. Must not be 0.
#   --errno E            EPERM | ENOSYS | N, forwarded to secexec. Default
#                        EPERM. Run twice (once per value) to cover both
#                        signatures the fallback treats as "filtered".
#   --secexec PATH, --capget-probe PATH   pre-built binaries; skip the build.
#   --warn-pattern REGEX  grep -E pattern for the new [warn] line. Default
#                        matches the wording that actually landed in
#                        src/nxt_capability.c ("capget() failed ...; process
#                        capabilities are unknown"), not the plan's draft.
#                        Override if the landed wording differs.
#   --keep                leave the temp trees behind for inspection.
#
# Exit: 0 all requested variants passed; 1 a variant failed (see which, and
# why, in stderr and the kept logs); 2 usage / missing prerequisite.
#
# Example:
#   capget-fallback-smoke.sh --unitd /path/to/build/sbin/unitd

set -u

HARNESS="$(cd "$(dirname "$0")/.." && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"

UNITD=""
MODULESDIR=""
APP_TYPE="python"
CAPS_MODE="all"
TARGET_UID=1000
TARGET_GID=1000
TARGET_ERRNO="EPERM"
SECEXEC=""
CAPGET_PROBE=""
WARN_PATTERN='capget\(\) failed.*capabilities are unknown'
# The --caps=capset variant's three lines: nxt_capability_drop() reporting
# that it could not drop, the prototype refusing to start because of it,
# and the router saying the application has no process. All three are
# asserted, because each one alone has a benign reading.
CAPSET_WARN_PATTERN='capset\(\) failed.*capabilities this process holds could not be dropped'
REFUSED_PATTERN='refused to start: capset\(\) is denied'
START_FAILED_PATTERN="failed to start a process"
KEEP=0

die() { echo "capget-fallback-smoke.sh: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --unitd) UNITD="$2"; shift 2 ;;
        --modulesdir) MODULESDIR="$2"; shift 2 ;;
        --app-type) APP_TYPE="$2"; shift 2 ;;
        --caps) CAPS_MODE="$2"; shift 2 ;;
        --uid) TARGET_UID="$2"; shift 2 ;;
        --gid) TARGET_GID="$2"; shift 2 ;;
        --errno) TARGET_ERRNO="$2"; shift 2 ;;
        --secexec) SECEXEC="$2"; shift 2 ;;
        --capget-probe) CAPGET_PROBE="$2"; shift 2 ;;
        --warn-pattern) WARN_PATTERN="$2"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d'; exit 0 ;;
        *) die "unknown argument: $1 (see --help)" ;;
    esac
done

[ -n "$UNITD" ] || die "--unitd PATH is required (a patched unitd does not exist until the fix/capget-fallback branch is built)"
[ -x "$UNITD" ] || die "--unitd $UNITD is not an executable file"
[ "$TARGET_UID" != 0 ] && [ "$TARGET_GID" != 0 ] || die "--uid/--gid must not be 0 -- the whole point is testing the non-root path"

case "$CAPS_MODE" in none|ambient|capset|capset-none|both|all) : ;; *) die "--caps must be none, ambient, capset, capset-none, both, or all" ;; esac

if [ -z "$MODULESDIR" ]; then
    builddir="$(cd "$(dirname "$UNITD")/.." && pwd)"
    MODULESDIR="$builddir/lib/unit/modules"
fi
[ -d "$MODULESDIR" ] || die "modules dir $MODULESDIR does not exist -- pass --modulesdir explicitly"

for t in setpriv curl; do
    command -v "$t" >/dev/null 2>&1 || die "$t is required and not on PATH (util-linux and curl)"
done

# --- build secexec / capget-probe if not given -------------------------------

CC="${CC:-cc}"

build_tool() {
    local src="$1" out="$2"
    if "$CC" --version 2>&1 | grep -qi 'tiny c compiler\|tcc version'; then
        if [ "${CC_ALLOW_TCC:-0}" != 1 ]; then
            die "$CC is tcc -- unproven for secexec's BPF filter (see BUILD NOTE at the top of this script). Run this in a container with a real gcc/clang, or set CC=gcc / CC=clang. (Override with CC_ALLOW_TCC=1 if you have independently verified this tcc build -- you should not need to.)"
        fi
    fi
    "$CC" -O2 -Wall -Wextra -o "$out" "$src" || die "failed to build $src"
}

if [ -z "$SECEXEC" ]; then
    SECEXEC="$HERE/secexec.bin"
    build_tool "$HERE/secexec.c" "$SECEXEC"
fi
if [ -z "$CAPGET_PROBE" ]; then
    CAPGET_PROBE="$HERE/capget-probe.bin"
    build_tool "$HERE/capget-probe.c" "$CAPGET_PROBE"
fi
[ -x "$SECEXEC" ] || die "--secexec $SECEXEC is not executable"
[ -x "$CAPGET_PROBE" ] || die "--capget-probe $CAPGET_PROBE is not executable"

# --- pre-flight: prove the filter survives execve before trusting it around
# unitd. This is the thing that was previously left unrun: capget-probe was
# built and required but never invoked. Run it once per invocation (the
# errno is the same for both variants), from inside the exec'd image, not
# just checking that secexec's own prctl(PR_SET_SECCOMP) call succeeded.
echo "== capget-probe pre-flight: secexec --errno=$TARGET_ERRNO -- capget-probe =="
case "$TARGET_ERRNO" in
    EPERM|ENOSYS)
        PROBE_OUT="$("$SECEXEC" --errno="$TARGET_ERRNO" -- "$CAPGET_PROBE" --want="$TARGET_ERRNO" 2>&1)"
        PROBE_RC=$?
        echo "$PROBE_OUT"
        [ "$PROBE_RC" = 0 ] || die "capget-probe pre-flight failed: capget() did not fail with $TARGET_ERRNO across execve -- see output above. The unitd variants below cannot be trusted until this passes."
        ;;
    *)
        # capget-probe's --want only names EPERM/ENOSYS; for a bare numeric
        # --errno, run without --want (always exits 0) and check the
        # reported errno ourselves.
        PROBE_OUT="$("$SECEXEC" --errno="$TARGET_ERRNO" -- "$CAPGET_PROBE" 2>&1)"
        PROBE_RC=$?
        echo "$PROBE_OUT"
        [ "$PROBE_RC" = 0 ] || die "capget-probe pre-flight: capget-probe itself failed (exit $PROBE_RC) -- see output above"
        printf '%s\n' "$PROBE_OUT" | grep -q "errno=$TARGET_ERRNO(" \
            || die "capget-probe pre-flight: expected errno=$TARGET_ERRNO in probe output, got: $PROBE_OUT"
        ;;
esac
echo "-- capget-probe pre-flight OK: filter survives execve, capget() fails with $TARGET_ERRNO as expected"

# --- temp tree, one per variant ----------------------------------------------

TMPROOT="$(mktemp -d /tmp/capget-fallback-smoke.XXXXXX)"
# mktemp -d makes this 0700, owned by root -- chown -R on a variant's
# subdirectory below does not help the target uid reach it: traversal needs
# +x on every ancestor. o+x here, not a chown, so root keeps ownership of
# the shared root while --uid/--gid can still walk into their own variant
# dir (which IS chowned to them, and stays private to them via its own
# mode).
chmod 0711 "$TMPROOT"
cleanup() {
    local ec=$?
    if [ "$KEEP" = 1 ]; then
        echo "capget-fallback-smoke.sh: kept $TMPROOT (--keep)" >&2
    else
        rm -rf "$TMPROOT"
    fi
    exit "$ec"
}
trap cleanup EXIT INT TERM

FAILED=0

wait_for() {   # path timeout_s
    local path="$1" n="${2:-50}"
    while [ ! -e "$path" ] && [ "$n" -gt 0 ]; do
        sleep 0.1
        n=$((n - 1))
    done
    [ -e "$path" ]
}

# Own your processes: everything unitd spawns (router, controller, app
# workers) re-parents under the main pid's own session, started via setsid
# so it is a distinct process group we can signal as a unit and never
# confuse with this script's own group -- same discipline as
# tools/unitd-ctl.
reap() {   # pgid
    local pgid="$1"
    [ -n "$pgid" ] || return 0
    kill -TERM "-$pgid" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "-$pgid" 2>/dev/null || return 0
        sleep 0.5
    done
    kill -KILL "-$pgid" 2>/dev/null
}

run_variant() {
    local label="$1" caps="$2" deny="${3:-capget}"
    local wd="$TMPROOT/$label"
    local statedir="$wd/state" pidfile="$wd/unit.pid" logfile="$wd/unit.log"
    local controlsock="$wd/control.unit.sock" tmpdir="$wd/tmp"
    local appdir="$wd/app" appsock="$wd/app.sock"

    mkdir -p "$statedir" "$tmpdir" "$appdir"

    cat > "$appdir/wsgi.py" <<'PY'
def application(environ, start_response):
    # The point of this app: report the OS credentials of the process it is
    # actually running as, from inside that process -- not a log line
    # claiming what they are. Uid/Gid alone only prove unit switched user;
    # the Cap* lines are the assertion that matters, and in BOTH caps modes
    # they must all be zero -- in caps=ambient that is only true because
    # nxt_capability_drop() ran in the prototype this app was forked from.
    with open('/proc/self/status', 'rb') as f:
        status = f.read()
    prefixes = (
        b'Uid:', b'Gid:', b'CapInh:', b'CapPrm:', b'CapEff:', b'CapAmb:',
    )
    lines = [
        l for l in status.split(b'\n')
        if l.startswith(prefixes)
    ]
    body = b'\n'.join(lines) + b'\n'
    start_response(
        '200 OK',
        [('Content-Type', 'text/plain'), ('Content-Length', str(len(body)))],
    )
    return [body]
PY

    chown -R "$TARGET_UID:$TARGET_GID" "$wd" 2>/dev/null || {
        echo "-- $label: chown failed (need root to set up --uid/--gid $TARGET_UID:$TARGET_GID); skipping" >&2
        FAILED=1
        return
    }

    local -a priv=(setpriv --reuid="$TARGET_UID" --regid="$TARGET_GID" --clear-groups --no-new-privs)
    if [ "$caps" = ambient ]; then
        priv+=(--inh-caps=+setuid,+setgid --ambient-caps=+setuid,+setgid)
    fi

    echo "== $label (caps=$caps, deny=$deny, errno=$TARGET_ERRNO) =="

    setsid "${priv[@]}" -- \
        "$SECEXEC" --errno="$TARGET_ERRNO" --syscall="$deny" -- \
        "$UNITD" --no-daemon \
            --modulesdir "$MODULESDIR" \
            --statedir "$statedir" \
            --pid "$pidfile" \
            --log "$logfile" \
            --control "unix:$controlsock" \
            --tmpdir "$tmpdir" \
            >"$wd/stdout.log" 2>&1 <&- &
    local pgid=$!

    if ! wait_for "$controlsock" 50; then
        echo "FAIL $label: control socket never appeared -- see $logfile / $wd/stdout.log" >&2
        cat "$logfile" 2>/dev/null >&2
        reap "$pgid"
        FAILED=1
        return
    fi

    # Pre-flight: does unitd itself actually hold the caps this variant
    # claims to have granted? Read from OUTSIDE the seccomp filter -- this
    # script's own /proc/<pid>/status read is not a syscall the filter
    # touches, only unitd's own capget() call is filtered -- so this is
    # independent of, and does not rely on, anything unitd itself could
    # observe. Without this, if setpriv silently dropped the ambient caps
    # across --reuid, caps=ambient would pass while testing the exact same
    # thing as caps=none.
    if ! wait_for "$pidfile" 50; then
        echo "FAIL $label: pid file never appeared -- see $logfile / $wd/stdout.log" >&2
        cat "$logfile" 2>/dev/null >&2
        reap "$pgid"
        FAILED=1
        return
    fi
    local unitd_pid unitd_status cap_eff_hex cap_amb_hex
    unitd_pid=$(tr -d '[:space:]' < "$pidfile")
    unitd_status="/proc/$unitd_pid/status"
    if [ -z "$unitd_pid" ] || [ ! -r "$unitd_status" ]; then
        echo "FAIL $label: cannot read $unitd_status (pid '$unitd_pid' from $pidfile)" >&2
        reap "$pgid"
        FAILED=1
        return
    fi
    cap_eff_hex=$(awk '/^CapEff:/ {print $2; exit}' "$unitd_status")
    cap_amb_hex=$(awk '/^CapAmb:/ {print $2; exit}' "$unitd_status")
    echo "-- $label: unitd (pid $unitd_pid) CapEff=$cap_eff_hex CapAmb=$cap_amb_hex"
    if [ "$caps" = ambient ]; then
        if [ -z "$cap_eff_hex" ] || [ -z "$cap_amb_hex" ] \
            || [ "$(( 0x$cap_eff_hex & 0xc0 ))" != $((0xc0)) ] \
            || [ "$(( 0x$cap_amb_hex & 0xc0 ))" != $((0xc0)) ]; then
            echo "FAIL $label: ambient-caps pre-flight -- unitd's own CapEff=$cap_eff_hex CapAmb=$cap_amb_hex do not both carry SETUID|SETGID (mask 0xc0). setpriv --ambient-caps did not survive to unitd; this variant would otherwise silently test the same thing as caps=none." >&2
            reap "$pgid"
            FAILED=1
            return
        fi
    fi

    # Load the app: PUT the whole config in one shot.
    local cfg
    cfg=$(cat <<JSON
{
    "listeners": { "unix:$appsock": { "pass": "applications/whoami" } },
    "applications": {
        "whoami": {
            "type": "$APP_TYPE",
            "processes": { "spare": 0 },
            "path": "$appdir",
            "working_directory": "$appdir",
            "module": "wsgi"
        }
    }
}
JSON
    )
    local put_out
    put_out=$(curl -s --unix-socket "$controlsock" -X PUT --data-binary "$cfg" 'http://localhost/config')
    if ! printf '%s' "$put_out" | grep -q '"success"'; then
        echo "FAIL $label: config PUT did not report success: $put_out" >&2
        reap "$pgid"
        FAILED=1
        return
    fi

    if ! wait_for "$appsock" 50; then
        echo "FAIL $label: app listener socket never appeared after config PUT" >&2
        reap "$pgid"
        FAILED=1
        return
    fi

    # --- a denied capset() with capabilities actually held asserts a
    # --- refusal, not a response.  With none held (capset-nocaps) the
    # --- expectation is the ordinary one and this branch is skipped.
    if [ "$deny" = capset ] && [ "$caps" = ambient ]; then
        local ok=1 code
        code=$(curl -s -o /dev/null -w '%{http_code}' \
                    --unix-socket "$appsock" 'http://localhost/')

        # 503, not 200: the prototype exited rather than fork a worker that
        # would inherit CapEff=...c0, and the router failed the request
        # parked on the application instead of letting it time out. A 200
        # here is the bug this variant exists to catch -- it would mean the
        # application ran with unitd's capabilities.
        if [ "$code" != 503 ]; then
            echo "FAIL $label: app request returned HTTP $code, wanted 503 -- body was:" >&2
            curl -s --unix-socket "$appsock" 'http://localhost/' >&2
            ok=0
        fi

        local pat
        for pat in "$CAPSET_WARN_PATTERN" "$REFUSED_PATTERN" \
                   "$START_FAILED_PATTERN"; do
            if ! grep -qE "$pat" "$logfile"; then
                echo "FAIL $label: no line matching /$pat/ in $logfile" >&2
                ok=0
            fi
        done

        if ! grep -q 'router started' "$logfile"; then
            echo "FAIL $label: no 'router started' in $logfile" >&2
            ok=0
        fi

        # The other half, and the reason the refusal is not inside
        # nxt_capability_drop(): the core processes got the same denied
        # capset() and carried on. If they had refused, router and
        # controller (.restart = 1, re-forked with no backoff) would be in a
        # respawn loop and unitd would not be sitting here answering.
        if ! kill -0 "$unitd_pid" 2>/dev/null; then
            echo "FAIL $label: unitd (pid $unitd_pid) is gone -- the core processes did not survive the denial" >&2
            ok=0
        fi

        local cfg_code
        cfg_code=$(curl -s -o /dev/null -w '%{http_code}' \
                        --unix-socket "$controlsock" 'http://localhost/config')
        if [ "$cfg_code" != 200 ]; then
            echo "FAIL $label: controller answered HTTP $cfg_code on /config, wanted 200" >&2
            ok=0
        fi

        if [ "$ok" = 1 ]; then
            echo "PASS $label: capset() denied -- app refused with 503, prototype and router both said so, unitd still serving its control socket while holding CapEff=$cap_eff_hex CapAmb=$cap_amb_hex"
        else
            FAILED=1
        fi

        reap "$pgid"
        return
    fi

    local body
    body=$(curl -s --unix-socket "$appsock" 'http://localhost/')
    if [ -z "$body" ]; then
        echo "FAIL $label: app request returned no body" >&2
        reap "$pgid"
        FAILED=1
        return
    fi

    local ok=1

    # Only when capget(2) is what was denied: the warning is
    # nxt_capability_specific_set()'s, and a capset-only filter never
    # reaches it.
    if [ "$deny" = capget ] && ! grep -qE "$WARN_PATTERN" "$logfile"; then
        echo "FAIL $label: no [warn] line matching /$WARN_PATTERN/ in $logfile" >&2
        ok=0
    fi

    # A capset-only run with no capabilities granted must not have
    # refused anything: nxt_capability_still_held() had to find the sets
    # already empty and let the start proceed.
    if [ "$deny" = capset ] && grep -qE "$REFUSED_PATTERN" "$logfile"; then
        echo "FAIL $label: prototype refused to start although no capability was held -- see $logfile" >&2
        ok=0
    fi
    if ! grep -q 'router started' "$logfile"; then
        echo "FAIL $label: no 'router started' in $logfile" >&2
        ok=0
    fi

    # The assertion that matters: the app's OWN /proc/self/status, read from
    # inside the app process, says it is running as the uid we started
    # unitd as -- not root, not escalated, in EITHER caps mode.
    local app_uid
    app_uid=$(printf '%s\n' "$body" | awk '/^Uid:/ {print $2; exit}')
    if [ "$app_uid" != "$TARGET_UID" ]; then
        echo "FAIL $label: app /proc/self/status Uid was '$app_uid', wanted $TARGET_UID -- body:" >&2
        printf '%s\n' "$body" >&2
        ok=0
    fi

    # Same idea, for capabilities, and this is the assertion the whole
    # ambient variant exists for: EVERY set the app reports must be empty,
    # in BOTH caps modes. caps=none makes that trivially true and is only a
    # control; caps=ambient makes it true only because
    # nxt_capability_drop() ran in the prototype -- CapAmb is included
    # because it is the one set that would otherwise survive an execve()
    # into an "external" application.
    local app_cap_eff_hex="" set_name set_hex
    for set_name in CapInh CapPrm CapEff CapAmb; do
        set_hex=$(printf '%s\n' "$body" | awk -v n="^$set_name:" '$0 ~ n {print $2; exit}')
        if [ -z "$set_hex" ]; then
            if [ "$set_name" = CapAmb ]; then
                continue    # needs Linux 4.3
            fi
            echo "FAIL $label: app /proc/self/status had no $set_name: line -- body:" >&2
            printf '%s\n' "$body" >&2
            ok=0
            continue
        fi
        [ "$set_name" = CapEff ] && app_cap_eff_hex="$set_hex"
        if [ "$(( 0x$set_hex ))" != 0 ]; then
            echo "FAIL $label: app $set_name=$set_hex, wanted 0 -- unitd held CapEff=$cap_eff_hex CapAmb=$cap_amb_hex and the fork should not have passed that on -- body:" >&2
            printf '%s\n' "$body" >&2
            ok=0
        fi
    done

    if [ "$ok" = 1 ]; then
        echo "PASS $label: [warn] present, router started, app uid == $TARGET_UID, app capability sets all empty (CapEff=$app_cap_eff_hex) while unitd held CapEff=$cap_eff_hex CapAmb=$cap_amb_hex"
    else
        FAILED=1
    fi

    reap "$pgid"
}

case "$CAPS_MODE" in
    none)    run_variant "caps-none" none capget ;;
    ambient) run_variant "caps-ambient" ambient capget ;;
    capset)  run_variant "capset-denied" ambient capset ;;
    capset-none) run_variant "capset-nocaps" none capset ;;
    both)    run_variant "caps-none" none capget
             run_variant "caps-ambient" ambient capget ;;
    all)     run_variant "caps-none" none capget
             run_variant "caps-ambient" ambient capget
             run_variant "capset-denied" ambient capset
             run_variant "capset-nocaps" none capset ;;
esac

exit "$FAILED"
