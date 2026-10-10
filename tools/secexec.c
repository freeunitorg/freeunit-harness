/*
 * secexec — install a seccomp BPF filter that fails one syscall with a
 * chosen errno, then exec a command under it.
 *
 * Built for the FreeUnit capget-fallback work (unit plan-capget-fallback.md
 * S3): pytest cannot mock a syscall. secexec is preferred over an
 * LD_PRELOAD shim not because a preload is impossible here -- glibc's
 * syscall() is an ordinary interposable symbol, so a preload COULD shadow
 * nxt_capability.c's `syscall(SYS_capget, ...)` call site -- but because a
 * real seccomp filter is the actual thing production systemd/bwrap/OCI
 * filters look like: this proves the code path with the real mechanism a
 * filtered deployment uses, not a stand-in for it.
 *
 * An earlier draft of this filter dropped the AUDIT_ARCH check to "keep it
 * portable". That is backwards: without it the filter matches the syscall
 * *number* alone, and the same number means a different call under a compat
 * ABI (e.g. an x86 process on an x86_64 kernel, or a 32-bit arm syscall
 * table) -- so a filter meant to fail capget can silently fail an unrelated
 * syscall instead, or fail to fail capget at all. This filter loads
 * seccomp_data.arch first and kills the process outright on a mismatch
 * rather than only checking what it meant to check.
 *
 * The filter attaches via prctl(2), which is preserved across execve(2) by
 * design (that is the entire point of a *wrapper*) -- see capget-probe.c,
 * the companion program in this directory, for a probe that exec's under
 * this filter and reports what SYS_capget actually returned in the exec'd
 * image, not just in secexec itself.
 *
 * Usage:
 *   secexec [--errno=EPERM|ENOSYS|N] [--syscall=capget|capset|N] -- CMD [ARGS...]
 *
 *   --errno=E     what the filtered syscall should return. Both signatures
 *                 unit's fallback treats as "filtered" are named; anything
 *                 else takes a bare positive number. Default EPERM.
 *   --syscall=S   which syscall to fail. Default capget (the only one this
 *                 work needs); capset and a bare number are accepted for
 *                 reuse elsewhere.
 *
 * Every other syscall, for CMD and everything it execs afterwards, is
 * unrestricted -- this is a single-syscall denylist, not a sandbox.
 *
 * Exit: CMD's own exit status once exec succeeds; 126 if exec fails; 1 if
 * the filter itself could not be installed (see the EACCES hint below); 2
 * on a usage error.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <sys/prctl.h>
#include <sys/syscall.h>

#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>

#ifndef SECCOMP_MODE_FILTER
#define SECCOMP_MODE_FILTER 2
#endif

#ifndef SECCOMP_RET_KILL_PROCESS
#define SECCOMP_RET_KILL_PROCESS 0x80000000U
#endif

/*
 * AUDIT_ARCH_* identifies the ABI a syscall was made through, not the CPU.
 * Compiling this per target arch (rather than probing at runtime) is what
 * makes the check meaningful: it asserts "this filter was built for, and is
 * running under, exactly the ABI it was reviewed for".
 */
#if defined(__x86_64__)
#  define SECEXEC_AUDIT_ARCH AUDIT_ARCH_X86_64
#elif defined(__aarch64__)
#  define SECEXEC_AUDIT_ARCH AUDIT_ARCH_AARCH64
#elif defined(__arm__)
#  define SECEXEC_AUDIT_ARCH AUDIT_ARCH_ARM
#elif defined(__i386__)
#  define SECEXEC_AUDIT_ARCH AUDIT_ARCH_I386
#elif defined(__s390x__)
#  define SECEXEC_AUDIT_ARCH AUDIT_ARCH_S390X
#elif defined(__riscv) && __riscv_xlen == 64
#  define SECEXEC_AUDIT_ARCH AUDIT_ARCH_RISCV64
#else
#  error "secexec: no AUDIT_ARCH_* mapping for this target -- add one here, do not drop the check"
#endif

struct nr_name {
    const char *name;
    long        nr;
};

static const struct nr_name known_syscalls[] = {
    { "capget", SYS_capget },
#ifdef SYS_capset
    { "capset", SYS_capset },
#endif
};

static long
resolve_syscall(const char *s)
{
    size_t i;
    char   *end;
    long    n;

    for (i = 0; i < sizeof(known_syscalls) / sizeof(known_syscalls[0]); i++) {
        if (strcmp(known_syscalls[i].name, s) == 0) {
            return known_syscalls[i].nr;
        }
    }

    n = strtol(s, &end, 0);
    if (*s != '\0' && *end == '\0') {
        return n;
    }

    return -1;
}

