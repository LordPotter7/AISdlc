---
description: Execute approved, ready Tasks under the runner's gates, locks, and retry limits
argument-hint: <intent-id|task-id>
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Write, Edit, Glob, Grep, Task
---

Execute approved work for **$1**.

Runner state: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" status $1`
Ready Tasks: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" ready-tasks $1`

Execution is gated. The runner refuses to start a Task unless the Intent is `building`,
Gate 3 is approved, and — for any phase after `P00` — Gate 4 is approved. If the runner
refuses, report the refusal and the missing approval. Never work around it.

For each Task you take:

1. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" context-pack <task-id>` — work from the focused pack, not the whole repository.
2. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" start-task <task-id>`.
3. Implement only within the Task's declared affected paths. Needing a change outside them
   means the Task was mis-scoped: stop and supersede it.
4. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" verify-task <task-id>` — captures evidence and moves the Task to `review`.
   Locally captured evidence is **provisional**. Your own account of a passing test is not
   evidence; only the runner's captured commands, exit codes, and hashes are.
5. Delegate independent review to the `aisdlc-verifier` agent from a fresh context, and to
   `aisdlc-red-team` for adversarial criteria. Self-review is not independent review, and
   the runner refuses a review whose reviewer identity matches the implementer.
6. Register the decision with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" review-task`.

When delegating with the Task tool, begin the description with the permanent
`[Ixxx-Pxx-Txxx]` identifier. The guard hook blocks delegation without it.

On failure, preserve the attempt — never rewrite it. Two retries are allowed; a third
failure blocks the Task and produces an escalation package for a human. Report that, stop,
and do not attempt a fourth.
