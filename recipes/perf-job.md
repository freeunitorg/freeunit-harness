---
name: perf-job
description: >-
  Run one FreeUnit perf lab job file and report its verdict (tools/perf-job.sh)
opencode:
  agent: perf-runner
  subtask: true
---
# Recipe: perf-job

Input: `$ARGUMENTS` is the path of one job file, for example
`tools/perf-lab/jobs/01-null-hello.job`. The perf-runner role in
`agents/perf-runner.md` sets the rules and the report format.

Run this FreeUnit perf lab job and report the result.

1. Run `tools/perf-job.sh $ARGUMENTS` with the longest timeout your bash tool allows.
   It prints progress on stderr and exactly one line on stdout that starts with `PERF `.
   Exit 0 means a verdict exists, 75 means the box was busy (nothing was measured),
   1 means an error, 2 means a bad job file or a missing run/lab.env.
2. If it exited 0, run `cat` on the summary.json path named in the PERF line.
3. Report in the format your agent instructions give. Do not compute any number.

Docs: docs/perf/perf-lab.md
