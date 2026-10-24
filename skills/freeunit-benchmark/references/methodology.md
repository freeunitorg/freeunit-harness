# Benchmark methodology

## What it measures

Per build, per scenario: RPS, p50/p99 latency, failed requests, and router
CPU-microseconds per request. `oha` is primary; ApacheBench runs as a secondary
for continuity with older results (`SKIP_AB=1` to drop it).

| Scenario | What it is for |
|---|---|
| `ret200_ka_hi` | keepalive at high concurrency (c=256) — throughput ceiling |
| `ret200_ka_lo` | keepalive at low concurrency (c=8) — listener wake distribution / herd effects |
| `ret200_close` | `Connection: close` (c=16) — accept and teardown path |
| `proxy_ka` | proxy to self (c=64) — the client side of the server |
| `php_ka` | PHP hello app (c=8) — process-tree CPU accounting |

## Knobs

`SERVER_CORES` / `LOAD_CORES` (default `0-3` and `4-7`), `N_HIGH`/`N_LOW`/etc
for request counts, `MAX_FAILED`, `RESULTS` for the output file, `ALLOW_SAME=1`
to permit an A/A run. The full list is in the header of
`tools/bench-engines.sh`.

## Reading the output

**CPU per request is the tighter signal.** RPS on a keepalive scenario is noisy
— the A/A floor is typically several percent — while router CPU-microseconds per
request is stable to about a percent on the close and high-concurrency
scenarios. A change that moves CPU/req but not RPS is still a real change.

**Interleave, and keep the round count even.** The harness alternates A/B and
B/A per round so thermal drift and warmup cannot stay correlated with one label.
With an odd count one build starts first more often, so a position effect does
not cancel.

**Results are only comparable within one methodology.** Kernel version,
liburing version, load generator version and core layout all move the numbers.
The harness header and run directory capture most of it; the kernel and library
versions are on you to record.

## Host kernel and sysctl tuning

