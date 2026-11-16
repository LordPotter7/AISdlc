"""0.2.0 release hardening: commands that hang, and state writes that survive a crash."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from aisdlc.evidence import TIMEOUT_EXIT_CODE, run_verification_command
from aisdlc.storage import atomic_write_json, read_json

SLEEPER = f'"{sys.executable}" -c "import time; time.sleep(60)"'


@pytest.mark.parametrize("command", [SLEEPER, f"echo started && {SLEEPER}"], ids=["plain", "compound"])
def test_a_hanging_verification_command_is_stopped_on_time(tmp_path: Path, command: str) -> None:
    # The return code alone is not enough: an earlier version reported 124 but only after
    # the child finished on its own, because killing the shell left the child holding the pipes.
    started = time.monotonic()

    result = run_verification_command(command, tmp_path, timeout=1)

    assert time.monotonic() - started < 15
    assert result.returncode == TIMEOUT_EXIT_CODE
    assert "exceeded" in result.stderr


def test_a_passing_command_is_returned_unchanged(tmp_path: Path) -> None:
    result = run_verification_command(f'"{sys.executable}" -c "print(42)"', tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == "42"


def test_atomic_json_write_round_trips_and_leaves_no_temporary_files(tmp_path: Path) -> None:
    target = tmp_path / "state.json"

    atomic_write_json(target, {"a": 1})
    atomic_write_json(target, {"a": 2})

    assert read_json(target, None) == {"a": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_doctor_warns_when_a_lifecycle_override_is_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aisdlc.scaffold import DoctorService

    monkeypatch.delenv("AISDLC_FRAMEWORK_ROOT", raising=False)
    monkeypatch.delenv("AISDLC_HOOKS", raising=False)
    quiet = {c["check"]: c for c in DoctorService(tmp_path).run()["checks"]}
    monkeypatch.setenv("AISDLC_HOOKS", "off")
    loud = {c["check"]: c for c in DoctorService(tmp_path).run()["checks"]}

    assert quiet["environment-overrides"]["status"] == "ok"
    assert loud["environment-overrides"]["status"] == "warn"
    assert "AISDLC_HOOKS=off" in loud["environment-overrides"]["detail"]
