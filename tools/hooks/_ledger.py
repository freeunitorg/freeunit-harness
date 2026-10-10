"""Shared code of the task-ledger hooks (docs/task-ledger.md section 5).

Each hook script reads one JSON object on stdin, decides, runs tools/task, and prints at most one
JSON object. A hook must never break a session: every error ends in exit 0 with no output.
Configuration: HOOKS_ENV (default run/hooks.env, else hooks.env.example beside this file).
A variable set in the environment wins over the file.
"""
import datetime, importlib.machinery, importlib.util, json, os, re, shlex, subprocess, sys

sys.dont_write_bytecode = True
HOOKS = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(os.path.dirname(HOOKS))
TASK, INBOX = os.path.join(HARNESS, "tools", "task"), os.path.join(HARNESS, "tools", "inbox")
KEYS = ("TASK_ROOT", "INBOX_LOG", "INBOX_SENDER", "TASK_SHARED_CHECKOUTS")
DEFAULTS = {"TASK_ROOT": "run/tasks", "INBOX_LOG": "run/inbox/bus.log", "INBOX_SENDER": "orchestrator@claude", "TASK_SHARED_CHECKOUTS": ""}
CONTEXT_CAP = 8 * 1024

# ---- configuration, stdin, output ----

def config():
    """The hook configuration: the env file, then the environment. Relative paths resolve against the harness root."""
    path = os.environ.get("HOOKS_ENV") or os.path.join(HARNESS, "run", "hooks.env")
    if not os.path.isfile(path): path = os.path.join(HOOKS, "hooks.env.example")
    conf = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.fullmatch(r"\s*(?:export\s+)?([A-Z_]+)=(.*?)\s*", line.rstrip("\n"))
                if m and m.group(1) in KEYS: conf[m.group(1)] = m.group(2).strip("'\"")
    except OSError: pass
    for k in KEYS:
        if os.environ.get(k): conf[k] = os.environ[k]
    for k in ("TASK_ROOT", "INBOX_LOG"):
        conf[k] = os.path.join(HARNESS, os.path.expanduser(os.path.expandvars(conf[k])))
    shared = (os.path.expanduser(os.path.expandvars(p)) for p in conf["TASK_SHARED_CHECKOUTS"].split(":"))
    conf["shared"] = [os.path.realpath(p) for p in shared if os.path.isabs(p)]
    return conf

def read_stdin():
    try: data = json.load(sys.stdin)
    except ValueError: sys.exit(0)
    if not isinstance(data, dict): sys.exit(0)
    return data

def start(data=None):
    """Return (stdin object, Ledger). Exit 0 silently when the ledger root does not exist."""
    data = read_stdin() if data is None else data
    conf = config()
    if not os.path.isdir(conf["TASK_ROOT"]): sys.exit(0)
    return data, Ledger(conf, str(data.get("session_id") or ""))

def run(fn):
    """Run a hook body. Any error ends in exit 0 with no output; TASK_HOOKS_DEBUG=1 prints the traceback on stderr."""
    try: fn()
    except SystemExit: pass
    except BaseException:
        if os.environ.get("TASK_HOOKS_DEBUG"):
            import traceback; traceback.print_exc()
    sys.exit(0)

def emit(event, **fields):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}))

def cap(text, limit):
    """Cut text to at most limit bytes of UTF-8 and say so."""
    if len(text.encode()) <= limit: return text
    return text.encode()[:limit - 30].decode("utf-8", "ignore").rstrip() + "\n[cut at the hook cap]\n"

def now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---- the task CLI ----

class Ledger:
    def __init__(self, conf, session):
        self.conf, self.session = conf, session
        self.root, self.sender = conf["TASK_ROOT"], conf["INBOX_SENDER"]
        self.base = os.path.dirname(os.path.dirname(self.root))     # tools/task resolves relative links against this

    def env(self, session=True, owner=None):
        env = dict(os.environ, TASK_ROOT=self.root, INBOX_LOG=self.conf["INBOX_LOG"], INBOX_SENDER=owner or self.sender,
                   TASK_SESSION=self.session if session else "")
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return env

    def run(self, tool, *args, timeout=8, cwd=None, session=True, owner=None):
        """Run tools/task or tools/inbox. Return the CompletedProcess, or None on a timeout or an OS error.
        owner is the sender for a write on a task: the owner recorded in the task, so its TTL counts the line."""
        try:
            return subprocess.run([sys.executable, tool, *args], capture_output=True, text=True, timeout=timeout,
                                  env=self.env(session, owner), cwd=cwd or self.base, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError): return None

    def task(self, *args, **kw): return self.run(TASK, *args, **kw)

    def mine(self, session=True, timeout=8):
        """`task mine` lines. With session=False, every task the sender owns in any session."""
        r = self.task("mine", session=session, timeout=timeout)
        return [l for l in r.stdout.splitlines() if l.strip()] if r and r.returncode == 0 else []

    def owned(self):
        """[(id, owner)] of the open tasks claimed in this session, whatever the owner string.
        The session id from stdin is the `session` key that `task claim` records, so it alone finds the claim.
        The sender in the hooks env does not need to match the owner that the model's shell recorded."""
        if not self.session: return []
        out = []
        for meta, _ in self.module().all_tasks():
            if (meta.get("session") == self.session and meta.get("owner") and meta.get("status") not in ("done", "abandoned")
                    and isinstance(meta.get("id"), str)):
                out.append((meta["id"], str(meta["owner"])))
        return out

    def active(self):
        """(id, owner) of the first task claimed in this session, or (None, None)."""
        found = self.owned()
        return found[0] if found else (None, None)

    def note(self, tid, text, tail=None, timeout=8, owner=None):
        args = ["note", tid] + (["--json", json.dumps(tail, separators=(",", ":"))] if tail else []) + ["--", text]
        return self.task(*args, timeout=timeout, owner=owner)

    def exists(self, tid): return os.path.isfile(os.path.join(self.root, tid + ".md"))

    def module(self):
        """tools/task loaded in this process, for lookups that the CLI has no command for. Never used to write."""
        if getattr(self, "_module", None): return self._module
        os.environ.update(TASK_ROOT=self.root, INBOX_LOG=self.conf["INBOX_LOG"], INBOX_SENDER=self.sender, TASK_SESSION=self.session)
        loader = importlib.machinery.SourceFileLoader("task_ledger", TASK)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("task_ledger", loader))
        loader.exec_module(mod)
        self._module = mod
        return mod

