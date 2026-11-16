"""Wave R3 — the hook seam: fast, verified, redacting, honest, and oriented."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from aisdlc import hooks
from aisdlc.evidence import EvidenceRunner, validate_task_evidence
from aisdlc.hooks import SUPPORTED_EVENTS, HookDecision, evaluate_hook
from aisdlc.redaction import BUILTIN_PATTERNS, RedactionError, Redactor
from aisdlc.scaffold import InitService
from aisdlc.vendor import yaml

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture()
def target(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    InitService(tmp_path).run()
    (tmp_path / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "seed"], check=True)
    return tmp_path


# --- R3.1 the fast path ---------------------------------------------------------------


def test_the_hook_route_never_imports_the_cli(tmp_path: Path) -> None:
    """The claim the fast path rests on, asserted rather than assumed.

    An import added to `hooks.py` at module scope would silently reinstate the cost this
    item removed, and nothing else in the suite would notice — the guards would still
    return the right exit codes, just slowly, everywhere on the machine.
    """
    payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Edit", "tool_input": {"file_path": "x.py"}})
    guard = str(REPOSITORY / "hooks" / "guard.py")
    probe = textwrap.dedent(
        f"""
        import sys, json, runpy
        sys.argv = [{guard!r}, "pre-tool-use"]
        try:
            runpy.run_path({guard!r}, run_name="__main__")
        except SystemExit:
            pass
        watched = {{"typer", "pydantic", "yaml", "jsonschema", "argparse"}}
        names = sorted(m for m in sys.modules if m.startswith("aisdlc") or m in watched)
        print(json.dumps(names), file=sys.stderr)
        """
    )
    result = subprocess.run([sys.executable, "-c", probe], input=payload, text=True, capture_output=True, check=False)
    loaded = json.loads(result.stderr.strip().splitlines()[-1])

    assert "aisdlc.cli" not in loaded
    # `typer` and `pydantic` are gone from the tree entirely. Their stdlib replacements
    # must not be reached on this path either, or the fast path has been reinstated in
    # name only.
    assert "aisdlc.typerlite" not in loaded
    assert "argparse" not in loaded
    assert "aisdlc.config" not in loaded
    assert "aisdlc.hooks" in loaded


def test_an_unmanaged_repository_is_dismissed_without_reading_state(tmp_path: Path) -> None:
    decision = evaluate_hook(
        "pre-tool-use",
        {"cwd": str(tmp_path), "tool_name": "Write", "tool_input": {"file_path": ".ai-sdlc/runtime/state.json"}},
        tmp_path,
    )
    # A path that would be denied outright in a managed repository is allowed here.
    assert decision == HookDecision()


def test_a_missing_payload_does_not_block_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyStdin:
        @staticmethod
        def read() -> str:
            return ""

    monkeypatch.setattr(hooks.sys, "stdin", EmptyStdin())
    assert hooks.hook_main("stop") == 0


def test_an_unknown_event_is_still_rejected_on_the_fast_path() -> None:
    assert hooks.hook_cli("not-an-event") == 2


# --- R3.2 superseded: the runner pin ---------------------------------------------------
#
# ADR-0006 pinned which `ai-sdlc` the guards were permitted to run under, because bare-name
# PATH resolution let a cloned repository's `.venv/Scripts/ai-sdlc` answer for its own
# guards. ADR-0007 removed bare-name resolution entirely — hooks now name an absolute path
# under `${CLAUDE_PLUGIN_ROOT}` — so there is no name left to shadow and nothing for a pin
# to defend. The pin, its `runner.allow_local_override` escape hatch, and these four tests
# went with it.
#
# What replaced the property is asserted in tests/plugin/test_plugin_surface.py
# (`test_hook_targets_exist_and_are_extensionless`, `test_nothing_invokes_a_bare_runner`)
# and by the instrument-boundary guard in `_pre_tool_use`.


# --- R3.3 evidence redaction ----------------------------------------------------------


def test_each_builtin_shape_is_masked_and_counted() -> None:
    secrets = {
        "aws-access-key": "AKIAIOSFODNN7EXAMPLE",
        "github-token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "openai-style-key": "sk-abcdefghijklmnopqrstuvwxyz0123",
        "slack-token": "xoxb-1234567890-abcdefghij",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    }
    redactor = Redactor()
    for rule, secret in secrets.items():
        result = redactor.apply(f"connecting with {secret} now")
        assert secret not in result.text, rule
        assert result.counts.get(rule) == 1, (rule, result.counts)
        # Length-annotated, so the record stays honest about the shape it removed.
        assert f":{len(secret)}]" in result.text


def test_redaction_happens_before_the_hash(target: Path) -> None:
    """The whole point of R3.3: the secret is never in the bytes that get hashed."""
    task_directory = target / "delivery" / "intents" / "I001-x" / "04-build" / "tasks" / "I001-P00-T001"
    task_directory.mkdir(parents=True)
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"

    manifest_path = EvidenceRunner(target).capture("I001-P00-T001", task_directory, [f"echo token={secret}"])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stdout = (manifest_path.parent / manifest["commands"][0]["stdout"]).read_text(encoding="utf-8")
    assert secret not in stdout
    assert "[redacted:github-token:" in stdout
    # The manifest declares what it removed rather than masking silently.
    record = manifest["commands"][0]["redactions"]["stdout"]
    assert record["rules"] == [{"rule": "github-token", "count": 1}]
    assert record["redacted_characters"] == len(secret)
    # The recorded invocation is masked too: a credential on the command line is pinned
    # as firmly as one printed, and with attestation on it would also be signed.
    assert secret not in manifest["commands"][0]["command"]
    assert "command" in manifest["commands"][0]["redactions"]
    # And the whole manifest — including the file hashes — is over the masked bytes.
    assert secret not in manifest_path.read_text(encoding="utf-8")


def test_a_clean_capture_carries_no_redaction_record(target: Path) -> None:
    task_directory = target / "delivery" / "intents" / "I001-x" / "04-build" / "tasks" / "I001-P00-T002"
    task_directory.mkdir(parents=True)

    manifest_path = EvidenceRunner(target).capture("I001-P00-T002", task_directory, ["echo ordinary output"])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "redactions" not in manifest["commands"][0]


def test_over_broad_redaction_is_reported_rather_than_accepted(target: Path) -> None:
    """The adversarial case: a rule wide enough to hide a failing check's output."""
    config_path = target / ".ai-sdlc" / "config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["evidence"]["redact"] = [{"name": "far-too-wide", "pattern": ".+"}]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    task_directory = target / "delivery" / "intents" / "I001-x" / "04-build" / "tasks" / "I001-P00-T003"
    task_directory.mkdir(parents=True)

    manifest_path = EvidenceRunner(target).capture(
        "I001-P00-T003", task_directory, ["echo assertion failed in module foo"]
    )
    validation = validate_task_evidence(manifest_path, target, task_directory)

    assert any("above the" in error and "ceiling" in error for error in validation.errors)


