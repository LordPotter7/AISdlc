from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .assets import schema_path
from .config import load_config, model_family
from .evidence import git_commit, run_verification_command, validate_task_evidence
from .jsonschema_mini import Draft202012Validator
from .state import StateStore
from .storage import FileMutex, atomic_write_json, atomic_write_text, read_json
from .tasks import ResourceLockManager, select_ready_tasks
from .understanding import load_for_context_pack
from .vendor import yaml


class OrchestrationError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(self, repository_root: Path, *, worktrees_root: Path | None = None) -> None:
        self.repository_root = repository_root.resolve()
        self.worktrees_root = (
            worktrees_root or self.repository_root.parent / ".ai-sdlc-worktrees" / self.repository_root.name
        ).resolve()
        runtime = self.repository_root / ".ai-sdlc" / "runtime"
        self.registry_path = runtime / "worktrees.json"
        self.lock_path = runtime / "worktrees.lock"

    def create(self, task_id: str, base_ref: str = "HEAD") -> Path:
        destination = (self.worktrees_root / task_id).resolve()
        self._assert_managed(destination)
        with FileMutex(self.lock_path):
            registry = read_json(self.registry_path, {})
            if task_id in registry and Path(registry[task_id]["path"]).exists():
                raise OrchestrationError(f"worktree already exists for {task_id}")
            self.worktrees_root.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["git", "worktree", "add", "--detach", str(destination), base_ref],
                cwd=self.repository_root,
                check=False,
                text=True,
                capture_output=True,
            )
            if result.returncode != 0:
                raise OrchestrationError(result.stderr.strip() or "git worktree add failed")
            registry[task_id] = {
                "path": str(destination),
                "base_commit": git_commit(destination),
                "status": "active",
                "created_at": datetime.now(UTC).isoformat(),
            }
            atomic_write_json(self.registry_path, registry)
        return destination

    initialize = create

    def resume(self, task_id: str) -> Path:
        record = read_json(self.registry_path, {}).get(task_id)
        if not record:
            raise OrchestrationError(f"unknown worktree for {task_id}")
        path = Path(record["path"]).resolve()
        self._assert_managed(path)
        if not path.exists():
            raise OrchestrationError(f"worktree path is missing: {path}")
        return path

    def retain(self, task_id: str) -> Path:
        with FileMutex(self.lock_path):
            registry = read_json(self.registry_path, {})
            if task_id not in registry:
                raise OrchestrationError(f"unknown worktree for {task_id}")
            registry[task_id]["status"] = "retained"
            atomic_write_json(self.registry_path, registry)
            return Path(registry[task_id]["path"])

    def remove(self, task_id: str, *, confirm: bool = False) -> None:
        if not confirm:
            raise OrchestrationError("worktree removal requires explicit confirmation")
        with FileMutex(self.lock_path):
            registry = read_json(self.registry_path, {})
            if task_id not in registry:
                raise OrchestrationError(f"unknown worktree for {task_id}")
            path = Path(registry[task_id]["path"]).resolve()
            self._assert_managed(path)
            result = subprocess.run(
                ["git", "worktree", "remove", "--force", str(path)],
                cwd=self.repository_root,
                check=False,
                text=True,
                capture_output=True,
            )
            if result.returncode != 0:
                raise OrchestrationError(result.stderr.strip() or "git worktree remove failed")
            registry[task_id]["status"] = "removed"
            registry[task_id]["removed_at"] = datetime.now(UTC).isoformat()
            atomic_write_json(self.registry_path, registry)

    def _assert_managed(self, path: Path) -> None:
        if os.path.commonpath([self.worktrees_root, path]) != str(self.worktrees_root):
            raise OrchestrationError(f"worktree path escapes managed root: {path}")


