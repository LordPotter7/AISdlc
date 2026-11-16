# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately through
GitHub: **Security → Report a vulnerability** on this repository. You will get an
acknowledgement within 7 days. Fixes are released as a patch version, and the advisory is
published once a fix is available.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | yes       |
| < 0.2   | no        |

## Scope

These are in scope:

- **The runner** (`scripts/`): state, gate, evidence and traceability integrity. Example: any way to move an Intent past a gate, mark a Task verified without captured evidence, or register a review from the implementer's model family.
- **The guard hooks** (`hooks/`): any way for a session to write runner-owned paths, held-out commands, or files under the plugin root.
- **AISDLC Studio** (`desktop/`): the Tauri IPC commands, path validation in the registry, the Python bridge, and markdown rendering.
- **Redaction** (`scripts/aisdlc/redaction.py`): a secret format that reaches evidence or audit logs unredacted.

## Security model, and what it deliberately does not cover

Read this before you use the framework on code you do not trust.

1. **Verification runs code.** `verify-task`, `baseline`, mutation testing, and `/aisdlc:run` execute the verification commands declared in a repository's Task files, through the shell. Treat them exactly like `npm test` or `make`: **running them on an untrusted repository runs that repository's code with your privileges.** The framework protects the *integrity of the lifecycle record*. It is not a sandbox.
2. **The guard fails open when it cannot run.** If no POSIX shell or no Python 3.12+ is found, the hook guard prints a warning on stderr and allows the action. It does not block. This is intentional: a missing interpreter must not brick every Claude Code session on the machine. The runner still refuses illegal transitions, because it is the authority and the guard is only an early warning. Run `/aisdlc:doctor` after installing, and treat a `runner: fail` line as "guards are off".
3. **The guard watches file-editing tools, not the shell.** It inspects `Write`, `Edit`, `NotebookEdit`, `Task`, and `ExitPlanMode`. A `Bash` command that writes `.ai-sdlc/runtime/` directly is not intercepted. The runtime state file carries no integrity digest yet, so a hand-edited state file is not detected. Review shell commands an agent proposes against runner-owned paths, the same way you would review `git push --force`.
4. **The interpreter comes from `PATH`.** The runner is resolved by absolute path under the plugin root (ADR-007), but `python3` itself is the first one found on `PATH`. Anyone who controls your `PATH` controls the runner.
5. **Evidence is not signed.** Manifests are hashed and pinned to a commit, but no signature proves who produced them (ADR-007 removed CI authority). A person with write access to the repository and the plugin directory can forge a record. The framework is built to make agents unable to grade their own work, not to stop a malicious human.
6. **Studio installers are unsigned** in 0.2.x. Windows SmartScreen and macOS Gatekeeper will warn you. Build from source if that matters to you.
7. **Two environment variables change what runs.** `AISDLC_HOOKS=off` (or `0`, `false`,
   `disabled`) turns every guard off for that process. `AISDLC_FRAMEWORK_ROOT` points the
   runner at a different set of schemas, templates and policies. Both exist for tests and
   deliberately unmanaged work; anyone who can set your environment can use them to weaken
   the lifecycle, so `/aisdlc:doctor` warns (`environment-overrides`) whenever either is set.
8. **Verification commands time out after one hour** and are recorded as failed (exit 124),
   so a hanging command cannot hold a Task's lock forever.
