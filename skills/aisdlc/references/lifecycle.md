# Lifecycle Reference

## Delivery model

The only delivery hierarchy is:

```text
Intent
└── Build Phase
    └── Task
```

Identifiers are permanent once their approval/execution boundary is crossed:

- Intent: `I001`
- Build Phase: `I001-P00`
- Task: `I001-P00-T001`
- Requirements: `REQ-F-001`, `REQ-NFR-001`
- Acceptance/success criteria: `AC-001`, `SC-001`
- Evaluation/finding/lesson: `EVAL-001`, `F-001`, `LESSON-001`

## Stages and gates

| Stage | Source folder | Outcome | Human gate |
|---|---|---|---|
| 0 Register | `00-intent/` | Permanent identity and intent type | — |
| 1 Frame | `01-brief/` | Problem, outcome, scope, measures | G1 |
| 2 Specify | `02-specification/` | Requirements, criteria, eval design | G2 |
| 3 Plan | `03-planning/` | Architecture, phases, Task graph, resources | G3 |
| 4 Build | `04-build/` | Verified Tasks, beginning with P00 | G4 after P00 |
| 5 Converge | `05-verification/` | Full traceability and final checks | G5 |
| 6 Close | `06-close/` | Completion record and promoted lessons | — |

G2 and G3 may be presented together for compact work. No other gates merge. A changed approved threshold reopens G1 and G2 as applicable; changed requirements reopen G2; changed architecture or Task graph reopens G3.

## Compact and full artifacts

Use one lifecycle for both paths. Compact mode shortens artifacts; it does not remove permanent identifiers, requirements, classified acceptance criteria, gates, verification, evidence rules, or traceability.

Use compact mode only when all are true:

- the outcome fits one Build Phase with one or a few Tasks;
- all criteria are Class D;
- there is no model behavior, security boundary, external side effect, data migration, public-interface change, or irreversible action;
- the Task graph and shared-resource map are simple enough to review together.

Otherwise use full artifacts. The runner enforces the parts it can decide (`new --mode compact` refuses model behavior; `validate-intent` refuses non-D criteria in a compact Intent); the rest is judgment for the G1 approver.

## Intent states

The runner owns the transition table (`INTENT_TRANSITIONS` in `scripts/aisdlc/state.py`): initialized → framed → specified → planned → building → converging → awaiting_acceptance → completed, with `aborted` from any open state. Moving back one stage is allowed, and that is how a reopened gate is answered: after `gate reopen <id> G2` while building, `advance planned`, then `advance specified`, edit, re-review, and approve G2 again. Artifact prose must never claim a transition the runner has not recorded.

## Task states

```text
pending -> ready -> in_progress -> verifying -> review -> verified -> merged
                     |                |          |
                     v                v          v
                  blocked           retry     retry
                     |                |          |
                     v                +----------+
                   failed

pending -> superseded
merged -> reverted
```

- Dependencies and locks decide `ready`.
- Two retries are allowed: three total attempts.
- Failed attempts and their evidence remain visible.
- A later defect creates a corrective Task; it does not rewrite executed history.

## Required Task contract

Every Task names an objective, trace source, dependencies, affected paths, exclusive shared resources, inputs, outputs, acceptance criteria, verification commands, parallelizability, and retry limit. A Task must fit one focused execution context and be independently verifiable.
