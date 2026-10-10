#!/usr/bin/env python3
"""
build-pack.py: build a "C-source map" of FreeUnit's router, libunit and
port IPC code as one text file, for loading whole into a long-context model.

Usage (run from anywhere; needs only git, python3 and tree-sitter-c):

    uv run --with tree-sitter --with tree-sitter-c \\
        build-pack.py [--repo DIR] [--rev REV] --level {0,1,2} > pack.txt
    uv run --with tree-sitter --with tree-sitter-c \\
        build-pack.py [--repo DIR] [--rev REV] --stats     # measurements
    uv run --with tree-sitter --with tree-sitter-c \\
        build-pack.py [--repo DIR] [--rev REV] --index     # function index

Sources are read with `git show REV:src/<file>`; the working tree is never
read or changed. REV defaults to origin/master.

Levels:
  0  skeleton. Every type, macro, global and table in full. Every function
     keeps its signature; its body becomes one stub line with the original
     line range and the functions it calls that are defined in the set.
  1  hot path. Level 0, plus full bodies of the request-path functions
     (HOT_PATTERNS below, plus their direct callees that are defined in the
     same file and are at most HOT_CALLEE_MAX_LINES long).
  2  full. Every file whole.

All levels drop: the license comment at the top of each file, debug-level
log statements (DEBUG_FNS), and runs of blank lines (kept as one).
Levels 0 and 1 also drop forward declarations of functions defined later
in the same file (the definition carries the same signature).

Line numbers: a right-margin marker "//@N" gives the original line number
N. It is placed at each function's name line, at the first line after any
dropped lines, and at least every 10 lines. A body stub reads
"{ ... }  //@A-B calls: f, g" for original lines A to B.

The shim that makes tree-sitter-c parse FreeUnit's loop and qualifier
macros comes from tools/fidx (branch proto/factory of
the maintainer's fork of FreeUnit; tools/fidx is not public yet). It replaces text with same-length text,
so all byte offsets and line numbers stay those of the original file.
"""

import argparse
import fnmatch
import re
import subprocess
import sys

import tree_sitter as ts
import tree_sitter_c as tsc


# Headers first (types before code), then port IPC, router, libunit.
FILES = [
    "src/nxt_port.h",
    "src/nxt_port_memory.h",
    "src/nxt_port_memory_int.h",
    "src/nxt_port_queue.h",
    "src/nxt_app_queue.h",
    "src/nxt_port_hash.h",
    "src/nxt_port_rpc.h",
    "src/nxt_application.h",
    "src/nxt_router.h",
    "src/nxt_router_request.h",
    "src/nxt_unit_typedefs.h",
    "src/nxt_unit.h",
    "src/nxt_unit_sptr.h",
    "src/nxt_unit_field.h",
    "src/nxt_unit_request.h",
    "src/nxt_unit_response.h",
    "src/nxt_unit_websocket.h",
    "src/nxt_port.c",
    "src/nxt_port_socket.c",
    "src/nxt_port_memory.c",
    "src/nxt_port_rpc.c",
    "src/nxt_port_hash.c",
    "src/nxt_router.c",
    "src/nxt_unit.c",
]

# Debug-level log calls. Their whole statement is dropped when it sits on
# lines of its own. Alert, warn and error logs stay: they name failure paths.
DEBUG_FNS = {
    "nxt_debug", "nxt_unit_debug", "nxt_unit_req_debug",
    "nxt_thread_log_debug", "nxt_log_debug",
}

# Request path: router accept to app and back, port IPC, libunit loop.
HOT_PATTERNS = [
    "nxt_router_app_*", "nxt_router_prepare_msg", "nxt_router_response_*",
    "nxt_router_req_headers_ack_handler", "nxt_router_http_request_*",
    "nxt_port_socket_write*", "nxt_port_write_*", "nxt_port_read_*",
    "nxt_port_queue_*", "nxt_port_mmap_*", "nxt_port_new_port_mmap",
    "nxt_port_get_port_incoming_mmap", "nxt_port_error_handler",
    "nxt_port_handler", "nxt_port_*_handler", "nxt_app_queue_*",
    "nxt_unit_process_*", "nxt_unit_request_*", "nxt_unit_response_*",
    "nxt_unit_run*", "nxt_unit_port_*", "nxt_unit_read_buf*",
    "nxt_unit_app_queue_recv", "nxt_unit_ctx_port_recv",
    "nxt_unit_send_*", "nxt_unit_sendmsg", "nxt_unit_mmap_*",
    "nxt_unit_buf_*", "nxt_unit_incoming_mmap", "nxt_unit_get_outgoing_buf",
    "nxt_unit_mmap_buf_*",
]
HOT_CALLEE_MAX_LINES = 80

