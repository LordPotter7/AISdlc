"""Allowlisted runner operations behind preview → confirm.

The app never changes lifecycle state itself. An operation here is exactly a runner CLI
invocation, chosen from a fixed allowlist, with arguments validated against strict
patterns and passed as an argument vector (never through a shell). Write operations need
a preview first: the preview returns the exact command, the runtime files it may change,
their current hashes, and a single-use token. Execute is refused when the token is
unknown or expired, the arguments differ, or any of those files moved in between —
someone else acted, so the person must look again before deciding.

The runner still enforces everything it always does (pre-gate reviews, approver policy,
identity strength). The app adds a confirmation step; it never removes a check.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aisdlc.desktop.protocol import BridgeError, Omission, sanitize_text, sanitize_value
from aisdlc.desktop.store import ProjectStore
from aisdlc.pipeline import intent_types

INTENT = re.compile(r"^I\d{3,}$")
GATE = re.compile(r"^G[1-5]$|^G2-G3$")
NAME = re.compile(r"^[\w .@'-]{1,80}$")
TERM = re.compile(r"^[\w .'/-]{1,80}$")
RESOURCE = re.compile(r"^[\w:#./@{} -]{1,160}$")
#: A path inside an Intent folder: no parent steps, no drive, no leading slash.
ARTIFACT_PATH = re.compile(r"^(?!.*\.\.)\w[\w./-]{0,199}$")
SHA = re.compile(r"^([0-9a-f]{64}|new)$")
TITLE = re.compile(r"^[^\r\n]{3,120}$")
PROGRAM = re.compile(r"^PRG\d{3,}$")
PROFILE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
PRIORITIES = ("high", "normal", "low")
INTENT_TYPES = intent_types()  # one list, declared in intent.schema.json
MAX_CONTENT = 256 * 1024
REASON_MAX = 500
TOKEN_TTL_SECONDS = 600
RUNTIME_FILES = (".ai-sdlc/runtime/state.json", ".ai-sdlc/runtime/audit.jsonl")


@dataclass(frozen=True)
class Spec:
    title: str
    writes: bool
    fields: tuple[tuple[str, re.Pattern[str] | None], ...]
    effect: str


OPERATIONS: dict[str, Spec] = {
    "validate_intent": Spec(
        "Validate Intent",
        False,
        (("intent_id", INTENT),),
        "Checks schemas, references, and completeness. Writes nothing.",
    ),
    "spec_lint": Spec(
        "Lint specification", False, (("intent_id", INTENT),), "Reports requirement-quality findings. Writes nothing."
    ),
    "contract_registry": Spec(
        "Check contracts across Intents",
        False,
        (("intent_id", INTENT),),
        "Reports contention and breaking changes. Writes nothing.",
    ),
    "doctor": Spec("Run doctor", False, (), "Checks the runner and repository setup. Writes nothing."),
    "loop_metrics": Spec("Loop metrics", False, (), "Reports learning-loop metrics. Writes nothing."),
    "gate_approve": Spec(
        "Approve gate",
        True,
        (("intent_id", INTENT), ("gate", GATE), ("approver", NAME)),
        "Records a human gate approval in runner state and the audit log.",
    ),
    "gate_reopen": Spec(
        "Reopen gate",
        True,
        (("intent_id", INTENT), ("gate", GATE), ("actor", NAME), ("reason", None)),
        "Reopens an approved gate; evidence pinned to it may go stale.",
    ),
    "glossary_promote": Spec(
        "Promote glossary term",
        True,
        (("intent_id", INTENT), ("term", TERM), ("owner", NAME)),
        "Adds the Intent's term to .ai-sdlc/glossary.yaml. Never overwrites.",
    ),
    "coapprove_resource": Spec(
        "Co-approve shared resource",
        True,
        (("resource", RESOURCE), ("intents", None), ("approver", NAME), ("reason", None)),
        "Records that two Intents may change one resource concurrently.",
    ),
    "artifact_write": Spec(
        "Save a file you author",
        True,
        (("intent_id", INTENT), ("path", ARTIFACT_PATH), ("expected_sha", SHA), ("content", None)),
        "The runner writes this one file and records it in the audit log. It refuses a file a "
        "gate locks, a file the runner owns, and a file that changed since you opened it.",
    ),
    "intent_priority": Spec(
        "Set an Intent's priority",
        True,
        (("intent_id", INTENT), ("priority", None), ("actor", NAME), ("reason", None)),
        "Records the priority with your name and reason in the audit log. It orders the "
        "portfolio; it changes no gate and no transition.",
    ),
    "program_waive": Spec(
        "Waive a programme dependency",
        True,
        (("program", PROGRAM), ("intent_id", INTENT), ("predecessor", INTENT), ("actor", NAME), ("reason", None)),
        "Lets this Intent enter Build before its predecessor completes. The waiver is kept in "
        "the programme registry and the audit log with your name and reason.",
    ),
    "intent_profile": Spec(
        "Move an Intent to a stricter profile",
        True,
        (("intent_id", INTENT), ("profile", PROFILE), ("actor", NAME), ("reason", None)),
        "Pins a profile at least as strict on every rule; the runner refuses a weaker one. "
        "Recorded with your name and reason; applies from the next transition.",
    ),
    "intent_new": Spec(
        "Start an Intent",
        True,
        (
            ("title", TITLE),
            ("intent_type", None),
            ("mode", None),
            ("discovery_reason", None),
            ("contains_model_behavior", None),
            ("profile", None),
        ),
        "The runner allocates the next Intent id and creates its folder from the templates.",
    ),
}


def _argv(name: str, args: dict[str, Any]) -> list[str]:
    if name == "validate_intent":
        return ["validate-intent", args["intent_id"]]
    if name == "spec_lint":
        return ["spec-lint", args["intent_id"]]
    if name == "contract_registry":
        return ["contract-registry", args["intent_id"]]
    if name == "doctor":
        return ["doctor"]
    if name == "loop_metrics":
        return ["loop-metrics"]
    if name == "gate_approve":
        return ["gate", "approve", args["intent_id"], args["gate"], "--approver", args["approver"]]
    if name == "gate_reopen":
        return ["gate", "reopen", args["intent_id"], args["gate"], "--actor", args["actor"], "--reason", args["reason"]]
    if name == "glossary_promote":
        return ["glossary", "promote", args["intent_id"], args["term"], "--owner", args["owner"]]
    if name == "artifact_write":
        return [
            "artifact",
            "write",
            args["intent_id"],
            args["path"],
            "--from",
            "-",
            "--expected-sha",
            args["expected_sha"],
            "--actor",
            "studio",
        ]
    if name == "intent_new":
        argv = [
            "new",
            "--title",
            args["title"],
            "--type",
            args["intent_type"],
            "--mode",
            args["mode"],
            "--discovery-reason",
            args["discovery_reason"],
        ]
        argv += ["--profile", args["profile"]] if args.get("profile") else []
        return argv + (["--contains-model-behavior"] if args["contains_model_behavior"] else [])
    if name == "intent_profile":
        return [
            "intent-profile",
            args["intent_id"],
            args["profile"],
            "--actor",
            args["actor"],
            "--reason",
            args["reason"],
        ]
    if name == "intent_priority":
        return [
            "intent-priority",
            args["intent_id"],
            args["priority"],
            "--actor",
            args["actor"],
            "--reason",
            args["reason"],
        ]
    if name == "program_waive":
        return [
            "program",
            "waive",
            args["program"],
            args["intent_id"],
            args["predecessor"],
            "--actor",
            args["actor"],
            "--reason",
            args["reason"],
        ]
    if name == "coapprove_resource":
        argv = ["coapprove-resource", args["resource"]]
        for intent_id in args["intents"]:
            argv += ["--intent", intent_id]
        return argv + ["--approver", args["approver"], "--reason", args["reason"]]
    raise BridgeError("unknown_operation", f"operation {name!r} is not allowlisted")


def _validated(name: str, raw: Any) -> dict[str, Any]:
    spec = OPERATIONS.get(name)
    if spec is None:
        raise BridgeError(
            "unknown_operation", f"operation {name!r} is not allowlisted", detail={"allowed": sorted(OPERATIONS)}
        )
    raw = raw if isinstance(raw, dict) else {}
    args: dict[str, Any] = {}
    for field_name, pattern in spec.fields:
        value = raw.get(field_name)
        if field_name == "content":
            if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_CONTENT:
                raise BridgeError("invalid_params", f"content must be text of at most {MAX_CONTENT} bytes")
            args[field_name] = value  # verbatim: newlines and trailing whitespace are content
            continue
        if field_name == "contains_model_behavior":
            args[field_name] = value is True
            continue
        if field_name == "profile" and name == "intent_new":
            if value in (None, ""):
                continue  # optional: the type's profile applies
            if not isinstance(value, str) or not PROFILE.match(value):
                raise BridgeError("invalid_params", "profile has unexpected characters", detail={"field": field_name})
            args[field_name] = value
            continue
        if field_name in {"intent_type", "mode", "priority"}:
            allowed = {"intent_type": INTENT_TYPES, "mode": ("compact", "full"), "priority": PRIORITIES}[field_name]
            if value not in allowed:
                raise BridgeError("invalid_params", f"{field_name} must be one of {', '.join(allowed)}")
            args[field_name] = value
            continue
        if field_name == "intents":
            if (
                not isinstance(value, list)
                or len(value) < 2
                or not all(isinstance(item, str) and INTENT.match(item) for item in value)
            ):
                raise BridgeError("invalid_params", "intents must list at least two Intent ids")
            args[field_name] = list(value)[:10]
            continue
        if not isinstance(value, str) or not value.strip():
            raise BridgeError("invalid_params", f"{field_name} is required", detail={"field": field_name})
        value = value.strip()
        if field_name in {"reason", "discovery_reason"}:
            if len(value) > REASON_MAX or "\n" in value:
                raise BridgeError("invalid_params", "reason must be one line of at most 500 characters")
        elif pattern is not None and not pattern.match(value):
            raise BridgeError("invalid_params", f"{field_name} has unexpected characters", detail={"field": field_name})
        args[field_name] = value
    return args


def _hashes(root: Path) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for relative in RUNTIME_FILES + (
        ".ai-sdlc/glossary.yaml",
        ".ai-sdlc/runtime/intent-resources.json",
        ".ai-sdlc/programs/registry.yaml",
    ):
        path = root / relative
        result[relative] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return result


_TOKENS: dict[str, dict[str, Any]] = {}


def _lifecycle_script() -> Path:
    return Path(__file__).resolve().parents[2] / "lifecycle.py"


def _base(store: ProjectStore, name: str, args: dict[str, Any]) -> dict[str, str | None]:
    base = _hashes(store.root)
    if name == "artifact_write":
        from aisdlc.desktop.projections import _intent_root

        target = _intent_root(store, args["intent_id"]) / args["path"]
        base[f"file:{args['path']}"] = (
            "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        )
    return base


def _draft(store: ProjectStore, args: dict[str, Any]) -> dict[str, Any]:
    """What saving would change and what the runner would say about it, without saving.

    The diff is against the file on disk. The checks run the runner's own lint and
    validation over a temporary copy of the Intent holding the proposed file, so the
    preview reports exactly what `artifact write` will, and the repository is untouched.
    """
    import difflib

    from aisdlc.authoring import AuthoringError, check_writable, parse_check
    from aisdlc.desktop.drafts import draft_copy, run_checks
    from aisdlc.desktop.projections import _intent_root
    from aisdlc.state import StateStore

    intent_root = _intent_root(store, args["intent_id"])
    relative, content = args["path"], args["content"]
    target = intent_root / relative
    current = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    state = StateStore(store.root).read()
    refusal = None
    try:
        check_writable(state, args["intent_id"], relative)
        parse_check(relative, content)
    except AuthoringError as error:
        refusal = str(error)
    actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else "new"
    if refusal is None and actual != args["expected_sha"]:
        refusal = f"{relative} changed since you opened it; reload it and apply your change again"
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
    checks = None
    if refusal is None:
        status = state.get("intents", {}).get(args["intent_id"], {}).get("status")
        with draft_copy(store, intent_root, relative, content) as copy:
            checks = run_checks(store, copy, status)
    return {
        "diff": sanitize_text(diff, limit=60_000) or "(no change)",
        "changed": content != current,
        "refusal": refusal,
        "checks": sanitize_value(checks, limit=20_000),
    }


def preview(store: ProjectStore, params: dict[str, Any]) -> tuple[dict[str, Any], list[Omission]]:
    name = str(params.get("name") or "")
    args = _validated(name, params.get("args"))
    spec = OPERATIONS[name]
    argv = _argv(name, args)
    token = secrets.token_urlsafe(18)
    base = _base(store, name, args)
    _TOKENS[token] = {
        "project_id": store.context.project_id,
        "name": name,
        "args": args,
        "base": base,
        "expires": time.monotonic() + TOKEN_TTL_SECONDS,
    }
    payload = {
        "name": name,
        "title": spec.title,
        "writes": spec.writes,
        "effect": spec.effect,
        "command": "ai-sdlc "
        + " ".join(_quote(part) for part in argv)
        + (" < (the new content)" if name == "artifact_write" else ""),
        "may_change": [path for path in base if spec.writes],
        "base_hashes": base,
        "token": token,
        "expires_in_seconds": TOKEN_TTL_SECONDS,
    }
    if name == "artifact_write":
        payload["draft"] = _draft(store, args)
    return payload, []


def _quote(part: str) -> str:
    return f'"{part}"' if (" " in part or not part) else part


def execute(store: ProjectStore, params: dict[str, Any]) -> tuple[dict[str, Any], list[Omission]]:
    name = str(params.get("name") or "")
    args = _validated(name, params.get("args"))
    token = str(params.get("token") or "")
    grant = _TOKENS.pop(token, None)
    if grant is None or grant["expires"] < time.monotonic():
        raise BridgeError("token_invalid", "preview this operation again; the confirmation expired or was used")
    if grant["project_id"] != store.context.project_id or grant["name"] != name or grant["args"] != args:
        raise BridgeError("token_mismatch", "the confirmed operation differs from the one previewed")
    if params.get("base_hashes") != grant["base"] or _base(store, name, args) != grant["base"]:
        raise BridgeError(
            "base_moved", "runner state changed since the preview; review the new state and retry", retryable=True
        )
    argv = [sys.executable, "-X", "utf8", str(_lifecycle_script()), *_argv(name, args), "--repo", store.context.root]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            cwd=store.context.root,
            encoding="utf-8",
            errors="replace",
            input=args["content"] if name == "artifact_write" else None,
        )
    except subprocess.TimeoutExpired:
        raise BridgeError("timeout", "the runner did not finish within 5 minutes", retryable=True) from None
    output: Any = completed.stdout.strip()
    try:
        output = json.loads(output) if output else None
    except ValueError:
        output = sanitize_text(output, limit=20_000)
    store.reconcile()
    return {
        "name": name,
        "exit_code": completed.returncode,
        "succeeded": completed.returncode == 0,
        "output": sanitize_value(output, limit=20_000),
        "error_text": sanitize_text(completed.stderr.strip(), limit=4_000) or None,
    }, []


def cancel(store: ProjectStore, params: dict[str, Any]) -> tuple[dict[str, Any], list[Omission]]:
    token = str(params.get("token") or "")
    return {"cancelled": _TOKENS.pop(token, None) is not None}, []


def catalogue() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "title": spec.title,
            "writes": spec.writes,
            "effect": spec.effect,
            "fields": [field_name for field_name, _ in spec.fields],
        }
        for name, spec in OPERATIONS.items()
    ]
