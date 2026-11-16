from __future__ import annotations

import re
from pathlib import Path

from .storage import FileMutex, atomic_write_json, read_json


class IdentifierError(ValueError):
    pass


class IdentifierCollisionError(IdentifierError):
    pass


PATTERNS = {
    "intent": re.compile(r"^I\d{3,}$"),
    "phase": re.compile(r"^I\d{3,}-P\d{2,}$"),
    "task": re.compile(r"^I\d{3,}-P\d{2,}-T\d{3,}$"),
    # An architectural decision is Intent-scoped, like a Phase, because it is made during
    # solutioning for a particular Intent and is what that Intent's Tasks trace to. It is
    # allocated by the runner for the same reason every other identifier is: a decision a
    # model can name is a decision a model can quietly renumber.
    "decision": re.compile(r"^I\d{3,}-D\d{3,}$"),
    "requirement-functional": re.compile(r"^REQ-F-\d{3,}$"),
    "requirement-non-functional": re.compile(r"^REQ-NFR-\d{3,}$"),
    "criterion": re.compile(r"^AC-\d{3,}$"),
    "success": re.compile(r"^SC-\d{3,}$"),
    "eval": re.compile(r"^EVAL-\d{3,}$"),
    "finding": re.compile(r"^F-\d{3,}$"),
    "lesson": re.compile(r"^LESSON-\d{3,}$"),
    # A Program groups Intents for large initiatives; a baseline pins current behaviour
    # in a brownfield repository. Both are permanent identifiers allocated by the runner,
    # never chosen by a model, for the same reason every other identifier is.
    "program": re.compile(r"^PRG\d{3,}$"),
    "baseline": re.compile(r"^BASELINE-\d{3,}$"),
}


class IdentifierRegistry:
    def __init__(self, repository_root: Path) -> None:
        runtime = repository_root / ".ai-sdlc" / "runtime"
        self.path = runtime / "id-registry.json"
        self.lock_path = runtime / "id-registry.lock"

    def allocate(self, kind: str, parent: str | None = None) -> str:
        if kind not in PATTERNS:
            raise IdentifierError(f"unknown identifier kind: {kind}")
        if kind in {"phase", "task", "decision"} and parent is None:
            raise IdentifierError(f"{kind} allocation requires a parent")
        with FileMutex(self.lock_path):
            registry = read_json(self.path, {"counters": {}, "reserved": []})
            reserved = set(registry["reserved"])
            counter_key = f"{kind}:{parent}" if parent else kind
            current = registry["counters"].get(counter_key, -1 if kind == "phase" else 0)
            while True:
                current += 1
                candidate = self._format(kind, current, parent)
                if candidate not in reserved:
                    break
            registry["counters"][counter_key] = current
            registry["reserved"].append(candidate)
            atomic_write_json(self.path, registry)
            return candidate

    def reserve(self, identifier: str) -> None:
        if not any(pattern.fullmatch(identifier) for pattern in PATTERNS.values()):
            raise IdentifierError(f"invalid identifier: {identifier}")
        with FileMutex(self.lock_path):
            registry = read_json(self.path, {"counters": {}, "reserved": []})
            if identifier in registry["reserved"]:
                raise IdentifierCollisionError(f"identifier already reserved: {identifier}")
            registry["reserved"].append(identifier)
            atomic_write_json(self.path, registry)

    @staticmethod
    def _format(kind: str, value: int, parent: str | None) -> str:
        prefixes = {
            "intent": "I",
            "requirement-functional": "REQ-F-",
            "requirement-non-functional": "REQ-NFR-",
            "criterion": "AC-",
            "success": "SC-",
            "eval": "EVAL-",
            "finding": "F-",
            "lesson": "LESSON-",
            "program": "PRG",
            "baseline": "BASELINE-",
        }
        if kind == "phase":
            return f"{parent}-P{value:02d}"
        if kind == "task":
            return f"{parent}-T{value:03d}"
        if kind == "decision":
            return f"{parent}-D{value:03d}"
        return f"{prefixes[kind]}{value:03d}"
