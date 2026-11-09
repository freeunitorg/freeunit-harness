"""Tests of the router-only perf lab workloads (rx0, rx1, rx32, drupal-ci), of the
Python workloads (py-*) and of the per-side configure keys CONFIGURE_ARGS_A and CONFIGURE_ARGS_B in tools/perf-job.sh.

Nothing touches the network: ssh, rsync and docker are stubs on PATH that record the call
and fail. Run: python3 -m unittest discover -s tools/tests -v
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile, unittest

sys.dont_write_bytecode = True
TOOLS = os.environ.get("LAB_ENV_TEST_TOOLS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(TOOLS, "perf-lab")
PERF_JOB = os.path.join(TOOLS, "perf-job.sh")
TEMPLATE = os.path.join(LAB, "lab.env.example")
CONF = os.path.join(LAB, "conf")
RX_WORKLOADS = ("rx0", "rx1", "rx32", "drupal-ci")
JIT_JOBS = sorted(glob.glob(os.path.join(LAB, "jobs", "23-pcre2-jit-*.job")))
PY_WORKLOADS = ("py-wsgi-len", "py-wsgi-stream", "py-wsgi-threads", "py-asgi-len", "py-asgi-stream", "py-fastapi")
PY_JOBS = sorted(glob.glob(os.path.join(LAB, "jobs", "py1-*.job")))


def uri_patterns(conf):
    out = []
    for step in conf["routes"]["rx"]:
        u = step.get("match", {}).get("uri")
        if u is None:
            continue
        out.extend(u if isinstance(u, list) else [u])
    return out


def is_regex(p):
    return p.lstrip("!").startswith("~")


def py_regex(p):
    """A Unit regex (without ! and ~) as a Python regex: POSIX classes become ranges."""
    p = p.lstrip("!")[1:]
    return p.replace("[[:alnum:]_.-]", "[A-Za-z0-9_.-]").replace("[[:alnum:]_-]", "[A-Za-z0-9_-]")


class PerfLabWorkloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="perf-lab-wl-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.lab = os.path.join(self.tmp, "lab.env")
        shutil.copy(TEMPLATE, self.lab)
        self.stubs = os.path.join(self.tmp, "stubs")
        self.calls = os.path.join(self.tmp, "remote-calls")
        os.mkdir(self.stubs)
        for name in ("ssh", "rsync", "docker"):
            path = os.path.join(self.stubs, name)
            with open(path, "w") as f:
                f.write(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\nexit 1\n')
            os.chmod(path, 0o755)
        self.results = os.path.join(self.tmp, "results")
        self.env = {k: v for k, v in os.environ.items()
                    if not k.endswith("_HOST") and not k.startswith(("LAB_", "UNIT_", "PERF_"))}
        self.env.update(LAB_ENV=self.lab, PATH=self.stubs + os.pathsep + os.environ["PATH"],
                        TMPDIR=self.tmp, LAB_RESULTS=self.results)

    def job(self, text):
        path = os.path.join(self.tmp, "t.job")
        with open(path, "w") as f:
            f.write(text)
        return path

    def check(self, path, check=True):
        env = dict(self.env, PERF_JOB_CHECK="1" if check else "0")
        return subprocess.run(["sh", PERF_JOB, path], capture_output=True, text=True, env=env,
                              cwd=self.tmp, timeout=30, stdin=subprocess.DEVNULL)

    BASE = "JOB=t\nREV_A=abc\nREV_B=abc\nWORKLOAD=rx32\n"

    # ---- job keys ----

    def test_configure_args_differ_gives_two_phases(self):
        r = self.check(self.job(self.BASE + "CONFIGURE_ARGS_B=--regex-jit\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("phases 2", r.stdout)
        self.assertIn("configure A '' B '--regex-jit'", r.stdout)

    def test_same_rev_same_args_is_null_only(self):
        r = self.check(self.job(self.BASE + "CONFIGURE_ARGS_A=--regex-jit\nCONFIGURE_ARGS_B=--regex-jit\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("phases 1", r.stdout)

    def test_shared_args_are_prefixed_to_each_side(self):
        r = self.check(self.job(self.BASE + "CONFIGURE_ARGS=--otel\nCONFIGURE_ARGS_B=--regex-jit\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("configure A '--otel' B '--otel --regex-jit'", r.stdout)

    def test_configure_args_with_shell_characters_are_refused(self):
        for bad in ("--x;id", "--x$(id)", "--x`id`", "--x|id", "--x&id"):
            r = self.check(self.job(self.BASE + f"CONFIGURE_ARGS_B={bad}\n"))
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("CONFIGURE_ARGS_B may hold only", r.stderr)

    def test_job_env_carries_per_side_args(self):
        r = self.check(self.job(self.BASE + "CONFIGURE_ARGS_B=--regex-jit\n"), check=False)
        self.assertEqual(r.returncode, 1, r.stderr)   # the ssh stub fails after job.env is written
        envs = glob.glob(os.path.join(self.results, "t-*", "job.env"))
        self.assertEqual(len(envs), 1)
        with open(envs[0]) as f:
            text = f.read()
        self.assertIn("CONFIGURE_ARGS_A=''", text)
        self.assertIn("CONFIGURE_ARGS_B=--regex-jit", text)

    def test_job_env_round_trips_shell_characters(self):
        # job.env is sourced by remote.sh.  A value with blanks, quotes, $, \
        # and * must read back unchanged under every shell that runs it.
        funcs = """a  b 'q' "d" $x \\y * `z` ; e"""
        r = self.check(self.job(self.BASE + f"CONNS=1 32\nFUNCS={funcs}\n"), check=False)
        self.assertEqual(r.returncode, 1, r.stderr)   # the ssh stub fails after job.env is written
        envs = glob.glob(os.path.join(self.results, "t-*", "job.env"))
        self.assertEqual(len(envs), 1)
        for sh in ("sh", "dash", "busybox sh", "bash --posix"):
            if not shutil.which(sh.split()[0]):
                continue
            with self.subTest(shell=sh):
                out = subprocess.run(sh.split() + ["-c", '. "$1"; printf "%s|%s" "$FUNCS" "$CONNS"', "sh", envs[0]],
                                     capture_output=True, text=True, timeout=30)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout, f"{funcs}|1 32")

    def test_every_rx_workload_passes_check(self):
        for w in RX_WORKLOADS:
            r = self.check(self.job(f"JOB=t\nREV_A=a\nREV_B=b\nWORKLOAD={w}\n"))
            self.assertEqual(r.returncode, 0, f"{w}: {r.stderr}")

    def test_every_py_workload_passes_check(self):
        for w in PY_WORKLOADS:
            r = self.check(self.job(f"JOB=t\nREV_A=a\nREV_B=b\nWORKLOAD={w}\n"))
            self.assertEqual(r.returncode, 0, f"{w}: {r.stderr}")

    def test_py1_jobs(self):
        self.assertEqual([os.path.basename(j)[4:-4] for j in PY_JOBS],
                         sorted(w[3:] for w in PY_WORKLOADS))
        for j in PY_JOBS:
            r = self.check(j)
            self.assertEqual(r.returncode, 0, f"{j}: {r.stderr}")
            self.assertIn("phases 2", r.stdout)
            with open(j) as f:
                text = f.read()
            for k in ("REV_A", "REV_B"):
                self.assertRegex(re.search(rf"^{k}=(\S+)$", text, re.M).group(1), r"^[0-9a-f]{40}$",
                                 "pin the full SHA")
            for k in ("EXPECT=", "FALSIFY=", "PRIMARY=r_instructions", "PERF=1"):
                self.assertIn(k, text)

    def test_unknown_workload_is_refused(self):
        r = self.check(self.job("JOB=t\nREV_A=a\nREV_B=b\nWORKLOAD=rx2\n"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("is not built yet", r.stderr)

    def test_pcre2_jit_jobs(self):
        self.assertEqual(len(JIT_JOBS), 4)
        for j in JIT_JOBS:
            r = self.check(j)
            self.assertEqual(r.returncode, 0, f"{j}: {r.stderr}")
            self.assertIn("phases 2", r.stdout)
            self.assertIn("configure A '' B '--regex-jit'", r.stdout)
            with open(j) as f:
                text = f.read()
            sha = re.search(r"^REV_A=(\S+)$", text, re.M).group(1)
            self.assertRegex(sha, r"^[0-9a-f]{40}$", "pin the full SHA")
            self.assertIn(f"REV_B={sha}\n", text)
            for k in ("EXPECT=", "FALSIFY=", "PRIMARY=r_cycles", "PERF=1"):
                self.assertIn(k, text)

    # ---- configs ----

    def load(self, w):
        with open(os.path.join(CONF, w + ".json")) as f:
            return json.load(f)

    def test_configs_are_router_only_and_use_the_port_placeholder(self):
        for w in RX_WORKLOADS:
            c = self.load(w)
            self.assertEqual(list(c["listeners"]), ["127.0.0.1:@PORT@"], w)
            self.assertNotIn("applications", c, w)
            self.assertEqual(c["routes"]["rx"][-1], {"action": {"return": 200 if w in ("rx0", "drupal-ci") else 404}}, w)

    def test_regex_counts(self):
        want = {"rx0": (32, 0), "rx1": (1, 1), "rx32": (32, 32), "drupal-ci": (32, 32)}
        for w, (n, nre) in want.items():
            pats = uri_patterns(self.load(w))
            self.assertEqual(len(pats), n, w)
            self.assertEqual(sum(map(is_regex, pats)), nre, w)

    def test_rx32_only_the_last_regex_matches(self):
        pats = uri_patterns(self.load("rx32"))
        hits = [bool(re.search(py_regex(p), "/catalog/section/item42")) for p in pats]
        self.assertEqual(hits, [False] * 31 + [True])
        self.assertTrue(all(p.startswith("~^/") for p in pats), "anchored")

    def test_drupal_ci_is_the_glob_list_made_case_insensitive(self):
        globs = uri_patterns(self.load("rx0"))
        regexes = uri_patterns(self.load("drupal-ci"))
        self.assertTrue(all(p.lstrip("!").startswith("~(?i)") for p in regexes))
        self.assertEqual([g.startswith("!") for g in globs], [r.startswith("!") for r in regexes])

        def denied(pats, uri, regex):
            pos = [p for p in pats if not p.startswith("!")]
            neg = [p for p in pats if p.startswith("!")]
            m = (lambda p: re.search(py_regex(p), uri)) if regex else \
                (lambda p: re.fullmatch(re.escape(p.lstrip("!")).replace(r"\*", ".*"), uri))
            return any(map(m, pos)) and not any(map(m, neg))

        for uri in ("/node/42/edit", "/index.php", "/.well-known/acme-challenge/x", "/user/login"):
            self.assertFalse(denied(regexes, uri, True), uri)
            self.assertFalse(denied(globs, uri, False), uri)
        for uri in ("/vendor/autoload.php", "/core/composer.json", "/a/b.module", "/x.yml", "/db.sql",
                    "/a/.git/config", "/f.swp", "/f~", "/CVS/Root", "/x/Entries.Log"):
            self.assertTrue(denied(regexes, uri, True), uri)
            self.assertTrue(denied(globs, uri, False), uri)
        for uri in ("/core/composer.JSON", "/WEB.CONFIG", "/x.YML", "/cvs/root", "/a/B.MODULE"):
            self.assertTrue(denied(regexes, uri, True), uri)
            self.assertFalse(denied(globs, uri, False), uri)

    def test_cells_and_remote_know_the_workloads(self):
        with open(os.path.join(LAB, "cells.sh")) as f:
            cells = f.read()
        self.assertIn("rx0|rx1|rx32|drupal-ci)", cells)
        self.assertIn('/lab/conf/$WORKLOAD.json', cells)
        self.assertIn("rx0|drupal-ci) echo", cells)
        self.assertIn("rx1|rx32) echo", cells)
        with open(os.path.join(LAB, "remote.sh")) as f:
            remote = f.read()
        self.assertIn('prepare "$SHA_A" "$PA" "${CONFIGURE_ARGS_A:-}"', remote)
        self.assertIn('prepare "$SHA_B" "$PB" "${CONFIGURE_ARGS_B:-}"', remote)
        self.assertIn("regex_jit_b=", remote)

    def test_cells_and_remote_know_the_py_workloads(self):
        with open(os.path.join(LAB, "cells.sh")) as f:
            cells = f.read()
        for w in PY_WORKLOADS:
            m = re.search(rf"^    {w}\) py_conf \w+ (\w+) ", cells, re.M)
            self.assertIsNotNone(m, w)
            self.assertTrue(os.path.isfile(os.path.join(LAB, "www", "py", m.group(1) + ".py")), w)
        self.assertIn("py-*-stream) echo chunked", cells)
        self.assertIn('check_body "$1"', cells)
        with open(os.path.join(LAB, "remote.sh")) as f:
            remote = f.read()
        self.assertIn("./configure python --config=python3-config --module=python3", remote)
        self.assertIn("pyvenv=$PY_PKGS", remote)

    def test_remote_checks_py_image_before_build(self):
        # A py-* job must stop before any build when the image lacks Python,
        # and the message must name both images to rebuild.
        with open(os.path.join(LAB, "remote.sh")) as f:
            remote = f.read()
        build = remote.index("prepare() {")
        for probe in ('fail "no python3-config in $LAB_IMAGE; $PY_IMAGE_FIX"',
                      'fail "no /opt/pyvenv in $LAB_IMAGE; $PY_IMAGE_FIX"'):
            self.assertIn(probe, remote)
            self.assertLess(remote.index(probe), build, probe)
        fix = re.search(r'^PY_IMAGE_FIX="([^"]*)"', remote, re.M)
        self.assertIsNotNone(fix)
        self.assertIn("tools/perf-lab/Dockerfile.base", fix.group(1))
        self.assertIn("tools/perf-lab/Dockerfile)", fix.group(1))
        self.assertLess(fix.start(), build)

    def test_scripts_parse(self):
        # POSIX sh (docs/shell.md): parse with sh, and with dash, busybox ash and bash --posix when present.
        shells = [["sh", "-n"]]
        for name, cmd in (("dash", ["dash", "-n"]), ("busybox", ["busybox", "sh", "-n"]),
                          ("bash", ["bash", "--posix", "-n"])):
            if shutil.which(name):
                shells.append(cmd)
        for s in ("perf-job.sh", "perf-lab/cells.sh", "perf-lab/remote.sh"):
            path = os.path.join(TOOLS, s)
            with open(path) as f:
                self.assertEqual(f.readline(), "#!/bin/sh\n", s)
            for cmd in shells:
                r = subprocess.run(cmd + [path], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{' '.join(cmd)} {s}: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
