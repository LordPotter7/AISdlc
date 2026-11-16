from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .migrate import assert_readable, stamp
from .storage import FileMutex, atomic_write_json, atomic_write_text, read_json


class TransitionError(ValueError):
    pass


class AuditCorruptionError(TransitionError):
    pass


INTENT_TRANSITIONS = {
    "initialized": {"framed", "aborted"},
    "framed": {"specified", "aborted"},
    "specified": {"planned", "framed", "aborted"},
    "planned": {"building", "specified", "aborted"},
    "building": {"converging", "planned", "aborted"},
    "converging": {"awaiting_acceptance", "building", "aborted"},
    "awaiting_acceptance": {"completed", "converging", "aborted"},
    "completed": set(),
    "aborted": set(),
}

# Lifecycle order of Intent states; a transition to an earlier state is a retreat.
STAGE_ORDER = (
    "initialized",
    "framed",
    "specified",
    "planned",
    "building",
    "converging",
    "awaiting_acceptance",
    "completed",
)

TASK_TRANSITIONS = {
    "pending": {"ready", "superseded"},
    "ready": {"in_progress", "superseded"},
    "in_progress": {"verifying", "blocked"},
    "verifying": {"review", "retry"},
    "review": {"verified", "retry"},
    "retry": {"in_progress", "blocked"},
    "blocked": {"in_progress", "failed"},
    "verified": {"merged"},
    "merged": {"reverted"},
    "reverted": set(),
    "superseded": set(),
    "failed": set(),
}


