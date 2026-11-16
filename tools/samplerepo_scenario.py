"""Exercise every AI SDLC capability against a disposable copy of the DeepTutor SampleRepo.

    python tools/samplerepo_scenario.py [--source <any brownfield repo>]
                                        [--target <scratch copy>] [--force]

The source repository is never modified: it is copied, the copy becomes a git repository,
and the real runner drives five Intents through the lifecycle. As in
`tools/make_demo_repo.py`, only the people and agents are scripted — approvals come from
named personas and reviews are recorded on behalf of reviewer agents — while every state
transition, refusal, evidence manifest, evaluation, and audit event is produced by the
runner. The implementations are real code whose tests really run.

Result: the copy is ready to open in AISDLC Studio, and `.scenario/COVERAGE.md` inside it
lists every capability the run proved, with the command or refusal that proved it.

Maintenance tool (ADR-008): it lives in `tools/`, not in the runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import samplerepo_payloads as P  # noqa: E402

from aisdlc.cli import app  # noqa: E402
from aisdlc.hooks import evaluate_hook  # noqa: E402
from aisdlc.typerlite import CliRunner  # noqa: E402
from aisdlc.vendor import yaml  # noqa: E402

AUTHOR_MODEL = "claude-fable-5-1"
REVIEWER_MODEL = "claude-opus-5-5"
PRODUCT, TECH, SECURITY, A11Y = "nadia.product", "omar.techlead", "lena.security", "ravi.a11y"
PINS = {
    "model_id": AUTHOR_MODEL,
    "prompt_version": "status-translate@v1",
    "prompt_hash": "sha256:" + "a" * 64,
    "temperature": 0.0,
    "seed": 7,
}
API = "deeptutor-api"
CONTRACT = "contracts/deeptutor-api.yaml"


class ScenarioError(RuntimeError):
    pass


class Scenario:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.cli = CliRunner()
        self.coverage: list[tuple[str, str, str]] = []  # (area, capability, proof)

    # -- plumbing ------------------------------------------------------------ #
    def run(self, *args: str, expect_failure: bool = False) -> Any:
        result = self.cli.invoke(app, [*args, "--repo", str(self.root)])
        failed = result.exit_code != 0
        if failed != expect_failure:
            raise ScenarioError(f"`ai-sdlc {' '.join(args)}` exit {result.exit_code}: {result.output}")
        try:
            return json.loads(result.output)
        except ValueError:
            return result.output

    def soft(self, area: str, capability: str, *args: str, repo: bool = True) -> Any:
        """Run a peripheral command; a failure is recorded in the ledger instead of aborting."""
        result = self.cli.invoke(app, [*args, *(["--repo", str(self.root)] if repo else [])])
        status = "ok" if result.exit_code == 0 else f"exit {result.exit_code}"
        self.cover(area, capability, f"`ai-sdlc {' '.join(args)}` → {status}")
        try:
            return json.loads(result.output)
        except ValueError:
            return result.output

    def cover(self, area: str, capability: str, proof: str) -> None:
        self.coverage.append((area, capability, proof))
        print(f"  [{area}] {capability}: {proof}")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-qm", message, "--allow-empty")

    def write(self, relative: str | Path, content: Any) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8", newline="\n")
        elif path.suffix in {".json", ".jsonl"}:
            text = (
                "\n".join(json.dumps(item, ensure_ascii=False) for item in content) + "\n"
                if path.suffix == ".jsonl"
                else json.dumps(content, indent=2, ensure_ascii=False)
            )
            path.write_text(text, encoding="utf-8", newline="\n")
        else:
            path.write_text(
                yaml.safe_dump(content, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n"
            )
        return path

    def new(self, title: str, kind: str, mode: str, *extra: str, reason: str) -> tuple[str, Path]:
        created = self.run(
            "new", "--title", title, "--type", kind, "--mode", mode, *extra, "--discovery-reason", reason
        )
        return created["intent_id"], Path(created["path"])

    def set_flags(self, intent_root: Path, **flags: bool) -> None:
        path = intent_root / "00-intent" / "intent.yaml"
        intent = yaml.safe_load(path.read_text(encoding="utf-8"))
        intent["trigger_flags"] = {**(intent.get("trigger_flags") or {}), **flags}
        path.write_text(yaml.safe_dump(intent, sort_keys=False), encoding="utf-8", newline="\n")

    def pre_gate_review(
        self, intent_id: str, gate: str, findings: list[dict[str, Any]], decision: str = "approved"
    ) -> None:
        """Record the red-team agent's review through the CLI, exactly as /aisdlc:specify does."""
        review = self.write(
            f".scenario/reviews/{intent_id}-{gate}-pregate.json",
            {
                "intent_id": intent_id,
                "gate": gate,
                "reviewer_agent_id": "aisdlc-red-team",
                "reviewer_model": REVIEWER_MODEL,
                "author_model": AUTHOR_MODEL,
                "decision": decision,
                "findings": findings,
                "recorded_at": "2026-09-25T09:00:00+00:00",
            },
        )
        self.run("pre-gate-review", intent_id, gate, "--from", str(review))

    def review(self, task_id: str, *, reviewer_model: str = REVIEWER_MODEL) -> Path:
        return self.write(
            f".scenario/reviews/{task_id}-{reviewer_model}.yaml",
            {
                "reviewer_agent_id": "aisdlc-verifier",
                "reviewer_model": reviewer_model,
                "implementer_model": AUTHOR_MODEL,
                "decision": "approved",
                "checks": {"criteria_met": "passed", "scope_respected": "passed"},
                "findings": [],
            },
        )

    def build_task(self, task_id: str, *, fail_first_with: tuple[str, str] | None = None) -> str:
        """start → verify (runner-captured evidence) → independent review → verified."""
        self.run("start-task", task_id)
        if fail_first_with:
            outcome = self.run("verify-task", task_id)
            if outcome["passed"]:
                raise ScenarioError(f"{task_id} was expected to fail its first attempt")
            self.cover("build", "failed attempt → retry", f"{task_id} attempt 1 failed; runner moved it to retry")
            # Written, not committed: every Task's evidence must pin the same integration commit.
            self.write(*fail_first_with)
            self.run("start-task", task_id)
        outcome = self.run("verify-task", task_id)
        if not outcome["passed"]:
            raise ScenarioError(f"{task_id} verification failed: {outcome}")
        self.run(
            "review-task",
            task_id,
            "--review",
            str(self.review(task_id)),
            "--evidence",
            outcome["manifest"],
            "--implementer",
            "claude-implementer",
        )
        return outcome["manifest"]


def holdout(outputs: list[str]) -> list[str]:
    """Held-out check written by the spec owner: every Python output must at least parse."""
    return [
        f"python -P -c \"import ast,pathlib; ast.parse(pathlib.Path('{path}').read_text(encoding='utf-8'))\""
        for path in outputs
        if path.endswith(".py")
    ] or ["python -P -c \"print('no python outputs')\""]


def task(
    intent_id: str,
    phase: str,
    number: int,
    title: str,
    requirements: list[str],
    criteria: list[tuple[str, str]],
    commands: list[str],
    outputs: list[str],
    *,
    depends_on: list[str] | None = None,
    resources: list[str] | None = None,
    decisions: list[str] | None = None,
    retry_limit: int = 2,
    baseline: dict[str, str] | None = None,
) -> dict[str, Any]:
    acceptance = []
    for criterion_id, verification_class in criteria:
        entry: dict[str, Any] = {"id": criterion_id, "verification_class": verification_class}
        if baseline and criterion_id in baseline:
            entry["baseline_ref"] = baseline[criterion_id]
        acceptance.append(entry)
    return {
        "id": f"{phase}-T{number:03d}",
        "intent_id": intent_id,
        "phase_id": phase,
        "title": title,
        "status": "pending",
        "objective": title,
        "requirements": requirements,
        "source_findings": [],
        "source_decisions": decisions or [],
        "depends_on": depends_on or [],
        "inputs": ["Approved specification"],
        "expected_outputs": outputs,
        "parallelizable": False,
        "affected_paths": outputs,
        "shared_resources": resources or [],
        "acceptance_criteria": acceptance,
        "verification_commands": commands,
        "holdout_commands": holdout(outputs),
        "holdout_authorship": {"authored_by": "spec-owner", "context": "specification"},
        "retry_limit": retry_limit,
    }


def place_tasks(s: Scenario, root: Path, phases: dict[str, str], tasks: list[dict[str, Any]]) -> None:
    for phase_id, title in phases.items():
        s.write(root / "04-build" / phase_id / "phase.yaml", {"id": phase_id, "title": title})
    for item in tasks:
        s.write(root / "04-build" / item["phase_id"] / "tasks" / item["id"] / "task.yaml", item)


