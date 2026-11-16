from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .assets import schema_path
from .contracts import validate_contracts
from .decisions import DecisionError, load_decisions
from .jsonschema_mini import Draft202012Validator
from .threatmodel import validate_threat_model
from .vendor import yaml


@dataclass(frozen=True)
class ValidationReport:
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _validate_schema(
    instance: Any, schema_name: str, label: str, errors: list[str], repository_root: Path | None = None
) -> None:
    root = repository_root if repository_root is not None else Path.cwd()
    schema = json.loads(schema_path(root, schema_name).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path)):
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        errors.append(f"{label}:{location}: {error.message}")


def validate_migrations(tasks: list[dict[str, Any]]) -> list[str]:
    """Enforce paired evidence and expand/contract ordering for stateful changes (W3.6).

    The riskiest changes in a large application are the ones that touch persistent state,
    and they are the ones a passing test suite says least about: the migration ran on an
    empty fixture, so of course it worked. Two rules cover most of the damage, and both
    are decidable statically from the plan.

    *Paired evidence*: a migration declares how it goes forward and how it comes back. An
    irreversible one is allowed, but only as a decision with a name attached rather than
    an omission nobody noticed.

    *Expand/contract*: a Task that drops a field while a Task in the same phase still
    reads it will break the moment both are deployed, and the parallel-change discipline
    exists precisely to prevent that. Dropping has to wait for a later phase, once nothing
    reads the field any more.
    """
    errors: list[str] = []
    reads_by_phase: dict[str, dict[str, list[str]]] = {}
    for task in tasks:
        migration = task.get("migration")
        if not isinstance(migration, dict):
            continue
        phase = str(task.get("phase_id", ""))
        for field in migration.get("reads_fields", []) or []:
            reads_by_phase.setdefault(phase, {}).setdefault(str(field), []).append(str(task.get("id")))
    for task in tasks:
        migration = task.get("migration")
        if not isinstance(migration, dict):
            continue
        task_id = str(task.get("id"))
        if not str(migration.get("forward_command", "")).strip():
            errors.append(f"{task_id} declares a migration without a forward command")
        if migration.get("irreversible"):
            if not str(migration.get("irreversibility_accepted_by", "")).strip():
                errors.append(
                    f"{task_id} declares an irreversible migration with no recorded acceptance; "
                    "an irreversible change is a decision somebody makes, not a property it simply has"
                )
        elif not str(migration.get("rollback_command", "")).strip():
            errors.append(
                f"{task_id} declares a reversible migration without a rollback command; "
                "declare `rollback_command`, or mark it irreversible and record who accepted that"
            )
        if not migration.get("invariant_commands"):
            errors.append(
                f"{task_id} declares a migration without data-invariant checks; a migration that "
                "runs is not a migration that preserved the data"
            )
        phase = str(task.get("phase_id", ""))
        for field in migration.get("drops_fields", []) or []:
            readers = [reader for reader in reads_by_phase.get(phase, {}).get(str(field), []) if reader != task_id]
            if readers:
                errors.append(
                    f"{task_id} drops `{field}` while {', '.join(sorted(readers))} still reads it in the same "
                    f"phase; expand and contract in separate phases"
                )
    return errors


def validate_accessibility(requirements: dict[str, Any]) -> list[str]:
    """The front-end carve-out (S3.1, ADR-009): accessibility is in scope as Class D only.

    Front-end work stays outside this lifecycle, because visual and interaction judgement
    fits none of the four classes. Accessibility is the exception that fits one honestly:
    an automated checker run against a declared WCAG target either reports violations or it
    does not. So an accessibility requirement is admitted exactly when it says which target
    it checks and names a deterministic command that checks it.
    """
    errors: list[str] = []
    criteria = {
        item.get("id"): item for item in requirements.get("acceptance_criteria", []) or [] if isinstance(item, dict)
    }
    for requirement in requirements.get("non_functional_requirements", []) or []:
        if not isinstance(requirement, dict) or requirement.get("category") != "accessibility":
            continue
        requirement_id = requirement.get("id")
        if not requirement.get("wcag"):
            errors.append(f"{requirement_id} is an accessibility requirement with no `wcag` version and level")
        owned = [criteria[key] for key in requirement.get("acceptance_criteria", []) if key in criteria]
        if not any(item.get("verification_class") == "D" and item.get("check") for item in owned):
            errors.append(
                f"{requirement_id} is an accessibility requirement with no Class D automated check; "
                "accessibility is in scope only as a deterministic checker run (ADR-009)"
            )
    return errors


