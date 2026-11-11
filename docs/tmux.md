# tmux for watching parallel work

One terminal session that shows every running job at a glance: builds, perf
runs, sanitizer runs and agents, on the laptop and on the boxes behind it.
`screen` does the same job and is still fine; this page is for contributors who
want tmux, or who land on a host where only one of the two is installed.

## Why tmux

| Need | tmux |
|---|---|
| build the layout from a script | `new-session -d` and `new-window -t` take a name and a command, so a layout is a shell script, not a sequence of keystrokes |
| one window per remote job | each window runs its own `ssh ... tail -F` or `watch`, so a dead host blanks one window, not the session |
| see what is where | the status line lists window names; `Ctrl-b w` shows a tree with a preview |
| mouse | `set -g mouse on`: click a window name to switch, wheel to scroll back |
| availability | packaged everywhere `screen` is; `tmux -V` to check |

Keep `screen` if you already use it. Nothing in the harness depends on either,
except that `tools/unit-build` runs remote builds in a tmux session on the
build host (see [../agents/remote-builder.md](../agents/remote-builder.md)).

## Install

```bash
sudo apt install tmux      # Debian, Ubuntu
tmux -V
```

Without sudo, unpack the package into your home directory and put its
`usr/bin` on `PATH`:

```bash
apt download tmux && dpkg -x tmux_*.deb ~/.local/tmux
export PATH="$HOME/.local/tmux/usr/bin:$PATH"
```

## A watch session

Hosts are named by role (build box, perf lab box) and come from the
environment, so the script carries no host names. The example watches the
jobs of one change, one window per task. Paths under `~/work/` and `/tmp/`
are placeholders for your own layout.

```bash
#!/bin/sh
# ops-session.sh: read-only watchers, one window per task.
S=ops
PR=${PR:?set PR, the PR or issue number being watched}
BUILD=${BUILD_HOST:?set BUILD_HOST}   # e.g. build-box
LAB=${PERF_HOST:?set PERF_HOST}       # e.g. perf-box
SSH='ssh -o BatchMode=yes -o ConnectTimeout=5'

tmux has-session -t "$S" 2>/dev/null && exec tmux attach -t "$S"

tmux new-session  -d -s "$S" -n "$PR" \
    "watch -n 15 'git -C ~/work/tree-$PR log --oneline -5; tail -5 /tmp/run-$PR/run.log'"
tmux new-window -t "$S" -n "$PR-ci" \
    "watch -n 15 'gh pr checks $PR | grep -v pass'"
tmux new-window -t "$S" -n leak-build \
    "$SSH -t $BUILD 'tail -F ~/work/leak/massif.log'"
tmux new-window -t "$S" -n usdt-build \
    "watch -n 15 \"$SSH $BUILD 'ls -lt ~/work/usdt | head; docker ps --filter name=usdt'\""
tmux new-window -t "$S" -n ab-lab \
    "watch -n 15 \"$SSH $LAB 'uptime; ls -t ~/work/ab | head -3'\""
tmux new-window -t "$S" -n tsan-build \
    "$SSH -t $BUILD 'tail -F ~/work/tsan/run.log'"
tmux set -t "$S" -g mouse on
tmux select-window -t "$S:0"
tmux attach -t "$S"
```

Rules for the windows:

- **Read only.** A window tails, lists or `watch`es. It never starts, kills or
  restarts a job; start jobs where their owner expects them (`tools/unit-build`,
  the perf runner) and watch the log.
- **Don't touch other people's jobs.** A shared box runs other contributors'
  benchmarks and agents. Look with `ps` and `uptime`; do not kill.
- **Pin away from busy CPUs.** If you do start load on a shared box, check
  `top` first and use `taskset -c` to avoid CPUs someone else is using.
- `BatchMode=yes` makes a missing key fail at once instead of hanging the
  window on a password prompt; `ConnectTimeout` does the same for a down host.
- `tail -F`, not `-f`, so a rotated or not-yet-created log is picked up.
- Keep `watch` at 15 seconds or more; a dozen windows polling over ssh every
  second is load on the box you are measuring.

### Window names

`<task>-<host role>`, short enough for the status line: `<pr>`, `<pr>-ci`,
`leak-build`, `ab-lab`, `tsan-build`. The task is the PR or issue number or a
one-word topic; drop the host when the window watches the laptop.

## Moving around

| Do | Keys or command |
|---|---|
| list sessions | `tmux ls` |
| list windows | `tmux list-windows -t ops` |
| attach | `tmux attach -t ops` |
| detach, leaving everything running | `Ctrl-b d` |
| go to window N | `Ctrl-b N` (0–9) |
| pick from a tree with preview | `Ctrl-b w` |
| next, previous window | `Ctrl-b n`, `Ctrl-b p` |
| scroll back | mouse wheel, or `Ctrl-b [` then `q` to leave |
| close the whole session | `tmux kill-session -t ops` (yours only) |
