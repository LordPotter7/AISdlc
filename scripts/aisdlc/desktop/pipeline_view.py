"""The effective pipeline, for Studio (ADR-010). Read only: `pipeline.yaml` is changed in an
editor and reviewed like any other governance file; this shows what it says, what it
loosens and by whose waiver, and which Intents run under which profile."""

from __future__ import annotations

from typing import Any

from aisdlc.desktop.protocol import Omission, sanitize_value
from aisdlc.desktop.store import ProjectStore

Result = tuple[dict[str, Any], list[Omission]]
CLOSED = {"completed", "aborted"}


def pipeline_inspect(store: ProjectStore, params: dict[str, Any] | None = None) -> Result:
    from aisdlc.desktop.projections import _load_yaml, _state, intent_roots
    from aisdlc.pipeline import PipelineError, describe, pinned, weaker_than

    report = describe(store.root)
    records = _state(store).get("intents", {})
    intents = []
    for intent_id, root in sorted(intent_roots(store).items(), key=lambda item: int(item[0][1:])):
        document = _load_yaml(root / "00-intent" / "intent.yaml")
        document = document if isinstance(document, dict) else {}
        try:
            rules = pinned(root, store.root)
        except PipelineError:
            continue
        status = records.get(intent_id, {}).get("status")
        stricter = []
        if status not in CLOSED and not report["error"]:
            stricter = sorted(
                name
                for name, candidate in report["profiles"].items()
                if name != rules.get("name")
                and document.get("artifact_mode") in candidate["modes"]
                and not weaker_than(rules, candidate)
            )
        intents.append(
            {
                "id": intent_id,
                "title": document.get("title") or intent_id,
                "type": document.get("intent_type"),
                "status": status,
                "profile": rules.get("name"),
                "implicit": bool(rules.get("implicit")),
                "loosened": [item.get("rule") for item in rules.get("loosened") or []],
                "stricter": stricter,
            }
        )
    counts: dict[str, int] = {}
    for item in intents:
        counts[item["profile"]] = counts.get(item["profile"], 0) + 1
    return sanitize_value({**report, "intents": intents, "pinned_counts": counts}, limit=4000), []
