"""P07 - local plugin packaging, bootstrap, and configurable delivery root.

Covers the original packaging gaps: G1 (`init`/`doctor`),
G2 (configurable `delivery_root`), G3 (plugin manifest and layout), G4 (slash commands),
and G5 (asset resolution independent of a project-local dependency).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from aisdlc import scaffold
from aisdlc.assets import AssetError, asset_path, framework_root, schema_path
from aisdlc.config import ConfigError, delivery_root, load_config
from aisdlc.hooks import evaluate_hook
from aisdlc.scaffold import HOOK_EVENTS, STANDING_SUITES, DoctorService, InitService
from aisdlc.vendor import yaml

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_ambient_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the default: these tests describe a machine with no aisdlc plugin installed.

    `init` and `doctor` now branch on whether the plugin already supplies the hooks, and
    that is read from the real `~/.claude/plugins`. Without this the suite would pass or
    fail depending on what the developer running it happens to have installed.
    """
    monkeypatch.setattr(scaffold, "plugin_installed", lambda: False)


def _write_config(root: Path, **values: object) -> None:
    (root / ".ai-sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".ai-sdlc" / "config.yaml").write_text(yaml.safe_dump({"version": 1, **values}), encoding="utf-8")


# --------------------------------------------------------------------------------------
# G2 - configurable delivery root
# --------------------------------------------------------------------------------------


def test_delivery_root_defaults_when_unconfigured(tmp_path: Path) -> None:
    assert delivery_root(tmp_path) == tmp_path / "delivery/intents"


def test_delivery_root_honours_configuration(tmp_path: Path) -> None:
    _write_config(tmp_path, delivery_root="work/lifecycle")
    assert delivery_root(tmp_path) == tmp_path / "work/lifecycle"


def test_durable_task_hook_finds_tasks_under_a_relocated_delivery_root(tmp_path: Path) -> None:
    """The defect G2 describes: the hook reported a missing Task instead of finding it."""
    _write_config(tmp_path, delivery_root="work/lifecycle")
    task = tmp_path / "work/lifecycle/I001-x/04-build/I001-P00/tasks/I001-P00-T001/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text("id: I001-P00-T001\n", encoding="utf-8")

    decision = evaluate_hook("task-created", {"task_subject": "[I001-P00-T001] Implement"}, tmp_path)
    assert decision.exit_code == 0, decision.stderr


def test_hook_fails_closed_on_unreadable_config(tmp_path: Path) -> None:
    (tmp_path / ".ai-sdlc").mkdir(parents=True)
    (tmp_path / ".ai-sdlc" / "config.yaml").write_text("delivery_root: [not, a, string]\n", encoding="utf-8")

    decision = evaluate_hook("task-created", {"task_subject": "[I001-P00-T001] Implement"}, tmp_path)
    assert decision.exit_code == 2
    assert "config.yaml is invalid" in decision.stderr


def test_load_config_rejects_unknown_keys(tmp_path: Path) -> None:
    _write_config(tmp_path, not_a_real_key=True)
    with pytest.raises(ConfigError):
        load_config(tmp_path)


# --------------------------------------------------------------------------------------
# G5 - framework asset resolution
# --------------------------------------------------------------------------------------


def test_framework_root_resolves_from_the_development_checkout() -> None:
    assert (framework_root() / "schemas" / "task.schema.json").is_file()


def test_framework_root_override_is_validated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    framework_root.cache_clear()
    monkeypatch.setenv("AISDLC_FRAMEWORK_ROOT", str(tmp_path))
    with pytest.raises(AssetError):
        framework_root()
    framework_root.cache_clear()


def test_repository_copy_wins_over_the_framework_copy(tmp_path: Path) -> None:
    local = tmp_path / ".ai-sdlc" / "schemas" / "task.schema.json"
    local.parent.mkdir(parents=True)
    local.write_text("{}", encoding="utf-8")
    assert schema_path(tmp_path, "task.schema.json") == local
    # A file the repository does not carry still resolves from the framework.
    assert asset_path(tmp_path, "constitution.md").is_file()


# --------------------------------------------------------------------------------------
# G1 - init and doctor
# --------------------------------------------------------------------------------------


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A real git repository: `doctor` shells out to git, so a fake .git will not do."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_init_scaffolds_a_runnable_repository(target: Path) -> None:
    report = InitService(target).run().as_dict()
    statuses = {step["step"]: step["status"] for step in report["steps"]}
    assert statuses[".ai-sdlc/config.yaml"] == "created"
    assert statuses[".ai-sdlc/project-context.md"] == "created"
    assert "failed" not in set(statuses.values())

    for relative in (
        ".ai-sdlc/config.yaml",
        ".ai-sdlc/project-context.md",
        ".ai-sdlc/lessons/registry.yaml",
        ".ai-sdlc/evals/datasets.yaml",
        "CLAUDE.md",
        ".gitignore",
        "delivery/intents/.gitkeep",
        ".ai-sdlc/runtime/state.json",
    ):
        assert (target / relative).exists(), relative

    # Framework-owned assets are inherited, never copied.
    assert not (target / ".ai-sdlc" / "schemas").exists()
    assert not (target / ".ai-sdlc" / "constitution.md").exists()


def test_init_writes_a_neutral_project_context_not_the_frameworks_own(target: Path) -> None:
    InitService(target).run()
    text = (target / ".ai-sdlc" / "project-context.md").read_text(encoding="utf-8")
    assert "src/aisdlc/" not in text, "the framework's own conventions leaked into a target repository"
    assert "Describe this repository" in text


def test_init_is_idempotent(target: Path) -> None:
    InitService(target).run()
    claude_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
    second = InitService(target).run().as_dict()

    statuses = {step["step"]: step["status"] for step in second["steps"]}
    assert statuses["CLAUDE.md"] == "skipped"
    assert statuses[".ai-sdlc/config.yaml"] == "skipped"
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == claude_md
    assert statuses[".claude/settings.json"] == "skipped"


def test_init_preserves_unrelated_settings_and_claude_md_content(target: Path) -> None:
    (target / ".claude").mkdir()
    (target / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"KEEP": "me"}, "hooks": {"SessionStart": [{"hooks": []}]}}), encoding="utf-8"
    )
    (target / "CLAUDE.md").write_text("# House rules\n\nExisting content.\n", encoding="utf-8")

    InitService(target).run()

    # ADR-007: `init` does not touch settings.json at all now, so an existing one must
    # come back byte-for-byte — including hooks it did not write.
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["env"] == {"KEEP": "me"}
    assert settings["hooks"] == {"SessionStart": [{"hooks": []}]}
    assert "Existing content." in (target / "CLAUDE.md").read_text(encoding="utf-8")