# fidx shim directives (tools/fidx/shim.h on proto/factory).
# nxt_packed is not in fidx's shim.h; it breaks two structs in nxt_unit.c.
SHIM_BLANK = ["nxt_inline", "nxt_noinline", "nxt_cdecl", "nxt_aligned",
              "nxt_packed"]
SHIM_LOOP = [
    ("nxt_queue_each", "nxt_queue_loop"),
    ("nxt_list_each", "nxt_list_loop"),
    ("nxt_http_fields_each", "nxt_http_fields_loop"),
    ("nxt_process_port_each", "nxt_process_port_loop"),
    ("nxt_runtime_process_each", "nxt_runtime_process_loop"),
]

# Calibrated on 2026-10-06 against the token counts the Read tool reports
# for a Sonnet agent: L0 1.99, L1 2.24, L2 2.25 characters per token at
# 47d2f933. The common 3.3 rule under-counts this code by about a third.
CHARS_PER_TOKEN = 2.2


def tokens(nchars):
    return int(round(nchars / CHARS_PER_TOKEN))


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), check=True,
                          capture_output=True).stdout


def _balanced_end(text, i):
    depth = 0
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def apply_shim(src):
    text = src.decode("latin-1")
    buf = bytearray(src)

    def blank(s, e):
        for i in range(s, e):
            if buf[i] != 0x0A:
                buf[i] = 0x20

    for open_name, _ in SHIM_LOOP:
        for m in re.finditer(r"\b" + open_name + r"\s*\(", text):
            end = _balanced_end(text, m.end() - 1)
            stub = "for (;;)"[: end - m.start()]
            buf[m.start():m.start() + len(stub)] = stub.encode()
            blank(m.start() + len(stub), end)

    for name in SHIM_BLANK + [c for _, c in SHIM_LOOP]:
        for m in re.finditer(r"\b" + name + r"\b", text):
            end = m.end()
            if end < len(text) and text[end] == "(":
                end = _balanced_end(text, end)
            blank(m.start(), end)

    return bytes(buf)


class Func:
    def __init__(self, name, node, name_line):
        self.name = name
        self.start = node.start_point[0] + 1
        self.end = node.end_point[0] + 1
        self.name_line = name_line
        body = node.child_by_field_name("body")
        self.body_start = body.start_point[0] + 1
        self.body_col = body.start_point[1]
        self.body_end = body.end_point[0] + 1
        self.body = body
        self.calls = []
        self.has_error = False


def declarator_name(decl):
    """Return (name, identifier node) of a function declarator, or None."""
    node = decl
    while node is not None and node.type != "function_declarator":
        if node.type in ("pointer_declarator", "parenthesized_declarator",
                         "attributed_declarator"):
            kids = node.child_by_field_name("declarator")
            node = kids if kids is not None else (
                node.named_children[0] if node.named_children else None)
        else:
            return None
    if node is None:
        return None
    ident = node.child_by_field_name("declarator")
    while ident is not None and ident.type == "parenthesized_declarator":
        ident = ident.named_children[0] if ident.named_children else None
    if ident is None or ident.type != "identifier":
        return None
    return ident.text.decode(), ident


def has_error_node(node):
    if node.type == "ERROR":
        return True
    return any(has_error_node(c) for c in node.children if c.has_error)


