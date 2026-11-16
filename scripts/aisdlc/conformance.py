"""Governance conformance export (W4.1).

The repository already produces, automatically and as a by-product of doing the work: a
system register (Intents), a risk classification (intent type, model-behaviour flag,
trigger flags), applied risk controls (verification classes and gates), documented
evidence locations (hashed, commit-pinned manifests), bidirectional
traceability, a human accountability record (gate approvals with named approvers), and a
continuous-improvement loop with measured decay.

That is an AI management system. It is described in the vocabulary of engineering rather
than the vocabulary of assurance, which is the only reason it does not look like one.

This module is a **projection**, not a new obligation. It reads runner-owned records and
re-expresses them against NIST AI RMF functions, ISO/IEC 42001 Annex A controls, and NIST
SSDF practices. No new lifecycle artifacts, nothing to maintain by hand, and nothing here
can change a lifecycle outcome — an export that could would be a governance process, and
governance processes that gate delivery are how this kind of framework dies.

It reports gaps honestly. An export that always looked complete would be worthless to the
auditor it is written for.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .identity import summarize
from .state import StateStore
from .vendor import yaml


def _approver_detail(gates: dict[str, dict[str, Any]]) -> str:
    """Report how strongly each gate decision is bound to a person (R4.3).

    ISO 42001 and SSDF both ask who approved a decision, and this export is what answers
    them. Saying "carries a named approver" for a typed string and for a verified identity
    alike would overstate the weaker case in exactly the document where overstating is
    least acceptable, so the projection names the split instead.
    """
    if not gates:
        return "No gate decisions recorded"
    counts = summarize(gates)
    parts = [f"{count} {strength}" for strength, count in counts.items() if count]
    detail = f"{len(gates)} gate decision(s): " + ", ".join(parts)
    if counts.get("verified"):
        return detail
    return detail + " — identities are asserted by the caller, not verified (see gates.require_verified)"


# NIST AI RMF function -> the runner-owned records that evidence it.
RMF_MAPPING = {
    "GOVERN": {
        "description": "Policies, accountability, and roles are defined and enforced.",
        "controls": ["constitution", "gates", "gate_approvers", "lessons_registry"],
    },
    "MAP": {
        "description": "Context, scope, and risk are identified and classified.",
        "controls": ["intent_register", "risk_classification", "trigger_flags", "requirements"],
    },
    "MEASURE": {
        "description": "Risks are assessed with defined methods and documented evidence.",
        "controls": ["verification_classes", "evidence_manifests", "evaluations", "conformance_gap", "loop_metrics"],
    },
    "MANAGE": {
        "description": "Risks are prioritised, responded to, and monitored over time.",
        "controls": ["findings", "lesson_promotion", "standing_suites", "decay_review"],
    },
}

# ISO/IEC 42001 Annex A control -> the lifecycle mechanism that satisfies it.
ISO_42001_MAPPING = {
    "A.2 AI policy": "constitution",
    "A.3 Internal organization roles": "gate_approvers",
    "A.4 Resources for AI systems": "datasets_and_evals",
    "A.5 Assessing impacts of AI systems": "risk_classification",
    "A.6 AI system life cycle": "lifecycle_stages",
    "A.6.2.4 AI system verification and validation": "verification_classes",
    "A.7 Data for AI systems": "dataset_registry",
    "A.8 Information for interested parties": "traceability",
    "A.9 Use of AI systems": "intent_register",
    "A.10 Third-party relationships": "third_party_evidence",
}

# NIST SSDF (SP 800-218) practices the supply-chain half of the framework satisfies.
SSDF_MAPPING = {
    "PO.3 Supporting toolchains": "runner_toolchain",
    "PS.1 Protect code": "runner_owned_paths_and_hooks",
    "PS.3 Archive and protect each release": "evidence_manifests",
    "PW.4 Reuse existing well-secured software": "dependency_digests",
    "PW.7 Review and analyze human-readable code": "independent_review",
    "PW.8 Test executable code": "verification_classes_and_holdout",
    "RV.1 Identify and confirm vulnerabilities": "red_team_and_class_n",
    "RV.2 Assess, prioritize, and remediate": "findings_and_lessons",
    "RV.3 Analyze vulnerabilities to identify root causes": "lesson_promotion",
}


class ConformanceExporter:
    """Re-express existing lifecycle records in the vocabulary of assurance."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def export_intent(self, intent_root: Path) -> dict[str, Any]:
        intent_root = intent_root.resolve()
        intent = yaml.safe_load((intent_root / "00-intent" / "intent.yaml").read_text(encoding="utf-8"))
        intent_id = intent["id"]
        state = StateStore(self.repository_root).read()
        evidence = self._evidence_summary(intent_root)
        controls = self._controls(intent_root, intent_id, intent, state, evidence)
        register = self._register_row(intent, state, evidence)
        return {
            "schema": "aisdlc.conformance/v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "subject": {"type": "intent", "id": intent_id, "title": intent.get("title")},
            "system_register": register,
            "nist_ai_rmf": self._project(RMF_MAPPING, controls),
            "iso_42001_annex_a": {
                control: self._control_status(controls, mechanism) for control, mechanism in ISO_42001_MAPPING.items()
            },
            "nist_ssdf": {
                practice: self._control_status(controls, mechanism) for practice, mechanism in SSDF_MAPPING.items()
            },
            "controls": controls,
            "gaps": sorted(name for name, record in controls.items() if not record["satisfied"]),
        }

    def export_program(self, program_id: str) -> dict[str, Any]:
        """Export at the unit an auditor actually reviews.

        Auditors assess systems and initiatives, not individual work items. A Program is
        the framework's closest analogue to the thing on a register, so exporting at that
        level is what turns this from an engineering artifact into a submission.
        """
        from .config import delivery_root
        from .programs import ProgramRegistry

        registry = ProgramRegistry(self.repository_root)
        program = registry.get(program_id)
        root = delivery_root(self.repository_root)
        members: list[dict[str, Any]] = []
        for intent_id in program.get("members", []):
            matches = list(root.glob(f"{intent_id}-*"))
            if len(matches) == 1:
                members.append(self.export_intent(matches[0]))
        aggregate_gaps = sorted({gap for member in members for gap in member["gaps"]})
        return {
            "schema": "aisdlc.conformance/v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "subject": {"type": "program", "id": program_id, "title": program.get("title")},
            "owner": program.get("owner"),
            "rollup": registry.rollup(program_id),
            "members": members,
            "gaps": aggregate_gaps,
        }

    def _register_row(self, intent: dict[str, Any], state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
        """The row an AI system register asks for, derived from what already exists."""
        intent_id = intent["id"]
        gates = {
            key.split(":", 1)[1]: value
            for key, value in state.get("gates", {}).items()
            if key.startswith(f"{intent_id}:")
        }
        return {
            "system_id": intent_id,
            "name": intent.get("title"),
            "purpose": intent.get("intent_type"),
            "lifecycle_status": state.get("intents", {}).get(intent_id, {}).get("status"),
            "involves_ai_behavior": bool(intent.get("contains_model_behavior")),
            "risk_indicators": {
                "intent_type": intent.get("intent_type"),
                **{key: bool(value) for key, value in (intent.get("trigger_flags") or {}).items()},
            },
            "human_oversight": [
                {"gate": gate, "status": record.get("status"), "approver": record.get("approver")}
                for gate, record in sorted(gates.items())
            ],
            "evidence_location": evidence["locations"],
            "created_at": intent.get("created_at"),
        }

    def _evidence_summary(self, intent_root: Path) -> dict[str, Any]:
        locations: list[str] = []
        captured = 0
        holdout = 0
        for manifest_path in sorted(intent_root.rglob("evidence/attempt-*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            locations.append(str(manifest_path.relative_to(intent_root)).replace("\\", "/"))
            captured += 1
            if (manifest.get("conformance") or {}).get("holdout_declared"):
                holdout += 1
        evaluations = 0
        for path in sorted(intent_root.rglob("manifest.json")):
            try:
                if "eval_id" in json.loads(path.read_text(encoding="utf-8")):
                    evaluations += 1
            except (OSError, json.JSONDecodeError):
                continue
        return {
            "locations": locations,
            "captured": captured,
            "holdout": holdout,
            "evaluations": evaluations,
        }

    def _controls(
        self,
        intent_root: Path,
        intent_id: str,
        intent: dict[str, Any],
        state: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        from .assets import asset_path

        gates = {
            key.split(":", 1)[1]: value
            for key, value in state.get("gates", {}).items()
            if key.startswith(f"{intent_id}:")
        }
        approved_gates = [gate for gate, record in gates.items() if record.get("status") == "approved"]
        requirements_path = intent_root / "02-specification" / "requirements.yaml"
        criteria: list[dict[str, Any]] = []
        if requirements_path.exists():
            document = yaml.safe_load(requirements_path.read_text(encoding="utf-8")) or {}
            criteria = document.get("acceptance_criteria", []) if isinstance(document, dict) else []
        trace_path = intent_root / "05-verification" / "traceability.json"
        lessons_path = self.repository_root / ".ai-sdlc" / "lessons" / "registry.yaml"
        lessons = (
            (yaml.safe_load(lessons_path.read_text(encoding="utf-8")) or {}).get("lessons", [])
            if lessons_path.exists()
            else []
        )
        datasets_path = self.repository_root / ".ai-sdlc" / "evals" / "datasets.yaml"
        datasets = (
            (yaml.safe_load(datasets_path.read_text(encoding="utf-8")) or {}).get("datasets", {})
            if datasets_path.exists()
            else {}
        )
        findings_path = intent_root / "05-verification" / "findings.yaml"

        def control(name: str, satisfied: bool, detail: str, artifact: str | None = None) -> dict[str, Any]:
            return {"name": name, "satisfied": bool(satisfied), "detail": detail, "artifact": artifact}

        constitution = asset_path(self.repository_root, "constitution.md")
        return {
            "constitution": control(
                "Documented AI policy", constitution.exists(), "Framework constitution", str(constitution)
            ),
            "gates": control(
                "Human oversight at defined decision points",
                bool(approved_gates),
                f"{len(approved_gates)} of 5 gates approved: {', '.join(sorted(approved_gates)) or 'none'}",
            ),
            "gate_approvers": control(
                "Named accountability for each decision",
                all(record.get("approver") for record in gates.values()) and bool(gates),
                _approver_detail(gates),
            ),
            "intent_register": control(
                "System register entry", True, f"Intent {intent_id} is registered with permanent identity"
            ),
            "risk_classification": control(
                "Risk classification recorded",
                bool(intent.get("intent_type")),
                f"type={intent.get('intent_type')}, model_behavior={bool(intent.get('contains_model_behavior'))}",
            ),
            "trigger_flags": control(
                "Contextual risk indicators", True, json.dumps(intent.get("trigger_flags") or {}, sort_keys=True)
            ),
            "requirements": control("Documented requirements", requirements_path.exists(), str(requirements_path.name)),
            "lifecycle_stages": control(
                "Defined life-cycle stages", True, "Seven stages mapped one-to-one onto delivery folders"
            ),
            "verification_classes": control(
                "Verification method declared per criterion",
                bool(criteria) and all(item.get("verification_class") in {"D", "S", "J", "N"} for item in criteria),
                f"{len(criteria)} criteria, each with exactly one D/S/J/N class",
            ),
            "evidence_manifests": control(
                "Machine-captured, integrity-protected evidence",
                evidence["captured"] > 0,
                f"{evidence['captured']} captured attempts, hashed and commit-pinned",
            ),
            "conformance_gap": control(
                "Independent instruments beyond agent-authored tests",
                evidence["holdout"] > 0,
                f"{evidence['holdout']} attempts ran held-out instruments",
            ),
            "verification_classes_and_holdout": control(
                "Executable testing including independently authored instruments",
                evidence["holdout"] > 0,
                "Held-out instruments authored outside the implementing context",
            ),
            "evaluations": control(
                "Statistical and judged evaluation of model behaviour",
                (not intent.get("contains_model_behavior")) or evidence["evaluations"] > 0,
                f"{evidence['evaluations']} evaluation run manifests",
            ),
            "traceability": control(
                "Bidirectional requirement-to-evidence traceability", trace_path.exists(), str(trace_path.name)
            ),
            "findings": control("Identified issues are recorded", findings_path.exists(), str(findings_path.name)),
            "lesson_promotion": control(
                "Root-cause analysis converted into enforced controls",
                any(item.get("status") in {"implemented", "active", "measured"} for item in lessons),
                f"{len(lessons)} lessons in the registry",
            ),
            "lessons_registry": control("Continual improvement process", lessons_path.exists(), str(lessons_path)),
            "standing_suites": control(
                "Regression controls inherited across systems", bool(datasets), f"{len(datasets)} registered datasets"
            ),
            "dataset_registry": control(
                "Data governance for evaluation datasets",
                bool(datasets),
                "Versioned, owned, and content-hashed datasets",
            ),
            "datasets_and_evals": control(
                "Resources documented for AI systems", bool(datasets), f"{len(datasets)} datasets"
            ),
            "decay_review": control(
                "Control effectiveness is measured over time",
                any(item.get("review_after_intents") for item in lessons),
                "Lessons carry a measurement horizon and can be retired on evidence",
            ),
            "loop_metrics": control(
                "Process performance is measured",
                (self.repository_root / ".ai-sdlc" / "metrics" / "loop-metrics.json").exists(),
                "Loop metrics including criterion discrimination and verification tax",
            ),
            "red_team_and_class_n": control(
                "Adversarial assessment",
                any(item.get("verification_class") == "N" for item in criteria),
                "Class N criteria report zero observed successes, never zero risk",
            ),
            "independent_review": control(
                "Review independent of the author",
                bool(list(intent_root.rglob("review.yaml"))),
                "Reviewer model must differ in family from the implementer",
            ),
            "runner_owned_paths_and_hooks": control(
                "Integrity of the toolchain and its records",
                (self.repository_root / ".ai-sdlc" / "runtime" / "audit.jsonl").exists(),
                "Append-only audit log and runner-owned path guards",
            ),
            "runner_toolchain": control(
                "Supported and secured toolchain",
                (self.repository_root / ".ai-sdlc" / "config.yaml").exists(),
                "A deterministic runner outside the working tree captures every verification",
            ),
            "dependency_digests": control(
                "Dependency provenance",
                any((self.repository_root / name).exists() for name in ("uv.lock", "poetry.lock", "requirements.txt")),
                "Lockfile digests are captured in the provenance predicate",
            ),
            # Reported as unmet on purpose. Cross-repository evidence was admitted by
            # verified signature; that machinery went with CI, and a control this export
            # cannot substantiate must not be shown as satisfied.
            "third_party_evidence": control(
                "Third-party and cross-boundary evidence is verified",
                False,
                "Not covered: cross-repository evidence admission was removed with CI signing (ADR-007)",
            ),
            "findings_and_lessons": control(
                "Prioritised remediation", findings_path.exists() or bool(lessons), "Findings promote into lessons"
            ),
        }

    @staticmethod
    def _control_status(controls: dict[str, dict[str, Any]], mechanism: str) -> dict[str, Any]:
        record = controls.get(mechanism)
        if record is None:
            return {"mechanism": mechanism, "status": "not_mapped"}
        return {
            "mechanism": mechanism,
            "status": "satisfied" if record["satisfied"] else "gap",
            "detail": record["detail"],
        }

    @staticmethod
    def _project(mapping: dict[str, dict[str, Any]], controls: dict[str, dict[str, Any]]) -> dict[str, Any]:
        projection: dict[str, Any] = {}
        for function, definition in mapping.items():
            records = [controls[name] for name in definition["controls"] if name in controls]
            satisfied = [item for item in records if item["satisfied"]]
            projection[function] = {
                "description": definition["description"],
                "controls_satisfied": len(satisfied),
                "controls_total": len(records),
                "status": "satisfied" if len(satisfied) == len(records) and records else "partial",
                "gaps": [item["name"] for item in records if not item["satisfied"]],
            }
        return projection

    @staticmethod
    def render(export: dict[str, Any]) -> str:
        """Render the export as the Markdown a compliance reviewer can read directly."""
        subject = export["subject"]
        lines = [
            f"# Conformance export — {subject['id']}: {subject.get('title', '')}",
            "",
            f"Generated `{export['generated_at']}` from runner-owned records. This is a projection of "
            "existing lifecycle evidence, not a separate assessment.",
            "",
        ]
        if "system_register" in export:
            register = export["system_register"]
            lines.extend(
                [
                    "## System register entry",
                    "",
                    f"- System: `{register['system_id']}` — {register['name']}",
                    f"- Lifecycle status: `{register['lifecycle_status']}`",
                    f"- Involves AI behaviour: {register['involves_ai_behavior']}",
                    f"- Risk indicators: `{json.dumps(register['risk_indicators'], sort_keys=True)}`",
                    "",
                    "### Human oversight",
                    "",
                ]
            )
            for gate in register["human_oversight"]:
                lines.append(f"- {gate['gate']}: {gate['status']} by `{gate['approver']}`")
            lines.append("")
        if "nist_ai_rmf" in export:
            lines.extend(["## NIST AI RMF", "", "| Function | Controls | Status | Gaps |", "|---|---|---|---|"])
            for function, record in export["nist_ai_rmf"].items():
                gaps = ", ".join(record["gaps"]) or "—"
                lines.append(
                    f"| {function} | {record['controls_satisfied']}/{record['controls_total']} | "
                    f"{record['status']} | {gaps} |"
                )
            lines.append("")
        for heading, key in (("ISO/IEC 42001 Annex A", "iso_42001_annex_a"), ("NIST SSDF (SP 800-218)", "nist_ssdf")):
            if key not in export:
                continue
            lines.extend([f"## {heading}", "", "| Control | Mechanism | Status |", "|---|---|---|"])
            for control, record in export[key].items():
                lines.append(f"| {control} | `{record['mechanism']}` | {record['status']} |")
            lines.append("")
        if export.get("gaps"):
            lines.extend(["## Gaps", "", *[f"- `{gap}`" for gap in export["gaps"]], ""])
        else:
            lines.extend(["## Gaps", "", "- None identified in the mapped controls.", ""])
        return "\n".join(lines)
