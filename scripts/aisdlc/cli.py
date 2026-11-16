from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from . import typerlite as typer
from .baseline import BaselineError, BaselineService
from .config import ConfigError, delivery_root, load_config
from .conformance import ConformanceExporter
from .convergence import CloseService, ConvergenceError, ConvergenceService
from .costs import CostLedger
from .decisions import DecisionError, DecisionRegistry, canonical_hash, load_decisions
from .evaluations import DatasetRegistry, EvaluationError, EvaluationRunner
from .evidence import EvidenceRunner, validate_evidence
from .gatepacket import GatePacketService
from .hooks import SUPPORTED_EVENTS, hook_main
from .identifiers import IdentifierError, IdentifierRegistry
from .lessons import LessonError, LessonRegistry, LoopMetrics
from .migrate import MigrationError, MigrationService
from .observability import MetricsExporter
from .orchestration import (
    ContextPackBuilder,
    MergeReadiness,
    OrchestrationError,
    RetryController,
    ReviewService,
    ScriptedOrchestrator,
    WorktreeManager,
)
from .pipeline import PipelineError, intent_types, profile_for, weaker_than
from .pipeline import describe as describe_pipeline
from .pipeline import load as load_pipeline
from .pipeline import pin as pin_profile
from .pipeline import pinned as pinned_profile
from .pregate import PreGateReviewError, PreGateReviewService
from .programs import ProgramError, ProgramRegistry
from .retrospective import PhaseRetrospective, RetrospectiveError
from .scaffold import DoctorService, InitService
from .specdiff import snapshot as snapshot_specification
from .specquality import lint_intent
from .state import INTENT_TRANSITIONS, STAGE_ORDER, StateStore, TransitionError
from .storage import atomic_write_json, atomic_write_text
from .tasks import IntentResourceCoordinator, ResourceConflictError, ResourceLockManager, select_ready_tasks
from .traceability import generate_traceability
from .understanding import UnderstandingError, UnderstandingService
from .validation import validate_intent, validate_intent_record
from .vendor import yaml
from .versioning import manifest_versions, version_consistency

app = typer.Typer(name="ai-sdlc", no_args_is_help=True, help="Deterministic adaptive evidence-driven AI SDLC runner.")
gate_app = typer.Typer(name="gate", no_args_is_help=True, help="Record human gate decisions.")
dataset_app = typer.Typer(name="dataset", no_args_is_help=True, help="Manage versioned evaluation datasets.")
evaluation_app = typer.Typer(name="eval", no_args_is_help=True, help="Run S, J, and N evaluations.")
worktree_app = typer.Typer(name="worktree", no_args_is_help=True, help="Manage isolated Task worktrees.")
lesson_app = typer.Typer(name="lesson", no_args_is_help=True, help="Manage learning-loop lessons.")
glossary_app = typer.Typer(name="glossary", no_args_is_help=True, help="Share domain terms across Intents.")
program_app = typer.Typer(name="program", no_args_is_help=True, help="Group Intents into a larger initiative.")
baseline_app = typer.Typer(
    name="baseline", no_args_is_help=True, help="Pin current behaviour in a brownfield repository."
)
metrics_app = typer.Typer(name="metrics", no_args_is_help=True, help="Export loop, cost, and DORA-proxy metrics.")
decision_app = typer.Typer(
    name="decision", no_args_is_help=True, help="Record architectural decisions Tasks can trace to."
)
artifact_app = typer.Typer(
    name="artifact", no_args_is_help=True, help="Write the files a developer authors, through the runner."
)
understand_app = typer.Typer(
    name="understand",
    no_args_is_help=True,
    help="Record and check an advisory project-understanding document.",
)
app.add_typer(gate_app, name="gate")
app.add_typer(dataset_app, name="dataset")
app.add_typer(evaluation_app, name="eval")
app.add_typer(worktree_app, name="worktree")
app.add_typer(lesson_app, name="lesson")
app.add_typer(program_app, name="program")
app.add_typer(glossary_app, name="glossary")
app.add_typer(baseline_app, name="baseline")
app.add_typer(metrics_app, name="metrics")
app.add_typer(decision_app, name="decision")
app.add_typer(understand_app, name="understand")
app.add_typer(artifact_app, name="artifact")

RepositoryOption = Annotated[Path, typer.Option("--repo", help="Repository root.", resolve_path=True)]


def _emit(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "intent"


def _find_intent(repository_root: Path, intent_id: str) -> Path:
    matches = list(delivery_root(repository_root).glob(f"{intent_id}-*"))
    if len(matches) != 1:
        raise typer.BadParameter(f"expected one directory for {intent_id}, found {len(matches)}")
    return matches[0]


def _find_task(repository_root: Path, task_id: str) -> tuple[Path, dict[str, Any]]:
    matches = list(delivery_root(repository_root).rglob(f"{task_id}/task.yaml"))
    if len(matches) != 1:
        raise typer.BadParameter(f"expected one Task artifact for {task_id}, found {len(matches)}")
    return matches[0], yaml.safe_load(matches[0].read_text(encoding="utf-8"))


def _default_posture(repository_root: Path) -> dict[str, str] | None:
    """The sandbox posture an attempt is assumed to run under when a Task declares none.

    Recorded rather than inferred at read time: a manifest that says which network and
    credential surface the attempt had is what lets the framework later show an agent
    could not have exfiltrated what it never had access to.
    """
    try:
        settings = load_config(repository_root).security
    except Exception:  # noqa: BLE001 - posture is metadata; never block capture on it
        return None
    return {
        "network_policy": settings.default_network_policy,
        "filesystem_scope": settings.default_filesystem_scope,
        "credential_surface": settings.default_credential_surface,
    }


def _require_execution_approved(store: StateStore, task: dict[str, Any]) -> None:
    """Refuse Task execution until the human gates that authorize it are approved.

    Gate 3 approves the task graph; Gate 4 approves the walking skeleton and
    releases every phase after `P00`. Without this check the runner would let an
    agent execute a plan no human ever saw.
    """
    intent_id = str(task.get("intent_id") or str(task["id"]).split("-", 1)[0])
    state = store.read()
    intent_status = state.get("intents", {}).get(intent_id, {}).get("status")
    if intent_status is None:
        raise typer.BadParameter(f"unknown Intent: {intent_id}")
    if intent_status != "building":
        raise typer.BadParameter(
            f"Tasks execute only while {intent_id} is building; current state is {intent_status!r}."
        )
    gates = state.get("gates", {})
    if gates.get(f"{intent_id}:G3", {}).get("status") != "approved":
        raise typer.BadParameter(f"Gate 3 must be approved before executing Tasks in {intent_id}.")
    phase_id = str(task.get("phase_id", ""))
    phase_number = phase_id.rsplit("-P", 1)[-1] if "-P" in phase_id else ""
    is_walking_skeleton = phase_number.strip("0") == ""
    if not is_walking_skeleton and gates.get(f"{intent_id}:G4", {}).get("status") != "approved":
        raise typer.BadParameter(f"Gate 4 must approve the walking skeleton before executing {phase_id or task['id']}.")


@app.command("init")
def init_repository(
    repository_root: RepositoryOption = Path("."),
    mode: Annotated[str, typer.Option(help="Default artifact mode for new Intents.")] = "compact",
    force: Annotated[bool, typer.Option(help="Rewrite repository-owned files that already exist.")] = False,
    hook_command: Annotated[
        str, typer.Option(help="Launcher path used in the generated hook entries.")
    ] = "${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd",
    hooks: Annotated[
        str,
        typer.Option(
            help=(
                "Where guardrails come from. The plugin registers them for every repository, so "
                "'auto' (the default) and 'none' both write nothing. 'project' writes them into "
                "this repository's settings.json as an explicit escape hatch."
            )
        ),
    ] = "auto",
) -> None:
    """Scaffold the repository-owned framework files into a target repository."""
    try:
        report = InitService(repository_root).run(mode=mode, force=force, hook_command=hook_command, hooks=hooks)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(report.as_dict())


@app.command("doctor")
def doctor(repository_root: RepositoryOption = Path(".")) -> None:
    """Report whether this repository can run the lifecycle, and what is missing.

    Always exits 0, including when it reports `fail`. `doctor` is a *report*, and its
    verdict is the `status` field of the JSON it just printed — not its exit code. This
    used to exit 1 on a failing check, which made the command unusable in the one
    situation it exists for: `/aisdlc:doctor` injects this call into its own prompt, and a
    non-zero exit means Claude Code discards the output and reports a shell failure
    instead. A diagnostic that cannot run on a broken repository is not a diagnostic.
    """
    _emit(DoctorService(repository_root).run())


@app.command("version")
def version_command() -> None:
    """Report which runner answered, and where its framework assets resolve from.

    The plugin is installed once and loaded in every repository, so "which `ai-sdlc` is
    this?" is a real question with a wrong answer available: a stale global install, a
    project venv shadowing it, or a checkout whose assets no longer match the runner.
    """
    from . import __version__
    from .assets import AssetError, framework_root

    try:
        assets = str(framework_root())
    except AssetError as error:
        assets = f"unresolved: {error}"
    from .versioning import SCHEMA_VERSION

    _emit(
        {
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "executable": sys.executable,
            "framework_assets": assets,
        }
    )


@decision_app.command("record")
def record_decision(
    intent_id: str,
    source: Annotated[Path, typer.Option("--from", help="YAML file holding the decision.", resolve_path=True)],
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Register an architectural decision and allocate its permanent identifier.

    Architecture used to be a heading in the plan. It is now a record a Task can trace to,
    hashed individually so a change invalidates only the Tasks that declared it. Author the
    substance in a YAML file from `.ai-sdlc/templates/decision-template.yaml`; the runner
    supplies identity, time, and the hash.
    """
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise typer.BadParameter(str(error)) from error
    if not isinstance(payload, dict):
        raise typer.BadParameter("a decision file must contain a mapping")
    payload.setdefault("intent_id", intent_id)
    try:
        record = DecisionRegistry(repository_root).record(_find_intent(repository_root, intent_id), payload)
    except (DecisionError, IdentifierError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"decision": record["id"], "status": record["status"], "content_hash": canonical_hash(record)})


@decision_app.command("accept")
def accept_decision(
    intent_id: str,
    decision_id: str,
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Move a proposed decision to accepted, without touching its substance."""
    try:
        record = DecisionRegistry(repository_root).accept(_find_intent(repository_root, intent_id), decision_id)
    except DecisionError as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"decision": record["id"], "status": record["status"]})


