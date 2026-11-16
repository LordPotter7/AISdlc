"""Suite-wide isolation from the developer's machine.

Gate decisions bind to a verified identity whenever an authenticated `gh` session exists
(`aisdlc.identity._github_identity`). On a contributor's machine that session is the norm,
so without isolation every test that approves a gate as a fixture name such as `human` is
refused with "the verified identity is '<login>' but the decision was recorded as ...".
CI runners have no `gh` login, which is why the suite passed there and failed locally.

Tests that exercise identity binding opt back in by monkeypatching
`identity._github_identity` themselves; a test's own monkeypatch runs after this fixture
and wins.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aisdlc import identity


def _write_unauthenticated_gh(directory: Path) -> None:
    """A `gh` stand-in that behaves like an unauthenticated CLI, for subprocess runners."""
    if sys.platform == "win32":
        (directory / "gh.cmd").write_text("@echo off\r\nexit /b 1\r\n", encoding="utf-8")
    else:
        stub = directory / "gh"
        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        stub.chmod(0o755)


@pytest.fixture(scope="session", autouse=True)
def _no_ambient_github_identity(tmp_path_factory: pytest.TempPathFactory):
    # Session scope, so module- and session-scoped fixtures that approve gates are covered too.
    stub_dir = tmp_path_factory.mktemp("gh_stub")
    _write_unauthenticated_gh(stub_dir)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(identity, "_github_identity", lambda: None)
        patch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        yield
