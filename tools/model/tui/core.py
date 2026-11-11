"""The parts of the model TUI that need no Textual. Standard library only.

- load_commands() reads tools/model/cli.py with the ast module and finds the
  subcommands and their options. It does not import the model.
- build_argv() turns form values into an argv for the CLI.
- Runner starts the CLI as a child process, streams its output and cancels it.
"""

from __future__ import annotations

import ast
import asyncio
import os
import shlex
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[3]
CLI_PATH = HARNESS_ROOT / "tools" / "model" / "cli.py"
CLI_MODULE = "tools.model.cli"
HISTORY_MAX = 20


@dataclass
class Option:
    flag: str            # "--seed"
    kind: str            # "int", "str" or "flag"
    default: object      # literal default from cli.py, or None
    help: str = ""

    @property
    def dest(self) -> str:
        return self.flag.lstrip("-").replace("-", "_")


@dataclass
class Command:
    name: str
    help: str = ""
    options: list[Option] = field(default_factory=list)
    prefix: list[str] = field(default_factory=list)   # launcher before "python3 -m"


def _literal(node: ast.AST | None) -> object:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None   # a name such as DEFAULT_SRC: the CLI applies it


def _prefixes(doc: str) -> dict[str, list[str]]:
    """Read launcher prefixes from the usage lines of the cli.py docstring.

    The line "uv run --with tree-sitter ... python3 -m tools.model.cli schema"
    says that schema needs tree-sitter. The words before "python3" become the
    prefix for that command.
    """
    out: dict[str, list[str]] = {}
    for line in doc.splitlines():
        words = line.split()
        if CLI_MODULE not in words:
            continue
        i = words.index(CLI_MODULE)
        if i >= 3 and i + 1 < len(words) and words[i - 1] == "-m":
            out[words[i + 1]] = words[: i - 2]
    return out


def _call(node: ast.AST, method: str) -> ast.Call | None:
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == method and node.args):
        return node
    return None


def load_commands(cli_path: Path = CLI_PATH) -> list[Command]:
    """Find every subparsers.add_parser() and the add_argument() calls on it."""
    tree = ast.parse(cli_path.read_text(encoding="utf-8"))
    prefixes = _prefixes(ast.get_docstring(tree) or "")
    commands: dict[str, Command] = {}
    by_var: dict[str, Command] = {}

    for node in ast.walk(tree):
        call = _call(node, "add_parser")
        if call is None:
            continue
        name = _literal(call.args[0])
        if isinstance(name, str):
            kw = {k.arg: k.value for k in call.keywords if k.arg}
            commands[name] = Command(name, str(_literal(kw.get("help")) or ""),
                                     prefix=prefixes.get(name, []))
    # "p_x = subparsers.add_parser(...)" binds a variable to a command.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            call = _call(node.value, "add_parser")
            if call is not None and _literal(call.args[0]) in commands:
                by_var[node.targets[0].id] = commands[_literal(call.args[0])]
    for node in ast.walk(tree):
        call = _call(node, "add_argument")
        if call is None or not isinstance(call.func.value, ast.Name) or call.func.value.id not in by_var:
            continue
        flag = _literal(call.args[0])
        if not isinstance(flag, str) or not flag.startswith("--"):
            continue
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        if _literal(kw.get("action")) == "store_true":
            kind = "flag"
        elif isinstance(kw.get("type"), ast.Name) and kw["type"].id == "int":
            kind = "int"
        else:
            kind = "str"
        default = False if kind == "flag" else _literal(kw.get("default"))
        by_var[call.func.value.id].options.append(
            Option(flag, kind, default, str(_literal(kw.get("help")) or "")))
    return list(commands.values())


def build_argv(cmd: Command, values: dict[str, object]) -> list[str]:
    """Turn form values into an argv. An empty text field is left out."""
    argv = [*cmd.prefix, "python3", "-m", CLI_MODULE, cmd.name]
    for opt in cmd.options:
        val = values.get(opt.dest, opt.default)
        if opt.kind == "flag":
            if val:
                argv.append(opt.flag)
        elif val not in (None, ""):
            argv += [opt.flag, str(val)]
    return argv


@dataclass
class RunRecord:
    argv: list[str]
    code: int | None = None
    seconds: float = 0.0
    cancelled: bool = False
    lines: list[str] = field(default_factory=list)

    @property
    def cmdline(self) -> str:
        return shlex.join(self.argv)

    @property
    def args(self) -> str:
        """The words after the module name, for the history list."""
        if CLI_MODULE in self.argv:
            return " ".join(self.argv[self.argv.index(CLI_MODULE) + 1:])
        return self.cmdline

    def summary(self) -> tuple[str, str]:
        """The end-of-run line and its colour name."""
        if self.cancelled:
            return f"cancelled after {self.seconds:.2f} s (exit {self.code})", "gold3"
        if self.code == 0:
            return f"exit 0 in {self.seconds:.2f} s", "green3"
        return f"exit {self.code} in {self.seconds:.2f} s", "red3"


class Runner:
    """Run one child process and stream its merged stdout and stderr.

    The child starts in a new session, so it leads its own process group.
    cancel() and kill() signal that group only: the child and the processes
    it started (for example the python3 that "uv run" starts). They never
    signal this process or its parents.
    """

    def __init__(self, cwd: Path, env: dict[str, str] | None = None, grace: float = 2.0) -> None:
        self.cwd = cwd
        self.env = dict(os.environ if env is None else env, PYTHONUNBUFFERED="1")
        self.grace = grace
        self.proc: asyncio.subprocess.Process | None = None
        self._timer: asyncio.TimerHandle | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, argv: list[str]) -> None:
        if self.running:
            raise RuntimeError("a command is already running")
        self.proc = await asyncio.create_subprocess_exec(
            *argv, cwd=self.cwd, env=self.env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)

    async def lines(self) -> AsyncIterator[str]:
        assert self.proc is not None and self.proc.stdout is not None
        async for raw in self.proc.stdout:
            yield raw.decode("utf-8", "replace").rstrip("\n")

    async def wait(self) -> int:
        assert self.proc is not None
        code = await self.proc.wait()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        return code

    def _signal(self, sig: int) -> None:
        if not self.running:
            return
        assert self.proc is not None
        try:
            # proc.pid is also the process group id: see start_new_session.
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError:
            pass

    def cancel(self) -> None:
        """SIGTERM to the child group, then SIGKILL after the grace time."""
        if not self.running:
            return
        self._signal(signal.SIGTERM)
        if self._timer is None:
            self._timer = asyncio.get_running_loop().call_later(
                self.grace, self._signal, signal.SIGKILL)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)
