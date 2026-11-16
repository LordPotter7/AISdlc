# Claude Code Integration Reference

## Plugin layout

The framework ships as a single Claude Code plugin. Everything Claude Code loads lives in
the plugin bundle; everything the target repository owns lives under its own `.ai-sdlc/`.

```text
.claude-plugin/plugin.json        manifest: identity only
.claude-plugin/marketplace.json   marketplace descriptor; source is `./`
commands/                         slash commands, one per lifecycle operation
skills/aisdlc/SKILL.md            doctrine, always loaded
agents/aisdlc-verifier.md         read-only independent review
agents/aisdlc-red-team.md         read-only adversarial review
hooks/hooks.json                  lifecycle guardrails
hooks/run-hook.cmd                polyglot launcher: batch under cmd.exe, sh elsewhere
hooks/guard                       extensionless target — NOT .sh, see below
scripts/lifecycle.py              the runner entry point; nothing is installed
scripts/aisdlc/                   the runner package, stdlib-only + vendored
tools/                            maintenance only; never referenced at runtime
```

The manifest carries identity — `name`, `version`, `description`, `author`, `keywords` —
and nothing else. `commands/`, `agents/`, `skills/`, and `hooks/hooks.json` are all
auto-discovered at those conventional paths, so declaring them again is at best redundant
and at worst fatal: `agents` is validated as a path to a *file*, so `"agents": "./agents"`
is rejected with `agents: Invalid input` and the entire plugin fails to install. `commands`
and `agents` also *replace* the default scan rather than adding to it, so a stale entry
drops components silently instead of erroring. Add a component field only to point at a
path outside the conventional layout, and match the type the field expects.

The target repository holds only what it owns: `.ai-sdlc/config.yaml`,
`.ai-sdlc/project-context.md`, `.ai-sdlc/lessons/`, `.ai-sdlc/evals/`, runtime state, and
the delivery records under the configured `delivery_root`.

Framework-owned assets — the constitution, schemas, workflow, policies, roles, and
templates — are **not** copied into a target repository. They resolve from the installed
runner package, so upgrading the runner upgrades every initialized repository. A repository
that needs to tailor one places its own copy under `.ai-sdlc/`, which then wins. See
`src/aisdlc/assets.py` for the resolution order.

These conventions follow the current official documentation:

- https://code.claude.com/docs/en/plugins
- https://code.claude.com/docs/en/slash-commands
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/hooks

## Installation

Two pieces, installed once per machine, in this order.

**The plugin.** The marketplace entry declares `"source": "./"`, which resolves against the
marketplace root — so the same descriptor works whether the marketplace was added from a
filesystem path or from a git remote. Nothing is published to PyPI.

```text
/plugin marketplace add LordPotter7/AISdlc          # hosted
/plugin marketplace add <path-to-this-checkout> # or local, for development
/plugin install aisdlc@aisdlc-local
```

The marketplace name is a registry key fixed at first registration — `add` matches an
existing entry by source path, so a rename in `marketplace.json` never re-keys an install
that already exists. Always install under the name `/plugin marketplace list` reports.

**The runner.** Nothing is installed. Every command file and the plugin's `hooks.json` name
an absolute path under `${CLAUDE_PLUGIN_ROOT}`, so the only prerequisite is a stock
`python3` on `PATH`:

```text
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" version
```

**The hook seam.** `hooks.json` routes every event through `hooks/run-hook.cmd`, a polyglot
file that is simultaneously a valid Windows batch script and a valid POSIX shell script. It
probes Git Bash at its two standard install locations before falling back to `PATH`, so a
Windows machine with Git installed needs no extra configuration. Its targets are
**extensionless** on purpose: Claude Code's Windows auto-detection prepends `bash` to a
`.sh` command string, which would re-enter the launcher instead of running the target.

The guard fails **open** when no shell or no Python 3.12+ is present, and says so on
stderr. Failing closed would make every edit impossible on such a machine; failing silently
would make an inactive guard indistinguishable from a satisfied one.

Then, in each repository that should be lifecycle-managed:

```text
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" init
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" doctor
```

### Why `PATH` and not `${CLAUDE_PLUGIN_ROOT}`

`${CLAUDE_PLUGIN_ROOT}` is documented as ephemeral: it changes on every plugin update and
must not hold state. An environment built inside it — `uv run --project "${CLAUDE_PLUGIN_ROOT}"`,
for instance — is therefore discarded and rebuilt on each upgrade. It is also undocumented
for slash-command markdown, so commands could not use it even if hooks did.

A bare name on `PATH` survives plugin updates, resolves identically on Windows and POSIX,
and gives commands and hooks one mechanism instead of two. It also keeps the hook command
string free of paths, quotes, and variables, so `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" hook stop` tokenises identically
under `sh`, Git Bash, `cmd`, and PowerShell.

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" version` reports the resolved runner and its framework asset root. `doctor` checks
the same thing and distinguishes *not installed* (`fail`) from *installed but not on `PATH`*
(`warn`), because the fixes differ.

## Commands over skill dispatch

Each lifecycle operation is a slash command under `commands/`, not a branch inside the
skill. A command carries `description`, `argument-hint`, and a scoped `allowed-tools`
list, and uses `!` pre-execution to inject live runner state into the prompt:

