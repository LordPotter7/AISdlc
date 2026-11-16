"""Read-only projections for AISDLC Studio.

Each function takes an open :class:`ProjectStore` and returns ``(payload, omissions)``.
They call the runner's own readers and services — the gate packet service, the spec
linter, the contract registry, the threat-model validator, the per-requirement hashes —
so a projection can only ever show what the runner itself would conclude. Nothing here
writes a file.

Honesty rules, shared with the governance app:

- Missing data is ``None``/absent, never ``0`` or "passed".
- History the runner did not record is not reconstructed. Where Story Mode shows a
  current file, it says whether the file is provably the approved version.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from aisdlc.desktop.protocol import BridgeError, Omission, sanitize_text, sanitize_value
from aisdlc.desktop.store import ProjectStore
from aisdlc.vendor import yaml

GATES = ("G1", "G2", "G3", "G4", "G5")
STAGES = (
    {"id": "register", "title": "Register", "folder": "00-intent", "gate": None, "status": "initialized"},
    {"id": "frame", "title": "Frame", "folder": "01-brief", "gate": "G1", "status": "framed"},
    {"id": "specify", "title": "Specify", "folder": "02-specification", "gate": "G2", "status": "specified"},
    {"id": "plan", "title": "Plan", "folder": "03-planning", "gate": "G3", "status": "planned"},
    {"id": "build", "title": "Build", "folder": "04-build", "gate": "G4", "status": "building"},
    {"id": "converge", "title": "Converge", "folder": "05-verification", "gate": "G5", "status": "converging"},
    {"id": "close", "title": "Close", "folder": "06-close", "gate": None, "status": "completed"},
)
STATUS_ORDER = [
    "initialized",
    "framed",
    "specified",
    "planned",
    "building",
    "converging",
    "awaiting_acceptance",
    "completed",
]
#: The gate an Intent in each status is waiting on, when a person has to act next.
WAITING_GATE = {"framed": "G1", "specified": "G2", "planned": "G3", "awaiting_acceptance": "G5"}
#: The stage whose work an Intent in each status is doing, before its gate is approved.
STATUS_STAGE = {
    "initialized": "frame",
    "framed": "frame",
    "specified": "specify",
    "planned": "plan",
    "building": "build",
    "converging": "converge",
    "awaiting_acceptance": "converge",
}
INTENT_DIR = re.compile(r"^(I\d{3,})-")
MAX_ARTIFACT_CHARS = 12_000
Result = tuple[dict[str, Any], list[Omission]]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _state(store: ProjectStore) -> dict[str, Any]:
    from aisdlc.state import StateStore

    return StateStore(store.root).read()


def intent_roots(store: ProjectStore) -> dict[str, Path]:
    delivery = Path(store.context.delivery_dir)
    roots: dict[str, Path] = {}
    if delivery.is_dir():
        for item in sorted(delivery.iterdir()):
            match = INTENT_DIR.match(item.name)
            if item.is_dir() and match:
                roots[match.group(1)] = item
    return roots


def _intent_root(store: ProjectStore, intent_id: str) -> Path:
    root = intent_roots(store).get(intent_id)
    if root is None:
        raise BridgeError("unknown_intent", f"no Intent folder for {intent_id}", detail={"intent_id": intent_id})
    return root


def _requirements(intent_root: Path) -> dict[str, Any] | None:
    document = _load_yaml(intent_root / "02-specification" / "requirements.yaml")
    return document if isinstance(document, dict) else None


def _requirement_records(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not document:
        return []
    records = (document.get("functional_requirements") or []) + (document.get("non_functional_requirements") or [])
    return [item for item in records if isinstance(item, dict)]


def _tasks(intent_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    found: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(intent_root.rglob("task.yaml")):
        task = _load_yaml(path)
        if isinstance(task, dict) and task.get("id"):
            found.append((path.parent, task))
    return found


def _audit_for(events: list[dict[str, Any]], intent_id: str) -> list[dict[str, Any]]:
    prefix_task = f"{intent_id}-"
    prefix_gate = f"{intent_id}:"
    return [
        event
        for event in events
        if str(event.get("entity_id", "")) == intent_id
        or str(event.get("entity_id", "")).startswith((prefix_task, prefix_gate))
    ]


def _last_entry(events: list[dict[str, Any]], entity_id: str, to: str | None = None) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("entity_id") == entity_id and (to is None or event.get("to") == to):
            return event
    return None


def working_stage(status: str | None, gates: dict[str, Any]) -> str | None:
    """The stage the team is working in, which is not the stage the status names.

    A status records the stage just *finished*: `framed` means the brief is written. Once
    that stage's gate is approved the work has moved on, so "framed + G1 approved" is
    writing the specification. `None` for completed or aborted Intents.
    """
    stage_id = STATUS_STAGE.get(str(status))
    if stage_id is None:
        return None
    ids = [stage["id"] for stage in STAGES]
    gate = next(stage["gate"] for stage in STAGES if stage["id"] == stage_id)
    if status in {"framed", "specified", "planned"} and gates.get(gate, {}).get("status") == "approved":
        return ids[ids.index(stage_id) + 1]
    return stage_id


def _pending_approvals(events: list[dict[str, Any]], gate_ids: set[str]) -> list[str]:
    """People who approved a gate that needs several, since it last changed state (ADR-010)."""
    approvers: list[str] = []
    for event in events:
        if event.get("entity_id") not in gate_ids:
            continue
        if event.get("to") is not None:
            approvers = []
        elif event.get("event") == "gate_approval":
            approvers.append(str(event.get("actor")))
    return approvers


def _gate_view(
    state: dict[str, Any], events: list[dict[str, Any]], intent_id: str, policies: dict[str, Any] | None = None
) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    for gate in GATES:
        needed = int(((policies or {}).get(gate) or {}).get("approvals") or 1)
        record = state.get("gates", {}).get(f"{intent_id}:{gate}")
        progress = {"needed": needed} if needed > 1 else {}
        if needed > 1 and (not record or record.get("status") != "approved"):
            ids = {f"{intent_id}:{gate}"} | ({f"{intent_id}:G2"} if gate == "G3" else set())
            progress["approvals"] = _pending_approvals(events, ids)
        if not record:
            gates[gate] = {"status": "undecided", **progress}
            continue
        last = _last_entry(events, f"{intent_id}:{gate}")
        gates[gate] = {
            "status": record.get("status"),
            "revision": record.get("revision"),
            "approver": record.get("approver"),
            "strength": record.get("strength") or record.get("identity_strength"),
            "reason": record.get("reason"),
            "at": last.get("at") if last else None,
            **({"approvers": record["approvers"]} if record.get("approvers") else {}),
            **progress,
        }
    return gates


def _profile(store: ProjectStore, root: Path | None) -> dict[str, Any] | None:
    """The pipeline profile an Intent runs under, and every default it loosens (ADR-010)."""
    from aisdlc.pipeline import PipelineError, pinned

    if root is None:
        return None
    try:
        rules = pinned(root, store.root)
    except (PipelineError, Exception):  # noqa: BLE001 - a broken pipeline never hides the Intent
        return None
    return {
        "name": rules.get("name"),
        "implicit": bool(rules.get("implicit")),
        "modes": rules.get("modes"),
        "fail_on": rules.get("fail_on"),
        "gates": rules.get("gates") or {},
        "loosened": [
            {"rule": item.get("rule"), "to": item.get("to"), **(item.get("waiver") or {})}
            for item in rules.get("loosened") or []
        ],
    }


def _task_summary(state: dict[str, Any], intent_id: str) -> dict[str, int]:
    summary: dict[str, int] = {}
    for task_id, record in state.get("tasks", {}).items():
        if task_id.startswith(f"{intent_id}-"):
            status = str(record.get("status"))
            summary[status] = summary.get(status, 0) + 1
    return summary


def _findings(intent_root: Path) -> list[dict[str, Any]]:
    from aisdlc.convergence import load_findings

    try:
        return [item for item in load_findings(intent_root) if isinstance(item, dict)]
    except Exception:  # noqa: BLE001 - an unreadable findings file is shown as absent, not fatal
        return []


def _lint(store: ProjectStore, intent_root: Path) -> dict[str, Any] | None:
    from aisdlc.specquality import lint_intent

    if not (intent_root / "02-specification" / "requirements.yaml").is_file():
        return None
    try:
        return lint_intent(intent_root, store.context.configuration.get("specification_fail_on", "error"))
    except Exception as error:  # noqa: BLE001
        return {"error": sanitize_text(error), "passed": False, "counts": {}}


def _intent_summary(
    store: ProjectStore, intent_id: str, root: Path | None, state: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    from aisdlc.state import INTENT_TRANSITIONS

    document = _load_yaml(root / "00-intent" / "intent.yaml") if root else None
    document = document if isinstance(document, dict) else {}
    status = state.get("intents", {}).get(intent_id, {}).get("status")
    requirements = _requirements(root) if root else None
    criteria = [item for item in (requirements or {}).get("acceptance_criteria", []) or [] if isinstance(item, dict)]
    classes: dict[str, int] = {}
    for criterion in criteria:
        key = str(criterion.get("verification_class"))
        classes[key] = classes.get(key, 0) + 1
    waiting_gate = WAITING_GATE.get(str(status))
    if waiting_gate and state.get("gates", {}).get(f"{intent_id}:{waiting_gate}", {}).get("status") == "approved":
        waiting_gate = None  # approved; the next move is the team's, not a person's decision
    entered = _last_entry(events, intent_id, status) if status else None
    profile = _profile(store, root)
    findings = _findings(root) if root else []
    lint = _lint(store, root) if root and status in {"framed", "specified"} else None
    return {
        "id": intent_id,
        "title": document.get("title") or intent_id,
        "type": document.get("intent_type"),
        "mode": document.get("artifact_mode"),
        "contains_model_behavior": document.get("contains_model_behavior"),
        "trigger_flags": document.get("trigger_flags") or {},
        "status": status,
        "status_known": status in STATUS_ORDER or status == "aborted",
        "status_since": entered.get("at") if entered else None,
        "path": str(root) if root else None,
        "profile": profile,
        "gates": (gate_view := _gate_view(state, events, intent_id, (profile or {}).get("gates"))),
        "working_stage": working_stage(status, gate_view),
        "waiting_on": {"gate": waiting_gate, "since": entered.get("at") if entered else None} if waiting_gate else None,
        "next_legal_actions": sorted(INTENT_TRANSITIONS.get(str(status), set())),
        "requirements": len(_requirement_records(requirements)) if requirements else None,
        "criteria": len(criteria) if requirements else None,
        "classes": classes,
        "tasks": _task_summary(state, intent_id),
        "open_findings": {
            severity: sum(1 for item in findings if item.get("severity") == severity and item.get("status") == "open")
            for severity in ("critical", "high", "medium", "low")
        },
        "lint": None if lint is None else {"counts": lint.get("counts"), "passed": lint.get("passed")},
    }


# --------------------------------------------------------------------------- #
# portfolio (Cockpit home)
# --------------------------------------------------------------------------- #
def portfolio(store: ProjectStore, params: dict[str, Any] | None = None) -> Result:
    from aisdlc.contractregistry import ContractRegistry

    omissions: list[Omission] = []
    state = _state(store)
    events = store.read_audit()
    roots = intent_roots(store)
    identifiers = sorted(set(roots) | set(state.get("intents", {})), key=lambda item: int(item[1:]))
    intents = [_intent_summary(store, key, roots.get(key), state, events) for key in identifiers]
    registry = ContractRegistry(store.root)
    contention: list[dict[str, Any]] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    for intent in intents:
        if intent["status"] in {"specified", "planned", "building", "converging"}:
            for item in registry.contention(intent["id"]):
                # One conflict between two Intents is reported from both sides; count it once.
                key = (item["resource"], frozenset({intent["id"], item["intent"]}))
                if key not in seen:
                    seen.add(key)
                    contention.append({**item, "claimant": intent["id"]})
    tasks_total = sum(sum(intent["tasks"].values()) for intent in intents)
    tasks_done = sum(intent["tasks"].get("verified", 0) + intent["tasks"].get("merged", 0) for intent in intents)
    if store.fingerprint_truncated:
        omissions.append(Omission("fingerprint", "delivery tree larger than the change-detection bound"))
    return {
        "repository": {"name": store.context.name, "root": store.context.root},
        "intents": intents,
        "waiting": [
            {"intent": item["id"], "title": item["title"], **item["waiting_on"]}
            for item in intents
            if item["waiting_on"]
        ],
        "kpis": {
            "intents": len(intents),
            "waiting_gates": sum(1 for item in intents if item["waiting_on"]),
            "tasks_total": tasks_total,
            "tasks_verified": tasks_done,
            "open_high": sum(item["open_findings"]["critical"] + item["open_findings"]["high"] for item in intents),
            "contention": len(contention),
        },
        "contention": contention,
        "recent_events": [_event_view(event) for event in events[-15:]][::-1],
        "audit_events": len(events),
    }, omissions


# --------------------------------------------------------------------------- #
# intent (rail, tasks)
# --------------------------------------------------------------------------- #
def intent_inspect(store: ProjectStore, intent_id: str, params: dict[str, Any] | None = None) -> Result:
    root = _intent_root(store, intent_id)
    state = _state(store)
    events = store.read_audit()
    summary = _intent_summary(store, intent_id, root, state, events)
    status = summary["status"]
    ids = [stage["id"] for stage in STAGES]
    working = summary["working_stage"]
    current = ids.index(working) if working in ids else (len(ids) if status == "completed" else -1)
    rail = []
    for index, stage in enumerate(STAGES):
        gate = stage["gate"]
        rail.append(
            {
                **stage,
                "state": "done" if index < current else "current" if index == current else "future",
                "present": (root / stage["folder"]).exists(),
                "gate_record": summary["gates"].get(gate) if gate else None,
            }
        )
    tasks = []
    for directory, task in _tasks(root):
        record = state.get("tasks", {}).get(task["id"], {})
        tasks.append(
            {
                "id": task["id"],
                "title": task.get("title"),
                "phase": task.get("phase_id"),
                "status": record.get("status") or "not registered",
                "attempt": record.get("attempt"),
                "depends_on": task.get("depends_on") or [],
                "requirements": task.get("requirements") or [],
                "criteria": [
                    item.get("id") for item in task.get("acceptance_criteria") or [] if isinstance(item, dict)
                ],
                "shared_resources": task.get("shared_resources") or [],
                "has_holdout": bool(task.get("holdout_commands")),
                "reviewed": (directory / "review.yaml").is_file(),
            }
        )
    artifacts = {
        stage["id"]: sorted(
            str(path.relative_to(root)).replace("\\", "/")
            for path in (root / stage["folder"]).rglob("*")
            if path.is_file()
        )[:40]
        if (root / stage["folder"]).is_dir()
        else []
        for stage in STAGES
    }
    return {**summary, "rail": rail, "task_list": tasks, "artifacts": artifacts}, []


# --------------------------------------------------------------------------- #
# gate packet
# --------------------------------------------------------------------------- #
def gate_packet(store: ProjectStore, intent_id: str, params: dict[str, Any] | None = None) -> Result:
    from aisdlc.gatepacket import GatePacketService

    gate = str((params or {}).get("gate") or "")
    if gate not in GATES:
        raise BridgeError("invalid_params", "gate must be one of G1-G5", detail={"gate": gate})
    service = GatePacketService(store.root)
    packet = service.build(_intent_root(store, intent_id), gate)
    return {"packet": sanitize_value(packet), "markdown": sanitize_text(service.render(packet), limit=60_000)}, []


# --------------------------------------------------------------------------- #
# specification
# --------------------------------------------------------------------------- #
def spec_inspect(store: ProjectStore, intent_id: str, params: dict[str, Any] | None = None) -> Result:
    from aisdlc.contractregistry import ContractRegistry
    from aisdlc.contracts import all_operations
    from aisdlc.specquality import source_lines
    from aisdlc.threatmodel import THREAT_MODEL, inherited_threats, stride_gaps
    from aisdlc.validation import validate_intent

    root = _intent_root(store, intent_id)
    document = _requirements(root)
    if document is None:
        return {"present": False}, [Omission("specification", "requirements.yaml not written yet")]
    intent = _load_yaml(root / "00-intent" / "intent.yaml") or {}
    spec_root = root / "02-specification"
    operations, contract_errors = all_operations(spec_root, document)
    covering: dict[str, list[str]] = {}
    for criterion in document.get("acceptance_criteria") or []:
        for reference in (criterion or {}).get("contract_operations", []) or []:
            covering.setdefault(reference, []).append(str(criterion.get("id")))
    excluded = {
        f"{declaration.get('path')}#{item.get('operation')}": item.get("reason")
        for declaration in document.get("contracts", []) or []
        if isinstance(declaration, dict)
        for item in declaration.get("excluded_operations", []) or []
    }
    registry = ContractRegistry(store.root)
    threat_model = _load_yaml(spec_root / THREAT_MODEL)
    validation = validate_intent(root, require_plan=False)
    lines = source_lines(root)

    def located(records: list[Any]) -> list[Any]:
        return [
            {**item, "source": lines.get(str(item.get("id")))} if isinstance(item, dict) else item for item in records
        ]

    if isinstance(threat_model, dict):
        threat_model = {
            **threat_model,
            "threats": located(threat_model.get("threats") or []),
            "boundaries": located(threat_model.get("boundaries") or []),
        }
    return {
        "present": True,
        "requirements": located(_requirement_records(document)),
        "criteria": located(document.get("acceptance_criteria") or []),
        "success_criteria": located(document.get("success_criteria") or []),
        "glossary": document.get("glossary") or [],
        "lint": _lint(store, root),
        "validation_errors": list(validation.errors),
        "contracts": {
            "operations": [
                {
                    "reference": reference,
                    "contract": operation.contract,
                    "name": operation.name,
                    "covered_by": covering.get(reference, []),
                    "excluded_reason": excluded.get(reference),
                }
                for reference, operation in sorted(operations.items())
            ],
            "errors": contract_errors,
            "breaking_changes": registry.breaking_changes(root, intent_id),
            "contention": registry.contention(intent_id),
        },
        "threat_model": threat_model if isinstance(threat_model, dict) else None,
        "stride_gaps": stride_gaps(root),
        "inherited_threats": inherited_threats(root, intent if isinstance(intent, dict) else {}),
    }, []


# --------------------------------------------------------------------------- #
# traceability canvas
# --------------------------------------------------------------------------- #
def _latest_manifest(task_directory: Path) -> dict[str, Any] | None:
    index = task_directory / "evidence" / "manifest.json"
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
        latest = data["attempts"][-1]["path"]
        return json.loads((task_directory / "evidence" / latest).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def trace_inspect(store: ProjectStore, intent_id: str, params: dict[str, Any] | None = None) -> Result:
    from aisdlc.evidence import requirement_hashes
    from aisdlc.specdiff import diff as spec_diff

    root = _intent_root(store, intent_id)
    document = _requirements(root)
    if document is None:
        return {"present": False, "nodes": [], "edges": [], "gaps": []}, [
            Omission("trace", "requirements.yaml not written yet")
        ]
    state = _state(store)
    requirements = _requirement_records(document)
    criteria = {
        str(item.get("id")): item for item in document.get("acceptance_criteria") or [] if isinstance(item, dict)
    }
    current_hashes = requirement_hashes(root / "02-specification")
    change = spec_diff(store.root, root, intent_id) or {}
    changed = set((change.get("requirements") or {}).get("changed", []))
    added = set((change.get("requirements") or {}).get("added", []))
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []

    def node(node_id: str, lane: str, label: str, **extra: Any) -> None:
        nodes.append({"id": node_id, "lane": lane, "label": sanitize_text(label, limit=160), **extra})

    owner: dict[str, list[str]] = {}
    for requirement in requirements:
        for criterion_id in requirement.get("acceptance_criteria", []) or []:
            owner.setdefault(criterion_id, []).append(str(requirement.get("id")))
    for success in document.get("success_criteria") or []:
        if not isinstance(success, dict):
            continue
        node(str(success.get("id")), "success", str(success.get("statement", "")))
        linked = {req for criterion in success.get("acceptance_criteria", []) or [] for req in owner.get(criterion, [])}
        if not linked:
            gaps.append(
                {
                    "id": str(success.get("id")),
                    "kind": "success_unlinked",
                    "message": "no acceptance criterion demonstrates this success measure",
                }
            )
        for requirement_id in sorted(linked):
            edges.append({"from": str(success.get("id")), "to": requirement_id, "kind": "demonstrated_by"})

    tasks = _tasks(root)
    task_by_requirement: dict[str, list[str]] = {}
    task_by_criterion: dict[str, list[str]] = {}
    for _, task in tasks:
        for requirement_id in task.get("requirements") or []:
            task_by_requirement.setdefault(requirement_id, []).append(task["id"])
        for item in task.get("acceptance_criteria") or []:
            if isinstance(item, dict) and item.get("id"):
                task_by_criterion.setdefault(str(item["id"]), []).append(task["id"])

    for requirement in requirements:
        requirement_id = str(requirement.get("id"))
        deferred = requirement.get("priority") == "wont"
        node(
            requirement_id,
            "requirement",
            str(requirement.get("statement", "")),
            priority=requirement.get("priority"),
            category=requirement.get("category"),
            deferred=deferred,
            changed=requirement_id in changed,
            added=requirement_id in added,
        )
        if not deferred and not task_by_requirement.get(requirement_id) and tasks:
            gaps.append({"id": requirement_id, "kind": "requirement_unplanned", "message": "no Task implements it"})
        for criterion_id in requirement.get("acceptance_criteria", []) or []:
            edges.append({"from": requirement_id, "to": criterion_id, "kind": "verified_by"})

    for criterion_id, criterion in criteria.items():
        node(
            criterion_id,
            "criterion",
            str(criterion.get("statement", "")),
            verification_class=criterion.get("verification_class"),
        )
        if criterion_id not in owner:
            gaps.append({"id": criterion_id, "kind": "criterion_orphan", "message": "no requirement owns it"})
        deferred_only = all(
            next((r for r in requirements if r.get("id") == req), {}).get("priority") == "wont"
            for req in owner.get(criterion_id, [])
        ) and owner.get(criterion_id)
        if tasks and not task_by_criterion.get(criterion_id) and not deferred_only:
            gaps.append({"id": criterion_id, "kind": "criterion_untasked", "message": "no Task declares it"})
        for task_id in task_by_criterion.get(criterion_id, []):
            edges.append({"from": criterion_id, "to": task_id, "kind": "implemented_by"})

    for directory, task in tasks:
        task_id = task["id"]
        record = state.get("tasks", {}).get(task_id, {})
        manifest = _latest_manifest(directory)
        stale: list[str] = []
        if manifest and isinstance(manifest.get("specification"), dict):
            pinned = manifest["specification"].get("requirement_hashes") or {}
            stale = sorted(
                requirement_id
                for requirement_id in task.get("requirements") or []
                if requirement_id in pinned and pinned.get(requirement_id) != current_hashes.get(requirement_id)
            )
        node(
            task_id,
            "task",
            str(task.get("title", "")),
            status=record.get("status") or "not registered",
            attempt=record.get("attempt"),
            stale_requirements=stale,
        )
        if manifest is None:
            if record.get("status") in {"verified", "merged"}:
                gaps.append({"id": task_id, "kind": "evidence_missing", "message": "verified without a manifest"})
            continue
        review = _load_yaml(directory / "review.yaml")
        review = review if isinstance(review, dict) else {}
        evidence_id = f"{task_id}#evidence"
        node(
            evidence_id,
            "evidence",
            f"attempt {manifest.get('attempt')} · {'passed' if manifest.get('passed') else 'failed'}",
            passed=manifest.get("passed"),
            commit=(manifest.get("source_commit") or "")[:10],
            holdout=bool(manifest.get("holdout_authorship")),
            stale=bool(stale),
            review_decision=review.get("decision"),
            reviewer_model=review.get("reviewer_model"),
            implementer_model=review.get("implementer_model"),
        )
        edges.append({"from": task_id, "to": evidence_id, "kind": "evidenced_by"})
        if stale:
            gaps.append(
                {
                    "id": task_id,
                    "kind": "evidence_stale",
                    "message": f"evidence pinned to an older version of {', '.join(stale)}",
                }
            )
    counts: dict[str, int] = {}
    for gap in gaps:
        counts[gap["kind"]] = counts.get(gap["kind"], 0) + 1
    return {
        "present": True,
        "nodes": nodes,
        "edges": edges,
        "gaps": gaps,
        "gap_counts": counts,
        "spec_diff": change or None,
    }, []


# --------------------------------------------------------------------------- #
# audit events
# --------------------------------------------------------------------------- #
def _event_view(event: dict[str, Any]) -> dict[str, Any]:
    view = {
        "sequence": event.get("sequence"),
        "at": event.get("at"),
        "entity_type": event.get("entity_type"),
        "entity_id": event.get("entity_id"),
        "from": event.get("from"),
        "to": event.get("to"),
        "event": event.get("event") or ("transition" if "to" in event else None),
        "actor": event.get("actor"),
    }
    details = event.get("details")
    if isinstance(details, dict):
        view["details"] = sanitize_value(details, limit=400)
    return view


def events_page(store: ProjectStore, params: dict[str, Any] | None = None) -> Result:
    params = params or {}
    events = store.read_audit()
    intent_id = str(params.get("intent_id") or "")
    if intent_id:
        events = _audit_for(events, intent_id)
    try:
        limit = max(1, min(int(params.get("limit", 200)), 1000))
        before = int(params["cursor"]) if params.get("cursor") else len(events)
    except (TypeError, ValueError):
        raise BridgeError("invalid_params", "limit and cursor must be integers") from None
    start = max(0, before - limit)
    page = [_event_view(event) for event in events[start:before]][::-1]
    omissions = [Omission("events", "paged", count=start)] if start else []
    return {"events": page, "matched_total": len(events), "next_cursor": str(start) if start else None}, omissions


# --------------------------------------------------------------------------- #
# story mode
# --------------------------------------------------------------------------- #
CLAIMS = {
    "register": "The runner allocates the permanent identifier; nobody picks or predicts it.",
    "frame": "The problem, outcome, and success measures are agreed before anyone specifies a solution.",
    "specify": "A requirement nobody can test is caught before anyone builds against it.",
    "plan": "Every Task traces to an approved requirement and declares what it will touch.",
    "build": "Evidence is captured by the runner, pinned to a commit, and reviewed by a different model family.",
    "converge": "The whole system is rechecked: every requirement has verified, current evidence.",
    "close": "Findings become enforced rules for the next Intent, not prose in a retrospective.",
}


def _stage_of(event: dict[str, Any], intent_id: str) -> str:
    entity = str(event.get("entity_id", ""))
    if entity.startswith(f"{intent_id}:"):
        gate = entity.split(":", 1)[1]
        return {"G1": "frame", "G2": "specify", "G3": "plan", "G4": "build", "G5": "converge"}.get(gate, "build")
    if entity.startswith(f"{intent_id}-"):
        return "build"
    target = event.get("to") or (event.get("details") or {}).get("target")
    return {
        "initialized": "register",
        "framed": "frame",
        "specified": "specify",
        "planned": "plan",
        "building": "build",
        "converging": "converge",
        "awaiting_acceptance": "converge",
        "completed": "close",
        "aborted": "close",
    }.get(str(target), "register")


def _artifact(root: Path, relative: str, provenance: str, note: str) -> dict[str, Any] | None:
    path = root / relative
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": relative,
        "provenance": provenance,
        "note": note,
        "content": sanitize_text(text, limit=MAX_ARTIFACT_CHARS),
        "truncated": len(text) > MAX_ARTIFACT_CHARS,
    }


def story(store: ProjectStore, intent_id: str, params: dict[str, Any] | None = None) -> Result:
    from aisdlc.evidence import requirement_hashes

    root = _intent_root(store, intent_id)
    state = _state(store)
    events = _audit_for(store.read_audit(), intent_id)
    summary = _intent_summary(store, intent_id, root, state, store.read_audit())
    by_stage: dict[str, list[dict[str, Any]]] = {stage["id"]: [] for stage in STAGES}
    timeline = []
    for index, event in enumerate(events):
        stage = _stage_of(event, intent_id)
        view = {**_event_view(event), "stage": stage, "index": index}
        by_stage[stage].append(view)
        timeline.append(view)

    # Can the current specification be shown as "the approved one"? Only when its
    # per-requirement hashes still equal the snapshot taken at G2 approval.
    snapshot_path = store.root / ".ai-sdlc" / "runtime" / "gate-snapshots" / f"{intent_id}-G2.json"
    spec_provenance, spec_note = "current", "Current file. No approval snapshot was recorded for comparison."
    if snapshot_path.is_file():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            same = snapshot.get("requirement_hashes") == requirement_hashes(root / "02-specification")
        except (OSError, ValueError):
            same = False
        spec_provenance = "verified_as_approved" if same else "changed_since_approval"
        spec_note = (
            f"Identical to what G2 approved (revision {snapshot.get('gate_revision')}), verified by hash."
            if same
            else "This file has changed since G2 was approved; the approved text was not stored."
        )
    review_g2 = _load_yaml(store.root / ".ai-sdlc" / "runtime" / "reviews" / f"{intent_id}-G2.json")
    inherited = _load_yaml(root / "02-specification" / "inherited-controls.yaml")
    tasks = _tasks(root)
    task_reviews = []
    for directory, task in tasks:
        review = _load_yaml(directory / "review.yaml")
        manifest = _latest_manifest(directory)
        if isinstance(review, dict) or manifest:
            task_reviews.append(
                {
                    "task": task["id"],
                    "decision": (review or {}).get("decision") if isinstance(review, dict) else None,
                    "reviewer_model": (review or {}).get("reviewer_model") if isinstance(review, dict) else None,
                    "implementer_model": (review or {}).get("implementer_model") if isinstance(review, dict) else None,
                    "passed": manifest.get("passed") if manifest else None,
                    "attempt": manifest.get("attempt") if manifest else None,
                    "commit": (manifest.get("source_commit") or "")[:10] if manifest else None,
                }
            )

    def enforcement(stage: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for event in by_stage[stage]:
            if event.get("event") == "advance_refused":
                details = event.get("details") or {}
                items.append(
                    {
                        "kind": "refused",
                        "at": event.get("at"),
                        "text": f"Runner refused to advance to {details.get('target')} "
                        f"({details.get('check')}): {details.get('count')} problem(s)",
                        "reasons": details.get("reasons") or [],
                    }
                )
            if ":" in str(event.get("entity_id", "")):  # a gate record, already grouped by stage
                if event.get("to") == "approved":
                    items.append(
                        {
                            "kind": "gate_approved",
                            "at": event.get("at"),
                            "text": f"{event.get('entity_id')} approved by {event.get('actor')}",
                        }
                    )
                elif event.get("to") == "reopened":
                    items.append(
                        {
                            "kind": "gate_reopened",
                            "at": event.get("at"),
                            "text": f"{event.get('entity_id')} reopened: {(event.get('details') or {}).get('reason')}",
                        }
                    )
        if stage == "specify":
            if isinstance(review_g2, dict):
                items.append(
                    {
                        "kind": "adversarial_review",
                        "text": f"Adversarial review by {review_g2.get('reviewer_model')}: "
                        f"{review_g2.get('decision')} with {len(review_g2.get('findings') or [])} "
                        "finding(s)",
                    }
                )
            if isinstance(inherited, dict):
                suites = len(inherited.get("standing_suites") or [])
                lessons = len(inherited.get("lessons") or [])
                if suites or lessons:
                    items.append(
                        {"kind": "inherited", "text": f"Inherited {suites} standing suite(s) and {lessons} lesson(s)"}
                    )
        if stage == "build":
            for review in task_reviews:
                if review["decision"]:
                    items.append(
                        {
                            "kind": "independent_review",
                            "text": f"{review['task']}: {review['decision']} by {review['reviewer_model']} "
                            f"(implementer {review['implementer_model']})",
                        }
                    )
            for event in by_stage["build"]:
                if event.get("to") in {"retry", "blocked", "failed"}:
                    items.append(
                        {
                            "kind": "task_failed",
                            "at": event.get("at"),
                            "text": f"{event.get('entity_id')} moved to {event.get('to')}",
                        }
                    )
        return items

    artifacts = {
        "register": _artifact(root, "00-intent/intent.yaml", "current", "Registration record."),
        "frame": _artifact(root, "01-brief/brief.md", "current", "Current brief. Briefs are not snapshotted at G1."),
        "specify": _artifact(root, "02-specification/requirements.yaml", spec_provenance, spec_note),
        "plan": _artifact(root, "03-planning/master-plan.md", "current", "Current plan.")
        or _artifact(root, "03-planning/plan.md", "current", "Current plan."),
        "build": None,
        "converge": _artifact(root, "05-verification/convergence-report.md", "current", "Convergence report."),
        "close": _artifact(root, "06-close/completion-report.md", "current", "Completion record."),
    }
    chapters = []
    for stage in STAGES:
        entries = by_stage[stage["id"]]
        chapters.append(
            {
                "id": stage["id"],
                "title": stage["title"],
                "gate": stage["gate"],
                "claim": CLAIMS[stage["id"]],
                "reached": bool(entries) or stage["id"] == "register",
                "started_at": entries[0]["at"] if entries else None,
                "ended_at": entries[-1]["at"] if entries else None,
                "event_count": len(entries),
                "enforcement": enforcement(stage["id"]),
                "artifact": artifacts[stage["id"]],
                "tasks": task_reviews if stage["id"] == "build" else None,
            }
        )
    return {
        "intent": {key: summary[key] for key in ("id", "title", "status", "mode", "type")},
        "chapters": chapters,
        "timeline": timeline,
        "complete": summary["status"] == "completed",
    }, []


# --------------------------------------------------------------------------- #
# authoring: compose a requirement (read-only; the write is `artifact_write`)
# --------------------------------------------------------------------------- #
SPEC_SKELETON = """intent_id: {intent_id}
functional_requirements: []
non_functional_requirements: []
acceptance_criteria: []
success_criteria: []
"""
MAX_COMPOSE_CHARS = 20_000


def compose_requirement(store: ProjectStore, intent_id: str, params: dict[str, Any] | None = None) -> Result:
    """The specification with one more requirement and its criteria, and the ids they get.

    Pure: nothing is written. The UI shows the result through `artifact_write`'s preview,
    so the diff, the lint of the proposed file and the confirmation are the same as for
    any other edit.
    """
    import hashlib as _hashlib

    from aisdlc.authoring import AuthoringError, add_requirement

    params = params or {}
    requirement, criteria = params.get("requirement"), params.get("criteria")
    kind = str(params.get("kind") or "functional")
    if (
        not isinstance(requirement, dict)
        or not isinstance(criteria, list)
        or not all(isinstance(item, dict) for item in criteria)
    ):
        raise BridgeError("invalid_params", "requirement must be a mapping and criteria a list of mappings")
    if len(json.dumps([requirement, criteria], default=str)) > MAX_COMPOSE_CHARS:
        raise BridgeError("invalid_params", "the requirement is too large to compose")
    root = _intent_root(store, intent_id)
    relative = "02-specification/requirements.yaml"
    path = root / relative
    text = path.read_text(encoding="utf-8") if path.is_file() else SPEC_SKELETON.format(intent_id=intent_id)
    snapshot_path = store.root / ".ai-sdlc" / "runtime" / "gate-snapshots" / f"{intent_id}-G2.json"
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.is_file() else None
    except (OSError, ValueError):
        snapshot = None
    try:
        content, ids = add_requirement(text, requirement, criteria, kind=kind, snapshot=snapshot)
    except AuthoringError as error:
        raise BridgeError("invalid_params", str(error)) from None
    return {
        "path": relative,
        "content": content,
        "expected_sha": _hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "new",
        "ids": ids,
    }, []


# --------------------------------------------------------------------------- #
# overview: "what we're building" on one page, with coverage and suspect links
# --------------------------------------------------------------------------- #
DONE_TASKS = {"verified", "merged"}


def _brief_sections(path: Path) -> dict[str, str]:
    """`## Heading` → text, from a brief written from the template."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    sections: dict[str, str] = {}
    heading = None
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            heading = match.group(1).strip().lower()
            sections[heading] = ""
        elif heading is not None:
            sections[heading] += line + "\n"
    return {key: sanitize_text(value.strip(), limit=1_500) for key, value in sections.items() if value.strip()}


