"""The repository as a codebase, for Studio: what it contains, how far adoption has come,
and which Intents change which modules.

Everything here is read. The inventory is `aisdlc.inventory.scan` over the files git tracks,
recomputed for each commit and never stored; adoption progress is derived from what exists
(config, the recorded understanding, baselines, Intents); the module map joins the
inventory to each Task's declared `affected_paths`. Nothing in this projection can make a
suggestion binding: a proposed probe still has to be copied into a probes file by a person.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from aisdlc.desktop.protocol import BridgeError, Omission, sanitize_value
from aisdlc.desktop.store import ProjectStore

Result = tuple[dict[str, Any], list[Omission]]
CLOSED = {"completed", "aborted"}
_SCANS: dict[tuple[str, str | None], dict[str, Any]] = {}


def _head(root: Path) -> str | None:
    completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True)
    return completed.stdout.strip() or None if completed.returncode == 0 else None


def inventory(store: ProjectStore) -> dict[str, Any]:
    """The scan for HEAD, computed once per commit: the same commit always gives the same answer."""
    from aisdlc.inventory import InventoryError, scan

    key = (str(store.root), _head(store.root))
    if key not in _SCANS:
        try:
            _SCANS[key] = scan(store.root)
        except InventoryError as error:
            raise BridgeError("unavailable", str(error)) from None
        while len(_SCANS) > 8:
            _SCANS.pop(next(iter(_SCANS)))
    return _SCANS[key]


def _baselines(root: Path) -> list[dict[str, Any]]:
    folder = root / ".ai-sdlc" / "baselines"
    try:
        index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = []
    for entry in index.get("baselines") or []:
        try:
            record = json.loads((folder / entry["path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        found.append(
            {
                "id": record.get("id"),
                "label": record.get("label"),
                "captured_at": record.get("captured_at"),
                "commit": record.get("source_commit"),
                "probes": [
                    {
                        "name": probe.get("name"),
                        "command": probe.get("command"),
                        "exit_code": probe.get("exit_code"),
                        "value": probe.get("value"),
                    }
                    for probe in record.get("probes") or []
                ],
            }
        )
    return found


def _understanding(root: Path) -> dict[str, Any]:
    from aisdlc.config import load_config
    from aisdlc.understanding import UnderstandingError, UnderstandingService

    try:
        threshold = load_config(root).understanding.drift_threshold
    except Exception:  # noqa: BLE001 - an unmanaged repository still has a codebase to show
        threshold = 0.34
    try:
        status = UnderstandingService(root).status(drift_threshold=threshold)
    except UnderstandingError as error:
        return {"status": "invalid", "detail": str(error)}
    return {
        key: status.get(key)
        for key in (
            "status",
            "source",
            "pinned_commit",
            "head_commit",
            "recorded_at",
            "anchored_paths",
            "changed_paths",
            "drift_ratio",
            "moved_claims",
        )
    }


def codebase_inspect(store: ProjectStore, params: dict[str, Any] | None = None) -> Result:
    """The codebase map: inventory, adoption steps, and the Intents that change each module."""
    from aisdlc.desktop.projections import _state, _tasks, intent_roots
    from aisdlc.inventory import module_of

    scan = inventory(store)
    state = _state(store)
    managed = (store.root / ".ai-sdlc" / "config.yaml").is_file()
    understanding = _understanding(store.root) if managed else {"status": "absent"}
    baselines = _baselines(store.root) if managed else []
    intents = state.get("intents", {}) if managed else {}

    touching: dict[str, list[dict[str, Any]]] = {}
    by_intent: dict[str, list[dict[str, Any]]] = {}
    unmapped: list[dict[str, Any]] = []
    for intent_id, root in (intent_roots(store) if managed else {}).items():
        record = intents.get(intent_id, {})
        status = record.get("status")
        for _, task in _tasks(root):
            for path in task.get("affected_paths") or []:
                path = str(path).replace("\\", "/").strip("/")
                module = module_of(path, scan["modules"])
                entry = {
                    "intent": intent_id,
                    "title": record.get("title"),
                    "status": status,
                    "task": task.get("id"),
                    "path": path,
                    "active": status not in CLOSED,
                }
                if module is None:
                    unmapped.append(entry)
                    continue
                touching.setdefault(module, []).append(entry)
                by_intent.setdefault(intent_id, []).append({**entry, "module": module})

    modules = []
    owners_by_module = {module["path"]: module["owners"] for module in scan["modules"]}
    for module in scan["modules"]:
        entries = touching.get(module["path"], [])
        grouped: dict[str, dict[str, Any]] = {}
        for entry in entries:
            item = grouped.setdefault(
                entry["intent"],
                {
                    "intent": entry["intent"],
                    "title": entry["title"],
                    "status": entry["status"],
                    "active": entry["active"],
                    "tasks": [],
                    "paths": [],
                },
            )
            if entry["task"] not in item["tasks"]:
                item["tasks"].append(entry["task"])
            if entry["path"] not in item["paths"]:
                item["paths"].append(entry["path"])
        active = [item for item in grouped.values() if item["active"]]
        modules.append({**module, "intents": list(grouped.values()), "contended": len(active) > 1})

    footprint = {}
    for intent_id, entries in by_intent.items():
        names = sorted({entry["module"] for entry in entries})
        footprint[intent_id] = {
            "modules": names,
            "owners": sorted({owner for name in names for owner in owners_by_module.get(name, [])}),
            "shared_with": sorted(
                {
                    other["intent"]
                    for name in names
                    for other in touching.get(name, [])
                    if other["intent"] != intent_id and other["active"]
                }
            ),
        }

    steps = [
        {
            "id": "init",
            "title": "Run AISDLC in this repository",
            "done": managed,
            "detail": "Adds .ai-sdlc/ with the configuration, schemas and templates the runner reads."
            if not managed
            else "The runner manages this repository.",
            "command": "ai-sdlc init --mode full",
            "handoff": "/aisdlc:init",
        },
        {
            "id": "understand",
            "title": "Record what the system is",
            "done": understanding.get("status") in {"fresh", "drifting"},
            "state": understanding.get("status"),
            "detail": {
                "absent": "No understanding yet. An agent drafts it from this scan, anchored to code; you read it.",
                "stale": "The code it describes has moved on. Draft it again.",
                "drifting": "Some described code has changed since it was pinned.",
                "fresh": "Pinned to a commit, and the code it describes has not moved.",
                "invalid": understanding.get("detail", ""),
            }.get(str(understanding.get("status")), ""),
            "command": "ai-sdlc understand record",
            "handoff": "/aisdlc:understand repo",
        },
        {
            "id": "baseline",
            "title": "Pin what it does today",
            "done": bool(baselines),
            "detail": (
                f"{len(baselines)} baseline(s) pin current behaviour; the first Intents cite them as "
                "`baseline_ref` so criteria read 'no regression'."
            )
            if baselines
            else (
                "Copy the proposed probes you accept into a probes file, then capture. "
                "The exit codes are recorded, not judged."
            ),
            "command": 'ai-sdlc baseline capture probes.yaml --label "before the first change"',
            "handoff": "/aisdlc:baseline",
        },
        {
            "id": "intent",
            "title": "Start the first Intent",
            "done": bool(intents),
            "detail": f"{len(intents)} Intent(s) so far."
            if intents
            else "Describe one change; the runner allocates its id.",
            "command": 'ai-sdlc new --title "<what changes>" --type feature --discovery '
            '--discovery-reason "understanding and baseline recorded"',
            "handoff": "/aisdlc:start",
        },
    ]
    payload = {
        "managed": managed,
        "inventory": {key: value for key, value in scan.items() if key != "modules"},
        "modules": modules,
        "footprint": footprint,
        "unmapped": unmapped[:50],
        "adoption": {"steps": steps, "complete": all(step["done"] for step in steps)},
        "understanding": understanding,
        "baselines": baselines,
    }
    return sanitize_value(payload, limit=20_000), []
