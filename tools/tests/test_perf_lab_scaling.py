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
import signal
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


class ParamsTest(Base):
    """meta.txt records the parameters that the run used."""

    KNOBS = dict(VARIANTS="a b", WORKLOADS="capp", NS="1 2", RUNS="5", DUR="30", WARM="3", LRUNS="2",
                 LDUR="10", SHIM="/lab/mutexshim.so", PAIRS="b:a", N="2", V="b", PERF_SEC="20", C2C_G="0",
                 LOAD_SEC="75")

    def line(self, mode):
        r = self.lib("params_line " + mode, **self.KNOBS)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_locks_line_has_the_lock_knobs(self):
        import re
        t = self.line("locks")
        for k in ("LOCKS=1", "LRUNS=2", "LDUR=10", "SHIM='/lab/mutexshim.so'", "NS='1 2'"):
            self.assertIn(k, t)
        self.assertIsNone(re.search(r"\b(RUNS|DUR)=", t), "a locks line names the unused RUNS or DUR: " + t)

    def test_sweep_line_has_pairs(self):
        t = self.line("sweep")
        for k in ("RUNS=5", "DUR=30", "PAIRS='b:a'"):
            self.assertIn(k, t)
        self.assertNotIn("LRUNS", t)

    def test_profile_line_has_the_profile_knobs(self):
        t = self.line("profile")
        for k in ("N=2", "V=b", "PERF_SEC=20", "C2C_G=0", "LOAD_SEC=75", "CONNS=", "APP_PROCS=", "PROFILE=",
                  "PERF_EVENT="):
            self.assertIn(k, t)


class CoreCountTest(Base):
    """The aarch64 lab box has two cores: the defaults must fit it, and a skipped n must say so."""

    def test_two_cores(self):
        r = self.lib('read_topology; default_n; usable_ns "1 2 3"', SYSCPU=self.topology(["0", "1"]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[:-1], ["2", "1 2"])
        self.assertIn("n=3", r.stderr)

    def test_four_cores_keep_the_default(self):
        r = self.lib('read_topology; default_n; usable_ns "1 2 3"',
                     SYSCPU=self.topology(["0,4", "1,5", "2,6", "3,7"] * 2))
        self.assertEqual(r.stdout.split("\n")[:-1], ["3", "1 2 3"])
        self.assertEqual(r.stderr, "")


class FakeBox(Base):
    """A fake /proc/sys, /proc/self, event_source and tracefs tree for perf_preflight."""

    INIT = "0 0 4294967295"
    ROOTLESS = "0 1000 1"
    PERFMON = 1 << 38
    SYS_ADMIN = 1 << 21

    def box(self, paranoid, pmu=("cpu", "cpu/events/mem-loads"), kptr=1, scope=1, cap=0, uid_map=INIT,
            tracefs=False):
        root = os.path.join(self.tmp, "box%d" % len(os.listdir(self.tmp)))
        files = {"sys/kernel/perf_event_paranoid": str(paranoid), "sys/kernel/kptr_restrict": str(kptr),
                 "sys/kernel/yama/ptrace_scope": str(scope),
                 "self/status": "Name:\tbash\nCapEff:\t%016x\n" % cap, "self/uid_map": uid_map,
                 "ev/software/type": "1", "ev/tracepoint/type": "2"}
        for d in pmu:
            files["ev/" + d + "/type"] = "4"
        if tracefs:
            files["tracing/events/syscalls/sys_enter_futex/id"] = "300"
        for rel, text in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text + "\n")
        return dict(PROCSYS=root + "/sys", PROCSELF=root + "/self", EVSRC=root + "/ev",
                    TRACEFS=root + "/tracing", STRACE="/nonexistent/strace")



