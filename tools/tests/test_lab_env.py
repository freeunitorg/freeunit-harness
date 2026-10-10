"""Tests of the lab env loader, tools/lib/env.sh, and of the tools that use it:
perf-job.sh (check mode), hosts and unit-build (config verb). Each test works in its own
temporary directory, with LAB_ENV pointing there. Nothing touches the network: ssh, rsync,
tmux and docker are replaced on PATH by stubs that record the call and fail.

Run: python3 -m unittest discover -s tools/tests -v
LAB_ENV_TEST_TOOLS=DIR runs the same tests against the tools in another copy of tools/.
"""
import glob, os, shutil, subprocess, sys, tempfile, unittest

sys.dont_write_bytecode = True
TOOLS = os.environ.get("LAB_ENV_TEST_TOOLS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_SH = os.path.join(TOOLS, "lib", "env.sh")
HOSTS, UNIT_BUILD, PERF_JOB = (os.path.join(TOOLS, n) for n in ("hosts", "unit-build", "perf-job.sh"))
TEMPLATE = os.path.join(TOOLS, "perf-lab", "lab.env.example")
JOBS = sorted(glob.glob(os.path.join(TOOLS, "perf-lab", "jobs", "*.job")))


def lab_keys():
    """Every documented key: the KEY= lines of the template."""
    with open(TEMPLATE) as f:
        return [line.split("=", 1)[0] for line in f if line[:1].isupper() and "=" in line]


class LabEnvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="lab-env-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.lab = os.path.join(self.tmp, "lab.env")
        self.stubs = os.path.join(self.tmp, "stubs")
        self.calls = os.path.join(self.tmp, "remote-calls")
        os.mkdir(self.stubs)
        for name in ("ssh", "rsync", "tmux", "docker"):
            path = os.path.join(self.stubs, name)
            with open(path, "w") as f:
                f.write(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\nexit 1\n')
            os.chmod(path, 0o755)
        keys = set(lab_keys())
        self.env = {k: v for k, v in os.environ.items()
                    if k not in keys and not k.endswith("_HOST") and not k.startswith(("LAB_", "UNIT_", "PERF_"))}
        self.env.update(LAB_ENV=self.lab, PATH=self.stubs + os.pathsep + os.environ["PATH"],
                        TMPDIR=self.tmp, LAB_RESULTS=os.path.join(self.tmp, "results"))

    def tearDown(self):
        self.assertFalse(os.path.exists(self.calls), "a remote command was started")

    def write_lab(self, text):
        with open(self.lab, "w") as f:
            f.write(text)

    def run_cmd(self, *cmd, env=None):
        return subprocess.run(list(cmd), capture_output=True, text=True, env=dict(self.env, **(env or {})),
                              cwd=self.tmp, timeout=30, stdin=subprocess.DEVNULL)

    def loader(self, script, env=None):
        return self.run_cmd("sh", "-c", f'LAB_HARNESS={self.tmp}; . "{ENV_SH}"; {script}', env=env)

    def hosts(self, *args, env=None):
        return self.run_cmd("bash", HOSTS, *args, env=env)

    def unit_build(self, *args, env=None):
        return self.run_cmd("bash", UNIT_BUILD, *args, env=env)

    def make_src(self, name="freeunit", configure=True):
        src = os.path.join(self.tmp, name)
        os.makedirs(src, exist_ok=True)
        if configure:
            open(os.path.join(src, "configure"), "w").close()
        return src

    # ---- the template ----

    def test_template_as_is_passes_perf_job_check_for_every_job(self):
        shutil.copy(TEMPLATE, self.lab)
        self.assertTrue(JOBS)
        for job in JOBS:
            r = self.run_cmd("bash", PERF_JOB, job, env={"PERF_JOB_CHECK": "1"})
            self.assertEqual(r.returncode, 0, f"{job}: {r.stderr}")
            self.assertTrue(r.stdout.startswith("CHECK "), r.stdout)
            self.assertNotIn("unknown key", r.stderr)

    def test_template_documents_every_loader_key(self):
        r = self.loader('for k in $LAB_KEYS; do echo "$k"; done')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(r.stdout.split()), sorted(lab_keys()))

    # ---- parsing ----

    def test_quotes_are_stripped_once(self):
        self.write_lab("BUILD_HOST=\"'inner'\"\nBOT_HOST='bot a'  # comment\nINFER_HOST=\"gpu#1\" # c\n"
                       "export PERF_HOST=bench-a\n")
        self.assertEqual(self.hosts("ssh", "build").stdout, "'inner'\n")
        self.assertEqual(self.hosts("ssh", "bot").stdout, "bot a\n")
        self.assertEqual(self.hosts("ssh", "infer").stdout, "gpu#1\n")
        self.assertEqual(self.hosts("ssh", "lab").stdout, "bench-a\n")

    def test_environment_wins_over_the_file(self):
        self.write_lab("BUILD_HOST=from-file\nPERF_HOST=bench-a\nPERF_HOST=bench-b\n")
        self.assertEqual(self.hosts("ssh", "build", env={"BUILD_HOST": "from-env"}).stdout, "from-env\n")
        self.assertEqual(self.hosts("ssh", "build").stdout, "from-file\n")
        self.assertEqual(self.hosts("ssh", "lab").stdout, "bench-b\n")
        r = self.loader("lab_env_load; echo $BUILD_HOST; lab_env_get BUILD_HOST", env={"BUILD_HOST": "from-env"})
        self.assertEqual(r.stdout, "from-env\nfrom-env\n")

    def test_command_substitution_is_never_executed(self):
        marker = os.path.join(self.tmp, "MARKER")
        shutil.copy(TEMPLATE, self.lab)
        with open(self.lab, "a") as f:
            f.write(f"LAB_IMAGE=$(touch {marker})\nBUILD_HOST=`touch {marker}`\nBOT_HOST=\"$(touch {marker})\"\n")
        r = self.loader("lab_env_load --export; printf '%s\\n' \"$LAB_IMAGE\"; lab_env_get BUILD_HOST")
        self.assertEqual(r.stdout, f"$(touch {marker})\n`touch {marker}`\n")
        self.assertEqual(self.hosts("ssh", "bot").stdout, f"$(touch {marker})\n")
        r = self.run_cmd("bash", PERF_JOB, JOBS[0], env={"PERF_JOB_CHECK": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_unknown_key_warns_and_perf_job_refuses_it(self):
        self.write_lab("PERF_HOST=bench-a\nBOGUS_KEY=1\n")
        r = self.hosts("ssh", "lab")
        self.assertEqual((r.returncode, r.stdout), (0, "bench-a\n"))
        self.assertIn(f"{self.lab}:2: unknown key BOGUS_KEY", r.stderr)
        shutil.copy(TEMPLATE, self.lab)
        with open(self.lab, "a") as f:
            f.write("SERVR_CPUS=0\n")
        r = self.run_cmd("bash", PERF_JOB, JOBS[0], env={"PERF_JOB_CHECK": "1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown key SERVR_CPUS", r.stderr)

    # ---- hosts ----

    def test_hosts_resolves_roles_and_paths_through_the_loader(self):
        self.write_lab("export BUILD_HOST=build-a  # the build box\nREMOTE_WORKDIR='/srv/lab'\nGPU2_HOST=gpu-b\n")
        self.assertEqual(self.hosts("ssh", "build").stdout, "build-a\n")
        self.assertEqual(self.hosts("ssh", "gpu2").stdout, "gpu-b\n")
        self.assertEqual(self.hosts("root", "build").stdout, "/srv/lab\n")
        self.assertEqual(self.hosts("cache", "build").stdout, "/srv/lab/cache\n")
        self.assertEqual(self.hosts("root", "build", env={"UNIT_REMOTE_ROOT": "/data/x"}).stdout, "/data/x\n")

    # ---- unit-build ----

    def test_unit_build_refuses_a_source_dir_without_configure(self):
        bare = self.make_src("bare", configure=False)
        for args in (["-s", bare], []):
            r = self.unit_build("-t", "local", *args, "config", env={"UNIT_SRC": bare})
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn(f"{bare} has no configure", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_unit_build_default_source_is_unit_src_not_the_cwd(self):
        # The working directory holds a configure; it must not be picked.
        open(os.path.join(self.tmp, "configure"), "w").close()
        src = self.make_src()
        self.write_lab("BUILD_HOST=build-a\nREMOTE_WORKDIR=/srv/lab\nALPINE_BUILD_IMAGE='img:1'\n")
        r = self.unit_build("-t", "remote", "-H", "build", "config", env={"UNIT_SRC": src})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["TARGET=remote", "HOST=build-a", f"SRC={src}", "NAME=freeunit",
                                                 "REMOTE_ROOT=/srv/lab", "REMOTE_CACHE=/srv/lab/cache", "IMAGE=img:1"])

    def test_unit_build_never_syncs_onto_the_remote_root(self):
        src = self.make_src()
        self.write_lab("BUILD_HOST=build-a\nBUILD_ROOT=/srv/b\n")
        r = self.unit_build("-t", "remote", "-H", "build", "-s", src + "/.", "config")
        self.assertIn("NAME=freeunit\n", r.stdout)
        r = self.unit_build("-t", "remote", "-H", "build", "-s", "/", "build")
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing to sync /", r.stderr)


if __name__ == "__main__":
    unittest.main()
