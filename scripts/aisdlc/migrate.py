"""Move a repository's runner-owned records forward one schema generation at a time.

Two rules shape this module, and the second is the interesting one.

**Mutable records are migrated by rewriting.** Runtime state and generated traceability
are the runner's current view of the world. When their shape changes, they are rewritten
in place, under the same file mutex every other state mutation takes.

**Immutable records are migrated by tolerating them.** Evidence manifests are hashed into
their evidence index and, once `evidence.require_attestation` is on, signed. Rewriting one
to stamp a new field would invalidate the hash and break the signature — that is, the
migration would destroy exactly the property the record exists to carry. So captured
evidence is never rewritten. It keeps the schema generation it was written under, readers
tolerate an absent `schema_version` as generation 0, and new manifests carry the current
generation from the moment they are captured.

That asymmetry is why `migrate` reports evidence separately as `tolerated` rather than
counting it as work left undone. A repository with a thousand generation-0 manifests is
fully migrated; its history is simply older than its state.

The refusal in `assert_readable` is the other half. A runner that meets a record from a
newer generation stops and names both versions, rather than reporting a record it cannot
interpret as a malformed one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .storage import FileMutex, atomic_write_json
from .versioning import SCHEMA_VERSION


class MigrationError(RuntimeError):
    """Raised when a repository cannot be read or moved to the current schema."""


def record_version(payload: dict[str, Any]) -> int:
    """The schema generation a record was written under. Absent means generation 0."""
    value = payload.get("schema_version", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def stamp(payload: dict[str, Any]) -> dict[str, Any]:
    """Return `payload` carrying the current schema generation.

    Every runner-owned record passes through here on its way to disk, so the generation a
    record claims is always the generation the code that wrote it actually spoke.
    """
    payload["schema_version"] = SCHEMA_VERSION
    return payload


def assert_readable(payload: dict[str, Any], description: str) -> dict[str, Any]:
    """Fail closed on a record from a generation this runner does not know.

    An older runner meeting a newer record has no safe reading of it: the fields it does
    not know about are exactly the ones carrying meaning it would silently drop. Naming
    both versions turns an inexplicable validation failure into an upgrade instruction.
    """
    found = record_version(payload)
    if found > SCHEMA_VERSION:
        raise MigrationError(
            f"{description} was written at schema generation {found}, but this runner understands "
            f"generation {SCHEMA_VERSION}. Upgrade the runner (`/plugin update aisdlc`) rather than "
            "editing the record."
        )
    return payload


# Each migration takes a record at generation `target - 1` and returns it at `target`.
# Generation 1 introduces the stamp itself, so it has no field work to do beyond being
# recorded as applied.
def _to_generation_1(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


def _to_generation_2(payload: dict[str, Any]) -> dict[str, Any]:
    """Add decision traceability to generated records (F1/F2).

    Shape-aware rather than type-tagged: the mutable records this walks are runtime state
    and generated traceability, and only the latter grows fields here. Detecting that by
    the presence of `links` keeps the migration honest about what it is changing — a state
    file passing through gains nothing but the stamp.

    The fields are added empty. A traceability record is regenerated from source artifacts
    on the next `trace` run, so this migration only has to make an existing file valid
    against the new schema, not reconstruct links that were never recorded.
    """
    if "links" not in payload:
        return payload
    payload.setdefault("decision_links", [])
    payload.setdefault("decision_reverse_links", {})
    return payload


MIGRATIONS: dict[int, tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    1: ("stamp schema_version onto runner-owned records", _to_generation_1),
    2: ("add decision traceability to generated records", _to_generation_2),
}


class MigrationService:
    """Plan and apply schema migrations for one repository."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root

    def _mutable_records(self) -> list[Path]:
        """Runner-owned records that may be rewritten in place."""
        paths = [self.repository_root / ".ai-sdlc" / "runtime" / "state.json"]
        try:
            from .config import delivery_root

            paths.extend(sorted(delivery_root(self.repository_root).rglob("traceability.json")))
        except Exception:  # noqa: BLE001 - a repository with no readable config has state only
            pass
        return [path for path in paths if path.is_file()]

    def _evidence_records(self) -> list[Path]:
        try:
            from .config import delivery_root

            return sorted(delivery_root(self.repository_root).rglob("evidence/**/manifest.json"))
        except Exception:  # noqa: BLE001
            return []

    def plan(self) -> dict[str, Any]:
        """What migrating this repository would do, without doing any of it."""
        pending: list[dict[str, Any]] = []
        ahead: list[dict[str, Any]] = []
        for path in self._mutable_records():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise MigrationError(f"cannot read {path}: {error}") from error
            if not isinstance(payload, dict):
                continue
            found = record_version(payload)
            entry = {"path": self._relative(path), "from": found, "to": SCHEMA_VERSION}
            if found > SCHEMA_VERSION:
                ahead.append(entry)
            elif found < SCHEMA_VERSION:
                pending.append(entry)

        tolerated = [self._relative(path) for path in self._evidence_records() if self._is_older_evidence(path)]
        return {
            "repository_root": str(self.repository_root.resolve()),
            "schema_version": SCHEMA_VERSION,
            "pending": pending,
            "ahead_of_runner": ahead,
            "tolerated_immutable": tolerated,
            "steps": [
                {"generation": generation, "description": MIGRATIONS[generation][0]}
                for generation in sorted(MIGRATIONS)
            ],
            "status": "ahead" if ahead else ("pending" if pending else "current"),
        }

    @staticmethod
    def _is_older_evidence(path: Path) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and record_version(payload) < SCHEMA_VERSION

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repository_root)).replace("\\", "/")
        except ValueError:
            return str(path)

    def apply(self) -> dict[str, Any]:
        """Migrate every mutable record forward. Idempotent: a current repository is untouched."""
        planned = self.plan()
        if planned["ahead_of_runner"]:
            raise MigrationError(
                "this repository holds records from a newer schema generation than this runner "
                "understands; upgrade the runner instead of migrating down: "
                + ", ".join(entry["path"] for entry in planned["ahead_of_runner"])
            )
        lock = self.repository_root / ".ai-sdlc" / "runtime" / "state.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        migrated: list[dict[str, Any]] = []
        with FileMutex(lock):
            for entry in planned["pending"]:
                path = self.repository_root / entry["path"]
                payload = json.loads(path.read_text(encoding="utf-8"))
                for generation in sorted(MIGRATIONS):
                    if generation > record_version(payload):
                        payload = MIGRATIONS[generation][1](payload)
                        payload["schema_version"] = generation
                atomic_write_json(path, payload)
                migrated.append(entry)
        return {**planned, "applied": migrated, "status": "migrated" if migrated else "current"}
