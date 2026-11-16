"""AISDLC Studio desktop bridge: protocol, projections over a real lifecycle, operations.

The fixture is the demo repository produced by `tools/make_demo_repo.py`, which drives the
real runner through four Intents. Tests that mutate it work on a copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from aisdlc.desktop.bridge import Bridge
from aisdlc.typerlite import CliRunner
from aisdlc.vendor import yaml

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
from tools.make_demo_repo import generate  # noqa: E402

ENVELOPE_KEYS = {
    "protocol_version",
    "schema_version",
    "kind",
    "request_id",
    "project_id",
    "worktree_id",
    "generation",
    "collected_at",
    "source_fingerprint",
    "freshness",
    "omissions",
    "warnings",
    "cursor",
    "scope",
    "error",
    "payload",
}
READ_OPS = [
    ("project.snapshot", {}),
    ("intent.inspect", {"intent_id": "I001"}),
    ("intent.inspect", {"intent_id": "I003"}),
    ("gate.packet", {"intent_id": "I002", "gate": "G2"}),
    ("gate.packet", {"intent_id": "I001", "gate": "G5"}),
    ("spec.inspect", {"intent_id": "I003"}),
    ("trace.inspect", {"intent_id": "I001"}),
    ("trace.inspect", {"intent_id": "I002"}),
    ("story.inspect", {"intent_id": "I001"}),
    ("events.page", {"limit": 50}),
    ("diagnostics.inspect", {}),
]


def _writable(function, path, _error):  # noqa: ANN001 - shutil callback signature
    os.chmod(path, stat.S_IWRITE)
    function(path)


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate(tmp_path_factory.mktemp("studio") / "orders-service", force=False)


@pytest.fixture()
def demo_copy(demo: Path, tmp_path: Path) -> Path:
    target = tmp_path / "orders-service"
    shutil.copytree(demo, target)
    yield target
    shutil.rmtree(target, onexc=_writable)


class Session:
    def __init__(self, root: Path) -> None:
        self.bridge = Bridge()
        opened = self.call("project.open", root=str(root))
        assert opened["error"] is None, opened["error"]
        self.project_id = opened["project_id"]

    def call(self, op: str, **params):  # noqa: ANN201
        if op != "project.open" and op != "hello":
            params.setdefault("project_id", getattr(self, "project_id", ""))
        return self.bridge.handle({"id": op, "op": op, "params": params})

    def payload(self, op: str, **params):  # noqa: ANN201
        reply = self.call(op, **params)
        assert reply["error"] is None, reply["error"]
        return reply["payload"]


def _tree_digest(root: Path) -> dict[str, str]:
    digest: dict[str, str] = {}
    for base in (root / ".ai-sdlc", root / "delivery"):
        for path in sorted(base.rglob("*")):
            if path.is_file():
                digest[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


# -- protocol --------------------------------------------------------------------------------


def test_hello_and_envelope_shape(demo: Path) -> None:
    reply = Bridge().handle({"id": "h", "op": "hello"})
    assert set(reply) == ENVELOPE_KEYS
    assert reply["protocol_version"] == "1.0"
    assert {"project.open", "story.inspect", "operation.execute"} <= set(reply["payload"]["operations"])


def test_errors_are_structured(demo: Path) -> None:
    bridge = Bridge()
    assert bridge.handle({"id": "x", "op": "nope"})["error"]["code"] == "unknown_operation"
    assert (
        bridge.handle({"id": "x", "op": "project.snapshot", "params": {"project_id": "p-missing"}})["error"]["code"]
        == "unknown_project"
    )
    session = Session(demo)
    assert session.call("intent.inspect", intent_id="I999")["error"]["code"] == "unknown_intent"
    assert session.call("gate.packet", intent_id="I001", gate="G9")["error"]["code"] == "invalid_params"


def test_stdio_framing_round_trip(demo: Path) -> None:
    lines = [
        json.dumps({"id": "1", "op": "hello"}),
        "not json",
        json.dumps({"id": "2", "op": "project.open", "params": {"root": str(demo)}}),
    ]
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "aisdlc.desktop"],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY / "scripts")},
        check=True,
    )
    replies = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [reply["request_id"] for reply in replies] == ["1", "malformed", "2"]
    assert replies[1]["error"]["code"] == "malformed_request"
    assert replies[2]["kind"] == "context"


# -- the read-only invariant -------------------------------------------------------------------


def test_every_read_operation_leaves_the_repository_byte_identical(demo_copy: Path) -> None:
    before = _tree_digest(demo_copy)
    session = Session(demo_copy)
    for op, params in READ_OPS:
        reply = session.call(op, **params)
        assert reply["error"] is None, (op, reply["error"])
        assert set(reply) == ENVELOPE_KEYS
    assert _tree_digest(demo_copy) == before


# -- projections over the real lifecycle -------------------------------------------------------


def test_portfolio_reports_what_waits_on_a_person(demo: Path) -> None:
    portfolio = Session(demo).payload("project.snapshot")
    statuses = {item["id"]: item["status"] for item in portfolio["intents"]}
    assert statuses == {"I001": "completed", "I002": "specified", "I003": "building", "I004": "framed"}
    assert {(item["intent"], item["gate"]) for item in portfolio["waiting"]} == {("I002", "G2"), ("I004", "G1")}
    assert all(item["since"] for item in portfolio["waiting"])
    assert [item["resource"] for item in portfolio["contention"]] == ["contract:orders#listOrders"]
    assert portfolio["kpis"]["contention"] == 1  # one conflict, reported from both sides, counted once


def test_reopened_gate_packet_prices_the_change(demo: Path) -> None:
    packet = Session(demo).payload("gate.packet", intent_id="I002", gate="G2")
    change = packet["packet"]["specification_diff"]
    assert change["requirements"] == {"added": ["REQ-F-003"], "removed": [], "changed": ["REQ-F-002"]}
    assert change["tasks_losing_evidence"] == [{"task": "I002-P00-T002", "requirements": ["REQ-F-002"]}]
    assert "## Specification changes since Gate 2 was approved" in packet["markdown"]


def test_trace_leads_with_gaps(demo: Path) -> None:
    session = Session(demo)
    complete = session.payload("trace.inspect", intent_id="I001")
    assert complete["gaps"] == []
    lanes = {node["lane"] for node in complete["nodes"]}
    assert lanes == {"success", "requirement", "criterion", "task", "evidence"}
    deferred = next(node for node in complete["nodes"] if node["id"] == "REQ-F-003")
    assert deferred["deferred"] is True
    reopened = session.payload("trace.inspect", intent_id="I002")
    assert {gap["id"] for gap in reopened["gaps"]} == {"REQ-F-003", "AC-004"}
    flags = {node["id"]: (node.get("added"), node.get("changed")) for node in reopened["nodes"]}
    assert flags["REQ-F-003"] == (True, False) and flags["REQ-F-002"] == (False, True)


def test_story_replays_recorded_enforcement(demo: Path) -> None:
    story = Session(demo).payload("story.inspect", intent_id="I001")
    chapters = {chapter["id"]: chapter for chapter in story["chapters"]}
    assert story["complete"] is True
    specify = chapters["specify"]
    refused = next(item for item in specify["enforcement"] if item["kind"] == "refused")
    assert any("SQ001" in reason for reason in refused["reasons"])
    assert specify["artifact"]["provenance"] == "verified_as_approved"
    build_kinds = [item["kind"] for item in chapters["build"]["enforcement"]]
    assert build_kinds.count("independent_review") == 3
    assert "task_failed" in build_kinds
    assert [event["index"] for event in story["timeline"]] == list(range(len(story["timeline"])))


def test_story_never_passes_off_a_changed_spec_as_approved(demo_copy: Path) -> None:
    spec = next((demo_copy / "delivery" / "intents").glob("I001-*")) / "02-specification" / "requirements.yaml"
    document = yaml.safe_load(spec.read_text(encoding="utf-8"))
    document["functional_requirements"][0]["statement"] += " Edited after approval."
    spec.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    story = Session(demo_copy).payload("story.inspect", intent_id="I001")
    artifact = next(chapter for chapter in story["chapters"] if chapter["id"] == "specify")["artifact"]
    assert artifact["provenance"] == "changed_since_approval"


def test_advance_refusals_are_on_the_audit_record(demo: Path) -> None:
    lines = (demo / ".ai-sdlc" / "runtime" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    refusals = [json.loads(line) for line in lines if '"advance_refused"' in line]
    assert len(refusals) == 1
    assert refusals[0]["entity_id"] == "I001"
    assert refusals[0]["details"]["check"] == "specification_quality"


def test_generation_moves_only_when_something_changed(demo_copy: Path) -> None:
    session = Session(demo_copy)
    first = session.payload("project.reconcile")
    assert first["changed"] is False
    brief = next((demo_copy / "delivery" / "intents").glob("I004-*")) / "01-brief" / "brief.md"
    brief.write_text(brief.read_text(encoding="utf-8") + "\n- One more measure.\n", encoding="utf-8")
    second = session.payload("project.reconcile")
    assert second["changed"] is True and second["generation"] == first["generation"] + 1


# -- operations -----------------------------------------------------------------------------------


def test_gate_approval_goes_through_preview_and_the_runner(demo_copy: Path) -> None:
    session = Session(demo_copy)
    args = {"intent_id": "I004", "gate": "G1", "approver": "priya.product"}
    preview = session.payload("operation.preview", name="gate_approve", args=args)
    assert preview["command"] == "ai-sdlc gate approve I004 G1 --approver priya.product"
    assert preview["writes"] is True
    result = session.payload(
        "operation.execute", name="gate_approve", args=args, token=preview["token"], base_hashes=preview["base_hashes"]
    )
    assert result["succeeded"] is True, result
    waiting = {item["intent"] for item in session.payload("project.snapshot")["waiting"]}
    assert "I004" not in waiting
    replay = session.call(
        "operation.execute", name="gate_approve", args=args, token=preview["token"], base_hashes=preview["base_hashes"]
    )
    assert replay["error"]["code"] == "token_invalid"


def test_coapproval_clears_the_contention_the_cockpit_shows(demo_copy: Path) -> None:
    """Two `--intent` values must reach the runner as two Intents, not as characters."""
    session = Session(demo_copy)
    args = {
        "resource": "contract:orders#listOrders",
        "intents": ["I002", "I003"],
        "approver": "marco.techlead",
        "reason": "Search and ledger touch disjoint fields",
    }
    preview = session.payload("operation.preview", name="coapprove_resource", args=args)
    result = session.payload(
        "operation.execute",
        name="coapprove_resource",
        args=args,
        token=preview["token"],
        base_hashes=preview["base_hashes"],
    )
    assert result["succeeded"] is True, result
    assert result["output"]["intents"] == ["I002", "I003"]


def test_execute_is_refused_when_state_moved_after_preview(demo_copy: Path) -> None:
    session = Session(demo_copy)
    args = {"intent_id": "I004", "gate": "G1", "approver": "priya.product"}
    preview = session.payload("operation.preview", name="gate_approve", args=args)
    # Someone else acts in the meantime — through the runner, as they would.
    CliRunner().invoke(
        __import__("aisdlc.cli", fromlist=["app"]).app,
        ["gate", "approve", "I004", "G1", "--approver", "marco.techlead", "--repo", str(demo_copy)],
    )
    reply = session.call(
        "operation.execute", name="gate_approve", args=args, token=preview["token"], base_hashes=preview["base_hashes"]
    )
    assert reply["error"]["code"] == "base_moved"


def test_operations_are_allowlisted_and_validated(demo: Path) -> None:
    session = Session(demo)
    assert session.call("operation.preview", name="repair_state", args={})["error"]["code"] == "unknown_operation"
    injected = session.call(
        "operation.preview", name="gate_approve", args={"intent_id": "I004; rm -rf /", "gate": "G1", "approver": "x"}
    )
    assert injected["error"]["code"] == "invalid_params"
    multiline = session.call(
        "operation.preview",
        name="gate_reopen",
        args={"intent_id": "I001", "gate": "G2", "actor": "x", "reason": "a\nb"},
    )
    assert multiline["error"]["code"] == "invalid_params"


def test_the_runner_still_refuses_what_it_always_refuses(demo_copy: Path) -> None:
    """The app adds a confirmation; it never removes a check. G2 for I002 has contention
    outstanding but no pre-gate review of the reopened spec, so the runner says no."""
    session = Session(demo_copy)
    args = {"intent_id": "I002", "gate": "G2", "approver": "marco.techlead"}
    preview = session.payload("operation.preview", name="gate_approve", args=args)
    result = session.payload(
        "operation.execute", name="gate_approve", args=args, token=preview["token"], base_hashes=preview["base_hashes"]
    )
    assert result["succeeded"] is False
    assert "review" in (result["error_text"] or "")
