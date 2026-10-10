#!/usr/bin/env python3
"""perf-compare.py -- compare A and B in a perf lab results TSV.

Every number a reader needs is computed here, so an agent never computes a
mean or a percentage itself. Standard library only. Prove the arithmetic
with:  python3 tools/perf-lab/test_perf_compare.py

Usage:
  perf-compare.py RESULTS.tsv [--meta META.txt] [--json SUMMARY.json]
                  [--primary server_us] [--null-limit 5]
                  [--null-band-from OTHER_SUMMARY.json]

Input: the TSV that tools/perf-job.sh writes, with exactly these columns:
  job phase side rev cell block slot metric value
  phase  null (A against a copy of A), ab (A against B), build (sizes)
  side   A or B
  block  one 8-cell block; even blocks run X Y Y X Y X X Y, odd blocks swap
  slot   0..7 timed cells, 8..9 strace probe cells

Method, per (phase, cell, metric):
  * per block, delta = (mean B - mean A) / mean A * 100, with a Welch
    standard error when each side has 2 or more values in the block;
    with one value per side, the spread of the per-block deltas is used.
  * the job delta is the mean of the block deltas, so a normal and a
    swapped block weigh the same and the slot-set bias cancels;
    se = sqrt(sum se_k^2) / K, df = sum(n_a + n_b - 2); 95 percent CI by t.
  * cells agree: adjacent slots (0,1) (2,3) (4,5) (6,7) (8,9) each hold one
    A and one B; a pair agrees when sign(B - A) equals the sign of the delta.
  * null band = |null delta| + null CI half-width, for the same cell and metric.
  * CLAIM when |delta| > null band, the CI excludes 0 and at least 3 of 4
    pairs agree; otherwise NO CLAIM. NOISY when the null band is over the
    limit. INVALID when any cell had a non-200 answer, an alert, or 0 requests.
"""
import argparse
import json
import math
import statistics as st
import sys

COLUMNS = ["job", "phase", "side", "rev", "cell", "block", "slot", "metric", "value"]
GUARDS = {"ok", "bad", "alerts", "probe_ok"}
HIGHER_IS_BETTER = {"rps"}
DESC = {
    "server_us": "server CPU per request (us)",
    "router_us": "router CPU per request (us)",
    "rps": "requests per second",
    "p50_ms": "p50 latency (ms)",
    "p99_ms": "p99 latency (ms)",
    "fsize_bytes": "function size (bytes)",
}
# two-sided 95 percent t quantiles
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
        14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
        20: 2.086, 22: 2.074, 24: 2.064, 26: 2.056, 28: 2.048, 30: 2.042,
        36: 2.028, 40: 2.021, 48: 2.011, 60: 2.000, 120: 1.980}


def tcrit(df):
    if df <= 0:
        return None
    if df in T975:
        return T975[df]
    lower = [k for k in T975 if k < df]
    return T975[max(lower)] if df < 120 else 1.960


def describe(metric):
    if metric in DESC:
        return DESC[metric]
    for p, who in (("r_", "router"), ("s_", "server"), ("db_", "database")):
        if metric.startswith(p):
            return "%s %s per request" % (who, metric[len(p):])
    for p, who in (("rsys_", "router"), ("asys_", "app")):
        if metric.startswith(p):
            return "%s %s syscalls per request" % (who, metric[len(p):])
    return metric


def load(path):
    rows = []
    with open(path) as f:
        head = f.readline().rstrip("\n").split("\t")
        if head != COLUMNS:
            sys.exit("%s: header is %s, expected %s" % (path, head, COLUMNS))
        for n, line in enumerate(f, 2):
            p = line.rstrip("\n").split("\t")
            if len(p) != len(COLUMNS):
                sys.exit("%s:%d: %d fields, expected %d" % (path, n, len(p), len(COLUMNS)))
            r = dict(zip(COLUMNS, p))
            r["block"], r["slot"], r["value"] = int(r["block"]), int(r["slot"]), float(r["value"])
            rows.append(r)
    return rows


