# PR 601: chdir() versus stat() per request

Decision: REJECTED 2026-10-07.

PR: https://github.com/freeunitorg/freeunit/pull/601 (merged, tests only).
Review: https://github.com/freeunitorg/freeunit/pull/601#issuecomment-6044204857

## The idea

A PHP target that has only `root` calls `chdir()` on every request. The first version of PR 601 skipped
the call when the directory was unchanged. The PR now holds tests only.

## Why it was dropped

1. Path compare breaks symlink swaps. A deploy replaces a symlink below `root` with `rename()`.
   The path string stays the same, so the compare says "unchanged" and the process stays in the old tree.
   The test `test_php_application_root_symlink_swap` in `test/test_php_targets.py` failed on the
   path-compare code at commit 05ea7ea2 with:
   `assert '<temp>/A|A' == '<temp>/B|B'`
   Expected B|B after the swap. The process answered A|A.
2. A device and inode check with `stat()` is not cheaper than the `chdir()` it would save (numbers below).
3. Option (b), `fstat()` of the opened script, does not work. It gives the inode of the file, not of the
   directory. Hardlinked releases keep the same file inodes, so two releases look equal.

## Measurement

Host: the reference bench host. Kernel 6.8.0-136-generic. CPU: Intel(R) Xeon(R) CPU E3-1245 v5 @ 3.50GHz.
Path: `/tmp/b601/srv/www/site/releases/r1/public` (5 levels below /tmp/b601).
1,000,000 calls each, 5 runs, order alternated between runs.

Program (`b.c`):

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e9+t.tv_nsec;}
int main(int argc,char**argv){
    const char *p=argv[1]; int stat_first=atoi(argv[2]); long N=1000000; struct stat st;
    double c=0,s=0,t;
    for(int k=0;k<2;k++){
        int do_stat=(k==0)==stat_first;
        t=now();
        for(long i=0;i<N;i++){ if(do_stat) stat(p,&st); else chdir(p); }
        t=(now()-t)/N;
        if(do_stat) s=t; else c=t;
    }
    printf("chdir %.1f ns/call, stat %.1f ns/call (%s first)\n",c,s,stat_first?"stat":"chdir");
    return 0;}
```

Commands:

```
mkdir -p /tmp/b601/srv/www/site/releases/r1/public
cd /tmp/b601 && gcc -O2 -o b b.c
for i in 1 2 3 4 5; do ./b /tmp/b601/srv/www/site/releases/r1/public $(( (i+1)%2 )); done
rm -rf /tmp/b601
```

Raw output:

```
chdir 2735.3 ns/call, stat 2847.3 ns/call (chdir first)
chdir 2710.4 ns/call, stat 2764.6 ns/call (stat first)
chdir 2347.4 ns/call, stat 3058.8 ns/call (chdir first)
chdir 2446.7 ns/call, stat 2907.6 ns/call (stat first)
chdir 2581.2 ns/call, stat 2537.0 ns/call (chdir first)
```

Range: chdir 2.35 to 2.74 us. stat 2.54 to 3.06 us.

## Reading

A `stat()` costs about the same as a `chdir()`, within run-to-run noise (run 5: stat was faster).
A check by `stat()` therefore saves nothing, and the path compare is wrong. The idea has no
safe version. An earlier measurement gave chdir 2.0 to 2.8 us and stat 2.8 to 4.0 us. The chdir range
agrees. The stat range was higher then; this rerun shows stat closer to chdir. The decision holds.
