"""Wave S3: accessibility carve-out, shared glossary, cross-Intent contracts, threat inheritance."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aisdlc.cli import app
from aisdlc.contractregistry import ContractRegistry
from aisdlc.gatepacket import GatePacketService
from aisdlc.glossary import GlossaryError, promote
from aisdlc.lessons import LessonError, LessonRegistry
from aisdlc.specquality import lint_intent
from aisdlc.state import StateStore
from aisdlc.tasks import IntentResourceCoordinator
from aisdlc.typerlite import CliRunner
from aisdlc.validation import validate_accessibility, validate_intent
from aisdlc.vendor import yaml
from aisdlc.versioning import SCHEMA_VERSION

runner = CliRunner()


def _intent(intent_id: str, **overrides) -> dict:
    return {
        "id": intent_id,
        "title": "x",
        "intent_type": "feature",
        "status": "initialized",
        "autonomy_mode": "autonomous",
        "contains_model_behavior": False,
        "artifact_mode": "compact",
        "trigger_flags": {},
        "created_at": "2026-09-24T00:00:00+00:00",
        **overrides,
    }


def _spec(intent_id: str, **extra) -> dict:
    return {
        "intent_id": intent_id,
        "functional_requirements": [
            {
                "id": "REQ-F-001",
                "statement": "The service shall accept orders.",
                "acceptance_criteria": ["AC-001"],
                "priority": "must",
                "sources": ["brief"],
            },
        ],
        "non_functional_requirements": [],
        "acceptance_criteria": [
            {"id": "AC-001", "verification_class": "D", "statement": "Orders are accepted.", "check": "pytest"},
        ],
        "success_criteria": [{"id": "SC-001", "statement": "Orders flow.", "acceptance_criteria": ["AC-001"]}],
        **extra,
    }


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".ai-sdlc" / "runtime").mkdir(parents=True)
    return tmp_path


def _place(repo: Path, intent_id: str, spec: dict, *, intent: dict | None = None, files: dict | None = None) -> Path:
    root = repo / "delivery" / "intents" / f"{intent_id}-work"
    (root / "00-intent").mkdir(parents=True, exist_ok=True)
    (root / "02-specification").mkdir(parents=True, exist_ok=True)
    (root / "00-intent" / "intent.yaml").write_text(yaml.safe_dump(intent or _intent(intent_id)), encoding="utf-8")
    (root / "02-specification" / "requirements.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    for relative, content in (files or {}).items():
        path = root / "02-specification" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return root


def _statuses(repo: Path, **statuses: str) -> None:
    state = {
        "schema_version": SCHEMA_VERSION,
        "tasks": {},
        "gates": {},
        "intents": {key: {"status": value} for key, value in statuses.items()},
    }
    StateStore(repo).state_path.write_text(json.dumps(state), encoding="utf-8")


def _rules(root: Path) -> set[str]:
    return {item["rule"] for item in lint_intent(root)["findings"]}


# -- S3.1 accessibility carve-out -------------------------------------------------------------

A11Y = {
    "id": "REQ-NFR-001",
    "statement": "The checkout pages shall have zero WCAG 2.2 AA automated violations.",
    "acceptance_criteria": ["AC-002"],
    "category": "accessibility",
    "measure": {"metric": "axe violations", "target": 0},
    "wcag": {"version": "2.2", "level": "AA"},
}
A11Y_CHECK = {
    "id": "AC-002",
    "verification_class": "D",
    "statement": "axe reports no violations.",
    "check": "npx @axe-core/cli http://localhost:3000/checkout --exit",
}


def test_accessibility_requirement_with_wcag_and_class_d_check_is_admitted() -> None:
    spec = _spec("I001", non_functional_requirements=[A11Y])
    spec["acceptance_criteria"].append(A11Y_CHECK)
    assert validate_accessibility(spec) == []


def test_accessibility_requirement_needs_a_wcag_target_and_an_automated_check() -> None:
    requirement = {key: value for key, value in A11Y.items() if key != "wcag"}
    spec = _spec("I001", non_functional_requirements=[requirement])
    spec["acceptance_criteria"].append({**A11Y_CHECK, "verification_class": "J", "check": ""})
    errors = validate_accessibility(spec)
    assert any("no `wcag`" in error for error in errors)
    assert any("no Class D automated check" in error for error in errors)


def test_accessibility_lint_names_the_manual_audit_gap(repo: Path) -> None:
    spec = _spec("I001", non_functional_requirements=[A11Y])
    spec["acceptance_criteria"].append(A11Y_CHECK)
    root = _place(repo, "I001", spec)
    assert "SQ016" in _rules(root)
    spec["non_functional_requirements"][0]["manual_audit"] = {"owner": "accessibility-guild"}
    root = _place(repo, "I001", spec)
    assert "SQ016" not in _rules(root)


# -- S3.2 shared glossary -------------------------------------------------------------------------


def _glossary(repo: Path, terms: list[dict], header: str = "") -> None:
    (repo / ".ai-sdlc" / "glossary.yaml").write_text(
        header + yaml.safe_dump({"version": 1, "terms": terms}), encoding="utf-8"
    )


def test_repository_glossary_term_and_alias_count_as_defined(repo: Path) -> None:
    _glossary(
        repo, [{"term": "responsive", "definition": "p95 under 200 ms", "owner": "platform", "aliases": ["fast"]}]
    )
    spec = _spec("I001")
    spec["functional_requirements"][0]["statement"] = "The service shall be responsive and fast."
    assert "SQ002" not in _rules(_place(repo, "I001", spec))


def test_intent_redefining_a_repository_term_is_flagged(repo: Path) -> None:
    _glossary(repo, [{"term": "active customer", "definition": "Paid in the last 90 days.", "owner": "finance"}])
    spec = _spec("I001", glossary=[{"term": "Active  Customer", "definition": "Logged in this year."}])
    findings = lint_intent(_place(repo, "I001", spec))["findings"]
    assert any(item["rule"] == "SQ017" and "finance" in item["message"] for item in findings)
    same = _spec("I001", glossary=[{"term": "active customer", "definition": "paid in the last  90 days."}])
    assert "SQ017" not in _rules(_place(repo, "I001", same))


def test_promote_adds_an_owned_term_and_keeps_the_header(repo: Path) -> None:
    _glossary(repo, [], header="# Governed by the data council.\n")
    root = _place(repo, "I001", _spec("I001", glossary=[{"term": "order", "definition": "A confirmed basket."}]))
    result = promote(repo, root, "Order", "commerce")
    assert result["status"] == "promoted" and result["source_intent"] == "I001"
    text = (repo / ".ai-sdlc" / "glossary.yaml").read_text(encoding="utf-8")
    assert text.startswith("# Governed by the data council.")
    assert yaml.safe_load(text)["terms"][0]["owner"] == "commerce"
    assert promote(repo, root, "order", "commerce")["status"] == "already_defined"


def test_promote_never_overwrites_a_different_definition(repo: Path) -> None:
    _glossary(repo, [{"term": "order", "definition": "An invoice.", "owner": "finance"}])
    root = _place(repo, "I001", _spec("I001", glossary=[{"term": "order", "definition": "A confirmed basket."}]))
    with pytest.raises(GlossaryError, match="owned by finance"):
        promote(repo, root, "order", "commerce")
    with pytest.raises(GlossaryError, match="does not define"):
        promote(repo, root, "invoice", "commerce")


def test_glossary_promote_cli(repo: Path) -> None:
    _glossary(repo, [])
    _place(repo, "I001", _spec("I001", glossary=[{"term": "order", "definition": "A confirmed basket."}]))
    result = runner.invoke(app, ["glossary", "promote", "I001", "order", "--owner", "commerce", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "promoted"


# -- S3.4 threat inheritance ----------------------------------------------------------------------

THREAT_LESSON = {
    "id": "LESSON-001",
    "source_intent": "I001",
    "source_findings": ["F-001"],
    "severity_of_source": "high",
    "category": "security",
    "status": "proposed",
    "observation": "Tenant id taken from the request body lets a caller read another tenant's data.",
    "impact": "Cross-tenant disclosure.",
    "review_after_intents": 5,
    "targets": [
        {
            "type": "threat",
            "artifact": ".ai-sdlc/security/standing-threats.yaml",
            "trigger": "multi_tenant",
            "enforcement": "blocking",
            "fires_at": ["stage_2_specification"],
            "stride": "information_disclosure",
        }
    ],
}


def _promote_threat(repo: Path) -> LessonRegistry:
    registry = LessonRegistry(repo)
    registry.propose(copy.deepcopy(THREAT_LESSON))
    registry.transition("LESSON-001", "accepted", actor="security-lead")
    registry.promote("LESSON-001")
    return registry


def test_threat_target_needs_a_stride_category_and_stage_2(repo: Path) -> None:
    lesson = copy.deepcopy(THREAT_LESSON)
    del lesson["targets"][0]["stride"]
    with pytest.raises(LessonError, match="STRIDE"):
        LessonRegistry(repo).propose(lesson)
    lesson = copy.deepcopy(THREAT_LESSON)
    lesson["targets"][0]["fires_at"] = ["stage_4_build"]
    with pytest.raises(LessonError, match="stage_2_specification"):
        LessonRegistry(repo).propose(lesson)


def test_promotion_writes_a_readable_threat_register(repo: Path) -> None:
    _promote_threat(repo)
    register = yaml.safe_load((repo / ".ai-sdlc" / "security" / "standing-threats.yaml").read_text(encoding="utf-8"))
    assert register["threats"][0]["lesson_id"] == "LESSON-001"
    assert register["threats"][0]["stride"] == "information_disclosure"


def test_matching_is_side_effect_free_and_inherit_is_not(repo: Path) -> None:
    registry = _promote_threat(repo)
    assert len(registry.matching({"multi_tenant": True}, "stage_2_specification")) == 1
    assert registry.get("LESSON-001").get("fire_count", 0) == 0
    registry.inherit({"multi_tenant": True}, "stage_2_specification", current_intent=2)
    assert registry.get("LESSON-001")["fire_count"] == 1


def _tenant_spec() -> dict:
    spec = _spec("I002")
    spec["acceptance_criteria"].append(
        {
            "id": "AC-002",
            "verification_class": "N",
            "statement": "No cross-tenant read succeeds.",
            "eval": "EVAL-001",
            "dataset": "tenant",
            "metric": "attack_success",
            "threshold": {"operator": "==", "value": 0},
            "minimum_sample_size": 3,
        }
    )
    spec["functional_requirements"][0]["acceptance_criteria"] = ["AC-001", "AC-002"]
    return spec


def _answer(**overrides) -> dict:
    threat = {
        "id": "TM-001",
        "boundary": "TB-001",
        "stride": "information_disclosure",
        "description": "Body-supplied tenant id.",
        "disposition": "mitigate",
        "inherited_from": "LESSON-001",
        "mitigated_by": ["REQ-F-001"],
        "verified_by": ["AC-002"],
        **overrides,
    }
    return {"intent_id": "I002", "boundaries": [{"id": "TB-001", "name": "API"}], "threats": [threat]}


def test_inherited_threat_must_be_answered(repo: Path) -> None:
    _promote_threat(repo)
    tenant = _intent("I002", trigger_flags={"multi_tenant": True})
    root = _place(repo, "I002", _tenant_spec(), intent=tenant)
    errors = validate_intent(root, require_plan=False).errors
    assert any("inherits promoted threat(s) LESSON-001" in error for error in errors)

    # A model that exists but omits the inherited threat — the likeliest real mistake.
    unrelated = _answer(inherited_from=None)
    del unrelated["threats"][0]["inherited_from"]
    root = _place(repo, "I002", _tenant_spec(), intent=tenant, files={"threat-model.yaml": unrelated})
    assert any(
        "inherited threat LESSON-001 (information_disclosure) is not answered" in error
        for error in validate_intent(root, require_plan=False).errors
    )

    root = _place(repo, "I002", _tenant_spec(), intent=tenant, files={"threat-model.yaml": _answer()})
    assert not any("LESSON-001" in error for error in validate_intent(root, require_plan=False).errors)

    root = _place(repo, "I002", _tenant_spec(), intent=tenant, files={"threat-model.yaml": _answer(stride="spoofing")})
    assert any(
        "but the promoted threat is information_disclosure" in error
        for error in validate_intent(root, require_plan=False).errors
    )


def test_declining_an_inherited_threat_is_an_acceptance_by_name(repo: Path) -> None:
    _promote_threat(repo)
    tenant = _intent("I002", trigger_flags={"multi_tenant": True})
    model = _answer(
        disposition="accept",
        mitigated_by=[],
        verified_by=[],
        accepted_by="ciso",
        reason="Tenant id is signed by the gateway for this service.",
    )
    root = _place(repo, "I002", _tenant_spec(), intent=tenant, files={"threat-model.yaml": model})
    assert not any(
        "LESSON-001" in error or "TM-001" in error for error in validate_intent(root, require_plan=False).errors
    )


def test_untriggered_intent_inherits_nothing(repo: Path) -> None:
    _promote_threat(repo)
    root = _place(repo, "I002", _spec("I002"))
    assert validate_intent(root, require_plan=False).errors == ()


# -- S3.3 cross-Intent contract registry -----------------------------------------------------------

V1 = {
    "openapi": "3.1.0",
    "info": {"title": "Orders", "version": "1"},
    "paths": {
        "/orders": {
            "post": {
                "operationId": "createOrder",
                "responses": {
                    "201": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "string"}, "total": {"type": "number"}},
                                }
                            }
                        }
                    },
                    "400": {"description": "bad"},
                },
            }
        },
        "/orders/{id}": {"get": {"operationId": "getOrder"}},
    },
}


def _contract_spec(intent_id: str, *, api: str | None = "orders", accepted: list | None = None) -> dict:
    declaration: dict = {"path": "contracts/orders.yaml"}
    if api:
        declaration["api"] = api
    if accepted:
        declaration["breaking_changes_accepted"] = accepted
    spec = _spec(intent_id, contracts=[declaration])
    spec["acceptance_criteria"][0]["contract_operations"] = [
        "contracts/orders.yaml#createOrder",
        "contracts/orders.yaml#getOrder",
    ]
    return spec


def _v2() -> dict:
    contract = copy.deepcopy(V1)
    post = contract["paths"]["/orders"]["post"]
    post["parameters"] = [{"name": "Idempotency-Key", "in": "header", "required": True}]
    del post["responses"]["201"]["content"]["application/json"]["schema"]["properties"]["total"]
    del post["responses"]["400"]
    return contract


def test_breaking_changes_against_the_last_completed_intent(repo: Path) -> None:
    _place(repo, "I001", _contract_spec("I001"), files={"contracts/orders.yaml": V1})
    root = _place(repo, "I002", _contract_spec("I002"), files={"contracts/orders.yaml": _v2()})
    _statuses(repo, I001="completed", I002="specified")
    changes = {item["change"] for item in ContractRegistry(repo).breaking_changes(root, "I002")}
    assert changes == {
        "new required header parameter `Idempotency-Key`",
        "response 201 no longer returns `total`",
        "response 400 removed",
    }
    errors = validate_intent(root, require_plan=False).errors
    assert sum("against I001" in error for error in errors) == 3


def test_removed_operation_is_breaking_and_acceptance_clears_it(repo: Path) -> None:
    v2 = copy.deepcopy(V1)
    del v2["paths"]["/orders/{id}"]
    _place(repo, "I001", _contract_spec("I001"), files={"contracts/orders.yaml": V1})
    spec = _contract_spec("I002")
    spec["acceptance_criteria"][0]["contract_operations"] = ["contracts/orders.yaml#createOrder"]
    root = _place(repo, "I002", spec, files={"contracts/orders.yaml": v2})
    _statuses(repo, I001="completed", I002="specified")
    assert any("getOrder: operation removed" in error for error in validate_intent(root, require_plan=False).errors)

    spec["contracts"][0]["breaking_changes_accepted"] = [
        {"operation": "getOrder", "accepted_by": "api-owner", "reason": "Replaced by search in I002."}
    ]
    root = _place(repo, "I002", spec, files={"contracts/orders.yaml": v2})
    assert not any("operation removed" in error for error in validate_intent(root, require_plan=False).errors)


def test_no_completed_baseline_means_no_breaking_change(repo: Path) -> None:
    _place(repo, "I001", _contract_spec("I001"), files={"contracts/orders.yaml": V1})
    root = _place(repo, "I002", _contract_spec("I002"), files={"contracts/orders.yaml": _v2()})
    _statuses(repo, I001="building", I002="specified")
    assert ContractRegistry(repo).breaking_changes(root, "I002") == []


def test_concurrent_change_is_contention_until_coapproved(repo: Path) -> None:
    _place(repo, "I002", _contract_spec("I002"), files={"contracts/orders.yaml": V1})
    root = _place(repo, "I003", _contract_spec("I003"), files={"contracts/orders.yaml": V1})
    _statuses(repo, I002="building", I003="specified")
    resources = {item["resource"] for item in ContractRegistry(repo).contention("I003")}
    assert resources == {"contract:orders#createOrder", "contract:orders#getOrder"}

    outstanding = GatePacketService(repo).build(root, "G2")["outstanding"]
    assert any(item.startswith("contract:orders#createOrder is also covered by I002") for item in outstanding)

    IntentResourceCoordinator(repo).coapprove(
        "contract:orders#createOrder", ["I002", "I003"], approver="api-owner", reason="Additive fields only."
    )
    assert {item["resource"] for item in ContractRegistry(repo).contention("I003")} == {"contract:orders#getOrder"}


def test_contract_without_api_identity_is_invisible_and_linted(repo: Path) -> None:
    _place(repo, "I002", _contract_spec("I002", api=None), files={"contracts/orders.yaml": V1})
    root = _place(repo, "I003", _contract_spec("I003"), files={"contracts/orders.yaml": V1})
    _statuses(repo, I002="building", I003="specified")
    assert ContractRegistry(repo).contention("I003") == []
    other = repo / "delivery" / "intents" / "I002-work"
    assert "SQ018" in _rules(other)
    assert "SQ018" not in _rules(root)


def test_contract_registry_cli(repo: Path) -> None:
    _place(repo, "I001", _contract_spec("I001"), files={"contracts/orders.yaml": V1})
    _place(repo, "I002", _contract_spec("I002"), files={"contracts/orders.yaml": _v2()})
    _statuses(repo, I001="completed", I002="specified")
    result = runner.invoke(app, ["contract-registry", "I002", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["requires_decision"] is True and len(payload["breaking_changes"]) == 3
