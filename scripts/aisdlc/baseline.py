"""Characterization baselines for brownfield repositories (W3.5).

"Any size of application" mostly means an *existing* application, and existing
applications arrive with no requirements, no evidence, and no tests worth trusting. The
lifecycle as specified assumes a green field: state what the system should do, then prove
it does. Applied unchanged to a legacy system, that produces either a fiction (specs
reverse-engineered from code, which encode the bugs as requirements) or paralysis.

A baseline sidesteps both. It asserts nothing about correctness — it pins *current
behaviour* as a reference, hashed and tied to a commit. The first Intents in a brownfield
repository then state criteria relative to that reference: no regression against
BASELINE-001, mutation score does not decrease, no new architectural violation. Honest,
cheap, and enforceable on day one, instead of pretending the legacy system has a
specification.

Architectural fitness functions live here for the same reason: dependency direction,
layer boundaries and cycle counts are ordinary Class D criteria — a command that exits
non-zero on violation — and a baseline is where a repository first learns what its
current violation count is.
"""

from __future__ import annotations

import json
import platform
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evidence import git_commit, sha256_file
from .identifiers import IdentifierRegistry
from .storage import FileMutex, atomic_write_json, read_json


class BaselineError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    name: str
    kind: str
    command: str
    exit_code: int
    value: float | None
    stdout_file: str
    stderr_file: str
    duration_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "command": self.command,
            "exit_code": self.exit_code,
            "value": self.value,
            "stdout": self.stdout_file,
            "stderr": self.stderr_file,
            "duration_seconds": round(self.duration_seconds, 6),
        }


