# Getting help

- **Something is broken:** run `/aisdlc:doctor` first, then open a
  [bug report](https://github.com/rezaS0/AISdlc/issues/new?template=bug_report.yml) with its output.
- **An idea or a gap:** open a [feature request](https://github.com/rezaS0/AISdlc/issues/new?template=feature_request.yml).
- **A question about how to use it:** start with the [README](README.md) and
  [docs/reference.md](docs/reference.md); if they do not answer it, open an issue.
- **A security problem:** never in public. See [SECURITY.md](SECURITY.md).

## Platform support

| Component | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Plugin, runner and guard hooks | yes | yes | yes |
| AISDLC Studio (desktop app) | yes | build from source, untested | build from source, untested |

All need Python 3.12+ and Git. CI runs the runner on all three systems with Python 3.12 and 3.13.
