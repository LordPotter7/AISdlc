"""Requirement-level specification diff for a reopened Gate 2 (Wave S2.3).

Reopening Gate 2 is expensive, and it is expensive in a way nobody could see: the gate
packet offered `git diff --stat`, which says a file changed but not which requirement,
and says nothing about which Tasks lose their evidence as a result. People avoid what they
cannot price, so specifications drift instead of being corrected through the gate.

The baseline is what the human approved, not what was last presented: the runner
snapshots the specification when Gate 2 is approved, into runner-owned runtime state, so
the diff also covers edits nobody committed. "Changed" uses the same per-requirement
hashes as W3.4 scoped evidence invalidation — criteria and referenced contract operations
folded in — so a requirement this report calls changed is exactly one whose Tasks'
evidence will go stale.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evidence import requirement_hashes
from .storage import atomic_write_text
from .vendor import yaml


def _snapshot_path(repository_root: Path, intent_id: str) -> Path:
    return repository_root / ".ai-sdlc" / "runtime" / "gate-snapshots" / f"{intent_id}-G2.json"


def _canonical(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _state(intent_root: Path) -> dict[str, Any]:
    spec_root = intent_root / "02-specification"
    document = yaml.safe_load((spec_root / "requirements.yaml").read_text(encoding="utf-8")) or {}
    records = (document.get("functional_requirements") or []) + (document.get("non_functional_requirements") or [])
    requirements = {str(item["id"]): item for item in records if isinstance(item, dict) and item.get("id")}
    criteria = {
        str(item["id"]): _canonical(item)
        for item in document.get("acceptance_criteria") or []
        if isinstance(item, dict) and item.get("id")
    }
    files = {
        str(path.relative_to(spec_root)).replace("\\", "/"): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(item for item in spec_root.rglob("*") if item.is_file())
        # Generated at `advance`, not authored: a change there is not a specification change.
        if path.name not in {"requirements.yaml", "inherited-controls.yaml"}
    }
    return {
        "requirement_hashes": requirement_hashes(spec_root),
        "priorities": {key: value.get("priority") for key, value in requirements.items()},
        "criterion_hashes": criteria,
        "file_hashes": files,
    }


def snapshot(repository_root: Path, intent_root: Path, intent_id: str, revision: int | None) -> Path:
    """Record the specification a Gate 2 approval covered."""
    payload = {
        "intent_id": intent_id,
        "gate": "G2",
        "gate_revision": revision,
        "captured_at": datetime.now(UTC).isoformat(),
        **_state(intent_root),
    }
    path = _snapshot_path(repository_root, intent_id)
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _compare(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(key for key in set(before) & set(after) if before[key] != after[key]),
    }


def diff(repository_root: Path, intent_root: Path, intent_id: str) -> dict[str, Any] | None:
    """What moved since Gate 2 was last approved, and which Tasks it costs. None without a baseline."""
    path = _snapshot_path(repository_root, intent_id)
    if not path.is_file():
        return None
    baseline = json.loads(path.read_text(encoding="utf-8"))
    current = _state(intent_root)
    requirements = _compare(baseline.get("requirement_hashes", {}), current["requirement_hashes"])
    reprioritised = [
        {"requirement": key, "from": baseline["priorities"].get(key), "to": current["priorities"].get(key)}
        for key in sorted(set(baseline.get("priorities", {})) & set(current["priorities"]))
        if baseline["priorities"].get(key) != current["priorities"].get(key)
    ]
    moved = set(requirements["changed"]) | set(requirements["removed"])
    impacted: list[dict[str, Any]] = []
    for task_path in sorted(intent_root.rglob("task.yaml")):
        task = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        hit = sorted(moved & set(task.get("requirements") or []))
        if hit:
            impacted.append({"task": task.get("id"), "requirements": hit})
    return {
        "baseline_revision": baseline.get("gate_revision"),
        "baseline_captured_at": baseline.get("captured_at"),
        "requirements": requirements,
        "criteria": _compare(baseline.get("criterion_hashes", {}), current["criterion_hashes"]),
        "reprioritised": reprioritised,
        "other_artifacts": _compare(baseline.get("file_hashes", {}), current["file_hashes"]),
        "tasks_losing_evidence": impacted,
    }
