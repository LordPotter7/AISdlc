"""Tests for the plugin surface itself, not for the runner behind it.

The runner suite exercises Python APIs. None of it touches the things this migration
actually changed: whether a command's frontmatter names a path that exists, whether a
hook target is reachable, whether anything still expects `ai-sdlc` on `PATH`, whether a
runtime module quietly imports something that is not installed.

Those failures do not show up in a unit test. They show up in a user's session, once, as
a hook that silently never fires. `superpowers` keeps a `tests/` tree for exactly this
reason; this is ours.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
RUNTIME_DIRS = ("commands", "hooks", "skills", "scripts", "agents")

# Directories a plugin ships but never executes from a user session.
MAINTENANCE_ONLY = "tools"

# `tools/` as a filesystem path, not as a JSON-RPC method name such as "tools/list".
_TOOLS_PATH = re.compile(r"(?<![\w/])tools/[\w.-]+\.(?:py|md|json|sh|cmd)")


def _plugin_files(*subdirs: str, suffix: str | None = None) -> list[Path]:
    found: list[Path] = []
    for subdir in subdirs:
        root = PLUGIN / subdir
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if suffix is None or path.suffix == suffix:
                found.append(path)
    return found


def _frontmatter(path: Path) -> dict[str, str]:
    """Parse the leading `---` block. Deliberately not a YAML parser.

    Command frontmatter is a flat block of `key: value` lines; reading it with a real
    parser would mean importing one, and this test must be able to run before the
    vendored parser is trusted.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---")
    block, _, _ = rest.partition("\n---")
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


# -- entry points --------------------------------------------------------------------


def test_every_command_has_frontmatter_with_a_description() -> None:
    commands = _plugin_files("commands", suffix=".md")
    assert commands, "no commands found"
    for path in commands:
        fields = _frontmatter(path)
        assert fields, f"{path.name}: no frontmatter block"
        assert fields.get("description"), f"{path.name}: no description"


def test_plugin_root_references_resolve() -> None:
    """`${CLAUDE_PLUGIN_ROOT}/x` must name a path this plugin actually ships.

    A typo here is invisible: the variable expands, the path does not exist, and the
    command silently does nothing.
    """
    pattern = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")
    missing: list[str] = []
    for path in _plugin_files(*RUNTIME_DIRS):
        if path.suffix not in {".md", ".json", ".cmd", ""} and path.suffix != ".py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for relative in pattern.findall(text):
            target = PLUGIN / relative.rstrip("/*")
            if not target.exists():
                missing.append(f"{path.relative_to(PLUGIN)} -> {relative}")
    assert not missing, "unresolvable ${CLAUDE_PLUGIN_ROOT} references:\n  " + "\n  ".join(missing)


def test_nothing_invokes_a_bare_runner() -> None:
    """No shipped file may call `ai-sdlc` as a bare name on PATH.

    That is the entire point of the migration: the plugin calls its own scripts by
    absolute path, so there is nothing to install and nothing to hijack.
    """
    bare = re.compile(r"(?<![\w/\\\"'.-])ai-sdlc\s+[a-z]")
    offenders: list[str] = []
    for path in _plugin_files(*RUNTIME_DIRS):
        if path.suffix not in {".md", ".json", ".py", ".cmd", ""}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # Prose and headings may name the command; executable positions may not.
            if stripped.startswith((">", "|", "#", "-", "*")) or "`ai-sdlc" in line:
                continue
            if bare.search(line):
                offenders.append(f"{path.relative_to(PLUGIN)}:{number}: {stripped[:90]}")
    assert not offenders, "bare `ai-sdlc` invocations remain:\n  " + "\n  ".join(offenders)


# -- hooks ---------------------------------------------------------------------------


