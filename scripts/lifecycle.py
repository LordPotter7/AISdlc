#!/usr/bin/env python3
"""Command-line entry point for the lifecycle runner.

Invoked by the plugin's commands as:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" <command> [args...]

There is nothing to install. The runtime package sits beside this file at
`scripts/aisdlc/`, so putting this directory on `sys.path` is the whole bootstrap — the
same `sys.path.insert` idiom the first-party `claude-security` plugin uses for its own
`scripts/lib/`.

`hooks/guard.py` is the other entry point and deliberately does *not* come through here:
it reaches `aisdlc.hooks` directly so an unmanaged repository never pays for importing
the CLI surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aisdlc.cli import app  # noqa: E402 - the path insert above must come first

if __name__ == "__main__":
    app()