def _links(store: ProjectStore, intent_id: str, root: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Requirement → criterion → Task → evidence, with every link that may no longer hold.

    Two kinds of suspect link, both read from records the runner already keeps, and each
    cleared by an action the lifecycle already has (no second review state to maintain):

    * evidence: a Task's latest evidence pinned an older version of a requirement it
      proves. Cleared by verifying the Task again (convergence refuses it until then).
    * plan: a requirement changed since G2 was approved, so the Tasks planned against it
      may no longer cover it. Cleared by approving G2 again after checking the plan.
    """
    from aisdlc.evidence import requirement_hashes
    from aisdlc.specdiff import diff as spec_diff

    document = _requirements(root) or {}
    criteria = {
        str(item.get("id")): item for item in document.get("acceptance_criteria") or [] if isinstance(item, dict)
    }
    current = requirement_hashes(root / "02-specification")
    try:
        moved_since_g2 = spec_diff(store.root, root, intent_id)
    except Exception:  # noqa: BLE001 - a missing or unreadable baseline means "no baseline"
        moved_since_g2 = None
    moved = set()
    if moved_since_g2:
        moved = set(moved_since_g2["requirements"]["changed"]) | set(moved_since_g2["requirements"]["removed"])
    tasks = []
    for directory, task in _tasks(root):
        manifest = _latest_manifest(directory) or {}
        pinned = (manifest.get("specification") or {}).get("requirement_hashes") or {}
        tasks.append(
            {
                "id": task["id"],
                "title": task.get("title"),
                "status": state.get("tasks", {}).get(task["id"], {}).get("status") or "not registered",
                "requirements": [str(item) for item in task.get("requirements") or []],
                "affected_paths": [str(item) for item in task.get("affected_paths") or []],
                "pinned": pinned if isinstance(pinned, dict) else {},
            }
        )
    rows = []
    for requirement in _requirement_records(document):
        rid = str(requirement.get("id"))
        mapped = [task for task in tasks if rid in task["requirements"]]
        suspect = []
        for task in mapped:
            if rid in task["pinned"] and task["pinned"][rid] != current.get(rid):
                suspect.append(
                    {
                        "kind": "evidence",
                        "task": task["id"],
                        "reason": f"{task['id']}'s evidence proves an older version of {rid}",
                        "clear": f"verify {task['id']} again (`ai-sdlc start-task {task['id']}`, then `verify-task`)",
                    }
                )
        if rid in moved and mapped:
            suspect.append(
                {
                    "kind": "plan",
                    "task": None,
                    "reason": f"{rid} changed since G2 was approved; the planned Tasks may not cover it",
                    "clear": "check the plan covers the change, then approve G2 again",
                }
            )
        owned = [criteria[key] for key in requirement.get("acceptance_criteria") or [] if key in criteria]
        rows.append(
            {
                "id": rid,
                "priority": requirement.get("priority"),
                "category": requirement.get("category"),
                "statement": requirement.get("statement"),
                "sources": requirement.get("sources") or [],
                "criteria": [
                    {"id": item.get("id"), "class": item.get("verification_class"), "statement": item.get("statement")}
                    for item in owned
                ],
                "tasks": [{"id": task["id"], "status": task["status"]} for task in mapped],
                "proven": bool(mapped)
                and all(task["status"] in DONE_TASKS for task in mapped)
                and not any(item["kind"] == "evidence" for item in suspect),
                "suspect": suspect,
            }
        )
    return {"rows": rows, "tasks": tasks, "moved_since_g2": sorted(moved)}


def overview_inspect(store: ProjectStore, intent_id: str, params: dict[str, Any] | None = None) -> Result:
    """One page, assembled from the files: the problem, what done means, the scope, what
    it touches, the plan, how much of it is proven, and what may no longer hold."""
    from aisdlc.contractregistry import ContractRegistry

    root = _intent_root(store, intent_id)
    state = _state(store)
    summary = _intent_summary(store, intent_id, root, state, store.read_audit())
    document = _requirements(root) or {}
    links = _links(store, intent_id, root, state)
    rows = links["rows"]
    scoped = [row for row in rows if row["priority"] != "wont"]
    threat_model = _load_yaml(root / "02-specification" / "threat-model.yaml")
    threats = threat_model.get("threats") or [] if isinstance(threat_model, dict) else []
    dispositions: dict[str, int] = {}
    for threat in threats:
        if isinstance(threat, dict):
            key = str(threat.get("disposition"))
            dispositions[key] = dispositions.get(key, 0) + 1
    registry = ContractRegistry(store.root)
    return {
        "intent": {
            key: summary[key]
            for key in (
                "id",
                "title",
                "type",
                "mode",
                "status",
                "working_stage",
                "waiting_on",
                "open_findings",
                "lint",
                "tasks",
            )
        },
        "brief": _brief_sections(root / "01-brief" / "brief.md"),
        "requirements": rows,
        "success_criteria": [
            {
                "id": item.get("id"),
                "statement": item.get("statement"),
                "brief_measure": item.get("brief_measure"),
                "criteria": item.get("acceptance_criteria") or [],
            }
            for item in document.get("success_criteria") or []
            if isinstance(item, dict)
        ],
        "coverage": {
            "requirements": len(scoped),
            "with_criterion": sum(1 for row in scoped if row["criteria"]),
            "with_task": sum(1 for row in scoped if row["tasks"]),
            "proven": sum(1 for row in scoped if row["proven"]),
            "suspect": sum(1 for row in rows if row["suspect"]),
        },
        "touches": sorted({path for task in links["tasks"] for path in task["affected_paths"]}),
        "contracts": [
            {
                "path": item.get("path"),
                "api": item.get("api"),
                "breaking_changes_accepted": len(item.get("breaking_changes_accepted") or []),
            }
            for item in document.get("contracts") or []
            if isinstance(item, dict)
        ],
        "contention": registry.contention(intent_id),
        "threats": {
            "total": len(threats),
            "by_disposition": dispositions,
            "inherited": sorted(
                {str(t.get("inherited_from")) for t in threats if isinstance(t, dict) and t.get("inherited_from")}
            ),
        },
        "plan": [
            {"id": task["id"], "title": task["title"], "status": task["status"], "requirements": task["requirements"]}
            for task in links["tasks"]
        ],
        "moved_since_g2": links["moved_since_g2"],
    }, []


# --------------------------------------------------------------------------- #
# inbox: everything waiting on a person, oldest first
# --------------------------------------------------------------------------- #
def inbox_inspect(store: ProjectStore, params: dict[str, Any] | None = None) -> Result:
    """Gates, contention to decide, blocked Tasks, open serious findings, specifications the
    linter blocks, and suspect links: each with who it waits on and what clears it."""
    from aisdlc.specquality import lint_intent

    state = _state(store)
    events = store.read_audit()
    items: list[dict[str, Any]] = []
    seen_contention: set[tuple[str, ...]] = set()
    from aisdlc.contractregistry import ContractRegistry

    registry = ContractRegistry(store.root)
    for intent_id, root in sorted(intent_roots(store).items()):
        summary = _intent_summary(store, intent_id, root, state, events)
        title = summary["title"]
        if summary["waiting_on"]:
            gate = summary["waiting_on"]["gate"]
            record = summary["gates"].get(gate) or {}
            text = f"{gate} waits for a person's decision"
            if record.get("needed", 1) > 1:
                have = record.get("approvals") or []
                text = f"{gate} needs {record['needed']} different approvers; " + (
                    f"{len(have)} so far ({', '.join(have)})" if have else "none yet"
                )
            items.append(
                {
                    "kind": "gate",
                    "intent": intent_id,
                    "title": title,
                    "since": summary["waiting_on"]["since"],
                    "gate": gate,
                    "summary": text,
                }
            )
        for item in registry.contention(intent_id):
            pair = tuple(sorted((intent_id, str(item.get("intent")))) + [str(item.get("resource"))])
            if pair in seen_contention:
                continue
            seen_contention.add(pair)
            items.append(
                {
                    "kind": "contention",
                    "intent": intent_id,
                    "title": title,
                    "since": None,
                    "resource": item.get("resource"),
                    "intents": [intent_id, item.get("intent")],
                    "summary": f"{intent_id} and {item.get('intent')} both change {item.get('resource')}",
                }
            )
        for task_id, record in sorted(state.get("tasks", {}).items()):
            if task_id.startswith(f"{intent_id}-") and record.get("status") == "blocked":
                last = _last_entry(events, task_id, "blocked")
                items.append(
                    {
                        "kind": "blocked",
                        "intent": intent_id,
                        "title": title,
                        "task": task_id,
                        "since": last.get("at") if last else None,
                        "summary": f"{task_id} is blocked after exhausting its retries",
                    }
                )
        high = summary["open_findings"]["critical"] + summary["open_findings"]["high"]
        if high:
            items.append(
                {
                    "kind": "finding",
                    "intent": intent_id,
                    "title": title,
                    "since": None,
                    "summary": f"{high} open critical or high finding(s) block convergence",
                }
            )
        if summary["status"] in {"framed", "specified"} and (root / "02-specification" / "requirements.yaml").is_file():
            try:
                lint = lint_intent(root, store.context.configuration.get("specification_fail_on", "error"))
            except Exception:  # noqa: BLE001 - a lint failure is reported by the spec view, not here
                lint = {"passed": True}
            if not lint.get("passed", True):
                items.append(
                    {
                        "kind": "lint",
                        "intent": intent_id,
                        "title": title,
                        "since": None,
                        "summary": f"{len(lint.get('blocking') or [])} spec lint error(s) block G2",
                    }
                )
        if summary["status"] not in {"completed", "aborted", None}:
            suspect = [row for row in _links(store, intent_id, root, state)["rows"] if row["suspect"]]
            if suspect:
                items.append(
                    {
                        "kind": "suspect",
                        "intent": intent_id,
                        "title": title,
                        "since": None,
                        "requirements": [row["id"] for row in suspect],
                        "summary": f"{len(suspect)} requirement link(s) may no longer hold",
                    }
                )
    order = {"gate": 0, "contention": 1, "blocked": 2, "finding": 3, "lint": 4, "suspect": 5}
    items.sort(key=lambda item: (order[item["kind"]], item["since"] or "~", item["intent"]))
    return {"items": items, "counts": {kind: sum(1 for item in items if item["kind"] == kind) for kind in order}}, []


# --------------------------------------------------------------------------- #
# learn: the framework explained, and where each Intent is on it
# --------------------------------------------------------------------------- #
def learn_inspect(store: ProjectStore, params: dict[str, Any] | None = None) -> Result:
    """The pipeline, vocabulary and lint catalogue from `framework-guide.yaml`, plus the
    open project's Intents placed on the stage they are working in."""
    from aisdlc.assets import AssetError, asset_path, framework_root

    omissions: list[Omission] = []
    try:
        guide = _load_yaml(asset_path(store.root, "framework-guide.yaml")) or {}
    except (AssetError, OSError) as error:
        guide = {}
        omissions.append(Omission("guide", f"framework-guide.yaml unavailable: {sanitize_text(error)}"))
    folders = {stage["id"]: stage for stage in STAGES}
    stages = [
        {**item, "status": folders.get(item.get("id"), {}).get("status")}
        for item in guide.get("stages") or []
        if isinstance(item, dict)
    ]
    state = _state(store)
    events = store.read_audit()
    intents = []
    for intent_id, root in sorted(intent_roots(store).items()):
        summary = _intent_summary(store, intent_id, root, state, events)
        intents.append({key: summary[key] for key in ("id", "title", "status", "mode", "working_stage", "waiting_on")})
    try:
        templates = sorted(path.name for path in (framework_root() / "templates").iterdir() if path.is_file())
    except (AssetError, OSError):
        templates = []
    return {
        "stages": stages,
        "gates": guide.get("gates") or {},
        "terms": guide.get("terms") or [],
        "lint_rules": guide.get("lint_rules") or [],
        "templates": templates,
        "intents": intents,
        "facts": {
            "intents": len(intents),
            "completed": sum(1 for item in intents if item["status"] == "completed"),
            "approved_gates": sum(
                1 for record in state.get("gates", {}).values() if record.get("status") == "approved"
            ),
            "waiting": sum(1 for item in intents if item["waiting_on"]),
        },
    }, omissions


# --------------------------------------------------------------------------- #
# artifact: one file, read-only
# --------------------------------------------------------------------------- #
MAX_READ_BYTES = 256 * 1024
#: Paths inside an Intent that only the runner writes, with what writes them.
RUNNER_WRITTEN = (
    (re.compile(r"(^|/)evidence/"), "evidence captured by `verify-task`"),
    (re.compile(r"(^|/)review\.yaml$"), "review recorded by `review-task`"),
    (re.compile(r"^traceability\.json$"), "generated by `trace` and `converge`"),
    (re.compile(r"^02-specification/inherited-controls\.yaml$"), "written by `advance specified`"),
    (re.compile(r"^03-planning/decisions/"), "recorded by `decision record`"),
    (re.compile(r"^05-verification/(convergence-report\.md|evals/)"), "written by `converge` and `eval`"),
    (re.compile(r"^06-close/completion-report\.md$"), "written by `close`"),
)
LANGUAGES = {".yaml": "yaml", ".yml": "yaml", ".json": "json", ".md": "markdown"}


def artifact_read(store: ProjectStore, params: dict[str, Any] | None = None) -> Result:
    """Read one file for the viewer. Never writes, and never follows a path out of its scope."""
    from aisdlc.assets import AssetError, framework_root

    params = params or {}
    scope = str(params.get("scope") or "intent")
    relative = str(params.get("path") or "").replace("\\", "/").strip()
    if not relative or relative.startswith("/") or ":" in relative or ".." in relative.split("/"):
        raise BridgeError("invalid_params", "path must be relative to its scope and stay inside it")
    intent_id = str(params.get("intent_id") or "")
    if scope == "intent":
        if not intent_id:
            raise BridgeError("invalid_params", "intent_id is required for an Intent file")
        base = _intent_root(store, intent_id)
    elif scope in {"template", "schema"}:
        try:
            base = framework_root() / ("templates" if scope == "template" else "schemas")
        except AssetError as error:
            raise BridgeError("unavailable", sanitize_text(error)) from None
        local = store.root / ".ai-sdlc" / base.name
        if (local / relative).is_file():  # the repository's own copy wins, as it does for the runner
            base = local
    else:
        raise BridgeError("invalid_params", "scope must be intent, template, or schema")
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()) or not target.is_file():
        raise BridgeError("not_found", f"no such file in this {scope}: {relative}")
    data = target.read_bytes()
    text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    owner, produced_by, locked = "you", None, None
    editable, edit_refusal = False, "framework files are copied into an Intent, never edited in place"
    if scope == "intent":
        from aisdlc.authoring import AuthoringError, check_writable

        try:
            check_writable(_state(store), intent_id, relative)
            editable, edit_refusal = True, None
        except AuthoringError as error:  # the runner's own rule, so Studio never guesses
            edit_refusal = str(error)
        for pattern, reason in RUNNER_WRITTEN:
            if pattern.search(relative):
                owner, produced_by = "runner", reason
                break
        if relative.startswith("02-specification/") and owner == "you":
            gate = _gate_view(_state(store), store.read_audit(), intent_id)["G2"]
            if gate.get("status") == "approved":
                locked = {
                    "gate": "G2",
                    "approver": gate.get("approver"),
                    "at": gate.get("at"),
                    "command": f'ai-sdlc gate reopen {intent_id} G2 --actor <you> --reason "<what changed>"',
                }
    else:
        owner = "framework"
    return {
        "scope": scope,
        "intent_id": intent_id or None,
        "path": relative,
        "language": LANGUAGES.get(target.suffix.lower(), "text"),
        "text": text,
        "bytes": len(data),
        "lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
        "truncated": len(data) > MAX_READ_BYTES,
        "sha256": hashlib.sha256(data).hexdigest(),
        "owner": owner,
        "produced_by": produced_by,
        "locked": locked,
        "editable": editable,
        "edit_refusal": edit_refusal,
    }, []


