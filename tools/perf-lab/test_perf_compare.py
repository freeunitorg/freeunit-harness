#!/usr/bin/env python3
"""Prove perf-compare.py's arithmetic on a fixed TSV before trusting it.

Run:  python3 tools/perf-lab/test_perf_compare.py      (exit 0 = math holds)

testdata/fixture.tsv was written by hand so every expected number below can
be checked with pencil and paper:

server_us, null phase, 2 blocks of 8 cells:
  each block: A = 100 101 99 100, B = 101 99 100 100 -> both means 100,
  both sample variances 2/3, block delta 0, block se = sqrt(2/3/4 * 2) = 0.57735
  job delta 0; se = sqrt(2 * 1/3) / 2 = 0.408248; df = 2 * 6 = 12; t = 2.179
  CI half-width = 0.889573 -> null band 0.889573 (PASS at limit 5)
server_us, ab phase:
  block 0: A = 100 102 98 100 (var 8/3), B = 95 97 93 95 (var 8/3) -> -5.0, se 1.154701
  block 1: A = 101 99 101 99 (var 4/3), B = 96 94 96 94 (var 4/3) -> -5.0, se 0.816497
  delta -5.0; se = sqrt(4/3 + 2/3) / 2 = 0.707107; half = 2.179 * se = 1.540786
  CI [-6.540786, -3.459214]; all 8 adjacent pairs have B < A -> CLAIM
rps, ab phase: B = A + 0.5 in every cell -> +0.5 percent, half-width 0.889573,
  CI [-0.389573, +1.389573] spans 0 -> NO CLAIM although 8 of 8 pairs agree
rsys_sendmsg: 2.0 per request everywhere, except B in the ab phase, where the
  probe printed no sendmsg line (so 0) -> -100 percent, null band 0 -> CLAIM, 2 of 2
fsize_bytes nxt_mp_free: 412 -> 380 = -32 bytes, -7.767 percent
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pc", os.path.join(HERE, "..", "perf-compare.py"))
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

FAIL = []


def near(name, got, want, tol=1e-4):
    if got is None or abs(got - want) > tol:
        FAIL.append("%s: got %r, want %r" % (name, got, want))


def eq(name, got, want):
    if got != want:
        FAIL.append("%s: got %r, want %r" % (name, got, want))


rows = pc.fill_probe_zeros(pc.load(os.path.join(HERE, "testdata", "fixture.tsv")))
res, sizes = pc.verdicts(rows, "server_us", 5.0, {})
get = {(r["phase"], r["metric"]): r for r in res}

n = get[("null", "server_us")]
near("null delta", n["delta_pct"], 0.0)
near("null ci_high", n["ci_high"], 0.889573)
near("null band", n["null_band_pct"], 0.889573)
eq("null verdict", n["verdict"], "PASS")
eq("null method", n["method"], "welch-blocks")

a = get[("ab", "server_us")]
near("ab a_mean", a["a_mean"], 100.0)
near("ab b_mean", a["b_mean"], 95.0)
near("ab delta", a["delta_pct"], -5.0)
near("ab ci_low", a["ci_low"], -6.540786)
near("ab ci_high", a["ci_high"], -3.459214)
eq("ab agree", (a["cells_agree"], a["cells_total"]), (8, 8))
eq("ab verdict", a["verdict"], "CLAIM")
eq("ab line", a["line"],
   "B is 5.0 percent less server CPU per request (us), outside the null band of 0.9, 8 of 8 cells agree")
eq("ab b_better", a["b_better"], True)

r = get[("ab", "rps")]
near("rps delta", r["delta_pct"], 0.5)
near("rps ci_low", r["ci_low"], -0.389573)
eq("rps agree", (r["cells_agree"], r["cells_total"]), (8, 8))
eq("rps verdict", r["verdict"], "NO CLAIM")

s = get[("ab", "rsys_sendmsg")]
near("sendmsg delta", s["delta_pct"], -100.0)
eq("sendmsg method", s["method"], "block-deltas")
eq("sendmsg agree", (s["cells_agree"], s["cells_total"]), (2, 2))
eq("sendmsg verdict", s["verdict"], "CLAIM")

eq("size line", sizes[0]["line"], "nxt_mp_free: 412 -> 380 bytes (-32, -7.8 percent)")
eq("headline", pc.headline(res, "server_us"),
   "CLAIM hello-c6: B is 5.0 percent less server CPU per request (us), "
   "outside the null band of 0.9, 8 of 8 cells agree")

# A tighter limit turns the same data into NOISY; a failed cell into INVALID.
res2, _ = pc.verdicts(rows, "server_us", 0.5, {})
eq("noisy verdict", {(x["phase"], x["metric"]): x for x in res2}[("ab", "server_us")]["verdict"], "NOISY")
bad = dict(rows[0], metric="bad", value=3.0, phase="ab")
res3, _ = pc.verdicts(rows + [bad], "server_us", 5.0, {})
eq("invalid verdict", {(x["phase"], x["metric"]): x for x in res3}[("ab", "server_us")]["verdict"], "INVALID")

# describe(): the database tree gets its own prefix, like router and server.
eq("describe db_us", pc.describe("db_us"), "database us per request")
eq("describe db_instructions", pc.describe("db_instructions"), "database instructions per request")
eq("describe s_instructions", pc.describe("s_instructions"), "server instructions per request")
eq("describe r_cycles", pc.describe("r_cycles"), "router cycles per request")

if FAIL:
    print("FAIL\n  " + "\n  ".join(FAIL))
    sys.exit(1)
print("OK: perf-compare arithmetic matches the hand-computed values (%d results)" % len(res))