def test_init_does_not_write_hook_entries(target: Path) -> None:
    """ADR-007: the plugin registers the guards for every repository.

    `init` used to merge four `ai-sdlc hook <event>` entries into the target's
    `.claude/settings.json`. There is no `ai-sdlc` on PATH to call any more, so a project
    entry could only name a runner that does not exist — and two sets of hooks meant every
    check ran twice.
    """
    report = InitService(target).run().as_dict()
    statuses = {step["step"]: step["status"] for step in report["steps"]}
    assert statuses[".claude/settings.json"] == "skipped"
    assert not (target / ".claude" / "settings.json").exists()


def test_init_registers_every_standing_suite(target: Path) -> None:
    InitService(target).run()
    registered = yaml.safe_load((target / ".ai-sdlc" / "evals" / "datasets.yaml").read_text(encoding="utf-8"))
    for name, _, trigger in STANDING_SUITES:
        assert registered["datasets"][name]["trigger"] == trigger


def test_init_records_the_requested_artifact_mode(target: Path) -> None:
    InitService(target).run(mode="full")
    assert load_config(target).default_artifact_mode == "full"


def test_init_rejects_an_unknown_mode(target: Path) -> None:
    with pytest.raises(ValueError):
        InitService(target).run(mode="enormous")


def test_doctor_reports_ok_for_an_initialized_repository(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A freshly scaffolded repository is ready, with two honest warnings.

    `hooks` warns because a bare temp directory has no plugin installed — under ADR-007
    that is where the guards come from, so saying "wired" would be a lie. `delivery-root`
    and `runtime-state` warn until the first Intent. Nothing may *fail*.
    """
    InitService(target).run()
    monkeypatch.setattr(scaffold, "plugin_installed", lambda: True)
    report = DoctorService(target).run()
    statuses = {check["check"]: check["status"] for check in report["checks"]}
    assert "fail" not in statuses.values(), report["checks"]
    assert statuses["runner"] == "ok"
    assert statuses["hooks"] == "ok"
    assert statuses["instrument-boundary"] == "ok"
    # The checks that CI paid for are gone, not silently passing.
    assert "ci-authority" not in statuses
    assert "attestation" not in statuses


def test_doctor_fails_without_initialization(tmp_path: Path) -> None:
    report = DoctorService(tmp_path).run()
    assert report["status"] == "fail"
    failures = {check["check"] for check in report["checks"] if check["status"] == "fail"}
    assert {"config", "git"} <= failures


def test_doctor_flags_hooks_still_wired_to_the_uninstalled_runner(target: Path) -> None:
    """A repository initialised before ADR-007 still carries `ai-sdlc hook` entries.

    Those call a name that is no longer installed. Reporting them as *unwired* would be
    misleading — they are wired, to nothing — so `doctor` names the cause.
    """
    InitService(target).run()
    settings_path = target / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "ai-sdlc hook stop"}]}]}}),
        encoding="utf-8",
    )
    check = next(c for c in DoctorService(target).run()["checks"] if c["check"] == "hooks")
    assert check["status"] == "warn"
    assert "no longer installed" in check["detail"]


def test_doctor_warns_when_runtime_state_is_not_ignored(target: Path) -> None:
    InitService(target).run()
    (target / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    report = DoctorService(target).run()
    check = next(c for c in report["checks"] if c["check"] == "gitignore")
    assert check["status"] == "warn"
    assert "breaks convergence" in check["detail"]


# --------------------------------------------------------------------------------------
# G3 and G4 - plugin bundle
# --------------------------------------------------------------------------------------


def test_plugin_manifest_declares_identity_and_leaves_components_to_convention() -> None:
    """The manifest must not name component paths that the layout already provides.

    Claude Code auto-discovers `commands/`, `agents/`, `skills/`, and `hooks/hooks.json`.
    Declaring them again buys nothing and costs correctness: `agents` is validated as a
    path to a *file*, so `"agents": "./agents"` is rejected outright with
    `agents: Invalid input` and the whole plugin fails to install. `commands` and `agents`
    also *replace* the default scan rather than adding to it, so a stale entry silently
    drops components instead of erroring.
    """
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "aisdlc"
    assert manifest["description"]
    assert manifest["version"]
    for field in ("commands", "agents", "skills", "hooks"):
        assert field not in manifest, f"{field} is auto-discovered; declaring it can only break the install"


def test_the_conventional_component_directories_are_the_ones_that_exist() -> None:
    """Convention is only safe while the layout actually matches it."""
    assert (REPO / "commands").is_dir()
    assert sorted(path.name for path in (REPO / "agents").glob("*.md"))
    assert (REPO / "skills" / "aisdlc" / "SKILL.md").is_file()
    assert (REPO / "hooks" / "hooks.json").is_file()


def test_marketplace_lists_the_plugin_from_a_root_relative_source() -> None:
    marketplace = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    # The marketplace name is a registry key, fixed on this machine at first
    # registration: `/plugin marketplace add` matches an existing entry by source path,
    # so renaming it here does not re-key an install that already exists. It is not worth
    # churning - it identifies the marketplace, not what the plugin can be used for.
    assert marketplace["name"] == "aisdlc-local"
    assert marketplace["owner"]["name"]
    assert [plugin["name"] for plugin in marketplace["plugins"]] == ["aisdlc"]
    # `./` resolves against the marketplace root, so the same descriptor works whether the
    # marketplace was added by filesystem path or from a git remote. A source pinned to a
    # local absolute path would only ever install on the author's machine.
    assert marketplace["plugins"][0]["source"] == "./"


def test_plugin_hooks_route_through_the_polyglot_launcher() -> None:
    """Supersedes the ADR-0005 contract this test used to guard.

    Hooks no longer resolve `ai-sdlc` by bare name on PATH. They name an absolute path
    under `${CLAUDE_PLUGIN_ROOT}`, which the official `claude-security` plugin does in
    `hooks.json` and in skill frontmatter, and which removes the hijack that ADR-0006
    existed to defend against: there is no bare name left to shadow.

    Every event goes through `run-hook.cmd`, a polyglot that dispatches under cmd.exe and
    under any POSIX shell, so the command string needs no `shell` key and works whether
    Claude Code hands it to a shell or to the Windows command processor.
    """
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    for event, matcher, subcommand in HOOK_EVENTS:
        entries = hooks[event]
        assert len(entries) == 1
        if matcher is not None:
            assert entries[0]["matcher"] == matcher
        command = entries[0]["hooks"][0]["command"]
        assert "${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" in command
        assert command.endswith(f"guard {subcommand}")
        # Nothing may depend on the runner being installed.
        assert "ai-sdlc " not in command
        assert "uv run" not in command
        # The target is extensionless on purpose: Claude Code's Windows auto-detection
        # prepends `bash` to a `.sh` command, which would re-enter the launcher.
        assert (REPO / "hooks" / "guard").exists()
        assert not (REPO / "hooks" / "guard.sh").exists()


def test_every_lifecycle_operation_has_a_command() -> None:
    expected = {
        # Core lifecycle path.
        "init",
        "doctor",
        "status",
        "start",
        "specify",
        "plan",
        "run",
        "verify",
        "close",
        "gate",
        # Gate economics: preparing a decision is what costs, not the gate itself.
        "gate-packet",
        # Brownfield entry: pin current behaviour before writing the first requirement,
        # and build the structural understanding the baseline's probes are written from.
        "baseline",
        "understand",
        # Scale and assurance projections over records the runner already owns.
        "program",
        "conformance",
        # Machine-wide bootstrap: the plugin is installed once, the runner has to reach
        # PATH once, and neither is per-repository work.
    }
    assert {path.stem for path in (REPO / "commands").glob("*.md")} == expected


@pytest.mark.parametrize("command_path", sorted((REPO / "commands").glob("*.md")), ids=lambda p: p.stem)
def test_command_files_declare_frontmatter_and_scoped_tools(command_path: Path) -> None:
    text = command_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---\n", 2)
    parsed = yaml.safe_load(frontmatter)

    assert parsed["description"], "a command without a description cannot be discovered"
    assert parsed["allowed-tools"], "a command must scope its tools"
    assert body.strip()

    # Every command that injects runner state must be allowed to run the runner.
    if "!`ai-sdlc" in body:
        assert "Bash(ai-sdlc" in parsed["allowed-tools"]


def test_skill_and_agents_ship_inside_the_plugin() -> None:
    assert (REPO / "skills" / "aisdlc" / "SKILL.md").is_file()
    for agent in ("aisdlc-verifier", "aisdlc-red-team", "aisdlc-cartographer"):
        assert (REPO / "agents" / f"{agent}.md").is_file()
    # The pre-plugin locations must not linger, or the skill loads twice.
    assert not (REPO / ".claude" / "skills").exists()
    assert not (REPO / ".claude" / "agents").exists()


# --------------------------------------------------------------------------------------
# Machine-wide installation - the plugin is installed once and loads in every repository
# --------------------------------------------------------------------------------------


def test_guards_are_inert_in_a_repository_that_never_adopted_the_lifecycle(tmp_path: Path) -> None:
    """The defect a global install would otherwise introduce everywhere.

    Task delegation and plan mode are denied unless a permanent identifier is present.
    Installed as a plugin those guards load in every repository the user opens, so without
    an adoption marker the plugin would block ordinary work in unrelated projects.
    """
    for event, payload in (
        ("pre-tool-use", {"tool_name": "Task", "tool_input": {"description": "Refactor the parser"}}),
        ("pre-tool-use", {"tool_name": "ExitPlanMode", "tool_input": {"plan": "Rewrite the CLI"}}),
        ("post-tool-use", {"tool_name": "Task", "tool_input": {"description": "Refactor the parser"}}),
        ("stop", {}),
    ):
        decision = evaluate_hook(event, payload, tmp_path)
        assert decision.exit_code == 0, f"{event} blocked an unmanaged repository: {decision.stderr}"


def test_adoption_is_marked_by_the_config_file_alone(tmp_path: Path) -> None:
    payload = {"tool_name": "Task", "tool_input": {"description": "Refactor the parser"}}
    assert evaluate_hook("pre-tool-use", payload, tmp_path).exit_code == 0
    _write_config(tmp_path)
    assert evaluate_hook("pre-tool-use", payload, tmp_path).exit_code == 2


def test_guards_can_be_suspended_by_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    payload = {"tool_name": "Task", "tool_input": {"description": "Refactor the parser"}}
    assert evaluate_hook("pre-tool-use", payload, tmp_path).exit_code == 2
    monkeypatch.setenv("AISDLC_HOOKS", "off")
    assert evaluate_hook("pre-tool-use", payload, tmp_path).exit_code == 0


def test_doctor_fails_when_no_usable_interpreter_is_on_path(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The one thing the plugin still needs from the machine is a python3.

    This replaces the check that asked whether `ai-sdlc` resolved on PATH. Nothing is
    installed on PATH now, so the failure that actually breaks a session is an interpreter
    the launcher cannot find.
    """
    InitService(target).run()
    monkeypatch.setattr(scaffold.shutil, "which", lambda _name: None)
    report = DoctorService(target).run()
    check = next(c for c in report["checks"] if c["check"] == "runner")
    assert check["status"] == "fail"
    assert "python3" in check["detail"]
    assert report["status"] == "fail"


def test_doctor_reports_the_interpreter_the_launcher_would_pick(target: Path) -> None:
    """`doctor` must answer for the launcher's interpreter, not for its own.

    `doctor` may run under a different python than a hook would find — a project venv, say.
    Reporting `sys.version_info` would answer the wrong question, so the check probes the
    first of `python3`, `python`, `py` on PATH and reports what that one says.
    """
    InitService(target).run()
    check = next(c for c in DoctorService(target).run()["checks"] if c["check"] == "runner")
    assert check["status"] == "ok"
    assert re.search(r"\(\d+\.\d+\.\d+\)$", check["detail"]), check["detail"]


def test_init_leaves_hooks_to_the_plugin_when_one_is_installed(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scaffold, "plugin_installed", lambda: True)
    report = InitService(target).run().as_dict()
    step = next(s for s in report["steps"] if s["step"] == ".claude/settings.json")
    assert step["status"] == "skipped"
    assert "plugin" in step["detail"]
    assert not (target / ".claude" / "settings.json").exists()


def test_init_writes_project_hooks_only_when_asked(target: Path) -> None:
    """`--hooks project` is the escape hatch, and it writes the launcher form.

    ADR-007 moved the guards into the plugin, so `auto` writes nothing. A repository that
    deliberately pins its own copy can still ask for project entries — and what it gets
    must be the same plugin-rooted launcher call the plugin itself uses, never a bare
    `ai-sdlc` that is not installed.
    """
    InitService(target).run(hooks="project")
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"] for entries in settings["hooks"].values() for entry in entries for hook in entry["hooks"]
    ]
    assert len(commands) == len(HOOK_EVENTS)
    for command in commands:
        assert "run-hook.cmd" in command
        assert "${CLAUDE_PLUGIN_ROOT}" in command
        assert not command.startswith("ai-sdlc")


