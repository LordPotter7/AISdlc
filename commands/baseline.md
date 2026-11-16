---
description: Pin an existing repository's current behaviour before the first Intent
argument-hint: <probes-file> [--label <text>]
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Glob, Grep, Write
---

Capture a characterization baseline from **$1**.

Current baselines: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" baseline capture $1 $2 $3`

This is the entry point for a brownfield repository — which is what "any size of
application" usually means in practice. Existing systems arrive with no requirements, no
evidence, and no tests worth trusting, and the lifecycle's normal assumption (state what
the system should do, then prove it does) produces one of two bad outcomes:

- a **fiction**, where requirements are reverse-engineered from the code and the current
  bugs get written down as intended behaviour; or
- **paralysis**, where nothing can start until the whole system is specified.

A baseline avoids both by asserting nothing about correctness. It records what the system
does *today*, hashed and pinned to a commit. A failing test suite is a fact about the
repository, and recording it as the reference is the point — the next Intent must not make
it worse, which is a claim the runner can actually check.

When writing the probes file, prefer probes that:

- **Characterize behaviour**: the existing test suite, smoke commands, golden outputs.
- **Carry a metric**: pass counts, coverage, mutation score. Declare the `metric` regex so
  the value is extracted the same way every time — a heuristic would silently change
  meaning when a tool reformats its output, and a moving reference is worse than none.
- **Encode architecture as fitness functions**: dependency direction, layer boundaries,
  cycle counts. These are ordinary Class D criteria — a command that exits non-zero on
  violation — and the baseline is where the repository learns its current violation count.

Afterwards, the first Intents state criteria *relative* to the baseline ("no regression
against BASELINE-001", "mutation score does not decrease") using `baseline_ref` on the
criterion. Honest, cheap, enforceable on day one.