class PreflightTest(FakeBox):
    """perf_preflight on a fake tree."""

    def run_pf(self, env, metrics, **extra):
        r = self.lib('perf_preflight %s; echo "rc=$? level=$PERF_LEVEL user=$PERF_USER_ONLY c2c=$C2C_FORM '
                     'futex=$FUTEX_FORM"' % metrics, **dict(env, **extra))
        self.assertEqual(r.stderr, "")
        return r.stdout

    def test_paranoid_1_per_process_works(self):
        out = self.run_pf(self.box(1), "record c2c")
        self.assertIn("rc=0 level=task user=0 c2c=full", out)
        self.assertIn("record: ok (cycles)", out)
        self.assertIn("kernel-samples: ok", out)
        self.assertIn("kernel-symbols: unavailable (kptr_restrict=1", out)
        self.assertIn("futex: unavailable", out)
        out = self.run_pf(self.box(1), "record cpus")
        self.assertIn("rc=1", out)
        self.assertIn("perf_event_paranoid <= 0", out)

    def test_paranoid_2_is_user_space_only(self):
        out = self.run_pf(self.box(2), "record c2c")
        self.assertIn("rc=0 level=user user=1 c2c=user", out)
        self.assertIn("record: ok (cycles, user space only)", out)
        self.assertIn("kernel-samples: unavailable", out)
        out = self.run_pf(self.box(2, pmu=("cpu", "ibs_op", "ibs_op/format/swfilt")), "record c2c")
        self.assertIn("c2c=amd-user", out)

    def test_a_reason_in_two_parts_is_printed_whole(self):
        # _pf printed only its third argument: the strace half of the futex reason was lost.
        out = self.run_pf(self.box(1, scope=3), "futex cpus")
        self.assertIn("rc=1", out)
        self.assertIn("strace -p needs /nonexistent/strace and ptrace_scope 0, or CAP_SYS_PTRACE at ptrace_scope 1 or 2)",
                      out)
        self.assertIn("CAP_PERFMON in the initial user namespace)", out)

    def test_amd_user_space_c2c_needs_swfilt(self):
        # ibs_op without the swfilt format bit (before kernel 6.14): "ibs_op/swfilt=1/u" does not parse.
        out = self.run_pf(self.box(2, pmu=("cpu", "ibs_op")), "record c2c")
        self.assertIn("rc=1", out)
        self.assertIn("c2c: unavailable (user space only on AMD needs ibs_op/swfilt=1/u", out)
        out = self.run_pf(self.box(1, pmu=("cpu", "ibs_op")), "record c2c")   # perf c2c record, all modes
        self.assertIn("rc=0 level=task user=0 c2c=full", out)

    def test_paranoid_3_is_software_only(self):
        out = self.run_pf(self.box(3), "record")
        self.assertIn("rc=1", out)
        self.assertIn("software events only", out)
        out = self.run_pf(self.box(3), "record", PERF_EVENT="cpu-clock")
        self.assertIn("rc=0 level=software user=1", out)

    def test_paranoid_4_needs_a_capability(self):
        out = self.run_pf(self.box(4), "record")
        self.assertIn("rc=1 level=none", out)
        self.assertIn("needs CAP_PERFMON (CAP_SYS_ADMIN on an older Ubuntu kernel)", out)
        out = self.run_pf(self.box(4), "record", PERF_EVENT="cpu-clock")
        self.assertIn("rc=1", out, "software events need a capability at level 4 too")
        # a rootless container: the capability is in a user namespace, and perf ignores it
        out = self.run_pf(self.box(4, cap=self.PERFMON, uid_map=self.ROOTLESS), "record")
        self.assertIn("rc=1 level=none", out)
        # rootful with CAP_PERFMON: allowed, with a note on older Ubuntu kernels
        out = self.run_pf(self.box(4, cap=self.PERFMON), "record c2c cpus", KERNEL_RELEASE="6.8.0-136-generic")
        self.assertIn("rc=0 level=full user=0", out)
        self.assertIn("note: perf_event_paranoid=4 and CAP_PERFMON only. Enough on this kernel, 6.8.0-136-generic"
                      " (measured on pro4s-2).", out)
        out = self.run_pf(self.box(4, cap=self.SYS_ADMIN), "record")
        self.assertIn("rc=0 level=full", out)
        self.assertNotIn("note:", out)

    def test_level_4_note_names_only_the_measured_kernel(self):
        # The note said "Enough on Ubuntu 6.8.0-136 (measured)" on every kernel.
        out = self.run_pf(self.box(4, cap=self.PERFMON), "record", KERNEL_RELEASE="6.8.0-90-generic")
        self.assertIn("rc=0 level=full", out)
        self.assertIn("CAP_PERFMON only. Not measured on this kernel, 6.8.0-90-generic.", out)
        self.assertNotIn("measured on pro4s-2", out)

    def test_no_pmu(self):
        out = self.run_pf(self.box(1, pmu=()), "record c2c")
        self.assertIn("rc=1", out)
        self.assertIn("record: unavailable (cycles: no hardware PMU", out)
        self.assertIn("c2c: unavailable (no hardware PMU", out)
        out = self.run_pf(self.box(1, pmu=()), "record", PERF_EVENT="cpu-clock")
        self.assertIn("rc=0", out)

    def test_futex_forms(self):
        out = self.run_pf(self.box(-1, tracefs=True), "futex")
        self.assertIn("rc=0", out)
        self.assertIn("futex=tracepoint", out)
        out = self.run_pf(self.box(1, scope=1, cap=1 << 19), "futex", STRACE="sh")
        self.assertIn("futex=strace", out)
        out = self.run_pf(self.box(1, scope=1), "futex", STRACE="sh")
        self.assertIn("rc=1", out)


