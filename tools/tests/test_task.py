"""Tests of tools/task and of `tools/inbox send --task`.

Run: python3 -m unittest discover -s tools/tests -v   (pytest also collects them)
Each test works in its own temporary directory, set through TASK_ROOT and INBOX_LOG.
The remote for leases is a local bare repository. Nothing touches the network or GitHub.
"""
import importlib.machinery, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, unittest, unittest.mock

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK, INBOX = os.path.join(TOOLS, "task"), os.path.join(TOOLS, "inbox")

def load_task():
    loader = importlib.machinery.SourceFileLoader("task_under_test", TASK)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("task_under_test", loader))
    loader.exec_module(module)
    return module

task = load_task()

try:
    import yaml
except ImportError:
    yaml = None


class TaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "run", "tasks")
        self.bus = os.path.join(self.tmp, "run", "inbox", "bus.log")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("INBOX_", "TASK_", "GIT_")) and k != "CLAUDE_CODE_SESSION_ID"}
        env.update(TASK_ROOT=self.root, INBOX_LOG=self.bus, INBOX_SENDER="orchestrator@claude", TASK_SESSION="s1",
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.env = env

    # ---- helpers ----

    def run_tool(self, tool, *args, sender=None, cwd=None, extra=None):
        env = dict(self.env)
        if sender: env["INBOX_SENDER"] = sender
        env.update(extra or {})
        return subprocess.run([sys.executable, tool, *args], capture_output=True, text=True, env=env, cwd=cwd or self.tmp)

    def task(self, *args, ok=True, **kw):
        r = self.run_tool(TASK, *args, **kw)
        if ok: self.assertEqual(r.returncode, 0, f"task {args}: {r.stderr}")
        return r

    def refused(self, *args, **kw):
        r = self.run_tool(TASK, *args, **kw)
        self.assertEqual(r.returncode, 1, f"task {args} was not refused: {r.stdout} {r.stderr}")
        self.assertTrue(r.stderr.startswith("task: "), r.stderr)
        return r

    def meta(self, tid):
        return task.read_task(os.path.join(self.root, tid + ".md"))[0]

    def file_log(self, tid):
        return task.read_task(os.path.join(self.root, tid + ".md"))[1]["log"]

    def bus_lines(self):
        with open(self.bus) as f:
            return [l.rstrip("\n") for l in f]

    def git(self, *args, cwd=None):
        r = subprocess.run(["git", *args], capture_output=True, text=True, env=self.env, cwd=cwd or self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def make_remote(self):
        """A bare repository with branch feat, and a clone that has feat checked out."""
        self.remote = os.path.join(self.tmp, "remote.git")
        self.work = os.path.join(self.tmp, "work")
        self.git("init", "-q", "--bare", self.remote)
        self.git("init", "-q", "-b", "feat", self.work)
        self.commit("one")
        self.git("remote", "add", "origin", self.remote, cwd=self.work)
        self.git("push", "-q", "origin", "feat", cwd=self.work)
        return self.git("rev-parse", "HEAD", cwd=self.work)

    def commit(self, msg):
        self.git("-c", "user.name=test", "-c", "user.email=test@example.invalid",
                 "commit", "-q", "--allow-empty", "-m", msg, cwd=self.work)
        return self.git("rev-parse", "HEAD", cwd=self.work)

    # ---- tests ----

    def test_new_and_show_round_trip(self):
        self.task("new", "issue/7/w1", "--title", "wave 1: a, b [c]", "--url", "https://example.org/7", "--dep", "tag/1.0")
        path = os.path.join(self.root, "issue", "7", "w1.md")
        with open(path) as f:
            self.assertEqual(self.task("show", "issue/7/w1").stdout, f.read())
        meta = self.meta("issue/7/w1")
        self.assertEqual((meta["id"], meta["kind"], meta["title"], meta["status"]), ("issue/7/w1", "issue", "wave 1: a, b [c]", "open"))
        self.assertEqual((meta["url"], meta["deps"], meta["links"]), ("https://example.org/7", ["tag/1.0"], {"issue": 7}))
        self.assertEqual(set(meta["gates"]), set(task.GATES))
        self.assertTrue(all(g == {"state": "pending"} for g in meta["gates"].values()))
        self.refused("new", "issue/7/w1", "--title", "again")

    def test_claim_refused_while_another_owner_is_live(self):
        self.task("new", "job/x", "--title", "x")
        self.task("claim", "job/x", sender="perf-runner@pi")
        r = self.refused("claim", "job/x", sender="reviewer@dsh")
        self.assertIn("claimed by perf-runner@pi", r.stderr)
        self.assertEqual(self.meta("job/x")["owner"], "perf-runner@pi")
        self.task("claim", "job/x", sender="perf-runner@pi")               # the owner may claim again

    def test_claim_records_lease_from_ls_remote(self):
        head = self.make_remote()
        self.task("new", "pr/1", "--title", "one")
        self.task("claim", "pr/1", "--ref", "feat", "--remote", self.remote, "--worktree", "scratch/w1")
        meta = self.meta("pr/1")
        self.assertEqual(meta["lease"]["ref"], "refs/heads/feat")
        self.assertEqual(meta["lease"]["sha"], head)
        self.assertEqual((meta["owner"], meta["session"], meta["status"]), ("orchestrator@claude", "s1", "claimed"))
        self.assertEqual(meta["links"]["worktree"], "scratch/w1")
        self.refused("claim", "pr/1", "--ref", "nope", "--remote", self.remote)

    def test_pr_claim_needs_a_relative_worktree(self):
        self.task("new", "pr/2", "--title", "two")
        self.assertIn("worktree", self.refused("claim", "pr/2").stderr)
        self.refused("claim", "pr/2", "--worktree", "/abs/w2")
        self.refused("claim", "pr/2", "--worktree", "~/w2")
        self.task("claim", "pr/2", "--worktree", "scratch/w2")

    def test_lease_check_passes_then_fails_after_the_branch_moves(self):
        old = self.make_remote()
        self.task("new", "pr/3", "--title", "three")
        self.task("claim", "pr/3", "--ref", "feat", "--remote", self.remote, "--worktree", "scratch/w3")
        self.assertIn(old, self.task("lease-check", "pr/3").stdout)
        self.assertEqual(self.task("lease-check", "--cwd", cwd=self.work).returncode, 0)
        new = self.commit("two")
        self.git("push", "-q", "origin", "feat", cwd=self.work)
        r = self.refused("lease-check", "pr/3")
        self.assertIn(old, r.stderr)
        self.assertIn(new, r.stderr)
        self.refused("lease-check", "--cwd", cwd=self.work)
        self.assertEqual(self.meta("pr/3")["lease"]["sha"], old)              # never updated by a check

    def test_gate_evidence_shapes(self):
        self.task("new", "issue/4", "--title", "four")
        self.refused("gate", "issue/4", "review_gate", "pass")
        self.refused("gate", "issue/4", "review_gate", "pass", "--evidence", "looks fine")
        self.refused("gate", "issue/4", "review_gate", "pass", "--evidence", "/abs/src/a.c:10@0123456789abcdef")
        self.refused("gate", "issue/4", "measured_number", "n/a")
        self.task("gate", "issue/4", "review_gate", "pass", "--evidence", "https://example.org/review/1")
        self.task("gate", "issue/4", "hardening_strict", "pass", "--evidence", "src/nxt_unit.c:120@0123456789abcdef")
        self.task("gate", "issue/4", "test_fails_prechange", "pass", "--evidence", "./build/tests > run/t.log")
        self.task("gate", "issue/4", "measured_number", "n/a", "--evidence", "no number is claimed")
        gates = self.meta("issue/4")["gates"]
        self.assertEqual(gates["hardening_strict"], {"state": "pass", "evidence": "src/nxt_unit.c:120@0123456789abcdef"})
        self.assertEqual(gates["plain_english"], {"state": "pending"})

    def test_gate_output_file_must_be_a_path(self):
        self.task("new", "issue/14", "--title", "fourteen")
        open(os.path.join(self.tmp, "out.txt"), "w").close()
        for evidence in ("it is fine > trust me", "make > missing.txt", "make > /abs/out.txt", "make > ../out.txt", " > out.txt"):
            self.refused("gate", "issue/14", "review_gate", "pass", "--evidence", evidence)
        self.assertEqual(self.meta("issue/14")["gates"]["review_gate"], {"state": "pending"})
        self.task("gate", "issue/14", "review_gate", "pass", "--evidence", "make check > out.txt")
        self.task("gate", "issue/14", "plain_english", "pass", "--evidence", "tools/check-style -> run/style.log")

    def test_sender_defaults_to_agent_at_claude_in_a_session(self):
        env = {k: v for k, v in self.env.items() if k != "INBOX_SENDER"}
        r = subprocess.run([sys.executable, INBOX, "send", "fact", "hello"], capture_output=True, text=True,
                           env=dict(env, CLAUDE_CODE_SESSION_ID="abc"), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(self.bus_lines()[-1], r"^\S+Z agent@claude fact [0-9a-f]{6} hello$")
        r = subprocess.run([sys.executable, INBOX, "send", "fact", "plain shell"], capture_output=True, text=True,
                           env=dict(env, USER="someone"), cwd=self.tmp)
        self.assertRegex(self.bus_lines()[-1], r"^\S+Z someone@sh fact [0-9a-f]{6} plain shell$")

    def test_done_refused_while_a_gate_is_pending(self):
        self.task("new", "issue/5", "--title", "five")
        self.assertIn("review_gate", self.refused("done", "issue/5").stderr)
        self.assertEqual(self.meta("issue/5")["status"], "open")
        self.task("done", "issue/5", "--maintainer")
        self.assertEqual(self.meta("issue/5")["status"], "done")
        self.assertIn("maintainer override", self.file_log("issue/5")[-1])

    def test_handoff_needs_a_next_section(self):
        self.task("new", "job/h", "--title", "h")
        self.task("claim", "job/h")
        self.assertIn("## Next", self.refused("handoff", "job/h").stderr)
        self.task("next", "job/h", "Run the bench on the lab box, then report.")
        self.task("handoff", "job/h", "--to", "perf-runner@pi")
        meta = self.meta("job/h")
        self.assertEqual((meta["owner"], meta["status"], meta["role"]), (None, "open", "perf-runner"))
        last = task.inbox.parse(self.bus_lines()[-1])
        self.assertEqual(last["kind"], "request")
        self.assertIn('"to":"perf-runner"', last["body"])
        self.assertIn("job/h", self.task("ready", "--role", "perf-runner").stdout)

    def test_every_write_goes_to_the_task_file_and_the_bus(self):
        self.task("new", "job/w", "--title", "w")
        self.task("claim", "job/w")
        self.task("note", "job/w", "a", "fact", "--json", '{"pr":9}')
        self.task("link", "job/w", "pr=9", "review=run/agent-findings/r.md")
        self.task("gate", "job/w", "review_gate", "pass", "--evidence", "https://example.org/r")
        self.task("next", "job/w", "next step")
        self.task("handoff", "job/w")
        self.task("done", "job/w")
        r = self.run_tool(INBOX, "send", "fact", "from inbox", "--task", "job/w")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.file_log("job/w")
        self.assertEqual(len(log), 9)
        self.assertEqual(log, self.bus_lines())
        for line in log:
            tail = json.loads(line[line.rindex(' {"') + 1:])
            self.assertEqual(tail["task"], "job/w", line)
            self.assertIsNotNone(task.inbox.LINE.match(line), line)
        self.assertIn('"pr":9', log[2])

    def test_parallel_writers_lose_no_line(self):
        self.task("new", "job/p", "--title", "p")
        procs = [subprocess.Popen([sys.executable, TASK] + (["next", "job/p", f"step {i}"] if i % 3 == 0 else ["note", "job/p", f"n {i}"]),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=self.env, cwd=self.tmp) for i in range(30)]
        for p in procs:
            self.assertEqual(p.wait(), 0, p.stderr.read())
            p.stderr.close()
        self.assertEqual(len(self.file_log("job/p")), 31)
        self.assertEqual(sorted(self.file_log("job/p")), sorted(self.bus_lines()))
        self.assertEqual(os.listdir(os.path.join(self.root, "job")), ["p.md"])

    def test_inbox_without_task_is_unchanged(self):
        r = self.run_tool(INBOX, "send", "fact", "plain", "--json", '{"pr":1}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^[0-9a-f]{6}\n$")
        self.assertRegex(self.bus_lines()[-1], r'^\S+Z orchestrator@claude fact [0-9a-f]{6} plain \{"pr":1\}$')
        self.assertEqual(self.run_tool(INBOX, "send", "fact", "x", "--task", "job/none").returncode, 1)

    def test_task_log_equals_a_search_of_the_bus(self):
        for tid in ("pr/10", "pr/1"):
            self.task("new", tid, "--title", tid)
        for i in range(3):
            self.task("note", "pr/1", f"one {i}")
            self.task("note", "pr/10", f"ten {i}")
        self.run_tool(INBOX, "send", "fact", "unrelated")
        search = shutil.which("rg") or shutil.which("grep")
        found = subprocess.run([search, "-F", '"task":"pr/1"', self.bus], capture_output=True, text=True).stdout
        self.assertEqual(self.task("log", "pr/1").stdout, found)
        self.assertEqual(len(found.splitlines()), 4)
        mid = task.inbox.parse(found.splitlines()[1])["id"]
        self.assertEqual(self.task("log", "pr/1", "--since", mid).stdout.splitlines(), found.splitlines()[2:])

    def test_reap_opens_an_expired_claim(self):
        self.task("new", "job/old", "--title", "old")
        self.task("new", "job/live", "--title", "live")
        self.task("claim", "job/old", "--ttl", "0", sender="watcher@pi")
        self.task("claim", "job/live", sender="watcher@pi")
        self.assertIn("job/old", self.task("ready").stdout)
        out = self.task("reap").stdout
        self.assertIn("job/old", out)
        self.assertNotIn("job/live", out)
        meta = self.meta("job/old")
        self.assertEqual((meta["status"], meta["owner"]), ("open", None))
        self.assertIn("watcher@pi", self.file_log("job/old")[-1])
        self.assertEqual(self.meta("job/live")["owner"], "watcher@pi")
        self.task("claim", "job/old", sender="reviewer@dsh")

    def test_brief_caps_with_a_long_log(self):
        self.task("new", "issue/8", "--title", "eight")
        self.task("next", "issue/8", "keep this line")
        path = os.path.join(self.root, "issue", "8.md")
        with open(path, "a") as f:
            for i in range(400):
                f.write(f"2026-10-08T00:00:00Z reviewer@dsh fact {i:06x} note {i} " + "x" * 400 + ' {"task":"issue/8"}\n')
        self.task("note", "issue/8", "newest line")
        short = self.task("brief", "issue/8", "--short").stdout
        full = self.task("brief", "issue/8", "--for", "review").stdout
        self.assertLessEqual(len(short.encode()), task.SHORT_CAP)
        self.assertLessEqual(len(full.encode()), task.FULL_CAP)
        for text in (short, full):
            self.assertIn("keep this line", text)
            self.assertIn("newest line", text)
            self.assertIn("truncated", text)
        self.assertIn("rules block: TASK_RULES_FILE not set", full)
        self.assertEqual(len(os.listdir(os.path.join(self.root, "briefs", "issue"))), 1)
        rules = os.path.join(self.tmp, "rules.md")
        with open(rules, "w") as f:
            f.write("rule line\n" * 4000)
        big = self.task("brief", "issue/8", "--for", "implement", extra={"TASK_RULES_FILE": rules}).stdout
        self.assertLessEqual(len(big.encode()), task.FULL_CAP)
        self.assertIn("truncated", big)

    def test_brief_findings_and_public_scrub(self):
        self.task("new", "pr/11", "--title", "eleven")
        self.task("claim", "pr/11", "--worktree", "scratch/w11")
        os.makedirs(os.path.join(self.tmp, "run", "agent-findings"))
        with open(os.path.join(self.tmp, "run", "agent-findings", "r1.md"), "w") as f:
            f.write("# Review\n## F1 leak\n- Status: applied\n## F2 nit\nStatus: rejected: measured flat\n")
        with open(os.path.join(self.tmp, "run", "agent-findings", "r2.md"), "w") as f:
            f.write("# Second review\nno statuses here\n")
        self.task("link", "pr/11", "review=run/agent-findings/r1.md", "review=run/agent-findings/r2.md")
        brief = self.task("brief", "pr/11", "--for", "review").stdout
        self.assertIn("F1 leak | - Status: applied", brief)
        self.assertIn("F2 nit | Status: rejected: measured flat", brief)
        self.assertIn("Second review: no Status lines", brief)
        public = self.task("brief", "pr/11", "--for", "review", "--public").stdout
        for private in ("scratch/w11", "session s1", "run/agent-findings/r1.md"):
            self.assertNotIn(private, public.split("## Log")[0])
        self.assertNotIn('{"task"', public)

    def test_frontmatter_round_trip(self):
        d = {"id": "pr/1", "title": "a: b, c [d] {e} #f \"g\"", "empty": "", "lead": " x", "num_str": "123",
             "float_str": "1.5", "bool_str": "true", "yes_str": "yes", "dash": "-x", "at": "@me", "na": "n/a",
             "ts": "2026-10-08T06:12:40Z", "date": "2026-10-08", "uni": "café", "tab": "a\tb", "sep": "a b",
             "n": 720, "neg": -3, "t": True, "f": False, "none": None, "list": ["a,b", 1, None, "x y", "[z]"], "nil": [],
             "lease": {"ref": "refs/heads/x", "sha": "5a57ffce", "checked_at": None},
             "gates": {"review_gate": {"state": "pending"}, "g2": {"state": "pass", "evidence": "https://example.org/1"}},
             "links": {}, "x-free": {"deep": {"deeper": "v"}}}
        text = "\n".join(task.dump(d))
        self.assertEqual(task.parse_front(text), d)
        if yaml is not None:
            self.assertEqual(yaml.safe_load(text), d)

    @unittest.skipIf(yaml is None, "PyYAML is not importable")
    def test_yaml_reads_a_written_task_like_the_tool(self):
        self.make_remote()
        self.task("new", "pr/12", "--title", "twelve: yes, no", "--dep", "tag/1.37.1")
        self.task("claim", "pr/12", "--ref", "feat", "--remote", self.remote, "--worktree", "scratch/w12")
        self.task("gate", "pr/12", "review_gate", "pass", "--evidence", "src/a.c:1@0123456789")
        self.task("link", "pr/12", "review=run/agent-findings/r.md")
        with open(os.path.join(self.root, "pr", "12.md")) as f:
            front = f.read()[4:].split("\n---\n")[0]
        self.assertEqual(yaml.safe_load(front), task.parse_front(front))

    def test_backfill_is_a_stub(self):
        r = self.run_tool(TASK, "backfill", "2026-10-08")
        self.assertEqual(r.returncode, 2)
        self.assertIn("run/agent-sessions/ does not exist yet", r.stderr)

    # ---- review findings, 2026-10-08 ----

    def usage_error(self, *args, **kw):
        r = self.run_tool(TASK, *args, **kw)
        self.assertEqual(r.returncode, 2, f"task {args}: {r.stdout} {r.stderr}")
        self.assertTrue(r.stderr.startswith("task: "), r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def edit(self, tid, old, new):
        path = os.path.join(self.root, tid + ".md")
        with open(path, newline="") as f:
            text = f.read()
        self.assertIn(old, text)
        with open(path, "w", newline="") as f:
            f.write(text.replace(old, new))

    def append_line(self, tid, line):
        with open(os.path.join(self.root, tid + ".md"), "a", newline="") as f:
            f.write(line + "\n")

    def test_remote_or_ref_that_starts_with_a_dash_runs_nothing(self):
        mark = os.path.join(self.tmp, "PWNED")
        self.task("new", "job/r", "--title", "r")
        self.edit("job/r", "lease:\n  ref: null\n  sha: null",
                  "lease:\n  ref: refs/heads/x\n  sha: 0123456789012345678901234567890123456789")
        self.edit("job/r", "embargo: false\n", "embargo: false\nremote: " + json.dumps(f"--upload-pack=sh -c 'touch {mark}; exit 1'") + "\n")
        self.run_tool(TASK, "lease-check", "job/r", sender="reviewer@dsh")
        self.assertFalse(os.path.exists(mark), "git ran the upload-pack command from the remote key")
        self.usage_error("new", "job/r2", "--title", "r2", "--remote=--upload-pack=x")
        self.usage_error("claim", "job/r", "--ref=--upload-pack=x")
        self.usage_error("claim", "job/r", "--remote=--upload-pack=x")
        self.usage_error("lease-check", "job/r", "--remote=--upload-pack=x")
        self.assertFalse(os.path.exists(os.path.join(self.root, "job", "r2.md")))

    def test_hand_edited_flow_map_is_an_error_and_a_bare_gate_is_pending(self):
        self.task("new", "issue/9", "--title", "nine")
        path = os.path.join(self.root, "issue", "9.md")
        with open(path) as f:
            text = f.read()
        flow = re.sub(r"gates:\n(?:  .*\n)+", "gates:\n  review_gate: {state: pending}\n", text)
        with open(path, "w") as f:
            f.write(flow)
        self.usage_error("done", "issue/9")
        with open(path) as f:
            self.assertEqual(f.read(), flow)
        with open(path, "w") as f:
            f.write(re.sub(r"gates:\n(?:  .*\n)+", "gates:\n  review_gate: pass\n", text))
        self.assertIn("review_gate", self.refused("done", "issue/9").stderr)

    def test_spec_example_is_readable_by_the_tool(self):
        with open(os.path.join(os.path.dirname(TOOLS), "docs", "task-ledger.md")) as f:
            spec = f.read()
        block = spec.split("## 3. The task file")[1].split("\n    ---\n", 1)[1].split("\n\n`status`")[0]
        os.makedirs(os.path.join(self.root, "pr"))
        path = os.path.join(self.root, "pr", "588.md")
        with open(path, "w") as f:
            f.write("---\n" + "\n".join(l[4:] for l in block.split("\n")) + "\n")
        meta, body = task.read_task(path)
        self.assertEqual(meta["gates"]["review_gate"], {"state": "pending"})
        self.assertEqual(meta["lease"]["checked_at"], "2026-10-08T06:12:41Z")
        self.assertEqual(len(body["log"]), 5)
        self.assertTrue(all(task.inbox.LINE.match(l) for l in body["log"]))

    def test_a_timestamp_that_does_not_parse_is_skipped(self):
        self.task("new", "job/t", "--title", "t")
        self.task("claim", "job/t")
        self.append_line("job/t", '2026-10-09T00:00:00.5Z orchestrator@claude fact abcdef x {"task":"job/t"}')
        self.task("ready")
        self.task("reap")
        self.assertIn("claimed by orchestrator@claude", self.refused("claim", "job/t", sender="reviewer@dsh").stderr)

    def test_save_replaces_the_file_once_with_the_line_in_it(self):
        self.task("new", "job/a", "--title", "a")
        path = os.path.join(self.root, "job", "a.md")
        meta, body = task.read_task(path)
        line = '2026-10-08T00:00:00Z orchestrator@claude fact 0a0b0c atomic {"task":"job/a"}'
        seen, real = [], os.replace
        def replace(src, dst):
            with open(src) as f:
                seen.append(f.read())
            real(src, dst)
        with unittest.mock.patch.object(task.os, "replace", replace):
            task.save(path, meta, body, line)
        self.assertEqual(len(seen), 1)
        self.assertIn(line, seen[0])
        with open(path) as f:
            self.assertEqual(f.read(), seen[0])

    def test_control_characters_are_refused(self):
        self.task("new", "job/c", "--title", "c")
        self.assertEqual(self.run_tool(TASK, "note", "job/c", "a\rb").returncode, 1)
        self.assertEqual(self.run_tool(INBOX, "send", "fact", "a\tb").returncode, 1)
        self.assertEqual(self.run_tool(INBOX, "send", "fact", "a", "--json", '{"k":\r1}').returncode, 1)
        self.assertEqual(self.file_log("job/c"), self.bus_lines())
        self.append_line("job/c", '2026-10-08T00:00:00Z reviewer@dsh fact 0a0b0c a\rb {"task":"job/c"}')
        self.assertEqual(len(self.file_log("job/c")), 2)

    def test_new_with_a_bad_title_creates_nothing(self):
        for title in ("a\nb", "a\tb", "x" * 301):
            self.usage_error("new", "job/n", "--title", title)
        self.assertFalse(os.path.exists(os.path.join(self.root, "job", "n.md")))
        self.assertFalse(os.path.exists(self.bus))

    def test_errors_exit_2_with_one_line(self):
        self.task("new", "job/e", "--title", "e")
        self.usage_error("note", "job/e", "x", "--json", "{bad")
        self.usage_error("brief", "job/e", "--for", "implement", extra={"TASK_RULES_FILE": os.path.join(self.tmp, "none")})
        ro = os.path.join(self.tmp, "ro")
        os.mkdir(ro, 0o555)
        self.addCleanup(os.chmod, ro, 0o755)
        r = self.usage_error("note", "job/e", "y", extra={"INBOX_LOG": os.path.join(ro, "bus.log")})
        self.assertIn("the bus does not", r.stderr)
        if os.geteuid() != 0:
            path = os.path.join(self.root, "job", "e.md")
            os.chmod(path, 0)
            self.addCleanup(os.chmod, path, 0o644)
            self.usage_error("note", "job/e", "z")
            self.usage_error("show", "job/e")

    def test_a_session_claim_is_foreign_to_the_same_sender_without_it(self):
        self.task("new", "job/s", "--title", "s")
        self.task("claim", "job/s")
        self.task("next", "job/s", "step")
        for session in ("", "s2"):
            self.refused("claim", "job/s", extra={"TASK_SESSION": session})
            self.refused("handoff", "job/s", extra={"TASK_SESSION": session})
        meta = self.meta("job/s")
        self.assertEqual((meta["owner"], meta["session"]), ("orchestrator@claude", "s1"))
        self.assertIn("job/s", self.task("mine", extra={"TASK_SESSION": ""}).stdout)
        self.assertNotIn("job/s", self.task("mine", extra={"TASK_SESSION": "s2"}).stdout)
        self.task("claim", "job/s")

    def test_gate_names(self):
        self.task("new", "issue/g", "--title", "g")
        self.usage_error("gate", "issue/g", "review_gaet", "n/a", "--evidence", "because")
        self.task("gate", "issue/g", "x-bench", "n/a", "--evidence", "no bench")
        self.assertEqual(self.meta("issue/g")["gates"]["x-bench"], {"state": "n/a", "evidence": "no bench"})
        self.assertNotIn("review_gaet", self.meta("issue/g")["gates"])

    def test_number_like_strings_are_quoted(self):
        d = {f"k{i}": v for i, v in enumerate(("0x1f", "0X1F", "-0x1f", "0o17", "0b101", "0x_1"))}
        text = "\n".join(task.dump(d))
        for v in d.values():
            self.assertIn(f'"{v}"', text)
        self.assertEqual(task.parse_front(text), d)
        if yaml is not None:
            self.assertEqual(yaml.safe_load(text), d)

    def test_brief_log_heading_counts_every_hidden_line(self):
        self.task("new", "job/b", "--title", "b")
        with open(os.path.join(self.root, "job", "b.md"), "a") as f:
            for i in range(100):
                f.write(f'2026-10-08T00:00:00Z reviewer@dsh fact {i:06x} note {i} {{"task":"job/b"}}\n')
        for args in (("--short",), ()):
            out = self.task("brief", "job/b", *args).stdout
            self.assertIn("## Log (truncated: 81 older lines dropped)", out, args)

    def test_sender_needs_role_at_tool(self):
        self.usage_error("new", "job/x", "--title", "x", sender="noat")
        self.task("new", "job/x", "--title", "x")
        r = self.run_tool(INBOX, "send", "fact", "y", "--task", "job/x", sender="noat")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(len(self.file_log("job/x")), 1)

    def test_inbox_send_task_keeps_text_words(self):
        self.task("new", "job/i", "--title", "i")
        r = self.run_tool(INBOX, "send", "fact", "fix", "job/i", "--task", "job/i", "then", '{"k":1}', "--json", '{"k":1}')
        self.assertEqual(r.returncode, 0, r.stderr)
        body = task.inbox.parse(self.file_log("job/i")[-1])["body"]
        self.assertEqual(body, 'fix job/i then {"k":1} {"task":"job/i","k":1}')

    def test_negative_ttl_is_refused(self):
        self.task("new", "job/ttl", "--title", "t")
        self.usage_error("claim", "job/ttl", "--ttl", "-5")
        self.assertIsNone(self.meta("job/ttl")["owner"])

    # ---- mutations the suite missed ----

    def test_json_tail_cannot_move_a_line_to_another_task(self):
        self.task("new", "job/k", "--title", "k")
        self.task("note", "job/k", "x", "--json", '{"task":"job/other","pr":3}')
        tail = json.loads(self.bus_lines()[-1].split(" x ", 1)[1])
        self.assertEqual(tail, {"task": "job/k", "pr": 3})

    def test_relative_paths_refuse_dot_dot(self):
        self.task("new", "pr/14", "--title", "fourteen")
        self.refused("claim", "pr/14", "--worktree", "scratch/../w")
        self.refused("link", "pr/14", "worktree=scratch/../../w")
        self.refused("link", "pr/14", "review=../outside.md")
        self.assertEqual(self.meta("pr/14")["links"], {"pr": 14})

    def test_embargo(self):
        self.task("new", "job/em", "--title", "em", "--embargo")
        self.task("note", "job/em", "secret")
        path = os.path.join(self.root, "job", "em.md")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(len(self.file_log("job/em")), 2)
        self.assertFalse(os.path.exists(self.bus) and self.bus_lines())
        self.refused("brief", "job/em", sender="reviewer@dsh")
        self.refused("brief", "job/em", "--public")
        self.assertIn("secret", self.task("brief", "job/em").stdout)
        self.assertNotIn("job/em", self.task("ready").stdout)

    def test_who_ready_deps_and_done(self):
        self.task("new", "pr/13", "--title", "thirteen", "--dep", "tag/9")
        self.task("new", "tag/9", "--title", "nine")
        self.task("link", "pr/13", "review=run/r13.md")
        self.assertEqual(self.task("who", "pr", "13").stdout, "pr/13 open - -\n")
        self.assertEqual(self.task("who", "review", "run/r13.md").stdout, "pr/13 open - -\n")
        self.assertEqual(self.task("who", "pr", "12").stdout, "")
        self.assertNotIn("pr/13", self.task("ready").stdout)
        self.task("done", "tag/9")
        self.assertIn("pr/13 thirteen", self.task("ready").stdout)
        self.assertIn("is done", self.refused("claim", "tag/9").stderr)

    def test_handoff_refused_while_a_foreign_claim_is_live(self):
        self.task("new", "job/f", "--title", "f")
        self.task("claim", "job/f", sender="perf-runner@pi")
        self.task("next", "job/f", "step", sender="perf-runner@pi")
        self.assertIn("claimed by perf-runner@pi", self.refused("handoff", "job/f").stderr)
        self.assertEqual(self.meta("job/f")["status"], "claimed")

    def test_scrub_drops_private_keys(self):
        meta = {"owner": "orchestrator@claude.e0", "session": "s1", "remote": "/srv/r.git", "x-host": "h",
                "links": {"worktree": "scratch/w", "x-note": "n", "pr": 1, "reviews": ["run/a.md", "docs/b.md"]},
                "url": "run/private.md", "title": "t"}
        self.assertEqual(task.scrub(meta), {"owner": "orchestrator@claude", "links": {"pr": 1, "reviews": ["docs/b.md"]}, "title": "t"})
        self.task("new", "job/p2", "--title", "p2")
        self.task("claim", "job/p2", sender="orchestrator@claude.e0")
        public = self.task("brief", "job/p2", "--public", sender="orchestrator@claude.e0").stdout
        self.assertIn("owner: orchestrator@claude\n", public)
        self.assertNotIn("claude.e0", public)

    def test_next_refuses_bad_text(self):
        self.task("new", "job/nx", "--title", "nx")
        for text in ("", "a\nb\nc\nd", "## Log"):
            self.refused("next", "job/nx", text)
        self.assertEqual(task.read_task(os.path.join(self.root, "job", "nx.md"))[1]["next"], "")

    def test_ttl_counts_from_the_last_owner_line(self):
        meta = {"owner": "a@b", "ttl_min": 60, "claimed_at": "2026-10-08T00:00:00Z"}
        body = {"log": ['2026-10-08T00:50:00Z a@b fact 000001 x {"task":"job/x"}',
                        '2026-10-08T00:59:00Z c@d fact 000002 y {"task":"job/x"}']}
        self.assertEqual(task.ttl_left(meta, body, "2026-10-08T01:30:00Z"), 20)
        self.assertEqual(task.ttl_left(meta, {"log": []}, "2026-10-08T00:30:00Z"), 30)


if __name__ == "__main__":
    unittest.main()
