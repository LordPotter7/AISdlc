from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aisdlc.convergence import CloseService, ConvergenceError, ConvergenceService
from aisdlc.evidence import EvidenceRunner
from aisdlc.lessons import LessonRegistry
from aisdlc.orchestration import ReviewService
from aisdlc.state import StateStore
from aisdlc.vendor import yaml


def write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def create_converged_intent(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    intent = root / "delivery/intents/I001-example"
    write_yaml(
        intent / "00-intent/intent.yaml",
        {
            "id": "I001",
            "title": "Example",
            "intent_type": "feature",
            "status": "building",
            "autonomy_mode": "autonomous",
            "contains_model_behavior": False,
            "artifact_mode": "compact",
            "trigger_flags": {},
            "created_at": "2026-08-23T09:00:00Z",
        },
    )
    write_yaml(
        intent / "02-specification/requirements.yaml",
        {
            "intent_id": "I001",
            "functional_requirements": [{"id": "REQ-F-001", "statement": "Works", "acceptance_criteria": ["AC-001"]}],
            "non_functional_requirements": [],
            "acceptance_criteria": [
                {"id": "AC-001", "verification_class": "D", "statement": "Passes", "check": "pytest"}
            ],
            "success_criteria": [],
        },
    )
    task_dir = intent / "04-build/I001-P00/tasks/I001-P00-T001"
    write_yaml(
        task_dir / "task.yaml",
        {
            "id": "I001-P00-T001",
            "intent_id": "I001",
            "phase_id": "I001-P00",
            "title": "Build",
            "status": "pending",
            "objective": "Build it.",
            "requirements": ["REQ-F-001"],
            "source_findings": [],
            "depends_on": [],
            "affected_paths": ["src/example.py"],
            "shared_resources": [],
            "inputs": [],
            "expected_outputs": ["src/example.py"],
            "acceptance_criteria": [{"id": "AC-001", "verification_class": "D"}],
            "verification_commands": ["python -c \"print('ok')\""],
            "holdout_commands": ["python -c \"print('holdout')\""],
            "holdout_authorship": {"authored_by": "spec-owner", "context": "specification"},
            "parallelizable": False,
            "retry_limit": 2,
        },
    )
    write_yaml(intent / "05-verification/findings.yaml", {"findings": []})
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "intent"], cwd=root, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()
    store = StateStore(root)
    store.initialize_intent("I001")
    for target in ("framed", "specified"):
        store.transition_intent("I001", target, actor="runner")
    store.approve_gate("I001", "G2", "owner")
    for target in ("planned", "building"):
        store.transition_intent("I001", target, actor="runner")
    store.approve_gate("I001", "G4", "owner")
    store.initialize_task("I001-P00-T001")
    for target in ("ready", "in_progress", "verifying", "review"):
        store.transition_task("I001-P00-T001", target, actor="runner")
    evidence = EvidenceRunner(root).capture(
        "I001-P00-T001",
        task_dir,
        ["python -c \"print('ok')\""],
        holdout_commands=["python -c \"print('holdout')\""],
        holdout_authorship={"authored_by": "spec-owner", "context": "specification"},
    )
    ReviewService(root).record(
        "I001-P00-T001",
        task_dir,
        {
            "reviewer_agent_id": "reviewer",
            "reviewer_model": "opus-5",
            "implementer_model": "claude-fable-5",
            "decision": "approved",
            "checks": {},
            "findings": [],
        },
        evidence,
        implementer_id="implementer",
    )
    return intent, commit


def test_convergence_requires_authoritative_reviewed_same_commit_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent, commit = create_converged_intent(tmp_path, monkeypatch)
    report = ConvergenceService(tmp_path).assess(intent)
    assert report.passed, report.errors
    assert report.source_commit == commit
    assert report.report_path.exists()
    trace = json.loads((intent / "traceability.json").read_text(encoding="utf-8"))
    assert trace["links"][0]["status"] == "verified"


def test_convergence_rejects_open_high_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    intent, _ = create_converged_intent(tmp_path, monkeypatch)
    write_yaml(
        intent / "05-verification/findings.yaml",
        {"findings": [{"id": "F-001", "severity": "high", "status": "open"}]},
    )
    report = ConvergenceService(tmp_path).assess(intent)
    assert not report.passed
    assert any("F-001" in error for error in report.errors)


def test_convergence_rejects_a_finding_that_breaks_its_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    intent, _ = create_converged_intent(tmp_path, monkeypatch)
    write_yaml(
        intent / "05-verification/findings.yaml",
        {"findings": [{"id": "F-001", "severity": "low", "status": "resolved", "summary": "no stages"}]},
    )
    report = ConvergenceService(tmp_path).assess(intent)
    assert not report.passed
    assert any("findings.yaml:F-001" in error and "detected_stage" in error for error in report.errors), report.errors


def test_close_requires_gate_five_and_writes_completion_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    intent, _ = create_converged_intent(tmp_path, monkeypatch)
    store = StateStore(tmp_path)
    store.transition_intent("I001", "converging", actor="runner")
    store.transition_intent("I001", "awaiting_acceptance", actor="runner")
    with pytest.raises(ConvergenceError, match="Gate 5"):
        CloseService(tmp_path, LessonRegistry(tmp_path)).close(intent, actor="owner")
    store.approve_gate("I001", "G5", "owner")
    result = CloseService(tmp_path, LessonRegistry(tmp_path)).close(intent, actor="owner")
    assert result["status"] == "completed"
    assert Path(result["completion_report"]).exists()
    assert "release and deployment are outside" in Path(result["completion_report"]).read_text(encoding="utf-8")
