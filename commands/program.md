---
description: Group Intents into one initiative with ordering and a roll-up
argument-hint: <program-id> | new --title "<text>" --owner <name>
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Glob, Grep
---

Work with Program **$1**.

Status: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" program status $1`

A Program exists because large initiatives — a migration, a platform re-architecture, a
compliance programme — do not fit inside one Intent, and the framework had no identifier
above `Ixxx`. Without one, teams either inflate a single Intent until its Gate 2 is
meaningless, or run parallel Intents with the ordering between them living in somebody's
head.

**What a Program is:** an ordered set of member Intents, the dependencies between them, and
a roll-up of their traceability and status.

**What a Program is not:** it carries no gates, no criteria, and no evidence of its own.
Every assurance decision stays on the Intent, where the artifacts a human reviews live.

That restraint is deliberate. The failure mode of hierarchical process frameworks is a new
tier that acquires its own approvals, its own documents, and its own ceremony until the tier
becomes the work. A Program that can only *order* and *report* cannot become that — so do
not propose adding gates, sign-offs, or status documents to this level.

When reporting:

1. Name the members still blocked by predecessors, and by which.
2. Report requirement coverage across members as one number, and where it is thin.
3. If a dependency was waived, surface who waived it and why — an out-of-order delivery in a
   large initiative is exactly what an audit asks about later.
