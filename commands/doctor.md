---
description: Check whether this repository can run the lifecycle, and what is missing
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Glob
---

Runner: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" version`
Readiness: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" doctor`

Explain each non-`ok` check in plain terms: what is broken, what it blocks, and the exact
command that fixes it. A `fail` blocks the lifecycle; a `warn` does not, but say why it
matters. If everything is `ok`, say so in one line and name the next legal action rather
than restating the whole report.

Read the `runner` check first. Nothing of this framework is installed on `PATH` — the
runner is a script inside the plugin — so the check reports the one thing the plugin
still needs from the machine: the `python3` the hook launcher would find.

- `fail` — no working `python3` 3.12+ on `PATH`. Every command and guard needs one.

Read `instrument-boundary` second. It fails when the runner is *inside* the repository
it judges, which happens when someone vendors `scripts/` into their project instead of
installing the plugin. That collapses the separation the whole framework rests on.

The `hooks` check has three distinct outcomes, and they need different answers:

- **`ok — provided by the aisdlc plugin`.** Nothing to do. `init` deliberately writes no
  hook entries; the plugin registers them for every repository.
- **`warn — no .claude/settings.json and no aisdlc plugin`.** The guards will not fire this
  session. This is a *plugin-management* problem, not a repository one — there is nothing
  to edit and no `init` flag that fixes it. Tell the user to install or re-enable the
  plugin (`/plugin install aisdlc@<marketplace>`, then `/reload-plugins`). Say what they
  lose meanwhile: the automatic side stops, but the runner still owns gates, locks, and
  state, so `/aisdlc:*` commands remain fully enforced. They just have to run
  `/aisdlc:status` themselves instead of being told.
- **`warn — wired in both …; every check runs twice`.** The repository carries its own
  entries *and* the plugin supplies them. Remove the project entries from
  `.claude/settings.json`; `init` no longer writes them.

If the `config` check warns about **retired settings**, those are keys an older runner
wrote that this one no longer reads. They are inert, not broken. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" init --force`
rewrites the file without them.

Never repair runtime state by editing files. Use `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" repair-state --confirm`. It preserves the
unreadable state, validates the audit log, and rebuilds; it refuses if the audit log itself is malformed.
