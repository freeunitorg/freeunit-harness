/* A minimal libunit application: a small fixed response, no test hooks. */
#include <string.h>
#include <nxt_unit.h>
#include <nxt_unit_request.h>

#define CONTENT_TYPE  "Content-Type"
#define TEXT_PLAIN    "text/plain"
#define HELLO_WORLD   "Hello, World\n"

static void
request_handler(nxt_unit_request_info_t *req)
{
    int  rc;

    rc = nxt_unit_response_init(req, 200, 1, sizeof(CONTENT_TYPE) - 1
                                + sizeof(TEXT_PLAIN) - 1
                                + sizeof(HELLO_WORLD) - 1);
    if (rc == NXT_UNIT_OK) {
        rc = nxt_unit_response_add_field(req, CONTENT_TYPE,
                                         sizeof(CONTENT_TYPE) - 1, TEXT_PLAIN,
                                         sizeof(TEXT_PLAIN) - 1);
    }

    if (rc == NXT_UNIT_OK) {
        rc = nxt_unit_response_add_content(req, HELLO_WORLD,
                                           sizeof(HELLO_WORLD) - 1);
    }

    if (rc == NXT_UNIT_OK) {
        rc = nxt_unit_response_send(req);
    }

    nxt_unit_request_done(req, rc);
}

int
main(void)
{
    nxt_unit_ctx_t   *ctx;
    nxt_unit_init_t  init;

    memset(&init, 0, sizeof(init));
    init.callbacks.request_handler = request_handler;

    ctx = nxt_unit_init(&init);
    if (ctx == NULL) {
        return 1;
    }

    nxt_unit_run(ctx);
    nxt_unit_done(ctx);

    return 0;
}
