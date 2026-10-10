# Context priming — when to load FreeUnit source whole, and which pack

Measured 2026-10-06 at `origin/master` 47d2f933. Method and full numbers:
[perf/csrc-map.md](perf/csrc-map.md). Generator: `tools/build-pack.py`.

The token sizes below are the counts the Read tool reported to a Sonnet agent for each
pack file built at 47d2f933 (csrc-map.md, "Measurements"). They are not tokenizer counts
of another model. Re-measured with `build-pack.py --stats` on 2026-10-09 at
`origin/master` 0f1ec0b1: the set is 27,771 lines (16,522 non-comment), and the L0, L1
and L2 packs are 203,028, 577,413 and 843,217 bytes. At the ratios measured at 47d2f933
that is about 102k, 258k and 375k tokens (estimate, not measured).

## The rule

Grep-and-read is right for one function, one file, one bug. It loses the thread
when a question crosses the router, libunit and the port IPC at once: message
order, who owns a port, what a timeout tears down, how a request's buffers move
through three processes. For those, build one pack and give it to one subagent
whose window holds it. The main thread never loads a pack; it reads the
subagent's conclusion.

## The packs

| level | content | tokens (Read tool) | fits |
|---|---|---|---|
| L0 skeleton | every type, macro, global and table; every function signature with a stub `{ ... } //@A-B calls: f, g` | 101k | any 200k model, as a cached prefix |
| L1 hot path | L0 plus full bodies of the request-path functions and their short callees | 256k | 1M models only |
| L2 full | every file whole, minus license headers, debug log lines and blank runs | 371k | 1M models only |

The set is `src/nxt_router.c`, `src/nxt_unit.c`, the five `src/nxt_port*.c`
files and their headers: 27,492 lines, 16,390 non-comment lines at 47d2f933 (27,771 at
0f1ec0b1). It is not 10k.
Stripping buys little: debug, asserts, licenses and `NXT_TESTS` blocks are 4.7%
of the lines. The tokens are in the code. For this C the ratio is about 2.2
characters per token; the usual 3.3 under-counts by a third.

Always add `--extra src/nxt_application.c` for anything that touches the
prototype or main-process side of a port. The first validation needed it.

## Which level for which task

| task | level | model |
|---|---|---|
| struct layout, field meaning, message type table, who calls what | L0 | any; cheap, cacheable |
| request path, ordering, ownership, port IPC invariants, "can X ever happen" | L1 (L2 if the window is free) | Sonnet 1M, or Opus/Fable 1M for a verdict that will be acted on |
| config apply, start and stop, error and teardown paths, anything one helper deep | L2 | 1M model |
| cross-subsystem review of a PR that touches two of the three (K1, K2, the message-order class) | L2 with the PR diff appended | Fable or Opus 1M |
| data-structure or performance proposals | L2 plus a measured profile (perf, strace -c) | 1M model; without the profile the proposals land below the noise floor |
| why the code is this way | no pack | `git log`, `git show <tag>:<file>`, the archive skill |

Validated: a Sonnet subagent with only the L1 pack answered three known-answer
invariant questions correctly with file:line citations (NXT_AGAIN needs
`port->queue != NULL`; the port error handler drains `port->messages` and tears
nothing down; a message enters the queue only when `stream == 0 || last`). Its
two optimisation proposals graded B- and C+: one was already a free-list
object, the other moved a hash lookup out from under the mutex that guards it.
Flow and invariants: yes. Optimisation without a profile: no.

## How to brief the subagent

- Build the pack to the session scratchpad, never into the repo:
  `uv run --with tree-sitter --with tree-sitter-c tools/build-pack.py --repo "$UNIT_SRC" --rev <sha> --level 2 --extra src/nxt_application.c > <scratch>/pack-L2-<sha>.txt`.
  Same input gives byte-identical output, so a pack is reproducible from the SHA.
- One pack per subagent. Tell it: read the whole file in chunks of at most
  1,300 lines (a 1,500-line chunk hits the Read tool's 25k-token cap), then
  answer. No other tools until it has read it all.
- Ask for citations from the `//@N` markers. Lines with a marker cite exactly;
  lines counted between markers were off by up to 70 in the validation. If a
  cited line matters, re-check it against `git show <sha>:src/<file>`.
- The pack has no history. Give the relevant commit subjects in the brief when
  the question is "what changed", or ask the subagent to say it cannot know.
- Grade the answer against the code yourself before acting on it. Known-answer
  questions in `docs/perf/csrc-map.md` section "Validation with Sonnet" are a
  ready calibration set.

## Second trial, 2026-10-06: two Phase 0 perf items

| task | method | cost | result |
|---|---|---|---|
| epoll_ctl MOD on every port drain (src/nxt_port_socket.c) | Opus, L2 pack read whole first | 561k tokens, 43 tool calls, 7 min | Correct, and the plan's estimate was low: 4 MODs per request, not 1. The pack answered the safety questions well, and its //@N citations were exact. But the deciding fact was in src/nxt_epoll_engine.c, which is not in the pack; two greps found it |
| chdir per request for a root-only PHP target (src/nxt_php_sapi.c) | Sonnet, grep-and-read | 83k tokens, 29 tool calls, 11 min | Correct. It found that the plan's "skip chdir" was unsafe (two requests can need different directories) and wrote the safe rule. Its time went on build setup, not on understanding the code |

Lessons:

- A perf question about one function and its backend does not need a pack. Grep first, and use L0 only for "who calls this".
- For event-loop syscall questions, add `--extra src/nxt_epoll_engine.c --extra src/nxt_event_engine.h --extra src/nxt_fd_event.h`.
- Do not make "read the whole pack before any other tool" a fixed rule. Use it only for questions that cross router, libunit and port IPC.
- A perf brief must ask for a syscall count before and after. Both items were settled by strace counts, not by reading.
- PHP builds: unit-build has no php verb. Use the freeunit-php83-pytest image on the build host. The build host has no host strace, so install it in the container.

## Costs seen

L1 to a Sonnet subagent: 386k tokens over 16 tool calls in 252 seconds,
including the 14 Read chunks. Budget about 1.5x the pack size per question
round.
