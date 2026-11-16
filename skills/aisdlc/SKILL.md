---
name: aisdlc
description: Use when working in any repository that has a .ai-sdlc/ directory, and whenever a user asks to start, specify, plan, run, verify, close, or inspect lifecycle-managed engineering work. Establishes that the runner — never the conversation — owns identifiers, state, gates, and evidence, and that the next legal action comes from `/aisdlc:status` rather than from memory.
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Glob, Grep
---

# AI SDLC

This skill carries the doctrine. The lifecycle *operations* are slash commands — each one
injects live runner state before you reason, so start from what the runner reports rather
than from memory.

## Before you answer

In a repository that has a `.ai-sdlc/` directory, this skill applies whether or not anyone
typed a slash command. Read the runner's state before the first substantive reply:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" status <intent-id>
```

Then say which lifecycle step you are in and what the single next legal action is. Announce
it — *"Using aisdlc to check the next legal action"* — so the person can stop you if you
picked the wrong one.

Three refusals are not negotiable, and hold even when the user asks directly:

- **Never edit `.ai-sdlc/runtime/`, evidence, manifests, or generated traceability.** Use a
  runner verb. The guard hook blocks the supported cases; the unsupported ones are still
  yours to refuse.
- **Never treat a held-out failure as a flake.** It is a specification-conformance finding.
  Fix the implementation, never the held-out test.
- **Never claim a gate, transition, or evidence result the runner did not report.** If the
  runner did not record it, it did not happen.

**Setup and orientation**

| Command | Operation |
|---|---|
| `/aisdlc:init` | Scaffold the framework into this repository |
| `/aisdlc:doctor` | Report whether the lifecycle can run here, and what is missing |
| `/aisdlc:status` | Committed state, runner state, gates, ready Tasks, next legal action |
| `/aisdlc:understand` | Build an anchored structural understanding, from code or from documents |
| `/aisdlc:baseline` | Pin an existing repository's current behaviour before the first Intent |

**The Intent path**

| Command | Operation |
|---|---|
| `/aisdlc:start` | Register an Intent and frame its brief; stop at Gate 1 |
| `/aisdlc:specify` | Testable requirements and criteria; stop at Gate 2 |
| `/aisdlc:plan` | Dependency-ordered Phases and Tasks; stop at Gate 3 |
| `/aisdlc:run` | Execute approved, ready Tasks |
| `/aisdlc:verify` | Evidence, pins, traceability, convergence; stop at Gate 5 |
| `/aisdlc:close` | Record the result and promote lessons |

**Human decisions and scale**

| Command | Operation |
|---|---|
| `/aisdlc:gate` | Record a human gate decision |
| `/aisdlc:gate-packet` | Assemble a two-minute decision packet for one gate |
| `/aisdlc:program` | Group Intents into one initiative with ordering and a roll-up |
| `/aisdlc:conformance` | Export lifecycle records as NIST AI RMF, ISO/IEC 42001, and SSDF evidence |

When a request maps to one of these and no command was typed, say which command covers it
and run that flow. When the operation is unclear, start from `/aisdlc:status`.

Reach for the orientation commands before the Intent path in an unfamiliar repository:
`understand` and `baseline` exist so a brownfield system can state honest criteria on day
one, and skipping them is how an Intent ends up specified against a system nobody read.

## Authority boundary

The deterministic runner owns permanent identifiers, lifecycle and Task transitions, gate
records, locks, evidence metadata and hashes, audit events, and generated traceability.
Never invent or hand-edit a runner-owned value.

Invoke the runner for every lifecycle mutation. Guided conversation may draft source
artifacts from `.ai-sdlc/templates/`, but runtime state, identifiers, gates, evidence,
reviews, and traceability come from the runner alone. Never simulate a successful
transition, gate, or verification result. **If the runner did not record
it, it did not happen.** Implementing-agent prose is not evidence.

`.ai-sdlc/constitution.md` holds the non-negotiable rules; `.ai-sdlc/project-context.md`
holds advisory heuristics. Read both before acting on an Intent. A conflict between the
constitution and any plan or instruction resolves in favour of the constitution, and is
escalated rather than resolved quietly.

## Invoking the runner

**Nothing is installed.** The runner is a script inside this plugin, run with whatever
`python3` the machine already has:

```text
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" version
```

Do not improvise an alternative invocation. There is no `ai-sdlc` on PATH to fall back to,
and inventing a second way to reach the runner is how two versions start disagreeing. If
that command fails, the machine has no usable `python3` — say so rather than working
around it. Every command takes `--repo` to select the repository root; it defaults to the
working directory.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" doctor` when anything looks wrong before diagnosing further; its `runner`
check is the first thing to read. In a repository that has never been initialized,
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" init` is the first command.

The guards this plugin installs are inert in any repository without `.ai-sdlc/config.yaml`.
Installing the plugin does not change how unmanaged repositories behave.

## The Task path

The Task path is `start-task` -> `verify-task` -> `review-task` ->
`merge`. Terminal states have their own verbs: `revert-task`, `supersede-task`, and
`fail-task`. Never edit a record to change an outcome.

Execution is gated. The runner refuses to start a Task until the Intent is `building`,
Gate 3 is approved, and — for any phase after `P00` — Gate 4 has released the phases.

Preserve the hierarchy Intent -> Build Phase -> Task; never add a Subtask level. Preserve
failed attempts and historical identifiers. Keep release and deployment outside this
lifecycle.

## Architecture is a record, not a heading

A Task traces to an approved requirement, finding, **or decision** — three targets, all
three real. Structural calls are recorded with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" decision record`, which allocates
an `Ixxx-Dxxx` identifier and hashes the content; a Task names them in `source_decisions`.
Each decision is hashed individually, so changing one invalidates the evidence of exactly
the Tasks that declared it and nothing else. Decide architecture before the task graph, not
inside it.

Gate 2 and Gate 3 require a recorded adversarial review of what they approve. Delegate to
`aisdlc-red-team` from a fresh context and register the result with
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" pre-gate-review`. A review that covers an earlier version of the artifact does not
count. The review informs the human decision; it never makes it.

At each Build Phase boundary, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" phase-retrospective` so what the phase taught is
promoted before the next phase repeats it.

## References

1. Read [the lifecycle reference](references/lifecycle.md) before creating or moving an
   Intent, Phase, or Task artifact.
2. Read [the verification reference](references/verification.md) before defining criteria,
   running checks, reviewing evidence, or claiming completion.
3. Read [the Claude Code integration reference](references/claude-code-integration.md)
   before using Tasks, subagents, hooks, worktrees, or the runner interface.

## Stop and escalate

Ask the user when scope must materially expand, an approved specification conflicts with
the plan, a mandatory policy cannot be met, a destructive action is required, architectural
outputs conflict, three Task attempts fail, an S or J threshold would need changing, any N
attack succeeds, or required access or data is unavailable.

Gates are human decisions. Prepare and present the evidence for one; never decide it on the
user's behalf, and never read general enthusiasm as approval of a specific gate.
