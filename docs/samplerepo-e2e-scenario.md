# End-to-end scenario: every AI SDLC capability on the DeepTutor SampleRepo

This scenario tests the whole AI SDLC surface against a real brownfield codebase: runner, gates, evidence, evaluations, learning loop, portfolio, hooks, and AISDLC Studio. Bring your own (pass it as `--source`); the run recorded here used DeepTutor 1.6.5 (about 1,800 files, a Python/FastAPI tutoring platform).

There are three tracks:

| Track | What it proves | How |
|---|---|---|
| **A. Automated lifecycle** | Every runner capability, including its refusals | `tools/samplerepo_scenario.py` (about 1 minute) |
| **B. Studio (native app)** | The bridge wire, and every Studio view on that repository | `tools/studio_bridge_smoke.py`, then the manual checklist below |
| **C. Live agents** | Real cartographer, red-team and verifier agents, and hooks in a Claude Code session | Checklist below |

The source repository is **never modified**. Track A copies it to `<target>` and turns the copy into a git repository. Re-running with `--force` resets everything.

## Prerequisites

- Python 3.12 or newer with pytest. DeepTutor's own dependencies are *not* needed: the scenario's code has no dependencies, and its tests run with `--noconftest`.
- Git.
- For Track B: the Studio release build at `desktop/src-tauri/target/release/aisdlc-studio.exe`, or `npm --prefix desktop run tauri dev`.

## Track A — run it

```bash
python tools/samplerepo_scenario.py --force
```

The script prints a ledger line for every capability it proves and writes `.scenario/COVERAGE.md` inside the copy. It also writes gate packets, conformance exports and OpenMetrics output to `.scenario/reports/`.

As in `tools/make_demo_repo.py`, **only the people and agents are scripted**:

- **People:** approvals come from four personas: `nadia.product`, `omar.techlead`, `lena.security` and `ravi.a11y`.
- **Agents:** reviews are recorded on behalf of `aisdlc-red-team` and `aisdlc-verifier`, using a model from a different family (`claude-opus-5-5`) than the implementer (`claude-fable-5-1`).

Everything else is produced by the runner: state, identifiers, refusals, evidence, eval manifests and the audit log. The code is real, and its tests really run.

### The story

