#!/usr/bin/env python3
"""Read or write the plugin version across every file that declares it.

MAINTENANCE ONLY. Nothing in `scripts/`, `hooks/`, `commands/`, or `skills/` may call
this — enforced by
tests/plugin/test_plugin_surface.py::test_runtime_never_reaches_into_tools.

    python tools/bump-version.py            # report the current versions
    python tools/bump-version.py 0.3.0      # set them all

The manifest is `.version-bump.json`, following the pattern in `obra/superpowers`. It
replaces `ai-sdlc check-versions`, which was a runner subcommand that only ever ran in
CI. CI now runs the same check through tests/plugin/test_plugin_surface.py.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / ".version-bump.json"


def _dotted_get(payload: object, field: str) -> object:
    current = payload
    for part in field.split("."):
        current = current[int(part)] if part.isdigit() else current[part]  # type: ignore[index]
    return current


def _dotted_set(payload: object, field: str, value: str) -> None:
    parts = field.split(".")
    current = payload
    for part in parts[:-1]:
        current = current[int(part)] if part.isdigit() else current[part]  # type: ignore[index]
    last = parts[-1]
    if last.isdigit():
        current[int(last)] = value  # type: ignore[index]
    else:
        current[last] = value  # type: ignore[index]


def read_all() -> dict[str, str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for entry in manifest["files"]:
        path = REPO / entry["path"]
        if path.suffix == ".json":
            found[entry["path"]] = str(_dotted_get(json.loads(path.read_text(encoding="utf-8")), entry["field"]))
        else:
            key = entry["field"].split(".")[-1]
            match = re.search(rf'^{key}\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
            found[entry["path"]] = match.group(1) if match else "<not found>"
    return found


def write_all(version: str) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = REPO / entry["path"]
        if path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            _dotted_set(payload, entry["field"], version)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        else:
            key = entry["field"].split(".")[-1]
            text = path.read_text(encoding="utf-8")
            text = re.sub(rf'^({key}\s*=\s*")[^"]+(")', rf"\g<1>{version}\g<2>", text, count=1, flags=re.M)
            path.write_text(text, encoding="utf-8")
        print(f"  {entry['path']} -> {version}")


def main() -> int:
    if len(sys.argv) > 2:
        print(__doc__)
        return 2
    if len(sys.argv) == 2:
        version = sys.argv[1]
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            print(f"not a semantic version: {version}", file=sys.stderr)
            return 2
        write_all(version)
        return 0
    versions = read_all()
    for path, value in versions.items():
        print(f"  {path:40} {value}")
    distinct = set(versions.values())
    if len(distinct) != 1:
        print(f"\nVERSIONS DISAGREE: {sorted(distinct)}", file=sys.stderr)
        return 1
    print(f"\nall agree: {distinct.pop()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
