#!/usr/bin/env python3
"""Run shellcheck over the tracked shell scripts.

Usage: tools/check-shell.py [--severity error|warning|info|style] [--list]
  --severity  the lowest level that fails (default: error). CI uses the default.
  --list      print the files that would be checked, and exit.

A file is a shell script when it is tracked, is a regular file, and its first
line is a shebang for sh, bash, dash, ksh or ash (directly or through env).
A file without a shebang, such as the sourced tools/lib/env.sh, is not checked.

A file with a line "#--PY--" is a shell and Python polyglot
(tools/check-public.sh). Only the part before that line is shell, so only
that part is checked. Line numbers stay the same.

Exit: 0 clean, 1 findings, 2 shellcheck missing or another setup error.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

SHEBANG = re.compile(rb"^#!\s*(?:\S*/)?(?:env\s+)?(?:sh|bash|dash|ksh|ash)(?:\s|$)")
MARKER = b"#--PY--\n"
LEVELS = ("error", "warning", "info", "style")


def top():
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL)
    if p.returncode != 0:
        sys.stderr.write("check-shell: not inside a git repository\n")
        sys.exit(2)
    return p.stdout.decode().strip()


def shell_files(root):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, stdout=subprocess.PIPE,
                         check=True).stdout
    files = []
    for path in out.decode("utf-8", "replace").split("\0"):
        full = os.path.join(root, path)
        if not path or os.path.islink(full) or not os.path.isfile(full):
            continue
        with open(full, "rb") as f:
            if SHEBANG.match(f.readline()):
                files.append(path)
    return files


def shell_part(data):
    """Return the shell part of a script: everything before a #--PY-- line."""
    if data.startswith(MARKER):
        return b""
    i = data.find(b"\n" + MARKER)
    return data if i < 0 else data[: i + 1]


def main(argv):
    severity, listing = "error", False
    args = argv[1:]
    while args:
        a = args.pop(0)
        if a == "--severity" and args:
            severity = args.pop(0)
        elif a.startswith("--severity="):
            severity = a.split("=", 1)[1]
        elif a == "--list":
            listing = True
        else:
            sys.stderr.write(__doc__)
            return 2
    if severity not in LEVELS:
        sys.stderr.write("check-shell: severity must be one of %s\n" % ", ".join(LEVELS))
        return 2
    root = top()
    files = shell_files(root)
    if listing:
        print("\n".join(files))
        return 0
    sc = shutil.which("shellcheck")
    if not sc:
        sys.stderr.write("check-shell: shellcheck not found; see docs/ci.md\n")
        return 2
    print("check-shell: %s, severity %s, %d files" % (
        subprocess.run([sc, "--version"], stdout=subprocess.PIPE, text=True).stdout.split("\n")[1],
        severity, len(files)), flush=True)
    # Polyglots are checked from a copy cut at the marker, under the same
    # relative path, so the output names the real file.
    with tempfile.TemporaryDirectory() as tmp:
        for path in files:
            with open(os.path.join(root, path), "rb") as f:
                data = f.read()
            part = shell_part(data)
            if part is not data:
                dst = os.path.join(tmp, path)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(part)
        cwd = {p: (tmp if os.path.exists(os.path.join(tmp, p)) else root) for p in files}
        status = 0
        for where in (root, tmp):
            batch = [p for p in files if cwd[p] == where]
            if batch:
                rc = subprocess.run([sc, "--severity=" + severity, "--format=gcc"] + batch,
                                    cwd=where).returncode
                status = max(status, 1 if rc == 1 else (2 if rc else 0))
    print("check-shell: %s" % ("OK" if status == 0 else "FAILED"))
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