class SrcFile:
    def __init__(self, path, src):
        self.path = path
        self.src = src
        self.lines = src.decode("utf-8", "replace").split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()
        parser = ts.Parser(ts.Language(tsc.language()))
        self.tree = parser.parse(apply_shim(src))
        self.funcs = []
        self.fwd_decls = []          # (start_line, end_line, name)
        self.debug_stmts = []        # (start_line, end_line, fn, sbyte, ebyte)
        self.asserts = []
        self.top_comments = []       # (start_line, end_line, text)
        self.comment_lines = set()   # lines with comment text
        self.code_lines = set()      # lines with non-comment text
        self._walk(self.tree.root_node, top=True)
        self._classify_lines()

    def _walk(self, node, top):
        t = node.type
        if t == "function_definition":
            r = declarator_name(node.child_by_field_name("declarator"))
            if r and node.child_by_field_name("body") is not None:
                f = Func(r[0], node, r[1].start_point[0] + 1)
                # A label just before "}" (C23 style) gives a MISSING node
                # only; count real ERROR nodes.
                f.has_error = has_error_node(node)
                self.funcs.append(f)
        elif t == "declaration" and top:
            d = node.child_by_field_name("declarator")
            r = declarator_name(d) if d is not None else None
            if r:
                self.fwd_decls.append((node.start_point[0] + 1,
                                       node.end_point[0] + 1, r[0]))
        elif t == "comment" and top:
            self.top_comments.append((node.start_point[0] + 1,
                                      node.end_point[0] + 1,
                                      node.text.decode("utf-8", "replace")))
        elif t == "expression_statement" and node.named_children:
            c = node.named_children[0]
            if c.type == "call_expression":
                fn = c.child_by_field_name("function")
                if fn is not None and fn.type == "identifier":
                    name = fn.text.decode()
                    rec = (node.start_point[0] + 1, node.end_point[0] + 1,
                           name, node.start_byte, node.end_byte)
                    if name in DEBUG_FNS:
                        self.debug_stmts.append(rec)
                    elif name == "nxt_assert":
                        self.asserts.append(rec)
        sub_top = top and t in ("translation_unit", "preproc_if",
                                "preproc_ifdef", "preproc_else",
                                "preproc_elif", "linkage_specification",
                                "declaration_list")
        for ch in node.children:
            self._walk(ch, sub_top)

    def _classify_lines(self):
        line_starts = [0]
        for i, b in enumerate(self.src):
            if b == 0x0A:
                line_starts.append(i + 1)
        comments = []

        def walk(n):
            if n.type == "comment":
                comments.append((n.start_byte, n.end_byte))
                return
            for ch in n.children:
                walk(ch)

        walk(self.tree.root_node)
        mask = bytearray(len(self.src))
        for s, e in comments:
            mask[s:e] = b"\x01" * (e - s)
        ln = 0
        for i, b in enumerate(self.src):
            while ln + 1 < len(line_starts) and i >= line_starts[ln + 1]:
                ln += 1
            if b in (0x20, 0x09, 0x0A, 0x0D):
                continue
            if mask[i]:
                self.comment_lines.add(ln + 1)
            else:
                self.code_lines.add(ln + 1)

    def license_lines(self):
        """The first comment block if it holds 'Copyright'."""
        i = 0
        while i < len(self.lines) and not self.lines[i].strip():
            i += 1
        if i < len(self.lines) and self.lines[i].startswith("/*"):
            j = i
            while j < len(self.lines) and "*/" not in self.lines[j]:
                j += 1
            block = "\n".join(self.lines[i:j + 1])
            if "Copyright" in block:
                return set(range(1, j + 2))
        return set()

    def tests_blocks(self):
        """(start, end) of each #if (NXT_TESTS...) ... #endif block.

        No such block in the target files has an #else branch; one that
        has is kept whole, so production code is never dropped.
        """
        out = []
        depth = 0
        has_else = False
        for n, line in enumerate(self.lines, 1):
            s = line.strip()
            if depth == 0:
                if re.match(r"#\s*if\s*\(?\s*NXT_TESTS\b", s):
                    depth, start, has_else = 1, n, False
                continue
            if re.match(r"#\s*if", s):
                depth += 1
            elif depth == 1 and re.match(r"#\s*(else|elif)", s):
                has_else = True
            elif re.match(r"#\s*endif", s):
                depth -= 1
                if depth == 0 and not has_else:
                    out.append((start, n))
        return out

    def tests_block_lines(self):
        out = set()
        for a, b in self.tests_blocks():
            out.update(range(a, b + 1))
        return out

    def stmt_lines(self, recs):
        """Lines wholly taken by the given statements."""
        out = set()
        for sl, el, _, sb, eb in recs:
            ls = self.src.rfind(b"\n", 0, sb) + 1
            le = self.src.find(b"\n", eb)
            le = len(self.src) if le < 0 else le
            if (self.src[ls:sb].strip() == b""
                    and self.src[eb:le].strip() == b""):
                out.update(range(sl, el + 1))
        return out


def load(repo, rev):
    sha = git(repo, "rev-parse", "--short=8", rev + "^{commit}").decode().strip()
    files = []
    for p in FILES:
        try:
            src = git(repo, "show", "%s:%s" % (rev, p))
        except subprocess.CalledProcessError:
            print("build-pack: %s missing at %s, skipped" % (p, rev),
                  file=sys.stderr)
            continue
        files.append(SrcFile(p, src))
    return sha, files