```markdown
Runner state: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" status $1`
```

This matters for two reasons. A slash command is user-typed and deterministic, which is
what a lifecycle transition should be, whereas a skill is model-invoked. And the injected
state means each phase begins from what the runner actually reports instead of from what
the conversation remembers.

## Runner-first interaction

```text
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" init                                              # scaffold a target repository
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" doctor                                            # readiness and diagnosis
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" new --title "..." --type feature --mode compact --discovery-reason "..."
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" validate-intent I001
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" advance I001 framed
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" gate approve I001 G1 --approver <person>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" gate approve I001 G2-G3 --approver <person>       # compact Intents only
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" ready-tasks I001
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" start-task I001-P01-T001
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" verify-task I001-P01-T001
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" review-task I001-P01-T001 --review review.yaml --evidence <manifest.json> --implementer <agent-id>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" merge I001-P01-T001 --expected-commit <sha>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" converge I001
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" close I001 --actor <person>
```

Order matters. `verify-task` moves the Task into `review` and captures the evidence
manifest, hashed and pinned to the current commit; `review-task` requires the Task to be in
`review` and refuses a manifest that is missing, altered, or pinned to another commit.
Terminal Task states have their own verbs: `merge`, `revert-task`, `supersede-task`, and
`fail-task`.

Execution is gated. `start-task` and `orchestrate` refuse to run unless the Intent is
`building`, Gate 3 is approved, and — for any phase after `P00` — Gate 4 is approved.

## Agents

Use `aisdlc-verifier` after runner-captured verification and before a Task becomes
verified. Use `aisdlc-red-team` for adversarial criteria, convergence, or an explicitly
requested threat review. Neither agent has editing tools; both are restricted to `Read,
Grep, Glob`. They report a structured decision to the orchestrating context, which
registers it through `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" review-task`. The runner refuses a review whose reviewer
identity matches the implementer.

## Hooks

Claude Code dispatches a fixed set of events: `PreToolUse`, `PostToolUse`, `Stop`,
`SubagentStop`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreCompact`, and
`Notification`. There is no task-creation or task-completion event, so both lifecycle
checks ride on the `Task` tool, which is the surface Claude Code actually delegates
through.

| Event | Matcher | Check |
|---|---|---|
| `PreToolUse` | `Write\|Edit\|NotebookEdit` | Protect approved specifications, generated traceability, runtime state, and evidence |
| `PreToolUse` | `Task` | Require an `[Ixxx-Pxx-Txxx]` identifier and one durable Task artifact before delegating |
| `PreToolUse` | `ExitPlanMode` | Validate the plan before Gate 3 presentation |
| `PostToolUse` | `Task` | Block completion unless the runner recorded `verified` or `merged` |
| `Stop` | — | Detect illegal unfinished runtime states without doing long-running work |

Hooks invoke the runner subcommand `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" hook <event>` rather than a path to a script.
A path breaks as soon as the plugin is installed outside the repository being worked on.
The plugin's `hooks/hooks.json` and the entries `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" init` writes into a repository's
`.claude/settings.json` are now the same string, so there is one mechanism and one version.

### The guards are inert until a repository opts in

The plugin is installed once and then loads in every repository the user opens. Two of the
checks deny by default — a `Task` without a permanent identifier, a plan without one — so
without an opt-in marker a global install would block ordinary work in every unrelated
project on the machine. `.ai-sdlc/config.yaml` is that marker: `init` writes it, nothing
else does, and `evaluate_hook` returns success immediately when it is absent. Inside a
managed repository, `AISDLC_HOOKS=off` suspends the guards for deliberately unmanaged work.

### Avoid wiring the same check twice

If a repository carries `init`-written hook entries *and* the plugin is installed, each
check runs twice. The checks are pure and deny identically, so the effect is a duplicated
message rather than wrong behaviour — but `doctor` reports it as a `warn`. `init --hooks`
controls this: `auto` (the default) writes project entries only when no `aisdlc` plugin is
installed, `project` always writes them, and `none` never does.

### Exec form is deliberately not used

Claude Code supports an exec form, where `args` is an array and the command is spawned with
no shell. It is the better choice when a hook command carries paths. It is the wrong choice
here: a Claude Code that predates the `args` field would spawn a bare `ai-sdlc`, and Typer
exits `2` when given no arguments — which is exactly the code that *blocks* a `PreToolUse`.
A version skew would turn every edit into a hard block. Shell form carries that risk only
if the command string needs quoting, and `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" hook stop` does not.

Hooks receive their payload as JSON on stdin (`cwd`, `tool_name`, `tool_input`, …) and
signal a block with exit code 2 and a reason on stderr, which is fed back to the session.
The repository is taken from the payload's `cwd`, which is why the same installed plugin
guards every repository correctly. Hook failures must be actionable, and hook code is
tested against Windows, macOS, and Linux path forms.

## Built-in Task boundary

Claude Code Tasks are an execution surface, not the durable source of truth. The Task
artifact under the Intent is authoritative; the built-in Task subject includes the
permanent identifier and must never create or renumber identifiers independently.
