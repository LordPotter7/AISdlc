"""Wave T3: trace and decide — the one-page overview with coverage and suspect links, and the
inbox of everything waiting on a person. Read-only: the repository stays byte-identical."""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

from aisdlc.desktop.bridge import Bridge

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
from tools.make_demo_repo import generate  # noqa: E402


@pytest.fixture(scope="module")
def demo_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate(tmp_path_factory.mktemp("trace") / "orders-service", force=False)


@pytest.fixture()
def demo(demo_source: Path, tmp_path: Path) -> Path:
    target = tmp_path / "orders-service"
    shutil.copytree(demo_source, target)
    return target


class Session:
    def __init__(self, root: Path) -> None:
        self.bridge = Bridge()
        self.project_id = self.bridge.handle({"id": "o", "op": "project.open", "params": {"root": str(root)}})[
            "project_id"
        ]

    def payload(self, op: str, **params):  # noqa: ANN201
        reply = self.bridge.handle({"id": op, "op": op, "params": {"project_id": self.project_id, **params}})
        assert reply["error"] is None, reply["error"]
        return reply["payload"]


def _digest(root: Path) -> str:
    hasher = hashlib.sha256()
    for base in (root / ".ai-sdlc", root / "delivery"):
        for path in sorted(base.rglob("*")):
            if path.is_file():
                hasher.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return hasher.hexdigest()


def test_overview_assembles_the_page_from_the_files(demo: Path) -> None:
    before = _digest(demo)
    page = Session(demo).payload("overview.inspect", intent_id="I001")
    assert "problem" in page["brief"] and "reconciles the ledger" in page["brief"]["problem"]
    assert {row["id"] for row in page["requirements"]} >= {"REQ-F-001", "REQ-F-002", "REQ-NFR-001"}
    # The won't requirement is shown but not counted: it is out of scope by decision.
    assert page["coverage"] == {"requirements": 3, "with_criterion": 3, "with_task": 3, "proven": 3, "suspect": 0}
    assert "src/export.py" in page["touches"]
    assert page["contracts"][0]["api"] == "orders"
    assert _digest(demo) == before


def test_a_requirement_changed_since_g2_makes_its_plan_suspect(demo: Path) -> None:
    page = Session(demo).payload("overview.inspect", intent_id="I002")  # G2 reopened; REQ-F-002 reworded
    row = next(row for row in page["requirements"] if row["id"] == "REQ-F-002")
    assert [item["kind"] for item in row["suspect"]] == ["plan"]
    assert "approve G2 again" in row["suspect"][0]["clear"]
    assert "REQ-F-002" in page["moved_since_g2"]
    assert page["coverage"]["suspect"] >= 1


def test_evidence_that_proved_an_older_requirement_is_suspect(demo: Path) -> None:
    session = Session(demo)
    assert all(not row["suspect"] for row in session.payload("overview.inspect", intent_id="I003")["requirements"])
    spec = next((demo / "delivery/intents").glob("I003-*")) / "02-specification/requirements.yaml"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "record each charge in the ledger of the tenant",
            "record each charge and refund in the ledger of the tenant",
        ),
        encoding="utf-8",
    )
    row = next(
        row for row in session.payload("overview.inspect", intent_id="I003")["requirements"] if row["id"] == "REQ-F-001"
    )
    kinds = {item["kind"] for item in row["suspect"]}
    assert "evidence" in kinds and row["proven"] is False
    evidence = next(item for item in row["suspect"] if item["kind"] == "evidence")
    assert evidence["task"] == "I003-P00-T001" and "verify-task" in evidence["clear"]


def test_inbox_lists_what_waits_on_a_person_once_and_in_order(demo: Path) -> None:
    before = _digest(demo)
    inbox = Session(demo).payload("inbox.inspect")
    kinds = [item["kind"] for item in inbox["items"]]
    assert kinds == sorted(kinds, key=["gate", "contention", "blocked", "finding", "lint", "suspect"].index)
    gates = {(item["intent"], item["gate"]) for item in inbox["items"] if item["kind"] == "gate"}
    assert gates == {("I002", "G2"), ("I004", "G1")}
    contention = [item for item in inbox["items"] if item["kind"] == "contention"]
    assert len(contention) == 1 and sorted(contention[0]["intents"]) == ["I002", "I003"]  # one pair, reported once
    assert inbox["counts"]["suspect"] == 1  # I002's reworded requirement
    assert _digest(demo) == before


def test_co_approval_clears_the_inbox_item(demo: Path) -> None:
    session = Session(demo)
    item = next(item for item in session.payload("inbox.inspect")["items"] if item["kind"] == "contention")
    args = {
        "resource": item["resource"],
        "intents": sorted(item["intents"]),
        "approver": "marco.techlead",
        "reason": "search and ledger read disjoint fields",
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
    assert not [item for item in session.payload("inbox.inspect")["items"] if item["kind"] == "contention"]
