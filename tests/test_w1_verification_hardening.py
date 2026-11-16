"""Wave 1 — verification hardening.

Each test here corresponds to a documented failure mode of current models rather than to a
style preference: reward hacking against visible tests, judge family bias, unattested
evidence, and weak test suites that pass while asserting nothing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aisdlc.config import model_family
from aisdlc.evidence import (
    EvidenceRunner,
    holdout_coverage_errors,
    summarize_conformance,
    validate_task_evidence,
)
from aisdlc.mutation import MutationResult, MutationRunner
from aisdlc.orchestration import OrchestrationError, ReviewService
from aisdlc.state import StateStore
from aisdlc.vendor import yaml

PASS = "python -c \"print('ok')\""
FAIL = 'python -c "import sys; sys.exit(1)"'


def initialize_repository(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, text=True, capture_output=True
    ).stdout.strip()


def write_config(root: Path, payload: dict) -> None:
    path = root / ".ai-sdlc" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def write_task(task_dir: Path, **overrides) -> dict:
    task = {
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
        "verification_commands": [PASS],
        "parallelizable": False,
        "retry_limit": 2,
    }
    task.update(overrides)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.yaml").write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    return task


# --- W1.1 reviewer model independence -------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        ("claude-fable-5", "claude-fable-5-20260101", True),
        ("claude-fable-5", "claude-opus-5", False),
        ("us.anthropic/claude-opus-5", "claude-opus-5-20260201", True),
        ("gpt-5.2", "claude-opus-5", False),
    ],
)
def test_model_family_identifies_the_family_not_the_exact_version(left: str, right: str, same: bool) -> None:
    """Family bias attaches to the family, so point releases must compare as equal."""
    assert (model_family(left) == model_family(right)) is same


def test_review_is_rejected_when_the_reviewer_shares_the_implementer_model_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_repository(tmp_path)
    task_dir = tmp_path / "delivery/intents/I001-x/04-build/I001-P00/tasks/I001-P00-T001"
    write_task(task_dir)
    store = StateStore(tmp_path)
    store.initialize_task("I001-P00-T001")
    for target in ("ready", "in_progress", "verifying", "review"):
        store.transition_task("I001-P00-T001", target, actor="runner")
    evidence = EvidenceRunner(tmp_path).capture(
        "I001-P00-T001",
        task_dir,
        [PASS],
        holdout_commands=[PASS],
        holdout_authorship={"authored_by": "spec", "context": "specification"},
    )
    service = ReviewService(tmp_path)
    same_family = {
        "reviewer_agent_id": "verifier-01",
        "reviewer_model": "claude-fable-5-20260101",
        "implementer_model": "claude-fable-5",
        "decision": "approved",
        "checks": {},
        "findings": [],
    }
    with pytest.raises(OrchestrationError, match="family"):
        service.record("I001-P00-T001", task_dir, same_family, evidence, implementer_id="developer-01")

    cross_family = {**same_family, "reviewer_model": "opus-5"}
    result = service.record("I001-P00-T001", task_dir, cross_family, evidence, implementer_id="developer-01")
    assert result["status"] == "verified"


def test_review_without_a_reviewer_model_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An upgrade must not keep silently accepting legacy-shaped, unattributed reviews."""
    initialize_repository(tmp_path)
    task_dir = tmp_path / "delivery/intents/I001-x/04-build/I001-P00/tasks/I001-P00-T001"
    write_task(task_dir)
    store = StateStore(tmp_path)
    store.initialize_task("I001-P00-T001")
    for target in ("ready", "in_progress", "verifying", "review"):
        store.transition_task("I001-P00-T001", target, actor="runner")
    evidence = EvidenceRunner(tmp_path).capture("I001-P00-T001", task_dir, [PASS])
    review = {"reviewer_agent_id": "verifier-01", "decision": "approved", "checks": {}, "findings": []}
    with pytest.raises(OrchestrationError, match="reviewer_model"):
        ReviewService(tmp_path).record("I001-P00-T001", task_dir, review, evidence, implementer_id="developer-01")


def test_an_inherited_reviewer_model_cannot_be_shown_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_repository(tmp_path)
    task_dir = tmp_path / "delivery/intents/I001-x/04-build/I001-P00/tasks/I001-P00-T001"
    write_task(task_dir)
    store = StateStore(tmp_path)
    store.initialize_task("I001-P00-T001")
    for target in ("ready", "in_progress", "verifying", "review"):
        store.transition_task("I001-P00-T001", target, actor="runner")
    evidence = EvidenceRunner(tmp_path).capture("I001-P00-T001", task_dir, [PASS])
    review = {
        "reviewer_agent_id": "verifier-01",
        "reviewer_model": "inherit",
        "decision": "approved",
        "checks": {},
        "findings": [],
    }
    with pytest.raises(OrchestrationError, match="inherit"):
        ReviewService(tmp_path).record("I001-P00-T001", task_dir, review, evidence, implementer_id="developer-01")


# --- W1.2 held-out verification -------------------------------------------------------


def test_conformance_summary_distinguishes_a_gap_from_a_missing_instrument() -> None:
    """No held-out command is not the same as a held-out command that passed."""
    none_declared = summarize_conformance([{"kind": "visible", "exit_code": 0}])
    assert none_declared["holdout_declared"] is False
    assert none_declared["gap"] is None
    assert none_declared["conformance_gap"] is False

    gamed = summarize_conformance([{"kind": "visible", "exit_code": 0}, {"kind": "holdout", "exit_code": 1}])
    assert gamed["visible_pass_rate"] == 1.0
    assert gamed["holdout_pass_rate"] == 0.0
    assert gamed["conformance_gap"] is True

    honestly_broken = summarize_conformance([{"kind": "visible", "exit_code": 1}, {"kind": "holdout", "exit_code": 1}])
    # Both suites fail: the implementation is simply incomplete, not gaming anything.
    assert honestly_broken["conformance_gap"] is False


