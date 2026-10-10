#!/usr/bin/env python3
"""ci-cost.py -- what a workflow run actually cost, measured from the API.

Reading a workflow YAML tells you what a run is *supposed* to do. It cannot tell
you that a job spent 14 minutes queued, that a matrix leg you thought was
excluded ran anyway, or that a `timeout-minutes` you added did nothing. Those
answers are in `/repos/{repo}/actions/runs/{id}/jobs` and `/timing`, and both are
one `gh api` call away -- so measure, don't reason about the YAML.

Two things this exists to make visible, both learned on freeunit (2026-08-29):

  * `timeout-minutes` bounds EXECUTION, not queue time. A run that is slow
    because it waited 20 minutes for a runner cannot trip it, and looks
    identical, in the run's wall clock, to one that was slow while running.
    This tool splits the two: queue = created_at -> started_at, execution =
    started_at -> completed_at.

  * Billable minutes are not the sum of job durations. GitHub rounds each job
    up to the whole minute and multiplies by the runner's factor, so many short
    jobs cost far more than their total runtime suggests. `/timing` is the only
    honest source for that number -- but on a PUBLIC repository it reports
    `total_ms: 0` for every runner class, because those minutes are free
    (verified on freeunitorg/freeunit run 33235501540, 2026-08-29). A budget
    check against that zero would pass unconditionally, which is exactly the
    shape of a gate that cannot fail. So when `/timing` reports zero, this tool
    falls back to per-job execution time rounded up to the minute, and prints
    which basis the budget was checked against.

Usage:
  ci-cost.py RUN_ID [--repo OWNER/REPO] [--budget-minutes N] [--json]
  ci-cost.py --pr N  [--repo OWNER/REPO] ...      # newest run on the PR head
  ci-cost.py --branch NAME [--repo OWNER/REPO] ...

Exit: 0 ok - 1 --budget-minutes exceeded (on the basis named below) -
      2 usage or API error (never 1, so a gate can tell the two apart).
Without --budget-minutes it is a report, not a gate, and always exits 0.

Needs `gh` authenticated (it shells out to `gh api`); no Python dependencies.
"""
import argparse
import datetime as dt
import json
import math
import subprocess
import sys


def die(msg):
    """Every error exits 2, so a --budget-minutes gate can tell "this run cost
    too much" (exit 1) from "gh is missing / no such run" (exit 2). sys.exit(str)
    exits 1, which collapsed the two — the same defect this toolkit's own
    new-failures.sh has an exit 3 to avoid."""
    print(msg, file=sys.stderr)
    sys.exit(2)


def gh_api(path):
    cmd = ["gh", "api", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        die("gh is not installed")
    except subprocess.CalledProcessError as e:
        die(f"gh api {path} failed: {e.stderr.strip()[:300]}")
    return json.loads(out)


def all_jobs(repo, run_id):
    """Every job of a run, paged explicitly.

    Not `gh api --paginate`: on this endpoint the response is an OBJECT
    ({total_count, jobs:[…]}), and --paginate concatenates one such object per
    page with nothing joining them, so json.loads raises "Extra data" the moment
    a run has more than one page. Verified on gh 2.46.0, which also has no
    --slurp. The bug only appears above 100 jobs, which is exactly when nobody
    is watching.
    """
    jobs, page = [], 1
    while True:
        r = gh_api(f"/repos/{repo}/actions/runs/{run_id}/jobs"
                   f"?per_page=100&page={page}")
        batch = r.get("jobs", [])
        jobs.extend(batch)
        if not batch or len(jobs) >= r.get("total_count", len(jobs)):
            return jobs
        page += 1


def ts(value):
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def secs(a, b):
    if not a or not b:
        return None
    return (b - a).total_seconds()


def fmt(s):
    if s is None:
        return "     -"
    return f"{int(s) // 60:3d}m{int(s) % 60:02d}s"


def resolve_run(args, repo_arg):
    if args.run_id:
        return args.run_id
    branch = args.branch
    if args.pr:
        pr = gh_api(f"/repos/{repo_arg}/pulls/{args.pr}")
        branch = pr["head"]["ref"]
    if not branch:
        die("give a RUN_ID, --pr N or --branch NAME")
    runs = gh_api(f"/repos/{repo_arg}/actions/runs?branch={branch}&per_page=1")
    if not runs.get("workflow_runs"):
        die(f"no runs on branch {branch}")
    return str(runs["workflow_runs"][0]["id"])


def current_repo():
    try:
        out = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner",
                              "-q", ".nameWithOwner"],
                             capture_output=True, text=True, check=True).stdout
    except Exception:
        die("--repo is required outside a GitHub checkout")
    return out.strip()


