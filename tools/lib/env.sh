# tools/lib/env.sh -- the one reader of the lab env file. POSIX sh; source it.
#
# The file is ${LAB_ENV:-<harness>/run/lab.env}, gitignored. The template is
# tools/perf-lab/lab.env.example, and it documents every key in LAB_KEYS below.
# The file is optional: without it, lab_env_load sets nothing.
#
# Format:
#   KEY=VALUE, one per line. An "export " prefix is allowed.
#   Blank lines and lines that start with # are skipped.
#   An unquoted value ends at the first #; blanks around it are trimmed.
#   A value in matching single or double quotes loses those quotes, once.
#   The value is never evaluated: "$(cmd)" stays a literal string.
#   A later line for the same key replaces an earlier one.
#
# Precedence: a key that is already set and not empty in the environment
# keeps its value. The file never overrides it.
#
# Keys: the names in LAB_KEYS, plus any ROLE_HOST name (an extra host role
# for tools/hosts). Any other key is a warning on stderr and is ignored.
# With --strict it is an error instead (exit status 2 from lab_env_load).
#
# Functions:
#   lab_env_load [--strict] [--export]
#       Set every known key from the file as a shell variable. With --export,
#       also export each key the file sets. Returns 2 on a strict error.
#   lab_env_get KEY
#       Print one value (environment first, then the file). Sets nothing.
#   lab_build_root, lab_build_cache
#       The remote build root and cache dir, resolved the same way for
#       tools/hosts and tools/unit-build.
#
# The caller sets LAB_HARNESS to the harness root before sourcing, or this
# file finds it from its own path when the shell provides one.

LAB_KEYS="PERF_HOST SSH_USER REMOTE_WORKDIR CLAIM_DIR LAB_IMAGE OHA REPO_URL
SERVER_CPUS LOAD_CPUS DB_CPUS PERF_WINDOW GOVERNOR LAB_DOCKER_HOST SHORT_JOB_MIN
IDLE_MAX_PCT WAIT_MAX_MIN LEGACY_CLAIM_MAX_H BUILD_JOBS LAB_RESULTS
BUILD_HOST ALPINE_HOST INFER_HOST BOT_HOST BUILD_ROOT BUILD_CACHE
ALPINE_BUILD_IMAGE UNIT_REPO UNIT_BUILD_M32 ALPINE_M32_IMAGE"

: "${LAB_HARNESS:=$(cd "$(dirname "${BASH_SOURCE:-$0}")/../.." 2>/dev/null && pwd)}"
LAB_ENV=${LAB_ENV:-$LAB_HARNESS/run/lab.env}

# _lab_known KEY: true when KEY is a documented key or a ROLE_HOST name.
_lab_known() {
    case " $LAB_KEYS " in *[[:space:]]"$1"[[:space:]]*) return 0 ;; esac
    case $1 in [A-Z]*_HOST) return 0 ;; esac
    return 1
}

# _lab_env_set KEY: true when KEY is set and not empty in the environment.
# KEY is a checked name, never file content, so the eval is safe.
_lab_env_set() {
    eval "[ -n \"\${$1:-}\" ]"
}

