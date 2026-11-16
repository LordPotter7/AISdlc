"""Pipeline profiles (ADR-010): one repository, different rules for different work.

Three tiers, and a profile can reach only the last two:

1. The constitution. Seven stages, five human gates, one verification class per
   criterion, Class N for security, runner-captured pinned evidence, independent review.
   Nothing here can express changing it; that is an amendment, not a setting.
2. Framework defaults: the lint bar, each lint rule's severity, who may approve a gate,
   whether held-out waivers are allowed. Together with the constitution they are the
   `standard` profile — exactly what a repository without `pipeline.yaml` runs today.
3. Profiles in `.ai-sdlc/pipeline.yaml`. Tightening a default is free. Loosening one is
   allowed only with a waiver in the profile naming the rule, who approved it, why and
   when; a loosening without its waiver makes the file invalid.

A profile is resolved and pinned when an Intent is created: `00-intent/profile.yaml` is a
runner-owned copy of the rules the Intent is specified, planned and reviewed under, so
editing `pipeline.yaml` changes only Intents created afterwards. An Intent without a pin
runs `standard`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .storage import atomic_write_text
from .vendor import yaml

PIPELINE_PATH = Path(".ai-sdlc") / "pipeline.yaml"
PINNED = Path("00-intent") / "profile.yaml"
STANDARD = "standard"
# Lint bars from weakest to strictest: `never` blocks nothing, `warning` blocks warnings and errors.
BARS = ("never", "error", "warning")
SEVERITIES = ("info", "warning", "error")
GATES = ("G1", "G2", "G3", "G4", "G5")
STAGES = ("framed", "specified", "planned", "building")
MODES = ("compact", "full")

# What the constitution fixes, stated for people; nothing in a profile can express changing it.
CONSTITUTION = (
    "Seven stages in order, and five gates each approved by a person",
    "Every acceptance criterion has exactly one verification class: D, S, J or N",
    "Security, isolation and side-effect requirements carry a zero-tolerance Class N criterion",
    "Evidence is captured by the runner, hashed and pinned to a commit",
    "Independent review by a different model family; self-review is void",
    "Failed attempts and identifiers are preserved; release and deployment stay outside",
)


class PipelineError(ValueError):
    """The pipeline file is invalid, or a profile cannot be used."""


def _read(repository_root: Path) -> tuple[dict[str, Any], str | None]:
    path = repository_root / PIPELINE_PATH
    if not path.is_file():
        return {}, None
    data = path.read_bytes()
    try:
        raw = yaml.safe_load(data.decode("utf-8")) or {}
    except yaml.YAMLError as error:
        raise PipelineError(f"{PIPELINE_PATH.as_posix()} is not valid YAML: {error}") from None
    if not isinstance(raw, dict):
        raise PipelineError(f"{PIPELINE_PATH.as_posix()} must contain a mapping")
    return raw, "sha256:" + hashlib.sha256(data).hexdigest()


def _schema_errors(repository_root: Path, raw: dict[str, Any]) -> list[str]:
    from .validation import _validate_schema

    errors: list[str] = []
    _validate_schema(raw, "pipeline.schema.json", PIPELINE_PATH.as_posix(), errors, repository_root)
    return errors


def intent_types(repository_root: Path | None = None) -> tuple[str, ...]:
    """The Intent types, read from the one place they are declared: `intent.schema.json`."""
    from .assets import AssetError, framework_root, schema_path

    try:
        path = (
            schema_path(repository_root, "intent.schema.json")
            if repository_root
            else (framework_root() / "schemas" / "intent.schema.json")
        )
        schema = json.loads(path.read_text(encoding="utf-8"))
        return tuple(schema["properties"]["intent_type"]["enum"])
    except (AssetError, OSError, KeyError, ValueError):
        return ("feature", "bugfix", "refactor", "infrastructure", "security", "investigation_or_poc")


def defaults(repository_root: Path) -> dict[str, Any]:
    """The `standard` profile: the repository's configured framework defaults."""
    from .config import load_config

    config = load_config(repository_root)
    return {
        "name": STANDARD,
        "description": "The framework defaults this repository is configured with.",
        "modes": list(MODES),
        "required_artifacts": {},
        "fail_on": config.specification.fail_on,
        "rules": {},
        "gates": {gate: {"approvals": 1, "approvers": sorted(config.gates.approvers.get(gate, []))} for gate in GATES},
        "holdout_waiver": "allowed" if config.verification.allow_holdout_waiver else "forbidden",
        "loosened": [],
        "waivers": [],
    }


