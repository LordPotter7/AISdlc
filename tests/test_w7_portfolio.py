"""W6/W7: many Intents in one repository. A programme dependency and the build limit both
refuse `advance <id> building` on the record and name the way through; a waiver and a
priority change are audited decisions with a name and a reason."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aisdlc.cli import app
from aisdlc.config import ConfigError, load_config
from aisdlc.state import StateStore
from aisdlc.typerlite import CliRunner
from aisdlc.vendor import yaml

runner = CliRunner()


def _run(root: Path, *args: str) -> Any:
    return runner.invoke(app, [*args, "--repo", str(root)])


def _ok(root: Path, *args: str) -> dict[str, Any]:
    result = _run(root, *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output) if result.output.strip().startswith("{") else {}


def _planned_intent(root: Path, title: str) -> str:
    """A compact Intent taken to `planned`, one step short of building."""
    created = _ok(
        root,
        "new",
        "--title",
        title,
        "--type",
        "feature",
        "--mode",
        "compact",
        "--discovery-reason",
        "fixture; nothing to discover",
    )
    intent_id, intent_root = created["intent_id"], Path(created["path"])
    (intent_root / "02-specification").mkdir(parents=True)
    (intent_root / "02-specification" / "requirements.yaml").write_text(
        yaml.safe_dump(
            {
                "intent_id": intent_id,
                "functional_requirements": [
                    {
                        "id": "REQ-F-001",
                        "statement": "The system shall record the change.",
                        "acceptance_criteria": ["AC-001"],
                    }
                ],
                "non_functional_requirements": [],
                "acceptance_criteria": [
                    {
                        "id": "AC-001",
                        "verification_class": "D",
                        "statement": "The command exits 0.",
                        "check": "python -c pass",
                    }
                ],
                "success_criteria": [{"id": "SC-001", "statement": "AC-001 passes."}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task_id = f"{intent_id}-P00-T001"
    task_root = intent_root / "04-build" / f"{intent_id}-P00" / "tasks" / task_id
    task_root.mkdir(parents=True)
    (task_root / "task.yaml").write_text(
        yaml.safe_dump(
            {
                "id": task_id,
                "intent_id": intent_id,
                "phase_id": f"{intent_id}-P00",
                "title": "Change",
                "status": "pending",
                "objective": "Make the change.",
                "requirements": ["REQ-F-001"],
                "source_findings": [],
                "depends_on": [],
                "inputs": ["The plan"],
                "expected_outputs": ["Evidence"],
                "parallelizable": False,
                "affected_paths": ["src/"],
                "shared_resources": [],
                "acceptance_criteria": [{"id": "AC-001", "verification_class": "D"}],
                "verification_commands": ["python -c pass"],
                "retry_limit": 2,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    for args in (
        ("advance", intent_id, "framed"),
        ("gate", "approve", intent_id, "G1", "--approver", "lee"),
        ("advance", intent_id, "specified"),
        ("gate", "approve", intent_id, "G2-G3", "--approver", "lee"),
        ("advance", intent_id, "planned"),
    ):
        _ok(root, *args)
    return intent_id


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@example.invalid"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    return tmp_path


def _audit(root: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in StateStore(root).audit_path.read_text(encoding="utf-8").splitlines() if line]


def test_a_programme_predecessor_blocks_building_until_it_closes_or_is_waived(repository: Path) -> None:
    first = _planned_intent(repository, "Schema migration")
    second = _planned_intent(repository, "Read path on the new schema")
    program = _ok(repository, "program", "new", "--title", "Schema move", "--owner", "lee")["id"]
    _ok(repository, "program", "add", program, second, "--requires", first)

    refused = _run(repository, "advance", second, "building")
    assert refused.exit_code != 0
    assert f"ai-sdlc program waive {program} {second} {first}" in refused.output
    assert StateStore(repository).read()["intents"][second]["status"] == "planned"
    record = [event for event in _audit(repository) if event.get("event") == "advance_refused"][-1]
    assert record["entity_id"] == second and record["details"]["check"] == "program_dependency"

    blank = _run(repository, "program", "waive", program, second, first, "--actor", "lee", "--reason", " ")
    assert blank.exit_code != 0, "a waiver needs a written reason"
    _ok(
        repository,
        "program",
        "waive",
        program,
        second,
        first,
        "--actor",
        "lee",
        "--reason",
        "the read path ships behind a flag",
    )
    waived = [event for event in _audit(repository) if event.get("event") == "dependency_waived"]
    assert waived and waived[-1]["details"] == {
        "program": program,
        "predecessor": first,
        "reason": "the read path ships behind a flag",
    }
    _ok(repository, "advance", second, "building")


def test_the_build_limit_refuses_one_more_and_names_what_is_building(repository: Path) -> None:
    first = _planned_intent(repository, "First")
    second = _planned_intent(repository, "Second")
    config_path = repository / ".ai-sdlc" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    config.setdefault("scale", {})["max_building"] = 1
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    assert load_config(repository).scale.max_building == 1

    _ok(repository, "advance", first, "building")
    refused = _run(repository, "advance", second, "building")
    assert refused.exit_code != 0 and f"1 of 1 allowed Intents are building ({first})" in refused.output
    record = [event for event in _audit(repository) if event.get("event") == "advance_refused"][-1]
    assert record["details"]["check"] == "build_limit"

    # Stepping back out of building is never limited, and frees the slot.
    _ok(repository, "advance", first, "planned")
    _ok(repository, "advance", second, "building")


def test_a_build_limit_below_one_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".ai-sdlc").mkdir()
    (tmp_path / ".ai-sdlc" / "config.yaml").write_text("scale:\n  max_building: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_building"):
        load_config(tmp_path)


def test_priority_is_an_audited_decision_and_changes_no_state(repository: Path) -> None:
    intent = _planned_intent(repository, "Refund page")
    before = StateStore(repository).read()
    _ok(repository, "intent-priority", intent, "high", "--actor", "priya", "--reason", "support backlog doubled")
    assert StateStore(repository).read() == before, "priority is a record in the log, not a state field"
    event = [item for item in _audit(repository) if item.get("event") == "priority_set"][-1]
    assert event["actor"] == "priya" and event["details"] == {"priority": "high", "reason": "support backlog doubled"}
    for args, message in (
        ((intent, "urgent"), "priority must be one of"),
        ((intent, "low"), "needs a reason"),
        (("I999", "low"), "unknown Intent"),
    ):
        reason = "" if message == "needs a reason" else "because"
        result = _run(repository, "intent-priority", *args, "--actor", "priya", "--reason", reason)
        assert result.exit_code != 0 and message in result.output, result.output


# -- Studio: the portfolio projection and the two decisions it can record ------------------------


def _bridge(root: Path):  # noqa: ANN202
    from aisdlc.desktop.bridge import Bridge

    bridge = Bridge()
    project = bridge.handle({"id": "o", "op": "project.open", "params": {"root": str(root)}})["project_id"]

    def call(op: str, **params: Any) -> dict[str, Any]:
        return bridge.handle({"id": op, "op": op, "params": {"project_id": project, **params}})

    def run(name: str, **args: Any) -> dict[str, Any]:
        preview = call("operation.preview", name=name, args=args)
        assert preview["error"] is None, preview["error"]
        result = call(
            "operation.execute",
            name=name,
            args=args,
            token=preview["payload"]["token"],
            base_hashes=preview["payload"]["base_hashes"],
        )
        assert result["error"] is None and result["payload"]["succeeded"], result
        return result["payload"]

    return call, run


def test_the_portfolio_orders_by_priority_and_shows_programme_order(repository: Path) -> None:
    first = _planned_intent(repository, "Schema migration")
    second = _planned_intent(repository, "Read path on the new schema")
    program = _ok(repository, "program", "new", "--title", "Schema move", "--owner", "lee")["id"]
    _ok(repository, "program", "add", program, second, "--requires", first)
    call, run = _bridge(repository)

    before = call("portfolio.inspect")["payload"]
    assert [item["id"] for item in before["intents"]] == [first, second]
    blocked = {item["id"]: item for item in before["intents"]}[second]
    assert blocked["blocked_by"] == [first] and blocked["program"] == program
    assert blocked["title"] == "Read path on the new schema"
    assert before["programs"][0]["edges"] == [
        {"from": first, "to": second, "waived": False, "waived_by": None, "waiver_reason": None, "satisfied": False}
    ]
    assert before["wip"] == {"building": [], "ready": [first, second], "limit": None, "full": False}
    assert before["proof"]["requirements"] == 2

    run("intent_priority", intent_id=second, priority="high", actor="priya", reason="the read path is the payoff")
    run(
        "program_waive",
        program=program,
        intent_id=second,
        predecessor=first,
        actor="lee",
        reason="the read path ships behind a flag",
    )
    after = call("portfolio.inspect")["payload"]
    assert [item["id"] for item in after["intents"]] == [second, first], "high priority leads"
    lead = after["intents"][0]
    assert lead["priority"]["priority"] == "high" and lead["priority"]["by"] == "priya"
    assert lead["blocked_by"] == [] and after["programs"][0]["edges"][0]["waived"] is True

    refused = call(
        "operation.preview",
        name="intent_priority",
        args={"intent_id": second, "priority": "urgent", "actor": "priya", "reason": "x"},
    )
    assert refused["error"]["code"] == "invalid_params"
    refused = call(
        "operation.preview",
        name="program_waive",
        args={"program": "../x", "intent_id": second, "predecessor": first, "actor": "lee", "reason": "x"},
    )
    assert refused["error"]["code"] == "invalid_params"
