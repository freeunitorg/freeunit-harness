#!/usr/bin/env python3
"""Pi session -> one normalized line. Usage: pi.py [<session>.jsonl]; default: newest.
One usage object per assistant message (no double count); usage.cost.total is Pi's own price estimate."""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(__file__)); import common

path = sys.argv[1] if len(sys.argv) > 1 else common.newest(glob.glob(os.path.expanduser("~/.pi/agent/sessions/*/*.jsonl")))
rec = common.new("pi", "pi-session", os.path.basename(path).split("_")[-1].split(".")[0])
cost = 0.0
for line in open(path, encoding="utf-8", errors="replace"):
    try: d = json.loads(line)
    except ValueError: continue
    if d.get("type") == "session": rec["cwd"] = common.cwd_class(d.get("cwd"))
    ts = d.get("timestamp")
    if ts: rec["start"] = rec["start"] or ts; rec["end"] = ts
    m = d.get("message") or {}
    if not isinstance(m, dict): continue
    if m.get("role") == "assistant":
        u = m.get("usage") or {}
        if u:
            rec["turns"] += 1; rec["tokens"]["in"] += u.get("input", 0); rec["tokens"]["out"] += u.get("output", 0)
            rec["tokens"]["cache_read"] += u.get("cacheRead", 0); cost += (u.get("cost") or {}).get("total", 0) or 0
        if m.get("model"): rec["models"][m["model"]] += 1
        for b in m.get("content") or []:
            if not isinstance(b, dict): continue
            if b.get("type") == "toolCall":
                rec["tools"][b.get("name", "?")] += 1; a = b.get("arguments") or {}
                if a.get("path"): common.note_file(rec, a["path"])
                common.scan(rec, json.dumps(a))
            elif b.get("type") == "text": common.scan(rec, b.get("text", ""))
    elif m.get("role") == "toolResult" and m.get("isError"): rec["markers"]["tool_errors"] += 1
rec["cost_usd"] = round(cost, 4)
common.emit(rec)
