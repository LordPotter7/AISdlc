<!-- ai-sdlc:begin -->
# AI SDLC repository rules

- Use the `/aisdlc:*` commands for lifecycle-managed work; `/aisdlc:status` when unsure where things stand.
- The runner owns permanent identifiers, state, gates, locks, evidence metadata, audit events, and traceability. Never edit those files directly.
- Every Task traces to an approved requirement, finding, or decision and declares affected paths, shared resources, criteria, and verification commands.
- Every criterion has exactly one D, S, J, or N verification class.
- Evidence is captured by the runner and pinned to a commit. It is not signed; CI authority was removed (ADR-007). The implementer never grades its own work.
- Use the read-only verifier and red-team agents from fresh contexts. Self-review is not independent review.
- Preserve failed attempts and historical identifiers.
- Release and deployment are outside this lifecycle.
<!-- ai-sdlc:end -->