def _resolve(repository_root: Path, name: str, raw: dict[str, Any]) -> dict[str, Any]:
    from .specquality import RULE_SEVERITY

    base = defaults(repository_root)
    if name == STANDARD:
        return base
    profiles = raw.get("profiles") or {}
    if name not in profiles:
        raise PipelineError(f"unknown profile {name!r}; declared: {', '.join([STANDARD, *sorted(profiles)])}")
    spec = profiles[name] or {}
    specification = spec.get("specification") or {}
    verification = spec.get("verification") or {}
    resolved = {
        "name": name,
        "description": spec.get("description") or "",
        "modes": list(spec.get("modes") or base["modes"]),
        "required_artifacts": {stage: list(paths) for stage, paths in (spec.get("required_artifacts") or {}).items()},
        "fail_on": specification.get("fail_on", base["fail_on"]),
        "rules": dict(specification.get("rules") or {}),
        "gates": {gate: dict(base["gates"][gate]) for gate in GATES},
        "holdout_waiver": verification.get("holdout_waiver", base["holdout_waiver"]),
        "waivers": list(spec.get("waivers") or []),
    }
    for gate, policy in (spec.get("gates") or {}).items():
        if "approvals" in policy:
            resolved["gates"][gate]["approvals"] = int(policy["approvals"])
        if "approvers" in policy:
            resolved["gates"][gate]["approvers"] = sorted(policy["approvers"])

    loosened: list[dict[str, Any]] = []
    if BARS.index(resolved["fail_on"]) < BARS.index(base["fail_on"]):
        loosened.append({"rule": "specification.fail_on", "from": base["fail_on"], "to": resolved["fail_on"]})
    for code, severity in resolved["rules"].items():
        natural = RULE_SEVERITY.get(code)
        if natural is None:
            raise PipelineError(f"profile {name!r} sets {code}, which is not a lint rule")
        if SEVERITIES.index(severity) < SEVERITIES.index(natural):
            loosened.append({"rule": f"specification.rules.{code}", "from": natural, "to": severity})
    for gate in GATES:
        allowed = set(base["gates"][gate]["approvers"])
        chosen = set(resolved["gates"][gate]["approvers"])
        if allowed and (not chosen or chosen - allowed):
            loosened.append(
                {"rule": f"gates.{gate}.approvers", "from": sorted(allowed), "to": sorted(chosen) or "anyone"}
            )
    if base["holdout_waiver"] == "forbidden" and resolved["holdout_waiver"] == "allowed":
        loosened.append({"rule": "verification.holdout_waiver", "from": "forbidden", "to": "allowed"})

    waived = {str(item.get("rule")): item for item in resolved["waivers"]}
    missing = [item["rule"] for item in loosened if item["rule"] not in waived]
    if missing:
        raise PipelineError(
            f"profile {name!r} loosens {', '.join(missing)} without a waiver; add a waiver with rule, approved_by, "
            "reason and approved_at for each, or keep the default"
        )
    for item in loosened:
        item["waiver"] = {key: waived[item["rule"]].get(key) for key in ("approved_by", "reason", "approved_at")}
    resolved["loosened"] = loosened
    return resolved


def load(repository_root: Path) -> dict[str, Any]:
    """The validated pipeline: every profile resolved, or `PipelineError` naming what is wrong."""
    raw, sha = _read(repository_root)
    errors = _schema_errors(repository_root, raw) if raw else []
    if errors:
        raise PipelineError("; ".join(errors))
    types = intent_types(repository_root)
    names = {STANDARD, *(raw.get("profiles") or {})}
    for kind, profile in (raw.get("by_type") or {}).items():
        if kind not in types:
            raise PipelineError(f"by_type names {kind!r}, which is not an Intent type ({', '.join(types)})")
        if profile not in names:
            raise PipelineError(f"by_type maps {kind} to unknown profile {profile!r}")
    if raw.get("default", STANDARD) not in names:
        raise PipelineError(f"default profile {raw.get('default')!r} is not declared")
    profiles = {name: _resolve(repository_root, name, raw) for name in sorted(names)}
    return {
        "sha256": sha,
        "default": raw.get("default", STANDARD),
        "by_type": dict(raw.get("by_type") or {}),
        "profiles": profiles,
    }


