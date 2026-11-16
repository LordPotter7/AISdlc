"""Measure what the guard hooks cost in a repository that never adopted the lifecycle.

The plugin loads in every repository the user opens, and `PreToolUse` fires on every
`Write`, `Edit`, `NotebookEdit`, `Task`, and `ExitPlanMode`. In an unmanaged repository
the correct answer is "exit 0, silently" — but the framework's own doctrine is that a
claim without a measurement is an assumption, and "inert" was being claimed about exit
codes while the cost was interpreter startup.

This reports both routes so the difference is a number rather than an argument:

* `fast`   — `aisdlc.entry:main`, which dispatches to the guard before importing the CLI.
* `full`   — the old route, importing `aisdlc.cli` first, kept here as the baseline.

Run it in the repository you care about; it creates its own unmanaged scratch directory
either way, so it never depends on where you point it.

    python scripts/measure_hook_latency.py --runs 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time

PAYLOAD = {"cwd": "", "tool_name": "Edit", "tool_input": {"file_path": "README.md"}}

FAST = "from aisdlc.entry import main; import sys; sys.argv = ['ai-sdlc', 'hook', 'pre-tool-use']; main()"
FULL = "from aisdlc.cli import app; import sys; sys.argv = ['ai-sdlc', 'hook', 'pre-tool-use']; app()"


def time_route(code: str, payload: str, runs: int) -> list[float]:
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", code],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
        )
        timings.append((time.perf_counter() - started) * 1000)
    return timings


def summarize(name: str, timings: list[float]) -> dict[str, float | str]:
    return {
        "route": name,
        "runs": len(timings),
        # Median rather than mean: process startup has a long right tail on Windows and
        # one antivirus scan should not decide the reported number.
        "median_ms": round(statistics.median(timings), 1),
        "min_ms": round(min(timings), 1),
        "max_ms": round(max(timings), 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=15)
    arguments = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as scratch:
        payload = json.dumps({**PAYLOAD, "cwd": scratch})
        fast = time_route(FAST, payload, arguments.runs)
        full = time_route(FULL, payload, arguments.runs)

    report = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "case": "unmanaged repository, PreToolUse/Edit",
        "measurements": [summarize("fast", fast), summarize("full", full)],
    }
    fast_median = statistics.median(fast)
    full_median = statistics.median(full)
    report["speedup"] = round(full_median / fast_median, 2) if fast_median else None
    report["saved_ms"] = round(full_median - fast_median, 1)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
