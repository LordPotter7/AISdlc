# Operations reference

Commands, agents, configuration, repository layout, responsibilities, and recovery.

> **`ai-sdlc` in examples is shorthand** for `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py"`. Nothing is installed on `PATH`.

## Command reference

### Claude Code — the fifteen `/aisdlc:*` commands

| Command                 | Operation                                                                   |
| ----------------------- | --------------------------------------------------------------------------- |
| `/aisdlc:init`        | Scaffold the framework into this repository and report what was written     |
| `/aisdlc:doctor`      | Report whether the lifecycle can run here, and what is missing              |
| `/aisdlc:status`      | Committed state, runner state, gates, ready Tasks, next legal action        |
| `/aisdlc:start`       | Register an Intent and frame its brief; stop at Gate 1                      |
| `/aisdlc:specify`     | Testable requirements and criteria; stop at Gate 2                          |
| `/aisdlc:plan`        | Dependency-ordered Phases and Tasks; stop at Gate 3                         |
| `/aisdlc:run`         | Execute approved, ready Tasks under gates, locks, and retry limits          |
| `/aisdlc:verify`      | Evidence, pins, conformance gaps, traceability, convergence; stop at Gate 5 |
| `/aisdlc:close`       | Record the result and promote lessons                                       |
| `/aisdlc:gate`        | Record a human gate decision, or show which gate is awaiting one            |
| `/aisdlc:gate-packet` | Assemble a two-minute decision packet for one gate                          |
| `/aisdlc:understand`  | Build an anchored structural understanding, from code or from documents     |
| `/aisdlc:baseline`    | Pin an existing repository's current behaviour                              |
| `/aisdlc:program`     | Group Intents into one initiative with ordering and a roll-up               |
| `/aisdlc:conformance` | Export lifecycle records as NIST AI RMF, ISO/IEC 42001, and SSDF evidence   |

### Agents

| Agent                   | Use                                                                                                                                       |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `aisdlc-verifier`     | Independently review a Task diff, declared checks, and runner-captured evidence — after verification, before the Task is marked verified |
| `aisdlc-red-team`     | Fresh-context adversarial review of a specification, plan, Task, phase, or convergence package                                            |
| `aisdlc-cartographer` | Build a structural understanding of a project from one declared source — once per repository, at onboarding                              |

All three are read-only and hold no lifecycle authority. Run them from **fresh contexts** and pin a model rather than using `inherit` — self-review is not independent review.

### Runner — day-to-day

| Command                                              | Purpose                                                                                        |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `ai-sdlc status I001`                              | State, gates, Tasks, and next legal actions                                                    |
| `ai-sdlc validate-intent I001`                     | Validate schemas, completeness, and references                                                 |
| `ai-sdlc spec-lint I001`                           | Report requirement, contract, and threat-model quality findings and quality-attribute coverage |
| `ai-sdlc contract-registry I001`                   | Report cross-Intent contract contention and breaking changes                                   |
| `ai-sdlc glossary promote I001 <term> --owner <o>` | Share an Intent's term through the repository glossary                                         |
| `ai-sdlc ready-tasks I001`                         | Select the next dependency- and lock-safe batch                                                |
| `ai-sdlc gate-packet I001 G3`                      | Assemble a gate decision packet                                                                |
| `ai-sdlc trace I001`                               | Regenerate bidirectional traceability                                                          |
| `ai-sdlc inherit-suites I001`                      | Preview matching standing suites                                                               |
| `ai-sdlc validate-evidence <manifest>`             | Check evidence hashes and optional pins                                                        |
| `ai-sdlc understand record` / `status`           | Validate anchors and stamp the pin · report `fresh` / `drifting` / `stale`              |
| `ai-sdlc baseline capture probes.yaml`             | Pin current behaviour                                                                          |
| `ai-sdlc baseline compare BASELINE-001`            | Report regressions against the reference                                                       |
| `ai-sdlc retry-task <task> --reason "…"`          | Record a failure and apply retry limits                                                        |
| `ai-sdlc loop-metrics`                             | Seven loop metrics, plus cost and gate-latency figures                                         |
| `ai-sdlc metrics cost` / `metrics export`        | Verification tax and gate latency · JSON and OpenMetrics                                      |
| `ai-sdlc export-conformance I001`                  | Assurance projection                                                                           |
| `ai-sdlc migrate --apply`                          | Move records forward a schema generation                                                       |
| `ai-sdlc repair-state --confirm`                   | Rebuild runtime state from the audit log                                                       |

