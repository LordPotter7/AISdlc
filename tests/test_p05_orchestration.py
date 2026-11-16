from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aisdlc.evidence import EvidenceRunner
from aisdlc.orchestration import (
    ContextPackBuilder,
    MergeReadiness,
    OrchestrationError,
    RetryController,
    ReviewService,
    ScriptedOrchestrator,
    WorktreeManager,
)
from aisdlc.state import StateStore
from aisdlc.vendor import yaml


def initialize_repository(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, text=True, capture_output=True
    ).stdout.strip()


def create_task(root: Path, status: str = "pending") -> tuple[Path, dict[str, object]]:
    task_dir = root / "delivery/intents/I001-example/04-build/I001-P00/tasks/I001-P00-T001"
    task_dir.mkdir(parents=True)
    task = {
        "id": "I001-P00-T001",
        "intent_id": "I001",
        "phase_id": "I001-P00",
        "title": "Implement status",
        "status": status,
        "objective": "Implement status.",
        "requirements": ["REQ-F-001"],
        "source_findings": [],
        "depends_on": [],
        "affected_paths": ["src/status.py"],
        "shared_resources": ["schema:status"],
        "inputs": ["requirements.yaml"],
        "expected_outputs": ["src/status.py"],
        "acceptance_criteria": [{"id": "AC-001", "verification_class": "D"}],
        "verification_commands": ["python -c \"print('ok')\""],
        "parallelizable": True,
        "retry_limit": 2,
    }
    (task_dir / "task.yaml").write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    spec = task_dir.parents[3] / "02-specification/requirements.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        yaml.safe_dump(
            {
                "intent_id": "I001",
                "functional_requirements": [
                    {"id": "REQ-F-001", "statement": "Status works", "acceptance_criteria": ["AC-001"]},
                    {"id": "REQ-F-002", "statement": "Unrelated", "acceptance_criteria": ["AC-002"]},
                ],
                "non_functional_requirements": [],
                "acceptance_criteria": [
                    {"id": "AC-001", "verification_class": "D", "statement": "Pass", "check": "pytest"},
                    {"id": "AC-002", "verification_class": "D", "statement": "Other", "check": "pytest"},
                ],
                "success_criteria": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    store = StateStore(root)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", actor="runner")
    store.transition_intent("I001", "specified", actor="runner")
    store.approve_gate("I001", "G2", "owner")
    return task_dir, task


def test_worktree_lifecycle_is_isolated_resumable_and_explicitly_removed(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    worktrees = tmp_path.parent / f"{tmp_path.name}-worktrees"
    manager = WorktreeManager(tmp_path, worktrees_root=worktrees)
    path = manager.create("I001-P00-T001")
    assert path.exists()
    assert manager.resume("I001-P00-T001") == path
    with pytest.raises(OrchestrationError, match="explicit confirmation"):
        manager.remove("I001-P00-T001")
    manager.remove("I001-P00-T001", confirm=True)
    assert not path.exists()


def test_context_pack_contains_only_mapped_requirements(tmp_path: Path) -> None:
    task_dir, _ = create_task(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("Repository rules\n", encoding="utf-8")
    path = ContextPackBuilder(tmp_path).build(task_dir / "task.yaml")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [item["id"] for item in payload["requirements"]] == ["REQ-F-001"]
    assert [item["id"] for item in payload["acceptance_criteria"]] == ["AC-001"]
    assert payload["repository_instructions"] == "Repository rules\n"


def test_independent_review_requires_valid_pinned_evidence_and_a_different_reviewer(
    tmp_path: Path,
) -> None:
    """What survives ADR-007's removal of the provisional/authoritative split.

    A review used to be rejected unless its evidence was recaptured by CI. With one kind of
    evidence, two guards still stand, and they are the ones that carried the independence
    claim: the evidence must be intact and pinned to the reviewed commit, and the reviewer
    must be neither the implementer nor a model of the implementer's family.
    """
    initialize_repository(tmp_path)
    task_dir, _ = create_task(tmp_path)
    store = StateStore(tmp_path)
    store.initialize_task("I001-P00-T001")
    for target in ("ready", "in_progress", "verifying", "review"):
        store.transition_task("I001-P00-T001", target, actor="runner")
    evidence = EvidenceRunner(tmp_path).capture("I001-P00-T001", task_dir, ["python -c \"print('ok')\""])
    review = {
        "reviewer_agent_id": "verifier-01",
        "reviewer_model": "opus-5",
        "implementer_model": "claude-fable-5",
        "decision": "approved",
        "checks": {"evidence": "passed"},
        "findings": [],
    }

    # Tampered evidence is refused before independence is even considered.
    manifest = json.loads(evidence.read_text(encoding="utf-8"))
    output = evidence.parent / manifest["files"][0]["path"]
    # Bytes, not text: `write_text` would translate newlines on Windows and the restored
    # file would no longer hash to what the manifest recorded.
    original = output.read_bytes()
    output.write_bytes(b"tampered")
    with pytest.raises(OrchestrationError, match="evidence is invalid"):
        ReviewService(tmp_path).record("I001-P00-T001", task_dir, review, evidence, implementer_id="developer-01")
    output.write_bytes(original)

    result = ReviewService(tmp_path).record("I001-P00-T001", task_dir, review, evidence, implementer_id="developer-01")
    assert result["status"] == "verified"
    assert store.read()["tasks"]["I001-P00-T001"]["status"] == "verified"

    # Self-review is still not review.
    with pytest.raises(OrchestrationError, match="different"):
        ReviewService(tmp_path).record("I001-P00-T001", task_dir, review, evidence, implementer_id="verifier-01")


def test_third_failed_attempt_blocks_and_writes_escalation(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.initialize_task("I001-P00-T001")
    store.transition_task("I001-P00-T001", "ready", actor="runner")
    store.transition_task("I001-P00-T001", "in_progress", actor="runner")
    controller = RetryController(tmp_path)
    for attempt in range(1, 4):
        store.transition_task("I001-P00-T001", "verifying", actor="runner")
        result = controller.failure("I001-P00-T001", reason=f"attempt {attempt}", retry_limit=2)
        if attempt < 3:
            assert result["status"] == "retry"
            store.transition_task("I001-P00-T001", "in_progress", actor="runner")
    assert result["status"] == "blocked"
    assert Path(result["escalation_package"]).exists()


def test_merge_readiness_requires_same_commit_evidence_review_and_clean_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = initialize_repository(tmp_path)
    task_dir, _ = create_task(tmp_path)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "task artifacts"], cwd=tmp_path, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, text=True, capture_output=True
    ).stdout.strip()
    store = StateStore(tmp_path)
    store.initialize_task("I001-P00-T001")
    for target in ("ready", "in_progress", "verifying", "review"):
        store.transition_task("I001-P00-T001", target, actor="runner")
    manifest = EvidenceRunner(tmp_path).capture("I001-P00-T001", task_dir, ["python -c \"print('ok')\""])
    review = {
        "reviewer_agent_id": "verifier-01",
        "reviewer_model": "opus-5",
        "implementer_model": "claude-fable-5",
        "decision": "approved",
        "checks": {},
        "findings": [],
    }
    ReviewService(tmp_path).record("I001-P00-T001", task_dir, review, manifest, implementer_id="developer-01")
    report = MergeReadiness(tmp_path).assess("I001-P00-T001", task_dir, expected_commit=commit)
    assert report.ready, report.errors


def test_scripted_orchestrator_batches_only_non_conflicting_ready_tasks(tmp_path: Path) -> None:
    _, first = create_task(tmp_path)
    second = dict(first) | {"id": "I001-P00-T002", "shared_resources": ["schema:status"]}
    third = dict(first) | {"id": "I001-P00-T003", "affected_paths": ["src/other.py"], "shared_resources": []}
    batch = ScriptedOrchestrator(tmp_path).batch([first, second, third])
    assert [task["id"] for task in batch] == ["I001-P00-T001", "I001-P00-T003"]
