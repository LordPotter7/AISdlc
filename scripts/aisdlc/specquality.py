"""Requirement-quality lint for Stage 2 (Wave S1).

Everything downstream of Gate 2 inherits the specification's quality. A held-out
instrument measuring "the service shall be fast" measures nothing, however carefully it is
pinned, so the cheapest place to catch an unverifiable requirement is before anyone builds
against it.

The rules are the characteristics ISO/IEC/IEEE 29148 asks of a requirement — singular,
unambiguous, verifiable, complete, traceable — reduced to what can be decided by reading
the file. Judgement that needs a reader stays with the red-team pre-gate review. This
module decides nothing about the lifecycle and writes nothing: it reports, and the caller
applies `specification.fail_on`.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .vendor import yaml

SEVERITIES = ("info", "warning", "error")
# Each rule's built-in severity. A pipeline profile that lowers one is loosening a default (ADR-010).
RULE_SEVERITY = {
    "SQ001": "error",
    "SQ002": "warning",
    "SQ003": "warning",
    "SQ004": "warning",
    "SQ005": "warning",
    "SQ006": "info",
    "SQ007": "info",
    "SQ008": "info",
    "SQ009": "info",
    "SQ010": "warning",
    "SQ011": "warning",
    "SQ012": "warning",
    "SQ013": "warning",
    "SQ014": "warning",
    "SQ015": "warning",
    "SQ016": "info",
    "SQ017": "warning",
    "SQ018": "info",
}

# Words that make a statement unverifiable unless the glossary pins them down. Kept short
# and specific: a lexicon that flags every adjective trains people to ignore the lint.
AMBIGUOUS_TERMS = (
    "fast",
    "quick",
    "quickly",
    "slow",
    "responsive",
    "efficient",
    "efficiently",
    "scalable",
    "user-friendly",
    "user friendly",
    "easy",
    "easily",
    "simple",
    "intuitive",
    "seamless",
    "seamlessly",
    "robust",
    "flexible",
    "reliable",
    "secure",
    "securely",
    "modern",
    "state-of-the-art",
    "appropriate",
    "appropriately",
    "adequate",
    "sufficient",
    "reasonable",
    "normally",
    "typically",
    "as needed",
    "as appropriate",
    "if possible",
    "where possible",
    "when necessary",
    "as required",
    "minimal",
    "minimize",
    "maximize",
    "optimal",
    "optimize",
    "significant",
    "high performance",
    "large number",
    "many",
    "several",
    "some",
    "various",
    "etc",
)
OPEN_ENDED = ("etc.", "and so on", "and so forth", "including but not limited to", "among others")
PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}|\bTBD\b|\bTBC\b|\bTODO\b", re.IGNORECASE)
MODAL = re.compile(r"\b(shall|must|will)\b", re.IGNORECASE)
BINDING = re.compile(r"\b(shall|must)\b", re.IGNORECASE)
PROHIBITION = re.compile(r"\b(shall|must|will)\s+not\b|\bshall never\b|\bmust never\b", re.IGNORECASE)
NUMBER = re.compile(r"\d")
PROTECTIVE_CATEGORIES = {"privacy", "security", "compliance"}


@dataclass(frozen=True)
class SpecFinding:
    rule: str
    severity: str
    location: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", re.IGNORECASE)


_TERM_PATTERNS = {term: _term_pattern(term) for term in AMBIGUOUS_TERMS}


def _ambiguous_terms(text: str, defined: set[str]) -> list[str]:
    found = [term for term, pattern in _TERM_PATTERNS.items() if term not in defined and pattern.search(text)]
    # "etc" is reported by the open-ended rule; do not report it twice.
    return [term for term in found if term != "etc"]


def lint_requirements(
    document: dict[str, Any], shared_terms: set[str] | frozenset[str] = frozenset()
) -> list[SpecFinding]:
    """Return every quality finding for one `requirements.yaml` document.

    `shared_terms` are pinned by the repository glossary (S3.2) and count as defined.
    """
    findings: list[SpecFinding] = []

    def add(rule: str, severity: str, location: str, message: str) -> None:
        findings.append(SpecFinding(rule, severity, location, message))

    defined = {
        str(entry.get("term", "")).strip().lower()
        for entry in document.get("glossary", []) or []
        if isinstance(entry, dict)
    } | set(shared_terms)
    functional = [item for item in document.get("functional_requirements", []) or [] if isinstance(item, dict)]
    non_functional = [item for item in document.get("non_functional_requirements", []) or [] if isinstance(item, dict)]
    criteria = {
        item.get("id"): item for item in document.get("acceptance_criteria", []) or [] if isinstance(item, dict)
    }

    def check_text(location: str, text: str) -> None:
        if PLACEHOLDER.search(text):
            add("SQ001", "error", location, "unfilled placeholder or TBD; a requirement cannot be approved unfinished")
        terms = _ambiguous_terms(text, defined)
        if terms:
            add(
                "SQ002",
                "warning",
                location,
                f"ambiguous term(s) {', '.join(repr(term) for term in terms)}: quantify them, "
                "or define them in the glossary",
            )
        lowered = text.lower()
        if any(marker in lowered for marker in OPEN_ENDED):
            add("SQ003", "warning", location, "open-ended list; enumerate the cases the criterion will check")

    for requirement in functional + non_functional:
        requirement_id = str(requirement.get("id", "unknown"))
        statement = str(requirement.get("statement", ""))
        check_text(requirement_id, statement)
        if len(BINDING.findall(statement)) > 1:
            add("SQ004", "warning", requirement_id, "more than one binding clause; split it into singular requirements")
        if not MODAL.search(statement):
            add("SQ006", "info", requirement_id, "no binding modal (shall, must, will); state it as an obligation")
        if "priority" not in requirement:
            add("SQ008", "info", requirement_id, "no priority (must, should, could, wont)")
        if not requirement.get("rationale") and not requirement.get("sources"):
            add("SQ009", "info", requirement_id, "neither rationale nor sources; record why it exists and where from")
        if PROHIBITION.search(statement):
            classes = {
                criteria[criterion_id].get("verification_class")
                for criterion_id in requirement.get("acceptance_criteria", [])
                if criterion_id in criteria
            }
            if classes and classes <= {"D"}:
                add(
                    "SQ011",
                    "warning",
                    requirement_id,
                    "prohibition verified only by Class D; a negative is usually an adversarial property (Class N)",
                )

    for requirement in non_functional:
        requirement_id = str(requirement.get("id", "unknown"))
        if not requirement.get("measure") and not NUMBER.search(str(requirement.get("statement", ""))):
            add(
                "SQ005",
                "warning",
                requirement_id,
                "quality requirement with no measure; declare metric, target, unit, and condition",
            )
        if not requirement.get("category"):
            add("SQ007", "info", requirement_id, "no quality category; the coverage report cannot place it")
        if requirement.get("category") == "accessibility" and not requirement.get("manual_audit"):
            add(
                "SQ016",
                "info",
                requirement_id,
                "automated checkers decide only a minority of WCAG success criteria; name the `manual_audit` "
                "owner for the rest, which this lifecycle does not verify",
            )

    for criterion_id, criterion in criteria.items():
        text = " ".join(str(criterion.get(key, "")) for key in ("statement", "given", "when", "then"))
        check_text(str(criterion_id), text)

    sensitive = [
        str(item.get("id"))
        for item in functional + non_functional
        if item.get("personal_data") or item.get("data_classification") in {"confidential", "restricted"}
    ]
    protective = any(item.get("category") in PROTECTIVE_CATEGORIES or item.get("compliance") for item in non_functional)
    if sensitive and not protective:
        add(
            "SQ010",
            "warning",
            ", ".join(sensitive),
            "handles personal or confidential data, but no privacy, security, or compliance requirement "
            "says how it is protected",
        )

    for success in document.get("success_criteria", []) or []:
        if isinstance(success, dict) and not success.get("acceptance_criteria"):
            add(
                "SQ012",
                "warning",
                str(success.get("id", "unknown")),
                "references no acceptance criterion; nothing demonstrates this success measure",
            )
    return findings


def lint_artifacts(intent_root: Path, intent: dict[str, Any], document: dict[str, Any]) -> list[SpecFinding]:
    """Rules that need the specification directory or repository, not just `requirements.yaml` (S2, S3)."""
    from . import glossary
    from .assets import repository_root_of
    from .threatmodel import THREAT_MODEL, stride_gaps

    findings: list[SpecFinding] = []
    spec_root = intent_root / "02-specification"
    root = repository_root_of(intent_root)
    for clash in glossary.conflicts(glossary.load(root) if root else [], document.get("glossary", []) or []):
        findings.append(
            SpecFinding(
                "SQ017",
                "warning",
                clash["term"],
                f"redefines a repository glossary term owned by {clash['owner']}; use the shared definition, "
                "or ask its owner to change it",
            )
        )
    declared = {str(item.get("path")) for item in document.get("contracts", []) or [] if isinstance(item, dict)}
    for declaration in document.get("contracts", []) or []:
        if isinstance(declaration, dict) and not declaration.get("api"):
            findings.append(
                SpecFinding(
                    "SQ018",
                    "info",
                    str(declaration.get("path")),
                    "contract has no `api` identity, so cross-Intent contention and breaking-change checks cannot see it",  # noqa: E501
                )
            )
    contracts_dir = spec_root / "contracts"
    if contracts_dir.is_dir():
        for path in sorted(item for item in contracts_dir.rglob("*") if item.is_file()):
            relative = str(path.relative_to(spec_root)).replace("\\", "/")
            if relative not in declared:
                findings.append(
                    SpecFinding(
                        "SQ013",
                        "warning",
                        relative,
                        "contract file is not declared in `contracts`, so no criterion is required to cover it",
                    )
                )
    for boundary, missing in stride_gaps(intent_root).items():
        findings.append(
            SpecFinding(
                "SQ014",
                "warning",
                boundary,
                f"trust boundary is silent on {', '.join(missing)}; name a threat or record why it does not apply",
            )
        )
    flags = intent.get("trigger_flags") or {}
    risky = intent.get("intent_type") == "security" or flags.get("multi_tenant") or flags.get("has_side_effect")
    if risky and intent.get("artifact_mode") != "full" and not (spec_root / THREAT_MODEL).is_file():
        findings.append(
            SpecFinding(
                "SQ015",
                "warning",
                str(intent.get("id", "intent")),
                "security, multi-tenant, or side-effecting Intent with no threat model; consider full mode, "
                "which requires one",
            )
        )
    return findings


def quality_coverage(document: dict[str, Any]) -> dict[str, list[str]]:
    """Which quality categories the non-functional requirements address, and by what."""
    coverage: dict[str, list[str]] = {}
    for item in document.get("non_functional_requirements", []) or []:
        if isinstance(item, dict) and item.get("category"):
            coverage.setdefault(str(item["category"]), []).append(str(item.get("id")))
    return dict(sorted(coverage.items()))


def effective_fail_on(fail_on: str, intent: dict[str, Any], profile: dict[str, Any] | None = None) -> str:
    """The lowest severity that blocks Gate 2 for this Intent.

    `fail_on` is the repository's configured bar (`error`, `warning`, or `never`). Whether
    risk raises it is a decision each repository declares, not one hard-coded here: the
    Intent's pinned pipeline profile (ADR-010) sets the bar for its kind of work, so a
    security profile can block on warnings while a spike's waived profile blocks on none.
    An Intent without a profile keeps the repository's bar.
    """
    if profile and not profile.get("implicit") and profile.get("fail_on"):
        return str(profile["fail_on"])
    return fail_on


def _pinned_profile(intent_root: Path) -> dict[str, Any] | None:
    from .pipeline import PipelineError, pinned

    try:
        return pinned(intent_root)
    except (PipelineError, OSError, ValueError):
        return None


def blocking(findings: list[SpecFinding], fail_on: str) -> list[SpecFinding]:
    if fail_on == "never":
        return []
    floor = SEVERITIES.index(fail_on)
    return [finding for finding in findings if SEVERITIES.index(finding.severity) >= floor]


SOURCE_FILES = ("02-specification/requirements.yaml", "02-specification/threat-model.yaml")


def source_lines(intent_root: Path) -> dict[str, dict[str, Any]]:
    """Where each identified record is written: `{"REQ-F-001": {"file": ..., "line": 7}}`.

    Walks the YAML node tree, not the loaded data, because only nodes keep their marks.
    Covers every mapping with an `id` (requirements, criteria, success criteria, threats,
    boundaries) and every glossary `term`. The first occurrence wins.
    """
    found: dict[str, dict[str, Any]] = {}

    def walk(node: Any, relative: str) -> None:
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if (
                    isinstance(key, yaml.ScalarNode)
                    and key.value in {"id", "term"}
                    and isinstance(value, yaml.ScalarNode)
                    and value.value
                ):
                    found.setdefault(value.value, {"file": relative, "line": value.start_mark.line + 1})
                walk(value, relative)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                walk(item, relative)

    for relative in SOURCE_FILES:
        path = intent_root / relative
        try:
            walk(yaml.compose(path.read_text(encoding="utf-8")), relative)
        except (OSError, yaml.YAMLError):
            continue
    return found


def _finding_source(location: str, lines: dict[str, dict[str, Any]], intent_root: Path) -> dict[str, Any] | None:
    """The file and line a finding's location names, when it names a record or a file."""
    first = location.split(",")[0].strip()
    if first in lines:
        return lines[first]
    for candidate in (intent_root / "02-specification" / first, intent_root / first):
        if first and candidate.is_file():
            return {"file": str(candidate.relative_to(intent_root)).replace("\\", "/"), "line": None}
    return None


