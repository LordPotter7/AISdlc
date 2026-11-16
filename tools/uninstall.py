#!/usr/bin/env python3
"""Remove this framework's scaffolding from a repository.

MAINTENANCE ONLY (ADR-008): nothing in the runtime path may call this.

Uninstalling the plugin removes the commands, skills, agents, and hooks. It does not touch
the repository, and it should not — `delivery/` holds the lifecycle records, and those are
the point of the exercise. This removes the *scaffolding* on request, and never the
records:

    python3 tools/uninstall.py --repo path/to/project            # report what would go
    python3 tools/uninstall.py --repo path/to/project --apply    # remove it

`delivery/` is never removed, and neither is `.ai-sdlc/runtime/` unless `--purge-state` is
passed, because an audit log is the one artifact that cannot be reconstructed.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

# Scaffolding `init` writes. Ordered so the report reads from most to least recoverable.
REMOVABLE = (
    ".ai-sdlc/config.yaml",
    ".ai-sdlc/project-context.md",
    ".ai-sdlc/security",
    ".ai-sdlc/evals",
    ".ai-sdlc/lessons",
    ".ai-sdlc/vendor",
    "AGENTS.md",
)

NEVER = ("delivery", ".ai-sdlc/runtime", ".ai-sdlc/project-understanding.md", ".ai-sdlc/baselines")


def _hook_entries_removed(settings: Path, apply: bool) -> str | None:
    if not settings.exists():
        return None
    payload = json.loads(settings.read_text(encoding="utf-8"))
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return None
    removed = 0
    for event, entries in list(hooks.items()):
        kept = []
        for entry in entries:
            inner = [
                h
                for h in entry.get("hooks", [])
                if "run-hook.cmd" not in h.get("command", "") and not h.get("command", "").startswith("ai-sdlc hook")
            ]
            removed += len(entry.get("hooks", [])) - len(inner)
            if inner:
                kept.append({**entry, "hooks": inner})
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)
    if not removed:
        return None
    if apply:
        if not hooks:
            payload.pop("hooks")
        settings.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return f".claude/settings.json ({removed} hook entries)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path("."), help="Repository to clean.")
    parser.add_argument("--apply", action="store_true", help="Actually remove. Without it, this is a dry run.")
    parser.add_argument(
        "--purge-state",
        action="store_true",
        help="Also remove .ai-sdlc/runtime/. The audit log cannot be reconstructed.",
    )
    args = parser.parse_args()

    root = args.repo.resolve()
    if not (root / ".ai-sdlc").exists():
        print(f"{root} has no .ai-sdlc/ — nothing to remove.")
        return 0

    targets = [root / relative for relative in REMOVABLE]
    if args.purge_state:
        targets.append(root / ".ai-sdlc" / "runtime")

    removed: list[str] = []
    for path in targets:
        if not path.exists():
            continue
        removed.append(str(path.relative_to(root)))
        if args.apply:
            shutil.rmtree(path) if path.is_dir() else path.unlink()

    entry = _hook_entries_removed(root / ".claude" / "settings.json", args.apply)
    if entry:
        removed.append(entry)

    verb = "removed" if args.apply else "would remove"
    for item in removed:
        print(f"  {verb}: {item}")
    if not removed:
        print("  nothing to remove")
    print("\n  kept, always: " + ", ".join(NEVER))
    if not args.apply:
        print("\nDry run. Re-run with --apply to remove.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
