"""Checks on a draft of one authored file, for the editor while a person types.

Every check runs the runner's own code (`validate_intent`, `lint_intent`) over a temporary
copy of the Intent holding the draft, so what the editor underlines is exactly what
`artifact write` will report, and the repository is never touched. The save preview uses
the same copy, so the two can never disagree.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aisdlc.desktop.store import ProjectStore
from aisdlc.vendor import yaml

MAX_DRAFT_BYTES = 256 * 1024

# Which schema describes which authored file, for key and value completion.
SCHEMAS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^02-specification/requirements\.ya?ml$"), "requirement.schema.json"),
    (re.compile(r"^02-specification/threat-model\.ya?ml$"), "threat-model.schema.json"),
    (re.compile(r"(^|/)phase\.ya?ml$"), "phase.schema.json"),
    (re.compile(r"(^|/)task\.ya?ml$"), "task.schema.json"),
    (re.compile(r"^05-verification/findings\.ya?ml$"), "finding.schema.json"),
)
ID = re.compile(r"\b(?:REQ-(?:F|NFR)|AC|SC|TM|TB|F|TH)-\d{3,}\b")


@contextmanager
def draft_copy(store: ProjectStore, intent_root: Path, relative: str, content: str) -> Iterator[Path]:
    """A throwaway repository holding this Intent with the draft in place of the file."""
    with tempfile.TemporaryDirectory(prefix="aisdlc-draft-") as scratch:
        repository = Path(scratch)
        (repository / ".ai-sdlc").mkdir()
        for name in ("config.yaml", "glossary.yaml"):
            if (store.root / ".ai-sdlc" / name).is_file():
                shutil.copy2(store.root / ".ai-sdlc" / name, repository / ".ai-sdlc" / name)
        copy = repository / "delivery" / "intents" / intent_root.name
        shutil.copytree(intent_root, copy, ignore=shutil.ignore_patterns("evidence"))
        (copy / relative).parent.mkdir(parents=True, exist_ok=True)
        (copy / relative).write_text(content, encoding="utf-8", newline="")
        yield copy


def run_checks(store: ProjectStore, copy: Path, status: str | None) -> dict[str, Any]:
    """The runner's validation and lint over a draft copy, as `artifact write` reports them."""
    from aisdlc.specquality import lint_intent
    from aisdlc.validation import validate_intent

    validation = validate_intent(copy, require_plan=status in {"planned", "building", "converging"})
    lint = None
    if (copy / "02-specification" / "requirements.yaml").is_file():
        result = lint_intent(copy, store.context.configuration.get("specification_fail_on", "error"))
        lint = {"passed": result["passed"], "counts": result["counts"], "findings": result["findings"]}
    return {"validation_errors": list(validation.errors), "lint": lint}


