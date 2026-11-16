"""Threat model as a specification artifact (Wave S2.2).

The constitution already says security, isolation, and side-effect requirements need a
Class N criterion with zero tolerance. What it could not say is *which* attacks those
criteria must cover, because nothing enumerated them. A STRIDE model per trust boundary is
that enumeration, and it joins the rest of the specification at two points:

- a mitigated threat names the requirement that mitigates it and a Class N criterion that
  proves the mitigation holds, so every threat is a traceable, adversarially tested claim;
- it may also cite W4.3 standing security checks, but only ones this Intent actually
  inherits, and only as supplementary assurance. A generic scanner does not demonstrate
  that a specific attack fails.

Silence is not a judgement: each boundary either names a threat in every STRIDE category or
records why that category does not apply there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .assets import repository_root_of, schema_path
from .jsonschema_mini import Draft202012Validator
from .vendor import yaml

STRIDE = (
    "spoofing",
    "tampering",
    "repudiation",
    "information_disclosure",
    "denial_of_service",
    "elevation_of_privilege",
)
THREAT_MODEL = "threat-model.yaml"


def threat_model_required(intent: dict[str, Any]) -> bool:
    """Full Intents that are security work, multi-tenant, or side-effecting need a model.

    Compact Intents are exempt because they may carry only Class D criteria, and a
    mitigated threat needs Class N. A compact side-effecting Intent is flagged by the
    quality lint instead, so the gap is visible without making compact mode unusable.
    """
    if intent.get("artifact_mode") != "full":
        return False
    flags = intent.get("trigger_flags") or {}
    return bool(intent.get("intent_type") == "security" or flags.get("multi_tenant") or flags.get("has_side_effect"))


def inherited_standing_checks(intent_root: Path, intent: dict[str, Any]) -> set[str] | None:
    """Names of the W4.3 checks this Intent inherits, or None when none are configured."""
    root = repository_root_of(intent_root)
    path = root / ".ai-sdlc" / "security" / "standing-checks.yaml" if root else None
    if path is None or not path.is_file():
        return None
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    flags = dict(intent.get("trigger_flags") or {})
    flags["contains_model_behavior"] = bool(intent.get("contains_model_behavior"))
    return {
        str(check.get("name"))
        for check in payload.get("checks", []) or []
        if isinstance(check, dict) and (check.get("trigger") is None or flags.get(check.get("trigger")))
    }


def inherited_threats(intent_root: Path, intent: dict[str, Any]) -> list[dict[str, Any]]:
    """Promoted threat lessons this Intent must answer (S3.4). Read-only: fires nothing."""
    from .lessons import LessonRegistry

    root = repository_root_of(intent_root)
    if root is None:
        return []
    flags = dict(intent.get("trigger_flags") or {})
    flags["contains_model_behavior"] = bool(intent.get("contains_model_behavior"))
    return [
        target
        for target in LessonRegistry(root).matching(flags, "stage_2_specification")
        if target.get("type") == "threat"
    ]


def validate_threat_model(intent_root: Path, intent: dict[str, Any], requirements: dict[str, Any]) -> list[str]:
    path = intent_root / "02-specification" / THREAT_MODEL
    inherited_lessons = inherited_threats(intent_root, intent)
    if not path.is_file():
        if threat_model_required(intent):
            return [
                f"full {intent.get('intent_type')} Intent with security, multi-tenant, or side-effect triggers "
                f"is missing 02-specification/{THREAT_MODEL}"
            ]
        if inherited_lessons:
            names = ", ".join(sorted(item["lesson_id"] for item in inherited_lessons))
            return [f"Intent inherits promoted threat(s) {names} and has no {THREAT_MODEL} to answer them"]
        return []
    try:
        model = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        return [f"unreadable {THREAT_MODEL}: {error}"]

    errors: list[str] = []
    schema_file = schema_path(repository_root_of(intent_root) or Path.cwd(), "threat-model.schema.json")
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    for error in Draft202012Validator(schema).iter_errors(model):
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        errors.append(f"{THREAT_MODEL}:{location}: {error.message}")
    if errors or not isinstance(model, dict):
        return errors
    if model.get("intent_id") != intent.get("id"):
        errors.append(f"{THREAT_MODEL} has mismatched intent_id {model.get('intent_id')}")

    requirement_ids = {
        item.get("id")
        for item in (requirements.get("functional_requirements") or [])
        + (requirements.get("non_functional_requirements") or [])
        if isinstance(item, dict)
    }
    criteria = {
        item.get("id"): item for item in requirements.get("acceptance_criteria") or [] if isinstance(item, dict)
    }
    boundaries = {item["id"]: item for item in model.get("boundaries", [])}
    inherited = inherited_standing_checks(intent_root, intent)

    ids = [threat["id"] for threat in model.get("threats", [])]
    for duplicate in sorted({identifier for identifier in ids if ids.count(identifier) > 1}):
        errors.append(f"{THREAT_MODEL} declares {duplicate} twice")

    for threat in model.get("threats", []):
        threat_id = threat["id"]
        if threat["boundary"] not in boundaries:
            errors.append(f"{threat_id} is on unknown trust boundary {threat['boundary']}")
        for requirement_id in threat.get("mitigated_by", []):
            if requirement_id not in requirement_ids:
                errors.append(f"{threat_id} is mitigated by unknown requirement {requirement_id}")
        for criterion_id in threat.get("verified_by", []):
            if criterion_id not in criteria:
                errors.append(f"{threat_id} is verified by unknown criterion {criterion_id}")
        for check in threat.get("standing_checks", []):
            if inherited is not None and check not in inherited:
                errors.append(
                    f"{threat_id} cites standing check {check}, which this Intent does not inherit; "
                    "assurance from a check that never runs for this change is not assurance"
                )
        disposition = threat["disposition"]
        if disposition == "mitigate":
            if not threat.get("mitigated_by"):
                errors.append(f"{threat_id} is mitigated by no requirement")
            classes = {
                criteria[item].get("verification_class") for item in threat.get("verified_by", []) if item in criteria
            }
            if "N" not in classes:
                errors.append(
                    f"{threat_id} has no Class N criterion proving the mitigation; an attack is disproved "
                    "adversarially, and standing checks do not substitute"
                )
        elif not (threat.get("accepted_by") and threat.get("reason")):
            errors.append(f"{threat_id} is {disposition}ed without `accepted_by` and `reason`")

    # S3.4: a threat promoted from an earlier Intent's finding is inherited like a standing
    # suite. Mitigating it takes Class N like any threat; declining it is an acceptance with
    # a name and a reason — the constitution's "declining is an escalation", made checkable.
    answered = {
        threat.get("inherited_from"): threat for threat in model.get("threats", []) if threat.get("inherited_from")
    }
    for lesson in inherited_lessons:
        answer = answered.get(lesson["lesson_id"])
        if answer is None:
            errors.append(
                f"inherited threat {lesson['lesson_id']} ({lesson['stride']}) is not answered; add a threat with "
                f"`inherited_from: {lesson['lesson_id']}` that mitigates it or accepts it by name"
            )
        elif answer.get("stride") != lesson["stride"]:
            errors.append(
                f"{answer['id']} answers {lesson['lesson_id']} as {answer.get('stride')}, "
                f"but the promoted threat is {lesson['stride']}"
            )
    return errors


def stride_gaps(intent_root: Path) -> dict[str, list[str]]:
    """STRIDE categories each boundary neither threatens nor dismisses with a reason."""
    path = intent_root / "02-specification" / THREAT_MODEL
    if not path.is_file():
        return {}
    try:
        model = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(model, dict):
        return {}
    gaps: dict[str, list[str]] = {}
    for boundary in model.get("boundaries", []) or []:
        if not isinstance(boundary, dict):
            continue
        considered = {
            threat.get("stride")
            for threat in model.get("threats", []) or []
            if isinstance(threat, dict) and threat.get("boundary") == boundary.get("id")
        } | {item.get("category") for item in boundary.get("not_applicable", []) or [] if isinstance(item, dict)}
        missing = [category for category in STRIDE if category not in considered]
        if missing:
            gaps[str(boundary.get("id"))] = missing
    return gaps
