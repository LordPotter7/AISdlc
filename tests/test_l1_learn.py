"""Wave L1: Studio for learning — the framework guide, working stages, source lines, and the
read-only file viewer. Everything here is read-only; the repository stays byte-identical."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

from aisdlc.desktop.bridge import HANDLERS, Bridge
from aisdlc.desktop.projections import GATES, STAGES, working_stage
from aisdlc.specquality import lint_intent, source_lines
from aisdlc.vendor import yaml

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
from tools.make_demo_repo import generate  # noqa: E402

GUIDE = yaml.safe_load((REPOSITORY / ".ai-sdlc" / "framework-guide.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate(tmp_path_factory.mktemp("learn") / "orders-service", force=False)


class Session:
    def __init__(self, root: Path) -> None:
        self.bridge = Bridge()
        self.project_id = self.bridge.handle({"id": "o", "op": "project.open", "params": {"root": str(root)}})[
            "project_id"
        ]

    def call(self, op: str, **params):  # noqa: ANN201
        return self.bridge.handle({"id": op, "op": op, "params": {"project_id": self.project_id, **params}})

    def payload(self, op: str, **params):  # noqa: ANN201
        reply = self.call(op, **params)
        assert reply["error"] is None, reply["error"]
        return reply["payload"]


def _digest(root: Path) -> str:
    hasher = hashlib.sha256()
    for base in (root / ".ai-sdlc", root / "delivery"):
        for path in sorted(base.rglob("*")):
            if path.is_file():
                hasher.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return hasher.hexdigest()


# -- the guide says only what the runner does --------------------------------------------------


def test_guide_stages_and_gates_match_the_runner() -> None:
    assert [(item["id"], item["folder"], item["gate"]) for item in GUIDE["stages"]] == [
        (stage["id"], stage["folder"], stage["gate"]) for stage in STAGES
    ]
    assert sorted(GUIDE["gates"]) == list(GATES)


def test_guide_catalogues_every_lint_rule_and_no_other() -> None:
    source = (REPOSITORY / "scripts" / "aisdlc" / "specquality.py").read_text(encoding="utf-8")
    assert {rule["code"] for rule in GUIDE["lint_rules"]} == set(re.findall(r'"(SQ\d{3})"', source))


def test_guide_terms_have_unique_ids_and_name_real_templates() -> None:
    ids = [term["id"] for term in GUIDE["terms"]]
    assert len(ids) == len(set(ids))
    templates = {path.name for path in (REPOSITORY / ".ai-sdlc" / "templates").iterdir()}
    schemas = {path.name for path in (REPOSITORY / ".ai-sdlc" / "schemas").iterdir()}
    for stage in GUIDE["stages"]:
        for item in stage["authored"]:
            assert item.get("template") in templates | {None}, item
            assert item.get("schema") in schemas | {None}, item


def test_rust_allowlist_matches_the_bridge_handlers() -> None:
    frame = (REPOSITORY / "desktop" / "src-tauri" / "src" / "frame.rs").read_text(encoding="utf-8")
    block = frame[frame.index("ALLOWED_OPS") : frame.index("];", frame.index("ALLOWED_OPS"))]
    assert set(re.findall(r'"([a-z.]+)"', block)) == set(HANDLERS)


# -- working stage -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "approved", "stage"),
    [
        ("initialized", set(), "frame"),
        ("framed", set(), "frame"),  # brief written, waiting on G1
        ("framed", {"G1"}, "specify"),  # G1 approved: writing the spec
        ("specified", {"G1", "G2"}, "plan"),
        ("planned", {"G1", "G2"}, "plan"),  # plan written, waiting on G3
        ("building", {"G1", "G2", "G3", "G4"}, "build"),
        ("awaiting_acceptance", {"G1", "G2", "G3", "G4"}, "converge"),
        ("completed", set(GATES), None),
    ],
)
def test_working_stage_is_the_stage_being_worked_on(status: str, approved: set[str], stage: str | None) -> None:
    gates = {gate: {"status": "approved" if gate in approved else "undecided"} for gate in GATES}
    assert working_stage(status, gates) == stage


def test_rail_marks_the_working_stage_current(demo: Path) -> None:
    session = Session(demo)
    rail = {item["id"]: item["state"] for item in session.payload("intent.inspect", intent_id="I003")["rail"]}
    assert rail["build"] == "current" and rail["plan"] == "done" and rail["converge"] == "future"
    completed = session.payload("intent.inspect", intent_id="I001")["rail"]
    assert {item["state"] for item in completed} == {"done"}


# -- source lines ----------------------------------------------------------------------------------


def test_every_requirement_and_finding_points_at_its_line(demo: Path) -> None:
    root = next((demo / "delivery" / "intents").glob("I001-*"))
    lines = source_lines(root)
    text = (root / "02-specification" / "requirements.yaml").read_text(encoding="utf-8").splitlines()
    for identifier in ("REQ-F-001", "AC-001", "SC-001"):
        location = lines[identifier]
        assert location["file"] == "02-specification/requirements.yaml"
        assert identifier in text[location["line"] - 1]
    spec = Session(demo).payload("spec.inspect", intent_id="I003")
    assert all(item["source"]["line"] for item in spec["requirements"] + spec["criteria"])
    assert all(item["source"]["file"].endswith("threat-model.yaml") for item in spec["threat_model"]["threats"])
    findings = lint_intent(next((demo / "delivery" / "intents").glob("I003-*")))["findings"]
    assert any(item["source"] and item["source"]["file"].endswith("threat-model.yaml") for item in findings)


# -- learn.inspect -----------------------------------------------------------------------------------


def test_learn_places_each_intent_on_its_working_stage(demo: Path) -> None:
    learn = Session(demo).payload("learn.inspect")
    assert [stage["id"] for stage in learn["stages"]] == [stage["id"] for stage in STAGES]
    assert {rule["code"] for rule in learn["lint_rules"]} >= {"SQ001", "SQ018"}
    assert "findings-template.yaml" in learn["templates"]
    placed = {item["id"]: item["working_stage"] for item in learn["intents"]}
    assert placed == {"I001": None, "I002": "specify", "I003": "build", "I004": "frame"}
    assert learn["facts"]["completed"] == 1 and learn["facts"]["waiting"] >= 1


# -- artifact.read -------------------------------------------------------------------------------------


def test_artifact_read_shows_owner_and_lock_and_changes_nothing(demo: Path) -> None:
    session = Session(demo)
    before = _digest(demo)
    spec = session.payload("artifact.read", intent_id="I001", path="02-specification/requirements.yaml")
    assert spec["language"] == "yaml" and "REQ-F-001" in spec["text"] and spec["owner"] == "you"
    assert spec["locked"]["gate"] == "G2" and "gate reopen I001 G2" in spec["locked"]["command"]
    assert (
        spec["sha256"]
        == hashlib.sha256(
            (next((demo / "delivery/intents").glob("I001-*")) / "02-specification/requirements.yaml").read_bytes()
        ).hexdigest()
    )
    brief = session.payload("artifact.read", intent_id="I004", path="01-brief/brief.md")
    assert brief["language"] == "markdown" and brief["locked"] is None
    reopened = session.payload("artifact.read", intent_id="I002", path="02-specification/requirements.yaml")
    assert reopened["locked"] is None  # G2 reopened: the spec is open for change
    evidence = next((demo / "delivery/intents").glob("I001-*")).rglob("manifest.json")
    relative = str(next(evidence).relative_to(next((demo / "delivery/intents").glob("I001-*")))).replace("\\", "/")
    manifest = session.payload("artifact.read", intent_id="I001", path=relative)
    assert manifest["owner"] == "runner" and "verify-task" in manifest["produced_by"]
    template = session.payload("artifact.read", scope="template", path="task-template.yaml")
    assert template["owner"] == "framework" and "{{" in template["text"]
    assert _digest(demo) == before


@pytest.mark.parametrize(
    "path",
    [
        "../I002-order-search/01-brief/brief.md",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "02-specification/../../../README.md",
        "",
    ],
)
def test_artifact_read_never_leaves_its_scope(demo: Path, path: str) -> None:
    reply = Session(demo).call("artifact.read", intent_id="I001", path=path)
    assert reply["error"]["code"] in {"invalid_params", "not_found"}


def test_artifact_read_refuses_unknown_scopes_and_missing_files(demo: Path) -> None:
    session = Session(demo)
    assert session.call("artifact.read", scope="runtime", path="state.json")["error"]["code"] == "invalid_params"
    assert session.call("artifact.read", intent_id="I001", path="nope.yaml")["error"]["code"] == "not_found"
    assert session.call("artifact.read", path="01-brief/brief.md")["error"]["code"] == "invalid_params"


def test_artifact_read_says_whether_the_runner_would_accept_an_edit(demo: Path) -> None:
    session = Session(demo)
    open_brief = session.payload("artifact.read", intent_id="I004", path="01-brief/brief.md")
    assert open_brief["editable"] is True and open_brief["edit_refusal"] is None
    locked = session.payload("artifact.read", intent_id="I003", path="02-specification/requirements.yaml")
    assert locked["editable"] is False and "gate reopen I003 G2" in locked["edit_refusal"]
    finished = session.payload("artifact.read", intent_id="I001", path="01-brief/brief.md")
    assert finished["editable"] is False and "completed" in finished["edit_refusal"]
    template = session.payload("artifact.read", scope="template", path="brief-template.md")
    assert template["editable"] is False
