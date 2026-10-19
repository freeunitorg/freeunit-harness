/*
 * LD_PRELOAD shim for the measurement only: counts pthread_mutex_lock() calls and the time spent
 * waiting for a contended mutex, per call site.  The shim records two return addresses: its own
 * (the function that called pthread_mutex_lock()) and the one a frame above it (found by walking
 * one frame pointer; unitd is built with -fno-omit-frame-pointer).  locks.py sites names the first
 * one, unless it is in nxt_thread_mutex_lock(); then it names the caller of that wrapper.  libunit
 * calls pthread_mutex_lock() directly, so the first address is the site there.
 *
 * A call first tries pthread_mutex_trylock().  It counts as contended only when the trylock fails,
 * and the wait is the time of the following blocking pthread_mutex_lock().
 *
 * Every process that loads the shim rewrites $MUTEXSHIM_OUT/mutex-<pid>-<uid>.tsv once a second
 * (default directory /out):
 *   site  object_base  object_name  calls  contended  wait_ns  caller  caller_base  caller_object
 * The uid is in the name because a process that drops to nobody cannot reopen its old file.
 * Read the counts as the difference of two snapshots (sweep.sh LOCKS=1, locks.py), never once
 * at exit.
 *
 * Build: cc -O2 -fno-omit-frame-pointer -shared -fPIC -o mutexshim.so mutexshim.c -ldl -lpthread
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define SLOTS  1024

typedef struct {
    _Atomic uintptr_t       key;        /* a hash of (site, caller); 0: a free slot */
    _Atomic uintptr_t       site;
    _Atomic uintptr_t       caller;
    _Atomic unsigned long   calls;
    _Atomic unsigned long   contended;
    _Atomic unsigned long   wait_ns;
} slot_t;

static slot_t  tab[SLOTS];
static int     (*real_lock)(pthread_mutex_t *);
static int     (*real_trylock)(pthread_mutex_t *);
static char    outdir[256] = "/out";
static char    exe[512] = "?";

static slot_t *
find(uintptr_t site, uintptr_t caller)
{
    unsigned   i, n;
    uintptr_t  key, cur, zero;
    slot_t     *s;

    key = (site * (uintptr_t) 0x9e3779b97f4a7c15ull) ^ caller;
    if (key == 0) {
        key = 1;
    }

    i = (unsigned) ((key >> 3) * 2654435761u) % SLOTS;

    for (n = 0; n < SLOTS; n++) {
        s = &tab[(i + n) % SLOTS];
        cur = atomic_load(&s->key);

        if (cur == key) {
            return s;
        }

        if (cur == 0) {
            zero = 0;
            if (atomic_compare_exchange_strong(&s->key, &zero, key)) {
                atomic_store(&s->caller, caller);
                atomic_store(&s->site, site);   /* last: the dumper skips a slot without a site */
                return s;
            }

            if (zero == key) {
                return s;
            }
        }
    }

    return NULL;
}

static void
resolve(void)
{
    if (real_lock == NULL) {
        real_trylock = dlsym(RTLD_NEXT, "pthread_mutex_trylock");
        real_lock = dlsym(RTLD_NEXT, "pthread_mutex_lock");
    }
}

static void *
dumper(void *arg)
{
    FILE      *f;
    char       path[300];
    unsigned   i;
    Dl_info    info, cinfo;
    slot_t     *s;
    uintptr_t  site, caller;

    (void) arg;

    for ( ;; ) {
        sleep(1);

        /* uid in the name: a file made before the drop to nobody cannot be reopened after it */
        snprintf(path, sizeof(path), "%s/mutex-%d-%d.tsv", outdir, (int) getpid(), (int) getuid());
        f = fopen(path, "w");
        if (f == NULL) {
            continue;
        }

        for (i = 0; i < SLOTS; i++) {
            s = &tab[i];
            site = atomic_load(&s->site);
            if (site == 0) {
                continue;
            }

            caller = atomic_load(&s->caller);

            info.dli_fname = "?";
            info.dli_fbase = NULL;
            dladdr((void *) site, &info);

            if (info.dli_fname == NULL || info.dli_fname[0] != '/') {
                info.dli_fname = exe;
            }

            cinfo.dli_fname = "?";
            cinfo.dli_fbase = NULL;
            if (caller != 0) {
                dladdr((void *) caller, &cinfo);
            }

            if (cinfo.dli_fname == NULL || cinfo.dli_fname[0] != '/') {
                cinfo.dli_fname = exe;
            }

            fprintf(f, "%#lx\t%#lx\t%s\t%lu\t%lu\t%lu\t%#lx\t%#lx\t%s\n", (unsigned long) site,
                    (unsigned long) info.dli_fbase, info.dli_fname,
                    atomic_load(&s->calls), atomic_load(&s->contended),
                    atomic_load(&s->wait_ns), (unsigned long) caller,
                    (unsigned long) cinfo.dli_fbase, cinfo.dli_fname);
        }

        fclose(f);
    }

    return NULL;
}

static void
start_dumper(void)
{
    pthread_t  t;

    if (pthread_create(&t, NULL, dumper, NULL) == 0) {
        pthread_detach(t);
    }
}

/* Unit forks its router and application processes without exec: the dumper thread does not
   survive the fork, and the counts of the parent are not the counts of the child. */
static void
child_after_fork(void)
{
    unsigned  i;

    for (i = 0; i < SLOTS; i++) {
        atomic_store(&tab[i].key, 0);
        atomic_store(&tab[i].site, 0);
        atomic_store(&tab[i].caller, 0);
        atomic_store(&tab[i].calls, 0);
        atomic_store(&tab[i].contended, 0);
        atomic_store(&tab[i].wait_ns, 0);
    }

    start_dumper();
}

__attribute__((constructor)) static void
shim_init(void)
{
    char  *d;

    resolve();

    {
        ssize_t  len = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
        exe[len > 0 ? len : 0] = '\0';
    }

    d = getenv("MUTEXSHIM_OUT");
    if (d != NULL && d[0] != '\0') {
        snprintf(outdir, sizeof(outdir), "%s", d);
    }

    pthread_atfork(NULL, NULL, child_after_fork);
    start_dumper();
}

int
pthread_mutex_lock(pthread_mutex_t *m)
{
    int              rc;
    void             **fp, **fp1;
    uintptr_t        site, caller;
    slot_t           *s;
    struct timespec  a, b;

    resolve();

    /* site: the function that called us.  caller: one frame above it, which locks.py uses
       when site is in nxt_thread_mutex_lock(). */
    site = (uintptr_t) __builtin_return_address(0);
    fp = (void **) __builtin_frame_address(0);
    fp1 = (fp != NULL) ? (void **) fp[0] : NULL;
    caller = (fp1 != NULL) ? (uintptr_t) fp1[1] : 0;

    s = find(site, caller);
    if (s != NULL) {
        atomic_fetch_add(&s->calls, 1);
    }

    if (real_trylock(m) == 0) {
        return 0;
    }

    clock_gettime(CLOCK_MONOTONIC, &a);
    rc = real_lock(m);
    clock_gettime(CLOCK_MONOTONIC, &b);

    if (s != NULL) {
        atomic_fetch_add(&s->contended, 1);
        atomic_fetch_add(&s->wait_ns, (unsigned long) ((b.tv_sec - a.tv_sec) * 1000000000L
                                                     + (b.tv_nsec - a.tv_nsec)));
    }

    return rc;
}
