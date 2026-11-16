# Vendored dependencies

The plugin ships its own copies of the third-party code it needs, because it is
installed with `/plugin install` and never with `pip`. A stock `python3` is the only
prerequisite; there is no build step, so nothing here may be a compiled extension.

| Package | Version | License | Source | Notes |
| --- | --- | --- | --- | --- |
| `yaml` (PyYAML) | 6.0.3 | MIT | PyPI `pyyaml-6.0.3` | Pure-Python modules only. `cyaml.py` and `_yaml.*` (the libyaml C accelerator) are deliberately excluded; `__with_libyaml__` is pinned to `False`. |

## Refreshing

1. Install the new version into a scratch virtual environment.
2. Copy only the pure-Python modules; never a `.so`, `.pyd`, or `.dll`.
3. Re-apply the local patches recorded in the Notes column above.
4. Run `pytest tests/` — `tests/plugin/test_plugin_surface.py` (`test_runtime_is_stdlib_only`, `test_vendored_code_is_pure_python`) fails if a compiled
   artifact or an un-vendored import sneaks in.

## Why not `jsonschema`

`jsonschema` >= 4.18 depends on `rpds-py`, which is a compiled Rust extension and
therefore cannot be vendored as pure Python. The eleven schemas in this framework use
a small, fixed subset of Draft 2020-12 — `type`, `properties`, `required`, `enum`,
`pattern`, `items`, `additionalProperties`, `minimum`/`maximum`, `minLength`,
`minItems`, `uniqueItems`, `const`, `anyOf`, and local `$ref`/`$defs`. That subset is
implemented directly in `scripts/lib/jsonschema_mini.py`, which is ours, not vendored.
