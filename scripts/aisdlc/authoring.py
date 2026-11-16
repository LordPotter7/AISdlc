"""Authoring: writing the files a developer owns, through the runner.

The runner owns identifiers, state, gates and evidence; a developer owns the brief, the
specification and the plan. Those files are normally edited in an editor or by the agent,
and the PreToolUse hook keeps the agent off a locked specification. A second writer (AISDLC
Studio) must meet the same rules without depending on that hook, so every write it makes
goes through this module:

* only the authored artifacts of an Intent, never a runner-written file;
* never a specification approved at G2 (the same rule the hook applies — one function);
* never an Intent that is completed or aborted;
* never over a file that changed since the writer last read it (`expected_sha`);
* and every write lands in the audit log with the hashes before and after.

The composer adds a requirement by inserting text, not by re-serialising the YAML, so the
author's comments and ordering survive.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .vendor import yaml

#: What a developer may write inside an Intent folder. Everything else is the runner's.
AUTHORED = (
    re.compile(r"^01-brief/brief\.md$"),
    re.compile(r"^02-specification/(requirements\.yaml|threat-model\.yaml|eval-design\.md)$"),
    re.compile(r"^02-specification/contracts/[\w.-]+\.(yaml|yml|json)$"),
    re.compile(r"^03-planning/master-plan\.md$"),
    re.compile(r"^04-build/I\d{3,}-P\d{2,}[\w-]*/phase\.yaml$"),
    re.compile(r"^04-build/I\d{3,}-P\d{2,}[\w-]*/tasks/I\d{3,}-P\d{2,}-T\d{3,}/task\.yaml$"),
    re.compile(r"^05-verification/findings\.yaml$"),
)
MAX_WRITE_BYTES = 256 * 1024
CLOSED = {"completed", "aborted"}
NEW = "new"


class AuthoringError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_authored(relative: str) -> bool:
    return any(pattern.match(relative) for pattern in AUTHORED)


def specification_lock(state: dict[str, Any], intent_id: str, relative: str) -> str | None:
    """Why `relative` is locked by a gate, or None. Shared with the PreToolUse hook."""
    if not relative.startswith("02-specification/"):
        return None
    if state.get("gates", {}).get(f"{intent_id}:G2", {}).get("status") == "approved":
        return (
            f"{intent_id}'s specification was approved at G2 and is locked while work is built against it. "
            f"To change it, reopen the gate: `ai-sdlc gate reopen {intent_id} G2 --actor <you> "
            f'--reason "<what changed>"`, '
            "then edit, get a fresh pre-gate review, and approve G2 again."
        )
    return None


def check_writable(state: dict[str, Any], intent_id: str, relative: str) -> None:
    """Raise with the reason when a developer may not write `relative` right now."""
    if not is_authored(relative):
        raise AuthoringError(
            f"{relative} is not an authored artifact. The runner writes everything else "
            "(evidence, reviews, traceability, reports, inherited controls, decisions)."
        )
    status = state.get("intents", {}).get(intent_id, {}).get("status")
    if status in CLOSED:
        raise AuthoringError(f"{intent_id} is {status}; its artifacts are part of the record and no longer change")
    lock = specification_lock(state, intent_id, relative)
    if lock:
        raise AuthoringError(lock)


def parse_check(relative: str, content: str) -> None:
    """A draft may fail lint, but it must at least parse, or no check can read it."""
    if relative.endswith((".yaml", ".yml")):
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as error:
            raise AuthoringError(f"{relative} is not valid YAML: {error}") from None
    elif relative.endswith(".json"):
        import json

        try:
            json.loads(content)
        except ValueError as error:
            raise AuthoringError(f"{relative} is not valid JSON: {error}") from None


@dataclass(frozen=True)
class WriteResult:
    path: str
    before: str | None
    after: str
    created: bool


def write_artifact(intent_root: Path, relative: str, content: str, expected_sha: str) -> WriteResult:
    """Write one authored file if nobody changed it since `expected_sha` was read."""
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise AuthoringError(f"{relative} is {len(data)} bytes; the limit is {MAX_WRITE_BYTES}")
    parse_check(relative, content)
    target = (intent_root / relative).resolve()
    if not target.is_relative_to(intent_root.resolve()):
        raise AuthoringError("path must stay inside the Intent folder")
    exists = target.is_file()
    before = sha256(target.read_bytes()) if exists else None
    if expected_sha == NEW:
        if exists:
            raise AuthoringError(f"{relative} already exists; read it first and pass its sha256")
    elif before != expected_sha:
        raise AuthoringError(
            f"{relative} changed since it was read (expected {expected_sha[:12]}, found "
            f"{(before or 'no file')[:12]}); reload it and apply your change again"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".aisdlc-tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    return WriteResult(relative, before, sha256(data), not exists)


# --------------------------------------------------------------------------- #
# composing a requirement
# --------------------------------------------------------------------------- #
ID_PATTERN = {
    "functional": ("REQ-F-", "functional_requirements"),
    "non_functional": ("REQ-NFR-", "non_functional_requirements"),
}


#: The order a person would write the keys in. Transports may sort object keys (the native
#: host's JSON maps do), so the composer restores this order rather than trusting input order.
REQUIREMENT_KEYS = (
    "id",
    "priority",
    "category",
    "sources",
    "rationale",
    "statement",
    "measure",
    "wcag",
    "manual_audit",
    "personal_data",
    "data_classification",
    "acceptance_criteria",
)
CRITERION_KEYS = (
    "id",
    "verification_class",
    "statement",
    "check",
    "eval",
    "dataset",
    "metric",
    "threshold",
    "minimum_sample_size",
    "tolerance_band",
    "contract_operations",
    "baseline_ref",
)


def _ordered(record: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    known = {key: record[key] for key in keys if key in record}
    return {**known, **{key: value for key, value in record.items() if key not in known}}


def _used_ids(text: str, snapshot: dict[str, Any] | None) -> set[str]:
    used = set(re.findall(r"\b(?:REQ-(?:F|NFR)|AC|SC)-\d{3,}\b", text))
    if snapshot:  # identifiers approved before and since removed are never reused
        used |= set(re.findall(r"\b(?:REQ-(?:F|NFR)|AC|SC)-\d{3,}\b", str(snapshot)))
    return used


def next_id(prefix: str, used: set[str]) -> str:
    numbers = [int(item[len(prefix) :]) for item in used if item.startswith(prefix) and item[len(prefix) :].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1:03d}"


def _insert_items(text: str, key: str, items: list[dict[str, Any]]) -> str:
    """Append list items under the top-level `key:`, keeping everything else as written."""
    lines = text.splitlines(keepends=True)
    head = next((index for index, line in enumerate(lines) if re.match(rf"^{key}:", line)), None)
    rendered = yaml.safe_dump(items, sort_keys=False, allow_unicode=True, width=100)
    if head is None:
        suffix = "" if not lines or lines[-1].endswith("\n") else "\n"
        return text + suffix + f"{key}:\n" + rendered
    if re.match(rf"^{key}:\s*\[\s*\]\s*(#.*)?$", lines[head].rstrip("\n")):
        lines[head] = f"{key}:\n"
        return "".join(lines[: head + 1]) + rendered + "".join(lines[head + 1 :])
    end = head + 1
    indent = None
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.startswith((" ", "-", "#", "\t")):
            break
        if indent is None and line.lstrip().startswith("- "):
            indent = len(line) - len(line.lstrip())
        end += 1
    while end > head + 1 and not lines[end - 1].strip():
        end -= 1  # keep blank lines between sections after the insertion
    pad = " " * (indent or 0)
    block = "".join(pad + line if line.strip() else line for line in rendered.splitlines(keepends=True))
    if end > 0 and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    return "".join(lines[:end]) + block + "".join(lines[end:])


def add_requirement(
    text: str,
    requirement: dict[str, Any],
    criteria: list[dict[str, Any]],
    *,
    kind: str = "functional",
    snapshot: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return `requirements.yaml` with one more requirement and its criteria, and the ids used.

    Identifiers are allocated here, past every id in the file and in the last G2 snapshot,
    so an id that was approved and later removed is never given to something else.
    """
    if kind not in ID_PATTERN:
        raise AuthoringError("kind must be functional or non_functional")
    if not str(requirement.get("statement", "")).strip():
        raise AuthoringError("a requirement needs a statement")
    if not criteria:
        raise AuthoringError("a requirement needs at least one acceptance criterion")
    prefix, key = ID_PATTERN[kind]
    used = _used_ids(text, snapshot)
    requirement_id = next_id(prefix, used)
    criterion_ids = []
    for _ in criteria:
        criterion_ids.append(next_id("AC-", used))
        used.add(criterion_ids[-1])
    record = {
        "id": requirement_id,
        **{k: v for k, v in requirement.items() if k not in {"id", "acceptance_criteria"}},
        "acceptance_criteria": criterion_ids,
    }
    records = [
        {"id": cid, **{k: v for k, v in item.items() if k != "id"}}
        for cid, item in zip(criterion_ids, criteria, strict=True)
    ]
    updated = _insert_items(text, key, [_ordered(record, REQUIREMENT_KEYS)])
    updated = _insert_items(updated, "acceptance_criteria", [_ordered(item, CRITERION_KEYS) for item in records])
    if yaml.safe_load(updated) is None:
        raise AuthoringError("composition produced an empty document")
    return updated, {"requirement": requirement_id, "criteria": criterion_ids}
