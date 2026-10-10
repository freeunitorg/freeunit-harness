#!/usr/bin/env python3
"""Cross-over combine of two bench-release.sh RESULTS files whose build/slot
assignment was swapped between them (see --swap / SWAP=1 in
tools/bench-release.sh and skills/freeunit-benchmark/references/release-ab.md).

Why this exists: bench-release.sh's 8-cell counterbalanced block (X Y Y X Y
X X Y) cancels a LINEAR position/drift effect, but not a non-linear
per-slot effect. Observed on the reference bench host (2026-08-28): a real A/B read -1.58%
on a normal run and +0.23% on a --swap run of the SAME two builds -- part
of the -1.58% was which slot set the candidate happened to occupy, not the
candidate itself. Averaging a normal run and a swapped run cancels the
slot term, because it enters the two runs with opposite sign; half the
runs' disagreement estimates the residual slot-set bias instead of hiding
it (about +/-0.9% on the reference bench host -- the harness's resolution floor).

Usage:
    bench-crossover.py normal.tsv swapped.tsv

normal.tsv / swapped.tsv are RESULTS files from two bench-release.sh runs
of the SAME <labelA> <builddirA> <labelB> <builddirB> pair, the second run
with --swap (or SWAP=1). The label strings must match between the two
files -- swapping changes which SLOTS a build occupies, not the label text.
"""
import sys
import math
import statistics as st

_TCRIT = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
          7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}


def load(path):
    rows = []
    with open(path) as f:
        f.readline()  # header
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 3:
                continue
            rows.append((p[0], int(p[1]), float(p[2])))
    labels = []
    for c, _, _ in rows:
        if c not in labels:
            labels.append(c)
    if len(labels) != 2:
        sys.exit("%s: expected exactly 2 labels, found %s" % (path, labels))
    byrep = {}
    for c, rep, us in rows:
        byrep.setdefault(rep, []).append((c, us))
    return byrep, labels


def perrep(byrep, lab_pos, lab_neg):
    """mean(lab_pos) - mean(lab_neg) per repetition, reps>=1 (rep0 is warm-up)."""
    out = []
    for r in sorted(x for x in byrep if x >= 1):
        cells = byrep[r]
        vp = [u for c, u in cells if c == lab_pos]
        vn = [u for c, u in cells if c == lab_neg]
        if vp and vn:
            out.append(st.mean(vp) - st.mean(vn))
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: bench-crossover.py normal.tsv swapped.tsv")

    r1, l1 = load(sys.argv[1])
    r2, l2 = load(sys.argv[2])
    if set(l1) != set(l2):
        sys.exit("label mismatch between files: %s vs %s" % (l1, l2))
    A, B = l1

    d1 = perrep(r1, A, B)  # A - B, run 1 (normal slot assignment)
    d2 = perrep(r2, A, B)  # A - B, run 2 (--swap)
    if not d1 or not d2:
        sys.exit("no paired reps (rep>=1) in one of the runs")

    grand = st.mean(u for byrep in (r1, r2) for r in byrep if r >= 1
                     for _, u in byrep[r])

    m1, n1 = st.mean(d1), len(d1)
    m2, n2 = st.mean(d2), len(d2)
    s1 = st.stdev(d1) if n1 > 1 else 0.0
    s2 = st.stdev(d2) if n2 > 1 else 0.0

    print("expressed throughout as (%s minus %s)\n" % (A, B))
    print("run 1 (normal) : mean %+.3f us  sd %.3f  n %d" % (m1, s1, n1))
    print("run 2 (swap)   : mean %+.3f us  sd %.3f  n %d" % (m2, s2, n2))
    if (m1 > 0) != (m2 > 0):
        print("\nthe two runs disagree in SIGN -> a single run's estimate did not replicate")

    eff = (m1 + m2) / 2.0             # build effect, slot term cancels
    slot = (m2 - m1) / 2.0            # slot-set bias (residual instrument noise)
    se = 0.5 * math.sqrt(s1 * s1 / max(n1, 1) + s2 * s2 / max(n2, 1))
    df = n1 + n2 - 2
    tcrit = _TCRIT.get(df, 1.96 if df > 30 else 2.145)
    lo, hi = eff - tcrit * se, eff + tcrit * se

    print("\n--- crossover combine (slot assignment cancelled) ---")
    print("grand mean router CPU: %.3f us/req" % grand)
    print("build effect (%s - %s): %+.3f us  95%% CI [%+.3f, %+.3f]  (se %.3f, df %d)"
          % (A, B, eff, lo, hi, se, df))
    print("               relative     : %+.3f %%   95%% CI [%+.3f%%, %+.3f%%]"
          % (100 * eff / grand, 100 * lo / grand, 100 * hi / grand))
    zero = lo <= 0 <= hi
    print("  spans zero: %s -> %s"
          % (zero, "NO DIFFERENCE DETECTED" if zero else "SYSTEMATIC DIFFERENCE"))

    print("\n--- residual instrument bias ---")
    print("slot-set bias (half the run-to-run swing): %+.3f us  (%+.3f %% of grand mean)"
          % (slot, 100 * slot / grand))
    print("i.e. a build placed on the OTHER slot set from run 1 reads about %.2f%% more expensive"
          % abs(100 * slot / grand))


if __name__ == "__main__":
    main()
