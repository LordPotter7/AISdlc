"""Generate the AISDLC Studio demo repository by driving the real runner.

    python tools/make_demo_repo.py [target-dir] [--force]

Every state transition, refusal, gate record, evidence manifest, and review in the result
was produced by the runner's own commands and services — nothing under `.ai-sdlc/runtime`
is written by hand. What *is* scripted is the people and agents: approvals are given by
named demo personas, and reviews are recorded on behalf of demo reviewer agents. The
generated README says so, so the repository can be shown without being mistaken for a
real project.

Maintenance tool (ADR-008): it lives in `tools/`, not in the runtime.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

from aisdlc.cli import app  # noqa: E402
from aisdlc.pregate import PreGateReviewService  # noqa: E402
from aisdlc.scaffold import InitService  # noqa: E402
from aisdlc.typerlite import CliRunner  # noqa: E402
from aisdlc.vendor import yaml  # noqa: E402

AUTHOR_MODEL = "claude-fable-5-1"
REVIEWER_MODEL = "claude-opus-5-5"
PY = "python"


class DemoError(RuntimeError):
    pass


class Demo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.cli = CliRunner()

    # -- plumbing ------------------------------------------------------------ #
    def run(self, *args: str, expect_failure: bool = False) -> Any:
        result = self.cli.invoke(app, [*args, "--repo", str(self.root)])
        failed = result.exit_code != 0
        if failed != expect_failure:
            raise DemoError(f"`ai-sdlc {' '.join(args)}` exit {result.exit_code}: {result.output}")
        try:
            return json.loads(result.output) if not failed else result.output
        except ValueError:
            return result.output

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-qm", message, "--allow-empty")

    def write(self, relative: str | Path, content: Any) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8", newline="\n")
        else:
            path.write_text(
                yaml.safe_dump(content, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n"
            )
        return path

    def new(self, title: str, kind: str, mode: str) -> tuple[str, Path]:
        created = self.run(
            "new",
            "--title",
            title,
            "--type",
            kind,
            "--mode",
            mode,
            "--discovery-reason",
            "demo repository; greenfield feature, nothing to discover",
        )
        return created["intent_id"], Path(created["path"])

    def set_flags(self, intent_root: Path, **flags: bool) -> None:
        path = intent_root / "00-intent" / "intent.yaml"
        intent = yaml.safe_load(path.read_text(encoding="utf-8"))
        intent["trigger_flags"] = {**(intent.get("trigger_flags") or {}), **flags}
        path.write_text(yaml.safe_dump(intent, sort_keys=False), encoding="utf-8", newline="\n")

    def pre_gate_review(
        self, intent_id: str, intent_root: Path, gate: str, findings: list[dict[str, Any]], decision: str = "approved"
    ) -> None:
        PreGateReviewService(self.root).record(
            intent_root,
            {
                "intent_id": intent_id,
                "gate": gate,
                "reviewer_agent_id": "demo-red-team",
                "reviewer_model": REVIEWER_MODEL,
                "author_model": AUTHOR_MODEL,
                "decision": decision,
                "findings": findings,
                "recorded_at": "2026-09-24T00:00:00+00:00",
            },
        )

    def build_task(self, task_id: str, *, fail_first_with: str | None = None) -> None:
        """start → verify (runner-captured evidence) → independent review → verified."""
        self.run("start-task", task_id)
        if fail_first_with:
            outcome = self.run("verify-task", task_id)
            if outcome["passed"]:
                raise DemoError(f"{task_id} was expected to fail its first attempt")
            # Fixed in the working tree, not committed: convergence checks every Task's
            # evidence against one commit, so evidence is captured at the integration
            # commit and the next commit comes after close.
            self.write(fail_first_with, "# implemented after the first attempt failed\n")
            self.run("start-task", task_id)
        outcome = self.run("verify-task", task_id)
        if not outcome["passed"]:
            raise DemoError(f"{task_id} verification failed: {outcome}")
        review = self.write(
            f".demo/reviews/{task_id}.yaml",
            {
                "reviewer_agent_id": "demo-verifier",
                "reviewer_model": REVIEWER_MODEL,
                "implementer_model": AUTHOR_MODEL,
                "decision": "approved",
                "checks": {"criteria_met": "passed", "scope_respected": "passed"},
                "findings": [],
            },
        )
        self.run(
            "review-task",
            task_id,
            "--review",
            str(review),
            "--evidence",
            outcome["manifest"],
            "--implementer",
            "demo-implementer",
        )


def _task(
    intent_id: str,
    phase: str,
    number: int,
    title: str,
    requirements: list[str],
    criteria: list[str],
    commands: list[str],
    outputs: list[str],
    depends_on: list[str] | None = None,
    resources: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"{phase}-T{number:03d}",
        "intent_id": intent_id,
        "phase_id": phase,
        "title": title,
        "status": "pending",
        "objective": title,
        "requirements": requirements,
        "source_findings": [],
        "depends_on": depends_on or [],
        "inputs": ["Approved specification"],
        "expected_outputs": outputs,
        "parallelizable": False,
        "affected_paths": outputs,
        "shared_resources": resources or [],
        "acceptance_criteria": [{"id": item, "verification_class": "D"} for item in criteria],
        "verification_commands": commands,
        "holdout_commands": [f"{PY} -c \"print('held-out check')\""],
        "holdout_authorship": {"authored_by": "demo-spec-owner", "context": "specification"},
        "retry_limit": 2,
    }


def exists(path: str) -> str:
    return f"{PY} -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('{path}').exists() else 1)\""


ORDERS_V1 = {
    "openapi": "3.1.0",
    "info": {"title": "Orders", "version": "1.0"},
    "paths": {
        "/orders": {"get": {"operationId": "listOrders", "responses": {"200": {"description": "ok"}}}},
        "/exports": {
            "post": {"operationId": "createExport", "responses": {"202": {"description": "accepted"}}},
        },
        "/exports/{id}": {"get": {"operationId": "getExport", "responses": {"200": {"description": "ok"}}}},
    },
}


UNCHANGED_EXPORTS = [
    {"operation": "createExport", "reason": "Shipped by I001; unchanged here."},
    {"operation": "getExport", "reason": "Shipped by I001; unchanged here."},
]


def intent_one(demo: Demo) -> None:
    """Customer export API: the complete lifecycle, including a refusal and a retry."""
    intent_id, root = demo.new("Customer export API", "feature", "full")
    demo.write(
        root / "01-brief" / "brief.md",
        """# Intent Brief: I001 — Customer export API

