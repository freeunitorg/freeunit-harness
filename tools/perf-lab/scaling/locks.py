#!/usr/bin/env python3
"""locks.py: mutex calls per request from two snapshots of the mutexshim files.

    locks.py diff DIR VARIANT WORKLOAD N RUN OK   -> locks.tsv rows on stdout
    locks.py summary LOCKS_TSV                    -> calls and contended calls per request
    locks.py sites DIR [--trees PATH]             -> per call site, with addr2line names

DIR is one measured run (sweep.sh LOCKS=1): DIR/a and DIR/b hold copies of the
shim files taken before and after the measured window, DIR/a/x and DIR/b/x a
second copy 0.3 s later, and roles.txt the cmdline of each pid. DIR/oha.json is
the oha result of the window.

The count of a window is b minus a. Never read the files once at the exit of
unitd: that count holds the start-up, the warm-up and the shutdown, and a
process that is gone has written its last file up to a second before.

The shim rewrites each file once a second. A copy taken during the rewrite is
short, so each pid takes the larger of its two copies. A process can write two
files (mutex-PID-UID.tsv, before and after the drop to nobody); the larger one
wins there too.

--trees PATH replaces the leading /t of the object names, for a run of the
lab container read outside it.
"""
import collections
import glob
import json
import os
import re
import subprocess
import sys

ROLES = ("router", "controller", "prototype", "application", "main")


def load_counts(d):
    """{pid: (calls, contended, wait_ns)}, the largest copy of each pid."""
    tot = {}
    for f in glob.glob(d + "/mutex-*.tsv") + glob.glob(d + "/x/mutex-*.tsv"):
        pid = re.match(r".*/mutex-(\d+)-", f).group(1)
        c = ct = wt = 0
        with open(f) as fh:
            for line in fh:
                p = line.rstrip("\n").split("\t")
                try:
                    c += int(p[3]); ct += int(p[4]); wt += int(p[5])
                except (IndexError, ValueError):
                    pass
        old = tot.get(pid)
        if old is None or c >= old[0]:
            tot[pid] = (c, ct, wt)
    return tot


def load_sites(d):
    """{(pid, site, base, object): (calls, contended)}, the largest copy of each key."""
    t = {}
    for f in glob.glob(d + "/mutex-*.tsv") + glob.glob(d + "/x/mutex-*.tsv"):
        pid = re.match(r".*/mutex-(\d+)-", f).group(1)
        with open(f) as fh:
            for line in fh:
                p = line.rstrip("\n").split("\t")
                try:
                    k = (pid, int(p[0], 16), int(p[1], 16), p[2])
                    v = (int(p[3]), int(p[4]))
                except (IndexError, ValueError):
                    continue
                if k not in t or v[0] > t[k][0]:
                    t[k] = v
    return t


def load_roles(d):
    roles = {}
    path = os.path.join(d, "roles.txt")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                pid, _, rest = line.strip().partition(" ")
                roles[pid] = rest
    return roles


def role(cmdline):
    for k in ROLES:
        if k in cmdline:
            return k
    if "lab_capp" in cmdline:
        return "app"
    return cmdline.split(" ")[0] or "gone"


def diff(d, v, w, n, r, ok):
    a = load_counts(d + "/a")
    b = load_counts(d + "/b")
    roles = load_roles(d + "/b")
    out = []
    for pid, (c, ct, wt) in sorted(b.items(), key=lambda kv: int(kv[0])):
        c0, ct0, wt0 = a.get(pid, (0, 0, 0))
        out.append([v, w, n, r, ok, role(roles.get(pid, "")), pid, str(c - c0), str(ct - ct0), str(wt - wt0)])
    return out


def summary(path):
    agg = collections.defaultdict(lambda: [0, 0, 0])   # (w, n, v, r, role) -> calls, contended, ok
    with open(path) as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            x = dict(zip(head, line.rstrip("\n").split("\t")))
            k = (x["workload"], int(x["n"]), x["variant"], x["run"], x["role"])
            agg[k][0] += int(x["calls"]); agg[k][1] += int(x["contended"]); agg[k][2] = int(x["ok"])
    lines = ["workload n variant run role calls/req contended/req"]
    for k in sorted(agg):
        c, ct, ok = agg[k]
        lines.append(f"{k[0]} {k[1]} {k[2]} {k[3]} {k[4]} {c / ok if ok else 0:.4f} {ct / ok if ok else 0:.5f}")
    return lines


def sym(site, base, name):
    for addr in (site - base, site):
        try:
            o = subprocess.run(["addr2line", "-f", "-e", name, hex(addr)], capture_output=True,
                               text=True).stdout.split("\n")
        except OSError:
            return "?"
        if o and o[0] and not o[0].startswith("??"):
            return o[0] + " " + o[1].split("/")[-1]
    return "?"


def sites(d, trees=None):
    a, b = load_sites(d + "/a"), load_sites(d + "/b")
    roles = load_roles(d + "/b")
    with open(os.path.join(d, "oha.json")) as f:
        ok = int(json.load(f)["statusCodeDistribution"]["200"])
    agg = collections.defaultdict(lambda: [0, 0])
    for k, (c, ct) in b.items():
        c0, ct0 = a.get(k, (0, 0))
        key = (role(roles.get(k[0], "")), k[1], k[2], k[3])
        agg[key][0] += c - c0; agg[key][1] += ct - ct0
    lines = ["ok %d" % ok, "role    calls/req contended/req  site"]
    for k, (c, ct) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
        if not ok or c / ok < 0.01:
            continue
        obj = k[3]
        if trees and obj.startswith("/t/"):
            obj = os.path.join(trees, obj[3:])
        lines.append(f"{k[0]:7} {c / ok:9.3f} {ct / ok:13.4f}  {sym(k[1], k[2], obj)}  [{os.path.basename(k[3])}]")
    return lines


def main(argv):
    if len(argv) >= 8 and argv[1] == "diff":
        for row in diff(*argv[2:8]):
            print("\t".join(row))
    elif len(argv) == 3 and argv[1] == "summary":
        print("\n".join(summary(argv[2])))
    elif len(argv) in (3, 5) and argv[1] == "sites":
        trees = argv[4] if len(argv) == 5 and argv[3] == "--trees" else None
        print("\n".join(sites(argv[2], trees)))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
