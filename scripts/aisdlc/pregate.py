"""Adversarial review of a specification or a plan, before the gate that would approve it.

The framework already owns an excellent read-only adversarial agent and pointed it at
exactly one place: a Task, after the code exists. That is the most expensive moment to
find a specification defect. Caught at Gate 2 it costs a document rewrite; caught at Task
review it costs the implementation, the captured evidence, and a slice of a two-retry
budget — and if it survives review it becomes a conformance finding that blocks close.

So the same instrument runs earlier, over the artifact rather than the diff. Three
properties carry over unchanged from Task review, because they are what make a review
worth recording at all:

*Independence is a property of the model.* A reviewer sharing the author's model family
reviews its own reasoning, whatever the agent is called.

*The review is pinned to what it reviewed.* An artifact hash means a specification edited
after its review shows up as a review that no longer covers the artifact, rather than as
an approval that silently still counts.

*It is recorded, not narrated.* Prose from the reviewing agent is not a review; a
schema-valid record written by the runner is.

What does not carry over is the merge-blocking consequence. A pre-gate review informs a
human decision — it does not make it. `changes_required` from a reviewer and `approved`
from a person are both legitimate outcomes, and the gate packet shows the human both.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .assets import schema_path
from .config import load_config, model_family
from .jsonschema_mini import Draft202012Validator
from .migrate import stamp
from .storage import atomic_write_json

# What each gate's review is actually looking at. Gate 2 approves the specification; Gate 3
# approves the plan and the architecture behind it.
GATE_SUBJECTS: dict[str, tuple[str, ...]] = {
    "G2": ("02-specification",),
    "G3": ("03-planning",),
}


class PreGateReviewError(ValueError):
    """Raised when a pre-gate review is malformed, dependent, or missing when required."""


def subject_hash(intent_root: Path, gate: str) -> str:
    """Hash everything the gate is being asked to approve.

    Directory-wide rather than per-file: the reviewer read the specification as a whole,
    and a claim that the review covers it is a claim about all of it. This is the coarse
    counterpart to the per-requirement and per-decision hashes used for evidence scoping,
    and coarse is right here — a review is an opinion about a document, not a pin for a
    command's exit code.
    """
    digest = hashlib.sha256()
    for relative in GATE_SUBJECTS.get(gate, ()):
        root = intent_root / relative
        if not root.is_dir():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


class PreGateReviewService:
    """Record and read adversarial reviews attached to Gate 2 and Gate 3."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def _path(self, intent_id: str, gate: str) -> Path:
        return self.repository_root / ".ai-sdlc" / "runtime" / "reviews" / f"{intent_id}-{gate}.json"

    def record(self, intent_root: Path, review: dict[str, Any]) -> dict[str, Any]:
        gate = str(review.get("gate", ""))
        if gate not in GATE_SUBJECTS:
            raise PreGateReviewError(f"pre-gate review applies to G2 and G3, not {gate!r}")
        record = dict(review)
        record.setdefault("recorded_at", datetime.now(UTC).isoformat())
        record["artifact_hash"] = subject_hash(intent_root, gate)
        self._enforce_independence(record)
        stamp(record)
        self._validate(record)
        atomic_write_json(self._path(str(record["intent_id"]), gate), record)
        return record

    def read(self, intent_id: str, gate: str) -> dict[str, Any] | None:
        path = self._path(intent_id, gate)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def status(self, intent_root: Path, intent_id: str, gate: str) -> dict[str, Any]:
        """What the human at this gate needs to know about its adversarial review.

        Three states worth distinguishing, because they call for different things:
        no review at all, a review of an artifact that has since changed, and a current
        review with an opinion in it.
        """
        if gate not in GATE_SUBJECTS:
            return {"applicable": False}
        review = self.read(intent_id, gate)
        if review is None:
            return {"applicable": True, "recorded": False, "current": False, "decision": None, "findings": 0}
        current = review.get("artifact_hash") == subject_hash(intent_root, gate)
        return {
            "applicable": True,
            "recorded": True,
            "current": current,
            "decision": review.get("decision"),
            "reviewer_agent_id": review.get("reviewer_agent_id"),
            "reviewer_model": review.get("reviewer_model"),
            "findings": len(review.get("findings", [])),
            "recorded_at": review.get("recorded_at"),
            "stale_reason": None if current else "the artifact changed after this review was recorded",
        }

    def require(self, intent_root: Path, intent_id: str, gate: str) -> None:
        """Refuse a gate whose adversarial review is missing or no longer covers the artifact.

        Called from the gate transition rather than from a command, so every path to an
        approval meets the same check. Silent when the repository has not enabled it, and
        silent for gates it does not apply to.
        """
        if gate not in GATE_SUBJECTS:
            return
        try:
            required = load_config(self.repository_root).review.require_pre_gate_review
        except Exception:  # noqa: BLE001 - an unreadable config must not block a gate here
            return
        if not required:
            return
        state = self.status(intent_root, intent_id, gate)
        if not state["recorded"]:
            raise PreGateReviewError(
                f"{gate} requires a recorded adversarial review of the artifact it approves. "
                f"Delegate to `aisdlc-red-team` from a fresh context, then "
                f"`ai-sdlc pre-gate-review {intent_id} {gate} --from <review.json>`."
            )
        if not state["current"]:
            raise PreGateReviewError(
                f"the recorded {gate} review covers an earlier version of the artifact; "
                "re-run the adversarial review against the current one."
            )

    def _enforce_independence(self, review: dict[str, Any]) -> None:
        """A reviewer sharing the author's model family is not an independent reviewer.

        Identical in intent to the Task-level check, and fails closed the same way: an
        unrecorded reviewer model is invalid rather than accepted as a legacy-shaped
        review, so enabling this control cannot silently admit self-review.
        """
        settings = load_config(self.repository_root).review
        reviewer_model = review.get("reviewer_model") or settings.reviewer_model
        if not reviewer_model:
            if settings.require_reviewer_model:
                raise PreGateReviewError(
                    "a pre-gate review must record `reviewer_model`; configure `review.reviewer_model` "
                    "or set `review.require_reviewer_model: false` to accept unattributed reviews"
                )
            return
        review["reviewer_model"] = reviewer_model
        author_model = review.get("author_model")
        same_family = author_model and model_family(reviewer_model) == model_family(author_model)
        if settings.forbid_same_family and same_family:
            raise PreGateReviewError(
                f"reviewer model {reviewer_model!r} shares a family with the authoring model "
                f"{author_model!r}; independence is a property of the model, not the agent label"
            )

    def _validate(self, record: dict[str, Any]) -> None:
        schema = json.loads(
            schema_path(self.repository_root, "pre-gate-review.schema.json").read_text(encoding="utf-8")
        )
        errors = sorted(Draft202012Validator(schema).iter_errors(record), key=lambda item: list(item.path))
        if errors:
            raise PreGateReviewError(
                "pre-gate review does not satisfy pre-gate-review.schema.json: "
                + "; ".join(
                    f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}" for error in errors
                )
            )
