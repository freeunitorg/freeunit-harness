# Relay: one GitHub issue between machines

Copyright (C) FreeUnit contributors.

`tools/inbox` and `tools/task` are per-machine files. A cloud session, the
Hermes bot and the laptop agent do not share a disk, so they cannot read the
same bus. `tools/relay` uses one GitHub issue as the shared channel. It reads
and writes plain comment lines. It adds no server and no database.

The first channel is https://github.com/freeunitorg/freeunit-exp/issues/11
(the 1.37.1 list). Use one issue for each release or campaign.

## Lines

Each protocol line starts a comment line. A comment can hold other text too.
The separator is ` — ` (em dash) or ` -- `.

| Line | Who writes it | Meaning |
|---|---|---|
| `claim: <item> — <agent> — <branch>` | any agent | I work on `<item>` now, on `<branch>` |
| `done: <item> — <url> — <evidence>` | the holder | the work is in `<url>`; `<evidence>` is the test that proves it |
| `takeover: <item> — <agent> — <reason>` | any agent | the holder did not push for 24 h; I take the item |
| `perf-request: <job> — <agent>` and a ` ```job ` block | a trusted login | please run this lab job |
| `perf-claim: <job> — <agent> — <lab>` | the lab agent | the job runs now |
| `perf-result: <job> — <agent> — <PERF line>` and compare.txt | the lab agent | the verdict, copied from the tools |

`<item>` is a GitHub reference such as `#647`, or a job name. The last
`claim`, `takeover` or `done` line for an item wins. The GitHub login of the
comment is the sender that relay trusts. `<agent>` is a label only.

## Commands

```
tools/relay state freeunitorg/freeunit-exp#11      # who holds each item
tools/relay pull  freeunitorg/freeunit-exp#11      # copy new lines to run/inbox/bus.log as <login>@github
tools/relay post  freeunitorg/freeunit-exp#11 claim: #603 — Claude — fix/603
tools/relay perf  freeunitorg/freeunit-exp#11      # list open perf requests
tools/relay perf  freeunitorg/freeunit-exp#11 --run
```

`pull` keeps a cursor in `run/relay/`. Each line goes to the bus once, so
`tools/inbox since` and a Monitor on the bus see remote lines like local ones.

## Perf requests from a machine with no lab

A cloud session cannot reach the lab box. It posts a request:

````
perf-request: 24-638-drain — cloud
```job
JOB=24-638-drain
ISSUE=https://github.com/freeunitorg/freeunit/pull/638
REV_A=<40-character SHA of master>
REV_B=<40-character SHA of the PR head>
WORKLOAD=hello
CONNS=6 32
PERF=1
PRIMARY=s_instructions
EXPECT=...
FALSIFY=...
```
````

The laptop agent runs `tools/relay perf <issue> --run`. Relay:

1. Takes the oldest request from a `RELAY_TRUSTED` login that has no
   `perf-claim` or `perf-result` yet.
2. Refuses the job when it names `PATCH_*`, `CONFIGURE_ARGS*` or `AE`, when
   `REV_A` or `REV_B` is not a full SHA, or when a value has a quote, `$`,
   a backtick, a backslash or `;`. A remote line must not name a local file or
   a build flag. Exit 1, and nothing is posted.
3. Writes `run/relay/jobs/<job>.job` and runs `PERF_JOB_CHECK=1 tools/perf-job.sh` on it.
4. Posts `perf-claim`, runs `tools/perf-job.sh`, and posts `perf-result` with
   the PERF line and compare.txt. Relay computes no number.
5. Exit 75 when the box is busy. The result line then says `BUSY, nothing
   measured`. The requester posts the request again later with a new job name.

The revisions must be reachable from `REPO_URL` in `run/lab.env`. A PR head of
freeunitorg/freeunit is reachable as `refs/pull/<n>/head` when the lab fetches
those refs; otherwise push the branch first.

## Authority

`relay post` and `relay perf --run` post on GitHub. Per
[agent-coordination.md](agent-coordination.md) section 2, an agent runs them
only on a named job from the maintainer, for example "watch #11 and run perf
requests until 18:00". `relay state`, `relay pull` and `relay perf` without
`--run` only read; any agent can run them.

## Limits

- Polling only. A laptop loop runs `relay pull` and `relay perf --run` every
  few minutes; there is no webhook on the laptop.
- One request per run. The lab runs one job at a time.
- The issue is public. Never post embargoed work, a host name or a lab path.
  compare.txt has none today; check it when the report format changes.