class ContextPackBuilder:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def build(self, task_path: Path) -> Path:
        task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        intent_root = next(
            parent
            for parent in task_path.parents
            if parent.name.startswith(f"{task['intent_id']}-") and parent.parent.name == "intents"
        )
        requirements_doc = yaml.safe_load(
            (intent_root / "02-specification" / "requirements.yaml").read_text(encoding="utf-8")
        )
        requirement_ids = set(task.get("requirements", []))
        requirements = [
            item
            for item in requirements_doc.get("functional_requirements", [])
            + requirements_doc.get("non_functional_requirements", [])
            if item["id"] in requirement_ids
        ]
        criterion_ids = {criterion for requirement in requirements for criterion in requirement["acceptance_criteria"]}
        criteria = [item for item in requirements_doc.get("acceptance_criteria", []) if item["id"] in criterion_ids]
        decisions = [
            {
                "path": str(path.relative_to(self.repository_root)).replace("\\", "/"),
                "content": path.read_text(encoding="utf-8"),
            }
            for path in sorted((intent_root / "03-planning" / "decisions").glob("*.md"))
        ]
        instructions = ""
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = self.repository_root / name
            if path.exists():
                instructions += path.read_text(encoding="utf-8")
        # `.ai-sdlc/project-context.md` describes itself as "durable engineering context
        # handed to agents in their Stage 4 prompt pack". Until this line it was not:
        # the file was read only by `/aisdlc:start`, in conversation, so a team that
        # filled it in diligently never got it in front of the implementing agent at the
        # stage it was written for. It stays a separate key rather than being folded into
        # `repository_instructions` because the two have different provenance — repository
        # rules the runner enforces, versus conventions a human wrote down.
        context_path = self.repository_root / ".ai-sdlc" / "project-context.md"
        project_context = context_path.read_text(encoding="utf-8") if context_path.exists() else ""
        # W5.1. The derived understanding travels with its mode and staleness attached, and
        # is dropped entirely past the configured drift threshold. An implementing agent
        # must be able to treat "the architecture note says the domain layer is pure"
        # differently from "the domain layer imports nothing from web/, verified at
        # src/domain/" — which it cannot do if the two arrive looking identical.
        understanding_config = load_config(self.repository_root).understanding
        understanding = (
            load_for_context_pack(
                self.repository_root,
                drift_threshold=understanding_config.drift_threshold,
                include_when_stale=understanding_config.include_when_stale,
            )
            if understanding_config.enabled
            else None
        )
        inherited_path = intent_root / "02-specification" / "inherited-controls.yaml"
        inherited_controls = (
            yaml.safe_load(inherited_path.read_text(encoding="utf-8")) if inherited_path.exists() else {}
        )
        output = self.repository_root / ".ai-sdlc" / "runtime" / "context-packs" / f"{task['id']}.json"
        atomic_write_json(
            output,
            {
                "task": task,
                "requirements": requirements,
                "acceptance_criteria": criteria,
                "decisions": decisions,
                "repository_instructions": instructions,
                "project_context": project_context,
                "project_understanding": understanding,
                "inherited_controls": inherited_controls or {},
            },
        )
        return output


