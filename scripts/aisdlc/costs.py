"""Cost and latency instrumentation for the lifecycle (W2.2).

The six loop metrics measure whether the loop catches defects. None of them measures what
it costs. That asymmetry is how frameworks of this shape die: the verification tax
accumulates invisibly, nobody can separate a gate that catches things from a gate that is
a ritual, and the whole apparatus is eventually abandoned wholesale rather than trimmed.

This module records one append-only ledger of lifecycle operations — what happened, how
long it took, what it cost — and derives two numbers from it:

- **verification tax**: verification cost divided by implementation cost.
- **gate latency**: the wall-clock interval from a gate becoming presentable to a human
  deciding it.

Both are reported, never enforced. They feed decay review, which is where a human decides
whether an expensive check has earned its keep. A budget that blocked automatically would
be a risk tier by another name.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .storage import FileMutex

# Operation categories. The split decides which side of the verification-tax ratio an
# entry lands on, so it is deliberately coarse and explicit rather than inferred.
IMPLEMENTATION_OPERATIONS = frozenset({"start-task", "context-pack", "orchestrate", "worktree"})
VERIFICATION_OPERATIONS = frozenset(
    {"verify-task", "verify-ci", "review-task", "eval-run", "converge", "merge-ready", "mutation", "baseline"}
)
GATE_OPERATIONS = frozenset({"gate-approve", "gate-reopen", "gate-packet"})


@dataclass(frozen=True)
class CostEntry:
    operation: str
    intent_id: str | None
    entity_id: str | None
    wall_clock_seconds: float
    tokens: int
    usd: float
    at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "intent_id": self.intent_id,
            "entity_id": self.entity_id,
            "wall_clock_seconds": round(self.wall_clock_seconds, 6),
            "tokens": self.tokens,
            "usd": round(self.usd, 6),
            "at": self.at,
        }


class CostLedger:
    """Append-only record of what each lifecycle operation cost.

    Append-only for the same reason the audit log is: a ledger that can be rewritten
    cannot support a decision to retire a gate, because the evidence for "this gate is
    expensive and never fires" would be exactly what an interested party would edit.
    """

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        runtime = self.repository_root / ".ai-sdlc" / "runtime"
        self.path = runtime / "cost-ledger.jsonl"
        self.lock_path = runtime / "cost-ledger.lock"

    def enabled(self) -> bool:
        from .config import ConfigError, load_config

        try:
            return load_config(self.repository_root).cost.enabled
        except ConfigError:
            return False

    def record(
        self,
        operation: str,
        *,
        wall_clock_seconds: float,
        intent_id: str | None = None,
        entity_id: str | None = None,
        tokens: int = 0,
        usd: float = 0.0,
    ) -> CostEntry:
        entry = CostEntry(
            operation=operation,
            intent_id=intent_id,
            entity_id=entity_id,
            wall_clock_seconds=wall_clock_seconds,
            tokens=int(tokens),
            usd=float(usd),
            at=datetime.now(UTC).isoformat(),
        )
        if not self.enabled():
            return entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileMutex(self.lock_path), self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry.as_dict(), sort_keys=True) + "\n")
        return entry

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                # A truncated final line is a crash artifact, not tampering. Skipping it
                # keeps metrics readable; the audit log remains the integrity record.
                continue
        return entries

    @contextmanager
    def measure(
        self,
        operation: str,
        *,
        intent_id: str | None = None,
        entity_id: str | None = None,
    ):
        """Time an operation and record it even when the operation raises.

        Failures are the expensive part of a verification tax — a gate that fails three
        times before passing costs four times as much as the ledger would show if only
        successes were recorded.
        """
        started = time.monotonic()
        usage: dict[str, float] = {"tokens": 0, "usd": 0.0}
        try:
            yield usage
        finally:
            self.record(
                operation,
                wall_clock_seconds=time.monotonic() - started,
                intent_id=intent_id,
                entity_id=entity_id,
                tokens=int(usage.get("tokens", 0)),
                usd=float(usage.get("usd", 0.0)),
            )

    def summary(self, *, intent_id: str | None = None) -> dict[str, Any]:
        entries = [item for item in self.read() if intent_id is None or item.get("intent_id") == intent_id]
        by_operation: dict[str, dict[str, float]] = {}
        for entry in entries:
            bucket = by_operation.setdefault(
                str(entry.get("operation")), {"count": 0, "wall_clock_seconds": 0.0, "tokens": 0, "usd": 0.0}
            )
            bucket["count"] += 1
            bucket["wall_clock_seconds"] += float(entry.get("wall_clock_seconds", 0))
            bucket["tokens"] += int(entry.get("tokens", 0))
            bucket["usd"] += float(entry.get("usd", 0))

        def total(operations: frozenset[str], field: str) -> float:
            return sum(float(values[field]) for operation, values in by_operation.items() if operation in operations)

        verification_usd = total(VERIFICATION_OPERATIONS, "usd")
        implementation_usd = total(IMPLEMENTATION_OPERATIONS, "usd")
        verification_seconds = total(VERIFICATION_OPERATIONS, "wall_clock_seconds")
        implementation_seconds = total(IMPLEMENTATION_OPERATIONS, "wall_clock_seconds")
        # Fall back to wall clock when no monetary cost was reported. A repository driving
        # the runner from a flat-rate agent still needs the ratio; time is the cost it has.
        if implementation_usd > 0:
            tax = verification_usd / implementation_usd
        elif implementation_seconds > 0:
            tax = verification_seconds / implementation_seconds
        else:
            tax = 0.0
        return {
            "intent_id": intent_id,
            "entries": len(entries),
            "by_operation": {
                operation: {
                    "count": int(values["count"]),
                    "wall_clock_seconds": round(values["wall_clock_seconds"], 3),
                    "tokens": int(values["tokens"]),
                    "usd": round(values["usd"], 6),
                }
                for operation, values in sorted(by_operation.items())
            },
            "verification_usd": round(verification_usd, 6),
            "implementation_usd": round(implementation_usd, 6),
            "verification_seconds": round(verification_seconds, 3),
            "implementation_seconds": round(implementation_seconds, 3),
            "verification_tax": round(tax, 4),
            "gate_seconds": round(total(GATE_OPERATIONS, "wall_clock_seconds"), 3),
        }


def gate_latencies(repository_root: Path) -> dict[str, float]:
    """Wall-clock seconds from an Intent reaching a gate's state to the gate being decided.

    Gate *count* is not the expensive part of this framework; gate *preparation* is. This
    measures the interval a human actually waits, which is the number `gate-packet` is
    meant to move.
    """
    from .state import StateStore

    audit_path = StateStore(repository_root).audit_path
    if not audit_path.exists():
        return {}
    events: list[dict[str, Any]] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    # The Intent state each gate waits in. A gate becomes presentable the moment its
    # Intent enters that state; it is decided when the gate event lands.
    presentable_state = {"G1": "framed", "G2": "specified", "G3": "planned", "G4": "building", "G5": "converging"}
    reached: dict[tuple[str, str], str] = {}
    latencies: dict[str, float] = {}
    for event in events:
        if event.get("entity_type") == "intent":
            for gate, state in presentable_state.items():
                if event.get("to") == state:
                    reached[(str(event.get("entity_id")), gate)] = str(event.get("at"))
        elif event.get("entity_type") == "gate" and event.get("to") == "approved":
            entity = str(event.get("entity_id", ""))
            intent_id, _, gate = entity.partition(":")
            start = reached.get((intent_id, gate))
            if not start:
                continue
            try:
                delta = datetime.fromisoformat(str(event.get("at"))) - datetime.fromisoformat(start)
            except ValueError:
                continue
            latencies[entity] = round(delta.total_seconds(), 3)
    return latencies
