"""Tests for tools/model/tui/core.py. Standard library only, no Textual."""

import asyncio
import re
import subprocess
import sys
import unittest

from tools.model.tui.core import CLI_PATH, HARNESS_ROOT, Runner, build_argv, load_commands


def cli_help(*args):
    out = subprocess.run([sys.executable, "-m", "tools.model.cli", *args, "--help"],
                         cwd=HARNESS_ROOT, capture_output=True, text=True, timeout=60, check=True)
    return out.stdout


class LoadCommandsTest(unittest.TestCase):
    def setUp(self):
        self.cmds = {c.name: c for c in load_commands(CLI_PATH)}

    def test_options_and_defaults_come_from_cli(self):
        verify = {o.flag: o for o in self.cmds["verify"].options}
        self.assertEqual(verify["--seed"].kind, "int")
        self.assertEqual(verify["--seed"].default, 42)
        self.assertEqual(verify["--chaos"].kind, "flag")
        self.assertIs(verify["--chaos"].default, False)
        # The --src default is a name in cli.py, so the form leaves it empty.
        src = self.cmds["schema"].options[0]
        self.assertEqual((src.flag, src.kind, src.default), ("--src", "str", None))

    def test_schema_prefix_comes_from_the_docstring(self):
        self.assertEqual(self.cmds["schema"].prefix,
                         ["uv", "run", "--with", "tree-sitter", "--with", "tree-sitter-c"])
        self.assertEqual(self.cmds["verify"].prefix, [])

    def test_build_argv(self):
        self.assertEqual(build_argv(self.cmds["verify"], {"chaos": True, "seed": "7"}),
                         ["python3", "-m", "tools.model.cli", "verify", "--chaos", "--seed", "7"])
        self.assertEqual(build_argv(self.cmds["verify"], {"chaos": False, "seed": ""}),
                         ["python3", "-m", "tools.model.cli", "verify"])
        self.assertEqual(build_argv(self.cmds["schema"], {"src": ""})[-1], "schema")

    def test_parsed_commands_match_cli_help(self):
        # The parser reads cli.py by shape. If a refactor of cli.py hides a
        # command or an option from it, this test fails and names the gap.
        top = cli_help()
        listed = set(re.search(r"\{([^}]*)\}", top).group(1).split(","))
        self.assertEqual(set(self.cmds), listed)
        for name, cmd in self.cmds.items():
            text = cli_help(name)
            options = text[text.index("options:"):]
            want = set(re.findall(r"(?m)^\s+(--[a-z][a-z0-9-]*)", options)) - {"--help"}
            got = {o.flag for o in cmd.options}
            self.assertEqual(got, want, f"{name}: parsed {sorted(got)}, --help lists {sorted(want)}")


class RunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_streams_lines_and_exit_code(self):
        r = Runner(HARNESS_ROOT)
        await r.start([sys.executable, "-c", "import sys; print('a'); print('b', file=sys.stderr); sys.exit(3)"])
        lines = [ln async for ln in r.lines()]
        self.assertEqual(await asyncio.wait_for(r.wait(), 10), 3)
        self.assertEqual(sorted(lines), ["a", "b"])
        self.assertFalse(r.running)

    async def _start_sleeper(self, r, ignore_term):
        code = ("import signal, time\n"
                + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
                + "print('ready', flush=True)\ntime.sleep(60)\n")
        await r.start([sys.executable, "-c", code])
        self.assertEqual(await anext(r.lines()), "ready")
        self.assertTrue(r.running)

    async def test_cancel_sends_sigterm(self):
        r = Runner(HARNESS_ROOT, grace=30)
        await self._start_sleeper(r, ignore_term=False)
        r.cancel()
        self.assertEqual(await asyncio.wait_for(r.wait(), 10), -15)

    async def test_cancel_kills_after_grace(self):
        r = Runner(HARNESS_ROOT, grace=0.3)
        await self._start_sleeper(r, ignore_term=True)
        r.cancel()
        self.assertEqual(await asyncio.wait_for(r.wait(), 10), -9)


if __name__ == "__main__":
    unittest.main()