static int
resolve_errno(const char *s)
{
    char *end;
    long  n;

    if (strcmp(s, "EPERM") == 0) {
        return EPERM;
    }
    if (strcmp(s, "ENOSYS") == 0) {
        return ENOSYS;
    }

    n = strtol(s, &end, 0);
    if (*s != '\0' && *end == '\0' && n > 0 && n < 4096) {
        return (int) n;
    }

    return -1;
}

static void
usage(const char *prog)
{
    fprintf(stderr,
        "usage: %s [--errno=EPERM|ENOSYS|N] [--syscall=capget|capset|N] "
        "-- CMD [ARGS...]\n", prog);
}

int
main(int argc, char **argv)
{
    const char        *errno_arg = "EPERM";
    const char        *syscall_arg = "capget";
    int                 i;
    int                 target_errno;
    long                target_nr;
    struct sock_filter  filter[7];
    struct sock_fprog   prog;

    i = 1;
    for (; i < argc; i++) {
        if (strcmp(argv[i], "--") == 0) {
            i++;
            break;
        }
        if (strncmp(argv[i], "--errno=", 8) == 0) {
            errno_arg = argv[i] + 8;
            continue;
        }
        if (strncmp(argv[i], "--syscall=", 10) == 0) {
            syscall_arg = argv[i] + 10;
            continue;
        }
        usage(argv[0]);
        return 2;
    }

    if (i >= argc) {
        usage(argv[0]);
        return 2;
    }

    target_errno = resolve_errno(errno_arg);
    if (target_errno < 0) {
        fprintf(stderr, "secexec: bad --errno value '%s'\n", errno_arg);
        return 2;
    }

    target_nr = resolve_syscall(syscall_arg);
    if (target_nr < 0) {
        fprintf(stderr, "secexec: bad --syscall value '%s'\n", syscall_arg);
        return 2;
    }

    /*
     * NO_NEW_PRIVS is required before an unprivileged process may install a
     * seccomp filter at all (CAP_SYS_ADMIN is the alternative unit does not
     * have here) -- and it is also what stops the exec'd command regaining
     * privilege (e.g. via a setuid binary) in a way that would sidestep the
     * filter's point.
     */
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == -1) {
        perror("secexec: prctl(PR_SET_NO_NEW_PRIVS)");
        return 1;
    }

    /*
     * [0] load arch
     * [1] arch == ours?  jt=skip the kill (fall into the real check)
     *                    jf=0 (fall through to the kill)
     * [2] wrong arch -> kill the process outright, don't ALLOW-by-default
     * [3] load syscall nr
     * [4] nr == target?  jt=0 (fall into RET_ERRNO)  jf=1 (skip to RET_ALLOW)
     * [5] RET_ERRNO | target_errno
     * [6] RET_ALLOW
     */
    filter[0] = (struct sock_filter) BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
        offsetof(struct seccomp_data, arch));
    filter[1] = (struct sock_filter) BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
        SECEXEC_AUDIT_ARCH, 1, 0);
    filter[2] = (struct sock_filter) BPF_STMT(BPF_RET | BPF_K,
        SECCOMP_RET_KILL_PROCESS);
    filter[3] = (struct sock_filter) BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
        offsetof(struct seccomp_data, nr));
    filter[4] = (struct sock_filter) BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
        (uint32_t) target_nr, 0, 1);
    filter[5] = (struct sock_filter) BPF_STMT(BPF_RET | BPF_K,
        SECCOMP_RET_ERRNO | ((uint32_t) target_errno & SECCOMP_RET_DATA));
    filter[6] = (struct sock_filter) BPF_STMT(BPF_RET | BPF_K,
        SECCOMP_RET_ALLOW);

    prog.len = (unsigned short) (sizeof(filter) / sizeof(filter[0]));
    prog.filter = filter;

    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog) == -1) {
        perror("secexec: prctl(PR_SET_SECCOMP)");
        if (errno == EACCES) {
            fprintf(stderr,
                "secexec: EACCES installing the filter usually means the "
                "container's OWN seccomp profile is mediating seccomp(2)/"
                "prctl(PR_SET_SECCOMP) -- run the container with "
                "--security-opt seccomp=unconfined and retry.\n");
        }
        return 1;
    }

    execvp(argv[i], &argv[i]);
    perror("secexec: execvp");
    return 126;
}
