"""Phase-completion retrospective: promote what a phase taught before the next one repeats it.

Lessons are collected and promoted at close, which is the right place for the Intent's
final account of itself and the wrong place for everything else. On a compact Intent the
difference is nil. On a full Intent with five Build Phases, a lesson learned in `P01` is
not promoted until the Intent ends — so `P02` through `P05` are free to repeat it, and the
learning loop's own latency becomes the one thing it fails to measure.

So the same station runs at each phase boundary. This adds no artifact anyone maintains
(tenet T5): it reads findings that already exist, checks them against the lessons registry
that already exists, and proposes into it. What it contributes is timing.

Two rules keep it from becoming the meeting it could easily degrade into.

*A retrospective only closes a phase whose work is actually finished.* Otherwise it becomes
a status update, held while the phase is still moving, teaching nothing.

*A proposal carries promotion targets or it is not a lesson.* An observation with nowhere
to land is a note. The registry already enforces this, and this module does not route
around it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .convergence import load_findings
from .lessons import LessonRegistry
from .state import StateStore

# Severities that must produce a lesson proposal rather than merely being noted. Mirrors
# the close-time rule that Critical and High findings block until they have a target.
BLOCKING_SEVERITIES = {"critical", "high"}

# Task states that mean the work is done rather than still moving.
SETTLED = {"verified", "merged", "superseded", "reverted", "failed"}


class RetrospectiveError(ValueError):
    """Raised when a phase cannot be retrospected yet, or its findings cannot be read."""


def _phase_of(task_id: str) -> str:
    return task_id.rsplit("-T", 1)[0]


def _finding_phase(finding: dict[str, Any]) -> str | None:
    """Which phase a finding belongs to, read from whatever names a Task in it.

    Findings reference their origin in several shapes depending on who recorded them, so
    the phase is derived from any Task identifier present rather than from a field the
    recorder had to remember to fill in.
    """
    import re

    haystack = " ".join(str(finding.get(key, "")) for key in ("source", "evidence", "description", "requirement"))
    match = re.search(r"(I\d{3,}-P\d{2,})-T\d{3,}", haystack)
    return match.group(1) if match else None


class PhaseRetrospective:
    """Assemble, and optionally act on, what one Build Phase taught."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def assess(self, intent_root: Path, phase_id: str) -> dict[str, Any]:
        """What this phase produced, and which of it has not yet become a lesson."""
        state = StateStore(self.repository_root).read()
        tasks = {
            task_id: record for task_id, record in state.get("tasks", {}).items() if _phase_of(task_id) == phase_id
        }
        unsettled = sorted(task_id for task_id, record in tasks.items() if record.get("status") not in SETTLED)

        findings = [finding for finding in load_findings(intent_root) if _finding_phase(finding) == phase_id]
        registry_lessons = LessonRegistry(self.repository_root).read_all()
        covered = {str(reference) for lesson in registry_lessons for reference in lesson.get("source_findings", [])}
        unpromoted = [
            finding
            for finding in findings
            if str(finding.get("severity", "")).lower() in BLOCKING_SEVERITIES and str(finding.get("id")) not in covered
        ]
        return {
            "phase_id": phase_id,
            "tasks": sorted(tasks),
            "unsettled_tasks": unsettled,
            "complete": bool(tasks) and not unsettled,
            "findings": [str(finding.get("id")) for finding in findings],
            "unpromoted_findings": [
                {
                    "id": str(finding.get("id")),
                    "severity": finding.get("severity"),
                    "description": finding.get("description"),
                }
                for finding in unpromoted
            ],
            "ready": bool(tasks) and not unsettled,
        }

    def record(self, intent_root: Path, intent_id: str, phase_id: str, *, actor: str) -> dict[str, Any]:
        """Close the phase's retrospective on the record.

        Refuses while the phase is still moving. A retrospective over unfinished work
        reports on a phase that has not happened yet, and recording it would put a claim
        in the audit log that the phase's own state contradicts.
        """
        assessment = self.assess(intent_root, phase_id)
        if not assessment["tasks"]:
            raise RetrospectiveError(f"no Tasks are recorded for {phase_id}")
        if assessment["unsettled_tasks"]:
            raise RetrospectiveError(
                f"{phase_id} still has work in flight: {', '.join(assessment['unsettled_tasks'])}. "
                "A retrospective over unfinished work is a status update."
            )
        StateStore(self.repository_root).record_event(
            "intent",
            intent_id,
            "phase_retrospective",
            actor=actor,
            details={
                "phase_id": phase_id,
                "findings": assessment["findings"],
                "unpromoted_findings": [item["id"] for item in assessment["unpromoted_findings"]],
            },
        )
        return {**assessment, "recorded": True}