Use `--repo path/to/project` when the current directory is not the target repository.

<details>
<summary><b>Full runner surface</b> — every command group</summary>

| Group         | Commands                                                                                                                                                                                                                   |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Repository    | `init` · `doctor` · `version` · `migrate`                                                                                                                                                                       |
| Intent        | `new` · `validate-intent` · `spec-lint` · `status` · `advance` · `converge` · `close` · `trace`                                                                                                     |
| Gates         | `gate approve` · `gate reopen` · `gate-packet` · `pre-gate-review` · `repair-state`                                                                                                                          |
| Decisions     | `decision record` · `decision accept` · `decision list`                                                                                                                                                            |
| Tasks         | `ready-tasks` · `start-task` · `verify-task` · `review-task` · `retry-task` · `fail-task` · `supersede-task` · `revert-task` · `merge-ready` · `merge` · `context-pack` · `orchestrate` |
| Evidence      | `validate-evidence`                                                                                                                                                                                                      |
| Evaluations   | `dataset register` · `dataset decline` · `eval run` · `eval execute` · `inherit-suites`                                                                                                                      |
| Worktrees     | `worktree create` · `resume` · `retain` · `remove`                                                                                                                                                              |
| Lessons       | `lesson propose` · `transition` · `promote` · `inherit` · `decay-review` · `phase-retrospective`                                                                                                          |
| Understanding | `understand record` · `understand status`                                                                                                                                                                             |
| Baselines     | `baseline capture` · `baseline compare`                                                                                                                                                                               |
| Programs      | `program new` · `add` · `waive` · `status`                                                                                                                                                                      |
| Resources     | `intent-resources` · `coapprove-resource`                                                                                                                                                                             |
| Metrics       | `loop-metrics` · `metrics export` · `metrics cost` · `export-conformance`                                                                                                                                       |

</details>

---

## Configuration reference

`.ai-sdlc/config.yaml` — all optional, with safe defaults.

