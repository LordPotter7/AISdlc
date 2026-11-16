# Installation and local development

How to install the plugin, check that it works, develop against a local checkout, and remove it.

> **`ai-sdlc` in examples is shorthand** for `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py"`. Nothing is installed on `PATH`.

## Install


One command. Nothing is installed on your machine, no `PATH` entry is created, and no
package is downloaded from an index.

```text
/plugin marketplace add LordPotter7/AISdlc            # from GitHub
/plugin marketplace add ./AISdlc                  # or a local checkout
/plugin install aisdlc@aisdlc-local
```

**Use the name the add reported, not the one written here.** The marketplace is named
`aisdlc-local` in `marketplace.json`, but that name is only read at *first* registration:
`/plugin marketplace add` matches an existing entry by **source path**, so re-adding a
directory that is already registered keeps whatever key it was first registered under.
`/plugin marketplace list` shows the real one.

That brings the fifteen `/aisdlc:*` commands, the `aisdlc` skill, the `aisdlc-verifier`,
`aisdlc-red-team`, and `aisdlc-cartographer` agents, and the lifecycle guard hooks.

## Requirements

|                        |                                                                                                             |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- |
| **Claude Code**  | with this plugin installed                                                                                  |
| **Python 3.12+** | on `PATH`, as `python3`, `python`, or `py`. The *interpreter* only — never this framework's code |
| **Git**          | for evidence capture, which pins every manifest to a commit                                                 |

The runner is `scripts/lifecycle.py` inside the plugin. Commands and hooks call it by
absolute path under `${CLAUDE_PLUGIN_ROOT}`, so a plugin update replaces it atomically and
there is no second copy to drift. See
[ADR-007](decisions/0007-the-plugin-root-is-the-trust-boundary.md).

Verify it, from anywhere:

```text
/aisdlc:doctor
```

Read the `runner` check first. It reports the `python3` the hook launcher would find — not
the one `doctor` happens to be running under, which may differ.


## If something is wrong

**`Marketplace "<name>" not found`, right after a successful add.** You installed under a
different name than the one that is registered. Read what the add actually reported, or run
`/plugin marketplace list`; the registry is `~/.claude/plugins/known_marketplaces.json`,
keyed by name with the source path inside each entry. Install under the registered name:

```text
/plugin install aisdlc@<the name it actually registered as>
```

Re-adding will not re-key it — the match is on source path, so the add is treated as a
refresh. Only `/plugin marketplace remove <name>` followed by a fresh add changes the key,
and it is rarely worth doing: the key is local to your machine.

**`has an invalid manifest file … agents: Invalid input`.** A component field in
`.claude-plugin/plugin.json` has the wrong shape. `agents` is validated as a path to a
*file*, so `"agents": "./agents"` is rejected and the whole plugin fails. This manifest
declares identity only — `commands/`, `agents/`, `skills/`, and `hooks/hooks.json` are
auto-discovered at their conventional paths.

**`[ai-sdlc] no POSIX shell found` or `no python3 >= 3.12`.** The guards could not run and
say so rather than failing silently. On Windows both usually come with Git and a normal
Python install. The guards **fail open** — they let the tool call through — because
blocking every edit on a machine without them would be worse than an unenforced lifecycle;
but an inactive guard and a satisfied guard must never look the same, which is why it
prints.

## Picking up your own changes

When the marketplace source is a **directory** — `/plugin marketplace add C:\path\to\AISdlc` —
Claude Code reads the plugin from that directory. `${CLAUDE_PLUGIN_ROOT}` resolves to your
checkout, so there is no build, no reinstall, and no copy to keep in sync. Editing the
checkout *is* editing the plugin.

What that means in practice depends on **who reads the file**:

| You changed                                                 | Who reads it                             | To pick it up                                             |
| ----------------------------------------------------------- | ---------------------------------------- | --------------------------------------------------------- |
| `scripts/**/*.py` — the runner                           | A fresh `python3` process, per command | **Nothing.** The next command already runs it       |
| `hooks/guard.py`, `hooks/guard`, `hooks/run-hook.cmd` | A fresh process, per tool call           | **Nothing.** The next Write or Edit already runs it |
| `commands/*.md`                                           | Claude Code, when the plugin loads       | `/reload-plugins`                                       |
| `skills/`, `agents/`, `hooks/hooks.json`              | Claude Code, when the plugin loads       | `/reload-plugins`                                       |
| `.claude-plugin/plugin.json` or `marketplace.json`      | Claude Code, at registration             | `/reload-plugins`; restart if the version does not move |

The split is worth internalising, because it is the difference between a two-second loop
and a confusing one: **Python is always live, markdown needs a reload.** If a command's
behaviour changed but its *text* did not — a different refusal, a new field in the JSON —
you do not need to reload anything.

```text
# edit scripts/aisdlc/…          -> just run the command again
# edit commands/specify.md       -> /reload-plugins, then /aisdlc:specify
```

Verify which copy answered at any time:

```text
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" version
```

The `executable` and `framework_assets` it prints tell you exactly which interpreter and
which asset tree are in play. If `framework_assets` is not your checkout's `.ai-sdlc/`,
you are not running the code you are editing.

### Test before you reload

The runner is plain Python with no install step, so the fast loop does not involve Claude
Code at all:

```text
uv run pytest -q                        # the whole suite
uv run pytest tests/plugin -q           # just the plugin surface: frontmatter, hooks, claims
uv run ruff check .
uv run python tools/bump-version.py     # are the declared versions still in agreement?
```

`tests/plugin/` is the one that catches what a reload would otherwise reveal the slow way —
a command naming a path that does not exist, a hook target that is not reachable, a
document claiming a capability the code no longer has.

### A stale copy may exist, and it is not what runs

Earlier plugin versions were installed by **copying** the directory into
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. A directory-source marketplace
now reads from the source, so that copy is inert — but it is confusing, it can be tens of
megabytes, and `/plugin` may still report its old version number.

Check whether you have one, and what it thinks it is:

```text
ls ~/.claude/plugins/cache/aisdlc-local/aisdlc/
```

If the version there does not match `.claude-plugin/plugin.json`, it is a leftover.
Deleting it is safe — nothing reads it — and removes the possibility of debugging the wrong
copy of your own plugin:

```text
rm -rf ~/.claude/plugins/cache/aisdlc-local
```

Then `/plugin install aisdlc@aisdlc-local` again if `/plugin` stops listing it.

## Installing the plugin does not change repositories you have not opted in to

The guard hooks load in every repository you open, but they are inert until a repository
has `.ai-sdlc/config.yaml`, which only `ai-sdlc init` writes. Repositories that never
adopted the lifecycle behave exactly as they did before the plugin existed:

```text
repository without .ai-sdlc/config.yaml   guard -> exit 0, silent
repository after /aisdlc:init             guard -> exit 2, edit refused
```

Inside a managed repository, `AISDLC_HOOKS=off` suspends the guards for deliberately
unmanaged work.

## Uninstalling

`/plugin uninstall` removes the commands, skills, agents, and hooks. It does not touch your
repositories, which is correct — `delivery/` holds your lifecycle records. To also remove
the scaffolding from one repository:

```text
python3 "${CLAUDE_PLUGIN_ROOT}/tools/uninstall.py" --repo .          # dry run
python3 "${CLAUDE_PLUGIN_ROOT}/tools/uninstall.py" --repo . --apply
```

`delivery/` and `.ai-sdlc/runtime/` are never removed unless you pass `--purge-state`: an
audit log is the one artifact that cannot be reconstructed.