def profile_for(repository_root: Path, intent_type: str, explicit: str | None = None) -> str:
    pipeline = load(repository_root)
    if explicit:
        if explicit not in pipeline["profiles"]:
            raise PipelineError(f"unknown profile {explicit!r}; declared: {', '.join(sorted(pipeline['profiles']))}")
        # The type's own profile (or the default) is the floor a person may choose above, never below.
        selected = pipeline["by_type"].get(intent_type) or pipeline["default"]
        if selected != explicit:
            weaker = weaker_than(pipeline["profiles"][selected], pipeline["profiles"][explicit])
            if weaker:
                raise PipelineError(
                    f"{intent_type} Intents run {selected!r}; {explicit!r} is weaker ({'; '.join(weaker)}). "
                    "Choose it only where it is at least as strict, or change by_type with review"
                )
        return explicit
    return pipeline["by_type"].get(intent_type) or pipeline["default"]


def weaker_than(stricter: dict[str, Any], other: dict[str, Any]) -> list[str]:
    """Every rule on which `other` is weaker than `stricter`; empty when it is at least as strict."""
    reasons = []
    if set(other["modes"]) - set(stricter["modes"]):
        reasons.append(f"allows {', '.join(sorted(set(other['modes']) - set(stricter['modes'])))} mode")
    for stage, paths in stricter["required_artifacts"].items():
        gone = set(paths) - set(other["required_artifacts"].get(stage, []))
        if gone:
            reasons.append(f"does not require {', '.join(sorted(gone))} before {stage}")
    if BARS.index(other["fail_on"]) < BARS.index(stricter["fail_on"]):
        reasons.append(f"lint bar {other['fail_on']} instead of {stricter['fail_on']}")
    from .specquality import RULE_SEVERITY

    for code in set(stricter["rules"]) | set(other["rules"]):
        mine = stricter["rules"].get(code, RULE_SEVERITY.get(code, "info"))
        theirs = other["rules"].get(code, RULE_SEVERITY.get(code, "info"))
        if SEVERITIES.index(theirs) < SEVERITIES.index(mine):
            reasons.append(f"{code} at {theirs} instead of {mine}")
    for gate in GATES:
        mine, theirs = stricter["gates"][gate], other["gates"][gate]
        if theirs["approvals"] < mine["approvals"]:
            reasons.append(f"{gate} needs {theirs['approvals']} approval(s) instead of {mine['approvals']}")
        if mine["approvers"] and (not theirs["approvers"] or set(theirs["approvers"]) - set(mine["approvers"])):
            reasons.append(f"{gate} accepts approvers outside {', '.join(mine['approvers'])}")
    if stricter["holdout_waiver"] == "forbidden" and other["holdout_waiver"] == "allowed":
        reasons.append("allows held-out waivers")
    return reasons


def pin(repository_root: Path, intent_root: Path, name: str) -> dict[str, str]:
    """Write the resolved profile into the Intent, and return what `intent.yaml` records."""
    pipeline = load(repository_root)
    resolved = pipeline["profiles"][name]
    record = {"pinned_at": datetime.now(UTC).isoformat(), "pipeline_sha256": pipeline["sha256"], **resolved}
    text = (
        "# Runner-owned (ADR-010): the pipeline rules this Intent was created under. Never edited by hand.\n"
        + yaml.safe_dump(record, sort_keys=False)
    )
    target = intent_root / PINNED
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, text)
    return {"name": name, "sha256": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()}


def pinned(intent_root: Path, repository_root: Path | None = None) -> dict[str, Any]:
    """The rules an Intent runs under: its pinned profile, or `standard` when it has none."""
    path = intent_root / PINNED
    if path.is_file():
        record = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(record, dict) and record.get("name"):
            return record
    if repository_root is None:
        from .assets import repository_root_of

        repository_root = repository_root_of(intent_root)
    if repository_root is None:
        raise PipelineError(f"cannot find the repository of {intent_root}")
    return {**defaults(repository_root), "implicit": True}


def describe(repository_root: Path) -> dict[str, Any]:
    """The effective pipeline for people: the constitution, `standard`, every profile, or the error."""
    try:
        pipeline = load(repository_root)
        error = None
    except PipelineError as problem:
        pipeline = {
            "sha256": None,
            "default": STANDARD,
            "by_type": {},
            "profiles": {STANDARD: defaults(repository_root)},
        }
        error = str(problem)
    return {
        "constitution": list(CONSTITUTION),
        "present": (repository_root / PIPELINE_PATH).is_file(),
        "error": error,
        **pipeline,
    }