class ProfilePreflightTest(FakeBox):
    """profile.sh stops before unitd when the preflight fails, and writes no result."""

    def profile(self, box, tag="out", **extra):
        stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(stubs, exist_ok=True)
        calls = os.path.join(self.tmp, "perf.calls")
        if os.path.exists(calls):
            os.remove(calls)
        with open(os.path.join(stubs, "perf"), "w") as f:
            f.write('#!/bin/sh\necho "$*" >> "%s"\necho "1.00,msec,task-clock" >&2\n' % calls)
        os.chmod(os.path.join(stubs, "perf"), 0o755)
        out = os.path.join(self.tmp, tag)
        env = dict(self.env, **box)
        env.update(OUT=out, OHA="true", R=os.path.join(self.tmp, "r"), WWW=os.path.join(self.tmp, "www"),
                   TREES=os.path.join(self.tmp, "none"),
                   SYSCPU=os.path.join(self.tmp, "cpu") if os.path.isdir(os.path.join(self.tmp, "cpu"))
                   else self.topology(["0", "1"]),
                   PATH=stubs + os.pathsep + os.environ["PATH"], **extra)
        r = subprocess.run(["bash", os.path.join(KIT, "profile.sh")], capture_output=True, text=True, env=env,
                           timeout=60, stdin=subprocess.DEVNULL)
        perf = ""
        if os.path.exists(calls):
            with open(calls) as f:
                perf = f.read()
        return r, out, perf

    def test_probe_only_what_the_steps_use(self):
        # A strace-only profile ran "perf stat -e task-clock,cycles" and failed where cycles cannot count.
        box = self.box(1, scope=0)
        r, out, perf = self.profile(box, tag="futex", PROFILE="futex", PREFLIGHT_ONLY="1", STRACE="sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(perf, "", "a strace-only profile ran perf")
        self.assertIn("probe: not needed", r.stdout)
        r, out, perf = self.profile(box, tag="c2c", PROFILE="c2c", PREFLIGHT_ONLY="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(perf, "stat -x, -e task-clock -- true\n")
        r, out, perf = self.profile(self.box(-1, scope=0, tracefs=True), tag="tp", PROFILE="futex", PREFLIGHT_ONLY="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(perf, "stat -x, -e task-clock,syscalls:sys_enter_futex -- true\n")

    def test_unknown_scope_is_refused(self):
        # The check called die before lib.sh defined it: "die: command not found", and the run went on.
        r, out, perf = self.profile(self.box(1, scope=0), PERF_SCOPE="typo", PREFLIGHT_ONLY="1", STRACE="sh")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("PERF_SCOPE is pids or cpus, not 'typo'", r.stderr)
        self.assertNotIn("command not found", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "DONE")))
        self.assertEqual(perf, "")

    def test_unknown_step_is_refused(self):
        # "recrod" passed the preflight, no step ran, and DONE followed.
        for i, prof in enumerate(("recrod", "record futx", " ", "record\nrecrod")):
            r, out, perf = self.profile(self.box(1, scope=0), tag="out%d" % i, PROFILE=prof, PREFLIGHT_ONLY="1",
                                        STRACE="sh")
            self.assertEqual(r.returncode, 1, "PROFILE=%r: %s" % (prof, r.stdout + r.stderr))
            self.assertIn("the steps are record, futex and c2c", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(out, "DONE")), "PROFILE=%r wrote DONE" % prof)
            self.assertEqual(perf, "", "perf ran with PROFILE=%r" % prof)

    def test_steps_split_on_any_blank(self):
        # "record<TAB>futex" passed the check, but has() looked for " futex ", so the futex step never ran.
        r, out, perf = self.profile(self.box(1, scope=0), PROFILE="record\tfutex\n c2c ", PREFLIGHT_ONLY="1",
                                    STRACE="sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(perf, "stat -x, -e task-clock,cycles -- true\n")
        with open(os.path.join(out, "meta.txt")) as f:
            self.assertIn("PROFILE='record futex c2c'", f.read())

    def test_paranoid_4_stops_before_any_measurement(self):
        r, out, perf = self.profile(self.box(4))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("preflight failed", r.stderr)
        self.assertTrue(os.path.exists(os.path.join(out, "FAILED")))
        self.assertFalse(os.path.exists(os.path.join(out, "summary.txt")))
        self.assertEqual(perf, "", "perf ran after a failed preflight")

    def test_preflight_only(self):
        r, out, perf = self.profile(self.box(0, scope=0), PREFLIGHT_ONLY="1", STRACE="sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(out, "DONE")))
        with open(os.path.join(out, "summary.txt")) as f:
            self.assertIn("preflight: ok", f.read())
        self.assertIn("stat -x, -e task-clock,cycles -- true", perf)


class PerProcessTest(FakeBox):
    """The default scope is the kit's processes (-p); system-wide -a -C is an opt-in."""

    STUB = 'perf() { echo "perf $*" >&2; }\n'

    def test_perf_args(self):
        r = self.lib(self.STUB + 'perf_args pids 10,11 0,1; echo "${PERF_T[*]}|${PERF_U[*]}"\n'
                     'PERF_USER_ONLY=1 perf_args pids 10,11 0,1; echo "${PERF_T[*]}|${PERF_U[*]}"\n'
                     'perf_args cpus 10,11 0,1; echo "${PERF_T[*]}"')
        self.assertEqual(r.stdout.split("\n")[:-1], ["-p 10,11|", "-p 10,11|--all-user --user-callchains",
                                                       "-a -C 0,1"])

    def test_c2c_forms(self):
        got = []
        for form in ("full", "user", "amd-user"):
            err = os.path.join(self.tmp, form + ".err")
            r = self.lib(self.STUB + 'perf_args pids 10 0; C2C_FORM=%s c2c_record /d "$ERR" -- sleep 15' % form,
                         ERR=err)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(err) as f:
                got.append(f.read().strip())
        self.assertEqual(got, ["perf c2c record -p 10 -o /d -- sleep 15",
                               "perf c2c record --all-user -p 10 -o /d -- sleep 15",
                               "perf record -W -d --sample-cpu -e ibs_op/swfilt=1/u -p 10 -o /d -- sleep 15"])
        r = self.lib(self.STUB + 'C2C_FORM= c2c_record /d /dev/null -- sleep 15')
        self.assertNotEqual(r.returncode, 0)

    def test_default_scope_needs_no_system_wide_access(self):
        # perf_event_paranoid 1: an ordinary user cannot use -a, but -p works.
        stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(stubs)
        with open(os.path.join(stubs, "perf"), "w") as f:
            f.write('#!/bin/sh\necho "1.00,msec,task-clock" >&2\n')
        os.chmod(os.path.join(stubs, "perf"), 0o755)
        env = dict(self.env, **self.box(1, scope=0))
        env.update(OHA="true", R=os.path.join(self.tmp, "r"), WWW=os.path.join(self.tmp, "www"),
                   TREES=os.path.join(self.tmp, "none"), SYSCPU=self.topology(["0", "1"]),
                   PATH=stubs + os.pathsep + os.environ["PATH"], PREFLIGHT_ONLY="1", STRACE="sh")
        for scope, rc in (("", 0), ("cpus", 1)):
            env["OUT"] = os.path.join(self.tmp, "out-" + (scope or "default"))
            env["PERF_SCOPE"] = scope
            r = subprocess.run(["bash", os.path.join(KIT, "profile.sh")], capture_output=True, text=True,
                               env=env, timeout=60, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, rc, scope + ": " + r.stdout + r.stderr)
        self.assertIn("cpus: unavailable (system-wide mode needs perf_event_paranoid <= 0", r.stdout)


class FakeKitTest(FakeBox):
    """profile.sh end to end on a fake kit. unitd is a script that starts a "unit: router"
    process under TREES. curl, perf, oha and strace are stubs."""

    ROUTER = r"""
#include <signal.h>
#include <unistd.h>
static void quit(int s) { (void) s; _exit(0); }
int main(void) { signal(SIGQUIT, quit); sleep(60); return 0; }
"""
    UNITD = """#!/usr/bin/env bash
while [ $# -gt 0 ]; do
    case $1 in --pid) pidf=$2; shift ;; --control) ctl=${2#unix:}; shift ;; esac
    shift
done
# bind in the directory: the absolute path can be longer than a unix socket path
(cd "$(dirname "$ctl")" && python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' \\
    "$(basename "$ctl")")
(exec -a "unit: router" "$(dirname "$0")/router") > /dev/null 2>&1 &
echo $! > "$pidf"
"""
    CURL = """#!/bin/sh
case "$*" in *"-X PUT"*) echo '{"success": "Reconfiguration done."}' ;; *http_code*) printf 200 ;; esac
"""
    # PERF_FAIL: a perf subcommand (record, report, stat, c2c) that fails
    PERF = """#!/bin/sh
echo "$*" >> "$PERF_CALLS"
case " ${PERF_FAIL:-} " in *" $1 "*) echo "perf $1: failed" >&2; exit 1 ;; esac
case $1 in
    report) printf '    50.00%%  pthread_mutex_lock\\n    50.00%%  main\\n' ;;
    *) echo "1.00,msec,task-clock" >&2 ;;
esac
"""
    OHA = """#!/bin/sh
echo '{"summary": {"requestsPerSec": 100.0}, "statusCodeDistribution": {"200": 1000}}'
"""

    def setUp(self):
        super().setUp()
        cc = shutil.which("cc")
        if cc is None or shutil.which("taskset") is None:
            self.skipTest("no cc or no taskset")
        self.trees = os.path.join(self.tmp, "trees")
        sbin = os.path.join(self.trees, "master", "build", "sbin")
        os.makedirs(sbin)
        src = os.path.join(self.tmp, "router.c")
        with open(src, "w") as f:
            f.write(self.ROUTER)
        subprocess.run([cc, "-o", os.path.join(sbin, "router"), src], check=True)
        self.stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(self.stubs)
        for path, text in ((os.path.join(sbin, "unitd"), self.UNITD), (os.path.join(self.stubs, "curl"), self.CURL),
                           (os.path.join(self.stubs, "perf"), self.PERF), (os.path.join(self.stubs, "oha"), self.OHA)):
            self.script(path, text)
        self.calls = os.path.join(self.tmp, "perf.calls")

    def script(self, path, text):
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)
        return path

    def run_kit(self, box, **extra):
        out = os.path.join(self.tmp, "out")
        env = dict(self.env, **box)
        env.update(OUT=out, OHA=os.path.join(self.stubs, "oha"), R=os.path.join(self.tmp, "r"),
                   WWW=os.path.join(self.tmp, "www"), TREES=self.trees, SYSCPU=self.topology(["0", "1"]),
                   PATH=self.stubs + os.pathsep + os.environ["PATH"], PERF_CALLS=self.calls, WORKLOADS="capp",
                   PERF_SEC="1", WARM="1")
        env.update(extra)
        r = subprocess.run(["bash", os.path.join(KIT, "profile.sh")], capture_output=True, text=True, env=env,
                           timeout=120, stdin=subprocess.DEVNULL)

        def read(name):
            p = os.path.join(out, name)
            if not os.path.exists(p):
                return None
            with open(p) as f:
                return f.read()
        return r, read

    def test_strace_that_cannot_attach_is_a_failure(self):
        # "bash -c 'timeout ... strace ...; true'" hid every strace failure.
        strace = self.script(os.path.join(self.tmp, "strace"),
                             '#!/bin/sh\necho "strace: attach: ptrace(PTRACE_SEIZE): Operation not permitted" >&2\n'
                             'exit 1\n')
        r, read = self.run_kit(self.box(1, scope=0), PROFILE="futex", STRACE=strace)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNotNone(read("DONE"))
        self.assertIn("FAILED: strace futex capp", read("failures.txt") or "")
        self.assertIn("Operation not permitted", read("futex-capp.err") or "")

    def test_failed_record_and_load_give_no_result(self):
        # A failed perf record and report gave "0.00% of 0.00% sampled", and a failed load was not counted.
        r, read = self.run_kit(self.box(1, scope=0), PROFILE="record", PERF_FAIL="record report", OHA="true")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        summary, fails = read("summary.txt") or "", read("failures.txt") or ""
        self.assertNotIn("% of ", summary)
        self.assertIn("capp: lock share: FAILED", summary)
        self.assertIsNone(read("lockshare-capp.txt"))
        for line in ("FAILED: perf record capp", "FAILED: lock share capp", "FAILED: load capp"):
            self.assertIn(line, fails)

    def test_record_gives_the_lock_share(self):
        r, read = self.run_kit(self.box(1, scope=0), PROFILE="record")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(read("failures.txt"), "")
        self.assertIn("capp: lock-related symbols: 50.00% of 100.00% sampled; load 1000 0 100.0", read("summary.txt"))

    # A strace stub: SIGINT (default disposition restored, a background job inherits it ignored)
    # writes OUT unless MODE is "noout", then exits with MODE ("die" re-raises SIGINT, as strace 6.19).
    STUB = """#!/usr/bin/env python3
import os, signal, sys, time
a = sys.argv[1:]
out = a[a.index("-o") + 1]
mode = os.environ.get("STUB_MODE", "0")
pidfile = os.environ.get("STUB_PIDFILE")
if pidfile:
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))
def on_int(sig, frame):
    if mode != "noout":
        with open(out, "w") as f:
            f.write("calls syscall\\n 10 futex\\n")
    if mode == "die":
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGINT)
    sys.exit(0 if mode == "noout" else int(mode))
signal.signal(signal.SIGINT, on_int)
time.sleep(30)
"""

    def stub(self):
        return self.script(os.path.join(self.tmp, "strace"), self.STUB)

    def strace_futex(self, sec="1", **env):
        for name in ("out", "err"):
            if os.path.exists(os.path.join(self.tmp, name)):
                os.remove(os.path.join(self.tmp, name))
        r = self.lib('strace_futex 1 "$SEC" "$TMPD/out" "$TMPD/err"', STRACE=os.path.join(self.tmp, "strace"),
                     TMPD=self.tmp, SEC=sec, **env)
        err = ""
        if os.path.exists(os.path.join(self.tmp, "err")):
            with open(os.path.join(self.tmp, "err")) as f:
                err = f.read()
        return r.returncode, err

    def test_strace_futex_needs_the_sigint_its_status_and_out(self):
        # A strace that failed after the SIGINT, or wrote no OUT, was a success.
        self.stub()
        for mode, rc, why in (("0", 0, ""), ("die", 0, ""), ("3", 1, "exited 3 after the SIGINT"),
                              ("noout", 1, "wrote no")):
            got, err = self.strace_futex(STUB_MODE=mode)
            self.assertEqual(got, rc, "mode %s: %s" % (mode, err))
            self.assertIn(why, err)

    def test_strace_futex_fails_on_an_early_exit(self):
        stub = os.path.join(self.tmp, "strace")
        for code in (0, 1):
            self.script(stub, "#!/bin/sh\nexit %d\n" % code)
            got, err = self.strace_futex()
            self.assertEqual(got, 1, err)
            self.assertIn("strace_futex: %s exited %d before 1 s" % (stub, code), err)

    def test_strace_futex_needs_a_positive_sec(self):
        # SEC=0 sent the SIGINT at once and gave success; "abc" stopped the caller under set -u.
        self.stub()
        for sec in ("0", "abc", "", "-1"):
            got, err = self.strace_futex(sec, STUB_MODE="0")
            self.assertEqual(got, 1, "SEC=%r: %s" % (sec, err))
            self.assertIn("SEC is a positive integer", err)

    def test_strace_futex_kills_a_strace_that_ignores_int(self):
        # "timeout -s INT" without -k waited for such a strace to end by itself, and then gave 124.
        self.script(os.path.join(self.tmp, "strace"), "#!/bin/sh\ntrap '' INT\nexec sleep 20\n")
        t0 = time.monotonic()
        got, err = self.strace_futex()
        took = time.monotonic() - t0
        self.assertEqual(got, 1, err)
        self.assertLess(took, 1 + 6, "strace_futex took %.1f s" % took)
        self.assertIn("did not stop within 5 s of SIGINT", err)

    def test_strace_futex_dies_with_its_caller(self):
        # The strace outlived a caller that got SIGTERM. The caller's traps must still run.
        self.stub()
        pidfile = os.path.join(self.tmp, "stub.pid")
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            if os.path.exists(pidfile):
                os.remove(pidfile)
            env = dict(self.env, LIB=LIB, STRACE=os.path.join(self.tmp, "strace"), TMPD=self.tmp,
                       STUB_PIDFILE=pidfile)
            log = os.path.join(self.tmp, "caller.log")
            with open(log, "w") as f:   # a file, not a pipe: an orphan strace would hold a pipe open
                p = subprocess.Popen(["bash", "-c", '. "$LIB"\ntrap "echo caller-exit" EXIT\n'
                                      'strace_futex 1 20 "$TMPD/out" "$TMPD/err"\necho returned'],
                                     stdout=f, env=env, stdin=subprocess.DEVNULL)
            for _ in range(100):
                if os.path.exists(pidfile) and os.path.getsize(pidfile):
                    break
                time.sleep(0.05)
            with open(pidfile) as f:
                stub = int(f.read())
            self.addCleanup(lambda s=stub: os.path.exists("/proc/%d" % s) and os.kill(s, signal.SIGKILL))
            p.send_signal(sig)
            p.wait(timeout=10)
            with open(log) as f:
                out = f.read()
            gone = False
            for _ in range(40):
                if not RunStageTest.alive(stub):
                    gone = True
                    break
                time.sleep(0.05)
            self.assertTrue(gone, "%s: the strace %d outlived its caller" % (sig.name, stub))
            self.assertEqual(p.returncode, -sig, "%s: %r" % (sig.name, out))
            self.assertIn("caller-exit", out, sig.name)
            self.assertNotIn("returned", out, sig.name)

    def test_strace_futex_gives_back_the_traps_of_the_caller(self):
        self.stub()
        r = self.lib('trap "echo mine" TERM\nstrace_futex 1 1 "$TMPD/out" "$TMPD/err"; echo "rc=$?"\ntrap -p TERM EXIT',
                     STRACE=os.path.join(self.tmp, "strace"), TMPD=self.tmp, STUB_MODE="0")
        self.assertEqual(r.stdout, "rc=0\ntrap -- 'echo mine' SIGTERM\n", r.stderr)

    TARGET = """
import ctypes, os, sys, threading, time
ctypes.CDLL(None).prctl(0x59616d61, ctypes.c_ulong(2 ** 64 - 1), 0, 0, 0)   # PR_SET_PTRACER_ANY
lock = threading.Lock()
def spin():
    while True:
        with lock:
            time.sleep(0.001)
for _ in range(3):
    threading.Thread(target=spin, daemon=True).start()
print(os.getpid(), flush=True)
time.sleep(30)
"""

    def test_strace_futex_with_a_real_strace(self):
        # The real strace must stop on SIGINT as a background job and write its -c table.
        if shutil.which("strace") is None:
            self.skipTest("no strace")
        p = subprocess.Popen([sys.executable, "-I", "-c", self.TARGET], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        pid = p.stdout.readline().strip()
        r = self.lib('strace_futex "$P" 1 "$TMPD/out" "$TMPD/err"', STRACE="strace", TMPD=self.tmp, P=pid)
        with open(os.path.join(self.tmp, "err")) as f:
            err = f.read()
        if r.returncode and "ptrace" in err:
            self.skipTest("ptrace refused here: " + err.strip())
        self.assertEqual(r.returncode, 0, err)
        with open(os.path.join(self.tmp, "out")) as f:
            self.assertIn("futex", f.read())


class SweepNameTest(Base):
    """sweep.sh builds a path from each variant and workload and runs rm -rf on it."""

    def test_a_variant_or_workload_that_is_a_path_is_refused(self):
        shim = os.path.join(self.tmp, "mutexshim.so")
        open(shim, "w").close()
        out = os.path.join(self.tmp, "out")
        os.makedirs(os.path.join(out, "shim"))   # as an earlier cell of the run leaves it
        for variants, workloads, victim in (("../../victim", "capp", "victim-capp-1-1"),
                                            ("master", "/../../../victim", "victim-1-1")):
            keep = os.path.join(self.tmp, victim, "keep")
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            open(keep, "w").close()
            env = dict(self.env, OUT=out, SHIM=shim, OHA="true", LOCKS="1", VARIANTS=variants, WORKLOADS=workloads,
                       NS="1", LRUNS="1", LDUR="1", WARM="1", R=os.path.join(self.tmp, "r"),
                       WWW=os.path.join(self.tmp, "www"), TREES=os.path.join(self.tmp, "none"),
                       SYSCPU=os.path.join(self.tmp, "cpu") if os.path.isdir(os.path.join(self.tmp, "cpu"))
                       else self.topology(["0", "1"]))
            r = subprocess.run(["bash", os.path.join(KIT, "sweep.sh")], capture_output=True, text=True, env=env,
                               timeout=60, stdin=subprocess.DEVNULL)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.exists(keep), "VARIANTS=%r WORKLOADS=%r removed %s" % (variants, workloads, keep))
            self.assertIn("letters, digits", r.stderr)


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


class NoLoadTest(Base):
    """A run whose load failed is a failure, never a zero-throughput sample."""

    HEAD = "variant\tworkload\tn\tlayout\trun\tok\tbad\trps\tp50_ms\tp99_ms\tsrv_cpu_pct\tload_cpu_pct\tts\n"

    def tsv(self, a_rps, b_rps):
        p = os.path.join(self.tmp, "sweep.tsv")
        with open(p, "w") as f:
            f.write(self.HEAD)
            for v, rps in (("a", a_rps), ("b", b_rps)):
                for i, x in enumerate(rps):
                    ok = int(x * 30) if x not in ("NA", 0) else x
                    f.write(f"{v}\tcapp\t1\tclean\t{i + 1}\t{ok}\t0\t{x}\t1\t2\t50\t50\t{1000 + i}\n")
        return p

    def analyze(self, path, pairs):
        return subprocess.run([sys.executable, os.path.join(KIT, "analyze.py"), path, pairs],
                              capture_output=True, text=True, timeout=60)

    def test_read_oha_fails_on_a_missing_result(self):
        r = self.lib('read_oha "$TMPD/none.json"', TMPD=self.tmp)
        self.assertNotEqual(r.returncode, 0, "read_oha gave %r for a missing file" % r.stdout)
        self.assertEqual(r.stdout, "")

    def test_zero_side_gets_no_verdict(self):
        a = module("analyze")
        rows = lambda v, rps: [{"variant": v, "run": str(i + 1), "rps": str(x)} for i, x in enumerate(rps)]
        verdict = a.ratio(rows("a", [100] * 5), rows("b", [0] * 5))[4]
        self.assertEqual(verdict, "NO LOAD")
        r = self.analyze(self.tsv([100] * 5, [0, 0, 0, 0, "NA"]), "b:a")
        self.assertNotIn("slower", r.stdout)
        self.assertIn("NO LOAD", r.stdout)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    def test_all_zero_exits_with_a_message(self):
        r = self.analyze(self.tsv([0] * 5, [0] * 5), "a:b")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("no load", r.stderr)

    def test_failed_analysis_leaves_no_summary(self):
        out = os.path.join(self.tmp, "out")
        os.mkdir(out)
        r = self.lib('finish_summary "$OUT/summary.txt" python3 "$KIT/analyze.py" "$TSV" a:b',
                     OUT=out, KIT=KIT, TSV=self.tsv([0] * 5, [0] * 5))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "summary.txt")), "a failed analysis left summary.txt")
        self.assertFalse(os.path.exists(os.path.join(out, "DONE")))
        self.assertTrue(os.path.exists(os.path.join(out, "FAILED")))
        r = self.lib('finish_summary "$OUT/summary.txt" python3 "$KIT/analyze.py" "$TSV" b:a',
                     OUT=out, KIT=KIT, TSV=self.tsv([100] * 5, [101] * 5))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(out, "DONE")))
        self.assertFalse(os.path.exists(os.path.join(out, "FAILED")))


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
        # an app process: 10 calls at a, 40 at b
        self.shim(os.path.join(d, "a"), 11, 65534, [(0x403000, 10, 0)])
        self.shim(os.path.join(d, "b"), 11, 65534, [(0x403000, 40, 0)])
        return d

    def test_counts_are_the_window_only(self):
        rows = module("locks").diff(self.run_dir(), "v", "capp", "1", "1", "100")
        got = {r[5]: (int(r[7]), int(r[8])) for r in rows}
        self.assertEqual(got, {"router": (300, 3), "app": (30, 0)})

    def test_a_cell_without_the_first_snapshot_has_no_counts(self):
        # Only b/ has files: b minus nothing holds the start-up and the warm-up, with no mark.
        d = os.path.join(self.tmp, "only-b")
        self.shim(os.path.join(d, "b"), 10, 65534, [(0x401000, 250, 4)])
        os.makedirs(os.path.join(d, "a", "x"))
        rows = module("locks").diff(d, "v", "capp", "1", "1", "100")
        self.assertEqual([r[7:] for r in rows], [["NA", "NA", "NA"]])
        tsv = os.path.join(self.tmp, "locks.tsv")
        with open(tsv, "w") as f:
            f.write("variant\tworkload\tn\trun\tok\trole\tpid\tcalls\tcontended\twait_ns\n")
            f.write("\t".join(rows[0]) + "\n")
        self.assertEqual(module("locks").summary(tsv)[1], "capp 1 v 1 gone NO COUNTS")

    def test_a_pid_without_a_first_snapshot_has_no_counts(self):
        # a={10: 100}, b={10: 150, 11: 250}: pid 11 got 250, though it may have counted 120 before a.
        d = os.path.join(self.tmp, "new-pid")
        self.shim(os.path.join(d, "a"), 10, 0, [(0x401000, 100, 2)])
        self.shim(os.path.join(d, "b"), 10, 0, [(0x401000, 150, 3)])
        self.shim(os.path.join(d, "b"), 11, 0, [(0x401000, 250, 4)])
        rows = module("locks").diff(d, "v", "capp", "1", "1", "100")
        self.assertEqual([r[6:] for r in rows], [["10", "50", "1", "1000"], ["11", "NA", "NA", "NA"]])

    def test_sites_without_a_first_snapshot_has_no_counts(self):
        # sites counted b from 0 when a was missing, while diff said NA.
        L = module("locks")
        d = os.path.join(self.tmp, "only-b-sites")
        self.shim(os.path.join(d, "b"), 10, 0, [(0x401000, 250, 4)])
        os.makedirs(os.path.join(d, "a"))
        with open(os.path.join(d, "oha.json"), "w") as f:
            f.write('{"statusCodeDistribution": {"200": 100}}')
        lines = L.sites(d)
        self.assertEqual(lines[:2], ["ok 100", "role    calls/req contended/req  site"])
        self.assertEqual(lines[2:], ["gone    NO COUNTS (pid 10: no first snapshot)"])
        # a pid missing in a, next to one that is there
        self.shim(os.path.join(d, "a"), 12, 0, [(0x401000, 100, 0)])
        self.shim(os.path.join(d, "b"), 12, 0, [(0x401000, 300, 0)])
        lines = L.sites(d)
        self.assertIn("gone    NO COUNTS (pid 10: no first snapshot)", lines)
        self.assertNotIn("2.500", "\n".join(lines))
        self.assertTrue(any(ln.startswith("gone        2.000") for ln in lines), lines)

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

    def test_summary_of_a_run_without_load(self):
        tsv = os.path.join(self.tmp, "locks.tsv")
        with open(tsv, "w") as f:
            f.write("variant\tworkload\tn\trun\tok\trole\tpid\tcalls\tcontended\twait_ns\n")
            f.write("v\tcapp\t1\t1\tNA\trouter\t10\t300\t3\t3000\n")
            f.write("v\tcapp\t1\t2\t0\trouter\t12\t300\t3\t3000\n")
        r = subprocess.run([sys.executable, os.path.join(KIT, "locks.py"), "summary", tsv],
                           capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stdout.split("\n")[1:3], ["capp 1 v 1 router NO LOAD", "capp 1 v 2 router NO LOAD"])

    def test_a_cell_without_snapshots_keeps_its_row(self):
        # A failed load with no shim file gave no row: summary then exited 0 and DONE followed.
        script = os.path.join(KIT, "locks.py")
        tsv = os.path.join(self.tmp, "locks.tsv")
        with open(tsv, "w") as f:
            f.write("variant\tworkload\tn\trun\tok\trole\tpid\tcalls\tcontended\twait_ns\n")
        for run, ok in (("1", "NA"), ("2", "100")):
            d = os.path.join(self.tmp, "cell" + run)
            os.makedirs(d)
            with open(tsv, "a") as f:
                r = subprocess.run([sys.executable, script, "diff", d, "v", "capp", "1", run, ok], stdout=f,
                                   timeout=60)
            self.assertEqual(r.returncode, 0)
        r = subprocess.run([sys.executable, script, "summary", tsv], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stdout.split("\n")[1:3], ["capp 1 v 1 none NO LOAD", "capp 1 v 2 none NO COUNTS"])


