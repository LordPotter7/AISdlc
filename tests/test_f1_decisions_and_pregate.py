"""F1/F2 architecture as a traceable decision, F3 pre-gate review, F6 discovery, F5 retrospective."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aisdlc.decisions import DecisionError, DecisionRegistry, canonical_hash, decision_hashes, load_decisions
from aisdlc.evidence import EvidenceRunner, specification_pin, validate_task_evidence
from aisdlc.identifiers import IdentifierRegistry
from aisdlc.lessons import LessonRegistry
from aisdlc.migrate import MigrationService
from aisdlc.pregate import PreGateReviewError, PreGateReviewService, subject_hash
from aisdlc.retrospective import PhaseRetrospective, RetrospectiveError
from aisdlc.scaffold import InitService
from aisdlc.state import StateStore, TransitionError
from aisdlc.traceability import generate_traceability
from aisdlc.validation import validate_intent
from aisdlc.vendor import yaml
from aisdlc.versioning import SCHEMA_VERSION

REPOSITORY = Path(__file__).resolve().parents[1]

REQUIREMENTS = {
    "intent_id": "I001",
    "functional_requirements": [
        {"id": "REQ-F-001", "statement": "The service answers health checks.", "acceptance_criteria": ["AC-001"]}
    ],
    "non_functional_requirements": [],
    "acceptance_criteria": [
        {"id": "AC-001", "statement": "GET /health returns 200.", "verification_class": "D", "check": "echo ok"}
    ],
    "success_criteria": [{"id": "SC-001", "statement": "Health endpoint is live."}],
}

INTENT = {
    "id": "I001",
    "title": "Add a health endpoint",
    "intent_type": "feature",
    "status": "initialized",
    "autonomy_mode": "autonomous",
    "contains_model_behavior": False,
    "artifact_mode": "compact",
    "trigger_flags": {},
    "created_at": "2026-08-31T00:00:00+00:00",
}

DECISION = {
    "intent_id": "I001",
    "title": "Serve health from the existing HTTP app",
    "context": "A second process would need its own deployment and its own failure mode.",
    "decision": "Health is a route on the existing application, not a sidecar.",
    "consequences": "One deployable, and health shares the app's fate — which is what we want it to report.",
    "alternatives": [{"option": "Sidecar process", "rejected_because": "Reports its own liveness, not the app's."}],
    "requirements": ["REQ-F-001"],
    "affects": ["src/app/http.py"],
}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    InitService(tmp_path).run()
    (tmp_path / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "seed"], check=True)
    return tmp_path


@pytest.fixture()
def intent_root(repo: Path) -> Path:
    root = repo / "delivery" / "intents" / "I001-health"
    (root / "00-intent").mkdir(parents=True)
    (root / "02-specification").mkdir(parents=True)
    (root / "00-intent" / "intent.yaml").write_text(yaml.safe_dump(INTENT), encoding="utf-8")
    (root / "02-specification" / "requirements.yaml").write_text(yaml.safe_dump(REQUIREMENTS), encoding="utf-8")
    return root


def _task(intent_root: Path, task_id: str = "I001-P00-T001", **overrides) -> Path:
    directory = intent_root / "04-build" / "I001-P00-compact" / "tasks" / task_id
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": task_id,
        "intent_id": "I001",
        "phase_id": task_id.rsplit("-T", 1)[0],
        "title": "Add the route",
        "status": "pending",
        "objective": "Serve /health",
        "requirements": ["REQ-F-001"],
        "source_findings": [],
        "depends_on": [],
        "affected_paths": ["src/app/http.py"],
        "shared_resources": [],
        "inputs": [],
        "expected_outputs": ["src/app/http.py"],
        "acceptance_criteria": [{"id": "AC-001", "verification_class": "D"}],
        "verification_commands": ["echo ok"],
        "parallelizable": False,
        "retry_limit": 2,
        **overrides,
    }
    (directory / "task.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    return directory


# --- F1: architecture is a record, hashed per decision ---------------------------------


def test_the_runner_allocates_decision_identifiers(repo: Path, intent_root: Path) -> None:
    first = DecisionRegistry(repo).record(intent_root, dict(DECISION))
    second = DecisionRegistry(repo).record(intent_root, dict(DECISION, title="Second call"))

    assert first["id"] == "I001-D001"
    assert second["id"] == "I001-D002"
    assert (intent_root / "03-planning" / "decisions" / "I001-D001.yaml").is_file()
    assert first["schema_version"] == SCHEMA_VERSION


def test_a_model_chosen_identifier_is_refused(repo: Path, intent_root: Path) -> None:
    with pytest.raises(DecisionError, match="allocated by the runner"):
        DecisionRegistry(repo).record(intent_root, dict(DECISION, id="I001-D009"))


def test_accepting_a_decision_does_not_change_its_content_hash(repo: Path, intent_root: Path) -> None:
    """The property scoped invalidation depends on: bookkeeping must not look like substance.

    If accepting a proposal changed the hash, approving architecture would invalidate the
    evidence of every Task built against it — which would teach people to record
    architecture late, or not at all.
    """
    recorded = DecisionRegistry(repo).record(repo / "delivery" / "intents" / "I001-health", dict(DECISION))
    before = canonical_hash(recorded)

    accepted = DecisionRegistry(repo).accept(intent_root, recorded["id"])

    assert accepted["status"] == "accepted"
    assert canonical_hash(accepted) == before


def test_superseding_preserves_the_earlier_decision(repo: Path, intent_root: Path) -> None:
    first = DecisionRegistry(repo).record(intent_root, dict(DECISION))
    DecisionRegistry(repo).record(intent_root, dict(DECISION, title="Sidecar after all", supersedes=first["id"]))

    records = {record["id"]: record for record in load_decisions(intent_root)}

    assert records[first["id"]]["status"] == "superseded"
    assert records[first["id"]]["superseded_by"] == "I001-D002"
    # The historical record stays readable. That is the point of recording why.
    assert records[first["id"]]["decision"] == DECISION["decision"]


def test_editing_a_decision_changes_only_its_own_hash(repo: Path, intent_root: Path) -> None:
    registry = DecisionRegistry(repo)
    first = registry.record(intent_root, dict(DECISION))
    second = registry.record(intent_root, dict(DECISION, title="Unrelated call"))
    before = decision_hashes(intent_root)

    path = intent_root / "03-planning" / "decisions" / f"{second['id']}.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["decision"] = "Something else entirely."
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    after = decision_hashes(intent_root)
    assert after[first["id"]] == before[first["id"]]
    assert after[second["id"]] != before[second["id"]]


# --- F2: the doctrine's third trace target is real -------------------------------------


def test_the_shipped_doctrine_names_only_trace_targets_the_schema_carries() -> None:
    """The drift instrument for F2.

    `CLAUDE.md` — and the identical block `init` writes into every adopting repository —
    promises a Task may trace to a requirement, a finding, or a decision. Before this, the
    third was unsatisfiable. This asserts the doctrine and the schema cannot part again.
    """
    from aisdlc.scaffold import CLAUDE_MD_BLOCK

    doctrine = (REPOSITORY / "CLAUDE.md").read_text(encoding="utf-8")
    properties = json.loads((REPOSITORY / ".ai-sdlc" / "schemas" / "task.schema.json").read_text(encoding="utf-8"))[
        "properties"
    ]

    for surface in (doctrine, CLAUDE_MD_BLOCK):
        assert "requirement, finding, or decision" in surface
    assert "requirements" in properties
    assert "source_findings" in properties
    assert "source_decisions" in properties


def test_a_task_traces_to_a_decision(repo: Path, intent_root: Path) -> None:
    recorded = DecisionRegistry(repo).record(intent_root, dict(DECISION))
    DecisionRegistry(repo).accept(intent_root, recorded["id"])
    _task(intent_root, source_decisions=[recorded["id"]])

    report = validate_intent(intent_root)

    assert report.valid, report.errors


def test_an_unknown_decision_reference_is_refused(repo: Path, intent_root: Path) -> None:
    _task(intent_root, source_decisions=["I001-D404"])

    errors = validate_intent(intent_root).errors

    assert any("references unknown decision I001-D404" in error for error in errors)


def test_a_task_may_not_trace_to_a_superseded_decision(repo: Path, intent_root: Path) -> None:
    first = DecisionRegistry(repo).record(intent_root, dict(DECISION))
    DecisionRegistry(repo).record(intent_root, dict(DECISION, title="Replacement", supersedes=first["id"]))
    _task(intent_root, source_decisions=[first["id"]])

    errors = validate_intent(intent_root).errors

    assert any("superseded decision" in error for error in errors)


def test_an_accepted_decision_with_no_task_is_reported(repo: Path, intent_root: Path) -> None:
    recorded = DecisionRegistry(repo).record(intent_root, dict(DECISION))
    DecisionRegistry(repo).accept(intent_root, recorded["id"])
    _task(intent_root)

    errors = validate_intent(intent_root).errors

    assert any("accepted decision has no Task mapping: I001-D001" in error for error in errors)


def test_traceability_carries_decisions_in_both_directions(repo: Path, intent_root: Path) -> None:
    recorded = DecisionRegistry(repo).record(intent_root, dict(DECISION))
    DecisionRegistry(repo).accept(intent_root, recorded["id"])
    _task(intent_root, source_decisions=[recorded["id"]])

    payload = json.loads(generate_traceability(intent_root).read_text(encoding="utf-8"))

    assert payload["decision_links"][0]["decision"] == "I001-D001"
    assert payload["decision_links"][0]["tasks"] == ["I001-P00-T001"]
    assert payload["decision_reverse_links"]["I001-P00-T001"] == ["I001-D001"]
    assert payload["schema_version"] == SCHEMA_VERSION


# --- F1: decision drift invalidates only the Tasks that declared it --------------------


def test_evidence_pins_the_decisions_a_task_declared(repo: Path, intent_root: Path) -> None:
    recorded = DecisionRegistry(repo).record(intent_root, dict(DECISION))
    directory = _task(intent_root, source_decisions=[recorded["id"]])

    pin = specification_pin(repo, directory)

    assert pin["decision_hashes"]["I001-D001"] == canonical_hash(recorded)


def test_a_changed_decision_invalidates_the_evidence_that_declared_it(repo: Path, intent_root: Path) -> None:
    recorded = DecisionRegistry(repo).record(intent_root, dict(DECISION))
    directory = _task(intent_root, source_decisions=[recorded["id"]])
    StateStore(repo).read()
    manifest = EvidenceRunner(repo).capture("I001-P00-T001", directory, ["echo ok"])

    path = intent_root / "03-planning" / "decisions" / "I001-D001.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["decision"] = "Health moves to a sidecar after all."
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    errors = validate_task_evidence(manifest, repo, directory).errors
    assert any("decision I001-D001 changed after this evidence was captured" in error for error in errors)


def test_a_task_that_declares_no_decision_is_unaffected_by_one_changing(repo: Path, intent_root: Path) -> None:
    """Proportionality: recording architecture must not make unrelated evidence fragile."""
    DecisionRegistry(repo).record(intent_root, dict(DECISION))
    directory = _task(intent_root)
    manifest = EvidenceRunner(repo).capture("I001-P00-T001", directory, ["echo ok"])

    path = intent_root / "03-planning" / "decisions" / "I001-D001.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["decision"] = "Changed."
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    errors = validate_task_evidence(manifest, repo, directory).errors
    assert not any("decision" in error for error in errors), errors


# --- F1/F2: the migration this schema change exercises ---------------------------------


def test_migration_brings_an_existing_traceability_record_to_generation_two(repo: Path, intent_root: Path) -> None:
    """The first migration to run against a record that already existed in the field."""
    _task(intent_root)
    output = generate_traceability(intent_root)
    stale = json.loads(output.read_text(encoding="utf-8"))
    stale.pop("decision_links")
    stale.pop("decision_reverse_links")
    stale["schema_version"] = 1
    output.write_text(json.dumps(stale), encoding="utf-8")

    planned = MigrationService(repo).plan()
    assert any(entry["from"] == 1 and entry["to"] == 2 for entry in planned["pending"])

    MigrationService(repo).apply()

    migrated = json.loads(output.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == SCHEMA_VERSION
    assert migrated["decision_links"] == []
    assert migrated["decision_reverse_links"] == {}
    # The migration must not have invented links it could not know about.
    assert migrated["links"] == stale["links"]


# --- F3: adversarial review before the gate --------------------------------------------


def _review(**overrides) -> dict:
    return {
        "intent_id": "I001",
        "gate": "G2",
        "reviewer_agent_id": "aisdlc-red-team",
        "reviewer_model": "claude-opus-5",
        "decision": "approved",
        "findings": [],
        **overrides,
    }


def test_a_recorded_review_is_pinned_to_what_it_reviewed(repo: Path, intent_root: Path) -> None:
    service = PreGateReviewService(repo)
    service.record(intent_root, _review())

    assert service.status(intent_root, "I001", "G2")["current"] is True

    requirements = intent_root / "02-specification" / "requirements.yaml"
    requirements.write_text(requirements.read_text(encoding="utf-8") + "\n# edited after review\n", encoding="utf-8")

    stale = service.status(intent_root, "I001", "G2")
    assert stale["current"] is False
    assert "changed after this review" in stale["stale_reason"]


def test_a_reviewer_sharing_the_authors_model_family_is_refused(repo: Path, intent_root: Path) -> None:
    with pytest.raises(PreGateReviewError, match="shares a family"):
        PreGateReviewService(repo).record(
            intent_root, _review(reviewer_model="claude-opus-5", author_model="claude-opus-5-20260101")
        )


def test_an_unattributed_review_is_refused(repo: Path, intent_root: Path) -> None:
    review = _review()
    del review["reviewer_model"]

    with pytest.raises(PreGateReviewError, match="reviewer_model"):
        PreGateReviewService(repo).record(intent_root, review)


def test_gate_two_refuses_without_a_recorded_review(repo: Path, intent_root: Path) -> None:
    store = StateStore(repo)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", "human")
    store.approve_gate("I001", "G1", "alex")
    store.transition_intent("I001", "specified", "human")

    with pytest.raises(TransitionError, match="adversarial review"):
        store.approve_gate("I001", "G2", "alex")

    PreGateReviewService(repo).record(intent_root, _review())
    store.approve_gate("I001", "G2", "alex")
    assert store.read()["gates"]["I001:G2"]["status"] == "approved"


def test_a_stale_review_does_not_satisfy_the_gate(repo: Path, intent_root: Path) -> None:
    store = StateStore(repo)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", "human")
    store.approve_gate("I001", "G1", "alex")
    store.transition_intent("I001", "specified", "human")
    PreGateReviewService(repo).record(intent_root, _review())
    requirements = intent_root / "02-specification" / "requirements.yaml"
    requirements.write_text(requirements.read_text(encoding="utf-8") + "\n# slipped in\n", encoding="utf-8")

    with pytest.raises(TransitionError, match="earlier version"):
        store.approve_gate("I001", "G2", "alex")


def test_changes_required_still_leaves_the_decision_to_the_human(repo: Path, intent_root: Path) -> None:
    """A review informs the gate; it does not decide it."""
    store = StateStore(repo)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", "human")
    store.approve_gate("I001", "G1", "alex")
    store.transition_intent("I001", "specified", "human")
    PreGateReviewService(repo).record(intent_root, _review(decision="changes_required", findings=[{"id": "F-001"}]))

    store.approve_gate("I001", "G2", "alex")

    assert store.read()["gates"]["I001:G2"]["status"] == "approved"


def test_the_gate_packet_says_when_nobody_reviewed_the_artifact(repo: Path, intent_root: Path) -> None:
    from aisdlc.gatepacket import GatePacketService

    StateStore(repo).initialize_intent("I001")
    packet = GatePacketService(repo).build(intent_root, "G2")
    rendered = GatePacketService.render(packet)

    assert packet["adversarial_review"]["recorded"] is False
    assert "No adversarial review recorded" in rendered


def test_the_gate_packet_reports_a_recorded_review(repo: Path, intent_root: Path) -> None:
    from aisdlc.gatepacket import GatePacketService

    StateStore(repo).initialize_intent("I001")
    PreGateReviewService(repo).record(intent_root, _review(decision="changes_required", findings=[{"id": "F-001"}]))

    rendered = GatePacketService.render(GatePacketService(repo).build(intent_root, "G2"))

    assert "changes_required" in rendered
    assert "1 finding(s)" in rendered


def test_gate_one_needs_no_adversarial_review(repo: Path, intent_root: Path) -> None:
    """The control applies to the specification and the plan, not to framing."""
    assert PreGateReviewService(repo).status(intent_root, "I001", "G1") == {"applicable": False}
    assert subject_hash(intent_root, "G1").startswith("sha256:")


# --- F6: the decision to skip discovery ------------------------------------------------


def test_declining_discovery_is_recorded_on_the_intent_and_in_the_audit_log(repo: Path) -> None:
    from aisdlc.cli import app
    from aisdlc.typerlite import CliRunner

    result = CliRunner().invoke(
        app,
        [
            "new",
            "--title",
            "Small fix",
            "--type",
            "bugfix",
            "--mode",
            "compact",
            "--no-discovery",
            "--discovery-reason",
            "one-line change to code the team wrote last week",
            "--repo",
            str(repo),
        ],
    )
    assert result.exit_code == 0, result.output

    intent = yaml.safe_load(
        next((repo / "delivery" / "intents").glob("I001-*/00-intent/intent.yaml")).read_text(encoding="utf-8")
    )
    assert intent["discovery"]["included"] is False
    assert "one-line change" in intent["discovery"]["reason"]

    audit = [json.loads(line) for line in (repo / ".ai-sdlc" / "runtime" / "audit.jsonl").read_text().splitlines()]
    assert any(event.get("event") == "discovery_declined" for event in audit)


def test_a_reason_is_required_in_both_directions(repo: Path) -> None:
    from aisdlc.cli import app
    from aisdlc.typerlite import CliRunner

    result = CliRunner().invoke(app, ["new", "--title", "X", "--type", "feature", "--discovery", "--repo", str(repo)])

    assert result.exit_code != 0


def test_a_recorded_judgment_does_not_disturb_audit_recovery(repo: Path) -> None:
    """Non-transition events occupy a sequence number and contribute no state."""
    store = StateStore(repo)
    store.initialize_intent("I001")
    store.record_event("intent", "I001", "discovery_declined", actor="human", details={"reason": "greenfield"})
    store.transition_intent("I001", "framed", "human")

    rebuilt = store.recover(confirm=True)

    assert rebuilt["intents"]["I001"]["status"] == "framed"


# --- F5: phase retrospective -----------------------------------------------------------


def _finding(intent_root: Path, finding_id: str, severity: str, task_id: str) -> None:
    path = intent_root / "05-verification" / "findings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"findings": []}
    existing["findings"].append(
        {
            "id": finding_id,
            "severity": severity,
            "status": "open",
            "source": f"task {task_id}",
            "description": "Retry logic swallowed the underlying error.",
            "impact": "Failures look like flakes.",
            "recommended_action": "Propagate the cause.",
            "introduced_stage": "stage_4_implementation",
            "detected_stage": "stage_5_verification",
            "evidence": f"{task_id}/evidence/attempt-1/manifest.json",
        }
    )
    path.write_text(yaml.safe_dump(existing), encoding="utf-8")


def test_a_retrospective_refuses_while_the_phase_is_still_moving(repo: Path, intent_root: Path) -> None:
    store = StateStore(repo)
    store.initialize_task("I001-P01-T001")

    with pytest.raises(RetrospectiveError, match="still has work in flight"):
        PhaseRetrospective(repo).record(intent_root, "I001", "I001-P01", actor="human")


def test_a_retrospective_names_high_findings_that_have_not_become_lessons(repo: Path, intent_root: Path) -> None:
    store = StateStore(repo)
    for task_id in ("I001-P01-T001",):
        store.initialize_task(task_id)
        for target in ("ready", "in_progress", "verifying", "review", "verified"):
            store.transition_task(task_id, target, "runner")
    _finding(intent_root, "F-001", "high", "I001-P01-T001")
    _finding(intent_root, "F-002", "low", "I001-P01-T001")

    report = PhaseRetrospective(repo).record(intent_root, "I001", "I001-P01", actor="human")

    assert report["complete"] is True
    assert [item["id"] for item in report["unpromoted_findings"]] == ["F-001"]
    audit = [json.loads(line) for line in (repo / ".ai-sdlc" / "runtime" / "audit.jsonl").read_text().splitlines()]
    assert any(event.get("event") == "phase_retrospective" for event in audit)


def test_a_finding_already_covered_by_a_lesson_is_not_reproposed(repo: Path, intent_root: Path) -> None:
    store = StateStore(repo)
    store.initialize_task("I001-P01-T001")
    for target in ("ready", "in_progress", "verifying", "review", "verified"):
        store.transition_task("I001-P01-T001", target, "runner")
    _finding(intent_root, "F-001", "critical", "I001-P01-T001")
    LessonRegistry(repo).propose(
        {
            "id": "LESSON-001",
            "source_intent": "I001",
            "source_findings": ["F-001"],
            "severity_of_source": "critical",
            "category": "process",
            "status": "proposed",
            "observation": "Retry logic hid the cause.",
            "impact": "Failures were misread as flakes.",
            "targets": [
                {
                    "type": "checklist",
                    "artifact": ".ai-sdlc/checklists/retry.md",
                    "enforcement": "blocking",
                    "fires_at": ["stage_4_implementation"],
                }
            ],
            "review_after_intents": 5,
        }
    )

    report = PhaseRetrospective(repo).assess(intent_root, "I001-P01")

    assert report["unpromoted_findings"] == []


def test_findings_from_another_phase_are_not_this_phases_business(repo: Path, intent_root: Path) -> None:
    store = StateStore(repo)
    store.initialize_task("I001-P01-T001")
    for target in ("ready", "in_progress", "verifying", "review", "verified"):
        store.transition_task("I001-P01-T001", target, "runner")
    _finding(intent_root, "F-009", "high", "I001-P02-T001")

    report = PhaseRetrospective(repo).assess(intent_root, "I001-P01")

    assert report["findings"] == []


def test_decision_identifiers_are_rejected_when_malformed() -> None:
    from aisdlc.identifiers import PATTERNS

    assert PATTERNS["decision"].fullmatch("I001-D001")
    assert not PATTERNS["decision"].fullmatch("I001-P00-D001")
    assert not PATTERNS["decision"].fullmatch("D001")


def test_decision_allocation_requires_a_parent(repo: Path) -> None:
    from aisdlc.identifiers import IdentifierError

    with pytest.raises(IdentifierError, match="requires a parent"):
        IdentifierRegistry(repo).allocate("decision")
