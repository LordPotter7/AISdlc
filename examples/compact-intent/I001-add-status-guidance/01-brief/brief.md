# Intent Brief: I001 — Add status operation guidance

## Problem

The initial `aisdlc` skill needs an explicit status operation so a developer can learn the current lifecycle state and next legal action without mutating the Intent.

## Expected outcome

The skill documents a read-only `status` operation that reports source artifacts, runner state, gates, Tasks, evidence authority, findings, and the next legal action.

## Scope

Only the skill entry point is changed. Runner implementation, hooks, CI, release, and deployment are outside this compact Intent.

## Success measure

Repository P00 validation confirms that the skill exposes the status operation and retains the runner authority boundary.

Gate 1 exercise decision: approved for the manual P00 package exercise.
