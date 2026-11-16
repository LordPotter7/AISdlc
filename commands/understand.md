---
description: Build an anchored structural understanding of this project from code or from documents
argument-hint: repo | document <doc.md> [<doc.md> ...]
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" *), Read, Glob, Grep, Write, Task
---

Build a project understanding in **$1** mode.

Current understanding: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" understand status`

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" baseline` pins what an existing system *does*. Nothing pins what it *is* — entry
points, module responsibilities, boundaries, data stores, risk concentrations. In a large
system nobody currently understands, filling in `.ai-sdlc/project-context.md` from a blank
page is the same paralysis the baseline was built to avoid. This command produces a
reviewable draft instead of a blank page.

## Pick the source, and only one

| Mode | Read | Use when |
|---|---|---|
| `repo` | The codebase | Brownfield system with code and little reliable documentation |
| `document` | The listed Markdown files | Knowledge exists as prose — a brief, an architecture note, a legacy spec |

Never both in one run. The two have different epistemic standing: code is the artifact
that actually runs, a document is a description of it. A blended run produces claims whose
grounding cannot be recovered afterwards, and the label on the file stops meaning anything.

To use both, run twice. Give the second run the first run's file, and it writes a
**Divergence** section listing where the written architecture and the real one disagree.
That section is more useful than either analysis alone, and it is the natural input to a
corrective Intent.

## Steps

1. In `repo` mode, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" understand scan` first. It lists, from the files
   git tracks, the languages, manifests, test suites and their commands, CI files, CODEOWNERS
   owners, modules and entry points: facts, not claims, recomputed on every run and never
   stored. Give its output to the agent as the map to explain, so the prose covers every
   module the scan found and says which it could not. Launch the `aisdlc-cartographer` agent
   with the mode and, for `document` mode, the exact list of input files. It is read-only and may read nothing outside the declared
   source. If a prior `.ai-sdlc/project-understanding.md` exists, pass it in and require
   the Divergence section.
2. The agent writes `.ai-sdlc/project-understanding.md` with an inline `[path:lines]`
   anchor on every claim, and leaves `commit`/`recorded_at` out of the frontmatter.
3. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" understand record`. It validates before it pins: every anchor resolves,
   no anchor points outside the declared source, required sections are present and in
   order, a document-mode file carries its unverified-against-code notice, and the prose
   reports observations rather than recommendations. A rejected document is never pinned.
4. **Read the result yourself.** It is advisory, and it is a model's prose about your
   project.
5. Work the `## Proposed checks` section. Copy the lines you accept into your probes file
   and run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" baseline capture`. One line at a time, deliberately — there is no
   promotion command, because a proposed check acquiring enforcing power without anyone
   reading it is exactly what the weakest control tier exists to prevent.

Afterwards, `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lifecycle.py" understand status` reports `fresh`, `drifting`, or `stale` from git,
and Stage 4 context packs carry the understanding with its mode and staleness attached —
dropping it entirely once stale, because stale orientation is worse than none.

## What this is not

- **Not a gate.** There is no `G0` for understanding, and there never will be.
- **Not a convergence check.** An Intent must never fail because a document is stale.
- **Not a requirements source.** Understanding describes what *is*, or what a document
  *says*; requirements state what *should be*. Deriving requirements from an agent's
  reading of current behaviour encodes existing bugs as intent — the exact failure
  `baseline` was designed to avoid. A legacy specification is not an approved requirement
  set, and importing it as one skips Gate 2.
- **Not a replacement for `project-context.md`.** That file is human-authored and stays
  separate, so a reader can always tell which half a person verified and which half is
  derived and may be stale.
