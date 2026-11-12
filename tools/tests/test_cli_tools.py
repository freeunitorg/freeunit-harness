"""Tests of the small command line tools: inbox, hosts, perf-bisect.sh, new-failures.sh, session-extract, clean-findings.py
and loop-invariant-scan.py, unitd-ctl and bench-engines.sh against fake unitd builds, plus the suites that make tools-test runs. Each test works in its own temporary directory. Nothing touches the network.

Run: python3 -m unittest discover -s tools/tests -v
"""
import importlib, json, os, shutil, subprocess, sys, tempfile, time, unittest

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
        return self.run_cmd("sh", HOSTS, *args, env={"LAB_ENV": path})

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

    # ---- POSIX sh entry points ----

    def test_entry_points_parse_as_posix_sh(self):
        # POSIX sh (docs/shell.md): parse every tracked #!/bin/sh script with sh, and with dash, busybox ash and
        # bash --posix when present. A missing shell is a skipped subtest, so the report shows it.
        shells = [("sh", ["sh", "-n"])]
        for name, cmd in (("dash", ["dash", "-n"]), ("busybox", ["busybox", "sh", "-n"]),
                          ("bash", ["bash", "--posix", "-n"])):
            if shutil.which(name):
                shells.append((name, cmd))
            else:
                with self.subTest(shell=name):
                    self.skipTest(f"{name} is not installed; scripts not parsed with it")
        root = os.path.dirname(TOOLS)
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
        scripts = []
        for path in out.decode().split("\0"):
            full = os.path.join(root, path)
            if path and os.path.isfile(full) and not os.path.islink(full):
                with open(full, "rb") as f:
                    if f.readline() == b"#!/bin/sh\n":
                        scripts.append(path)
        # The list comes from git: check that it holds the entry points, so an empty listing cannot pass.
        for s in ("unit-build", "test-isolated", "hosts", "doctor", "unitd-ctl", "ci-local", "perf-bisect.sh",
                  "new-failures.sh", "mirror-check.sh", "watch-run.sh", "check-style", "capget-fallback-smoke.sh",
                  "gates/run-gates", "perf-lab/scaling/lib.sh"):
            self.assertIn("tools/" + s, scripts)
        for path in scripts:
            # A shell and Python polyglot (check-public.sh) is shell only up to its "#--PY--" line.
            full = os.path.join(root, path)
            with open(full, "rb") as f:
                data = f.read()
            cut = data.find(b"\n#--PY--\n")
            if cut >= 0:
                full = os.path.join(self.tmp, os.path.basename(path))
                with open(full, "wb") as f:
                    f.write(data[:cut + 1])
            for name, cmd in shells:
                with self.subTest(shell=name, script=path):
                    r = subprocess.run(cmd + [full], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, f"{' '.join(cmd)} {path}: {r.stderr}")

    def test_new_failures_option_without_a_value(self):
        for opt in ("--base", "--repo", "--match", "--key-sed", "--ok-status"):
            try:
                r = self.run_cmd("sh", os.path.join(TOOLS, "new-failures.sh"), opt, timeout=5)
            except subprocess.TimeoutExpired:
                self.fail(f"new-failures.sh {opt} with no value did not exit in 5 s")
            self.assertEqual(r.returncode, 2, opt)
            self.assertIn(f"{opt} needs a value", r.stderr)

    @unittest.skipUnless(shutil.which("dash") and shutil.which("git"), "needs dash and git")
    def test_new_failures_prints_user_text_as_given(self):
        # dash's echo expands backslashes: an --match or --ok-status of '\n' became a line break in the message.
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
        env = {k: v for k, v in self.env.items() if not k.startswith("GIT_")}
        for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "base"]):
            subprocess.run(git + cmd, check=True, capture_output=True, env=env)
        script = os.path.join(TOOLS, "new-failures.sh")
        cases = ((["--match", r"\n"], "exit 1", r"matched no '\n' lines but exited 1"),
                 (["--ok-status", r"0,\n"], "exit 2", r"(not in --ok-status=0,\n)"))
        for opts, checker, want in cases:
            with self.subTest(opts=opts):
                r = subprocess.run(["dash", script, "--base", "HEAD", "--repo", repo] + opts + ["--", "sh", "-c", checker],
                                   capture_output=True, text=True, env=env, cwd=self.tmp, timeout=30,
                                   stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                self.assertIn(want, r.stderr)

    # ---- perf-bisect.sh ----

    def test_perf_bisect_option_without_a_value(self):
        for opt in ("--threshold", "--metric", "--conns", "--workload", "--null-limit", "--repo", "--log", "--max-steps"):
            try:
                r = self.run_cmd("sh", os.path.join(TOOLS, "perf-bisect.sh"), opt, timeout=5)
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

    def test_session_extract_cwd_class_puts_a_known_checkout_before_scratch_prefixes(self):
        # A checkout under /tmp/ (TMPDIR on a CI runner) or a scratchpad is still the server checkout.
        pad = os.path.join("/srv/agent", "scratchpad")
        for root in ("/tmp/ci-run/unit", os.path.join(pad, "unit")):
            common = self.load_common({"LAB_ENV": os.devnull, "UNIT_SRC": root})
            self.assertEqual(common.cwd_class(root), "unit", root)
            self.assertEqual(common.cwd_class(os.path.join(root, "src", "test")), "unit", root)
            self.assertEqual(common.cwd_class(os.path.join(root, "x", "scratchpad", "w")), "scratch", root)
            self.assertEqual(common.cwd_class(os.path.join(pad, "other")), "scratch", root)
            self.assertEqual(common.cwd_class("/srv/elsewhere"), "other", root)

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

# A stand-in for unitd: it writes its pid to the --pid file and forks one worker, as the real main process does.
# The worker stays in the process group of the main process; unitd-ctl starts the main in a new session. With
# FAKE_IGNORE_TERM set, both ignore SIGTERM, so only SIGKILL ends them. alarm() ends each process after 60 s if a
# test fails to stop it.
FAKE_UNITD_C = r"""
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    const char *pidfile = NULL;
    FILE *f;
    int i;

    for (i = 1; i + 1 < argc; i++) {
        if (strcmp(argv[i], "--pid") == 0) {
            pidfile = argv[i + 1];
        }
    }
    if (getenv("FAKE_IGNORE_TERM") != NULL) {
        signal(SIGTERM, SIG_IGN);
    }
    if (fork() == 0) {
        alarm(60);
        for (;;) {
            pause();
        }
    }
    alarm(60);
    if (pidfile != NULL) {
        f = fopen(pidfile, "w");
        if (f == NULL) {
            return 1;
        }
        fprintf(f, "%d\n", (int) getpid());
        fclose(f);
    }
    for (;;) {
        pause();
    }
}
"""


class UnitdCtlTest(unittest.TestCase):
    """unitd-ctl start and stop under each POSIX shell, against a fake unitd in its own process group."""

    @classmethod
    def setUpClass(cls):
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cc:
            raise unittest.SkipTest("no C compiler for the fake unitd")
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="unitd-ctl-"))
        src = os.path.join(cls.tmp, "fake.c")
        with open(src, "w") as f:
            f.write(FAKE_UNITD_C)
        cls.build = os.path.join(cls.tmp, "build")
        os.makedirs(os.path.join(cls.build, "sbin"))
        cls.unitd = os.path.join(cls.build, "sbin", "unitd")
        subprocess.run([cc, "-o", cls.unitd, src], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def members(self, pgid):
        """The live, non-zombie pids of group PGID that run our fake unitd."""
        pids = []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/stat") as f:
                    fields = f.read().rsplit(")", 1)[1].split()
                exe = os.readlink(f"/proc/{d}/exe")
            except OSError:
                continue
            if fields[0] != "Z" and int(fields[2]) == pgid and exe == self.unitd:
                pids.append(int(d))
        return pids

    def kill_fakes(self, pgid):
        # Only the processes that run our own fake binary; never the group as a whole.
        for pid in self.members(pgid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass

    def ctl(self, shell, cmd, env):
        return subprocess.run(shell + [os.path.join(TOOLS, "unitd-ctl"), cmd], capture_output=True, text=True,
                              env=env, stdin=subprocess.DEVNULL, timeout=30)

    def test_stop_ends_the_group(self):
        shells = [("sh", ["sh"])]
        for name, cmd in (("dash", ["dash"]), ("busybox", ["busybox", "sh"]), ("bash", ["bash", "--posix"])):
            if shutil.which(name):
                shells.append((name, cmd))
            else:
                with self.subTest(shell=name):
                    self.skipTest(f"{name} is not installed")
        for name, shell in shells:
            for ignore_term, grace in ((False, 5), (True, 1)):
                with self.subTest(shell=name, ignore_term=ignore_term):
                    run_dir = tempfile.mkdtemp(dir=self.tmp)
                    env = dict(os.environ, RUN_DIR=run_dir, BUILD_DIR=self.build, CONTROL="ctl.sock",
                               STOP_GRACE=str(grace), START_TIMEOUT="5")
                    env.pop("FAKE_IGNORE_TERM", None)
                    if ignore_term:
                        env["FAKE_IGNORE_TERM"] = "1"
                    r = self.ctl(shell, "start", env)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    with open(os.path.join(run_dir, "unitd.pgid")) as f:
                        pgid = int(f.read())
                    self.addCleanup(self.kill_fakes, pgid)
                    self.assertEqual(len(self.members(pgid)), 2, "the fake and its worker")
                    t0 = time.monotonic()
                    r = self.ctl(shell, "stop", env)
                    took = time.monotonic() - t0
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertEqual(self.members(pgid), [], r.stdout + r.stderr)
                    # SIGTERM ends the group inside the grace period; SIGKILL follows the grace period.
                    self.assertLess(took, grace if not ignore_term else grace + 3, r.stdout)
                    self.assertIn("==> stopped", r.stdout)
                    self.assertFalse(os.path.exists(os.path.join(run_dir, "unitd.pgid")))


BENCH_FAKE_UNITD_C = r"""
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    static const char *names[] = { "unit: controller", "unit: router" };
    const char *pidfile = NULL, *control = NULL;
    struct sockaddr_un sa;
    FILE *f;
    int i, s;

    alarm(60);
    if (argc > 1 && strcmp(argv[1], "--fake-child") == 0) {
        for (;;) {
            pause();
        }
    }
    for (i = 1; i + 1 < argc; i++) {
        if (strcmp(argv[i], "--pid") == 0) {
            pidfile = argv[i + 1];
        }
        if (strcmp(argv[i], "--control") == 0 && strncmp(argv[i + 1], "unix:", 5) == 0) {
            control = argv[i + 1] + 5;
        }
    }
    if (pidfile == NULL || control == NULL || strlen(control) >= sizeof(sa.sun_path)) {
        return 1;
    }
    fprintf(stderr, "using \"epoll\" event engine\n");
    for (i = 0; i < 2; i++) {
        if (fork() == 0) {
            execl("/proc/self/exe", names[i], "--fake-child", (char *) NULL);
            _exit(1);
        }
    }
    f = fopen(pidfile, "w");
    if (f == NULL) {
        return 1;
    }
    fprintf(f, "%d\n", (int) getpid());
    fclose(f);
    memset(&sa, 0, sizeof(sa));
    sa.sun_family = AF_UNIX;
    strcpy(sa.sun_path, control);
    s = socket(AF_UNIX, SOCK_STREAM, 0);
    if (s < 0 || bind(s, (struct sockaddr *) &sa, sizeof(sa)) != 0 || listen(s, 1) != 0) {
        return 1;
    }
    for (;;) {
        pause();
    }
}
"""

# Stubs for bench-engines.sh. curl answers every config PUT with success and records the listener ports with the
# main pid of the run; ss reports a port as listening while that pid is alive. oha prints a fixed result. taskset
# runs the command unpinned, so the test does not depend on the CPU count.
BENCH_STUBS = {
    "oha": r"""#!/bin/sh
printf '%s\n' '{"summary":{"requestsPerSec":1000.0,"successRate":1.0},"latencyPercentiles":{"p50":0.001,"p99":0.002},"statusCodeDistribution":{"200":10}}'
""",
    "curl": r"""#!/bin/sh
sock="" data=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --unix-socket) sock=$2; shift ;;
        -d) data=$2; shift ;;
    esac
    shift
done
pid=$(cat "${sock%/*}/unit.pid")
for port in $(printf '%s\n' "$data" | grep -o '"\*:[0-9]*"' | tr -dc '0-9\n'); do
    printf '%s\n' "$pid" > "$FAKE_STATE/port-$port"
done
printf '%s\n' '{"success": "Reconfiguration done."}'
""",
    "ss": r"""#!/bin/sh
for a in "$@"; do
    case "$a" in "sport = :"*) port=${a#sport = :} ;; esac
done
pid=$(cat "$FAKE_STATE/port-$port" 2>/dev/null) || exit 0
st=$(sed 's/.*) //' "/proc/$pid/stat" 2>/dev/null | cut -c1)
case "$st" in ''|Z) ;; *) printf 'LISTEN 0 128 *:%s *:*\n' "$port" ;; esac
""",
    "taskset": r"""#!/bin/sh
case "$1" in
    -c) shift 2; exec "$@" ;;
    -p|-pc) exit 0 ;;
esac
exit 1
""",
}

# Tools that bench-engines.sh needs and that some busybox builds lack. Alpine's busybox has flock, comm and seq;
# getconf comes from musl-utils there. Ubuntu's busybox lacks flock, comm and getconf, so the test takes them from
# the host. ps, pgrep and pkill never come from the host, so procps cannot leak into the busybox run.
BUSYBOX_HOST_FALLBACK = ("flock", "comm", "getconf", "setsid", "seq")


class BenchEnginesRunTest(unittest.TestCase):
    """bench-engines.sh runs one A/A round against two fake unitd builds under each POSIX shell.

    The busybox case puts a directory of busybox applets first, and only that directory and the stubs on PATH,
    so the script cannot reach procps ps or pgrep. Set HARNESS_BUSYBOX to test another busybox binary, such as
    Alpine's busybox.static. A second test runs the /proc helpers of the script directly. Not covered: a real unitd,
    real oha, ab, the php scenario, real CPU pinning, and the crash recovery paths (an orphaned group found by its
    session, a run dir found by command line), whose helpers proc_sid and cmdline_has are tested alone.
    """

    @classmethod
    def setUpClass(cls):
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cc:
            raise unittest.SkipTest("no C compiler for the fake unitd")
        # The control socket path, <tmp>/tmpXXXXXXXX/rb/A-preflight/control.sock.tmp, must fit AF_UNIX sun_path
        # (108 bytes). Fall back to /tmp when TMPDIR is too deep.
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="be-"))
        if len(cls.tmp) + 50 >= 108:
            shutil.rmtree(cls.tmp)
            cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="be-", dir="/tmp"))
            if len(cls.tmp) + 50 >= 108:
                shutil.rmtree(cls.tmp)
                raise unittest.SkipTest("no temporary directory short enough for the control socket path")
        src = os.path.join(cls.tmp, "fake.c")
        with open(src, "w") as f:
            f.write(BENCH_FAKE_UNITD_C)
        cls.builds = {}
        for label in ("A", "B"):
            build = os.path.join(cls.tmp, label)
            os.makedirs(os.path.join(build, "sbin"))
            subprocess.run([cc, "-o", os.path.join(build, "sbin", "unitd"), src], check=True)
            cls.builds[label] = build
        cls.stubs = os.path.join(cls.tmp, "stubs")
        os.makedirs(cls.stubs)
        for name, body in BENCH_STUBS.items():
            path = os.path.join(cls.stubs, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fakes(self):
        """The live, non-zombie pids that run one of the fake unitd builds."""
        exes = {os.path.join(b, "sbin", "unitd") for b in self.builds.values()}
        pids = []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/stat") as f:
                    state = f.read().rsplit(")", 1)[1].split()[0]
                exe = os.readlink(f"/proc/{d}/exe")
            except (OSError, IndexError):
                continue
            if state != "Z" and exe in exes:
                pids.append(int(d))
        return pids

    def kill_fakes(self):
        # Only the processes that run the fake binaries.
        for pid in self.fakes():
            try:
                os.kill(pid, 9)
            except OSError:
                pass

    def busybox_path(self, busybox):
        """A directory of busybox applets, plus the host tools in BUSYBOX_HOST_FALLBACK that this busybox lacks."""
        bindir = os.path.join(self.tmp, "bb")
        if os.path.isdir(bindir):
            return bindir
        os.makedirs(bindir)
        applets = subprocess.run([busybox, "--list"], capture_output=True, text=True, check=True).stdout.split()
        for name in applets:
            os.symlink(busybox, os.path.join(bindir, name))
        for name in BUSYBOX_HOST_FALLBACK:
            host = shutil.which(name)
            if name not in applets and host:
                os.symlink(host, os.path.join(bindir, name))
        # The JSON parse and the summary need python3; a link to the real binary, not to a venv wrapper.
        os.symlink(os.path.realpath(sys.executable), os.path.join(bindir, "python3"))
        for name in ("ps", "pgrep", "pkill"):
            path = os.path.join(bindir, name)
            if os.path.lexists(path):
                self.assertEqual(os.readlink(path), busybox, name)
        return bindir

    def run_bench(self, shell, path_dirs):
        work = tempfile.mkdtemp(dir=self.tmp)
        state = os.path.join(work, "state")
        os.makedirs(state)
        results = os.path.join(work, "results.txt")
        runbase = os.path.join(work, "rb")
        # Two CPUs that this process may use. busybox ash can run its own taskset applet in place of the stub
        # on PATH, and the default lists (0-3 and 4-7) need 8 CPUs; the two lists must not overlap.
        cpus = sorted(os.sched_getaffinity(0))
        if len(cpus) < 2:
            self.skipTest("bench-engines.sh needs two CPUs")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("N_", "OHA", "PHPAPP", "ENGINE_", "EXPECT_", "SERVER_CORES", "LOAD_CORES",
                                    "CURL_MAXTIME", "HARNESS_SCRATCH"))}
        env.update(PATH=os.pathsep.join(path_dirs), RUNBASE=runbase, RESULTS=results, FAKE_STATE=state,
                   SKIP_AB="1", ALLOW_SAME="1", MAX_FAILED="0", HOME=work, CARGO_HOME=work,
                   SERVER_CORES=str(cpus[0]), LOAD_CORES=str(cpus[1]))
        for n in ("N_HIGH", "N_LOW", "N_CLOSE", "N_PROXY", "N_PHP"):
            env[n] = "10"
        self.addCleanup(self.kill_fakes)
        r = subprocess.run(shell + [os.path.join(TOOLS, "bench-engines.sh"), "A", self.builds["A"], "B",
                                    self.builds["B"], "1", "--allow-same"],
                           capture_output=True, text=True, env=env, cwd=work, stdin=subprocess.DEVNULL, timeout=120)
        rows = []
        if os.path.exists(results):
            with open(results) as f:
                rows = sorted(line.rstrip("\n") for line in f if " oha rps=" in line)
        pgids = []
        for d in sorted(os.listdir(runbase)) if os.path.isdir(runbase) else []:
            pf = os.path.join(runbase, d, "unitd.pgid")
            if os.path.exists(pf):
                with open(pf) as f:
                    pgids.append(int(f.read()))
        return r, rows, pgids

    def group_members(self, pgid):
        out = []
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    with open(f"/proc/{d}/stat") as f:
                        fields = f.read().rsplit(")", 1)[1].split()
                except (OSError, IndexError):
                    continue
                if fields[0] != "Z" and int(fields[2]) == pgid:
                    out.append(int(d))
        return out

    def shells(self):
        """(name, argv, PATH dirs) per shell; a missing shell is a skipped subtest."""
        host_path = [self.stubs] + os.environ.get("PATH", "").split(os.pathsep)
        shells = [("sh", ["sh"], host_path)]
        for name, cmd in (("dash", ["dash"]), ("bash", ["bash", "--posix"])):
            if shutil.which(name):
                shells.append((name, cmd, host_path))
            else:
                with self.subTest(shell=name):
                    self.skipTest(f"{name} is not installed")
        busybox = os.environ.get("HARNESS_BUSYBOX") or shutil.which("busybox")
        if busybox:
            busybox = os.path.realpath(busybox)
            bindir = self.busybox_path(busybox)
            shells.append(("busybox", [os.path.join(bindir, "sh")], [self.stubs, bindir]))
        else:
            with self.subTest(shell="busybox"):
                self.skipTest("busybox is not installed")
        return shells

    def test_proc_helpers_see_a_newline_in_the_command_name(self):
        # The command name can hold a newline, so /proc/PID/stat is then two lines. A reader of the first line only
        # missed the process, and its group looked empty: no signal, and a green leak scan.
        with open(os.path.join(TOOLS, "bench-engines.sh")) as f:
            text = f.read()
        start = min(i for i in (text.find("\nproc_state() {"), text.find("\n# Process ids")) if i >= 0)
        fns = os.path.join(self.tmp, "proc-helpers.sh")
        with open(fns, "w") as f:
            f.write(text[start:text.index("\nreap_child() {")])
        marker = "bench-engines-helper-test-" + str(os.getpid())
        child = subprocess.Popen([sys.executable, "-c", "import sys, time\nopen('/proc/self/comm', 'w').write("
                                  "'nl\\nx) S 1 77 77')\ntime.sleep(30)", marker], start_new_session=True)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        # A fake unitd image, for unitd_pids; "--fake-child" makes it wait for a signal.
        fake = subprocess.Popen([os.path.join(self.builds["A"], "sbin", "unitd"), "--fake-child", marker])
        self.addCleanup(fake.wait)
        self.addCleanup(fake.kill)
        for _ in range(100):
            with open(f"/proc/{child.pid}/comm") as f:
                if f.read().startswith("nl\n"):
                    break
            time.sleep(0.05)
        p = str(child.pid)
        script = (f'. "{fns}"; printf "group=%s\\n" "$(group_pids {p} | tr "\\n" " ")"; '
                  f'printf "child=%s\\n" "$(child_pids {os.getpid()} | tr "\\n" " ")"; '
                  f'printf "unitd=%s\\n" "$(unitd_pids | tr "\\n" " ")"; '
                  f'for q in {p} {fake.pid}; do cmdline_has $q {marker} && printf "has=%s\\n" $q; done; '
                  f'cmdline_has {p} no-such-{marker} && printf "has=wrong\\n"; '
                  f'printf "ids=%s %s %s\\n" "$(proc_pgid {p})" "$(proc_sid {p})" "$(proc_state {p})"; '
                  'for bad in "" 12x; do v=$(proc_pgid "$bad"); printf "bad=[%s] %s\\n" "$v" "$?"; done')
        for name, shell, path_dirs in self.shells():
            with self.subTest(shell=name):
                r = subprocess.run(shell + ["-c", script], capture_output=True, text=True, timeout=30,
                                   env=dict(os.environ, PATH=os.pathsep.join(path_dirs)), stdin=subprocess.DEVNULL)
                lines = r.stdout.splitlines()
                out = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("has="))
                self.assertEqual(out.get("group", "").split(), [p], r.stdout + r.stderr)
                self.assertIn(p, out.get("child", "").split(), r.stdout)
                self.assertIn(str(fake.pid), out.get("unitd", "").split(), r.stdout)
                self.assertNotIn(p, out.get("unitd", "").split(), r.stdout)
                # The marker is the last argument: a reader that stops at the first NUL does not see it.
                self.assertEqual([l for l in r.stdout.splitlines() if l.startswith("has=")],
                                 [f"has={p}", f"has={fake.pid}"], r.stdout)
                self.assertRegex(out.get("ids", ""), rf"^{p} {p} [RS]$", r.stdout)
                # An empty pid must not read /proc//stat, which is /proc/stat.
                self.assertEqual(r.stdout.count("bad=[] 1"), 2, r.stdout)

    def test_one_round_under_each_shell(self):
        expect = sorted(f"{label} {scen} oha rps=1000 p50ms=1.000 p99ms=2.000 failed=0 cpu_us_per_req=0.00 "
                        f"engine=epoll" for label in ("A", "B")
                        for scen in ("ret200_ka_hi", "ret200_ka_lo", "ret200_close", "proxy_ka"))
        for name, shell, path_dirs in self.shells():
            with self.subTest(shell=name):
                r, rows, pgids = self.run_bench(shell, path_dirs)
                self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
                self.assertEqual(rows, expect, r.stderr[-3000:])
                # Two preflight starts and one start per build in the round, each with its own group.
                self.assertEqual(len(pgids), 4, pgids)
                for pgid in pgids:
                    self.assertEqual(self.group_members(pgid), [], f"group {pgid} survived")
                self.assertEqual(self.fakes(), [], "a fake unitd survived")


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
