# Specification: {{INTENT_ID}} — {{INTENT_TITLE}}

Narrative companion to `requirements.yaml`. Identifiers are permanent after Gate 2.

## Scope

In scope: {{WHAT_THIS_INTENT_DELIVERS}}

Out of scope: {{CARRIED_FORWARD_FROM_THE_BRIEF}}

## Behaviour

### {{REQ_ID}} — {{SHORT_NAME}}

{{WHAT_THE_SYSTEM_SHALL_DO_IN_TESTABLE_TERMS}}

Failure behaviour: {{WHAT_HAPPENS_WHEN_THIS_CANNOT_BE_SATISFIED}}

Criteria: {{AC_IDS}}

## Quality attributes

Which quality categories this Intent's non-functional requirements address, and — as
deliberately — which it does not. `ai-sdlc spec-lint` reports the coverage; a category left
silent should be a decision, not an oversight.

| Category | Requirement | Measure | Or: why not applicable |
|---|---|---|---|
| {{CATEGORY}} | `{{REQ_NFR_ID}}` | {{METRIC_TARGET_UNIT_CONDITION}} | {{REASON}} |

## Data and compliance

Data classification handled: {{PUBLIC_INTERNAL_CONFIDENTIAL_RESTRICTED}}. Personal data: {{YES_OR_NO}}.

| Control | Requirement | Criterion |
|---|---|---|
| {{FRAMEWORK_AND_CONTROL}} | `{{REQ_ID}}` | `{{AC_ID}}` |

## Interfaces

Contracts this Intent delivers, under `contracts/`. Every operation is covered by a
criterion or excluded with a reason in `requirements.yaml`.

| Contract | Operations | Covered by | Excluded (reason) |
|---|---|---|---|
| `contracts/{{API}}.yaml` | {{N}} | {{AC_IDS}} | {{OPERATION_AND_REASON_OR_NONE}} |

## Threats

Summary of `threat-model.yaml` (required for full security, multi-tenant, or side-effecting
Intents): trust boundaries, and each threat's disposition and Class N criterion.

| Threat | Boundary | STRIDE | Disposition | Mitigated by | Proven by |
|---|---|---|---|---|---|
| `{{TM_ID}}` | `{{TB_ID}}` | {{CATEGORY}} | {{MITIGATE_ACCEPT_TRANSFER}} | `{{REQ_ID}}` | `{{AC_ID}}` |

## Deferred scope

Requirements prioritised `wont` for this Intent. They stay in `requirements.yaml` so the
exclusion is auditable; a Task that implements one fails validation.

- `{{REQ_ID}}` — {{WHY_DEFERRED}}

## Specification quality

Output of `ai-sdlc spec-lint {{INTENT_ID}}` at the time Gate 2 is requested, and how each
remaining warning was resolved or accepted: {{LINT_SUMMARY}}

## Verification approach

| Criterion | Class | Instrument | Threshold source |
|---|---|---|---|
| `{{AC_ID}}` | {{D_S_J_OR_N}} | {{COMMAND_OR_EVAL_ID}} | {{GATE_1_SUCCESS_MEASURE}} |

Every criterion carries exactly one class. Class S and J name a dataset, metric, threshold, minimum sample size, and tolerance band before implementation. Class N states a zero-success threshold. See `eval-design.md` when `contains_model_behavior` is true.

## Inherited controls

Standing suites and policies inherited from the lesson registry by this Intent's trigger flags:

- {{SUITE_OR_POLICY}}

Declining an inherited suite is an escalation. Record the reason here: {{DECLINE_REASON_OR_NONE}}

## Resolved ambiguities

- {{QUESTION_AND_THE_DECISION_TAKEN}}

## Gate 2

Decision record: {{PENDING_OR_APPROVED_RECORD}}
