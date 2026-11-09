/*
 * Copyright (C) NGINX, Inc.
 *
 * cut from FreeUnit 1.37.2 src/nxt_unit_sptr.h, layout only
 */

#ifndef _NXT_UNIT_SPTR_H_INCLUDED_
#define _NXT_UNIT_SPTR_H_INCLUDED_

#include <inttypes.h>
#include "nxt_unit_typedefs.h"

union nxt_unit_sptr_u {
    uint8_t   base[1];
    uint32_t  offset;
};

#endif
