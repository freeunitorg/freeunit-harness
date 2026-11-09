"""Relative Markdown links in the model docs must resolve."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TARGETS = [ROOT / "docs" / "model", ROOT / "tools" / "model" / "README.md"]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
TEXT_SUFFIXES = {".md", ".py", ".txt"}


def _md_files():
    files = []
    for target in TARGETS:
        if target.is_file():
            files.append(target)
        else:
            files.extend(sorted(target.rglob("*.md")))
            files.extend(sorted(target.rglob("*.txt")))
    return files


def _hrefs(text):
    for raw in LINK_RE.findall(text):
        href = raw.strip().split()[0].strip("<>")
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        yield href.split("#", 1)[0]


def test_relative_markdown_links_resolve():
    missing = []
    for path in _md_files():
        text = path.read_text(encoding="utf-8")
        for href in _hrefs(text):
            if not href:
                continue
            dest = (path.parent / href).resolve()
            if not dest.exists():
                missing.append("%s -> %s" % (path.relative_to(ROOT), href))
    assert missing == [], "broken relative links:\n  " + "\n  ".join(missing)


def test_no_trailing_whitespace_in_model_tree():
    """Scan the model docs and code. CI checkouts often have no origin/main."""
    dirty = []
    roots = [ROOT / "docs" / "model", ROOT / "tools" / "model"]
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line != line.rstrip(" \t"):
                    dirty.append("%s:%d" % (path.relative_to(ROOT), i))
    assert dirty == [], "trailing whitespace:\n  " + "\n  ".join(dirty)
