"""The Draft 2020-12 subset this framework's schemas actually use.

`jsonschema` cannot be vendored. Since 4.18 it depends on `rpds-py`, a compiled Rust
extension, and this plugin must run under any stock `python3` with no build step. So the
validator is ours rather than borrowed — which is only defensible because the surface is
small and fixed.

Counted across the schemas in `.ai-sdlc/schemas/`, every keyword in use is
implemented here:

    type (284)  items (45)  minLength (38)  properties (36)  pattern (36)
    minimum (34)  required (28)  enum (27)  additionalProperties (25)
    minItems (11)  maximum (7)  $ref (4)  const (2)  $defs (1)  uniqueItems (1)
    anyOf (1)

`format` appears 13 times and is deliberately **not** enforced, because `jsonschema` does
not enforce it either unless a format checker is passed — and none ever was. Treating it
as a constraint now would fail records that have always been valid.

Anything outside that list is a *silent* no-op in a hand-written validator, which is how a
schema quietly stops being enforced. `assert_supported_keywords` exists to make that loud
instead. `tests/plugin/test_jsonschema_mini.py` runs it over every shipped schema and
differential-tests this validator against the real `jsonschema` (a dev-only dependency).

The public surface mirrors the slice of `jsonschema` the runner used, so call sites did
not change:

    Draft202012Validator(schema).iter_errors(instance)  -> yields errors with
        .message         human-readable, names the offending field
        .path            deque of path segments, relative to the instance
        .absolute_path   same, for compatibility with the previous call sites
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterator
from typing import Any

__all__ = ["Draft202012Validator", "SchemaError", "ValidationError", "assert_supported_keywords"]

# Keywords this validator understands. Anything else in a schema is an error at
# construction time rather than a constraint that silently never runs.
SUPPORTED = frozenset(
    {
        # structural
        "$schema",
        "$id",
        "$defs",
        "definitions",
        "$ref",
        "title",
        "description",
        "default",
        "examples",
        "$comment",
        # applicators
        "properties",
        "items",
        "anyOf",
        # assertions
        "type",
        "required",
        "enum",
        "const",
        "pattern",
        "additionalProperties",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        # annotation only — see the module docstring
        "format",
    }
)

# JSON type name -> predicate. `bool` is checked before the numeric types on purpose:
# in Python `True` is an instance of `int`, and in JSON Schema it is emphatically not an
# integer. Getting this backwards makes `{"type": "integer"}` accept `true`.
_TYPES: dict[str, Any] = {
    "null": lambda value: value is None,
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "string": lambda value: isinstance(value, str),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}


class SchemaError(ValueError):
    """The schema itself is malformed, or uses a keyword this validator does not implement."""


class ValidationError:
    """One constraint violation, shaped like the `jsonschema` error the runner consumed."""

    __slots__ = ("message", "path")

    def __init__(self, message: str, path: deque[str | int]) -> None:
        self.message = message
        self.path = path

    @property
    def absolute_path(self) -> deque[str | int]:
        return self.path

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        location = ".".join(str(part) for part in self.path) or "root"
        return f"<ValidationError {location}: {self.message}>"


def assert_supported_keywords(schema: Any, _where: str = "root") -> None:
    """Refuse a schema using a keyword this validator would otherwise ignore.

    An unimplemented keyword in a hand-written validator does not fail — it disappears,
    and the schema goes on looking like it is enforced. Raising here converts that into a
    loud failure at the moment a schema is written.
    """
    if isinstance(schema, list):
        for index, item in enumerate(schema):
            assert_supported_keywords(item, f"{_where}[{index}]")
        return
    if not isinstance(schema, dict):
        return
    for key, value in schema.items():
        if key in ("properties", "$defs", "definitions"):
            if not isinstance(value, dict):
                raise SchemaError(f"{_where}.{key} must be an object")
            for name, subschema in value.items():
                assert_supported_keywords(subschema, f"{_where}.{key}.{name}")
            continue
        if key not in SUPPORTED:
            raise SchemaError(f"unsupported schema keyword at {_where}: {key!r}")
        if key in ("items", "additionalProperties", "not"):
            assert_supported_keywords(value, f"{_where}.{key}")
        elif key == "anyOf":
            assert_supported_keywords(value, f"{_where}.anyOf")


class Draft202012Validator:
    """Validate an instance against the supported Draft 2020-12 subset."""

    def __init__(self, schema: dict[str, Any]) -> None:
        if not isinstance(schema, dict):
            raise SchemaError("a schema must be an object")
        assert_supported_keywords(schema)
        self.schema = schema

    def iter_errors(self, instance: Any) -> Iterator[ValidationError]:
        yield from self._validate(instance, self.schema, deque())

    def is_valid(self, instance: Any) -> bool:
        return next(self.iter_errors(instance), None) is None

    # -- internals ---------------------------------------------------------------

    def _resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Follow a local `$ref`. Only same-document pointers are supported.

        Every `$ref` in this framework's schemas is a `#/$defs/...` pointer into the same
        file. A remote or cross-file reference would need a resolver, a fetch policy, and
        a cache — none of which a plugin should be doing — so it is refused outright
        rather than half-implemented.
        """
        reference = schema.get("$ref")
        if reference is None:
            return schema
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise SchemaError(f"only local '#/...' references are supported, got {reference!r}")
        target: Any = self.schema
        for raw in reference[2:].split("/"):
            segment = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or segment not in target:
                raise SchemaError(f"unresolvable reference: {reference}")
            target = target[segment]
        if not isinstance(target, dict):
            raise SchemaError(f"reference does not point at a schema: {reference}")
        # A sibling keyword next to `$ref` is legal in 2020-12; merge so it still applies.
        merged = {key: value for key, value in schema.items() if key != "$ref"}
        return {**target, **merged} if merged else target

    def _validate(self, instance: Any, schema: dict[str, Any], path: deque) -> Iterator[ValidationError]:
        schema = self._resolve(schema)

        expected = schema.get("type")
        if expected is not None:
            names = expected if isinstance(expected, list) else [expected]
            for name in names:
                if name not in _TYPES:
                    raise SchemaError(f"unknown type {name!r}")
            if not any(_TYPES[name](instance) for name in names):
                yield ValidationError(f"{_describe(instance)} is not of type {' or '.join(names)}", deque(path))
                # Every remaining keyword assumes the type held. Reporting them too would
                # bury the one error that matters under a cascade of consequences.
                return

        if "const" in schema and not _json_equal(instance, schema["const"]):
            yield ValidationError(f"{_describe(instance)} was expected to be {schema['const']!r}", deque(path))

        if "enum" in schema and not any(_json_equal(instance, option) for option in schema["enum"]):
            allowed = ", ".join(repr(option) for option in schema["enum"])
            yield ValidationError(f"{_describe(instance)} is not one of [{allowed}]", deque(path))

        if "anyOf" in schema and not any(self._matches(instance, option) for option in schema["anyOf"]):
            yield ValidationError("value does not satisfy any of the permitted shapes", deque(path))

        if isinstance(instance, str):
            yield from self._string(instance, schema, path)
        elif isinstance(instance, bool):
            pass  # a bool is not a number here, so no numeric bound applies
        elif isinstance(instance, (int, float)):
            yield from self._number(instance, schema, path)

        if isinstance(instance, list):
            yield from self._array(instance, schema, path)
        elif isinstance(instance, dict):
            yield from self._object(instance, schema, path)

    def _matches(self, instance: Any, schema: dict[str, Any]) -> bool:
        return next(self._validate(instance, schema, deque()), None) is None

    def _string(self, instance: str, schema: dict[str, Any], path: deque) -> Iterator[ValidationError]:
        minimum = schema.get("minLength")
        if minimum is not None and len(instance) < minimum:
            yield ValidationError(f"{instance!r} is too short (minimum {minimum} characters)", deque(path))
        maximum = schema.get("maxLength")
        if maximum is not None and len(instance) > maximum:
            yield ValidationError(f"{instance!r} is too long (maximum {maximum} characters)", deque(path))
        pattern = schema.get("pattern")
        # `search`, not `match`: JSON Schema patterns are unanchored unless the schema
        # anchors them itself, and these schemas do (`^...$`).
        if pattern is not None and re.search(pattern, instance) is None:
            yield ValidationError(f"{instance!r} does not match {pattern!r}", deque(path))

    def _number(self, instance: float, schema: dict[str, Any], path: deque) -> Iterator[ValidationError]:
        minimum = schema.get("minimum")
        if minimum is not None and instance < minimum:
            yield ValidationError(f"{instance} is less than the minimum of {minimum}", deque(path))
        maximum = schema.get("maximum")
        if maximum is not None and instance > maximum:
            yield ValidationError(f"{instance} is greater than the maximum of {maximum}", deque(path))
        exclusive_minimum = schema.get("exclusiveMinimum")
        if exclusive_minimum is not None and instance <= exclusive_minimum:
            yield ValidationError(f"{instance} is not greater than {exclusive_minimum}", deque(path))
        exclusive_maximum = schema.get("exclusiveMaximum")
        if exclusive_maximum is not None and instance >= exclusive_maximum:
            yield ValidationError(f"{instance} is not less than {exclusive_maximum}", deque(path))

    def _array(self, instance: list[Any], schema: dict[str, Any], path: deque) -> Iterator[ValidationError]:
        minimum = schema.get("minItems")
        if minimum is not None and len(instance) < minimum:
            yield ValidationError(f"array is too short (minimum {minimum} items)", deque(path))
        maximum = schema.get("maxItems")
        if maximum is not None and len(instance) > maximum:
            yield ValidationError(f"array is too long (maximum {maximum} items)", deque(path))
        if schema.get("uniqueItems") is True:
            seen: list[Any] = []
            for item in instance:
                if item in seen:
                    yield ValidationError(f"array has non-unique elements ({_describe(item)})", deque(path))
                    break
                seen.append(item)
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(instance):
                child = deque(path)
                child.append(index)
                yield from self._validate(item, items, child)

    def _object(self, instance: dict[str, Any], schema: dict[str, Any], path: deque) -> Iterator[ValidationError]:
        # `required` reports at the *parent* path and names the missing property, which is
        # what `jsonschema` did and what the runner's `label:location:` prefix assumes.
        for name in schema.get("required", []):
            if name not in instance:
                yield ValidationError(f"{name!r} is a required property", deque(path))

        properties = schema.get("properties", {})
        for name, subschema in properties.items():
            if name in instance:
                child = deque(path)
                child.append(name)
                yield from self._validate(instance[name], subschema, child)

        additional = schema.get("additionalProperties")
        if additional is False:
            unexpected = sorted(set(instance) - set(properties))
            if unexpected:
                names = ", ".join(repr(name) for name in unexpected)
                yield ValidationError(f"additional properties are not allowed ({names})", deque(path))
        elif isinstance(additional, dict):
            for name in sorted(set(instance) - set(properties)):
                child = deque(path)
                child.append(name)
                yield from self._validate(instance[name], additional, child)


def _json_equal(left: Any, right: Any) -> bool:
    """Equality as JSON Schema defines it, not as Python does.

    Python says `True == 1` and `False == 0`; JSON Schema says a boolean never equals a
    number, so `{"const": 1}` must refuse `true`. Numbers compare by value (`1 == 1.0`),
    and arrays and objects compare element by element under the same rule.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    return type(left) is type(right) and left == right


def _describe(value: Any) -> str:
    """Short, stable rendering of a value for an error message."""
    if isinstance(value, str) and len(value) > 60:
        return repr(value[:57] + "...")
    if isinstance(value, (dict, list)):
        return f"a {'object' if isinstance(value, dict) else 'array'} of {len(value)} item(s)"
    return repr(value)