def fill_probe_zeros(rows):
    """strace probes print only syscalls at >= 0.01 per request, so a
    syscall missing from one probe cell means 0 there, not no data."""
    probes = {(r["phase"], r["side"], r["cell"], r["block"], r["slot"], r["rev"], r["job"])
              for r in rows if r["metric"] == "probe_ok"}
    names = {(r["phase"], r["cell"], r["metric"]) for r in rows
             if r["metric"].startswith(("rsys_", "asys_"))}
    have = {(r["phase"], r["side"], r["cell"], r["block"], r["slot"], r["metric"]) for r in rows}
    out = list(rows)
    for (ph, side, cell, blk, slot, rev, job) in probes:
        for (nph, ncell, m) in names:
            if nph == ph and ncell == cell and (ph, side, cell, blk, slot, m) not in have:
                out.append({"job": job, "phase": ph, "side": side, "rev": rev, "cell": cell,
                            "block": blk, "slot": slot, "metric": m, "value": 0.0})
    return out


def compare(rows, phase, cell, metric):
    sel = [r for r in rows if r["phase"] == phase and r["cell"] == cell and r["metric"] == metric]
    blocks = sorted({r["block"] for r in sel})
    per = []
    for b in blocks:
        a = [r for r in sel if r["block"] == b and r["side"] == "A"]
        bb = [r for r in sel if r["block"] == b and r["side"] == "B"]
        if a and bb:
            per.append((b, sorted(a, key=lambda r: r["slot"]), sorted(bb, key=lambda r: r["slot"])))
    res = {"phase": phase, "cell": cell, "metric": metric, "description": describe(metric),
           "blocks": len(per), "n_a": sum(len(a) for _, a, _ in per),
           "n_b": sum(len(b) for _, _, b in per)}
    if not per:
        res.update(method="none", a_mean=None, b_mean=None, delta_pct=None, ci_low=None,
                   ci_high=None, cells_agree=0, cells_total=0)
        return res
    av = [r["value"] for _, a, _ in per for r in a]
    bv = [r["value"] for _, _, b in per for r in b]
    res["a_mean"], res["b_mean"] = st.mean(av), st.mean(bv)
    ds, ses, df = [], [], 0
    welch = all(len(a) >= 2 and len(b) >= 2 for _, a, b in per)
    for _, a, b in per:
        ma, mb = st.mean(r["value"] for r in a), st.mean(r["value"] for r in b)
        if ma == 0:
            ds = None
            break
        ds.append((mb - ma) / ma * 100.0)
        if welch:
            va, vb = st.variance([r["value"] for r in a]), st.variance([r["value"] for r in b])
            ses.append(math.sqrt(va / len(a) + vb / len(b)) / ma * 100.0)
            df += len(a) + len(b) - 2
    if ds is None:
        res.update(method="zero-baseline", delta_pct=None, ci_low=None, ci_high=None)
    else:
        delta = st.mean(ds)
        if welch:
            se = math.sqrt(sum(s * s for s in ses)) / len(ses)
            method = "welch-blocks"
        elif len(ds) >= 2:
            se, df = st.stdev(ds) / math.sqrt(len(ds)), len(ds) - 1
            method = "block-deltas"
        else:
            se, method = None, "single"
        t = tcrit(df) if se is not None else None
        half = t * se if t is not None else None
        res.update(method=method, delta_pct=delta,
                   ci_low=None if half is None else delta - half,
                   ci_high=None if half is None else delta + half)
    agree = total = 0
    sign = 0 if res["delta_pct"] in (None, 0) else (1 if res["delta_pct"] > 0 else -1)
    for _, a, b in per:
        bys = {r["slot"]: r for r in a + b}
        for lo in range(0, max(bys) + 1, 2):
            x, y = bys.get(lo), bys.get(lo + 1)
            if not x or not y or x["side"] == y["side"]:
                continue
            va = x["value"] if x["side"] == "A" else y["value"]
            vb = y["value"] if y["side"] == "B" else x["value"]
            total += 1
            d = vb - va
            if sign and d != 0 and (d > 0) == (sign > 0):
                agree += 1
    res["cells_agree"], res["cells_total"] = agree, total
    return res


