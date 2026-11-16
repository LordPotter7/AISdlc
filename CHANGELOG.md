# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). The plugin and AISDLC Studio share one version
(`tools/bump-version.py`).

## [Unreleased]

### Added
- Public documentation site on GitHub Pages (`docs/index.html` opens the visual guide), linked from the README and the repository's About panel.
- README status badges: CI, CodeQL, OpenSSF Scorecard, Release, docs, platforms, tooling.
- `CITATION.cff`, so GitHub offers a ready-made citation for the project.

### Fixed
- The test suite no longer depends on the developer's machine: an authenticated `gh` session made gate approvals bind to that login, so 89 of 530 tests failed locally while CI passed. `tests/conftest.py` now isolates every test from the ambient GitHub identity.
- Links in the visual guide now point to GitHub, so they work on the published site.

## [0.2.0] — first public release

### Added
- Claude Code plugin: 15 `/aisdlc:*` commands, the `aisdlc` skill, verifier, red-team, and cartographer agents, guard hooks, and the Build-stage workflow.
- A deterministic runner (`scripts/lifecycle.py`) with zero runtime dependencies. It owns identifiers, state, five human gates, resource locks, commit-pinned evidence, reviewer-independence checks, convergence, traceability, and the lessons loop.
- Brownfield onboarding (`understand`, `baseline`), pipeline profiles (ADR-010), portfolio programs, conformance export (NIST AI RMF, ISO/IEC 42001, SSDF).
- AISDLC Studio, a desktop companion (Tauri 2 + React 19), read-mostly over the same records.
- `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue and PR templates, Dependabot.
- CI on Linux, Windows, and macOS with Python 3.12 and 3.13: lint, format, tests, Studio UI, Studio host.
- A differential test that checks the bundled JSON Schema validator against `jsonschema` on every shipped schema.
- Redaction rules for Anthropic keys, GitHub fine-grained PATs, npm tokens, and Stripe live keys.
- Release workflow: a version tag checks that every version file and the changelog agree, builds the Studio installers on Windows with SHA-256 checksums and build-provenance attestations, and drafts the GitHub release.
- CodeQL, OpenSSF Scorecard, `cargo-deny` advisory checks, a Windows Studio host job, `CODEOWNERS`, `SUPPORT.md`, and a pre-commit configuration.
- `doctor` warns (`environment-overrides`) when `AISDLC_HOOKS` or `AISDLC_FRAMEWORK_ROOT` is set.
- Release builds re-run the tests on all three systems before building installers, so a tag on an untested commit cannot ship.
- The README is now a short front page (why, install, quickstart, verification, security model, support). Detail moved to `docs/installation.md`, `docs/onboarding.md`, `docs/verification.md`, and `docs/operations.md`, with stale facts corrected: the `doctor` check count, retired `scale` keys, and pipeline profiles (ADR-010).
- The architecture decision records are back in the tree under `docs/decisions/`.

### Fixed
- **Security:** mutation testing passed a Task's declared paths to the shell unquoted, so a crafted path could run commands. It now runs as an argument list with no shell.
- **Security:** upgraded `dompurify` to 3.4.17 (critical advisory against `<=3.4.16`) and `markdown-it` to 15.0.3.
- `doctor` reported Python 3.11 as `ok` while the guard hooks require 3.12 and stayed inactive. Every check and document now states 3.12.
- **Security:** the guard matched raw path text, so Win32 path aliases (trailing dots or spaces, NTFS stream syntax, 8.3 names, links) could write runner-owned files. Paths are now canonicalised first.
- **Security:** credential rules began with ``, which treats `_` as a word character, so a token glued to an identifier (`MY_TOKEN_npm_…`) reached evidence unmasked. They now use a lookbehind that refuses only letters and digits.
- The bundled schema validator compared `const` and `enum` with Python equality, where `True == 1`, so `{"version": true}` satisfied `const: 1`. It now uses JSON equality. Found by strengthening the differential test, which previously generated almost no valid records (26 of 3,567; now 225 of 3,855, with every schema exercising both verdicts).
- The guard treated any folder named `evidence` as runner-owned, blocking ordinary files (and every file in a repository below such a folder), and resolved relative paths against the hook's working directory. Evidence now matches only under `04-build/`, and relative paths resolve against the repository.
- Tests imported a top-level `yaml` that the lockfile never provided. They now use the bundled parser the runtime ships.

- Verification and integration commands had no timeout, so a hanging command held its Task's lock forever. They now run in their own process group and, after one hour, the whole group is stopped and the command is recorded as failed (exit 124).
- Runner-owned JSON was renamed into place without `fsync`, so a crash could leave an empty state file. Writes are now flushed to disk before the rename, and on POSIX the directory is flushed after it.
- `doctor` and the schema-generation error still pointed at the removed `/aisdlc:install`, and checked for a vendored CI wheel that no longer exists. Both are gone.

### Known limitations
- The guard hooks **fail open** when no POSIX shell or Python 3.12+ is found: they warn on stderr and allow the action. The runner still refuses illegal transitions. See `SECURITY.md`.
- The guard does not inspect `Bash`, and the runtime state file has no integrity digest, so a shell write to `.ai-sdlc/runtime/` is not detected.
- Evidence is hashed and commit-pinned but **not signed** (ADR-007).
- Verification commands run through the shell. Running them on an untrusted repository runs its code.
- Studio installers are unsigned.

[0.2.0]: https://github.com/LordPotter7/AISdlc/releases/tag/v0.2.0