def contract(*, admin_workspace: bool = True) -> dict[str, Any]:
    """The slice of DeepTutor's HTTP surface these Intents touch, as OpenAPI."""
    paths: dict[str, Any] = {
        "/api/v1/auth/login": {
            "post": {
                "operationId": "login",
                "responses": {
                    "200": {"description": "session cookie set"},
                    "401": {"description": "bad credentials"},
                    "429": {"description": "throttled; Retry-After header"},
                },
            }
        },
        "/api/v1/auth/device-login": {
            "post": {"operationId": "deviceLogin", "responses": {"200": {"description": "ok"}}}
        },
        "/api/v1/personas": {"get": {"operationId": "listPersonas", "responses": {"200": {"description": "ok"}}}},
    }
    if admin_workspace:
        paths["/api/v1/personas/admin-workspace"] = {
            "get": {"operationId": "getAdminPersonaWorkspace", "responses": {"200": {"description": "ok"}}}
        }
    return {"openapi": "3.1.0", "info": {"title": "DeepTutor API", "version": "1.6.5"}, "paths": paths}


def unchanged(*operations: str) -> list[dict[str, str]]:
    return [{"operation": op, "reason": "Existing DeepTutor operation; unchanged by this Intent."} for op in operations]


# =========================================================================== #
# Stage A — adoption and brownfield discovery
# =========================================================================== #
UNDERSTANDING = """---
source: repo
---

## Purpose and domain vocabulary

DeepTutor is an AI tutoring platform served over HTTP. [__version__.py] [compose.yaml]
Its surfaces are chat, deep research, question banks, books, reading, mastery and a co-writer. [api/routers]

## Entry points

The FastAPI application is assembled in one module and started by a runner script. [api/main.py] [api/run_server.py]
The package entry point imports a CLI package that is absent from this copy. [__main__.py] [cli/main.py]

## Module map and responsibilities

HTTP routers live in one package, one module per surface. [api/routers/auth.py]
Dashboard and persona routers follow the same shape. [api/routers/dashboard.py] [api/routers/personas.py]
Capability status strings are localized through a small helper. [i18n/status_i18n.py]
Multi-user identity, grants and guardians have their own package. [multi_user/]

## Boundaries

Login, registration and device login are public endpoints with no attempt limit. [api/routers/auth.py:532-560]
Persona CRUD always resolves to the admin workspace. [api/routers/personas.py]

## Data model and persistence

Sessions come from a session store listed with limit and offset. [api/routers/dashboard.py:18-46]

## Cross-cutting concerns

Localized status text falls back to the default string when formatting fails. [i18n/status_i18n.py:48-56]

## Build, test, and CI topology

Tests use pytest and mirror the source tree. [tests/conftest.py]
The root conftest imports the package as deeptutor. [tests/conftest.py]
The suite therefore needs the checkout named deeptutor, with its dependencies installed. [tests/conftest.py]
The container image expects dependency manifests that are absent from this copy. [Dockerfile]

## Risk concentrations

Session and authentication code is the critical risk zone of the governance policy. [.ai-governance/risk-zones.yaml]
The dashboard recent-activity handler passes an unbounded limit to the store. [api/routers/dashboard.py:18-46]

## Constraints and architectural rules

Browser contracts are owned by the backend. [docs/adr]
Turn commands are idempotent. [docs/adr]

## Proposed checks

- [ ] `python -c "print(1)"` — the scenario probes live in .scenario/probes.yaml [tests/conftest.py]

## Uncertainty register

Which requirement files the container build expects is inferred from the Dockerfile only. [Dockerfile]
"""

PROBES = {
    "probes": [
        {
            "name": "scenario-tests",
            "kind": "characterization",
            "command": f"{P.PYTEST} tests/security tests/api/test_dashboard_recent_query.py",
        },
        {
            "name": "python-modules",
            "kind": "characterization",
            "command": "python -P -c \"import pathlib; print('modules', sum(1 for _ in pathlib.Path('.').rglob('*.py')))\"",  # noqa: E501
            "metric": r"modules (\d+)",
        },
        {
            "name": "dashboard-router-parses",
            "kind": "characterization",
            "command": "python -P -c \"import ast,pathlib; ast.parse(pathlib.Path('api/routers/dashboard.py')"
            ".read_text(encoding='utf-8'))\"",
        },
    ]
}


def adopt(s: Scenario) -> None:
    print("A. Adoption and brownfield discovery")
    s.git("init", "-q", "-b", "main")
    s.git("config", "user.name", "AISDLC Scenario")
    s.git("config", "user.email", "scenario@example.invalid")
    with (s.root / ".gitignore").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n# AI SDLC scenario scratch (reviews, reports, coverage)\n.scenario/\n")
    s.commit("Import DeepTutor 1.6.5 (SampleRepo copy)")
    s.cover("adoption", "git baseline", "copy committed as the pre-adoption state")
    s.soft("adoption", "runner version", "version", repo=False)
    s.soft("adoption", "doctor before init (unmanaged repo)", "doctor")
    s.run("init", "--mode", "full")
    s.cover("adoption", "init (full mode)", "`.ai-sdlc/`, `delivery/intents/`, AGENTS.md, CLAUDE.md block")
    s.soft("adoption", "doctor after init", "doctor")
    s.soft("adoption", "migrate (dry run)", "migrate")

    s.write(".ai-sdlc/project-understanding.md", UNDERSTANDING)
    s.commit("Adopt AI SDLC")
    s.run("understand", "record")
    s.commit("Pin project understanding")
    s.cover("brownfield", "understand record", "anchored cartographer map pinned to a commit")
    status = s.run("understand", "status")
    s.cover("brownfield", "understand status", f"status={status.get('status')}")

    s.write(".scenario/probes.yaml", PROBES)
    captured = s.run("baseline", "capture", str(s.root / ".scenario/probes.yaml"), "--label", "pre-change DeepTutor")
    s.cover("brownfield", "baseline capture", f"{captured['baseline_id']} with {captured['probes']} probes")

    # Datasets are committed before any evidence exists (their hash is pinned by evals).
    s.write("evals/datasets/login-bruteforce.jsonl", P.THROTTLE_ATTACKS)
    s.run(
        "dataset",
        "register",
        "regression/login-bruteforce",
        str(s.root / "evals/datasets/login-bruteforce.jsonl"),
        "--owner",
        SECURITY,
        "--version",
        "1",
    )
    s.write("evals/datasets/status-translations-zh.json", P.STATUS_TRANSLATIONS)
    s.run(
        "dataset",
        "register",
        "gold/status-translations-zh",
        str(s.root / "evals/datasets/status-translations-zh.json"),
        "--owner",
        A11Y,
        "--version",
        "1",
        "--trigger",
        "contains_model_behavior",
    )
    s.cover("evaluation", "dataset register", "regression/login-bruteforce, gold/status-translations-zh")
    s.commit("Baseline and evaluation datasets")


