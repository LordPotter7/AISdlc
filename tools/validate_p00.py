from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FILES = (
    "skills/aisdlc/SKILL.md",
    "skills/aisdlc/references/lifecycle.md",
    "skills/aisdlc/references/verification.md",
    "skills/aisdlc/references/claude-code-integration.md",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "hooks/hooks.json",
    "agents/aisdlc-verifier.md",
    "agents/aisdlc-red-team.md",
    ".ai-sdlc/constitution.md",
    ".ai-sdlc/project-context.md",
    ".ai-sdlc/roles/orchestrator.md",
    ".ai-sdlc/roles/product.md",
    ".ai-sdlc/roles/architect.md",
    ".ai-sdlc/roles/developer.md",
    ".ai-sdlc/roles/verifier.md",
    ".ai-sdlc/roles/red-team.md",
    ".ai-sdlc/evals/regression/tool-call-injection/suite.yaml",
    ".ai-sdlc/evals/regression/tenant-isolation/suite.yaml",
    ".ai-sdlc/evals/regression/citation-integrity/suite.yaml",
    ".ai-sdlc/evals/regression/refusal-behavior/suite.yaml",
    ".ai-sdlc/templates/intent-template.yaml",
    ".ai-sdlc/templates/brief-template.md",
    ".ai-sdlc/templates/spec-template.md",
    ".ai-sdlc/templates/compact-change-template.md",
    ".ai-sdlc/templates/requirements-template.yaml",
    ".ai-sdlc/templates/eval-design-template.md",
    ".ai-sdlc/templates/master-plan-template.md",
    ".ai-sdlc/templates/phase-template.yaml",
    ".ai-sdlc/templates/task-template.yaml",
    ".ai-sdlc/templates/result-template.md",
    "examples/compact-intent/I001-add-status-guidance/00-intent/intent.yaml",
    "examples/compact-intent/I001-add-status-guidance/01-brief/brief.md",
    "examples/compact-intent/I001-add-status-guidance/02-specification/requirements.yaml",
    "examples/compact-intent/I001-add-status-guidance/03-planning/plan.md",
    "examples/compact-intent/I001-add-status-guidance/04-build/I001-P00-compact/tasks/I001-P00-T001/task.yaml",
    "examples/compact-intent/I001-add-status-guidance/04-build/I001-P00-compact/tasks/I001-P00-T001/result.md",
    "examples/compact-intent/I001-add-status-guidance/04-build/I001-P00-compact/tasks/I001-P00-T001/evidence/manifest.json",
    "examples/compact-intent/I001-add-status-guidance/traceability.json",
    "examples/compact-intent/I001-add-status-guidance/06-close/observed-gaps.md",
)

SAMPLE_ROOT = Path("examples/compact-intent/I001-add-status-guidance")
REQUIRED_OPERATIONS = ("start", "specify", "plan", "run", "verify", "close", "status")
SAMPLE_IDENTIFIERS = ("I001", "I001-P00", "I001-P00-T001", "REQ-F-001", "AC-001")


@dataclass(frozen=True)
class ValidationReport:
    checked_files: int
    errors: tuple[str, ...] = ()


class ValidationError(RuntimeError):
    pass


def _read(path: Path, errors: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"cannot read {path}: {error}")
        return ""


def _version_problems(root: Path) -> list[str]:
    """Version consistency, without making this stdlib-only validator import the runner.

    `validate_p00.py` runs against a checkout that may not have the runner installed —
    that is the point of it — so the check is inlined rather than imported from
    `aisdlc.versioning`. The two are kept honest by a test that asserts they agree.
    """
    import tomllib

    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return ["missing pyproject.toml"]
    with pyproject.open("rb") as handle:
        authority = tomllib.load(handle).get("project", {}).get("version")
    if not authority:
        return ["pyproject.toml declares no [project] version"]

    problems = []
    for relative, keys in (
        (".claude-plugin/plugin.json", ("version",)),
        (".claude-plugin/marketplace.json", ("metadata", "version")),
    ):
        value: object = json.loads((root / relative).read_text(encoding="utf-8"))
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if value != authority:
            problems.append(f"{relative} declares {value}, but pyproject.toml declares {authority}")
    return problems


