"""Third-party code vendored so the plugin needs no installation step.

Nothing here is ours. Each subpackage keeps its own LICENSE and is recorded in
README.md with the exact version it was taken from. Import these through the
package path — `from .vendor import yaml` — never as a bare top-level import, so
a reader can always tell which code is vendored and `tests/plugin` can enforce it.
"""
