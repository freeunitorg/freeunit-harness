#!/usr/bin/env python3
"""openapi-example-check.py — validate every committed example in an OpenAPI
spec against the schema it is attached to.

Usage:
  openapi-example-check.py [SPEC] [--repo DIR] [--required-hints] [--strict-hints]

SPEC defaults to docs/unit-openapi.yaml, resolved relative to --repo (which
itself defaults to "."). Pass an absolute path to SPEC to ignore --repo
entirely.

Exits 0 if every example validates, 1 and prints one FAIL line per example
that does not. Run it against the base commit too and compare the failing
labels (not just the count): the number that matters is "sites failing
after" minus "sites failing before", which must be zero. Run the whole
comparison under LC_ALL=C -- GNU `comm` pairs lines by collating sequence, so
a C-sorted input fed to a comm running in a UTF-8 locale mis-pairs and invents
differences. tools/new-failures.sh does this for any checker.

--required-hints is a second, optional lint: it scans every object schema
(components/schemas/* and inline object schemas found anywhere in the spec)
for a property whose description implies it is always present ("always
present", "always emitted", "always included", "is always", "every
response", "in all responses" -- case-insensitive) while that property is
missing from the schema's `required` list. Each hit prints one line:
    HINT <schema>.<property>: description says always present but not in required
Hints are informational only -- they do not affect the exit code -- unless
--strict-hints is also passed, in which case any hint makes the run exit 1
(passing --strict-hints alone implies --required-hints).

Why it exists: freeunit's spec shipped examples that violated their own
schemas -- errorInvalidJson showed a `location` object under a schema
declaring string-only members. Nothing in the build catches that; the
generator does not validate examples. On the 1.36.1 spec (pre-#238) this
found 61 failing sites; #238 fixed all but 7 pre-existing ones under
/config.

Deps: PyYAML, jsonschema (and referencing, its own dependency, used
for $ref resolution). None ship with the stdlib. Check first:
    python3 -c 'import yaml, jsonschema, referencing'
If that fails:
    uv pip install pyyaml jsonschema
"""
import argparse
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

REQUIRED_HINT_PHRASES = re.compile(
    r"always present|always emitted|always included|is always|"
    r"every response|in all responses",
    re.IGNORECASE,
)

# Base URI of the synthetic document that carries components/ for $ref
# resolution. The spec's refs are all fragment-only pointers into it.
BASE_URI = "urn:freeunit:openapi:example-check"


def schema_label(breadcrumb):
    """human-readable name for a schema found at this breadcrumb path"""
    if len(breadcrumb) == 3 and breadcrumb[0] == "components" and breadcrumb[1] == "schemas":
        return str(breadcrumb[2])
    return "/".join(str(b) for b in breadcrumb)


def find_required_hints(node, breadcrumb=()):
    """recursively find object schemas (components/schemas/* and inline)
    whose property description implies always-present while the property
    is absent from `required`; yields report lines"""
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            required = set(node.get("required") or [])
            for prop, propschema in properties.items():
                if not isinstance(propschema, dict):
                    continue
                desc = propschema.get("description") or ""
                if prop not in required and REQUIRED_HINT_PHRASES.search(desc):
                    yield (
                        f"HINT {schema_label(breadcrumb)}.{prop}: description "
                        "says always present but not in required"
                    )
        for key, val in node.items():
            yield from find_required_hints(val, breadcrumb + (key,))
    elif isinstance(node, list):
        for i, val in enumerate(node):
            yield from find_required_hints(val, breadcrumb + (i,))


def examples_for(content):
    """yield (schema, example_value, label) for one content-type block"""
    for ctype, block in (content or {}).items():
        schema = block.get("schema")
        if not schema:
            continue
        for name, ex in (block.get("examples") or {}).items():
            val = ex
            if isinstance(ex, dict) and "$ref" in ex:
                key = ex["$ref"].rsplit("/", 1)[-1]
                val = spec["components"]["examples"].get(key, {})
            if isinstance(val, dict) and "value" in val:
                yield schema, val["value"], f"{ctype}:{name}"


def main():
    ap = argparse.ArgumentParser(
        description="Validate every committed OpenAPI example against its schema.",
    )
    ap.add_argument(
        "spec_path",
        nargs="?",
        default="docs/unit-openapi.yaml",
        help="path to the OpenAPI spec (default: docs/unit-openapi.yaml, "
        "resolved relative to --repo)",
    )
    ap.add_argument(
        "--repo",
        default=".",
        help='repo root the default spec path is relative to (default: ".")',
    )
    ap.add_argument(
        "--required-hints",
        action="store_true",
        help="also flag object schema properties whose description implies "
        "always-present ('always present', 'always emitted', 'always "
        "included', 'is always', 'every response', 'in all responses') "
        "while the property is missing from `required`; prints HINT lines "
        "and does not affect the exit code unless --strict-hints is set",
    )
    ap.add_argument(
        "--strict-hints",
        action="store_true",
        help="like --required-hints (implies it), but exit 1 if any hint is found",
    )
    args = ap.parse_args()

    spec_path = Path(args.spec_path)
    if not spec_path.is_absolute():
        spec_path = Path(args.repo) / spec_path

    global spec
    spec = yaml.safe_load(spec_path.read_text())
    components = spec["components"]

    checked = 0
    failed = 0

    def check(schema, value, where):
        nonlocal checked, failed
        checked += 1
        # Every $ref in the spec is a fragment-only pointer into
        # components/ ("#/components/..."). A registry-backed validator
        # roots resolution at the schema it validates, so wrap the schema
        # in a document that also carries components/ and validate the
        # document's $ref to it.
        document = {
            "$id": BASE_URI,
            "components": components,
            "target": schema,
        }
        registry = Registry().with_resource(
            BASE_URI,
            Resource.from_contents(document, default_specification=DRAFT7),
        )
        v = Draft7Validator({"$ref": f"{BASE_URI}#/target"}, registry=registry)
        errs = sorted(v.iter_errors(value), key=lambda e: e.path)
        for e in errs:
            failed += 1
            print(f"FAIL {where}: {list(e.path)}: {e.message}")

    for path, item in spec["paths"].items():
        for method, op in item.items():
            if not isinstance(op, dict) or "responses" not in op:
                continue
            for code, resp in op["responses"].items():
                if "$ref" in resp:
                    resp = spec["components"]["responses"][resp["$ref"].rsplit("/", 1)[-1]]
                for schema, val, label in examples_for(resp.get("content")):
                    check(schema, val, f"{path} {method} {code} {label}")

    for name, resp in spec["components"].get("responses", {}).items():
        for schema, val, label in examples_for(resp.get("content")):
            check(schema, val, f"components/responses/{name} {label}")

    print(f"\nexamples checked: {checked}, failures: {failed}")

    hints = 0
    if args.required_hints or args.strict_hints:
        for line in find_required_hints(spec):
            hints += 1
            print(line)
        print(f"required-hints: {hints}")

    if failed or (args.strict_hints and hints):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