def artifact_check(store: ProjectStore, intent_id: str, params: dict[str, Any] | None = None) -> Result:
    """What the runner would say about a draft of one authored file, line by line. Never writes.

    The editor calls this as a person types. The draft is checked in a temporary copy of the
    Intent by the same validation and lint `artifact write` runs; the answer also carries the
    identifiers the specification defines and the keys the file's schema allows, for
    completion and links.
    """
    from aisdlc.authoring import is_authored
    from aisdlc.desktop.drafts import MAX_DRAFT_BYTES, diagnose

    params = params or {}
    relative = str(params.get("path") or "").replace("\\", "/").strip()
    content = params.get("content")
    if not relative or relative.startswith("/") or ":" in relative or ".." in relative.split("/"):
        raise BridgeError("invalid_params", "path must be relative to the Intent and stay inside it")
    if not is_authored(relative):
        raise BridgeError("invalid_params", f"{relative} is not a file a person authors")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_DRAFT_BYTES:
        raise BridgeError("invalid_params", "content must be text of at most 256 KiB")
    status = _state(store).get("intents", {}).get(intent_id, {}).get("status")
    report = diagnose(store, _intent_root(store, intent_id), relative, content, status)
    return sanitize_value(report, limit=200_000), []


# --------------------------------------------------------------------------- #
# diagnostics
# --------------------------------------------------------------------------- #
def diagnostics(store: ProjectStore, params: dict[str, Any] | None = None) -> Result:
    import platform
    import sys

    from aisdlc.scaffold import DoctorService
    from aisdlc.versioning import version_consistency

    try:
        doctor = DoctorService(store.root).run()
    except Exception as error:  # noqa: BLE001
        doctor = {"status": "fail", "checks": [], "error": sanitize_text(error)}
    return {
        "context": store.context.to_dict(),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "doctor": sanitize_value(doctor),
        "version_consistency": version_consistency(Path(store.context.runner["module"]).parents[1]),
        "fingerprint_truncated": store.fingerprint_truncated,
    }, []
