# tools/model/tui: a terminal UI for the model CLI

A small Textual app that runs the model CLI (`tools/model/cli.py`) and shows
its output. It is a front end only. The model stays standard library only;
Textual is needed for this app and nothing else.

## Start

From the harness root:

    uv run --no-project --with textual==8.2.8 python3 -m tools.model.tui

or the POSIX sh starter, which runs the same command:

    tools/model/tui/model-tui

## Screen

- Left, top: the CLI subcommands. Select one with Enter or a click.
- Left, bottom: the history of runs in this session, newest first. Each line
  shows the exit code (C for a cancelled run), the run time and the
  arguments. Select a line to show its saved output again.
- Right, top: a form with the options of the selected command. Integer
  options are integer fields, `store_true` options are check boxes, other
  options are text fields. An empty text field is left out, so the CLI
  applies its own default. Below the form is the command line that Run
  starts.
- Right, bottom: the output pane. Standard output and standard error are
  merged and shown line by line while the command runs. The last line shows
  the exit code and the run time: green for 0, red for any other code,
  gold for a cancelled run.

## Keys

| key | action |
|---|---|
| r, ctrl+r | run the selected command |
| c, escape | cancel the running command |
| l | clear the output pane |
| ? | help |
| q | quit; a running command is killed |

A focused text field takes the letter keys, so r and c type into it there.
ctrl+r and escape work everywhere, also inside a text field.

## How it works

- `core.py` holds the parts that need no Textual: the command parser, the
  argv builder and the process runner. `app.py` holds the UI only.
- The app does not import the model. It reads `cli.py` with the `ast`
  module and finds each `add_parser()` call and its `add_argument()` calls.
  The defaults in the form are the literal defaults in `cli.py`. A default
  that is a name, such as the `--src` default of `schema`, shows as an empty
  field and the CLI applies it.
- The usage lines in the `cli.py` docstring give a launcher for a command
  that needs more packages. `schema` runs under
  `uv run --with tree-sitter --with tree-sitter-c`.
- Run starts `python3 -m tools.model.cli <command> ...` from the harness
  root, as a new process group. Cancel sends SIGTERM to that group, then
  SIGKILL after 2 seconds. Quit kills a running child. The app signals no
  other process.
- The history holds the last 20 runs in memory. The app writes no files.
- `conformance --url` needs a live server. The app only passes the URL you
  type.

## Tests

`tools/model/tests/test_tui_core.py` tests `core.py` and needs no Textual. It
also checks that the commands and options parsed from `cli.py` match
`cli.py --help`, so a refactor of `cli.py` cannot drop an option silently.
`make tools-test` runs it.

`tools/model/tests/test_tui_app.py` drives the app with the Textual pilot. It
skips when Textual is not installed. Run it with:

    uv run --no-project --with textual==8.2.8 python3 -m unittest tools.model.tests.test_tui_app -v