@decision_app.command("list")
def list_decisions(intent_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    """Every decision recorded for an Intent, with its content hash and the Tasks on it."""
    intent_root = _find_intent(repository_root, intent_id)
    tasks = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in intent_root.rglob("task.yaml")]
    try:
        records = load_decisions(intent_root)
    except DecisionError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(
        {
            "intent_id": intent_id,
            "decisions": [
                {
                    "id": record.get("id"),
                    "title": record.get("title"),
                    "status": record.get("status"),
                    "content_hash": canonical_hash(record),
                    "tasks": sorted(
                        task["id"] for task in tasks if record.get("id") in (task.get("source_decisions") or [])
                    ),
                }
                for record in records
            ],
        }
    )


@app.command("pre-gate-review")
def record_pre_gate_review(
    intent_id: str,
    gate: Annotated[str, typer.Argument(help="G2 or G3.")],
    source: Annotated[Path, typer.Option("--from", help="JSON file holding the review.", resolve_path=True)],
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Record a fresh-context adversarial review of a specification or a plan.

    The same instrument the framework already points at Task diffs, aimed one stage
    earlier. A defect found here costs a document rewrite; the same defect found at Task
    review costs the implementation, its evidence, and a slice of the retry budget.

    The review informs the human gate decision; it does not make it. A reviewer may return
    `changes_required` and a person may still approve — the gate packet shows them both.
    """
    intent_root = _find_intent(repository_root, intent_id)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
    if not isinstance(payload, dict):
        raise typer.BadParameter("a review file must contain a JSON object")
    payload.setdefault("intent_id", intent_id)
    payload.setdefault("gate", gate)
    try:
        record = PreGateReviewService(repository_root).record(intent_root, payload)
    except PreGateReviewError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(
        {
            "intent_id": record["intent_id"],
            "gate": record["gate"],
            "decision": record["decision"],
            "findings": len(record.get("findings", [])),
            "artifact_hash": record["artifact_hash"],
        }
    )


@app.command("phase-retrospective")
def phase_retrospective(
    intent_id: str,
    phase_id: Annotated[str, typer.Argument(help="Build Phase identifier, e.g. I001-P01.")],
    repository_root: RepositoryOption = Path("."),
    record: Annotated[
        bool, typer.Option("--record", help="Close the retrospective on the record. Requires a finished phase.")
    ] = False,
    actor: Annotated[str, typer.Option(help="Who held the retrospective.")] = "human",
) -> None:
    """Report what a finished Build Phase taught, before the next phase repeats it.

    Lessons are promoted at close, which on a five-phase Intent means `P01`'s lesson
    arrives after `P05` has already had the chance to repeat it. This runs the same station
    at each phase boundary: it reads findings that already exist and reports which
    Critical or High ones have not yet become a lesson.
    """
    service = PhaseRetrospective(repository_root)
    intent_root = _find_intent(repository_root, intent_id)
    try:
        report = (
            service.record(intent_root, intent_id, phase_id, actor=actor)
            if record
            else service.assess(intent_root, phase_id)
        )
    except RetrospectiveError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(report)


@app.command("migrate")
def migrate_records(
    repository_root: RepositoryOption = Path("."),
    apply: Annotated[bool, typer.Option("--apply", help="Write the migration. Without it, this is a dry run.")] = False,
) -> None:
    """Move this repository's runner-owned records to the current schema generation.

    Dry run by default. Captured evidence is never rewritten — a manifest is hashed and
    may be signed, so stamping it would destroy the property it exists to carry. Older
    manifests are reported as `tolerated_immutable` and read at the generation they were
    written under.
    """
    service = MigrationService(repository_root)
    try:
        _emit(service.apply() if apply else service.plan())
    except MigrationError as error:
        raise typer.BadParameter(str(error)) from error


@app.command("check-versions", hidden=True)
def check_versions(source_root: RepositoryOption = Path(".")) -> None:
    """Verify that pyproject.toml and the two plugin manifests declare one version (R1.1).

    Hidden because it addresses the framework's own checkout rather than a target
    repository: it is a release check, wired into CI and the P00 validator.
    """
    problems = version_consistency(source_root)
    _emit({"versions": manifest_versions(source_root), "problems": problems, "status": "fail" if problems else "ok"})
    if problems:
        raise typer.Exit(code=1)


@app.command("hook", hidden=True)
def hook(event: str) -> None:
    """Evaluate a Claude Code lifecycle guard. Reads the hook payload as JSON on stdin."""
    if event not in SUPPORTED_EVENTS:
        typer.echo(f"unsupported hook event: {event}; expected one of {', '.join(SUPPORTED_EVENTS)}", err=True)
        raise typer.Exit(code=2)
    raise typer.Exit(code=hook_main(event))


@app.command("new")
def new_intent(
    title: Annotated[str, typer.Option("--title", help="Outcome-oriented Intent title.")],
    intent_type: Annotated[
        str,
        typer.Option("--type", help="feature, bugfix, refactor, infrastructure, security, or investigation_or_poc."),
    ],
    repository_root: RepositoryOption = Path("."),
    mode: Annotated[
        str | None, typer.Option(help="Artifact mode: compact or full. Defaults to full where the profile allows it.")
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(help="Pipeline profile (ADR-010). Defaults to the one pipeline.yaml maps this type to."),
    ] = None,
    contains_model_behavior: Annotated[bool, typer.Option(help="Require model-dependent evaluation design.")] = False,
    discovery: Annotated[
        bool,
        typer.Option(
            "--discovery/--no-discovery",
            help="Whether this Intent runs discovery (`understand`, `baseline`) before specifying.",
        ),
    ] = False,
    discovery_reason: Annotated[
        str,
        typer.Option(help="Why discovery is or is not being run. Required either way."),
    ] = "",
) -> None:
    if intent_type not in intent_types(repository_root):
        raise typer.BadParameter(f"invalid Intent type: {intent_type}")
    try:
        profile_name = profile_for(repository_root, intent_type, profile)
        rules = load_pipeline(repository_root)["profiles"][profile_name]
    except PipelineError as error:
        raise typer.BadParameter(str(error)) from error
    if mode is None:
        mode = "full" if "full" in rules["modes"] else rules["modes"][0]
    if mode not in {"compact", "full"}:
        raise typer.BadParameter("mode must be compact or full")
    if mode not in rules["modes"]:
        raise typer.BadParameter(f"the {profile_name!r} profile allows only {', '.join(rules['modes'])} mode")
    if mode == "compact" and contains_model_behavior:
        raise typer.BadParameter("model behavior requires full artifact mode")
    # F6: the reason is required in both directions. Discovery is the one phase the
    # lifecycle lets you skip, and an unexplained skip is indistinguishable from an
    # oversight six months later — which is exactly when someone asks why nobody read the
    # system before specifying against it.
    if not discovery_reason.strip():
        raise typer.BadParameter("--discovery-reason is required. Skipping discovery is a judgment; record it as one.")
    identifier = IdentifierRegistry(repository_root).allocate("intent")
    intent_root = delivery_root(repository_root) / f"{identifier}-{_slug(title)}"
    intent_path = intent_root / "00-intent" / "intent.yaml"
    intent_path.parent.mkdir(parents=True, exist_ok=False)
    payload = {
        "id": identifier,
        "title": title,
        "intent_type": intent_type,
        "status": "initialized",
        "autonomy_mode": "autonomous",
        "contains_model_behavior": contains_model_behavior,
        "artifact_mode": mode,
        "trigger_flags": {},
        "created_at": datetime.now(UTC).isoformat(),
        "discovery": {
            "included": discovery,
            "reason": discovery_reason.strip(),
            "decided_at": datetime.now(UTC).isoformat(),
        },
        # ADR-010: the rules this Intent runs under, pinned so later edits to pipeline.yaml cannot move them.
        "profile": pin_profile(repository_root, intent_root, profile_name),
    }
    intent_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8", newline="\n")
    store = StateStore(repository_root)
    store.initialize_intent(identifier)
    store.record_event(
        "intent",
        identifier,
        "discovery_included" if discovery else "discovery_declined",
        actor="human",
        details={"reason": discovery_reason.strip()},
    )
    store.record_event(
        "intent",
        identifier,
        "profile_pinned",
        actor="runner",
        details={"profile": profile_name, "loosened": [item["rule"] for item in rules["loosened"]]},
    )
    _emit(
        {
            "intent_id": identifier,
            "path": str(intent_root.resolve()),
            "status": "initialized",
            "discovery": payload["discovery"],
            "profile": profile_name,
            "artifact_mode": mode,
        }
    )


@app.command("intent-profile")
def change_intent_profile(
    intent_id: str,
    profile: Annotated[str, typer.Argument(help="A profile at least as strict as the Intent's current one.")],
    actor: Annotated[str, typer.Option(help="Who decided.")],
    reason: Annotated[str, typer.Option(help="Why this Intent needs the stricter rules.")],
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Move an in-flight Intent to a profile that is at least as strict on every rule (ADR-010).

    Loosening an in-flight Intent is not a move: the rules it was specified and reviewed
    under stay pinned. The change applies from the next transition and is on the record.
    """
    if not reason.strip():
        raise typer.BadParameter("a profile change needs a reason")
    intent_root = _find_intent(repository_root, intent_id)
    status = StateStore(repository_root).read().get("intents", {}).get(intent_id, {}).get("status")
    if status in {"completed", "aborted"}:
        raise typer.BadParameter(f"{intent_id} is {status}; its record is closed")
    try:
        current = pinned_profile(intent_root, repository_root)
        target = load_pipeline(repository_root)["profiles"].get(profile)
        if target is None:
            raise PipelineError(f"unknown profile {profile!r}")
    except PipelineError as error:
        raise typer.BadParameter(str(error)) from error
    weaker = weaker_than(current, target)
    if weaker:
        raise typer.BadParameter(f"{profile!r} is weaker than {current['name']!r}: {'; '.join(weaker)}")
    intent_path = intent_root / "00-intent" / "intent.yaml"
    record = yaml.safe_load(intent_path.read_text(encoding="utf-8"))
    if record.get("artifact_mode") not in target["modes"]:
        raise typer.BadParameter(
            f"{profile!r} does not allow {record.get('artifact_mode')} mode, which {intent_id} uses"
        )
    record["profile"] = pin_profile(repository_root, intent_root, profile)
    intent_path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8", newline="\n")
    StateStore(repository_root).record_event(
        "intent",
        intent_id,
        "profile_changed",
        actor=actor,
        details={"from": current["name"], "to": profile, "reason": reason.strip()},
    )
    _emit({"intent_id": intent_id, "profile": profile, "from": current["name"], "actor": actor})


@app.command("pipeline")
def show_pipeline(repository_root: RepositoryOption = Path(".")) -> None:
    """Show the constitution, the `standard` profile, every profile and its waivers (ADR-010)."""
    report = describe_pipeline(repository_root)
    _emit(report)
    if report["error"]:
        raise typer.Exit(1)


@artifact_app.command("write")
def write_artifact_command(
    intent_id: str,
    path: Annotated[str, typer.Argument(help="Path inside the Intent folder, e.g. 02-specification/spec.yaml.")],
    source: Annotated[str, typer.Option("--from", help="File holding the new content, or - for standard input.")],
    expected_sha: Annotated[
        str, typer.Option(help="sha256 of the file as you read it, or `new` when creating it. Refused if it moved.")
    ],
    actor: Annotated[str, typer.Option(help="Who is writing.")] = "developer",
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Write one file a developer authors (brief, specification, plan, findings).

    Refuses runner-written files, a specification approved at G2, a completed or aborted
    Intent, and a file that changed since it was read. The write is audited, and the
    result carries the lint and validation findings the new content produces.
    """
    import sys

    from .authoring import AuthoringError, check_writable, write_artifact
    from .specquality import lint_intent

    relative = path.replace("\\", "/").strip()
    content = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    store = StateStore(repository_root)
    intent_root = _find_intent(repository_root, intent_id)
    try:
        check_writable(store.read(), intent_id, relative)
        result = write_artifact(intent_root, relative, content, expected_sha)
    except AuthoringError as error:
        raise typer.BadParameter(str(error)) from error
    store.record_event(
        "artifact",
        f"{intent_id}:{relative}",
        "artifact_written",
        actor=actor,
        details={"path": relative, "before": result.before, "after": result.after, "created": result.created},
    )
    status = store.read().get("intents", {}).get(intent_id, {}).get("status")
    validation = validate_intent(intent_root, require_plan=status in {"planned", "building", "converging"})
    lint = None
    if (intent_root / "02-specification" / "requirements.yaml").is_file():
        lint = lint_intent(intent_root, _specification_fail_on(repository_root))
    _emit(
        {
            "intent_id": intent_id,
            "path": relative,
            "created": result.created,
            "before": result.before,
            "after": result.after,
            "validation_errors": list(validation.errors),
            "lint": None
            if lint is None
            else {"passed": lint["passed"], "counts": lint["counts"], "findings": lint["findings"]},
        }
    )


@app.command("validate-intent")
def validate_intent_command(
    intent_id: str,
    repository_root: RepositoryOption = Path("."),
    for_stage: Annotated[
        str,
        typer.Option(
            "--for",
            help="Check only what `advance <stage>` needs: framed, specified, planned or building (the default, "
            "the complete check). Before planning, use `--for specified`: the complete check always asks for Tasks.",
        ),
    ] = "building",
) -> None:
    if for_stage not in {"framed", "specified", "planned", "building"}:
        raise typer.BadParameter("--for must be framed, specified, planned, or building")
    intent_root = _find_intent(repository_root, intent_id)
    if for_stage == "framed":
        report = validate_intent_record(intent_root)
    else:
        report = validate_intent(intent_root, require_plan=for_stage in {"planned", "building"})
    _emit({"intent_id": intent_id, "for": for_stage, "valid": report.valid, "errors": report.errors})
    if not report.valid:
        raise typer.Exit(1)


def _specification_fail_on(repository_root: Path) -> str:
    try:
        return load_config(repository_root).specification.fail_on
    except ConfigError as error:
        raise typer.BadParameter(f"configuration is invalid: {error}") from error


@app.command("spec-lint")
def spec_lint(intent_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    """Report requirement-quality findings for an Intent's specification.

    Exits non-zero when a finding reaches `specification.fail_on`, which is the same bar
    `advance <id> specified` applies. Everything below the bar is advice for the author
    and the Gate 2 reviewer.
    """
    report = lint_intent(_find_intent(repository_root, intent_id), _specification_fail_on(repository_root))
    _emit(report)
    if not report["passed"]:
        raise typer.Exit(1)


@app.command("status")
def status(
    intent_id: Annotated[str, typer.Argument(help="Intent to report on. Omit for a repository-wide summary.")] = "",
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Report lifecycle state, gates, ready Tasks, and the next legal action.

    With no Intent, report every Intent the repository holds. `/aisdlc:status` declares
    its argument optional and the documentation promises it works "at any point", so the
    no-argument form has to answer rather than refuse — including in a repository that has
    no Intents yet, which is the first thing a new user runs.
    """
    store = StateStore(repository_root)
    state = store.read()
    if not intent_id:
        _emit(
            {
                "repository": str(repository_root.resolve()),
                "intents": {
                    identifier: record.get("status") for identifier, record in sorted(state["intents"].items())
                },
                "count": len(state["intents"]),
                "next_action": (
                    "register the first Intent with /aisdlc:start"
                    if not state["intents"]
                    else "pass an Intent id for its gates, Tasks, and next legal action"
                ),
            }
        )
        return
    if intent_id not in state["intents"]:
        raise typer.BadParameter(f"unknown Intent: {intent_id}")
    current = state["intents"][intent_id]["status"]
    labels = {
        "framed": "frame intent",
        "specified": "specify requirements",
        "planned": "plan phases and Tasks",
        "building": "begin build",
        "converging": "converge",
        "awaiting_acceptance": "present Gate 5",
        "completed": "close",
        "aborted": "abort",
    }
    target_order = [
        "framed",
        "specified",
        "planned",
        "building",
        "converging",
        "awaiting_acceptance",
        "completed",
        "aborted",
    ]
    actions = [labels[target] for target in target_order if target in INTENT_TRANSITIONS[current]]
    gates = {key.split(":", 1)[1]: value for key, value in state["gates"].items() if key.startswith(f"{intent_id}:")}
    tasks = {key: value for key, value in state["tasks"].items() if key.startswith(f"{intent_id}-")}
    _emit({"intent_id": intent_id, "status": current, "gates": gates, "tasks": tasks, "next_legal_actions": actions})


def _record_refusal(store: StateStore, intent_id: str, target: str, check: str, reasons: list[str]) -> None:
    """Put an artifact-quality refusal on the record.

    A refused transition moves no state, so it used to leave no trace, and the audit
    log could show what was approved but never what the runner stopped. Those refusals
    are the enforcement a reviewer — or a Story Mode replay — most needs to see. Only
    refusals that judge the artifacts are recorded; a typo'd target is not enforcement.
    """
    store.record_event(
        "intent",
        intent_id,
        "advance_refused",
        actor="runner",
        details={"target": target, "check": check, "reasons": reasons[:20], "count": len(reasons)},
    )


def _check_portfolio(repository_root: Path, store: StateStore, state: dict[str, Any], intent_id: str) -> None:
    """Refuse entering `building` ahead of a programme predecessor, or past the build limit.

    Both are ordering decisions a person made in advance: a programme dependency says
    this Intent builds on another's result, and `scale.max_building` says how much may be
    in flight at once. Each refusal is put on the record and names the way through: finish
    the predecessor or waive the dependency with a reason; finish an Intent that is building.
    """
    registry = ProgramRegistry(repository_root)
    blockers = registry.blocking_predecessors(intent_id)
    if blockers:
        statuses = state.get("intents", {})
        programs = registry.read().get("programs", {})

        def program_of(predecessor: str) -> str:
            return next(
                (
                    key
                    for key, program in programs.items()
                    if any(
                        item.get("intent") == intent_id and str(item.get("requires")) == predecessor
                        for item in program.get("dependencies", [])
                    )
                ),
                "<program>",
            )

        reasons = [
            f"{item} has not completed ({statuses.get(item, {}).get('status', 'not started')}); finish it, or waive "
            f"the dependency: `ai-sdlc program waive {program_of(item)} {intent_id} {item} --actor <you> "
            f'--reason "<why>"`'
            for item in blockers
        ]
        _record_refusal(store, intent_id, "building", "program_dependency", reasons)
        raise typer.BadParameter("programme predecessors have not completed: " + "; ".join(reasons))
    limit = load_config(repository_root).scale.max_building
    building = sorted(
        key
        for key, record in state.get("intents", {}).items()
        if record.get("status") == "building" and key != intent_id
    )
    if limit is not None and len(building) >= limit:
        reasons = [
            f"{len(building)} of {limit} allowed Intents are building ({', '.join(building)}); "
            "finish one before starting another, or raise scale.max_building"
        ]
        _record_refusal(store, intent_id, "building", "build_limit", reasons)
        raise typer.BadParameter(reasons[0])


def _check_profile_artifacts(repository_root: Path, store: StateStore, intent_id: str, target: str) -> None:
    """Refuse a stage whose files the Intent's pinned profile requires first (ADR-010)."""
    intent_root = _find_intent(repository_root, intent_id)
    try:
        rules = pinned_profile(intent_root, repository_root)
    except PipelineError as error:
        raise typer.BadParameter(str(error)) from error
    missing = [
        path for path in (rules.get("required_artifacts") or {}).get(target, []) if not (intent_root / path).is_file()
    ]
    if missing:
        reasons = [f"the {rules['name']!r} profile requires {path} before {target}" for path in missing]
        _record_refusal(store, intent_id, target, "profile_artifacts", reasons)
        raise typer.BadParameter("; ".join(reasons))


PRIORITIES = ("high", "normal", "low")


@app.command("intent-priority")
def set_intent_priority(
    intent_id: str,
    priority: Annotated[str, typer.Argument(help="high, normal, or low.")],
    actor: Annotated[str, typer.Option(help="Who decided.")],
    reason: Annotated[str, typer.Option(help="Why this Intent moves up or down.")],
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Record an Intent's priority among the others, with who decided and why.

    Priority lives in the audit log, not in state: the latest `priority_set` event is the
    current priority, so the history of every reprioritisation is kept and `repair-state`
    has nothing to lose. It orders the portfolio; it changes no gate and no transition.
    """
    if priority not in PRIORITIES:
        raise typer.BadParameter(f"priority must be one of {', '.join(PRIORITIES)}")
    if not reason.strip():
        raise typer.BadParameter("a priority change needs a reason")
    store = StateStore(repository_root)
    if intent_id not in store.read().get("intents", {}):
        raise typer.BadParameter(f"unknown Intent {intent_id}")
    store.record_event(
        "intent", intent_id, "priority_set", actor=actor, details={"priority": priority, "reason": reason.strip()}
    )
    _emit({"intent_id": intent_id, "priority": priority, "actor": actor, "reason": reason.strip()})


@app.command("advance")
def advance_intent(
    intent_id: str,
    target: Annotated[str, typer.Argument(help="framed, specified, planned, or building.")],
    actor: Annotated[str, typer.Option(help="Actor requesting the transition.")] = "runner",
    repository_root: RepositoryOption = Path("."),
) -> None:
    prerequisites = {"specified": "G1", "planned": "G2", "building": "G3"}
    if target not in {"framed", "specified", "planned", "building"}:
        raise typer.BadParameter("target must be framed, specified, planned, or building")
    store = StateStore(repository_root)
    state = store.read()
    current = state.get("intents", {}).get(intent_id, {}).get("status")
    if target not in INTENT_TRANSITIONS.get(current, set()):
        raise typer.BadParameter(f"illegal transition {current} -> {target} for {intent_id}")
    # A prerequisite gate guards moving *forward* into a stage. Stepping back is how a
    # reopened gate is answered (building -> planned -> specified after `gate reopen G2`),
    # and requiring the reopened gate to be approved first made that path a dead end.
    retreat = STAGE_ORDER.index(target) < STAGE_ORDER.index(current) if current in STAGE_ORDER else False
    gate = prerequisites.get(target)
    if gate and not retreat and state.get("gates", {}).get(f"{intent_id}:{gate}", {}).get("status") != "approved":
        raise typer.BadParameter(f"{gate} must be approved before entering {target}")
    if not retreat:
        _check_profile_artifacts(repository_root, store, intent_id, target)
    if target == "building" and not retreat:
        _check_portfolio(repository_root, store, state, intent_id)
    if target in {"specified", "planned", "building"}:
        validation = validate_intent(
            _find_intent(repository_root, intent_id), require_plan=target in {"planned", "building"}
        )
        if not validation.valid:
            _record_refusal(store, intent_id, target, "validation", list(validation.errors))
            raise typer.BadParameter("Intent artifacts are invalid: " + "; ".join(validation.errors))
    if target == "specified":
        quality = lint_intent(_find_intent(repository_root, intent_id), _specification_fail_on(repository_root))
        if not quality["passed"]:
            reasons = [f"{item['rule']} {item['location']}: {item['message']}" for item in quality["blocking"]]
            _record_refusal(store, intent_id, target, "specification_quality", reasons)
            raise typer.BadParameter(
                f"specification quality findings at or above `{quality['fail_on']}` block Gate 2 entry: "
                + "; ".join(reasons)
            )
    inherited: dict[str, Any] | None = None
    if target == "specified":
        intent_root = _find_intent(repository_root, intent_id)
        intent = yaml.safe_load((intent_root / "00-intent" / "intent.yaml").read_text(encoding="utf-8"))
        flags = dict(intent.get("trigger_flags", {}))
        flags["contains_model_behavior"] = bool(intent.get("contains_model_behavior"))
        inherited = {
            "standing_suites": DatasetRegistry(repository_root).inherit(flags),
            "lessons": LessonRegistry(repository_root).inherit(
                flags,
                "stage_2_specification",
                current_intent=int(intent_id[1:]),
            ),
        }
        inherited_path = intent_root / "02-specification" / "inherited-controls.yaml"
        inherited_path.write_text(yaml.safe_dump(inherited, sort_keys=False), encoding="utf-8", newline="\n")
    try:
        store.transition_intent(intent_id, target, actor=actor)
    except TransitionError as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"intent_id": intent_id, "status": target, "actor": actor, "inherited_controls": inherited})


@gate_app.command("approve")
def approve_gate(
    intent_id: str,
    gate: str,
    approver: Annotated[str, typer.Option(help="Human approver identity.")],
    repository_root: RepositoryOption = Path("."),
) -> None:
    gates = ["G2", "G3"] if gate == "G2-G3" else [gate]
    if any(item not in {"G1", "G2", "G3", "G4", "G5"} for item in gates):
        raise typer.BadParameter("gate must be G1 through G5, or G2-G3 for a compact Intent")
    if gate == "G2-G3":
        intent = yaml.safe_load(
            (_find_intent(repository_root, intent_id) / "00-intent" / "intent.yaml").read_text(encoding="utf-8")
        )
        if intent.get("artifact_mode") != "compact":
            raise typer.BadParameter("only compact Intents may combine G2 and G3")
    try:
        policies = pinned_profile(_find_intent(repository_root, intent_id), repository_root).get("gates") or {}
    except PipelineError as error:
        raise typer.BadParameter(str(error)) from error
    try:
        store = StateStore(repository_root)
        if gate == "G2-G3":
            first, second = policies.get("G2") or {}, policies.get("G3") or {}
            named = [set(item.get("approvers") or []) for item in (first, second) if item.get("approvers")]
            policy = {
                "approvals": max(int(first.get("approvals") or 1), int(second.get("approvals") or 1)),
                "approvers": sorted(set.intersection(*named)) if named else [],
            }
            outcome = store.approve_compact_gates(intent_id, approver, policy)
        else:
            outcome = store.approve_gate(intent_id, gate, approver, policies.get(gate))
    except TransitionError as error:
        raise typer.BadParameter(str(error)) from error
    if outcome["status"] != "approved":
        _emit(
            {
                "intent_id": intent_id,
                "gates": gates,
                "status": outcome["status"],
                "approver": approver,
                "approvals": outcome["approvals"],
                "needed": outcome["needed"],
            }
        )
        return
    if "G2" in gates:
        # S2.3: the baseline a reopened Gate 2 is diffed against is what was approved.
        revision = store.read().get("gates", {}).get(f"{intent_id}:G2", {}).get("revision")
        snapshot_specification(repository_root, _find_intent(repository_root, intent_id), intent_id, revision)
    _emit(
        {
            "intent_id": intent_id,
            "gates": gates,
            "status": "approved",
            "approver": approver,
            "approvals": outcome["approvals"],
        }
    )


@gate_app.command("reopen")
def reopen_gate(
    intent_id: str,
    gate: str,
    actor: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
    repository_root: RepositoryOption = Path("."),
) -> None:
    try:
        StateStore(repository_root).reopen_gate(intent_id, gate, actor=actor, reason=reason)
    except TransitionError as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"intent_id": intent_id, "gate": gate, "status": "reopened", "actor": actor, "reason": reason})


@app.command("repair-state")
def repair_state(
    repository_root: RepositoryOption = Path("."),
    confirm: Annotated[bool, typer.Option(help="Confirm reconstruction from the append-only audit log.")] = False,
) -> None:
    try:
        state = StateStore(repository_root).recover(confirm=confirm)
    except TransitionError as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"status": "recovered", "state": state})


@app.command("ready-tasks")
def ready_tasks(intent_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    intent_root = _find_intent(repository_root, intent_id)
    tasks = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in intent_root.rglob("task.yaml")]
    selected = select_ready_tasks(tasks, ResourceLockManager(repository_root).read())
    store = StateStore(repository_root)
    blocked_by_gates: dict[str, str] = {}
    for task in selected:
        try:
            _require_execution_approved(store, task)
        except typer.BadParameter as error:
            blocked_by_gates[task["id"]] = str(error.message)
    _emit(
        {
            "intent_id": intent_id,
            "ready_tasks": selected,
            "execution_authorized": not blocked_by_gates,
            "blocked_by_gates": blocked_by_gates,
        }
    )


@app.command("start-task")
def start_task(task_id: str, repository_root: RepositoryOption = Path("."), actor: str = "developer") -> None:
    _, task = _find_task(repository_root, task_id)
    store = StateStore(repository_root)
    _require_execution_approved(store, task)
    state = store.read()
    if task_id not in state["tasks"]:
        store.initialize_task(task_id)
        state = store.read()
    current = state["tasks"][task_id]["status"]
    if current == "pending":
        store.transition_task(task_id, "ready", actor="runner")
    elif current not in {"ready", "retry", "blocked"}:
        raise typer.BadParameter(f"Task cannot start from {current}")
    ResourceLockManager(repository_root).acquire(task_id, task.get("shared_resources", []))
    store.transition_task(task_id, "in_progress", actor=actor)
    _emit({"task_id": task_id, "status": "in_progress", "resources": task.get("shared_resources", [])})


@app.command("verify-task")
def verify_task(
    task_id: str,
    repository_root: RepositoryOption = Path("."),
) -> None:
    task_path, task = _find_task(repository_root, task_id)
    store = StateStore(repository_root)
    state = store.read()
    current = state["tasks"].get(task_id, {}).get("status")
    if current == "in_progress":
        store.transition_task(task_id, "verifying", actor="runner")
    elif current not in {"verifying", "review"}:
        raise typer.BadParameter(f"Task cannot verify from {current}")
    ledger = CostLedger(repository_root)
    with ledger.measure("verify-task", intent_id=str(task.get("intent_id")), entity_id=task_id):
        manifest_path = EvidenceRunner(repository_root).capture(
            task_id,
            task_path.parent,
            task.get("verification_commands", []),
            holdout_commands=task.get("holdout_commands"),
            holdout_authorship=task.get("holdout_authorship"),
            posture=task.get("execution_posture") or _default_posture(repository_root),
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    conformance = manifest.get("conformance") or {}
    if conformance.get("conformance_gap"):
        # A held-out failure under a fully passing visible suite is not a flake to retry
        # blindly. Say what it means, so the next attempt fixes the implementation rather
        # than the test.
        typer.echo(
            f"specification-conformance gap on {task_id}: the visible suite passes and the held-out suite "
            f"does not (visible {conformance.get('visible_pass_rate')}, held-out "
            f"{conformance.get('holdout_pass_rate')}). The implementation satisfies its own tests rather "
            "than the requirement they stand for.",
            err=True,
        )
    if manifest["passed"]:
        if current != "review":
            store.transition_task(task_id, "review", actor="runner")
    else:
        if current == "review":
            store.transition_task(task_id, "retry", actor="runner")
        else:
            store.transition_task(task_id, "retry", actor="runner")
        attempts = store.read()["tasks"][task_id]["attempt"]
        if attempts > task.get("retry_limit", 2):
            store.transition_task(task_id, "blocked", actor="runner")
    _emit(
        {
            "task_id": task_id,
            "status": store.read()["tasks"][task_id]["status"],
            "passed": manifest["passed"],
            "conformance": conformance,
            "manifest": str(manifest_path.resolve()),
        }
    )


@app.command("validate-evidence")
def validate_evidence_command(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False, resolve_path=True)],
    expected_commit: Annotated[str | None, typer.Option()] = None,
) -> None:
    report = validate_evidence(manifest, expected_commit=expected_commit)
    _emit({"valid": report.valid, "errors": report.errors})
    if not report.valid:
        raise typer.Exit(1)


@app.command("trace")
def trace(intent_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    path = generate_traceability(_find_intent(repository_root, intent_id))
    _emit({"intent_id": intent_id, "traceability": str(path.resolve())})


@dataset_app.command("register")
def register_dataset(
    name: str,
    path: Annotated[Path, typer.Argument(exists=True, resolve_path=True)],
    owner: Annotated[str, typer.Option()],
    version: Annotated[str, typer.Option()],
    repository_root: RepositoryOption = Path("."),
    trigger: Annotated[str | None, typer.Option(help="Intent trigger flag required for inheritance.")] = None,
    selection_policy: Annotated[str, typer.Option(help="Predeclared case-selection policy.")] = "all",
) -> None:
    registry = DatasetRegistry(repository_root)
    registry.register(
        name,
        path,
        owner=owner,
        version=version,
        trigger=trigger,
        selection_policy=selection_policy,
    )
    _emit({"dataset": name, **registry.resolve(name)})


@dataset_app.command("decline")
def decline_dataset(
    intent_id: str,
    name: str,
    reason: Annotated[str, typer.Option(help="Written escalation reason.")],
    repository_root: RepositoryOption = Path("."),
) -> None:
    record = DatasetRegistry(repository_root).decline_inherited(intent_id, name, reason)
    _emit(record)


@evaluation_app.command("run")
def run_evaluation(
    spec_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, resolve_path=True)],
    results_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, resolve_path=True)],
    output: Annotated[Path, typer.Option(resolve_path=True)],
    repository_root: RepositoryOption = Path("."),
) -> None:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    results = json.loads(results_path.read_text(encoding="utf-8"))
    manifest = EvaluationRunner(repository_root).evaluate(spec, results, output)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    _emit({"manifest": str(manifest.resolve()), "result": payload["result"]})
    if not payload["result"]["passed"]:
        raise typer.Exit(1)


@app.command("inherit-suites")
def inherit_suites(intent_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    intent_root = _find_intent(repository_root, intent_id)
    intent = yaml.safe_load((intent_root / "00-intent" / "intent.yaml").read_text(encoding="utf-8"))
    flags = dict(intent.get("trigger_flags", {}))
    flags["contains_model_behavior"] = intent.get("contains_model_behavior", False)
    inherited = DatasetRegistry(repository_root).inherit(flags)
    _emit({"intent_id": intent_id, "inherited_suites": inherited})


@worktree_app.command("create")
def create_worktree(
    task_id: str,
    repository_root: RepositoryOption = Path("."),
    base_ref: Annotated[str, typer.Option()] = "HEAD",
) -> None:
    path = WorktreeManager(repository_root).create(task_id, base_ref)
    _emit({"task_id": task_id, "worktree": str(path), "status": "active"})


@worktree_app.command("resume")
def resume_worktree(task_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    path = WorktreeManager(repository_root).resume(task_id)
    _emit({"task_id": task_id, "worktree": str(path), "status": "active"})


@worktree_app.command("retain")
def retain_worktree(task_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    path = WorktreeManager(repository_root).retain(task_id)
    _emit({"task_id": task_id, "worktree": str(path), "status": "retained"})


@worktree_app.command("remove")
def remove_worktree(
    task_id: str,
    repository_root: RepositoryOption = Path("."),
    confirm: Annotated[bool, typer.Option(help="Explicitly confirm destructive worktree removal.")] = False,
) -> None:
    WorktreeManager(repository_root).remove(task_id, confirm=confirm)
    _emit({"task_id": task_id, "status": "removed"})


@app.command("context-pack")
def context_pack(task_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    task_path, _ = _find_task(repository_root, task_id)
    output = ContextPackBuilder(repository_root).build(task_path)
    _emit({"task_id": task_id, "context_pack": str(output.resolve())})


@app.command("review-task")
def review_task(
    task_id: str,
    review_path: Annotated[Path, typer.Option("--review", exists=True, dir_okay=False, resolve_path=True)],
    evidence_path: Annotated[Path, typer.Option("--evidence", exists=True, dir_okay=False, resolve_path=True)],
    implementer_id: Annotated[str, typer.Option("--implementer")],
    repository_root: RepositoryOption = Path("."),
) -> None:
    task_path, _ = _find_task(repository_root, task_id)
    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    current = StateStore(repository_root).read()["tasks"].get(task_id, {}).get("status")
    if current != "review":
        raise typer.BadParameter(
            f"{task_id} must be in review before an independent review is recorded; current state is {current!r}. "
            "Run verify-task first so the runner captures evidence and moves the Task into review."
        )
    try:
        result = ReviewService(repository_root).record(
            task_id, task_path.parent, review, evidence_path, implementer_id=implementer_id
        )
    except (OrchestrationError, TransitionError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(result)


@app.command("retry-task")
def retry_task(
    task_id: str,
    reason: Annotated[str, typer.Option()],
    repository_root: RepositoryOption = Path("."),
    retry_limit: Annotated[int, typer.Option(min=0)] = 2,
) -> None:
    _emit(RetryController(repository_root).failure(task_id, reason=reason, retry_limit=retry_limit))


@app.command("merge-ready")
def merge_ready(
    task_id: str,
    expected_commit: Annotated[str, typer.Option()],
    repository_root: RepositoryOption = Path("."),
) -> None:
    task_path, _ = _find_task(repository_root, task_id)
    report = MergeReadiness(repository_root).assess(task_id, task_path.parent, expected_commit=expected_commit)
    _emit({"task_id": task_id, "ready": report.ready, "errors": report.errors})
    if not report.ready:
        raise typer.Exit(1)


@app.command("merge")
def merge_task(
    task_id: str,
    expected_commit: Annotated[str, typer.Option(help="Verified commit the evidence is pinned to.")],
    repository_root: RepositoryOption = Path("."),
    actor: Annotated[str, typer.Option(help="Actor recording the integration.")] = "runner",
) -> None:
    """Record integration of a verified Task after merge readiness passes."""
    task_path, _ = _find_task(repository_root, task_id)
    report = MergeReadiness(repository_root).assess(task_id, task_path.parent, expected_commit=expected_commit)
    if not report.ready:
        raise typer.BadParameter(f"{task_id} is not merge-ready: " + "; ".join(report.errors))
    store = StateStore(repository_root)
    try:
        store.transition_task(task_id, "merged", actor=actor)
    except TransitionError as error:
        raise typer.BadParameter(str(error)) from error
    released = ResourceLockManager(repository_root).release_all(task_id)
    _emit({"released_resources": released, "task_id": task_id, "status": "merged", "source_commit": expected_commit})


@app.command("revert-task")
def revert_task(
    task_id: str,
    reason: Annotated[str, typer.Option(help="Why the merged Task is being rolled back.")],
    corrective_task_id: Annotated[
        str, typer.Option("--corrective", help="Permanent identifier of the corrective Task that replaces it.")
    ],
    repository_root: RepositoryOption = Path("."),
    actor: Annotated[str, typer.Option()] = "runner",
) -> None:
    """Roll back a merged Task. The original record is preserved, never edited or renumbered."""
    if not reason.strip():
        raise typer.BadParameter("reverting a merged Task requires a written reason")
    _find_task(repository_root, task_id)
    _find_task(repository_root, corrective_task_id)
    store = StateStore(repository_root)
    try:
        store.transition_task(task_id, "reverted", actor=actor, reason=reason, corrective_task=corrective_task_id)
    except TransitionError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(
        {
            "task_id": task_id,
            "status": "reverted",
            "reason": reason,
            "corrective_task": corrective_task_id,
        }
    )


@app.command("supersede-task")
def supersede_task(
    task_id: str,
    reason: Annotated[str, typer.Option(help="Why the Task was replaced before execution.")],
    repository_root: RepositoryOption = Path("."),
    replaced_by: Annotated[
        list[str] | None, typer.Option("--replaced-by", help="Peer Task identifiers that replace it.")
    ] = None,
    actor: Annotated[str, typer.Option()] = "runner",
) -> None:
    """Replace a planned Task before execution begins. Identifiers are never reused."""
    if not reason.strip():
        raise typer.BadParameter("superseding a Task requires a written reason")
    _find_task(repository_root, task_id)
    for peer_id in replaced_by or []:
        _find_task(repository_root, peer_id)
    store = StateStore(repository_root)
    if task_id not in store.read()["tasks"]:
        store.initialize_task(task_id)
    try:
        store.transition_task(task_id, "superseded", actor=actor, reason=reason, replaced_by=list(replaced_by or []))
    except TransitionError as error:
        raise typer.BadParameter(str(error)) from error
    _emit({"task_id": task_id, "status": "superseded", "reason": reason, "replaced_by": list(replaced_by or [])})


@app.command("fail-task")
def fail_task(
    task_id: str,
    reason: Annotated[str, typer.Option(help="Escalation outcome that abandons the Task.")],
    repository_root: RepositoryOption = Path("."),
    actor: Annotated[str, typer.Option(help="Human who resolved the escalation.")] = "user",
) -> None:
    """Abandon a blocked Task after a human resolves the escalation. Attempts stay in the audit trail."""
    if not reason.strip():
        raise typer.BadParameter("failing a Task requires a written escalation outcome")
    _find_task(repository_root, task_id)
    store = StateStore(repository_root)
    try:
        store.transition_task(task_id, "failed", actor=actor, reason=reason)
    except TransitionError as error:
        raise typer.BadParameter(str(error)) from error
    released = ResourceLockManager(repository_root).release_all(task_id)
    _emit({"released_resources": released, "task_id": task_id, "status": "failed", "reason": reason})


@app.command("orchestrate")
def orchestrate(
    intent_id: str,
    repository_root: RepositoryOption = Path("."),
    prepare: Annotated[bool, typer.Option(help="Create worktrees and context packs for the selected batch.")] = False,
) -> None:
    intent_root = _find_intent(repository_root, intent_id)
    tasks = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in sorted(intent_root.rglob("task.yaml"))]
    state = StateStore(repository_root).read()["tasks"]
    for task in tasks:
        if task["id"] in state:
            task["status"] = state[task["id"]]["status"]
    controller = ScriptedOrchestrator(repository_root)
    batch = controller.batch(tasks)
    store = StateStore(repository_root)
    for task in batch:
        _require_execution_approved(store, task)
    prepared = []
    if prepare:
        paths = {
            yaml.safe_load(path.read_text(encoding="utf-8"))["id"]: path for path in intent_root.rglob("task.yaml")
        }
        prepared = [controller.prepare(paths[task["id"]]) for task in batch]
    _emit({"intent_id": intent_id, "ready_batch": [task["id"] for task in batch], "prepared": prepared})


@app.command("converge")
def converge(intent_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    store = StateStore(repository_root)
    current = store.read().get("intents", {}).get(intent_id, {}).get("status")
    try:
        if current == "building":
            store.transition_intent(intent_id, "converging", actor="runner")
        elif current != "converging":
            raise ConvergenceError(f"Intent cannot converge from {current}")
        report = ConvergenceService(repository_root).assess(_find_intent(repository_root, intent_id))
        if report.passed:
            store.transition_intent(intent_id, "awaiting_acceptance", actor="runner")
    except (ConvergenceError, TransitionError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(
        {
            "intent_id": intent_id,
            "passed": report.passed,
            "status": store.read()["intents"][intent_id]["status"],
            "errors": report.errors,
            "report": str(report.report_path.resolve()),
        }
    )
    if not report.passed:
        raise typer.Exit(1)


@app.command("close")
def close_intent(
    intent_id: str,
    actor: Annotated[str, typer.Option(help="Human acceptance owner.")],
    repository_root: RepositoryOption = Path("."),
) -> None:
    try:
        result = CloseService(repository_root).close(_find_intent(repository_root, intent_id), actor=actor)
    except ConvergenceError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(result)


@lesson_app.command("propose")
def propose_lesson(
    lesson_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, resolve_path=True)],
    repository_root: RepositoryOption = Path("."),
) -> None:
    try:
        result = LessonRegistry(repository_root).propose(yaml.safe_load(lesson_path.read_text(encoding="utf-8")))
    except LessonError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(result)


@lesson_app.command("transition")
def transition_lesson(
    lesson_id: str,
    target: str,
    actor: Annotated[str, typer.Option()],
    repository_root: RepositoryOption = Path("."),
) -> None:
    try:
        result = LessonRegistry(repository_root).transition(lesson_id, target, actor=actor)
    except LessonError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(result)


@lesson_app.command("promote")
def promote_lesson(lesson_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    try:
        result = LessonRegistry(repository_root).promote(lesson_id)
    except LessonError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(result)


@lesson_app.command("inherit")
def inherit_lessons(
    intent_id: str,
    fires_at: Annotated[str, typer.Option(help="Lifecycle firing point.")],
    current_intent: Annotated[int, typer.Option(min=0)],
    repository_root: RepositoryOption = Path("."),
) -> None:
    intent_root = _find_intent(repository_root, intent_id)
    intent = yaml.safe_load((intent_root / "00-intent" / "intent.yaml").read_text(encoding="utf-8"))
    flags = dict(intent.get("trigger_flags", {}))
    flags["contains_model_behavior"] = bool(intent.get("contains_model_behavior"))
    inherited = LessonRegistry(repository_root).inherit(flags, fires_at, current_intent=current_intent)
    _emit({"intent_id": intent_id, "fires_at": fires_at, "inherited_lessons": inherited})


@lesson_app.command("decay-review")
def decay_review(
    current_intent: Annotated[int, typer.Option(min=0)],
    runtime_budget_seconds: Annotated[float, typer.Option(min=0)],
    estimated_suite_seconds: Annotated[float, typer.Option(min=0)],
    repository_root: RepositoryOption = Path("."),
) -> None:
    _emit(
        LessonRegistry(repository_root).decay_review(
            current_intent=current_intent,
            runtime_budget_seconds=runtime_budget_seconds,
            estimated_suite_seconds=estimated_suite_seconds,
        )
    )


@app.command("loop-metrics")
def loop_metrics(repository_root: RepositoryOption = Path(".")) -> None:
    result = LoopMetrics.from_repository(repository_root)
    output = repository_root / ".ai-sdlc" / "metrics" / "loop-metrics.json"
    atomic_write_json(output, result)
    _emit({"metrics": result, "path": str(output.resolve())})


@app.command("gate-packet")
def gate_packet(
    intent_id: str,
    gate: Annotated[str, typer.Argument(help="G1 through G5.")],
    repository_root: RepositoryOption = Path("."),
    output: Annotated[Path | None, typer.Option(resolve_path=True, help="Write the rendered packet here.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit the packet as JSON instead of Markdown.")] = False,
) -> None:
    """Assemble everything a human needs to decide one gate.

    Gate count is not what makes this framework expensive; gate preparation is. A packet
    turns a decision that required reading the delivery tree into one that requires
    reading a page.
    """
    intent_root = _find_intent(repository_root, intent_id)
    service = GatePacketService(repository_root)
    ledger = CostLedger(repository_root)
    try:
        with ledger.measure("gate-packet", intent_id=intent_id, entity_id=gate):
            packet = service.build(intent_root, gate)
    except (ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from error
    service.record_presentation(intent_id, gate)
    if json_output:
        _emit(packet)
    else:
        rendered = GatePacketService.render(packet)
        if output is not None:
            atomic_write_text(output, rendered)
            _emit({"intent_id": intent_id, "gate": gate, "packet": str(output.resolve())})
        else:
            typer.echo(rendered)


@app.command("export-conformance")
def export_conformance(
    subject: Annotated[str, typer.Argument(help="Intent identifier (I001) or Program identifier (PRG001).")],
    repository_root: RepositoryOption = Path("."),
    output: Annotated[Path | None, typer.Option(resolve_path=True)] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON instead of Markdown.")] = False,
) -> None:
    """Project runner-owned records onto NIST AI RMF, ISO/IEC 42001, and NIST SSDF.

    Read-only. This adds no lifecycle obligation and cannot change a lifecycle outcome —
    it re-expresses evidence the repository already holds in the vocabulary an assurance
    reviewer works in.
    """
    exporter = ConformanceExporter(repository_root)
    try:
        if subject.startswith("PRG"):
            export = exporter.export_program(subject)
        else:
            export = exporter.export_intent(_find_intent(repository_root, subject))
    except (ProgramError, OSError, KeyError) as error:
        raise typer.BadParameter(str(error)) from error
    if json_output:
        _emit(export)
        return
    rendered = ConformanceExporter.render(export)
    if output is not None:
        atomic_write_text(output, rendered)
        _emit({"subject": subject, "export": str(output.resolve()), "gaps": export["gaps"]})
    else:
        typer.echo(rendered)


@app.command("intent-resources")
def intent_resources(
    intent_id: str,
    repository_root: RepositoryOption = Path("."),
    declare: Annotated[
        list[str] | None, typer.Option("--declare", help="Shared resource this Intent will touch.")
    ] = None,
) -> None:
    """Declare and inspect Intent-level shared-resource contention.

    The Task lock manager stops collisions inside an Intent. This surfaces the collisions
    between Intents, which is what appears once more than one team is running work at
    once. Contention is reported, not silently blocked: the decision to serialize or to
    co-approve belongs to a human at Gate 3, and it gets recorded either way.
    """
    coordinator = IntentResourceCoordinator(repository_root)
    if declare:
        coordinator.declare(intent_id, list(declare))
    state = StateStore(repository_root).read()
    active = {key: value.get("status") for key, value in state.get("intents", {}).items()}
    conflicts = coordinator.contention(intent_id, active)
    _emit(
        {
            "intent_id": intent_id,
            "declared_resources": coordinator.read().get("claims", {}).get(intent_id, []),
            "contention": conflicts,
            "requires_decision": bool(conflicts),
        }
    )


@app.command("contract-registry")
def contract_registry(intent_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    """Report cross-Intent contract contention and breaking changes for one Intent (S3.3)."""
    from .contractregistry import ContractRegistry

    registry = ContractRegistry(repository_root)
    changes = registry.breaking_changes(_find_intent(repository_root, intent_id), intent_id)
    contention = registry.contention(intent_id)
    _emit(
        {
            "intent_id": intent_id,
            "contention": contention,
            "breaking_changes": changes,
            "requires_decision": bool(contention) or any(not item["accepted_by"] for item in changes),
        }
    )


@app.command("coapprove-resource")
def coapprove_resource(
    resource: str,
    intents: Annotated[list[str], typer.Option("--intent", help="Intents permitted to share the resource.")],
    approver: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Record a human decision that two Intents may share one resource."""
    try:
        record = IntentResourceCoordinator(repository_root).coapprove(
            resource, list(intents), approver=approver, reason=reason
        )
    except ResourceConflictError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(record)


@program_app.command("new")
def new_program(
    title: Annotated[str, typer.Option("--title")],
    owner: Annotated[str, typer.Option()],
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Create a Program: an ordered set of Intents, with no gates or evidence of its own."""
    try:
        _emit(ProgramRegistry(repository_root).create(title, owner=owner))
    except ProgramError as error:
        raise typer.BadParameter(str(error)) from error


@program_app.command("add")
def add_program_member(
    program_id: str,
    intent_id: str,
    repository_root: RepositoryOption = Path("."),
    requires: Annotated[list[str] | None, typer.Option("--requires", help="Intent that must close first.")] = None,
) -> None:
    try:
        _emit(ProgramRegistry(repository_root).add_member(program_id, intent_id, requires=list(requires or [])))
    except ProgramError as error:
        raise typer.BadParameter(str(error)) from error


@program_app.command("waive")
def waive_program_dependency(
    program_id: str,
    intent_id: str,
    predecessor: str,
    actor: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Override one programme dependency, on the record."""
    try:
        waived = ProgramRegistry(repository_root).waive(program_id, intent_id, predecessor, reason=reason, actor=actor)
    except ProgramError as error:
        raise typer.BadParameter(str(error)) from error
    # The registry keeps the waiver; the audit log keeps it in the one chronology of decisions.
    StateStore(repository_root).record_event(
        "intent",
        intent_id,
        "dependency_waived",
        actor=actor,
        details={"program": program_id, "predecessor": predecessor, "reason": reason.strip()},
    )
    _emit(waived)


@glossary_app.command("promote")
def promote_glossary_term(
    intent_id: str,
    term: str,
    owner: Annotated[str, typer.Option(help="Person or team accountable for the definition.")],
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Promote a term an Intent defined into the repository glossary (S3.2). Never overwrites."""
    from .glossary import GlossaryError, promote

    try:
        _emit(promote(repository_root.resolve(), _find_intent(repository_root, intent_id), term, owner))
    except GlossaryError as error:
        raise typer.BadParameter(str(error)) from error


@program_app.command("status")
def program_status(program_id: str, repository_root: RepositoryOption = Path(".")) -> None:
    try:
        _emit(ProgramRegistry(repository_root).rollup(program_id))
    except ProgramError as error:
        raise typer.BadParameter(str(error)) from error


@baseline_app.command("capture")
def capture_baseline(
    probes_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, resolve_path=True)],
    repository_root: RepositoryOption = Path("."),
    label: Annotated[str, typer.Option(help="Human label for this snapshot.")] = "",
) -> None:
    """Pin an existing repository's current behaviour as a reference.

    This asserts nothing about correctness. It records what the system does today so the
    first Intents can state honest, enforceable criteria relative to it, instead of
    reverse-engineering a specification that would encode the current bugs as requirements.
    """
    probes = yaml.safe_load(probes_path.read_text(encoding="utf-8")) or {}
    if isinstance(probes, dict):
        probes = probes.get("probes", [])
    ledger = CostLedger(repository_root)
    try:
        with ledger.measure("baseline"):
            manifest = BaselineService(repository_root).capture(list(probes), label=label)
    except BaselineError as error:
        raise typer.BadParameter(str(error)) from error
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    _emit({"baseline_id": payload["id"], "manifest": str(manifest.resolve()), "probes": len(payload["probes"])})


@baseline_app.command("compare")
def compare_baseline(
    baseline_id: str,
    repository_root: RepositoryOption = Path("."),
) -> None:
    """Re-run a baseline's probes and report regressions against it."""
    try:
        report = BaselineService(repository_root).compare(baseline_id)
    except BaselineError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(report)
    if not report["passed"]:
        raise typer.Exit(1)


@understand_app.command("record")
def record_understanding(
    repository_root: RepositoryOption = Path("."),
    document: Annotated[
        Path | None,
        typer.Option("--file", help="Understanding document. Defaults to .ai-sdlc/project-understanding.md."),
    ] = None,
) -> None:
    """Validate an understanding document and stamp its pin into its own frontmatter.

    This asserts nothing about correctness, and it is not a gate. It performs the checks
    a Markdown file can actually be held to: every inline `[path:lines]` anchor resolves,
    no anchor points outside the declared source, the required sections are present and
    in order, a document-mode file carries its unverified-against-code notice, and the
    prose reports what *is* rather than what *ought to be*.

    Anchor validation does not catch every hallucination. It catches the cheapest and
    most common kind — confident prose about something that is not there — which is what
    turns an unfalsifiable document into a falsifiable one.
    """
    service = UnderstandingService(repository_root, document_path=document)
    try:
        report = service.record()
    except UnderstandingError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(report)


@understand_app.command("status")
def understanding_status(
    repository_root: RepositoryOption = Path("."),
    document: Annotated[
        Path | None,
        typer.Option("--file", help="Understanding document. Defaults to .ai-sdlc/project-understanding.md."),
    ] = None,
) -> None:
    """Report `fresh`, `drifting`, or `stale`, and which claims moved.

    Staleness comes from git — `git diff` between the pinned commit and HEAD over the
    anchored paths — rather than from a stored hash table, because git already is one.
    Reported, never enforced: an Intent must never fail because a document is stale.
    """
    config = load_config(repository_root).understanding
    service = UnderstandingService(repository_root, document_path=document)
    try:
        report = service.status(drift_threshold=config.drift_threshold)
    except UnderstandingError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(report)


@understand_app.command("scan")
def scan_repository(repository_root: RepositoryOption = Path(".")) -> None:
    """Inventory what the repository contains, from the files git tracks at HEAD.

    Languages, manifests, test suites and the commands that would run them, CI files,
    CODEOWNERS owners, modules, and entry points. Facts, not claims: the scan runs nothing
    and writes nothing, and the same commit always gives the same answer, so there is no
    stored inventory to drift. Detected test commands are *proposed* probes; copy the ones
    you accept into a probes file and run `baseline capture`.
    """
    from .inventory import InventoryError, scan

    try:
        _emit(scan(repository_root))
    except InventoryError as error:
        raise typer.BadParameter(str(error)) from error


@metrics_app.command("export")
def export_metrics(
    repository_root: RepositoryOption = Path("."),
    output: Annotated[Path | None, typer.Option(resolve_path=True)] = None,
    openmetrics: Annotated[bool, typer.Option(help="Emit OpenMetrics text instead of JSON.")] = False,
) -> None:
    """Export loop metrics, cost metrics, and DORA proxies for an external dashboard."""
    exporter = MetricsExporter(repository_root)
    payload = exporter.collect()
    if openmetrics:
        rendered = exporter.to_openmetrics(payload)
        if output is not None:
            atomic_write_text(output, rendered)
            _emit({"metrics": str(output.resolve()), "format": "openmetrics"})
        else:
            typer.echo(rendered)
        return
    if output is not None:
        atomic_write_json(output, payload)
        _emit({"metrics": str(output.resolve()), "format": "json"})
    else:
        _emit(payload)


@metrics_app.command("cost")
def cost_report(
    repository_root: RepositoryOption = Path("."),
    intent_id: Annotated[str | None, typer.Option("--intent")] = None,
) -> None:
    """Report the verification tax and where the time went."""
    _emit(CostLedger(repository_root).summary(intent_id=intent_id))


@evaluation_app.command("execute")
def execute_evaluation(
    spec_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, resolve_path=True)],
    output: Annotated[Path, typer.Option(resolve_path=True)],
    repository_root: RepositoryOption = Path("."),
    adapter: Annotated[str | None, typer.Option(help="Adapter name: anthropic, command, or a registered one.")] = None,
) -> None:
    """Produce per-case results *and* adjudicate them, without an untrusted caller.

    `eval run` adjudicates results handed to it, which means an agent can produce those
    results. This drives the adapter from the runner and writes what comes back directly
    into evidence, closing that gap.
    """
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    ledger = CostLedger(repository_root)
    try:
        with ledger.measure("eval-run", entity_id=str(spec.get("id"))):
            manifest = EvaluationRunner(repository_root).execute(spec, output, adapter_name=adapter)
    except EvaluationError as error:
        raise typer.BadParameter(str(error)) from error
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    _emit({"manifest": str(manifest.resolve()), "result": payload["result"], "sampling": payload.get("sampling")})
    if not payload["result"]["passed"]:
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
