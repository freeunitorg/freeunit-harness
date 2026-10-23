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


if __name__ == "__main__":
    unittest.main()
