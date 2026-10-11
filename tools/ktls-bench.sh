#!/bin/sh
# ktls-bench.sh -- build tools/ktls-bench.c and run the kTLS question in C.
# Copyright (C) FreeUnit contributors.
#
# WHAT IT ANSWERS
#   Does kernel TLS save sender CPU per MiB against user-space OpenSSL, when
#   both arms run the same C loop? exp PR 31 adds "tls": {"ktls": true}; the
#   Python probe in exp PR 30 compared a Python SSL_write loop with a C
#   sendfile() loop, so its multiplier carries interpreter cost. See the
#   header of tools/ktls-bench.c for the arms and the fairness rules.
#
# USAGE
#   tools/ktls-bench.sh [--quick]
#     --quick   256 MiB, 3 reps (default: 1 GiB, 5 reps)
#   Environment: KTLS_CC (default cc), OPENSSL_PREFIX (an OpenSSL install with
#   include/ and lib/ or lib64/; default: the system OpenSSL).
#
# NEEDS
#   - OpenSSL 3.0 or newer, built with kTLS (Ubuntu and Fedora are).
#   - The kernel "tls" module: `sudo modprobe tls`. Without it the kTLS arms
#     print "skip" and the user-space arms still run.
#   - A quiet box. Run it on the perf lab, not next to a build.
#
# OUTPUT
#   run/ktls-bench/<UTC time>/report.tsv  every line of every case
#   run/ktls-bench/<UTC time>/summary.txt the "median" lines, the environment,
#                                         and /proc/net/tls_stat before/after
#   The report has no host name and no path; it can be posted as is.
#
# Exit: 0 done, 1 build or run error.
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root=$(dirname -- "$here")
out=$root/run/ktls-bench/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out"

bytes=1073741824 reps=5
[ "${1:-}" = "--quick" ] && bytes=268435456 reps=3

cc=${KTLS_CC:-cc}
if [ -n "${OPENSSL_PREFIX:-}" ]; then
    lib=$OPENSSL_PREFIX/lib
    [ -d "$OPENSSL_PREFIX/lib64" ] && lib=$OPENSSL_PREFIX/lib64
    flags="-I$OPENSSL_PREFIX/include -L$lib -Wl,-rpath,$lib"
else
    flags=
fi

# shellcheck disable=SC2086
$cc -O2 -Wall -Wextra -Werror -o "$out/ktls-bench" "$here/ktls-bench.c" \
    $flags -lssl -lcrypto

data=$out/data.bin
head -c "$bytes" /dev/urandom > "$data"
cat "$data" > /dev/null                 # warm the page cache for every arm

tls_state() {
    if [ -r /proc/net/tls_stat ]; then
        grep -E 'TlsCurr|TlsTx|TlsRx' /proc/net/tls_stat | tr -s ' \t' ' ' | paste -sd ' ' -
    else
        echo "no /proc/net/tls_stat (tls module not loaded)"
    fi
}

{
    echo "kernel     $(uname -r)"
    echo "openssl    $("$out/ktls-bench" --version)"
    echo "cpus       $(nproc)"
    echo "aes-ni     $(grep -qw aes /proc/cpuinfo && echo yes || echo no)"
    echo "bytes      $bytes x $reps reps, loopback, AES-128-GCM"
    echo "tls_stat0  $(tls_state)"
} > "$out/env.txt"

run() {
    "$out/ktls-bench" --file "$data" --bytes "$bytes" --reps "$reps" "$@"
}

{
    run --arms plain --tls 1.3
    for tls in 1.3 1.2; do
        for w in 131072 16384 4096; do
            run --arms ossl,ktls-write --tls "$tls" --write "$w" | tail -n +2
        done
        run --arms ktls-sendfile --tls "$tls" | tail -n +2
    done
} | tee "$out/report.tsv"

echo "tls_stat1  $(tls_state)" >> "$out/env.txt"

{
    cat "$out/env.txt"
    echo
    head -1 "$out/report.tsv"
    grep -E '^(median|skip)' "$out/report.tsv"
} > "$out/summary.txt"

rm -f "$data"
echo
cat "$out/summary.txt"
echo
echo "ktls-bench: report in run/ktls-bench/$(basename "$out")/"
