#!/usr/bin/env python3
"""Strip personality, check evidence, gate promotion. Used by Strace/Claude curators.
Usage: tools/clean-findings.py   (no arguments). Checks every run/agent-findings/*.md file.
Exit 0 when every file passes, 1 when one fails, 2 on a usage error or when the directory does not exist."""
import pathlib, re, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent / "run/agent-findings"
if sys.argv[1:] in (["-h"], ["--help"]):
    print(__doc__); sys.exit(0)
if sys.argv[1:]:
    print(f"clean-findings: takes no arguments, got {' '.join(sys.argv[1:])}", file=sys.stderr); sys.exit(2)
if not ROOT.is_dir():
    print(f"clean-findings: no directory {ROOT}; nothing to check", file=sys.stderr); sys.exit(2)
SKIP = {"README.md", "EVOLUTION-CYCLE.md"}
BAD = [re.compile(p, re.I) for p in [r"as an ai", r"i think", r"maybe", r"😀|😃|😄|🎉|✨", r"hope this helps"]]
REQ = ["Source:", "Claim:", "Evidence:", "Proposed doc/harness update:"]
ok=True
for f in ROOT.glob("*.md"):
    if f.name in SKIP: continue
    # also skip kb-pending subdir handled separately
    if "kb-pending" in str(f): continue
    t=f.read_text(errors="ignore")
    miss=[r for r in REQ if r not in t]
    pers=[p.pattern for p in BAD if p.search(t)]
    no_ev = "file:" not in t and "rg " not in t and "gh " not in t and "strace" not in t
    if miss or pers or no_ev:
        print(f"{f.name}: missing {miss} personality {pers} no_ev={no_ev}")
        ok=False
    else:
        print(f"{f.name}: ok")
for f in (ROOT/"kb-pending").glob("*.md"):
    # kb-pending are proposals, not findings — just check no personality
    t=f.read_text(errors="ignore")
    pers=[p.pattern for p in BAD if p.search(t)]
    if pers:
        print(f"kb-pending/{f.name}: personality {pers}")
        ok=False
    else:
        print(f"kb-pending/{f.name}: ok")
sys.exit(0 if ok else 1)
