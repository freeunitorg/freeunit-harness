"""Tests of the router-thread scaling kit in tools/perf-lab/scaling.

The shell functions run from lib.sh under bash. Nothing touches the network:
ssh is a stub that runs the remote command locally, and the topology is a fake
sysfs tree. SCALING_LIB=<file> tests another copy of lib.sh.
Run: python3 -m unittest discover -s tools/tests -v
"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(TOOLS, "perf-lab", "scaling")
LIB = os.environ.get("SCALING_LIB") or os.path.join(KIT, "lib.sh")


def module(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(KIT, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="perf-lab-scaling-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.endswith("_HOST") and not k.startswith(("LAB_", "UNIT_", "PERF_"))}

    def lib(self, script, **env):
        e = dict(self.env, LIB=LIB, **env)
        return subprocess.run(["bash", "-c", '. "$LIB"\n' + script], capture_output=True, text=True,
                              env=e, timeout=60, stdin=subprocess.DEVNULL)

    def topology(self, siblings):
        """siblings: one thread_siblings_list string per cpu, cpu0 first."""
        root = os.path.join(self.tmp, "cpu")
        for i, s in enumerate(siblings):
            d = os.path.join(root, "cpu%d" % i, "topology")
            os.makedirs(d)
            with open(os.path.join(d, "thread_siblings_list"), "w") as f:
                f.write(s + "\n")
        return root

    def cpus(self, siblings, calls):
        r = self.lib("read_topology\necho \"$NCORES\"\n" + "\n".join(calls), SYSCPU=self.topology(siblings))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split("\n")[:-1]


class TopologyTest(Base):
    def test_expand_ranges_and_lists(self):
        r = self.lib('expand_cpus 0-1; expand_cpus 0,4; expand_cpus 0-1,4-5; expand_cpus 7')
        self.assertEqual(r.stdout.split("\n")[:-1], ["0 1", "0 4", "0 1 4 5", "7"])

    def test_smt_pairs_as_a_list(self):
        # four cores, siblings 0,4 1,5 2,6 3,7
        sib = ["0,4", "1,5", "2,6", "3,7"] * 2
        out = self.cpus(sib, ["cpus_server 2", "cpus_load 2", "cpus_server 4", "cpus_load 4"])
        self.assertEqual(out, ["4", "0,1", "2,6,3,7", "0,1,2,3", "4,5,6,7"])

    def test_smt_pairs_as_a_range(self):
        # two cores, siblings 0-1 2-3
        out = self.cpus(["0-1", "0-1", "2-3", "2-3"], ["cpus_server 1", "cpus_load 1", "cpus_load 2"])
        self.assertEqual(out, ["2", "0", "2,3", "1,3"])

    def test_no_smt_and_no_core_left_shares_every_cpu(self):
        # two cores without SMT, n=2: no free core and no sibling. The load set must not be empty.
        out = self.cpus(["0", "1"], ["cpus_server 2", "cpus_load 2", "cpus_load 1"])
        self.assertEqual(out, ["2", "0,1", "0,1", "1"])


class ScopedKillTest(Base):
    def setUp(self):
        super().setUp()
        # A sleeper that runs under any name. /bin/sleep can be a multi-call
        # binary that picks its applet from argv[0]; then build one with cc.
        probe = os.path.join(self.tmp, "probe", "unitd")
        os.makedirs(os.path.dirname(probe))
        shutil.copy(os.path.realpath(shutil.which("sleep") or "/bin/sleep"), probe)
        if subprocess.run([probe, "0"], capture_output=True).returncode == 0:
            self.sleeper, self.args = probe, ["60"]
            return
        cc = shutil.which("cc")
        if cc is None:
            self.skipTest("sleep is a multi-call binary and there is no cc")
        src = os.path.join(self.tmp, "sleeper.c")
        with open(src, "w") as f:
            f.write("#include <unistd.h>\nint main(void) { sleep(60); return 0; }\n")
        self.sleeper, self.args = os.path.join(self.tmp, "sleeper"), []
        subprocess.run([cc, "-o", self.sleeper, src], check=True)

    def spawn(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy(self.sleeper, path)
        p = subprocess.Popen([path] + self.args)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    def test_stop_unit_kills_only_processes_of_the_trees(self):
        trees = os.path.join(self.tmp, "trees")
        main = self.spawn(os.path.join(trees, "v", "build", "sbin", "unitd"))
        app = self.spawn(os.path.join(trees, "v", "build", "lab_capp"))
        foreign_unitd = self.spawn(os.path.join(self.tmp, "other", "build", "sbin", "unitd"))
        foreign_app = self.spawn(os.path.join(self.tmp, "other", "build", "lab_capp"))
        time.sleep(0.2)
        r = self.lib('my_pids | sort -n | tr "\\n" " "; echo; stop_unit', TREES=trees, SRV_PID=str(main.pid))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[0].split(), sorted([str(main.pid), str(app.pid)], key=int))
        time.sleep(0.2)
        self.assertIsNotNone(main.poll(), "the main process of the tree survived")
        self.assertIsNotNone(app.poll(), "an application process of the tree survived")
        self.assertIsNone(foreign_unitd.poll(), "a unitd outside the trees was killed")
        self.assertIsNone(foreign_app.poll(), "a lab_capp outside the trees was killed")


class AnalyzeTest(unittest.TestCase):
    def rows(self, variant, rps):
        return [{"variant": variant, "run": str(i + 1), "rps": str(x)} for i, x in enumerate(rps)]

    def test_overlapping_runs_are_noise(self):
        a = module("analyze")
        r, paired, lo, hi, verdict = a.ratio(self.rows("a", [100, 102, 98, 101, 99]),
                                             self.rows("b", [101, 99, 100, 103, 98]))
        self.assertLessEqual(lo, 1.0)
        self.assertGreaterEqual(hi, 1.0)
        self.assertEqual(verdict, "noise")

    def test_separated_runs_are_a_verdict(self):
        a = module("analyze")
        r, paired, lo, hi, verdict = a.ratio(self.rows("a", [100, 101, 99, 100, 100]),
                                             self.rows("b", [90, 91, 89, 90, 90]))
        self.assertEqual(verdict, "slower")
        self.assertAlmostEqual(r, 0.9, places=3)

    def test_two_runs_give_no_verdict(self):
        # two clearly separated runs per side still make no claim
        a = module("analyze")
        verdict = a.ratio(self.rows("a", [100, 100]), self.rows("b", [90, 90]))[4]
        self.assertEqual(verdict, "few runs")

    def test_mixed_sign_rounds_are_noise(self):
        # the medians differ, but one round goes the other way
        a = module("analyze")
        verdict = a.ratio(self.rows("a", [100, 100, 100, 100, 100]), self.rows("b", [90, 91, 92, 93, 101]))[4]
        self.assertEqual(verdict, "noise")


class FreqTest(Base):
    def test_server_cpus_come_from_meta(self):
        d = self.tmp
        with open(os.path.join(d, "meta.txt"), "w") as f:
            f.write("params RUNS=1 DUR=10\ncore0 0,1\ncore1 2,3\n")
        with open(os.path.join(d, "freq.log"), "w") as f:
            # cpu0 and cpu2 are the server cpus of n=2; cpu1 and cpu3 must not count
            for t in (1000, 1005, 1010):
                f.write(f"{t} 2000000 900000 3000000 900000\n")
        with open(os.path.join(d, "sweep.tsv"), "w") as f:
            f.write("variant\tworkload\tn\tlayout\trun\tok\tbad\trps\tp50_ms\tp99_ms\tsrv_cpu_pct\tload_cpu_pct\tts\n")
            f.write("a\tcapp\t2\tclean\t1\t1\t0\t1\t0\t0\t0\t0\t1010\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            module("freq").main(["freq.py", d])
        self.assertIn("median 2500 MHz", buf.getvalue())


class LocksTest(Base):
    """locks.py on shim snapshots written by hand."""

    def shim(self, d, pid, uid, rows):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "mutex-%d-%d.tsv" % (pid, uid)), "w") as f:
            for site, calls, cont in rows:
                f.write("%#x\t0x400000\t/t/v/build/sbin/unitd\t%d\t%d\t%d\n" % (site, calls, cont, cont * 1000))

    def run_dir(self):
        d = os.path.join(self.tmp, "v-capp-1-1")
        for snap in ("a", "b"):
            os.makedirs(os.path.join(d, snap, "x"))
            with open(os.path.join(d, snap, "roles.txt"), "w") as f:
                f.write("10 unit: router\n11 /t/v/build/lab_capp\n")
        # router: 100 calls after the warm-up, 400 after the window
        self.shim(os.path.join(d, "a"), 10, 65534, [(0x401000, 60, 1), (0x402000, 40, 0)])
        self.shim(os.path.join(d, "a", "x"), 10, 65534, [(0x401000, 60, 1), (0x402000, 40, 0)])
        self.shim(os.path.join(d, "b"), 10, 65534, [(0x401000, 250, 4), (0x402000, 150, 0)])
        # the second copy of b was taken during a rewrite: one line is missing
        self.shim(os.path.join(d, "b", "x"), 10, 65534, [(0x401000, 250, 4)])
        # an app process whose first file appears only in b
        self.shim(os.path.join(d, "b"), 11, 65534, [(0x403000, 30, 0)])
        return d

    def test_counts_are_the_window_only(self):
        rows = module("locks").diff(self.run_dir(), "v", "capp", "1", "1", "100")
        got = {r[5]: (int(r[7]), int(r[8])) for r in rows}
        self.assertEqual(got, {"router": (300, 3), "app": (30, 0)})

    def test_summary_is_per_request(self):
        L = module("locks")
        tsv = os.path.join(self.tmp, "locks.tsv")
        with open(tsv, "w") as f:
            f.write("variant\tworkload\tn\trun\tok\trole\tpid\tcalls\tcontended\twait_ns\n")
            for row in L.diff(self.run_dir(), "v", "capp", "1", "1", "100"):
                f.write("\t".join(row) + "\n")
        self.assertEqual(L.summary(tsv), ["workload n variant run role calls/req contended/req",
                                          "capp 1 v 1 app 0.3000 0.00000",
                                          "capp 1 v 1 router 3.0000 0.03000"])


class RunStageTest(Base):
    """run.sh claim and release against a stub ssh that runs the command here."""

    def setUp(self):
        super().setUp()
        stubs = os.path.join(self.tmp, "stubs")
        os.mkdir(stubs)
        with open(os.path.join(stubs, "ssh"), "w") as f:
            f.write('#!/bin/sh\nwhile [ "$1" = -o ]; do shift 2; done\nshift\nexec sh -c "$*"\n')
        os.chmod(os.path.join(stubs, "ssh"), 0o755)
        # The stub ssh runs the remote command here: no test may reach a real docker.
        with open(os.path.join(stubs, "docker"), "w") as f:
            f.write('#!/bin/sh\necho "docker stub: $*" >&2\nexit 1\n')
        os.chmod(os.path.join(stubs, "docker"), 0o755)
        self.claims = os.path.join(self.tmp, "claims")
        os.mkdir(self.claims)
        lab = os.path.join(self.tmp, "lab.env")
        with open(lab, "w") as f:
            f.write(f"PERF_HOST=box.example\nREMOTE_WORKDIR={self.tmp}/rw\nCLAIM_DIR={self.claims}\n"
                    f"LAB_IMAGE=img\nOHA=/bin/true\nLAB_RESULTS={self.tmp}/results\n")
        self.env.update(LAB_ENV=lab, PATH=stubs + os.pathsep + os.environ["PATH"])
        self.addCleanup(self.kill_holders)

    def kill_holders(self):
        rec = os.path.join(self.tmp, "results", "scaling", ".claim")
        if os.path.exists(rec):
            with open(rec) as f:
                pids = [line[4:].strip() for line in f if line.startswith("pid=")]
            for pid in pids:
                subprocess.run(["kill", pid], capture_output=True)

    def run_sh(self, *args):
        return subprocess.run(["sh", os.path.join(KIT, "run.sh")] + list(args), capture_output=True,
                              text=True, env=self.env, timeout=60, stdin=subprocess.DEVNULL)

    def test_claim_and_release(self):
        r = self.run_sh("claim", "5")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        files = os.listdir(self.claims)
        claim = [f for f in files if f.startswith("CLAIMED-perflab-scaling-")]
        self.assertEqual(len(claim), 1, files)
        with open(os.path.join(self.claims, claim[0])) as f:
            text = f.read()
        self.assertIn("format=perflab-1\n", text)
        self.assertIn("expires=", text)
        r = self.run_sh("claim")
        self.assertNotEqual(r.returncode, 0, "a second claim passed")
        r = self.run_sh("release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([f for f in os.listdir(self.claims) if f.startswith("CLAIMED-")], [])

    def test_claim_refuses_a_live_foreign_claim(self):
        open(os.path.join(self.claims, "CLAIMED-other-bench"), "w").close()   # fresh legacy claim
        r = self.run_sh("claim", "5")
        self.assertEqual(r.returncode, 75, r.stdout + r.stderr)
        self.assertIn("CLAIMED-other-bench", r.stdout)
        self.assertEqual(sorted(f for f in os.listdir(self.claims) if f.startswith("CLAIMED-")),
                         ["CLAIMED-other-bench"])

    def test_old_legacy_claim_is_ignored(self):
        p = os.path.join(self.claims, "CLAIMED-old-bench")
        open(p, "w").close()
        old = time.time() - 80 * 3600
        os.utime(p, (old, old))
        r = self.run_sh("claim", "5")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.run_sh("release")

    def unit_repo(self):
        repo = os.path.join(self.tmp, "unit-src")
        os.mkdir(repo)
        with open(os.path.join(repo, "README"), "w") as f:
            f.write("stub\n")
        g = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(g + ["add", "README"], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "stub"], check=True)
        return repo

    def test_build_refuses_a_tree_name_outside_trees(self):
        # "../trees" made the tree path $RD/trees/../trees, and build ran rm -rf on it.
        trees = os.path.join(self.tmp, "rw", "scaling", "trees")
        os.makedirs(trees)
        marker = os.path.join(trees, "keep")
        open(marker, "w").close()
        self.env["UNIT_REPO"] = self.unit_repo()
        for name in ("../trees", "..", ".", "a/b"):
            r = self.run_sh("build", name + "=HEAD")
            self.assertNotEqual(r.returncode, 0, name + ": " + r.stdout + r.stderr)
            self.assertIn("letters, digits", r.stderr, name)
            self.assertTrue(os.path.exists(marker), "build %s=HEAD removed %s" % (name, marker))

    def test_tag_stages_refuse_a_path(self):
        rw = os.path.join(self.tmp, "rw", "scaling")
        for d in ("trees", "results"):
            os.makedirs(os.path.join(rw, d))
        for stage in ("stop", "watch", "fetch"):
            r = self.run_sh(stage, "../trees")
            self.assertNotEqual(r.returncode, 0, stage)
            self.assertIn("letters, digits", r.stderr, stage + ": " + r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(rw, "trees", "STOP")), "stop wrote trees/STOP")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "results", "trees")), "fetch made results/trees")

    def test_usage(self):
        r = self.run_sh()
        self.assertEqual(r.returncode, 2)
        self.assertIn("run.sh claim", r.stdout)


if __name__ == "__main__":
    unittest.main()
