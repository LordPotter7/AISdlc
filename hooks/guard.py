"""Hook entry point, reached from `hooks/guard` through `hooks/run-hook.cmd`.

This file replaces the installed console script's `hook` subcommand. It imports nothing
beyond the standard library until `aisdlc.hooks` has decided the repository is
lifecycle-managed, which is what keeps an unmanaged repository cheap: the guards fire on
every Write, Edit, Task and Stop in every repository the user opens, and the overwhelming
majority of those have never run `ai-sdlc init`.

The runtime package sits at `<plugin>/scripts/aisdlc/`, so the plugin's own `scripts/`
directory goes on `sys.path` and nothing needs to be installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from aisdlc.hooks import hook_cli  # noqa: E402 - the path insert above must come first


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    if not event:
        return 0
    return hook_cli(event)


if __name__ == "__main__":
    raise SystemExit(main())
