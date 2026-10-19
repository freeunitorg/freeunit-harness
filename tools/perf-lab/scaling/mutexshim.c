/*
 * LD_PRELOAD shim for the measurement only: counts pthread_mutex_lock() calls and the time spent
 * waiting for a contended mutex, per call site.  The site is the caller of nxt_thread_mutex_lock()
 * (found by walking one frame pointer; unitd is built with -fno-omit-frame-pointer).
 *
 * A call first tries pthread_mutex_trylock().  It counts as contended only when the trylock fails,
 * and the wait is the time of the following blocking pthread_mutex_lock().
 *
 * Every process that loads the shim rewrites $MUTEXSHIM_OUT/mutex-<pid>-<uid>.tsv once a second
 * (default directory /out):
 *   site  object_base  object_name  calls  contended  wait_ns
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
    _Atomic uintptr_t       site;
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
find(uintptr_t site)
{
    unsigned   i, n;
    uintptr_t  cur, zero;
    slot_t     *s;

    i = (unsigned) ((site >> 3) * 2654435761u) % SLOTS;

    for (n = 0; n < SLOTS; n++) {
        s = &tab[(i + n) % SLOTS];
        cur = atomic_load(&s->site);

        if (cur == site) {
            return s;
        }

        if (cur == 0) {
            zero = 0;
            if (atomic_compare_exchange_strong(&s->site, &zero, site)
                || zero == site)
            {
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
    char      path[300];
    unsigned  i;
    Dl_info   info;
    slot_t    *s;

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
            if (atomic_load(&s->site) == 0) {
                continue;
            }

            info.dli_fname = "?";
            info.dli_fbase = NULL;
            dladdr((void *) s->site, &info);

            if (info.dli_fname == NULL || info.dli_fname[0] != '/') {
                info.dli_fname = exe;
            }

            fprintf(f, "%#lx\t%#lx\t%s\t%lu\t%lu\t%lu\n", (unsigned long) s->site,
                    (unsigned long) info.dli_fbase, info.dli_fname,
                    atomic_load(&s->calls), atomic_load(&s->contended),
                    atomic_load(&s->wait_ns));
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
        atomic_store(&tab[i].site, 0);
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
    uintptr_t        site;
    slot_t           *s;
    struct timespec  a, b;

    resolve();

    /* Our frame pointer; the frame of nxt_thread_mutex_lock() is above it. */
    fp = (void **) __builtin_frame_address(0);
    fp1 = (fp != NULL) ? (void **) fp[0] : NULL;
    site = (fp1 != NULL) ? (uintptr_t) fp1[1] : (uintptr_t) fp[1];

    s = find(site);
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
