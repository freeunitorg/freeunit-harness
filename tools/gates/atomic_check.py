#!/usr/bin/env python3
"""Fail on mixed atomic/plain accesses in a FreeUnit checkout.

The class: a value declared with one of the atomic typedefs in
src/nxt_atomic.h (nxt_atomic_t, nxt_atomic_uint_t, nxt_atomic_int_t,
nxt_nncq_atomic_t, nxt_app_nncq_atomic_t, nxt_nvbcq_atomic_t,
nxt_thread_spinlock_t) is read or written outside the nxt_atomic_* macros
and the __sync_* builtins.  The freeunit pull requests 730, 736, 742, 749 and
752 removed the known sites; this check stops new ones from coming back.

How it works:
  1. copies the checkout's tracked and untracked-but-not-ignored files to a
     temporary directory, so the checkout itself is never touched (no
     configure, no build/ rewrite);
  2. configures the copy, which writes build/include/nxt_auto_config.h;
  3. writes a compile_commands.json for src/*.c (src/test/ is out of scope);
  4. runs clang-query with atomic-query.txt over every src/*.c;
  5. compares the matches with atomic-baseline.txt.

A match is identified by its file and its matched source text, and the count
of each key is compared, so line shifts do not churn the baseline while a
second copy of a known match in the same file is new.  A new match and a
stale baseline entry both fail, the same rules as the ast-grep baseline.

The baseline holds only deliberate entries: initialization stores before the
object is shared; value-type uses of nxt_*_atomic_t through a private local;
a global initializer and the next-port-id reset in the child.  There is no
accepted debt left.  Anything else is a real mixed access: fix it, do not
baseline it.

The baseline was generated on freeunit master 4a0271cc5 with the src/nxt_unit.c
parked and quit_pending fix applied (661c0d5ea), which includes the pull
requests listed above.  A checkout without them reports the sites they fix,
which is correct: they are the same class until they merge.

Requirements: clang-query (Debian/Ubuntu package clang-tools-21, binary in
/usr/lib/llvm-*/bin) and clang.  Override the binary with CLANG_QUERY.

Usage: atomic_check.py <checkout> [--update] [--cc CC] [--keep]
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
QUERY = os.path.join(HERE, "atomic_query.txt")
BASELINE = os.path.join(HERE, "atomic_baseline.txt")


def die(msg):
    sys.exit("atomic_check: " + msg)


def find_clang_query():
    env = os.environ.get("CLANG_QUERY")
    if env:
        return env
    found = shutil.which("clang-query")
    if found:
        return found
    for cand in sorted(glob.glob("/usr/lib/llvm-*/bin/clang-query"),
                       reverse=True):
        return cand
    die("clang-query not found: install clang-tools-21 (Debian/Ubuntu) "
        "or set CLANG_QUERY")


def copy_tree(repo, dest):
    """Copy the files git would show: tracked plus untracked, not ignored."""
    out = subprocess.run(["git", "-C", repo, "ls-files", "-co",
                          "--exclude-standard", "-z"],
                         capture_output=True, check=True)
    files = [f for f in out.stdout.split(b"\0") if f]
    for raw in files:
        rel = raw.decode()
        src = os.path.join(repo, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst) or dest, exist_ok=True)
        try:
            # follow_symlinks=False keeps the tracked symlinks (for example
            # tools/unitctl/HomebrewFormula -> pkg/brew) as symlinks instead
            # of copying the directory they point at.
            shutil.copy2(src, dst, follow_symlinks=False)
        except FileNotFoundError:
            continue          # tracked but deleted in the working tree
    return len(files)


def configure(tree, cc):
    log = os.path.join(os.path.dirname(tree), "configure.log")
    env = dict(os.environ)
    env.pop("CFLAGS", None)
    env.pop("CC", None)
    if cc:
        env["CC"] = cc
    with open(log, "w") as fh:
        rc = subprocess.call(["./configure"], cwd=tree, stdout=fh,
                             stderr=subprocess.STDOUT, env=env)
    if rc != 0 or not os.path.exists(
            os.path.join(tree, "build/include/nxt_auto_config.h")):
        die("configure failed in the copy, see " + log)


def make_ccdb(tree, outdir):
    entries = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(tree, "src")):
        dirnames[:] = [d for d in dirnames if d != "test"]
        for fn in sorted(filenames):
            if not fn.endswith(".c"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), tree)
            entries.append({
                "directory": tree,
                "arguments": ["clang", "-std=gnu11",
                              "-D_FILE_OFFSET_BITS=64",
                              "-Isrc", "-Ibuild/include", "-c", rel],
                "file": rel,
            })
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "compile_commands.json"), "w") as fh:
        json.dump(entries, fh, indent=1)
    return [e["file"] for e in entries]


NOTE = re.compile(r'^(src/[^:]+):(\d+):(\d+): note: "([^"]+)" binds here')
EXCERPT = re.compile(r"^\s*\d+\s*\|\s?")


def parse(path):
    rows = []
    lines = open(path, errors="replace").readlines() if os.path.exists(path) \
        else []
    for i, line in enumerate(lines):
        m = NOTE.match(line)
        if m and i + 1 < len(lines):
            text = EXCERPT.sub("", lines[i + 1].rstrip()).strip()
            rows.append((m.group(1), text))
    return rows


def read_baseline(path):
    entries = []
    if not os.path.exists(path):
        return entries
    for line in open(path):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            die("bad baseline line (want file<TAB>text): " + line)
        entries.append((parts[0], parts[1]))
    return entries


def main():
    args = [a for a in sys.argv[1:]]
    update = "--update" in args
    keep = "--keep" in args
    cc = "clang"
    repo = None
    while args:
        a = args.pop(0)
        if a == "--update":
            continue
        if a == "--keep":
            continue
        if a == "--cc":
            cc = args.pop(0)
            continue
        if a.startswith("-"):
            die("unknown argument: " + a)
        repo = a
    if repo is None:
        die("usage: atomic_check.py <checkout> [--update] [--cc CC] [--keep]")
    repo = os.path.abspath(repo)
    if not os.path.exists(os.path.join(repo, "configure")):
        die(repo + " is not a FreeUnit checkout")

    cq = find_clang_query()
    tmp = tempfile.mkdtemp(prefix="atomic-check.")
    tree = os.path.join(tmp, "tree")
    try:
        n = copy_tree(repo, tree)
        print(f"atomic_check: {n} files copied, configuring")
        configure(tree, cc)
        files = make_ccdb(tree, tmp)
        matches = os.path.join(tmp, "matches.txt")
        with open(matches, "w") as fh:
            rc = subprocess.call([cq, "-p", tmp, "-f", QUERY] + files,
                                 cwd=tree, stdout=fh,
                                 stderr=subprocess.STDOUT)
        if rc != 0:
            die(f"clang-query exited {rc}, see {matches}")
        rows = parse(matches)
        if not rows and not update:
            die("no matches at all: the check is blind, refusing to pass")

        have = {}
        for key in rows:
            have[key] = have.get(key, 0) + 1
        want = {}
        for key in read_baseline(BASELINE):
            want[key] = want.get(key, 0) + 1

        new = sorted((k, have[k] - want.get(k, 0)) for k in have
                     if have[k] > want.get(k, 0))
        stale = sorted(k for k in want if want[k] > have.get(k, 0))

        if update:
            keys = sorted(rows)
            with open(BASELINE, "w") as fh:
                fh.write("# Mixed atomic/plain access baseline, one line per "
                         "match: file<TAB>matched source text.\n")
                fh.write("# Generated by tools/gates/atomic_check.py --update "
                         "on a configured copy.\n")
                fh.write("# See the script's docstring for the allowed "
                         "entries.\n")
                for f, t in keys:
                    fh.write(f"{f}\t{t}\n")
            print(f"atomic_check: baseline written, {len(keys)} entries")
            return 0

        if new:
            print(f"atomic-check: NEW mixed atomic/plain accesses "
                  f"({len(new)}):")
            for (f, t), extra in new:
                print(f"  {f}: {t}" + ("" if extra == 1 else f" (x{extra})"))
            print("  Fix the access with the nxt_atomic_* macro for that "
                  "type; baseline only if it is by design.")
        if stale:
            print(f"atomic-check: STALE baseline entries ({len(stale)}):")
            for f, t in stale:
                print(f"  {f}: {t}")
            print("  Remove them or regenerate with --update.")
        if new or stale:
            return 1
        print(f"atomic-check: PASS, {len(rows)} matches, all in the baseline")
        return 0
    finally:
        if keep:
            print("atomic_check: kept " + tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
