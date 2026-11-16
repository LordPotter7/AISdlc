# Observed gaps from the manual compact Intent

- Identifier allocation and lifecycle transitions are still manual examples; P01 must make them runner-owned and atomic.
- The verification command can be executed locally, but its manifest is not runner-captured, hashed, or commit-pinned.
- Local output is not authoritative; P03 must reproduce the command from a clean checkout in CI.
- The traceability record is illustrative and explicitly not runner-generated.
- Gate decisions are exercise annotations rather than immutable audit events.