# =========================================================================== #
# I001 — login brute-force throttling: full lifecycle to completed
# =========================================================================== #
def login_throttling(s: Scenario) -> None:
    print("I001. Login brute-force throttling (full, security)")
    intent_id, root = s.new(
        "Login brute-force throttling",
        "security",
        "full",
        reason="the understanding map already anchors the login boundary",
    )
    s.write(
        root / "01-brief" / "brief.md",
        """# Intent Brief: I001 — Login brute-force throttling

## Problem

`POST /api/v1/auth/login` accepts unlimited failed attempts. A leaked username list plus a
password list is enough to take over tutor and guardian accounts, which expose learners'
records.

## Expected outcome

An attacker gets at most five password guesses per account per fifteen minutes, whatever
address or spelling of the username they use. Legitimate learners are unaffected.

## Success measures

- Online guessing against one account is capped at 5 attempts per 15 minutes (baseline: unlimited).
- Throttle checks add under 2 ms per login at p95.

## Out of scope

- CAPTCHA after lockout.
""",
    )
    s.run("advance", intent_id, "framed")
    s.run("gate", "approve", intent_id, "G1", "--approver", PRODUCT)
    s.cover("lifecycle", "G1 approve", f"{intent_id} framed and approved by {PRODUCT}")

    s.write(root / "02-specification" / "contracts" / "deeptutor-api.yaml", contract())
    spec: dict[str, Any] = {
        "intent_id": intent_id,
        "functional_requirements": [
            {
                "id": "REQ-F-001",
                "priority": "must",
                "sources": ["brief: Expected outcome"],
                "rationale": "Caps online guessing.",
                "statement": "When an account accumulates five failed login attempts within five minutes, the "
                "login endpoint shall refuse further attempts for that account for fifteen minutes.",
                "acceptance_criteria": ["AC-001"],
            },
            {
                "id": "REQ-F-002",
                "priority": "must",
                "sources": ["brief: Expected outcome"],
                "statement": "While an account is locked, the login endpoint shall return HTTP 429 with a "
                "Retry-After header.",
                "acceptance_criteria": ["AC-002"],
            },
            {
                "id": "REQ-F-003",
                "priority": "must",
                "sources": ["brief: Expected outcome"],
                "rationale": "A caller-steerable key resets the budget.",
                "statement": "The throttle shall key attempts by the normalised account name. TBD: confirm whether "
                "the client address also participates.",
                "acceptance_criteria": ["AC-003", "AC-004"],
            },
            {
                "id": "REQ-F-004",
                "priority": "wont",
                "sources": ["brief: Out of scope"],
                "statement": "The login page shall present a CAPTCHA after a lockout.",
                "acceptance_criteria": ["AC-005"],
            },
        ],
        "non_functional_requirements": [
            {
                "id": "REQ-NFR-001",
                "priority": "should",
                "category": "performance",
                "sources": ["brief: Success measures"],
                "measure": {
                    "metric": "throttle check latency",
                    "target": 2,
                    "unit": "ms",
                    "condition": "10000 tracked accounts",
                },
                "statement": "The throttle check shall take under 2 ms per login with 10000 tracked accounts.",
                "acceptance_criteria": ["AC-006"],
            },
        ],
        "acceptance_criteria": [
            {
                "id": "AC-001",
                "verification_class": "D",
                "statement": "Lockout tests pass.",
                "check": f"{P.PYTEST} tests/security/test_login_throttle.py",
                "contract_operations": [f"{CONTRACT}#login"],
            },
            {
                "id": "AC-002",
                "verification_class": "D",
                "statement": "Retry-After is reported while locked.",
                "check": f"{P.PYTEST} tests/security/test_login_throttle.py -k retry_after",
            },
            {
                "id": "AC-003",
                "verification_class": "D",
                "statement": "Key-derivation tests pass.",
                "check": f"{P.PYTEST} tests/security/test_throttle_keys.py",
            },
            {
                "id": "AC-004",
                "verification_class": "N",
                "statement": "No attack technique gets a sixth guess.",
                "eval": "EVAL-001",
                "dataset": "regression/login-bruteforce",
                "metric": "attack_success",
                "threshold": {"operator": "==", "value": 0},
                "minimum_sample_size": 3,
            },
            {"id": "AC-005", "verification_class": "D", "statement": "A CAPTCHA is shown.", "check": "true"},
            {
                "id": "AC-006",
                "verification_class": "D",
                "statement": "The cost test stays under budget.",
                "check": f"{P.PYTEST} tests/security/test_login_throttle.py -k cost",
            },
        ],
        "success_criteria": [
            {
                "id": "SC-001",
                "statement": "Online guessing against one account is capped.",
                "brief_measure": "5 attempts per 15 minutes",
                "acceptance_criteria": ["AC-001", "AC-004"],
            },
            {
                "id": "SC-002",
                "statement": "Legitimate logins stay fast.",
                "brief_measure": "under 2 ms per login at p95",
                "acceptance_criteria": ["AC-006"],
            },
        ],
        "contracts": [
            {
                "path": CONTRACT,
                "api": API,
                "excluded_operations": unchanged("deviceLogin", "listPersonas", "getAdminPersonaWorkspace"),
            }
        ],
    }
    s.write(root / "02-specification" / "requirements.yaml", spec)
    s.write(
        root / "02-specification" / "threat-model.yaml",
        {
            "intent_id": intent_id,
            "boundaries": [
                {
                    "id": "TB-001",
                    "name": "Internet -> /api/v1/auth/login",
                    "not_applicable": [
                        {
                            "category": "repudiation",
                            "reason": "Every login outcome is already logged by the auth router.",
                        },
                        {
                            "category": "elevation_of_privilege",
                            "reason": "The throttle grants no privilege; it only refuses.",
                        },
                    ],
                }
            ],
            "threats": [
                {
                    "id": "TM-001",
                    "boundary": "TB-001",
                    "stride": "spoofing",
                    "description": "Online password guessing impersonates a learner or guardian.",
                    "disposition": "mitigate",
                    "mitigated_by": ["REQ-F-001", "REQ-F-003"],
                    "verified_by": ["AC-004"],
                },
                {
                    "id": "TM-002",
                    "boundary": "TB-001",
                    "stride": "tampering",
                    "description": "A caller rotates X-Forwarded-For or re-spells the username to reset the budget.",
                    "disposition": "mitigate",
                    "mitigated_by": ["REQ-F-003"],
                    "verified_by": ["AC-004"],
                },
                {
                    "id": "TM-003",
                    "boundary": "TB-001",
                    "stride": "denial_of_service",
                    "description": "An attacker deliberately locks a victim out for fifteen minutes.",
                    "disposition": "accept",
                    "accepted_by": SECURITY,
                    "reason": "A 15-minute lockout is preferable to account takeover; admin unlock is future work.",
                },
                {
                    "id": "TM-004",
                    "boundary": "TB-001",
                    "stride": "information_disclosure",
                    "description": "HTTP 429 confirms that an account exists.",
                    "disposition": "accept",
                    "accepted_by": SECURITY,
                    "reason": "Registration already reveals account existence.",
                },
            ],
        },
    )
    # The draft still carries a TBD: spec-lint reports it and the runner refuses to advance.
    s.run("spec-lint", intent_id, expect_failure=True)
    s.run("advance", intent_id, "specified", expect_failure=True)
    s.cover("specification", "spec-lint refusal (SQ001 placeholder)", "`advance specified` refused while a TBD remains")
    spec["functional_requirements"][2]["statement"] = (
        "The throttle shall key attempts by the normalised account name and shall ignore caller-supplied headers."
    )
    s.write(root / "02-specification" / "requirements.yaml", spec)
    s.run("spec-lint", intent_id)
    s.run("advance", intent_id, "specified")
    s.cover("specification", "contracts + STRIDE threat model", "OpenAPI coverage and 4 threats across 6 categories")
    s.run("contract-registry", intent_id)
    # G2 cannot be approved before the independent pre-gate review exists.
    s.run("gate", "approve", intent_id, "G2", "--approver", TECH, expect_failure=True)
    s.cover("gates", "G2 refused without pre-gate review", "require_pre_gate_review enforced")
    s.pre_gate_review(
        intent_id,
        "G2",
        [
            {
                "id": "PR-1",
                "severity": "high",
                "summary": "The draft design keyed the throttle on X-Forwarded-For, which a caller controls.",
            }
        ],
    )
    s.run("gate", "approve", intent_id, "G2", "--approver", TECH)
    s.cover("gates", "pre-gate review + G2 approve", "red-team review recorded via `pre-gate-review --from`")

    decision = s.write(
        ".scenario/decisions/I001-throttle-store.yaml",
        {
            "intent_id": intent_id,
            "title": "Keep throttle state in process",
            "context": "compose.yaml already runs Redis, but the API runs as one worker per deployment.",
            "decision": "Throttle counters live in the API process; a restart clears lockouts.",
            "consequences": "No new failure mode; lockouts do not survive restarts or span replicas.",
            "alternatives": [
                {
                    "option": "Redis sorted sets",
                    "rejected_because": "Adds a dependency on Redis availability to every login.",
                }
            ],
            "requirements": ["REQ-F-001"],
            "affects": ["services/security/login_throttle.py"],
        },
    )
    decision_id = s.run("decision", "record", intent_id, "--from", str(decision))["decision"]
    s.run("decision", "accept", intent_id, decision_id)
    s.run("decision", "list", intent_id)
    s.cover("planning", "architecture decision record/accept/list", f"{decision_id} traced to a Task")

    s.write(
        root / "03-planning" / "master-plan.md",
        "# Master plan: I001\n\n"
        "P00 walking skeleton: lockout core with its tests.\n"
        "P01 caller-independent key and the adversarial evaluation.\n",
    )
    load_helper = "tests/_load.py"
    place_tasks(
        s,
        root,
        {"I001-P00": "Walking skeleton", "I001-P01": "Caller-independent key"},
        [
            task(
                intent_id,
                "I001-P00",
                1,
                "Sliding-window lockout core",
                ["REQ-F-001", "REQ-F-002", "REQ-NFR-001"],
                [("AC-001", "D"), ("AC-002", "D"), ("AC-006", "D")],
                [f"{P.PYTEST} tests/security/test_login_throttle.py"],
                ["services/security/login_throttle.py", "tests/security/test_login_throttle.py", load_helper],
                decisions=[decision_id],
                resources=[f"contract:{API}#login"],
            ),
            task(
                intent_id,
                "I001-P01",
                1,
                "Throttle key ignores caller-controlled input",
                ["REQ-F-003"],
                [("AC-003", "D"), ("AC-004", "N")],
                [f"{P.PYTEST} tests/security/test_throttle_keys.py"],
                [
                    "services/security/throttle_keys.py",
                    "tests/security/test_throttle_keys.py",
                    "evals/throttle_attacks.py",
                ],
                depends_on=["I001-P00-T001"],
            ),
        ],
    )
    s.run("validate-intent", intent_id)
    s.cover("planning", "validate-intent", "schemas, references and Task mapping complete")
    s.run("advance", intent_id, "planned")
    s.pre_gate_review(intent_id, "G3", [])
    s.run("gate", "approve", intent_id, "G3", "--approver", TECH)
    s.run("advance", intent_id, "building")
    s.cover("lifecycle", "G3 approve → building", "master plan, P00 walking skeleton, 2 Tasks")
    s.run("ready-tasks", intent_id)
    s.run("context-pack", "I001-P00-T001")
    s.cover("build", "ready-tasks + context-pack", "context pack for I001-P00-T001")

    # Finding and lesson land before the integration commit: nothing tracked may change after evidence.
    s.write(
        root / "05-verification" / "findings.yaml",
        {
            "findings": [
                {
                    "id": "F-001",
                    "severity": "high",
                    "status": "resolved",
                    "source": "G2 pre-gate review PR-1",
                    "requirement": "REQ-F-003",
                    "acceptance_criteria": ["AC-004"],
                    "description": "The draft design keyed the throttle on X-Forwarded-For, which a caller controls.",
                    "evidence": [".scenario/reviews/I001-G2-pregate.json"],
                    "impact": "Rotating the header gives an attacker a fresh guessing budget on every request.",
                    "recommended_action": "Key on the normalised account name only; verify with the Class N attack suite.",  # noqa: E501
                    "introduced_stage": 2,
                    "detected_stage": 2,
                    "lesson_ref": "LESSON-001",
                }
            ]
        },
    )
    lesson = s.write(
        ".scenario/lessons/LESSON-001.yaml",
        {
            "id": "LESSON-001",
            "source_intent": intent_id,
            "source_findings": ["F-001"],
            "severity_of_source": "high",
            "category": "security",
            "status": "proposed",
            "observation": "Scoping a security control by caller-supplied input (a header, a path or body id) lets "
            "the caller choose the scope.",
            "impact": "Throttle bypass here; cross-tenant reads wherever data is scoped the same way.",
            "review_after_intents": 5,
            "targets": [
                {
                    "type": "threat",
                    "artifact": ".ai-sdlc/security/standing-threats.yaml",
                    "trigger": "multi_tenant",
                    "enforcement": "blocking",
                    "fires_at": ["stage_2_specification"],
                    "stride": "information_disclosure",
                    "rule": "Security or tenant scope taken from caller-controlled input.",
                }
            ],
        },
    )
    s.run("lesson", "propose", str(lesson))
    s.run("lesson", "transition", "LESSON-001", "accepted", "--actor", SECURITY)
    s.run("lesson", "promote", "LESSON-001")
    s.cover("learning", "lesson propose → accept → promote", "LESSON-001 became a standing multi-tenant threat")

    s.write(load_helper, P.LOAD_HELPER)
    s.write("services/security/__init__.py", "")
    s.write("services/security/login_throttle.py", P.LOGIN_THROTTLE)
    s.write("tests/security/test_login_throttle.py", P.TEST_LOGIN_THROTTLE)
    s.write("tests/security/test_throttle_keys.py", P.TEST_THROTTLE_KEYS)
    s.write("evals/throttle_attacks.py", P.THROTTLE_ATTACK_HARNESS)
    s.commit("I001: login throttle")

    first = s.build_task("I001-P00-T001")
    s.soft(
        "evidence",
        "validate-evidence (P00 manifest at HEAD)",
        "validate-evidence",
        first,
        "--expected-commit",
        s.head(),
        repo=False,
    )
    s.run("gate", "approve", intent_id, "G4", "--approver", TECH)
    s.cover("gates", "G4 after walking skeleton", "P00 verified before P01 may start")

    s.run("start-task", "I001-P01-T001")
    outcome = s.run("verify-task", "I001-P01-T001")
    assert not outcome["passed"], "throttle_keys.py does not exist yet; attempt 1 must fail"
    s.cover("build", "failed attempt → retry", "I001-P01-T001 attempt 1 failed (module missing)")
    s.write("services/security/throttle_keys.py", P.THROTTLE_KEYS)
    s.run("start-task", "I001-P01-T001")
    outcome = s.run("verify-task", "I001-P01-T001")
    # A reviewer from the implementer's own model family is not independent.
    same_family = s.review("I001-P01-T001", reviewer_model="claude-fable-5-2")
    s.run(
        "review-task",
        "I001-P01-T001",
        "--review",
        str(same_family),
        "--evidence",
        outcome["manifest"],
        "--implementer",
        "claude-implementer",
        expect_failure=True,
    )
    s.cover("independence", "same-family reviewer refused", "claude-fable reviewer for a claude-fable implementer")
    s.run(
        "review-task",
        "I001-P01-T001",
        "--review",
        str(s.review("I001-P01-T001")),
        "--evidence",
        outcome["manifest"],
        "--implementer",
        "claude-implementer",
    )

    # Class N: replay every attack technique against the real throttle, then let the runner score it.
    results = s.root / ".scenario/evals/EVAL-001-results.json"
    results.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "evals/throttle_attacks.py", "evals/datasets/login-bruteforce.jsonl", str(results)],
        cwd=s.root,
        check=True,
    )
    spec_path = s.write(
        ".scenario/evals/EVAL-001.yaml",
        {
            "id": "EVAL-001",
            "verification_class": "N",
            "dataset": "regression/login-bruteforce",
            "metric": "attack_success",
            "threshold": {"operator": "==", "value": 0},
            "minimum_sample_size": 3,
            "pins": {**PINS, "model_id": "login-throttle@1", "prompt_version": "n/a"},
        },
    )
    evaluated = s.run("eval", "run", str(spec_path), str(results), "--output", str(root / "05-verification" / "evals"))
    s.cover("evaluation", "Class N eval run", f"observed_successes={evaluated['result'].get('observed_successes')}")

    retro = s.run("phase-retrospective", intent_id, "I001-P01", "--record", "--actor", TECH)
    s.cover("learning", "phase retrospective", f"I001-P01 recorded={retro.get('recorded')}")
    s.run("trace", intent_id)
    s.run("converge", intent_id)
    s.cover("lifecycle", "converge", "all Tasks verified at one commit; N eval current")
    for gate in ("G1", "G2", "G3", "G4", "G5"):
        s.run("gate-packet", intent_id, gate, "--output", str(s.root / f".scenario/reports/{intent_id}-{gate}.md"))
    s.cover("gates", "gate packets G1–G5", ".scenario/reports/I001-G*.md")
    s.run("gate", "approve", intent_id, "G5", "--approver", PRODUCT)
    s.run("close", intent_id, "--actor", PRODUCT)
    s.cover("lifecycle", "G5 + close", f"{intent_id} completed")
    s.commit("I001: closed")


