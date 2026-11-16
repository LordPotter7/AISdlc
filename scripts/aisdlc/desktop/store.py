"""One open repository in the bridge: identity, context, and change detection.

A store never caches lifecycle meaning. It caches only *whether anything changed*: a
fingerprint over the files the runner owns or reads, from which a monotonic
``generation`` is derived. Every projection is rebuilt from the runner's own readers on
request, so a view can never disagree with what the runner would say right now.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aisdlc.desktop.protocol import BridgeError

#: Upper bound on files hashed per reconcile. A larger delivery tree is still watched,
#: and the omission is reported instead of silently stopping at an arbitrary file.
MAX_FINGERPRINT_FILES = 20_000

#: Runtime and configuration paths, relative to the repository, whose change means the
#: projections may have moved.
WATCHED = (
    ".ai-sdlc/runtime/state.json",
    ".ai-sdlc/runtime/audit.jsonl",
    ".ai-sdlc/runtime/gate-snapshots",
    ".ai-sdlc/runtime/gate-packets",
    ".ai-sdlc/runtime/reviews",
    ".ai-sdlc/runtime/intent-resources.json",
    ".ai-sdlc/config.yaml",
    ".ai-sdlc/glossary.yaml",
    ".ai-sdlc/lessons",
    ".ai-sdlc/security",
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


@dataclass
class Context:
    """Who this repository is, and what the runner makes of it."""

    project_id: str
    worktree_id: str
    root: str
    name: str
    initialized: bool
    delivery_dir: str
    git: dict[str, Any] = field(default_factory=dict)
    runner: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # `delivery.dir` is what the host watcher reads to follow a relocated delivery root.
        data["delivery"] = {"dir": self.delivery_dir}
        return data


def build_context(root: Path) -> Context:
    from aisdlc.config import ConfigError, delivery_root, load_config
    from aisdlc.versioning import runner_version

    canonical = root.resolve()
    if not canonical.is_dir():
        raise BridgeError("invalid_root", "the folder does not exist or is not a directory", detail={"root": str(root)})
    problems: list[str] = []
    initialized = (canonical / ".ai-sdlc" / "config.yaml").is_file()
    if not initialized:
        problems.append("This folder has no .ai-sdlc/config.yaml. Run `ai-sdlc init` (or /aisdlc:init) first.")
    configuration: dict[str, Any] = {}
    try:
        config = load_config(canonical)
        delivery = delivery_root(canonical)
        configuration = {
            "default_artifact_mode": config.default_artifact_mode,
            "require_pre_gate_review": config.review.require_pre_gate_review,
            "forbid_same_family": config.review.forbid_same_family,
            "specification_fail_on": config.specification.fail_on,
            "require_holdout": config.verification.require_holdout,
            "runner_version_recorded": config.runner_version,
        }
    except ConfigError as error:
        delivery = canonical / "delivery" / "intents"
        problems.append(f"Configuration is invalid: {error}")
    common = _git(canonical, "rev-parse", "--path-format=absolute", "--git-common-dir")
    identity = str(canonical).lower().replace("\\", "/")
    project_id = "p-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    worktree_id = "w-" + hashlib.sha256(f"{identity}|{common or ''}".encode()).hexdigest()[:12]
    return Context(
        project_id=project_id,
        worktree_id=worktree_id,
        root=str(canonical),
        name=canonical.name,
        initialized=initialized,
        delivery_dir=str(delivery),
        git={
            "branch": _git(canonical, "rev-parse", "--abbrev-ref", "HEAD"),
            "head": _git(canonical, "rev-parse", "--short", "HEAD"),
            "is_repository": common is not None,
        },
        runner={"version": runner_version(), "module": str(Path(__file__).resolve().parents[1])},
        configuration=configuration,
        problems=problems,
    )


class ProjectStore:
    def __init__(self, context: Context, cursors: dict[str, Any] | None = None) -> None:
        self.context = context
        self.root = Path(context.root)
        self.generation = 0
        self.source_fingerprint = ""
        self.last_reconciled: str | None = None
        self.last_error: str | None = None
        self.fingerprint_truncated = False
        self.restored_cursors = cursors or None
        self.reconcile()

    @classmethod
    def open(cls, root: Path, *, cursors: dict[str, Any] | None = None) -> ProjectStore:
        return cls(build_context(root), cursors)

    # -- change detection ------------------------------------------------------ #
    def _files(self) -> list[Path]:
        files: list[Path] = []
        for relative in WATCHED:
            path = self.root / relative
            if path.is_file():
                files.append(path)
            elif path.is_dir():
                files.extend(sorted(item for item in path.rglob("*") if item.is_file()))
        delivery = Path(self.context.delivery_dir)
        if delivery.is_dir():
            for item in sorted(delivery.rglob("*")):
                if len(files) >= MAX_FINGERPRINT_FILES:
                    self.fingerprint_truncated = True
                    break
                if item.is_file():
                    files.append(item)
        return files

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        self.fingerprint_truncated = False
        for path in self._files():
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{path}|{stat.st_mtime_ns}|{stat.st_size}\n".encode())
        return "sha256:" + digest.hexdigest()[:24]

    def reconcile(self) -> bool:
        """Recompute the fingerprint; bump the generation when it moved."""
        try:
            current = self.fingerprint()
            self.last_error = None
        except OSError as error:
            self.last_error = f"cannot read repository files: {error}"
            return False
        self.last_reconciled = _now()
        if current != self.source_fingerprint:
            self.source_fingerprint = current
            self.generation += 1
            return True
        return False

    # -- audit cursors ------------------------------------------------------------ #
    def audit_lines(self) -> int:
        path = self.root / ".ai-sdlc" / "runtime" / "audit.jsonl"
        if not path.is_file():
            return 0
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for _ in handle)

    def cursors(self) -> dict[str, Any]:
        return {"audit_sequence": self.audit_lines()}

    def catch_up(self) -> dict[str, Any] | None:
        """Audit events that arrived while the app was closed, counted from the cursor."""
        if not self.restored_cursors:
            return None
        try:
            seen = int(self.restored_cursors.get("audit_sequence", 0))
        except (TypeError, ValueError):
            seen = 0
        total = self.audit_lines()
        return {"audit_events_missed": max(0, total - seen), "since_sequence": seen}

    def read_audit(self) -> list[dict[str, Any]]:
        path = self.root / ".ai-sdlc" / "runtime" / "audit.jsonl"
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    events.append({"event": "unreadable", "raw_length": len(line)})
                    continue
                if isinstance(record, dict):
                    events.append(record)
        return events
