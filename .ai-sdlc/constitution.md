# Constitution

These rules bind every Intent, Build Phase, and Task. They are not negotiable by an agent, a Task, or a deadline. A conflict between this document and any plan, prompt, or instruction is resolved in favour of this document, and the conflict is escalated.

## Authority

- The deterministic runner owns permanent identifiers, lifecycle and Task state, gate records, resource locks, evidence metadata and hashes, audit events, and generated traceability. No agent writes those values.
- Humans own the five gates. A gate is approved by a person, never inferred from progress.
- An agent's narrative is never a substitute for a runner-captured result. If the runner did not record it, it did not happen.

## Traceability

- Every Task traces to at least one approved requirement, recorded finding, or architectural decision.
- Traceability is bidirectional and regenerated, never hand-edited.
- Delivered work stays inside the approved intent. Material scope expansion is an escalation.

## Verification

- Every acceptance criterion declares exactly one verification class: D, S, J, or N.
- **The instrument that decides a criterion is not authored in the context that satisfies it.** Every behavioural requirement carries at least one held-out instrument authored by the specification, the verifier, or a human — or a waiver recorded with a reason and a name. Proving a command ran is not proving it could have failed.
- A visible suite that passes while its held-out suite fails is a specification-conformance finding, not a flake. The implementation satisfies its tests rather than the requirement those tests stand for.
- Thresholds are set before implementation and changed only through escalation and a reopened gate. Across Intents, a criterion that has not failed within the review window is measured and then defended or retired, like any other decaying control.
- Security, isolation, and side-effect requirements require a Class N criterion with zero tolerance.
- Task-level subsets accelerate feedback and never close a phase-level criterion.

## Evidence

- Evidence is captured by the runner, hashed, and never written by the implementing agent.
- The runner lives outside the repository it judges. That is what keeps the instrument beyond the reach of the session it measures: an implementing agent may write anywhere in the working tree and still cannot reach the code that decides whether its work passed.
- Every manifest is pinned to the commit it was captured at. A pin against a stale commit is a failure, not a formality.
- Evidence produced outside a Task's declared execution posture is invalid, on the same footing as evidence pinned to the wrong commit.
- Model-dependent evidence carries a complete run manifest, and judges are calibrated against human labels before their scores count.
- Failed attempts, flaky checks, and reverted merges stay visible in the audit history. Nothing is deleted or renumbered.

## Review

- Independent review is performed by an agent other than the implementer, from fresh context, with no write access to the implementation.
- Independence is a property of the model, not of the persona name. A review whose reviewer model shares a family with the implementer's is void, because judges over-reward their own family's output. A review that records no reviewer model is void as well: the runner fails closed rather than assuming independence it cannot check.
- Self-review is void and returns the Task to `Retry`.

## Learning

- Findings are resolved, accepted as documented limitations, or escalated.
- Every Critical or High finding leaves the Intent as a blocking policy rule or a promoted regression eval case.
- Standing suites matching an Intent's triggers are inherited automatically. Declining one is an escalation with a written reason.

## Boundaries

- Release and deployment actions are prohibited inside this lifecycle.
- Front-end work is outside this lifecycle, with one exception: accessibility, admitted only as a requirement with a declared WCAG target verified by a Class D automated checker. What automated checkers cannot decide stays with a named manual-audit owner, outside the lifecycle (ADR-009).
- Destructive or irreversible actions require explicit human authorization.