class ShimSiteTest(Base):
    """mutexshim.so and locks.py sites name the function that locks.

    A direct pthread_mutex_lock() call (libunit does this) is its own site. A call
    through nxt_thread_mutex_lock() is attributed to the caller of that wrapper.
    """

    PROG = r"""
#include <pthread.h>
#include <unistd.h>
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
__attribute__((noinline)) int nxt_thread_mutex_lock(pthread_mutex_t *mx)
{ int rc = pthread_mutex_lock(mx); __asm__ volatile("" ::: "memory"); return rc; }
__attribute__((noinline)) void wrapper_caller(void)
{ for (int i = 0; i < 1000; i++) { nxt_thread_mutex_lock(&m); pthread_mutex_unlock(&m); } __asm__ volatile(""); }
__attribute__((noinline)) void direct_lock(void)
{ for (int i = 0; i < 1000; i++) { pthread_mutex_lock(&m); pthread_mutex_unlock(&m); } __asm__ volatile(""); }
__attribute__((noinline)) void outer(void) { direct_lock(); __asm__ volatile(""); }
int main(void) { wrapper_caller(); outer(); sleep(2); return 0; }
"""

    def test_sites_name_the_locking_function(self):
        cc = shutil.which("cc")
        if cc is None or shutil.which("addr2line") is None:
            self.skipTest("no cc or no addr2line")
        shim = os.path.join(self.tmp, "mutexshim.so")
        subprocess.run([cc, "-O2", "-fno-omit-frame-pointer", "-shared", "-fPIC", "-o", shim,
                        os.path.join(KIT, "mutexshim.c"), "-ldl", "-lpthread"], check=True)
        src = os.path.join(self.tmp, "prog.c")
        with open(src, "w") as f:
            f.write(self.PROG)
        prog = os.path.join(self.tmp, "lab_capp")
        subprocess.run([cc, "-O0", "-fno-omit-frame-pointer", "-o", prog, src, "-lpthread"], check=True)
        d = os.path.join(self.tmp, "run")
        for sub in ("a", "b"):
            os.makedirs(os.path.join(d, sub))
        p = subprocess.run([prog], env=dict(self.env, LD_PRELOAD=shim, MUTEXSHIM_OUT=os.path.join(d, "b")),
                           timeout=30)
        self.assertEqual(p.returncode, 0)
        files = os.listdir(os.path.join(d, "b"))
        self.assertTrue(files, "the shim wrote no file")
        pid = files[0].split("-")[1]
        # snapshot a: the file of the pid before any lock call (the shim writes one with no line)
        open(os.path.join(d, "a", files[0]), "w").close()
        with open(os.path.join(d, "b", "roles.txt"), "w") as f:
            f.write(pid + " " + prog + "\n")
        with open(os.path.join(d, "oha.json"), "w") as f:
            f.write('{"statusCodeDistribution": {"200": 1000}}')
        lines = module("locks").sites(d)
        text = "\n".join(lines)
        named = {ln.split()[3] for ln in lines[2:]}
        self.assertIn("direct_lock", named, text)
        self.assertIn("wrapper_caller", named, text)
        self.assertNotIn("outer", named, text)


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
            f.write('#!/bin/sh\necho "docker stub: $*" >&2\n[ -z "${DOCKER_LOG:-}" ] || echo "$*" >> "$DOCKER_LOG"\n'
                    'exit "${DOCKER_RC:-1}"\n')
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
                try:
                    if not self.alive(pid) or "sleep" not in open("/proc/%s/cmdline" % pid).read():
                        continue
                except OSError:
                    continue
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
        # The holder process must not keep the claim lock: tools/perf-lab/remote.sh takes it too.
        lock = subprocess.run(["flock", "-w", "1", os.path.join(self.claims, ".perflab.lock"), "true"],
                              capture_output=True)
        self.assertEqual(lock.returncode, 0, "the claim lock is still held after run.sh claim")
        r = self.run_sh("claim")
        self.assertNotEqual(r.returncode, 0, "a second claim passed")
        holder = [x[4:] for x in text.split("\n") if x.startswith("pid=")][0]
        r = self.run_sh("release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([f for f in os.listdir(self.claims) if f.startswith("CLAIMED-")], [])
        for _ in range(20):
            if not self.alive(holder):
                break
            time.sleep(0.1)
        self.assertFalse(self.alive(holder), "release left the holder %s running" % holder)

    @staticmethod
    def alive(pid):
        try:
            with open("/proc/%s/stat" % pid) as f:
                return f.read().rsplit(") ", 1)[1][0] != "Z"
        except OSError:
            return False

    def test_release_kills_only_the_holder(self):
        # The claim expired an hour ago and its pid now belongs to another process.
        other = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (other.kill(), other.wait()))
        claim = os.path.join(self.claims, "CLAIMED-perflab-scaling-20261019T000000")
        now = int(time.time())
        with open(claim, "w") as f:
            f.write("format=perflab-1\nwho=t\npurpose=scaling kit\nhost=t\npid=%d\nstarted=%d\nexpires=%d\n"
                    % (other.pid, now - 3600, now - 3300))
        rec = os.path.join(self.tmp, "results", "scaling")
        os.makedirs(rec)
        with open(os.path.join(rec, ".claim"), "w") as f:
            f.write("claim=%s\npid=%d\n" % (claim, other.pid))
        r = self.run_sh("release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(claim), "release kept the claim file")
        time.sleep(0.2)
        self.assertIsNone(other.poll(), "release killed pid %d, which is not the holder" % other.pid)

    @staticmethod
    def start_time(pid):
        """The start of pid in epoch seconds, as run.sh release computes it."""
        with open("/proc/%d/stat" % pid) as f:
            st = int(f.read().rsplit(") ", 1)[1].split()[19])
        with open("/proc/stat") as f:
            btime = int([x for x in f if x.startswith("btime ")][0].split()[1])
        return btime + st // os.sysconf("SC_CLK_TCK")

    def test_release_keeps_a_sleep_started_4_s_off(self):
        # A real holder starts within -1..0 s of "started". The window was +-5 s.
        rec = os.path.join(self.tmp, "results", "scaling")
        os.makedirs(rec)
        for off in (4, -4):
            other = subprocess.Popen(["sleep", "60"])
            self.addCleanup(lambda p=other: (p.kill(), p.wait()))
            time.sleep(0.1)
            claim = os.path.join(self.claims, "CLAIMED-perflab-scaling-20261019T00000%d" % (off > 0))
            now = int(time.time())
            with open(claim, "w") as f:
                f.write("format=perflab-1\nwho=t\npurpose=scaling kit\nhost=t\npid=%d\nstarted=%d\nexpires=%d\n"
                        % (other.pid, self.start_time(other.pid) - off, now + 600))
            with open(os.path.join(rec, ".claim"), "w") as f:
                f.write("claim=%s\npid=%d\n" % (claim, other.pid))
            r = self.run_sh("release")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("is not the holder", r.stdout)
            time.sleep(0.2)
            self.assertIsNone(other.poll(), "release killed a sleep that started %d s off the claim" % off)

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

    def test_watch_does_not_accept_a_failed_run(self):
        out = os.path.join(self.tmp, "rw", "scaling", "results", "t1")
        os.makedirs(out)
        with open(os.path.join(out, "summary.txt"), "w") as f:
            f.write("## medians per cell\n| capp | 1 |\n")   # the partial table of a failed analyzer
        with open(os.path.join(out, "FAILED"), "w") as f:
            f.write("analyze.py exited 1\n")
        r = self.run_sh("watch", "t1")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("done:", r.stdout)

    def docker_run(self, *args, **env):
        log = os.path.join(self.tmp, "docker.log")
        if os.path.exists(log):
            os.remove(log)
        self.env.update(DOCKER_LOG=log, DOCKER_RC="0", **env)
        r = self.run_sh(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(log) as f:
            return f.read()

    def test_container_capabilities(self):
        # profile.sh needs PERFMON (perf) and SYS_PTRACE (strace); a sweep and a locks run need neither.
        for stage in ("sweep", "locks"):
            line = self.docker_run(stage, "s1")
            self.assertNotIn("--cap-add", line, stage)
            self.assertNotIn("--privileged", line, stage)
        prof = self.docker_run("profile", "p1")
        self.assertIn("--cap-add PERFMON --cap-add SYS_PTRACE --security-opt no-new-privileges ", prof)
        # container root: with --user the capabilities are not effective
        self.assertNotIn("--user", prof)
        self.assertNotIn("--privileged", prof)
        self.assertNotIn("SYSLOG", prof)
        self.assertNotIn("/sys/kernel/tracing", prof)
        # kernel symbols and the futex tracepoint only on request
        prof = self.docker_run("profile", "p2", PERF_KERNEL="1")
        self.assertIn("--cap-add SYSLOG", prof)
        # read-only: perf reads the event id and format; the container must not write the host's tracefs
        self.assertIn("-v /sys/kernel/tracing:/sys/kernel/tracing:ro ", prof)

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

    def test_sweep_and_locks_refuse_a_variant_that_is_a_path(self):
        log = os.path.join(self.tmp, "docker.log")
        for stage in ("sweep", "locks"):
            for knob, value in (("VARIANTS", "master ../trees"), ("WORKLOADS", "capp a/b"), ("VARIANTS", "*")):
                env = dict(self.env, DOCKER_LOG=log, DOCKER_RC="0", **{knob: value})
                r = subprocess.run(["sh", os.path.join(KIT, "run.sh"), stage, "t1"], capture_output=True, text=True,
                                   env=env, timeout=60, stdin=subprocess.DEVNULL)
                self.assertNotEqual(r.returncode, 0, "%s %s=%r" % (stage, knob, value))
                self.assertIn("letters, digits", r.stderr, "%s %s=%r: %s" % (stage, knob, value, r.stdout))
                self.assertFalse(os.path.exists(log), "%s %s=%r started a container" % (stage, knob, value))

    def test_profile_refuses_a_variant_that_is_a_path(self):
        # run.sh profile passed V and WORKLOADS to the container unchecked.
        log = os.path.join(self.tmp, "docker.log")
        for knob, value in (("V", "../trees"), ("WORKLOADS", "capp a/b"), ("V", "*")):
            env = dict(self.env, DOCKER_LOG=log, DOCKER_RC="0", **{knob: value})
            r = subprocess.run(["sh", os.path.join(KIT, "run.sh"), "profile", "p1"], capture_output=True, text=True,
                               env=env, timeout=60, stdin=subprocess.DEVNULL)
            self.assertNotEqual(r.returncode, 0, "%s=%r" % (knob, value))
            self.assertIn("letters, digits", r.stderr, "%s=%r: %s" % (knob, value, r.stdout))
            self.assertFalse(os.path.exists(log), "%s=%r started a container" % (knob, value))

    def test_usage(self):
        r = self.run_sh()
        self.assertEqual(r.returncode, 2)
        self.assertIn("run.sh claim", r.stdout)


if __name__ == "__main__":
    unittest.main()