| Section           | Key settings                                                                                                                                                                               |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| *(top level)*   | `delivery_root` (default `delivery/intents`), `default_artifact_mode` (`compact` / `full`), `retry_limit` (default 2)                                                          |
| `evidence`      | `retain_failed_attempts`, `max_payload_bytes`, `redact_secrets`, `redact_builtins`, `redact`, `max_redaction_ratio`                                                            |
| `review`        | `reviewer_model`, `forbid_same_family`, `require_reviewer_model`, `require_pre_gate_review`                                                                                        |
| `evaluations`   | `runs` (≥ 3), `judge_minimum_agreement` (floor 0.85), dual-order / rubric / exemplar / reasoning requirements, `judge_human_review_fraction`, `sequential_stopping`, `max_runs` |
| `verification`  | `require_holdout`, `allow_holdout_waiver`, `conformance_gap_severity`, `mutation_default_threshold`, `mutation_stages`                                                           |
| `orchestration` | `max_parallel_tasks` (default 4), `retry_limit`, `retain_worktrees`                                                                                                                  |
| `gates`         | `approvers` (gate → permitted identities), `require_declared_approver`, `require_verified`                                                                                          |
| `merge`         | `max_base_lag`, `integration_ref`                                                                                                                                                      |
| `scale` | `programs_enabled`, `max_building` (WIP limit). `repository_id` and `federation_enabled` are retired (ADR-007) and ignored |
| `cost`          | `enabled`, `currency`, `verification_tax_warning_ratio`                                                                                                                              |
| `security`      | `require_declared_posture`, default network / filesystem / credential posture                                                                                                            |
| `understanding` | `enabled`, `drift_threshold` (fraction of anchored paths that may move before stale), `include_when_stale`                                                                           |
| `specification` | `fail_on`: lowest spec-lint severity that blocks Gate 2 (`error` default, `warning`, `never`)                                                                                      |
| *pipeline* | Per-kind-of-work profiles live in `.ai-sdlc/pipeline.yaml`, not here. See [Pipeline profiles](reference.md#pipeline-profiles-adr-010) |

`judge_minimum_agreement` may be **raised** but never lowered: an old `0.80` left in a target repository does not survive the upgrade.

**Environment variables**

| Variable                  | Effect                                                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------- |
| `AISDLC_HOOKS=off` | Suspend the guard hooks inside a managed repository, for deliberately unmanaged work. `doctor` warns while it is set |
| `AISDLC_FRAMEWORK_ROOT` | Override where templates and schemas resolve from (tests and unusual layouts). `doctor` warns while it is set |
| `AISDLC_PLUGIN_ROOT`    | Studio only: the plugin checkout to run the bridge from, if it is not found automatically |
| `AISDLC_BRIDGE_PYTHON`  | Studio only: the exact Python to run the bridge with (authoritative; no fallback)         |

---

## Repository layout and what `init` writes

`init` writes only what the repository owns. The rest is inherited from the runner, so upgrading the runner upgrades every initialized repository at once.

| Written by `init`                                              | Inherited from the runner    |
| ---------------------------------------------------------------- | ---------------------------- |
| `.ai-sdlc/config.yaml`                                         | `constitution.md`          |
| `.ai-sdlc/project-context.md` (a stub to fill in)              | `schemas/`                 |
| `.ai-sdlc/lessons/`, `.ai-sdlc/evals/`                       | `workflow/`, `policies/` |
| `.ai-sdlc/security/standing-checks.yaml`                       | `roles/`, `templates/`   |
| `CLAUDE.md` rules block, `AGENTS.md`, `.gitignore` entries |                              |
| the delivery root                                                |                              |

`init` writes **no hook entries**. The plugin registers the guards for every repository you
open, so a project-level copy would only double every check — and could only name a runner
that is not installed. `--hooks project` remains as an explicit escape hatch.

A repository that needs to tailor an inherited file places its own copy under `.ai-sdlc/`, which then takes precedence.

A prepared repository looks like this:

```text
your-project/
├── .ai-sdlc/                 # config, context, lessons, evals, security, baselines, runtime
├── delivery/intents/         # lifecycle records for this project's changes
├── AGENTS.md                 # agent-agnostic doctrine
└── CLAUDE.md                 # concise repository-wide rules
```

**Notes that save time later**

- `init` does **not** scaffold `.ai-sdlc/project-understanding.md`. That file is *produced* by `/aisdlc:understand`, not filled in from a stub. The two stay separate on purpose: `project-context.md` is what a person verified; the understanding is derived and may go stale. Merging them would destroy the ability to tell which half is which.
- `init` is **idempotent**. Re-running reports `skipped` for anything that already exists, never duplicates a hook entry or a `CLAUDE.md` block, and re-pins the standing regression suites against the current checkout. Use `--force` to deliberately rewrite repository-owned files.
- Keep `.ai-sdlc/runtime/` and `.ai-sdlc/metrics/` **out of version control** — `init` gitignores both. Committing runtime state breaks convergence. Never copy `.ai-sdlc/runtime/` or delivery records between repositories; they belong to the repository that created them.
- If a repository was initialised *before* the move to a plugin-native runner, its `.claude/settings.json` still calls `ai-sdlc hook <event>` — a command that is no longer installed. `doctor` names that specifically rather than reporting the hooks as merely unwired. Delete those entries; the plugin already supplies them.

<details>
<summary><b>This repository's own layout</b> — for people working on the framework itself</summary>

```text
AISdlc/
├── .claude-plugin/        # plugin + marketplace manifests
├── .version-bump.json     # every file that carries the version; tools/bump-version.py writes them
├── after-install.md       # what to do next, shown after /plugin install
├── commands/              # the fifteen /aisdlc:* slash commands
├── agents/                # verifier, red-team, cartographer
├── skills/aisdlc/         # the aisdlc skill and its references
├── hooks/
│   ├── hooks.json         # SessionStart, PreToolUse, PostToolUse, Stop
│   ├── run-hook.cmd       # polyglot launcher: batch under cmd.exe, sh elsewhere
│   ├── guard              # extensionless target — NOT .sh (see ADR-007)
│   └── guard.py           # the guard itself, stdlib only
├── scripts/               # RUNTIME — what the plugin executes (ADR-008)
│   ├── lifecycle.py       # the runner entry point; nothing is installed
│   └── aisdlc/            # the runner package
│       └── vendor/        # pure-Python third-party code; no build step
├── tools/                 # MAINTENANCE — never referenced from runtime (ADR-008)
├── workflows/build.js     # the Build-stage phase structure
├── legacy-command-shims/  # removed commands, not loaded, each naming its replacement
├── .ai-sdlc/              # framework assets: schemas, workflow, policies, roles, templates
├── docs/
│   ├── reference.md       # the exhaustive guide
│   ├── decisions/         # architecture decision records, ADR-001 to ADR-010
│   ├── diagrams/          # editable HTML sources for the diagrams in this README
│   └── guide.html         # visual, interactive guide to the whole framework
├── image/README/          # exported SVG diagrams referenced above
├── examples/              # a worked compact Intent
├── desktop/               # AISDLC Studio: Tauri 2 host (src-tauri/) + React UI (src/)
├── .github/               # this repository's own CI, release and code scanning
├── SECURITY.md · SUPPORT.md · CONTRIBUTING.md · CHANGELOG.md · CODE_OF_CONDUCT.md
└── tests/
    ├── test_*.py          # the deterministic runner suite
    └── plugin/            # the plugin surface: frontmatter, hooks, imports, claims, schema validator
```

Regenerate a diagram by editing its HTML in [docs/diagrams/](diagrams/) and re-exporting the `<svg>` node into [image/README/](../image/README/).

</details>

---

## Who decides what

### The runner will not decide

- **G1** — whether the problem and outcome are worth pursuing.
- **G2** — whether requirements, thresholds, failure behaviour, and exclusions are correct.
- **G3** — whether the architecture and Task graph are acceptable.
- **G4** — whether the walking skeleton proved the path.
- **G5** — whether the complete outcome is acceptable with its limitations.
- Whether a held-out waiver is justified.
- Whether a dormant criterion is a solved problem or a dead instrument.
- Whether two Intents may share a contended resource.
- Whether a lesson's ongoing cost is worth accepting.
- Whether a proposed check from an understanding becomes an enforcing check.
- Whether to merge, release, or deploy through external repository processes.

### Runner-owned files — never hand-edit

- `.ai-sdlc/runtime/` state, counters, gates, locks, cost ledger, or audit events.
- Task evidence and evidence manifests.
- `.ai-sdlc/baselines/` and `.ai-sdlc/programs/` records.
- Generated `traceability.json` or convergence reports.
- Permanent identifiers after their lifecycle boundary.
- The `commit` and `recorded_at` frontmatter fields in `.ai-sdlc/project-understanding.md`.

Use the runner for these mutations. Claude Code hooks block supported direct-edit attempts; under another agent the runner refuses them one step later.

---

## Troubleshooting and recovery

Begin with `ai-sdlc status I001` and `ai-sdlc validate-intent I001`.

| Condition                                | Meaning                                     | Action                                                                              |
| ---------------------------------------- | ------------------------------------------- | ----------------------------------------------------------------------------------- |
| `illegal transition`                   | The requested stage is out of order         | Run `status`; complete the required artifact or gate                              |
| Gate requires another state              | Approval was attempted too early            | Move the Intent to that gate's review state                                         |
| `may not approve`                      | Approver is not in the gate's declared list | Route to a declared approver, or amend `gates.approvers`                          |
| **Specification-conformance gap**  | Visible suite passes, held-out suite fails  | **Fix the implementation. Never edit the held-out test**                      |
| `declares no held-out instrument`      | A behavioural Task has no independent check | Add `holdout_commands`, or record a `holdout_waiver` with a reason and approver |
| `reviewer_model` missing / same family | Independence cannot be established          | Pin a cross-family reviewer model                                                   |
| Specification pin mismatch               | A mapped requirement changed                | Re-run verification against the approved revision                                   |
| `commits behind`                       | The verified base is stale                  | Rebase and re-verify against the integration branch                                 |
| Resource conflict                        | Ready Tasks share a path or named resource  | Run them sequentially                                                               |
| Intent-scope contention                  | Two Intents claim one shared resource       | Serialize, or `coapprove-resource` on the record                                  |
| Task is `blocked`                      | The retry limit was exceeded                | Resolve the escalation package, then resume explicitly                              |
| Tracked source changes at convergence    | Source files are not committed              | Commit intended changes and recapture evidence if needed                            |

If runner state is corrupt, **do not repair JSON by hand**:

```text
ai-sdlc repair-state --confirm
```

The command validates the audit sequence, preserves unreadable state as `state.corrupt-<timestamp>.json`, and reconstructs runtime state. It refuses recovery if the audit log is malformed. Blocked Tasks resume only through `blocked → in_progress` after a human resolves the escalation; evidence attempts and retired lessons are never discarded.