def test_a_pattern_that_does_not_compile_fails_loudly() -> None:
    with pytest.raises(RedactionError, match="does not compile"):
        Redactor([("broken", "([unclosed")])


def test_redaction_can_be_turned_off(target: Path) -> None:
    config_path = target / ".ai-sdlc" / "config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["evidence"]["redact_secrets"] = False
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    task_directory = target / "delivery" / "intents" / "I001-x" / "04-build" / "tasks" / "I001-P00-T004"
    task_directory.mkdir(parents=True)
    secret = "AKIAIOSFODNN7EXAMPLE"

    manifest_path = EvidenceRunner(target).capture("I001-P00-T004", task_directory, [f"echo {secret}"])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert secret in (manifest_path.parent / manifest["commands"][0]["stdout"]).read_text(encoding="utf-8")


def test_builtin_rules_are_ordered_specific_before_generic() -> None:
    """The generic `key = value` catch-all must not absorb the shapes it would mislabel."""
    names = [name for name, _ in BUILTIN_PATTERNS]
    assert names.index("assigned-secret") == len(names) - 1
    assert names.index("github-token") < names.index("assigned-secret")


# --- R3.4 doctrine-surface conformance -------------------------------------------------


def test_the_skill_table_names_every_slash_command() -> None:
    """The drift instrument. `SKILL.md` is the model's routing table; a command missing
    from it is a command the agent will not reach for."""
    commands = {path.stem for path in (REPOSITORY / "commands").glob("*.md")}
    skill = (REPOSITORY / "skills" / "aisdlc" / "SKILL.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `/aisdlc:([a-z-]+)`", skill, re.MULTILINE))

    assert commands - documented == set(), f"commands missing from SKILL.md: {sorted(commands - documented)}"
    assert documented - commands == set(), f"SKILL.md names commands that do not exist: {sorted(documented - commands)}"


