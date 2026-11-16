---
description: Record a human gate decision, or show which gate is awaiting one
argument-hint: <intent-id> [G1|G2|G2-G3|G3|G4|G5] [approver]
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read
---

Gate handling for Intent **$1**.

Runner state: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" status $1`

If no gate was named, report which gate is awaiting a decision, what it authorizes, and what
the user should examine before deciding.

If a gate and approver were given, record it with
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" gate approve $1 $2 --approver $3`.

A gate is a human decision. You may prepare and present the evidence for one; you may never
decide one on the user's behalf, or infer approval from their enthusiasm. If the user has
not explicitly approved this specific gate, ask.

What each gate authorizes:

- **G1** — the framed brief and its trigger flags.
- **G2** — the specification. Approving freezes it against edits.
- **G3** — the plan. This is what permits any Task to execute.
- **G2-G3** — the combined review, for compact Intents only.
- **G4** — release of the phases after the `P00` walking skeleton.
- **G5** — final acceptance, the precondition for close.

Reopening an approved gate is recorded and invalidates evidence pinned to it. Say so before
reopening one.
