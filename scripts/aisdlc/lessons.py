from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .assets import schema_path
from .config import delivery_root
from .jsonschema_mini import Draft202012Validator
from .storage import FileMutex, atomic_write_text
from .vendor import yaml


class LessonError(RuntimeError):
    pass


LESSON_TRANSITIONS = {
    "proposed": {"accepted", "rejected"},
    "accepted": {"implemented"},
    "implemented": {"active"},
    "active": {"measured"},
    "measured": {"active", "retired"},
    "rejected": set(),
    "retired": set(),
}


class LessonRegistry:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.path = self.repository_root / ".ai-sdlc" / "lessons" / "registry.yaml"
        self.retired_path = self.repository_root / ".ai-sdlc" / "lessons" / "retired.yaml"
        self.lock_path = self.repository_root / ".ai-sdlc" / "runtime" / "lessons.lock"

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"lessons": []}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {"lessons": []}

    def read_all(self) -> list[dict[str, Any]]:
        """Every lesson in the active registry, in recorded order.

        Retired lessons are deliberately excluded: a retired lesson is one the framework
        decided had stopped earning its keep, so it must not count as coverage for a
        finding that has just recurred.
        """
        lessons = self._read(self.path).get("lessons", [])
        return [lesson for lesson in lessons if isinstance(lesson, dict)]

    def propose(self, lesson: dict[str, Any]) -> dict[str, Any]:
        required = {
            "id",
            "source_intent",
            "source_findings",
            "severity_of_source",
            "category",
            "status",
            "observation",
            "impact",
            "targets",
            "review_after_intents",
        }
        missing = sorted(required - lesson.keys())
        if missing:
            raise LessonError(f"missing lesson fields: {', '.join(missing)}")
        schema = json.loads(schema_path(self.repository_root, "lesson.schema.json").read_text(encoding="utf-8"))
        schema_errors = sorted(Draft202012Validator(schema).iter_errors(lesson), key=lambda item: list(item.path))
        if schema_errors:
            raise LessonError("invalid lesson: " + "; ".join(error.message for error in schema_errors))
        if lesson["status"] != "proposed":
            raise LessonError("new lessons must start as proposed")
        if not lesson["targets"]:
            raise LessonError("a lesson requires at least one firing target")
        for target in lesson["targets"]:
            if target.get("type") == "threat":
                # S3.4: a promoted threat is answered in a threat model, which is a Stage 2
                # artifact; firing it anywhere else would ask for an answer nobody can give.
                if not target.get("stride"):
                    raise LessonError("a threat target requires a STRIDE category")
                if target.get("fires_at") != ["stage_2_specification"]:
                    raise LessonError("a threat target fires only at stage_2_specification")
        if lesson["severity_of_source"] in {"critical", "high"} and not any(
            target.get("enforcement") == "blocking" for target in lesson["targets"]
        ):
            raise LessonError("Critical and High lessons require at least one blocking target")
        with FileMutex(self.lock_path):
            registry = self._read(self.path)
            if any(item["id"] == lesson["id"] for item in registry["lessons"]):
                raise LessonError(f"duplicate lesson identifier: {lesson['id']}")
            record = {
                **lesson,
                "history": [
                    {
                        "from": None,
                        "to": "proposed",
                        "actor": "runner",
                        "at": datetime.now(UTC).isoformat(),
                    }
                ],
            }
            registry["lessons"].append(record)
            self._write(self.path, registry)
            return record

    def get(self, lesson_id: str) -> dict[str, Any]:
        for path in (self.path, self.retired_path):
            for lesson in self._read(path)["lessons"]:
                if lesson["id"] == lesson_id:
                    return lesson
        raise LessonError(f"unknown lesson: {lesson_id}")

    def transition(self, lesson_id: str, target: str, *, actor: str) -> dict[str, Any]:
        with FileMutex(self.lock_path):
            registry = self._read(self.path)
            lesson = next((item for item in registry["lessons"] if item["id"] == lesson_id), None)
            if lesson is None:
                raise LessonError(f"unknown active lesson: {lesson_id}")
            current = lesson["status"]
            if target not in LESSON_TRANSITIONS.get(current, set()):
                raise LessonError(f"illegal lesson transition {current} -> {target}")
            lesson["status"] = target
            lesson.setdefault("history", []).append(
                {"from": current, "to": target, "actor": actor, "at": datetime.now(UTC).isoformat()}
            )
            if target == "retired":
                registry["lessons"] = [item for item in registry["lessons"] if item["id"] != lesson_id]
                retired = self._read(self.retired_path)
                retired["lessons"].append(lesson)
                self._write(self.retired_path, retired)
            self._write(self.path, registry)
            return lesson

    def promote(self, lesson_id: str) -> dict[str, Any]:
        lesson = self.get(lesson_id)
        if lesson["status"] != "accepted":
            raise LessonError("only accepted lessons can be promoted")
        for target in lesson["targets"]:
            self._materialize_target(lesson, target)
        self.transition(lesson_id, "implemented", actor="runner")
        return self.transition(lesson_id, "active", actor="runner")

    @staticmethod
    def _fires(target: dict[str, Any], flags: dict[str, bool], fires_at: str) -> bool:
        trigger = target.get("trigger")
        return (not trigger or flags.get(trigger) is True) and fires_at in target.get("fires_at", [])

    def matching(self, flags: dict[str, bool], fires_at: str) -> list[dict[str, Any]]:
        """The active lesson targets that would fire, without recording that they did.

        Validation needs to know what an Intent inherits, and validation must not have side
        effects: a spec validated five times while being drafted has not inherited a lesson
        five times. `inherit` is this plus the fire accounting.
        """
        return [
            {"lesson_id": lesson["id"], **target}
            for lesson in self.read_all()
            if lesson.get("status") == "active"
            for target in lesson.get("targets", [])
            if self._fires(target, flags, fires_at)
        ]

    def inherit(
        self,
        flags: dict[str, bool],
        fires_at: str,
        *,
        current_intent: int,
    ) -> list[dict[str, Any]]:
        inherited: list[dict[str, Any]] = []
        with FileMutex(self.lock_path):
            registry = self._read(self.path)
            changed = False
            for lesson in registry["lessons"]:
                if lesson["status"] != "active":
                    continue
                for target in lesson["targets"]:
                    if self._fires(target, flags, fires_at):
                        inherited.append({"lesson_id": lesson["id"], **target})
                        lesson["fire_count"] = int(lesson.get("fire_count", 0)) + 1
                        lesson["last_fired_intent"] = current_intent
                        changed = True
            if changed:
                self._write(self.path, registry)
        return inherited

    def decay_review(
        self,
        *,
        current_intent: int,
        runtime_budget_seconds: float,
        estimated_suite_seconds: float,
    ) -> dict[str, Any]:
        active = [lesson for lesson in self._read(self.path)["lessons"] if lesson["status"] == "active"]
        due = [
            lesson["id"]
            for lesson in active
            if current_intent - int(lesson.get("last_fired_intent", 0)) >= int(lesson["review_after_intents"])
        ]
        checklist_targets = sum(1 for lesson in active for target in lesson["targets"] if target["type"] == "checklist")
        return {
            "due_for_measurement": due,
            "checklist_count": checklist_targets,
            "checklist_action": "automate_or_retire" if checklist_targets > 20 else "within_cap",
            "budget_action": (
                "sample_per_intent_run_full_at_convergence"
                if estimated_suite_seconds > runtime_budget_seconds
                else "run_full"
            ),
        }

    def validate_close(self, intent_id: str, findings: list[dict[str, Any]]) -> list[str]:
        """Enforce the learning gate for severe findings before an Intent can close."""
        errors: list[str] = []
        lessons = self._read(self.path)["lessons"] + self._read(self.retired_path)["lessons"]
        for finding in findings:
            if finding.get("severity") not in {"critical", "high"}:
                continue
            finding_id = str(finding.get("id", "unknown"))
            if finding.get("status") == "open":
                errors.append(f"{finding_id} is still open")
                continue
            candidates = [
                lesson
                for lesson in lessons
                if lesson.get("source_intent") == intent_id
                and finding_id in lesson.get("source_findings", [])
                and lesson.get("status") in {"implemented", "active", "measured", "retired"}
            ]
            if not any(
                target.get("enforcement") == "blocking" for lesson in candidates for target in lesson.get("targets", [])
            ):
                errors.append(f"{finding_id} has no implemented blocking lesson")
        if errors:
            raise LessonError("; ".join(errors))
        return errors

    def _materialize_target(self, lesson: dict[str, Any], target: dict[str, Any]) -> None:
        artifact_reference = str(target["artifact"])
        artifact_path, _, _fragment = artifact_reference.partition("#")
        artifact = (self.repository_root / artifact_path).resolve()
        if os.path.commonpath([self.repository_root, artifact]) != str(self.repository_root):
            raise LessonError(f"target artifact escapes repository: {artifact}")
        if target["type"] == "threat":
            # A readable register of promoted threats. Enforcement does not read it — the
            # lesson registry is the authority — but a person reviewing security posture
            # should not have to reconstruct it from lesson records.
            register = yaml.safe_load(artifact.read_text(encoding="utf-8")) if artifact.exists() else None
            register = register if isinstance(register, dict) else {"threats": []}
            entries = [item for item in register.get("threats", []) if item.get("lesson_id") != lesson["id"]]
            entries.append(
                {
                    "lesson_id": lesson["id"],
                    "stride": target["stride"],
                    "threat": target.get("rule", lesson["observation"]),
                    "trigger": target.get("trigger"),
                    "enforcement": target["enforcement"],
                    "source_findings": lesson["source_findings"],
                }
            )
            atomic_write_text(artifact, yaml.safe_dump({"threats": entries}, sort_keys=False))
            return
        if target["type"] == "regression_eval":
            path = artifact if artifact.suffix else artifact / "promotion.yaml"
            payload = {
                "lesson_id": lesson["id"],
                "source_findings": lesson["source_findings"],
                "trigger": target.get("trigger"),
                "enforcement": target["enforcement"],
                "promoted_cases": target.get("promoted_cases", []),
            }
            atomic_write_text(path, yaml.safe_dump(payload, sort_keys=False))
            return
        existing = artifact.read_text(encoding="utf-8") if artifact.exists() else ""
        marker = f"## {lesson['id']}"
        if marker not in existing:
            content = existing.rstrip() + f"\n\n{marker}\n\n{target.get('rule', lesson['observation'])}\n"
            atomic_write_text(artifact, content.lstrip())

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        atomic_write_text(path, yaml.safe_dump(value, sort_keys=False))