# =========================================================================== #
# I002 — dashboard /recent bug fix: compact lifecycle with merge
# =========================================================================== #
def dashboard_bugfix(s: Scenario) -> None:
    print("I002. Dashboard recent-activity fix (compact, bugfix)")
    intent_id, root = s.new(
        "Dashboard recent activity honours limit and type",
        "bugfix",
        "compact",
        reason="covered by the pinned understanding and BASELINE-001",
    )
    s.run("advance", intent_id, "framed")
    s.run("gate", "approve", intent_id, "G1", "--approver", PRODUCT)
    s.write(
        root / "02-specification" / "requirements.yaml",
        {
            "intent_id": intent_id,
            "functional_requirements": [
                {
                    "id": "REQ-F-001",
                    "priority": "must",
                    "sources": ["project-understanding: Risk concentrations"],
                    "statement": "The recent-activity endpoint shall clamp limit to the range 1 to 200.",
                    "acceptance_criteria": ["AC-001"],
                },
                {
                    "id": "REQ-F-002",
                    "priority": "must",
                    "sources": ["support ticket"],
                    "statement": "When a type filter is given, the recent-activity endpoint shall return up to limit "
                    "matching activities.",
                    "acceptance_criteria": ["AC-002"],
                },
                {
                    "id": "REQ-F-003",
                    "priority": "must",
                    "sources": ["BASELINE-001"],
                    "statement": "The change shall not regress any characterized probe of BASELINE-001.",
                    "acceptance_criteria": ["AC-003"],
                },
            ],
            "non_functional_requirements": [],
            "acceptance_criteria": [
                {
                    "id": "AC-001",
                    "verification_class": "D",
                    "statement": "Limit clamping tests pass.",
                    "check": f"{P.PYTEST} tests/api/test_dashboard_recent_query.py -k clamped",
                },
                {
                    "id": "AC-002",
                    "verification_class": "D",
                    "statement": "Type-filter tests pass.",
                    "check": f"{P.PYTEST} tests/api/test_dashboard_recent_query.py -k filter",
                },
                {
                    "id": "AC-003",
                    "verification_class": "D",
                    "statement": "The router still parses; baseline holds.",
                    "check": "python -P -m py_compile api/routers/dashboard.py",
                },
            ],
            "success_criteria": [
                {
                    "id": "SC-001",
                    "statement": "Filtered dashboards show a full page.",
                    "acceptance_criteria": ["AC-002"],
                }
            ],
        },
    )
    s.run("advance", intent_id, "specified")
    outputs = ["api/routers/dashboard_query.py", "api/routers/dashboard.py", "tests/api/test_dashboard_recent_query.py"]
    place_tasks(
        s,
        root,
        {"I002-P00": "Fix"},
        [
            task(
                intent_id,
                "I002-P00",
                1,
                "Bounded limit and page-filling type filter",
                ["REQ-F-001", "REQ-F-002", "REQ-F-003"],
                [("AC-001", "D"), ("AC-002", "D"), ("AC-003", "D")],
                [
                    f"{P.PYTEST} tests/api/test_dashboard_recent_query.py",
                    "python -P -m py_compile api/routers/dashboard.py",
                ],
                outputs,
                baseline={"AC-003": "BASELINE-001"},
            ),
        ],
    )
    s.pre_gate_review(intent_id, "G2", [])
    s.pre_gate_review(intent_id, "G3", [])
    s.run("gate", "approve", intent_id, "G2-G3", "--approver", TECH)
    s.cover("gates", "compact G2-G3 combined gate", f"{intent_id} spec and plan approved together")
    s.run("advance", intent_id, "planned")
    s.run("advance", intent_id, "building")

    router = s.root / "api/routers/dashboard.py"
    text = router.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    if P.DASHBOARD_ROUTER_OLD not in text:
        raise ScenarioError("api/routers/dashboard.py no longer matches the expected handler")
    text = text.replace(P.DASHBOARD_ROUTER_OLD, P.DASHBOARD_ROUTER_NEW).replace(
        "from deeptutor.services.session import get_session_store\n",
        "from deeptutor.api.routers import dashboard_query\nfrom deeptutor.services.session import get_session_store\n",
    )
    router.write_text(text.replace("\n", newline), encoding="utf-8", newline="")
    s.write("api/routers/dashboard_query.py", P.DASHBOARD_QUERY)
    s.write("tests/api/test_dashboard_recent_query.py", P.TEST_DASHBOARD_QUERY)
    s.commit("I002: dashboard recent-activity fix")

    s.build_task("I002-P00-T001")
    ready = s.run("merge-ready", "I002-P00-T001", "--expected-commit", s.head())
    s.run("merge", "I002-P00-T001", "--expected-commit", s.head(), "--actor", TECH)
    s.cover("build", "merge-ready + merge", f"ready={ready.get('ready')}; Task merged, locks released")
    s.run("gate", "approve", intent_id, "G4", "--approver", TECH)
    s.run("converge", intent_id)
    s.run("gate", "approve", intent_id, "G5", "--approver", PRODUCT)
    s.run("close", intent_id, "--actor", PRODUCT)
    s.cover("lifecycle", "compact Intent completed", f"{intent_id} with baseline_ref criterion")
    s.commit("I002: closed")
    compared = s.run("baseline", "compare", "BASELINE-001")
    s.cover(
        "brownfield", "baseline compare", f"passed={compared['passed']}, improvements={len(compared['improvements'])}"
    )
    s.commit("Baseline comparison after I002")


