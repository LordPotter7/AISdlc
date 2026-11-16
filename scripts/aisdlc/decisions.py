"""Architectural decisions as records the runner owns, hashed per decision.

Until now architecture was a heading. `master-plan-template.md` carried
`## Architecture` followed by a prose placeholder, and that was the whole of it: no
identifier, no hash, no traceability link, and nothing for a Task to point at. Meanwhile
the repository rule shipped into every adopting repository said a Task traces to an
approved requirement, finding, **or decision** — one third of a mandatory rule that the
schema made unsatisfiable.

This module is the missing third. A decision is Intent-scoped (`I001-D001`), allocated by
the runner, schema-validated, and hashed individually.

The per-decision hash is the load-bearing part, and it is deliberately the same shape as
W3.4's per-requirement hashes. A single whole-document hash would mean any edit to any
decision invalidated every Task's evidence, which teaches people not to record decisions.
Hashing each one means a changed decision invalidates exactly the Tasks that declared it
in `source_decisions`, and nothing else.

That also makes one edge expressible that was not before: work that arrives mid-Intent can
invalidate a structural decision, and the Tasks that trace to it are precisely the ones
that must re-enter. Without a record at the other end there was nothing to route back to.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .assets import schema_path
from .identifiers import IdentifierRegistry
from .jsonschema_mini import Draft202012Validator
from .migrate import stamp
from .storage import atomic_write_text
from .vendor import yaml

DECISIONS_DIRECTORY = ("03-planning", "decisions")


class DecisionError(ValueError):
    """Raised when a decision record is malformed, unknown, or conflicts with another."""


def decisions_root(intent_root: Path) -> Path:
    return intent_root.joinpath(*DECISIONS_DIRECTORY)


def load_decisions(intent_root: Path) -> list[dict[str, Any]]:
    """Every decision recorded for an Intent, ordered by identifier.

    Missing directory means none: architecture records are additive, so an Intent from
    before they existed reads as an Intent with no recorded decisions rather than an
    invalid one.
    """
    root = decisions_root(intent_root)
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise DecisionError(f"unreadable decision {path.name}: {error}") from error
        if isinstance(payload, dict):
            records.append(payload)
    return records


def canonical_hash(decision: dict[str, Any]) -> str:
    """Hash the decision's substance, not its bookkeeping.

    `status` and `recorded_by` move without the decision changing — accepting a proposed
    decision must not invalidate evidence — so they are excluded. What remains is the part
    a Task actually depends on: the context, the call, its consequences, the alternatives
    that were rejected, and what it constrains.
    """
    material = {
        key: decision.get(key)
        for key in ("id", "title", "context", "decision", "consequences", "alternatives", "affects", "supersedes")
        if decision.get(key) is not None
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def decision_hashes(intent_root: Path) -> dict[str, str]:
    """Identifier to content hash for every decision this Intent has recorded."""
    return {str(record["id"]): canonical_hash(record) for record in load_decisions(intent_root) if record.get("id")}


class DecisionRegistry:
    """Allocate, validate, and persist architectural decisions for one repository."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def record(self, intent_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        """Register a new decision, allocating its permanent identifier.

        The caller supplies substance; the runner supplies identity and time. A payload
        arriving with its own `id` is refused rather than honoured, because an identifier
        a model chose is an identifier a model can reuse.
        """
        if "id" in payload:
            raise DecisionError(
                "decision identifiers are allocated by the runner; remove `id` and let the registry assign one"
            )
        intent_id = str(payload.get("intent_id") or "")
        if not intent_id:
            raise DecisionError("a decision must name the Intent it was made for")

        record = dict(payload)
        record["id"] = IdentifierRegistry(self.repository_root).allocate("decision", parent=intent_id)
        record.setdefault("status", "proposed")
        record.setdefault("created_at", datetime.now(UTC).isoformat())
        stamp(record)
        self._validate(record)

        supersedes = record.get("supersedes")
        if supersedes:
            self._mark_superseded(intent_root, str(supersedes), record["id"])

        root = decisions_root(intent_root)
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(root / f"{record['id']}.yaml", yaml.safe_dump(record, sort_keys=False))
        return record

    def accept(self, intent_root: Path, decision_id: str) -> dict[str, Any]:
        """Move a proposed decision to accepted. Never rewrites its substance."""
        path = decisions_root(intent_root) / f"{decision_id}.yaml"
        if not path.is_file():
            raise DecisionError(f"unknown decision: {decision_id}")
        record = yaml.safe_load(path.read_text(encoding="utf-8"))
        if record.get("status") == "superseded":
            raise DecisionError(f"{decision_id} is superseded; record a new decision instead of reviving it")
        before = canonical_hash(record)
        record["status"] = "accepted"
        # Accepting must not invalidate evidence pinned to this decision. If it does, the
        # hash is covering bookkeeping it should not.
        if canonical_hash(record) != before:
            raise DecisionError("accepting a decision changed its content hash; this is a runner defect")
        atomic_write_text(path, yaml.safe_dump(record, sort_keys=False))
        return record

    def _mark_superseded(self, intent_root: Path, decision_id: str, replacement: str) -> None:
        path = decisions_root(intent_root) / f"{decision_id}.yaml"
        if not path.is_file():
            raise DecisionError(f"cannot supersede unknown decision: {decision_id}")
        record = yaml.safe_load(path.read_text(encoding="utf-8"))
        record["status"] = "superseded"
        record["superseded_by"] = replacement
        # Superseding preserves the record. The historical decision stays readable, which
        # is the whole point of recording why something was decided.
        atomic_write_text(path, yaml.safe_dump(record, sort_keys=False))

    def _validate(self, record: dict[str, Any]) -> None:
        schema = json.loads(schema_path(self.repository_root, "decision.schema.json").read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(record), key=lambda item: list(item.path))
        if errors:
            raise DecisionError(
                "decision does not satisfy decision.schema.json: "
                + "; ".join(
                    f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}" for error in errors
                )
            )
