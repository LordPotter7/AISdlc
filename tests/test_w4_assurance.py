"""Wave 4 — assurance and ecosystem, plus the signed-evidence chain it depends on.

The attestation tests sit here rather than with Wave 1 because they exercise the same
property the conformance export claims: that authoritative evidence can be verified by
signature instead of by filename.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from aisdlc.conformance import ConformanceExporter
from aisdlc.evidence import EvidenceRunner, validate_task_evidence
from aisdlc.observability import MetricsExporter
from aisdlc.scaffold import AGENTS_MD_BLOCK, MARKER_BEGIN, InitService
from aisdlc.state import StateStore
from aisdlc.vendor import yaml

PASS = "python -c \"print('ok')\""


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


def capture_evidence(root: Path, task_dir: Path) -> Path:
    return EvidenceRunner(root).capture(
        "I001-P00-T001",
        task_dir,
        [PASS],
        holdout_commands=[PASS],
        holdout_authorship={"authored_by": "spec", "context": "specification"},
    )


# --- W4.1 conformance export -----------------------------------------------------------------


def build_intent(root: Path) -> Path:
    intent_root = root / "delivery/intents/I001-x"
    write_yaml(
        intent_root / "00-intent/intent.yaml",
        {
            "id": "I001",
            "title": "Support assistant",
            "intent_type": "feature",
            "status": "building",
            "autonomy_mode": "autonomous",
            "contains_model_behavior": False,
            "artifact_mode": "compact",
            "trigger_flags": {"multi_tenant": True},
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    write_yaml(
        intent_root / "02-specification/requirements.yaml",
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
    return intent_root


def test_conformance_export_projects_existing_records_onto_assurance_frameworks(tmp_path: Path) -> None:
    init_git(tmp_path)
    intent_root = build_intent(tmp_path)
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", actor="runner")
    store.approve_gate("I001", "G1", "principal")

    export = ConformanceExporter(tmp_path).export_intent(intent_root)
    register = export["system_register"]
    assert register["system_id"] == "I001"
    assert register["risk_indicators"]["multi_tenant"] is True
    # The accountability record an auditor asks for is already present as gate approvals.
    assert register["human_oversight"][0] == {"gate": "G1", "status": "approved", "approver": "principal"}
    assert set(export["nist_ai_rmf"]) == {"GOVERN", "MAP", "MEASURE", "MANAGE"}
    assert "A.2 AI policy" in export["iso_42001_annex_a"]
    assert "PW.8 Test executable code" in export["nist_ssdf"]


def test_conformance_export_reports_gaps_honestly(tmp_path: Path) -> None:
    """An export that always looked complete would be worthless to its reader."""
    init_git(tmp_path)
    intent_root = build_intent(tmp_path)
    StateStore(tmp_path).initialize_intent("I001")
    export = ConformanceExporter(tmp_path).export_intent(intent_root)
    # No evidence captured yet, so the evidence-bearing controls must show as gaps.
    assert "evidence_manifests" in export["gaps"]
    assert "conformance_gap" in export["gaps"]
    assert export["nist_ai_rmf"]["MEASURE"]["status"] == "partial"
    rendered = ConformanceExporter.render(export)
    assert "System register entry" in rendered
    assert "NIST AI RMF" in rendered


def test_conformance_export_is_read_only(tmp_path: Path) -> None:
    """A projection must not be able to change a lifecycle outcome."""
    init_git(tmp_path)
    intent_root = build_intent(tmp_path)
    StateStore(tmp_path).initialize_intent("I001")
    before = json.dumps(StateStore(tmp_path).read(), sort_keys=True)
    ConformanceExporter(tmp_path).export_intent(intent_root)
    assert json.dumps(StateStore(tmp_path).read(), sort_keys=True) == before


# --- W4.2 agent-agnostic doctrine --------------------------------------------------------------


def test_init_emits_agents_md_so_any_agent_receives_the_doctrine(tmp_path: Path) -> None:
    init_git(tmp_path)
    InitService(tmp_path).run(mode="compact")
    agents = tmp_path / "AGENTS.md"
    assert agents.exists()
    body = agents.read_text(encoding="utf-8")
    assert MARKER_BEGIN in body
    # The rules that make the framework work must survive the port to another agent.
    assert "The instrument that decides a criterion is not authored" in body
    assert "different model family" in body


def test_agents_md_block_states_what_the_runner_refuses() -> None:
    assert "What the runner will refuse" in AGENTS_MD_BLOCK
    assert "Release and deployment are outside this lifecycle" in AGENTS_MD_BLOCK


# --- W4.3 security posture ------------------------------------------------------------------------


def test_evidence_outside_a_declared_posture_is_invalid_when_required(tmp_path: Path) -> None:
    write_yaml(tmp_path / ".ai-sdlc" / "config.yaml", {"security": {"require_declared_posture": True}})
    init_git(tmp_path)
    task_dir = tmp_path / "delivery/intents/I001-x/04-build/I001-P00/tasks/I001-P00-T001"
    unposted = capture_evidence(tmp_path, task_dir)
    report = validate_task_evidence(unposted, tmp_path, task_dir)
    assert any("records no execution posture" in error for error in report.errors)

    posted = EvidenceRunner(tmp_path).capture(
        "I001-P00-T001",
        task_dir,
        [PASS],
        holdout_commands=[PASS],
        holdout_authorship={"authored_by": "spec", "context": "specification"},
        posture={"network_policy": "deny", "filesystem_scope": "worktree", "credential_surface": "none"},
    )
    assert validate_task_evidence(posted, tmp_path, task_dir).valid


def test_init_ships_security_standing_checks(tmp_path: Path) -> None:
    init_git(tmp_path)
    InitService(tmp_path).run(mode="compact")
    path = tmp_path / ".ai-sdlc" / "security" / "standing-checks.yaml"
    assert path.exists()
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    names = {check["name"] for check in payload["checks"]}
    assert "security/secret-scan" in names
    # Secret scanning and dependency audit are unconditional: deciding in advance which
    # changes cannot leak a credential is not a decision anyone can make correctly.
    unconditional = {check["name"] for check in payload["checks"] if check["trigger"] is None}
    assert {"security/secret-scan", "security/dependency-audit"} <= unconditional


# --- W4.4 metrics export ----------------------------------------------------------------------------


def test_metrics_export_labels_dora_numbers_as_proxies(tmp_path: Path) -> None:
    init_git(tmp_path)
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    for target in ("framed", "specified", "planned", "building", "converging", "awaiting_acceptance", "completed"):
        store.transition_intent("I001", target, actor="runner")
    payload = MetricsExporter(tmp_path).collect()
    dora = payload["dora_proxy"]
    assert dora["available"] is True
    assert dora["intents_closed"] == 1
    # Release is outside this lifecycle, so the export must not claim to measure deployment.
    assert "not artifacts deployed" in dora["note"]


def test_openmetrics_output_emits_only_numeric_gauges(tmp_path: Path) -> None:
    init_git(tmp_path)
    exporter = MetricsExporter(tmp_path)
    payload = exporter.collect()
    payload["loop_metrics"]["dormant_criteria"] = ["AC-001"]  # non-numeric, must be skipped
    rendered = exporter.to_openmetrics(payload)
    assert "aisdlc_loop_dormant_criteria" not in rendered
    assert "aisdlc_verification_tax" in rendered
    assert rendered.rstrip().endswith("# EOF")