class StateStore:
    def __init__(self, repository_root: Path) -> None:
        self.runtime = repository_root / ".ai-sdlc" / "runtime"
        self.state_path = self.runtime / "state.json"
        self.audit_path = self.runtime / "audit.jsonl"
        self.lock_path = self.runtime / "state.lock"

    def initialize_intent(self, intent_id: str) -> None:
        self._initialize("intents", intent_id, "initialized")

    def initialize_task(self, task_id: str) -> None:
        self._initialize("tasks", task_id, "pending")

    def transition_intent(self, intent_id: str, target: str, actor: str) -> None:
        self._transition("intents", intent_id, target, actor, INTENT_TRANSITIONS)

    def transition_task(self, task_id: str, target: str, actor: str, **details: Any) -> None:
        self._transition("tasks", task_id, target, actor, TASK_TRANSITIONS, details=details or None)

    def _assert_approver_permitted(self, gate: str, approver: str) -> list[str]:
        """Check the approver against the gate's declared approver policy (W3.3).

        In a small team, whoever is present approves the gate and that is fine. In a large
        organisation it is how gate-shopping happens: the decision goes to whoever will say
        yes. Recording *who may approve* alongside who did is both the control against that
        and the accountability record an ISO 42001 or EU AI Act auditor asks for.

        A gate with no declared approvers keeps today's behaviour exactly, so this is
        opt-in per repository rather than a new obligation on every existing one.
        """
        try:
            from .config import load_config

            settings = load_config(self.runtime.parent.parent).gates
        except Exception:  # noqa: BLE001 - an unreadable config must not block a gate here
            return []
        permitted = settings.approvers.get(gate, [])
        if not permitted:
            if settings.require_declared_approver:
                raise TransitionError(
                    f"{gate} has no declared approvers and `gates.require_declared_approver` is enabled"
                )
            return []
        if approver not in permitted:
            raise TransitionError(
                f"{approver!r} may not approve {gate}; declared approvers are: {', '.join(sorted(permitted))}"
            )
        return permitted

    def _identity_record(self, approver: str) -> dict[str, str]:
        """Establish the binding strength of this decision (R4.3).

        Resolved here rather than in the CLI so every path to a gate approval — command,
        orchestration, or a future caller — records the same thing. A repository that has
        not enabled `gates.require_verified` still gets the field, marked `declared`,
        because "we did not check" and "we checked and it was weak" must not look alike in
        an accountability record.
        """
        from .config import load_config
        from .identity import IdentityError, resolve

        repository_root = self.runtime.parent.parent
        try:
            require_verified = load_config(repository_root).gates.require_verified
        except Exception:  # noqa: BLE001 - an unreadable config must not block a gate
            require_verified = False
        try:
            return resolve(approver, repository_root, require_verified=require_verified).as_record()
        except IdentityError as error:
            raise TransitionError(str(error)) from error

    def _assert_pre_gate_reviewed(self, intent_id: str, gate: str) -> None:
        """Refuse Gate 2 or Gate 3 without a current adversarial review (F3).

        Enforced at the transition rather than in a command so that every route to an
        approval — CLI, orchestration, or anything later — meets the same check. Locating
        the Intent is best-effort: a repository whose delivery tree cannot be resolved is
        not a repository where this control can meaningfully run, and failing there would
        block gates for a reason unrelated to review.
        """
        from .pregate import GATE_SUBJECTS, PreGateReviewError, PreGateReviewService

        if gate not in GATE_SUBJECTS:
            return
        repository_root = self.runtime.parent.parent
        try:
            from .config import delivery_root

            matches = list(delivery_root(repository_root).glob(f"{intent_id}-*"))
        except Exception:  # noqa: BLE001 - unresolvable delivery tree, nothing to review
            return
        if len(matches) != 1:
            return
        try:
            PreGateReviewService(repository_root).require(matches[0], intent_id, gate)
        except PreGateReviewError as error:
            raise TransitionError(str(error)) from error

    def _approvals_so_far(self, gate_ids: list[str]) -> list[str]:
        """Who has approved these gates since each last changed state (ADR-010).

        Read from the audit log, the one chronology. A partial approval moves no state, so
        it is an event, not a field: `reconstruct` ignores it by design, and a decision or
        a reopening (any event with a `to`) starts the count again.
        """
        if not self.audit_path.exists():
            return []
        approvers: list[str] = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("entity_id") not in gate_ids:
                continue
            if event.get("to") is not None:
                approvers = []
            elif event.get("event") == "gate_approval":
                approvers.append(str(event.get("actor")))
        return approvers

    def _awaiting_more(self, gate_ids: list[str], approver: str, needed: int) -> dict[str, Any] | None:
        """Record one approval of a gate that needs several different people, if it is not the last."""
        if needed <= 1:
            return None
        prior = self._approvals_so_far(gate_ids)
        if approver in prior:
            raise TransitionError(
                f"{approver} has already approved {', '.join(gate_ids)}; it needs {needed} different people"
            )
        if len(prior) + 1 >= needed:
            return None
        approvers = [*prior, approver]
        self.record_event(
            "gate",
            gate_ids[0],
            "gate_approval",
            actor=approver,
            details={"gates": gate_ids, "approvals": len(approvers), "needed": needed, "approvers": approvers},
        )
        return {"status": "awaiting_approvals", "approvals": approvers, "needed": needed}

    def _assert_policy_approver(self, gate: str, approver: str, policy: dict[str, Any] | None) -> list[str]:
        """The pinned profile's approver list when it names one, else the repository's."""
        named = sorted((policy or {}).get("approvers") or [])
        if not named:
            return self._assert_approver_permitted(gate, approver)
        if approver not in named:
            raise TransitionError(
                f"{approver!r} may not approve {gate}; this Intent's profile names: {', '.join(named)}"
            )
        return named

    def approve_gate(
        self, intent_id: str, gate: str, approver: str, policy: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Approve a gate, or record one approval of a gate that needs several people.

        `policy` is the gate's rule from the Intent's pinned pipeline profile (ADR-010):
        how many different people must approve, and who may.
        """
        if gate not in {"G1", "G2", "G3", "G4", "G5"}:
            raise TransitionError("gate must be G1 through G5")
        permitted = self._assert_policy_approver(gate, approver, policy)
        identity = self._identity_record(approver)
        self._assert_pre_gate_reviewed(intent_id, gate)
        needed = int((policy or {}).get("approvals") or 1)
        gate_id = f"{intent_id}:{gate}"
        if needed > 1:
            self._assert_gate_ready(intent_id, gate)
            partial = self._awaiting_more([gate_id], approver, needed)
            if partial:
                return partial
        approvers = [*self._approvals_so_far([gate_id]), approver] if needed > 1 else [approver]
        with FileMutex(self.lock_path):
            state = self.read()
            if intent_id not in state["intents"]:
                raise TransitionError(f"unknown Intent: {intent_id}")
            required_status = {
                "G1": "framed",
                "G2": "specified",
                "G3": "planned",
                "G4": "building",
                "G5": "awaiting_acceptance",
            }[gate]
            current = state["intents"][intent_id]["status"]
            if current != required_status:
                raise TransitionError(f"{gate} requires Intent state {required_status}, current state is {current}")
            gate_id = f"{intent_id}:{gate}"
            if state["gates"].get(gate_id, {}).get("status") == "approved":
                raise TransitionError(f"gate already approved: {gate_id}")
            source = state["gates"].get(gate_id, {}).get("status")
            revision = int(state["gates"].get(gate_id, {}).get("revision", 0)) + 1
            record: dict[str, Any] = {"status": "approved", "revision": revision, **identity}
            if permitted:
                # Record the policy in force at decision time, not just the decision. A
                # later change to the approver list must not silently rewrite the history
                # of who was entitled to approve what.
                record["permitted_approvers"] = sorted(permitted)
            if len(approvers) > 1:
                record["approvers"] = approvers
            state["gates"][gate_id] = record
            details: dict[str, Any] = {}
            if permitted:
                details["permitted_approvers"] = sorted(permitted)
            if len(approvers) > 1:
                details["approvers"] = approvers
            self._persist(state, "gate", gate_id, source, "approved", approver, details=details or None)
        return {"status": "approved", "approvals": approvers, "needed": needed}

    def _assert_gate_ready(self, intent_id: str, gate: str) -> None:
        """The state a gate is decided in, checked before a partial approval is recorded."""
        required = {"G1": "framed", "G2": "specified", "G3": "planned", "G4": "building", "G5": "awaiting_acceptance"}
        state = self.read()
        current = state.get("intents", {}).get(intent_id, {}).get("status")
        if intent_id not in state.get("intents", {}):
            raise TransitionError(f"unknown Intent: {intent_id}")
        if current != required[gate]:
            raise TransitionError(f"{gate} requires Intent state {required[gate]}, current state is {current}")
        if state["gates"].get(f"{intent_id}:{gate}", {}).get("status") == "approved":
            raise TransitionError(f"gate already approved: {intent_id}:{gate}")

    def approve_compact_gates(
        self, intent_id: str, approver: str, policy: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._assert_policy_approver("G2", approver, policy)
        identity = self._identity_record(approver)
        # A compact Intent combines G2 and G3 into one decision, so it inherits both
        # reviews. Compactness is about artifact count, never about rigor.
        for gate in ("G2", "G3"):
            self._assert_pre_gate_reviewed(intent_id, gate)
        needed = int((policy or {}).get("approvals") or 1)
        gate_ids = [f"{intent_id}:G2", f"{intent_id}:G3"]
        if needed > 1:
            self._assert_gate_ready(intent_id, "G2")
            partial = self._awaiting_more(gate_ids, approver, needed)
            if partial:
                return partial
        approvers = [*self._approvals_so_far(gate_ids), approver] if needed > 1 else [approver]
        with FileMutex(self.lock_path):
            state = self.read()
            current = state.get("intents", {}).get(intent_id, {}).get("status")
            if current != "specified":
                raise TransitionError(f"combined G2/G3 requires Intent state specified, current state is {current}")
            gate_ids = [f"{intent_id}:G2", f"{intent_id}:G3"]
            if any(state["gates"].get(gate_id, {}).get("status") == "approved" for gate_id in gate_ids):
                raise TransitionError("combined G2/G3 requires both gates to be undecided")
            for gate_id in gate_ids:
                state["gates"][gate_id] = {
                    "status": "approved",
                    "revision": 1,
                    **identity,
                    **({"approvers": approvers} if len(approvers) > 1 else {}),
                }
            for gate_id in gate_ids:
                self._persist(
                    state,
                    "gate",
                    gate_id,
                    None,
                    "approved",
                    approver,
                    details={"approvers": approvers} if len(approvers) > 1 else None,
                )
        return {"status": "approved", "approvals": approvers, "needed": needed}

    def reopen_gate(self, intent_id: str, gate: str, actor: str, reason: str) -> None:
        if not reason.strip():
            raise TransitionError("reopening a gate requires a reason")
        with FileMutex(self.lock_path):
            state = self.read()
            gate_id = f"{intent_id}:{gate}"
            record = state["gates"].get(gate_id)
            if not record or record.get("status") != "approved":
                raise TransitionError(f"gate is not approved: {gate_id}")
            record.update({"status": "reopened", "reopened_by": actor, "reason": reason})
            self._persist(
                state,
                "gate",
                gate_id,
                "approved",
                "reopened",
                actor,
                details={"reopened_by": actor, "reason": reason},
            )

    def record_event(
        self,
        entity_type: str,
        entity_id: str,
        event: str,
        *,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit event that records a judgment without moving any state machine.

        Some decisions change nothing and still need to be on the record — declining
        discovery, for instance, or completing a phase retrospective. Routing them through
        the audit log rather than inventing a parallel place to write them keeps one
        chronology of what people decided, and keeps `reconstruct` able to ignore what it
        does not recognise: these events carry no `from`/`to`, so state rebuilt from the
        log is unaffected by them.
        """
        with FileMutex(self.lock_path):
            self.runtime.mkdir(parents=True, exist_ok=True)
            sequence = (
                sum(1 for _ in self.audit_path.open("r", encoding="utf-8")) + 1 if self.audit_path.exists() else 1
            )
            record = {
                "sequence": sequence,
                "at": datetime.now(UTC).isoformat(),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "event": event,
                "actor": actor,
            }
            if details:
                record["details"] = details
            with self.audit_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

    def read(self) -> dict[str, Any]:
        state = read_json(self.state_path, {"intents": {}, "tasks": {}, "gates": {}})
        # R1.2: refuse a state file from a newer schema generation rather than reading it
        # partially. Every other consumer in the runner goes through here, so this is the
        # one place the check has to be.
        return assert_readable(state, "runtime state")

    def recover(self, *, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise TransitionError("runtime recovery requires explicit confirmation")
        with FileMutex(self.lock_path):
            reconstructed = self.reconstruct(self.audit_path)
            if self.state_path.exists():
                captured_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                backup = self.state_path.with_name(f"state.corrupt-{captured_at}.json")
                atomic_write_text(backup, self.state_path.read_text(encoding="utf-8"))
            atomic_write_json(self.state_path, stamp(reconstructed))
            return reconstructed

    def _initialize(self, collection: str, entity_id: str, status: str) -> None:
        with FileMutex(self.lock_path):
            state = self.read()
            if entity_id in state[collection]:
                raise TransitionError(f"state already exists for {entity_id}")
            state[collection][entity_id] = {"status": status, "attempt": 0}
            self._persist(state, collection[:-1], entity_id, None, status, "runner")

    def _transition(
        self,
        collection: str,
        entity_id: str,
        target: str,
        actor: str,
        transitions: dict[str, set[str]],
        details: dict[str, Any] | None = None,
    ) -> None:
        with FileMutex(self.lock_path):
            state = self.read()
            if entity_id not in state[collection]:
                raise TransitionError(f"unknown entity: {entity_id}")
            current = state[collection][entity_id]["status"]
            if target not in transitions.get(current, set()):
                raise TransitionError(f"illegal transition {current} -> {target} for {entity_id}")
            state[collection][entity_id]["status"] = target
            if target == "retry":
                state[collection][entity_id]["attempt"] += 1
            if details:
                state[collection][entity_id].update(details)
            self._persist(state, collection[:-1], entity_id, current, target, actor, details=details)

    def _persist(
        self,
        state: dict[str, Any],
        entity_type: str,
        entity_id: str,
        source: str | None,
        target: str,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        atomic_write_json(self.state_path, stamp(state))
        self.runtime.mkdir(parents=True, exist_ok=True)
        sequence = sum(1 for _ in self.audit_path.open("r", encoding="utf-8")) + 1 if self.audit_path.exists() else 1
        event = {
            "sequence": sequence,
            "at": datetime.now(UTC).isoformat(),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "from": source,
            "to": target,
            "actor": actor,
        }
        if details:
            event["details"] = details
        with self.audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    @staticmethod
    def reconstruct(audit_path: Path) -> dict[str, Any]:
        state: dict[str, Any] = {"intents": {}, "tasks": {}, "gates": {}}
        if not audit_path.exists():
            return state
        for expected_sequence, line in enumerate(audit_path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise AuditCorruptionError(f"invalid audit JSON at sequence {expected_sequence}") from error
            if event.get("sequence") != expected_sequence:
                raise AuditCorruptionError(
                    f"audit sequence mismatch: expected {expected_sequence}, got {event.get('sequence')}"
                )
            if "to" not in event:
                # A recorded judgment that moved no state machine — a declined discovery,
                # a phase retrospective. It occupies a sequence number so the chronology
                # stays contiguous, and contributes nothing to reconstructed state. Its
                # sequence was already checked above, which is the property recovery needs.
                continue
            collection = f"{event['entity_type']}s"
            if collection not in state:
                raise AuditCorruptionError(f"unknown audit entity type: {event.get('entity_type')}")
            record = state[collection].setdefault(event["entity_id"], {"status": event["to"], "attempt": 0})
            record["status"] = event["to"]
            if event["to"] == "retry":
                record["attempt"] += 1
            if event["entity_type"] == "gate":
                record["approver"] = event["actor"]
                record.update(event.get("details", {}))
                if event["to"] == "approved":
                    record["revision"] = int(record.get("revision", 0)) + 1
            else:
                record.update(event.get("details", {}))
        return state
