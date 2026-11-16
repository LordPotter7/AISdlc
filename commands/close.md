---
description: Record the completion result and promote lessons, after Gate 5 only
argument-hint: <intent-id>
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Write, Edit, Glob, Grep
---

Close Intent **$1**.

Runner state: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" status $1`
Convergence: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" converge $1`

Close only after Gate 5 is approved. If it is not, stop and say so.

1. Write the completion result from `.ai-sdlc/templates/result-template.md`, including what
   was observed that the plan did not anticipate.
2. Promote lessons with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" lesson`. Every lesson needs an explicit firing target:
   - a **regression eval** — fires on its own, the strongest target;
   - a **policy** — enforced by the runner or a hook;
   - a **checklist** item — subject to the checklist cap;
   - a **context heuristic** — the weakest, because it relies on someone reading it.
   Prefer the strongest target the finding supports. A lesson that fires nowhere is a note.
3. Every Critical and High finding must have a blocking target before close.
4. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" close $1 --actor <person>`, then `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" loop-metrics`.

Release and deployment are outside this lifecycle. Do not tag, publish, or deploy.
