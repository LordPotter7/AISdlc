from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .decisions import canonical_hash, load_decisions
from .migrate import stamp
from .storage import atomic_write_json
from .vendor import yaml


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def generate_traceability(intent_root: Path, task_statuses: dict[str, str] | None = None) -> Path:
    requirements = _load_yaml(intent_root / "02-specification" / "requirements.yaml")
    tasks = [_load_yaml(path) for path in sorted(intent_root.rglob("task.yaml"))]
    requirement_records = requirements.get("functional_requirements", []) + requirements.get(
        "non_functional_requirements", []
    )
    criteria = {item["id"]: item for item in requirements.get("acceptance_criteria", [])}
    links: list[dict[str, Any]] = []
    for requirement in requirement_records:
        mapped_tasks = [task for task in tasks if requirement["id"] in task.get("requirements", [])]
        artifacts = sorted({artifact for task in mapped_tasks for artifact in task.get("expected_outputs", [])})
        checks = sorted({command for task in mapped_tasks for command in task.get("verification_commands", [])})
        evidence = sorted(
            str(path.relative_to(intent_root)).replace("\\", "/")
            for task_path in intent_root.rglob("task.yaml")
            if requirement["id"] in _load_yaml(task_path).get("requirements", [])
            for path in (task_path.parent / "evidence").glob("manifest*.json")
        )
        mapped_statuses = {(task_statuses or {}).get(task["id"], task.get("status")) for task in mapped_tasks}
        status = "verified" if mapped_tasks and mapped_statuses <= {"verified", "merged"} else "planned"
        if requirement.get("priority") == "wont":
            # Recorded, excluded scope: kept in the matrix so the exclusion is auditable.
            status = "deferred"
        verification_classes = sorted(
            {
                criteria[criterion_id]["verification_class"]
                for criterion_id in requirement.get("acceptance_criteria", [])
                if criterion_id in criteria
            }
        )
        links.append(
            {
                "requirement": requirement["id"],
                "acceptance_criteria": requirement.get("acceptance_criteria", []),
                "verification_classes": verification_classes,
                "tasks": [task["id"] for task in mapped_tasks],
                "artifacts": artifacts,
                "checks": checks,
                "evidence": evidence,
                "status": status,
            }
        )
    # F1/F2: architectural decisions are the third trace target the repository rules
    # require. They are generated alongside requirement links rather than instead of them,
    # because a Task normally traces to both — a requirement says what must be true, a
    # decision says which shape the solution takes.
    decisions = load_decisions(intent_root)
    decision_links = [
        {
            "decision": decision["id"],
            "title": decision.get("title"),
            "status": decision.get("status"),
            "content_hash": canonical_hash(decision),
            "requirements": sorted(decision.get("requirements", [])),
            "tasks": sorted(task["id"] for task in tasks if decision["id"] in task.get("source_decisions", [])),
        }
        for decision in decisions
        if decision.get("id")
    ]

    output = intent_root / "traceability.json"
    reverse_links = {
        task["id"]: sorted(
            requirement["id"]
            for requirement in requirement_records
            if requirement["id"] in task.get("requirements", [])
        )
        for task in tasks
    }
    decision_reverse_links = {
        task["id"]: sorted(task.get("source_decisions", [])) for task in tasks if task.get("source_decisions")
    }
    atomic_write_json(
        output,
        stamp(
            {
                "intent_id": requirements["intent_id"],
                "generated_at": datetime.now(UTC).isoformat(),
                "generated_by": "runner",
                "links": links,
                "reverse_links": reverse_links,
                "decision_links": decision_links,
                "decision_reverse_links": decision_reverse_links,
            }
        ),
    )
    return output
