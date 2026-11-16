"""Record AISDLC Studio UI fixtures from the real bridge over the demo repository.

    python tools/record_studio_fixtures.py

Generates the demo repository (`tools/make_demo_repo.py`), opens it in the desktop
bridge, and saves every envelope the UI needs to `desktop/src/fixtures/demo.json`. The
browser preview and the frontend tests replay these envelopes, so what they show is real
runner output rather than hand-written sample data. Local paths are replaced with a
neutral placeholder before writing.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
sys.path.insert(0, str(REPOSITORY))

from aisdlc.authoring import is_authored  # noqa: E402
from aisdlc.desktop.bridge import Bridge  # noqa: E402
from tools.make_demo_repo import generate  # noqa: E402

OUTPUT = REPOSITORY / "desktop" / "src" / "fixtures" / "demo.json"
PLACEHOLDER = "C:\\demo\\orders-service"
REACHED = {
    "framed": ("G1",),
    "specified": ("G1", "G2"),
    "planned": ("G1", "G2", "G3"),
    "building": ("G1", "G2", "G3", "G4"),
    "converging": ("G1", "G2", "G3", "G4"),
    "awaiting_acceptance": ("G1", "G2", "G3", "G4", "G5"),
    "completed": ("G1", "G2", "G3", "G4", "G5"),
}


def _scrub(value: object, replacements: list[tuple[str, str]]) -> object:
    """Replace local paths in every string, including escaped (repr) forms."""
    if isinstance(value, str):
        for local, neutral in replacements:
            value = value.replace(local, neutral)
        return value
    if isinstance(value, list):
        return [_scrub(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _scrub(item, replacements) for key, item in value.items()}
    return value


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aisdlc-fixtures-") as scratch:
        root = generate(Path(scratch) / "orders-service", force=False)
        bridge = Bridge()

        def call(op: str, **params: object) -> dict:
            reply = bridge.handle({"id": op, "op": op, "params": params})
            if reply["error"]:
                raise SystemExit(f"{op} {params}: {reply['error']}")
            return reply

        opened = call("project.open", root=str(root))
        project_id = opened["project_id"]
        record: dict = {
            "hello": call("hello"),
            "open": opened,
            "snapshot": call("project.snapshot", project_id=project_id),
            "events": call("events.page", project_id=project_id, limit=200),
            "diagnostics": call("diagnostics.inspect", project_id=project_id),
            "learn": call("learn.inspect", project_id=project_id),
            "inbox": call("inbox.inspect", project_id=project_id),
            "codebase": call("codebase.inspect", project_id=project_id),
            "portfolio": call("portfolio.inspect", project_id=project_id),
            "pipeline": call("pipeline.inspect", project_id=project_id),
            "intents": {},
            "framework_files": {},
        }
        # Every template and schema the guide links to, so the viewer works in the preview.
        for stage in record["learn"]["payload"]["stages"]:
            for item in stage["authored"]:
                for scope in ("template", "schema"):
                    name = item.get(scope)
                    if name and f"{scope}:{name}" not in record["framework_files"]:
                        record["framework_files"][f"{scope}:{name}"] = call(
                            "artifact.read", project_id=project_id, scope=scope, path=name
                        )
        for intent in record["snapshot"]["payload"]["intents"]:
            intent_id = intent["id"]
            entry = {
                "intent": call("intent.inspect", project_id=project_id, intent_id=intent_id),
                "spec": call("spec.inspect", project_id=project_id, intent_id=intent_id),
                "trace": call("trace.inspect", project_id=project_id, intent_id=intent_id),
                "story": call("story.inspect", project_id=project_id, intent_id=intent_id),
                "overview": call("overview.inspect", project_id=project_id, intent_id=intent_id),
                "events": call("events.page", project_id=project_id, intent_id=intent_id, limit=200),
                "gates": {},
                "files": {},
                "checks": {},
            }
            # The files a developer opens: everything the runner does not capture as evidence.
            for paths in entry["intent"]["payload"]["artifacts"].values():
                for path in paths:
                    if "/evidence/" not in f"/{path}":
                        entry["files"][path] = call(
                            "artifact.read", project_id=project_id, intent_id=intent_id, path=path
                        )
                        # What the workbench underlines in each file a person authors, as it is on disk.
                        if is_authored(path):
                            entry["checks"][path] = call(
                                "artifact.check",
                                project_id=project_id,
                                intent_id=intent_id,
                                path=path,
                                content=entry["files"][path]["payload"]["text"],
                            )
            # Only the gates the Intent has reached; later packets would report missing artifacts.
            for gate in REACHED.get(intent["status"], ()):
                entry["gates"][gate] = call("gate.packet", project_id=project_id, intent_id=intent_id, gate=gate)
            record["intents"][intent_id] = entry
        replacements = []
        plugin_root = str(Path(__file__).resolve().parents[1])
        for local, neutral in (
            (plugin_root, r"C:\aisdlc-plugin"),
            (str(root), PLACEHOLDER),
            (scratch, r"C:\demo"),
            (str(Path.home()), r"C:\Users\demo"),
        ):
            for form in (local, local.replace("\\", "\\\\"), local.replace("\\", "/")):
                replacements.append((form, neutral if "/" not in form else neutral.replace("\\", "/")))
        record = _scrub(record, replacements)
        text = json.dumps(record, indent=1, ensure_ascii=False)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"{OUTPUT} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