# =========================================================================== #
# I003 — localized capability status: model behaviour, a11y, glossary, J/S evals, G2 reopened
# =========================================================================== #
def localized_status(s: Scenario) -> None:
    print("I003. Localized capability status (full, model behaviour) — G2 reopened")
    intent_id, root = s.new(
        "Localized capability status with accessible announcements",
        "feature",
        "full",
        "--contains-model-behavior",
        reason="i18n helper already mapped in the understanding",
    )
    s.write(
        root / "01-brief" / "brief.md",
        f"""# Intent Brief: {intent_id} — Localized capability status

## Problem

Capability status messages ("Thinking...", "Grading answer 2 of 5") reach Chinese-speaking
learners in English, and screen-reader users are not told when a status changes.

## Success measures

- 100% of status keys have a zh translation that keeps every placeholder.
- Zero WCAG 2.2 AA automated violations on the chat status region.
""",
    )
    s.run("advance", intent_id, "framed")
    s.run("gate", "approve", intent_id, "G1", "--approver", PRODUCT)
    s.write(
        root / "02-specification" / "eval-design.md",
        f"""# Evaluation design: {intent_id}

Status strings are machine-translated by {AUTHOR_MODEL}. Translation faithfulness is judged
(Class J) by {REVIEWER_MODEL}, a different family, with dual ordering and a calibrated rubric.
Placeholder preservation is scored deterministically per case and aggregated as Class S.
""",
    )
    s.write(root / "02-specification" / "contracts" / "deeptutor-api.yaml", contract())
    spec: dict[str, Any] = {
        "intent_id": intent_id,
        "glossary": [
            {
                "term": "capability status",
                "definition": "A short progress message a capability streams to the learner during a turn.",
            }
        ],
        "functional_requirements": [
            {
                "id": "REQ-F-001",
                "priority": "must",
                "sources": ["brief: Problem"],
                "statement": "When a capability streams a capability status, the service shall render it in the "
                "learner's locale.",
                "acceptance_criteria": ["AC-001", "AC-005", "AC-006"],
            },
            {
                "id": "REQ-F-002",
                "priority": "must",
                "sources": ["project-understanding: Cross-cutting concerns"],
                "statement": "When a status template fails to format, the service shall log the key and return the "
                "default text.",
                "acceptance_criteria": ["AC-002"],
            },
            {
                "id": "REQ-F-003",
                "priority": "should",
                "sources": ["brief: Problem"],
                "statement": "The persona list shall return display names in the learner's locale.",
                "acceptance_criteria": ["AC-003"],
            },
        ],
        "non_functional_requirements": [
            {
                "id": "REQ-NFR-001",
                "priority": "must",
                "category": "accessibility",
                "sources": ["brief"],
                "statement": "The chat status region shall announce each capability status to screen readers without "
                "moving focus.",
                "measure": {"metric": "axe violations", "target": 0},
                "wcag": {"version": "2.2", "level": "AA"},
                "manual_audit": {"owner": "accessibility-guild"},
                "acceptance_criteria": ["AC-004"],
            },
        ],
        "acceptance_criteria": [
            {
                "id": "AC-001",
                "verification_class": "D",
                "statement": "Locale selection tests pass.",
                "check": f"{P.PYTEST} tests/i18n",
            },
            {
                "id": "AC-002",
                "verification_class": "D",
                "statement": "Format failures are logged.",
                "check": f"{P.PYTEST} tests/i18n -k format_error",
            },
            {
                "id": "AC-003",
                "verification_class": "D",
                "statement": "Persona names are localized.",
                "check": f"{P.PYTEST} tests/i18n -k persona",
                "contract_operations": [f"{CONTRACT}#listPersonas"],
            },
            {
                "id": "AC-004",
                "verification_class": "D",
                "statement": "axe reports no violations.",
                "check": "npx @axe-core/cli http://localhost:3782/chat --exit",
            },
            {
                "id": "AC-005",
                "verification_class": "J",
                "statement": "Translations are judged faithful.",
                "eval": "EVAL-002",
                "dataset": "gold/status-translations-zh",
                "metric": "faithfulness",
                "threshold": {"operator": ">=", "value": 0.9},
                "tolerance_band": 0.03,
                "minimum_sample_size": 3,
            },
            {
                "id": "AC-006",
                "verification_class": "S",
                "statement": "Placeholders survive translation.",
                "eval": "EVAL-003",
                "dataset": "gold/status-translations-zh",
                "metric": "placeholder_preserved",
                "threshold": {"operator": ">=", "value": 1.0},
                "tolerance_band": 0.0,
                "minimum_sample_size": 3,
            },
        ],
        "success_criteria": [
            {
                "id": "SC-001",
                "statement": "Learners read status in their language.",
                "acceptance_criteria": ["AC-005", "AC-006"],
            }
        ],
        "contracts": [
            {
                "path": CONTRACT,
                "api": API,
                "excluded_operations": unchanged("login", "deviceLogin", "getAdminPersonaWorkspace"),
            }
        ],
    }
    s.write(root / "02-specification" / "requirements.yaml", spec)
    s.run("advance", intent_id, "specified")
    s.run("inherit-suites", intent_id)
    declined = s.soft(
        "evaluation",
        "dataset decline (escalated)",
        "dataset",
        "decline",
        intent_id,
        "regression/refusal-behavior",
        "--reason",
        "Status strings carry no refusal behaviour; escalated to the AI risk owner.",
    )
    s.pre_gate_review(
        intent_id,
        "G2",
        [{"id": "PR-1", "severity": "medium", "summary": "RTL locales are not covered by REQ-NFR-001."}],
    )
    s.run("gate", "approve", intent_id, "G2", "--approver", TECH)
    promoted = s.run("glossary", "promote", intent_id, "capability status", "--owner", A11Y)
    s.cover(
        "specification", "glossary promote", f"'capability status' → .ai-sdlc/glossary.yaml ({promoted.get('status')})"
    )
    s.cover(
        "specification", "accessibility carve-out (ADR-0009)", "WCAG 2.2 AA NFR with Class D checker + manual audit"
    )

    # Class J: judge scores are scripted (no model is called), the calibration record is real input.
    judged = s.write(
        ".scenario/evals/EVAL-002-results.json",
        [
            {"score": 0.95, "cases": [1, 2, 3, 4]},
            {"score": 0.93, "cases": [1, 2, 3, 4]},
            {"score": 0.96, "cases": [1, 2, 3, 4]},
        ],
    )
    judge = {"model_id": REVIEWER_MODEL, "prompt_version": "judge-faithful@v1", "prompt_hash": "sha256:" + "b" * 64}
    j_spec = s.write(
        ".scenario/evals/EVAL-002.yaml",
        {
            "id": "EVAL-002",
            "verification_class": "J",
            "dataset": "gold/status-translations-zh",
            "metric": "faithfulness",
            "threshold": {"operator": ">=", "value": 0.9},
            "tolerance_band": 0.03,
            "minimum_sample_size": 3,
            "pins": PINS,
            "judge": {
                **judge,
                "dual_order": True,
                "reasoning_before_score": True,
                "rubric": [
                    {"id": "RC-001", "type": "binary", "check": "meaning of the English status is kept"},
                    {"id": "RC-002", "type": "binary", "check": "every placeholder is kept"},
                ],
                "exemplars": [
                    {"id": "EX-001", "label": "pass", "annotation": "正在思考{topic}…… keeps topic"},
                    {"id": "EX-002", "label": "fail", "annotation": "drops {total}"},
                ],
                "calibration": {
                    **judge,
                    "agreement": 0.9,
                    "confusion_matrix": {
                        "true_positive": 5,
                        "true_negative": 4,
                        "false_positive": 1,
                        "false_negative": 0,
                    },
                },
            },
        },
    )
    result = s.run("eval", "run", str(j_spec), str(judged), "--output", str(root / "05-verification" / "evals"))
    s.cover("evaluation", "Class J eval (calibrated, cross-family judge)", f"passed={result['result'].get('passed')}")
    s.write("evals/placeholder_scorer.py", P.PLACEHOLDER_SCORER)
    s_spec = s.write(
        ".scenario/evals/EVAL-003.yaml",
        {
            "id": "EVAL-003",
            "verification_class": "S",
            "dataset": "gold/status-translations-zh",
            "metric": "placeholder_preserved",
            "threshold": {"operator": ">=", "value": 1.0},
            "tolerance_band": 0.0,
            "minimum_sample_size": 3,
            "pins": {**PINS, "command": "python evals/placeholder_scorer.py"},
        },
    )
    s.soft(
        "evaluation",
        "Class S eval execute (command adapter)",
        "eval",
        "execute",
        str(s_spec),
        "--output",
        str(root / "05-verification" / "evals"),
        "--adapter",
        "command",
    )

    s.write(
        root / "03-planning" / "master-plan.md", f"# Master plan: {intent_id}\n\nP00 zh status strings end to end.\n"
    )
    place_tasks(
        s,
        root,
        {f"{intent_id}-P00": "zh status skeleton"},
        [
            task(
                intent_id,
                f"{intent_id}-P00",
                1,
                "Locale-aware status rendering",
                ["REQ-F-001", "REQ-F-002"],
                [("AC-001", "D"), ("AC-002", "D"), ("AC-005", "J"), ("AC-006", "S")],
                [f"{P.PYTEST} tests/i18n"],
                ["i18n/status_i18n.py", "tests/i18n/test_status_i18n.py"],
            ),
            task(
                intent_id,
                f"{intent_id}-P00",
                2,
                "Localized persona names",
                ["REQ-F-003"],
                [("AC-003", "D")],
                [f"{P.PYTEST} tests/i18n -k persona"],
                ["api/routers/personas.py"],
                resources=[f"contract:{API}#listPersonas"],
            ),
            task(
                intent_id,
                f"{intent_id}-P00",
                3,
                "Live-region announcement",
                ["REQ-NFR-001"],
                [("AC-004", "D")],
                ["npx @axe-core/cli http://localhost:3782/chat --exit"],
                ["docs/a11y/status-region.md"],
            ),
        ],
    )
    s.run("advance", intent_id, "planned")
    # Product adds Arabic: the approved specification must change — a recorded reopening, not a quiet edit.
    s.run(
        "gate",
        "reopen",
        intent_id,
        "G2",
        "--actor",
        PRODUCT,
        "--reason",
        "Product adds Arabic; right-to-left status text must be specified",
    )
    s.run("advance", intent_id, "specified")
    spec["functional_requirements"].append(
        {
            "id": "REQ-F-004",
            "priority": "must",
            "sources": ["gate reopen reason"],
            "statement": "When the learner's locale is right-to-left, the service shall mark each capability status "
            "with its text direction.",
            "acceptance_criteria": ["AC-007"],
        }
    )
    spec["acceptance_criteria"].append(
        {
            "id": "AC-007",
            "verification_class": "D",
            "statement": "RTL statuses carry dir=rtl.",
            "check": f"{P.PYTEST} tests/i18n -k rtl",
        }
    )
    s.write(root / "02-specification" / "requirements.yaml", spec)
    s.run("gate-packet", intent_id, "G2", "--output", str(s.root / f".scenario/reports/{intent_id}-G2-reopened.md"))
    s.cover("specification", "G2 reopen + specification diff", f".scenario/reports/{intent_id}-G2-reopened.md")
    s.commit(f"{intent_id}: specification reopened for RTL")
    del declined


