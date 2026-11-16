from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .baseline import baseline_criteria_errors
from .evaluations import DatasetRegistry, EvaluationError
from .evidence import holdout_coverage_errors, sha256_file, validate_task_evidence
from .lessons import LessonError, LessonRegistry
from .state import StateStore, TransitionError
from .storage import atomic_write_text
from .traceability import generate_traceability
from .validation import validate_intent
from .vendor import yaml


class ConvergenceError(RuntimeError):
    pass


def _in_scope_criteria(requirements: dict[str, Any]) -> list[dict[str, Any]]:
    """Criteria owned by at least one requirement this Intent actually delivers.

    A criterion owned only by `wont` requirements describes scope Gate 2 excluded, so no
    evaluation evidence is owed for it. A criterion shared with delivered scope still is.
    """
    records = requirements.get("functional_requirements", []) + requirements.get("non_functional_requirements", [])
    in_scope = {
        criterion_id
        for record in records
        if record.get("priority") != "wont"
        for criterion_id in record.get("acceptance_criteria", [])
    }
    return [item for item in requirements.get("acceptance_criteria", []) if item.get("id") in in_scope]


@dataclass(frozen=True)
class ConvergenceReport:
    errors: tuple[str, ...]
    report_path: Path
    source_commit: str

    @property
    def passed(self) -> bool:
        return not self.errors


def load_findings(intent_root: Path) -> list[dict[str, Any]]:
    path = intent_root / "05-verification" / "findings.yaml"
    if not path.exists():
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    return [item for item in payload.get("findings", []) if isinstance(item, dict)]