| Intent | DeepTutor change | Mode | Left in | Capabilities it exercises |
|---|---|---|---|---|
| — | Adoption | — | — | `init --mode full`, `doctor` (before and after), `migrate` dry run, `understand record/status` (an anchored map of DeepTutor), `baseline capture` (3 probes), `dataset register` |
| **I001** | Login brute-force throttling (`api/routers/auth.py` had no limit) | full · security | **completed** | See [I001 in detail](#i001-in-detail) below |
| **I002** | Dashboard `/recent`: `limit` was unbounded, and the type filter under-filled the page (a real defect in `api/routers/dashboard.py`) | compact · bugfix | **completed** | Combined **G2-G3** gate, `baseline_ref` criterion, `merge-ready` then `merge`, `baseline compare` after the change |
| **I003** | Localized capability status (`i18n/`) plus accessible announcements | full · model behaviour | **specified, G2 reopened** | See [I003 in detail](#i003-in-detail) below |
| **I004** | Restore the missing dependency manifests | compact · infrastructure | **framed, waiting on G1** | Approve it **in Studio** (Track B) |
| **I005** | Per-learner persona scoping (`api/routers/personas.py` always used the admin workspace) | full · multi-tenant | **building, one Task in progress** | See [I005 in detail](#i005-in-detail) below |
| — | Portfolio and assurance | — | — | `program new/add/status`; `export-conformance` for an Intent and for the program (NIST AI RMF, ISO 42001, SSDF); `metrics cost`; `metrics export --openmetrics`; `loop-metrics`; `lesson decay-review`; understanding drift; the refusal of compact mode with model behaviour; **8 hook guards** evaluated in-process |

#### I001 in detail

Artifacts:
- Brief.
- Spec with 4 functional requirements (one of them `won't`) and a measured performance NFR.
- An OpenAPI contract with every operation covered or excluded.
- A STRIDE threat model: 4 threats, with both mitigate and accept dispositions, and every category answered.
- An architecture decision, recorded and accepted as `I001-D001` (in-process throttle state instead of Redis).
- 2 phases, with a walking skeleton and then G4.

Refusals on the record:
- `spec-lint` / `advance specified` refused while a **TBD** remains.
- **G2 refused before the pre-gate review exists.**
- A **same-family reviewer** is refused at `review-task`.

The rest of the lifecycle:
- A **failed first attempt, then a retry**: the key module did not exist yet.
- `validate-evidence`.
- **Class N eval**: 5 attack techniques (header rotation, case, whitespace, full-width homoglyphs) replayed against the real throttle, with `observed_successes = 0`.
- High finding F-001 becomes **LESSON-001**, which is proposed, accepted, and promoted to a standing multi-tenant threat.
- `phase-retrospective --record`, `trace`, `converge`, gate packets G1–G5, G5, and `close`.

#### I003 in detail

- `--contains-model-behavior` plus `eval-design.md`.
- **Accessibility NFR under the ADR-0009 carve-out**: WCAG 2.2 AA, a Class D checker, and a manual-audit owner.
- **Glossary promote** of "capability status".
- **Class J eval**: a calibrated judge from a different family, with dual ordering.
- **Class S eval via `eval execute --adapter command`**: a real placeholder scorer, run 3 times.
- `inherit-suites`, and `dataset decline` escalated.
- Planned, then **G2 reopened** because product added Arabic (RTL). The gate packet shows the **specification diff**.

#### I005 in detail

- Answers the **standing threat inherited** from I001 (`inherited_from: LESSON-001`).
- `lesson inherit`.
- **Contract registry**: a breaking change accepted against I001's completed contract, and **contention** with I003 on `listPersonas`.
- Decision `I005-D001`.
- `intent-resources --declare`.
- Terminal Task states:
  - `merge` then `revert-task --corrective`
  - `supersede-task`
  - `retry_limit 0` → blocked → `fail-task`
- `orchestrate --prepare`: a worktree and a context pack.
- `start-task`, so the Stop hook has something to block.

### Expected result

`COVERAGE.md` should show about 70 checks, with **0 failed or unexpected**. All 8 hook verdicts should match the expected allow/deny.

### Defects this scenario found (all fixed)

| ID | Where | Symptom | Fix and regression test |
|---|---|---|---|
| DEFECT-001 | `scripts/aisdlc/typerlite.py` | `list[str]` options are not repeatable (no `action="append"`), so `--replaced-by`, `--declare`, `--intent` and `--requires` are iterated character by character. `supersede-task --replaced-by`, `program add --requires` (and so `program waive`) and `coapprove-resource` (CLI **and** Studio) fail. No test passes these options. | `typerlite` registers `list[X]` options with `action="append"` and applies the declared default itself, so argparse never mutates it. Tests: `test_p01_cli.py` (toy app plus `coapprove-resource`, `program add --requires`, `intent-resources --declare`) and `test_d1_desktop_bridge.py::test_coapproval_clears_the_contention_the_cockpit_shows`. |
| DEFECT-002 | `scripts/aisdlc/lessons.py:448` | `loop-metrics` raises `KeyError: 'detected_stage'` on a finding that lacks the fields `finding.schema.json` requires. Nothing validates `findings.yaml` earlier; convergence and close accept it. `tools/make_demo_repo.py` writes such findings too. | `validate_intent` checks every finding against `finding.schema.json`, so convergence refuses it and names the finding and field. `LoopMetrics` averages the stage shift over findings that carry both stages. The demo generator writes valid findings. Tests: `test_p06_convergence.py`, `test_p06_lessons.py`. |
| DEFECT-003 | Studio `src-tauri/src/bridge.rs` | A "bridge is not running" banner shows at every launch. The UI's first request races the child spawn (`live` is still `None`). The banner persists after the handshake succeeds. | While the supervisor is `Connecting` with no child yet, a request waits up to `STARTUP_WAIT` (20 s) for the launch instead of failing. After a crash (`Unavailable`), requests still fail fast. Verified by relaunching the release build. |
| DEFECT-004 | Studio | The last-opened repository is re-opened on launch even when it is not AISDLC-managed (the original `SampleRepo`). The Cockpit shows "0 Intents" with no prompt to run `init`. | The Cockpit shows the bridge's `context.problems` (for example "no .ai-sdlc/config.yaml, run `ai-sdlc init`") as an alert under the header. Test: `app.test.tsx` "says so when the open folder is not run by AISDLC". |

### Known gaps (not exercisable)

- **Mutation testing.** `mutation.py` has no CLI entry, and convergence only reads a `mutation` block that already exists.
- **Commands that exist only in the docs.** `federation`, `attest`, `index rebuild` and `verify-task --authority` do not exist in `cli.py`.
- **Inherited-suite convergence** (a triggered suite needing its own passing run) is shown on I005, which does not converge. An Intent flagged `has_side_effect` would need a real `regression/tool-call-injection` run.

## Track B — AISDLC Studio

### B1. Bridge smoke test (automated, over the real stdio wire)

```bash
python tools/studio_bridge_smoke.py
```

This spawns `python -X utf8 -m aisdlc.desktop` exactly as the Tauri shell does. It then:
- requests every projection for all 5 Intents;
- requests 3 gate packets, the event page and diagnostics;
- runs the 5 read-only operations through preview → execute and proves `.ai-sdlc/` and `delivery/` are byte-identical afterwards;
- checks that an unknown Intent, an operation outside the allowlist, and shell injection are all refused.

Expect **38/38**. Adding `--writes` also approves I004 G1, proves the token is single-use, co-approves the `listPersonas` contention and checks that the Cockpit clears it (42/42). **Run that only against a throwaway copy**, so those steps are left for you to do in the UI.

### B2. Native app walkthrough

1. Launch Studio. There should be no "bridge is not running" banner (DEFECT-003).

   ```bash
   desktop/src-tauri/target/release/aisdlc-studio.exe
   ```

   Set `AISDLC_PLUGIN_ROOT=<plugin checkout>` if the app can't find the runner.
2. **Repositories.** Click "Add repository…", pick `<target>`, then click **Open**.
3. **Cockpit.** You should see:
   - 5 Intents, about 94 audit events.
   - **2 gates waiting**: I003 G2 and I004 G1.
   - **Tasks verified 3/7.**
   - **Contention 1** (`contract:deeptutor-api#listPersonas`, I003 ↔ I005).
   - Kanban columns: Frame & specify 2, Build 1, Converge & done 2.
4. **Decision packet for I003 G2.** It prices the reopen: *1 requirement moved, 1 criterion moved*, with `REQ-F-004` added. Spec quality: 0 errors. Accessibility is highlighted, and the runner reports the contention as outstanding.
5. **I001 → Traceability.** Expect:
   - No gaps.
   - "Nothing changed since G2".
   - The won't-requirement greyed out.
   - The N criterion AC-004.
   - Both Tasks verified, with reviewed evidence.
6. **I001 → Specification.** The STRIDE matrix (4 threats, 2 categories not applicable with reasons) and the contract coverage table.
7. **I005 → Specification.** REQ-F-002 sourced from LESSON-001, the accepted breaking change, and the contract table.
8. **I005 → Overview and Activity.** Look for:
   - the reverted, superseded and failed Tasks;
   - the Task in progress;
   - the retry history.
9. **Story Mode on I001.** Replay the chapters: refusal → review → retry → Class N → lesson → close. Try presenter mode.
10. **Doctor.** Expect warnings for `hooks` (the plugin is not installed into the copy) and `runtime-state` (I005-P01-T002 in progress). Everything else should be ok.
11. **Write paths (preview → confirm).**
    - **Approve I004 G1** from its gate packet. The dialog shows the exact `ai-sdlc gate approve …` command. After confirming, the Cockpit drops to 1 waiting.
    - **Stale preview.** Preview again, change state from a terminal, then confirm: the app must refuse with *runner state changed since the preview*. To change state, for example:

      ```bash
      python scripts/lifecycle.py advance I004 specified --repo <target>
      ```
    - **Co-approve** `contract:deeptutor-api#listPersonas`. The Studio UI does not have a button for this yet; it is reachable through the bridge (see B1 `--writes`) or the CLI:

      ```bash
      python scripts/lifecycle.py coapprove-resource contract:deeptutor-api#listPersonas --intent I003 --intent I005 --approver omar.techlead --reason "disjoint fields" --repo <target>
      ```

      Within one poll, the Cockpit's contention should drop to 0.
12. **Live refresh.** With Studio open, run the command below in a terminal. Within about 5 seconds the generation should advance and the activity feed should update.

    ```bash
    python scripts/lifecycle.py gate-packet I003 G2 --repo <target>
    ```

## Track C — live agents and hooks (Claude Code session in the copy)

Install the plugin, open a Claude Code session with `<target>` as the working directory, and run:

1. `/aisdlc:doctor`. Check that `hooks` is now ok.
2. `/aisdlc:status`. The SessionStart briefing should list I003/I004 waiting and I005's ready and in-progress Tasks.
3. **Hooks, live.** Ask Claude to:
   - edit `delivery/intents/I001-*/02-specification/requirements.yaml` → denied (the specification is approved);
   - write `.ai-sdlc/runtime/state.json` → denied;
   - delegate "refactor the persona router" to a subagent → denied (no Task id);
   - end the session → blocked (I005-P01-T002 in progress).
4. `/aisdlc:understand repo`. The real **cartographer** agent re-maps DeepTutor. Compare its Divergence section with the scripted map.
5. `/aisdlc:start "StatusI18n logs template format errors"`, then `/aisdlc:specify`. The real **red-team** agent reviews before G2. Then run `/aisdlc:gate-packet`, `/aisdlc:gate`, `/aisdlc:plan` and `/aisdlc:run`. The real **verifier** agent reviews the evidence. Finish with `/aisdlc:verify` and `/aisdlc:close`.

   This is REQ-F-002 of I003 as a standalone Intent: `i18n/status_i18n.py:48-56` swallows format errors today.
6. `/aisdlc:program PRG001` and `/aisdlc:conformance I001`.
7. Watch Studio throughout: each command in steps 1–6 should appear in the Cockpit and the Activity tab within one poll.

## Reset

```bash
python tools/samplerepo_scenario.py --force
```

This deletes the copy and its runner worktrees (`<parent>/.ai-sdlc-worktrees/<target-name>`), then rebuilds both. Remove the Studio registry entry from the Repositories view if you no longer need it.
