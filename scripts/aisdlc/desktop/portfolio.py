"""Many Intents in one repository, for Studio: programmes and their order, what is in
flight against the build limit, priorities, overlaps, and proof across the portfolio.

Read only. Programme membership and waivers come from the runner's programme registry;
priorities are the latest `priority_set` event per Intent in the audit log; proof is the
same requirement → criterion → Task → evidence walk each Intent's own page uses, so the
portfolio can never count something as proven that its Intent does not. The runner's
`program status` roll-up is deliberately not called: it regenerates `traceability.json`,
and a projection must not write.
"""

from __future__ import annotations

from typing import Any

from aisdlc.desktop.protocol import Omission, sanitize_value
from aisdlc.desktop.store import ProjectStore

Result = tuple[dict[str, Any], list[Omission]]
CLOSED = {"completed", "aborted"}
RANK = {"high": 0, "normal": 1, "low": 2}


def _priorities(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event") == "priority_set" and isinstance(event.get("details"), dict):
            latest[str(event.get("entity_id"))] = {
                "priority": event["details"].get("priority"),
                "by": event.get("actor"),
                "reason": event["details"].get("reason"),
                "at": event.get("at"),
            }
    return latest


def _title(root: Any) -> str | None:
    from aisdlc.desktop.projections import _load_yaml

    record = _load_yaml(root / "00-intent" / "intent.yaml") if root is not None else None
    return str(record.get("title")) if isinstance(record, dict) and record.get("title") else None


def portfolio_inspect(store: ProjectStore, params: dict[str, Any] | None = None) -> Result:
    from aisdlc.config import load_config
    from aisdlc.desktop.codebase import codebase_inspect
    from aisdlc.desktop.projections import _links, _state, intent_roots
    from aisdlc.programs import ProgramRegistry

    omissions: list[Omission] = []
    state = _state(store)
    records = state.get("intents", {})
    roots = intent_roots(store)
    events = store.read_audit()
    priorities = _priorities(events)
    registry = ProgramRegistry(store.root)
    programs_raw = registry.read().get("programs", {}) or {}
    try:
        limit = load_config(store.root).scale.max_building
    except Exception:  # noqa: BLE001 - an unreadable config shows as "no limit", never as a crash
        limit = None
    try:
        codebase, _ = codebase_inspect(store)
        footprint = codebase.get("footprint", {})
        overlaps = [
            {"module": m["path"], "intents": [i["intent"] for i in m["intents"] if i["active"]]}
            for m in codebase.get("modules", [])
            if m.get("contended")
        ]
    except Exception as error:  # noqa: BLE001 - the portfolio stands without the codebase map
        footprint, overlaps = {}, []
        omissions.append(Omission("codebase", str(error)[:200]))

    membership: dict[str, str] = {}
    for program_id, program in programs_raw.items():
        for member in program.get("members", []):
            membership.setdefault(str(member), str(program_id))

    intents = []
    proven_total = scoped_total = 0
    for intent_id in sorted(set(roots) | set(records), key=lambda item: int(item[1:])):
        record = records.get(intent_id, {})
        status = record.get("status")
        proof = {"requirements": 0, "proven": 0, "suspect": 0}
        root = roots.get(intent_id)
        if root is not None:
            try:
                rows = _links(store, intent_id, root, state)["rows"]
                scoped = [row for row in rows if row["priority"] != "wont"]
                proof = {
                    "requirements": len(scoped),
                    "proven": sum(1 for row in scoped if row["proven"]),
                    "suspect": sum(1 for row in rows if row["suspect"]),
                }
            except Exception:  # noqa: BLE001 - an Intent with no specification yet has nothing to prove
                pass
        if status not in {"aborted"}:
            proven_total += proof["proven"]
            scoped_total += proof["requirements"]
        intents.append(
            {
                "id": intent_id,
                "title": _title(root) or intent_id,
                "status": status,
                "active": status not in CLOSED,
                "priority": priorities.get(intent_id),
                "program": membership.get(intent_id),
                "blocked_by": registry.blocking_predecessors(intent_id) if status not in CLOSED else [],
                "proof": proof,
                "modules": footprint.get(intent_id, {}).get("modules", []),
                "shared_with": footprint.get(intent_id, {}).get("shared_with", []),
            }
        )
    by_id = {item["id"]: item for item in intents}

    programs = []
    for program_id, program in sorted(programs_raw.items()):
        members = [str(member) for member in program.get("members", [])]
        edges = []
        for item in program.get("dependencies", []):
            before, after = str(item.get("requires")), str(item.get("intent"))
            edges.append(
                {
                    "from": before,
                    "to": after,
                    "waived": bool(item.get("waived")),
                    "waived_by": item.get("waived_by"),
                    "waiver_reason": item.get("waiver_reason"),
                    "satisfied": records.get(before, {}).get("status") == "completed",
                }
            )
        member_rows = [by_id[m] for m in members if m in by_id]
        programs.append(
            {
                "id": program_id,
                "title": program.get("title"),
                "owner": program.get("owner"),
                "members": members,
                "edges": edges,
                "closed": sum(1 for row in member_rows if row["status"] == "completed"),
                "proof": {
                    "requirements": sum(row["proof"]["requirements"] for row in member_rows),
                    "proven": sum(row["proof"]["proven"] for row in member_rows),
                },
            }
        )

    building = sorted(item["id"] for item in intents if item["status"] == "building")
    ready = sorted(item["id"] for item in intents if item["status"] == "planned")
    return sanitize_value(
        {
            "intents": sorted(
                intents,
                key=lambda item: (
                    not item["active"],
                    RANK.get((item["priority"] or {}).get("priority"), 1),
                    int(item["id"][1:]),
                ),
            ),
            "programs": programs,
            "wip": {
                "building": building,
                "ready": ready,
                "limit": limit,
                "full": limit is not None and len(building) >= limit,
            },
            "proof": {
                "requirements": scoped_total,
                "proven": proven_total,
                "intents": sum(1 for item in intents if item["proof"]["requirements"]),
            },
            "overlaps": overlaps,
        },
        limit=4000,
    ), omissions
