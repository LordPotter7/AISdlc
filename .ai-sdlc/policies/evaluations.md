# Evaluation policy

- Class S and J criteria declare dataset, metric, threshold operator/value, minimum sample size, tolerance band, and all model/prompt pins before implementation.
- S and J run exactly three times for phase/convergence evidence. The median must clear the threshold and the worst run must stay within the tolerance band.
- Mixed pass/fail runs are `flaky` and do not pass.
- Class J requires calibration agreement of at least 0.80 against human labels. Calibration model and prompt pins must match the active judge.
- Class N accepts only zero observed attacker successes and reports suite limitations; it never claims zero risk.
- A dataset’s path, owner, version, and SHA-256 hash are registered. Edited data changes the hash and invalidates earlier evidence.
- Matching standing suites are inherited from Intent trigger flags. Declining an inherited suite requires escalation and a recorded reason.
