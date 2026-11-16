"""Wave 2 — adaptivity and economics.

The loop already measured whether it catches defects. These cover what it costs, and
whether its instruments still carry information.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aisdlc.config import JUDGE_AGREEMENT_FLOOR, effective_judge_agreement, load_config
from aisdlc.costs import CostLedger, gate_latencies
from aisdlc.evaluations import DatasetRegistry, EvaluationError, EvaluationRunner
from aisdlc.gatepacket import GatePacketService
from aisdlc.lessons import LoopMetrics
from aisdlc.state import StateStore
from aisdlc.vendor import yaml


def write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def judge_spec(**overrides) -> dict:
    judge = {
        "model_id": "opus-5",
        "prompt_version": "judge@v1",
        "prompt_hash": "sha256:" + "b" * 64,
        "dual_order": True,
        "reasoning_before_score": True,
        "rubric": [{"id": "RC-001", "type": "binary", "check": "claims are cited"}],
        "exemplars": [
            {"id": "EX-001", "label": "pass", "annotation": "every claim maps to a span"},
            {"id": "EX-002", "label": "fail", "annotation": "fluent but unsupported"},
        ],
        "calibration": {
            "agreement": 0.9,
            "model_id": "opus-5",
            "prompt_version": "judge@v1",
            "prompt_hash": "sha256:" + "b" * 64,
            "confusion_matrix": {
                "true_positive": 5,
                "true_negative": 4,
                "false_positive": 1,
                "false_negative": 0,
            },
        },
    }
    judge.update(overrides)
    return {
        "id": "EVAL-001",
        "verification_class": "J",
        "dataset": "suite",
        "metric": "groundedness",
        "threshold": {"operator": ">=", "value": 0.9},
        "minimum_sample_size": 3,
        "tolerance_band": 0.02,
        "pins": {
            "model_id": "claude-fable-5",
            "prompt_version": "p@v1",
            "prompt_hash": "sha256:" + "a" * 64,
            "temperature": 0.0,
            "seed": 7,
        },
        "judge": judge,
    }


def register_suite(root: Path) -> None:
    suite = root / "data" / "suite.json"
    suite.parent.mkdir(parents=True, exist_ok=True)
    suite.write_text(json.dumps([{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]), encoding="utf-8")
    DatasetRegistry(root).register("suite", suite, owner="quality", version="v1")


# --- W1.5 / W2 judge calibration -------------------------------------------------------


def test_configured_agreement_cannot_drop_below_the_framework_floor(tmp_path: Path) -> None:
    """An old 0.80 left in a target repository must not survive the upgrade."""
    write_yaml(tmp_path / ".ai-sdlc" / "config.yaml", {"evaluations": {"judge_minimum_agreement": 0.80}})
    assert effective_judge_agreement(load_config(tmp_path)) == JUDGE_AGREEMENT_FLOOR

    write_yaml(tmp_path / ".ai-sdlc" / "config.yaml", {"evaluations": {"judge_minimum_agreement": 0.95}})
    assert effective_judge_agreement(load_config(tmp_path)) == 0.95


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"dual_order": False}, "both orderings"),
        ({"rubric": [{"id": "RC-001", "type": "scale", "check": "quality 1-10"}]}, "binary"),
        ({"exemplars": []}, "exemplars"),
        ({"reasoning_before_score": False}, "reasoning before the score"),
        ({"model_id": "claude-fable-5"}, "own\nfamily's output".replace("\n", " ")),
    ],
)
def test_judge_calibration_rejects_each_documented_bias(tmp_path: Path, override: dict, expected: str) -> None:
    """Order bias, length-confidence bias, anchor drift, rationalisation, family bias."""
    register_suite(tmp_path)
    spec = judge_spec(**override)
    if "model_id" in override:
        # Family bias: the judge must not share a family with the generator it grades.
        spec["judge"]["calibration"]["model_id"] = override["model_id"]
    runs = [{"score": 0.95, "cases": [1, 2, 3]}] * 3
    with pytest.raises(EvaluationError, match=expected):
        EvaluationRunner(tmp_path).evaluate(spec, runs, tmp_path / "runs")


def test_judge_run_records_a_human_review_queue(tmp_path: Path) -> None:
    """The escape hatch is only auditable if the routed fraction is recorded."""
    register_suite(tmp_path)
    runs = [
        {
            "score": 0.95,
            "cases": [
                {"id": "c1", "margin": 0.9},
                {"id": "c2", "margin": 0.01},
                {"id": "c3", "order_disagreement": True, "margin": 0.8},
            ],
        }
    ] * 3
    manifest = EvaluationRunner(tmp_path).evaluate(judge_spec(), runs, tmp_path / "runs")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    queue = payload["judge_human_review"]
    assert queue["routed"] >= 1
    # A case where the two orderings disagreed is maximally uncertain, whatever margin
    # it reported, so it must sort ahead of a confident case.
    assert queue["cases"][0]["margin"] == 0.0
    assert payload["judge_agreement_floor"] == JUDGE_AGREEMENT_FLOOR


# --- W2.1 criterion discrimination ------------------------------------------------------


def test_criterion_discrimination_separates_dormant_from_discriminating(tmp_path: Path) -> None:
    intent_root = tmp_path / "delivery/intents/I001-x"
    write_yaml(
        intent_root / "02-specification/requirements.yaml",
        {
            "acceptance_criteria": [
                {"id": "AC-001", "verification_class": "D"},
                {"id": "AC-002", "verification_class": "D"},
            ]
        },
    )
    write_yaml(
        intent_root / "05-verification/findings.yaml",
        {"findings": [{"id": "F-001", "criterion": "AC-001", "severity": "high"}]},
    )
    metrics = LoopMetrics.criterion_discrimination([intent_root])
    assert metrics["criteria_declared"] == 2
    assert metrics["criterion_discrimination"] == 0.5
    # AC-002 never failed: either a solved problem or a dead instrument, and the framework
    # cannot tell which — so it proposes a review rather than retiring anything itself.
    assert metrics["dormant_criteria"] == ["AC-002"]


def test_a_criterion_that_failed_on_an_earlier_attempt_counts_as_discriminating(tmp_path: Path) -> None:
    """The retry is the evidence the instrument works; only reading the latest attempt hides it."""
    intent_root = tmp_path / "delivery/intents/I001-x"
    write_yaml(
        intent_root / "02-specification/requirements.yaml",
        {"acceptance_criteria": [{"id": "AC-001", "verification_class": "D"}]},
    )
    task_dir = intent_root / "04-build/I001-P00/tasks/I001-P00-T001"
    write_yaml(
        task_dir / "task.yaml",
        {"id": "I001-P00-T001", "acceptance_criteria": [{"id": "AC-001", "verification_class": "D"}]},
    )
    for attempt, passed in ((1, False), (2, True)):
        manifest = task_dir / "evidence" / f"attempt-{attempt}" / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"passed": passed}), encoding="utf-8")
    metrics = LoopMetrics.criterion_discrimination([intent_root])
    assert metrics["criterion_discrimination"] == 1.0
    assert metrics["dormant_criteria"] == []


# --- W2.2 cost instrumentation ----------------------------------------------------------


def test_cost_ledger_measures_operations_including_failures(tmp_path: Path) -> None:
    write_yaml(tmp_path / ".ai-sdlc" / "config.yaml", {"cost": {"enabled": True}})
    ledger = CostLedger(tmp_path)
    with ledger.measure("start-task", intent_id="I001", entity_id="I001-P00-T001") as usage:
        usage["usd"] = 1.0
    with pytest.raises(RuntimeError), ledger.measure("verify-task", intent_id="I001") as usage:
        usage["usd"] = 3.0
        raise RuntimeError("verification blew up")
    summary = ledger.summary(intent_id="I001")
    assert summary["entries"] == 2
    # A failed verification is the expensive case; excluding it would understate the tax.
    assert summary["verification_usd"] == 3.0
    assert summary["implementation_usd"] == 1.0
    assert summary["verification_tax"] == 3.0


def test_cost_ledger_falls_back_to_wall_clock_when_no_money_is_reported(tmp_path: Path) -> None:
    write_yaml(tmp_path / ".ai-sdlc" / "config.yaml", {"cost": {"enabled": True}})
    ledger = CostLedger(tmp_path)
    ledger.record("start-task", wall_clock_seconds=10.0, intent_id="I001")
    ledger.record("verify-task", wall_clock_seconds=5.0, intent_id="I001")
    assert ledger.summary(intent_id="I001")["verification_tax"] == 0.5


def test_gate_latency_measures_the_wait_a_human_actually_had(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", actor="runner")
    store.approve_gate("I001", "G1", "owner")
    latencies = gate_latencies(tmp_path)
    assert "I001:G1" in latencies
    assert latencies["I001:G1"] >= 0


# --- W2.3 gate packet -------------------------------------------------------------------


def test_gate_packet_surfaces_outstanding_checks_before_a_human_looks(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    intent_root = tmp_path / "delivery/intents/I001-x"
    write_yaml(
        intent_root / "00-intent/intent.yaml",
        {
            "id": "I001",
            "title": "Example",
            "intent_type": "feature",
            "status": "specified",
            "autonomy_mode": "autonomous",
            "contains_model_behavior": False,
            "artifact_mode": "compact",
            "trigger_flags": {},
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
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", actor="runner")
    store.transition_intent("I001", "specified", actor="runner")
    packet = GatePacketService(tmp_path).build(intent_root, "G2")
    assert packet["gate"] == "G2"
    assert packet["question"].startswith("Are these requirements")
    assert packet["changes_since_last_presentation"]["first_presentation"] is True
    rendered = GatePacketService.render(packet)
    assert "Decision required" in rendered
    assert "Outstanding checks" in rendered


def test_gate_packet_rejects_an_unknown_gate(tmp_path: Path) -> None:
    intent_root = tmp_path / "delivery/intents/I001-x"
    write_yaml(intent_root / "00-intent/intent.yaml", {"id": "I001", "title": "x"})
    with pytest.raises(ValueError, match="unknown gate"):
        GatePacketService(tmp_path).build(intent_root, "G9")


# --- W2.4 sequential stopping ------------------------------------------------------------


def test_sequential_stopping_accepts_a_range_of_runs_but_fixed_policy_does_not(tmp_path: Path) -> None:
    """Only the sample size adapts. The decision rule stays predeclared."""
    register_suite(tmp_path)
    spec = {
        "id": "EVAL-002",
        "verification_class": "S",
        "dataset": "suite",
        "metric": "recall",
        "threshold": {"operator": ">=", "value": 0.8},
        "minimum_sample_size": 3,
        "tolerance_band": 0.05,
        "pins": {
            "model_id": "claude-fable-5",
            "prompt_version": "p@v1",
            "prompt_hash": "sha256:" + "a" * 64,
            "temperature": 0.0,
            "seed": 1,
        },
    }
    runs = [{"score": 0.95, "cases": [1, 2, 3]}] * 4

    write_yaml(tmp_path / ".ai-sdlc" / "config.yaml", {"evaluations": {"sequential_stopping": False}})
    with pytest.raises(EvaluationError, match="exactly 3 runs"):
        EvaluationRunner(tmp_path).evaluate(spec, runs, tmp_path / "runs-fixed")

    write_yaml(
        tmp_path / ".ai-sdlc" / "config.yaml",
        {"evaluations": {"sequential_stopping": True, "max_runs": 5}},
    )
    manifest = EvaluationRunner(tmp_path).evaluate(spec, runs, tmp_path / "runs-seq")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["sampling"]["policy"] == "sequential"
    assert payload["sampling"]["runs"] == 4
    assert payload["sampling"]["extended"] is True
