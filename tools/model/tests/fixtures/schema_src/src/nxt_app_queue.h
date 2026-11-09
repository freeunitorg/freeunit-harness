/*
 * Copyright (C) NGINX, Inc.
 *
 * cut from FreeUnit 1.37.2 src/nxt_app_queue.h, layout only
 */

#ifndef _NXT_APP_QUEUE_H_INCLUDED_
#define _NXT_APP_QUEUE_H_INCLUDED_

#include <inttypes.h>

#define NXT_APP_QUEUE_MSG_SIZE  31

typedef struct {
    uint8_t   size;
    uint8_t   data[NXT_APP_QUEUE_MSG_SIZE];
    uint32_t  tracking;
} nxt_app_queue_item_t;

#endif
