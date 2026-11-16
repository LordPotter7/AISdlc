---
description: Break a specified Intent into Build Phases and verifiable Tasks, and stop at Gate 3
argument-hint: <intent-id>
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Write, Edit, Glob, Grep
---

Plan Intent **$1**.

Runner state: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" status $1`
Decisions recorded: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" decision list $1`

Read the lifecycle reference in the `aisdlc` skill first. Preserve the hierarchy
Intent -> Build Phase -> Task. Never add a Subtask level.

**Architecture comes before the task graph.** Record each structural call as a decision
from `.ai-sdlc/templates/decision-template.yaml`, then `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" decision record $1 --from
<file>` and `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" decision accept $1 <id>`. The runner allocates the identifier and
hashes the content; Tasks name it in `source_decisions`. Deciding architecture inside the
task graph, rather than before it, is how a plan ends up encoding a choice nobody made
deliberately — and a decision that is only prose cannot be traced to, pinned, or
invalidated when it changes.

1. Order Build Phases by dependency. `P00` is the walking skeleton: the thinnest slice that
   proves the approach end to end. Gate 4 releases everything after it. Files go here
   (a phase folder may add a slug after its id, e.g. `I001-P00-walking-skeleton`):

   ```text
   delivery/intents/I001-<slug>/
     03-planning/master-plan.md                  # full mode; from master-plan-template.md
     04-build/I001-P00/phase.yaml                # from phase-template.yaml; id must match the folder
     04-build/I001-P00/tasks/I001-P00-T001/task.yaml
     04-build/I001-P01/phase.yaml
     04-build/I001-P01/tasks/I001-P01-T001/task.yaml
   ```

   The runner writes `evidence/` and `review.yaml` inside each Task folder later; never create them.
2. Write each Task from `.ai-sdlc/templates/task-template.yaml`. A Task declares:
   - the requirement, finding, or decision it traces to — exactly one;
   - its affected paths, so the orchestrator can detect collisions;
   - any exclusive shared resource it needs, by name;
   - its acceptance criteria, inherited from the specification;
   - the verification commands that produce its evidence.
3. Size a Task to one focused context. If it will not fit, split it into peer Tasks now;
   superseding later costs an audit record.
4. Two Tasks that write the same path cannot run in parallel. Serialize them with a
   dependency, or narrow their declared paths.
5. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" validate-intent $1`, then `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" advance $1 planned`. Validation refuses
   an accepted decision no Task implements, and a Task tracing to a superseded one.
6. Delegate an adversarial review of the plan and its architecture to the `aisdlc-red-team`
   agent from a fresh context, and register it with
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" pre-gate-review $1 G3 --from <file>`. The runner refuses Gate 3 without one.

Stop at Gate 3. Gate 3 authorizes execution — `start-task` and `orchestrate` refuse to run
until it is approved. Present the phase order, the Task list, and the parallelism the plan
allows.
