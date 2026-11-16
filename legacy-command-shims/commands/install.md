---
description: "REMOVED — the runner is no longer installed. See legacy-command-shims/README.md."
---

# `/aisdlc:install` no longer exists

Nothing is installed on your machine any more. The runner is a script inside this plugin,
run with your existing `python3`:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" --help
```

**What replaced it:** `/plugin install aisdlc@<marketplace>`, and nothing else. There is no
`PATH` entry to add, no wheel to build, and no package index to reach. See
ADR-007.

**If you are looking for the checks this used to run**, `/aisdlc:doctor` reports whether a
usable `python3` is on `PATH` and whether the guards are wired.
