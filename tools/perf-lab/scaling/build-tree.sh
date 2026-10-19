#!/bin/sh
# build-tree.sh -- build one FreeUnit tree for the scaling kit, as the perf lab
# does: release -O2, frame pointers, no --debug, no --tests. Then build the
# libunit test application capp.c as build/lab_capp.
#
# In the lab image (run.sh build): the tree is /src and capp.c is /capp.c.
# Natively (NATIVE=1 run.sh build): SRC, CAPP and LOGDIR name the paths.
set -e
cd "${SRC:-/src}"
log=${LOGDIR:-/tmp}
./configure --no-regex --cc-opt='-O2 -fno-omit-frame-pointer' > "$log/conf.log" 2>&1
make -j"${JOBS:-4}" unitd > "$log/make.log" 2>&1
make -j"${JOBS:-4}" build/lib/libunit.a >> "$log/make.log" 2>&1
cc -O2 -fno-omit-frame-pointer -I src -I build/include -o build/lab_capp "${CAPP:-/capp.c}" \
    build/lib/libunit.a -lpthread
grep -q -- '-O2 -fno-omit-frame-pointer' build/Makefile || { echo "build-tree: the flags are not in build/Makefile" >&2; exit 1; }
echo "built: build/sbin/unitd build/lab_capp"