def main():
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("run_id", nargs="?")
    p.add_argument("--repo")
    p.add_argument("--pr")
    p.add_argument("--branch")
    p.add_argument("--budget-minutes", type=float)
    p.add_argument("--json", action="store_true", dest="as_json")
    args = p.parse_args()

    repo = args.repo or current_repo()
    run_id = resolve_run(args, repo)

    jobs = all_jobs(repo, run_id)
    timing = gh_api(f"/repos/{repo}/actions/runs/{run_id}/timing")

    rows = []
    for j in jobs:
        created, started, done = ts(j.get("created_at")), ts(j.get("started_at")), ts(j.get("completed_at"))
        rows.append({
            "name": j["name"],
            "conclusion": j.get("conclusion") or j.get("status"),
            "queue_s": secs(created, started),
            "exec_s": secs(started, done),
        })

    billable = timing.get("billable", {})
    billable_min = sum(v.get("total_ms", 0) for v in billable.values()) / 60000.0

    # Public repos report zero billable ms; charge the budget against per-job
    # execution rounded up to the minute instead, and say so.
    rounded_min = sum(math.ceil((r["exec_s"] or 0) / 60.0) for r in rows)
    if billable_min > 0:
        basis, basis_min = "billable (/timing)", billable_min
    else:
        basis, basis_min = ("per-job execution rounded up, unweighted "
                            "(/timing reports 0; no runner multiplier applied — "
                            "macOS bills 10x)"), float(rounded_min)

    if args.as_json:
        print(json.dumps({"run_id": run_id, "repo": repo, "jobs": rows,
                          "billable_minutes": billable_min,
                          "rounded_execution_minutes": rounded_min,
                          "budget_basis": basis,
                          "run_duration_ms": timing.get("run_duration_ms")}, indent=2))
    else:
        print(f"run {run_id}  {repo}   {len(rows)} jobs")
        print(f"{'queue':>6} {'exec':>6}  conclusion    job")
        for r in sorted(rows, key=lambda r: -(r["exec_s"] or 0)):
            print(f"{fmt(r['queue_s'])} {fmt(r['exec_s'])}  "
                  f"{(r['conclusion'] or '?'):<12}  {r['name']}")
        q = sum(r["queue_s"] or 0 for r in rows)
        e = sum(r["exec_s"] or 0 for r in rows)
        print(f"\nqueued (sum) {fmt(q)}   executing (sum) {fmt(e)}")
        for runner, v in sorted(billable.items()):
            print(f"billable {runner:<12} {v.get('total_ms', 0) / 60000.0:8.1f} min"
                  f"  ({v.get('jobs', 0)} jobs)")
        print(f"billable total {billable_min:.1f} min   "
              f"| per-job rounded up {rounded_min} min")
        wall = timing.get("run_duration_ms")
        if wall:
            print(f"run wall clock {wall / 60000.0:.1f} min"
                  "   -- includes queue; timeout-minutes does not")
        print(f"budget basis: {basis}")

    if args.budget_minutes is not None and basis_min > args.budget_minutes:
        print(f"\nFAIL {basis_min:.1f} min > budget {args.budget_minutes:.1f} min"
              f"  [basis: {basis}]", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
