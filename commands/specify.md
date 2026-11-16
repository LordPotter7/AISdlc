---
description: Turn a framed Intent into testable requirements and criteria, and stop at Gate 2
argument-hint: <intent-id>
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Write, Edit, Glob, Grep
---

Specify Intent **$1**.

Runner state: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" status $1`

Read the verification reference in the `aisdlc` skill before writing any criterion.

1. Write requirements into `02-specification/requirements.yaml` from
   `.ai-sdlc/templates/requirements-template.yaml`.
2. Give every acceptance criterion exactly one verification class:
   - **D** — deterministic. A command exits zero or non-zero. Name the exact command.
   - **S** — statistical. A rate over a versioned dataset, with threshold and tolerance
     predeclared *before* any run.
   - **J** — judged. A pinned judge prompt, a calibration set, and an agreement floor.
   - **N** — adversarial. An attack suite where the only passing result is zero successes.
   A criterion carrying two classes, or none, is not specifiable — split it.
3. Prefer D. Reach for S, J, or N only when the behavior genuinely cannot be settled by a
   command, because each carries a dataset, a pin, and a recurring cost.
4. Every requirement traces to the approved brief — record it in `sources`, with a
   `rationale`. Anything that does not trace is scope growth: stop and ask rather than
   absorbing it. Scope the user explicitly defers stays in the file as `priority: wont`.
5. Write each requirement as one obligation ("When <trigger>, the <system> shall
   <response>"). Give every non-functional requirement a `category` and a `measure`
   (metric, target, unit, condition). Mark `personal_data`, `data_classification`, and
   `compliance` controls where they apply — the user knows which regimes bind them; ask.
   If the Intent exposes or changes an interface, put the contract (OpenAPI 3, AsyncAPI
   2/3, or JSON Schema) under `02-specification/contracts/`, declare it in `contracts`, and
   cover every operation with a criterion's `contract_operations` — or exclude it with a
   reason. Give each contract a stable `api` id so the runner can compare it with other
   Intents; run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" contract-registry $1`
   and put any breaking change or concurrent change in front of the user — accepting a
   breaking change is their decision, recorded with a name and reason. If the Intent is
   security work, multi-tenant, or side-effecting, write
   `02-specification/threat-model.yaml` from `.ai-sdlc/templates/threat-model-template.yaml`:
   STRIDE per trust boundary, each mitigated threat proven by a Class N criterion. Full
   mode requires it; in compact mode, recommend full mode instead. Threats promoted from
   earlier Intents are inherited: answer each with `inherited_from`.
   Use terms from `.ai-sdlc/glossary.yaml` as defined there; never redefine one locally.
   Accessibility is in scope only as a WCAG target checked by a Class D automated checker,
   with a named `manual_audit` owner for the rest; other front-end work is out of scope.
6. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" spec-lint $1`. Fix every
   finding you can; for each warning you leave, say why to the user. Show the
   `quality_coverage` and name any quality category that is silent, so the gate reviewer
   sees the omission as a decision.
7. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" validate-intent $1 --for specified` (the plan does not exist yet, so the complete check would report missing Tasks), then `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" advance $1 specified`.
   `advance` refuses lint findings at or above `specification.fail_on`.
8. Delegate an adversarial review of the specification to the `aisdlc-red-team` agent from
   a fresh context, and register it with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" pre-gate-review $1 G2 --from <file>`.
   The runner refuses Gate 2 without one, and refuses one that covers an earlier version of
   the requirements. A defect found here costs a rewrite; the same defect found at Task
   review costs the implementation, its evidence, and a slice of the retry budget.

The review informs the gate; it does not decide it. `changes_required` from a reviewer and
approval from a human are both legitimate — present both.

If Gate 2 was reopened, present the G2 gate packet's `specification_diff` before anything
else: which requirements were added, changed, removed, or reprioritised since the approved
baseline, and which Tasks lose their evidence if this version is approved. That list is the
price of the change; the human should see it before deciding.

Stop at Gate 2. Once approved, the specification is frozen — the guard hook refuses edits to
it, and reopening the gate is a recorded act that invalidates evidence pinned to it. Say so
before presenting.
