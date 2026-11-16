"""Execution authorization and the terminal Task verbs.

These cover the two rules the state machine declared but nothing enforced: that a
Task may not execute before the gates that approve it, and that `merged`,
`reverted`, `superseded`, and `failed` are reachable through the runner rather
than by editing a record.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from aisdlc.cli import app
from aisdlc.state import StateStore
from aisdlc.tasks import ResourceLockManager
from aisdlc.typerlite import CliRunner
from aisdlc.vendor import yaml

runner = CliRunner()


def _run(*args: str) -> Any:
    return runner.invoke(app, list(args))


def _seed_intent(root: Path, *, phase: str = "I001-P00") -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    created = _run(
        "new",
        "--discovery-reason",
        "greenfield fixture; nothing to discover",
        "--title",
        "Gated work",
        "--type",
        "feature",
        "--mode",
        "compact",
        "--repo",
        str(root),
    )
    assert created.exit_code == 0, created.output
    intent_root = Path(json.loads(created.output)["path"])
    specification = intent_root / "02-specification"
    specification.mkdir(parents=True)
    (specification / "requirements.yaml").write_text(
        yaml.safe_dump(
            {
                "intent_id": "I001",
                "functional_requirements": [
                    {
                        "id": "REQ-F-001",
                        "statement": "The system shall record a gated Task execution.",
                        "acceptance_criteria": ["AC-001"],
                    }
                ],
                "non_functional_requirements": [],
                "acceptance_criteria": [
                    {
                        "id": "AC-001",
                        "verification_class": "D",
                        "statement": "The declared command exits 0.",
                        "check": "python -c pass",
                    }
                ],
                "success_criteria": [{"id": "SC-001", "statement": "AC-001 passes."}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task_id = f"{phase}-T001"
    task_dir = intent_root / "04-build" / phase / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        yaml.safe_dump(
            {
                "id": task_id,
                "intent_id": "I001",
                "phase_id": phase,
                "title": "Gated Task",
                "status": "pending",
                "objective": "Prove that execution authorization is enforced by the runner.",
                "requirements": ["REQ-F-001"],
                "source_findings": [],
                "depends_on": [],
                "inputs": ["Approved task graph"],
                "expected_outputs": ["Runner-captured evidence"],
                "parallelizable": False,
                "affected_paths": ["src/"],
                "shared_resources": ["prompt:answer@v1"],
                "acceptance_criteria": [{"id": "AC-001", "verification_class": "D"}],
                "verification_commands": ["python -c pass"],
                "retry_limit": 2,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return task_id


def _advance_to_building(root: Path, *, approve_gate_4: bool) -> None:
    for args in (
        ("advance", "I001", "framed"),
        ("gate", "approve", "I001", "G1", "--approver", "human"),
        ("advance", "I001", "specified"),
        ("gate", "approve", "I001", "G2-G3", "--approver", "human"),
        ("advance", "I001", "planned"),
        ("advance", "I001", "building"),
    ):
        result = _run(*args, "--repo", str(root))
        assert result.exit_code == 0, result.output
    if approve_gate_4:
        result = _run("gate", "approve", "I001", "G4", "--approver", "human", "--repo", str(root))
        assert result.exit_code == 0, result.output


def test_start_task_refuses_before_the_intent_is_building(tmp_path: Path) -> None:
    task_id = _seed_intent(tmp_path)
    result = _run("start-task", task_id, "--repo", str(tmp_path))
    assert result.exit_code == 2
    assert "Tasks execute only while I001 is building" in result.output


def test_start_task_refuses_a_later_phase_until_gate_4(tmp_path: Path) -> None:
    task_id = _seed_intent(tmp_path, phase="I001-P01")
    _advance_to_building(tmp_path, approve_gate_4=False)
    blocked = _run("start-task", task_id, "--repo", str(tmp_path))
    assert blocked.exit_code == 2
    assert "Gate 4 must approve the walking skeleton" in blocked.output

    approved = _run("gate", "approve", "I001", "G4", "--approver", "human", "--repo", str(tmp_path))
    assert approved.exit_code == 0, approved.output
    assert _run("start-task", task_id, "--repo", str(tmp_path)).exit_code == 0


def test_walking_skeleton_runs_on_gate_3_alone(tmp_path: Path) -> None:
    task_id = _seed_intent(tmp_path)
    _advance_to_building(tmp_path, approve_gate_4=False)
    result = _run("start-task", task_id, "--repo", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "in_progress"


def test_ready_tasks_reports_gate_authorization_without_blocking(tmp_path: Path) -> None:
    task_id = _seed_intent(tmp_path)
    result = _run("ready-tasks", "I001", "--repo", str(tmp_path))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["execution_authorized"] is False
    assert task_id in payload["blocked_by_gates"]


def test_orchestrate_refuses_a_batch_before_the_intent_is_building(tmp_path: Path) -> None:
    _seed_intent(tmp_path)
    result = _run("orchestrate", "I001", "--repo", str(tmp_path))
    assert result.exit_code == 2
    assert "Tasks execute only while I001 is building" in result.output


def test_orchestrate_refuses_a_later_phase_batch_until_gate_4(tmp_path: Path) -> None:
    _seed_intent(tmp_path, phase="I001-P01")
    _advance_to_building(tmp_path, approve_gate_4=False)
    blocked = _run("orchestrate", "I001", "--repo", str(tmp_path))
    assert blocked.exit_code == 2
    assert "Gate 4 must approve the walking skeleton" in blocked.output

    assert _run("gate", "approve", "I001", "G4", "--approver", "human", "--repo", str(tmp_path)).exit_code == 0
    released = _run("orchestrate", "I001", "--repo", str(tmp_path))
    assert released.exit_code == 0, released.output
    assert json.loads(released.output)["ready_batch"] == ["I001-P01-T001"]


def test_supersede_records_a_reason_and_preserves_the_identifier(tmp_path: Path) -> None:
    task_id = _seed_intent(tmp_path)
    missing_reason = _run("supersede-task", task_id, "--reason", "  ", "--repo", str(tmp_path))
    assert missing_reason.exit_code == 2

    result = _run("supersede-task", task_id, "--reason", "Split into peer Tasks", "--repo", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "superseded"
    record = StateStore(tmp_path).read()["tasks"][task_id]
    assert record["status"] == "superseded"
    assert record["reason"] == "Split into peer Tasks"


def test_fail_task_releases_locks_after_an_escalation(tmp_path: Path) -> None:
    task_id = _seed_intent(tmp_path)
    _advance_to_building(tmp_path, approve_gate_4=False)
    assert _run("start-task", task_id, "--repo", str(tmp_path)).exit_code == 0
    assert ResourceLockManager(tmp_path).read() == {"prompt:answer@v1": task_id}

    store = StateStore(tmp_path)
    store.transition_task(task_id, "blocked", actor="runner")
    result = _run("fail-task", task_id, "--reason", "Abandoned at escalation", "--repo", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert store.read()["tasks"][task_id]["status"] == "failed"
    assert ResourceLockManager(tmp_path).read() == {}


def test_revert_requires_a_merged_task_and_a_durable_corrective_task(tmp_path: Path) -> None:
    task_id = _seed_intent(tmp_path)
    _advance_to_building(tmp_path, approve_gate_4=False)
    store = StateStore(tmp_path)
    store.initialize_task(task_id)
    for target in ("ready", "in_progress", "verifying", "review", "verified"):
        store.transition_task(task_id, target, actor="runner")

    unknown_corrective = _run(
        "revert-task", task_id, "--reason", "broke trunk", "--corrective", "I001-P00-T999", "--repo", str(tmp_path)
    )
    assert unknown_corrective.exit_code == 2

    tasks_root = next((tmp_path / "delivery" / "intents").rglob(f"{task_id}/task.yaml")).parent.parent
    corrective_dir = tasks_root / "I001-P00-T002"
    corrective_dir.mkdir()
    (corrective_dir / "task.yaml").write_text("id: I001-P00-T002\n", encoding="utf-8")

    not_merged = _run(
        "revert-task", task_id, "--reason", "broke trunk", "--corrective", "I001-P00-T002", "--repo", str(tmp_path)
    )
    assert not_merged.exit_code == 2
    assert "illegal transition verified -> reverted" in not_merged.output

    store.transition_task(task_id, "merged", actor="runner")
    reverted = _run(
        "revert-task", task_id, "--reason", "broke trunk", "--corrective", "I001-P00-T002", "--repo", str(tmp_path)
    )
    assert reverted.exit_code == 0, reverted.output
    record = store.read()["tasks"][task_id]
    assert record["status"] == "reverted"
    assert record["corrective_task"] == "I001-P00-T002"


def test_audit_replay_preserves_terminal_state_details(tmp_path: Path) -> None:
    task_id = _seed_intent(tmp_path)
    assert _run("supersede-task", task_id, "--reason", "Replaced", "--repo", str(tmp_path)).exit_code == 0
    store = StateStore(tmp_path)
    replayed = store.reconstruct(store.audit_path)
    assert replayed["tasks"][task_id]["status"] == "superseded"
    assert replayed["tasks"][task_id]["reason"] == "Replaced"


def test_review_task_refuses_a_task_that_is_not_in_review(tmp_path: Path) -> None:
    task_id = _seed_intent(tmp_path)
    _advance_to_building(tmp_path, approve_gate_4=False)
    assert _run("start-task", task_id, "--repo", str(tmp_path)).exit_code == 0
    review = tmp_path / "review.yaml"
    review.write_text("task_id: x\n", encoding="utf-8")
    evidence = tmp_path / "manifest.json"
    evidence.write_text("{}", encoding="utf-8")
    result = _run(
        "review-task",
        task_id,
        "--review",
        str(review),
        "--evidence",
        str(evidence),
        "--implementer",
        "developer-01",
        "--repo",
        str(tmp_path),
    )
    assert result.exit_code == 2
    assert "must be in review" in result.output


def test_a_specification_reopened_during_build_can_be_approved_again(tmp_path: Path) -> None:
    """Reopening G2 while building must lead somewhere: retreat to specified, re-approve
    G2, and return to building on the G3 that was never reopened."""
    _seed_intent(tmp_path)
    _advance_to_building(tmp_path, approve_gate_4=False)
    repo = ("--repo", str(tmp_path))
    assert _run("gate", "reopen", "I001", "G2", "--actor", "human", "--reason", "scope changed", *repo).exit_code == 0
    for args in (
        ("advance", "I001", "planned"),
        ("advance", "I001", "specified"),
        ("gate", "approve", "I001", "G2", "--approver", "human"),
        ("advance", "I001", "planned"),
        ("advance", "I001", "building"),
    ):
        result = _run(*args, *repo)
        assert result.exit_code == 0, (args, result.output)
    assert StateStore(tmp_path).read()["intents"]["I001"]["status"] == "building"


def test_moving_forward_still_requires_the_reopened_gate(tmp_path: Path) -> None:
    _seed_intent(tmp_path)
    _advance_to_building(tmp_path, approve_gate_4=False)
    repo = ("--repo", str(tmp_path))
    assert _run("gate", "reopen", "I001", "G2", "--actor", "human", "--reason", "scope changed", *repo).exit_code == 0
    assert _run("advance", "I001", "planned", *repo).exit_code == 0
    assert _run("advance", "I001", "specified", *repo).exit_code == 0
    refused = _run("advance", "I001", "planned", *repo)
    assert refused.exit_code != 0
    assert "G2 must be approved" in refused.output
