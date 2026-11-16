"""Resolution of framework assets shipped with the runner.

The framework splits into files a target
repository owns (config, project context, lessons, evals, delivery records) and files the
framework owns (constitution, schemas, workflow, policies, templates, roles).

The framework-owned half resolves from the installed package rather than from
`${CLAUDE_PLUGIN_ROOT}`. That variable exists only inside a Claude Code plugin session,
and the runner must resolve schemas identically in CI (`verify-ci`) and in a plain
terminal (`doctor`), where it is unset.

Resolution order for a framework asset:

1. `AISDLC_FRAMEWORK_ROOT` - explicit override, used by tests and unusual layouts.
2. Package data at `aisdlc/assets/` - present in a built wheel.
3. The development checkout's own `.ai-sdlc/` - present in an editable install.

A repository-local copy always wins over the framework copy, so a target repository can
still tailor any file by placing it under its own `.ai-sdlc/`.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

# Directories the framework owns. A target repository inherits these from the package
# unless it deliberately places its own copy under `.ai-sdlc/`.
FRAMEWORK_OWNED = ("schemas", "workflow", "policies", "roles", "templates")

# Files the framework owns, adopted unchanged.
FRAMEWORK_OWNED_FILES = ("constitution.md", "framework-guide.yaml")

# Paths a target repository owns. `init` writes these; upgrades never overwrite them.
REPOSITORY_OWNED = ("config.yaml", "project-context.md", "lessons", "evals", "sensors", "glossary.yaml")


class AssetError(RuntimeError):
    """Raised when framework assets cannot be located."""


def _is_framework_root(candidate: Path) -> bool:
    return (candidate / "schemas" / "task.schema.json").is_file()


@cache
def framework_root() -> Path:
    """Locate the directory holding the framework-owned assets."""
    override = os.environ.get("AISDLC_FRAMEWORK_ROOT")
    if override:
        candidate = Path(override).expanduser().resolve()
        if not _is_framework_root(candidate):
            raise AssetError(f"AISDLC_FRAMEWORK_ROOT does not contain framework assets: {candidate}")
        return candidate

    packaged = Path(__file__).resolve().parent / "assets"
    if _is_framework_root(packaged):
        return packaged

    # Editable install: walk up from src/aisdlc/ to the checkout root.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".ai-sdlc"
        if _is_framework_root(candidate):
            return candidate

    raise AssetError(
        "Framework assets not found. Reinstall the runner, or set AISDLC_FRAMEWORK_ROOT "
        "to a directory containing schemas/, workflow/, policies/, roles/, and templates/."
    )


def asset_path(repository_root: Path, relative: str) -> Path:
    """Resolve one asset, preferring the target repository's own copy."""
    local = repository_root / ".ai-sdlc" / relative
    if local.exists():
        return local
    return framework_root() / relative


def repository_root_of(intent_root: Path) -> Path | None:
    """The repository an Intent directory belongs to: the nearest ancestor with `.ai-sdlc/`."""
    for parent in intent_root.resolve().parents:
        if (parent / ".ai-sdlc").is_dir():
            return parent
    return None


def schema_path(repository_root: Path, schema_name: str) -> Path:
    """Resolve a JSON schema by file name, e.g. `task.schema.json`."""
    return asset_path(repository_root, f"schemas/{schema_name}")
