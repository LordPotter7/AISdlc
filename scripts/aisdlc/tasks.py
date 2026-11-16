from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any  # noqa: F401  (used by the Intent-scope coordinator below)

from .storage import FileMutex, atomic_write_json, read_json


class ResourceConflictError(RuntimeError):
    pass


def _paths_overlap(left: str, right: str) -> bool:
    left_path = PurePosixPath(left.replace("\\", "/"))
    right_path = PurePosixPath(right.replace("\\", "/"))
    return left_path == right_path or left_path in right_path.parents or right_path in left_path.parents


def select_ready_tasks(
    tasks: list[dict[str, Any]],
    held_resources: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    by_id = {task["id"]: task for task in tasks}
    reserved_resources = set((held_resources or {}).keys())
    reserved_paths: list[str] = []
    selected: list[dict[str, Any]] = []
    for task in sorted(tasks, key=lambda item: item["id"]):
        if task.get("status") not in {"pending", "ready"}:
            continue
        dependencies = task.get("depends_on", [])
        if any(by_id.get(dependency, {}).get("status") not in {"verified", "merged"} for dependency in dependencies):
            continue
        resources = set(task.get("shared_resources", []))
        paths = task.get("affected_paths", [])
        if resources & reserved_resources:
            continue
        if any(_paths_overlap(path, reserved) for path in paths for reserved in reserved_paths):
            continue
        selected.append(task)
        reserved_resources.update(resources)
        reserved_paths.extend(paths)
    return selected


class ResourceLockManager:
    def __init__(self, repository_root: Path) -> None:
        runtime = repository_root / ".ai-sdlc" / "runtime"
        self.path = runtime / "resource-locks.json"
        self.lock_path = runtime / "resource-locks.lock"

    def read(self) -> dict[str, str]:
        return read_json(self.path, {})

    def acquire(self, task_id: str, resources: list[str]) -> None:
        with FileMutex(self.lock_path):
            locks = self.read()
            conflicts = [resource for resource in resources if resource in locks and locks[resource] != task_id]
            if conflicts:
                raise ResourceConflictError(f"resources already locked: {', '.join(sorted(conflicts))}")
            locks.update({resource: task_id for resource in resources})
            atomic_write_json(self.path, locks)

    def release(self, task_id: str, resources: list[str]) -> None:
        with FileMutex(self.lock_path):
            locks = self.read()
            wrong_owner = [resource for resource in resources if resource in locks and locks[resource] != task_id]
            if wrong_owner:
                raise ResourceConflictError(f"resources owned by another Task: {', '.join(sorted(wrong_owner))}")
            for resource in resources:
                locks.pop(resource, None)
            atomic_write_json(self.path, locks)

    def release_all(self, task_id: str) -> list[str]:
        """Free every resource held by a Task that has reached a terminal state."""
        with FileMutex(self.lock_path):
            locks = self.read()
            released = sorted(resource for resource, owner in locks.items() if owner == task_id)
            for resource in released:
                locks.pop(resource)
            atomic_write_json(self.path, locks)
            return released


class IntentResourceCoordinator:
    """Serialize Intents that contend for the same shared resource (W3.3).

    The Task lock manager prevents collisions *inside* one Intent. At organisational scale
    the collisions move up a level: two teams open two Intents, both of which will edit the
    same database schema, the same prompt, or the same public API surface. Nothing today
    notices until the second one merges on top of the first.

    Contention is reported rather than silently blocked, because the right answer is
    genuinely ambiguous: sometimes the second Intent should wait, and sometimes both teams
    knowingly coordinate on one resource. What must not happen is that nobody decides. So
    an overlap surfaces at Gate 3 and is either serialized or co-approved *on the record*.
    """

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        runtime = self.repository_root / ".ai-sdlc" / "runtime"
        self.path = runtime / "intent-resources.json"
        self.lock_path = runtime / "intent-resources.lock"

    def read(self) -> dict[str, Any]:
        return read_json(self.path, {"claims": {}, "coapprovals": []})

    def declare(self, intent_id: str, resources: list[str]) -> dict[str, Any]:
        """Record the shared resources an Intent expects to touch."""
        with FileMutex(self.lock_path):
            record = self.read()
            record.setdefault("claims", {})[intent_id] = sorted(set(resources))
            atomic_write_json(self.path, record)
            return record

    def contention(self, intent_id: str, active_intents: dict[str, str]) -> list[dict[str, Any]]:
        """Report other building Intents that claim a resource this one also claims."""
        record = self.read()
        claims = record.get("claims", {})
        mine = set(claims.get(intent_id, []))
        if not mine:
            return []
        coapproved = {
            (item["resource"], frozenset(item["intents"]))
            for item in record.get("coapprovals", [])
            if isinstance(item, dict) and item.get("resource") and item.get("intents")
        }
        conflicts: list[dict[str, Any]] = []
        for other_id, resources in sorted(claims.items()):
            if other_id == intent_id:
                continue
            if active_intents.get(other_id) not in {"planned", "building", "converging"}:
                continue
            overlap = mine & set(resources)
            for resource in sorted(overlap):
                if any(
                    resource == candidate and {intent_id, other_id} <= set(intents) for candidate, intents in coapproved
                ):
                    continue
                conflicts.append({"resource": resource, "intent": other_id, "status": active_intents.get(other_id)})
        return conflicts

    def coapprove(self, resource: str, intents: list[str], *, approver: str, reason: str) -> dict[str, Any]:
        """Record a human decision that two Intents may share one resource."""
        if not reason.strip():
            raise ResourceConflictError("co-approving a shared resource requires a written reason")
        if len(set(intents)) < 2:
            raise ResourceConflictError("co-approval requires at least two Intents")
        entry = {
            "resource": resource,
            "intents": sorted(set(intents)),
            "approver": approver,
            "reason": reason.strip(),
        }
        with FileMutex(self.lock_path):
            record = self.read()
            record.setdefault("coapprovals", []).append(entry)
            atomic_write_json(self.path, record)
        return entry

    def release(self, intent_id: str) -> None:
        with FileMutex(self.lock_path):
            record = self.read()
            record.get("claims", {}).pop(intent_id, None)
            atomic_write_json(self.path, record)
