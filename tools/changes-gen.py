#!/usr/bin/env python3
"""changes-gen.py — regenerate a CHANGES section for one version from docs/changes.xml.

FreeUnit's CHANGES file is generated from docs/changes.xml by an XSLT step
(docs/changes.xslt via xsltproc). xsltproc is not installed on every
contributor box -- it wasn't on this one -- so this reproduces the transform
in Python. "Reproduces" is not a claim, it is checked: `verify` regenerates a
section that already exists in CHANGES and compares byte-for-byte, so the
tool proves itself against real data before you trust it on a new entry.
ALWAYS run verify before using write on a version you haven't generated
before -- skipping it risks reflowing untouched entries into a noisy diff.

The header line ("Changes with FreeUnit X.Y.Z    DD Mon YYYY") is not copied
verbatim from CHANGES -- its date is derived from the matching <changes
ver=...> block's date="YYYY-MM-DD" attribute, so a date bump in changes.xml
reaches CHANGES on the next write instead of needing a hand edit. `verify`
checks the header too, not just the body.

Only the apply="unit" <changes> block feeds CHANGES -- changes.xml also
carries one block per language module (apply="unit-php", "unit-python...",
etc), almost always just "NGINX Unit updated to X.Y.Z". This tool ignores
those; see _blocks() below for why that filter has to be exact-match on
apply, not just "does <changes> for this ver appear more than once".

The three formatting settings, found by bisecting against the real file:

  width 76                 wrapping the "*) Label: text" body
  break_on_hyphens=False   or "use-after-free" splits across lines
  break_long_words=False   never split a token
  whitespace collapsed     changes.xml uses two spaces after a sentence,
                           CHANGES uses one

Usage:
  changes-gen.py verify <version> [--repo DIR]   prove it reproduces CHANGES
  changes-gen.py print  <version> [--repo DIR]   emit the section to stdout
  changes-gen.py write  <version> [--repo DIR]   rewrite that section in place

--repo defaults to "." — for the harness, that's UNIT_SRC (usually ./src);
run it from there or pass --repo src.
"""
import argparse, sys, textwrap, xml.etree.ElementTree as ET
from pathlib import Path

WIDTH = 76
LABELS = {"feature": "Feature", "bugfix": "Bugfix", "change": "Change"}


def _blocks(xml_text):
    """Yield (version, raw_block) for each apply="unit" <changes> element.

    changes.xml has one <changes> block per RELEASED PACKAGE per version --
    apply="unit" (the core, what CHANGES documents) plus one per language
    module (apply="unit-php", "unit-python...", etc, each usually just "NGINX
    Unit updated to X.Y.Z"). A naive `find("<changes"` match against every
    element with that tag, unfiltered by `apply`, treats EVERY version as
    ambiguous -- 52 of 54 versions in the real file have more than one
    <changes> element sharing a `ver`, and refusing to guess among them
    would make `write` fail almost always instead of almost never. Filtering
    to apply="unit" specifically resolves all but a genuine case (1.4, which
    really does have two apply="unit" blocks) -- that's the one write should
    still refuse.
    """
    pos = 0
    while True:
        start = xml_text.find("<changes", pos)
        if start == -1:
            return
        end = xml_text.index("</changes>", start) + len("</changes>")
        block = xml_text[start:end]
        root = ET.fromstring(block)
        if root.get("apply") == "unit":
            yield root.get("ver"), root
        pos = end


def _fmt_date(date_attr):
    """changes.xml's date="YYYY-MM-DD" -> CHANGES's "28 Aug 2026"."""
    import datetime
    return datetime.datetime.strptime(date_attr, "%Y-%m-%d").strftime("%d %b %Y")


def render(root):
    out = []
    for ch in root.findall("change"):
        kind = ch.get("type")
        label = LABELS.get(kind, kind.capitalize())
        text = " ".join("".join(ch.find("para").itertext()).split())
        out.append(textwrap.fill(
            f"*) {label}: {text}", width=WIDTH,
            initial_indent="    ", subsequent_indent="       ",
            break_on_hyphens=False, break_long_words=False))
    return "\n\n".join(out)


def section_bounds(changes_text, version):
    head = f"Changes with FreeUnit {version}"
    i = changes_text.find(head)
    if i == -1:
        # Not every version in changes.xml has its own CHANGES section --
        # some patch releases (npm/docker-only, no user-visible change) are
        # never written up. That's a real, expected case, not a bug: fail
        # with a message that says so instead of an unhandled ValueError
        # traceback out of str.index().
        sys.exit(f'no "{head}" section in CHANGES -- this version may never '
                 f'have gotten one (use print to see what would be generated)')
    j = changes_text.find("Changes with FreeUnit ", i + len(head))
    if j == -1:
        j = len(changes_text)
    return i, j


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["verify", "print", "write"])
    ap.add_argument("version")
    ap.add_argument("--repo", default=".")
    a = ap.parse_args()

    repo = Path(a.repo)
    xml_text = (repo / "docs/changes.xml").read_text()
    changes_text = (repo / "CHANGES").read_text()

    matches = [r for v, r in _blocks(xml_text) if v == a.version]
    if not matches:
        sys.exit(f"no <changes ver=\"{a.version}\"> block in docs/changes.xml")
    if len(matches) > 1:
        # Real case: 1.4 genuinely has two apply="unit" <changes> blocks for
        # the same ver. Picking one silently would regenerate a partial
        # section over a complete one, so refuse and make the caller say
        # which -- there is no flag to pick a block; merge or split the XML
        # first if this fires.
        sys.exit(f"{len(matches)} apply=\"unit\" <changes ver=\"{a.version}\"> "
                 f"blocks in docs/changes.xml -- ambiguous, refusing to guess")
    root = matches[0]

    generated = render(root)
    n = len(root.findall("change"))

    if a.mode == "print":
        print(generated)
        return

    i, j = section_bounds(changes_text, a.version)
    current_header = changes_text[i:j].split("\n", 1)[0]
    current_body = changes_text[i:j].split("\n", 1)[1].strip("\n")

    # The header's date comes from changes.xml's date="YYYY-MM-DD", not from
    # copying the existing CHANGES line -- that's the whole point of
    # regenerating: a date bump in the XML must reach CHANGES without a hand
    # edit. Padding width is derived from the existing header's own length
    # (not hardcoded) so a change to that convention doesn't need a matching
    # edit here.
    left = f"Changes with FreeUnit {a.version}"
    right = _fmt_date(root.get("date"))
    width = len(current_header)
    header = left + " " * (width - len(left) - len(right)) + right

    if a.mode == "verify":
        if generated.strip() == current_body.strip() and header == current_header:
            print(f"OK: regenerated {a.version} ({n} entries) matches CHANGES "
                  f"byte-for-byte")
            return
        import difflib
        d = list(difflib.unified_diff(
            [current_header] + current_body.splitlines(),
            [header] + generated.splitlines(),
            "CHANGES", "generated", lineterm="", n=1))
        print(f"MISMATCH for {a.version} ({len(d)} diff lines):")
        print("\n".join(d[:40]))
        sys.exit(1)

    (repo / "CHANGES").write_text(
        changes_text[:i] + header + "\n\n" + generated + "\n\n\n" + changes_text[j:])
    print(f"wrote {a.version} ({n} entries) into CHANGES")


if __name__ == "__main__":
    main()
