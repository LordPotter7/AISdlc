"""W5.1 — project understanding: anchors, provenance, staleness, context-pack carriage.

The obligations under test are the ones that make an agent-written Markdown file
admissible at all: a claim that cites nothing real is rejected, a document-mode claim
anchored to code is a provenance violation, the honesty sections are mandatory, staleness
comes from git rather than a stored hash table, and a stale understanding never reaches an
implementing agent.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_p05_orchestration import create_task, initialize_repository

from aisdlc.orchestration import ContextPackBuilder
from aisdlc.understanding import UnderstandingError, UnderstandingService, extract_anchors

SECTIONS = (
    "Purpose and domain vocabulary",
    "Entry points",
    "Module map and responsibilities",
    "Boundaries",
    "Data model and persistence",
    "Cross-cutting concerns",
    "Build, test, and CI topology",
    "Risk concentrations",
    "Constraints and architectural rules",
    "Proposed checks",
    "Uncertainty register",
)


def build_document(
    *,
    source: str = "repo",
    inputs: list[str] | None = None,
    notice: str | None = None,
    claims: dict[str, str] | None = None,
    omit: tuple[str, ...] = (),
) -> str:
    lines = ["---", f"source: {source}"]
    if inputs:
        lines.append("inputs:")
        lines.extend(f"  - {item}" for item in inputs)
    lines += ["---", ""]
    if notice:
        lines += [f"> {notice}", ""]
    claims = claims or {}
    for name in SECTIONS:
        if name in omit:
            continue
        lines += [f"## {name}", "", claims.get(name, "Nothing recorded for this section."), ""]
    return "\n".join(lines)


def write_understanding(root: Path, content: str) -> Path:
    path = root / ".ai-sdlc" / "project-understanding.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def commit_all(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()


def make_source_tree(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "auth.py").write_text("\n".join(f"line {n}" for n in range(1, 121)), encoding="utf-8")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "architecture.md").write_text("# Architecture\n\nServices never share a database.\n", "utf-8")


# -- anchor extraction ----------------------------------------------------------------


def test_markdown_links_and_checkboxes_are_not_mistaken_for_anchors() -> None:
    body = "- [ ] `pytest -q` — suite [tests/orders/]\n- see [the note](https://example.com/x.md)\n"
    assert [anchor.path for anchor in extract_anchors(body)] == ["tests/orders/"]


def test_anchors_inside_fenced_blocks_are_ignored() -> None:
    body = "Real claim. [src/auth.py:1-5]\n\n```markdown\nExample. [src/does-not-exist.py:1]\n```\n"
    assert [anchor.path for anchor in extract_anchors(body)] == ["src/auth.py"]


# -- record: anchors ------------------------------------------------------------------


def test_claim_citing_a_nonexistent_path_is_rejected_in_repo_mode(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(
        tmp_path,
        build_document(claims={"Entry points": "The CLI starts here. [src/ghost.py:1-10]"}),
    )
    with pytest.raises(UnderstandingError, match="anchored path does not exist"):
        UnderstandingService(tmp_path).record()


def test_claim_citing_a_nonexistent_path_is_rejected_in_document_mode(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(
        tmp_path,
        build_document(
            source="document",
            inputs=["docs/architecture.md"],
            notice="Unverified against the codebase.",
            claims={"Boundaries": "Stated boundary. [docs/missing.md:2]"},
        ),
    )
    with pytest.raises(UnderstandingError, match="outside the declared inputs|does not exist"):
        UnderstandingService(tmp_path).record()


def test_anchor_citing_lines_past_end_of_file_is_rejected(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(
        tmp_path,
        build_document(claims={"Entry points": "Auth entry. [src/auth.py:100-900]"}),
    )
    with pytest.raises(UnderstandingError, match="past end of file"):
        UnderstandingService(tmp_path).record()


def test_document_mode_claim_anchored_to_source_file_is_a_provenance_violation(tmp_path: Path) -> None:
    """The failure that would corrupt the trust model: a document run that read code."""
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(
        tmp_path,
        build_document(
            source="document",
            inputs=["docs/architecture.md"],
            notice="Unverified against the codebase.",
            claims={"Module map and responsibilities": "Auth lives here. [src/auth.py:1-20]"},
        ),
    )
    with pytest.raises(UnderstandingError, match="outside the declared inputs"):
        UnderstandingService(tmp_path).record()


# -- record: required sections and notice ---------------------------------------------


def test_document_missing_its_uncertainty_register_is_rejected(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(tmp_path, build_document(omit=("Uncertainty register",)))
    with pytest.raises(UnderstandingError, match="Uncertainty register"):
        UnderstandingService(tmp_path).record()


def test_document_mode_without_unverified_notice_is_rejected(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(
        tmp_path,
        build_document(
            source="document",
            inputs=["docs/architecture.md"],
            claims={"Constraints and architectural rules": "Services never share a database. [docs/architecture.md:3]"},
        ),
    )
    with pytest.raises(UnderstandingError, match="unverified-against-code notice"):
        UnderstandingService(tmp_path).record()


def test_repo_mode_rejects_declared_inputs(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(tmp_path, build_document(inputs=["docs/architecture.md"]))
    with pytest.raises(UnderstandingError, match="repo mode does not take"):
        UnderstandingService(tmp_path).record()


def test_sections_out_of_order_are_rejected(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    content = build_document()
    swapped = (
        content.replace("## Entry points", "@@TMP@@")
        .replace("## Purpose and domain vocabulary", "## Entry points")
        .replace("@@TMP@@", "## Purpose and domain vocabulary")
    )
    write_understanding(tmp_path, swapped)
    with pytest.raises(UnderstandingError, match="out of order"):
        UnderstandingService(tmp_path).record()


# -- record: observation, not recommendation ------------------------------------------


def test_output_containing_a_recommendation_is_rejected(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(
        tmp_path,
        build_document(claims={"Risk concentrations": "This module is large, and we should refactor it."}),
    )
    with pytest.raises(UnderstandingError, match="recommendation rather than an observation"):
        UnderstandingService(tmp_path).record()


def test_proposed_checks_may_propose(tmp_path: Path) -> None:
    """A proposal in the payload section is the point, not a violation."""
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(
        tmp_path,
        build_document(
            claims={"Proposed checks": "- [ ] `pytest -q` — we recommend this suite as a probe [src/auth.py]"}
        ),
    )
    report = UnderstandingService(tmp_path).record()
    assert report["proposed_checks"] == 1


# -- record: the pin ------------------------------------------------------------------


def test_record_stamps_the_pin_into_frontmatter_and_writes_no_sidecar(tmp_path: Path) -> None:
    head = initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    path = write_understanding(tmp_path, build_document(claims={"Entry points": "Auth entry. [src/auth.py:40-120]"}))
    head = commit_all(tmp_path, "sources")
    report = UnderstandingService(tmp_path).record()
    assert report["commit"] == head
    text = path.read_text(encoding="utf-8")
    assert f"commit: {head}" in text
    assert "recorded_at:" in text
    assert "Auth entry. [src/auth.py:40-120]" in text
    siblings = {item.name for item in path.parent.iterdir()}
    assert siblings == {"project-understanding.md"}, "the artifact must have no machine-readable sidecar"


def test_a_rejected_document_is_never_pinned(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    path = write_understanding(tmp_path, build_document(claims={"Entry points": "Ghost. [src/ghost.py]"}))
    with pytest.raises(UnderstandingError):
        UnderstandingService(tmp_path).record()
    assert "commit:" not in path.read_text(encoding="utf-8")


# -- status: staleness from git, not stored hashes ------------------------------------


def test_unmoved_anchors_report_fresh(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(tmp_path, build_document(claims={"Entry points": "Auth entry. [src/auth.py:40-120]"}))
    commit_all(tmp_path, "sources")
    UnderstandingService(tmp_path).record()
    commit_all(tmp_path, "pin")
    assert UnderstandingService(tmp_path).status()["status"] == "fresh"


def test_a_moved_anchor_is_reported_as_drift_by_status(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(tmp_path, build_document(claims={"Entry points": "Auth entry. [src/auth.py:40-120]"}))
    commit_all(tmp_path, "sources")
    UnderstandingService(tmp_path).record()
    commit_all(tmp_path, "pin")
    (tmp_path / "src" / "auth.py").write_text("\n".join(f"moved {n}" for n in range(1, 121)), encoding="utf-8")
    commit_all(tmp_path, "move the auth module")
    report = UnderstandingService(tmp_path).status()
    assert report["status"] == "stale"
    assert report["changed_paths"] == ["src/auth.py"]
    assert [claim["anchor"] for claim in report["moved_claims"]] == ["src/auth.py:40-120"]


def test_status_below_the_threshold_reports_drifting(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    for name in ("a", "b", "c", "d"):
        (tmp_path / "src" / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    claims = " ".join(f"[src/{name}.py]" for name in ("a", "b", "c", "d"))
    write_understanding(tmp_path, build_document(claims={"Module map and responsibilities": f"Modules. {claims}"}))
    commit_all(tmp_path, "sources")
    UnderstandingService(tmp_path).record()
    commit_all(tmp_path, "pin")
    (tmp_path / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    commit_all(tmp_path, "touch one of four")
    report = UnderstandingService(tmp_path).status(drift_threshold=0.5)
    assert report["status"] == "drifting"
    assert report["drift_ratio"] == 0.25


def test_status_refuses_an_unpinned_document(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(tmp_path, build_document())
    with pytest.raises(UnderstandingError, match="unpinned"):
        UnderstandingService(tmp_path).status()


def test_document_mode_freshness_does_not_claim_the_code_still_matches(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    write_understanding(
        tmp_path,
        build_document(
            source="document",
            inputs=["docs/architecture.md"],
            notice="Unverified against the codebase: claims below are anchored only to the declared documents.",
            claims={"Constraints and architectural rules": "Services never share a database. [docs/architecture.md:3]"},
        ),
    )
    commit_all(tmp_path, "sources")
    UnderstandingService(tmp_path).record()
    commit_all(tmp_path, "pin")
    report = UnderstandingService(tmp_path).status()
    assert report["status"] == "fresh"
    assert "the code may still have diverged" in report["freshness_means"]


# -- context pack ---------------------------------------------------------------------


def test_project_context_finally_reaches_the_stage_four_pack(tmp_path: Path) -> None:
    """The §1.2 defect: the stub promised the Stage 4 pack and never reached it."""
    initialize_repository(tmp_path)
    task_dir, _ = create_task(tmp_path)
    context = tmp_path / ".ai-sdlc" / "project-context.md"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("# Project context\n\nRun `pytest -q`.\n", encoding="utf-8")
    payload = json.loads(ContextPackBuilder(tmp_path).build(task_dir / "task.yaml").read_text(encoding="utf-8"))
    assert "Run `pytest -q`." in payload["project_context"]
    assert payload["project_understanding"] is None


def test_a_fresh_understanding_travels_with_its_mode_and_status(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    task_dir, _ = create_task(tmp_path)
    write_understanding(tmp_path, build_document(claims={"Entry points": "Auth entry. [src/auth.py:40-120]"}))
    commit_all(tmp_path, "sources")
    UnderstandingService(tmp_path).record()
    commit_all(tmp_path, "pin")
    payload = json.loads(ContextPackBuilder(tmp_path).build(task_dir / "task.yaml").read_text(encoding="utf-8"))
    carried = payload["project_understanding"]
    assert carried["status"] == "fresh"
    assert carried["source"] == "repo"
    assert carried["unverified_against_code"] is False
    assert "Auth entry." in carried["content"]


def test_a_document_mode_understanding_always_carries_its_notice(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    task_dir, _ = create_task(tmp_path)
    write_understanding(
        tmp_path,
        build_document(
            source="document",
            inputs=["docs/architecture.md"],
            notice="Unverified against the codebase: these claims are anchored only to the declared documents.",
            claims={"Constraints and architectural rules": "Services never share a database. [docs/architecture.md:3]"},
        ),
    )
    commit_all(tmp_path, "sources")
    UnderstandingService(tmp_path).record()
    commit_all(tmp_path, "pin")
    payload = json.loads(ContextPackBuilder(tmp_path).build(task_dir / "task.yaml").read_text(encoding="utf-8"))
    carried = payload["project_understanding"]
    assert carried["unverified_against_code"] is True
    assert "unverified against the codebase" in carried["content"].lower()


def test_a_stale_understanding_is_excluded_from_the_context_pack(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    make_source_tree(tmp_path)
    task_dir, _ = create_task(tmp_path)
    write_understanding(tmp_path, build_document(claims={"Entry points": "Auth entry. [src/auth.py:40-120]"}))
    commit_all(tmp_path, "sources")
    UnderstandingService(tmp_path).record()
    commit_all(tmp_path, "pin")
    (tmp_path / "src" / "auth.py").write_text("\n".join(f"moved {n}" for n in range(1, 121)), encoding="utf-8")
    commit_all(tmp_path, "move the auth module")
    payload = json.loads(ContextPackBuilder(tmp_path).build(task_dir / "task.yaml").read_text(encoding="utf-8"))
    carried = payload["project_understanding"]
    assert carried["excluded"] is True
    assert carried["status"] == "stale"
    assert "content" not in carried, "stale orientation must not reach the implementing agent"
