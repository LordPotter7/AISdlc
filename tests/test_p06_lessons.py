from __future__ import annotations

from pathlib import Path

import pytest

from aisdlc.lessons import LessonError, LessonRegistry, LoopMetrics


def lesson(severity: str = "critical") -> dict[str, object]:
    return {
        "id": "LESSON-001",
        "source_intent": "I001",
        "source_findings": ["F-001"],
        "severity_of_source": severity,
        "category": "planning",
        "status": "proposed",
        "observation": "An adversarial criterion lacked a dedicated Task.",
        "impact": "The defect escaped until convergence.",
        "targets": [
            {
                "type": "policy",
                "artifact": ".ai-sdlc/policies/adversarial.md",
                "rule": "Every Class N criterion has an independent attacking Task.",
                "trigger": "contains_model_behavior",
                "enforcement": "blocking",
                "fires_at": ["gate_3"],
            }
        ],
        "review_after_intents": 10,
        "last_fired_intent": 1,
        "fire_count": 0,
    }


def test_critical_and_high_lessons_require_a_blocking_target(tmp_path: Path) -> None:
    record = lesson()
    record["targets"] = [
        {
            "type": "heuristic",
            "artifact": ".ai-sdlc/project-context.md",
            "enforcement": "advisory",
            "fires_at": ["stage_4_agent_context"],
        }
    ]
    with pytest.raises(LessonError, match="blocking target"):
        LessonRegistry(tmp_path).propose(record)


def test_lesson_target_fragments_update_the_real_artifact_not_a_hash_named_file(tmp_path: Path) -> None:
    record = lesson("medium")
    record["targets"] = [
        {
            "type": "policy",
            "artifact": ".ai-sdlc/policies/security.md#class-n-required",
            "rule": "Class N coverage is mandatory.",
            "enforcement": "blocking",
            "fires_at": ["gate_2_validator"],
        }
    ]
    registry = LessonRegistry(tmp_path)
    registry.propose(record)
    registry.transition("LESSON-001", "accepted", actor="human")
    registry.promote("LESSON-001")
    assert (tmp_path / ".ai-sdlc/policies/security.md").exists()
    assert not (tmp_path / ".ai-sdlc/policies/security.md#class-n-required").exists()


def test_lesson_lifecycle_promotes_artifact_activates_and_inherits(tmp_path: Path) -> None:
    registry = LessonRegistry(tmp_path)
    registry.propose(lesson())
    registry.transition("LESSON-001", "accepted", actor="human")
    promoted = registry.promote("LESSON-001")
    assert promoted["status"] == "active"
    assert (tmp_path / ".ai-sdlc/policies/adversarial.md").exists()
    inherited = registry.inherit({"contains_model_behavior": True}, "gate_3", current_intent=2)
    assert inherited[0]["lesson_id"] == "LESSON-001"
    assert registry.get("LESSON-001")["fire_count"] == 1
    with pytest.raises(LessonError, match="illegal lesson transition"):
        registry.transition("LESSON-001", "accepted", actor="human")


def test_decay_review_marks_inactive_rules_for_measurement_and_preserves_retirement(tmp_path: Path) -> None:
    registry = LessonRegistry(tmp_path)
    registry.propose(lesson("medium"))
    registry.transition("LESSON-001", "accepted", actor="human")
    registry.promote("LESSON-001")
    review = registry.decay_review(current_intent=12, runtime_budget_seconds=30, estimated_suite_seconds=45)
    assert review["due_for_measurement"] == ["LESSON-001"]
    assert review["budget_action"] == "sample_per_intent_run_full_at_convergence"
    registry.transition("LESSON-001", "measured", actor="process-owner")
    registry.transition("LESSON-001", "retired", actor="process-owner")
    assert registry.get("LESSON-001")["status"] == "retired"
    assert (tmp_path / ".ai-sdlc/lessons/retired.yaml").exists()


def test_loop_metrics_measure_repeat_findings_detection_shift_and_lesson_yield() -> None:
    findings = [
        {"id": "F-001", "repeat": True, "introduced_stage": 3, "detected_stage": 5, "detected_by": "manual"},
        {
            "id": "F-002",
            "repeat": False,
            "introduced_stage": 3,
            "detected_stage": 4,
            "detected_by": "regression/tool-call-injection",
        },
    ]
    tasks = [
        {"id": "T1", "attempt": 1, "passed": True, "source_findings": []},
        {"id": "T2", "attempt": 2, "passed": True, "source_findings": ["F-001"]},
    ]
    lessons = [{"status": "active"}, {"status": "proposed"}]
    result = LoopMetrics.calculate(findings, tasks, lessons, intent_count=2)
    assert result["repeat_finding_rate"] == 0.5
    assert result["inherited_suite_catch_rate"] == 0.5
    assert result["detection_stage_shift"] == 1.5
    assert result["first_attempt_task_pass_rate"] == 0.5
    assert result["corrective_tasks_per_intent"] == 0.5
    assert result["lesson_yield"] == 0.5


def test_loop_metrics_skip_findings_recorded_without_stages() -> None:
    """A finding written before stages were validated must not crash `loop-metrics`."""
    findings = [
        {"id": "F-001", "introduced_stage": 2, "detected_stage": 4},
        {"id": "F-002", "severity": "high", "status": "resolved"},
    ]
    result = LoopMetrics.calculate(findings, [], [], intent_count=1)
    assert result["detection_stage_shift"] == 2.0


def test_close_requires_every_critical_or_high_finding_to_have_a_blocking_lesson(tmp_path: Path) -> None:
    registry = LessonRegistry(tmp_path)
    findings = [{"id": "F-001", "severity": "critical", "status": "resolved"}]
    with pytest.raises(LessonError, match="F-001"):
        registry.validate_close("I001", findings)
    registry.propose(lesson())
    registry.transition("LESSON-001", "accepted", actor="human")
    registry.promote("LESSON-001")
    assert registry.validate_close("I001", findings) == []
