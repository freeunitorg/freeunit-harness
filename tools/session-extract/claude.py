#!/usr/bin/env python3
"""Claude Code transcript -> one normalized line. Usage: claude.py [<sid>.jsonl]; default: the newest
transcript of a server checkout (UNIT_SRC, else <harness>/src and UNIT_REPO from ${LAB_ENV:-run/lab.env}).
Trap: a usage object is written once per content block of one message; dedupe by message.id or tokens double."""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(__file__)); import common

if len(sys.argv) > 1: path = sys.argv[1]
else:                                                              # every server checkout: UNIT_SRC, else harness src and UNIT_REPO
    projects = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")
    path = common.newest([p for u in common.UNITS for p in glob.glob(os.path.join(projects, common.claude_key(u), "*.jsonl"))])
rec = common.new("claude", "claude-code", os.path.basename(path).split(".")[0])
usage_by_id, errors = {}, 0
for line in open(path, encoding="utf-8", errors="replace"):
    try: d = json.loads(line)
    except ValueError: continue
    if d.get("cwd") and rec["cwd"] == "other": rec["cwd"] = common.cwd_class(d["cwd"])
    ts = d.get("timestamp")
    if ts: rec["start"] = rec["start"] or common.iso(ts); rec["end"] = common.iso(ts)
    m = d.get("message") or {}
    if not isinstance(m, dict): continue
    if d.get("type") == "assistant":
        if isinstance(m.get("usage"), dict): usage_by_id[m.get("id") or d.get("uuid")] = m["usage"]
        if m.get("model"): rec["models"][m["model"]] += 1
        for b in m.get("content") or []:
            if not isinstance(b, dict): continue
            if b.get("type") == "tool_use":
                rec["tools"][b.get("name", "?")] += 1; inp = b.get("input") or {}
                if inp.get("file_path"): common.note_file(rec, inp["file_path"])
                common.scan(rec, json.dumps(inp))
            elif b.get("type") == "text": common.scan(rec, b.get("text", ""))
    elif d.get("type") == "user":
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"): errors += 1
rec["turns"] = len(usage_by_id)
for u in usage_by_id.values():
    rec["tokens"]["in"] += u.get("input_tokens", 0); rec["tokens"]["out"] += u.get("output_tokens", 0)
    rec["tokens"]["cache_read"] += u.get("cache_read_input_tokens", 0)
rec["markers"]["tool_errors"] = errors
common.emit(rec)
