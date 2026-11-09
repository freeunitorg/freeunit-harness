/*
 * Copyright (C) Max Romanov
 * Copyright (C) NGINX, Inc.
 *
 * cut from FreeUnit 1.37.2 src/nxt_port_memory_int.h, layout only
 */

#ifndef _NXT_PORT_MEMORY_INT_H_INCLUDED_
#define _NXT_PORT_MEMORY_INT_H_INCLUDED_

#include <inttypes.h>

typedef struct nxt_port_mmap_msg_s nxt_port_mmap_msg_t;

struct nxt_port_mmap_msg_s {
    uint32_t            mmap_id;
    uint32_t            chunk_id;
    uint32_t            size;
};

#endif