def lint_intent(intent_root: Path, fail_on: str = "error") -> dict[str, Any]:
    """Lint one Intent's specification and summarise it for a person and a gate packet."""
    intent = yaml.safe_load((intent_root / "00-intent" / "intent.yaml").read_text(encoding="utf-8")) or {}
    document = yaml.safe_load((intent_root / "02-specification" / "requirements.yaml").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("requirements.yaml must contain a mapping")
    intent = intent if isinstance(intent, dict) else {}
    from . import glossary
    from .assets import repository_root_of

    root = repository_root_of(intent_root)
    shared = glossary.defined_terms(glossary.load(root)) if root else set()
    findings = lint_requirements(document, shared) + lint_artifacts(intent_root, intent, document)
    profile = _pinned_profile(intent_root)
    overrides = (profile or {}).get("rules") or {}
    if overrides:  # the profile says what each rule counts as for this kind of work
        findings = [replace(item, severity=overrides.get(item.rule, item.severity)) for item in findings]
    bar = effective_fail_on(fail_on, intent, profile)
    blocked = blocking(findings, bar)
    lines = source_lines(intent_root)
    counts = {severity: sum(1 for item in findings if item.severity == severity) for severity in SEVERITIES}
    requirements = (document.get("functional_requirements") or []) + (document.get("non_functional_requirements") or [])
    return {
        "intent_id": document.get("intent_id"),
        "profile": (profile or {}).get("name", "standard"),
        "fail_on": bar,
        "passed": not blocked,
        "counts": counts,
        "requirements": len(requirements),
        "prioritised": sum(1 for item in requirements if isinstance(item, dict) and "priority" in item),
        "quality_coverage": quality_coverage(document),
        "blocking": [item.to_dict() for item in blocked],
        "findings": [
            {**item.to_dict(), "source": _finding_source(item.location, lines, intent_root)} for item in findings
        ],
    }
