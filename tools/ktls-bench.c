/*
 * ktls-bench -- sender CPU per MiB for a TLS bulk send: user-space OpenSSL
 * against kernel TLS (kTLS), in C, over loopback.
 *
 * Copyright (C) FreeUnit contributors.
 *
 * The question is what FreeUnit would save if the kernel encrypted the
 * records. A Python driver adds interpreter cost per call to one arm only,
 * so this program measures every arm with the same C loop:
 *
 *   plain          sendfile() on plain TCP: the floor, no cryptography
 *   ossl           pread() + SSL_write(), user-space records (FreeUnit today)
 *   ktls-write     pread() + SSL_write() with SSL_OP_ENABLE_KTLS: what
 *                  "tls": {"ktls": true} in exp PR 31 does
 *   ktls-sendfile  SSL_sendfile() with kTLS: zero copy, a later step
 *
 * Fairness rules:
 *   - The sender is this process. Its CPU is getrusage(RUSAGE_SELF); the
 *     receiver is a forked child and is not counted.
 *   - The receiver is the same in every TLS arm: user-space OpenSSL, no kTLS.
 *   - The handshake is outside the window. The window ends when the receiver
 *     has all the bytes, so wall time and throughput are end to end.
 *   - One cipher for all TLS arms: AES-128-GCM. No session tickets.
 *   - The file is read from the page cache in every arm (warm it first).
 *
 * FreeUnit passes the whole coalesced buffer to SSL_write(), up to 128 KiB
 * for a static file (src/nxt_http_static.c NXT_HTTP_STATIC_BUF_SIZE);
 * NXT_TLS_BUFFER_SIZE (4096) is not used on that path. So --write 131072 is
 * the shape of FreeUnit today; 16384 and 4096 show the record-size effect.
 *
 * Usage:
 *   ktls-bench --file F [--arms a,b,..] [--tls 1.2|1.3] [--write N]
 *              [--bytes N] [--reps N]
 *   ktls-bench --version     the OpenSSL the binary runs with
 *
 * Output: one "rep" line per run and one "median" line per arm, TAB
 * separated, header first. A kTLS arm whose socket the kernel did not take
 * prints "skip" with the reason and does not count.
 * Exit: 0 done, 1 error, 2 usage.
 */

#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/sendfile.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>


#define MAX_REPS  64


typedef struct {
    double  wall;
    double  user;
    double  sys;
} sample_t;


static const char  *opt_file;
static const char  *opt_arms = "plain,ossl,ktls-write,ktls-sendfile";
static int         opt_tls = 13;
static size_t      opt_write = 131072;
static off_t       opt_bytes;
static int         opt_reps = 5;

static EVP_PKEY    *pkey;
static X509        *cert;


static void
die(const char *what)
{
    fprintf(stderr, "ktls-bench: %s: %s\n", what, strerror(errno));
    ERR_print_errors_fp(stderr);
    exit(1);
}


static void
usage(const char *msg)
{
    fprintf(stderr, "ktls-bench: %s\n"
            "usage: ktls-bench --file F [--arms a,b] [--tls 1.2|1.3] "
            "[--write N] [--bytes N] [--reps N]\n", msg);
    exit(2);
}


static double
now(void)
{
    struct timespec  ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}


static double
tv(struct timeval t)
{
    return t.tv_sec + t.tv_usec / 1e6;
}


static void
make_cert(void)
{
    X509_NAME  *name;

    pkey = EVP_EC_gen("P-256");
    cert = X509_new();

    if (pkey == NULL || cert == NULL) {
        die("key");
    }

    X509_set_version(cert, 2);
    ASN1_INTEGER_set(X509_get_serialNumber(cert), 1);
    X509_gmtime_adj(X509_getm_notBefore(cert), 0);
    X509_gmtime_adj(X509_getm_notAfter(cert), 86400);
    X509_set_pubkey(cert, pkey);

    name = X509_get_subject_name(cert);
    X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC,
                               (const unsigned char *) "ktls-bench", -1, -1, 0);
    X509_set_issuer_name(cert, name);

    if (X509_sign(cert, pkey, EVP_sha256()) == 0) {
        die("sign");
    }
}


static SSL_CTX *
make_ctx(int server, int ktls)
{
    int      ver;
    SSL_CTX  *ctx;

    ctx = SSL_CTX_new(server ? TLS_server_method() : TLS_client_method());
    if (ctx == NULL) {
        die("SSL_CTX_new");
    }

    ver = (opt_tls == 12) ? TLS1_2_VERSION : TLS1_3_VERSION;
    SSL_CTX_set_min_proto_version(ctx, ver);
    SSL_CTX_set_max_proto_version(ctx, ver);
    SSL_CTX_set_ciphersuites(ctx, "TLS_AES_128_GCM_SHA256");
    SSL_CTX_set_cipher_list(ctx, "ECDHE-ECDSA-AES128-GCM-SHA256");
    SSL_CTX_set_options(ctx, SSL_OP_NO_TICKET);
    SSL_CTX_set_num_tickets(ctx, 0);

    if (server) {
        if (SSL_CTX_use_certificate(ctx, cert) != 1
            || SSL_CTX_use_PrivateKey(ctx, pkey) != 1)
        {
            die("certificate");
        }
    }

    if (ktls) {
        SSL_CTX_set_options(ctx, SSL_OP_ENABLE_KTLS);
    }

    return ctx;
}