## Problem

Finance reconciles the ledger monthly by asking support to run SQL against production.
It takes two days, and last quarter a query exported another region's customers.

## Expected outcome

An administrator requests a CSV export of their own customers and downloads it without
involving support.

## Success measures

- 100% of monthly reconciliation exports self-served by administrators (baseline: 0%).
- An export of 100 000 customers completes within 30 seconds at p95.

## Out of scope

- XLSX format (finance has not confirmed it).
""",
    )
    demo.run("advance", intent_id, "framed")
    demo.run("gate", "approve", intent_id, "G1", "--approver", "priya.product")

    spec = {
        "intent_id": intent_id,
        "functional_requirements": [
            {
                "id": "REQ-F-001",
                "priority": "must",
                "sources": ["brief: Expected outcome"],
                "rationale": "Removes support from the monthly reconciliation.",
                "statement": "When an administrator requests an export, the service shall produce a CSV of the "
                "administrator's customers.",
                "acceptance_criteria": ["AC-001"],
            },
            {
                "id": "REQ-F-002",
                "priority": "must",
                "sources": ["brief: Problem"],
                "rationale": "Last quarter's incident exported another region's customers.",
                "statement": "The service shall not include customers outside the administrator's region. TBD: "
                "confirm the region source.",
                "acceptance_criteria": ["AC-002"],
            },
            {
                "id": "REQ-F-003",
                "priority": "wont",
                "sources": ["brief: Out of scope"],
                "rationale": "Deferred until finance confirms the format.",
                "statement": "The service shall export XLSX.",
                "acceptance_criteria": ["AC-004"],
            },
        ],
        "non_functional_requirements": [
            {
                "id": "REQ-NFR-001",
                "priority": "should",
                "category": "performance",
                "sources": ["brief: Success measures"],
                "measure": {"metric": "p95 export duration", "target": 30, "unit": "s", "condition": "100000 rows"},
                "statement": "The service shall complete an export of 100000 customers within 30 seconds at p95.",
                "acceptance_criteria": ["AC-003"],
            },
        ],
        "acceptance_criteria": [
            {
                "id": "AC-001",
                "verification_class": "D",
                "statement": "The export endpoint returns a CSV.",
                "check": exists("src/export.py"),
                "contract_operations": ["contracts/orders.yaml#createExport", "contracts/orders.yaml#getExport"],
            },
            {
                "id": "AC-002",
                "verification_class": "D",
                "statement": "Region filter tests pass.",
                "check": exists("src/region_filter.py"),
            },
            {
                "id": "AC-003",
                "verification_class": "D",
                "statement": "The benchmark stays under 30 s.",
                "check": exists("bench/export_bench.py"),
            },
            {"id": "AC-004", "verification_class": "D", "statement": "An XLSX file opens.", "check": "true"},
        ],
        "success_criteria": [
            {
                "id": "SC-001",
                "statement": "Administrators self-serve reconciliation exports.",
                "brief_measure": "100% of monthly reconciliation exports self-served",
                "acceptance_criteria": ["AC-001"],
            },
            {
                "id": "SC-002",
                "statement": "Exports are fast enough to use during reconciliation.",
                "brief_measure": "100 000 customers within 30 seconds at p95",
                "acceptance_criteria": ["AC-003"],
            },
        ],
        "contracts": [
            {
                "path": "contracts/orders.yaml",
                "api": "orders",
                "excluded_operations": [
                    {"operation": "listOrders", "reason": "Existing operation; unchanged by this Intent."}
                ],
            }
        ],
    }
    demo.write(root / "02-specification" / "contracts" / "orders.yaml", ORDERS_V1)
    demo.write(root / "02-specification" / "requirements.yaml", spec)
    # The draft still carries a TBD: the runner refuses, and the refusal is on the record.
    demo.run("advance", intent_id, "specified", expect_failure=True)
    spec["functional_requirements"][1]["statement"] = (
        "The service shall not include customers outside the administrator's region, taken from the "
        "authenticated session."
    )
    demo.write(root / "02-specification" / "requirements.yaml", spec)
    demo.run("advance", intent_id, "specified")
    demo.pre_gate_review(
        intent_id,
        root,
        "G2",
        [
            {
                "id": "PR-1",
                "severity": "medium",
                "summary": "REQ-F-002 is a prohibition verified only by Class D; consider an adversarial case.",
            },
        ],
    )
    demo.run("gate", "approve", intent_id, "G2", "--approver", "marco.techlead")

    demo.write(
        root / "03-planning" / "master-plan.md",
        "# Master plan: I001\n\n"
        "P00 walking skeleton: export endpoint end to end.\n"
        "P01 region filter and performance.\n",
    )
    demo.write(root / "04-build" / "I001-P00" / "phase.yaml", {"id": "I001-P00", "title": "Walking skeleton"})
    demo.write(root / "04-build" / "I001-P01" / "phase.yaml", {"id": "I001-P01", "title": "Filter and speed"})
    tasks = [
        _task(
            intent_id,
            "I001-P00",
            1,
            "Export endpoint end to end",
            ["REQ-F-001"],
            ["AC-001"],
            [exists("src/export.py")],
            ["src/export.py"],
        ),
        _task(
            intent_id,
            "I001-P01",
            1,
            "Region filter from the session",
            ["REQ-F-002"],
            ["AC-002"],
            [exists("src/region_filter.py")],
            ["src/region_filter.py"],
            depends_on=["I001-P00-T001"],
        ),
        _task(
            intent_id,
            "I001-P01",
            2,
            "Export benchmark",
            ["REQ-NFR-001"],
            ["AC-003"],
            [exists("bench/export_bench.py")],
            ["bench/export_bench.py"],
            depends_on=["I001-P00-T001"],
        ),
    ]
    for task in tasks:
        demo.write(root / "04-build" / task["phase_id"] / "tasks" / task["id"] / "task.yaml", task)
    demo.run("advance", intent_id, "planned")
    demo.pre_gate_review(intent_id, root, "G3", [])
    demo.run("gate", "approve", intent_id, "G3", "--approver", "marco.techlead")
    demo.run("advance", intent_id, "building")

    # The G2 red-team review raised the header-supplied region as a High risk. It is
    # recorded as a finding and promoted to a threat lesson *before* the integration
    # commit: convergence pins every Task's evidence to one commit and close refuses
    # tracked changes after it, so nothing tracked may change once evidence exists.
    demo.write(
        root / "05-verification" / "findings.yaml",
        {
            "findings": [
                {
                    "id": "F-001",
                    "severity": "high",
                    "status": "resolved",
                    "source": "G2 pre-gate review PR-1",
                    "requirement": "REQ-F-002",
                    "description": "The draft design read the region from a request header a caller controls.",
                    "evidence": ["G2 pre-gate review PR-1"],
                    "impact": "A caller could export another region's customers.",
                    "recommended_action": "Take the region from the authenticated session.",
                    "introduced_stage": 2,
                    "detected_stage": 2,
                    "lesson_ref": "LESSON-001",
                },
            ]
        },
    )
    lesson = demo.write(
        ".demo/lessons/LESSON-001.yaml",
        {
            "id": "LESSON-001",
            "source_intent": intent_id,
            "source_findings": ["F-001"],
            "severity_of_source": "high",
            "category": "security",
            "status": "proposed",
            "observation": "Scoping data by a caller-supplied value (header or body) lets a caller read another "
            "scope's records.",
            "impact": "Cross-region or cross-tenant disclosure.",
            "review_after_intents": 5,
            "targets": [
                {
                    "type": "threat",
                    "artifact": ".ai-sdlc/security/standing-threats.yaml",
                    "trigger": "multi_tenant",
                    "enforcement": "blocking",
                    "fires_at": ["stage_2_specification"],
                    "stride": "information_disclosure",
                    "rule": "Tenant or region scope taken from caller-controlled input.",
                }
            ],
        },
    )
    demo.run("lesson", "propose", str(lesson))
    demo.run("lesson", "transition", "LESSON-001", "accepted", "--actor", "marco.techlead")
    demo.run("lesson", "promote", "LESSON-001")

    demo.write("src/export.py", "def export_customers(session):\n    return 'id,name\\n'\n")
    demo.write("bench/export_bench.py", "# p95 benchmark harness\n")
    demo.commit("I001: implementation")
    demo.build_task("I001-P00-T001")
    demo.run("gate", "approve", intent_id, "G4", "--approver", "marco.techlead")
    # The region filter is "forgotten" on the first attempt: verification fails and the
    # runner records a retry before the fixed attempt passes.
    demo.build_task("I001-P01-T001", fail_first_with="src/region_filter.py")
    demo.build_task("I001-P01-T002")

    demo.run("converge", intent_id)

    demo.run("gate", "approve", intent_id, "G5", "--approver", "priya.product")
    demo.run("close", intent_id, "--actor", "priya.product")
    demo.commit("I001: closed")


def intent_two(demo: Demo) -> None:
    """Order search: planned, then Gate 2 reopened with a changed requirement."""
    intent_id, root = demo.new("Order search", "feature", "full")
    demo.write(
        root / "01-brief" / "brief.md",
        "# Intent Brief: I002 — Order search\n\n## Problem\n\n"
        "Support cannot find an order without its id.\n\n## Success measures\n\n"
        "- 90% of support lookups resolved without escalation.\n",
    )
    demo.run("advance", intent_id, "framed")
    demo.run("gate", "approve", intent_id, "G1", "--approver", "priya.product")
    # Builds on the contract I001 shipped: adding an optional query parameter is compatible.
    contract = copy.deepcopy(ORDERS_V1)
    contract["info"]["version"] = "1.1"
    contract["paths"]["/orders"]["get"]["parameters"] = [{"name": "q", "in": "query", "required": False}]
    demo.write(root / "02-specification" / "contracts" / "orders.yaml", contract)
    spec = {
        "intent_id": intent_id,
        "functional_requirements": [
            {
                "id": "REQ-F-001",
                "priority": "must",
                "sources": ["brief: Problem"],
                "statement": "When support searches by customer email, the service shall return that customer's orders.",  # noqa: E501
                "acceptance_criteria": ["AC-001"],
            },
            {
                "id": "REQ-F-002",
                "priority": "should",
                "sources": ["brief: Problem"],
                "statement": "The service shall rank results by order date, newest first.",
                "acceptance_criteria": ["AC-002"],
            },
        ],
        "non_functional_requirements": [
            {
                "id": "REQ-NFR-001",
                "category": "performance",
                "priority": "should",
                "sources": ["brief"],
                "measure": {"metric": "p95 latency", "target": 300, "unit": "ms", "condition": "10 rps"},
                "statement": "Search shall respond within 300 ms at p95.",
                "acceptance_criteria": ["AC-003"],
            },
        ],
        "acceptance_criteria": [
            {
                "id": "AC-001",
                "verification_class": "D",
                "statement": "Email search returns the orders.",
                "check": "pytest -k search_email",
                "contract_operations": ["contracts/orders.yaml#listOrders"],
            },
            {
                "id": "AC-002",
                "verification_class": "D",
                "statement": "Results are newest first.",
                "check": "pytest -k search_rank",
            },
            {
                "id": "AC-003",
                "verification_class": "D",
                "statement": "p95 stays under 300 ms.",
                "check": "pytest -k search_perf",
            },
        ],
        "success_criteria": [
            {"id": "SC-001", "statement": "Support finds orders unaided.", "acceptance_criteria": ["AC-001"]}
        ],
        "contracts": [{"path": "contracts/orders.yaml", "api": "orders", "excluded_operations": UNCHANGED_EXPORTS}],
    }
    demo.write(root / "02-specification" / "requirements.yaml", spec)
    demo.run("advance", intent_id, "specified")
    demo.pre_gate_review(intent_id, root, "G2", [])
    demo.run("gate", "approve", intent_id, "G2", "--approver", "marco.techlead")
    demo.write(root / "03-planning" / "master-plan.md", "# Master plan: I002\n\nP00 email search end to end.\n")
    demo.write(root / "04-build" / "I002-P00" / "phase.yaml", {"id": "I002-P00", "title": "Search skeleton"})
    for task in (
        _task(
            intent_id,
            "I002-P00",
            1,
            "Email search endpoint",
            ["REQ-F-001"],
            ["AC-001"],
            ["pytest -k search_email"],
            ["src/search.py"],
            resources=["contract:orders#listOrders"],
        ),
        _task(
            intent_id,
            "I002-P00",
            2,
            "Ranking",
            ["REQ-F-002"],
            ["AC-002"],
            ["pytest -k search_rank"],
            ["src/ranking.py"],
            depends_on=["I002-P00-T001"],
        ),
        _task(
            intent_id,
            "I002-P00",
            3,
            "Latency budget",
            ["REQ-NFR-001"],
            ["AC-003"],
            ["pytest -k search_perf"],
            ["bench/search_bench.py"],
            depends_on=["I002-P00-T001"],
        ),
    ):
        demo.write(root / "04-build" / task["phase_id"] / "tasks" / task["id"] / "task.yaml", task)
    demo.run("advance", intent_id, "planned")
    # Support asks for relevance ranking instead: the approved specification must change,
    # which is a recorded reopening of Gate 2, not a quiet edit.
    demo.run(
        "gate",
        "reopen",
        intent_id,
        "G2",
        "--actor",
        "priya.product",
        "--reason",
        "Support needs relevance ranking, not date order",
    )
    demo.run("advance", intent_id, "specified")
    spec["functional_requirements"][1]["statement"] = (
        "The service shall rank results by relevance to the query, then by order date."
    )
    spec["functional_requirements"].append(
        {
            "id": "REQ-F-003",
            "priority": "could",
            "sources": ["gate reopen reason"],
            "statement": "The service shall highlight the matched field in each result.",
            "acceptance_criteria": ["AC-004"],
        }
    )
    spec["acceptance_criteria"].append(
        {
            "id": "AC-004",
            "verification_class": "D",
            "statement": "The matched field is marked.",
            "check": "pytest -k highlight",
        }
    )
    demo.write(root / "02-specification" / "requirements.yaml", spec)
    demo.commit("I002: specification reopened")


def intent_three(demo: Demo) -> None:
    """Tenant billing ledger: multi-tenant, inherits I001's threat lesson, building."""
    intent_id, root = demo.new("Tenant billing ledger", "feature", "full")
    demo.set_flags(root, multi_tenant=True)
    demo.write(
        root / "01-brief" / "brief.md",
        "# Intent Brief: I003 — Tenant billing ledger\n\n## Problem\n\n"
        "Each tenant needs its own billing ledger; today one shared table serves all tenants.\n",
    )
    demo.run("advance", intent_id, "framed")
    demo.run("gate", "approve", intent_id, "G1", "--approver", "priya.product")
    demo.write(root / "02-specification" / "contracts" / "orders.yaml", ORDERS_V1)
    spec = {
        "intent_id": intent_id,
        "functional_requirements": [
            {
                "id": "REQ-F-001",
                "priority": "must",
                "sources": ["brief: Problem"],
                "statement": "The service shall record each charge in the ledger of the tenant that incurred it.",
                "acceptance_criteria": ["AC-001"],
            },
            {
                "id": "REQ-F-002",
                "priority": "must",
                "sources": ["LESSON-001"],
                "statement": "The service shall scope every ledger read to the tenant of the authenticated session.",
                "acceptance_criteria": ["AC-002", "AC-003"],
            },
        ],
        "non_functional_requirements": [],
        "acceptance_criteria": [
            {
                "id": "AC-001",
                "verification_class": "D",
                "statement": "Charges land in the right ledger.",
                "check": exists("src/ledger.py"),
                "contract_operations": ["contracts/orders.yaml#listOrders"],
            },
            {
                "id": "AC-002",
                "verification_class": "D",
                "statement": "Tenant scoping tests pass.",
                "check": exists("src/tenant_scope.py"),
            },
            {
                "id": "AC-003",
                "verification_class": "N",
                "statement": "No cross-tenant read succeeds.",
                "eval": "EVAL-001",
                "dataset": "regression/tenant-isolation",
                "metric": "attack_success",
                "threshold": {"operator": "==", "value": 0},
                "minimum_sample_size": 3,
            },
        ],
        "success_criteria": [
            {"id": "SC-001", "statement": "Every tenant sees only its own charges.", "acceptance_criteria": ["AC-003"]}
        ],
        "contracts": [{"path": "contracts/orders.yaml", "api": "orders", "excluded_operations": UNCHANGED_EXPORTS}],
    }
    demo.write(root / "02-specification" / "requirements.yaml", spec)
    demo.write(
        root / "02-specification" / "threat-model.yaml",
        {
            "intent_id": intent_id,
            "boundaries": [
                {
                    "id": "TB-001",
                    "name": "Public API -> ledger service",
                    "not_applicable": [
                        {
                            "category": "repudiation",
                            "reason": "Every ledger write is already in the append-only audit table.",
                        },
                    ],
                }
            ],
            "threats": [
                {
                    "id": "TM-001",
                    "boundary": "TB-001",
                    "stride": "information_disclosure",
                    "description": "A caller supplies another tenant's id and reads its ledger.",
                    "disposition": "mitigate",
                    "inherited_from": "LESSON-001",
                    "mitigated_by": ["REQ-F-002"],
                    "verified_by": ["AC-003"],
                },
                {
                    "id": "TM-002",
                    "boundary": "TB-001",
                    "stride": "spoofing",
                    "description": "A forged session impersonates a tenant administrator.",
                    "disposition": "transfer",
                    "accepted_by": "security.lead",
                    "reason": "Session integrity is owned by the identity platform.",
                },
            ],
        },
    )
    demo.run("advance", intent_id, "specified")
    demo.pre_gate_review(
        intent_id,
        root,
        "G2",
        [
            {"id": "PR-1", "severity": "low", "summary": "TB-001 is silent on tampering and denial of service."},
        ],
    )
    demo.run("gate", "approve", intent_id, "G2", "--approver", "marco.techlead")
    demo.write(
        root / "03-planning" / "master-plan.md", "# Master plan: I003\n\nP00 ledger write path.\nP01 tenant scoping.\n"
    )
    demo.write(root / "04-build" / "I003-P00" / "phase.yaml", {"id": "I003-P00", "title": "Ledger skeleton"})
    demo.write(root / "04-build" / "I003-P01" / "phase.yaml", {"id": "I003-P01", "title": "Tenant scoping"})
    for task in (
        _task(
            intent_id,
            "I003-P00",
            1,
            "Ledger write path",
            ["REQ-F-001"],
            ["AC-001"],
            [exists("src/ledger.py")],
            ["src/ledger.py"],
            resources=["contract:orders#listOrders"],
        ),
        _task(
            intent_id,
            "I003-P01",
            1,
            "Tenant-scoped reads",
            ["REQ-F-002"],
            ["AC-002"],
            [exists("src/tenant_scope.py")],
            ["src/tenant_scope.py"],
            depends_on=["I003-P00-T001"],
        ),
    ):
        demo.write(root / "04-build" / task["phase_id"] / "tasks" / task["id"] / "task.yaml", task)
    demo.run("advance", intent_id, "planned")
    demo.pre_gate_review(intent_id, root, "G3", [])
    demo.run("gate", "approve", intent_id, "G3", "--approver", "marco.techlead")
    demo.run("advance", intent_id, "building")
    demo.write("src/ledger.py", "# per-tenant ledger\n")
    demo.commit("I003: ledger write path")
    demo.build_task("I003-P00-T001")
    demo.run("gate", "approve", intent_id, "G4", "--approver", "marco.techlead")
    demo.run("start-task", "I003-P01-T001")
    demo.run("verify-task", "I003-P01-T001")  # fails: scoping not written yet; recorded as a retry
    demo.commit("I003: in progress")


