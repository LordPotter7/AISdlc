## Why

<!-- The problem this solves. Link the issue, ADR, or Finding. -->

## What changed

## How it was verified

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] Studio (if touched): `npm --prefix desktop run typecheck && npm --prefix desktop test`
- [ ] New behaviour has a test that fails without this change

## Checklist

- [ ] No hand edits to runner-owned files
- [ ] `CHANGELOG.md` updated (user-visible change)
- [ ] ADR added or superseded (if it changes who decides what)
