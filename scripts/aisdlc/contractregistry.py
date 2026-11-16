"""Cross-Intent contract registry (Wave S3.3).

S2.1 made a contract part of one Intent's specification. At portfolio scale the failure
modes are between Intents: two teams change the same operation at once, or one ships a
change that breaks consumers of what an earlier Intent delivered. Neither is visible from
inside a single specification.

The registry is not a new store. It is a read-only projection over the delivery tree and
runner state, keyed by the `api` a contract declaration names:

- **Contention.** Two active Intents covering the same operation of the same `api` is
  exactly W3.3's shared-resource contention, with the resource `contract:<api>#<operation>`.
  It is reported at Gates 2 and 3 and resolved the W3.3 way — serialise the work, or record
  a co-approval with `coapprove-resource` — so there is one mechanism, not two.
- **Breaking change.** The baseline for an `api` is its contract in the most recent
  *completed* Intent: the version consumers are built against. A removed operation, a new
  required input, a removed response, or a removed or retyped field is a breaking change,
  and it fails validation unless the declaration accepts it by name with a reason.

"Most recent" is the highest Intent number, because identifiers are allocated
monotonically by the runner and completion order is not recorded independently of it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .contracts import ContractError, Operation, contract_operations, resolve
from .vendor import yaml

ACTIVE = {"specified", "planned", "building", "converging"}
INTENT_DIRECTORY = re.compile(r"^(I\d{3,})-")


def _json_schemas(container: Any) -> list[Any]:
    """Every schema under an OpenAPI `content` map."""
    if not isinstance(container, dict):
        return []
    return [media.get("schema") for media in (container.get("content") or {}).values() if isinstance(media, dict)]


def _schema_breaks(before: Any, after: Any, where: str, *, is_input: bool) -> list[str]:
    """Top-level schema changes that break a consumer.

    For an input (a request), a new required property breaks every existing caller. For an
    output (a response or an event), a removed property breaks every existing reader. A
    changed type breaks both. Deeper structural diffing is the job of a dedicated tool in
    the criterion's command; these are the changes that are unambiguous at a glance.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    breaks: list[str] = []
    if before.get("type") and after.get("type") and before.get("type") != after.get("type"):
        breaks.append(f"{where} type changed {before.get('type')} -> {after.get('type')}")
    old_properties = before.get("properties") or {}
    new_properties = after.get("properties") or {}
    if is_input:
        for name in sorted(set(after.get("required") or []) - set(before.get("required") or [])):
            breaks.append(f"{where} now requires `{name}`")
    else:
        for name in sorted(set(old_properties) - set(new_properties)):
            breaks.append(f"{where} no longer returns `{name}`")
    for name in sorted(set(old_properties) & set(new_properties)):
        old_type = (old_properties[name] or {}).get("type") if isinstance(old_properties[name], dict) else None
        new_type = (new_properties[name] or {}).get("type") if isinstance(new_properties[name], dict) else None
        if old_type and new_type and old_type != new_type:
            breaks.append(f"{where} field `{name}` type changed {old_type} -> {new_type}")
    return breaks


def _operation_breaks(kind: str, before: Operation, after: Operation) -> list[str]:
    old = resolve(before.fragment, before.root)
    new = resolve(after.fragment, after.root)
    if kind == "jsonschema":
        return _schema_breaks(old, new, "schema", is_input=True) + _schema_breaks(old, new, "schema", is_input=False)
    if kind == "asyncapi":
        old_payload = ((old or {}).get("message") or {}).get("payload") if isinstance(old, dict) else None
        new_payload = ((new or {}).get("message") or {}).get("payload") if isinstance(new, dict) else None
        return _schema_breaks(old_payload, new_payload, "message payload", is_input=False)
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    breaks: list[str] = []
    old_required = {
        (p.get("in"), p.get("name")) for p in old.get("parameters") or [] if isinstance(p, dict) and p.get("required")
    }
    for location, name in sorted(
        {(p.get("in"), p.get("name")) for p in new.get("parameters") or [] if isinstance(p, dict) and p.get("required")}
        - old_required
    ):
        breaks.append(f"new required {location} parameter `{name}`")
    for old_schema, new_schema in zip(
        _json_schemas(old.get("requestBody")), _json_schemas(new.get("requestBody")), strict=False
    ):
        breaks.extend(_schema_breaks(old_schema, new_schema, "request body", is_input=True))
    old_responses, new_responses = old.get("responses") or {}, new.get("responses") or {}
    for code in sorted(set(old_responses) - set(new_responses), key=str):
        breaks.append(f"response {code} removed")
    for code in sorted(set(old_responses) & set(new_responses), key=str):
        for old_schema, new_schema in zip(
            _json_schemas(old_responses[code]), _json_schemas(new_responses[code]), strict=False
        ):
            breaks.extend(_schema_breaks(old_schema, new_schema, f"response {code}", is_input=False))
    return breaks


