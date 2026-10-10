#!/bin/sh
# check-public.sh - fail when a file that would be committed carries a private detail.
#
# Usage: tools/check-public.sh [--all] [--history] [--deny-file PATH]
#   default        tracked files (index content, so staged edits count)
#   --all          also untracked files that are not ignored
#   --history      also every added line and message in `git log -p --all`
#   --deny-file F  private list, one ERE or literal per line, case-insensitive.
#                  HARNESS_DENY_FILE does the same. The file is never published.
# Exit: 0 clean, 1 at least one hit, 2 usage or setup error.
# Output: one TSV line per hit: file, line, rule id. The matched text is never
# printed, so a CI log does not repeat it. History hits use the file name
# "history:<sha8>:<path>" or "history:<sha8>:message".
# Rules: tools/public-deny.txt (id, TAB, ERE, TAB, "# why").
# Allowlist: tools/public-allow.txt. A line is an exact string a rule may match.
# A line "re:<ERE>" allows any match that the whole ERE matches.
# Files under tools/tests/check-public/ are test fixtures and are not scanned,
# and neither are the rule and allow files themselves.
#
# TODO: the HMAC layer of docs/public-harness-plan.md section 3
# (tools/public-deny.hmac, key HARNESS_DENY_KEY) is not implemented yet.
#
# The work is done by python3 (stdlib only); the code follows the marker line.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v python3 >/dev/null 2>&1 || { echo "check-public: python3 not found" >&2; exit 2; }
exec python3 -I -c "
import sys
src = open(sys.argv[1], encoding='utf-8').read().split('#--PY--\n', 1)[1]
sys.argv = sys.argv[1:]
exec(compile(src, sys.argv[0], 'exec'))
" "$here/check-public.sh" "$@"
exit 2
#--PY--
import os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(sys.argv[0]))
FIXTURES = "tools/tests/check-public/"
# The rule and allow files hold patterns and allowed strings, not content.
SELF = ("tools/public-deny.txt", "tools/public-allow.txt")


def die(msg):
    sys.stderr.write("check-public: %s\n" % msg)
    sys.exit(2)


def usage():
    sys.stderr.write("usage: check-public.sh [--all] [--history] [--deny-file PATH]\n")
    sys.exit(2)


def git(args, cwd=None, check=True):
    p = subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        die("git %s failed: %s" % (args[0], p.stderr.decode("utf-8", "replace").strip()))
    return p.stdout


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def load_rules(path):
    rules = []
    for n, line in enumerate(read_lines(path), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            die("%s:%d: want id TAB ERE" % (path, n))
        try:
            rules.append((parts[0], re.compile(parts[1])))
        except re.error as e:
            die("%s:%d: bad regex: %s" % (path, n, e))
    return rules


def load_allow(path):
    exact, pats = set(), []
    if not os.path.exists(path):
        return exact, pats
    for n, line in enumerate(read_lines(path), 1):
        if not line.strip() or line.startswith("#"):
            continue
        if line.startswith("re:"):
            try:
                pats.append(re.compile(r"(?:%s)\Z" % line[3:]))
            except re.error as e:
                die("%s:%d: bad regex: %s" % (path, n, e))
        else:
            exact.add(line)
    return exact, pats


def load_private(path):
    rules = []
    for n, line in enumerate(read_lines(path), 1):
        if not line.strip() or line.startswith("#"):
            continue
        try:
            rx = re.compile(line, re.I)
        except re.error:
            rx = re.compile(re.escape(line), re.I)
        rules.append(("private-%d" % n, rx))
    return rules


def main():
    do_all = do_hist = False
    deny_file = os.environ.get("HARNESS_DENY_FILE") or None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--all":
            do_all = True
        elif a == "--history":
            do_hist = True
        elif a == "--deny-file":
            i += 1
            if i >= len(args):
                usage()
            deny_file = args[i]
        elif a.startswith("--deny-file="):
            deny_file = a.split("=", 1)[1]
        else:
            usage()
        i += 1

    top = git(["rev-parse", "--show-toplevel"], check=False)
    if not top:
        die("not inside a git repository")
    top = top.decode().strip()
    # Rules and allowlist come from the script's own directory, so a scanned
    # checkout cannot weaken them.
    deny_path = os.path.join(HERE, "public-deny.txt")
    if not os.path.exists(deny_path):
        die("missing %s" % deny_path)
    rules = load_rules(deny_path)
    exact, pats = load_allow(os.path.join(HERE, "public-allow.txt"))
    if deny_file:
        if not os.path.isfile(deny_file):
            die("deny file not found: %s" % deny_file)
        rules += load_private(deny_file)

    def allowed(text):
        return text in exact or any(p.match(text) for p in pats)

    hits = []

    def scan(label, lineno, text):
        for rid, rx in rules:
            for m in rx.finditer(text):
                if not allowed(m.group(0)):
                    hits.append((label, lineno, rid))
                    break

    def scan_text(label, data):
        if b"\0" in data[:8192]:
            return
        for n, line in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
            scan(label, n, line)

    tracked = [p for p in git(["ls-files", "-z"], cwd=top).decode("utf-8", "replace").split("\0") if p]
    for path in tracked:
        if (path.startswith(FIXTURES) or path in SELF):
            continue
        blob = subprocess.run(["git", "show", ":0:" + path], cwd=top,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if blob.returncode == 0:
            scan_text(path, blob.stdout)
    if do_all:
        out = git(["ls-files", "-z", "--others", "--exclude-standard"], cwd=top)
        for path in out.decode("utf-8", "replace").split("\0"):
            if not path or (path.startswith(FIXTURES) or path in SELF):
                continue
            full = os.path.join(top, path)
            if os.path.isfile(full) and not os.path.islink(full):
                with open(full, "rb") as f:
                    scan_text(path, f.read())
    if do_hist:
        log = git(["log", "-p", "--all", "--no-color", "--no-ext-diff", "-U0",
                   "--format=commit %H%n%B%x00END"], cwd=top, check=False)
        sha, path, ln, in_msg = "", "", 0, False
        for line in log.decode("utf-8", "replace").split("\n"):
            if line.startswith("commit ") and len(line) == 47 and not in_msg:
                sha, path, ln, in_msg = line[7:15], "", 0, True
                msgn = 0
                continue
            if in_msg:
                if line.startswith("\0END"):
                    in_msg = False
                    continue
                msgn += 1
                scan("history:%s:message" % sha, msgn, line)
                continue
            if line.startswith("+++ "):
                path = line[6:] if line.startswith("+++ b/") else ""
                continue
            if line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                ln = int(m.group(1)) if m else 0
                continue
            if line.startswith("+") and path and not (path.startswith(FIXTURES) or path in SELF):
                scan("history:%s:%s" % (sha, path), ln, line[1:])
                ln += 1

    for label, n, rid in hits:
        print("%s\t%d\t%s" % (label, n, rid))
    sys.exit(1 if hits else 0)


main()
