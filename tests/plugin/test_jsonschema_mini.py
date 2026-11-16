"""`jsonschema_mini` must agree with the real `jsonschema` on every shipped schema.

The runner validates every lifecycle record with a hand-written Draft 2020-12 subset
(`scripts/aisdlc/jsonschema_mini.py`) because `jsonschema` cannot be vendored. A
hand-written validator fails quietly: an unimplemented keyword is a no-op. This module is
the evidence that it does not:

1. every shipped schema uses only keywords the mini validator implements; and
2. over a corpus of real records, synthetic records, and systematic mutations of both,
   the mini validator and `jsonschema` (dev-only dependency) give the same verdict.
"""

from __future__ import annotations

import copy
import json
import pathlib
import re
from collections.abc import Iterator
from typing import Any

import jsonschema
import pytest

from aisdlc.jsonschema_mini import Draft202012Validator, SchemaError, assert_supported_keywords
from aisdlc.vendor import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMAS = sorted((ROOT / ".ai-sdlc" / "schemas").glob("*.schema.json"))
WRONG_TYPES: list[Any] = [None, True, 0, 1.5, -1, "", "x", [], [None], {}, {"unexpected": 1}]


def _load(path: pathlib.Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def _real_records(schema_name: str) -> Iterator[Any]:
    """Templates and worked examples whose file name matches the schema."""
    stem = schema_name.removesuffix(".schema.json")
    for folder in (ROOT / ".ai-sdlc" / "templates", ROOT / "examples"):
        for path in folder.rglob("*"):
            if path.suffix not in (".json", ".yaml", ".yml") or not path.is_file():
                continue
            name = path.stem.removesuffix("-template")
            if name == stem or (stem == "evidence" and name == "manifest"):
                try:
                    yield _load(path)
                except (yaml.YAMLError, json.JSONDecodeError):
                    continue  # templates may hold placeholders that are not valid YAML


# Sample strings for the identifier shapes the schemas constrain with `pattern`. The
# synthesiser picks the first one a pattern accepts, so generated records can be *valid*:
# without them every string was "x", nearly every record was invalid, and the test only ever
# proved that the validator rejects things (final release review).
# Deep enough for the most nested shipped schema; still bounds a recursive `$ref`.
MAX_DEPTH = 12

SAMPLES = (
    "I001",
    "I001-D001",
    "I001-P01",
    "I001-P01-T001",
    "I001-P01-walking-skeleton",
    "F-001",
    "AC-001",
    "SC-001",
    "REQ-F-001",
    "REQ-NFR-001",
    "LESSON-001",
    "EVAL-001",
    "BASELINE-001",
    "TB-001",
    "TM-001",
    "sha256:" + "a" * 64,
    "contracts/orders.yaml#create",
    "api:orders",
    "standard",
    "src/app.py",
)


def _string(schema: dict[str, Any]) -> str:
    low, high = schema.get("minLength", 1), schema.get("maxLength", 10_000)
    pattern = schema.get("pattern")
    for sample in SAMPLES if pattern else ():
        if re.search(pattern, sample) and low <= len(sample) <= high:
            return sample
    return "x" * max(low, 1)


def _synthesise(schema: dict[str, Any], root: dict[str, Any], depth: int = 0) -> Any:
    """A best-effort *valid* instance of the schema; validity is still decided by the oracle."""
    if "$ref" in schema and depth < MAX_DEPTH:
        target: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return _synthesise(target, root, depth + 1)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        # `anyOf` may sit beside `properties` (task.schema.json): build the object from the
        # rest of the schema, then merge in what the first branch adds.
        rest = {key: value for key, value in schema.items() if key != "anyOf"}
        branch = _synthesise(schema["anyOf"][0], root, depth + 1)
        if "properties" in rest or rest.get("type") == "object":
            base = _synthesise(rest, root, depth + 1)
            return {**base, **branch} if isinstance(base, dict) and isinstance(branch, dict) else base
        return branch
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), kind[0])
    if kind == "object" or "properties" in schema:
        if depth >= MAX_DEPTH:
            return {}
        props = schema.get("properties", {})
        built = {key: _synthesise(sub, root, depth + 1) for key, sub in props.items()}
        for key in schema.get("required", []):
            built.setdefault(key, "x")
        return built
    if kind == "array":
        count = max(schema.get("minItems", 1), 1)
        item = schema.get("items", {})
        if depth >= MAX_DEPTH:
            return []
        values = [_synthesise(item, root, depth + 1) for _ in range(count)]
        if schema.get("uniqueItems") and count > 1:
            values = [f"{value}-{index}" if isinstance(value, str) else value for index, value in enumerate(values)]
        return values
    if kind == "string":
        return _string(schema)
    if kind == "integer":
        return schema.get("minimum", 1)
    if kind == "number":
        return float(schema.get("minimum", 1))
    if kind == "boolean":
        return True
    return None


