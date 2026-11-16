# AI SDLC is installed

Nothing else was installed on your machine. The runner is a script inside this plugin,
run with your existing `python3` — there is no `PATH` entry, no wheel, and no package to
upgrade separately.

**Requirements:** Claude Code, Python 3.12 or newer on `PATH`, and Git.

## Next: opt one repository in

The lifecycle is inert until a repository has a `.ai-sdlc/` directory. Until then the
guard hooks recognise the repository as unmanaged and exit silently, so every repository
you open behaves exactly as it did before.

```
/aisdlc:init      # scaffold this repository
/aisdlc:doctor    # report what is ready and what is missing
```

Then fill in `.ai-sdlc/project-context.md` — its verification commands become Class D
criteria, so a stub left empty weakens every Task that inherits from it. That is the
highest-value five minutes of onboarding.

## Then run one small Intent end to end

```
/aisdlc:start "Add a health endpoint"
```

`/aisdlc:status` at any point reports where things stand and the single next legal action.
If the runner refuses something, the refusal names the missing approval — that is the
design, not a fault.

## If Claude Code says the guards are inactive

The hooks need a POSIX shell and a Python 3.12+. On Windows both usually come with Git
and a normal Python install; the guard says so on stderr rather than failing silently,
because an inactive guard and a satisfied guard otherwise look identical.

## Removing it

`/plugin uninstall` removes the plugin. To also remove the scaffolding from a repository,
run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/uninstall.py" --repo .` — it never touches
`delivery/`, which holds your lifecycle records.
