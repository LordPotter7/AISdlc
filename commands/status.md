---
description: Report lifecycle state, gates, ready Tasks, and the next legal action
argument-hint: [intent-id]
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Glob, Grep
---

Runner state: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" status $1`
Ready Tasks: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" ready-tasks $1`

Report:

- Committed source state versus runner state, and any divergence between them.
- Gate status, naming the next gate awaiting a human decision.
- Ready Tasks, and for each blocked Task the specific reason it is blocked.
- Evidence: which Tasks hold a manifest pinned to the current commit, and which are stale.
- Open findings, with Critical and High called out separately because they block close.
- The single next legal action.

The runner's output is the source of truth. If the runner did not record it, it did not
happen — never describe a gate, transition, or evidence result the runner does not show.
