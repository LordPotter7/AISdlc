---
description: Export lifecycle records as NIST AI RMF, ISO/IEC 42001, and SSDF evidence
argument-hint: <intent-id|program-id>
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Glob, Grep
---

Export the conformance projection for **$1**.

Export: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" export-conformance $1`

This is a **read-only projection**, not an assessment and not a new obligation. Everything
it reports already exists as runner-owned records; the export re-expresses it in the
vocabulary an assurance reviewer works in.

What the repository already produces, as a by-product of doing the work:

| Assurance artifact | Where it already comes from |
|---|---|
| System register | Intents with permanent identifiers |
| Risk classification | Intent type, model-behaviour flag, trigger flags |
| Applied risk controls | Verification classes and the five gates |
| Documented evidence | Hashed, commit-pinned manifests. Not signed — see ADR-007 |
| Traceability | Generated bidirectional requirement-to-evidence graph |
| Accountability record | Gate approvals with named approvers, and who *may* approve |
| Continual improvement | Lessons registry with measured decay |

When you present it:

1. **Report the gaps, do not paper over them.** An export that always looked complete would
   be worthless to the person reading it. If `MEASURE` is partial because no evidence has
   been captured yet, say that plainly.
2. **Do not treat the export as a control.** It cannot pass or fail an Intent, and it must
   never become something that gates delivery — a governance process that blocks work is how
   frameworks like this one get abandoned.
3. **Prefer the Program level for an audit.** Auditors assess systems and initiatives, not
   individual work items. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" export-conformance PRG001` aggregates member Intents into
   the unit a reviewer actually examines.

Never claim certification, compliance, or legal adequacy. This produces evidence a
qualified human uses to make that judgement; it does not make the judgement.