def _decision_status(decisions: list[dict[str, Any]], decision_id: str) -> str | None:
    for record in decisions:
        if record.get("id") == decision_id:
            return record.get("status")
    return None


def validate_intent_record(intent_root: Path) -> ValidationReport:
    """What `advance framed` depends on: a readable, schema-valid `intent.yaml` and nothing else."""
    try:
        intent = _load_yaml(intent_root / "00-intent" / "intent.yaml")
    except (OSError, yaml.YAMLError) as error:
        return ValidationReport((f"unreadable required artifact: {error}",))
    errors: list[str] = []
    _validate_schema(intent, "intent.schema.json", "intent.yaml", errors)
    return ValidationReport(tuple(errors))


def validate_intent(intent_root: Path, *, require_plan: bool = True) -> ValidationReport:
    errors: list[str] = []
    try:
        intent = _load_yaml(intent_root / "00-intent" / "intent.yaml")
        requirements = _load_yaml(intent_root / "02-specification" / "requirements.yaml")
    except (OSError, yaml.YAMLError) as error:
        return ValidationReport((f"unreadable required artifact: {error}",))

    _validate_schema(intent, "intent.schema.json", "intent.yaml", errors)
    _validate_schema(requirements, "requirement.schema.json", "requirements.yaml", errors)
    if not isinstance(intent, dict) or not isinstance(requirements, dict):
        errors.append("intent.yaml and requirements.yaml must contain mappings")
        return ValidationReport(tuple(errors))
    if intent.get("artifact_mode") == "full":
        required_paths = [intent_root / "01-brief" / "brief.md"]
        if require_plan:
            required_paths.append(intent_root / "03-planning" / "master-plan.md")
        if intent.get("contains_model_behavior"):
            required_paths.append(intent_root / "02-specification" / "eval-design.md")
        for path in required_paths:
            if not path.is_file():
                relative = str(path.relative_to(intent_root)).replace("\\", "/")
                errors.append(f"full Intent is missing {relative}")
    tasks: list[dict[str, Any]] = []
    for task_path in sorted(intent_root.rglob("task.yaml")):
        try:
            task = _load_yaml(task_path)
            if not isinstance(task, dict):
                errors.append(f"Task must contain a mapping: {task_path.relative_to(intent_root)}")
                continue
            tasks.append(task)
            _validate_schema(task, "task.schema.json", str(task_path.relative_to(intent_root)), errors)
        except (OSError, yaml.YAMLError) as error:
            errors.append(f"unreadable Task {task_path}: {error}")
    for phase_path in sorted((intent_root / "04-build").glob("*/phase.yaml")):
        label = str(phase_path.relative_to(intent_root)).replace("\\", "/")
        try:
            phase = _load_yaml(phase_path)
        except (OSError, yaml.YAMLError) as error:
            errors.append(f"unreadable Build Phase {label}: {error}")
            continue
        _validate_schema(phase, "phase.schema.json", label, errors)
        folder = phase_path.parent.name  # the phase id, optionally followed by a slug
        if isinstance(phase, dict) and not (folder == phase.get("id") or folder.startswith(f"{phase.get('id')}-")):
            errors.append(f"{label}: id {phase.get('id')!r} does not match its folder {folder!r}")

    requirement_records = requirements.get("functional_requirements", []) + requirements.get(
        "non_functional_requirements", []
    )
    requirement_ids = {item.get("id") for item in requirement_records}
    criteria = requirements.get("acceptance_criteria", [])
    criterion_ids = {item.get("id") for item in criteria}
    all_ids = [item.get("id") for item in requirement_records + criteria + requirements.get("success_criteria", [])]
    duplicates = sorted({identifier for identifier in all_ids if identifier and all_ids.count(identifier) > 1})
    if duplicates:
        errors.append(f"duplicate identifiers: {', '.join(duplicates)}")

    owned_criteria: set[str] = set()
    for requirement in requirement_records:
        for criterion_id in requirement.get("acceptance_criteria", []):
            owned_criteria.add(criterion_id)
            if criterion_id not in criterion_ids:
                errors.append(f"{requirement.get('id')} references unknown criterion {criterion_id}")
    # Traceability is built requirement-down, so a criterion no requirement owns never
    # appears in the matrix: it could be verified while tracing to nothing Gate 1 approved.
    for criterion_id in sorted(identifier for identifier in criterion_ids - owned_criteria if identifier):
        errors.append(f"{criterion_id} is not owned by any requirement")
    for success in requirements.get("success_criteria", []):
        for criterion_id in success.get("acceptance_criteria", []) or []:
            if criterion_id not in criterion_ids:
                errors.append(f"{success.get('id')} references unknown criterion {criterion_id}")
    # S2: interfaces and threats are specification too, validated against the same file.
    errors.extend(validate_contracts(intent_root / "02-specification", requirements))
    errors.extend(validate_threat_model(intent_root, intent, requirements))
    errors.extend(validate_accessibility(requirements))
    # S3.3: a breaking change against the last completed Intent for the same `api`.
    from .contractregistry import breaking_change_errors

    errors.extend(breaking_change_errors(intent_root, str(intent.get("id"))))
    # `wont` is recorded scope, not planned scope: it needs no Task, and building it is growth.
    deferred_requirements = {item.get("id") for item in requirement_records if item.get("priority") == "wont"}

    # F1/F2: architectural decisions are the third trace target the repository rules
    # name. Validated exactly like requirements — every reference must resolve, and every
    # recorded decision must earn its place by having work that implements it.
    try:
        decisions = load_decisions(intent_root)
    except DecisionError as error:
        errors.append(str(error))
        decisions = []
    decision_ids = {str(record.get("id")) for record in decisions if record.get("id")}
    for record in decisions:
        _validate_schema(record, "decision.schema.json", f"decisions/{record.get('id')}.yaml", errors)
        if record.get("intent_id") != intent.get("id"):
            errors.append(f"{record.get('id')} has mismatched intent_id {record.get('intent_id')}")
        superseded = record.get("supersedes")
        if superseded and superseded not in decision_ids:
            errors.append(f"{record.get('id')} supersedes unknown decision {superseded}")
        for requirement_id in record.get("requirements", []):
            if requirement_id not in requirement_ids:
                errors.append(f"{record.get('id')} references unknown requirement {requirement_id}")

    mapped_decisions: set[str] = set()
    mapped_requirements: set[str] = set()
    for task in tasks:
        if task.get("intent_id") != intent.get("id"):
            errors.append(f"{task.get('id')} has mismatched intent_id {task.get('intent_id')}")
        for decision_id in task.get("source_decisions", []):
            mapped_decisions.add(decision_id)
            if decision_id not in decision_ids:
                errors.append(f"{task.get('id')} references unknown decision {decision_id}")
            elif _decision_status(decisions, decision_id) == "superseded":
                errors.append(
                    f"{task.get('id')} traces to superseded decision {decision_id}; "
                    "retarget it at the decision that replaced it"
                )
        for requirement_id in task.get("requirements", []):
            mapped_requirements.add(requirement_id)
            if requirement_id not in requirement_ids:
                errors.append(f"{task.get('id')} references unknown requirement {requirement_id}")
            elif requirement_id in deferred_requirements:
                errors.append(
                    f"{task.get('id')} implements {requirement_id}, which is prioritised `wont` for this Intent; "
                    "building deferred scope is scope growth — reprioritise it through Gate 2 or drop the mapping"
                )
        for criterion in task.get("acceptance_criteria", []):
            if criterion.get("id") not in criterion_ids:
                errors.append(f"{task.get('id')} references unknown criterion {criterion.get('id')}")
        for dependency in task.get("depends_on", []):
            if dependency not in {candidate.get("id") for candidate in tasks}:
                errors.append(f"{task.get('id')} references unknown dependency {dependency}")
    if require_plan and not tasks:
        errors.append("Intent has no Task artifacts")
    if require_plan and intent.get("artifact_mode") == "full" and not list(intent_root.rglob("phase.yaml")):
        errors.append("full Intent has no Build Phase artifact")
    if require_plan:
        for requirement_id in sorted(requirement_ids - mapped_requirements - deferred_requirements):
            errors.append(f"requirement has no Task mapping: {requirement_id}")
        # An accepted decision with no Task implementing it is either work nobody planned
        # or a decision nobody needed. Both are worth saying out loud before Gate 3.
        for decision_id in sorted(decision_ids - mapped_decisions):
            if _decision_status(decisions, decision_id) == "accepted":
                errors.append(f"accepted decision has no Task mapping: {decision_id}")

    classes = {criterion.get("verification_class") for criterion in criteria}
    for criterion in criteria:
        verification_class = criterion.get("verification_class")
        criterion_id = criterion.get("id", "unknown")
        if verification_class == "D" and not criterion.get("check"):
            errors.append(f"{criterion_id} Class D criterion requires a deterministic check")
        if verification_class in {"S", "J"}:
            required_eval_fields = {"eval", "dataset", "metric", "threshold", "minimum_sample_size", "tolerance_band"}
            missing_eval_fields = sorted(field for field in required_eval_fields if field not in criterion)
            if missing_eval_fields:
                errors.append(f"{criterion_id} is missing evaluation fields: {', '.join(missing_eval_fields)}")
        if verification_class == "N":
            required_eval_fields = {"eval", "dataset", "metric", "threshold", "minimum_sample_size"}
            missing_eval_fields = sorted(field for field in required_eval_fields if field not in criterion)
            if missing_eval_fields:
                errors.append(f"{criterion_id} is missing adversarial fields: {', '.join(missing_eval_fields)}")
            threshold = criterion.get("threshold", {})
            if not isinstance(threshold, dict) or threshold.get("operator") != "==" or threshold.get("value") != 0:
                errors.append(f"{criterion_id} Class N criterion requires a zero-success threshold")
    for finding_path in sorted((intent_root / "05-verification").glob("findings.yaml")):
        payload = _load_yaml(finding_path) or {}
        if not isinstance(payload, (dict, list)):
            errors.append(f"findings artifact must contain a mapping or list: {finding_path.name}")
            continue
        findings = payload if isinstance(payload, list) else payload.get("findings", [])
        for finding in findings:
            if not isinstance(finding, dict):
                errors.append(f"finding must contain a mapping: {finding_path.name}")
                continue
            # Loop metrics read `introduced_stage`/`detected_stage`; an unvalidated finding
            # used to pass convergence and close, then crash `loop-metrics` much later.
            _validate_schema(finding, "finding.schema.json", f"{finding_path.name}:{finding.get('id')}", errors)
            if finding.get("repeat") is True and not finding.get("lesson_ref"):
                errors.append(f"repeat finding {finding.get('id')} must reference its prior lesson")
    errors.extend(validate_migrations(tasks))
    if intent.get("contains_model_behavior") and not classes.intersection({"S", "J", "N"}):
        errors.append("model behavior requires at least one S, J, or N criterion")
    security_sensitive = intent.get("intent_type") == "security" or intent.get("trigger_flags", {}).get("multi_tenant")
    if security_sensitive and "N" not in classes:
        errors.append("security and multi-tenant Intents require at least one Class N criterion")
    if intent.get("artifact_mode") == "compact" and classes - {"D"}:
        errors.append("compact Intent may contain only Class D criteria")
    return ValidationReport(tuple(errors))
