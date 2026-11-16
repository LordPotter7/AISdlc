"""Pre-assembled gate decision packets (W2.3).

Five human gates plus per-Task independent review plus three-run statistical checks plus
red-team plus convergence is a real verification tax, and the DORA evidence is that AI
adoption's return runs through review capacity: opening work faster does not deliver value
if review cannot keep up.

The wrong fix is execution profiles or risk tiers — the mechanism by which rigor gets
negotiated away, ending with everything running at the lowest tier. The right fix is to
attack gate *cost* rather than gate *count*. A human gate should be a two-minute decision
on a prepared packet, not an archaeology exercise across a delivery tree.

This module renders exactly what a gate approves: what changed since it was last
presented, which controls were inherited, what evidence exists, and what is still
outstanding. It is a read-only projection over runner-owned records — it decides nothing,
writes nothing into the lifecycle, and adds no artifact anyone must maintain.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .pregate import PreGateReviewService
from .state import StateStore
from .vendor import yaml

# What each gate is actually being asked to approve. Stated in the packet so the reviewer
# knows the scope of their decision rather than inferring it from the artifacts shown.
GATE_QUESTIONS = {
    "G1": "Is this the right problem, framed correctly, worth solving now?",
    "G2": "Are these requirements and acceptance criteria complete, testable, and correctly classified?",
    "G3": "Is this plan of Build Phases and Tasks sound, correctly ordered, and safely parallelised?",
    "G4": "Does the walking skeleton prove the approach end to end?",
    "G5": "Is this Intent complete, converged, and acceptable?",
}

GATE_ARTIFACTS = {
    "G1": ("00-intent/intent.yaml", "01-brief/brief.md"),
    "G2": (
        "02-specification/requirements.yaml",
        "02-specification/eval-design.md",
        "02-specification/inherited-controls.yaml",
        "02-specification/threat-model.yaml",
        "02-specification/contracts",
    ),
    "G3": ("03-planning/master-plan.md",),
    "G4": ("04-build",),
    "G5": ("05-verification/convergence-report.md", "06-close/completion-report.md"),
}


class GatePacketService:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def build(self, intent_root: Path, gate: str) -> dict[str, Any]:
        if gate not in GATE_QUESTIONS:
            raise ValueError(f"unknown gate: {gate}")
        intent_root = intent_root.resolve()
        intent = yaml.safe_load((intent_root / "00-intent" / "intent.yaml").read_text(encoding="utf-8"))
        intent_id = intent["id"]
        state = StateStore(self.repository_root).read()
        gate_record = state.get("gates", {}).get(f"{intent_id}:{gate}", {})
        return {
            "intent_id": intent_id,
            "title": intent.get("title"),
            "gate": gate,
            "question": GATE_QUESTIONS[gate],
            "generated_at": datetime.now(UTC).isoformat(),
            "intent_status": state.get("intents", {}).get(intent_id, {}).get("status"),
            "gate_status": gate_record.get("status", "undecided"),
            "revision": gate_record.get("revision", 0),
            "previous_approver": gate_record.get("approver"),
            "artifacts": self._artifacts(intent_root, gate),
            "changes_since_last_presentation": self._changes(intent_root, intent_id, gate, gate_record),
            "inherited_controls": self._inherited(intent_root),
            "outstanding": self._outstanding(intent_root, intent_id, gate, state),
            "evidence": self._evidence(intent_root),
            "open_findings": self._findings(intent_root),
            # F3: whether an adversarial reviewer read this artifact before the human was
            # asked to approve it, and whether that review still covers what is on the
            # page. A packet that omits this invites approval of an unreviewed spec.
            "adversarial_review": PreGateReviewService(self.repository_root).status(intent_root, intent_id, gate),
            # Wave S1: requirement quality, so the reviewer judges substance rather than
            # hunting for vague words and unfilled placeholders by eye.
            "specification_quality": self._specification_quality(intent_root) if gate == "G2" else None,
            # S2.3: requirement-level delta against what G2 approved, and its evidence cost.
            "specification_diff": self._specification_diff(intent_root, intent_id) if gate == "G2" else None,
            "cost": self._cost(intent_id),
        }

    def _specification_diff(self, intent_root: Path, intent_id: str) -> dict[str, Any] | None:
        from .specdiff import diff

        try:
            return diff(self.repository_root, intent_root, intent_id)
        except (OSError, ValueError, yaml.YAMLError) as error:
            return {"error": str(error)}

    def _specification_quality(self, intent_root: Path) -> dict[str, Any] | None:
        from .config import ConfigError, load_config
        from .specquality import lint_intent

        try:
            fail_on = load_config(self.repository_root).specification.fail_on
        except ConfigError:
            fail_on = "error"
        try:
            return lint_intent(intent_root, fail_on)
        except (OSError, ValueError, yaml.YAMLError) as error:
            return {"passed": False, "error": str(error), "blocking": [], "findings": []}

    def _artifacts(self, intent_root: Path, gate: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for relative in GATE_ARTIFACTS.get(gate, ()):
            path = intent_root / relative
            entries.append(
                {
                    "path": relative,
                    "present": path.exists(),
                    "bytes": path.stat().st_size if path.is_file() else None,
                }
            )
        return entries

    def _changes(self, intent_root: Path, intent_id: str, gate: str, gate_record: dict[str, Any]) -> dict[str, Any]:
        """What moved since this gate was last decided.

        A re-presented gate is the expensive case: the reviewer has already read the
        artifacts once and needs only the delta. Without it they re-read everything, which
        is the cost that makes people avoid reopening gates and let specifications drift.
        """
        if gate_record.get("status") not in {"approved", "reopened"}:
            return {"first_presentation": True, "diff": None}
        marker = self.repository_root / ".ai-sdlc" / "runtime" / "gate-packets" / f"{intent_id}-{gate}.commit"
        if not marker.exists():
            return {"first_presentation": False, "diff": None, "note": "no recorded commit for the last presentation"}
        previous = marker.read_text(encoding="utf-8").strip()
        result = subprocess.run(
            ["git", "diff", "--stat", previous, "--", str(intent_root)],
            cwd=self.repository_root,
            check=False,
            text=True,
            capture_output=True,
        )
        return {
            "first_presentation": False,
            "since_commit": previous,
            "diff": result.stdout.strip() if result.returncode == 0 else None,
            "reopened_reason": gate_record.get("reason"),
        }

    def record_presentation(self, intent_id: str, gate: str) -> None:
        """Pin the commit a gate was presented at, so the next packet can diff against it."""
        from .evidence import git_commit
        from .storage import atomic_write_text

        marker = self.repository_root / ".ai-sdlc" / "runtime" / "gate-packets" / f"{intent_id}-{gate}.commit"
        atomic_write_text(marker, git_commit(self.repository_root) + "\n")

    @staticmethod
    def _inherited(intent_root: Path) -> dict[str, Any]:
        path = intent_root / "02-specification" / "inherited-controls.yaml"
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _outstanding(self, intent_root: Path, intent_id: str, gate: str, state: dict[str, Any]) -> list[str]:
        """Everything the runner already knows is incomplete for this gate.

        The point is that the reviewer should never be the one to discover a missing
        artifact or an unverified Task. Anything deterministic belongs here, so the human
        decision is about judgement rather than bookkeeping.
        """
        from .evidence import holdout_coverage_errors
        from .validation import validate_intent

        outstanding: list[str] = []
        if gate in {"G2", "G3"}:
            report = validate_intent(intent_root, require_plan=gate == "G3")
            outstanding.extend(report.errors)
            # S3.3: concurrent change to one contract operation is W3.3 contention, decided
            # by a human — serialise or `coapprove-resource` — never discovered at merge.
            from .contractregistry import ContractRegistry

            for item in ContractRegistry(self.repository_root).contention(intent_id):
                outstanding.append(
                    f"{item['resource']} is also covered by {item['intent']} ({item['status']}); serialise the "
                    "Intents or record `coapprove-resource`"
                )
        if gate == "G2":
            outstanding.extend(holdout_coverage_errors(self.repository_root, intent_root))
            quality = self._specification_quality(intent_root)
            outstanding.extend(
                f"{item['rule']} {item['location']}: {item['message']}" for item in (quality or {}).get("blocking", [])
            )
        if gate in {"G4", "G5"}:
            for task_path in sorted(intent_root.rglob("task.yaml")):
                task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
                status = state.get("tasks", {}).get(task["id"], {}).get("status", "unknown")
                if status not in {"verified", "merged"}:
                    outstanding.append(f"{task['id']} is {status}")
        return outstanding

    @staticmethod
    def _evidence(intent_root: Path) -> dict[str, Any]:
        attempts = 0
        gaps: list[str] = []
        for manifest_path in sorted(intent_root.rglob("evidence/attempt-*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            attempts += 1
            if (manifest.get("conformance") or {}).get("conformance_gap"):
                gaps.append(str(manifest.get("task_id")))
        return {
            "attempts": attempts,
            "conformance_gaps": sorted(set(gaps)),
        }

    @staticmethod
    def _findings(intent_root: Path) -> list[dict[str, Any]]:
        path = intent_root / "05-verification" / "findings.yaml"
        if not path.exists():
            return []
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        records = payload if isinstance(payload, list) else payload.get("findings", [])
        return [
            {"id": item.get("id"), "severity": item.get("severity"), "summary": item.get("summary")}
            for item in records
            if isinstance(item, dict) and item.get("status") == "open"
        ]

    def _cost(self, intent_id: str) -> dict[str, Any]:
        from .costs import CostLedger

        summary = CostLedger(self.repository_root).summary(intent_id=intent_id)
        return {
            "verification_tax": summary["verification_tax"],
            "verification_seconds": summary["verification_seconds"],
            "implementation_seconds": summary["implementation_seconds"],
        }

    @staticmethod
    def render(packet: dict[str, Any]) -> str:
        """Render the packet as the Markdown a reviewer actually reads."""
        lines = [
            f"# Gate {packet['gate']} packet — {packet['intent_id']}: {packet['title']}",
            "",
            f"**Decision required:** {packet['question']}",
            "",
            f"- Intent status: `{packet['intent_status']}`",
            f"- Gate status: `{packet['gate_status']}` (revision {packet['revision']})",
            f"- Generated: `{packet['generated_at']}`",
            "",
        ]
        changes = packet["changes_since_last_presentation"]
        lines.append("## What changed since the last presentation")
        lines.append("")
        if changes.get("first_presentation"):
            lines.append("First presentation of this gate — review the artifacts below in full.")
        elif changes.get("diff"):
            if changes.get("reopened_reason"):
                lines.append(f"Reopened because: {changes['reopened_reason']}")
                lines.append("")
            lines.append("```")
            lines.append(changes["diff"])
            lines.append("```")
        else:
            lines.append(changes.get("note", "No recorded change."))
        lines.extend(["", "## Artifacts under review", ""])
        for artifact in packet["artifacts"]:
            mark = "x" if artifact["present"] else " "
            lines.append(f"- [{mark}] `{artifact['path']}`")
        outstanding = packet["outstanding"]
        lines.extend(["", "## Outstanding checks", ""])
        lines.extend(
            [f"- {item}" for item in outstanding] if outstanding else ["- None. Every deterministic check passes."]
        )
        evidence = packet["evidence"]
        lines.extend(
            [
                "",
                "## Evidence",
                "",
                f"- Attempts captured: {evidence['attempts']}",
            ]
        )
        if evidence["conformance_gaps"]:
            lines.append(
                f"- **Specification-conformance gaps:** {', '.join(evidence['conformance_gaps'])} "
                "(visible suite passes, held-out suite does not)"
            )
        inherited = packet.get("inherited_controls") or {}
        if inherited:
            lines.extend(["", "## Inherited controls", ""])
            for suite in inherited.get("standing_suites", []):
                lines.append(f"- standing suite `{suite}`")
            for lesson in inherited.get("lessons", []):
                lines.append(
                    f"- lesson `{lesson.get('lesson_id')}` -> {lesson.get('type')} ({lesson.get('enforcement')})"
                )
        review = packet.get("adversarial_review") or {}
        if review.get("applicable"):
            lines.extend(["", "## Adversarial review", ""])
            if not review.get("recorded"):
                lines.append(
                    "- **No adversarial review recorded.** Nobody has read this artifact against it. "
                    "Delegate to `aisdlc-red-team` from a fresh context before deciding."
                )
            else:
                lines.append(
                    f"- Reviewer `{review.get('reviewer_agent_id')}` "
                    f"(model `{review.get('reviewer_model')}`) returned **{review.get('decision')}** "
                    f"with {review.get('findings')} finding(s)."
                )
                if not review.get("current"):
                    lines.append(
                        f"- **This review is stale:** {review.get('stale_reason')}. "
                        "It does not cover what you are being asked to approve."
                    )
        change = packet.get("specification_diff")
        if change:
            lines.extend(["", "## Specification changes since Gate 2 was approved", ""])
            if change.get("error"):
                lines.append(f"- **Diff could not be computed:** {change['error']}")
            else:
                lines.append(
                    f"- Baseline: revision {change.get('baseline_revision')}, {change.get('baseline_captured_at')}"
                )
                for label, key in (
                    ("Requirements", "requirements"),
                    ("Criteria", "criteria"),
                    ("Other artifacts", "other_artifacts"),
                ):
                    moved = change.get(key) or {}
                    parts = [
                        f"{kind} {', '.join(f'`{item}`' for item in moved.get(kind, []))}"
                        for kind in ("added", "changed", "removed")
                        if moved.get(kind)
                    ]
                    lines.append(f"- {label}: {'; '.join(parts) if parts else 'unchanged'}")
                for item in change.get("reprioritised") or []:
                    lines.append(f"- `{item['requirement']}` reprioritised {item['from']} -> {item['to']}")
                cost = change.get("tasks_losing_evidence") or []
                lines.append(
                    "- **Evidence invalidated on approval:** "
                    + (", ".join(f"`{item['task']}` ({', '.join(item['requirements'])})" for item in cost) or "none")
                )
        quality = packet.get("specification_quality")
        if quality:
            counts = quality.get("counts") or {}
            lines.extend(["", "## Specification quality", ""])
            if quality.get("error"):
                lines.append(f"- **Lint could not run:** {quality['error']}")
            else:
                lines.append(
                    f"- {'Passes' if quality.get('passed') else '**Blocked**'} at `{quality.get('fail_on')}`: "
                    f"{counts.get('error', 0)} error(s), {counts.get('warning', 0)} warning(s), "
                    f"{counts.get('info', 0)} note(s). "
                    f"{quality.get('prioritised', 0)} of {quality.get('requirements', 0)} requirements prioritised."
                )
                coverage = quality.get("quality_coverage") or {}
                lines.append(
                    "- Quality attributes addressed: "
                    + (", ".join(f"{name} ({', '.join(ids)})" for name, ids in coverage.items()) or "**none**")
                )
                for item in quality.get("findings") or []:
                    if item.get("severity") != "info":
                        lines.append(f"- `{item['rule']}` {item['location']}: {item['message']}")
        findings = packet.get("open_findings") or []
        if findings:
            lines.extend(["", "## Open findings", ""])
            for finding in findings:
                lines.append(f"- `{finding['id']}` ({finding['severity']}): {finding.get('summary', '')}")
        cost = packet.get("cost") or {}
        lines.extend(
            [
                "",
                "## Cost so far",
                "",
                f"- Verification tax: {cost.get('verification_tax')}",
                f"- Verification time: {cost.get('verification_seconds')}s",
                f"- Implementation time: {cost.get('implementation_seconds')}s",
                "",
            ]
        )
        return "\n".join(lines)
