# Verification and Evidence Reference

## Verification classes

Every acceptance criterion has exactly one class.

| Class | Pass rule | Required evidence |
|---|---|---|
| D — Deterministic | Declared command exits 0 | Command, exit code, stdout, stderr, source commit |
| S — Statistical | Median clears a predeclared threshold and minimum stays within tolerance | Pinned run manifest, per-case results, metric and interval |
| J — Judged | Calibrated judge clears a predeclared threshold | Judge prompt hash, calibration record, per-case scores |
| N — Negative/adversarial | Zero attack cases succeed | Suite version, per-case outcomes, zero-success assertion |

Class S and J criteria declare dataset, metric, threshold, minimum sample size, tolerance band, model identifier, prompt version, dataset hash, temperature, and seed when supported before implementation. A Class J judge must reach at least 0.85 agreement with human labels and be recalibrated when its model or prompt changes. One Class N success blocks the phase.

## Reruns and flakes

- Run S and J checks three times; report median, minimum, and maximum.
- Alternating pass/fail outcomes are `flaky`, never `passed`.
- A D check that fails and then passes creates a finding; do not erase the first attempt.
- Task-level subsets accelerate feedback but never close a phase-level criterion.

## Evidence integrity

- Only the runner/orchestrator writes evidence files and manifests.
- Retain every attempt for the life of the Intent.
- Hash each evidence file and record the hash in the Task record.
- Missing, unreadable, changed, or source-pin-mismatched evidence is invalid.
- Evidence is pinned to the commit it was captured at, and a stale pin invalidates it.
- Narrative claims, screenshots without capture metadata, and uncaptured passing commands are not evidence.

Guided conversation cannot produce evidence at all. Only the runner captures it, and the
runner lives in the plugin directory — outside the repository under change. That is the
instrument boundary: an agent may edit anything in the working tree and still cannot edit
the code that judges it. Reporting that you ran a command and it passed is not evidence;
`review-task` refuses a review whose manifest is missing, altered, or pinned elsewhere.

## Independent review

The verifier must be different from the implementer, receive fresh context, and remain read-only. It checks requirements, criteria, class correctness, evidence presence/hash/pins, test quality, errors, policy compliance, affected paths, and shared resources. Self-review is void.

The red-team reviewer looks for cross-feature and adversarial failures, especially side-effect authorization, isolation, prompt injection, provenance confusion, bypasses, and evidence tampering. A finding is not a failing command by itself; it must point to reproducible evidence or a precise missing check.

## Convergence

Regenerate, never hand-edit, bidirectional traceability across brief, requirements, criteria, phases, Tasks, artifacts, checks, and evidence. Convergence requires all required Tasks and phase exits, matching Gate 2 thresholds, current judge calibration, no flakes, zero N successes, and no open Critical or High findings.