def intent_four(demo: Demo) -> None:
    intent_id, root = demo.new("Refund notifications", "feature", "compact")
    demo.write(
        root / "01-brief" / "brief.md",
        "# Intent Brief: I004 — Refund notifications\n\n## Problem\n\n"
        "Customers phone support to ask whether a refund was issued.\n\n## Success measures\n\n"
        "- Refund-status calls fall by half within a month.\n",
    )
    demo.run("advance", intent_id, "framed")
    demo.commit("I004: framed")


README = """# orders-service (AISDLC Studio demo)

Generated by `tools/make_demo_repo.py` in the AISDLC repository.

Every lifecycle record under `.ai-sdlc/runtime/` and every evidence manifest was produced
by the real `ai-sdlc` runner. The **people and agents are scripted**: gate approvals come
from demo personas (priya.product, marco.techlead), and pre-gate and Task reviews are
recorded on behalf of demo reviewer agents. Nothing here describes a real project.

| Intent | State | Shows |
|---|---|---|
| I001 Customer export API | completed | refusal at G2 entry, failed first attempt, threat lesson |
| I002 Order search | G2 reopened | requirement diff and the Tasks it would cost |
| I003 Tenant billing ledger | building | inherited threat answered in the threat model, contract contention with I002 |
| I004 Refund notifications | framed | waiting on Gate 1 |
"""


