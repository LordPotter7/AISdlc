# Contributing

Thanks for helping. This repository is both the plugin **and** a repository managed by it, so please read the two short sections below before you open a pull request.

## Set up

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Git, and Node 20+ (Studio only). For the Studio host you also need a Rust toolchain; on Windows, run Rust builds from a *Developer PowerShell for VS* so that MSVC's `link.exe` is found.

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run python tools/validate_p00.py
```

Studio:

```bash
npm --prefix desktop ci
npm --prefix desktop run typecheck
npm --prefix desktop test
cargo test --manifest-path desktop/src-tauri/Cargo.toml
```

CI runs all of these on Linux, Windows, and macOS. A pull request needs a green CI run.

## Working in this repository

- **This checkout dogfoods its own hooks.** `.claude/settings.json` runs `hooks/run-hook.cmd` from the checkout. If you also have the `aisdlc` plugin installed, every guard runs twice. That is harmless but slower; disable the plugin while you work here if it bothers you.
- **Use the `/aisdlc:*` commands for lifecycle-managed work**, and `/aisdlc:status` when you are unsure where things stand. Never hand-edit runner-owned files (`.ai-sdlc/runtime/`, evidence manifests, traceability, the audit log).
- **`scripts/` is runtime, `tools/` is maintenance** (ADR-008). Runtime code must never import from `tools/`, and runtime has zero third-party dependencies: anything it needs is the standard library or vendored under `scripts/aisdlc/vendor/`.
- **Design decisions** get the next ADR number (after ADR-010) in `docs/decisions/` and a row in the README's ADR table. Never reuse or renumber an old one: supersede it.
- **Versions** change only through `python tools/bump-version.py X.Y.Z`, which updates the plugin and Studio together. Add a `CHANGELOG.md` entry in the same pull request.

## Finding the tests

Test files are named after the work package that introduced them. The prefixes, roughly:
`p0x` the core runner (state, hooks, evidence, evaluations, gates, lessons, packaging),
`r` release integrity and adoption, `s` specification quality and contracts, `w` hardening
and scale (verification, economics, assurance, codebase, portfolio, pipeline profiles),
`a` authoring, `d`/`l`/`t` Studio's bridge, and `f` decisions. `tests/plugin/` covers the
plugin surface itself. When in doubt, `git grep` the module name under `tests/`.

Install the pre-commit hooks once to catch lint before CI does:

```bash
uv run --with pre-commit pre-commit install
```

## Pull requests

- Keep them small and focused, with a test that fails without the change.
- Write the commit message about *why*, not just *what*.
- Report security problems privately. See [SECURITY.md](SECURITY.md).

By contributing you agree that your contributions are licensed under the Apache License 2.0 (see [LICENSE](LICENSE)), and you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