# _lab_parse: read $LAB_ENV line by line. For each pair it calls
# _lab_on_pair KEY with the value in _lab_v. For a malformed line it calls
# _lab_on_bad MESSAGE. The line number is in _lab_n. The caller defines both.
_lab_parse() {
    _lab_n=0
    while IFS= read -r _lab_line || [ -n "$_lab_line" ]; do
        _lab_n=$((_lab_n + 1))
        _lab_line=${_lab_line#"${_lab_line%%[![:space:]]*}"}
        case $_lab_line in ''|'#'*) continue ;; esac
        case $_lab_line in 'export '*) _lab_line=${_lab_line#export } ;; esac
        case $_lab_line in
            *=*) ;;
            *) _lab_on_bad "not KEY=VALUE"; continue ;;
        esac
        _lab_k=${_lab_line%%=*}
        case $_lab_k in
            ''|[0-9]*|*[!A-Z0-9_]*) _lab_on_bad "not KEY=VALUE"; continue ;;
        esac
        _lab_v=${_lab_line#*=}
        _lab_v=${_lab_v#"${_lab_v%%[![:space:]]*}"}
        case $_lab_v in
            \"*\"*)
                _lab_v=${_lab_v#\"}; _lab_rest=${_lab_v#*\"}; _lab_v=${_lab_v%%\"*} ;;
            \'*\'*)
                _lab_v=${_lab_v#\'}; _lab_rest=${_lab_v#*\'}; _lab_v=${_lab_v%%\'*} ;;
            *)
                _lab_v=${_lab_v%%#*}; _lab_rest= ;;
        esac
        # After a closing quote only blanks or a comment may follow.
        _lab_rest=${_lab_rest#"${_lab_rest%%[![:space:]]*}"}
        case $_lab_rest in
            ''|'#'*) ;;
            *) _lab_on_bad "text after the closing quote"; continue ;;
        esac
        _lab_v=${_lab_v%"${_lab_v##*[![:space:]]}"}
        _lab_on_pair "$_lab_k"
    done < "$LAB_ENV"
}

lab_env_load() {
    _lab_strict=0 _lab_export=0 _lab_err=0
    while [ $# -gt 0 ]; do
        case $1 in
            --strict) _lab_strict=1 ;;
            --export) _lab_export=1 ;;
        esac
        shift
    done
    [ -f "$LAB_ENV" ] || return 0
    # The first time the file names a key, the environment is checked once.
    # A key the environment holds is skipped on every line. A key the file
    # owns takes each later line, so the last line wins.
    _lab_envkeys=" " _lab_filekeys=" "
    _lab_on_bad() {
        if [ "$_lab_strict" = 1 ]; then
            printf 'lab env: %s:%s: %s\n' "$LAB_ENV" "$_lab_n" "$1" >&2
            _lab_err=1
        else
            printf 'lab env: %s:%s: %s, ignored\n' "$LAB_ENV" "$_lab_n" "$1" >&2
        fi
    }
    _lab_on_pair() {
        if ! _lab_known "$1"; then
            _lab_on_bad "unknown key $1"
            return 0
        fi
        case $_lab_envkeys in *" $1 "*) return 0 ;; esac
        case $_lab_filekeys in
            *" $1 "*) ;;
            *) if _lab_env_set "$1"; then
                   _lab_envkeys="$_lab_envkeys$1 "
                   return 0
               fi
               _lab_filekeys="$_lab_filekeys$1 " ;;
        esac
        eval "$1=\$_lab_v"
        if [ "$_lab_export" = 1 ]; then export "$1"; fi
        return 0
    }
    _lab_parse
    [ "$_lab_err" = 0 ] || return 2
    return 0
}

lab_env_get() {
    case $1 in ''|[0-9]*|*[!A-Z0-9_]*) return 0 ;; esac
    if _lab_env_set "$1"; then eval "printf '%s\n' \"\${$1}\""; return 0; fi
    [ -f "$LAB_ENV" ] || { printf '\n'; return 0; }
    _lab_want=$1 _lab_found=
    _lab_on_bad() { :; }
    _lab_on_pair() { [ "$1" = "$_lab_want" ] && _lab_found=$_lab_v; return 0; }
    _lab_parse
    printf '%s\n' "$_lab_found"
}

# Remote build root: UNIT_REMOTE_ROOT, else BUILD_ROOT, else REMOTE_WORKDIR.
lab_build_root() {
    _lab_r=${UNIT_REMOTE_ROOT:-}
    [ -n "$_lab_r" ] || _lab_r=$(lab_env_get BUILD_ROOT)
    [ -n "$_lab_r" ] || _lab_r=$(lab_env_get REMOTE_WORKDIR)
    printf '%s\n' "$_lab_r"
}

# Remote cache dir: UNIT_REMOTE_CACHE, else BUILD_CACHE, else <root>/cache.
# Empty when there is no root either.
lab_build_cache() {
    _lab_c=${UNIT_REMOTE_CACHE:-}
    [ -n "$_lab_c" ] || _lab_c=$(lab_env_get BUILD_CACHE)
    if [ -z "$_lab_c" ]; then
        _lab_c=$(lab_build_root)
        [ -z "$_lab_c" ] || _lab_c=$_lab_c/cache
    fi
    printf '%s\n' "$_lab_c"
}
