/*
 * Copyright (C) NGINX, Inc.
 *
 * cut from FreeUnit 1.37.2 src/nxt_unit_field.h, layout only
 */

#ifndef _NXT_UNIT_FIELD_H_INCLUDED_
#define _NXT_UNIT_FIELD_H_INCLUDED_

#include <inttypes.h>
#include "nxt_unit_sptr.h"

struct nxt_unit_field_s {
    uint16_t              hash;
    uint8_t               skip:1;
    uint8_t               hopbyhop:1;
    uint8_t               name_length;
    uint32_t              value_length;

    nxt_unit_sptr_t       name;
    nxt_unit_sptr_t       value;
};

#endif
