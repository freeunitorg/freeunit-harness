#!/usr/bin/env python3
"""Candidate finder for loop-invariant work (docs/perf/loop-invariant-audit-2026-10-07.md).

Run from a FreeUnit tree: python3 loop-invariant-scan.py scope.txt > candidates.tsv
scope.txt lists the .c files, one per line. Columns: pattern, file, line, loop start,
loop kind, call:class, inv/var (all identifiers in the arguments unassigned in the loop), code.
Loops come from ast-grep (for/while/do) plus the nxt_*_each macro loops.
Each finding is attributed to the innermost loop that contains its line.
"""
import json, re, shutil, subprocess, sys, collections

USAGE = "usage: loop-invariant-scan.py SCOPE.txt > candidates.tsv   (SCOPE.txt lists .c files, one per line; needs ast-grep)"
if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
    print(USAGE, file=sys.stderr if len(sys.argv) != 2 else sys.stdout)
    sys.exit(0 if sys.argv[1:] in (["-h"], ["--help"]) else 2)
if not shutil.which("ast-grep"):
    print("loop-invariant-scan: ast-grep is not on PATH; install it (https://ast-grep.github.io)", file=sys.stderr); sys.exit(2)
try:
    files = [l.strip() for l in open(sys.argv[1]) if l.strip()]
except OSError as e:
    print(f"loop-invariant-scan: cannot read {sys.argv[1]}: {e.strerror}", file=sys.stderr); sys.exit(2)

loops = []  # (file, start, end, kind, cond, text)
for kind in ("for_statement", "while_statement", "do_statement"):
    rule = f"id: x\nlanguage: c\nrule:\n  kind: {kind}\n"
    out = subprocess.run(["ast-grep", "scan", "--inline-rules", rule,
                          "--json=compact"] + files,
                         capture_output=True, text=True, check=True).stdout
    for m in json.loads(out):
        s = m["range"]["start"]["line"] + 1
        e = m["range"]["end"]["line"] + 1
        txt = m["text"]
        if kind == "for_statement":
            hdr = re.match(r"for\s*\((.*?)\)\s*[{\n]", txt, re.S)
            cond = hdr.group(1) if hdr else txt.split("\n")[0]
        elif kind == "while_statement":
            cond = txt.split("\n")[0]
        else:
            cond = txt.strip().split("\n")[-1]
        loops.append((m["file"], s, e, kind, cond, txt))

MACROS = ["nxt_list_each", "nxt_queue_each", "nxt_http_fields_each",
          "nxt_runtime_process_each", "nxt_process_port_each"]
src = {}
for f in files:
    src[f] = open(f, encoding="utf-8", errors="replace").read().split("\n")
    lines = src[f]
    for i, l in enumerate(lines):
        for mac in MACROS:
            if re.search(r"\b%s\(" % mac, l) and "#define" not in l:
                end_mac = mac.replace("_each", "_loop")
                for j in range(i + 1, len(lines)):
                    if re.search(r"\b%s\b" % end_mac, lines[j]):
                        break
                loops.append((f, i + 1, j + 1, mac, l.strip(),
                              "\n".join(lines[i:j + 1])))

print(f"# loops: {len(loops)} " + str(collections.Counter(l[3] for l in loops)),
      file=sys.stderr)

ASSIGN = re.compile(r"(?<![=!<>])\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:=(?!=)|\+=|-=|\*=|/=|\|=|&=|<<=|>>=|\+\+|--)")
PREINC = re.compile(r"(?:\+\+|--)\s*([A-Za-z_]\w*)")
ADDR = re.compile(r"&\s*([A-Za-z_]\w*)")
IDENT = re.compile(r"(?<![\.>\w])([A-Za-z_]\w*)")

def assigned(text):
    s = set(ASSIGN.findall(text)) | set(PREINC.findall(text)) | set(ADDR.findall(text))
    # declarations inside the loop body are per-iteration
    for m in re.finditer(r"^\s*(?:const\s+)?[A-Za-z_][\w ]*?[\s\*]+([A-Za-z_]\w*)\s*[;=\[]", text, re.M):
        s.add(m.group(1))
    return s

def innermost(f, line):
    best = None
    for lp in loops:
        if lp[0] == f and lp[1] <= line <= lp[2]:
            if best is None or (lp[2] - lp[1]) < (best[2] - best[1]):
                best = lp
    return best

def depth(f, line):
    return sum(1 for lp in loops if lp[0] == f and lp[1] <= line <= lp[2])

