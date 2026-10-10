# Capturing a faulting PC, and what actually faults on ARMv7

## Capture the faulting instruction

Host core dumps are usually intercepted (apport and friends), and the crash is
in a forked child anyway. Install a `SIGBUS`/`SIGSEGV` handler that records the
PC and registers to a **fixed file**, so the record survives pytest's teardown
of the temp directory:

```c
static void crash(int sig, siginfo_t *si, void *ucv)
{
    ucontext_t *uc = ucv;
    char b[256];
    int  n = snprintf(b, sizeof(b), "CRASH sig=%d addr=%p pc=0x%lx lr=0x%lx\n",
                      sig, si->si_addr,
                      (unsigned long) uc->uc_mcontext.arm_pc,
                      (unsigned long) uc->uc_mcontext.arm_lr);
    /* pre-created 0666 by the runner */
    int fd = open("/src/CRASH.txt", O_WRONLY | O_APPEND);
    if (fd >= 0) { write(fd, b, n); /* + /proc/self/maps */ close(fd); }
    _exit(107);
}
```

Install it as the first statement of `nxt_lib_start()`. Dump `/proc/self/maps`
alongside the PC — you need the text base to turn an absolute PC into a file
offset, and it is randomised per process.

The file *must* be pre-created world-writable and opened without `O_CREAT`,
because the router runs unprivileged; if a capture comes back empty, validate
the instrument before believing the result. Full reasoning in
[`freeunit-debug/references/instrumenting.md`](../../freeunit-debug/references/instrumenting.md).

Then resolve it:

```bash
OFF=$(printf '%x' $((0xPC - 0xTEXTBASE)))
addr2line -f -C -i -e build/sbin/unitd "0x$OFF"
objdump -d build/sbin/unitd --start-address=$((0x$OFF-0x30)) \
                            --stop-address=$((0x$OFF+0xc)) | tail -30
```

The disassembly is the part that convinces. A fault two instructions after a
`bl nxt_mp_get` whose return register is the recorded fault address is proof; an
`addr2line` symbol on its own is an assertion.

## Measured ARMv7 alignment behaviour

`ldrd`/`strd`/`ldm`/`stm` require only *word* alignment, not doubleword. With
`SCTLR.A=0`, measured on the target:

```
ldrd @ …d0 (mod 8 = 0): OK
ldrd @ …d4 (mod 8 = 4): OK          <- 4 mod 8 does not fault
ldrd @ …d2 (mod 4 = 2): SIGBUS
ldrd @ …d1 (odd):       SIGBUS
```

A 4-mod-8 pointer is still UB and can fault via alignment-hinted NEON
(`vld1.64 [rN:64]`) or on a stricter target (ARMv5TE, SPARC) — but do not claim
it faults on ARMv7 without measuring. Re-measure with an inline `asm("ldrd …")`
against `aligned_alloc` offsets before relying on it.
