# Legacy command shims

Commands that were removed or renamed. **These are not loaded.** The `commands/` directory
is what the plugin registers; this directory is documentation plus a copy you can take if
you still depend on one.

| Was | Status | Use instead |
| --- | --- | --- |
| `/aisdlc:install` | Removed — the runner is no longer installed | `/plugin install`, then `/aisdlc:init` (ADR-007) |

## If you still need one

Copy the file into your own `.claude/commands/` and edit it to call something that exists.
Nothing here is maintained, and each shim names its replacement rather than silently doing
something different — a shim that quietly works is worse than one that tells you it is
gone.
