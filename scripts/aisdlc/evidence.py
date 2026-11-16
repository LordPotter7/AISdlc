from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .decisions import decision_hashes
from .migrate import stamp
from .redaction import redactor_for
from .state import StateStore
from .storage import atomic_write_json, read_json

# A verification command that never returns would hold the Task's lock forever. An hour is
# far beyond any honest test run; a command that exceeds it is recorded as a failure.
COMMAND_TIMEOUT_SECONDS = 3600
TIMEOUT_EXIT_CODE = 124  # the code coreutils `timeout` uses, so logs read the same


def run_verification_command(
    command: str, cwd: Path, timeout: float = COMMAND_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    """Run one declared command through the shell, turning a timeout into a failed result.

    The command runs in its own process group so that a timeout stops everything it started.
    `subprocess.run(timeout=...)` kills only the shell; its children keep the output pipes
    open and the call then waits for them, so the timeout would bound nothing.
    """
    if os.name == "nt":
        group: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group = {"start_new_session": True}
    process = subprocess.Popen(
        command, cwd=cwd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **group
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # Something outside the group still holds a pipe; record the failure without its output.
            stdout, stderr = "", ""
        note = f"[ai-sdlc] command exceeded {timeout:g}s and was stopped\n"
        return subprocess.CompletedProcess(command, TIMEOUT_EXIT_CODE, stdout or "", (stderr or "") + note)


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False, timeout=30)
    else:
        import signal

        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


class EvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceValidation:
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def specification_pin(repository_root: Path, task_directory: Path) -> dict[str, Any] | None:
    intent_root = next(
        (
            parent
            for parent in task_directory.resolve().parents
            if parent.parent.name == "intents" and parent.name.startswith("I")
        ),
        None,
    )
    if intent_root is None:
        return None
    spec_root = intent_root / "02-specification"
    if not spec_root.exists():
        return None
    digest = hashlib.sha256()
    for path in sorted(item for item in spec_root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(spec_root)).replace("\\", "/").encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    intent_id = intent_root.name.split("-", 1)[0]
    gate = StateStore(repository_root).read().get("gates", {}).get(f"{intent_id}:G2", {})
    return {
        "intent_id": intent_id,
        "specification_hash": f"sha256:{digest.hexdigest()}",
        "requirement_hashes": requirement_hashes(spec_root),
        "gate_2_revision": gate.get("revision"),
        "gate_2_status": gate.get("status", "unapproved"),
        # F1: architecture is pinned the same way the specification is, and for the same
        # reason. A Task built against a decision that has since changed is running on a
        # premise nobody re-approved, and per-decision hashes keep that scoped to the
        # Tasks that actually declared it.
        "decision_hashes": decision_hashes(intent_root),
    }


def requirement_hashes(spec_root: Path) -> dict[str, str]:
    """Hash each requirement and criterion independently.

    The whole-specification hash answers "did anything change"; this answers "what
    changed", which is the question scoped evidence invalidation needs (W3.4). Criteria
    are folded into the requirements that reference them, because a Task's evidence
    depends on the criterion text just as much as on the requirement prose.
    """
    path = spec_root / "requirements.yaml"
    if not path.is_file():
        return {}
    try:
        from .vendor import yaml

        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    criteria = {
        str(item.get("id")): item
        for item in document.get("acceptance_criteria", [])
        if isinstance(item, dict) and item.get("id")
    }
    hashes: dict[str, str] = {}
    records = document.get("functional_requirements", []) + document.get("non_functional_requirements", [])
    for requirement in records:
        if not isinstance(requirement, dict) or not requirement.get("id"):
            continue
        owned = [criteria[key] for key in requirement.get("acceptance_criteria", []) if key in criteria]
        payload: dict[str, Any] = {"requirement": requirement, "criteria": owned}
        references = sorted({ref for item in owned for ref in item.get("contract_operations", []) or []})
        if references:
            # S2.1: the Task's evidence depends on the interface it was tested against, so
            # an operation that changed under it must move this hash. Added only when
            # referenced, which keeps every pre-S2 hash byte-identical.
            from .contracts import operation_hashes

            payload["contracts"] = operation_hashes(spec_root, document, references)
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        hashes[str(requirement["id"])] = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    return hashes


def summarize_conformance(command_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the visible/held-out pass rates and the gap between them.

    The gap is the framework's reward-hacking detector. A suite the implementing agent
    authored proves the command ran; it cannot prove the command could have failed. When
    the visible suite passes and the held-out suite does not, the implementation
    satisfies the tests it was given rather than the specification they stand for — a
    specification-conformance defect with a named severity, not a flaky retry.

    `holdout_declared` is false when a Task ran no held-out commands at all. That is a
    distinct state from a zero gap: no instrument ran, so nothing was measured, and the
    validator must not read the absent gap as a pass.
    """
    groups: dict[str, list[dict[str, Any]]] = {"visible": [], "holdout": []}
    for record in command_records:
        groups.setdefault(str(record.get("kind", "visible")), []).append(record)

    def pass_rate(records: list[dict[str, Any]]) -> float | None:
        if not records:
            return None
        return sum(1 for record in records if record.get("exit_code") == 0) / len(records)

    visible_rate = pass_rate(groups["visible"])
    holdout_rate = pass_rate(groups["holdout"])
    gap = None if visible_rate is None or holdout_rate is None else round(visible_rate - holdout_rate, 6)
    return {
        "visible_commands": len(groups["visible"]),
        "holdout_commands": len(groups["holdout"]),
        "visible_pass_rate": visible_rate,
        "holdout_pass_rate": holdout_rate,
        "gap": gap,
        "holdout_declared": bool(groups["holdout"]),
        # The signature of reward hacking: everything the agent could see passes, and the
        # instrument it could not see does not.
        "conformance_gap": bool(gap is not None and gap > 0 and visible_rate == 1.0),
    }


def git_commit(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository_root, check=False, text=True, capture_output=True
    )
    if result.returncode != 0:
        raise EvidenceError("evidence capture requires a Git commit")
    return result.stdout.strip()


class EvidenceRunner:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def capture(
        self,
        task_id: str,
        task_directory: Path,
        commands: list[str],
        *,
        holdout_commands: list[str] | None = None,
        holdout_authorship: dict[str, Any] | None = None,
        posture: dict[str, Any] | None = None,
    ) -> Path:
        if not commands:
            raise EvidenceError("evidence capture requires at least one declared verification command")
        evidence_root = task_directory / "evidence"
        attempt = self._next_attempt(evidence_root)
        attempt_root = evidence_root / f"attempt-{attempt}"
        attempt_root.mkdir(parents=True, exist_ok=False)
        source_commit = git_commit(self.repository_root)
        spec_pin = specification_pin(self.repository_root, task_directory)
        # Built once per attempt, not per command: a pattern that fails to compile must
        # stop the capture before any output is written, not halfway through it.
        redactor = redactor_for(self.repository_root)
        started_at = datetime.now(UTC)
        command_records: list[dict[str, Any]] = []
        files: list[dict[str, str]] = []
        index = 0
        # `visible` commands may be authored by the implementing agent; `holdout` commands
        # may not. Both run in the same attempt against the same commit so the pass-rate
        # delta between them is a property of one measurement, not of two runs that could
        # have drifted apart.
        for kind, group in (("visible", commands), ("holdout", list(holdout_commands or []))):
            for command in group:
                index += 1
                command_started = time.monotonic()
                result = run_verification_command(command, self.repository_root)
                duration = time.monotonic() - command_started
                stdout_name = f"command-{index:03d}.stdout.txt"
                stderr_name = f"command-{index:03d}.stderr.txt"
                stdout_path = attempt_root / stdout_name
                stderr_path = attempt_root / stderr_name
                # R3.3: mask before the write, so the secret is never in the bytes that
                # get hashed, indexed, signed, and retained.
                out = redactor.apply(result.stdout) if redactor else None
                err = redactor.apply(result.stderr) if redactor else None
                stdout_path.write_text(out.text if out else result.stdout, encoding="utf-8", newline="\n")
                stderr_path.write_text(err.text if err else result.stderr, encoding="utf-8", newline="\n")
                files.extend(
                    [
                        {"path": stdout_name, "sha256": sha256_file(stdout_path)},
                        {"path": stderr_name, "sha256": sha256_file(stderr_path)},
                    ]
                )
                # The command string is recorded and hashed alongside its output, so a
                # credential passed on the command line is pinned exactly as firmly as one
                # printed to stdout — which is why it is redacted before either happens.
                invocation = redactor.apply(command) if redactor else None
                record: dict[str, Any] = {
                    "command": invocation.text if invocation else command,
                    "kind": kind,
                    "exit_code": result.returncode,
                    "stdout": stdout_name,
                    "stderr": stderr_name,
                    "duration_seconds": round(duration, 6),
                }
                redactions = {
                    stream: summary
                    for stream, summary in (
                        ("command", invocation.as_manifest_record() if invocation else None),
                        ("stdout", out.as_manifest_record() if out else None),
                        ("stderr", err.as_manifest_record() if err else None),
                    )
                    if summary is not None
                }
                if redactions:
                    record["redactions"] = redactions
                command_records.append(record)
        ended_at = datetime.now(UTC)
        conformance = summarize_conformance(command_records)
        manifest = {
            "task_id": task_id,
            "attempt": attempt,
            "source_commit": source_commit,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": round((ended_at - started_at).total_seconds(), 6),
            "commands": command_records,
            "files": files,
            "passed": all(record["exit_code"] == 0 for record in command_records),
            "captured_by": "runner",
            "conformance": conformance,
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "cwd": str(self.repository_root),
            },
        }
        if spec_pin is not None:
            manifest["specification"] = spec_pin
        if holdout_authorship:
            manifest["holdout_authorship"] = holdout_authorship
        if posture:
            # W4.3: an attempt executed outside its declared sandbox posture is invalid
            # evidence, the same rule shape as a mismatched commit pin.
            manifest["execution_posture"] = posture
        manifest_path = attempt_root / "manifest.json"
        atomic_write_json(manifest_path, stamp(manifest))
        self._update_index(evidence_root, manifest_path)
        return manifest_path

    @staticmethod
    def _next_attempt(evidence_root: Path) -> int:
        existing = (
            [int(path.name.split("-", 1)[1]) for path in evidence_root.glob("attempt-*")]
            if evidence_root.exists()
            else []
        )
        return max(existing, default=0) + 1

    @staticmethod
    def _update_index(evidence_root: Path, manifest_path: Path) -> None:
        index_path = evidence_root / "manifest.json"
        index = read_json(index_path, {"attempts": []})
        index["attempts"].append(
            {
                "path": str(manifest_path.relative_to(evidence_root)).replace("\\", "/"),
                "sha256": sha256_file(manifest_path),
            }
        )
        index["latest_attempt"] = len(index["attempts"])
        atomic_write_json(index_path, index)


def validate_evidence(
    manifest_path: Path,
    *,
    expected_commit: str | None = None,
) -> EvidenceValidation:
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return EvidenceValidation((f"unreadable manifest: {error}",))
    if expected_commit is not None and manifest.get("source_commit") != expected_commit:
        errors.append("source commit does not match the required pin")
    for record in manifest.get("files", []):
        path = manifest_path.parent / record.get("path", "")
        if not path.is_file():
            errors.append(f"evidence file is missing: {record.get('path')}")
        elif sha256_file(path) != record.get("sha256"):
            errors.append(f"evidence hash mismatch: {record.get('path')}")
    return EvidenceValidation(tuple(errors))


def validate_task_evidence(
    manifest_path: Path,
    repository_root: Path,
    task_directory: Path,
    *,
    expected_commit: str | None = None,
) -> EvidenceValidation:
    base = validate_evidence(manifest_path, expected_commit=expected_commit)
    errors = list(base.errors)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    current = specification_pin(repository_root, task_directory)
    captured = manifest.get("specification")
    if current is not None:
        if captured is None:
            errors.append("evidence has no specification pin")
        elif captured.get("gate_2_revision") != current.get("gate_2_revision"):
            errors.append("Gate 2 revision does not match")
        # W3.4: a whole-document hash mismatch is not by itself invalidating. Only evidence
        # tracing to a requirement that actually changed goes stale, so ask the scoped
        # comparison before failing the Task.
        elif captured.get("specification_hash") != current.get("specification_hash") and _specification_change_affects(
            repository_root, task_directory, captured, current
        ):
            errors.append("specification hash does not match")
        if current.get("gate_2_status") != "approved":
            errors.append("Gate 2 is reopened or unapproved")
        errors.extend(_decision_drift(task_directory, captured, current))
    errors.extend(_validate_verification_instruments(repository_root, task_directory, manifest))
    errors.extend(_validate_posture(repository_root, manifest))
    errors.extend(_validate_redaction(repository_root, manifest))
    return EvidenceValidation(tuple(errors))


def _validate_redaction(repository_root: Path, manifest: dict[str, Any]) -> list[str]:
    """Flag a stream that redaction consumed rather than cleaned (R3.3).

    Masking before hashing is the right place for it, and it is also the one place a
    redaction rule could be used to hide a failing check's output from review. The
    manifest records what each rule removed, so the disproportion is measurable: a rule
    that ate most of a stream is reported here rather than passing as a clean capture.
    """
    from .config import load_config

    try:
        ceiling = load_config(repository_root).evidence.max_redaction_ratio
    except Exception:  # noqa: BLE001 - validation must not fail on a config read
        return []
    problems: list[str] = []
    for record in manifest.get("commands", []):
        for stream, summary in (record.get("redactions") or {}).items():
            ratio = float(summary.get("ratio", 0))
            if ratio > ceiling:
                problems.append(
                    f"redaction removed {ratio:.0%} of {stream} for {record.get('command')!r}, above the "
                    f"{ceiling:.0%} ceiling; narrow the rule or review the output by hand"
                )
    return problems


def _task_document(task_directory: Path) -> dict[str, Any]:
    path = task_directory / "task.yaml"
    if not path.is_file():
        return {}
    try:
        from .vendor import yaml

        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _validate_verification_instruments(
    repository_root: Path, task_directory: Path, manifest: dict[str, Any]
) -> list[str]:
    """Enforce the conformance gap and held-out authorship on one attempt (W1.2).

    Scope note: this checks the instruments that *ran*. Whether a Task was required to
    declare a held-out instrument at all is a specification-time question, answered by
    `holdout_coverage_errors` at Gate 2 and again at convergence. Splitting them keeps a
    single attempt's validation about what the attempt actually shows, and keeps the
    coverage obligation where a human can still act on it.
    """
    from .config import ConfigError, load_config

    try:
        settings = load_config(repository_root).verification
    except ConfigError:
        return []
    conformance = manifest.get("conformance") or {}
    if conformance.get("conformance_gap"):
        return [
            f"specification-conformance gap: the visible suite passes and the held-out suite does not "
            f"(gap {conformance.get('gap')}); severity {settings.conformance_gap_severity}"
        ]
    if not conformance.get("holdout_declared"):
        return []
    task = _task_document(task_directory)
    authorship = manifest.get("holdout_authorship") or task.get("holdout_authorship") or {}
    author = str(authorship.get("authored_by", "")).strip()
    context = str(authorship.get("context", "")).strip().lower()
    if not author:
        return ["held-out commands must record who authored them, outside the implementing context"]
    if context == "implementing":
        return [
            "held-out commands were authored in the implementing context; the instrument that decides a "
            "criterion is not authored in the context that satisfies it"
        ]
    return []


def holdout_coverage_errors(repository_root: Path, intent_root: Path) -> list[str]:
    """Check that every behaviour-bearing Task carries a held-out instrument (W1.2).

    Class D says a criterion passes when the command exits zero, and the command is
    declared on the Task — the artifact the implementing agent works from, alongside the
    tests it wrote. The integrity machinery proves the command ran; it cannot prove the
    command could have failed. This is the check that asks for an instrument the
    implementing context did not author.

    A waiver is accepted, because some Tasks genuinely admit no held-out instrument — but
    it must be written down with a name against it, so an absent instrument is a decision
    somebody made rather than a silence nobody noticed.
    """
    from .config import ConfigError, load_config

    try:
        settings = load_config(repository_root).verification
    except ConfigError:
        return []
    if not settings.require_holdout:
        return []
    # ADR-010: the Intent's pinned profile may forbid waivers, or allow them under its own waiver.
    from .pipeline import PipelineError, pinned

    try:
        profile = pinned(intent_root, repository_root)
    except PipelineError:
        profile = {"implicit": True}
    explicit = not profile.get("implicit")
    forbidden_by_profile = explicit and profile.get("holdout_waiver") == "forbidden" and settings.allow_holdout_waiver
    allowed_by_profile = explicit and profile.get("holdout_waiver") == "allowed"
    errors: list[str] = []
    for task_path in sorted(intent_root.rglob("task.yaml")):
        task = _task_document(task_path.parent)
        if not task:
            continue
        task_id = str(task.get("id", task_path.parent.name))
        # Only criteria that assert behaviour need an independent instrument. A Task whose
        # criteria are all structural has nothing a held-out test could catch.
        behavioral = [
            criterion
            for criterion in task.get("acceptance_criteria", [])
            if isinstance(criterion, dict) and criterion.get("verification_class") in {"D", "S", "J", "N"}
        ]
        if not behavioral:
            continue
        if task.get("holdout_commands"):
            authorship = task.get("holdout_authorship") or {}
            if str(authorship.get("context", "")).strip().lower() == "implementing":
                errors.append(
                    f"{task_id} held-out instrument was authored in the implementing context; the instrument "
                    "that decides a criterion is not authored in the context that satisfies it"
                )
            elif not str(authorship.get("authored_by", "")).strip():
                errors.append(f"{task_id} held-out instrument records no authorship")
            continue
        waiver = task.get("holdout_waiver")
        if waiver:
            if forbidden_by_profile:
                errors.append(f"{task_id} relies on a held-out waiver, which this Intent's pipeline profile forbids")
            elif not settings.allow_holdout_waiver and not allowed_by_profile:
                errors.append(f"{task_id} relies on a held-out waiver, which this repository disables")
            elif not str((waiver or {}).get("reason", "")).strip():
                errors.append(f"{task_id} held-out waiver has no written reason")
            elif not str((waiver or {}).get("approved_by", "")).strip():
                errors.append(f"{task_id} held-out waiver names no approver")
            continue
        errors.append(
            f"{task_id} declares no held-out instrument and no waiver; add `holdout_commands` authored "
            "outside the implementing context, or record a `holdout_waiver`"
        )
    return errors


def _validate_posture(repository_root: Path, manifest: dict[str, Any]) -> list[str]:
    """Reject an attempt executed outside its declared sandbox posture (W4.3)."""
    from .config import ConfigError, load_config

    try:
        settings = load_config(repository_root).security
    except ConfigError:
        return []
    if not settings.require_declared_posture:
        return []
    posture = manifest.get("execution_posture")
    if not posture:
        return ["evidence records no execution posture; the sandbox it ran under cannot be established"]
    missing = [
        field
        for field in ("network_policy", "filesystem_scope", "credential_surface")
        if not str(posture.get(field, "")).strip()
    ]
    if missing:
        return [f"execution posture is incomplete: {', '.join(missing)}"]
    return []


def _decision_drift(
    task_directory: Path,
    captured: dict[str, Any],
    current: dict[str, Any],
) -> list[str]:
    """Report architectural decisions that moved under a Task after its evidence was captured.

    Scoped by declaration, exactly like the requirement comparison above: only the
    decisions a Task named in `source_decisions` can invalidate its evidence. A Task that
    declares none is unaffected, which keeps the control proportional — recording an
    architecture must not make every unrelated Task's evidence fragile.

    Absent pins mean silence rather than failure. Evidence captured before decisions
    existed is old, not wrong, and re-capturing every historical attempt to prove that
    would be the migration mistake this framework already refuses to make elsewhere.
    """
    captured_decisions = captured.get("decision_hashes")
    current_decisions = current.get("decision_hashes")
    if not isinstance(captured_decisions, dict) or not isinstance(current_decisions, dict):
        return []
    declared = [str(item) for item in _task_document(task_directory).get("source_decisions", [])]
    problems = []
    for decision_id in declared:
        if decision_id not in current_decisions:
            problems.append(f"evidence traces to decision {decision_id}, which no longer exists")
        elif captured_decisions.get(decision_id) != current_decisions.get(decision_id):
            problems.append(
                f"decision {decision_id} changed after this evidence was captured; "
                "re-run verification against the current decision"
            )
    return problems


def _specification_change_affects(
    repository_root: Path,
    task_directory: Path,
    captured: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """Decide whether a spec revision touched the requirements this Task's evidence covers.

    A single whole-document hash makes every reopened Gate 2 invalidate every Task's
    evidence, however small the edit. That is why teams avoid reopening the gate and let
    the specification drift instead — the failure that dominates complaints about
    non-blocking verify steps elsewhere. Comparing per-requirement hashes makes correction
    affordable: only Tasks mapped to a requirement that actually changed lose their
    evidence.

    Falls back to "affected" whenever the scoped comparison cannot be made, so an
    unreadable or absent per-requirement record can never weaken the check.
    """
    captured_requirements = captured.get("requirement_hashes")
    current_requirements = current.get("requirement_hashes")
    if not isinstance(captured_requirements, dict) or not isinstance(current_requirements, dict):
        return True
    task = _task_document(task_directory)
    mapped = [str(item) for item in task.get("requirements", [])]
    if not mapped:
        return True
    for requirement_id in mapped:
        if captured_requirements.get(requirement_id) != current_requirements.get(requirement_id):
            return True
    return False
