"""Version identity and compatibility across the three things that can disagree.

A plugin install has version skew of its own. One machine can hold a development checkout,
an installed plugin, and any number of target repositories, each scaffolded by whichever
runner was current at the time. Nothing in that arrangement forces them to move together,
and the failure mode is silent: an older
runner reads a newer repository's records and reports them as malformed rather than as
out of date.

Two jobs live here.

`manifest_versions` and `version_consistency` answer *within the framework checkout*:
does `pyproject.toml` — the single hand-edited version — agree with the two plugin
manifests that Claude Code reads? R1.1 makes `pyproject.toml` the authority and this the
instrument that keeps it one.

`compatibility_report` answers *inside a target repository*: does the runner answering
now agree with the runner that scaffolded this repository? R1.3. It reports rather than
decides; `doctor` renders it, and the hook seam uses
`incompatible_reason` to fail closed.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

# Stamped into every runner-owned record. Bump only alongside a migration in
# `aisdlc.migrate`; the runner refuses records written by a schema it does not know.
# Generation 2 adds decision traceability, the first migration to run against records that
# already existed in the field.
SCHEMA_VERSION = 2


def runner_version() -> str:
    from . import __version__

    return __version__


def _release(version: str) -> tuple[int, ...]:
    """The numeric release prefix of a version, ignoring any local or pre-release suffix.

    `0.2.0`, `0.2.0+editable`, and `0.2.0.dev3` are the same release for compatibility
    purposes: they carry the same records and the same command surface. Only a difference
    in the release itself is worth interrupting somebody over.
    """
    head = re.split(r"[-+]", str(version).strip(), maxsplit=1)[0]
    parts: list[int] = []
    for chunk in head.split("."):
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    return tuple(parts)


def compatible(left: str | None, right: str | None) -> bool:
    """Whether two runner versions may operate on the same repository.

    Compatibility is minor-level: `0.2.x` reads what `0.2.y` wrote. A differing minor
    means the record shapes or the command surface may have moved, which is exactly when
    a human should be told rather than left to read a confusing validation error.
    """
    if not left or not right:
        return True
    first, second = _release(left), _release(right)
    if not first or not second:
        return True
    return first[:2] == second[:2]


def manifest_versions(source_root: Path) -> dict[str, str | None]:
    """The three versions a framework checkout declares, read from their own files."""
    versions: dict[str, str | None] = {"pyproject": None, "plugin": None, "marketplace": None}
    pyproject = source_root / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as handle:
            versions["pyproject"] = tomllib.load(handle).get("project", {}).get("version")
    versions["plugin"] = _json_field(source_root / ".claude-plugin" / "plugin.json", ("version",))
    versions["marketplace"] = _json_field(source_root / ".claude-plugin" / "marketplace.json", ("metadata", "version"))
    return versions


def _json_field(path: Path, keys: tuple[str, ...]) -> str | None:
    import json

    if not path.is_file():
        return None
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, str) else None


def version_consistency(source_root: Path) -> list[str]:
    """Every disagreement between `pyproject.toml` and the manifests Claude Code reads.

    Empty means consistent. `pyproject.toml` is the authority; the manifests are checked
    against it rather than against each other, so the remedy is always one edit in one
    known direction.
    """
    versions = manifest_versions(source_root)
    authority = versions["pyproject"]
    if authority is None:
        return ["pyproject.toml declares no [project] version"]
    problems = []
    for name, path in (
        ("plugin", ".claude-plugin/plugin.json"),
        ("marketplace", ".claude-plugin/marketplace.json (metadata.version)"),
    ):
        declared = versions[name]
        if declared is None:
            problems.append(f"{path} declares no version")
        elif declared != authority:
            problems.append(f"{path} declares {declared}, but pyproject.toml declares {authority}")
    return problems


def scaffold_version(repository_root: Path) -> str | None:
    """The runner version recorded by the `init` that scaffolded this repository.

    Read straight from the YAML rather than through `load_config`, because this is also
    consulted from the hook fast path where importing the configuration model is the cost
    being avoided, and because a config too new for this runner to validate is precisely
    the case the field exists to explain.
    """
    path = repository_root / ".ai-sdlc" / "config.yaml"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^runner_version:\s*['\"]?([^'\"\s#]+)", text, re.MULTILINE)
    return match.group(1) if match else None


def compatibility_report(repository_root: Path) -> dict[str, Any]:
    """Runner and scaffold versions, with the remedy for any disagreement."""
    current = runner_version()
    scaffolded = scaffold_version(repository_root)

    problems: list[str] = []
    if scaffolded and not compatible(current, scaffolded):
        problems.append(
            f"this repository was scaffolded by runner {scaffolded} but {current} is answering now; "
            "run `ai-sdlc migrate` to move its records forward, then `ai-sdlc init --force`"
        )
    return {
        "runner": current,
        "scaffolded_with": scaffolded,
        "status": "warn" if problems else "ok",
        "problems": problems,
    }


def incompatible_reason(repository_root: Path) -> str | None:
    """A single sentence for the hook seam, or `None` when the versions agree.

    Only a runner that disagrees with the one
    that wrote this repository's records stops work here.
    """
    scaffolded = scaffold_version(repository_root)
    current = runner_version()
    if scaffolded and not compatible(current, scaffolded):
        return (
            f"Runner {current} cannot manage a repository scaffolded by {scaffolded}. "
            "Run `ai-sdlc migrate --apply`, then `ai-sdlc init --force`, or set "
            "AISDLC_HOOKS=off for deliberately unmanaged work."
        )
    return None
