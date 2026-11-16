"""Wave R1 — one version, one schema generation, and a handshake between them."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisdlc.migrate import MigrationError, MigrationService, assert_readable, record_version, stamp
from aisdlc.scaffold import DoctorService, InitService
from aisdlc.state import StateStore
from aisdlc.vendor import yaml
from aisdlc.versioning import (
    SCHEMA_VERSION,
    compatible,
    manifest_versions,
    scaffold_version,
    version_consistency,
)

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture()
def target(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


# --- R1.1 single version source ------------------------------------------------------


def test_the_three_declared_versions_agree() -> None:
    """pyproject.toml is the authority; the manifests Claude Code reads must match it."""
    assert version_consistency(REPOSITORY) == []
    versions = manifest_versions(REPOSITORY)
    assert len({versions["pyproject"], versions["plugin"], versions["marketplace"]}) == 1


def test_a_mismatched_manifest_is_reported_against_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")
    plugin = tmp_path / ".claude-plugin"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(json.dumps({"name": "aisdlc", "version": "1.0.0"}), encoding="utf-8")
    (plugin / "marketplace.json").write_text(json.dumps({"metadata": {"version": "9.9.9"}}), encoding="utf-8")

    problems = version_consistency(tmp_path)

    assert len(problems) == 1
    assert "plugin.json declares 1.0.0" in problems[0]
    assert "9.9.9" in problems[0]


def test_check_versions_exits_non_zero_on_a_mismatch(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "aisdlc.entry", "check-versions", "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1


# --- R1.2 schema versioning and migration --------------------------------------------


def test_an_absent_schema_version_reads_as_generation_zero() -> None:
    assert record_version({}) == 0
    assert record_version({"schema_version": 3}) == 3
    # A record that lies about its generation with a non-integer is read as the oldest
    # generation rather than trusted, so it migrates forward instead of being skipped.
    assert record_version({"schema_version": "seven"}) == 0


def test_a_record_from_a_newer_generation_is_refused_by_name() -> None:
    with pytest.raises(MigrationError) as error:
        assert_readable({"schema_version": SCHEMA_VERSION + 1}, "runtime state")
    message = str(error.value)
    assert str(SCHEMA_VERSION + 1) in message
    assert str(SCHEMA_VERSION) in message
    # The remedy must be "upgrade the runner", never "edit the record".
    assert "Upgrade the runner" in message


def test_state_written_by_the_runner_carries_the_current_generation(target: Path) -> None:
    InitService(target).run()
    store = StateStore(target)
    store.initialize_intent("I001")

    written = json.loads(store.state_path.read_text(encoding="utf-8"))

    assert written["schema_version"] == SCHEMA_VERSION


def test_the_runner_refuses_to_read_state_from_a_newer_generation(target: Path) -> None:
    InitService(target).run()
    store = StateStore(target)
    state = store.read()
    state["schema_version"] = SCHEMA_VERSION + 1
    store.state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(MigrationError):
        store.read()


def test_migrate_is_a_dry_run_until_apply(target: Path) -> None:
    InitService(target).run()
    store = StateStore(target)
    stale = store.read()
    stale.pop("schema_version", None)
    store.state_path.write_text(json.dumps(stale), encoding="utf-8")

    service = MigrationService(target)
    planned = service.plan()
    assert planned["status"] == "pending"
    assert planned["pending"][0]["from"] == 0
    # Planning must not have touched the file.
    assert "schema_version" not in json.loads(store.state_path.read_text(encoding="utf-8"))

    applied = service.apply()
    assert applied["status"] == "migrated"
    assert json.loads(store.state_path.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION

    # Idempotent: a second run is a no-op rather than a second migration.
    assert MigrationService(target).apply()["status"] == "current"


def test_migration_refuses_to_move_records_backwards(target: Path) -> None:
    InitService(target).run()
    store = StateStore(target)
    state = {"intents": {}, "tasks": {}, "gates": {}, "schema_version": SCHEMA_VERSION + 1}
    store.state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(MigrationError, match="newer schema generation"):
        MigrationService(target).apply()


def test_captured_evidence_is_tolerated_rather_than_rewritten(target: Path) -> None:
    """The load-bearing asymmetry: immutable records migrate by being read, not rewritten.

    An evidence manifest is hashed into its index and may be signed. Stamping one would
    invalidate both, so a migration that "fixed" it would destroy the property the record
    exists to carry.
    """
    InitService(target).run()
    manifest = target / "delivery" / "intents" / "I001-x" / "04-build" / "evidence" / "attempt-1" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    original = {"task_id": "I001-P00-T001", "attempt": 1, "authority": "provisional"}
    manifest.write_text(json.dumps(original), encoding="utf-8")

    report = MigrationService(target).apply()

    assert any("manifest.json" in path for path in report["tolerated_immutable"])
    assert not any("manifest.json" in entry["path"] for entry in report["pending"])
    assert json.loads(manifest.read_text(encoding="utf-8")) == original


def test_every_schema_admits_a_schema_version() -> None:
    schemas = sorted((REPOSITORY / ".ai-sdlc" / "schemas").glob("*.json"))
    assert len(schemas) == 15  # S2.2 threat-model, S3.2 glossary, phase, ADR-010 pipeline
    for path in schemas:
        properties = json.loads(path.read_text(encoding="utf-8"))["properties"]
        assert "schema_version" in properties, path.name


# --- R1.3 compatibility handshake ----------------------------------------------------


def test_compatibility_is_minor_level() -> None:
    assert compatible("0.2.0", "0.2.7")
    assert compatible("0.2.0", "0.2.0+editable")
    assert not compatible("0.2.0", "0.3.0")
    assert not compatible("0.2.0", "1.2.0")
    # An unknown version on either side is not evidence of incompatibility.
    assert compatible(None, "0.2.0")


def test_init_records_the_runner_that_scaffolded_the_repository(target: Path) -> None:
    InitService(target).run()

    recorded = scaffold_version(target)

    assert recorded is not None
    assert recorded == manifest_versions(REPOSITORY)["pyproject"]
    # Read straight from YAML too, so the hook fast path's regex reader agrees with the
    # configuration model.
    payload = yaml.safe_load((target / ".ai-sdlc" / "config.yaml").read_text(encoding="utf-8"))
    assert payload["runner_version"] == recorded


def test_doctor_names_a_scaffold_version_mismatch_and_its_remedy(target: Path) -> None:
    InitService(target).run()
    config_path = target / ".ai-sdlc" / "config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["runner_version"] = "99.0.0"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = DoctorService(target).run()
    check = next(item for item in report["checks"] if item["check"] == "compatibility")

    assert check["status"] == "warn"
    assert "99.0.0" in check["detail"]
    assert "ai-sdlc migrate" in check["detail"]


def test_stamp_is_what_every_writer_goes_through() -> None:
    assert stamp({"a": 1}) == {"a": 1, "schema_version": SCHEMA_VERSION}


# --- Templates stay valid against the schemas they document --------------------------------

SAMPLE_VALUES = {
    "INTENT_ID": "I001",
    "PHASE_ID": "I001-P00",
    "TASK_ID": "I001-P00-T001",
    "REQUIREMENT_ID": "REQ-F-001",
    "ISO_8601_TIMESTAMP": "2026-09-25T09:00:00+00:00",
}


def _filled(text: str) -> str:
    import re

    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", lambda match: SAMPLE_VALUES.get(match.group(1), "sample"), text)


@pytest.mark.parametrize(
    ("template", "schema", "select", "runner_adds"),
    [
        ("findings-template.yaml", "finding.schema.json", lambda doc: doc["findings"][0], {}),
        ("lesson-template.yaml", "lesson.schema.json", lambda doc: doc, {}),
        ("phase-template.yaml", "phase.schema.json", lambda doc: doc, {}),
        (
            "pre-gate-review-template.json",
            "pre-gate-review.schema.json",
            lambda doc: {key: value for key, value in doc.items() if key != "_comment"},
            {},
        ),
        # `review-task` adds these four fields itself; the template must not ask the reviewer for them.
        (
            "review-template.yaml",
            "review.schema.json",
            lambda doc: doc,
            {
                "task_id": "I001-P00-T001",
                "implementer_agent_id": "implementer",
                "evidence_manifest": "e/manifest.json",
                "recorded_at": "2026-09-25T09:00:00+00:00",
            },
        ),
    ],
)
def test_template_is_valid_against_its_schema(template, schema, select, runner_adds) -> None:  # noqa: ANN001
    from aisdlc.jsonschema_mini import Draft202012Validator
    from aisdlc.vendor import yaml as vendored_yaml

    text = _filled((REPOSITORY / ".ai-sdlc" / "templates" / template).read_text(encoding="utf-8"))
    document = json.loads(text) if template.endswith(".json") else vendored_yaml.safe_load(text)
    instance = {**select(document), **runner_adds}
    validator = Draft202012Validator(json.loads((REPOSITORY / ".ai-sdlc" / "schemas" / schema).read_text("utf-8")))
    assert [error.message for error in validator.iter_errors(instance)] == []
    if runner_adds:
        assert not set(runner_adds) & set(document), "the template asks for fields the runner writes"