class ContractRegistry:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def _entries(self) -> list[dict[str, Any]]:
        """Every Intent's `api`-identified contracts, with its lifecycle status."""
        from .config import ConfigError, delivery_root
        from .state import StateStore

        try:
            root = delivery_root(self.repository_root)
        except ConfigError:
            return []
        if not root.is_dir():
            return []
        intents = StateStore(self.repository_root).read()["intents"]
        statuses = {key: value.get("status") for key, value in intents.items()}
        entries: list[dict[str, Any]] = []
        for intent_root in sorted(item for item in root.iterdir() if item.is_dir()):
            match = INTENT_DIRECTORY.match(intent_root.name)
            spec_root = intent_root / "02-specification"
            if not match or not (spec_root / "requirements.yaml").is_file():
                continue
            try:
                document = yaml.safe_load((spec_root / "requirements.yaml").read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            for declaration in document.get("contracts", []) or []:
                if not isinstance(declaration, dict) or not declaration.get("api"):
                    continue
                entries.append(
                    {
                        "intent_id": match.group(1),
                        "status": statuses.get(match.group(1)),
                        "intent_root": intent_root,
                        "document": document,
                        "declaration": declaration,
                    }
                )
        return entries

    @staticmethod
    def _operations(entry: dict[str, Any]) -> dict[str, Operation]:
        try:
            operations = contract_operations(entry["intent_root"] / "02-specification", entry["declaration"])
        except ContractError:
            return {}
        return {operation.name: operation for operation in operations}

    @staticmethod
    def _covered(entry: dict[str, Any]) -> set[str]:
        excluded = {item.get("operation") for item in entry["declaration"].get("excluded_operations", []) or []}
        return set(ContractRegistry._operations(entry)) - excluded

    def baseline(self, api: str, intent_id: str) -> dict[str, Any] | None:
        candidates = [
            entry
            for entry in self._entries()
            if entry["declaration"]["api"] == api and entry["status"] == "completed" and entry["intent_id"] != intent_id
        ]
        return max(candidates, key=lambda entry: int(entry["intent_id"][1:]), default=None)

    def breaking_changes(self, intent_root: Path, intent_id: str) -> list[dict[str, Any]]:
        """Changes against each `api`'s baseline, with whether each is accepted on the record."""
        changes: list[dict[str, Any]] = []
        for entry in self._entries():
            if entry["intent_id"] != intent_id:
                continue
            declaration = entry["declaration"]
            baseline = self.baseline(declaration["api"], intent_id)
            if baseline is None:
                continue
            accepted = {item.get("operation"): item for item in declaration.get("breaking_changes_accepted", []) or []}
            current = self._operations(entry)
            kind = declaration.get("kind") or baseline["declaration"].get("kind")
            if kind is None:
                sample = next(iter(current.values()), None) or next(iter(self._operations(baseline).values()), None)
                from .contracts import detect_kind

                kind = detect_kind(sample.root) if sample else None
            for name, before in sorted(self._operations(baseline).items()):
                after = current.get(name)
                problems = ["operation removed"] if after is None else _operation_breaks(str(kind), before, after)
                for problem in problems:
                    changes.append(
                        {
                            "api": declaration["api"],
                            "operation": name,
                            "change": problem,
                            "baseline_intent": baseline["intent_id"],
                            "accepted_by": (accepted.get(name) or {}).get("accepted_by"),
                        }
                    )
        return changes

    def contention(self, intent_id: str) -> list[dict[str, Any]]:
        """Operations this Intent covers that another active Intent also covers, not co-approved."""
        from .tasks import IntentResourceCoordinator

        coapproved = [
            item
            for item in IntentResourceCoordinator(self.repository_root).read().get("coapprovals", [])
            if isinstance(item, dict)
        ]
        entries = self._entries()
        mine = [entry for entry in entries if entry["intent_id"] == intent_id]
        conflicts: list[dict[str, Any]] = []
        for entry in mine:
            api = entry["declaration"]["api"]
            covered = self._covered(entry)
            for other in entries:
                if other["intent_id"] == intent_id or other["status"] not in ACTIVE:
                    continue
                if other["declaration"]["api"] != api:
                    continue
                for operation in sorted(covered & self._covered(other)):
                    resource = f"contract:{api}#{operation}"
                    pair = {intent_id, other["intent_id"]}
                    if any(
                        item.get("resource") == resource and pair <= set(item.get("intents", [])) for item in coapproved
                    ):
                        continue
                    conflicts.append({"resource": resource, "intent": other["intent_id"], "status": other["status"]})
        return conflicts


def breaking_change_errors(intent_root: Path, intent_id: str) -> list[str]:
    """Validation errors for unaccepted breaking changes. Empty outside a repository."""
    from .assets import repository_root_of

    root = repository_root_of(intent_root)
    if root is None:
        return []
    return [
        f"{item['api']}#{item['operation']}: {item['change']} against {item['baseline_intent']}; accept it in "
        "`breaking_changes_accepted` with a name and reason, or keep the contract compatible"
        for item in ContractRegistry(root).breaking_changes(intent_root, intent_id)
        if not item["accepted_by"]
    ]
