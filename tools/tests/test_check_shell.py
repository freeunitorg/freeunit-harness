"""Tests of tools/check-shell.py: which files it picks, and the polyglot cut."""
import importlib.util, os, subprocess, sys, tempfile, unittest

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(TOOLS, "check-shell.py")
spec = importlib.util.spec_from_file_location("check_shell", SCRIPT)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


class ShebangTest(unittest.TestCase):
    def test_shell_shebangs_match(self):
        for line in (b"#!/bin/sh\n", b"#!/bin/bash\n", b"#!/usr/bin/env bash\n", b"#! /bin/sh -eu\n",
                     b"#!/bin/dash\n", b"#!/bin/ash"):
            self.assertTrue(cs.SHEBANG.match(line), line)

    def test_other_shebangs_do_not_match(self):
        for line in (b"#!/usr/bin/env python3\n", b"#!/bin/zsh\n", b"#!/usr/bin/fish\n",
                     b"#!/usr/bin/bashdb\n", b"# sh\n", b""):
            self.assertFalse(cs.SHEBANG.match(line), line)


class ShellPartTest(unittest.TestCase):
    def test_plain_script_is_kept_whole(self):
        data = b"#!/bin/sh\necho hi\n"
        self.assertIs(cs.shell_part(data), data)

    def test_polyglot_is_cut_before_the_marker(self):
        data = b"#!/bin/sh\nexec python3 x\nexit 2\n#--PY--\nimport os\nif (a): pass\n"
        self.assertEqual(cs.shell_part(data), b"#!/bin/sh\nexec python3 x\nexit 2\n")

    def test_marker_inside_a_line_does_not_cut(self):
        data = b"#!/bin/sh\necho '#--PY--'\n"
        self.assertIs(cs.shell_part(data), data)


class RepoTest(unittest.TestCase):
    def test_list_picks_tracked_shell_scripts_only(self):
        with tempfile.TemporaryDirectory() as d:
            files = {"a.sh": b"#!/bin/sh\n:\n", "b": b"#!/usr/bin/env bash\n:\n",
                     "c.py": b"#!/usr/bin/env python3\n", "lib.sh": b"# sourced, no shebang\n"}
            for name, data in files.items():
                with open(os.path.join(d, name), "wb") as f:
                    f.write(data)
            with open(os.path.join(d, "untracked.sh"), "wb") as f:
                f.write(b"#!/bin/sh\n")
            subprocess.run(["git", "init", "-q", d], check=True)
            subprocess.run(["git", "-C", d, "add"] + sorted(files), check=True)
            os.symlink("a.sh", os.path.join(d, "link.sh"))
            subprocess.run(["git", "-C", d, "add", "link.sh"], check=True)
            self.assertEqual(cs.shell_files(d), ["a.sh", "b"])


class SeverityTest(unittest.TestCase):
    def run_check(self, *args, stub_rc=0):
        # A stub shellcheck records its arguments and exits with stub_rc, so the test needs no real shellcheck.
        # shellcheck exits 1 when it reports a finding at the selected severity.
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.sh"), "w") as f:
                f.write("#!/bin/sh\n:\n")
            subprocess.run(["git", "init", "-q", d], check=True)
            subprocess.run(["git", "-C", d, "add", "a.sh"], check=True)
            stub, log = os.path.join(d, "bin", "shellcheck"), os.path.join(d, "argv.log")
            os.mkdir(os.path.dirname(stub))
            with open(stub, "w") as f:
                f.write('#!/bin/sh\n[ "$1" = --version ] && { printf \'ShellCheck\\nversion: stub\\n\'; exit 0; }\n'
                        'printf \'%s\\n\' "$*" >> "$ARGV_LOG"\nexit "$STUB_RC"\n')
            os.chmod(stub, 0o755)
            env = dict(os.environ, PATH=os.path.dirname(stub) + os.pathsep + os.environ["PATH"], ARGV_LOG=log,
                       STUB_RC=str(stub_rc))
            r = subprocess.run([sys.executable, SCRIPT] + list(args), cwd=d, env=env, capture_output=True, text=True)
            with open(log) as f:
                return r, f.read().split()

    def test_default_fails_on_warnings(self):
        # SC3xxx (bashisms in a #!/bin/sh script) are warnings: the default, which CI uses, must catch them.
        r, argv = self.run_check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--severity=warning", argv)
        # A finding (shellcheck exit 1) must fail the gate, not only reach shellcheck as an argument.
        r, argv = self.run_check(stub_rc=1)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("check-shell: FAILED", r.stdout)

    def test_severity_option_is_passed_on(self):
        r, argv = self.run_check("--severity", "error")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--severity=error", argv)


if __name__ == "__main__":
    unittest.main()
