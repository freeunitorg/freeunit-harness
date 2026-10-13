#!/usr/bin/env python3
"""Check the model trailers of a commit or a comment against tools/reviewers.txt.

Usage:
    check-trailers.py <commit>          # git commit message (any revision git understands)
    check-trailers.py -f <file>         # a file: a commit message or a review comment
    check-trailers.py -                 # the message on stdin

Rules (AGENTS.md, "Attribution: the trailer pair"):
- Co-Authored-By and Reviewed-by name a model from tools/reviewers.txt, spelled as its
  canonical name. An alias is an error; the message names the canonical spelling.
- The model may be followed by the harness that ran it, in parentheses:
  "Reviewed-by: DeepSeek V4.1 Flash (DSH)".  A Claude address goes after it.
- Claude models carry " <noreply@anthropic.com>"; other models carry no address.
- Trailers sit in the last paragraph, one per line, with no blank line between them.
- Claude-Session trailers are not allowed.
Exit 0 when clean, 1 on a violation, 2 on usage or a missing list.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIST = os.path.join(HERE, "reviewers.txt")
ADDR = "<noreply@anthropic.com>"
KEYS = ("co-authored-by", "reviewed-by")


def load(path=LIST):
    canon, alias = {}, {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                sys.exit("%s:%d: expected name TAB addr|- [TAB aliases]" % (path, n))
            name, addr = parts[0].strip(), parts[1].strip() == "addr"
            canon[name.lower()] = (name, addr)
            for a in (parts[2].split(",") if len(parts) > 2 else []):
                a = a.strip()
                if a:
                    alias[a.lower()] = name
    return canon, alias


def check(text, canon, alias):
    """Return a list of problems in a commit message or comment body."""
    problems = []
    lines = text.rstrip("\n").split("\n")
    found = []
    for i, line in enumerate(lines, 1):
        m = re.match(r"^([A-Za-z-]+):\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key.lower() == "claude-session":
            problems.append("line %d: Claude-Session trailers are not allowed" % i)
            continue
        if key.lower() not in KEYS:
            continue
        found.append(i)
        if key not in ("Co-Authored-By", "Reviewed-by"):
            problems.append("line %d: spell the key %s" % (i, "Co-Authored-By" if key.lower() == KEYS[0] else "Reviewed-by"))
        has_addr = value.endswith(ADDR)
        name = value[: -len(ADDR)].strip() if has_addr else value
        if re.search(r"<[^>]*>$", name):
            problems.append("line %d: unexpected address %r; only %s is used" % (i, name, ADDR))
            continue
        # A whole value in the list wins, so an alias that ends in parentheses
        # is still reported as an alias; otherwise drop a "(harness)" suffix.
        if name.lower() not in canon and name.lower() not in alias:
            h = re.match(r"^(.*\S)\s+\([^()]+\)$", name)
            if h:
                name = h.group(1)
        if name.lower() in canon:
            cname, want_addr = canon[name.lower()]
            if cname != name:
                problems.append("line %d: %r should be spelled %r" % (i, name, cname))
            if want_addr and not has_addr:
                problems.append("line %d: %r needs ' %s'" % (i, cname, ADDR))
            if not want_addr and has_addr:
                problems.append("line %d: %r takes no address" % (i, cname))
        elif name.lower() in alias:
            problems.append("line %d: %r is an alias; use %r" % (i, name, alias[name.lower()]))
        else:
            problems.append("line %d: unknown model %r; add it to tools/reviewers.txt" % (i, name))
    # Trailers in one block at the end: no blank line between the first trailer and the last line.
    if found:
        tail = lines[found[0] - 1:]
        for j, line in enumerate(tail, found[0]):
            if not line.strip():
                problems.append("line %d: blank line inside the trailer block" % j)
                break
    return problems


def main(argv):
    if len(argv) != 2 and not (len(argv) == 3 and argv[1] == "-f"):
        sys.stderr.write(__doc__)
        return 2
    if argv[1] == "-f":
        text = open(argv[2], encoding="utf-8").read()
    elif argv[1] == "-":
        text = sys.stdin.read()
    else:
        try:
            text = subprocess.check_output(["git", "log", "-1", "--format=%B", argv[1]], text=True)
        except subprocess.CalledProcessError:
            return 2
    canon, alias = load()
    problems = check(text, canon, alias)
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
