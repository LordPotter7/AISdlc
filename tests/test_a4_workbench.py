"""Wave A4: the workbench checks a draft as a person types. `artifact.check` runs the runner's
own validation and lint on a copy, places each problem on its line, and never writes."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aisdlc.desktop.bridge import Bridge  # noqa: E402
from tools.make_demo_repo import generate  # noqa: E402

SPEC = "02-specification/requirements.yaml"


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate(tmp_path_factory.mktemp("workbench") / "orders-service", force=False)


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


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "delivery").rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return digest.hexdigest()


def test_check_places_a_syntax_error_on_its_line_and_writes_nothing(demo: Path) -> None:
    session = Session(demo)
    text = session.payload("artifact.read", intent_id="I002", path=SPEC)["text"]
    before = _tree_hash(demo)
    lines = text.splitlines()
    lines.insert(3, "  - id: [unclosed")
    report = session.payload("artifact.check", intent_id="I002", path=SPEC, content="\n".join(lines) + "\n")
    assert _tree_hash(demo) == before
    assert report["passed"] is False
    first = report["diagnostics"][0]
    assert first["source"] == "syntax" and first["severity"] == "error"
    assert first["line"] in {4, 5}  # the parser may stop on the line after the bad token
    assert report["identifiers"], "identifiers still come from the file on disk while the draft does not parse"


def test_check_puts_lint_on_the_requirement_line_and_offers_ids_and_keys(demo: Path) -> None:
    session = Session(demo)
    text = session.payload("artifact.read", intent_id="I002", path=SPEC)["text"]
    report = session.payload("artifact.check", intent_id="I002", path=SPEC, content=text)
    ids = {item["id"]: item for item in report["identifiers"]}
    assert "REQ-F-001" in ids and ids["REQ-F-001"]["file"] == SPEC and ids["REQ-F-001"]["line"] >= 1
    assert ids["REQ-F-001"]["label"], "each identifier carries what it says, for hover and completion"
    keys = {item["key"]: item for item in report["keys"]}
    assert {"statement", "acceptance_criteria", "verification_class"} <= set(keys)
    assert keys["verification_class"]["values"] == ["D", "J", "N", "S"]

    # An ambiguous word in REQ-F-001 is flagged by the runner's lint on REQ-F-001's line.
    edited = text.replace("statement: ", "statement: The service shall respond quickly and ", 1)
    report = session.payload("artifact.check", intent_id="I002", path=SPEC, content=edited)
    lint = [d for d in report["diagnostics"] if d["source"] == "lint" and d["line"] is not None]
    assert lint, report["diagnostics"]
    first_requirement_line = min(
        item["line"] for item in report["identifiers"] if item["id"].startswith("REQ-") and item["file"] == SPEC
    )
    assert any(d["line"] >= first_requirement_line for d in lint)


def test_check_is_only_for_files_a_person_authors(demo: Path) -> None:
    session = Session(demo)
    for path in ("00-intent/intent.yaml", "../../.ai-sdlc/config.yaml", "traceability.json"):
        reply = session.call("artifact.check", intent_id="I002", path=path, content="x: 1\n")
        assert reply["error"]["code"] == "invalid_params", path
    reply = session.call("artifact.check", intent_id="I002", path=SPEC, content="x" * (256 * 1024 + 1))
    assert reply["error"]["code"] == "invalid_params"


def test_the_save_preview_and_the_live_check_agree(demo: Path) -> None:
    session = Session(demo)
    read = session.payload("artifact.read", intent_id="I002", path=SPEC)
    edited = read["text"].replace("statement: ", "statement: The service shall respond quickly and ", 1)
    live = session.payload("artifact.check", intent_id="I002", path=SPEC, content=edited)
    preview = session.payload(
        "operation.preview",
        name="artifact_write",
        args={"intent_id": "I002", "path": SPEC, "content": edited, "expected_sha": read["sha256"]},
    )
    counted = {
        severity: sum(1 for d in live["diagnostics"] if d["source"] == "lint" and d["severity"] == severity)
        for severity in ("info", "warning", "error")
    }
    assert counted == preview["draft"]["checks"]["lint"]["counts"]


def test_a_schema_error_lands_on_its_key_and_other_files_are_not_blamed(demo: Path) -> None:
    session = Session(demo)
    text = session.payload("artifact.read", intent_id="I002", path=SPEC)["text"]
    assert "verification_class: D" in text
    edited = text.replace("verification_class: D", "verification_class: X", 1)
    report = session.payload("artifact.check", intent_id="I002", path=SPEC, content=edited)
    bad = [d for d in report["diagnostics"] if d["source"] == "validation" and "verification_class" in d["message"]]
    assert bad, report["diagnostics"]
    line = edited.splitlines().index(next(text for text in edited.splitlines() if "verification_class: X" in text)) + 1
    assert bad[0]["file"] == SPEC and bad[0]["line"] == line
    assert report["passed"] is False
    assert "aisdlc-draft-" not in " ".join(d["message"] for d in report["diagnostics"])

    # The brief is fine; the specification's problems are reported, but never as the brief's.
    brief = session.payload("artifact.read", intent_id="I001", path="01-brief/brief.md")["text"]
    report = session.payload("artifact.check", intent_id="I001", path="01-brief/brief.md", content=brief)
    assert all(d["file"] != "01-brief/brief.md" for d in report["diagnostics"])
    assert report["passed"] is True
