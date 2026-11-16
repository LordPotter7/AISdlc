---
description: Validate evidence, pins, traceability, and convergence, and stop at Gate 5
argument-hint: <intent-id>
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Glob, Grep, Task
---

Verify Intent **$1**.

Runner state: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" status $1`
Traceability: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" trace $1`
Convergence: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" converge $1`

Work through what the runner reports:

1. **Evidence integrity.** Every manifest is hashed and pinned to the commit it was
   captured at. A pin against a stale commit is a failure, not a formality. The runner
   captured it from outside this repository, which is what makes it worth more than a
   claim — but it is not signed, so it proves nothing to a third party who does not
   trust this machine. Say that plainly if anyone asks what the evidence is worth.
2. **Specification conformance.** A Task whose visible suite passes while its held-out suite
   fails is not flaky and must not be retried as though it were. It is an implementation
   that satisfies the tests it was given rather than the requirement those tests stand for.
   Report it as a conformance finding with its severity, and fix the implementation — never
   the held-out test.
3. **Held-out coverage.** Every behavioural requirement carries an instrument authored
   outside the implementing context, or a waiver with a reason and an approver. An absent
   instrument is not a passing one: nothing was measured.
4. **Bidirectional traceability.** Every requirement reaches a Task and every Task reaches a
   requirement. Report orphans on both sides.
5. **Inherited suites.** Every standing suite the trigger flags pulled in has run, or carries
   a recorded decline with a written reason.
6. **Open findings.** Every Critical and High finding needs a firing lesson target before
   close.
7. **Phase exits.** Each Build Phase meets its exit criteria.

If convergence fails, report exactly which check failed and what would satisfy it. Never
describe an Intent as converged when the runner says otherwise.

Stop at Gate 5. That is final human acceptance — yours to present, not to grant.
