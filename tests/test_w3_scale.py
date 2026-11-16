"""Wave 3 — scale.

Everything in Waves 1 and 2 hardens a single-repository, single-team, one-Intent-at-a-time
lifecycle. These cover what breaks when an application is large: programmes, concurrent
teams, long-lived specifications, brownfield entry, stateful change, and federation.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aisdlc.baseline import BaselineError, BaselineService
from aisdlc.evidence import requirement_hashes, validate_task_evidence
from aisdlc.programs import ProgramError, ProgramRegistry
from aisdlc.state import StateStore, TransitionError
from aisdlc.tasks import IntentResourceCoordinator, ResourceConflictError
from aisdlc.validation import validate_migrations
from aisdlc.vendor import yaml


def write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def init_git(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    (path / "tracked.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, text=True, capture_output=True
    ).stdout.strip()


# --- W3.1 program layer -----------------------------------------------------------------


def register_intents(root: Path, *intent_ids: str) -> StateStore:
    store = StateStore(root)
    for intent_id in intent_ids:
        store.initialize_intent(intent_id)
    return store


def test_program_orders_intents_without_acquiring_gates_of_its_own(tmp_path: Path) -> None:
    store = register_intents(tmp_path, "I001", "I002")
    registry = ProgramRegistry(tmp_path)
    program = registry.create("Platform migration", owner="architect")
    assert program["id"].startswith("PRG")
    registry.add_member(program["id"], "I001")
    registry.add_member(program["id"], "I002", requires=["I001"])

    # I002 waits on I001 until I001 actually closes.
    assert registry.blocking_predecessors("I002") == ["I001"]
    for target in ("framed", "specified", "planned", "building", "converging", "awaiting_acceptance", "completed"):
        store.transition_intent("I001", target, actor="runner")
    assert registry.blocking_predecessors("I002") == []

    # A Program carries no gates, criteria, or evidence — only membership and ordering.
    record = registry.get(program["id"])
    assert set(record) == {"id", "title", "owner", "created_at", "members", "dependencies"}


def test_program_rejects_a_dependency_cycle(tmp_path: Path) -> None:
    register_intents(tmp_path, "I001", "I002")
    registry = ProgramRegistry(tmp_path)
    program = registry.create("Circular", owner="architect")
    registry.add_member(program["id"], "I001", requires=["I002"])
    with pytest.raises(ProgramError, match="cycle"):
        registry.add_member(program["id"], "I002", requires=["I001"])


def test_a_program_dependency_can_be_waived_only_with_a_reason(tmp_path: Path) -> None:
    register_intents(tmp_path, "I001", "I002")
    registry = ProgramRegistry(tmp_path)
    program = registry.create("Migration", owner="architect")
    registry.add_member(program["id"], "I001")
    registry.add_member(program["id"], "I002", requires=["I001"])
    with pytest.raises(ProgramError, match="written reason"):
        registry.waive(program["id"], "I002", "I001", reason="  ", actor="architect")
    registry.waive(program["id"], "I002", "I001", reason="hotfix ordering agreed", actor="architect")
    assert registry.blocking_predecessors("I002") == []


# --- W3.3 concurrent multi-team operation -------------------------------------------------


def test_intent_scope_contention_is_surfaced_and_can_be_co_approved(tmp_path: Path) -> None:
    store = register_intents(tmp_path, "I001", "I002")
    for intent_id in ("I001", "I002"):
        for target in ("framed", "specified", "planned", "building"):
            store.transition_intent(intent_id, target, actor="runner")
    coordinator = IntentResourceCoordinator(tmp_path)
    coordinator.declare("I001", ["schema:orders"])
    coordinator.declare("I002", ["schema:orders", "prompt:summary"])
    active = {key: value["status"] for key, value in store.read()["intents"].items()}

    conflicts = coordinator.contention("I002", active)
    assert [item["resource"] for item in conflicts] == ["schema:orders"]

    with pytest.raises(ResourceConflictError, match="written reason"):
        coordinator.coapprove("schema:orders", ["I001", "I002"], approver="architect", reason=" ")
    coordinator.coapprove("schema:orders", ["I001", "I002"], approver="architect", reason="coordinated expand/contract")
    assert coordinator.contention("I002", active) == []


def test_gate_approver_policy_prevents_gate_shopping(tmp_path: Path) -> None:
    write_yaml(
        tmp_path / ".ai-sdlc" / "config.yaml",
        {"gates": {"approvers": {"G1": ["principal", "staff-eng"]}}},
    )
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", actor="runner")
    with pytest.raises(TransitionError, match="may not approve"):
        store.approve_gate("I001", "G1", "convenient-bystander")
    store.approve_gate("I001", "G1", "principal")
    record = store.read()["gates"]["I001:G1"]
    # Who *may* approve is recorded alongside who did, and pinned at decision time so a
    # later policy change cannot rewrite the history.
    assert record["approver"] == "principal"
    assert record["permitted_approvers"] == ["principal", "staff-eng"]


def test_merge_readiness_flags_a_stale_verified_base(tmp_path: Path) -> None:
    from aisdlc.orchestration import MergeReadiness

    base = init_git(tmp_path)
    write_yaml(tmp_path / ".ai-sdlc" / "config.yaml", {"merge": {"max_base_lag": 1, "integration_ref": "HEAD"}})
    for index in range(3):
        (tmp_path / f"file-{index}.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", f"c{index}"], cwd=tmp_path, check=True)
    task_dir = tmp_path / "delivery/intents/I001-x/04-build/I001-P00/tasks/I001-P00-T001"
    task_dir.mkdir(parents=True)
    report = MergeReadiness(tmp_path).assess("I001-P00-T001", task_dir, expected_commit=base)
    assert any("commits behind" in error for error in report.errors)


# --- W3.4 specification drift --------------------------------------------------------------


def test_per_requirement_hashes_scope_evidence_invalidation(tmp_path: Path) -> None:
    """A reopened gate must invalidate only the evidence tracing to what actually changed."""
    spec_root = tmp_path / "02-specification"
    write_yaml(
        spec_root / "requirements.yaml",
        {
            "functional_requirements": [
                {"id": "REQ-F-001", "statement": "A", "acceptance_criteria": ["AC-001"]},
                {"id": "REQ-F-002", "statement": "B", "acceptance_criteria": ["AC-002"]},
            ],
            "acceptance_criteria": [
                {"id": "AC-001", "verification_class": "D", "check": "a"},
                {"id": "AC-002", "verification_class": "D", "check": "b"},
            ],
        },
    )
    before = requirement_hashes(spec_root)
    write_yaml(
        spec_root / "requirements.yaml",
        {
            "functional_requirements": [
                {"id": "REQ-F-001", "statement": "A", "acceptance_criteria": ["AC-001"]},
                {"id": "REQ-F-002", "statement": "B, revised", "acceptance_criteria": ["AC-002"]},
            ],
            "acceptance_criteria": [
                {"id": "AC-001", "verification_class": "D", "check": "a"},
                {"id": "AC-002", "verification_class": "D", "check": "b"},
            ],
        },
    )
    after = requirement_hashes(spec_root)
    assert before["REQ-F-001"] == after["REQ-F-001"]
    assert before["REQ-F-002"] != after["REQ-F-002"]


def test_unchanged_requirement_keeps_its_evidence_after_a_spec_revision(tmp_path: Path) -> None:
    from aisdlc.evidence import EvidenceRunner

    intent_root = tmp_path / "delivery/intents/I001-x"
    spec = intent_root / "02-specification" / "requirements.yaml"
    write_yaml(
        spec,
        {
            "functional_requirements": [
                {"id": "REQ-F-001", "statement": "A", "acceptance_criteria": []},
                {"id": "REQ-F-002", "statement": "B", "acceptance_criteria": []},
            ],
            "acceptance_criteria": [],
        },
    )
    task_dir = intent_root / "04-build/I001-P00/tasks/I001-P00-T001"
    write_yaml(
        task_dir / "task.yaml",
        {
            "id": "I001-P00-T001",
            "requirements": ["REQ-F-001"],
            "holdout_waiver": {"reason": "covered by baseline", "approved_by": "owner"},
        },
    )
    init_git(tmp_path)
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", actor="runner")
    store.transition_intent("I001", "specified", actor="runner")
    store.approve_gate("I001", "G2", "owner")
    manifest = EvidenceRunner(tmp_path).capture("I001-P00-T001", task_dir, ["python -c \"print('ok')\""])
    assert validate_task_evidence(manifest, tmp_path, task_dir).valid

    # Edit only REQ-F-002. This Task maps to REQ-F-001, so its evidence still stands.
    write_yaml(
        spec,
        {
            "functional_requirements": [
                {"id": "REQ-F-001", "statement": "A", "acceptance_criteria": []},
                {"id": "REQ-F-002", "statement": "B, revised", "acceptance_criteria": []},
            ],
            "acceptance_criteria": [],
        },
    )
    report = validate_task_evidence(manifest, tmp_path, task_dir)
    assert report.valid, report.errors

    # Editing the requirement this Task *does* map to invalidates it.
    write_yaml(
        spec,
        {
            "functional_requirements": [
                {"id": "REQ-F-001", "statement": "A, revised", "acceptance_criteria": []},
                {"id": "REQ-F-002", "statement": "B, revised", "acceptance_criteria": []},
            ],
            "acceptance_criteria": [],
        },
    )
    assert not validate_task_evidence(manifest, tmp_path, task_dir).valid


# --- W3.5 brownfield baseline ---------------------------------------------------------------


def test_baseline_pins_current_behaviour_without_asserting_correctness(tmp_path: Path) -> None:
    init_git(tmp_path)
    service = BaselineService(tmp_path)
    manifest = service.capture(
        [
            {"name": "suite", "command": 'python -c "import sys; sys.exit(1)"'},
            {"name": "coverage", "command": "python -c \"print('coverage 42%')\"", "metric": r"coverage (\d+)%"},
        ],
        label="legacy entry",
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["id"].startswith("BASELINE-")
    # A failing suite is a fact about the repository, recorded rather than rejected.
    assert payload["probes"][0]["exit_code"] == 1
    assert payload["probes"][1]["value"] == 42.0
    assert service.load(payload["id"])["id"] == payload["id"]


def test_baseline_comparison_reports_regressions_not_mere_difference(tmp_path: Path) -> None:
    init_git(tmp_path)
    service = BaselineService(tmp_path)
    manifest = service.capture(
        [{"name": "coverage", "command": "python -c \"print('coverage 42%')\"", "metric": r"coverage (\d+)%"}]
    )
    baseline_id = json.loads(manifest.read_text(encoding="utf-8"))["id"]

    improved = service.compare(
        baseline_id,
        [{"name": "coverage", "command": "python -c \"print('coverage 55%')\"", "metric": r"coverage (\d+)%"}],
    )
    assert improved["passed"] is True
    assert improved["improvements"][0]["after"] == 55.0

    worse = service.compare(
        baseline_id,
        [{"name": "coverage", "command": "python -c \"print('coverage 30%')\"", "metric": r"coverage (\d+)%"}],
    )
    assert worse["passed"] is False
    assert worse["regressions"][0]["reason"] == "metric decreased"


def test_a_tampered_baseline_is_rejected(tmp_path: Path) -> None:
    init_git(tmp_path)
    service = BaselineService(tmp_path)
    manifest = service.capture([{"name": "suite", "command": "python -c \"print('ok')\""}])
    baseline_id = json.loads(manifest.read_text(encoding="utf-8"))["id"]
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["probes"][0]["exit_code"] = 99
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BaselineError, match="hash"):
        service.load(baseline_id)


# --- W3.6 migration safety ---------------------------------------------------------------------


def test_migration_requires_rollback_or_recorded_irreversibility() -> None:
    reversible = [
        {
            "id": "I001-P01-T001",
            "phase_id": "I001-P01",
            "migration": {"forward_command": "migrate up", "invariant_commands": ["check"]},
        }
    ]
    assert any("rollback_command" in error for error in validate_migrations(reversible))

    irreversible = [
        {
            "id": "I001-P01-T001",
            "phase_id": "I001-P01",
            "migration": {
                "forward_command": "migrate up",
                "irreversible": True,
                "invariant_commands": ["check"],
            },
        }
    ]
    assert any("recorded acceptance" in error for error in validate_migrations(irreversible))

    accepted = [
        {
            "id": "I001-P01-T001",
            "phase_id": "I001-P01",
            "migration": {
                "forward_command": "migrate up",
                "irreversible": True,
                "irreversibility_accepted_by": "data-owner",
                "invariant_commands": ["check"],
            },
        }
    ]
    assert validate_migrations(accepted) == []


def test_expand_and_contract_must_not_share_a_phase() -> None:
    tasks = [
        {
            "id": "I001-P01-T001",
            "phase_id": "I001-P01",
            "migration": {
                "forward_command": "up",
                "rollback_command": "down",
                "invariant_commands": ["check"],
                "reads_fields": ["orders.legacy_total"],
            },
        },
        {
            "id": "I001-P01-T002",
            "phase_id": "I001-P01",
            "migration": {
                "forward_command": "up",
                "rollback_command": "down",
                "invariant_commands": ["check"],
                "drops_fields": ["orders.legacy_total"],
            },
        },
    ]
    errors = validate_migrations(tasks)
    assert any("separate phases" in error for error in errors)

    # Moving the drop into a later phase is exactly the parallel-change discipline.
    tasks[1]["phase_id"] = "I001-P02"
    assert validate_migrations(tasks) == []
