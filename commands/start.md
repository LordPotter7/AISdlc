---
description: Register a new Intent, frame its brief, and stop at Gate 1
argument-hint: <title>
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Write, Edit, Glob, Grep
---

Register and frame the Intent: **$ARGUMENTS**

Read `.ai-sdlc/constitution.md` and `.ai-sdlc/project-context.md` before acting.

1. Establish the outcome, not the implementation. Ask what changes, for whom, and how they
   will know it worked. If the request already makes that clear, do not re-ask.
2. Decide the artifact mode with the user. Compact suits a change one person can hold in
   one context; full suits work crossing several phases or carrying model behavior. An
   Intent containing model behavior must be full — the runner refuses compact.
3. Register with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" new --title "<title>" --type <type> --mode <mode> --discovery-reason "<why>"`.
   `--discovery-reason` is required either way: say why discovery (`/aisdlc:understand`, `/aisdlc:baseline`) is or is not
   needed, and add `--discovery` when it is. The runner allocates the permanent identifier. Never choose
   or predict one yourself.
4. Draft the brief from `.ai-sdlc/templates/brief-template.md` into `01-brief/brief.md`.
5. Set the trigger flags honestly — `retrieval_grounded`, `contains_model_behavior`,
   `multi_tenant`, `has_side_effect`. They decide which standing regression suites are
   inherited, so understating one silently drops a mandatory suite.
6. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" validate-intent <id> --for framed` (only `intent.yaml` exists yet), then `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" advance <id> framed`.

Stop at Gate 1. Present the brief and the trigger flags for human approval, and say plainly
that you are not approving it. Gate 1 exists only once
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" gate approve <id> G1 --approver <person>` has recorded it.