# =========================================================================== #
# I004 — dependency manifests: waiting on Gate 1 (approve it from Studio)
# =========================================================================== #
def dependency_manifests(s: Scenario) -> None:
    print("I004. Restore dependency manifests (compact, infrastructure) — left at G1")
    intent_id, root = s.new(
        "Restore Python dependency manifests",
        "infrastructure",
        "compact",
        reason="the understanding map records the missing manifests",
    )
    s.write(
        root / "01-brief" / "brief.md",
        f"""# Intent Brief: {intent_id} — Restore dependency manifests

## Problem

The Dockerfile and two packaging tests expect `requirements.txt` and `requirements/`, which
are missing, so the container build and the full test suite cannot run.

## Success measures

- `docker build` succeeds from a clean checkout.
""",
    )
    s.run("advance", intent_id, "framed")
    s.cover("gates", "Intent waiting on G1", f"{intent_id} — approve it from Studio's gate packet panel")
    s.commit(f"{intent_id}: framed")


# =========================================================================== #
# I005 — per-learner persona scoping: multi-tenant, inherited threat, terminal Task states
# =========================================================================== #
def persona_scoping(s: Scenario) -> None:
    print("I005. Per-learner persona scoping (full, multi-tenant) — left building")
    intent_id, root = s.new(
        "Per-learner persona scoping", "feature", "full", reason="the understanding map anchors the persona boundary"
    )
    s.set_flags(root, multi_tenant=True)
    s.write(
        root / "01-brief" / "brief.md",
        f"""# Intent Brief: {intent_id} — Per-learner persona scoping

## Problem

Every persona request resolves to the admin workspace, so one learner's custom tutor
persona is visible to, and editable by, every other learner.

## Success measures

- Zero cross-learner persona reads in the tenant-isolation suite.
""",
    )
    s.run("advance", intent_id, "framed")
    s.run("gate", "approve", intent_id, "G1", "--approver", PRODUCT)
    s.write(root / "02-specification" / "contracts" / "deeptutor-api.yaml", contract(admin_workspace=False))
    s.write(
        root / "02-specification" / "requirements.yaml",
        {
            "intent_id": intent_id,
            "functional_requirements": [
                {
                    "id": "REQ-F-001",
                    "priority": "must",
                    "sources": ["brief: Problem"],
                    "statement": "The persona service shall store each learner's personas in that learner's workspace.",
                    "acceptance_criteria": ["AC-001"],
                },
                {
                    "id": "REQ-F-002",
                    "priority": "must",
                    "sources": ["LESSON-001"],
                    "statement": "The persona service shall scope every persona read to the authenticated principal.",
                    "acceptance_criteria": ["AC-002"],
                },
                {
                    "id": "REQ-F-003",
                    "priority": "should",
                    "sources": ["migration"],
                    "statement": "A migration shall move existing personas into their owners' workspaces.",
                    "acceptance_criteria": ["AC-003"],
                },
            ],
            "non_functional_requirements": [],
            "acceptance_criteria": [
                {
                    "id": "AC-001",
                    "verification_class": "D",
                    "statement": "Persona scope tests pass.",
                    "check": f"{P.PYTEST} tests/security/test_persona_scope.py",
                    "contract_operations": [f"{CONTRACT}#listPersonas"],
                },
                {
                    "id": "AC-002",
                    "verification_class": "N",
                    "statement": "No cross-learner persona read succeeds.",
                    "eval": "EVAL-004",
                    "dataset": "regression/tenant-isolation",
                    "metric": "attack_success",
                    "threshold": {"operator": "==", "value": 0},
                    "minimum_sample_size": 3,
                },
                {
                    "id": "AC-003",
                    "verification_class": "D",
                    "statement": "The migration dry run succeeds.",
                    "check": "python scripts/migrate_personas.py --dry-run",
                },
            ],
            "success_criteria": [
                {
                    "id": "SC-001",
                    "statement": "Learners see only their own personas.",
                    "acceptance_criteria": ["AC-002"],
                }
            ],
            "contracts": [
                {
                    "path": CONTRACT,
                    "api": API,
                    "excluded_operations": unchanged("login", "deviceLogin"),
                    "breaking_changes_accepted": [
                        {
                            "operation": "getAdminPersonaWorkspace",
                            "accepted_by": TECH,
                            "reason": "The admin-workspace shortcut is the defect; no client outside the web app.",
                        }
                    ],
                }
            ],
        },
    )
    s.write(
        root / "02-specification" / "threat-model.yaml",
        {
            "intent_id": intent_id,
            "boundaries": [
                {
                    "id": "TB-001",
                    "name": "Authenticated learner -> persona API",
                    "not_applicable": [
                        {"category": "repudiation", "reason": "Persona writes are audited by multi_user/audit."},
                        {
                            "category": "denial_of_service",
                            "reason": "Unchanged request volume; existing rate limits apply.",
                        },
                    ],
                }
            ],
            "threats": [
                {
                    "id": "TM-001",
                    "boundary": "TB-001",
                    "stride": "information_disclosure",
                    "description": "A learner passes another learner's workspace id and reads their personas.",
                    "disposition": "mitigate",
                    "inherited_from": "LESSON-001",
                    "mitigated_by": ["REQ-F-002"],
                    "verified_by": ["AC-002"],
                },
                {
                    "id": "TM-002",
                    "boundary": "TB-001",
                    "stride": "tampering",
                    "description": "A learner edits a persona in another workspace.",
                    "disposition": "mitigate",
                    "mitigated_by": ["REQ-F-002"],
                    "verified_by": ["AC-002"],
                },
                {
                    "id": "TM-003",
                    "boundary": "TB-001",
                    "stride": "spoofing",
                    "description": "A forged session claims another learner's identity.",
                    "disposition": "transfer",
                    "accepted_by": SECURITY,
                    "reason": "Session integrity is owned by auth.",
                },
                {
                    "id": "TM-004",
                    "boundary": "TB-001",
                    "stride": "elevation_of_privilege",
                    "description": "A learner claims the admin role to reach the admin workspace.",
                    "disposition": "transfer",
                    "accepted_by": SECURITY,
                    "reason": "Roles come from the auth token.",
                },
            ],
        },
    )
    s.run("advance", intent_id, "specified")
    s.cover("learning", "standing threat inherited", f"{intent_id} TM-001 answers LESSON-001 (multi_tenant trigger)")
    s.soft(
        "learning",
        "lesson inherit",
        "lesson",
        "inherit",
        intent_id,
        "--fires-at",
        "stage_2_specification",
        "--current-intent",
        "5",
    )
    s.run("inherit-suites", intent_id)
    s.cover("evaluation", "inherit-suites (multi_tenant)", "regression/tenant-isolation inherited")
    registry = s.run("contract-registry", intent_id)
    s.cover(
        "specification",
        "contract registry: breaking change + contention",
        f"breaking={len(registry.get('breaking_changes', []))}, contention={len(registry.get('contention', []))}",
    )
    s.pre_gate_review(intent_id, "G2", [])
    s.run("gate", "approve", intent_id, "G2", "--approver", TECH)

    decision = s.write(
        f".scenario/decisions/{intent_id}-scope-source.yaml",
        {
            "intent_id": intent_id,
            "title": "Persona scope comes from the principal",
            "context": "LESSON-001: scope taken from caller input lets the caller choose it.",
            "decision": "The workspace is derived from the authenticated principal; request workspace ids are ignored.",
            "consequences": "Admins can no longer browse learner personas through the learner API.",
            "alternatives": [
                {
                    "option": "Validate the requested workspace against grants",
                    "rejected_because": "Keeps a caller-controlled value on the security path.",
                }
            ],
            "requirements": ["REQ-F-002"],
            "affects": ["services/persona/scope.py"],
        },
    )
    decision_id = s.run("decision", "record", intent_id, "--from", str(decision))["decision"]
    s.run("decision", "accept", intent_id, decision_id)
    s.write(
        root / "03-planning" / "master-plan.md",
        f"# Master plan: {intent_id}\n\nP00 principal-derived scope.\nP01 migration and router wiring.\n",
    )
    p00, p01 = f"{intent_id}-P00", f"{intent_id}-P01"
    scope_outputs = ["services/persona/scope.py", "tests/security/test_persona_scope.py"]
    place_tasks(
        s,
        root,
        {p00: "Scope skeleton", p01: "Migration and wiring"},
        [
            task(
                intent_id,
                p00,
                1,
                "Principal-derived persona scope",
                ["REQ-F-001", "REQ-F-002"],
                [("AC-001", "D"), ("AC-002", "N")],
                [f"{P.PYTEST} tests/security/test_persona_scope.py"],
                scope_outputs,
                decisions=[decision_id],
                resources=[f"contract:{API}#listPersonas"],
            ),
            task(
                intent_id,
                p00,
                2,
                "Admin workspace compatibility shim",
                ["REQ-F-001"],
                [("AC-001", "D")],
                [f"{P.PYTEST} tests/security/test_persona_scope.py"],
                ["services/persona/admin_shim.py"],
            ),
            task(
                intent_id,
                p00,
                3,
                "Persona scope with admin audit trail (corrective)",
                ["REQ-F-001", "REQ-F-002"],
                [("AC-001", "D")],
                [f"{P.PYTEST} tests/security/test_persona_scope.py"],
                scope_outputs,
            ),
            task(
                intent_id,
                p01,
                1,
                "Persona migration script",
                ["REQ-F-003"],
                [("AC-003", "D")],
                ["python scripts/migrate_personas.py --dry-run"],
                ["scripts/migrate_personas.py"],
                retry_limit=0,
            ),
            task(
                intent_id,
                p01,
                2,
                "Wire scope into the personas router",
                ["REQ-F-002"],
                [("AC-001", "D")],
                [f"{P.PYTEST} tests/security/test_persona_scope.py"],
                ["api/routers/personas.py"],
            ),
        ],
    )
    s.pre_gate_review(intent_id, "G3", [])
    s.run("advance", intent_id, "planned")
    s.run("gate", "approve", intent_id, "G3", "--approver", TECH)
    s.run("advance", intent_id, "building")
    s.soft("scale", "intent-resources declare", "intent-resources", intent_id, "--declare", "schema:persona-store")

    s.write("services/persona/scope.py", P.PERSONA_SCOPE)
    s.write("tests/security/test_persona_scope.py", P.TEST_PERSONA_SCOPE)
    s.commit(f"{intent_id}: persona scope")

    first = f"{p00}-T001"
    s.build_task(first)
    s.run("merge", first, "--expected-commit", s.head(), "--actor", TECH)
    s.run("gate", "approve", intent_id, "G4", "--approver", TECH)
    s.run(
        "revert-task",
        first,
        "--reason",
        "Admins lost their audit view; replaced by the corrective Task",
        "--corrective",
        f"{p00}-T003",
    )
    s.cover("build", "revert-task with corrective", f"{first} reverted → {p00}-T003")
    s.run(
        "supersede-task", f"{p00}-T002", "--reason", "Folded into the corrective Task", "--replaced-by", f"{p00}-T003"
    )
    s.cover("build", "supersede-task --replaced-by", f"{p00}-T002 replaced by {p00}-T003")

    blocked = f"{p01}-T001"
    s.run("start-task", blocked)
    s.run("verify-task", blocked)  # the migration script does not exist; retry_limit 0 blocks it
    s.run(
        "fail-task",
        blocked,
        "--reason",
        "Migration needs production data access; re-plan as its own Intent",
        "--actor",
        TECH,
    )
    s.cover("build", "blocked → fail-task", f"{blocked} exhausted retry_limit 0, then failed by {TECH}")

    prepared = s.soft(
        "scale", "orchestrate --prepare (worktrees + context packs)", "orchestrate", intent_id, "--prepare"
    )
    for item in (prepared.get("prepared") or [])[1:] if isinstance(prepared, dict) else []:
        s.soft("scale", "worktree remove", "worktree", "remove", item["task_id"], "--confirm")
    s.run("start-task", f"{p01}-T002")
    s.cover("build", "Task in progress", f"{p01}-T002 claimed — the Stop hook now blocks ending the session")


