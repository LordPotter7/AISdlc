"""ADR-010: pipeline profiles. Tighten a framework default freely, loosen one only with a
waiver, and never reach the constitution. A profile is pinned when an Intent is created,
and a gate can need more than one different person."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aisdlc.cli import app
from aisdlc.evidence import holdout_coverage_errors
from aisdlc.pipeline import PipelineError, intent_types, load, pinned, weaker_than
from aisdlc.specquality import RULE_SEVERITY, lint_intent
from aisdlc.state import INTENT_TRANSITIONS, STAGE_ORDER, TASK_TRANSITIONS, StateStore
from aisdlc.typerlite import CliRunner
from aisdlc.vendor import yaml

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]

PIPELINE = {
    "version": 1,
    "by_type": {"security": "regulated", "investigation_or_poc": "spike"},
    "profiles": {
        "spike": {
            "description": "Time-boxed investigation.",
            "modes": ["compact"],
            "specification": {"fail_on": "never"},
            "waivers": [
                {
                    "rule": "specification.fail_on",
                    "approved_by": "head.of.eng",
                    "reason": "Spikes are thrown away",
                    "approved_at": "2026-09-25",
                }
            ],
        },
        "regulated": {
            "description": "Changes an auditor will read about.",
            "modes": ["full"],
            "required_artifacts": {"specified": ["02-specification/threat-model.yaml"]},
            "specification": {"fail_on": "warning", "rules": {"SQ009": "error"}},
            "gates": {"G1": {"approvals": 2, "approvers": ["lee", "priya", "sam"]}},
            "verification": {"holdout_waiver": "forbidden"},
        },
    },
}


def _run(root: Path, *args: str) -> Any:
    return runner.invoke(app, [*args, "--repo", str(root)])


def _ok(root: Path, *args: str) -> dict[str, Any]:
    result = _run(root, *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@example.invalid"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    return tmp_path


def _pipeline(root: Path, document: dict[str, Any]) -> None:
    (root / ".ai-sdlc").mkdir(exist_ok=True)
    (root / ".ai-sdlc" / "pipeline.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _new(root: Path, kind: str, *extra: str) -> dict[str, Any]:
    return _ok(root, "new", "--title", f"A {kind} change", "--type", kind, "--discovery-reason", "fixture", *extra)


def _spec(intent_root: Path, intent_id: str) -> None:
    (intent_root / "01-brief").mkdir(exist_ok=True)
    (intent_root / "01-brief" / "brief.md").write_text("# Brief\n", encoding="utf-8")
    (intent_root / "02-specification").mkdir(exist_ok=True)
    (intent_root / "02-specification" / "requirements.yaml").write_text(
        yaml.safe_dump(
            {
                "intent_id": intent_id,
                "functional_requirements": [
                    {
                        "id": "REQ-F-001",
                        "priority": "must",
                        "statement": "The system shall log it.",
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


def _audit(root: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in StateStore(root).audit_path.read_text(encoding="utf-8").splitlines()]


# -- the three tiers --------------------------------------------------------------------------------


def test_without_a_pipeline_every_intent_runs_standard_exactly_as_before(repository: Path) -> None:
    created = _new(repository, "feature")
    assert created["profile"] == "standard" and created["artifact_mode"] == "full"
    rules = pinned(Path(created["path"]))
    assert rules["name"] == "standard" and rules["loosened"] == [] and rules["fail_on"] == "error"
    assert all(policy["approvals"] == 1 for policy in rules["gates"].values())


def test_a_loosened_default_needs_its_waiver_and_the_constitution_has_no_field(repository: Path) -> None:
    unwaived = json.loads(json.dumps(PIPELINE))
    del unwaived["profiles"]["spike"]["waivers"]
    _pipeline(repository, unwaived)
    with pytest.raises(PipelineError, match="loosens specification.fail_on without a waiver"):
        load(repository)
    result = _run(repository, "new", "--title", "Spike", "--type", "investigation_or_poc", "--discovery-reason", "x")
    assert result.exit_code != 0 and "without a waiver" in result.output
    assert _run(repository, "pipeline").exit_code == 1

    for bad in ({"skip": True}, {"approvals": 0}, {"approvers": []}):
        document = json.loads(json.dumps(PIPELINE))
        document["profiles"]["regulated"]["gates"] = {"G2": bad}
        _pipeline(repository, document)
        with pytest.raises(PipelineError):
            load(repository)
    for escape in ({"verification": {"classes": ["D"]}}, {"stages": ["build"]}, {"evidence": {"pinned": False}}):
        document = json.loads(json.dumps(PIPELINE))
        document["profiles"]["regulated"].update(escape)
        _pipeline(repository, document)
        with pytest.raises(PipelineError):
            load(repository)

    _pipeline(repository, PIPELINE)
    spike = load(repository)["profiles"]["spike"]
    assert spike["loosened"] == [
        {
            "rule": "specification.fail_on",
            "from": "error",
            "to": "never",
            "waiver": {"approved_by": "head.of.eng", "reason": "Spikes are thrown away", "approved_at": "2026-09-25"},
        }
    ]


def test_the_type_picks_the_profile_and_a_weaker_one_cannot_be_chosen_by_hand(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    refused = _run(
        repository, "new", "--title", "Auth", "--type", "security", "--mode", "compact", "--discovery-reason", "x"
    )
    assert refused.exit_code != 0 and "allows only full" in refused.output
    security = _new(repository, "security")
    assert security["profile"] == "regulated" and security["artifact_mode"] == "full"
    assert _new(repository, "investigation_or_poc")["artifact_mode"] == "compact"
    weaker = _run(
        repository, "new", "--title", "Auth", "--type", "security", "--profile", "spike", "--discovery-reason", "x"
    )
    assert weaker.exit_code != 0 and "weaker" in weaker.output
    also_weaker = _run(
        repository, "new", "--title", "Feature", "--type", "feature", "--profile", "spike", "--discovery-reason", "x"
    )
    assert also_weaker.exit_code != 0, "an unmapped type runs the default, and a person may only choose above it"
    assert _new(repository, "feature", "--profile", "regulated")["profile"] == "regulated"
    pinned_event = [event for event in _audit(repository) if event.get("event") == "profile_pinned"][0]
    assert pinned_event["details"] == {"profile": "regulated", "loosened": []}


# -- pinned at creation -----------------------------------------------------------------------------


def test_a_pinned_profile_does_not_move_when_the_pipeline_changes(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    created = _new(repository, "security")
    intent_root = Path(created["path"])
    record = yaml.safe_load((intent_root / "00-intent" / "intent.yaml").read_text(encoding="utf-8"))
    assert record["profile"]["name"] == "regulated" and record["profile"]["sha256"].startswith("sha256:")
    document = json.loads(json.dumps(PIPELINE))
    document["profiles"]["regulated"]["specification"]["fail_on"] = "error"
    _pipeline(repository, document)
    assert pinned(intent_root)["fail_on"] == "warning", "work in flight keeps its rules"
    assert load(repository)["profiles"]["regulated"]["fail_on"] == "error"


def test_an_intent_moves_only_to_a_profile_at_least_as_strict(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    feature = _new(repository, "feature")
    refused = _run(repository, "intent-profile", feature["intent_id"], "spike", "--actor", "lee", "--reason", "x")
    assert refused.exit_code != 0 and "weaker" in refused.output
    moved = _ok(
        repository,
        "intent-profile",
        feature["intent_id"],
        "regulated",
        "--actor",
        "lee",
        "--reason",
        "it now touches authentication",
    )
    assert moved == {"intent_id": feature["intent_id"], "profile": "regulated", "from": "standard", "actor": "lee"}
    assert pinned(Path(feature["path"]))["name"] == "regulated"
    back = _run(repository, "intent-profile", feature["intent_id"], "standard", "--actor", "lee", "--reason", "x")
    assert back.exit_code != 0 and "weaker" in back.output
    event = [item for item in _audit(repository) if item.get("event") == "profile_changed"][-1]
    assert event["details"] == {"from": "standard", "to": "regulated", "reason": "it now touches authentication"}


# -- what a profile enforces ------------------------------------------------------------------------


def test_required_artifacts_and_the_lint_bar_follow_the_pinned_profile(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    security = _new(repository, "security")
    intent_id, intent_root = security["intent_id"], Path(security["path"])
    _spec(intent_root, intent_id)
    _ok(repository, "advance", intent_id, "framed")
    _ok(repository, "gate", "approve", intent_id, "G1", "--approver", "lee")
    _ok(repository, "gate", "approve", intent_id, "G1", "--approver", "priya")
    refused = _run(repository, "advance", intent_id, "specified")
    assert refused.exit_code != 0 and "requires 02-specification/threat-model.yaml before specified" in refused.output
    record = [event for event in _audit(repository) if event.get("event") == "advance_refused"][-1]
    assert record["details"]["check"] == "profile_artifacts"
    report = lint_intent(intent_root, "error")
    assert report["profile"] == "regulated" and report["fail_on"] == "warning"

    spike = _new(repository, "investigation_or_poc")
    _spec(Path(spike["path"]), spike["intent_id"])
    assert lint_intent(Path(spike["path"]), "error")["fail_on"] == "never"


def test_a_profile_can_count_a_rule_as_more_severe(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    security = _new(repository, "security")
    intent_root = Path(security["path"])
    _spec(intent_root, security["intent_id"])  # REQ-F-001 has neither rationale nor sources: SQ009
    findings = {item["rule"]: item["severity"] for item in lint_intent(intent_root, "error")["findings"]}
    assert RULE_SEVERITY["SQ009"] == "info" and findings["SQ009"] == "error"


def test_a_two_person_gate_waits_for_a_second_different_named_approver(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    security = _new(repository, "security")
    intent_id = security["intent_id"]
    _ok(repository, "advance", intent_id, "framed")
    first = _ok(repository, "gate", "approve", intent_id, "G1", "--approver", "lee")
    assert first["status"] == "awaiting_approvals" and first["approvals"] == ["lee"] and first["needed"] == 2
    assert StateStore(repository).read()["gates"].get(f"{intent_id}:G1", {}).get("status") != "approved"
    again = _run(repository, "gate", "approve", intent_id, "G1", "--approver", "lee")
    assert again.exit_code != 0 and "different people" in again.output
    outsider = _run(repository, "gate", "approve", intent_id, "G1", "--approver", "mallory")
    assert outsider.exit_code != 0 and "profile names" in outsider.output
    second = _ok(repository, "gate", "approve", intent_id, "G1", "--approver", "priya")
    assert second["status"] == "approved" and second["approvals"] == ["lee", "priya"]
    assert StateStore(repository).read()["gates"][f"{intent_id}:G1"]["approvers"] == ["lee", "priya"]

    # Reopening starts the count again: two people decide the new version too.
    _ok(repository, "gate", "reopen", intent_id, "G1", "--actor", "lee", "--reason", "scope moved")
    assert _ok(repository, "gate", "approve", intent_id, "G1", "--approver", "sam")["status"] == "awaiting_approvals"
    kinds = [event.get("event") for event in _audit(repository) if event.get("entity_id") == f"{intent_id}:G1"]
    assert kinds.count("gate_approval") == 2

    # State rebuilt from the audit log keeps every decision; a partial approval moves no state.
    before = StateStore(repository).read()["gates"][f"{intent_id}:G1"]["status"]
    _ok(repository, "repair-state", "--confirm")
    assert StateStore(repository).read()["gates"][f"{intent_id}:G1"]["status"] == before == "reopened"


def test_a_profile_can_forbid_held_out_waivers(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    security = _new(repository, "security")
    intent_root = Path(security["path"])
    task_root = intent_root / "04-build" / "P00" / "tasks" / "T001"
    task_root.mkdir(parents=True)
    (task_root / "task.yaml").write_text(
        yaml.safe_dump(
            {
                "id": f"{security['intent_id']}-P00-T001",
                "acceptance_criteria": [{"id": "AC-001", "verification_class": "D"}],
                "holdout_waiver": {"reason": "no second oracle exists", "approved_by": "lee"},
            }
        ),
        encoding="utf-8",
    )
    errors = holdout_coverage_errors(repository, intent_root)
    assert any("pipeline profile forbids" in error for error in errors), errors
    feature = _new(repository, "feature")
    (Path(feature["path"]) / "04-build" / "P00" / "tasks" / "T001").mkdir(parents=True)
    (Path(feature["path"]) / "04-build" / "P00" / "tasks" / "T001" / "task.yaml").write_text(
        (task_root / "task.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert not any("pipeline profile" in error for error in holdout_coverage_errors(repository, Path(feature["path"])))


def test_strictness_is_compared_rule_by_rule(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    profiles = load(repository)["profiles"]
    assert weaker_than(profiles["regulated"], profiles["regulated"]) == []
    reasons = weaker_than(profiles["regulated"], profiles["standard"])
    assert any("compact" in item for item in reasons) and any("lint bar" in item for item in reasons)
    assert any("G1 needs 1" in item for item in reasons) and any("held-out" in item for item in reasons)


# -- one source, and documentation proven to agree ---------------------------------------------------


def test_the_workflow_files_match_the_runner(repository: Path) -> None:
    workflow = ROOT / ".ai-sdlc" / "workflow"
    rules = yaml.safe_load((workflow / "transition-rules.yaml").read_text(encoding="utf-8"))
    assert {state: set(targets) for state, targets in rules["intent"].items()} == INTENT_TRANSITIONS
    assert {state: set(targets) for state, targets in rules["task"].items()} == TASK_TRANSITIONS
    lifecycle = yaml.safe_load((workflow / "lifecycle.yaml").read_text(encoding="utf-8"))
    assert [stage.get("gate") for stage in lifecycle["stages"] if stage.get("gate")] == ["G1", "G2", "G3", "G4", "G5"]
    assert len(lifecycle["stages"]) == 7 and "initialized" in STAGE_ORDER


def test_intent_types_have_one_source(repository: Path) -> None:
    from aisdlc.desktop.operations import INTENT_TYPES

    schema = json.loads((ROOT / ".ai-sdlc" / "schemas" / "intent.schema.json").read_text(encoding="utf-8"))
    assert intent_types(repository) == tuple(schema["properties"]["intent_type"]["enum"]) == INTENT_TYPES
    guide = yaml.safe_load((ROOT / ".ai-sdlc" / "framework-guide.yaml").read_text(encoding="utf-8"))
    assert {item["code"]: item["severity"] for item in guide["lint_rules"]} == RULE_SEVERITY


# -- Studio: the pipeline is shown, never edited; decisions go through the runner -------------------


def _bridge(root: Path):  # noqa: ANN202
    from aisdlc.desktop.bridge import Bridge

    bridge = Bridge()
    project = bridge.handle({"id": "o", "op": "project.open", "params": {"root": str(root)}})["project_id"]

    def call(op: str, **params: Any) -> dict[str, Any]:
        reply = bridge.handle({"id": op, "op": op, "params": {"project_id": project, **params}})
        assert reply["error"] is None, reply["error"]
        return reply["payload"]

    def run(name: str, **args: Any) -> dict[str, Any]:
        preview = call("operation.preview", name=name, args=args)
        return call(
            "operation.execute", name=name, args=args, token=preview["token"], base_hashes=preview["base_hashes"]
        )

    return call, run


def test_studio_shows_profiles_waivers_and_gate_progress(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    call, run = _bridge(repository)
    created = run(
        "intent_new",
        title="Harden login",
        intent_type="security",
        mode="full",
        discovery_reason="fixture",
        contains_model_behavior=False,
    )
    assert created["succeeded"], created
    feature = run(
        "intent_new",
        title="Search",
        intent_type="feature",
        mode="full",
        discovery_reason="fixture",
        contains_model_behavior=False,
        profile="regulated",
    )
    assert feature["succeeded"], feature

    view = call("pipeline.inspect")
    assert view["error"] is None and view["by_type"]["security"] == "regulated"
    assert view["profiles"]["spike"]["loosened"][0]["waiver"]["approved_by"] == "head.of.eng"
    assert len(view["constitution"]) >= 5
    by_id = {item["id"]: item for item in view["intents"]}
    assert by_id["I001"]["profile"] == "regulated" and by_id["I002"]["profile"] == "regulated"
    assert view["pinned_counts"] == {"regulated": 2}

    _ok(repository, "advance", "I001", "framed")
    run("gate_approve", intent_id="I001", gate="G1", approver="lee")
    detail = call("intent.inspect", intent_id="I001")
    assert detail["profile"]["name"] == "regulated"
    assert detail["gates"]["G1"]["needed"] == 2 and detail["gates"]["G1"]["approvals"] == ["lee"]
    inbox = [item for item in call("inbox.inspect")["items"] if item["kind"] == "gate"]
    assert "needs 2 different approvers; 1 so far (lee)" in inbox[0]["summary"]


def test_studio_moves_an_intent_only_to_a_stricter_profile(repository: Path) -> None:
    _pipeline(repository, PIPELINE)
    call, run = _bridge(repository)
    _new(repository, "feature")
    options = {item["id"]: item for item in call("pipeline.inspect")["intents"]}["I001"]["stricter"]
    assert options == ["regulated"], "spike is weaker, and standard is where it already is"
    moved = run("intent_profile", intent_id="I001", profile="regulated", actor="lee", reason="touches auth now")
    assert moved["succeeded"], moved
    refused = run("intent_profile", intent_id="I001", profile="spike", actor="lee", reason="x")
    assert refused["succeeded"] is False and "weaker" in (refused["error_text"] or "")