class LoopMetrics:
    # Window, in Intents, over which a criterion must have failed at least once to count
    # as discriminating. A criterion that never fails is either a solved problem or a dead
    # instrument, and the framework cannot tell which without asking a human.
    DISCRIMINATION_WINDOW = 10

    @staticmethod
    def from_repository(repository_root: Path) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        tasks: list[dict[str, Any]] = []
        intent_roots = sorted(delivery_root(repository_root).glob("I*-*"))
        state_path = repository_root / ".ai-sdlc" / "runtime" / "state.json"
        state = {}
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8")).get("tasks", {})
        for intent_root in intent_roots:
            findings_path = intent_root / "05-verification" / "findings.yaml"
            if findings_path.exists():
                payload = yaml.safe_load(findings_path.read_text(encoding="utf-8")) or {}
                findings.extend(payload if isinstance(payload, list) else payload.get("findings", []))
            for task_path in intent_root.rglob("task.yaml"):
                task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
                runtime = state.get(task["id"], {})
                tasks.append(
                    {
                        **task,
                        "attempt": int(runtime.get("attempt", 0)) + 1,
                        "passed": runtime.get("status") in {"verified", "merged"},
                    }
                )
        registry = LessonRegistry(repository_root)
        lessons = registry._read(registry.path)["lessons"] + registry._read(registry.retired_path)["lessons"]
        metrics = LoopMetrics.calculate(findings, tasks, lessons, intent_count=len(intent_roots))
        metrics.update(LoopMetrics.criterion_discrimination(intent_roots))
        metrics.update(LoopMetrics.cost_metrics(repository_root))
        return metrics

    @staticmethod
    def criterion_discrimination(intent_roots: list[Path]) -> dict[str, Any]:
        """Measure how many criteria still carry information (W2.1).

        A verification instrument set before implementation is right for one Intent — it
        kills the "choose the threshold after seeing the result" failure. Across many
        Intents the same rule hardens into a different failure: a threshold every model
        now clears trivially stops discriminating, and nothing notices. No fixed reward
        function stays effective as the generator improves, so the instruments have to be
        reviewed on evidence too.

        A criterion counts as discriminating when something actually failed against it —
        a finding cites it, or a Task declaring it recorded a failing attempt. The result
        routes into the same decay lifecycle as policy rules: measured, then defended or
        retired by a human. This proposes; it never retires anything itself.
        """
        window = intent_roots[-LoopMetrics.DISCRIMINATION_WINDOW :]
        declared: set[str] = set()
        discriminating: set[str] = set()
        for intent_root in window:
            requirements_path = intent_root / "02-specification" / "requirements.yaml"
            if requirements_path.exists():
                document = yaml.safe_load(requirements_path.read_text(encoding="utf-8")) or {}
                for criterion in document.get("acceptance_criteria", []) if isinstance(document, dict) else []:
                    if isinstance(criterion, dict) and criterion.get("id"):
                        declared.add(str(criterion["id"]))
            findings_path = intent_root / "05-verification" / "findings.yaml"
            if findings_path.exists():
                payload = yaml.safe_load(findings_path.read_text(encoding="utf-8")) or {}
                records = payload if isinstance(payload, list) else payload.get("findings", [])
                for finding in records:
                    if not isinstance(finding, dict):
                        continue
                    for key in ("criterion", "criterion_id", "violated_criterion"):
                        if finding.get(key):
                            discriminating.add(str(finding[key]))
            for task_path in intent_root.rglob("task.yaml"):
                try:
                    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
                except (OSError, yaml.YAMLError):
                    continue
                if not isinstance(task, dict):
                    continue
                criterion_ids = [
                    str(item.get("id"))
                    for item in task.get("acceptance_criteria", [])
                    if isinstance(item, dict) and item.get("id")
                ]
                if not criterion_ids or not LoopMetrics._task_ever_failed(task_path.parent):
                    continue
                discriminating.update(criterion_ids)
        dormant = sorted(declared - discriminating)
        return {
            "criteria_declared": float(len(declared)),
            "criterion_discrimination": (len(discriminating & declared) / len(declared)) if declared else 0.0,
            "dormant_criterion_rate": (len(dormant) / len(declared)) if declared else 0.0,
            "dormant_criteria": dormant,
        }

    @staticmethod
    def _task_ever_failed(task_root: Path) -> bool:
        """True when any recorded attempt for this Task failed.

        Reads every attempt, not just the latest: a criterion that caught a defect on
        attempt one and passed on attempt three did its job, and the retry that followed
        is precisely the evidence that it discriminates.
        """
        evidence_root = task_root / "evidence"
        if not evidence_root.is_dir():
            return False
        for manifest_path in evidence_root.glob("attempt-*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("passed") is False:
                return True
            if (manifest.get("conformance") or {}).get("conformance_gap"):
                return True
        return False

    @staticmethod
    def cost_metrics(repository_root: Path) -> dict[str, Any]:
        """Surface the verification tax alongside the defect-catching metrics (W2.2)."""
        from .costs import CostLedger, gate_latencies

        summary = CostLedger(repository_root).summary()
        latencies = gate_latencies(repository_root)
        return {
            "verification_tax": float(summary["verification_tax"]),
            "verification_seconds": float(summary["verification_seconds"]),
            "implementation_seconds": float(summary["implementation_seconds"]),
            "median_gate_latency_seconds": (
                float(sorted(latencies.values())[len(latencies) // 2]) if latencies else 0.0
            ),
            "gate_latencies_seconds": latencies,
        }

    @staticmethod
    def calculate(
        findings: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        lessons: list[dict[str, Any]],
        *,
        intent_count: int,
    ) -> dict[str, float]:
        total_findings = len(findings)
        total_tasks = len(tasks)
        # Validation refuses findings without stages, but records written before that check
        # must not crash the metric: the shift is averaged over the findings that carry both.
        staged = [
            item
            for item in findings
            if isinstance(item.get("introduced_stage"), int) and isinstance(item.get("detected_stage"), int)
        ]
        implemented_states = {"implemented", "active", "measured", "retired"}
        return {
            "repeat_finding_rate": (
                sum(bool(item.get("repeat")) for item in findings) / total_findings if total_findings else 0.0
            ),
            "inherited_suite_catch_rate": (
                sum(str(item.get("detected_by", "")).startswith("regression/") for item in findings) / total_findings
                if total_findings
                else 0.0
            ),
            "detection_stage_shift": (
                sum(item["detected_stage"] - item["introduced_stage"] for item in staged) / len(staged)
                if staged
                else 0.0
            ),
            "first_attempt_task_pass_rate": (
                sum(item.get("attempt") == 1 and item.get("passed") is True for item in tasks) / total_tasks
                if total_tasks
                else 0.0
            ),
            "corrective_tasks_per_intent": (
                sum(bool(item.get("source_findings")) for item in tasks) / intent_count if intent_count else 0.0
            ),
            "lesson_yield": (
                sum(item.get("status") in implemented_states for item in lessons) / len(lessons) if lessons else 0.0
            ),
        }