/* The receiver: read everything, then report the count on the pipe. */

static void
receiver(int port, int tls, int ack)
{
    int                 s;
    char                buf[65536];
    ssize_t             n;
    uint64_t            got;
    SSL                 *ssl;
    SSL_CTX             *ctx;
    struct sockaddr_in  sa;

    s = socket(AF_INET, SOCK_STREAM, 0);

    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_port = htons(port);
    sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    if (connect(s, (struct sockaddr *) &sa, sizeof(sa)) != 0) {
        die("connect");
    }

    got = 0;
    ssl = NULL;

    if (tls) {
        ctx = make_ctx(0, 0);
        ssl = SSL_new(ctx);
        SSL_set_fd(ssl, s);

        if (SSL_connect(ssl) != 1) {
            die("SSL_connect");
        }
    }

    while (got < (uint64_t) opt_bytes) {
        n = tls ? SSL_read(ssl, buf, sizeof(buf)) : read(s, buf, sizeof(buf));
        if (n <= 0) {
            break;
        }

        got += n;
    }

    if (write(ack, &got, sizeof(got)) != sizeof(got)) {
        die("ack");
    }

    _exit(0);
}


static int
send_plain(int s, int fd)
{
    off_t    off;
    ssize_t  n;

    off = 0;

    while (off < opt_bytes) {
        n = sendfile(s, fd, &off, opt_bytes - off);
        if (n <= 0) {
            return -1;
        }
    }

    return 0;
}


static int
send_write(SSL *ssl, int fd, char *buf)
{
    off_t    off;
    size_t   len;
    ssize_t  n;

    for (off = 0; off < opt_bytes; off += len) {
        len = opt_bytes - off < (off_t) opt_write
              ? (size_t) (opt_bytes - off) : opt_write;

        n = pread(fd, buf, len, off);
        if (n != (ssize_t) len) {
            return -1;
        }

        if (SSL_write(ssl, buf, len) != (int) len) {
            return -1;
        }
    }

    return 0;
}


static int
send_sendfile(SSL *ssl, int fd)
{
    off_t         off;
    ossl_ssize_t  n;

    off = 0;

    while (off < opt_bytes) {
        n = SSL_sendfile(ssl, fd, off, opt_bytes - off, 0);
        if (n <= 0) {
            return -1;
        }

        off += n;
    }

    return 0;
}


/*
 * One run of one arm.  Returns 0 and fills *out, 1 when the kernel did not
 * take the socket (skip), -1 on error.
 */

static int
run(const char *arm, int fd, char *buf, sample_t *out, char *why, size_t wl)
{
    int                 ls, s, tls, ktls, rc, ack[2], one, port;
    pid_t               pid;
    SSL                 *ssl;
    double              t0;
    SSL_CTX             *ctx;
    uint64_t            got;
    socklen_t           sl;
    struct rusage       r0, r1;
    struct sockaddr_in  sa;

    tls = strcmp(arm, "plain") != 0;
    ktls = strncmp(arm, "ktls", 4) == 0;

    ls = socket(AF_INET, SOCK_STREAM, 0);
    one = 1;
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    if (bind(ls, (struct sockaddr *) &sa, sizeof(sa)) != 0
        || listen(ls, 1) != 0)
    {
        die("listen");
    }

    sl = sizeof(sa);
    getsockname(ls, (struct sockaddr *) &sa, &sl);
    port = ntohs(sa.sin_port);

    if (pipe(ack) != 0) {
        die("pipe");
    }

    pid = fork();
    if (pid < 0) {
        die("fork");
    }

    if (pid == 0) {
        close(ls);
        close(ack[0]);
        receiver(port, tls, ack[1]);
    }

    close(ack[1]);

    s = accept(ls, NULL, NULL);
    if (s < 0) {
        die("accept");
    }

    close(ls);

    ssl = NULL;
    ctx = NULL;

    if (tls) {
        ctx = make_ctx(1, ktls);
        ssl = SSL_new(ctx);
        SSL_set_fd(ssl, s);

        if (SSL_accept(ssl) != 1) {
            die("SSL_accept");
        }

        if (ktls && !BIO_get_ktls_send(SSL_get_wbio(ssl))) {
            snprintf(why, wl, "the kernel did not take TLS_TX "
                     "(no tls module, or OpenSSL built without kTLS)");
            rc = 1;
            goto done;
        }
    }

    getrusage(RUSAGE_SELF, &r0);
    t0 = now();

    if (!tls) {
        rc = send_plain(s, fd);

    } else if (strcmp(arm, "ktls-sendfile") == 0) {
        rc = send_sendfile(ssl, fd);

    } else {
        rc = send_write(ssl, fd, buf);
    }

    if (rc != 0) {
        snprintf(why, wl, "send failed: %s", strerror(errno));
        ERR_print_errors_fp(stderr);
        rc = -1;
        goto done;
    }

    if (read(ack[0], &got, sizeof(got)) != sizeof(got)
        || got != (uint64_t) opt_bytes)
    {
        snprintf(why, wl, "the receiver got a short count");
        rc = -1;
        goto done;
    }

    out->wall = now() - t0;
    getrusage(RUSAGE_SELF, &r1);
    out->user = tv(r1.ru_utime) - tv(r0.ru_utime);
    out->sys = tv(r1.ru_stime) - tv(r0.ru_stime);

done:

    if (ssl != NULL) {
        SSL_free(ssl);
        SSL_CTX_free(ctx);
    }

    close(s);
    close(ack[0]);
    kill(pid, SIGTERM);
    waitpid(pid, NULL, 0);

    return rc;
}