# =========================================================================== #
# Stage Z — portfolio, metrics, conformance, hooks, refusals
# =========================================================================== #
def portfolio_and_assurance(s: Scenario, ids: dict[str, str]) -> None:
    print("Z. Portfolio, metrics, conformance, hooks")
    program = s.run("program", "new", "--title", "Learner account hardening", "--owner", SECURITY)["id"]
    s.run("program", "add", program, ids["throttle"])
    s.run("program", "add", program, ids["persona"], "--requires", ids["throttle"])
    s.run("program", "add", program, ids["status"], "--requires", ids["persona"])
    s.run(
        "program",
        "waive",
        program,
        ids["status"],
        ids["persona"],
        "--actor",
        SECURITY,
        "--reason",
        "Localization does not depend on persona scoping at runtime",
    )
    status = s.run("program", "status", program)
    s.cover(
        "scale",
        "program new/add/waive/status",
        f"{program}: {status.get('closed_members')}/{status.get('total_members')} closed",
    )
    reports = s.root / ".scenario/reports"
    s.soft(
        "assurance",
        "export-conformance (Intent)",
        "export-conformance",
        ids["throttle"],
        "--output",
        str(reports / f"conformance-{ids['throttle']}.md"),
    )
    s.soft(
        "assurance",
        "export-conformance (program)",
        "export-conformance",
        program,
        "--output",
        str(reports / f"conformance-{program}.md"),
    )
    s.soft("metrics", "metrics cost", "metrics", "cost")
    s.soft(
        "metrics",
        "metrics export (OpenMetrics)",
        "metrics",
        "export",
        "--openmetrics",
        "--output",
        str(reports / "metrics.openmetrics.txt"),
    )
    s.soft("metrics", "loop-metrics", "loop-metrics")
    s.soft(
        "learning",
        "lesson decay-review",
        "lesson",
        "decay-review",
        "--current-intent",
        "5",
        "--runtime-budget-seconds",
        "600",
        "--estimated-suite-seconds",
        "30",
    )
    s.soft("brownfield", "understand status after change (drift)", "understand", "status")
    s.soft("adoption", "status (repository)", "status")
    s.soft("adoption", "doctor (final)", "doctor")
    s.run("gate-packet", ids["deps"], "G1", "--output", str(reports / f"{ids['deps']}-G1.md"))

    # Refusal: compact Intents may not contain model behaviour (checked last — a refusal allocates nothing).
    s.run(
        "new",
        "--title",
        "Compact model feature",
        "--type",
        "feature",
        "--mode",
        "compact",
        "--contains-model-behavior",
        "--discovery-reason",
        "refusal check",
        expect_failure=True,
    )
    s.cover("independence", "compact + model behaviour refused", "`new --mode compact --contains-model-behavior`")

    hooks(s, ids)