class BaselineService:
    """Capture and compare characterization snapshots."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.root = self.repository_root / ".ai-sdlc" / "baselines"
        self.lock_path = self.repository_root / ".ai-sdlc" / "runtime" / "baselines.lock"

    def capture(self, probes: list[dict[str, Any]], *, label: str = "") -> Path:
        """Run every declared probe and record the result as hashed evidence.

        A probe's *exit code is not a verdict here*. A failing test suite in a legacy
        repository is a fact about the repository, and recording it as the reference is
        the entire point — the next Intent must not make it worse, which is a claim the
        framework can actually check.
        """
        if not probes:
            raise BaselineError("a baseline requires at least one declared probe")
        identifier = IdentifierRegistry(self.repository_root).allocate("baseline")
        snapshot_root = self.root / identifier
        snapshot_root.mkdir(parents=True, exist_ok=False)
        commit = git_commit(self.repository_root)
        results: list[ProbeResult] = []
        files: list[dict[str, str]] = []
        for index, probe in enumerate(probes, start=1):
            command = str(probe.get("command", "")).strip()
            if not command:
                raise BaselineError(f"probe {probe.get('name', index)} declares no command")
            started = time.monotonic()
            completed = subprocess.run(
                command,
                cwd=self.repository_root,
                shell=True,
                text=True,
                capture_output=True,
                check=False,
                timeout=int(probe.get("timeout_seconds", 3600)),
            )
            duration = time.monotonic() - started
            stdout_name = f"probe-{index:03d}.stdout.txt"
            stderr_name = f"probe-{index:03d}.stderr.txt"
            (snapshot_root / stdout_name).write_text(completed.stdout, encoding="utf-8", newline="\n")
            (snapshot_root / stderr_name).write_text(completed.stderr, encoding="utf-8", newline="\n")
            for name in (stdout_name, stderr_name):
                files.append({"path": name, "sha256": sha256_file(snapshot_root / name)})
            results.append(
                ProbeResult(
                    name=str(probe.get("name", f"probe-{index}")),
                    kind=str(probe.get("kind", "characterization")),
                    command=command,
                    exit_code=completed.returncode,
                    value=_extract_value(probe, completed.stdout),
                    stdout_file=stdout_name,
                    stderr_file=stderr_name,
                    duration_seconds=duration,
                )
            )
        snapshot = {
            "id": identifier,
            "label": label,
            "source_commit": commit,
            "captured_at": datetime.now(UTC).isoformat(),
            "captured_by": "baseline-runner",
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "probes": [result.as_dict() for result in results],
            "files": files,
        }
        manifest_path = snapshot_root / "baseline.json"
        atomic_write_json(manifest_path, snapshot)
        self._index(identifier, manifest_path)
        return manifest_path

    def _index(self, identifier: str, manifest_path: Path) -> None:
        index_path = self.root / "index.json"
        with FileMutex(self.lock_path):
            index = read_json(index_path, {"baselines": []})
            index["baselines"].append(
                {
                    "id": identifier,
                    "path": str(manifest_path.relative_to(self.root)).replace("\\", "/"),
                    "sha256": sha256_file(manifest_path),
                }
            )
            atomic_write_json(index_path, index)

    def load(self, identifier: str) -> dict[str, Any]:
        path = self.root / identifier / "baseline.json"
        if not path.is_file():
            raise BaselineError(f"unknown baseline: {identifier}")
        index = read_json(self.root / "index.json", {"baselines": []})
        record = next((item for item in index["baselines"] if item["id"] == identifier), None)
        if record and sha256_file(path) != record.get("sha256"):
            raise BaselineError(f"baseline {identifier} does not match its recorded hash")
        return json.loads(path.read_text(encoding="utf-8"))

    def compare(self, identifier: str, probes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Re-run the baseline's probes and report regressions against it.

        Regression, not equality. A probe that improved is not a failure — the criterion a
        brownfield Task declares is "no worse than the reference", which is the only claim
        the repository can honestly make before it has real requirements.
        """
        reference = self.load(identifier)
        current_probes = probes or [
            {"name": probe["name"], "kind": probe["kind"], "command": probe["command"], "metric": probe.get("metric")}
            for probe in reference["probes"]
        ]
        current_path = self.capture(current_probes, label=f"comparison-against-{identifier}")
        current = json.loads(current_path.read_text(encoding="utf-8"))
        by_name = {probe["name"]: probe for probe in reference["probes"]}
        regressions: list[dict[str, Any]] = []
        improvements: list[dict[str, Any]] = []
        for probe in current["probes"]:
            previous = by_name.get(probe["name"])
            if previous is None:
                continue
            # An exit code that went from passing to failing is a regression regardless of
            # any numeric metric: something that used to work no longer does.
            if previous["exit_code"] == 0 and probe["exit_code"] != 0:
                regressions.append({"probe": probe["name"], "reason": "probe now fails and previously passed"})
                continue
            before, after = previous.get("value"), probe.get("value")
            if before is None or after is None:
                continue
            if after < before:
                regressions.append(
                    {"probe": probe["name"], "reason": "metric decreased", "before": before, "after": after}
                )
            elif after > before:
                improvements.append({"probe": probe["name"], "before": before, "after": after})
        return {
            "baseline_id": identifier,
            "comparison_id": current["id"],
            "source_commit": current["source_commit"],
            "regressions": regressions,
            "improvements": improvements,
            "passed": not regressions,
        }


def _extract_value(probe: dict[str, Any], stdout: str) -> float | None:
    """Pull a numeric metric out of a probe's output using its declared pattern.

    The pattern is declared on the probe rather than guessed, because a baseline whose
    metric extraction is heuristic would silently change meaning when a tool reformats
    its output — and a moving reference is worse than no reference.
    """
    pattern = probe.get("metric")
    if not pattern:
        return None
    import re

    match = re.search(str(pattern), stdout)
    if not match:
        return None
    try:
        return float(match.group(1) if match.groups() else match.group(0))
    except (TypeError, ValueError):
        return None


def baseline_criteria_errors(repository_root: Path, intent_root: Path) -> list[str]:
    """Verify that criteria citing a baseline cite one that exists (W3.5)."""
    service = BaselineService(repository_root)
    errors: list[str] = []
    for task_path in sorted(intent_root.rglob("task.yaml")):
        from .vendor import yaml

        try:
            task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(task, dict):
            continue
        for criterion in task.get("acceptance_criteria", []):
            reference = isinstance(criterion, dict) and criterion.get("baseline_ref")
            if not reference:
                continue
            try:
                service.load(str(reference))
            except BaselineError as error:
                errors.append(f"{task.get('id')} criterion {criterion.get('id')}: {error}")
    return errors
