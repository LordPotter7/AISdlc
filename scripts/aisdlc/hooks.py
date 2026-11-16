"""Lifecycle guards for the Claude Code hook events.

Every import here is deliberate. This module is reached from `aisdlc.entry` before the
Typer application exists, so that an unmanaged repository can be recognised and dismissed
without paying for the runner's dependency graph (R3.1). `config` and `state` are imported
inside the functions that need them, which is *after* `is_lifecycle_managed` has already
said yes. Moving either to module scope silently reinstates the cost on every edit in
every repository on the machine.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TASK_ID = re.compile(r"\[(I\d{3,}-P\d{2,}-T\d{3,})\]")

# Claude Code dispatches these event names; the runner maps each to a lifecycle check.
SUPPORTED_EVENTS = ("pre-tool-use", "post-tool-use", "exit-plan-mode", "stop", "session-start")


@dataclass(frozen=True)
class HookDecision:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


def _normalized(path: str) -> str:
    return "/" + path.replace("\\", "/").lower().strip("/")


def _canonical(path: str, repository_root: Path | None = None) -> str:
    """The path the filesystem will actually write, in `_normalized` form.

    Matching raw text let OS-level aliases through (0.2.0 release review): Win32 strips
    trailing dots and spaces from components, expands 8.3 short names, follows links and
    junctions, and accepts NTFS stream syntax (`dir::$INDEX_ALLOCATION`). Resolving first
    and then removing exactly what Win32 removes closes all of them on every platform.
    """
    candidate = Path(path)
    # A relative path names a file in the repository being edited, not in the hook's cwd.
    if repository_root is not None and not candidate.is_absolute():
        candidate = repository_root / candidate
    try:
        resolved = str(candidate.resolve(strict=False))
    except (OSError, ValueError):
        resolved = path
    parts = []
    for index, part in enumerate(resolved.replace("\\", "/").split("/")):
        if index > 0 or not re.fullmatch(r"[A-Za-z]:", part):
            part = part.split(":", 1)[0]  # an NTFS stream suffix names the same file or directory
        parts.append(part.rstrip(". ") if part not in (".", "..") else part)
    return _normalized("/".join(parts))


# Evidence is only ever written to `<task directory>/evidence/`, and Task directories live
# under a `04-build/` stage folder. Matching any `/evidence/` blocked ordinary user folders,
# and every file at all when the repository itself sat below a folder named `evidence`.
_EVIDENCE = re.compile(r"/04-build/(?:[^/]+/)*evidence/")


def _is_runner_owned(normalized: str) -> bool:
    return (
        "/.ai-sdlc/runtime/" in normalized
        or _EVIDENCE.search(normalized) is not None
        or normalized.endswith("/traceability.json")
    )


def _task_subject(payload: dict[str, Any]) -> str:
    """Collect every field a Claude Code Task call can carry the identifier in."""
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}
    return " ".join(
        str(value)
        for value in (
            payload.get("task_subject", ""),
            tool_input.get("description", ""),
            tool_input.get("prompt", ""),
        )
        if value
    )


def _task_id(payload: dict[str, Any]) -> str | None:
    match = TASK_ID.search(_task_subject(payload))
    return match.group(1) if match else None


def _durable_task_count(repository_root: Path, task_id: str) -> int:
    from .config import delivery_root

    return len(list(delivery_root(repository_root).rglob(f"{task_id}/task.yaml")))


def _state(repository_root: Path) -> dict[str, Any]:
    from .state import StateStore

    return StateStore(repository_root).read()


def _deny(reason: str) -> HookDecision:
    return HookDecision(exit_code=2, stderr=reason)


def is_lifecycle_managed(repository_root: Path) -> bool:
    """Whether these guards apply to the repository the session is working in.

    The plugin is installed once and then loaded in every repository the user opens, so
    the guards must be inert wherever the lifecycle was never adopted. `.ai-sdlc/config.yaml`
    is the marker: `init` writes it, nothing else does, and a repository that deletes it
    has opted out. Without this gate a global install would deny every `Task` delegation
    and every plan in every unrelated repository on the machine.
    """
    return (repository_root / ".ai-sdlc" / "config.yaml").is_file()


def _hooks_disabled() -> bool:
    """Escape hatch for a managed repository doing deliberately unmanaged work."""
    return os.environ.get("AISDLC_HOOKS", "").strip().lower() in {"0", "off", "false", "disabled"}


def evaluate_hook(event: str, payload: dict[str, Any], repository_root: Path) -> HookDecision:
    # The order is the point (R3.1). Both of these are pure standard-library checks, and
    # the overwhelmingly common case — an unmanaged repository — returns here without the
    # runner's dependency graph ever being imported.
    if _hooks_disabled() or not is_lifecycle_managed(repository_root):
        return HookDecision()
    from .config import ConfigError

    try:
        from .versioning import incompatible_reason

        skew = incompatible_reason(repository_root)
        if skew is not None:
            return _deny(skew)
        return _dispatch(event, payload, repository_root)
    except ConfigError as error:
        # Fail closed. A malformed config makes every artifact lookup return nothing,
        # which would otherwise be reported as a missing Task rather than a broken file.
        return _deny(f"Cannot evaluate the lifecycle guard: .ai-sdlc/config.yaml is invalid. {error}")


def _dispatch(event: str, payload: dict[str, Any], repository_root: Path) -> HookDecision:
    if event == "pre-tool-use":
        return _pre_tool_use(payload, repository_root)
    if event == "post-tool-use":
        return _post_tool_use(payload, repository_root)
    if event == "task-created":
        return _task_created(payload, repository_root)
    if event == "task-completed":
        return _task_completed(payload, repository_root)
    if event == "exit-plan-mode":
        return _exit_plan_mode(payload, repository_root)
    if event == "stop":
        return _stop(payload, repository_root)
    if event == "session-start":
        return _session_start(payload, repository_root)
    return _deny(f"unsupported hook event: {event}")


def _session_start(_payload: dict[str, Any], repository_root: Path) -> HookDecision:
    """Tell the session where the lifecycle stands, before it edits anything (R3.5).

    Without this the agent starts blind: the guards can refuse an action, but nothing
    tells it an Intent is mid-flight or a gate is waiting until somebody runs a command,
    and by then the first edits have happened. The slash commands already open by
    injecting live runner state; this is the same move, one step earlier.

    It emits context and never blocks. A session that cannot be oriented is worse off than
    one that is not oriented, so every failure here degrades to silence.
    """
    try:
        state = _state(repository_root)
    except Exception:  # noqa: BLE001 - orientation is a convenience, never a gate
        return HookDecision()

    intents = state.get("intents", {})
    active = sorted(
        (intent_id, record.get("status"))
        for intent_id, record in intents.items()
        if record.get("status") not in {"completed", "aborted"}
    )
    if not active:
        return HookDecision()

    gates = state.get("gates", {})
    tasks = state.get("tasks", {})
    lines = ["AI SDLC: this repository is lifecycle-managed."]
    for intent_id, status in active:
        pending = sorted(
            gate.split(":", 1)[1]
            for gate, record in gates.items()
            if gate.startswith(f"{intent_id}:") and record.get("status") != "approved"
        )
        ready = sum(
            1
            for task_id, record in tasks.items()
            if task_id.startswith(f"{intent_id}-") and record.get("status") == "ready"
        )
        detail = f"  {intent_id}: {status}"
        if ready:
            detail += f", {ready} ready Task(s)"
        if pending:
            # Naming the packet alongside the pending gate is the point: the decision and
            # the two-minute brief for making it should never be two separate discoveries.
            detail += f", awaiting a human decision on {pending[0]} (`ai-sdlc gate-packet {intent_id} {pending[0]}`)"
        lines.append(detail)
    lines.append("  Run `/aisdlc:status` for the next legal action. The runner is the source of truth.")
    return HookDecision(stdout="\n".join(lines))


def _task_created(payload: dict[str, Any], repository_root: Path) -> HookDecision:
    task_id = _task_id(payload)
    if task_id is None:
        return _deny("Task description must begin with a permanent [Ixxx-Pxx-Txxx] identifier allocated by the runner.")
    if _durable_task_count(repository_root, task_id) != 1:
        return _deny(f"Durable Task artifact not found for {task_id}; create it with the runner before delegating.")
    return HookDecision()


def _task_completed(payload: dict[str, Any], repository_root: Path) -> HookDecision:
    task_id = _task_id(payload)
    if task_id is None:
        return _deny("Task subject lacks a permanent identifier.")
    status = _state(repository_root)["tasks"].get(task_id, {}).get("status")
    if status not in {"verified", "merged"}:
        return _deny(
            f"Runner verification is required before completing {task_id}; current state is {status!r}. "
            "Run verify-task, capture authoritative evidence, and record an independent review."
        )
    return HookDecision()


def _exit_plan_mode(payload: dict[str, Any], repository_root: Path) -> HookDecision:
    tool_input = payload.get("tool_input", {})
    plan = str(tool_input.get("plan", payload.get("plan", "")) if isinstance(tool_input, dict) else "")
    task_ids = sorted(set(re.findall(r"I\d{3,}-P\d{2,}-T\d{3,}", plan)))
    if not task_ids:
        return _deny("Plan must contain at least one permanent Task identifier before Gate 3 presentation.")
    missing = [task_id for task_id in task_ids if _durable_task_count(repository_root, task_id) != 1]
    if missing:
        return _deny("Plan references Tasks without one durable artifact: " + ", ".join(missing))
    return HookDecision()


def _stop(_payload: dict[str, Any], repository_root: Path) -> HookDecision:
    tasks = _state(repository_root)["tasks"]
    transient = sorted(
        task_id for task_id, record in tasks.items() if record.get("status") in {"in_progress", "verifying", "review"}
    )
    if transient:
        return _deny(f"Cannot stop with unfinished runtime state: {', '.join(transient)}")
    return HookDecision()


def _instrument_root() -> Path | None:
    """The plugin directory this guard is running from, if it can be established.

    With CI gone, this is what carries the framework's first principle:

        The instrument that decides a criterion is not authored in the context that
        satisfies it.

    CI enforced that with a clean room. Here it is enforced by location — the scripts that
    run held-out commands, hash stdout, and pin the commit live under the plugin root,
    which is outside the repository the session is editing. An implementing session may
    write anywhere in the working tree and still cannot reach the instrument that judges
    it, *provided* it cannot edit the plugin either. This is the check that makes that
    proviso true.

    `CLAUDE_PLUGIN_ROOT` is preferred because it is what the host actually set. The
    fallback walks up from this file so the guard still holds when the framework is run
    from its own checkout, where the plugin root and the repository root coincide — in
    that case the deny below is skipped, because refusing every edit to this repository
    would make the framework undevelopable.
    """
    declared = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if declared:
        try:
            return Path(declared).resolve()
        except OSError:
            return None
    return Path(__file__).resolve().parents[2]


def _pre_tool_use(payload: dict[str, Any], repository_root: Path) -> HookDecision:
    tool_name = payload.get("tool_name")
    if tool_name == "ExitPlanMode":
        return _exit_plan_mode(payload, repository_root)
    if tool_name == "Task":
        return _task_created(payload, repository_root)
    if tool_name not in {"Write", "Edit", "NotebookEdit"}:
        return HookDecision()
    tool_input = payload.get("tool_input", {})
    target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    # Both forms are checked: the canonical one catches aliases, the raw one keeps working
    # if resolution itself fails or a link points somewhere unexpected.
    normalized = _canonical(target, repository_root)
    if _is_runner_owned(normalized) or _is_runner_owned(_normalized(target)):
        # Name what produces the file, so the refusal says what to run instead.
        return _deny(
            "Direct edit denied: this file is written only by the runner (runner state, evidence, or "
            "traceability.json). Use the command that produces it — `verify-task` captures evidence, `trace` "
            "regenerates traceability.json — and never edit runner state; `/aisdlc:status` shows the next action."
        )
    specification = re.search(r"/(i\d{3,})-[^/]+/(02-specification/.*)$", normalized)
    if specification:
        from .authoring import specification_lock

        lock = specification_lock(_state(repository_root), specification.group(1).upper(), specification.group(2))
        if lock:
            return _deny(f"Direct edit denied: {lock}")

    # The instrument boundary. Skipped when the plugin root *is* the repository under
    # edit, which is the framework developing itself; everywhere else, a session may not
    # rewrite the code that judges it.
    instrument = _instrument_root()
    if instrument is not None and instrument != repository_root.resolve():
        try:
            resolved = Path(target).resolve()
        except (OSError, ValueError):
            resolved = None
        if resolved is not None and resolved.is_relative_to(instrument):
            return _deny(
                "Direct edit denied: the lifecycle instrument is not editable from a session it judges. "
                "Change it in the framework's own repository and reinstall the plugin."
            )
    return HookDecision()


def _post_tool_use(payload: dict[str, Any], repository_root: Path) -> HookDecision:
    if payload.get("tool_name") != "Task":
        return HookDecision()
    return _task_completed(payload, repository_root)


def hook_main(event: str) -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # A hook invoked without a payload has nothing to judge. Exiting 0 keeps a
        # malformed dispatch from blocking every tool call in the session.
        return 0
    if not isinstance(payload, dict):
        return 0
    repository_root = Path(payload.get("cwd", ".")).resolve()
    decision = evaluate_hook(event, payload, repository_root)
    if decision.stdout:
        print(decision.stdout)
    if decision.stderr:
        print(decision.stderr, file=sys.stderr)
    return decision.exit_code


def hook_cli(event: str) -> int:
    """Entry point for `ai-sdlc hook <event>`, reached without importing the CLI (R3.1).

    Validation lives here rather than in Typer so the fast path stays honest: an unknown
    event is still rejected with the same exit code and the same message, it just no
    longer costs a full application import to say so.
    """
    if event not in SUPPORTED_EVENTS:
        print(
            f"unsupported hook event: {event}; expected one of {', '.join(SUPPORTED_EVENTS)}",
            file=sys.stderr,
        )
        return 2
    return hook_main(event)
