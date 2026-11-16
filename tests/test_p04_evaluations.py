from __future__ import annotations

import json
from pathlib import Path

import pytest

from aisdlc.evaluations import DatasetRegistry, EvaluationError, EvaluationRunner, validate_run_manifest


def pins() -> dict[str, object]:
    return {
        "model_id": "answer@2026-08-01",
        "prompt_version": "answer@v1",
        "prompt_hash": "sha256:" + "a" * 64,
        "temperature": 0.0,
        "seed": 7,
    }


def register_dataset(root: Path, trigger: str | None = None) -> DatasetRegistry:
    dataset = root / "cases"
    dataset.mkdir()
    (dataset / "cases.json").write_text('[{"id":"c1"},{"id":"c2"},{"id":"c3"}]\n', encoding="utf-8")
    registry = DatasetRegistry(root)
    registry.register("gold/example-v1", dataset, owner="quality", version="1", trigger=trigger)
    return registry


def statistical_spec() -> dict[str, object]:
    return {
        "id": "EVAL-001",
        "verification_class": "S",
        "dataset": "gold/example-v1",
        "metric": "accuracy",
        "threshold": {"operator": ">=", "value": 0.90},
        "tolerance_band": 0.03,
        "minimum_sample_size": 3,
        "pins": pins(),
    }


def test_dataset_registry_versions_hashes_and_inherits_by_trigger(tmp_path: Path) -> None:
    registry = register_dataset(tmp_path, trigger="contains_model_behavior")
    original = registry.resolve("gold/example-v1")["hash"]
    assert registry.resolve("gold/example-v1")["selection_policy"] == "all"
    assert registry.inherit({"contains_model_behavior": True}) == ["gold/example-v1"]
    assert registry.inherit({"contains_model_behavior": False}) == []
    (tmp_path / "cases" / "cases.json").write_text('[{"id":"changed"}]\n', encoding="utf-8")
    assert registry.compute_hash(tmp_path / "cases") != original


def test_declining_an_inherited_suite_requires_and_records_escalation(tmp_path: Path) -> None:
    registry = register_dataset(tmp_path, trigger="contains_model_behavior")
    with pytest.raises(EvaluationError, match="reason"):
        registry.decline_inherited("I001", "gold/example-v1", "")
    record = registry.decline_inherited("I001", "gold/example-v1", "Dataset owner is unavailable")
    assert record["status"] == "escalated"
    assert (tmp_path / ".ai-sdlc" / "runtime" / "suite-declines.yaml").exists()


def test_statistical_evaluation_uses_three_runs_median_and_tolerance(tmp_path: Path) -> None:
    register_dataset(tmp_path)
    results = [
        {"score": 0.91, "cases": [1, 2, 3], "latency_seconds": 1.0, "cost_usd": 0.01},
        {"score": 0.93, "cases": [1, 2, 3], "latency_seconds": 1.1, "cost_usd": 0.01},
        {"score": 0.88, "cases": [1, 2, 3], "latency_seconds": 0.9, "cost_usd": 0.01},
    ]
    path = EvaluationRunner(tmp_path).evaluate(statistical_spec(), results, tmp_path / "runs")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["result"]["median"] == 0.91
    assert manifest["result"]["minimum"] == 0.88
    assert manifest["result"]["confidence_interval_95"]["low"] < manifest["result"]["median"]
    assert manifest["result"]["confidence_interval_95"]["high"] > manifest["result"]["median"]
    assert manifest["result"]["passed"] is False
    assert manifest["result"]["flaky"] is True
    assert manifest["result"]["decision"] == "flaky"


def test_judged_evaluation_rejects_missing_or_stale_calibration(tmp_path: Path) -> None:
    register_dataset(tmp_path)
    spec = statistical_spec() | {"verification_class": "J"}
    with pytest.raises(EvaluationError, match="calibration"):
        EvaluationRunner(tmp_path).evaluate(spec, [{"score": 0.9, "cases": [1, 2, 3]}] * 3, tmp_path / "runs")
    spec["judge"] = {
        "model_id": "judge@v2",
        "prompt_hash": "sha256:" + "b" * 64,
        "calibration": {"agreement": 0.85, "model_id": "judge@v1", "prompt_hash": "sha256:" + "b" * 64},
    }
    with pytest.raises(EvaluationError, match="stale"):
        EvaluationRunner(tmp_path).evaluate(spec, [{"score": 0.9, "cases": [1, 2, 3]}] * 3, tmp_path / "runs")


def test_judged_evaluation_records_calibrated_confusion_matrix(tmp_path: Path) -> None:
    register_dataset(tmp_path)
    spec = statistical_spec() | {
        "verification_class": "J",
        "judge": {
            "model_id": "judge@v2",
            "prompt_version": "judge-prompt@v3",
            "prompt_hash": "sha256:" + "b" * 64,
            "dual_order": True,
            "reasoning_before_score": True,
            "rubric": [
                {"id": "RC-001", "type": "binary", "check": "every claim is supported by a cited span"},
                {"id": "RC-002", "type": "binary", "check": "no unsupported entity is introduced"},
            ],
            "exemplars": [
                {"id": "EX-001", "label": "pass", "annotation": "every claim maps to a cited span"},
                {"id": "EX-002", "label": "fail", "annotation": "fluent but the date is not in the source"},
            ],
            "calibration": {
                "agreement": 0.9,
                "model_id": "judge@v2",
                "prompt_version": "judge-prompt@v3",
                "prompt_hash": "sha256:" + "b" * 64,
                "confusion_matrix": {"true_positive": 5, "true_negative": 4, "false_positive": 1, "false_negative": 0},
            },
        },
    }
    path = EvaluationRunner(tmp_path).evaluate(spec, [{"score": 0.95, "cases": [1, 2, 3]}] * 3, tmp_path / "runs")
    judge = json.loads(path.read_text(encoding="utf-8"))["judge"]
    assert judge["calibration"]["confusion_matrix"]["false_positive"] == 1


def test_negative_evaluation_reports_zero_observed_successes_without_zero_risk_claim(tmp_path: Path) -> None:
    register_dataset(tmp_path)
    spec = {
        "id": "EVAL-002",
        "verification_class": "N",
        "dataset": "gold/example-v1",
        "metric": "attacker_successes",
        "threshold": {"operator": "==", "value": 0},
        "minimum_sample_size": 3,
        "pins": pins(),
    }
    passed_path = EvaluationRunner(tmp_path).evaluate(
        spec, [{"cases": [{"success": False}, {"success": False}, {"success": False}]}], tmp_path / "runs"
    )
    passed = json.loads(passed_path.read_text(encoding="utf-8"))["result"]
    assert passed["observed_successes"] == 0
    assert passed["claim"] == "zero observed successes"
    failed_path = EvaluationRunner(tmp_path).evaluate(
        spec, [{"cases": [{"success": False}, {"success": True}, {"success": False}]}], tmp_path / "runs"
    )
    assert json.loads(failed_path.read_text(encoding="utf-8"))["result"]["passed"] is False


def test_changed_pins_invalidate_a_run_manifest(tmp_path: Path) -> None:
    register_dataset(tmp_path)
    path = EvaluationRunner(tmp_path).evaluate(
        statistical_spec(), [{"score": 0.91, "cases": [1, 2, 3]}] * 3, tmp_path / "runs"
    )
    changed = pins() | {"prompt_version": "answer@v2"}
    report = validate_run_manifest(path, expected_pins=changed)
    assert not report.valid
    assert "prompt_version" in report.errors[0]
