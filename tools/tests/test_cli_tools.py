"""Tests of the small command line tools: inbox, hosts, perf-bisect.sh, session-extract, clean-findings.py
and loop-invariant-scan.py, plus the suites that make tools-test runs. Each test works in its own temporary directory. Nothing touches the network.

Run: python3 -m unittest discover -s tools/tests -v
"""
import importlib, json, os, shutil, subprocess, sys, tempfile, unittest

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX, HOSTS = os.path.join(TOOLS, "inbox"), os.path.join(TOOLS, "hosts")
EXTRACT = os.path.join(TOOLS, "session-extract")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cli-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bus = os.path.join(self.tmp, "bus.log")
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("INBOX_", "TASK_", "LAB_", "UNIT_")) and k != "CLAUDE_CODE_SESSION_ID"}
        self.env.update(INBOX_LOG=self.bus, INBOX_SENDER="tester@sh")

    def run_cmd(self, *cmd, env=None, timeout=20):
        return subprocess.run(list(cmd), capture_output=True, text=True, env=dict(self.env, **(env or {})),
                              cwd=self.tmp, timeout=timeout, stdin=subprocess.DEVNULL)

    def inbox(self, *args):
        return self.run_cmd(sys.executable, INBOX, *args)

    # ---- inbox ----

    def test_inbox_refuses_usage_errors_with_exit_2(self):
        for args in (["send", "fact"], ["send", "fact", "  "], ["send", "fact", "hi", "--json"], ["send", "fact", "hi", "--task"],
                     ["send"], ["ack"], ["since"], ["bogus"], ["tail", "x"], ["tail", "5", "6"]):
            r = self.inbox(*args)
            self.assertEqual(r.returncode, 2, f"{args}: {r.stdout} {r.stderr}")
            self.assertTrue(r.stderr.startswith("inbox: "), r.stderr)
            self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(os.path.exists(self.bus))

    def test_inbox_valid_commands_are_unchanged(self):
        mid = self.inbox("send", "fact", "one", "two", "--json", '{"pr":1}').stdout.strip()
        self.assertRegex(mid, r"^[0-9a-f]{6}$")
        self.assertEqual(self.inbox("ack", mid).returncode, 0)
        with open(self.bus) as f:
            lines = f.read().splitlines()
        self.assertTrue(lines[0].endswith(f' tester@sh fact {mid} one two {{"pr":1}}'), lines[0])
        self.assertTrue(lines[1].endswith(f' ack {{"re": "{mid}"}}'), lines[1])
        self.assertEqual(self.inbox("tail").stdout.splitlines(), lines)
        self.assertEqual(self.inbox("tail", "1").stdout.splitlines(), lines[1:])
        self.assertEqual(self.inbox("since", mid).stdout.splitlines(), lines[1:])
        self.assertEqual(self.inbox().stdout.splitlines(), lines)

    def test_inbox_warns_once_per_bad_line(self):
        good = "2026-10-08T00:00:00Z tester@sh fact abcdef fine"
        legacy = "2026-10-08T00:00:01Z someone: an old line"
        with open(self.bus, "w") as f:
            f.write(f"{good}\nnot a bus line\n\n{legacy}\nanother bad one\n")
        r = self.inbox("tail")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines(), [good, legacy])
        self.assertEqual(r.stderr.splitlines(), [f"inbox: {self.bus}:2: not a bus line, skipped",
                                                 f"inbox: {self.bus}:5: not a bus line, skipped"])
        r = self.inbox("since", "2026-10-08T00:00:00Z")
        self.assertEqual(r.stdout.splitlines(), [good, legacy])
        self.assertEqual(len(r.stderr.splitlines()), 2)

    # ---- hosts ----

    def hosts(self, *args, lab=""):
        path = os.path.join(self.tmp, "lab.env")
        with open(path, "w") as f:
            f.write(lab)
        return self.run_cmd("bash", HOSTS, *args, env={"LAB_ENV": path})

    def test_hosts_resolves_bot_and_strips_quotes(self):
        lab = "PERF_HOST=\"bench-a\"\nBOT_HOST='bot-a'\nINFER_HOST=gpu-a  # a comment\n"
        self.assertEqual(self.hosts("ssh", "bot", lab=lab).stdout, "bot-a\n")
        self.assertEqual(self.hosts("ssh", "lab", lab=lab).stdout, "bench-a\n")
        self.assertEqual(self.hosts("ssh", "infer", lab=lab).stdout, "gpu-a\n")
        self.assertIn("bot      bot-a", self.hosts("list", lab=lab).stdout)

    def test_hosts_reverse_exit_status(self):
        lab = "PERF_HOST=bench-a\nBUILD_HOST=build-a\n"
        r = self.hosts("reverse", "build-a", lab=lab)
        self.assertEqual((r.returncode, r.stdout), (0, "build\n"))
        r = self.hosts("reverse", "bench-a", lab=lab)
        self.assertEqual((r.returncode, r.stdout), (0, "lab\n"))
        self.assertEqual(self.hosts("reverse", "nowhere", lab=lab).returncode, 1)

    def test_hosts_build_never_falls_back_to_the_bench_box(self):
        r = self.hosts("ssh", "build", lab="PERF_HOST=bench-a\n")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("BUILD_HOST", r.stderr)
        r = self.hosts("check", lab="PERF_HOST=bench-a\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("PROBLEM build", r.stdout)
        self.assertEqual(self.hosts("ssh", "build", lab="PERF_HOST=bench-a\nBUILD_HOST=build-a\n").stdout, "build-a\n")
        self.assertEqual(self.hosts("ssh", "some-literal", lab="PERF_HOST=bench-a\n").returncode, 0)

    # ---- perf-bisect.sh ----

    def test_perf_bisect_option_without_a_value(self):
        for opt in ("--threshold", "--metric", "--conns", "--workload", "--null-limit", "--repo", "--log", "--max-steps"):
            try:
                r = self.run_cmd("bash", os.path.join(TOOLS, "perf-bisect.sh"), opt, timeout=5)
            except subprocess.TimeoutExpired:
                self.fail(f"perf-bisect.sh {opt} with no value did not exit in 5 s")
            self.assertEqual(r.returncode, 2, opt)
            self.assertIn(f"{opt} needs a value", r.stderr)

    # ---- session-extract ----

    def load_common(self, env):
        old = {k: os.environ.get(k) for k in ("UNIT_SRC", "LAB_ENV")}
        try:
            for k in old: os.environ.pop(k, None)
            os.environ.update(env)
            sys.path.insert(0, EXTRACT)
            sys.modules.pop("common", None)
            return importlib.import_module("common")
        finally:
            sys.path.remove(EXTRACT)
            sys.modules.pop("common", None)
            for k, v in old.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v

    def test_session_extract_iso_is_utc_with_z(self):
        common = self.load_common({"LAB_ENV": os.devnull})
        for value, want in ((1760000000, "2025-10-09T08:53:20Z"), (1760000000123, "2025-10-09T08:53:20Z"),
                            ("2026-10-09T10:00:00.123456+02:00", "2026-10-09T08:00:00Z"), ("2026-10-09T10:00:00.5Z", "2026-10-09T10:00:00Z"),
                            ("2026-10-09 10:00:00", "2026-10-09T10:00:00Z"), ("2026-10-09T10:00:00Z", "2026-10-09T10:00:00Z")):
            self.assertEqual(common.iso(value), want, value)
        self.assertIsNone(common.iso(None))

    def test_session_extract_finds_the_checkout_from_lab_env(self):
        repo = os.path.join(self.tmp, "my-unit")
        os.makedirs(repo)
        lab = os.path.join(self.tmp, "lab.env")
        with open(lab, "w") as f:
            f.write(f'UNIT_REPO="{repo}"\n')
        self.assertIn(repo, self.load_common({"LAB_ENV": lab}).UNITS)
        self.assertEqual(self.load_common({"LAB_ENV": lab, "UNIT_SRC": self.tmp}).UNITS, [self.tmp])
        home = os.path.join(self.tmp, "home")
        project = os.path.join(home, ".claude", "projects", "-" + repo.strip("/").replace("/", "-"))
        os.makedirs(project)
        with open(os.path.join(project, "abc123.jsonl"), "w") as f:
            f.write(json.dumps({"type": "assistant", "cwd": repo, "timestamp": "2026-10-09T10:00:00.250Z",
                                "message": {"id": "m1", "model": "x", "usage": {"input_tokens": 3, "output_tokens": 4}, "content": []}}) + "\n")
        r = self.run_cmd(sys.executable, os.path.join(EXTRACT, "claude.py"), env={"LAB_ENV": lab, "HOME": home})
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(r.stdout)
        self.assertEqual((rec["session"], rec["cwd"], rec["start"], rec["tokens"]["out"]), ("abc123", "unit", "2026-10-09T10:00:00Z", 4))

    # ---- clean-findings.py, loop-invariant-scan.py ----

    def test_clean_findings_needs_its_directory(self):
        os.makedirs(os.path.join(self.tmp, "tools"))
        script = os.path.join(self.tmp, "tools", "clean-findings.py")
        shutil.copy(os.path.join(TOOLS, "clean-findings.py"), script)
        r = self.run_cmd(sys.executable, script)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no directory", r.stderr)
        os.makedirs(os.path.join(self.tmp, "run", "agent-findings"))
        self.assertEqual(self.run_cmd(sys.executable, script).returncode, 0)
        self.assertEqual(self.run_cmd(sys.executable, script, "--bogus").returncode, 2)

    def test_loop_invariant_scan_usage_and_ast_grep_check(self):
        script = os.path.join(TOOLS, "loop-invariant-scan.py")
        r = self.run_cmd(sys.executable, script)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage:", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        r = self.run_cmd(sys.executable, script, "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("usage:", r.stdout)
        empty = os.path.join(self.tmp, "empty-bin")
        os.makedirs(empty)
        scope = os.path.join(self.tmp, "scope.txt")
        open(scope, "w").close()
        r = self.run_cmd(sys.executable, script, scope, env={"PATH": empty})
        self.assertEqual(r.returncode, 2)
        self.assertIn("ast-grep", r.stderr)


class MakeToolsTestTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("make"), "make is not installed")
    def test_tools_test_runs_every_suite(self):
        # Run make tools-test with a stub python3 first in PATH. The stub records its arguments and exits 0,
        # so a recipe that only prints a command, or names a suite in a comment, records nothing.
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="make-test-"))
        self.addCleanup(shutil.rmtree, tmp)
        stub, log = os.path.join(tmp, "python3"), os.path.join(tmp, "argv.log")
        with open(stub, "w") as f:
            f.write('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$PYTHON3_ARGV_LOG"\n')
        os.chmod(stub, 0o755)
        env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
        env.update(PATH=tmp + os.pathsep + os.environ.get("PATH", ""), PYTHON3_ARGV_LOG=log)
        r = subprocess.run(["make", "-s", "tools-test"], capture_output=True, text=True, env=env,
                           cwd=os.path.dirname(TOOLS), stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(log) as f:
            runs = f.read().splitlines()
        for suite in ("-m unittest discover -s tools/tests -v", "tools/perf-lab/test_perf_compare.py",
                      "-m pytest -q tools/model/tests"):
            self.assertIn(suite, runs, f"make tools-test does not run python3 {suite}")


if __name__ == "__main__":
    unittest.main()
