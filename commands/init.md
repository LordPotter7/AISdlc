---
description: Scaffold the AI SDLC framework into this repository and report what was written
argument-hint: [compact|full]
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read
---

Initialize lifecycle-managed engineering in this repository.

Scaffold result: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" init --mode ${1:-compact}`
Readiness: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" doctor`

Report to the user, in this order:

1. What was created, merged, and skipped. A `skipped` step means the file already existed
   and was left alone; that is the normal result of a re-run.
2. Any `failed` step, with the reason and what to do about it.
3. Every `warn` or `fail` readiness check.
4. The single next legal action.

Two results are worth calling out explicitly when they appear:

- `.claude/settings.json` skipped. That is correct, not a failure: the plugin registers
  the guardrails for every repository, and `init` no longer writes project entries at all
  (ADR-007).
- A `runner` check that is not `ok`. It reports the `python3` the hook launcher would find.
  Nothing works without one, and there is nothing this framework can install to fix it —
  the user needs Python 3.12 or newer on `PATH`.

Then tell them `.ai-sdlc/project-context.md` was written as a stub and needs this
repository's own conventions and verification commands filled in, because those become
Class D criteria. Offer to draft it from what is actually in the repository. Do not
invent conventions you have not verified.