def _clear_readonly(function: Any, path: str, _error: BaseException) -> None:
    """Git marks object files read-only; Windows refuses to delete them until cleared."""
    import os
    import stat

    os.chmod(path, stat.S_IWRITE)
    function(path)


def generate(target: Path, force: bool) -> Path:
    if target.exists() and any(target.iterdir()):
        if not force:
            raise DemoError(f"{target} is not empty; pass --force to replace it")
        shutil.rmtree(target, onexc=_clear_readonly)
    target.mkdir(parents=True, exist_ok=True)
    demo = Demo(target.resolve())
    demo.git("init", "-q", "-b", "main")
    demo.git("config", "user.name", "AISDLC Demo")
    demo.git("config", "user.email", "demo@example.invalid")
    InitService(demo.root).run()
    demo.write("README.md", README)
    demo.write(".gitignore", ".ai-sdlc/runtime/\n.demo/\n")
    demo.commit("Adopt AISDLC")
    intent_one(demo)
    intent_two(demo)
    intent_three(demo)
    intent_four(demo)
    portfolio(demo)
    pipeline(demo)
    return demo.root


PIPELINE = {
    "version": 1,
    "by_type": {"security": "regulated", "investigation_or_poc": "spike"},
    "profiles": {
        "regulated": {
            "description": "Changes an auditor will read about: tenant data, payments, access.",
            "modes": ["full"],
            "required_artifacts": {"specified": ["02-specification/threat-model.yaml"]},
            "specification": {"fail_on": "warning", "rules": {"SQ009": "warning"}},
            "gates": {
                "G2": {"approvals": 2, "approvers": ["lee.architect", "priya.product", "sam.lead"]},
                "G5": {"approvals": 2},
            },
            "verification": {"holdout_waiver": "forbidden"},
        },
        "spike": {
            "description": "Time-boxed investigation. Nothing it builds ships.",
            "modes": ["compact"],
            "specification": {"fail_on": "never"},
            "waivers": [
                {
                    "rule": "specification.fail_on",
                    "approved_by": "sam.lead",
                    "reason": "Spikes are thrown away; findings inform, they do not block",
                    "approved_at": "2026-09-20",
                }
            ],
        },
    },
}


