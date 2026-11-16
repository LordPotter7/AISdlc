# AISDLC Studio

The Windows desktop companion for the AISDLC runner. One app, three ways to look at the
same records:

| View | Direction | Answers |
|---|---|---|
| **How AISDLC works** | learn | The seven stages and five gates on one line, each Intent on the stage it is working in, which files each stage owns (with their templates), and the vocabulary and lint rules in plain words |
| **Needs you** | decide | Every gate, shared-resource decision, blocked Task, serious finding, blocking lint and suspect link waiting on a person, with the action that clears it (co-approve included) |
| **Intent → What we're building** | understand | One page from the brief, spec, contracts and plan: problem, done means, scope, touches, risks, plan, coverage, and the links that may no longer hold |
| **Cockpit** (home) | A — operate | Which gates wait on a person, what approving each one costs, where the build is stuck |
| **Intent → Specification / Traceability** | B — prove | Is every requirement specified well, and traced to a criterion, a Task, and current evidence? Gaps first |
| **Story Mode** | C — present | Replays a real Intent chapter by chapter from the audit log: what the agent drafted, what the runner refused, what a person approved |

Every artifact name and every `file:line` opens a **viewer** (`artifact.read`), which says who writes the file (you, the runner, or the framework) and, for a specification approved at G2, that it is locked and the exact command that reopens it. Dotted words explain themselves on hover or focus (`.ai-sdlc/framework-guide.yaml`).

**Authoring.** Files you own open with **Edit** when the runner would accept a change. A save previews the exact diff
and the runner's own lint and validation of the new content, then `ai-sdlc artifact write` writes it and records it in
the audit log. **+ Requirement** composes a requirement from an EARS sentence shape and one acceptance criterion; the
runner allocates its ids (never reusing one) and inserts it without disturbing comments. **+ New Intent** runs
`ai-sdlc new` and opens the brief from its template. A locked specification offers **Propose a change…**, which
reopens its gate on the record.

Studio is **never a second authority**. It reads projections the runner computes, follows
changes live while you work in Claude Code, and changes state only by running an
allowlisted runner command after you preview and confirm it — the same command you would
type, subject to every check the runner applies.

## Working in Studio

- **Search or jump to anything** with `Ctrl K`: views, every Intent and its tabs, and actions (start an Intent,
  refresh, theme, spacing). Arrow keys and Enter; it is a combobox a screen reader can follow.
- **Keyboard shortcuts** (`?` lists them): `g n` Needs you, `g c` Cockpit, `g l` How AISDLC works, `g s` Story Mode,
  `g d` Doctor, `Shift N` start an Intent, `Shift R` refresh, `[` show or hide sidebar labels, arrow keys between tabs.
- **Where you are**: breadcrumbs above every view; each Intent in the sidebar carries a seven-step micro-rail (done,
  working, or an amber diamond when a person must decide), with the same fact in words for screen readers.
- **What just happened**: every runner operation echoes as a toast in the runner's words (one polite live region).
- **Files, in a workbench**: an Intent's Files tab lists its files by stage (you author them, or the runner owns them)
  beside an editor. The runner checks what is on screen as you type (`artifact.check`, the same validation and lint
  `artifact write` runs, on a throwaway copy) and draws each problem on its line; problems from other files fold
  under "elsewhere in this Intent". `Ctrl Space` completes identifiers and the keys and values the file's schema
  allows, `Ctrl`+click on an identifier opens where it is written, `Ctrl S` reviews the diff before the runner saves.
  Markdown reads rendered, or side by side while you edit. Unsaved changes keep you on the file until you save or
  discard them. The same editor opens in the drawer from anywhere else in Studio.
- **Portfolio** (`g p`): every Intent highest priority first, with what it waits on (programme order), how much is
  proven, and which open Intents change the same code; programmes as ordered steps; Build against `scale.max_building`.
  Setting a priority and waiving a programme dependency are confirmed runner operations with your name and a reason.
