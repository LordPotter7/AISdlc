"""Repository glossary shared across Intents (Wave S3.2).

An Intent's glossary pins an ambiguous word for one specification. Across a portfolio that
is not enough: "active customer" defined three ways in three Intents is three systems that
disagree, each internally consistent. The repository glossary gives a term one definition
and one accountable owner.

Ownership follows the lesson registry's model. The file is repository-owned and a person
may edit it directly — that is how an owner changes a definition. The runner mediates only
promotion from an Intent, and it never overwrites: promoting a term the repository already
defines differently is refused, because silently replacing an owned definition is exactly
the drift this file exists to stop. An Intent that redefines a repository term is flagged
by the quality lint rather than refused, since the Intent may be the place the conflict is
discovered.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .assets import schema_path
from .jsonschema_mini import Draft202012Validator
from .storage import atomic_write_text
from .vendor import yaml

GLOSSARY = Path(".ai-sdlc") / "glossary.yaml"


class GlossaryError(RuntimeError):
    pass


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def load(repository_root: Path) -> list[dict[str, Any]]:
    path = repository_root / GLOSSARY
    if not path.is_file():
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [item for item in payload.get("terms", []) or [] if isinstance(item, dict)]


def defined_terms(entries: list[dict[str, Any]]) -> set[str]:
    """Every spelling a glossary pins down: terms and their aliases, lower-cased."""
    names: set[str] = set()
    for entry in entries:
        names.add(_normal(entry.get("term", "")))
        names.update(_normal(alias) for alias in entry.get("aliases", []) or [])
    return {name for name in names if name}


def conflicts(repository_entries: list[dict[str, Any]], intent_entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Intent terms the repository already defines with a different meaning."""
    owned = {}
    for entry in repository_entries:
        for name in [entry.get("term", ""), *(entry.get("aliases", []) or [])]:
            owned[_normal(name)] = entry
    found: list[dict[str, str]] = []
    for entry in intent_entries:
        match = owned.get(_normal(entry.get("term", "")))
        if match and _normal(match.get("definition", "")) != _normal(entry.get("definition", "")):
            found.append({"term": str(entry.get("term")), "owner": str(match.get("owner", "unowned"))})
    return found


def _validate(repository_root: Path, payload: dict[str, Any]) -> None:
    schema = json.loads(schema_path(repository_root, "glossary.schema.json").read_text(encoding="utf-8"))
    problems = [error.message for error in Draft202012Validator(schema).iter_errors(payload)]
    if problems:
        raise GlossaryError("invalid glossary: " + "; ".join(problems))


def promote(repository_root: Path, intent_root: Path, term: str, owner: str) -> dict[str, Any]:
    """Copy one Intent-defined term into the repository glossary under a named owner."""
    requirements = yaml.safe_load((intent_root / "02-specification" / "requirements.yaml").read_text(encoding="utf-8"))
    candidates = (requirements or {}).get("glossary", []) or []
    local = next((item for item in candidates if _normal(item.get("term", "")) == _normal(term)), None)
    if local is None:
        raise GlossaryError(f"{intent_root.name} does not define {term!r}")
    path = repository_root / GLOSSARY
    payload = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None) or {}
    entries = [item for item in payload.get("terms", []) or [] if isinstance(item, dict)]
    clash = conflicts(entries, [local])
    if clash:
        raise GlossaryError(
            f"{term!r} is already defined differently, owned by {clash[0]['owner']}; the owner changes it "
            "by editing the glossary, not by promotion"
        )
    if _normal(local["term"]) in defined_terms(entries):
        return {"term": local["term"], "status": "already_defined"}
    entry = {
        "term": local["term"],
        "definition": local["definition"],
        "owner": owner,
        "source_intent": intent_root.name.split("-", 1)[0],
        "promoted_at": datetime.now(UTC).isoformat(),
    }
    payload["terms"] = [*entries, entry]
    payload.setdefault("version", 1)
    _validate(repository_root, payload)
    # Keep the leading comment block: it tells owners how the file is governed.
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    header = []
    for line in existing:
        if not line.startswith("#"):
            break
        header.append(line)
    preamble = "\n".join(header) + "\n" if header else ""
    atomic_write_text(path, preamble + yaml.safe_dump(payload, sort_keys=False))
    return {**entry, "status": "promoted"}
