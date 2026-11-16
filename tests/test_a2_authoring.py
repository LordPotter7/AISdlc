"""Wave A2: authoring through the runner. A developer's files are written only if they are
authored artifacts, not locked by a gate, part of an open Intent, and unchanged since read."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

from aisdlc.authoring import (
    AuthoringError,
    add_requirement,
    check_writable,
    is_authored,
    specification_lock,
    write_artifact,
)
from aisdlc.cli import app
from aisdlc.state import StateStore
from aisdlc.typerlite import CliRunner
from aisdlc.vendor import yaml

runner = CliRunner()

SPEC = """intent_id: I001
# Written with the product owner on 2026-09-20.
functional_requirements:
  - id: REQ-F-001
    priority: must
    statement: The service shall log each login.  # keep this comment
    acceptance_criteria: [AC-001]
non_functional_requirements: []
acceptance_criteria:
  - id: AC-001
    verification_class: D
    statement: Login lines appear in the log.
    check: python -c pass

success_criteria:
  - id: SC-001
    statement: Logins are traceable.
    acceptance_criteria: [AC-001]
"""


def _state(**gates: str) -> dict:
    return {
        "intents": {"I001": {"status": "specified"}},
        "gates": {f"I001:{gate}": {"status": status} for gate, status in gates.items()},
    }


# -- what may be written ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "01-brief/brief.md",
        "02-specification/requirements.yaml",
        "02-specification/threat-model.yaml",
        "02-specification/contracts/orders.yaml",
        "03-planning/master-plan.md",
        "04-build/I001-P00/phase.yaml",
        "04-build/I001-P00-skeleton/tasks/I001-P00-T001/task.yaml",
        "05-verification/findings.yaml",
    ],
)
def test_authored_artifacts_are_writable(path: str) -> None:
    assert is_authored(path)
    check_writable(_state(), "I001", path)


@pytest.mark.parametrize(
    "path",
    [
        "00-intent/intent.yaml",
        "traceability.json",
        "02-specification/inherited-controls.yaml",
        "04-build/I001-P00/tasks/I001-P00-T001/review.yaml",
        "04-build/I001-P00/tasks/I001-P00-T001/evidence/attempt-1/manifest.json",
        "03-planning/decisions/I001-D001.yaml",
        "06-close/completion-report.md",
        "../../.ai-sdlc/config.yaml",
    ],
)
def test_runner_written_files_are_refused(path: str) -> None:
    with pytest.raises(AuthoringError, match="not an authored artifact"):
        check_writable(_state(), "I001", path)


def test_an_approved_specification_is_locked_with_the_way_out() -> None:
    with pytest.raises(AuthoringError, match=r"ai-sdlc gate reopen I001 G2"):
        check_writable(_state(G2="approved"), "I001", "02-specification/requirements.yaml")
    check_writable(_state(G2="reopened"), "I001", "02-specification/requirements.yaml")
    check_writable(_state(G2="approved"), "I001", "03-planning/master-plan.md")
    assert specification_lock(_state(G2="approved"), "I001", "01-brief/brief.md") is None


def test_a_closed_intent_no_longer_changes() -> None:
    state = {"intents": {"I001": {"status": "completed"}}, "gates": {}}
    with pytest.raises(AuthoringError, match="completed"):
        check_writable(state, "I001", "01-brief/brief.md")


# -- how it is written ---------------------------------------------------------------------------


def test_write_refuses_a_file_that_moved_since_it_was_read(tmp_path: Path) -> None:
    target = tmp_path / "01-brief" / "brief.md"
    target.parent.mkdir()
    target.write_text("# Brief\n", encoding="utf-8")
    read_as = hashlib.sha256(target.read_bytes()).hexdigest()
    target.write_text("# Brief, edited in the editor meanwhile\n", encoding="utf-8")
    with pytest.raises(AuthoringError, match="changed since it was read"):
        write_artifact(tmp_path, "01-brief/brief.md", "# Mine\n", read_as)
    assert "meanwhile" in target.read_text(encoding="utf-8")


def test_write_creates_only_with_new_and_rejects_unparseable_yaml(tmp_path: Path) -> None:
    result = write_artifact(tmp_path, "05-verification/findings.yaml", "findings: []\n", "new")
    assert result.created and result.before is None
    with pytest.raises(AuthoringError, match="already exists"):
        write_artifact(tmp_path, "05-verification/findings.yaml", "findings: []\n", "new")
    with pytest.raises(AuthoringError, match="not valid YAML"):
        write_artifact(tmp_path, "05-verification/findings.yaml", "findings: [\n", result.after)
    assert not list(tmp_path.rglob("*.aisdlc-tmp"))


# -- composing a requirement -----------------------------------------------------------------------


def test_add_requirement_keeps_comments_and_allocates_the_next_ids() -> None:
    text, ids = add_requirement(
        SPEC,
        {"priority": "should", "statement": "When a login fails, the service shall record the reason."},
        [{"verification_class": "D", "statement": "Failure reasons are logged.", "check": "python -c pass"}],
    )
    assert ids == {"requirement": "REQ-F-002", "criteria": ["AC-002"]}
    assert "# Written with the product owner" in text and "# keep this comment" in text
    document = yaml.safe_load(text)
    added = document["functional_requirements"][-1]
    assert added["id"] == "REQ-F-002" and added["acceptance_criteria"] == ["AC-002"]
    assert document["acceptance_criteria"][-1]["id"] == "AC-002"
    assert [item["id"] for item in document["success_criteria"]] == ["SC-001"]  # later sections untouched
    # Inserted at the list's own indentation, before the blank line that separates sections.
    assert "\n  - id: REQ-F-002\n" in text and "check: python -c pass\n\nsuccess_criteria:" in text


def test_add_requirement_never_reuses_an_approved_then_removed_id() -> None:
    snapshot = {"requirements": {"REQ-F-001": "...", "REQ-F-007": "removed after approval"}, "criteria": ["AC-009"]}
    _, ids = add_requirement(
        SPEC,
        {"statement": "The service shall rotate logs."},
        [{"verification_class": "D", "statement": "Rotated.", "check": "true"}],
        snapshot=snapshot,
    )
    assert ids == {"requirement": "REQ-F-008", "criteria": ["AC-010"]}


def test_add_non_functional_requirement_fills_an_empty_list() -> None:
    text, ids = add_requirement(
        SPEC,
        {
            "category": "performance",
            "statement": "Login shall complete within 300 ms at p95.",
            "measure": {"metric": "p95 latency", "target": 300, "unit": "ms"},
        },
        [{"verification_class": "D", "statement": "Benchmark passes.", "check": "true"}],
        kind="non_functional",
    )
    assert ids["requirement"] == "REQ-NFR-001"
    assert yaml.safe_load(text)["non_functional_requirements"][0]["measure"]["target"] == 300


def test_add_requirement_needs_a_statement_and_a_criterion() -> None:
    with pytest.raises(AuthoringError, match="statement"):
        add_requirement(SPEC, {"statement": " "}, [{"verification_class": "D"}])
    with pytest.raises(AuthoringError, match="criterion"):
        add_requirement(SPEC, {"statement": "The service shall log."}, [])


# -- the CLI -----------------------------------------------------------------------------------------


def _intent(tmp_path: Path) -> Path:
    created = runner.invoke(
        app,
        [
            "new",
            "--title",
            "Login log",
            "--type",
            "feature",
            "--mode",
            "compact",
            "--discovery-reason",
            "fixture",
            "--repo",
            str(tmp_path),
        ],
    )
    assert created.exit_code == 0, created.output
    return Path(json.loads(created.output)["path"])


def test_artifact_write_through_stdin_is_audited_and_reports_lint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _intent(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(SPEC.replace("log each login", "log each login fast")))
    result = runner.invoke(
        app,
        [
            "artifact",
            "write",
            "I001",
            "02-specification/requirements.yaml",
            "--from",
            "-",
            "--expected-sha",
            "new",
            "--actor",
            "dana",
            "--repo",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["created"] is True
    assert any(item["rule"] == "SQ002" and item["source"]["line"] for item in payload["lint"]["findings"])
    assert (root / "02-specification" / "requirements.yaml").read_text(encoding="utf-8").startswith("intent_id")
    events = [json.loads(line) for line in (tmp_path / ".ai-sdlc/runtime/audit.jsonl").read_text("utf-8").splitlines()]
    written = [event for event in events if event.get("event") == "artifact_written"]
    assert written and written[-1]["details"]["path"] == "02-specification/requirements.yaml", events[-2:]
    assert written[-1]["actor"] == "dana" and written[-1]["details"]["created"] is True


def test_artifact_write_refuses_a_locked_specification(tmp_path: Path) -> None:
    root = _intent(tmp_path)
    source = tmp_path / "spec.yaml"
    source.write_text(SPEC, encoding="utf-8")
    (root / "02-specification").mkdir(parents=True, exist_ok=True)
    (root / "02-specification" / "requirements.yaml").write_text(SPEC, encoding="utf-8")
    store = StateStore(tmp_path)
    store.transition_intent("I001", "framed", actor="t")
    store.approve_gate("I001", "G1", "human")
    store.transition_intent("I001", "specified", actor="t")
    store.approve_gate("I001", "G2", "human")
    sha = hashlib.sha256((root / "02-specification" / "requirements.yaml").read_bytes()).hexdigest()
    refused = runner.invoke(
        app,
        [
            "artifact",
            "write",
            "I001",
            "02-specification/requirements.yaml",
            "--from",
            str(source),
            "--expected-sha",
            sha,
            "--repo",
            str(tmp_path),
        ],
    )
    assert refused.exit_code != 0
    assert "gate reopen I001 G2" in refused.output


# -- the Studio path: compose → preview (diff + the runner's own checks) → execute ----------------

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aisdlc.desktop.bridge import Bridge  # noqa: E402
from tools.make_demo_repo import generate  # noqa: E402


@pytest.fixture(scope="module")
def demo_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate(tmp_path_factory.mktemp("authoring") / "orders-service", force=False)


@pytest.fixture()
def demo(demo_source: Path, tmp_path: Path) -> Path:
    import shutil

    target = tmp_path / "orders-service"
    shutil.copytree(demo_source, target)
    return target


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

    def write(self, **args):  # noqa: ANN201
        preview = self.payload("operation.preview", name="artifact_write", args=args)
        result = self.payload(
            "operation.execute",
            name="artifact_write",
            args=args,
            token=preview["token"],
            base_hashes=preview["base_hashes"],
        )
        return preview, result


REQUIREMENT = {
    "priority": "should",
    "statement": "When support searches by order number, the service shall return that order first.",
}
CRITERION = [
    {
        "verification_class": "D",
        "statement": "Order-number search ranks the order first.",
        "check": "pytest -k order_number",
    }
]


def test_compose_preview_and_save_a_requirement_through_the_runner(demo: Path) -> None:
    session = Session(demo)
    spec = next((demo / "delivery/intents").glob("I002-*")) / "02-specification/requirements.yaml"
    before = spec.read_bytes()
    composed = session.payload("authoring.compose", intent_id="I002", requirement=REQUIREMENT, criteria=CRITERION)
    assert spec.read_bytes() == before  # composing is a read
    assert composed["ids"]["requirement"] == "REQ-F-004"  # I002 already has REQ-F-001..003
    args = {
        "intent_id": "I002",
        "path": composed["path"],
        "content": composed["content"],
        "expected_sha": composed["expected_sha"],
    }
    preview = session.payload("operation.preview", name="artifact_write", args=args)
    draft = preview["draft"]
    assert draft["refusal"] is None and draft["changed"] is True
    assert "+- id: REQ-F-004" in draft["diff"] or "+  - id: REQ-F-004" in draft["diff"]
    assert draft["checks"]["lint"] is not None and spec.read_bytes() == before  # the preview wrote nothing
    assert preview["command"].startswith("ai-sdlc artifact write I002 02-specification/requirements.yaml --from -")
    result = session.payload(
        "operation.execute",
        name="artifact_write",
        args=args,
        token=preview["token"],
        base_hashes=preview["base_hashes"],
    )
    assert result["succeeded"] is True, result
    assert "REQ-F-004" in spec.read_text(encoding="utf-8")
    assert result["output"]["lint"]["counts"] == draft["checks"]["lint"]["counts"]  # the preview told the truth


def test_a_locked_specification_is_refused_in_the_preview_and_by_the_runner(demo: Path) -> None:
    session = Session(demo)
    composed = session.payload("authoring.compose", intent_id="I003", requirement=REQUIREMENT, criteria=CRITERION)
    preview, result = session.write(
        intent_id="I003", path=composed["path"], content=composed["content"], expected_sha=composed["expected_sha"]
    )
    assert "gate reopen I003 G2" in preview["draft"]["refusal"]
    assert result["succeeded"] is False and "gate reopen I003 G2" in (result["error_text"] or "")


def test_a_file_edited_after_the_preview_is_not_overwritten(demo: Path) -> None:
    session = Session(demo)
    brief = next((demo / "delivery/intents").glob("I004-*")) / "01-brief/brief.md"
    read = session.payload("artifact.read", intent_id="I004", path="01-brief/brief.md")
    args = {
        "intent_id": "I004",
        "path": "01-brief/brief.md",
        "content": read["text"] + "\n## Out of scope\n\n- SMS\n",
        "expected_sha": read["sha256"],
    }
    preview = session.payload("operation.preview", name="artifact_write", args=args)
    brief.write_text(brief.read_text(encoding="utf-8") + "\nEdited in the editor meanwhile.\n", encoding="utf-8")
    reply = session.call(
        "operation.execute",
        name="artifact_write",
        args=args,
        token=preview["token"],
        base_hashes=preview["base_hashes"],
    )
    assert reply["error"]["code"] == "base_moved"
    assert "meanwhile" in brief.read_text(encoding="utf-8")


def test_start_an_intent_from_studio(demo: Path) -> None:
    session = Session(demo)
    args = {
        "title": "Refund status page",
        "intent_type": "feature",
        "mode": "compact",
        "discovery_reason": "small, well-understood change",
        "contains_model_behavior": False,
    }
    preview = session.payload("operation.preview", name="intent_new", args=args)
    assert preview["command"] == (
        'ai-sdlc new --title "Refund status page" --type feature --mode compact '
        '--discovery-reason "small, well-understood change"'
    )
    result = session.payload(
        "operation.execute", name="intent_new", args=args, token=preview["token"], base_hashes=preview["base_hashes"]
    )
    assert result["succeeded"] is True and result["output"]["intent_id"] == "I005"


@pytest.mark.parametrize(
    ("name", "args"),
    [
        (
            "artifact_write",
            {"intent_id": "I002", "path": "../I001-x/01-brief/brief.md", "content": "x", "expected_sha": "new"},
        ),
        ("artifact_write", {"intent_id": "I002", "path": "01-brief/brief.md", "content": "x", "expected_sha": "abc"}),
        (
            "artifact_write",
            {
                "intent_id": "I002",
                "path": "01-brief/brief.md",
                "content": "x" * (256 * 1024 + 1),
                "expected_sha": "new",
            },
        ),
        ("intent_new", {"title": "Refund", "intent_type": "rewrite", "mode": "compact", "discovery_reason": "x"}),
        ("intent_new", {"title": "Refund", "intent_type": "feature", "mode": "compact", "discovery_reason": "a\nb"}),
    ],
)
def test_write_arguments_are_validated_before_anything_runs(demo: Path, name: str, args: dict) -> None:
    assert Session(demo).call("operation.preview", name=name, args=args)["error"]["code"] == "invalid_params"


def test_composed_keys_read_in_the_order_a_person_writes_them() -> None:
    # The native host delivers JSON objects with sorted keys; the file must not read alphabetically.
    text, _ = add_requirement(
        SPEC,
        {"sources": ["brief"], "statement": "The service shall rotate logs.", "priority": "must"},
        [{"check": "true", "statement": "Rotated.", "verification_class": "D"}],
    )
    added = text[text.index("REQ-F-002") - 6 :]
    assert added.index("priority") < added.index("sources") < added.index("statement")
    criterion = text[text.index("- id: AC-002") :]
    assert criterion.index("verification_class") < criterion.index("statement") < criterion.index("check")
