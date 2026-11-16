# Master Plan: {{INTENT_ID}} — {{INTENT_TITLE}}

## Architecture

Architecture is recorded as decisions, not prose. Each one is registered with
`ai-sdlc decision record`, carries a permanent `Ixxx-Dxxx` identifier and its own content
hash, and is what a Task names in `source_decisions`. This table is the index; the records
under `03-planning/decisions/` are the substance.

| Decision | Title | Status | Serves | Implemented by |
|---|---|---|---|---|
| `{{DECISION_ID}}` | {{TITLE}} | {{PROPOSED_OR_ACCEPTED}} | `{{REQUIREMENT_IDS}}` | `{{TASK_IDS}}` |

{{ARCHITECTURE_SUMMARY}}

## Build Phases

| Phase | Objective | Dependencies | Exit evidence |
|---|---|---|---|
| `{{PHASE_ID}}` | {{OBJECTIVE}} | {{DEPENDENCIES_OR_NONE}} | {{FULL_PHASE_EVIDENCE}} |

## Task graph

| Task | Traces to | Depends on | Affected paths | Shared resources | Verification |
|---|---|---|---|---|---|
| `{{TASK_ID}}` | `{{REQUIREMENT_OR_FINDING_ID}}` | {{DEPENDENCIES_OR_NONE}} | {{PATHS}} | {{LOCKS_OR_NONE}} | `{{COMMAND}}` |

## Gate 3

Decision record: {{PENDING_OR_APPROVED_RECORD}}
