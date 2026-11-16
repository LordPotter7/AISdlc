from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from aisdlc import typerlite
from aisdlc.cli import app
from aisdlc.typerlite import CliRunner

runner = CliRunner()


def test_new_creates_a_registered_intent(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "new",
            "--discovery-reason",
            "greenfield fixture; nothing to discover",
            "--title",
            "Status report",
            "--type",
            "feature",
            "--mode",
            "compact",
            "--repo",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["intent_id"] == "I001"
    assert Path(payload["path"]).name == "I001-status-report"
    assert (Path(payload["path"]) / "00-intent" / "intent.yaml").exists()
    assert (tmp_path / ".ai-sdlc" / "runtime" / "audit.jsonl").exists()


def test_status_reports_the_next_legal_action(tmp_path: Path) -> None:
    created = runner.invoke(
        app,
        [
            "new",
            "--discovery-reason",
            "greenfield fixture; nothing to discover",
            "--title",
            "Status",
            "--type",
            "feature",
            "--repo",
            str(tmp_path),
        ],
    )
    intent_id = json.loads(created.output)["intent_id"]
    result = runner.invoke(app, ["status", intent_id, "--repo", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "initialized"
    assert payload["next_legal_actions"] == ["frame intent", "abort"]


def test_gate_rejects_unknown_gate(tmp_path: Path) -> None:
    created = runner.invoke(
        app,
        [
            "new",
            "--discovery-reason",
            "greenfield fixture; nothing to discover",
            "--title",
            "Gate",
            "--type",
            "feature",
            "--repo",
            str(tmp_path),
        ],
    )
    intent_id = json.loads(created.output)["intent_id"]
    result = runner.invoke(app, ["gate", "approve", intent_id, "G9", "--approver", "human", "--repo", str(tmp_path)])
    assert result.exit_code == 2
    assert "G1 through G5" in result.output


# -- repeatable list options (typerlite) -----------------------------------------------------


def _new(repo: Path, title: str) -> str:
    result = runner.invoke(
        app,
        [
            "new",
            "--discovery-reason",
            "fixture",
            "--title",
            title,
            "--type",
            "feature",
            "--mode",
            "compact",
            "--repo",
            str(repo),
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["intent_id"]


def test_list_options_repeat_and_keep_whole_values() -> None:
    toy = typerlite.Typer()
    seen: dict[str, object] = {}

    @toy.command("go")
    def go(
        tags: Annotated[list[str] | None, typerlite.Option("--tag")] = None,
        sizes: Annotated[list[int] | None, typerlite.Option("--size")] = None,
    ) -> None:
        seen.update(tags=tags, sizes=sizes)

    assert runner.invoke(toy, ["go", "--tag", "I001-P00-T003"]).exit_code == 0
    assert seen == {"tags": ["I001-P00-T003"], "sizes": None}
    assert runner.invoke(toy, ["go", "--tag", "a", "--tag", "b", "--size", "3"]).exit_code == 0
    assert seen == {"tags": ["a", "b"], "sizes": [3]}
    # The declared default is never mutated by a previous invocation.
    assert runner.invoke(toy, ["go"]).exit_code == 0
    assert seen == {"tags": None, "sizes": None}


def test_coapprove_resource_accepts_two_intents(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "coapprove-resource",
            "schema:orders",
            "--intent",
            "I001",
            "--intent",
            "I002",
            "--approver",
            "architect",
            "--reason",
            "coordinated expand/contract",
            "--repo",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["intents"] == ["I001", "I002"]


def test_program_add_requires_a_whole_intent_id(tmp_path: Path) -> None:
    first, second = _new(tmp_path, "Ledger"), _new(tmp_path, "Ledger export")
    program = json.loads(
        runner.invoke(app, ["program", "new", "--title", "Billing", "--owner", "o", "--repo", str(tmp_path)]).output
    )["id"]
    assert runner.invoke(app, ["program", "add", program, first, "--repo", str(tmp_path)]).exit_code == 0
    added = runner.invoke(app, ["program", "add", program, second, "--requires", first, "--repo", str(tmp_path)])
    assert added.exit_code == 0, added.output
    status = json.loads(runner.invoke(app, ["program", "status", program, "--repo", str(tmp_path)]).output)
    member = next(item for item in status["members"] if item["intent_id"] == second)
    assert member["blocking_predecessors"] == [first]


def test_intent_resources_declares_each_resource(tmp_path: Path) -> None:
    intent_id = _new(tmp_path, "Orders schema")
    result = runner.invoke(
        app,
        [
            "intent-resources",
            intent_id,
            "--declare",
            "schema:orders",
            "--declare",
            "prompt:summary",
            "--repo",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert sorted(json.loads(result.output)["declared_resources"]) == ["prompt:summary", "schema:orders"]


def test_validate_intent_checks_only_what_the_next_stage_needs(tmp_path: Path) -> None:
    intent_id = _new(tmp_path, "Staged validation")
    repo = ("--repo", str(tmp_path))

    def check(*extra: str) -> dict:
        return json.loads(runner.invoke(app, ["validate-intent", intent_id, *extra, *repo]).output)

    assert check("--for", "framed")["valid"] is True  # only intent.yaml exists at Stage 1
    assert check("--for", "specified")["valid"] is False  # no requirements.yaml yet
    intent_root = next((tmp_path / "delivery" / "intents").glob(f"{intent_id}-*"))
    (intent_root / "02-specification").mkdir(parents=True, exist_ok=True)
    (intent_root / "02-specification" / "requirements.yaml").write_text(
        json.dumps(
            {
                "intent_id": intent_id,
                "functional_requirements": [
                    {"id": "REQ-F-001", "statement": "The system shall log.", "acceptance_criteria": ["AC-001"]}
                ],
                "non_functional_requirements": [],
                "acceptance_criteria": [
                    {"id": "AC-001", "verification_class": "D", "statement": "Logs.", "check": "python -c pass"}
                ],
                "success_criteria": [{"id": "SC-001", "statement": "AC-001 passes."}],
            }
        ),
        encoding="utf-8",
    )
    assert check("--for", "specified")["valid"] is True
    complete = check()
    assert complete["for"] == "building" and complete["valid"] is False
    assert any("no Task" in error for error in complete["errors"])
    refused = runner.invoke(app, ["validate-intent", intent_id, "--for", "converging", *repo])
    assert refused.exit_code != 0