def test_init_hooks_mode_can_be_forced_either_way(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scaffold, "plugin_installed", lambda: True)
    InitService(target).run(hooks="project")
    assert (target / ".claude" / "settings.json").exists()

    other = target / "nested"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True, capture_output=True)
    monkeypatch.setattr(scaffold, "plugin_installed", lambda: False)
    InitService(other).run(hooks="none")
    assert not (other / ".claude" / "settings.json").exists()


def test_init_rejects_an_unknown_hooks_mode(target: Path) -> None:
    with pytest.raises(ValueError):
        InitService(target).run(hooks="sometimes")


def test_doctor_warns_when_the_plugin_and_the_repository_both_wire_the_hooks(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--hooks project` is still an escape hatch, and still double-fires with the plugin."""
    InitService(target).run(hooks="project")
    monkeypatch.setattr(scaffold, "plugin_installed", lambda: True)
    check = next(c for c in DoctorService(target).run()["checks"] if c["check"] == "hooks")
    assert check["status"] == "warn"
    assert "runs twice" in check["detail"]


def test_doctor_accepts_hooks_supplied_only_by_the_plugin(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    InitService(target).run(hooks="none")
    monkeypatch.setattr(scaffold, "plugin_installed", lambda: True)
    check = next(c for c in DoctorService(target).run()["checks"] if c["check"] == "hooks")
    assert check["status"] == "ok"
    assert "plugin" in check["detail"]