# --- R3.5 session-start orientation ----------------------------------------------------


def test_session_start_is_a_supported_event_everywhere_it_is_wired() -> None:
    from aisdlc.scaffold import HOOK_EVENTS

    plugin = json.loads((REPOSITORY / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert "SessionStart" in plugin
    assert "session-start" in json.dumps(plugin["SessionStart"])
    assert "session-start" in SUPPORTED_EVENTS
    assert ("SessionStart", None, "session-start") in HOOK_EVENTS


def test_the_digest_names_the_pending_gate_and_its_packet(target: Path) -> None:
    from aisdlc.state import StateStore

    store = StateStore(target)
    store.initialize_intent("I001")
    store.transition_intent("I001", "framed", "human")
    state = store.read()
    state["gates"]["I001:G1"] = {"status": "pending", "revision": 0}
    store.state_path.write_text(json.dumps(state), encoding="utf-8")

    decision = evaluate_hook("session-start", {"cwd": str(target)}, target)

    assert decision.exit_code == 0
    assert "I001: framed" in decision.stdout
    assert "G1" in decision.stdout
    # The decision and the brief for making it arrive together.
    assert "gate-packet I001 G1" in decision.stdout


def test_the_digest_is_silent_when_there_is_nothing_to_report(target: Path) -> None:
    assert evaluate_hook("session-start", {"cwd": str(target)}, target) == HookDecision()


def test_the_digest_never_blocks_a_session(target: Path) -> None:
    """Orientation is a convenience. A session that cannot be oriented still starts."""
    (target / ".ai-sdlc" / "runtime" / "state.json").write_text("{ not json", encoding="utf-8")

    assert evaluate_hook("session-start", {"cwd": str(target)}, target).exit_code == 0


def test_release_review_secret_shapes_are_masked_under_their_own_rule() -> None:
    """Shapes added in the 0.2.0 release review.

    Before it, an Anthropic key was masked only by the generic `sk-` rule (so the manifest
    named the wrong provider), and GitHub fine-grained PATs, npm tokens, and Stripe live
    keys were not masked at all when printed bare.
    """
    secrets = {
        "anthropic-key": "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0" * 4 + "-AbCdEfAA",
        "github-fine-grained-pat": "github_pat_11ABCDEFG0123456789_"
        + "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTU",
        "npm-token": "npm_" + "abcdefghijklmnopqrstuvwxyz0123456789",
        "stripe-key": "sk_live_" + "51HabcdefghijklmnopqrstuvWXYZ",
    }
    redactor = Redactor()
    for rule, secret in secrets.items():
        result = redactor.apply(f"connecting with {secret} now")
        assert secret not in result.text, rule
        assert result.counts == {rule: 1}, (rule, result.counts)


def test_ordinary_output_is_not_redacted() -> None:
    """Negative cases: a redactor that masks test output hides failures."""
    benign = [
        "npm_config_cache=/tmp/x",
        "AssertionError: expected 3 == 4 in test_skeleton",
        "sk-learn is spelled scikit-learn",
        "github_pat is the env var name we read",
        "collected 470 items; 470 passed in 6.03s",
    ]
    redactor = Redactor()
    for line in benign:
        assert redactor.apply(line).counts == {}, line


def test_tokens_glued_to_an_identifier_are_still_masked() -> None:
    """Final release review: `\b` treats `_` as a word character, so `MY_npm_...` slipped through."""
    redactor = Redactor()
    for prefix in ("TOKEN_", "MY_", "KEY=", "("):
        for rule, secret in {
            "npm-token": "npm_" + "a1" * 18,
            "stripe-key": "sk_live_" + "b2" * 10,
            "anthropic-key": "sk-ant-api03-" + "c3" * 15,
            "github-fine-grained-pat": "github_pat_" + "d4" * 30,
            "github-token": "ghp_" + "e5" * 15,
        }.items():
            result = redactor.apply(f"{prefix}{secret} done")
            assert secret not in result.text, (prefix, rule)
            assert result.counts == {rule: 1}, (prefix, rule, result.counts)
    # A prefix glued to a *letter* stays unmasked on purpose: otherwise ordinary words such as
    # `task-<long slug>` would be masked as `sk-` keys and hide real test output.
    assert redactor.apply("task-" + "abcdefghijklmnopqrstuvwxyz").counts == {}