def check_coverage(files):
    """Every column-0 'name(' definition line must be a parsed function."""
    bad = 0
    for f in files:
        found = {fn.name_line for fn in f.funcs}
        for n, line in enumerate(f.lines, 1):
            m = re.match(r"^([a-z_][a-z0-9_]*)\(", line)
            if not m or n in found:
                continue
            nxt = f.lines[n:n + 40]
            # A definition has "{" at column 0 before any ";" line ends it.
            for l2 in [line] + nxt:
                if l2.startswith("{"):
                    print("build-pack: %s:%d %s not parsed as a function"
                          % (f.path, n, m.group(1)), file=sys.stderr)
                    bad += 1
                    break
                if l2.rstrip().endswith(";"):
                    break
        for fn in f.funcs:
            if fn.has_error:
                print("build-pack: %s:%d %s has parse errors"
                      % (f.path, fn.name_line, fn.name), file=sys.stderr)
    return bad


def callgraph(files):
    defined = {}
    for f in files:
        for fn in f.funcs:
            defined.setdefault(fn.name, (f, fn))

    def calls_in(node, out):
        if node.type == "call_expression":
            c = node.child_by_field_name("function")
            if c is not None and c.type == "identifier":
                name = c.text.decode()
                if name in defined and name not in out:
                    out.append(name)
        for ch in node.children:
            calls_in(ch, out)

    for f in files:
        for fn in f.funcs:
            calls_in(fn.body, fn.calls)
    return defined


def hot_set(files):
    hot = set()
    for f in files:
        for fn in f.funcs:
            if any(fnmatch.fnmatchcase(fn.name, p) for p in HOT_PATTERNS):
                hot.add((f.path, fn.name))
    extra = set()
    for f in files:
        local = {fn.name: fn for fn in f.funcs}
        for fn in f.funcs:
            if (f.path, fn.name) not in hot:
                continue
            for c in fn.calls:
                g = local.get(c)
                if g and g.end - g.start + 1 <= HOT_CALLEE_MAX_LINES:
                    extra.add((f.path, c))
    return hot | extra


def render(files, level, rev, sha):
    out = []
    hot = hot_set(files) if level == 1 else set()
    for f in files:
        drop = set(f.license_lines())
        drop |= f.stmt_lines(f.debug_stmts)
        repl = {}         # first line -> (text, last line it stands for)
        mark_at = {fn.name_line for fn in f.funcs}
        if level < 2:
            defined = {fn.name for fn in f.funcs}
            for sl, el, name in f.fwd_decls:
                if name in defined:
                    drop.update(range(sl, el + 1))
            starts = {}
            for fn in f.funcs:
                full = level == 1 and (f.path, fn.name) in hot
                starts[fn.start] = full
                if full:
                    continue
                drop.update(range(fn.body_start, fn.body_end + 1))
                head = f.lines[fn.body_start - 1][:fn.body_col]
                calls = ", ".join(fn.calls) if fn.calls else "-"
                repl[fn.body_start] = ("%s{ ... }  //@%d-%d calls: %s" % (
                    head, fn.start, fn.end, calls), fn.body_end)
            # A comment block right above an elided function shrinks to
            # its first sentence.
            for cs, ce, text in f.top_comments:
                full = starts.get(ce + 1, starts.get(ce + 2))
                if full is None or full or (ce + 2 in starts
                                            and f.lines[ce].strip()):
                    continue
                drop.update(range(cs, ce + 1))
                repl[cs] = ("/* %s */  //@%d-%d" % (first_sentence(text), cs, ce),
                               ce)
            # At L0 every other long top-level comment in a .c file also
            # shrinks to its first sentence. Header comments stay whole:
            # they document the types.
            if level == 0 and f.path.endswith(".c"):
                for cs, ce, text in f.top_comments:
                    if cs in drop or ce - cs < 3:
                        continue
                    drop.update(range(cs, ce + 1))
                    repl[cs] = ("/* %s */  //@%d-%d" % (first_sentence(text), cs, ce),
                               ce)
            # Test hook blocks become one line.
            for a, b in f.tests_blocks():
                if a in drop:
                    continue
                for k in range(a, b + 1):
                    repl.pop(k, None)
                drop.update(range(a, b + 1))
                repl[a] = ("#if (NXT_TESTS) ... #endif  //@%d-%d test hooks"
                           " dropped" % (a, b), b)

        out.append("=== FILE %s (%s %s) ===" % (f.path, rev, sha))
        last = 0          # last emitted original line
        last_mark = -100
        pending = False
        gap_pending = False
        blank_run = False
        for n, line in enumerate(f.lines, 1):
            if n in repl:
                text, end = repl[n]
                if gap_pending and "//@" not in text:
                    text += "  //@%d" % n
                out.append(text)
                last, last_mark, blank_run = end, n, False
                gap_pending = False
                continue
            if n in drop:
                if n > last:
                    gap_pending = True
                continue
            if not line.strip():
                if blank_run or last == 0:
                    gap_pending = gap_pending or last != 0
                    continue
                blank_run = True
                out.append("")
                last = n
                continue
            blank_run = False
            want = (pending or gap_pending or n in mark_at or last == 0
                    or n - last_mark >= 10)
            text = line.rstrip()
            if want:
                if text.endswith("\\"):
                    # A "//" comment would swallow the line splice.
                    text = text[:-1].rstrip() + "  /*@%d*/ \\" % n
                else:
                    text += "  //@%d" % n
                last_mark, pending, gap_pending = n, False, False
            out.append(text)
            last = n
        out.append("")
    return "\n".join(out) + "\n"


