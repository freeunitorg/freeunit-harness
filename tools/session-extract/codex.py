#!/usr/bin/env python3
"""Codex rollout -> one normalized line. Usage: codex.py [rollout-*.jsonl]; default: newest rollout.
Trap: token_count.total_token_usage is cumulative; take the last one, never sum. Also carries rate_limits (quota)."""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(__file__)); import common

path = sys.argv[1] if len(sys.argv) > 1 else common.newest(glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*.jsonl")))
rec = common.new("codex", "codex-rollout", os.path.basename(path).rsplit("-", 5)[-5] if "-" in path else path)
last_total, last_limits = None, None
for line in open(path, encoding="utf-8", errors="replace"):
    try: d = json.loads(line)
    except ValueError: continue
    p = d.get("payload") or {}
    ts = d.get("timestamp")
    if ts: rec["start"] = rec["start"] or ts; rec["end"] = ts
    if d.get("type") == "session_meta":
        rec["cwd"] = common.cwd_class(p.get("cwd")); rec["session"] = str(p.get("id", ""))[:12]
    elif d.get("type") == "turn_context" and p.get("model"): rec["models"][p["model"]] += 1
    elif d.get("type") == "event_msg" and p.get("type") == "token_count" and p.get("info"):
        last_total = p["info"].get("total_token_usage") or last_total; last_limits = p.get("rate_limits") or last_limits
    elif d.get("type") == "response_item" and p.get("type") in ("custom_tool_call", "function_call"):
        rec["turns"] += 1; rec["tools"][p.get("name", "?")] += 1
        common.scan(rec, p.get("input") or p.get("arguments") or "")
        try:
            a = json.loads(p.get("arguments") or "{}")
            for k in ("path", "file_path", "filePath"):
                if a.get(k): common.note_file(rec, a[k])
        except ValueError: pass
    elif d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "assistant":
        for c in p.get("content") or []:
            if isinstance(c, dict): common.scan(rec, c.get("text", ""))
if last_total:
    rec["tokens"] = {"in": last_total.get("input_tokens", 0), "out": last_total.get("output_tokens", 0),
                     "cache_read": last_total.get("cached_input_tokens", 0)}
if last_limits:
    rec["quota"] = {"plan": last_limits.get("plan_type"), "window_5h_pct": (last_limits.get("primary") or {}).get("used_percent"),
                    "weekly_pct": (last_limits.get("secondary") or {}).get("used_percent"),
                    "weekly_resets_at": common.iso((last_limits.get("secondary") or {}).get("resets_at"))}
common.emit(rec)