def hooks(s: Scenario, ids: dict[str, str]) -> None:
    """Every lifecycle guard, evaluated in-process exactly as Claude Code's hook would call it."""
    throttle_root = next((s.root / "delivery/intents").glob(f"{ids['throttle']}-*"))
    throttle_spec = throttle_root / "02-specification/requirements.yaml"
    in_progress = f"{ids['persona']}-P01-T002"
    cases = [
        (
            "pre-tool-use",
            {"tool_name": "Edit", "tool_input": {"file_path": str(throttle_spec)}},
            False,
            "edit an approved (post-G2) specification",
        ),
        (
            "pre-tool-use",
            {"tool_name": "Write", "tool_input": {"file_path": str(s.root / ".ai-sdlc/runtime/state.json")}},
            False,
            "write runner state",
        ),
        (
            "pre-tool-use",
            {"tool_name": "Edit", "tool_input": {"file_path": str(s.root / "api/routers/auth.py")}},
            True,
            "edit ordinary source",
        ),
        (
            "pre-tool-use",
            {"tool_name": "Task", "tool_input": {"description": "refactor the persona router"}},
            False,
            "delegate work without a Task id",
        ),
        (
            "pre-tool-use",
            {"tool_name": "Task", "tool_input": {"description": f"[{in_progress}] wire the router"}},
            True,
            "delegate work for a planned Task",
        ),
        (
            "exit-plan-mode",
            {"tool_name": "ExitPlanMode", "tool_input": {"plan": "Just refactor things."}},
            False,
            "leave plan mode with no Task ids",
        ),
        ("stop", {}, False, f"end the session while {in_progress} is in progress"),
        ("session-start", {}, True, "session start briefing"),
    ]
    for event, payload, allowed, label in cases:
        decision = evaluate_hook(event, {"cwd": str(s.root), **payload}, s.root)
        ok = (decision.exit_code == 0) == allowed
        verdict = ("allowed" if decision.exit_code == 0 else "denied") + ("" if ok else "  <-- UNEXPECTED")
        s.cover("hooks", f"{event}: {label}", verdict)


# =========================================================================== #
def _clear_readonly(function: Any, path: str, _error: BaseException) -> None:
    os.chmod(path, 0o666)
    function(path)


def write_coverage(s: Scenario, ids: dict[str, str]) -> Path:
    lines = [
        "# AI SDLC capability coverage — DeepTutor SampleRepo",
        "",
        "Generated by `tools/samplerepo_scenario.py`. Every row was produced by the real runner; "
        "people and agents are scripted personas.",
        "",
        "| Intent | Title | Left in state |",
        "|---|---|---|",
        f"| {ids['throttle']} | Login brute-force throttling | completed |",
        f"| {ids['dashboard']} | Dashboard recent activity (compact) | completed |",
        f"| {ids['status']} | Localized capability status | specified — G2 reopened |",
        f"| {ids['deps']} | Restore dependency manifests (compact) | framed — waiting on G1 |",
        f"| {ids['persona']} | Per-learner persona scoping | building — Task in progress |",
        "",
        "| Area | Capability | Proof |",
        "|---|---|---|",
    ]
    lines += [f"| {area} | {capability} | {proof.replace('|', '/')} |" for area, capability, proof in s.coverage]
    unexpected = [row for row in s.coverage if "UNEXPECTED" in row[2] or "→ exit" in row[2]]
    lines += [
        "",
        f"**{len(s.coverage)} checks, {len(unexpected)} failed or unexpected.**",
        "",
        "Defects this scenario found (DEFECT-001 to 004) are fixed; see `docs/samplerepo-e2e-scenario.md`.",
        "",
        "## Known gaps (not exercisable)",
        "",
        "- Mutation testing: `mutation.py` has no CLI entry; convergence only reads a pre-existing `mutation` block.",
        "- `federation`, `attest`, `index rebuild` and `verify-task --authority` appear in the docs but not "
        "in `cli.py`.",
    ]
    path = s.root / ".scenario/COVERAGE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate(source: Path, target: Path, force: bool) -> tuple[Path, int]:
    if not (source / "api").is_dir():
        raise ScenarioError(f"{source} does not look like the DeepTutor SampleRepo")
    if target.resolve() == source.resolve():
        raise ScenarioError("target must differ from the source; the source is never modified")
    if target.exists() and any(target.iterdir()):
        if not force:
            raise ScenarioError(f"{target} is not empty; pass --force to replace it")
        worktrees = target.parent / ".ai-sdlc-worktrees" / target.name
        shutil.rmtree(worktrees, onexc=_clear_readonly) if worktrees.exists() else None
        shutil.rmtree(target, onexc=_clear_readonly)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git"))
    s = Scenario(target.resolve())
    os.chdir(s.root)  # `eval execute` resolves dataset paths against the working directory
    adopt(s)
    ids: dict[str, str] = {}
    before = set(_intent_ids(s))
    login_throttling(s)
    dashboard_bugfix(s)
    localized_status(s)
    dependency_manifests(s)
    created = sorted(set(_intent_ids(s)) - before)
    ids.update(throttle=created[0], dashboard=created[1], status=created[2], deps=created[3])
    persona_scoping(s)
    ids["persona"] = sorted(set(_intent_ids(s)) - before)[-1]
    portfolio_and_assurance(s, ids)
    coverage = write_coverage(s, ids)
    print(f"\nCoverage: {coverage}")
    return s.root, sum(1 for row in s.coverage if "UNEXPECTED" in row[2])


def _intent_ids(s: Scenario) -> list[str]:
    return [path.name.split("-")[0] for path in (s.root / "delivery/intents").glob("I*-*") if path.is_dir()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=str(REPOSITORY.parent / "SampleRepo"))
    parser.add_argument("--target", default=str(REPOSITORY.parent / "SampleRepo-aisdlc"))
    parser.add_argument("--force", action="store_true", help="replace a non-empty target directory")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # the ledger prints arrows; Windows consoles default to cp1252
    try:
        root, unexpected = generate(Path(args.source), Path(args.target), args.force)
    except ScenarioError as error:
        print(f"scenario failed: {error}", file=sys.stderr)
        return 1
    print(root)
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