def validate_repository(repository_root: Path) -> ValidationReport:
    root = repository_root.resolve()
    errors: list[str] = []

    for relative_path in REQUIRED_FILES:
        if not (root / relative_path).is_file():
            errors.append(f"missing required file: {relative_path}")

    if errors:
        raise ValidationError("\n".join(errors))

    skill_path = root / REQUIRED_FILES[0]
    skill = _read(skill_path, errors)
    if not re.search(r"\A---\s+.*?\bname:\s*aisdlc\s+.*?\bdescription:\s*.+?\s+---", skill, re.DOTALL):
        errors.append("SKILL.md requires valid name and description frontmatter")
    # Operations are slash commands now, not branches inside the skill. Each one must
    # exist as a command file and be named in the skill's command table.
    for operation in REQUIRED_OPERATIONS:
        if not (root / "commands" / f"{operation}.md").is_file():
            errors.append(f"missing command for operation: commands/{operation}.md")
        if not re.search(rf"`/aisdlc:{re.escape(operation)}`", skill):
            errors.append(f"SKILL.md does not expose operation: /aisdlc:{operation}")
    for invariant in ("runner owns", "Never invent", "Implementing-agent prose is not evidence"):
        if invariant not in skill:
            errors.append(f"SKILL.md is missing authority invariant: {invariant}")

    # R3.4: the skill's table is the model's routing surface, so a command it does not
    # name is a command the agent will not reach for. The check runs both directions —
    # a table row with no command file is just as broken as the reverse.
    shipped = {path.stem for path in (root / "commands").glob("*.md")}
    documented = set(re.findall(r"^\| `/aisdlc:([a-z-]+)`", skill, re.MULTILINE))
    for missing in sorted(shipped - documented):
        errors.append(f"SKILL.md does not name shipped command: /aisdlc:{missing}")
    for phantom in sorted(documented - shipped):
        errors.append(f"SKILL.md names a command that does not exist: /aisdlc:{phantom}")

    # R1.1: pyproject.toml is the only hand-edited version.
    errors.extend(_version_problems(root))

    for linked_path in re.findall(r"\[[^\]]+\]\((references/[^)]+)\)", skill):
        if not (skill_path.parent / linked_path).is_file():
            errors.append(f"SKILL.md links missing reference: {linked_path}")

    for relative_path in (
        "agents/aisdlc-verifier.md",
        "agents/aisdlc-red-team.md",
    ):
        agent = _read(root / relative_path, errors)
        frontmatter = agent.split("---", 2)[1] if agent.count("---") >= 2 else ""
        tools_line = next((line for line in frontmatter.splitlines() if line.startswith("tools:")), "")
        for forbidden_tool in ("Write", "Edit", "NotebookEdit", "Bash"):
            if forbidden_tool in tools_line:
                errors.append(f"{relative_path} is not read-only: {forbidden_tool} is enabled")

    sample_files = [path for path in (root / SAMPLE_ROOT).rglob("*") if path.is_file()]
    for path in sample_files:
        contents = _read(path, errors)
        for marker in ("{{", "}}", "TODO", "TBD"):
            if marker in contents:
                errors.append(f"sample contains unresolved marker {marker!r}: {path.relative_to(root)}")

    requirements = _read(root / SAMPLE_ROOT / "02-specification/requirements.yaml", errors)
    plan = _read(root / SAMPLE_ROOT / "03-planning/plan.md", errors)
    task = _read(
        root / SAMPLE_ROOT / "04-build/I001-P00-compact/tasks/I001-P00-T001/task.yaml",
        errors,
    )
    traceability_path = root / SAMPLE_ROOT / "traceability.json"
    traceability_text = _read(traceability_path, errors)
    expected_references = {
        "requirements.yaml": (requirements, ("I001", "REQ-F-001", "AC-001")),
        "plan.md": (plan, SAMPLE_IDENTIFIERS),
        "task.yaml": (task, SAMPLE_IDENTIFIERS),
        "traceability.json": (traceability_text, ("I001", "I001-P00-T001", "REQ-F-001", "AC-001")),
    }
    for artifact_name, (document, identifiers) in expected_references.items():
        for identifier in identifiers:
            if identifier not in document:
                errors.append(f"{artifact_name} does not reference {identifier}")
    for required_task_field in (
        "affected_paths:",
        "shared_resources:",
        "acceptance_criteria:",
        "verification_commands:",
        "retry_limit: 2",
    ):
        if required_task_field not in task:
            errors.append(f"sample Task is missing: {required_task_field}")

    for relative_path in (
        SAMPLE_ROOT / "04-build/I001-P00-compact/tasks/I001-P00-T001/evidence/manifest.json",
        SAMPLE_ROOT / "traceability.json",
    ):
        try:
            json.loads((root / relative_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"invalid JSON in {relative_path}: {error}")

    if errors:
        raise ValidationError("\n".join(errors))
    return ValidationReport(checked_files=len(REQUIRED_FILES))


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    try:
        report = validate_repository(repository_root)
    except ValidationError as error:
        print(f"P00 validation failed:\n{error}", file=sys.stderr)
        return 1
    print(f"P00 validation passed ({report.checked_files} required files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
