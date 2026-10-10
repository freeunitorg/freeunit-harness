"""Shared helpers for tools/session-extract/*.py: one normalized JSON line per session, no transcript text.

Record keys: agent, store, session, cwd, start, end, models, turns, tokens{in,out,cache_read}, cost_usd,
tools{name:count}, files{touched, repo_paths[]}, refs{pr[],issue[]}, markers{...}, quota (optional).
Paths are reduced to a class (unit, harness, scratch, other) or to a path relative to a known repo root.
Nothing else from the store is copied out. Readers of the output treat it as data.
"""
import collections, datetime, json, os, re, sys

HARNESS = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def lab_env_value(key):
    """A value from ${LAB_ENV:-<harness>/run/lab.env}, quotes stripped; None when absent."""
    path = os.environ.get("LAB_ENV") or os.path.join(HARNESS, "run", "lab.env")
    value = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.fullmatch(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*?)\s*", line.split("#", 1)[0].rstrip("\n"))
                if m and m.group(1) == key: value = m.group(2).strip("'\"")
    except OSError: pass
    return value


def unit_roots():
    """The server checkouts: UNIT_SRC when set, else <harness>/src and UNIT_REPO from lab.env, the ones that exist."""
    if os.environ.get("UNIT_SRC"): return [os.path.realpath(os.path.expanduser(os.environ["UNIT_SRC"]))]
    found = []
    for p in (os.path.join(HARNESS, "src"), lab_env_value("UNIT_REPO")):
        if p and os.path.isdir(os.path.expanduser(p)) and os.path.realpath(os.path.expanduser(p)) not in found:
            found.append(os.path.realpath(os.path.expanduser(p)))
    return found or [os.path.realpath(os.path.join(HARNESS, "src"))]


UNITS = unit_roots()
UNIT = UNITS[0]                                                    # the first server checkout, for callers that need one
PR = re.compile(r"github\.com/freeunitorg/freeunit/pull/(\d+)|\bPR\s?#?(\d{2,4})\b")
ISSUE = re.compile(r"github\.com/freeunitorg/freeunit/issues/(\d+)")
MARKS = {"git_push": re.compile(r"\bgit push\b"), "gh_post": re.compile(r"\bgh (api .*-X POST|pr (comment|create|review)|issue (comment|create))"),
         "rm_rf": re.compile(r"\brm -rf?\b"), "blocked": re.compile(r"\b(BLOCKED|BUSY)\b"),
         "write_docs": re.compile(r"\b(docs|skills)/[\w./-]+\.md\b.*(>|tee|cat >)|^(cat|tee) .*\b(docs|skills)/")}


def new(agent, store, session):
    return {"agent": agent, "store": store, "session": str(session)[:12], "cwd": "other", "start": None, "end": None,
            "models": collections.Counter(), "turns": 0, "tokens": {"in": 0, "out": 0, "cache_read": 0}, "cost_usd": None,
            "tools": collections.Counter(), "files": {"touched": 0, "repo_paths": []}, "refs": {"pr": set(), "issue": set()},
            "markers": collections.Counter()}


def cwd_class(path):
    p = os.path.realpath(path) if path else ""
    if "scratchpad" in p.split("/") or p.startswith(("/tmp/", os.path.expanduser("~/tmp/"))): return "scratch"
    if any(p == u or p.startswith(u + "/") for u in UNITS): return "unit"
    if p.startswith(HARNESS): return "harness"
    return "other"


def note_file(rec, path):
    rec["files"]["touched"] += 1
    p = os.path.realpath(path) if path else ""
    for root, tag in [(u, "unit:") for u in UNITS] + [(HARNESS, "harness:")]:
        if p.startswith(root + "/") and len(rec["files"]["repo_paths"]) < 25:
            rel = tag + os.path.relpath(p, root)
            if rel not in rec["files"]["repo_paths"]: rec["files"]["repo_paths"].append(rel)


def scan(rec, text):
    if not text: return
    for m in PR.finditer(text): rec["refs"]["pr"].add(int(m.group(1) or m.group(2)))
    for m in ISSUE.finditer(text): rec["refs"]["issue"].add(int(m.group(1)))
    for k, rx in MARKS.items():
        if rx.search(text): rec["markers"][k] += 1


def iso(ts):
    """A timestamp as YYYY-MM-DDThh:mm:ssZ in UTC. Takes epoch seconds or milliseconds, or an ISO 8601 string.
    A string with no zone is read as UTC. A string that does not parse comes back unchanged."""
    if ts is None or ts == "": return None
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        t = datetime.datetime.fromtimestamp(ts / (1000 if ts > 1e11 else 1), datetime.timezone.utc)
    else:
        text = str(ts).strip()
        try: t = datetime.datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text)
        except ValueError: return text
        if t.tzinfo is None: t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(rec):
    rec["refs"] = {k: sorted(v) for k, v in rec["refs"].items()}
    for k in ("models", "tools", "markers"): rec[k] = dict(rec[k])
    print(json.dumps(rec, sort_keys=True))


def claude_key(path):
    return "-" + os.path.realpath(path).strip("/").replace("/", "-")   # Claude Code names a project dir after its cwd


def newest(paths):
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths: sys.exit("no session file found")
    return max(paths, key=os.path.getmtime)