def first_sentence(comment, limit=200):
    t = re.sub(r"^/\*+|\*+/$", "", comment.strip())
    t = " ".join(l.strip().lstrip("*").strip() for l in t.split("\n"))
    t = re.sub(r"\s+", " ", t).strip()
    m = re.search(r"(?<=[.:;])\s", t)
    short = t[:m.start()] if m else t
    if len(short) > limit:
        short = short[:limit].rsplit(" ", 1)[0]
    return short + (" [...]" if short != t else "")


def preamble(level, rev, sha, files, body):
    names = {0: "L0 skeleton", 1: "L1 hot path", 2: "L2 full"}
    return (
        "FreeUnit C-source map, %s, %s (%s).\n"
        "Files: %s.\n"
        "Conventions: '//@N' at line end = original line N of that file.\n"
        "'{ ... }  //@A-B calls: f, g' = function body elided; it spans "
        "lines A-B and calls f, g (functions defined in these files).\n"
        "Dropped everywhere: license comments, debug-level log statements "
        "(%s), extra blank lines. L0/L1 also drop forward declarations of "
        "functions defined in the same file, cut the comment above an "
        "elided function to its first sentence ('[...]' marks the cut; L0 "
        "cuts every top-level comment in a .c file this way), "
        "and fold #if (NXT_TESTS) test-hook blocks to one line.\n"
        "Built by build-pack.py; about %d tokens at %.1f chars/token.\n\n"
        % (names[level], rev, sha, ", ".join(f.path for f in files),
           ", ".join(sorted(DEBUG_FNS)), tokens(len(body)), CHARS_PER_TOKEN))


def stats(files, sha):
    hdr = ("file", "lines", "ncloc", "comment", "blank", "chars", "tok",
           "debug", "assert", "license", "tests", "tlog")
    rows = []
    for f in files:
        text = "\n".join(f.lines)
        ncloc = len(f.code_lines)
        tlog = sum(1 for l in f.lines if "nxt_thread_log_" in l)
        cmt = len(f.comment_lines - f.code_lines)
        blank = sum(1 for l in f.lines if not l.strip())
        rows.append((f.path, len(f.lines), ncloc, cmt, blank, len(text),
                     tokens(len(text)),
                     len(f.stmt_lines(f.debug_stmts)),
                     len(f.stmt_lines(f.asserts)), len(f.license_lines()),
                     len(f.tests_block_lines()), tlog))
    tot = ["TOTAL"] + [sum(r[i] for r in rows) for i in range(1, len(hdr))]
    print("# measurements at %s; tok = chars / %.1f" % (sha, CHARS_PER_TOKEN))
    print("\t".join(hdr))
    for r in rows + [tuple(tot)]:
        print("\t".join(str(x) for x in r))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--rev", default="origin/master")
    ap.add_argument("--extra", action="append", default=[], metavar="PATH",
                    help="add a file to the set, e.g. src/nxt_application.c"
                         " (the prototype's port handlers); repeatable")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--level", type=int, choices=(0, 1, 2))
    g.add_argument("--stats", action="store_true")
    g.add_argument("--index", action="store_true")
    a = ap.parse_args()

    FILES.extend(p for p in a.extra if p not in FILES)
    sha, files = load(a.repo, a.rev)
    if check_coverage(files):
        sys.exit("build-pack: parse coverage check failed")
    callgraph(files)

    if a.stats:
        stats(files, sha)
        return
    if a.index:
        hot = hot_set(files)
        for f in files:
            for fn in f.funcs:
                print("%s:%d\t%s\t%d\t%s\t%s" % (
                    f.path, fn.name_line, fn.name, fn.end - fn.start + 1,
                    "hot" if (f.path, fn.name) in hot else "-",
                    ",".join(fn.calls)))
        return
    body = render(files, a.level, a.rev, sha)
    sys.stdout.write(preamble(a.level, a.rev, sha, files, body) + body)


if __name__ == "__main__":
    main()