def fmt(x, nd=1):
    return "n/a" if x is None else ("%.*f" % (nd, x))


def invalid_reason(rows, phase):
    bad = [r for r in rows if r["phase"] == phase and (
        (r["metric"] in ("bad", "alerts") and r["value"] > 0) or
        (r["metric"] in ("ok", "probe_ok") and r["value"] <= 0))]
    if not bad:
        return None
    r = bad[0]
    return "%d cell(s) failed, first: %s %s block %d slot %d %s=%g" % (
        len(bad), r["side"], r["cell"], r["block"], r["slot"], r["metric"], r["value"])


def verdicts(rows, primary, null_limit, external_null):
    results = []
    phases = sorted({r["phase"] for r in rows} - {"build"}, key=lambda p: (p != "null", p))
    invalid = {ph: invalid_reason(rows, ph) for ph in phases}
    nulls = {}
    for ph in phases:
        cells = sorted({r["cell"] for r in rows if r["phase"] == ph})
        for cell in cells:
            metrics = sorted({r["metric"] for r in rows if r["phase"] == ph and r["cell"] == cell} - GUARDS,
                             key=lambda m: (m != primary, m))
            for m in metrics:
                res = compare(rows, ph, cell, m)
                d = res["delta_pct"]
                word = "more" if d is not None and d > 0 else "less"
                res["b_better"] = None if d is None or d == 0 else ((d > 0) == (m in HIGHER_IS_BETTER))
                if ph == "null":
                    band = None if d is None else abs(d) + (
                        (res["ci_high"] - d) if res["ci_high"] is not None else 0.0)
                    res["null_band_pct"] = band
                    nulls[(cell, m)] = band
                    if invalid[ph]:
                        res["verdict"], res["line"] = "INVALID", "INVALID: " + invalid[ph]
                    elif band is None:
                        res["verdict"], res["line"] = "NO DATA", "no data for %s" % m
                    else:
                        ok = band <= null_limit
                        res["verdict"] = "PASS" if ok else "NOISY"
                        res["line"] = ("null gate: A and a copy of A differ by %s percent in %s, "
                                       "95 percent CI [%s, %s], null band %s, limit %s: %s" % (
                                           fmt(d), describe(m), fmt(res["ci_low"]), fmt(res["ci_high"]),
                                           fmt(band), fmt(null_limit), res["verdict"]))
                else:
                    band = nulls.get((cell, m), external_null.get((cell, m)))
                    res["null_band_pct"] = band
                    agree = "%d of %d cells agree" % (res["cells_agree"], res["cells_total"])
                    what = "B is %s percent %s %s" % (fmt(abs(d) if d is not None else None), word, describe(m))
                    ci_ex = res["ci_low"] is not None and (res["ci_low"] > 0 or res["ci_high"] < 0)
                    if res["ci_low"] is None:
                        ci_ex = res["cells_total"] > 0 and res["cells_agree"] == res["cells_total"]
                    if invalid[ph]:
                        v, line = "INVALID", "INVALID: " + invalid[ph]
                    elif d is None:
                        v, line = "NO DATA", "no comparable data for %s (baseline 0 or missing)" % m
                    elif band is None:
                        v, line = "NO NULL GATE", "no null gate for %s, no claim (%s)" % (m, what)
                    elif band > null_limit:
                        v, line = "NOISY", ("the null band %s is over the limit %s, no claim; rerun on a quiet box (%s)"
                                            % (fmt(band), fmt(null_limit), what))
                    elif abs(d) > band and ci_ex and res["cells_agree"] * 4 >= res["cells_total"] * 3:
                        v, line = "CLAIM", "%s, outside the null band of %s, %s" % (what, fmt(band), agree)
                    else:
                        v, line = "NO CLAIM", "inside the noise, no claim (%s, null band %s, %s)" % (
                            what, fmt(band), agree)
                    res["verdict"], res["line"] = v, line
                results.append(res)
    sizes = []
    for cell in sorted({r["cell"] for r in rows if r["phase"] == "build"}):
        a = [r["value"] for r in rows if r["phase"] == "build" and r["cell"] == cell and r["side"] == "A"]
        b = [r["value"] for r in rows if r["phase"] == "build" and r["cell"] == cell and r["side"] == "B"]
        if a and b:
            da = b[0] - a[0]
            pct = (da / a[0] * 100.0) if a[0] else None
            sizes.append({"phase": "build", "cell": cell, "metric": "fsize_bytes",
                          "description": "function size (bytes)", "a_mean": a[0], "b_mean": b[0],
                          "delta_pct": pct, "verdict": "EXACT",
                          "line": "%s: %d -> %d bytes (%+d, %s percent)" % (cell, a[0], b[0], da, fmt(pct))})
    return results, sizes