def test_hook_targets_exist_and_are_extensionless() -> None:
    """Every target named in hooks.json resolves, and none ends in `.sh`.

    The extension matters: Claude Code's Windows auto-detection prepends `bash` to a
    `.sh` command, which would re-enter the launcher instead of running the target.
    """
    config = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    seen = 0
    for entries in config["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                command = hook["command"]
                assert "${CLAUDE_PLUGIN_ROOT}" in command, f"hook is not plugin-rooted: {command}"
                assert "run-hook.cmd" in command, f"hook bypasses the launcher: {command}"
                target = command.rsplit('run-hook.cmd"', 1)[-1].split()[0]
                path = PLUGIN / "hooks" / target
                assert path.exists(), f"hook target does not exist: {target}"
                assert path.suffix == "", f"hook target must be extensionless: {target}"
                seen += 1
    assert seen >= 4, "expected a guard on each lifecycle event"


def test_launcher_is_a_valid_posix_script() -> None:
    """The shell half of the polyglot must parse, and must exec before the batch half."""
    launcher = PLUGIN / "hooks" / "run-hook.cmd"
    text = launcher.read_text(encoding="utf-8")
    shell_lines = [line for line in text.splitlines() if line.startswith(":;")]
    assert shell_lines, "no shell branch found"
    assert any("exec" in line for line in shell_lines), "shell branch never execs"
    # Everything cmd.exe runs must come after the shell branch has exec'd away.
    first_batch = next(i for i, line in enumerate(text.splitlines()) if line.startswith("@echo off"))
    last_shell = max(i for i, line in enumerate(text.splitlines()) if line.startswith(":;"))
    assert last_shell < first_batch, "batch branch precedes the shell exec"


def test_launcher_batch_branch_is_well_formed() -> None:
    launcher = (PLUGIN / "hooks" / "run-hook.cmd").read_text(encoding="utf-8")
    assert "@echo off" in launcher
    assert "%~dp0" in launcher, "batch branch does not resolve its own directory"
    assert "exit /b" in launcher, "batch branch does not propagate an exit code"
    assert "Git\\bin\\bash.exe" in launcher, "batch branch does not probe the Git Bash install"


# -- runtime hygiene -----------------------------------------------------------------


def _module_level_imports(path: Path) -> set[str]:
    """Top-level imports only.

    A module-level import of a package that is not installed crashes the session at
    import time. A function-local import inside `try/except ImportError` is the correct
    way to offer an optional integration — `adapters.py` does exactly that for the
    `anthropic` client, and names the `command` adapter as the fallback — so it is not a
    finding.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_runtime_is_stdlib_only() -> None:
    """Nothing the plugin executes may import a package that is not installed.

    `pip` never runs for a plugin, so a third-party import is not a missing dependency —
    it is a crash in the user's session with no way to fix it from inside Claude Code.
    """
    allowed = set(sys.stdlib_module_names) | {"aisdlc", "lib", "vendor", "yaml"}
    offenders: list[str] = []
    for path in _plugin_files("scripts", "hooks", suffix=".py"):
        if "vendor" in path.parts:
            continue
        for name in _module_level_imports(path) - allowed:
            offenders.append(f"{path.relative_to(PLUGIN)}: {name} (module level)")
    assert not offenders, "non-stdlib imports in runtime code:\n  " + "\n  ".join(offenders)


def test_vendored_code_is_pure_python() -> None:
    """A compiled artifact under vendor/ would break the no-build-step promise."""
    vendor = PLUGIN / "scripts" / "aisdlc" / "vendor"
    if not vendor.exists():
        pytest.skip("no vendored packages")
    compiled = [
        path.relative_to(PLUGIN) for path in vendor.rglob("*") if path.suffix in {".so", ".pyd", ".dll", ".dylib"}
    ]
    assert not compiled, f"compiled artifacts under vendor/: {compiled}"
    assert (vendor / "README.md").exists(), "vendor/ must record provenance"


def test_runtime_never_reaches_into_tools() -> None:
    """`scripts/` is runtime, `tools/` is maintenance. The boundary is one-way.

    `claude-security` puts runtime code in `scripts/`; `superpowers` and `ponytail` put
    maintenance scripts there. Because the two conventions collide, this repository
    states the rule and enforces it rather than trusting a directory name.
    """
    offenders: list[str] = []
    for path in _plugin_files(*RUNTIME_DIRS):
        if path.suffix not in {".md", ".json", ".py", ".cmd", ""}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _TOOLS_PATH.search(line) and not line.lstrip().startswith(("#", "-", ">", "*", "|")):
                offenders.append(f"{path.relative_to(PLUGIN)}:{number}: {line.strip()[:90]}")
    assert not offenders, "runtime code references tools/:\n  " + "\n  ".join(offenders)


# -- release hygiene -----------------------------------------------------------------


def _dotted(payload: object, field: str) -> object:
    current = payload
    for part in field.split("."):
        current = current[int(part)] if part.isdigit() else current[part]  # type: ignore[index]
    return current


def test_declared_versions_agree() -> None:
    """`.version-bump.json` is the replacement for the deleted `check-versions`."""
    manifest = json.loads((PLUGIN / ".version-bump.json").read_text(encoding="utf-8"))
    versions: dict[str, str] = {}
    for entry in manifest["files"]:
        path = PLUGIN / entry["path"]
        assert path.exists(), f"version target missing: {entry['path']}"
        if path.suffix == ".json":
            value = _dotted(json.loads(path.read_text(encoding="utf-8")), entry["field"])
        else:
            match = re.search(
                rf'^{entry["field"].split(".")[-1]}\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M
            )
            assert match, f"no version found in {entry['path']}"
            value = match.group(1)
        versions[entry["path"]] = str(value)
    assert len(set(versions.values())) == 1, f"versions disagree: {versions}"


# -- truth-telling --------------------------------------------------------------------

# Documents a reader or a model acts on *now*. Historical plan documents are excluded on
# purpose: they record what was intended at the time, and rewriting them to match the
# current architecture would falsify the record. The framework's own constitution says to
# preserve failed attempts and historical identifiers; that applies to its own plans.
LIVE_DOCS = (
    "README.md",
    "docs/reference.md",
    ".ai-sdlc/constitution.md",
    "commands",
    "skills",
    "agents",
    "after-install.md",
)

# Claims the code no longer supports. Each maps to what actually happened, so a failure
# names the fix rather than just the offence.
REMOVED_CLAIMS = {
    r"\bverify-ci\b": "CI was removed (ADR-007)",
    r"\brequire_attestation\b": "attestation was removed with CI (ADR-007)",
    r"attestation_provider": "attestation was removed with CI (ADR-007)",
    r"AI_SDLC_CI_AUTHORITY": "the CI authority marker no longer exists (ADR-007)",
    r"/aisdlc:install": "the runner is no longer installed (ADR-007)",
    r"\bAISDLC_WHEEL\b": "no wheel is built or vendored (ADR-007)",
    r"\bAISDLC_HOME\b": "there is no bootstrap directory (ADR-007)",
    r"allow_local_override": "the runner pin went with bare-name resolution (ADR-007)",
    r"authoritative evidence": "evidence has one kind now (ADR-007)",
    r"provisional evidence": "evidence has one kind now (ADR-007)",
    # Added after a manual pass found these surviving the first gate: they name removed
    # machinery without using any of the phrases above.
    r"\.github/workflows": "there is no CI workflow (ADR-007)",
    r"ci-authority": "the ci-authority check became instrument-boundary (ADR-007)",
    r"CI is the authority": "CI was removed (ADR-007)",
    r"ai-sdlc mcp": "the MCP surface was removed (ADR-007)",
    r"ai-sdlc index": "the derived SQLite index was removed (ADR-007)",
    r"ai-sdlc federation": "federation was removed with CI signing (ADR-007)",
    r"attestation envelope": "attestation was removed with CI (ADR-007)",
    r"scripts/install_runner": "the installer was removed (ADR-007)",
    r"scripts/measure_hook_latency": "maintenance scripts live in tools/ (ADR-008)",
}


def _live_files() -> list[Path]:
    found: list[Path] = []
    for entry in LIVE_DOCS:
        target = PLUGIN / entry
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            found.extend(p for p in target.rglob("*.md") if "__pycache__" not in p.parts)
    return found


def test_no_document_claims_a_guarantee_the_code_no_longer_provides() -> None:
    """Phase 3's grep gate, as an assertion rather than a checklist item.

    Removing a capability and leaving the documentation claiming it is worse than not
    removing it: a reader trusts a guarantee nobody is enforcing. This is the check that
    makes the demolition honest.
    """
    offenders: list[str] = []
    for path in _live_files():
        # A shim's whole job is to name the thing that went away.
        if "legacy-command-shims" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            # A line whose subject is a superseded ADR is allowed to name what it settled —
            # that is the record of the change, not a live claim.
            if "0002-ci-owns-authoritative-evidence" in line or "Superseded by" in line:
                continue
            for pattern, reason in REMOVED_CLAIMS.items():
                if re.search(pattern, line, re.IGNORECASE):
                    offenders.append(f"{path.relative_to(PLUGIN)}:{number}: {reason} — {line.strip()[:80]}")
    assert not offenders, "documents still claim removed capabilities:\n  " + "\n  ".join(offenders)


def test_the_build_workflow_declares_its_contract_and_keeps_authority_in_the_runner() -> None:
    """A workflow describes an order; it must never *be* the authority (ADR-001).

    `workflows/build.js` names the Build-stage phases so the loop runs the same way twice.
    Every phase still ends in a runner call — if a command string here stopped naming
    `lifecycle.py`, the workflow would have quietly taken over a decision that belongs to
    the runner.
    """
    source = (PLUGIN / "workflows" / "build.js").read_text(encoding="utf-8")
    assert "export const meta" in source
    assert '"${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py"' in source
    for verb in ("ready-tasks", "start-task", "verify-task", "review-task", "merge-ready", "merge"):
        assert verb in source, f"the build loop does not call {verb}"
    # The stop conditions are the point: a refusal ends the pass rather than being retried.
    assert "stopOn" in source
    assert "conformance gap" in source


def test_the_runner_knows_its_version_without_being_installed() -> None:
    """The regression that only appears in the configuration that is now normal.

    `__version__` used to come from `importlib.metadata.version("ai-sdlc")`, which requires
    an installed distribution. Nothing is installed any more, so that returned
    `0+unknown` — and the compatibility check then refused to manage a repository the
    runner had itself scaffolded. The version now comes from the plugin manifest, which
    ships beside the code and is kept in sync by `.version-bump.json`.
    """
    import aisdlc

    assert aisdlc.__version__ != "0+unknown"
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert aisdlc.__version__ == manifest["version"]


def test_no_runtime_module_reads_installed_package_metadata() -> None:
    """`importlib.metadata` answers for installed distributions. There is no distribution."""
    offenders: list[str] = []
    for path in _plugin_files("scripts", "hooks", suffix=".py"):
        if "vendor" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # Imports, not prose. The module that fixed this names the old call in its
        # docstring, and a substring search would flag the explanation as the offence.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.startswith("importlib.metadata") for name in names):
                offenders.append(str(path.relative_to(PLUGIN)))
    assert not offenders, f"runtime code depends on being pip-installed: {sorted(set(offenders))}"


# -- command injection lines ----------------------------------------------------------

# Injection lines that need an argument the caller supplies. They are expected to refuse a
# bad one; the no-argument case is what this test is about.
_NEEDS_ARGUMENT = ("$1", "$2", "$3", "${1")


def _injection_commands() -> list[tuple[str, str]]:
    """Every `!`…`` line in commands/, as (command file, runner subcommand)."""
    found: list[tuple[str, str]] = []
    for path in _plugin_files("commands", suffix=".md"):
        for raw in re.findall(r"!`([^`]+)`", path.read_text(encoding="utf-8")):
            found.append((path.name, raw))
    return found


def test_argument_free_injection_lines_succeed_on_an_unprepared_repository(tmp_path: Path) -> None:
    """A command must be able to run in the state it exists to fix.

    Slash commands inject a runner call into their own prompt *before* Claude reasons. A
    non-zero exit there makes Claude Code discard the output and report a shell failure, so
    the command fails outright — and the commands most likely to hit it are the ones you
    reach for when something is wrong.

    This is a regression test for three real instances:
      * `understand status` raised when no understanding existed, so `/aisdlc:understand`
        failed on its first run — the only run that matters.
      * `doctor` exited 1 whenever a check failed, so `/aisdlc:doctor` could not run on a
        repository that needed diagnosing.
      * `status` refused an empty Intent id, though its own `argument-hint` declares the
        argument optional.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = str(PLUGIN / "scripts" / "lifecycle.py")

    failures: list[str] = []
    for source, command in _injection_commands():
        if any(token in command for token in _NEEDS_ARGUMENT):
            continue
        argv = shlex.split(command.replace('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py"', ""))
        if not argv:
            continue
        # Run from inside the repository rather than passing `--repo`: that is how a hook
        # or an injection line actually invokes it, and not every command takes the flag.
        result = subprocess.run(
            [sys.executable, runner, *argv],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            first = (result.stderr or result.stdout).strip().splitlines()[:1]
            failures.append(f"{source}: `{' '.join(argv)}` exited {result.returncode} — {first}")
    assert not failures, "injection lines fail on an unprepared repository:\n  " + "\n  ".join(failures)


def test_status_answers_without_an_intent_id(tmp_path: Path) -> None:
    """`/aisdlc:status` declares `[intent-id]` optional; the runner must honour that."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "lifecycle.py"), "status", ""],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 0
    assert "start" in payload["next_action"]


def test_a_config_from_a_previous_runner_still_loads(tmp_path: Path) -> None:
    """An upgrade must not brick a repository that an older runner scaffolded.

    `extra="forbid"` is there to catch a misspelled setting, and it should stay. But the
    first repository upgraded past ADR-007 carried a `runner:` block that this version no
    longer reads, and the whole file was refused — with an unhandled traceback, in a
    command whose job is to report the repository's state.

    Retired settings are ignored and reported; genuinely unknown ones are still refused.
    """
    sys.path.insert(0, str(PLUGIN / "scripts"))
    from aisdlc.config import Config, ConfigError, _build, retired_settings

    aged = {
        "version": 1,
        "delivery_root": "delivery/intents",
        "runner": {"allow_local_override": True},
        "evidence": {"local_authority": "provisional", "require_attestation": False},
        "scale": {"programs_enabled": True, "federation_enabled": False, "index_enabled": True},
    }
    config = _build(Config, aged)
    assert config.delivery_root == "delivery/intents"
    assert config.scale.programs_enabled is True
    assert not hasattr(config, "runner")

    reported = retired_settings(aged)
    assert "runner" in reported
    assert "evidence.require_attestation" in reported
    assert "scale.index_enabled" in reported

    # A typo is still a typo.
    with pytest.raises(ConfigError, match="unknown setting"):
        _build(Config, {"delivry_root": "x"})


def test_plugin_detection_reads_the_registry_not_the_directory_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`doctor` warned that the guards were unwired on a machine where they were wired.

    The old check walked `~/.claude/plugins/<marketplace>/<plugin>` two levels deep. Real
    installs land at `cache/<marketplace>/<plugin>/<version>/` — deeper, and under
    `cache/` — and a **directory-source** marketplace has no copy under `~/.claude` at all,
    because `${CLAUDE_PLUGIN_ROOT}` is the developer's own checkout.

    So detection reads `installed_plugins.json`, the record Claude Code actually keeps.
    """
    sys.path.insert(0, str(PLUGIN / "scripts"))
    from aisdlc import scaffold

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    registry = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)

    registry.write_text(json.dumps({"plugins": {"something-else@market": [{}]}}), encoding="utf-8")
    assert scaffold.plugin_installed() is False

    # A directory-source install: a registry entry and nothing on disk to walk.
    registry.write_text(
        json.dumps({"plugins": {"aisdlc@aisdlc-local": [{"scope": "user", "version": "0.2.0"}]}}),
        encoding="utf-8",
    )
    assert scaffold.plugin_installed() is True, "a registry entry alone must be enough"

    # The fallback, for a Claude Code that keeps no registry: find the real cache depth.
    registry.unlink()
    cached = tmp_path / ".claude" / "plugins" / "cache" / "aisdlc-local" / "aisdlc" / "0.2.0"
    (cached / ".claude-plugin").mkdir(parents=True)
    (cached / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "aisdlc", "version": "0.2.0"}), encoding="utf-8"
    )
    assert scaffold.plugin_installed() is True, "the cache fallback must reach <marketplace>/<plugin>/<version>"


