"""Interface contracts as part of the specification (Wave S2.1).

An enterprise service is specified as much by its interfaces as by its prose. A contract
file under `02-specification/contracts/` is frozen at Gate 2 like every other
specification artifact, but a frozen contract nobody tests is decoration. This module
answers one deterministic question: is every operation the contract declares covered by an
acceptance criterion, or excluded with a written reason?

Formats are read structurally with the vendored YAML parser — OpenAPI 3.x, AsyncAPI 2.x
and 3.x, and JSON Schema — so the runner stays dependency-free. Validating a payload
against a schema is the job of the criterion's command (a Schemathesis or Dredd run, a
consumer contract test), not of the runner.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .vendor import yaml

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
CONTRACT_KINDS = ("openapi", "asyncapi", "jsonschema")


class ContractError(ValueError):
    """Raised when a declared contract cannot be read."""


@dataclass(frozen=True)
class Operation:
    """One addressable unit of a contract: `<path>#<operation>`."""

    contract: str
    name: str
    fragment: Any
    # The whole contract, so local `$ref`s in the fragment can be resolved when hashing.
    root: Any = field(default=None, compare=False, repr=False)

    @property
    def reference(self) -> str:
        return f"{self.contract}#{self.name}"


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ContractError(f"unreadable contract {path.name}: {error}") from error


def detect_kind(document: Any) -> str | None:
    if not isinstance(document, dict):
        return None
    if str(document.get("openapi", "")).startswith("3"):
        return "openapi"
    if str(document.get("asyncapi", "")).split(".")[0] in {"2", "3"}:
        return "asyncapi"
    if "$schema" in document or document.get("type") == "object":
        return "jsonschema"
    return None


def _openapi_operations(contract: str, document: dict[str, Any]) -> list[Operation]:
    operations: list[Operation] = []
    for route, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, dict):
                # operationId is the stable name; fall back to the route so an unnamed
                # operation is still addressable rather than silently uncovered.
                name = operation.get("operationId") or f"{method.upper()} {route}"
                operations.append(Operation(contract, str(name), operation, document))
    return operations


def _asyncapi_operations(contract: str, document: dict[str, Any]) -> list[Operation]:
    operations: list[Operation] = []
    if str(document.get("asyncapi", "")).startswith("3"):
        for name, operation in (document.get("operations") or {}).items():
            operations.append(Operation(contract, str(name), operation, document))
        return operations
    for channel, item in (document.get("channels") or {}).items():
        if not isinstance(item, dict):
            continue
        for action in ("publish", "subscribe"):
            operation = item.get(action)
            if isinstance(operation, dict):
                name = operation.get("operationId") or f"{action} {channel}"
                operations.append(Operation(contract, str(name), operation, document))
    return operations


def contract_operations(spec_root: Path, declaration: dict[str, Any]) -> list[Operation]:
    """Every operation one declared contract exposes."""
    relative = str(declaration.get("path", ""))
    path = spec_root / relative
    if not path.is_file():
        raise ContractError(f"declared contract {relative} does not exist")
    document = _load(path)
    kind = declaration.get("kind") or detect_kind(document)
    if kind == "openapi":
        return _openapi_operations(relative, document)
    if kind == "asyncapi":
        return _asyncapi_operations(relative, document)
    if kind == "jsonschema":
        # A data contract has one unit: the schema itself.
        title = document.get("title") if isinstance(document, dict) else None
        return [Operation(relative, str(title or "schema"), document, document)]
    raise ContractError(f"{relative} is not a recognised OpenAPI 3, AsyncAPI 2/3, or JSON Schema document")


def all_operations(spec_root: Path, document: dict[str, Any]) -> tuple[dict[str, Operation], list[str]]:
    """Resolve every declared contract. Returns operations by reference, and read errors."""
    operations: dict[str, Operation] = {}
    errors: list[str] = []
    for declaration in document.get("contracts", []) or []:
        if not isinstance(declaration, dict):
            continue
        try:
            for operation in contract_operations(spec_root, declaration):
                if operation.reference in operations:
                    errors.append(f"contract operation {operation.reference} is declared twice")
                operations[operation.reference] = operation
        except ContractError as error:
            errors.append(str(error))
    return operations, errors


def validate_contracts(spec_root: Path, document: dict[str, Any]) -> list[str]:
    """Contract references resolve, and every operation is covered or excluded with a reason."""
    operations, errors = all_operations(spec_root, document)
    referenced: set[str] = set()
    for criterion in document.get("acceptance_criteria", []) or []:
        if not isinstance(criterion, dict):
            continue
        for reference in criterion.get("contract_operations", []) or []:
            referenced.add(reference)
            if reference not in operations:
                errors.append(f"{criterion.get('id')} references unknown contract operation {reference}")
    excluded: set[str] = set()
    for declaration in document.get("contracts", []) or []:
        if not isinstance(declaration, dict):
            continue
        for exclusion in declaration.get("excluded_operations", []) or []:
            reference = f"{declaration.get('path')}#{exclusion.get('operation')}"
            if reference not in operations:
                errors.append(f"excluded operation {reference} is not in the contract")
            elif reference in referenced:
                errors.append(f"{reference} is both excluded and covered by a criterion; pick one")
            excluded.add(reference)
    for reference in sorted(set(operations) - referenced - excluded):
        errors.append(
            f"contract operation {reference} is covered by no acceptance criterion; "
            "cover it, or exclude it with a reason"
        )
    return errors


def resolve(fragment: Any, root: Any, seen: frozenset[str] = frozenset()) -> Any:
    """Inline local `#/...` references, so a change to a shared component moves the hash."""
    if isinstance(fragment, list):
        return [resolve(item, root, seen) for item in fragment]
    if not isinstance(fragment, dict):
        return fragment
    reference = fragment.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        if reference in seen:
            return {"$ref": reference}
        target: Any = root
        for part in reference[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            target = target.get(part) if isinstance(target, dict) else None
        if target is None:
            return fragment
        return resolve(target, root, seen | {reference})
    return {key: resolve(value, root, seen) for key, value in fragment.items()}


def operation_hashes(spec_root: Path, document: dict[str, Any], references: list[str]) -> dict[str, str]:
    """Hash the contract fragments a criterion depends on, for scoped evidence drift."""
    try:
        operations, _ = all_operations(spec_root, document)
    except (OSError, ValueError):
        return {}
    hashes: dict[str, str] = {}
    for reference in references:
        operation = operations.get(reference)
        if operation is not None:
            resolved = resolve(operation.fragment, operation.root)
            canonical = json.dumps(resolved, sort_keys=True, ensure_ascii=False, default=str)
            hashes[reference] = f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    return hashes
