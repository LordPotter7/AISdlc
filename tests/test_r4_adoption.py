"""Wave R4 — verified on the platform it runs on, and observable by other agents."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aisdlc import identity
from aisdlc.conformance import _approver_detail
from aisdlc.identity import ApproverIdentity, IdentityError, resolve
from aisdlc.scaffold import InitService
from aisdlc.state import StateStore, TransitionError
from aisdlc.vendor import yaml

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture()
def target(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    InitService(tmp_path).run()
    return tmp_path


# --- R4.1 Windows CI leg ---------------------------------------------------------------


def test_declared_versions_are_kept_in_one_manifest() -> None:
    """`check-versions` ran in CI. There is no CI, so the contract moved to a manifest.

    `.version-bump.json` names every file that carries the plugin's version, following the
    pattern in `obra/superpowers`. `tools/bump-version.py` writes them together and
    `tests/plugin/test_plugin_surface.py::test_declared_versions_agree` fails on drift —
    no runner subcommand and no pipeline involved.
    """
    manifest = json.loads((REPOSITORY / ".version-bump.json").read_text(encoding="utf-8"))
    declared = {entry["path"] for entry in manifest["files"]}
    assert ".claude-plugin/plugin.json" in declared
    assert ".claude-plugin/marketplace.json" in declared
    assert "pyproject.toml" in declared
    for entry in manifest["files"]:
        assert (REPOSITORY / entry["path"]).exists(), entry["path"]


# --- R4.3 verifiable gate identities ---------------------------------------------------


def test_an_unverified_identity_is_recorded_as_declared_not_as_verified(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identity, "_github_identity", lambda: None)
    store = StateStore(target)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", "human")

    store.approve_gate("I001", "G1", "alex")

    record = store.read()["gates"]["I001:G1"]
    assert record["approver"] == "alex"
    assert record["identity_strength"] == "declared"
    assert record["identity_method"] == "asserted"


def test_a_verified_identity_is_bound_to_the_gate_record(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_github_identity", lambda: ("alex", "gh-auth"))
    store = StateStore(target)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", "human")

    store.approve_gate("I001", "G1", "alex")

    record = store.read()["gates"]["I001:G1"]
    assert record["identity_strength"] == "verified"
    assert record["identity_detail"] == "alex"


def test_require_verified_refuses_a_gate_with_no_verifiable_identity(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identity, "_github_identity", lambda: None)
    config_path = target / ".ai-sdlc" / "config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["gates"]["require_verified"] = True
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    store = StateStore(target)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", "human")

    with pytest.raises(TransitionError, match="no verifiable identity"):
        store.approve_gate("I001", "G1", "alex")


def test_approving_as_somebody_else_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The case an accountability record exists to prevent, and the reason the mismatch is
    not silently resolved in either direction."""
    monkeypatch.setattr(identity, "_github_identity", lambda: ("alex", "gh-auth"))

    with pytest.raises(IdentityError, match="under your own identity"):
        resolve("sam", tmp_path, require_verified=False)


def test_default_behaviour_is_unchanged_for_a_solo_operator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_github_identity", lambda: None)
    resolved = resolve("alex", tmp_path, require_verified=False)
    assert resolved == ApproverIdentity(approver="alex", strength="declared", method="asserted")


def test_the_conformance_export_does_not_overstate_a_declared_identity() -> None:
    declared = {"I001:G1": {"approver": "alex", "identity_strength": "declared"}}
    verified = {"I001:G1": {"approver": "alex", "identity_strength": "verified"}}

    assert "not verified" in _approver_detail(declared)
    assert "not verified" not in _approver_detail(verified)
    assert _approver_detail({}) == "No gate decisions recorded"


# --- R4.4 read-only MCP surface --------------------------------------------------------


def _call(method: str, params: dict | None = None, message_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}}
