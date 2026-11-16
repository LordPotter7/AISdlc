"""Wave S1: requirement integrity, requirement-quality lint, and adoption hygiene."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aisdlc.cli import app
from aisdlc.config import ConfigError, load_config
from aisdlc.convergence import _in_scope_criteria
from aisdlc.gatepacket import GatePacketService
from aisdlc.scaffold import CLAUDE_MD_BLOCK, MARKER_BEGIN, MARKER_END, InitService
from aisdlc.specquality import blocking, lint_intent, lint_requirements
from aisdlc.traceability import generate_traceability
from aisdlc.typerlite import CliRunner
from aisdlc.validation import validate_intent
from aisdlc.vendor import yaml

runner = CliRunner()
REPOSITORY = Path(__file__).resolve().parents[1]

INTENT = {
    "id": "I001",
    "title": "Customer export",
    "intent_type": "feature",
    "status": "initialized",
    "autonomy_mode": "autonomous",
    "contains_model_behavior": False,
    "artifact_mode": "compact",
    "trigger_flags": {},
    "created_at": "2026-09-24T00:00:00+00:00",
}

# A specification the lint should find nothing blocking or warning-level in.
CLEAN = {
    "intent_id": "I001",
    "functional_requirements": [
        {
            "id": "REQ-F-001",
            "statement": "When an administrator requests an export, the service shall produce a CSV of all customers.",
            "acceptance_criteria": ["AC-001"],
            "priority": "must",
            "rationale": "Administrators reconcile the ledger monthly.",
            "sources": ["brief: Expected outcome"],
        }
    ],
    "non_functional_requirements": [
        {
            "id": "REQ-NFR-001",
            "statement": "The service shall complete an export of 100000 customers within 30 seconds.",
            "acceptance_criteria": ["AC-002"],
            "category": "performance",
            "measure": {"metric": "export duration", "target": 30, "unit": "s", "condition": "100000 rows"},
            "priority": "should",
            "sources": ["brief: Success measures"],
        }
    ],
    "acceptance_criteria": [
        {"id": "AC-001", "verification_class": "D", "statement": "Export test passes.", "check": "pytest -k export"},
        {"id": "AC-002", "verification_class": "D", "statement": "The benchmark stays under 30 s.", "check": "bench"},
    ],
    "success_criteria": [
        {"id": "SC-001", "statement": "Administrators export without support.", "acceptance_criteria": ["AC-001"]}
    ],
}


def _write(root: Path, requirements: dict, intent: dict | None = None) -> Path:
    (root / "00-intent").mkdir(parents=True, exist_ok=True)
    (root / "02-specification").mkdir(parents=True, exist_ok=True)
    (root / "00-intent" / "intent.yaml").write_text(yaml.safe_dump(intent or INTENT), encoding="utf-8")
    (root / "02-specification" / "requirements.yaml").write_text(yaml.safe_dump(requirements), encoding="utf-8")
    return root


def _rules(document: dict) -> set[str]:
    return {finding.rule for finding in lint_requirements(document)}


# -- S1.1 schema: optional and backward compatible ---------------------------------------


def test_enriched_specification_is_schema_valid(tmp_path: Path) -> None:
    root = _write(tmp_path / "I001-export", CLEAN)
    assert validate_intent(root, require_plan=False).errors == ()


def test_a_minimal_pre_s1_specification_is_still_valid(tmp_path: Path) -> None:
    minimal = {
        "intent_id": "I001",
        "functional_requirements": [{"id": "REQ-F-001", "statement": "It works.", "acceptance_criteria": ["AC-001"]}],
        "non_functional_requirements": [],
        "acceptance_criteria": [{"id": "AC-001", "verification_class": "D", "statement": "ok", "check": "true"}],
        "success_criteria": [{"id": "SC-001", "statement": "done"}],
    }
    root = _write(tmp_path / "I001-min", minimal)
    assert validate_intent(root, require_plan=False).errors == ()


def test_unknown_quality_category_is_rejected(tmp_path: Path) -> None:
    document = copy.deepcopy(CLEAN)
    document["non_functional_requirements"][0]["category"] = "speediness"
    root = _write(tmp_path / "I001-bad", document)
    assert any("category" in error for error in validate_intent(root, require_plan=False).errors)


# -- S1.2 integrity -------------------------------------------------------------------------


def test_orphan_criterion_is_an_error(tmp_path: Path) -> None:
    document = copy.deepcopy(CLEAN)
    document["acceptance_criteria"].append(
        {"id": "AC-003", "verification_class": "D", "statement": "Nobody owns this.", "check": "true"}
    )
    root = _write(tmp_path / "I001-orphan", document)
    assert "AC-003 is not owned by any requirement" in validate_intent(root, require_plan=False).errors


def test_success_criterion_reference_must_resolve(tmp_path: Path) -> None:
    document = copy.deepcopy(CLEAN)
    document["success_criteria"][0]["acceptance_criteria"] = ["AC-099"]
    root = _write(tmp_path / "I001-sc", document)
    assert "SC-001 references unknown criterion AC-099" in validate_intent(root, require_plan=False).errors


def _deferred_intent(tmp_path: Path, *, task_maps_deferred: bool) -> Path:
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"].append(
        {
            "id": "REQ-F-002",
            "statement": "The service shall export to XLSX.",
            "acceptance_criteria": ["AC-003"],
            "priority": "wont",
            "rationale": "Deferred until finance confirms the format.",
        }
    )
    document["acceptance_criteria"].append(
        {"id": "AC-003", "verification_class": "D", "statement": "An XLSX opens.", "check": "true"}
    )
    root = _write(tmp_path / "I001-deferred", document)
    task_dir = root / "04-build" / "I001-P00-compact" / "tasks" / "I001-P00-T001"
    task_dir.mkdir(parents=True)
    requirements = ["REQ-F-001", "REQ-NFR-001"] + (["REQ-F-002"] if task_maps_deferred else [])
    task = {
        "id": "I001-P00-T001",
        "intent_id": "I001",
        "phase_id": "I001-P00",
        "title": "Export",
        "status": "pending",
        "objective": "Export customers",
        "requirements": requirements,
        "source_decisions": [],
        "depends_on": [],
        "affected_paths": ["src/export.py"],
        "shared_resources": [],
        "expected_outputs": ["src/export.py"],
        "acceptance_criteria": [{"id": "AC-001", "verification_class": "D", "statement": "passes"}],
        "verification_commands": ["pytest -k export"],
        "evidence_required": ["test output"],
        "retry_limit": 2,
    }
    (task_dir / "task.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    return root


def test_wont_requirement_needs_no_task_and_is_deferred_in_traceability(tmp_path: Path) -> None:
    root = _deferred_intent(tmp_path, task_maps_deferred=False)
    errors = validate_intent(root).errors
    assert not any("REQ-F-002" in error for error in errors), errors
    trace = json.loads(generate_traceability(root).read_text(encoding="utf-8"))
    status = {link["requirement"]: link["status"] for link in trace["links"]}
    assert status["REQ-F-002"] == "deferred"


def test_task_implementing_a_wont_requirement_is_scope_growth(tmp_path: Path) -> None:
    root = _deferred_intent(tmp_path, task_maps_deferred=True)
    assert any("prioritised `wont`" in error for error in validate_intent(root).errors)


def test_convergence_owes_no_evaluation_for_criteria_owned_only_by_deferred_scope() -> None:
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["priority"] = "wont"
    assert [item["id"] for item in _in_scope_criteria(document)] == ["AC-002"]


# -- S1.3 lint rules ------------------------------------------------------------------------


def test_clean_specification_has_no_warnings() -> None:
    findings = lint_requirements(CLEAN)
    assert [finding for finding in findings if finding.severity != "info"] == []


def test_placeholder_is_an_error() -> None:
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["statement"] = "The service shall {{DO_SOMETHING}}."
    findings = lint_requirements(document)
    assert any(finding.rule == "SQ001" and finding.severity == "error" for finding in findings)


def test_ambiguous_term_is_flagged_unless_the_glossary_defines_it() -> None:
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["statement"] = "The export shall be fast and user-friendly."
    assert "SQ002" in _rules(document)
    document["glossary"] = [
        {"term": "fast", "definition": "under 2 s at p95"},
        {"term": "user-friendly", "definition": "completes in one click"},
    ]
    assert "SQ002" not in _rules(document)


def test_ambiguity_matches_whole_words_only() -> None:
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["statement"] = "The service shall fasten something breakfast."
    assert "SQ002" not in _rules(document)


def test_open_ended_list_compound_and_missing_modal() -> None:
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["statement"] = "The service shall export CSV, JSON, etc. and must email it."
    assert {"SQ003", "SQ004"} <= _rules(document)
    document["functional_requirements"][0]["statement"] = "Exports go to CSV."
    assert "SQ006" in _rules(document)


def test_unmeasured_and_uncategorised_quality_requirement() -> None:
    document = copy.deepcopy(CLEAN)
    nfr = document["non_functional_requirements"][0]
    nfr["statement"] = "The service shall stay up during business hours."
    del nfr["measure"], nfr["category"]
    assert {"SQ005", "SQ007"} <= _rules(document)


def test_personal_data_without_protective_requirement() -> None:
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["personal_data"] = True
    assert "SQ010" in _rules(document)
    document["non_functional_requirements"][0]["compliance"] = [{"framework": "GDPR", "control": "Art. 32"}]
    assert "SQ010" not in _rules(document)


def test_prohibition_verified_only_by_class_d() -> None:
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["statement"] = "The service shall not export another tenant's rows."
    assert "SQ011" in _rules(document)


def test_success_criterion_without_criteria_is_flagged() -> None:
    document = copy.deepcopy(CLEAN)
    del document["success_criteria"][0]["acceptance_criteria"]
    assert "SQ012" in _rules(document)


def test_blocking_honours_the_configured_bar() -> None:
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["statement"] = "The export shall be fast."
    findings = lint_requirements(document)
    assert blocking(findings, "error") == []
    assert [finding.rule for finding in blocking(findings, "warning")] == ["SQ002"]
    assert blocking(findings, "never") == []


def test_lint_intent_reports_quality_coverage(tmp_path: Path) -> None:
    report = lint_intent(_write(tmp_path / "I001-export", CLEAN))
    assert report["passed"] is True
    assert report["quality_coverage"] == {"performance": ["REQ-NFR-001"]}
    assert report["prioritised"] == report["requirements"] == 2


# -- wiring: config, CLI, advance, gate packet -------------------------------------------


def test_specification_fail_on_rejects_unknown_values(tmp_path: Path) -> None:
    (tmp_path / ".ai-sdlc").mkdir()
    (tmp_path / ".ai-sdlc" / "config.yaml").write_text("specification:\n  fail_on: warn\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not one of"):
        load_config(tmp_path)


def test_shipped_config_declares_the_specification_bar() -> None:
    assert load_config(REPOSITORY).specification.fail_on == "error"


def _registered(tmp_path: Path) -> Path:
    created = runner.invoke(
        app,
        [
            "new",
            "--discovery-reason",
            "greenfield fixture",
            "--title",
            "Export",
            "--type",
            "feature",
            "--mode",
            "compact",
            "--repo",
            str(tmp_path),
        ],
    )
    assert created.exit_code == 0, created.output
    intent_root = Path(json.loads(created.output)["path"])
    (intent_root / "02-specification").mkdir(exist_ok=True)
    for command in (["advance", "I001", "framed"], ["gate", "approve", "I001", "G1", "--approver", "human"]):
        result = runner.invoke(app, [*command, "--repo", str(tmp_path)])
        assert result.exit_code == 0, result.output
    return intent_root


def test_advance_to_specified_refuses_blocking_lint_findings(tmp_path: Path) -> None:
    intent_root = _registered(tmp_path)
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["statement"] = "The service shall export customers. TBD: format."
    (intent_root / "02-specification" / "requirements.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    lint = runner.invoke(app, ["spec-lint", "I001", "--repo", str(tmp_path)])
    assert lint.exit_code == 1
    assert json.loads(lint.output)["blocking"][0]["rule"] == "SQ001"

    refused = runner.invoke(app, ["advance", "I001", "specified", "--repo", str(tmp_path)])
    assert refused.exit_code != 0
    assert "SQ001" in refused.output

    (intent_root / "02-specification" / "requirements.yaml").write_text(yaml.safe_dump(CLEAN), encoding="utf-8")
    accepted = runner.invoke(app, ["advance", "I001", "specified", "--repo", str(tmp_path)])
    assert accepted.exit_code == 0, accepted.output


def test_g2_gate_packet_carries_specification_quality(tmp_path: Path) -> None:
    intent_root = _registered(tmp_path)
    document = copy.deepcopy(CLEAN)
    document["functional_requirements"][0]["statement"] = "The service shall {{DO_IT}}."
    (intent_root / "02-specification" / "requirements.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    packet = GatePacketService(tmp_path).build(intent_root, "G2")
    assert packet["specification_quality"]["passed"] is False
    assert any(item.startswith("SQ001 REQ-F-001") for item in packet["outstanding"])
    assert GatePacketService(tmp_path).build(intent_root, "G1")["specification_quality"] is None
    rendered = GatePacketService.render(packet)
    assert "## Specification quality" in rendered and "**Blocked**" in rendered


# -- S1.4 adoption hygiene ---------------------------------------------------------------------


def test_init_adopts_an_unmarked_legacy_rules_block(tmp_path: Path) -> None:
    legacy = CLAUDE_MD_BLOCK.rstrip() + "\n"
    (tmp_path / "CLAUDE.md").write_text("# House rules\n\n" + legacy, encoding="utf-8")
    InitService(tmp_path).run()
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.count(CLAUDE_MD_BLOCK.strip().splitlines()[0]) == 1
    assert MARKER_BEGIN in text and MARKER_END in text
    assert text.startswith("# House rules")


def test_this_repository_carries_one_rules_block() -> None:
    text = (REPOSITORY / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.count("# AI SDLC repository rules") == 1
