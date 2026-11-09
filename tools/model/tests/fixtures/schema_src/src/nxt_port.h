/*
 * Copyright (C) Igor Sysoev
 * Copyright (C) NGINX, Inc.
 *
 * cut from FreeUnit 1.37.2 src/nxt_port.h, layout only
 */

#ifndef _NXT_PORT_H_INCLUDED_
#define _NXT_PORT_H_INCLUDED_

#include <inttypes.h>

typedef struct {
    uint32_t             stream;
    int32_t              pid;
    uint16_t             reply_port;
    uint8_t              type;
    uint8_t              last;
    uint8_t              mmap;
    uint8_t              nf;
    uint8_t              mf;
} nxt_port_msg_t;

#endif
