"""A small Textual front end for the model CLI (tools/model/cli.py).

This file holds the UI only. core.py finds the commands and runs the CLI as
a child process from the harness root. The app does not import the model, so
a change inside the model does not break it.
"""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from tools.model.tui.core import (
    CLI_PATH,
    HARNESS_ROOT,
    HISTORY_MAX,
    Command,
    Runner,
    RunRecord,
    build_argv,
    load_commands,
)

KEYS = [
    ("r, ctrl+r", "run the selected command (ctrl+r also works inside a text field)"),
    ("c, escape", "cancel the running command (escape also works inside a text field)"),
    ("l", "clear the output pane"),
    ("?", "this help"),
    ("q", "quit; a running command is killed"),
    ("tab", "move between the lists, the form and the buttons"),
]


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss", "Close")]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help-box { width: 90; height: auto; border: round $primary; padding: 1 2; background: $surface; }
    """

    def compose(self) -> ComposeResult:
        t = Table(show_header=True, box=None)
        t.add_column("key", style="bold")
        t.add_column("action")
        for k, v in KEYS:
            t.add_row(k, v)
        with VerticalScroll(id="help-box"):
            yield Static("Select a command on the left, set its options, then run it. "
                         "Select a history line to see its output again.\n")
            yield Static(t)

    def action_dismiss(self) -> None:
        self.dismiss()


class ModelTui(App):
    TITLE = "FreeUnit model"
    CSS = """
    #left { width: 34; }
    #commands { height: 1fr; border: round $primary; }
    #history { height: 1fr; border: round $secondary; }
    #form { height: auto; max-height: 14; border: round $primary; }
    #form Input { width: 40; }
    .opt-row { height: auto; }
    .opt-label { width: 16; padding: 1 1 0 0; }
    #buttons { height: auto; }
    #argv { height: auto; padding: 0 1; color: $text-muted; }
    #status { height: 1; padding: 0 1; }
    #log { height: 1fr; border: round $secondary; }
    """
    # r and c are taken by a focused text field. ctrl+r and escape are
    # priority bindings, so they work there too.
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "run", "Run"),
        Binding("c", "cancel", "Cancel"),
        Binding("l", "clear_log", "Clear log"),
        Binding("question_mark", "help", "Help"),
        Binding("ctrl+r", "run", "Run", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    def __init__(self, cli_path: Path = CLI_PATH, cwd: Path = HARNESS_ROOT) -> None:
        super().__init__()
        self.commands = load_commands(cli_path)
        self.runner = Runner(cwd)
        self.current: Command | None = None
        self.fields: dict[str, Input | Checkbox] = {}
        self.history: list[RunRecord] = []
        self.last: RunRecord | None = None
        self.run_done = asyncio.Event()
        self._form_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="left"):
                yield ListView(*[ListItem(Label(c.name), name=c.name) for c in self.commands],
                               id="commands")
                yield ListView(id="history")
            with Vertical():
                yield VerticalScroll(id="form")
                with Horizontal(id="buttons"):
                    yield Button("Run", id="run", variant="success")
                    yield Button("Cancel", id="cancel", variant="error", disabled=True)
                yield Static(id="argv")
                yield Static("idle  (press ? for help)", id="status")
                yield RichLog(id="log", wrap=True, markup=False, max_lines=20000)
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#commands").border_title = "Commands"
        self.query_one("#history").border_title = "History"
        self.query_one("#log").border_title = "Output"
        if self.commands:
            self.query_one("#commands", ListView).index = 0
            await self.select_command(self.commands[0].name)

    # ---- form -------------------------------------------------------------

    async def select_command(self, name: str) -> None:
        async with self._form_lock:
            await self._build_form(next(c for c in self.commands if c.name == name))

    async def _build_form(self, cmd: Command) -> None:
        form = self.query_one("#form", VerticalScroll)
        form.border_title = f"{cmd.name}: {cmd.help}"
        self.current, self.fields = None, {}
        await form.remove_children()
        rows = []
        fields: dict[str, Input | Checkbox] = {}
        for opt in cmd.options:
            if opt.kind == "flag":
                widget = Checkbox(opt.flag, value=bool(opt.default), id=f"opt-{opt.dest}")
            else:
                widget = Input(value="" if opt.default is None else str(opt.default),
                               placeholder="(CLI default)" if opt.default is None else "",
                               type="integer" if opt.kind == "int" else "text",
                               id=f"opt-{opt.dest}")
            widget.tooltip = opt.help
            fields[opt.dest] = widget
            rows.append(Horizontal(Label(opt.flag, classes="opt-label"), widget, classes="opt-row"))
        if not rows:
            rows.append(Label("This command has no options."))
        await form.mount_all(rows)
        self.current, self.fields = cmd, fields
        self.update_argv()

    def form_values(self) -> dict[str, object]:
        return {dest: (w.value.strip() if isinstance(w, Input) else w.value)
                for dest, w in self.fields.items()}

    def current_argv(self) -> list[str]:
        assert self.current is not None
        return build_argv(self.current, self.form_values())

    def update_argv(self) -> None:
        if self.current is not None:
            self.query_one("#argv", Static).update(Text("$ " + shlex.join(self.current_argv())))

    @on(Input.Changed)
    @on(Checkbox.Changed)
    def _form_changed(self) -> None:
        self.update_argv()

    @on(ListView.Selected, "#commands")
    async def _command_selected(self, event: ListView.Selected) -> None:
        if event.item.name:
            await self.select_command(event.item.name)

    @on(ListView.Selected, "#history")
    def _history_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or self.runner.running:
            return
        rec = self.history[idx]
        log = self.query_one("#log", RichLog)
        log.clear()
        log.write(Text("$ " + rec.cmdline, style="bold"))
        for line in rec.lines:
            log.write(line)
        self._show_summary(rec)

    # ---- run and cancel ---------------------------------------------------

    @on(Button.Pressed, "#run")
    def _run_pressed(self) -> None:
        self.action_run()

    @on(Button.Pressed, "#cancel")
    def _cancel_pressed(self) -> None:
        self.action_cancel()

    def action_run(self) -> None:
        if self.current is None or isinstance(self.screen, HelpScreen):
            return
        if self.runner.running or (self.last is not None and not self.run_done.is_set()):
            self.notify("A command is already running.", severity="warning")
            return
        self.run_done.clear()
        self.last = RunRecord(self.current_argv())
        self.run_command(self.last)

    @work(exclusive=True, group="run")
    async def run_command(self, rec: RunRecord) -> RunRecord:
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Static)
        log.write(Text("$ " + rec.cmdline, style="bold"))
        self._set_running(True)
        status.update(Text("running ...", style="bold"))
        t0 = time.monotonic()
        try:
            await self.runner.start(rec.argv)
        except OSError as exc:
            rec.code, rec.lines = 127, [f"cannot start: {exc}"]
            log.write(Text(rec.lines[0], style="red3"))
        else:
            try:
                async for line in self.runner.lines():
                    rec.lines.append(line)
                    log.write(line)
                rec.code = await self.runner.wait()
            except asyncio.CancelledError:
                # The app is closing and cancels this worker. Do not leave
                # the child running.
                self.runner.kill()
                raise
        rec.seconds = time.monotonic() - t0
        self._show_summary(rec, to_log=True)
        self._set_running(False)
        self._add_history(rec)
        self.run_done.set()
        return rec

    def _show_summary(self, rec: RunRecord, to_log: bool = True) -> None:
        msg, colour = rec.summary()
        text = Text(msg, style=f"bold {colour}")
        if to_log:
            self.query_one("#log", RichLog).write(text)
        self.query_one("#status", Static).update(text)

    def _set_running(self, running: bool) -> None:
        self.query_one("#run", Button).disabled = running
        self.query_one("#cancel", Button).disabled = not running

    def _add_history(self, rec: RunRecord) -> None:
        self.history.insert(0, rec)
        del self.history[HISTORY_MAX:]
        hv = self.query_one("#history", ListView)
        hv.clear()
        for r in self.history:
            mark = "C" if r.cancelled else str(r.code)
            hv.append(ListItem(Label(f"[{mark}] {r.seconds:.1f}s {r.args}", markup=False)))

    def action_cancel(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss()
            return
        if not self.runner.running or self.last is None:
            return
        self.last.cancelled = True
        self.runner.cancel()

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen())

    def on_unmount(self) -> None:
        self.runner.kill()
