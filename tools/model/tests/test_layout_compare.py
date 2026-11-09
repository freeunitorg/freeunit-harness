"""Layout comparison of ctypes mirrors with extracted structs."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.model.c_types import (
    CNxtUnitRequest,
    CNxtUnitResponse,
    compare_ctypes_to_schema,
    ctypes,
)

FIXTURE_SRC = Path(__file__).resolve().parent / "fixtures" / "schema_src"


def _tree_sitter_available():
    return (
        importlib.util.find_spec("tree_sitter") is not None
        and importlib.util.find_spec("tree_sitter_c") is not None
    )


def _field(name, offset, array_len=1, size=None, is_flexible=False, unresolved_array=None):
    return SimpleNamespace(
        name=name,
        offset=offset,
        array_len=array_len,
        size=size,
        is_flexible=is_flexible,
        unresolved_array=unresolved_array,
    )


def _struct(size, fields):
    return SimpleNamespace(size=size, fields=fields)


def _header_name(ctypes_name):
    if ctypes_name.endswith("_offset"):
        return ctypes_name[: -len("_offset")]
    return ctypes_name


def _header_fields(typ, trailing_flexible=None):
    fields = [
        _field(_header_name(name), getattr(typ, name).offset)
        for name, *_ in typ._fields_
        if not name.startswith("_")
    ]
    if trailing_flexible:
        # Offset of a trailing flexible array equals the ctypes size.
        fields.append(
            _field(
                trailing_flexible,
                ctypes.sizeof(typ),
                array_len=0,
                size=0,
                is_flexible=True,
            )
        )
    return fields


def _port_msg_fields(extra=None):
    fields = [
        _field("stream", 0),
        _field("pid", 4),
        _field("reply_port", 8),
        _field("type", 10),
        _field("last", 11),
        _field("mmap", 12),
        _field("nf", 13),
        _field("mf", 14),
    ]
    if extra:
        fields.extend(extra)
    return fields


def _matching_schema():
    # Header names, not the ctypes X_offset aliases. fields[] is present
    # on the request and response, as in the C headers.
    return {
        "nxt_port_msg_t": _struct(16, _port_msg_fields()),
        "nxt_port_mmap_msg_s": _struct(
            12,
            [_field("mmap_id", 0), _field("chunk_id", 4), _field("size", 8)],
        ),
        "nxt_app_queue_item_t": _struct(
            36,
            [_field("size", 0), _field("data", 1), _field("tracking", 32)],
        ),
        "nxt_unit_field_s": _struct(
            16,
            [
                _field("hash", 0),
                _field("skip", 2),
                _field("hopbyhop", 2),
                _field("name_length", 3),
                _field("value_length", 4),
                _field("name", 8),
                _field("value", 12),
            ],
        ),
        "nxt_unit_request_s": _struct(
            ctypes.sizeof(CNxtUnitRequest),
            _header_fields(CNxtUnitRequest, trailing_flexible="fields"),
        ),
        "nxt_unit_response_s": _struct(
            ctypes.sizeof(CNxtUnitResponse),
            _header_fields(CNxtUnitResponse, trailing_flexible="fields"),
        ),
    }


def test_compare_matches_current_mirrors():
    assert compare_ctypes_to_schema(_matching_schema()) == []


def test_compare_fails_when_header_gains_a_field():
    schema = _matching_schema()
    # A header with an extra field, as in the review's mutated nxt_port_msg_t.
    schema["nxt_port_msg_t"] = _struct(
        24,
        _port_msg_fields(extra=[_field("mutation_pad", 16)]),
    )
    diffs = compare_ctypes_to_schema(schema)
    assert diffs
    assert any("nxt_port_msg_t" in d and "mutation_pad" in d for d in diffs)
    assert any("size" in d for d in diffs)


def test_compare_fails_when_field_is_retyped_without_moving():
    """uint16_t status to uint32_t status keeps offset and struct size, but not field size."""
    schema = _matching_schema()
    fields = []
    for f in schema["nxt_unit_response_s"].fields:
        if f.name == "status":
            fields.append(_field("status", f.offset, size=4))
        else:
            fields.append(f)
    schema["nxt_unit_response_s"] = _struct(ctypes.sizeof(CNxtUnitResponse), fields)
    diffs = compare_ctypes_to_schema(schema)
    assert diffs
    assert any("status" in d and "size" in d for d in diffs)


def test_compare_fails_on_unresolved_trailing_array_dimension():
    """fields[NXT_SOME_MACRO] is not a flexible array and must not be skipped."""
    schema = _matching_schema()
    fields = [
        f for f in schema["nxt_unit_response_s"].fields if f.name != "fields"
    ]
    fields.append(
        _field(
            "fields",
            ctypes.sizeof(CNxtUnitResponse),
            array_len=0,
            size=0,
            is_flexible=False,
            unresolved_array="NXT_SOME_MACRO",
        )
    )
    schema["nxt_unit_response_s"] = _struct(ctypes.sizeof(CNxtUnitResponse), fields)
    diffs = compare_ctypes_to_schema(schema)
    assert diffs
    assert any("unresolved array dimension NXT_SOME_MACRO" in d for d in diffs)


@pytest.mark.skipif(
    not _tree_sitter_available(),
    reason="tree-sitter and tree-sitter-c are optional",
)
def test_schema_extractor_on_checked_in_headers():
    from tools.model.ast_extractor import SchemaExtractor

    extractor = SchemaExtractor(FIXTURE_SRC)
    extractor.load_all()
    diffs = compare_ctypes_to_schema(extractor.structs)
    assert diffs == [], "\n".join(diffs)

    # A mutated header must still fail.
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "schema_src"
        shutil.copytree(FIXTURE_SRC, dest)
        port_h = dest / "src" / "nxt_port.h"
        port_h.write_text(
            port_h.read_text(encoding="utf-8").replace(
                "    uint8_t              mf;\n} nxt_port_msg_t;",
                "    uint8_t              mf;\n"
                "    uint64_t             mutation_pad;\n} nxt_port_msg_t;",
            )
        )
        mutated = SchemaExtractor(dest)
        mutated.load_all()
        mutated_diffs = compare_ctypes_to_schema(mutated.structs)
        assert mutated_diffs
        assert any("nxt_port_msg_t" in d for d in mutated_diffs)

    resp_h = FIXTURE_SRC / "src" / "nxt_unit_response.h"
    original_resp = resp_h.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "schema_src"
        shutil.copytree(FIXTURE_SRC, dest)
        dest.joinpath("src", "nxt_unit_response.h").write_text(
            original_resp.replace("fields[];", "fields[NXT_SOME_MACRO];")
        )
        mutated = SchemaExtractor(dest)
        mutated.load_all()
        mutated_diffs = compare_ctypes_to_schema(mutated.structs)
        assert mutated_diffs, "fields[NXT_SOME_MACRO] must fail the compare"
        assert any("unresolved array dimension NXT_SOME_MACRO" in d for d in mutated_diffs)

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "schema_src"
        shutil.copytree(FIXTURE_SRC, dest)
        dest.joinpath("src", "nxt_unit_response.h").write_text(
            original_resp.replace(
                "uint16_t              status;",
                "uint32_t              status;",
            )
        )
        mutated = SchemaExtractor(dest)
        mutated.load_all()
        mutated_diffs = compare_ctypes_to_schema(mutated.structs)
        assert mutated_diffs, "uint32_t status must fail the compare"
        assert any("status" in d and "size" in d for d in mutated_diffs)
