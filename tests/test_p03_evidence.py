from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aisdlc.evidence import EvidenceError, EvidenceRunner, validate_evidence, validate_task_evidence
from aisdlc.state import StateStore


def initialize_repository(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, text=True, capture_output=True
    ).stdout.strip()


def test_command_capture_writes_hashed_attempt_and_preserves_failures(tmp_path: Path) -> None:
    commit = initialize_repository(tmp_path)
    task_dir = tmp_path / "task"
    manifest_path = EvidenceRunner(tmp_path).capture(
        "I001-P00-T001",
        task_dir,
        ["python -c \"print('ok')\"", "python -c \"import sys; print('bad', file=sys.stderr); sys.exit(3)\""],
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["attempt"] == 1
    assert manifest["source_commit"] == commit
    assert manifest["captured_by"] == "runner"
    assert manifest["passed"] is False
    assert manifest["commands"][1]["exit_code"] == 3
    assert validate_evidence(manifest_path, expected_commit=commit).valid
    second = EvidenceRunner(tmp_path).capture("I001-P00-T001", task_dir, ["python -c \"print('second')\""])
    assert second.parent.name == "attempt-2"
    assert manifest_path.exists()


def test_changed_evidence_and_commit_pins_are_rejected(tmp_path: Path) -> None:
    commit = initialize_repository(tmp_path)
    manifest_path = EvidenceRunner(tmp_path).capture("I001-P00-T001", tmp_path / "task", ["python -c \"print('ok')\""])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_path = manifest_path.parent / manifest["files"][0]["path"]
    output_path.write_text("tampered", encoding="utf-8")
    assert not validate_evidence(manifest_path, expected_commit=commit).valid
    assert not validate_evidence(manifest_path, expected_commit="0" * 40).valid


def test_evidence_has_one_kind_and_names_its_capturer(tmp_path: Path) -> None:
    """There is no `authority` field left, because it would always read the same value.

    Evidence used to be `provisional` until CI recaptured it as `authoritative`, gated on
    an environment variable. CI is gone (ADR-007), and a field that can only ever hold one
    value is worse than no field: it reads like a distinction still being drawn. What
    replaces it is the *location* of the capturer — the runner lives under the plugin root,
    outside the repository it is judging.
    """
    commit = initialize_repository(tmp_path)
    path = EvidenceRunner(tmp_path).capture("I001-P00-T001", tmp_path / "task", ["python -c \"print('ok')\""])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert "authority" not in manifest
    assert manifest["captured_by"] == "runner"
    assert manifest["source_commit"] == commit
    assert validate_evidence(path, expected_commit=commit).valid


def test_evidence_capture_rejects_an_empty_declared_command_set(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    with pytest.raises(EvidenceError, match="at least one"):
        EvidenceRunner(tmp_path).capture("I001-P00-T001", tmp_path / "task", [])


def test_specification_pin_and_reopened_gate_invalidate_evidence(tmp_path: Path) -> None:
    task_dir = tmp_path / "delivery/intents/I001-example/04-build/I001-P00/tasks/I001-P00-T001"
    task_dir.mkdir(parents=True)
    spec = tmp_path / "delivery/intents/I001-example/02-specification/requirements.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text("intent_id: I001\n", encoding="utf-8")
    initialize_repository(tmp_path)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "spec"], cwd=tmp_path, check=True)
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", actor="runner")
    store.transition_intent("I001", "specified", actor="runner")
    store.approve_gate("I001", "G2", "owner")
    manifest = EvidenceRunner(tmp_path).capture("I001-P00-T001", task_dir, ["python -c \"print('ok')\""])
    assert validate_task_evidence(manifest, tmp_path, task_dir).valid
    store.reopen_gate("I001", "G2", actor="owner", reason="requirements changed")
    report = validate_task_evidence(manifest, tmp_path, task_dir)
    assert not report.valid
    assert any("Gate 2" in error for error in report.errors)