def _syntax(relative: str, content: str) -> dict[str, Any] | None:
    """Where the draft stops parsing, if it does. Nothing else can be checked until it parses."""
    if relative.endswith((".yaml", ".yml")):
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
            problem = getattr(error, "problem", None) or str(error).splitlines()[0]
            return {
                "line": mark.line + 1 if mark else None,
                "column": mark.column + 1 if mark else None,
                "message": f"Not valid YAML: {problem}",
            }
    elif relative.endswith(".json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as error:
            return {"line": error.lineno, "column": error.colno, "message": f"Not valid JSON: {error.msg}"}
    return None


def _identifiers(copy: Path) -> list[dict[str, Any]]:
    """Every identified record in the specification, with where it is written and what it says."""
    from aisdlc.specquality import SOURCE_FILES, source_lines

    labels: dict[str, str] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            identifier = value.get("id")
            if isinstance(identifier, str):
                text = value.get("statement") or value.get("title") or value.get("description") or value.get("name")
                labels.setdefault(identifier, str(text or "").strip())
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for relative in SOURCE_FILES:
        try:
            walk(yaml.safe_load((copy / relative).read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError):
            continue
    lines = source_lines(copy)
    return [
        {"id": identifier, "label": labels.get(identifier, ""), "file": where["file"], "line": where["line"]}
        for identifier, where in lines.items()
        if ID.fullmatch(identifier)
    ]


def _schema_keys(store: ProjectStore, relative: str) -> list[dict[str, Any]]:
    """Property names the file's schema allows, with its description and allowed values."""
    from aisdlc.assets import AssetError, schema_path

    name = next((schema for pattern, schema in SCHEMAS if pattern.search(relative)), None)
    if not name:
        return []
    try:
        schema = json.loads(schema_path(store.root, name).read_text(encoding="utf-8"))
    except (AssetError, OSError, ValueError):
        return []
    keys: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, spec in (node.get("properties") or {}).items():
                if isinstance(spec, dict):
                    entry = keys.setdefault(key, {"key": key, "description": "", "values": []})
                    entry["description"] = entry["description"] or str(spec.get("description") or "")
                    values = spec.get("enum") or (spec.get("items") or {}).get("enum") or []
                    entry["values"] = sorted({*entry["values"], *(str(v) for v in values)})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return sorted(keys.values(), key=lambda entry: entry["key"])


PATH = re.compile(r"\b\d\d-[\w-]+/[\w./-]+\.(?:ya?ml|md|json)\b")
TASK = re.compile(r"\bI\d{3,}-P\d{2,}-T\d{3,}\b")


def _line_at(content: str, location: str) -> int | None:
    """The line of a dotted schema location (`functional_requirements.0.statement`) in YAML."""
    try:
        node = yaml.compose(content)
    except yaml.YAMLError:
        return None
    line = None
    for part in [piece for piece in location.split(".") if piece and piece != "root"]:
        if isinstance(node, yaml.MappingNode):
            found = next(
                (pair for pair in node.value if isinstance(pair[0], yaml.ScalarNode) and pair[0].value == part), None
            )
            if not found:
                break
            line, node = found[0].start_mark.line + 1, found[1]
        elif isinstance(node, yaml.SequenceNode) and part.isdigit() and int(part) < len(node.value):
            node = node.value[int(part)]
            line = node.start_mark.line + 1
        else:
            break
    return line


def _place(
    message: str, relative: str, content: str, identifiers: list[dict[str, Any]]
) -> tuple[str | None, int | None]:
    """Which file, and which line of it, a validation message is about; `None` when it names neither.

    Schema errors read `<file>:<dotted.location>: ...`; others name a path, a record, or a Task.
    """
    prefix, colon, rest = message.partition(":")
    name = prefix.replace("\\", "/")
    if colon and name.endswith((".yaml", ".yml", ".json", ".md")):
        if relative == name or relative.endswith("/" + name):
            location = rest.split(":", 1)[0].strip()
            return relative, _line_at(content, location) if relative.endswith((".yaml", ".yml")) else None
        return (name if "/" in name else None), None
    path = PATH.search(message.replace("\\", "/"))
    if path:
        return path.group(0), None
    for match in ID.finditer(message):
        item = next((entry for entry in identifiers if entry["id"] == match.group(0)), None)
        if item:
            return item["file"], item["line"]
    task = TASK.search(message)
    if task and f"/{task.group(0)}/" in f"/{relative}":
        return relative, None
    return None, None


def diagnose(store: ProjectStore, intent_root: Path, relative: str, content: str, status: str | None) -> dict[str, Any]:
    """What the runner would say about this draft, placed on the lines it concerns."""
    diagnostics: list[dict[str, Any]] = []
    syntax = _syntax(relative, content)
    if syntax:
        diagnostics.append({"file": relative, "severity": "error", "source": "syntax", "code": None, **syntax})
    identifiers: list[dict[str, Any]] = []
    passed = syntax is None
    checked = content if syntax is None else _on_disk(intent_root, relative)
    with draft_copy(store, intent_root, relative, checked) as copy:
        identifiers = _identifiers(copy)
        if syntax is None:
            checks = run_checks(store, copy, status)
            scratch = str(copy)
            prefixes = (scratch + "\\", scratch.replace("\\", "/") + "/", scratch)
            for raw in checks["validation_errors"]:
                # Paths inside the throwaway copy mean nothing to a person; name them from the Intent.
                message = raw
                for prefix in prefixes:
                    message = message.replace(prefix, "")
                file, line = _place(message, relative, content, identifiers)
                diagnostics.append(
                    {
                        "file": file,
                        "line": line if file == relative else None,
                        "column": None,
                        "severity": "error",
                        "source": "validation",
                        "code": None,
                        "message": message,
                    }
                )
            lint = checks["lint"] or {"passed": True, "findings": []}
            for finding in lint["findings"]:
                source = finding.get("source") or {}
                diagnostics.append(
                    {
                        "file": source.get("file"),
                        "line": source.get("line") if source.get("file") == relative else None,
                        "column": None,
                        "severity": finding["severity"],
                        "source": "lint",
                        "code": finding["rule"],
                        "message": f"{finding['location']}: {finding['message']}",
                    }
                )
            passed = not any(d["file"] == relative and d["severity"] == "error" for d in diagnostics)
    rank = {"error": 0, "warning": 1, "info": 2}
    diagnostics.sort(key=lambda d: (d["file"] != relative, rank.get(d["severity"], 3), d["line"] or 0))
    return {
        "path": relative,
        "suggestions": _suggestions(store, relative),
        "passed": passed,
        "diagnostics": diagnostics,
        "identifiers": identifiers,
        "keys": _schema_keys(store, relative),
    }


def _suggestions(store: ProjectStore, relative: str) -> dict[str, list[str]]:
    """For a Task: the codebase's modules as `affected_paths`, its test commands as checks."""
    if not re.search(r"(^|/)task\.ya?ml$", relative):
        return {}
    from aisdlc.desktop.codebase import inventory

    try:
        scan = inventory(store)
    except Exception:  # noqa: BLE001 - completion is a convenience; the check stands without it
        return {}
    code = [module["path"] for module in scan["modules"] if module["kind"] in {"code", "tests"}]
    return {
        "affected_paths": [f"{path}/" for path in code],
        "verification_commands": [suite["command"] for suite in scan["test_suites"]],
    }


def _on_disk(intent_root: Path, relative: str) -> str:
    target = intent_root / relative
    return target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
