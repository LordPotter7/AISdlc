from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from aisdlc.config import ConfigError, load_config
from aisdlc.identifiers import IdentifierCollisionError, IdentifierRegistry
from aisdlc.state import AuditCorruptionError, StateStore, TransitionError
from aisdlc.tasks import ResourceConflictError, ResourceLockManager, select_ready_tasks
from aisdlc.traceability import generate_traceability
from aisdlc.validation import validate_intent
from aisdlc.vendor import yaml


def write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def create_intent(root: Path) -> Path:
    intent = root / "delivery" / "intents" / "I001-example"
    write_yaml(
        intent / "00-intent" / "intent.yaml",
        {
            "id": "I001",
            "title": "Example",
            "intent_type": "feature",
            "status": "planned",
            "autonomy_mode": "autonomous",
            "contains_model_behavior": False,
            "artifact_mode": "compact",
            "trigger_flags": {},
            "created_at": "2026-08-23T09:00:00Z",
        },
    )
    write_yaml(
        intent / "02-specification" / "requirements.yaml",
        {
            "intent_id": "I001",
            "functional_requirements": [
                {
                    "id": "REQ-F-001",
                    "statement": "The runner shall report status.",
                    "acceptance_criteria": ["AC-001"],
                }
            ],
            "non_functional_requirements": [],
            "acceptance_criteria": [
                {
                    "id": "AC-001",
                    "verification_class": "D",
                    "statement": "The status command exits successfully.",
                    "check": "ai-sdlc status I001",
                }
            ],
            "success_criteria": [],
        },
    )
    write_yaml(
        intent / "04-build" / "I001-P00-walking-skeleton" / "phase.yaml",
        {
            "id": "I001-P00",
            "intent_id": "I001",
            "title": "Walking skeleton",
            "status": "pending",
            "objective": "Prove the path.",
            "requirements": ["REQ-F-001"],
            "depends_on": [],
            "entry_criteria": ["Specification approved"],
            "tasks": ["I001-P00-T001"],
            "exit_criteria": ["AC-001 passes"],
            "verification_commands": ["ai-sdlc status I001"],
        },
    )
    write_yaml(
        intent / "04-build" / "I001-P00-walking-skeleton" / "tasks" / "I001-P00-T001" / "task.yaml",
        {
            "id": "I001-P00-T001",
            "intent_id": "I001",
            "phase_id": "I001-P00",
            "title": "Implement status",
            "status": "pending",
            "objective": "Implement status.",
            "requirements": ["REQ-F-001"],
            "source_findings": [],
            "depends_on": [],
            "affected_paths": ["src/aisdlc/status.py"],
            "shared_resources": ["schema:runtime-state"],
            "inputs": ["requirements.yaml"],
            "expected_outputs": ["src/aisdlc/status.py"],
            "acceptance_criteria": [{"id": "AC-001", "verification_class": "D"}],
            "verification_commands": ["pytest -q"],
            "parallelizable": False,
            "retry_limit": 2,
        },
    )
    return intent


