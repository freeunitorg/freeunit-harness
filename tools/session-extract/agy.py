#!/usr/bin/env python3
"""agy (Antigravity CLI) conversations -> one normalized line per conversation. Usage: agy.py [conversation id | --all].
Store: ~/.gemini/antigravity-cli/conversation_summaries.db (metadata) and conversations/<id>.db (steps; payloads are
protobuf blobs, not decoded here). No token or cost field is readable without the proto schema; title and preview are
transcript text and are not emitted."""
import json, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(__file__)); import common

ROOT = os.path.expanduser("~/.gemini/antigravity-cli")
con = sqlite3.connect(f"file:{ROOT}/conversation_summaries.db?mode=ro", uri=True)
arg = sys.argv[1] if len(sys.argv) > 1 else None
q = "select conversation_id, step_count, last_modified_time, last_user_input_time, workspace_uris, agent_name, status, killed from conversation_summaries"
rows = con.execute(q + (" where conversation_id=?" if arg and arg != "--all" else " order by last_modified_time desc" + ("" if arg == "--all" else " limit 1")), (arg,) if arg and arg != "--all" else ()).fetchall()
for cid, steps, modified, first_input, uris, agent, status, killed in rows:
    rec = common.new("agy", "antigravity-conversations", cid)
    first = (json.loads(uris) if uris else [None])[0] if uris else None   # workspace_uris is a JSON list of file:// URIs
    rec["cwd"] = common.cwd_class(first.replace("file://", "")) if first else "other"
    rec["start"], rec["end"] = common.iso(first_input), common.iso(modified)
    rec["turns"] = steps; rec["tokens"] = {"in": None, "out": None, "cache_read": None}
    if agent: rec["models"][agent] += 0
    rec["markers"]["status"] = status; rec["markers"]["killed"] = int(bool(killed))
    db = f"{ROOT}/conversations/{cid}.db"
    if os.path.exists(db):
        c2 = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for step_type, status_code, n in c2.execute("select step_type, status, count(*) from steps group by 1, 2"):
            rec["tools"][f"step_type_{step_type}_status_{status_code}"] += n
    common.emit(rec)
