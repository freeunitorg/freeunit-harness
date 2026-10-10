# libunit message cost: recovering the CPU of the 1.37.0 order fix (2026-10-08)

Branch `perf/libunit-message-cost` (local worktree, not pushed), one commit 5ed400102 on origin/master 24ea96ed2
(worked on 856d23b9; the C sources it touches are the same). Result in one line: hello.php c=32 server instructions
per request back to 1.36.1 (NO CLAIM, -1.9% inside the null band), 14.4% below master; the order protection of
39700f29 stays, with a proof and stress at 0 failures.
Tracker: https://github.com/freeunitorg/freeunit/issues/597. The fix it builds on: 39700f29 "libunit: keep the
messages of a request in order on a queue port" (1.37.0).

## 1. Where the cost goes

The lab bisect (run/perf-lab/bisect-1.36.1-1.37.0-20261008.log) found 39700f29 as the first bad commit: hello.php,
c=32, `s_instructions` +13.7%, `r_instructions` +15.7%, 8/8 cells. This page explains it at function level.

### Messages per request (hello.php, 2000 requests at 1000 rps, strace in the lab container)

hello.php sends: an acknowledgement (ACK, 16 bytes), the response header (plain, about 150 bytes), the body
`hello\n` (plain, 16 + 6 = 22 bytes) and the last message (16 bytes). A queue item holds 31 bytes.

| per request | 25ec9f6e (before 39700f29) | 39700f29 |
|---|---|---|
| ACK | queue | socket: marker + datagram |
| header (plain, > 31 bytes) | socket | socket |
| body (22 bytes) | queue | socket |
| last | queue | queue |
| app `sendmsg`, data datagrams | 1.000 (header) | 3.000 (ACK, header, body) |
| app `sendmsg`, READ_QUEUE wake-ups | 0.946 | 0.009 |
| router `recvmsg` returning data | 1.946 | 3.009 |
| router `recvmsg` returning EAGAIN | 1.804 | 1.977 |
| router `epoll_wait` / `epoll_ctl` | 3.267 / 1.837 | 3.720 / 2.009 |

So 39700f29 moved two messages per request from the queue to the socket, not one: the ACK and the small body. At
low load the queue path also paid a READ_QUEUE wake-up datagram (0.946 per request at 1000 rps), which hid part of the
cost there. At c=32 the router is busy and the queue rarely needs a wake-up, so the delta is larger.

### Instructions per request, user and kernel (perf stat, 200,000 requests at c=32, A1 B1 B2 A2)

Profile run: prof/p1 (`msgcost-out/p1` on the perf box), trees 25ec9f6e4fe1 (A) and 39700f29466f (B) from the
bisect, perf record at a period of 20011 instructions running at the same time (so totals are a few percent above
the lab's).

| process | metric | A | B | delta |
|---|---|---|---|---|
| router | instructions:u | 20,275 | 20,616 | +341 (+1.7%) |
| router | instructions:k | 37,244 | 46,466 | +9,222 (+24.8%) |
| PHP processes | instructions:u | 29,498 | 29,619 | +121 (+0.4%) |
| PHP processes | instructions:k | 41,054 | 49,274 | +8,220 (+20.0%) |

97% of the added instructions (17,442 of 17,904) are in the kernel. FreeUnit's own code changed by 462
instructions per request.

### Kernel functions (perf record, sampled, delta per request, grouped)

| group | router | PHP processes | top symbols |
|---|---|---|---|
| receive path | +4,047 | +637 | `__unix_dgram_recvmsg` +378, `put_cmsg` +288 (SCM_CREDENTIALS), `_copy_to_iter` +285, `put_pid` +242, `___sys_recvmsg` +216, `from_kuid_munged` +131 |
| skb free, slab and memcg | +1,887 | +905 | `__memcg_slab_free_hook` +545, `mod_objcg_state` +370, `kmem_cache_free` +258, `__slab_free` +245 |
| syscall entry and exit, seccomp | +1,013 | +605 | `__seccomp_filter` +258 (router) +198 (app), `entry_SYSCALL_64` +152 |
| send path and skb alloc | -23 | +2,688 | `__memcg_slab_post_alloc_hook` +425, `kmem_cache_alloc_node` +297, `__alloc_skb` +232, `unix_dgram_sendmsg` +200, `maybe_add_creds` +96 |
| epoll and wake-up | -4 | +232 | `sock_def_readable` +128, `ep_poll_callback` +86 |

The router's user-space socket path (`nxt_port_queue_read_handler`, `nxt_port_buf_alloc`,
`nxt_port_read_msg_process`) moved by less than 1% each: a datagram costs the router about the same user-space
work as a queue item. The cost of a datagram is the kernel: per datagram, the sender allocates and charges an skb
and attaches credentials; the receiver copies it out, converts the credentials (`SO_PASSCRED` on router ports),
frees and uncharges the skb; each side passes seccomp once per syscall. Per small datagram this is about 8,000
instructions over both processes.

