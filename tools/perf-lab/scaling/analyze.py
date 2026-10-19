#!/usr/bin/env python3
"""analyze.py sweep.tsv [pairs]: medians with spread per cell, and A/B ratios.

pairs: "b:a,b:a", for example "post:pre,pad:base". Empty: the medians table only.
A ratio is b/a. 'paired' lists the per-round ratios: the rounds are interleaved,
so the two runs of one round are neighbours in time. 'ci95' is a bootstrap
interval of median(b)/median(a).

Verdict: "few runs" when a side has fewer than MIN_RUNS (5) runs: a bootstrap
of one or two runs per side gives a narrow interval that means nothing. Else
"noise" when the ci95 holds 1.0 or the paired ratios change sign, otherwise
"faster" or "slower". Even with 5 runs per side the interval is wide: a 1
percent difference was noise on every box measured so far
(docs/perf/scaling-kit.md).

A run without load is a failure, not a sample: its rps is "NA" (sweep.sh
could not read the oha result) or 0. Such a run is left out of the
medians, its cell is marked "NO LOAD k/n", and a ratio with such a run on
either side has the verdict "NO LOAD". analyze.py then prints the tables
and exits 1.

Notes per cell: LOADGEN-BOUND when oha used over 90 percent of its cpus (the
cell measures oha, not the server), UNSTABLE when the rps spread is over 6
percent, ERRORS when any request failed.
"""
import csv
import random
import statistics as st
import sys

MIN_RUNS = 5
LOADGEN_PCT = 90.0
UNSTABLE_PCT = 6.0


def load(path):
    cells = {}
    with open(path) as f:
        for r in csv.DictReader(f, delimiter="\t"):
            cells.setdefault((r["workload"], int(r["n"])), {}).setdefault(r["variant"], []).append(r)
    return cells


def loaded(x):
    """True when the run had load: a positive rps."""
    try:
        return float(x["rps"]) > 0
    except ValueError:
        return False


def med(g, f):
    return st.median(float(x[f]) for x in g)


def notes(g, failed=0):
    rps = [float(x["rps"]) for x in g]
    m = st.median(rps)
    out = ["NO LOAD %d/%d" % (failed, failed + len(g))] if failed else []
    if med(g, "load_cpu_pct") > LOADGEN_PCT:
        out.append("LOADGEN-BOUND")
    if m and 100 * (max(rps) - min(rps)) / m > UNSTABLE_PCT:
        out.append("UNSTABLE")
    if sum(int(x["bad"]) for x in g):
        out.append("ERRORS")
    return " ".join(out)


def ratio(a_rows, b_rows, boots=4000, seed=1):
    """-> (ratio of medians, sorted per-round ratios, ci low, ci high, verdict)."""
    if not all(map(loaded, a_rows)) or not all(map(loaded, b_rows)):
        return None, [], None, None, "NO LOAD"
    rnd = random.Random(seed)
    ra = {x["run"]: float(x["rps"]) for x in a_rows}
    rb = {x["run"]: float(x["rps"]) for x in b_rows}
    A, B = list(ra.values()), list(rb.values())
    r = st.median(B) / st.median(A)
    paired = sorted(rb[k] / ra[k] for k in ra if k in rb and ra[k] > 0)
    bs = []
    for _ in range(boots):
        sa = [rnd.choice(A) for _ in A]
        sb = [rnd.choice(B) for _ in B]
        bs.append(st.median(sb) / st.median(sa))
    bs.sort()
    lo, hi = bs[int(0.025 * len(bs))], bs[int(0.975 * len(bs)) - 1]
    mixed = bool(paired) and min(paired) < 1 < max(paired)
    if min(len(A), len(B)) < MIN_RUNS:
        verdict = "few runs"
    elif lo <= 1 <= hi or mixed:
        verdict = "noise"
    else:
        verdict = "faster" if r > 1 else "slower"
    return r, paired, lo, hi, verdict


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cells = load(argv[1])
    pairs = [p.split(":") for p in (argv[2] if len(argv) > 2 else "").split(",") if ":" in p]

    print("## medians per cell (n runs; spread = (max-min)/median)")
    print("| workload | n | layout | variant | runs | rps median | rps min..max | spread % | p50 ms | p99 ms "
          "| srv cpu % | load cpu % | bad | note |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    failed = 0
    for (w, n) in sorted(cells):
        for v, g_all in sorted(cells[(w, n)].items()):
            g = [x for x in g_all if loaded(x)]
            bad = len(g_all) - len(g)
            failed += bad
            if not g:
                print(f"| {w} | {n} | {g_all[0]['layout']} | {v} | {len(g_all)} | - | - | - | - | - | - | - | - "
                      f"| NO LOAD {bad}/{bad} |")
                continue
            rps = [float(x["rps"]) for x in g]
            m = st.median(rps)
            print(f"| {w} | {n} | {g[0]['layout']} | {v} | {len(g)} | {m:.0f} | {min(rps):.0f}..{max(rps):.0f} | "
                  f"{100 * (max(rps) - min(rps)) / m if m else 0:.1f} | {med(g, 'p50_ms'):.3f} | "
                  f"{med(g, 'p99_ms'):.3f} | {med(g, 'srv_cpu_pct'):.1f} | {med(g, 'load_cpu_pct'):.1f} | "
                  f"{sum(int(x['bad']) for x in g)} | {notes(g, bad)} |")
    if pairs:
        ratios(cells, pairs)
    if failed:
        sys.exit(f"analyze.py: {failed} runs had no load (rps NA or 0); no verdict uses them")


def ratios(cells, pairs):
    print()
    print("## A/B ratios (b/a, rps and p99)")
    print("| workload | n | b vs a | rps ratio (medians) | paired per-round ratios | ci95 | p99 ratio | verdict |")
    print("|---|---|---|---|---|---|---|---|")
    for (w, n) in sorted(cells):
        c = cells[(w, n)]
        for b, a in pairs:
            if a not in c or b not in c:
                continue
            r, paired, lo, hi, verdict = ratio(c[a], c[b])
            if r is None:
                print(f"| {w} | {n} | {b} vs {a} | - | - | - | - | {verdict} |")
                continue
            p99 = med(c[b], "p99_ms") / med(c[a], "p99_ms") if med(c[a], "p99_ms") else 0
            print(f"| {w} | {n} | {b} vs {a} | {r:.4f} ({100 * (r - 1):+.1f}%) | "
                  f"{' '.join(f'{p:.3f}' for p in paired)} | {lo:.3f}..{hi:.3f} | {p99:.3f} | {verdict} |")


if __name__ == "__main__":
    main(sys.argv)
