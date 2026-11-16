from __future__ import annotations

from pathlib import Path

import pytest

from aisdlc.hooks import evaluate_hook
from aisdlc.state import StateStore


@pytest.fixture(autouse=True)
def lifecycle_managed(tmp_path: Path) -> None:
    """Every guard in this module is about a repository that adopted the lifecycle.

    The guards are inert everywhere else - see
    `test_p07_packaging.py::test_guards_are_inert_in_a_repository_that_never_adopted_the_lifecycle`
    - so a fixture has to state that adoption explicitly.
    """
    config = tmp_path / ".ai-sdlc" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("version: 1\n", encoding="utf-8")


def test_pre_tool_use_blocks_runner_owned_and_frozen_paths_for_windows_and_posix(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", actor="runner")
    store.transition_intent("I001", "specified", actor="runner")
    store.approve_gate("I001", "G2", "human")
    for path in (
        str(tmp_path / ".ai-sdlc/runtime/state.json").replace("/", "\\"),
        str(tmp_path / "delivery/intents/I001-example/traceability.json"),
        str(tmp_path / "delivery/intents/I001-example/04-build/I001-P00/tasks/I001-P00-T001/evidence/out.txt"),
    ):
        decision = evaluate_hook("pre-tool-use", {"tool_name": "Write", "tool_input": {"file_path": path}}, tmp_path)
        assert decision.exit_code == 2
        assert "written only by the runner" in decision.stderr
    frozen = str(tmp_path / "delivery/intents/I001-example/02-specification/spec.md")
    decision = evaluate_hook("pre-tool-use", {"tool_name": "Write", "tool_input": {"file_path": frozen}}, tmp_path)
    assert decision.exit_code == 2
    # The refusal names the Intent, the gate and the exact command that lifts the lock.
    assert "I001's specification was approved at G2" in decision.stderr
    assert "ai-sdlc gate reopen I001 G2" in decision.stderr


def test_task_created_requires_permanent_subject_and_durable_task(tmp_path: Path) -> None:
    invalid = evaluate_hook("task-created", {"task_subject": "Implement status"}, tmp_path)
    assert invalid.exit_code == 2
    task = tmp_path / "delivery/intents/I001-example/04-build/I001-P00/tasks/I001-P00-T001/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text("id: I001-P00-T001\n", encoding="utf-8")
    valid = evaluate_hook("task-created", {"task_subject": "[I001-P00-T001] Implement status"}, tmp_path)
    assert valid.exit_code == 0


def test_task_completed_requires_runner_verified_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.initialize_task("I001-P00-T001")
    blocked = evaluate_hook("task-completed", {"task_subject": "[I001-P00-T001] Implement status"}, tmp_path)
    assert blocked.exit_code == 2
    for target in ("ready", "in_progress", "verifying", "review", "verified"):
        store.transition_task("I001-P00-T001", target, actor="runner")
    allowed = evaluate_hook("task-completed", {"task_subject": "[I001-P00-T001] Implement status"}, tmp_path)
    assert allowed.exit_code == 0


def test_exit_plan_mode_requires_permanent_and_durable_task_ids(tmp_path: Path) -> None:
    decision = evaluate_hook("exit-plan-mode", {"tool_input": {"plan": "Implement the feature"}}, tmp_path)
    assert decision.exit_code == 2
    assert "permanent Task identifier" in decision.stderr
    missing = evaluate_hook("exit-plan-mode", {"tool_input": {"plan": "I001-P00-T001"}}, tmp_path)
    assert missing.exit_code == 2
    task = tmp_path / "delivery/intents/I001-example/04-build/I001-P00/tasks/I001-P00-T001/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text("id: I001-P00-T001\n", encoding="utf-8")
    valid = evaluate_hook("exit-plan-mode", {"tool_input": {"plan": "I001-P00-T001"}}, tmp_path)
    assert valid.exit_code == 0


def test_stop_blocks_transient_runtime_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.initialize_task("I001-P00-T001")
    store.transition_task("I001-P00-T001", "ready", actor="runner")
    store.transition_task("I001-P00-T001", "in_progress", actor="runner")
    decision = evaluate_hook("stop", {}, tmp_path)
    assert decision.exit_code == 2
    assert "unfinished runtime state" in decision.stderr


def test_pre_tool_use_routes_task_tool_to_creation_check(tmp_path: Path) -> None:
    """Claude Code has no TaskCreated event; delegation is a PreToolUse call on the Task tool."""
    payload = {"tool_name": "Task", "tool_input": {"description": "Implement status", "prompt": "do the work"}}
    invalid = evaluate_hook("pre-tool-use", payload, tmp_path)
    assert invalid.exit_code == 2
    assert "permanent [Ixxx-Pxx-Txxx] identifier" in invalid.stderr

    task = tmp_path / "delivery/intents/I001-example/04-build/I001-P00/tasks/I001-P00-T001/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text("id: I001-P00-T001\n", encoding="utf-8")
    payload["tool_input"]["description"] = "[I001-P00-T001] Implement status"
    assert evaluate_hook("pre-tool-use", payload, tmp_path).exit_code == 0


def test_post_tool_use_blocks_unverified_task_and_ignores_other_tools(tmp_path: Path) -> None:
    """Claude Code has no TaskCompleted event; completion is a PostToolUse call on the Task tool."""
    store = StateStore(tmp_path)
    store.initialize_task("I001-P00-T001")
    payload = {"tool_name": "Task", "tool_input": {"description": "[I001-P00-T001] Implement status"}}
    blocked = evaluate_hook("post-tool-use", payload, tmp_path)
    assert blocked.exit_code == 2
    assert "Runner verification is required" in blocked.stderr

    assert evaluate_hook("post-tool-use", {"tool_name": "Bash", "tool_input": {}}, tmp_path).exit_code == 0

    for target in ("ready", "in_progress", "verifying", "review", "verified"):
        store.transition_task("I001-P00-T001", target, actor="runner")
    assert evaluate_hook("post-tool-use", payload, tmp_path).exit_code == 0


def test_task_identifier_is_read_from_prompt_when_description_omits_it(tmp_path: Path) -> None:
    task = tmp_path / "delivery/intents/I001-example/04-build/I001-P00/tasks/I001-P00-T001/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text("id: I001-P00-T001\n", encoding="utf-8")
    payload = {"tool_name": "Task", "tool_input": {"description": "work", "prompt": "[I001-P00-T001] context"}}
    assert evaluate_hook("pre-tool-use", payload, tmp_path).exit_code == 0


def _write(tmp_path: Path, file_path: str):
    return evaluate_hook("pre-tool-use", {"tool_name": "Write", "tool_input": {"file_path": file_path}}, tmp_path)


@pytest.mark.parametrize(
    "alias",
    [
        ".ai-sdlc/runtime./state.json",  # Win32 strips trailing dots
        ".ai-sdlc./runtime/state.json",
        ".ai-sdlc/runtime /state.json",  # ...and trailing spaces
        ".AI-SDLC/Runtime/state.json",  # case-insensitive filesystems
        ".ai-sdlc/runtime::$INDEX_ALLOCATION/state.json",  # NTFS stream syntax
        "delivery/../.ai-sdlc/runtime/state.json",  # traversal
        "delivery/intents/I001-x/04-build/t/evidence./out.txt",
    ],
)
def test_runner_owned_check_sees_through_path_aliases(tmp_path: Path, alias: str) -> None:
    """Release review finding: the check matched raw path text, so OS-level aliases passed."""
    (tmp_path / ".ai-sdlc" / "runtime").mkdir(parents=True, exist_ok=True)
    decision = _write(tmp_path, str(tmp_path / alias))
    assert decision.exit_code == 2, alias


def test_runner_owned_check_follows_links_into_runtime(tmp_path: Path) -> None:
    runtime = tmp_path / ".ai-sdlc" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    link = tmp_path / "innocent"
    try:
        link.symlink_to(runtime, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privilege on this platform")
    assert _write(tmp_path, str(link / "state.json")).exit_code == 2


def test_ordinary_files_are_still_writable(tmp_path: Path) -> None:
    for path in ("src/app.py", "docs/evidence-guide.md", "notes/runtime/x.md", "a.b./c.py".replace("./", "/")):
        assert _write(tmp_path, str(tmp_path / path)).exit_code == 0, path


def test_a_folder_named_evidence_outside_04_build_is_ordinary(tmp_path: Path) -> None:
    """Final release review: any `/evidence/` matched, including user folders and ancestors."""
    assert _write(tmp_path, str(tmp_path / "docs" / "evidence" / "notes.md")).exit_code == 0
    nested = tmp_path / "evidence" / "project"
    (nested / ".ai-sdlc").mkdir(parents=True)
    (nested / ".ai-sdlc" / "config.yaml").write_text("version: 1\n", encoding="utf-8")
    assert _write(nested, str(nested / "src" / "app.py")).exit_code == 0
    # ...while real evidence under 04-build stays runner-owned.
    real = nested / "delivery/intents/I001-x/04-build/I001-P00/tasks/I001-P00-T001/evidence/m.json"
    assert _write(nested, str(real)).exit_code == 2


def test_relative_paths_resolve_against_the_repository(tmp_path: Path, monkeypatch) -> None:
    """Final release review: a relative path was resolved against the hook's cwd."""
    elsewhere = tmp_path.parent / (tmp_path.name + "-cwd")
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert _write(tmp_path, ".ai-sdlc/runtime/state.json").exit_code == 2
    assert _write(tmp_path, "src/app.py").exit_code == 0