def _mutations(instance: Any) -> Iterator[Any]:
    """Each top-level field removed, and each replaced by every wrong type in turn."""
    yield instance
    for wrong in WRONG_TYPES:
        yield wrong
    if not isinstance(instance, dict):
        return
    for key in instance:
        dropped = copy.deepcopy(instance)
        del dropped[key]
        yield dropped
        for wrong in WRONG_TYPES:
            changed = copy.deepcopy(instance)
            changed[key] = wrong
            yield changed
    extra = copy.deepcopy(instance)
    extra["__not_in_schema__"] = 1
    yield extra


@pytest.mark.parametrize("schema_path", SCHEMAS, ids=lambda p: p.name)
def test_schema_uses_only_supported_keywords(schema_path: pathlib.Path) -> None:
    assert_supported_keywords(json.loads(schema_path.read_text(encoding="utf-8")))


def test_unsupported_keyword_is_refused() -> None:
    with pytest.raises(SchemaError):
        Draft202012Validator({"type": "object", "patternProperties": {"^x": {"type": "string"}}})


@pytest.mark.parametrize("schema_path", SCHEMAS, ids=lambda p: p.name)
def test_mini_validator_agrees_with_jsonschema(schema_path: pathlib.Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    oracle = jsonschema.Draft202012Validator(schema)
    mini = Draft202012Validator(schema)
    seeds = [*_real_records(schema_path.name), _synthesise(schema, schema)]
    checked = accepted = 0
    for seed in seeds:
        for instance in _mutations(seed):
            expected = oracle.is_valid(instance)
            assert mini.is_valid(instance) is expected, (
                f"{schema_path.name}: mini={not expected} jsonschema={expected} for {json.dumps(instance)[:300]}"
            )
            checked += 1
            accepted += expected
    assert checked >= len(WRONG_TYPES) + 1
    # Both verdicts must be exercised, or agreement proves only half the validator.
    assert accepted >= 3, f"{schema_path.name}: only {accepted} of {checked} instances were valid"


@pytest.mark.parametrize(
    ("schema", "instance", "valid"),
    [
        ({"const": 1}, True, False),
        ({"const": 0}, False, False),
        ({"const": True}, 1, False),
        ({"enum": [1, 2]}, True, False),
        ({"enum": [0]}, False, False),
        ({"const": [1]}, [True], False),
        ({"const": {"a": 1}}, {"a": True}, False),
        ({"const": 1}, 1.0, True),  # JSON has one number type: 1 and 1.0 are equal
        ({"enum": [True]}, True, True),
    ],
)
def test_const_and_enum_use_json_equality(schema: dict[str, Any], instance: Any, valid: bool) -> None:
    """Found by the differential test: Python's `True == 1` let `{"version": true}` pass `const: 1`."""
    assert jsonschema.Draft202012Validator(schema).is_valid(instance) is valid
    assert Draft202012Validator(schema).is_valid(instance) is valid
