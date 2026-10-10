"""Tests of the router-only perf lab workloads (rx0, rx1, rx32, drupal-ci) and of the
per-side configure keys CONFIGURE_ARGS_A and CONFIGURE_ARGS_B in tools/perf-job.sh.

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
        return subprocess.run(["bash", PERF_JOB, path], capture_output=True, text=True, env=env,
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

    def test_every_rx_workload_passes_check(self):
        for w in RX_WORKLOADS:
            r = self.check(self.job(f"JOB=t\nREV_A=a\nREV_B=b\nWORKLOAD={w}\n"))
            self.assertEqual(r.returncode, 0, f"{w}: {r.stderr}")

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

    def test_scripts_parse(self):
        for s in ("perf-job.sh", "perf-lab/cells.sh", "perf-lab/remote.sh"):
            r = subprocess.run(["bash", "-n", os.path.join(TOOLS, s)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