def headline(results, primary):
    prim = [r for r in results if r["metric"] == primary]
    ab = [r for r in prim if r["phase"] == "ab"]
    pick = ab if ab else prim
    if not pick:
        return "NO DATA: no rows for the primary metric %s" % primary
    return " ; ".join("%s %s: %s" % (r["verdict"], r["cell"], r["line"]) for r in pick)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tsv")
    ap.add_argument("--meta")
    ap.add_argument("--json")
    ap.add_argument("--primary", default="server_us")
    ap.add_argument("--null-limit", type=float, default=5.0)
    ap.add_argument("--null-band-from", help="summary.json of a null job on the same box and day")
    a = ap.parse_args()
    rows = fill_probe_zeros(load(a.tsv))
    meta = {}
    if a.meta:
        with open(a.meta) as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    meta[k] = v
    ext = {}
    if a.null_band_from:
        with open(a.null_band_from) as f:
            for r in json.load(f)["results"]:
                if r["phase"] == "null" and r.get("null_band_pct") is not None:
                    ext[(r["cell"], r["metric"])] = r["null_band_pct"]
    results, sizes = verdicts(rows, a.primary, a.null_limit, ext)
    head = headline(results, a.primary)
    print(head)
    if meta:
        print("job %s  run %s  A %s (%s)  B %s (%s)  governor %s/%s  %s" % (
            meta.get("job"), meta.get("run_id"), meta.get("rev_a"), meta.get("sha_a", "")[:10],
            meta.get("rev_b"), meta.get("sha_b", "")[:10], meta.get("governor"), meta.get("epp"),
            meta.get("cpu", "")))
    print("%-5s %-14s %-22s %12s %12s %8s %18s %6s %6s  %s" % (
        "phase", "cell", "metric", "A mean", "B mean", "delta%", "95% CI", "band", "agree", "verdict"))
    for r in results:
        ci = "n/a" if r["ci_low"] is None else "[%+.2f, %+.2f]" % (r["ci_low"], r["ci_high"])
        print("%-5s %-14s %-22s %12s %12s %8s %18s %6s %6s  %s" % (
            r["phase"], r["cell"], r["metric"], fmt(r["a_mean"], 3), fmt(r["b_mean"], 3),
            "n/a" if r["delta_pct"] is None else "%+.2f" % r["delta_pct"], ci,
            fmt(r.get("null_band_pct"), 2), "%d/%d" % (r["cells_agree"], r["cells_total"]), r["verdict"]))
    for s in sizes:
        print("size  " + s["line"])
    if a.json:
        out = {"job": meta.get("job") or (rows[0]["job"] if rows else None), "run_id": meta.get("run_id"),
               "primary": a.primary, "null_limit_pct": a.null_limit, "headline": head,
               "meta": meta, "results": results, "sizes": sizes}
        with open(a.json, "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