def test_python_minimum_is_stated_once() -> None:
    """`requires-python`, the hook guard, `doctor`, and every shipped doc name one minimum.

    They drifted once: the guard refused 3.11 while `doctor` reported it `ok` and three
    documents promised it would work, so a user saw a green check and inactive guards.
    """
    pyproject = (PLUGIN / "pyproject.toml").read_text(encoding="utf-8")
    major, minor = re.search(r'requires-python\s*=\s*">=(\d+)\.(\d+)"', pyproject).groups()
    floor = f"{major}.{minor}"
    guard = (PLUGIN / "hooks" / "guard").read_text(encoding="utf-8")
    assert f"sys.version_info >= ({major}, {minor})" in guard
    scaffold = (PLUGIN / "scripts" / "aisdlc" / "scaffold.py").read_text(encoding="utf-8")
    assert f"parts < ({major}, {minor})" in scaffold
    shipped = [PLUGIN / "README.md", PLUGIN / "after-install.md", PLUGIN / "AGENTS.md"]
    for folder in ("commands", "skills", "agents"):
        shipped += sorted((PLUGIN / folder).rglob("*.md"))
    stated = re.compile(r"(?:Python|python3?)\s*(?:>=\s*)?3\.(\d+)")
    for path in shipped:
        for match in stated.finditer(path.read_text(encoding="utf-8")):
            assert f"3.{match.group(1)}" == floor, (
                f"{path.relative_to(PLUGIN)} says {match.group(0)!r}; floor is {floor}"
            )