- **Pipeline** (`g f`): the constitution (fixed), then `standard` and every profile side by side, rule by rule,
  marked as stricter or loosened; a loosened rule names who waived it and why. Lists which Intents run under which
  profile and moves one to a stricter profile through the runner. Read only for `pipeline.yaml` itself (ADR-010).
  Each Intent's header names its profile, and a gate that needs several people shows how many have approved.
- **Codebase** (`g b`): what the repository contains, read from the files git tracks at HEAD and never stored
  (`understand scan`): languages, modules with owners and tests, test suites with proposed probes to copy, CI,
  entry points, baselines, and whether the understanding is fresh. It leads with adoption steps until they are done,
  and with modules that more than one open Intent is changing. Each Intent's Overview says where it changes the code.
- **Settings** (gear): dark, light, or match Windows; comfortable or compact spacing. Kept per viewer.
- **Colour has one rule**: amber means a person decides (gates, approvals). Nothing else is amber.
- **Accessibility**: WCAG 2.2 AA colour pairs in both themes, focus kept inside dialogs and returned on close, a skip
  link, keyboard-operable tabs, reduced motion respected.

## Architecture

```text
React (src/)  ──invoke──▶  Tauri core (src-tauri/)  ──stdio JSON──▶  python -m aisdlc.desktop
   views, charts            registry, file watcher,                   (scripts/aisdlc/desktop/ in this repo)
                            bridge supervisor                          projections over the runner
```

- **Bridge**: `scripts/aisdlc/desktop/` — protocol 1.0, wire-compatible with the AI
  Governance desktop app. Read operations are tested to leave the repository
  byte-identical (`tests/test_d1_desktop_bridge.py`).
- **Native layer**: adapted from the AI Governance app (same supervisor, framing,
  registry, watcher). The operation allowlist in `src-tauri/src/frame.rs` is checked
  against the Python `HANDLERS` table at test time (`tests/test_l1_learn.py`).
- **Runner discovery** (`src-tauri/src/runtime.rs`): `AISDLC_PLUGIN_ROOT`, else a checkout
  above the executable, else the installed `aisdlc` plugin from
  `~/.claude/plugins/installed_plugins.json`. Python 3.12+ from `AISDLC_BRIDGE_PYTHON`,
  the plugin `.venv`, or `python`/`python3`/`py -3` on `PATH`.

## Develop

```bash
npm ci
npm run tauri dev        # the desktop app, with a live bridge
npm run dev              # browser preview on http://localhost:1420 (recorded demo)
npm test                 # UI tests over recorded envelopes
npm run typecheck
```

The browser preview replays envelopes recorded from the real bridge over a generated demo
repository. Regenerate them after changing a projection:

```bash
npm run fixtures         # runs ../tools/record_studio_fixtures.py
```

To explore the demo in the desktop app, generate it and add the folder:

```bash
python ../tools/make_demo_repo.py %TEMP%\aisdlc-studio-demo --force
```

## Build for Windows

```bash
npm run tauri build                   # release exe plus NSIS and MSI installers
npm run tauri build -- --no-bundle    # just the exe
```

Output: `src-tauri/target/release/aisdlc-studio.exe` and
`src-tauri/target/release/bundle/{nsis,msi}/`.

## What it deliberately does not do

- Write any file under `.ai-sdlc/` or `delivery/` except through a confirmed runner command. Files you author (brief,
  specification, plan, findings) are saved by `ai-sdlc artifact write`, which refuses runner-owned files, a
  specification locked by G2, a finished Intent, and a file that changed since you opened it.
- Offer `repair-state`, `migrate --apply`, merges, reverts, or anything touching evidence.
- Show history the runner did not record. Story Mode marks every artifact as *verified
  identical to the approved version* (by hash), *changed since approval*, or *current file*.
- Release or deploy anything.
