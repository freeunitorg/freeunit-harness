#!/usr/bin/env python3
"""freq.py <sweep-dir>: the clock of the server cpus in each run, and whether rps follows it.

Reads <sweep-dir>/sweep.tsv, freq.log and meta.txt as sweep.sh writes them.
freq.log has one line per sample: "epoch kHz(cpu0) kHz(cpu1) ...". A run is the
window [ts - DUR, ts]; its clock is the mean of the server cpus over the
samples in it. The server cpus of a row with n router threads are the first
threads of cores 0..n-1, read from the "coreI T0,T1" lines of meta.txt.

Prints the median, min and max over all runs, the per-variant medians per cell,
and Spearman(freq, rps) per cell. A spread of variant medians of a few percent
in one cell, with a positive Spearman, means the cell compares clocks, not code.
"""
import collections
import csv
import os
import re
import statistics as st
import sys


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(xs, ys):
    if len(xs) < 3:
        return float("nan")
    rx, ry = ranks(xs), ranks(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else float("nan")


def read_meta(path):
    t0, dur = [], 15
    with open(path) as f:
        lines = f.read().splitlines()
    for line in lines:
        m = re.match(r"core(\d+) (\d+),", line)
        if m:
            t0.append(int(m.group(2)))
        m = re.search(r"\bDUR=(\d+)", line)
        if m:
            dur = int(m.group(1))
    return t0, dur


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    d = argv[1]
    t0, dur = read_meta(os.path.join(d, "meta.txt"))
    samples = []
    with open(os.path.join(d, "freq.log")) as f:
        for line in f:
            p = line.split()
            if len(p) >= 2:
                samples.append((int(p[0]), [int(x) / 1000 for x in p[1:]]))
    cell = collections.defaultdict(list)
    pairs = collections.defaultdict(list)
    allf = []
    with open(os.path.join(d, "sweep.tsv")) as f:
        for r in csv.DictReader(f, delimiter="\t"):
            try:
                rps = float(r["rps"])
            except ValueError:
                rps = 0
            if rps <= 0:   # a run without load: nothing to compare with the clock
                continue
            ts, n = int(r["ts"]), int(r["n"])
            cpus = t0[:n]
            win = [s for t, s in samples if ts - dur <= t <= ts and len(s) > max(cpus)]
            if not win:
                continue
            fq = st.mean(st.mean(s[c] for c in cpus) for s in win)
            cell[(r["workload"], n, r["variant"])].append(fq)
            pairs[(r["workload"], n)].append((fq, rps))
            allf.append(fq)
    if not allf:
        sys.exit("freq.py: no run window has samples")
    print(f"all runs: server-cpu freq median {st.median(allf):.0f} MHz, "
          f"min {min(allf):.0f}, max {max(allf):.0f} (n={len(allf)})")
    by = collections.defaultdict(list)
    for (w, n, v), fs in cell.items():
        by[(w, n)].append((v, st.median(fs)))
    for k in sorted(by):
        vals = sorted(by[k])
        meds = [m for _, m in vals]
        rho = spearman([a for a, _ in pairs[k]], [b for _, b in pairs[k]])
        print(f"{k[0]} n={k[1]}: variant medians {min(meds):.0f}..{max(meds):.0f} MHz, "
              f"Spearman(freq, rps) {rho:+.2f}; " + " ".join(f"{v}={m:.0f}" for v, m in vals))


if __name__ == "__main__":
    main(sys.argv)
