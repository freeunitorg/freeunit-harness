#!/usr/bin/env python3
"""OpenCode sqlite -> one normalized line per session. Usage: opencode.py [session id | --all]; default: newest.
Tokens and cost come from the session row; they equal the sum over assistant messages (checked 2026-10-07), so read the row."""
import json, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(__file__)); import common

DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
arg = sys.argv[1] if len(sys.argv) > 1 else None
ids = [r[0] for r in con.execute("select id from session order by time_created desc" + ("" if arg == "--all" else " limit 1"))] if arg in (None, "--all") else [arg]
for sid in ids:
    row = con.execute("select directory, time_created, time_updated, model, agent, cost, tokens_input, tokens_output, tokens_cache_read, summary_files from session where id=?", (sid,)).fetchone()
    if not row: sys.exit(f"no session {sid}")
    rec = common.new("opencode", "opencode-db", sid)
    rec["cwd"] = common.cwd_class(row[0]); rec["start"], rec["end"] = common.iso(row[1]), common.iso(row[2])
    try: m = json.loads(row[3] or "{}"); rec["models"][f"{m.get('providerID')}/{m.get('id')}"] += 0
    except ValueError: pass
    rec["cost_usd"] = round(row[5] or 0, 4); rec["tokens"] = {"in": row[6], "out": row[7], "cache_read": row[8]}
    for (data,) in con.execute("select data from message where session_id=? and json_extract(data,'$.role')='assistant'", (sid,)):
        d = json.loads(data); rec["turns"] += 1; rec["models"][f"{d.get('providerID')}/{d.get('modelID')}"] += 1
    for (data,) in con.execute("select data from part where session_id=?", (sid,)):
        d = json.loads(data)
        if d.get("type") == "tool":
            st = d.get("state") or {}; rec["tools"][d.get("tool", "?")] += 1; inp = st.get("input") or {}
            if inp.get("filePath"): common.note_file(rec, inp["filePath"])
            common.scan(rec, json.dumps(inp))
            if st.get("status") == "error": rec["markers"]["tool_errors"] += 1
        elif d.get("type") == "text": common.scan(rec, d.get("text", ""))
    common.emit(rec)
