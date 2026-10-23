#!/usr/bin/env python3
"""Check the model trailers of a commit or a comment against tools/reviewers.txt.

Usage:
    check-trailers.py <commit>          # git commit message (any revision git understands)
    check-trailers.py -f <file>         # a file: a commit message
    check-trailers.py -c <file>         # a file: a GitHub comment (no address)
    check-trailers.py -                 # the message on stdin
    check-trailers.py -c -              # a comment on stdin

Rules (AGENTS.md, "Conventions here", the Trailers item):
- Co-Authored-By and Reviewed-by name a model from tools/reviewers.txt, spelled as its
  canonical name. An alias is an error; the message names the canonical spelling.
  Only the model name is checked against the list.
- Ask for the harness that ran the model. Write it only when the answer is complete
  (name and version), in parentheses after the model:
  "Reviewed-by: DeepSeek V4.1 Flash (dsh 0.2.1-rc.2)". Any string is accepted there,
  nested parentheses too, so a custom harness passes. When the harness is not disclosed,
  leave it out or write "()". The model is the longest canonical name or alias that the
  value starts with; everything in the parentheses after it is the harness.
  A Claude address goes after it.
- In a commit, Claude models carry " <noreply@anthropic.com>" or end with "(claude <version>)",
  for example "Co-Authored-By: Claude Opus 5.5 (claude 2.1.302)"; other models carry no address.
- In a comment (-c), no model carries an address: no "<...>", no email address
  and no "@" in the model name. Another "@" in the harness is accepted.
- Trailers sit in the last paragraph, one per line, with no blank line between them
  and no text after them.
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
# "(claude 2.1.302)": the Claude Code version that ran the model; stands in for the address.
CLAUDE_VER = re.compile(r"\(claude \d+\.\d+\.\d+\)$")
KEYS = ("co-authored-by", "reviewed-by")
# An email address: a plain or quoted local part, "@", then a domain with a dot whose
# last label starts with a letter, or an address literal such as "[192.0.2.1]".
# Unicode counts on both sides. "agent@lab", "x@y" and "dsh@0.2.1" are not addresses.
_LOCAL = r'(?:"[^"\n]*"|[^\s()<>@,;:"\[\]]+)'
_LABEL = r'[^\s()<>@,;:"\[\].]+'
EMAIL = re.compile(_LOCAL + r"@(?:(?:" + _LABEL + r"\.)+[^\W\d_]" + _LABEL
                   + r"|\[(?:IPv6:)?[0-9A-Fa-f:.]+\])")
# The whitespace between the model and its harness: a tab and a no-break space count.
HARNESS_OPEN = re.compile(r"\s+\(")


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


def split_model(name, canon, alias):
    """Split "<model> (<harness>)" into the model and the harness (None if there is none).

    A whole value in the list wins, so an alias that ends in parentheses is still an
    alias. Otherwise the model is the longest canonical name or alias that the value
    starts with, followed by whitespace and "(". The whitespace may be a tab or a
    no-break space. The harness is everything after that "(" up to the final ")": any
    string, empty included. A value that starts with no listed name splits at its
    first whitespace and "(", so the error names the unknown model.
    """
    low = name.lower()
    if low in canon or low in alias:
        return name, None
    if name.endswith(")"):
        for k in sorted(list(canon) + list(alias), key=len, reverse=True):
            m = HARNESS_OPEN.match(name, len(k)) if low.startswith(k) else None
            if m:
                return name[: len(k)], name[m.end(): -1]
        m = HARNESS_OPEN.search(name)
        if m and m.start() > 0:
            return name[: m.start()], name[m.end(): -1]
    return name, None


def check(text, canon, alias, comment=False):
    """Return a list of problems in a commit message or comment body."""
    problems = []
    lines = text.rstrip().split("\n")
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
        if comment:
            # The whole value is searched, so "(<noreply@anthropic.com>)" is caught too.
            model = split_model(value, canon, alias)[0]
            if re.search(r"<[^>]*>", value) or EMAIL.search(value) or "@" in model:
                problems.append("line %d: a comment takes no address" % i)
                continue
        if key not in ("Co-Authored-By", "Reviewed-by"):
            problems.append("line %d: spell the key %s" % (i, "Co-Authored-By" if key.lower() == KEYS[0] else "Reviewed-by"))
        has_addr = value.endswith(ADDR)
        name = value[: -len(ADDR)].strip() if has_addr else value
        if re.search(r"<[^>]*>$", name):
            problems.append("line %d: unexpected address %r; only %s is used" % (i, name, ADDR))
            continue
        name = split_model(name, canon, alias)[0]
        if name.lower() in canon:
            cname, want_addr = canon[name.lower()]
            if cname != name:
                problems.append("line %d: %r should be spelled %r" % (i, name, cname))
            if want_addr and not has_addr and not comment and not CLAUDE_VER.search(value):
                problems.append("line %d: %r needs ' %s' or a '(claude <version>)' suffix" % (i, cname, ADDR))
            elif not want_addr and has_addr:
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
                if any(k > j for k in found):
                    problems.append("line %d: blank line inside the trailer block" % j)
                else:
                    k = next(k for k in range(j + 1, len(lines) + 1) if lines[k - 1].strip())
                    problems.append("line %d: text after the trailer block; trailers go last" % k)
                break
    return problems


def main(argv):
    if len(argv) == 3 and argv[1] in ("-f", "-c"):
        src = argv[2]
    elif len(argv) == 2 and argv[1] not in ("-f", "-c"):
        src = argv[1]
    else:
        sys.stderr.write(__doc__)
        return 2
    try:
        if src == "-":
            text = sys.stdin.read()
        elif len(argv) == 3:
            with open(src, encoding="utf-8") as f:
                text = f.read()
        else:
            try:
                text = subprocess.check_output(["git", "log", "-1", "--format=%B", src], text=True)
            except subprocess.CalledProcessError:
                return 2
        canon, alias = load()
    except OSError as e:
        sys.stderr.write("check-trailers.py: %s\n" % e)
        return 2
    problems = check(text, canon, alias, argv[1] == "-c")
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