PURE = r"strlen|nxt_strlen|nxt_length|nxt_str_eq|nxt_strstr_eq|nxt_strcasestr_eq|nxt_strcasecmp|nxt_strncasecmp|nxt_memcmp|nxt_memcasecmp|memcmp|strcmp|strncmp|nxt_lvlhsh_find|nxt_http_field_hash|nxt_djb_hash|nxt_djb_hash_lowcase|nxt_murmur_hash2|nxt_conf_get_path|nxt_conf_get_object_member|nxt_conf_object_members_count|nxt_conf_array_elements_count|nxt_conf_type"
CLOCK = r"nxt_thread_time|nxt_thread_monotonic_time|nxt_thread|nxt_thread_log|getpid|nxt_pid|nxt_random|nxt_thread_tid|clock_gettime|gettimeofday|time"
ALLOC = r"nxt_mp_alloc|nxt_mp_zalloc|nxt_mp_get|nxt_mp_nget|nxt_mp_zget|nxt_malloc|nxt_zalloc|nxt_realloc|malloc|calloc|realloc|nxt_buf_mem_alloc|nxt_array_add|nxt_array_zero_add|nxt_list_add|nxt_unit_malloc|nxt_mp_align|nxt_memalign"
LOCK = r"nxt_thread_mutex_lock|nxt_thread_spin_lock|pthread_mutex_lock|nxt_atomic_\w+|nxt_queue_lock"
COPY = r"nxt_sprintf|nxt_cpymem|nxt_memcpy|memcpy|nxt_cpystr|nxt_cpystrn|nxt_memzero|nxt_memset|memset"
SYSC = r"write|read|writev|readv|sendmsg|recvmsg|send|recv|sendto|recvfrom|epoll_ctl|kevent|fcntl|ioctl|nxt_fd_write|nxt_fd_read|nxt_socket_\w+|nxt_file_\w+|sched_yield|nxt_thread_yield|nxt_nanosleep|pread|pwrite|lseek|fstat|stat|open|close|nxt_fd_close|munmap|mmap|ftruncate|nxt_port_socket_write2?|nxt_port_socket_twrite|nxt_unit_port_send|nxt_port_post"
CALL = re.compile(r"\b(%s|%s|%s|%s|%s|%s)\s*\(" % (PURE, CLOCK, ALLOC, LOCK, COPY, SYSC))

def cls(name):
    for tag, rx in (("PURE", PURE), ("CLOCK", CLOCK), ("ALLOC", ALLOC),
                    ("LOCK", LOCK), ("COPY", COPY), ("SYSC", SYSC)):
        if re.fullmatch(rx, name):
            return tag

def args_of(text, pos):
    d = 0
    for k in range(pos, len(text)):
        if text[k] == "(":
            d += 1
        elif text[k] == ")":
            d -= 1
            if d == 0:
                return text[pos + 1:k]
    return text[pos + 1:]

STATIC_IDX = re.compile(r"&?\b([a-z_]\w*)\s*\[\s*([A-Z][A-Z0-9_]*|\d+)\s*\]((?:\s*(?:\.|->)\s*\w+)*)")
CHAIN = re.compile(r"\b([a-z_]\w*)((?:->\w+){2,})")

out = []
for f in files:
    lines = src[f]
    for ln, l in enumerate(lines, 1):
        lp = innermost(f, ln)
        if lp is None:
            continue
        code = re.sub(r"/\*.*?\*/|//.*$", "", l)
        if not code.strip() or code.lstrip().startswith(("*", "#")):
            continue
        asg = assigned(lp[5])
        is_hdr = (ln == lp[1])
        # P1: calls in the loop condition
        if is_hdr and lp[3] in ("for_statement", "while_statement"):
            for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", lp[4]):
                n = m.group(1)
                if n in ("for", "while", "if", "sizeof", "nxt_fast_path", "nxt_slow_path", "nxt_expect"):
                    continue
                a = args_of(lp[4], m.end() - 1)
                ids = {x for x in IDENT.findall(a) if not x.isupper()}
                inv = not (ids & asg)
                out.append(("P1", f, ln, lp[1], lp[3], n, "inv" if inv else "var", code.strip()))
        if lp[3] == "do_statement" and ln == lp[2]:
            for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", code):
                n = m.group(1)
                if n in ("while", "sizeof"):
                    continue
                a = args_of(code, m.end() - 1)
                ids = {x for x in IDENT.findall(a) if not x.isupper()}
                inv = not (ids & asg)
                out.append(("P1", f, ln, lp[1], "do_cond", n, "inv" if inv else "var", code.strip()))
        if is_hdr:
            continue
        # P2: static table with constant index, and invariant pointer chains
        asg_strict = set(ASSIGN.findall(lp[5])) | set(PREINC.findall(lp[5]))
        for m in STATIC_IDX.finditer(code):
            if m.group(1) in asg_strict:
                continue
            out.append(("P2tab", f, ln, lp[1], lp[3], m.group(0).strip(), "", code.strip()))
        for m in CHAIN.finditer(code):
            if m.group(1) in asg:
                continue
            # the chain is invariant only if no field on it is reassigned
            out.append(("P2chain", f, ln, lp[1], lp[3], m.group(0), "", code.strip()))
        # P3/P5: listed calls
        for m in CALL.finditer(code):
            n = m.group(1)
            if code[:m.start()].rstrip().endswith((".", "->")):
                continue
            a = args_of(code, m.end() - 1)
            ids = {x for x in IDENT.findall(a) if not x.isupper()}
            inv = not (ids & asg)
            out.append(("P3" if cls(n) not in ("SYSC",) and not n.startswith("nxt_atomic") else "P5",
                        f, ln, lp[1], lp[3], n + ":" + cls(n), "inv" if inv else "var", code.strip()))
        # P4: nested loop marker
        if depth(f, ln) >= 2 and re.search(r"\b(nxt_str_eq|nxt_strcasestr_eq|nxt_memcasecmp|nxt_memcmp|memcmp|strcmp|nxt_strcasecmp|nxt_strncasecmp|nxt_http_field_hash)\s*\(", code):
            out.append(("P4", f, ln, lp[1], lp[3], "nested-compare", "", code.strip()))

for o in out:
    print("\t".join(str(x) for x in o))
