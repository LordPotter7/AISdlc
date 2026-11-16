<!-- ai-sdlc:begin -->
# AI SDLC — agent operating rules

This repository runs an adaptive, evidence-driven SDLC. A deterministic runner — not the
model — owns identifiers, state transitions, gates, resource locks, evidence metadata,
audit events, and traceability. Any agent may drive the lifecycle; none may decide it.

## Non-negotiable rules

- Never edit runner-owned paths directly: `.ai-sdlc/runtime/`, evidence manifests,
  traceability output, the audit log, or the lesson registry's state fields.
- Every Task traces to an approved requirement, finding, or decision, and declares its
  affected paths, shared resources, acceptance criteria, and verification commands.
- Every acceptance criterion carries exactly one verification class: D (deterministic),
  S (statistical), J (judged), or N (adversarial).
- The instrument that decides a criterion is not authored in the context that satisfies
  it. Held-out commands come from the specification, the verifier, or a human — never
  from the implementing agent.
- Evidence is captured by the runner, which lives in the plugin directory — outside this
  repository. That is what keeps the instrument beyond the reach of the session it judges.
  Never run a criterion's command yourself and report the result as evidence.
- Independent review means a different reviewer *and* a different model family. Self-review
  is not review.
- Preserve failed attempts and historical identifiers. Nothing is renumbered or deleted.
- Release and deployment are outside this lifecycle.

## Command surface

The runner is not installed and is not on `PATH`. It is a script inside the plugin
directory, run with any stock `python3`:

```
RUNNER="python3 <plugin-dir>/scripts/lifecycle.py"

$RUNNER --help                                     # the full surface
$RUNNER new --title "<outcome>" --type <type>      # register an Intent
$RUNNER advance <intent> framed|specified|planned|building
$RUNNER gate-packet <intent> <G1..G5>              # assemble a gate decision packet
$RUNNER gate approve <intent> <G1..G5> --approver <name>
$RUNNER ready-tasks <intent>                       # dependency- and lock-aware selection
$RUNNER start-task <task> / verify-task <task> / review-task <task>
$RUNNER converge <intent> / close <intent> --actor <name>
```

Under Claude Code the plugin directory is `${CLAUDE_PLUGIN_ROOT}`. Under any other agent,
locate the installed plugin once and substitute its path.

## What the runner will refuse

Attempting these produces an error, not a warning: executing a Task before Gate 3, marking
a Task complete without captured evidence, recording a review whose model shares a family
with the implementer's, converging with an unresolved specification-conformance gap, and
editing an approved specification without reopening its gate.
<!-- ai-sdlc:end -->