def pipeline(demo: Demo) -> None:
    """Adopted after the first four Intents, as a team would: they keep running `standard`."""
    demo.write(".ai-sdlc/pipeline.yaml", PIPELINE)
    demo.commit("Pipeline profiles: regulated work and spikes")


def portfolio(demo: Demo) -> None:
    """Order search reads the ledger's tenant scope, so it builds after the ledger; the ledger
    leads the portfolio because finance closes the quarter on it."""
    demo.run("program", "new", "--title", "Order platform", "--owner", "sam.lead")
    demo.run("program", "add", "PRG001", "I003")
    demo.run("program", "add", "PRG001", "I002", "--requires", "I003")
    demo.run(
        "intent-priority", "I003", "high", "--actor", "sam.lead", "--reason", "Finance closes the quarter on the ledger"
    )
    demo.run("intent-priority", "I004", "low", "--actor", "sam.lead", "--reason", "No customer has asked yet")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", nargs="?", default=str(Path(tempfile.gettempdir()) / "aisdlc-studio-demo"))
    parser.add_argument("--force", action="store_true", help="replace a non-empty target directory")
    args = parser.parse_args()
    try:
        root = generate(Path(args.target), args.force)
    except DemoError as error:
        print(f"demo generation failed: {error}", file=sys.stderr)
        return 1
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
