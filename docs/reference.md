# Reference

The complete guide to the Adaptive Evidence-Driven AI SDLC: every onboarding path, the
lifecycle in detail, evaluations, programs, conformance, configuration, the
full command surface, and troubleshooting.

Start at the [README](../README.md) if you have not installed the framework yet — it
covers the one-time machine setup and a ten-minute first run, and everything here assumes
that is already done.

---


> **`ai-sdlc` in this guide is shorthand** for `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py"`. Nothing named `ai-sdlc` is installed on your `PATH`; see the README's *Installation* section.

## Path A — greenfield repository

```text
cd your-new-project
ai-sdlc init
ai-sdlc doctor
```

Or, from Claude Code, `/aisdlc:init` — which runs both and explains the result.

Then fill in one file and run one Intent:

1. **Fill in `.ai-sdlc/project-context.md`.** Its verification commands become Class D criteria, so a stub left empty weakens every Task that inherits from it. This is the single highest-value five minutes of onboarding.
2. **Run one small compact Intent end to end** before anything real. See [First Intent](#first-intent-complete-walkthrough).

That is the whole greenfield path. Everything else in this document is available when you need it and inert until then.

## Path B — existing (brownfield) repository

Most repositories are not new, and the normal lifecycle assumption — state what the system should do, then prove it does — fails badly on a legacy system. Applied literally it produces either a **fiction** (requirements reverse-engineered from the code, encoding the current bugs as intended behaviour) or **paralysis** (nothing starts until everything is specified).

Two artifacts exist to skip both, and they answer different questions. The **understanding** records what the system *is* — entry points, module responsibilities, boundaries, data stores, risk concentrations. The **baseline** pins what the system *does* — test results, coverage, metrics, fitness checks — hashed and tied to a commit. Neither asserts anything about correctness.

Do them in that order, because the understanding is where the baseline's probes come from. Writing a probes file for a 500k-line system nobody currently understands is the blank-page problem all over again.

```text
ai-sdlc init
ai-sdlc doctor
```

### 1. Build the understanding

```text
/aisdlc:understand repo
```

The `aisdlc-cartographer` agent is read-only, runs once per repository, and holds no lifecycle authority. It writes a single Markdown file, `.ai-sdlc/project-understanding.md`, with an inline anchor on every claim:

```markdown
The auth module manages sessions and refresh rotation. [src/auth/session.py:40-120]
```

Pick **one** source, and never both in one run:

| Mode         | Reads                   | Use when                                                                |
| ------------ | ----------------------- | ----------------------------------------------------------------------- |
| `repo`     | The codebase            | You have code and little reliable documentation                         |
| `document` | Declared Markdown files | Your knowledge is prose — a brief, an architecture note, a legacy spec |

```text
/aisdlc:understand repo
/aisdlc:understand document docs/architecture.md docs/legacy-spec.md
```

The two have different standing, and the file says which one it is. A `repo` claim is anchored to the artifact that actually runs: it can be wrong about intent, not about existence. A `document` claim is anchored to a *description*, and a perfectly fresh document-sourced understanding may describe a system that no longer exists — so a `document`-mode file must carry that notice in its opening lines. It is the same distinction the framework draws between what a system *is* and what it *does*.

To use both, run twice. Give the second run the first run's file, and it writes a **Divergence** section listing where the written architecture and the real one disagree. That section is usually more useful than either analysis alone.

Then validate and pin it:

```text
ai-sdlc understand record
```

`record` checks before it pins, and a rejected document is never pinned: every anchor must resolve at the pinned commit, no anchor may point outside the declared source, the required sections must be present and in order, a `document`-mode file must carry its unverified-against-code notice, and the prose must report what *is* rather than what *ought to be*. This does not catch every hallucination — it catches the cheapest and most common kind, confident prose about something that is not there, which is what turns an unfalsifiable document into a falsifiable one.

Staleness comes from git, not from a stored hash table:

```text
ai-sdlc understand status     # fresh | drifting | stale, and which claims moved
```

Before an understanding is drafted, `understand scan` lists what the repository contains, from the files git tracks at HEAD: languages, manifests, test suites and the commands that would run them, CI files, CODEOWNERS owners, modules and entry points. It runs nothing and writes nothing, and the same commit always gives the same answer, so there is no stored inventory to drift. Its test commands are *proposed* probes, to copy into a probes file deliberately.

```text
ai-sdlc understand scan       # facts from git, as JSON; nothing stored
```

**Read the result yourself.** It is advisory heuristics, permanently — the weakest control tier the framework has. There is no gate for it, no Intent fails because of it, and it is never a requirements source: understanding describes what *is*, requirements state what *should be*, and deriving one from the other encodes your existing bugs as intent.

Stage 4 context packs carry the understanding with its mode and staleness attached, and drop it once stale, because stale orientation is worse than none.

### 2. Capture the baseline

The understanding's `## Proposed checks` section is its payload, and the reason the document earns its place. A document is the thing this framework is least equipped to trust; a probe is the thing it is best equipped to enforce. The framework could always *enforce* a check — what it lacked was a way to know which checks your codebase needs:

```markdown
## Proposed checks

- [ ] `python tools/check_layers.py` — domain layer imports nothing from `web/`
      [src/domain/, src/web/]
- [ ] `pytest tests/orders -q` — order totals suite, currently 12 passing
      [tests/orders/]
```

Read each line, decide, and copy the ones you accept into your probes file yourself — deliberately, one at a time. There is no promotion command, by design: a proposed check acquiring enforcing power without anyone reading it is exactly what the advisory tier exists to prevent.

In `document` mode these come from the architectural claims your documents *state* — "services never share a database", "the domain layer does not import the web layer". Turning those into runnable commands does something a document can never do on its own: it shows you where reality has already diverged from the documentation.

Write a probes file describing what the system does today:

```yaml
probes:
  - name: suite
    command: pytest -q
    metric: '(\d+) passed'
  - name: coverage
    command: pytest --cov --cov-report=term
    metric: 'TOTAL.*?(\d+)%'
  - name: layering
    command: python tools/check_layers.py     # a fitness function: non-zero on violation
```

Use **single quotes** around a `metric` regex. YAML double-quoted strings process backslash
escapes, so `"(\d+)"` is a parse error and `"(\\d+)"` is needed instead; single-quoted
strings are literal and read the way the regex is actually written.

Capture it:

```text
ai-sdlc baseline capture probes.yaml --label "pre-lifecycle entry"
```

A failing suite here is a **fact about the repository**, recorded rather than rejected — that is the point. Now your first Intents state criteria *relative* to the reference, which is honest and enforceable immediately:

```yaml
acceptance_criteria:
  - id: AC-001
    verification_class: D
    baseline_ref: BASELINE-001      # no regression against the recorded reference
```

Check any time:

```text
ai-sdlc baseline compare BASELINE-001
```

Comparison reports **regressions**, not mere difference. A probe that improved is not a failure. Declare the `metric` regex explicitly — a heuristic extractor would silently change meaning when a tool reformats its output, and a moving reference is worse than none.

### 3. Adopt incrementally

Then adopt incrementally, in this order:

| Step | Do this                                               | Why this order                                     |
| ---- | ----------------------------------------------------- | -------------------------------------------------- |
| 1    | Run one compact Intent on a small, real change        | Learn gates and evidence on something low-stakes   |
| 2    | Add held-out instruments to new Tasks                 | The check that makes evidence mean something       |
| 3    | Add mutation thresholds to logic-bearing Tasks        | Catches weak tests the held-out suite cannot       |

Do not enable everything at once. Each step is independently useful, and a repository that turns on every control before running a single Intent will conclude the framework is unusable.

## Path C — a new teammate

Point them at three things, in order:

1. **`AGENTS.md` in the repository root** (written by `init`). It is short and states the non-negotiable rules, the command surface, and — usefully — *what the runner will refuse*. It is written for agents, which makes it an unusually blunt and accurate summary for people.
2. **`.ai-sdlc/constitution.md`.** The rules that are not negotiable by an agent, a Task, or a deadline. About one page.
3. **[Five-minute orientation](#five-minute-orientation)** below, then shadow one compact Intent.

On a brownfield repository, add `.ai-sdlc/project-understanding.md` if one has been recorded — it is the fastest orientation to the system itself, and every claim in it carries a `[path:lines]` anchor they can go read. Tell them what it is worth: it is advisory, it was written by a model, and `ai-sdlc understand status` says whether it has drifted since.

The mental model that makes everything else obvious:

> Claude and humans create and change *source artifacts*. The runner decides whether the lifecycle may move forward. If the runner did not record it, it did not happen.

The most common early mistakes, worth saying out loud on day one:

- Editing `.ai-sdlc/runtime/` to "fix" a state. Use `ai-sdlc repair-state --confirm` instead; hand-editing is blocked by hooks and breaks the audit chain.
- Treating a held-out failure as a flaky test. It is the opposite of flaky — it is the most informative signal the framework produces.
- Committing `.ai-sdlc/runtime/`. It makes convergence permanently unsatisfiable. `init` gitignores it for you.

## Path D — a non-Claude agent

The deterministic core has no Claude Code dependency. `init` emits an `AGENTS.md` carrying the constitution summary, the runner-owned path list, the command surface, and what the runner refuses. Any AGENTS.md-reading agent receives the doctrine, and the runner keeps the authority regardless of which agent is driving.

```text
ai-sdlc init            # writes AGENTS.md alongside everything else
```

Claude Code hooks remain an *advantage* — they block invalid edits before they happen — not a *requirement*. Under another agent the same rules are enforced at the runner boundary instead, one step later.

## Path E — organisation scale

Add these only when the corresponding pain is real:

| Pain                                           | Reach for                        | Command                                                   |
| ---------------------------------------------- | -------------------------------- | --------------------------------------------------------- |
| One initiative spans many Intents              | Programs                         | `ai-sdlc program new --title "…" --owner …`           |
| Two teams keep colliding on a schema or prompt | Intent-scope contention          | `ai-sdlc intent-resources I00X --declare schema:orders` |
| A change spans several repositories            | Not supported (ADR-007)          | one Intent per repository, ordered by a Program       |
| Gates feel slow                                | Gate packets, then measure       | `ai-sdlc gate-packet I00X G3`                           |
| Nobody can say what the process costs          | Cost and DORA export             | `ai-sdlc metrics cost`, `ai-sdlc metrics export`      |
| Compliance is asking questions                 | Conformance projection           | `ai-sdlc export-conformance I00X`                       |

Rigor is adjusted only through pipeline profiles (ADR-010): a profile may tighten any rule freely, may loosen a default only with a recorded waiver, and can never touch the constitution. One thing deliberately **does not** exist, and should not be added: additional agent personas (more agents, more tokens, more handoff failure surface, and no authority anywhere).

---

## What `init` writes

`init` writes only what the repository owns:

| Written by`init`                                               | Inherited from the runner    |
| ---------------------------------------------------------------- | ---------------------------- |
| `.ai-sdlc/config.yaml`                                         | `constitution.md`          |
| `.ai-sdlc/project-context.md` (a stub to fill in)              | `schemas/`                 |
| `.ai-sdlc/lessons/`, `.ai-sdlc/evals/`                       | `workflow/`, `policies/` |
| `.ai-sdlc/security/standing-checks.yaml`                       | `roles/`, `templates/`   |
| `CLAUDE.md` rules block, `AGENTS.md`, `.gitignore` entries |                              |
| the delivery root                                                |                              |

The inherited half is never copied, so upgrading the runner upgrades every initialized repository at once. A repository that needs to tailor one of those files places its own copy under `.ai-sdlc/`, which then takes precedence.

`init` does not scaffold `.ai-sdlc/project-understanding.md`. That file is *produced* by `/aisdlc:understand`, not filled in from a stub, and the two stay separate on purpose: `project-context.md` is what a person verified, the understanding is derived and may go stale. Merging them would destroy the ability to tell which half is which.

`init` is idempotent. Re-running it reports `skipped` for anything that already exists, never duplicates a hook entry or a `CLAUDE.md` block, and re-pins the standing regression suites against the current checkout. Use `--force` to deliberately rewrite repository-owned files.

Keep `.ai-sdlc/runtime/` and `.ai-sdlc/metrics/` out of version control — `init` adds both to `.gitignore`. Committing runtime state breaks convergence: evidence pins to the commit it was captured at, so a commit made after capture leaves the clean-tree check permanently unsatisfiable. Never copy `.ai-sdlc/runtime/` or delivery records between repositories; they belong to the repository that created them.

A prepared repository looks like this:

```text
your-project/
├── .ai-sdlc/                 # config, context, lessons, evals, security, baselines, runtime
├── delivery/intents/         # lifecycle records for this project's changes
├── AGENTS.md                 # agent-agnostic doctrine
└── CLAUDE.md                 # concise repository-wide rules
```

### What `doctor` checks

Fifteen checks. A `warn` means something was *weakened* or is incomplete, never that you declined an opt-in hardening:

`runner`, `framework-assets`, `config`, `delivery-root`, `hooks`, `git`, `runtime-state`, `standing-suites`, `gitignore`, `instrument-boundary`, `reviewer-independence`, `holdout-policy`, `agents-md`, `compatibility`, `schema-generation`.

Read `runner` first — it names the `python3` the hook launcher would find (Python 3.12+). A `fail` means no usable interpreter, and nothing works until there is one. The runner itself is the installed plugin's `scripts/lifecycle.py`; nothing named `ai-sdlc` needs to be on `PATH`.

On a fresh repository with the plugin installed, expect every check to pass.

## The two interfaces

Use both together:

| Interface            | Example                            | Responsibility                                                                                |
| -------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------- |
| Claude commands      | `/aisdlc:specify I001`           | Guides the conversation, drafts source artifacts, implements Tasks, explains decisions        |
| Deterministic runner | `ai-sdlc advance I001 specified` | Validates artifacts and owns identifiers, state, gates, locks, evidence, retries, and reports |

Claude never invents a successful gate, state transition, or evidence result. Each command injects live runner state into its own prompt before Claude reasons, so a phase always starts from what the runner reports rather than from what the conversation remembers.

| Command                 | Operation                                                                   |
| ----------------------- | --------------------------------------------------------------------------- |
| `/aisdlc:init`        | Scaffold the framework into this repository                                 |
| `/aisdlc:doctor`      | Report whether the lifecycle can run here, and what is missing              |
| `/aisdlc:status`      | Committed state, runner state, gates, ready Tasks, next legal action        |
| `/aisdlc:start`       | Register an Intent and frame its brief; stop at Gate 1                      |
| `/aisdlc:specify`     | Testable requirements and criteria; stop at Gate 2                          |
| `/aisdlc:plan`        | Dependency-ordered Phases and Tasks; stop at Gate 3                         |
| `/aisdlc:run`         | Execute approved, ready Tasks                                               |
| `/aisdlc:verify`      | Evidence, pins, conformance gaps, traceability, convergence; stop at Gate 5 |
| `/aisdlc:close`       | Record the result and promote lessons                                       |
| `/aisdlc:gate`        | Record a human gate decision                                                |
| `/aisdlc:gate-packet` | Assemble a two-minute decision packet for one gate                          |
| `/aisdlc:understand`  | Build an anchored structural understanding, from code or from documents     |
| `/aisdlc:baseline`    | Pin an existing repository's current behaviour                              |
| `/aisdlc:program`     | Group Intents into one initiative                                           |
| `/aisdlc:conformance` | Export lifecycle records as assurance evidence                              |

## Five-minute orientation

```text
ai-sdlc doctor
ai-sdlc new --title "Pilot change" --type feature --mode compact --discovery-reason "small, well-understood change; nothing to discover"
ai-sdlc status I001
```

Then, in Claude Code:

```text
/aisdlc:start Pilot change
```

Claude drafts the Stage 1 brief and stops at Gate 1. A human approves it; the runner records the transition.

## How the lifecycle works

The only delivery hierarchy is:

```text
Program (optional, for large initiatives)
└── Intent
    └── Build Phase
        └── Task
```

| Stage         | Purpose                                                         | Main folder           | Human gate   |
| ------------- | --------------------------------------------------------------- | --------------------- | ------------ |
| 0 — Register | Create the permanent Intent                                     | `00-intent/`        | —           |
| 1 — Frame    | Agree on problem, outcome, scope, and success                   | `01-brief/`         | G1           |
| 2 — Specify  | Define requirements and acceptance criteria                     | `02-specification/` | G2           |
| 3 — Plan     | Define architecture, Phases, Tasks, dependencies, and resources | `03-planning/`      | G3           |
| 4 — Build    | Implement, capture evidence, and review Tasks                   | `04-build/`         | G4 after P00 |
| 5 — Converge | Recheck the complete system and traceability                    | `05-verification/`  | G5           |
| 6 — Close    | Record completion and promote lessons                           | `06-close/`         | —           |

G2 and G3 may be approved together only for a compact Intent. No other gates combine.

## Choose compact or full mode

Use `compact` only when all of these are true:

- The change fits one Build Phase and one or a few Tasks.
- Every acceptance criterion is deterministic Class D.
- There is no model behavior, security boundary, external side effect, migration, public-interface change, or irreversible action.
- Dependencies and shared resources are simple enough to review together.

Use `full` for everything else. Model-dependent work always uses full mode.

```text
ai-sdlc new --title "Improve status guidance" --type feature --mode compact --discovery-reason "small, well-understood change; nothing to discover"
ai-sdlc new --title "Add grounded support assistant" --type feature --mode full --contains-model-behavior --discovery-reason "new capability; no existing code to map"
```

Held-out verification applies in **both** modes. In compact mode the instrument may be as small as one verifier-authored test — the requirement is *provenance*, not volume.

## First Intent: complete walkthrough

For the first pilot, run Tasks sequentially in the main checkout. Add worktrees and parallel preparation only after the team understands gates, evidence, and independent review.

### 1. Register the Intent

```text
ai-sdlc new --title "Improve status guidance" --type feature --mode compact --discovery-reason "small, well-understood change; nothing to discover"
```

The runner returns a permanent identifier such as `I001` and creates `delivery/intents/I001-improve-status-guidance/00-intent/intent.yaml`. Identifiers are permanent — do not rename or reuse them manually.

### 2. Frame the outcome and approve G1

```text
/aisdlc:start I001
ai-sdlc advance I001 framed --actor developer
ai-sdlc gate approve I001 G1 --approver product-owner
```

### 3. Specify requirements

```text
/aisdlc:specify I001
```

Each acceptance criterion must have exactly one verification class:

| Class              | Use when                                   | Pass rule                                                            |
| ------------------ | ------------------------------------------ | -------------------------------------------------------------------- |
| D — Deterministic | A command has a stable result              | The declared command exits`0`                                      |
| S — Statistical   | Quality is measured across cases           | Median clears the threshold and the worst run stays within tolerance |
| J — Judged        | A calibrated judge evaluates quality       | Pinned judge and calibration clear the threshold                     |
| N — Negative      | An adversary attempts a prohibited outcome | Zero observed attack successes                                       |

**Every behavioural requirement also names a held-out instrument, or records a waiver.** This is checked at Gate 2 and again at convergence:

```text
ai-sdlc gate-packet I001 G2      # the packet lists any missing instrument before a human looks
ai-sdlc advance I001 specified --actor developer
ai-sdlc gate approve I001 G2 --approver product-owner
```

Advancing to `specified` also writes `02-specification/inherited-controls.yaml`, loading matching standing suites and active lessons from prior Intents.

If an approved requirement must change, reopen the gate instead of editing around it:

```text
ai-sdlc gate reopen I001 G2 --actor product-owner --reason "Acceptance threshold changed"
```

Reopening G2 invalidates evidence — but only for the requirements that **actually changed**. The runner compares per-requirement hashes, so a Task mapped to an untouched requirement keeps its evidence. That is what makes correcting a specification affordable enough that people actually do it instead of letting it drift.

### 4. Create the plan and approve G3

```text
/aisdlc:plan I001
```

Every Task declares its mapped requirements or source findings, dependencies, affected paths, exclusive shared resources, inputs and expected outputs, acceptance criteria, **visible verification commands**, **held-out commands with their authorship**, whether it is parallelizable, and its retry limit. Tasks that change persistent state also declare a `migration` block.

```text
ai-sdlc validate-intent I001
ai-sdlc advance I001 planned --actor developer
ai-sdlc gate approve I001 G3 --approver technical-owner
```

For a compact Intent, draft both specification and plan while the Intent is `specified`, then combine:

```text
ai-sdlc gate approve I001 G2-G3 --approver owner
ai-sdlc advance I001 planned --actor developer
```

### 5. Enter build and run the walking skeleton

```text
ai-sdlc advance I001 building --actor developer
ai-sdlc ready-tasks I001
ai-sdlc start-task I001-P00-T001 --actor implementer-01
/aisdlc:run I001-P00-T001
ai-sdlc verify-task I001-P00-T001
```

`verify-task` runs the visible and held-out commands in one attempt against one commit, and reports the delta:

```json
"conformance": {
  "visible_pass_rate": 1.0,
  "holdout_pass_rate": 0.0,
  "gap": 1.0,
  "conformance_gap": true
}
```

A `conformance_gap` means the implementation satisfies the tests it was given rather than the requirement those tests stand for. **Fix the implementation, never the held-out test.** This is not a flaky result and must not be retried as though it were.

Two retries are allowed; a third failed attempt blocks the Task and creates an escalation package.

### 6. Understand what the evidence is worth

`verify-task` captured the manifest: every command, its exit code, timings, a SHA-256 of
each stream, and the Git commit it is pinned to. There is one kind of evidence — the
provisional/authoritative split went with CI (ADR-007).

What makes it worth more than a claim is **where the capturing code lives**. The runner is
`scripts/lifecycle.py` inside the plugin directory, outside the repository under change,
and the `PreToolUse` guard refuses any edit that resolves inside the plugin root. So the
agent that implemented the Task cannot have authored the instrument that judged it — which
is the property the whole framework is built on.

What it is **not**: signed. Nobody who distrusts this machine can verify it. If you need
that, you need a CI workload identity, and this framework no longer has one.

Add `--mutation` thresholds on Class D criteria where test quality matters; surviving
mutants come back as structured retry input.

### 7. Register an independent review

```text
Use the aisdlc-verifier agent to review I001-P00-T001 against its captured evidence.
```

For Class N criteria or threat-sensitive changes, also use `aisdlc-red-team`. Both agents run a pinned model rather than `inherit`.

```yaml
reviewer_agent_id: verifier-01
reviewer_model: opus-5           # required; must differ in family from the implementer
implementer_model: claude-fable-5
decision: approved
checks:
  requirements: passed
  evidence: passed
  tests: passed
findings: []
```

```text
ai-sdlc review-task I001-P00-T001 \
  --review path/to/review-input.yaml \
  --evidence delivery/intents/.../evidence/attempt-2/manifest.json \
  --implementer implementer-01
```

The runner validates reviewer independence **by model family**, review schema, evidence hashes, source commit, and specification pins before moving the Task to `verified`. A review with no `reviewer_model` is rejected — the runner fails closed rather than assuming independence it cannot check.

Then approve G4 and scale to the remaining Tasks:

```text
ai-sdlc gate approve I001 G4 --approver technical-owner
ai-sdlc ready-tasks I001
```

### 8. Check integration readiness

```text
ai-sdlc merge-ready I001-P00-T001 --expected-commit <verified-git-commit>
ai-sdlc merge I001-P00-T001 --expected-commit <verified-git-commit>
```

Readiness passes only when the Task is verified, its review is approved, evidence is valid and pinned to the same commit, the verified base is not more than `merge.max_base_lag` commits behind the integration branch, and integration checks are clean.

Other terminal states have their own verbs, so an outcome is never recorded by editing a file:

```text
ai-sdlc supersede-task I001-P02-T004 --reason "Replaced by peer Tasks" --replaced-by I001-P02-T007
ai-sdlc fail-task I001-P03-T002 --reason "Escalation resolved by abandoning the Task"
ai-sdlc revert-task I001-P02-T003 --reason "Defect surfaced on trunk" --corrective I001-P04-T006
```

### 9. Converge the complete Intent

Commit all tracked source changes, then:

```text
ai-sdlc converge I001
```

Convergence requires every required Task verified or merged; G4 approved; latest evidence untampered and correctly pinned; approved independent reviews; complete bidirectional traceability; passing, non-stale S/J/N manifests; passing inherited suites; no open Critical or High finding; **no unresolved conformance gap**; **held-out coverage or a recorded waiver for every behavioural Task**; passing declared mutation thresholds; and valid baseline references.

Run convergence before committing the captured evidence: commit the source, capture evidence, converge, then commit the generated records.

### 10. Approve G5 and close

```text
ai-sdlc gate approve I001 G5 --approver acceptance-owner
ai-sdlc close I001 --actor acceptance-owner
```

Before close, every Critical or High finding must have an implemented blocking lesson target.

## Gate packets

Gate *count* is not what makes this framework expensive; gate *preparation* is. A reviewer who must reconstruct what changed will either rubber-stamp the gate or avoid it.

```text
ai-sdlc gate-packet I001 G3
ai-sdlc gate-packet I001 G3 --output review/g3-packet.md
ai-sdlc gate-packet I001 G3 --json
```

The packet renders the decision being asked, the diff since the gate was last presented, the artifacts under review, every outstanding deterministic check, evidence status including conformance gaps, inherited controls, open findings, and the cost so far. It is a read-only projection: it decides nothing and writes nothing into the lifecycle.

## Cost, metrics, and criterion decay

```text
ai-sdlc metrics cost                      # verification tax and where the time went
ai-sdlc metrics cost --intent I001
ai-sdlc loop-metrics                      # seven loop metrics, including discrimination
ai-sdlc metrics export --output metrics.json
ai-sdlc metrics export --openmetrics --output metrics.prom
```

The **verification tax** is verification cost divided by implementation cost, falling back to wall-clock when no monetary cost is reported. **Gate latency** is the interval from a gate becoming presentable to a human deciding it. **Criterion discrimination** is the fraction of criteria that have failed at least once in the last ten Intents — a criterion that never fails is either a solved problem or a dead instrument, and the framework routes it to a human to decide which.

All of these are reported, never enforced. A budget that blocked automatically would be a risk tier by another name.

## S, J, and N evaluations

Register a versioned dataset:

```text
ai-sdlc dataset register gold/groundedness-v1 datasets/groundedness \
  --owner quality-team --version 1 --trigger contains_model_behavior --selection-policy all
```

Adjudicate results you produced yourself:

```text
ai-sdlc eval run eval-spec.yaml results.json --output delivery/intents/I002-assistant/05-verification/evals
```

Or let the runner produce them, which closes the untrusted-caller hole:

```text
ai-sdlc eval execute eval-spec.yaml --output delivery/.../evals --adapter anthropic
```

Non-negotiable rules:

- S and J use exactly three runs — or, with `sequential_stopping` enabled, between three and `max_runs`. Only the sample size adapts; the decision rule stays predeclared.
- Mixed pass/fail results are flaky and do not pass.
- J requires current model and prompt pins, a confusion matrix, calibration agreement of at least **0.85**, a judge model from a **different family than the generator**, dual-order judgments, a decomposed binary rubric, at least two annotated exemplars, and reasoning before score.
- N passes only with zero observed attacker successes and never claims zero risk.
- Dataset, model, prompt, temperature, seed, and source commit pins must remain current.

Declining an inherited suite is an escalation and requires a written reason:

```text
ai-sdlc dataset decline I002 gold/groundedness-v1 --reason "Dataset owner has not approved this version"
```

## Pipeline profiles (ADR-010)

Different kinds of work can run under different rules. The constitution is fixed for every
Intent. `.ai-sdlc/pipeline.yaml` declares named profiles on top of the framework defaults
(the `standard` profile):

- **Tightening is free.** A profile can restrict the artifact modes, require files before a
  stage, raise the lint bar or a single rule's severity, require several different approvers
  from a named list, and forbid held-out waivers.
- **Loosening needs a waiver.** Lowering the lint bar, lowering a rule's severity, naming
  approvers outside `gates.approvers`, or allowing held-out waivers the repository disables
  each needs a `waivers` entry in the profile: `rule`, `approved_by`, `reason`, `approved_at`.
  Without it, the file is invalid.

```yaml
version: 1
by_type: {security: regulated, investigation_or_poc: spike}
profiles:
  regulated:
    description: Changes an auditor will read about.
    modes: [full]
    required_artifacts: {specified: [02-specification/threat-model.yaml]}
    specification: {fail_on: warning, rules: {SQ009: warning}}
    gates: {G2: {approvals: 2, approvers: [lena, omar, priya]}}
    verification: {holdout_waiver: forbidden}
  spike:
    description: Time-boxed investigation.
    modes: [compact]
    specification: {fail_on: never}
    waivers:
      - {rule: specification.fail_on, approved_by: omar, reason: "Spikes are thrown away", approved_at: "2026-09-25"}
```

```text
ai-sdlc pipeline                                   # the constitution, standard, every profile and waiver; exits 1 if invalid
ai-sdlc new --title "..." --type security ...      # runs the type's profile; --profile may only choose one at least as strict
ai-sdlc intent-profile I007 regulated --actor lena --reason "now touches authentication"
```

`new` pins the resolved profile into `00-intent/profile.yaml` (runner-owned) and records
its name and hash in `intent.yaml`. Editing `pipeline.yaml` therefore changes only
Intents created afterwards, and an Intent without a pin runs `standard`. `intent-profile`
moves an in-flight Intent only to a profile at least as strict on every rule. `advance`
refuses a stage whose required files are missing (`advance_refused`, check
`profile_artifacts`). A gate that needs several approvers stays waiting until enough
different named people approve. Each approval is a `gate_approval` audit event, and a
reopened gate starts counting again. `doctor` reports an invalid pipeline as a failure,
and a waived loosening as a warning that names it.

## Programs and contention

```text
ai-sdlc program new --title "Platform migration" --owner architect
ai-sdlc program add PRG001 I004
ai-sdlc program add PRG001 I007 --requires I004
ai-sdlc program status PRG001
ai-sdlc program waive PRG001 I007 I004 --actor architect --reason "Hotfix ordering agreed"
```

A Program is a dependency graph and a reporting scope. It carries no gates, criteria, or evidence of its own — every assurance decision stays on the Intent.

`advance <id> building` enforces the order: while a predecessor has neither completed nor been waived, the transition is refused on the record (`advance_refused`, check `program_dependency`) and the refusal names the exact waive command. A waiver is kept in the programme registry and in the audit log (`dependency_waived`). `scale.max_building` limits how many Intents may be building at once (check `build_limit`); stepping back out of `building` is never limited.

```text
ai-sdlc intent-priority I007 high --actor product --reason "Support backlog doubled"
```

Priority (`high`, `normal`, `low`) is an audited decision, not a state field: the latest `priority_set` event is the current priority, so every reprioritisation stays on the record. It orders the portfolio and changes no gate.

```text
ai-sdlc intent-resources I007 --declare schema:orders --declare prompt:summary
ai-sdlc coapprove-resource schema:orders --intent I004 --intent I007 \
  --approver architect --reason "Coordinated expand/contract"
```

Contention between Intents is *surfaced*, not silently blocked: sometimes the second Intent should wait, sometimes both teams knowingly coordinate. What must not happen is that nobody decides.

Cross-repository Intents are **not** supported. Federation admitted evidence from another repository by verified signature, and the signing path went with CI (ADR-007). Run one Intent per repository and order them with a Program.

## Conformance export

```text
ai-sdlc export-conformance I001
ai-sdlc export-conformance PRG001 --output audit/register.md
ai-sdlc export-conformance I001 --json
```

A read-only projection mapping records you already hold onto NIST AI RMF functions, ISO/IEC 42001 Annex A controls, and NIST SSDF practices, plus the system-register row those frameworks ask for. It reports gaps honestly — an export that always looked complete would be worthless to the person reading it — and it cannot pass or fail an Intent. Prefer the Program level for an audit; that is the unit a reviewer actually examines.

## Lessons and continuous improvement

A lesson moves through:

```text
proposed -> accepted -> implemented -> active -> measured -> active or retired
```

```text
ai-sdlc lesson propose lesson.yaml
ai-sdlc lesson transition LESSON-001 accepted --actor process-owner
ai-sdlc lesson promote LESSON-001
ai-sdlc lesson decay-review --current-intent 20 --runtime-budget-seconds 60 --estimated-suite-seconds 90
```

Promotion materializes the target artifact. Later matching Intents inherit active controls automatically during specification. Retired lessons move to `.ai-sdlc/lessons/retired.yaml` instead of being deleted.

## Optional isolated worktrees

```text
ai-sdlc orchestrate I001 --prepare
ai-sdlc worktree create I001-P01-T001
ai-sdlc context-pack I001-P01-T001
ai-sdlc worktree remove I001-P01-T001 --confirm
```

The orchestrator selects only Tasks whose dependencies, affected paths, and named shared resources do not conflict. Removal is restricted to the managed worktree root and always requires explicit confirmation.

## Configuration reference

`.ai-sdlc/config.yaml`, all optional with safe defaults:

| Section           | Key settings                                                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `evidence`      | `retain_failed_attempts`, `max_payload_bytes`, `redact_secrets`, `redact`, `max_redaction_ratio`                                                                |
| `review`        | `reviewer_model`, `forbid_same_family`, `require_reviewer_model`                                                                                                    |
| `evaluations`   | `judge_minimum_agreement` (floor 0.85), dual-order / rubric / exemplar / reasoning requirements, `judge_human_review_fraction`, `sequential_stopping`, `max_runs` |
| `verification`  | `require_holdout`, `allow_holdout_waiver`, `conformance_gap_severity`, `mutation_default_threshold`                                                               |
| `gates`         | `approvers` (gate → permitted identities), `require_declared_approver`                                                                                               |
| `merge`         | `max_base_lag`, `integration_ref`                                                                                                                                     |
| `scale`         | `programs_enabled`, `max_building` (Intents building at once; unset means no limit), `index_enabled` (`repository_id` and `federation_enabled` are retired, ADR-007)                                                                |
| `cost`          | `enabled`, `verification_tax_warning_ratio`                                                                                                                           |
| `security`      | `require_declared_posture`, default network / filesystem / credential posture                                                                                           |
| `understanding` | `enabled`, `drift_threshold` (fraction of anchored paths that may move before stale), `include_when_stale`                                                          |

`judge_minimum_agreement` may be **raised** but never lowered: an old `0.80` left in a target repository does not survive the upgrade.

## Daily command reference

| Command                                      | Purpose                                                            |
| -------------------------------------------- | ------------------------------------------------------------------ |
| `ai-sdlc status I001`                      | State, gates, Tasks, and next legal actions                        |
| `ai-sdlc validate-intent I001`             | Validate schemas, completeness, and references                     |
| `ai-sdlc spec-lint I001`                   | Report requirement-quality findings and quality-attribute coverage |
| `ai-sdlc contract-registry I001`           | Report cross-Intent contract contention and breaking changes       |
| `ai-sdlc glossary promote I001 <term>`     | Share an Intent's term through the repository glossary             |
| `ai-sdlc ready-tasks I001`                 | Select the next dependency- and lock-safe batch                    |
| `ai-sdlc gate-packet I001 G3`              | Assemble a gate decision packet                                    |
| `ai-sdlc trace I001`                       | Regenerate bidirectional traceability                              |
| `ai-sdlc inherit-suites I001`              | Preview matching standing suites                                   |
| `ai-sdlc validate-evidence <manifest>`     | Check evidence hashes and optional pins                            |
| `ai-sdlc understand record`                | Validate an understanding document's anchors and stamp its pin     |
| `ai-sdlc understand status`                | Report`fresh` / `drifting` / `stale`, and which claims moved |
| `ai-sdlc understand scan`                  | Inventory what git tracks: languages, tests, owners, modules       |
| `ai-sdlc intent-priority I001 high`        | Record an Intent's priority with `--actor` and `--reason`          |
| `ai-sdlc pipeline`                         | Show the constitution, `standard`, every profile and its waivers   |
| `ai-sdlc intent-profile I001 regulated`    | Move an Intent to a stricter profile, with `--actor` and `--reason` |
| `ai-sdlc baseline capture probes.yaml`     | Pin current behaviour                                              |
| `ai-sdlc baseline compare BASELINE-001`    | Report regressions against the reference                           |
| `ai-sdlc retry-task <task> --reason "..."` | Record a failure and apply retry limits                            |
| `ai-sdlc loop-metrics`                     | Seven loop metrics, plus cost and gate-latency figures             |
| `ai-sdlc metrics cost`                     | Verification tax and gate latency                                  |
| `ai-sdlc export-conformance I001`          | Assurance projection                                               |

Use `--repo path/to/project` when the current directory is not the target repository.

## Human responsibilities

The runner will not decide:

- G1: whether the problem and outcome are worth pursuing.
- G2: whether requirements, thresholds, failure behavior, and exclusions are correct.
- G3: whether the architecture and Task graph are acceptable.
- G4: whether the walking skeleton proved the path.
- G5: whether the complete outcome is acceptable with its limitations.
- Whether a held-out waiver is justified.
- Whether a dormant criterion is a solved problem or a dead instrument.
- Whether two Intents may share a contended resource.
- Whether a lesson's ongoing cost is worth accepting.
- Whether a proposed check from an understanding becomes an enforcing check. There is no promotion command: you copy accepted lines into your probes file yourself, one at a time.
- Whether to merge, release, or deploy through external repository processes.

## Runner-owned files

Never hand-edit:

- `.ai-sdlc/runtime/` state, counters, gates, locks, cost ledger, or audit events.
- Task evidence and evidence manifests.
- `.ai-sdlc/baselines/` and `.ai-sdlc/programs/` records.
- Generated `traceability.json` or convergence reports.
- Permanent identifiers after their lifecycle boundary.
- The `commit` and `recorded_at` frontmatter fields in `.ai-sdlc/project-understanding.md`. `understand record` stamps those after validating the document; writing them by hand claims a validation that never ran. The prose and anchors above the frontmatter are yours to edit freely.

Use the runner for these mutations. Claude Code hooks block supported direct-edit attempts.

## Troubleshooting and recovery

Begin with `ai-sdlc status I001` and `ai-sdlc validate-intent I001`.

| Condition                                | Meaning                                          | Action                                                                             |
| ---------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------- |
| `illegal transition`                   | The requested stage is out of order              | Run`status`; complete the required artifact or gate                              |
| Gate requires another state              | Approval was attempted too early                 | Move the Intent to that gate's review state                                        |
| `may not approve`                      | Approver is not in the gate's declared list      | Route to a declared approver, or amend`gates.approvers`                          |
| **Specification-conformance gap**  | Visible suite passes, held-out suite fails       | Fix the implementation. Never edit the held-out test                               |
| `declares no held-out instrument`      | A behavioural Task has no independent check      | Add`holdout_commands`, or record a `holdout_waiver` with a reason and approver |
| `reviewer_model` missing / same family | Independence cannot be established               | Pin a cross-family reviewer model                                                  |
| Specification pin mismatch               | A mapped requirement changed                     | Re-run verification against the approved revision                                  |
| `commits behind`                       | The verified base is stale                       | Rebase and re-verify against the integration branch                                |
| Resource conflict                        | Ready Tasks share a path or named resource       | Run them sequentially                                                              |
| Intent-scope contention                  | Two Intents claim one shared resource            | Serialize, or`coapprove-resource` on the record                                  |
| Task is`blocked`                       | The retry limit was exceeded                     | Resolve the escalation package, then resume explicitly                             |
| Tracked source changes at convergence    | Source files are not committed                   | Commit intended changes and recapture evidence if needed                           |

If runner state is corrupt, do not repair JSON by hand:

```text
ai-sdlc repair-state --confirm
```

The command validates the audit sequence, preserves unreadable state as `state.corrupt-<timestamp>.json`, and reconstructs runtime state. It refuses recovery if the audit log is malformed.

Recovery rules, in full:

- **Never repair runner-owned state by hand.** Preserve the repository and its worktrees, find the source of the corruption, then run `repair-state --confirm`.
- **Blocked Tasks** resume only through the legal `blocked → in_progress` transition, after a human resolves the escalation package.
- **Terminal records** (superseded, failed, reverted, completed, aborted) stay terminal and remain in the audit trail.
- **Retained worktrees** can be resumed with `ai-sdlc worktree resume`. Removal always needs `worktree remove --confirm` and is limited to the managed worktree root.
- **Evidence attempts and retired lessons** are never discarded during recovery.

## Repository architecture

```text
<plugin>/                     # installed by /plugin install; the runner lives here, outside your repo
├── .claude-plugin/           # plugin and marketplace manifests
├── commands/                 # the fifteen /aisdlc:* slash commands
├── agents/                   # read-only verifier, red-team, cartographer (pinned models)
├── skills/aisdlc/            # conversational lifecycle skill
├── hooks/                    # guard hooks: SessionStart, PreToolUse, PostToolUse, Stop
├── scripts/                  # the deterministic runner (scripts/lifecycle.py, scripts/aisdlc/)
└── .ai-sdlc/                 # framework assets: schemas, templates, workflow, policies

<your repository>/            # written by /aisdlc:init
├── .ai-sdlc/
│   ├── config.yaml           # repository configuration
│   ├── constitution.md       # non-negotiable rules
│   ├── project-context.md    # human-authored conventions, commands, habits
│   ├── project-understanding.md  # agent-derived structure, anchored and pinned (advisory)
│   ├── lessons/ evals/ security/ baselines/ programs/
│   └── runtime/              # runner-owned mutable state; gitignored
├── delivery/intents/         # lifecycle records, one folder per Intent
└── AGENTS.md                 # agent operating rules
```

Design decisions are cited by ADR number throughout. The README's *Design decisions and deeper reading*
section lists every ADR and its status; the full texts are in git history.

## Developing the framework

```text
uv sync --locked --dev
uv run pytest -q
uv run ruff check .
python tools/validate_p00.py
uv build
```

## Production-readiness expectation

The implementation is complete, but process readiness must be proven through real use. Before calling a pilot repository production-ready, complete at least five compact and two full Intents without manual runtime-state repair — and additionally demonstrate:

- A held-out instrument, or a recorded waiver, for every behavioural requirement.
- No review accepted whose reviewer model family matched the implementer's.
- An instrument boundary that held: `doctor` reporting `instrument-boundary: ok` in a repository that installed the plugin rather than vendoring the runner.
- A measured verification tax, and at least one check retired on that evidence — proof the framework can shrink as well as grow.
- A brownfield repository onboarded via `baseline` and completed with regression-relative criteria.
- Two concurrent Intents correctly serialized or co-approved on an intersecting resource.
- A reopened G2 that invalidated only the evidence tracing to changed requirements.
- A conformance export reviewed and accepted as register-grade by a compliance stakeholder.
- A non-Claude agent driving the lifecycle via `AGENTS.md` with all gates and guards holding.

Do not create synthetic Intents merely to satisfy the threshold.
