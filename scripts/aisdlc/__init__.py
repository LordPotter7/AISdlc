"""Deterministic runtime for the adaptive evidence-driven AI SDLC."""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["__version__"]


def _declared_version() -> str:
    """Read the version from the plugin manifest, not from package metadata.

    This used to be `importlib.metadata.version("ai-sdlc")`, which needs the runner to have
    been *installed*. Nothing is installed any more (ADR-007), so that call fell through to
    `0+unknown` — and the compatibility check then refused to manage a repository the
    runner had itself scaffolded, in the one configuration that is now the normal one.

    `.claude-plugin/plugin.json` ships beside the code and is one of the files
    `.version-bump.json` keeps in sync, so it is the version that is true by construction.
    """
    manifest = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
    try:
        declared = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, json.JSONDecodeError, AttributeError):
        return "0+unknown"
    return str(declared) if declared else "0+unknown"


__version__ = _declared_version()