# ---- shell commands ----

SHELLS = ("bash", "sh", "dash", "zsh", "ksh")

def inner_command(words):
    """The command string that `bash -c '<string>'`, `sh -c` or `eval` runs, or None."""
    name = os.path.basename(words[0])
    if name == "eval": return " ".join(words[1:]) or None
    if name not in SHELLS: return None
    has_c = False
    for w in words[1:]:
        if re.fullmatch(r"-[a-zA-Z]+", w):
            has_c = has_c or "c" in w
        elif w.startswith(("-", "+")): continue
        else: return w if has_c else None
    return None

def segments(command, cwd, depth=0):
    """Split a shell command into simple commands. Yield (directory, words), following `cd <path>`.
    The string of `bash -c`, `sh -c` and `eval` is split the same way, two levels deep at most."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lex.whitespace, lex.commenters, lex.whitespace_split = " \t\r", "", True
        tokens = list(lex)
    except ValueError:
        tokens = command.split()
    cur, words = cwd, []
    for t in tokens + [";"]:
        if t and all(c in ";&|()\n" for c in t):
            while words and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]) or words[0] in ("command", "env", "exec", "nohup", "sudo", "time")):
                words.pop(0)
            inner = inner_command(words) if words and depth < 2 else None
            if words and words[0] in ("cd", "pushd") and len(words) > 1 and words[1] != "-": cur = resolve(cur, words[1])
            elif inner: yield from segments(inner, cur, depth + 1)
            elif words: yield cur, words
            words = []
        else: words.append(t)

def resolve(cur, path): return os.path.normpath(os.path.join(cur or "/", os.path.expanduser(path)))

def git_command(cur, words):
    """For `git [-C dir] [-c k=v] <sub> args`, return (directory, sub, args); else None."""
    if os.path.basename(words[0]) != "git": return None
    i, d = 1, cur
    while i < len(words) and words[i].startswith("-"):
        if words[i] == "-C" and i + 1 < len(words): d = resolve(d, words[i + 1]); i += 2
        elif words[i] in ("-c", "--git-dir", "--work-tree", "--namespace", "--exec-path") and i + 1 < len(words): i += 2
        else: i += 1
    return (d, words[i], words[i + 1:]) if i < len(words) else None

def git(d, *args):
    """git output in directory d, stripped; empty on any failure."""
    try:
        r = subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError): return ""

def full_ref(name): return name if name.startswith("refs/") else "refs/heads/" + name

def push_targets(d, args):
    """For `git push args` in d: (remote URL, [(source, destination ref)]). None for --all, --mirror, --tags or a delete."""
    pos, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("--all", "--mirror", "--tags", "--delete", "-d", "--branches"): return None
        if a in ("-o", "--push-option", "--repo", "--receive-pack", "--exec"): i += 2; continue
        if not a.startswith("-"): pos.append(a)
        i += 1
    remote = pos[0] if pos else "origin"
    url = remote if ("/" in remote or ":" in remote) else git(d, "remote", "get-url", remote)
    branch = git(d, "symbolic-ref", "-q", "--short", "HEAD")
    targets = []
    for spec in pos[1:] or ([branch] if branch else []):
        src, colon, dst = spec.lstrip("+").partition(":")
        if not colon: dst = src
        if dst == "HEAD": dst = branch
        if src and dst: targets.append((src, full_ref(dst)))
    return url, targets

def norm_remote(url):
    """A remote URL in a comparable form: host/owner/repo for a network URL, the real path for a local one."""
    url = url.strip().rstrip("/")
    url = url[:-4] if url.endswith(".git") else url
    m = re.fullmatch(r"(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+\.[^/:]+)(?::\d+)?[:/](.+)", url)
    return (m.group(1) + "/" + m.group(2)).lower() if m and not url.startswith("/") else os.path.realpath(url)
