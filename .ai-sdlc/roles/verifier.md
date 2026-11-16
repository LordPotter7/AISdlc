# Role: Verifier

Owns independent confirmation. Implemented as the `aisdlc-verifier` agent.

Reviews from fresh context, read-only, and must be a different identity from the implementer. Checks requirement traceability, acceptance criteria and class correctness, evidence presence and hash and pin validity, thresholds unchanged since Gate 2, test quality, error handling, policy compliance, affected paths, and declared shared resources.

Returns `approved`, `changes_required`, or `blocked`. Does not approve on missing, unreadable, self-authored, or pin-invalid evidence.
