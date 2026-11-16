"""The Program layer: Intents that belong to one initiative (W3.1).

Large initiatives — a migration, a platform re-architecture, a compliance programme — do
not fit inside one Intent, and until now the framework had no identifier above `Ixxx`.
Teams facing that gap either inflate a single Intent until its Gate 2 is meaningless, or
they run parallel Intents with the ordering between them living in somebody's head.

A Program is deliberately thin. It is a dependency graph and a reporting scope: an ordered
set of member Intents, the constraints between them, and a roll-up of their traceability.
It carries **no gates, no criteria, and no evidence of its own**. Every assurance decision
stays on the Intent, where the artifacts a human reviews actually live.

That restraint is the design. The failure mode of hierarchical process frameworks is a new
tier that acquires its own approvals, its own documents and its own ceremony until the
tier itself is the work. A Program that can only *order* and *report* cannot become that.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import delivery_root
from .identifiers import IdentifierRegistry
from .state import StateStore
from .storage import FileMutex, atomic_write_text
from .vendor import yaml


class ProgramError(RuntimeError):
    pass


class ProgramRegistry:
    """Runner-owned record of programmes and their member Intents."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.path = self.repository_root / ".ai-sdlc" / "programs" / "registry.yaml"
        self.lock_path = self.repository_root / ".ai-sdlc" / "runtime" / "programs.lock"

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"programs": {}}
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {"programs": {}}

    def create(self, title: str, *, owner: str) -> dict[str, Any]:
        if not title.strip():
            raise ProgramError("a Program requires a title")
        identifier = IdentifierRegistry(self.repository_root).allocate("program")
        record = {
            "id": identifier,
            "title": title.strip(),
            "owner": owner,
            "created_at": datetime.now(UTC).isoformat(),
            "members": [],
            "dependencies": [],
        }
        with FileMutex(self.lock_path):
            registry = self.read()
            registry.setdefault("programs", {})[identifier] = record
            self._write(registry)
        return record

    def get(self, program_id: str) -> dict[str, Any]:
        try:
            return self.read()["programs"][program_id]
        except KeyError as error:
            raise ProgramError(f"unknown Program: {program_id}") from error

    def add_member(self, program_id: str, intent_id: str, *, requires: list[str] | None = None) -> dict[str, Any]:
        """Enrol an Intent, optionally behind predecessors that must close first."""
        with FileMutex(self.lock_path):
            registry = self.read()
            program = registry.get("programs", {}).get(program_id)
            if program is None:
                raise ProgramError(f"unknown Program: {program_id}")
            state = StateStore(self.repository_root).read()
            if intent_id not in state.get("intents", {}):
                raise ProgramError(f"unknown Intent: {intent_id}")
            for predecessor in requires or []:
                if predecessor not in state.get("intents", {}):
                    raise ProgramError(f"unknown predecessor Intent: {predecessor}")
                if predecessor == intent_id:
                    raise ProgramError("an Intent cannot depend on itself")
            if intent_id not in program["members"]:
                program["members"].append(intent_id)
            program["dependencies"] = [
                item for item in program.get("dependencies", []) if item.get("intent") != intent_id
            ]
            for predecessor in requires or []:
                program["dependencies"].append({"intent": intent_id, "requires": predecessor})
            self._detect_cycle(program)
            self._write(registry)
            return program

    @staticmethod
    def _detect_cycle(program: dict[str, Any]) -> None:
        """Refuse a dependency graph that cannot be satisfied.

        A cycle here is not a subtle ordering inefficiency; it is a Program in which no
        member can ever become ready, and it is far cheaper to reject at declaration time
        than to diagnose later from a queue that never advances.
        """
        edges: dict[str, set[str]] = {}
        for item in program.get("dependencies", []):
            edges.setdefault(str(item["intent"]), set()).add(str(item["requires"]))
        visiting: set[str] = set()
        settled: set[str] = set()

        def visit(node: str, trail: list[str]) -> None:
            if node in settled:
                return
            if node in visiting:
                cycle = " -> ".join([*trail, node])
                raise ProgramError(f"Program dependencies contain a cycle: {cycle}")
            visiting.add(node)
            for neighbour in sorted(edges.get(node, set())):
                visit(neighbour, [*trail, node])
            visiting.discard(node)
            settled.add(node)

        for node in sorted(edges):
            visit(node, [])

    def blocking_predecessors(self, intent_id: str) -> list[str]:
        """Programme predecessors of an Intent that have not closed.

        This is what stops an Intent entering `building` ahead of work it depends on. It
        answers only for programme membership; Task-level dependencies inside an Intent
        remain the ready-selection logic's job, unchanged.
        """
        state = StateStore(self.repository_root).read().get("intents", {})
        blocking: list[str] = []
        for program in self.read().get("programs", {}).values():
            if intent_id not in program.get("members", []):
                continue
            for item in program.get("dependencies", []):
                if item.get("intent") != intent_id:
                    continue
                predecessor = str(item.get("requires"))
                if item.get("waived"):
                    continue
                if state.get(predecessor, {}).get("status") != "completed":
                    blocking.append(predecessor)
        return sorted(set(blocking))

    def waive(self, program_id: str, intent_id: str, predecessor: str, *, reason: str, actor: str) -> dict[str, Any]:
        """Record a deliberate override of one programme dependency.

        Waivers are named and reasoned rather than silent, because "we shipped this out of
        order and here is who decided to" is exactly the record an audit of a large
        initiative asks for.
        """
        if not reason.strip():
            raise ProgramError("waiving a Program dependency requires a written reason")
        with FileMutex(self.lock_path):
            registry = self.read()
            program = registry.get("programs", {}).get(program_id)
            if program is None:
                raise ProgramError(f"unknown Program: {program_id}")
            for item in program.get("dependencies", []):
                if item.get("intent") == intent_id and item.get("requires") == predecessor:
                    item.update(
                        {
                            "waived": True,
                            "waived_by": actor,
                            "waiver_reason": reason.strip(),
                            "waived_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    self._write(registry)
                    return item
            raise ProgramError(f"{intent_id} does not depend on {predecessor} in {program_id}")

    def rollup(self, program_id: str) -> dict[str, Any]:
        """Aggregate member Intent status and traceability into one reporting view."""
        from .traceability import generate_traceability

        program = self.get(program_id)
        state = StateStore(self.repository_root).read()
        root = delivery_root(self.repository_root)
        members: list[dict[str, Any]] = []
        covered = uncovered = 0
        for intent_id in program.get("members", []):
            matches = list(root.glob(f"{intent_id}-*"))
            record: dict[str, Any] = {
                "intent_id": intent_id,
                "status": state.get("intents", {}).get(intent_id, {}).get("status", "unknown"),
                "blocking_predecessors": self.blocking_predecessors(intent_id),
            }
            if len(matches) == 1:
                try:
                    trace_path = generate_traceability(matches[0])
                    import json

                    trace = json.loads(trace_path.read_text(encoding="utf-8"))
                    links = trace.get("links", [])
                    verified = sum(1 for link in links if link.get("status") == "verified")
                    covered += verified
                    uncovered += len(links) - verified
                    record["requirements"] = len(links)
                    record["verified_requirements"] = verified
                except (OSError, ValueError, KeyError) as error:
                    record["traceability_error"] = str(error)
            members.append(record)
        total = covered + uncovered
        return {
            "program_id": program_id,
            "title": program.get("title"),
            "owner": program.get("owner"),
            "members": members,
            "requirement_coverage": round(covered / total, 4) if total else 0.0,
            "closed_members": sum(1 for item in members if item["status"] == "completed"),
            "total_members": len(members),
        }

    def _write(self, registry: dict[str, Any]) -> None:
        atomic_write_text(self.path, yaml.safe_dump(registry, sort_keys=False))
