#!/usr/bin/env python3
"""dsh (DeepSeek Harness) session -> one normalized line. Usage: dsh.py [session.v4.jsonl.zstd]; default: newest.
Trap: an assistant/message line holds usage twice (data.usage and a copy in data.stream[].chunk.usage); read data.usage only."""
import glob, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(__file__)); import common

path = sys.argv[1] if len(sys.argv) > 1 else common.newest(glob.glob(os.path.expanduser("~/.dsh/sessions/*/*/session.v4.jsonl.zstd")))
rec = common.new("dsh", "dsh-session", os.path.basename(os.path.dirname(path)).replace("session-", ""))
text = subprocess.run(["zstd", "-dc", path], capture_output=True, text=True, check=True).stdout
for line in text.splitlines():
    try: d = json.loads(line)
    except ValueError: continue
    data = d.get("data") or {}
    if d.get("type") == "session":
        rec["cwd"] = common.cwd_class(d.get("cwd")); rec["start"] = common.iso(d.get("createdAt"))
    if d.get("time"): rec["end"] = common.iso(d["time"])
    if d.get("type") == "assistant/message":
        rec["turns"] += 1; u = data.get("usage") or {}
        rec["tokens"]["in"] += u.get("inputTokens", 0); rec["tokens"]["out"] += u.get("outputTokens", 0)
        rec["tokens"]["cache_read"] += u.get("cacheReadTokens", 0)
        msg = data.get("message") or {}
        src = msg.get("source") or {}
        if src.get("model"): rec["models"][f"{src.get('provider')}/{src['model']}"] += 1
        for b in msg.get("content") or []:
            if b.get("type") == "text": common.scan(rec, b.get("text", ""))
    elif d.get("type") == "tool/call":
        rec["tools"][data.get("name", "?")] += 1; args = data.get("arguments") or ""
        common.scan(rec, args)
        try:
            a = json.loads(args)
            for k in ("path", "file_path", "filePath"):
                if a.get(k): common.note_file(rec, a[k])
        except ValueError: pass
    elif d.get("type") == "tool/result" and (data.get("message") or {}).get("isError"):
        rec["markers"]["tool_errors"] += 1
common.emit(rec)