static int
cmp(const void *a, const void *b)
{
    double  x = *(const double *) a, y = *(const double *) b;

    return (x > y) - (x < y);
}


static double
median(double *v, int n)
{
    qsort(v, n, sizeof(double), cmp);
    return (n % 2) ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}


int
main(int argc, char **argv)
{
    int          i, fd, n, rc;
    char         *arms, *arm, *save, *buf, why[256];
    double       mib, cpu[MAX_REPS], thr[MAX_REPS];
    sample_t     s;
    struct stat  st;

    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        printf("%s\n", OpenSSL_version(OPENSSL_VERSION));
        return 0;
    }

    for (i = 1; i < argc; i++) {
        if (i + 1 >= argc) {
            usage("an option needs a value");
        }

        if (strcmp(argv[i], "--file") == 0) {
            opt_file = argv[++i];

        } else if (strcmp(argv[i], "--arms") == 0) {
            opt_arms = argv[++i];

        } else if (strcmp(argv[i], "--tls") == 0) {
            i++;
            opt_tls = strcmp(argv[i], "1.2") == 0 ? 12
                      : strcmp(argv[i], "1.3") == 0 ? 13 : 0;

        } else if (strcmp(argv[i], "--write") == 0) {
            opt_write = strtoul(argv[++i], NULL, 10);

        } else if (strcmp(argv[i], "--bytes") == 0) {
            opt_bytes = strtoll(argv[++i], NULL, 10);

        } else if (strcmp(argv[i], "--reps") == 0) {
            opt_reps = atoi(argv[++i]);

        } else {
            usage("unknown option");
        }
    }

    if (opt_file == NULL || opt_tls == 0 || opt_write == 0
        || opt_reps < 1 || opt_reps > MAX_REPS)
    {
        usage("bad or missing option");
    }

    fd = open(opt_file, O_RDONLY);
    if (fd < 0 || fstat(fd, &st) != 0) {
        die(opt_file);
    }

    if (opt_bytes == 0 || opt_bytes > st.st_size) {
        opt_bytes = st.st_size;
    }

    buf = malloc(opt_write);
    if (buf == NULL) {
        die("malloc");
    }

    signal(SIGPIPE, SIG_IGN);
    make_cert();

    mib = opt_bytes / 1048576.0;

    printf("kind\tarm\ttls\twrite\tMiB\twall_s\tuser_s\tsys_s"
           "\tMiB_per_s\tcpu_ms_per_MiB\n");

    arms = strdup(opt_arms);

    for (arm = strtok_r(arms, ",", &save); arm != NULL;
         arm = strtok_r(NULL, ",", &save))
    {
        if (strcmp(arm, "plain") != 0 && strcmp(arm, "ossl") != 0
            && strcmp(arm, "ktls-write") != 0
            && strcmp(arm, "ktls-sendfile") != 0)
        {
            usage("unknown arm");
        }

        n = 0;

        for (i = 0; i < opt_reps; i++) {
            rc = run(arm, fd, buf, &s, why, sizeof(why));

            if (rc == 1) {
                printf("skip\t%s\t1.%d\t-\t-\t-\t-\t-\t-\t%s\n",
                       arm, opt_tls % 10, why);
                break;
            }

            if (rc < 0) {
                fprintf(stderr, "ktls-bench: %s: %s\n", arm, why);
                return 1;
            }

            cpu[n] = (s.user + s.sys) * 1000 / mib;
            thr[n] = mib / s.wall;

            printf("rep\t%s\t1.%d\t%zu\t%.1f\t%.4f\t%.4f\t%.4f\t%.0f\t%.3f\n",
                   arm, opt_tls % 10, opt_write, mib, s.wall, s.user, s.sys,
                   thr[n], cpu[n]);
            n++;
        }

        if (n > 0) {
            printf("median\t%s\t1.%d\t%zu\t%.1f\t-\t-\t-\t%.0f\t%.3f\n",
                   arm, opt_tls % 10, opt_write, mib, median(thr, n),
                   median(cpu, n));
        }

        fflush(stdout);
    }

    return 0;
}
