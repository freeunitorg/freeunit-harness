/*
 * capget-probe — call SYS_capget directly and report what actually happened.
 *
 * secexec's whole job is to make SYS_capget fail with a chosen errno inside
 * whatever it execs. A log line saying the filter "installed" does not
 * prove that -- prctl(PR_SET_SECCOMP) can succeed while the filter still
 * gets the jump logic wrong, or while the syscall number it matched against
 * is wrong for the running ABI. This program is the thing that actually
 * makes the call, from inside the exec'd image (i.e. after the filter has
 * survived an execve, which is the property secexec exists to provide), and
 * reports the syscall's own return value and errno -- not what secexec
 * logged about itself.
 *
 * Usage:
 *   capget-probe [--want=ok|EPERM|ENOSYS]
 *
 *   --want=ok      assert capget() succeeds (ret == 0): the no-filter case.
 *   --want=EPERM   assert capget() fails with exactly EPERM.
 *   --want=ENOSYS  assert capget() fails with exactly ENOSYS.
 *   (omitted)      make the call, print the outcome, always exit 0.
 *
 * Always prints one line to stdout:
 *   capget: ret=<n> errno=<number>(<name-or-?>)
 *
 * Exit: 0 outcome matches --want (or no --want given), 1 mismatch, 2 usage.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <sys/syscall.h>

#include <linux/capability.h>

#ifndef _LINUX_CAPABILITY_VERSION_3
#define _LINUX_CAPABILITY_VERSION_3 0x20080522
#endif

static const char *
errno_name(int e)
{
    switch (e) {
    case 0:      return "0";
    case EPERM:  return "EPERM";
    case ENOSYS: return "ENOSYS";
    case EFAULT: return "EFAULT";
    case EINVAL: return "EINVAL";
    case ESRCH:  return "ESRCH";
    default:     return NULL;
    }
}

int
main(int argc, char **argv)
{
    const char                      *want;
    struct __user_cap_header_struct  hdr;
    struct __user_cap_data_struct    data[2];
    long                              ret;
    int                               err;
    const char                       *name;
    int                               i;

    want = NULL;
    for (i = 1; i < argc; i++) {
        if (strncmp(argv[i], "--want=", 7) == 0) {
            want = argv[i] + 7;
            continue;
        }
        fprintf(stderr, "usage: capget-probe [--want=ok|EPERM|ENOSYS]\n");
        return 2;
    }

    memset(&hdr, 0, sizeof(hdr));
    memset(data, 0, sizeof(data));
    hdr.version = _LINUX_CAPABILITY_VERSION_3;
    hdr.pid = 0; /* self, same as nxt_capability.c's nxt_pid usage */

    errno = 0;
    ret = syscall(SYS_capget, &hdr, data);
    err = (ret == -1) ? errno : 0;
    name = errno_name(err);

    printf("capget: ret=%ld errno=%d(%s)\n", ret, err, name ? name : "?");
    fflush(stdout);

    if (want == NULL) {
        return 0;
    }

    if (strcmp(want, "ok") == 0) {
        if (ret == 0) {
            return 0;
        }
        fprintf(stderr, "capget-probe: wanted success, got errno %d(%s)\n",
            err, name ? name : "?");
        return 1;
    }

    if (ret != -1) {
        fprintf(stderr, "capget-probe: wanted errno %s, capget succeeded\n",
            want);
        return 1;
    }

    if (name != NULL && strcmp(name, want) == 0) {
        return 0;
    }

    fprintf(stderr, "capget-probe: wanted errno %s, got %d(%s)\n", want, err,
        name ? name : "?");
    return 1;
}