class ConvergenceService:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.store = StateStore(self.repository_root)

    def assess(self, intent_root: Path) -> ConvergenceReport:
        intent_root = intent_root.resolve()
        errors = list(validate_intent(intent_root).errors)
        source_commit = self._git_commit()
        try:
            intent = yaml.safe_load((intent_root / "00-intent" / "intent.yaml").read_text(encoding="utf-8"))
            intent_id = intent["id"]
        except (OSError, yaml.YAMLError, TypeError, KeyError) as error:
            errors.append(f"Intent identity is unreadable: {error}")
            report_path = intent_root / "05-verification" / "convergence-report.md"
            self._write_report(report_path, intent_root.name, source_commit, errors)
            return ConvergenceReport(tuple(errors), report_path, source_commit)
        state = self.store.read()
        if not self._tracked_source_is_clean():
            errors.append("tracked source changes are not committed")
        if state.get("gates", {}).get(f"{intent_id}:G4", {}).get("status") != "approved":
            errors.append("Gate 4 is not approved")

        task_statuses: dict[str, str] = {}
        for task_path in sorted(intent_root.rglob("task.yaml")):
            task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
            task_id = task["id"]
            status = state.get("tasks", {}).get(task_id, {}).get("status")
            task_statuses[task_id] = status or "unknown"
            if status not in {"verified", "merged"}:
                errors.append(f"{task_id} is not verified or merged")
            self._validate_task_evidence(task_id, task_path.parent, source_commit, errors)

        trace_path = generate_traceability(intent_root, task_statuses)
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        for link in trace.get("links", []):
            if link.get("status") not in {"verified", "deferred"}:
                errors.append(f"{link.get('requirement')} lacks verified traceability")

        requirements = yaml.safe_load(
            (intent_root / "02-specification" / "requirements.yaml").read_text(encoding="utf-8")
        )
        self._validate_evaluations(intent_root, _in_scope_criteria(requirements), source_commit, errors)
        inherited_path = intent_root / "02-specification" / "inherited-controls.yaml"
        if inherited_path.exists():
            inherited = yaml.safe_load(inherited_path.read_text(encoding="utf-8")) or {}
            self._validate_inherited_suites(intent_root, inherited.get("standing_suites", []), errors)
        for finding in load_findings(intent_root):
            if finding.get("severity") in {"critical", "high"} and finding.get("status") == "open":
                errors.append(f"{finding.get('id')} is an open {finding.get('severity')} finding")

        # W1.2: the Gate 2 promise that every behavioural requirement carries an
        # independently authored instrument is cashed here, where it can still block.
        errors.extend(holdout_coverage_errors(self.repository_root, intent_root))
        # W3.5: a criterion stated relative to a baseline is only meaningful while that
        # baseline still exists and still hashes to what it did when the criterion was set.
        errors.extend(baseline_criteria_errors(self.repository_root, intent_root))

        report_path = intent_root / "05-verification" / "convergence-report.md"
        self._write_report(report_path, intent_id, source_commit, errors)
        return ConvergenceReport(tuple(errors), report_path, source_commit)

    def _validate_task_evidence(self, task_id: str, task_root: Path, source_commit: str, errors: list[str]) -> None:
        index_path = task_root / "evidence" / "manifest.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            latest_record = index["attempts"][-1]
            latest = latest_record["path"]
            manifest_path = index_path.parent / latest
            if not manifest_path.is_file():
                raise FileNotFoundError(manifest_path)
            manifest_hash = sha256_file(manifest_path)
        except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            errors.append(f"{task_id} has no readable evidence index: {error}")
            return
        if manifest_hash != latest_record.get("sha256"):
            errors.append(f"{task_id} evidence manifest hash does not match its immutable index")
        report = validate_task_evidence(
            manifest_path,
            self.repository_root,
            task_root,
            expected_commit=source_commit,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        errors.extend(f"{task_id}: {error}" for error in report.errors)
        if not manifest.get("passed"):
            errors.append(f"{task_id} verification failed")
        conformance = manifest.get("conformance") or {}
        if conformance.get("conformance_gap"):
            # Not a flake and not a retry: the visible suite passes and the held-out suite
            # does not, which means the implementation satisfies its tests rather than the
            # specification those tests stand for.
            errors.append(
                f"{task_id} has an unresolved specification-conformance gap "
                f"(visible {conformance.get('visible_pass_rate')}, held-out {conformance.get('holdout_pass_rate')})"
            )
        mutation = manifest.get("mutation")
        if isinstance(mutation, dict) and mutation.get("passed") is False:
            errors.append(
                f"{task_id} mutation score {mutation.get('score')} is below its declared threshold "
                f"{mutation.get('threshold')}; {mutation.get('survived')} mutants survived"
            )
        review_path = task_root / "review.yaml"
        try:
            review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            errors.append(f"{task_id} has no readable independent review: {error}")
            return
        if not isinstance(review, dict):
            errors.append(f"{task_id} independent review is not a mapping")
            return
        if review.get("decision") != "approved":
            errors.append(f"{task_id} independent review is not approved")
        expected_reference = str(manifest_path.resolve().relative_to(self.repository_root)).replace("\\", "/")
        if review.get("evidence_manifest") != expected_reference:
            errors.append(f"{task_id} review does not pin the latest evidence manifest")

    def _validate_evaluations(
        self,
        intent_root: Path,
        criteria: list[dict[str, Any]],
        source_commit: str,
        errors: list[str],
    ) -> None:
        manifests: dict[str, list[dict[str, Any]]] = {}
        for path in sorted(intent_root.rglob("manifest.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if "eval_id" in payload:
                manifests.setdefault(payload["eval_id"], []).append(payload)
        for criterion in criteria:
            verification_class = criterion.get("verification_class")
            if verification_class not in {"S", "J", "N"}:
                continue
            eval_id = criterion.get("eval", criterion.get("evaluation_id", criterion.get("id")))
            candidates = manifests.get(eval_id, [])
            if not candidates:
                errors.append(f"{criterion.get('id')} has no {verification_class} evaluation manifest")
                continue
            latest = candidates[-1]
            if latest.get("source_commit") != source_commit:
                errors.append(f"{criterion.get('id')} evaluation source commit is stale")
            try:
                current_hash = DatasetRegistry(self.repository_root).resolve(latest["dataset"])["hash"]
                if latest.get("dataset_hash") != current_hash:
                    errors.append(f"{criterion.get('id')} evaluation dataset pin is stale")
            except (EvaluationError, KeyError):
                errors.append(f"{criterion.get('id')} evaluation dataset is not registered")
            result = latest.get("result", {})
            if not result.get("passed") or result.get("flaky") is True:
                errors.append(f"{criterion.get('id')} evaluation did not pass consistently")
            if verification_class == "N" and result.get("observed_successes") != 0:
                errors.append(f"{criterion.get('id')} has observed attacker successes")

    @staticmethod
    def _validate_inherited_suites(intent_root: Path, suites: list[str], errors: list[str]) -> None:
        manifests: list[dict[str, Any]] = []
        for path in sorted(intent_root.rglob("manifest.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if "dataset" in payload and "result" in payload:
                manifests.append(payload)
        for suite in suites:
            candidates = [manifest for manifest in manifests if manifest.get("dataset") == suite]
            if not candidates:
                errors.append(f"inherited suite {suite} has no convergence run")
            elif not candidates[-1].get("result", {}).get("passed"):
                errors.append(f"inherited suite {suite} did not pass")

    def _git_commit(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repository_root,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise ConvergenceError("convergence requires a Git commit")
        return result.stdout.strip()

    def _tracked_source_is_clean(self) -> bool:
        unstaged = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=self.repository_root, check=False)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "HEAD", "--"], cwd=self.repository_root, check=False
        )
        return unstaged.returncode == 0 and staged.returncode == 0

    @staticmethod
    def _write_report(path: Path, intent_id: str, source_commit: str, errors: list[str]) -> None:
        status = "passed" if not errors else "failed"
        details = "\n".join(f"- {error}" for error in errors) if errors else "- All convergence checks passed."
        atomic_write_text(
            path,
            f"# Convergence report — {intent_id}\n\n"
            f"- Status: **{status}**\n"
            f"- Source commit: `{source_commit}`\n"
            f"- Generated at: `{datetime.now(UTC).isoformat()}`\n\n"
            f"## Checks\n\n{details}\n",
        )


class CloseService:
    def __init__(self, repository_root: Path, lessons: LessonRegistry | None = None) -> None:
        self.repository_root = repository_root.resolve()
        self.store = StateStore(self.repository_root)
        self.lessons = lessons or LessonRegistry(self.repository_root)

    def close(self, intent_root: Path, *, actor: str) -> dict[str, str]:
        intent = yaml.safe_load((intent_root / "00-intent" / "intent.yaml").read_text(encoding="utf-8"))
        intent_id = intent["id"]
        state = self.store.read()
        if state.get("intents", {}).get(intent_id, {}).get("status") != "awaiting_acceptance":
            raise ConvergenceError("Intent must be awaiting acceptance before closure")
        if state.get("gates", {}).get(f"{intent_id}:G5", {}).get("status") != "approved":
            raise ConvergenceError("Gate 5 must be approved before closure")
        convergence = ConvergenceService(self.repository_root).assess(intent_root)
        if not convergence.passed:
            raise ConvergenceError("convergence is no longer valid: " + "; ".join(convergence.errors))
        try:
            self.lessons.validate_close(intent_id, load_findings(intent_root))
            self.store.transition_intent(intent_id, "completed", actor=actor)
        except (LessonError, TransitionError) as error:
            raise ConvergenceError(str(error)) from error
        path = intent_root / "06-close" / "completion-report.md"
        atomic_write_text(
            path,
            f"# Completion report — {intent_id}\n\n"
            f"- Status: **completed**\n"
            f"- Accepted by: `{actor}`\n"
            f"- Closed at: `{datetime.now(UTC).isoformat()}`\n"
            "- Evidence and audit records are retained in the Intent and runtime stores.\n"
            "- Automated release and deployment are outside this lifecycle.\n",
        )
        return {"intent_id": intent_id, "status": "completed", "completion_report": str(path.resolve())}