Raw: run/perf-lab/msgcost-prof-20261008/ (p1/*.stat, p1/*.full per-symbol reports, p1-router-delta.txt,
p1-app-delta.txt, census.py, cmp.py, prof-in.sh and prof-remote.sh, which take the lab claim and idle gate).

## 2. Design

### Chosen: the response of a request moves to the queue

The ordering rule behind 39700f29: a datagram can be read **before** a queue message that its sender sent earlier
(another process's READ_SOCKET marker takes it). It is never read **after** a queue message that its sender sent
later (its own marker is in the queue before that message). 39700f29 drew the safe conclusion "only the last message
of a request may use the queue". The general one is: **once a message of a request is in the queue, every later
message of the request must be in the queue too**, or wait until the router has taken the queued ones (section 3).
A request starts on the socket (the ACK) and switches to the queue once. Then its queue messages are read in queue
order (FIFO per sender), and any earlier datagram of the same request is read no later than its own marker, which
precedes them.

For that, every message after the switch must fit a queue item (31 bytes). If every message of a request is in the
queue, the order needs no argument about markers at all: one FIFO. So on a port with a queue, libunit now
puts data over 15 bytes into shared memory (a 28-byte message) even below 1024 bytes, where it used plain memory
before. Data of up to 15 bytes stays plain and fits the queue (16 + 15 = 31).

Rules in the code (src/nxt_unit.c, final version v4, after the three reviews in section 7):

- Each request has a `queue_state`: UNQUEUED (no message in the queue yet), QUEUED (messages in the queue), SOCKET
  (on the socket until its last message). A websocket handshake request starts as SOCKET: its frames can wait in
  the router for a slow client, and in shared memory each would hold a chunk of the application's "shm" limit, so
  a websocket keeps the 1.37.0 path.
- `nxt_unit_req_send()` sends every message of a request (ACK, data, last). A message other than the ACK goes into
  the queue when it fits and (it is the last message, or the request is QUEUED, or the request is UNQUEUED and
  `lib->shm` is OK). The
  first such message makes the request QUEUED; after each queue message the request records the queue tail.
- A message that does not fit for a QUEUED request: `nxt_unit_req_queue_drain()` first waits until the router's
  queue head has passed that tail, then the request becomes SOCKET and the message goes to the socket. The router
  takes no queue item while it waits for the datagram of a READ_SOCKET marker, so once the head has passed the
  request's items, every marker before them has its datagram and a datagram sent now is read after them. The wait
  depends on how fast the router runs, not on clients. It gives up after 1 s (100 `sched_yield()`, then sleeps of
  100 us until a deadline from the monotonic clock) with the alert "the router has not taken the queue messages of
  the request" and fails the send:
  only a marker whose datagram never came (the "limit stays" case of 39700f29) can stop the router that long, and a
  datagram sent then could be read for that marker.
- The ACK always goes to the socket (final version v4, after review round 3). Every request then sends one
  datagram, as in 1.36.1. A READ_SOCKET marker whose datagram never comes (a `sendmsg()` that failed after the
  marker, a process killed between the two) leaves the router one datagram behind for good; the datagrams of later
  requests keep it moving, as on master. With the ACK in the queue too (v3), a steady state sent no datagram at all,
  and one such marker would stop the engine port until some datagram came.
- `lib->shm` (UNKNOWN, OK, FAILED, FULL) records the last outgoing shared memory allocation: OK after any chunk was
  allocated, FAILED after a new segment failed (small buffers then skip shared memory for 60 s), FULL at the "shm"
  limit with no free chunk. A request moves to the queue only when OK.
- `nxt_unit_get_outgoing_buf()`: plain memory for data up to 15 bytes (fits a queue item), for ports without a queue,
  and for SOCKET requests. Other buffers up to 1024 bytes try shared memory with `probe` set: `nxt_unit_mmap_get()`
  then takes a whole run of chunks or nothing, and at the limit returns NULL at once, without OOSM and without
  setting the segment's `oosm` flag. If that gives nothing, the buffer is plain memory, as in 1.37.0, and a small
  write never waits for shared memory. An UNQUEUED request with such a plain buffer becomes SOCKET at once; a QUEUED
  one moves to the socket when the buffer is sent (drain above). Buffers over 1024 bytes are unchanged: they wait
  at the limit, as before.
- Messages of no request (stream 0) keep the old rule in `nxt_unit_port_send()`.
- Fixed on the way: `nxt_unit_mmap_get()` added `*n` to `allocated_chunks` also when `nxt_unit_new_mmap()` failed;
  the count only grew and fed the early NULL at the limit.

hello.php after the change: ACK (socket), header (shared memory, 28 bytes, queue), body (22 bytes, queue), last
(queue). One datagram per request, the ACK, as 1.36.1 sent one (the header).

### Rejected

**a. Fewer messages for small PHP responses (597 wave 4 K2).** One message for header + body + last saves
messages, but the plain message is over 31 bytes and still goes to the socket, and the ACK still goes to the socket.
Best case for hello.php: ACK and one combined message = 2 datagrams against 1.36.1's 1 (plus 0.95 wake-ups at low
load). It cannot reach 1.36.1 alone. Full K2 also changes when PHP output leaves (output before `flush()` waits until
the end), and only helps PHP; the other modules keep the cost. It stays a separate 1.38 item and combines with
this change (one shared-memory message instead of two queue items).

**b. A marker that says who sent it.** The router would have to match datagrams to markers: read ahead, keep a
per-sender stash of datagrams, and wait at a marker whose datagram has not come. A failed `sendmsg()` after the marker
(the "limit stays" case of 39700f29) would block the port for good unless a cancel path exists. New router buffering
and a port-protocol change in a patch release: rejected. Per-sender sockets are a larger version of the same.

**c1. Only the ACK back into the queue.** Undoes half the cost for every module, but the router then sees response
data before the ACK when a datagram is taken by another marker. That was possible from 1.19.0 to 1.36.1 and is not
safe: `nxt_router_response_ready_handler()` handles a 101 response with `req_rpc_data->app_port`, which is still the
application's shared port until the ACK (src/nxt_router.c:8041), and the shared port has no `main_app_port`
(zero-allocated in `nxt_port_new()`, never set), so `app_port->main_app_port->active_websockets++` would dereference
NULL. The request accounting (`active_requests`, `ack_waiting_req`) also assumes the ACK first. With this change the
ACK is still always read before any later message of its request.

**c4. The ACK in the queue too (v3).** Measured: hello.php c=32 8.4% fewer server instructions than 1.36.1 (v4
is near 1.36.1). Rejected for a patch release by review round 3: a steady state then sends no datagram at all, so
one marker without its datagram (a failed `sendmsg()`, or a process killed between marker and datagram) stops the
engine port until some datagram comes; master and 1.36.1 keep moving because every request sends datagrams. Worth
doing together with router-side recovery for such a marker (a marker that carries pid and stream, option b), in
1.38.

**c2. ACK in the first response message.** High risk (597 lists it last): the router needs the ACK before the
request body is sent and for its process accounting and timers. Not needed after the chosen change.

**c3. Cheaper datagrams.** The cost is kernel work per syscall; `SO_PASSCRED` and seccomp cannot be dropped. The
router's extra EAGAIN `recvmsg()` per drain (about 1.8 per request in both A and B) is a separate optimisation, not
part of the regression.

## 3. The invariant and its proof

Invariant: for every request, the router processes the messages that one sender sent for it in the order they were
sent.

1. The queue is FIFO per producer thread: `nxt_port_queue_send()` fills an item and publishes its index with one
   `nxt_nncq_enqueue()`; the router dequeues indexes in order (src/nxt_port_queue.h, src/nxt_nncq.h).
2. Each marker makes the router take exactly one datagram, the next one on the socket. Let D, sent by S, be the k-th
   datagram on the socket. Each of the first k datagrams was queued on the socket no later than D, and its marker was
   enqueued before its `sendmsg()`. So all k markers are in the queue before any message Q that S enqueues after
   sending D. When the router reaches Q it has passed at least k markers and so has read D. D is never read after a
   queue message that S sent later (39700f29's "the reverse cannot happen"). D can be read at another sender's
   earlier marker, so it can be read before queue messages that S sent before D: that is the bug.
3. With the change, a request's messages are: zero or more datagrams, then zero or more queue messages, then
   (only after `nxt_unit_req_queue_drain()` succeeded) zero or more datagrams, then possibly the last message in the
   queue. Before the drain, by 2, each datagram is read before every later queue message. Queue messages are read in
   order, by 1. A datagram sent after the drain is read after every queue message of the request, because the router
   had taken them all before it was sent and takes no queue item while a marker waits for its datagram
   (src/nxt_port_socket.c, `nxt_port_queue_read_handler()`: with `from_socket` set it reads only the socket). The last
   message, in the queue after datagrams, is read after them by 2. Datagrams of one sender are read in socket order.
   So all messages of the request are read in send order.
4. The reverse case of 39700f29 (a datagram read before an earlier queue message of its sender) needs a datagram
   after a queue message of the same request, which step 3 excludes.

Messages of no request (stream 0) are not ordered against requests by this rule, as before. A new segment's MMAP
message (stream 0, with a file descriptor, so a datagram) is read before the data that uses the segment, by step 2.

### Failure semantics

- `sendmsg()` failure after a marker ("one limit stays" in 39700f29), or a process killed between marker and
  datagram: as on master. The router stays one datagram behind; the ACK datagram of each later request moves it on
  (with the shift, an ACK can be read at an earlier marker, which is harmless: it is the first message of its
  request). A quiet port waits for the next datagram, as on master. Fewer datagrams per request than master also
  means the socket fills (278 datagrams) less often.
- A READ_QUEUE wake-up is sent only when `nitems` goes from 0 to 1, as before; a lost wake-up waits for the next
  datagram from any process, as for every queue message since 1.19.0.
- Queue overflow (16384 items): the message fails with "queue overflow", as before; a socket message also takes an
  item (its marker), so the number of items per request is unchanged or lower.
- A segment that cannot be made, or the "shm" limit: a small buffer falls back to plain memory without waiting, as
  in 1.37.0; a QUEUED request then waits for the router to take its queue messages (microseconds normally), and
  fails the send only if the router stays stuck at a marker for about 1 s. Buffers over 1024 bytes wait at the limit
  as before.
- Pre-existing, not changed: `nxt_unit_response_send()` leaves `req->response_buf` pointing at a freed buffer when
  the send fails; the router keeps response field pointers into the application's shared memory (now also for
  headers under 1 KiB, as for headers over 1 KiB before).

### Modules

All modules use libunit's send functions, so all get the change; none changes. Node and Java write into
`req->response_buf` directly: the buffer is now shared memory for small headers too (a 16 KiB chunk instead of an
exact-size malloc); the code only uses `start/free/end`. Python ASGI, Node and Go use non-blocking writes
(`min_size 0`); for a QUEUED request a small write can now return 0 bytes when the "shm" limit is reached, as a
large one always could; all three loop on it (Go: `wait_shm_ack()` in go/response.go). Websocket requests stay on the
socket with plain memory for small frames, as in 1.37.0. External apps get the change when rebuilt against the new libunit; older binaries keep their
own behaviour, and the router is unchanged.

## 4. Tests

C tests, src/test/nxt_unit_msg_test.c (v4 built on the build host with `unit-build -t remote --debug --hardening tests`:
0 warnings, `./build/tests` exit 0, hook gate exit 0, `./build/unit_msg_test`, `unit_close_test` and
`unit_port_recv_test` exit 0; `unit_msg_test` 20 runs in a row, 0 failures). The cases play process "Y" (a marker in
the queue before the request, its datagram after it) and the router (queue in order, next datagram per marker), and
classify the messages of the request as A (ack), H (header), B and C (body parts, by size), L (last). Two hooks under
`#if (NXT_TESTS)`: `nxt_unit_test_new_mmap_failures()` and `nxt_unit_test_shm_probe_failures()`.

| case | what it pins |
|---|---|
| response messages keep their order through the queue and the socket (39700f29's case: header over 1 KiB, JSON body) | "AHBL" |
| a small response sends one datagram (hello-world header + `hello\n`, after a first response made a segment) | 1 datagram (the ack), "AHBL" |
| without shared memory a response keeps its order on the socket (a failed segment) | 3 datagrams, "AHBL" |
| a plain buffer keeps its request on the socket (a 500-byte buffer allocated before the header is sent, sent after a 2000-byte write, as Java does) | "AHBCL" |
| a websocket stays on the socket (101 after shared memory works) | 2 datagrams, "AH" |
| a request leaves the queue after the router took it (a thread plays the router; the header is in the queue, the 100-byte body part finds no free chunk; the router waits at the ack's marker for 20 ms after the header was queued) | no data datagram before the router took the header, 2 datagrams, "AHBL" |

Mutation runs against the v4 tests (the Docker host, freeunit-php85-pytest:flush, gcc 14.2, `--debug`; values as printed):

| code | failing cases and values |
|---|---|
| 856d23b9 + the two hooks (pre-change) | "a small response sent 3 datagrams, expected 1"; plain buffer: response failed (master tries no segment for the header, so the hook fails the 2000-byte write); drain case: "the body part went out before the router took the header". The order, no-shm and websocket cases pass: master behaves that way; they guard the new mechanism. |
| M1: the 1.36.1 rule (plain up to 1024 bytes for every request, no SOCKET state, no drain) | "the router got \"ABHL\"" (order); "a small response sent 2 datagrams, expected 1"; plain buffer: response failed; drain case: "the body part went out before the router took the header" |
| M4: no SOCKET state for a plain buffer | plain buffer: "the router has not taken the queue messages of the request" (the single-threaded case has no router to drain), response failed |
| M5: a websocket not kept on the socket | "a websocket sent 1 datagrams, expected 2" |
| M6: switch to the socket without the drain | "the body part went out before the router took the header" |
| M7: the ACK into the queue (the v3 rule) | "a small response sent 0 datagrams, expected 1"; "1 datagrams after the drain, expected 2" |

Earlier versions: v1 failed the plain-buffer case with "#7: a 516-byte message after a queue message" and the
websocket case with 0 datagrams; v3 (ACK in the queue) passes the v3 tests, which expected 0 datagrams for the small
response.

Python and Java pytest (the build host, freeunit-test:latest, own netns): test_python_application, _basic, _shm_limit,
_targets, test_asgi_application, _lifespan, _targets, _websockets, _websockets_timeout, test_java_websockets,
test_java_application: branch v2, branch v3 and 856d23b9 all 207 passed, 22 skipped (the same set of 229 ids),
0 failed, 0 C warnings; shm_limit's "validation" and "segments" and 40 ASGI websocket tests ran.
PHP pytest (the Docker host, freeunit-php85-pytest:flush): all test_php_*.py and test_static.py, 173 tests, v1, v2, v3, v4
and 856d23b9 identical per file: 150 passed, 17 skipped, 6 failed and 5 errors from the environment (no zlib in the
image for one compression test; unshare()/mount() EPERM without CAP_SYS_ADMIN for 5 isolation tests; with
SYS_ADMIN only rootfs_credential_with_mount still fails, on both trees). Python/Java pytest ran on v2 and v3; v4
changes only the ACK path (back to master's) and was not rerun there.

Stress (the Docker host, freeunit-php85-pytest:flush image, release builds `-O -g`, 0 warnings; kit and results in
run/perf-lab/msgcost-stress-20261008/, runner `$BUILD_ROOT/msgcost-stress/run-variant.sh <variant> <load>` on
the Docker host): `listen_threads` 64, PHP
`processes` max 64 spare 5, `output_buffering` 4096, `fastcgi_finish_request` disabled, oha 32 keep-alive clients.

- Load A: `oha -n 300000 -c 32 --rand-regex-url 'http://<ip>:8533/index[.]php[?]n=[1-9][0-9]{1,2}&h=[0-9]{1,2}&s=[0-9]{1,4}&m=f'`
  (Drupal-like 404: body 10-999 bytes, 0-99 extra headers, 0-10 ms sleep, flush()). About 15,000 per run are oha
  client errors "message head is too large" (h >= 90 gives over 100 header lines, which oha refuses); not server faults.
- Load B: 32 threads x 3000 requests, exact body check. B1 2000 x A, ob_flush(), 100 x B. B2 20000 x A, ob_flush(),
  flush(), 100 x B. B3 (second form) 10 x A, 2000 x B, 300 x C, 20 x D, each followed by ob_flush() and flush(), so
  the router gets the header and four separate writes, tiny (plain, queue), shared memory, plain and tiny.

| build | A: 404 / 503 / alerts (per run) | B1 bad / 96,000 | B2 bad | B3 bad |
|---|---|---|---|---|
| ac2f2d29 (pre-fix) | 284,203 / 787 / 787; 284,087 / 745 / 745 | 10 | 17 | 32 |
| b12fb677 (pre-fix) | 284,278 / 720 / 720 | 11 | 17 | - |
| 856d23b9 (master) | 284,984 / 0 / 0 | 0 | 0 | 0 |
| branch v1 | 285,155 / 0 / 0; 284,993 / 0 / 0 | 0 | 0 | 0 (first B3 form) |
| branch v2 | 285,056 / 0 / 0; 284,892 / 0 / 0 | 0 | 0 | 0 |
| branch v3 | 284,864 / 0 / 0; 284,985 / 0 / 0 | 0 | 0 | 0 |
| branch v4 | 285,097 / 0 / 0; 284,935 / 0 / 0 | 0 | 0 | 0 |

Load B4 (application "shm" limit of one 10 MiB segment; 8 curl clients download an 8 MiB big.php written in 4 KiB
ob_flush()/flush() steps at 50 KB/s; after 5 s, load A for 100,000 requests against index.php): the limit binds
(big.php's first write over 100 ms comes at write 756-1957 of 2048). index.php stays fast: p99 10.0-10.5 ms, max
18-47 ms on 856d23b9, v3 and v4 alike; no 503, no alert. v4: two runs, 95,054 answered each, `requests.active=0`
afterwards.

Every pre-fix alert was "response buffer too small for fields count: 574235237"; every bad body was out of order
("B*100,A*2000"; B3 "A*10,C*300,B*2000,D*20" x 23 and "C*300,A*10,B*2000,D*20" x 9). On the branch, the strings
"after a queue message", "queue overflow", "failed to get out buf" and "has not taken the queue messages" appear 0
times. The first B3 form (flush() without ob_flush()) found only 1 and 9 on ac2f2d29: PHP's flush() does not empty
PHP's own output buffer.


## 5. Measurements

Lab jobs on the build host (`tools/perf-job.sh`), hello.php, 4 PHP processes, 8 s cells, 2 blocks, null phase in every
job. Job files tools/perf-lab/jobs/14-msgcost-master.job, 15-msgcost-vs-1.36.1.job, 16-msgcost-capp.job; the patch
is tools/perf-lab/jobs/patches/libunit-message-cost.diff (src/nxt_unit.c and src/nxt_unit.h of the branch; its
sha256 is in each run's meta). origin/master resolved to d50d12c26, which has the same C sources as 856d23b9.

Job 14, A origin/master, B v1 (before the reviews; same message path as v3 for these requests):

```
PERF 14-msgcost-master 14-msgcost-master-20261008T142625Z: CLAIM hello-c32: B is 18.9 percent less server instructions per request, outside the null band of 1.6, 8 of 8 cells agree ; CLAIM hello-c6: B is 10.3 percent less server instructions per request, outside the null band of 3.3, 8 of 8 cells agree
```

c=32: s_instructions 134,781 to 109,363; r_instructions 62,064 to 47,758 (-23.1%, CLAIM); asys_sendmsg 3.36 to 1.88
per request in the strace probe cells at 2000 req/s (the rest are READ_QUEUE wake-ups of an idle router).

Job 15, A 1.36.1, B v2 (after the first review):

```
PERF 15-msgcost-vs-1.36.1 15-msgcost-vs-1.36.1-20261008T150307Z: CLAIM hello-c32: B is 6.6 percent less server instructions per request, outside the null band of 1.6, 8 of 8 cells agree ; NOISY hello-c6: the null band 5.0 is over the limit 5.0, no claim; rerun on a quiet box (B is 1.5 percent less server instructions per request)
```

c=32: s_instructions 116,698 to 108,939; r_instructions 52,195 to 47,399 (-9.2%, CLAIM). c=6: s -1.5%, r +0.1%,
both inside the noise; my own build started on the box for about 30 s during this job's cells, which may have
widened the c=6 null band.

Job 15 again, A 1.36.1, B v3 (ACK in the queue; rejected in review round 3, section 2 c4):

```
PERF 15-msgcost-vs-1.36.1 15-msgcost-vs-1.36.1-20261008T153951Z: CLAIM hello-c32: B is 8.4 percent less server instructions per request, outside the null band of 1.9, 8 of 8 cells agree ; NOISY hello-c6: the null band 7.4 is over the limit 5.0, no claim; rerun on a quiet box (B is 2.8 percent less server instructions per request)
```

c=32: s_instructions 118,373 to 108,453; r_instructions 53,501 to 47,161 (-11.8%, CLAIM).

Job 15, A 1.36.1, B v4 (final):

```
PERF 15-msgcost-vs-1.36.1 15-msgcost-vs-1.36.1-20261008T161832Z: NO CLAIM hello-c32: inside the noise, no claim (B is 1.9 percent less server instructions per request, null band 2.3, 7 of 8 cells agree) ; NOISY hello-c6: the null band 5.5 is over the limit 5.0, no claim; rerun on a quiet box (B is 1.7 percent more server instructions per request)
```

c=32: s_instructions 117,709 to 115,476 (-1.9%, inside the null band of 2.3: back at 1.36.1); r_instructions 53,011
to 50,434 (-4.9%, CLAIM, outside the band of 4.0). c=6: s +1.7%, r +2.3%, both inside a null band of 5.5-5.8 that is
over the lab's limit; no claim either way. The c=6 null band was over 5% in all three runs of job 15 today (5.0, 7.4,
5.5), so c=6 cannot resolve a 2% difference on this box at 8 s cells.

Job 14 again, A origin/master, B v4 (final):

```
PERF 14-msgcost-master 14-msgcost-master-20261008T163310Z: CLAIM hello-c32: B is 14.4 percent less server instructions per request, outside the null band of 1.3, 8 of 8 cells agree ; CLAIM hello-c6: B is 5.4 percent less server instructions per request, outside the null band of 3.1, 8 of 8 cells agree
```

c=32: s_instructions 135,461 to 115,923; r_instructions 62,297 to 50,833 (-18.4%, CLAIM); asys_sendmsg 3.57 to 2.45
in the strace probe cells. c=6: r_instructions -6.3% (CLAIM). Function sizes: `nxt_unit_get_outgoing_buf` 1972 to
2268 bytes, `nxt_unit_req_send` new, 378 bytes; `nxt_port_queue_read_handler` unchanged (the router is not changed).

Job 16, A origin/master, B v4, workload capp (src/test/nxt_unit_app_test.c on the plain libunit.a: header with
content, a 16 KiB-chunk body, last):

```
PERF 16-msgcost-capp 16-msgcost-capp-20261008T165035Z: CLAIM capp-c256: B is 19.3 percent less server instructions per request, outside the null band of 2.7, 8 of 8 cells agree ; CLAIM capp-c32: B is 25.2 percent less server instructions per request, outside the null band of 2.6, 8 of 8 cells agree
```

c=32: s_instructions 86,303 to 64,532; r_instructions 59,861 to 48,005 (-19.8%). `asys_sendmsg` 3.060 to 1.000
per request (CLAIM): exactly the ACK, as designed; the same at c=256 (3.029 to 1.000).

## 6. Overlap

`git merge-tree --write-tree` against the branch (5701916de; rechecked for 573 against the final 5ed400102 with
the same result), textual only:

- https://github.com/freeunitorg/freeunit/pull/573 (websocket frame order across a wait for a segment, head
  48cb7090b): src/nxt_unit.c and src/test/nxt_unit_msg_test.c merge cleanly. It changes the receive and parking
  side (read_buf_release, incoming_mmap, unpark_rbufs, check_rbuf_mmap, process_pending_rbuf, wait_shm_ack,
  run_once_impl, process_port_msg_impl); this branch changes the send side. CHANGES and docs/changes.xml conflict,
  as they do against origin/master (573 is based on 47d2f933). Order: land 573 first (small, not draft, needs a
  rebase for its changelog anyway), rebase this branch on it, add the changelog stanzas after 573's, and build and
  run both C test programs on the combined tree before the second merge (both edit unit_msg_test).
- https://github.com/freeunitorg/freeunit/pull/452 clean. https://github.com/freeunitorg/freeunit/pull/583,
  https://github.com/freeunitorg/freeunit/pull/451, https://github.com/freeunitorg/freeunit/pull/453,
  https://github.com/freeunitorg/freeunit/pull/463, https://github.com/freeunitorg/freeunit/pull/467 and
  https://github.com/freeunitorg/freeunit/pull/470 conflict only in files this branch does not touch (the same
  conflicts as against origin/master). None of them changes nxt_unit.c, nxt_unit.h or unit_msg_test.c.

## 7. Review

Three fresh Opus reviews, each given only the diff and the tree, never my conclusions.

Round 1 (v1). No blocker on ordering. Findings and what I did:
1. Small writes in a queued request now wait at the "shm" limit (blocking) or return 0 (non-blocking); slow readers
   pin 16 KiB per small message; an ASGI websocket loop could stall. Done: websockets stay on the socket; the probe
   mode; later (round 2) the drain removes all waits for small buffers.
2. A plain buffer allocated before the request entered the queue and sent after it was refused (Java with a small
   `setBufferSize`, or the public API): done, such a request becomes SOCKET; test "a plain buffer keeps its request
   on the socket".
3. The non-blocking probe still sent OOSM and set the `oosm` flags: done, `probe` argument.
4. `allocated_chunks` grew on a failed new segment (pre-existing): fixed.
5. The refusal could be deleted without a failing test: done, tests for the plain buffer and later the drain.
6. FAILED was sticky: done, 60 s retry and OK after any allocation.
7. TINY_CHUNK builds could get a partial run in probe mode: done, a probe takes the whole run or nothing.
8. Comment fixes, `|| pm->last` in the stream-0 wrapper: done.
9. Mutex and segment creation cost in threaded apps: not measured (section 8).

Round 2 (v2). Findings:
1. BLOCKER: a request whose ACK went into the queue waits at the "shm" limit for small buffers, or fails on a failed
   segment, where master used plain memory; a few slow downloads could stall small API responses of the same
   process. Done: the reviewer's own proposal, `nxt_unit_req_queue_drain()`: a small buffer never waits for shared
   memory; a queued request that gets plain memory waits for the router to take its queue messages, then moves to
   the socket. Test "a request leaves the queue after the router took it", which fails without the drain ("HAB").
2. The `plain:` label could move a QUEUED request to SOCKET without a drain if the plain rule changed: done, it
   moves only UNQUEUED requests; QUEUED ones move in `nxt_unit_req_send()` after the drain.
3. Three of the new tests pass on master: they guard the new mechanism; the mutation table in section 4 quotes the
   value each one gives without its protection.
4. Cost of chunks per message for streaming and the 10 MiB segment per process: partly measured (section 8).
5. An ACK in the queue carries no SCM_CREDENTIALS (the router uses `port_msg.pid`): as in 1.19.0 to 1.36.1; noted.
6-10. Comment about `shm_retry` and the mutex, the probe comment, a 97-column line: fixed. A send failure of an MMAP
   message on a retired port marks the process FAILED for 60 s: performance only, noted. The 60 s retry has no
   test (needs a clock hook). TINY_CHUNK builds already break the order case of 39700f29 (header over 1024 bytes).

Round 3 (v3). It confirmed the order (every sender with a stream, buffers allocated in one state and sent in
another, the drain condition: the router dequeues nothing while `from_socket > 0`, one reader per engine port, the
recorded tail is at least the item's position + 1). Findings:
1. BLOCKER: with the ACK in the queue a steady state sends no datagram, so one marker without its datagram (EAGAIN
   on the non-blocking app socket after 278 unread datagrams, Go's `nxt_go_port_send()` returning 0 for an unknown
   port, a process killed between marker and datagram) stops the engine port, where master only lags. Done: v4, the
   ACK always goes to the socket (section 2, c4). The reviewer's other fix, poll(POLLOUT) and resend after a
   marker, would help master too; not done here (follow-up).
2. Non-blocking writers can wait in the drain inside an event loop: microseconds while the router runs, 1 s if it
   is stuck at a marker (then master does not finish the response either). Kept: the alternative (OOSM, 0 bytes,
   wait for SHM_ACK) brings back the wait for slow clients that round 2 removed.
3. The drain waits for the recorded tail, which can include later items of other processes; exact position needs a
   change of `nxt_nncq_enqueue()` in the router-shared header. Not done (follow-up); with v4 a stuck marker behaves
   as on master.
4. The drain case could pass without the drain on a slow machine. Done: the 20 ms start when the header is queued,
   and the case fails ("the body part went out before the router took the header") if a data datagram of the
   request comes before Y's. Mutation M6 gives exactly that line.
5. Missing tests: a probe at the real "shm" limit, the drain time-out, the 60 s gate, the `allocated_chunks` fix.
   Not added (follow-up); the drain time-out shows in mutation M4. The no-shm and plain-buffer comments are fixed.
6. The time-out counted loop turns (1.5-2 s with timer slack): done, a 1 s deadline from the monotonic clock.
7. The `allocated_chunks` fix is in the commit message.
8. Mutex and free-map scan per small buffer in threaded apps, a 10 MiB segment per process: not measured (section 8).
9. `lib->shm` and `lib->shm_retry` are benign races (they choose plain or shared memory and the retry time, never
   the order); TSan will report them. Noted.
10. NXT_MMAP_TINY_CHUNK builds: a probe can need a whole 64-chunk segment. Debug-only build; noted.
11. Comments, `nxt_sched_yield()`: done.

Trailer: these reviews were fresh Claude Opus 5.5 agents whose findings changed the commit, so the commit carries
`Reviewed-by: Claude Opus 5.5 <noreply@anthropic.com>`.

## 8. Not verified, and the release call

Not verified:
- hello.php at c=1 and c=256 (only capp ran at 256); macOS, FreeBSD, s390x and NXT_MMAP_TINY_CHUNK builds.
- Node, Go, Ruby, Perl and WASM module tests (PHP, Python WSGI/ASGI/websockets and Java ran).
- Contention on `lib->outgoing.mutex` for small buffers in threaded applications (Java, Python threads, Go), and
  the memory of the one 10 MiB segment each process now makes at its first response (only touched pages count).
- The 60 s FAILED retry and a probe at the real "shm" limit have no C test; the drain time-out shows only in a
  mutation run.
- Router-side cost of 28-byte shared memory messages against plain datagrams was measured only in aggregate (the
  lab), not per function after the change.

Found on the way, not caused by this change (both present on 856d23b9):
- Load B4 (application "shm" limit of one 10 MiB segment, 8 slow clients downloading 8 MiB, load A against
  index.php): in 1 of 3 runs on each of 856d23b9 and v3, one request never gets an answer; /status keeps
  `requests.active=1` and one application process stays blocked in recvmsg() on its port socket. Probably a
  process waiting for an SHM_ACK that never comes. Worth an issue with a debug build trace.
- `nxt_unit_response_send()` leaves `req->response_buf` pointing at a freed buffer when the send fails, and a later
  `nxt_unit_request_done()` sends it again.

Release call: the code is ready as a PR, but I would ship it in 1.37.2, not 1.37.1.
- It changes the transport of every response, for every module, and the memory of small responses (a 16 KiB shared
  memory chunk each, plain memory when none is free). The cost it removes is CPU, not a wrong answer.
- The final shape (v4: ACK on the socket) came from the third review and has had no fresh review of its own; v3
  had one blocker, which v4 removes by restoring master's ACK path.
- Node, Go, Ruby, Perl, WASM and macOS were not run; macOS shared memory has surprised this project before (the
  16 KiB page segment fix already in 1.37.1).
If it must be in 1.37.1: a fresh review of the final diff, the full CI matrix including macOS and the Node and Go
legs, and one Drupal CI pipeline run on it first (that pipeline found the 1.37.0 race).

## 9. Final commit

Branch `perf/libunit-message-cost` in the session worktree `wt-msgcost`: one commit, 5ed400102 "libunit: send
the response of a request through the port queue again", on origin/master 24ea96ed2 (rebased; the CHANGES and
changes.xml conflict with 8d921f3f was textual, both entries kept). src/nxt_unit.c sha256 6a350a39265fd5f0... is the
one measured as v4 (the lab patch tools/perf-lab/jobs/patches/libunit-message-cost.diff, sha256 4622152d4240f1a2...).
The final commit: `unit-build -t remote -H build --hardening tests` (release, strict) PASSED: 0 warnings,
`./build/tests` exit 0, hook gate exit 0; `unit_msg_test`, `unit_close_test`, `unit_port_recv_test` exit 0. The same
in a gcc 14.2 container on the Docker host. Not pushed.