def test_configuration_defaults_and_rejects_unknown_fields(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert config.delivery_root == "delivery/intents"
    assert config.retry_limit == 2
    write_yaml(tmp_path / ".ai-sdlc" / "config.yaml", {"unexpected": True})
    with pytest.raises(ConfigError, match="unexpected"):
        load_config(tmp_path)


def test_identifier_registry_allocates_permanent_ids_and_detects_collisions(tmp_path: Path) -> None:
    registry = IdentifierRegistry(tmp_path)
    assert registry.allocate("intent") == "I001"
    assert registry.allocate("intent") == "I002"
    assert registry.allocate("phase", parent="I001") == "I001-P00"
    assert registry.allocate("task", parent="I001-P00") == "I001-P00-T001"
    registry.reserve("F-001")
    with pytest.raises(IdentifierCollisionError):
        registry.reserve("F-001")


def test_identifier_allocation_is_atomic_under_concurrency(tmp_path: Path) -> None:
    registry = IdentifierRegistry(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        identifiers = list(pool.map(lambda _: registry.allocate("finding"), range(40)))
    assert len(set(identifiers)) == 40
    assert sorted(identifiers) == [f"F-{index:03d}" for index in range(1, 41)]


def test_state_store_enforces_transitions_and_appends_audit_events(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    store.initialize_task("I001-P00-T001")
    store.transition_task("I001-P00-T001", "ready", actor="runner")
    store.transition_task("I001-P00-T001", "in_progress", actor="developer-01")
    with pytest.raises(TransitionError, match="in_progress -> merged"):
        store.transition_task("I001-P00-T001", "merged", actor="developer-01")
    reconstructed = StateStore.reconstruct(store.audit_path)
    assert reconstructed["tasks"]["I001-P00-T001"]["status"] == "in_progress"
    assert len(store.audit_path.read_text(encoding="utf-8").splitlines()) == 4


def test_gate_approval_requires_the_correct_lifecycle_stage_and_compact_combination(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    with pytest.raises(TransitionError, match="state framed"):
        store.approve_gate("I001", "G1", "owner")
    store.transition_intent("I001", "framed", actor="runner")
    store.approve_gate("I001", "G1", "owner")
    store.transition_intent("I001", "specified", actor="runner")
    store.approve_compact_gates("I001", "owner")
    gates = store.read()["gates"]
    assert gates["I001:G2"]["status"] == "approved"
    assert gates["I001:G3"]["status"] == "approved"


def test_corrupt_runtime_state_can_be_recovered_only_from_a_valid_audit_log(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", actor="runner")
    store.state_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(TransitionError, match="confirmation"):
        store.recover(confirm=False)
    recovered = store.recover(confirm=True)
    assert recovered["intents"]["I001"]["status"] == "framed"
    lines = store.audit_path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["sequence"] = 99
    lines[1] = json.dumps(second)
    store.audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AuditCorruptionError, match="sequence"):
        store.recover(confirm=True)


def test_terminal_and_recovery_task_transitions_are_explicit(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    for task_id in ("I001-P00-T001", "I001-P00-T002", "I001-P00-T003"):
        store.initialize_task(task_id)
        store.transition_task(task_id, "ready", actor="runner")
    store.transition_task("I001-P00-T001", "superseded", actor="owner")
    for target in ("in_progress", "verifying", "review", "verified", "merged", "reverted"):
        store.transition_task("I001-P00-T002", target, actor="runner")
    store.transition_task("I001-P00-T003", "in_progress", actor="runner")
    store.transition_task("I001-P00-T003", "blocked", actor="runner")
    store.transition_task("I001-P00-T003", "in_progress", actor="owner")
    assert store.read()["tasks"]["I001-P00-T001"]["status"] == "superseded"
    assert store.read()["tasks"]["I001-P00-T002"]["status"] == "reverted"
    assert store.read()["tasks"]["I001-P00-T003"]["status"] == "in_progress"


def test_validator_detects_broken_cross_references(tmp_path: Path) -> None:
    intent = create_intent(tmp_path)
    assert validate_intent(intent).valid
    task_path = next(intent.rglob("task.yaml"))
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["requirements"] = ["REQ-F-999"]
    write_yaml(task_path, task)
    report = validate_intent(intent)
    assert not report.valid
    assert any("REQ-F-999" in error for error in report.errors)


def test_full_mode_validator_requires_brief_plan_phase_and_model_eval_design(tmp_path: Path) -> None:
    intent = create_intent(tmp_path)
    intent_path = intent / "00-intent/intent.yaml"
    payload = yaml.safe_load(intent_path.read_text(encoding="utf-8"))
    payload["artifact_mode"] = "full"
    payload["contains_model_behavior"] = True
    write_yaml(intent_path, payload)
    report = validate_intent(intent)
    assert not report.valid
    assert any("01-brief/brief.md" in error for error in report.errors)
    assert any("03-planning/master-plan.md" in error for error in report.errors)
    assert any("eval-design.md" in error for error in report.errors)


def test_all_checked_in_json_schemas_are_well_formed() -> None:
    schema_root = Path(__file__).resolve().parents[1] / ".ai-sdlc" / "schemas"
    for schema_path in schema_root.glob("*.schema.json"):
        assert json.loads(schema_path.read_text(encoding="utf-8"))["$schema"].endswith("2020-12/schema")


def test_ready_task_selection_respects_dependencies_paths_and_resources() -> None:
    tasks = [
        {
            "id": "T1",
            "status": "pending",
            "depends_on": [],
            "affected_paths": ["src/a/"],
            "shared_resources": ["schema:x"],
        },
        {
            "id": "T2",
            "status": "pending",
            "depends_on": [],
            "affected_paths": ["src/b/"],
            "shared_resources": ["schema:x"],
        },
        {"id": "T3", "status": "pending", "depends_on": ["T1"], "affected_paths": ["src/c/"], "shared_resources": []},
        {"id": "T4", "status": "verified", "depends_on": [], "affected_paths": ["src/d/"], "shared_resources": []},
    ]
    assert [task["id"] for task in select_ready_tasks(tasks)] == ["T1"]


def test_resource_locks_are_exclusive_and_owner_checked(tmp_path: Path) -> None:
    locks = ResourceLockManager(tmp_path)
    locks.acquire("I001-P00-T001", ["schema:x", "prompt:y"])
    with pytest.raises(ResourceConflictError, match="schema:x"):
        locks.acquire("I001-P00-T002", ["schema:x"])
    with pytest.raises(ResourceConflictError, match="owned"):
        locks.release("I001-P00-T002", ["schema:x"])
    locks.release("I001-P00-T001", ["schema:x", "prompt:y"])
    assert locks.read() == {}


def test_traceability_is_generated_from_requirements_and_tasks(tmp_path: Path) -> None:
    intent = create_intent(tmp_path)
    output = generate_traceability(intent)
    payload = json.loads(output.read_text(encoding="utf-8"))
    link = payload["links"][0]
    assert link["requirement"] == "REQ-F-001"
    assert link["acceptance_criteria"] == ["AC-001"]
    assert link["tasks"] == ["I001-P00-T001"]
    assert link["artifacts"] == ["src/aisdlc/status.py"]
    assert payload["reverse_links"]["I001-P00-T001"] == ["REQ-F-001"]
    assert payload["generated_by"] == "runner"