class ReviewService:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def _enforce_model_independence(
        self,
        review: dict[str, Any],
        implementer_id: str,
        implementer_model: str | None,
    ) -> dict[str, Any]:
        """Reject a review whose reviewer shares a model family with the implementer.

        The previous check compared agent labels. A label is a persona name chosen by
        whoever wrote the agent file; the property that actually matters is which model
        produced the review, because family bias — a judge over-rewarding its own
        family's output — attaches to the model, not the name. Both agents defaulting to
        `model: inherit` meant the same model reviewed its own work while satisfying a
        `reviewer_id != implementer_id` string comparison.

        Fails closed: an unrecorded reviewer model is invalid rather than accepted as a
        legacy-shaped review, so upgrading the runner cannot silently keep approving
        same-family reviews.
        """
        settings = load_config(self.repository_root).review
        reviewer_model = review.get("reviewer_model") or (
            settings.reviewer_model if review.get("reviewer_model") is None else None
        )
        if not reviewer_model:
            if settings.require_reviewer_model:
                raise OrchestrationError(
                    "independent review must record `reviewer_model`; configure `review.reviewer_model` "
                    "or set `review.require_reviewer_model: false` to accept unattributed reviews"
                )
            return review
        implementer_model = review.get("implementer_model") or implementer_model
        resolved = {**review, "reviewer_model": reviewer_model}
        if implementer_model:
            resolved["implementer_model"] = implementer_model
        if not settings.forbid_same_family:
            return resolved
        if str(reviewer_model).strip().lower() == "inherit":
            raise OrchestrationError(
                "reviewer model `inherit` cannot be shown independent of the implementer; "
                "pin an explicit cross-family model in `review.reviewer_model`"
            )
        if not implementer_model:
            if settings.require_reviewer_model:
                raise OrchestrationError(
                    "model independence cannot be established without the implementer model; "
                    "record `implementer_model` on the review or pass --implementer-model "
                    f"(implementer: {implementer_id})"
                )
            return resolved
        reviewer_family = model_family(reviewer_model)
        implementer_family = model_family(implementer_model)
        if reviewer_family is not None and reviewer_family == implementer_family:
            raise OrchestrationError(
                f"reviewer model {reviewer_model!r} shares the {reviewer_family!r} family with implementer model "
                f"{implementer_model!r}; independent review requires a different model family"
            )
        return resolved

    def record(
        self,
        task_id: str,
        task_directory: Path,
        review: dict[str, Any],
        evidence_manifest: Path,
        *,
        implementer_id: str,
        implementer_model: str | None = None,
    ) -> dict[str, Any]:
        reviewer_id = review.get("reviewer_agent_id")
        if not reviewer_id or reviewer_id == implementer_id:
            raise OrchestrationError("independent review requires a different reviewer and implementer identity")
        if review.get("decision") not in {"approved", "changes_required", "blocked"}:
            raise OrchestrationError("invalid review decision")
        review = self._enforce_model_independence(review, implementer_id, implementer_model)
        evidence = validate_task_evidence(
            evidence_manifest,
            self.repository_root,
            task_directory,
            expected_commit=git_commit(self.repository_root),
        )
        if not evidence.valid:
            raise OrchestrationError("evidence is invalid: " + "; ".join(evidence.errors))
        try:
            evidence_reference = str(evidence_manifest.resolve().relative_to(self.repository_root)).replace("\\", "/")
        except ValueError as error:
            raise OrchestrationError("review evidence must be stored inside the repository") from error
        recorded = {
            **review,
            "task_id": task_id,
            "implementer_agent_id": implementer_id,
            "evidence_manifest": evidence_reference,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        if implementer_model and not recorded.get("implementer_model"):
            recorded["implementer_model"] = implementer_model
        schema = json.loads(schema_path(self.repository_root, "review.schema.json").read_text(encoding="utf-8"))
        schema_errors = sorted(Draft202012Validator(schema).iter_errors(recorded), key=lambda item: list(item.path))
        if schema_errors:
            raise OrchestrationError("invalid review artifact: " + "; ".join(error.message for error in schema_errors))
        atomic_write_text(task_directory / "review.yaml", yaml.safe_dump(recorded, sort_keys=False))
        store = StateStore(self.repository_root)

        if review["decision"] == "approved":
            store.transition_task(task_id, "verified", actor=reviewer_id)
            status = "verified"
        else:
            status = review["decision"]
        return {"task_id": task_id, "status": status, "review": str((task_directory / "review.yaml").resolve())}


class RetryController:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.store = StateStore(self.repository_root)

    def failure(self, task_id: str, *, reason: str, retry_limit: int) -> dict[str, Any]:
        self.store.transition_task(task_id, "retry", actor="runner")
        attempts = self.store.read()["tasks"][task_id]["attempt"]
        if attempts <= retry_limit:
            return {"task_id": task_id, "status": "retry", "attempt": attempts, "reason": reason}
        self.store.transition_task(task_id, "blocked", actor="runner")
        output = self.repository_root / ".ai-sdlc" / "runtime" / "escalations" / f"{task_id}-attempt-{attempts}.json"
        history = [
            json.loads(line)
            for line in self.store.audit_path.read_text(encoding="utf-8").splitlines()
            if json.loads(line)["entity_id"] == task_id
        ]
        atomic_write_json(
            output,
            {"task_id": task_id, "status": "blocked", "attempt": attempts, "reason": reason, "history": history},
        )
        return {
            "task_id": task_id,
            "status": "blocked",
            "attempt": attempts,
            "reason": reason,
            "escalation_package": str(output.resolve()),
        }


@dataclass(frozen=True)
class MergeReport:
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors


class MergeReadiness:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def assess(
        self,
        task_id: str,
        task_directory: Path,
        *,
        expected_commit: str,
        integration_commands: list[str] | None = None,
    ) -> MergeReport:
        errors: list[str] = []
        state = StateStore(self.repository_root).read()["tasks"].get(task_id, {})
        if state.get("status") != "verified":
            errors.append("Task state is not verified")
        review_path = task_directory / "review.yaml"
        if not review_path.exists():
            errors.append("independent review is missing")
        else:
            review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
            if review.get("decision") != "approved":
                errors.append("independent review is not approved")
            else:
                manifest = self.repository_root / review["evidence_manifest"]
                evidence = validate_task_evidence(
                    manifest,
                    self.repository_root,
                    task_directory,
                    expected_commit=expected_commit,
                )
                errors.extend(evidence.errors)
        if git_commit(self.repository_root) != expected_commit:
            errors.append("current source commit differs from verified commit")
        errors.extend(self._staleness_errors(expected_commit))
        diff_check = subprocess.run(
            ["git", "diff", "--check"],
            cwd=self.repository_root,
            check=False,
            text=True,
            capture_output=True,
        )
        if diff_check.returncode != 0:
            errors.append("integration diff contains conflict markers or whitespace errors")
        for command in integration_commands or []:
            result = run_verification_command(command, self.repository_root)
            if result.returncode != 0:
                errors.append(f"integration command failed: {command}")
        return MergeReport(tuple(errors))

    def _staleness_errors(self, expected_commit: str) -> list[str]:
        """Require re-verification when a Task's base has fallen too far behind (W3.3).

        Evidence pinned to a commit proves the Task worked *at that commit*. With one
        worktree at a time that is the same thing as "works now". With many concurrent
        worktrees it stops being: a Task verified against a base fifty commits behind the
        integration branch was verified against a system that no longer exists, and it can
        merge cleanly while being semantically broken against everything that landed
        meanwhile.

        A bound rather than a ban, because forcing every Task to rebase constantly is its
        own tax. Beyond the bound the Task re-verifies; inside it, the evidence stands.
        """
        settings = load_config(self.repository_root).merge
        if settings.max_base_lag <= 0:
            return []
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{expected_commit}..{settings.integration_ref}"],
            cwd=self.repository_root,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            # An unresolvable integration ref is a configuration problem, not a merge
            # blocker: report it rather than failing a Task for it.
            return [f"could not measure base lag against {settings.integration_ref}: {result.stderr.strip()}"]
        try:
            lag = int(result.stdout.strip() or 0)
        except ValueError:
            return []
        if lag > settings.max_base_lag:
            return [
                f"verified base is {lag} commits behind {settings.integration_ref} "
                f"(limit {settings.max_base_lag}); re-verify against the current integration branch"
            ]
        return []


class ScriptedOrchestrator:
    """Deterministic coordinator; Claude Code is the pluggable execution adapter."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def batch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        limit = load_config(self.repository_root).orchestration.max_parallel_tasks
        locks = ResourceLockManager(self.repository_root).read()
        return select_ready_tasks(tasks, locks)[:limit]

    def prepare(self, task_path: Path) -> dict[str, str]:
        task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        worktree = WorktreeManager(self.repository_root).create(task["id"])
        context_pack = ContextPackBuilder(self.repository_root).build(task_path)
        return {"task_id": task["id"], "worktree": str(worktree), "context_pack": str(context_pack)}