def test_visible_pass_with_holdout_failure_is_reported_as_a_conformance_gap(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    task_dir = tmp_path / "delivery/intents/I001-x/04-build/I001-P00/tasks/I001-P00-T001"
    write_task(task_dir)
    manifest_path = EvidenceRunner(tmp_path).capture(
        "I001-P00-T001",
        task_dir,
        [PASS],
        holdout_commands=[FAIL],
        holdout_authorship={"authored_by": "spec-owner", "context": "specification"},
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["conformance"]["conformance_gap"] is True
    report = validate_task_evidence(manifest_path, tmp_path, task_dir)
    assert not report.valid
    assert any("specification-conformance gap" in error for error in report.errors)


def test_holdout_authored_in_the_implementing_context_is_rejected(tmp_path: Path) -> None:
    """The instrument that decides a criterion is not authored where it is satisfied."""
    initialize_repository(tmp_path)
    task_dir = tmp_path / "delivery/intents/I001-x/04-build/I001-P00/tasks/I001-P00-T001"
    write_task(task_dir)
    manifest_path = EvidenceRunner(tmp_path).capture(
        "I001-P00-T001",
        task_dir,
        [PASS],
        holdout_commands=[PASS],
        holdout_authorship={"authored_by": "implementer-01", "context": "implementing"},
    )
    report = validate_task_evidence(manifest_path, tmp_path, task_dir)
    assert not report.valid
    assert any("implementing context" in error for error in report.errors)


def test_holdout_coverage_requires_an_instrument_or_a_named_waiver(tmp_path: Path) -> None:
    intent_root = tmp_path / "delivery/intents/I001-x"
    task_dir = intent_root / "04-build/I001-P00/tasks/I001-P00-T001"
    write_task(task_dir)
    errors = holdout_coverage_errors(tmp_path, intent_root)
    assert any("declares no held-out instrument" in error for error in errors)

    write_task(task_dir, holdout_waiver={"reason": "pure config change", "approved_by": "owner"})
    assert holdout_coverage_errors(tmp_path, intent_root) == []

    write_task(
        task_dir,
        holdout_commands=[PASS],
        holdout_authorship={"authored_by": "verifier", "context": "verifier"},
    )
    assert holdout_coverage_errors(tmp_path, intent_root) == []


def test_a_waiver_without_a_reason_is_not_a_waiver(tmp_path: Path) -> None:
    intent_root = tmp_path / "delivery/intents/I001-x"
    task_dir = intent_root / "04-build/I001-P00/tasks/I001-P00-T001"
    write_task(task_dir, holdout_waiver={"reason": "  ", "approved_by": "owner"})
    assert any("no written reason" in error for error in holdout_coverage_errors(tmp_path, intent_root))


# --- W1.4 mutation as a Class D companion ---------------------------------------------


def test_mutation_result_turns_surviving_mutants_into_corrective_input() -> None:
    """A bare score tells an agent it did badly; surviving mutants tell it what to assert."""
    result = MutationResult(
        score=0.7,
        killed=7,
        survived=3,
        threshold=0.85,
        surviving_mutants=({"location": "src/a.py:12", "description": "replaced > with >="},),
        tool="mutmut",
        raw_output="",
    )
    assert result.passed is False
    retry = result.retry_input()
    assert "src/a.py:12" in retry
    assert "replaced > with >=" in retry


def test_mutation_parsing_degrades_to_a_failing_score_not_a_silent_pass() -> None:
    """An unrecognised report must fail a threshold loudly rather than pass quietly."""
    parsed = MutationRunner._parse("mutmut", "totally unexpected output")
    assert parsed["score"] == 0.0
    assert parsed["killed"] == 0


def test_declared_mutation_threshold_takes_the_strictest_criterion() -> None:
    from aisdlc.mutation import declared_thresholds

    task = {
        "acceptance_criteria": [
            {"id": "AC-001", "verification_class": "D", "mutation_threshold": 0.7},
            {"id": "AC-002", "verification_class": "D", "mutation_threshold": 0.85},
        ]
    }
    assert declared_thresholds(task) == 0.85
    assert declared_thresholds({"acceptance_criteria": []}, default=0.6) == 0.6


def test_mutation_paths_never_reach_a_shell(tmp_path: Path, monkeypatch) -> None:
    """Task paths are data. A path holding shell syntax must arrive as one literal argument.

    Found in the 0.2.0 release review: `_command` joined paths into a `shell=True` string,
    so an `affected_paths` entry of `src; touch pwned` ran `touch pwned`.
    """
    import subprocess

    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["shell"] = command, kwargs.get("shell", False)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("aisdlc.mutation.subprocess.run", fake_run)
    hostile = "src; touch pwned"
    MutationRunner(tmp_path).run([hostile, "lib/x.py"])
    assert seen["shell"] is False
    assert isinstance(seen["command"], list)
    assert seen["command"][:4] == ["mutmut", "run", "--paths-to-mutate", f"{hostile},lib/x.py"]
    assert not (tmp_path / "pwned").exists()