PR [#810](https://github.com/freeunitorg/freeunit/pull/810) raises the soft
`RLIMIT_NOFILE` (`ulimit -Sn`) in the router and controller to
`min(rlim_max, 1048576)` (on macOS, `min(OPEN_MAX, rlim_max)`), so containers
and systemd services starting at `1024:524288` no longer hit `EMFILE (24: Too
many open files)` at ~1000 connections. Once per-process descriptor limits are
raised, host kernel limits must be sized to match so the bottleneck does not
simply move to `ENFILE`, listen-queue drops, ephemeral port exhaustion, or
VMA/writeback stalls.

Run `./tools/doctor` (or `make doctor`) before benchmarking to verify the live
`RLIMIT_NOFILE` (`ulimit -Sn` / `ulimit -Hn`), `fs.file-max`,
`net.core.somaxconn`, and `fs.aio-max-nr` values on the current host.

| Parameter | Typical default | Recommended (`bench` / prod) | Summary |
|---|---|---|---|
| `fs.nr_open` | `1048576` | `>= 1048576` | Per-process kernel ceiling for `RLIMIT_NOFILE` (`rlim_max`). |
| `fs.file-max` | ~10% of RAM | `>= 2097152` | System-wide open file table across all processes (`ENFILE` if exhausted). |
| `fs.epoll.max_user_watches` | ~4% of lowmem | `>= 1048576` | Maximum file descriptors registered across `epoll` instances for one UID. |
| `net.core.somaxconn` | `4096` (`128` on old kernels) | `>= 8192`–`65535` | Upper bound on `listen(fd, backlog)` for completed TCP handshakes awaiting `accept4()`. |
| `net.ipv4.tcp_max_syn_backlog` | `1024`–`4096` | `>= 8192`–`65535` | Queue size for half-open `SYN_RECV` sockets before handshake completion. |
| `net.core.netdev_max_backlog` | `1000` | `>= 16384` | Per-CPU ingress packet queue between NIC driver poll and `NET_RX_SOFTIRQ`. |
| `net.ipv4.ip_local_port_range` | `32768 60999` | `10240 65535` | Ephemeral source port range for local load generators and upstream proxy connections. |
| `fs.aio-max-nr` | `65536` | `>= 262144`–`1048576` | System-wide concurrent Linux AIO context ring slots. |
| `vm.max_map_count` | `65530` | `>= 262144` | Maximum virtual memory areas (`mmap` VMA regions) per process. |
| `vm.dirty_background_ratio` / `vm.dirty_ratio` | `10` / `20` | `5` / `15` | Page-cache dirty thresholds for background flusher threads vs synchronous writeback stalls. |

### Why each parameter affects FreeUnit performance and correctness

- **`fs.nr_open` (`>= 1048576`):** Sets the maximum value that `setrlimit(RLIMIT_NOFILE)` accepts for `rlim_max` and `rlim_cur`. In PR [#810](https://github.com/freeunitorg/freeunit/pull/810), `nxt_process_nofile_raise()` clamps the router and controller soft limit to `min(rlim_max, 1048576)`. If `fs.nr_open` is lowered below `rlim_max` or below the desired concurrency ceiling, neither systemd (`LimitNOFILE=`), container runtimes (`--ulimit nofile`), nor `unitd` can raise the per-process descriptor table to hold client sockets, port sockets, and static file descriptors.
- **`fs.file-max` (`>= 2097152`):** Bounds the kernel's global `struct file` allocation table across the entire host. Once the router's soft `RLIMIT_NOFILE` is raised up to `524288` or `1048576`, a single benchmark box runs `unitd` (router, controller, and application workers) alongside load generators (`oha`, `ab`) that hold their own client sockets. If `fs.file-max` is smaller than the combined open descriptors of router + workers + load generator, `socket()`, `accept4()`, or `openat()` fails with `ENFILE (23: Too many open files in system)` even though each process is well below its own `RLIMIT_NOFILE`.
- **`fs.epoll.max_user_watches` (`>= 1048576`):** Limits the total number of file descriptors a single non-root UID can register across all `epoll` instances (each registered watch consumes ~90–160 bytes of kernel slab memory). In FreeUnit, every router worker thread runs its own `epoll` instance and registers every accepted client connection, listen socket, and IPC port fd via `epoll_ctl(EPOLL_CTL_ADD)`. When `RLIMIT_NOFILE` allows hundreds of thousands of connections under the `unitd` user, hitting `max_user_watches` makes `epoll_ctl()` fail with `ENOSPC (28: No space left on device)`, causing dropped connections and false errors under high-concurrency keepalive benchmarks.
- **`net.core.somaxconn` (`>= 8192`–`65535`):** Silently truncates the `backlog` argument passed to `listen(fd, backlog)` to `min(backlog, net.core.somaxconn)`. Under burst connection arrival (`ret200_close` or high-concurrency startup), if the router thread is busy draining an epoll batch and the completed-handshake accept queue fills past `somaxconn`, the kernel drops new ACKs or sends `RST` (`TcpExtListenDrops` / `TcpExtListenOverflows`), producing failed requests and artificial p99 tail latency spikes even when CPU and file descriptors are plentiful. Pair with the listener `"backlog"` option in Unit configuration.
- **`net.ipv4.tcp_max_syn_backlog` (`>= 8192`–`65535`):** Bounds the per-listener queue of half-open (`SYN_RECV`) connections waiting for the final ACK of the three-way handshake. During high-rate short-lived connection benchmarks (`ret200_close`) or multi-core `oha` bursts, a small SYN backlog overflows before sockets ever reach the `accept4()` queue, triggering SYN cookies or `SYN` drops and distorting accept-path CPU/req measurements.
- **`net.core.netdev_max_backlog` (`>= 16384`):** Sizes each CPU's ingress packet queue (`softnet_data.input_pkt_queue`) when packets arrive faster than `NET_RX_SOFTIRQ` drains them. On loopback and 10/25/100 GbE benchmark hosts generating hundreds of thousands of RPS (`ret200_ka_hi`), the default `1000`-packet queue overflows during micro-bursts (visible as non-zero drops in Column 2 of `/proc/net/softnet_stat`), causing TCP retransmissions and multi-millisecond p99 spikes unrelated to `unitd` code changes.
- **`net.ipv4.ip_local_port_range` (`10240 65535`):** Expands the pool of ephemeral source ports from ~28,000 (`32768–60999`) to ~55,000. In `ret200_close` (rapid connect/close churning through `TIME_WAIT`) and `proxy_ka` / upstream proxying (where the router itself opens outbound connections to backends alongside the load generator's connections to the router), a narrow port range causes `connect()` to fail with `EADDRNOTAVAIL (99: Cannot assign requested address)` or burn kernel spin time scanning the inet hash table for a free `(src_ip, src_port, dst_ip, dst_port)` tuple.
- **`fs.aio-max-nr` (`>= 262144`–`1048576`):** Caps the total number of concurrent in-flight slots across kernel POSIX/Linux AIO rings (`io_setup()`). On hosts where FreeUnit serves static assets with async I/O or runs alongside database/storage backends on the same machine, raising `RLIMIT_NOFILE` without raising `fs.aio-max-nr` causes `io_setup()` to fail with `EAGAIN` once concurrent async I/O contexts exceed `65536`.
- **`vm.max_map_count` (`>= 262144`):** Limits the number of distinct `mmap()` virtual memory areas (VMAs) a process may hold. FreeUnit's router and application processes communicate over shared-memory port queues and pass request/response bodies via on-demand `mmap()` shared-memory segments; language runtimes (PHP OPcache/JIT, JVM, Python, Rust/Go allocators) also allocate per-thread arenas and guard pages. Under high concurrency (enabled by the raised `RLIMIT_NOFILE` in PR [#810](https://github.com/freeunitorg/freeunit/pull/810)), hitting the default `65530` VMA ceiling makes `mmap()` fail with `ENOMEM`, causing shared-memory allocation failures in the router or worker crashes.
- **`vm.dirty_background_ratio` / `vm.dirty_ratio` (`5` / `15`):** Controls when kernel `flusher` threads start asynchronous writeback (`dirty_background_ratio`, default 10% of RAM) versus when threads calling `write()` are forced into synchronous disk I/O (`dirty_ratio`, default 20% of RAM). On large-memory benchmark hosts, 10–20% of RAM represents many gigabytes of buffered access logs or temporary request body files; when the kernel flushes that backlog all at once or crosses `dirty_ratio`, router threads block in `write()`, introducing bimodal p99 latency spikes across A/B rounds. Starting background flush earlier (`5` / `15`) keeps writeback continuous and prevents synchronous stalls.

Example `/etc/sysctl.d/90-freeunit.conf`:

```ini
fs.nr_open = 1048576
fs.file-max = 2097152
fs.epoll.max_user_watches = 1048576
fs.aio-max-nr = 524288
net.core.somaxconn = 32768
net.ipv4.tcp_max_syn_backlog = 32768
net.core.netdev_max_backlog = 16384
net.ipv4.ip_local_port_range = 10240 65535
vm.max_map_count = 262144
vm.dirty_background_ratio = 5
vm.dirty_ratio = 15
```
