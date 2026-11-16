"""Wave S2: interface contracts, threat model, and the requirement-level Gate 2 diff."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from aisdlc.cli import app
from aisdlc.contracts import all_operations, validate_contracts
from aisdlc.evidence import requirement_hashes
from aisdlc.gatepacket import GatePacketService
from aisdlc.specquality import lint_intent
from aisdlc.threatmodel import stride_gaps, threat_model_required, validate_threat_model
from aisdlc.typerlite import CliRunner
from aisdlc.validation import validate_intent
from aisdlc.vendor import yaml

runner = CliRunner()

INTENT = {
    "id": "I001",
    "title": "Orders API",
    "intent_type": "feature",
    "status": "initialized",
    "autonomy_mode": "autonomous",
    "contains_model_behavior": False,
    "artifact_mode": "compact",
    "trigger_flags": {},
    "created_at": "2026-09-24T00:00:00+00:00",
}

OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "Orders", "version": "1"},
    "paths": {
        "/orders": {
            "post": {"operationId": "createOrder", "requestBody": {"$ref": "#/components/requestBodies/Order"}},
            "get": {"operationId": "listOrders"},
        },
        "/orders/{id}": {"delete": {}},
    },
    "components": {
        "requestBodies": {
            "Order": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}}}
        },
        "schemas": {"Order": {"type": "object", "properties": {"sku": {"type": "string"}}}},
    },
}

SPEC = {
    "intent_id": "I001",
    "functional_requirements": [
        {
            "id": "REQ-F-001",
            "statement": "The service shall accept orders.",
            "acceptance_criteria": ["AC-001"],
            "priority": "must",
            "sources": ["brief"],
        },
        {
            "id": "REQ-F-002",
            "statement": "The service shall list orders.",
            "acceptance_criteria": ["AC-002"],
            "priority": "should",
            "sources": ["brief"],
        },
    ],
    "non_functional_requirements": [],
    "acceptance_criteria": [
        {
            "id": "AC-001",
            "verification_class": "D",
            "statement": "Create conforms to the contract.",
            "check": "schemathesis run",
            "contract_operations": ["contracts/orders.yaml#createOrder"],
        },
        {
            "id": "AC-002",
            "verification_class": "D",
            "statement": "List conforms to the contract.",
            "check": "schemathesis run",
            "contract_operations": ["contracts/orders.yaml#listOrders"],
        },
    ],
    "success_criteria": [{"id": "SC-001", "statement": "Orders flow.", "acceptance_criteria": ["AC-001"]}],
    "contracts": [
        {
            "path": "contracts/orders.yaml",
            "excluded_operations": [{"operation": "DELETE /orders/{id}", "reason": "Deletion ships in I002."}],
        }
    ],
}


def _write(root: Path, spec: dict, intent: dict | None = None, contract: dict | None = OPENAPI) -> Path:
    (root / "00-intent").mkdir(parents=True, exist_ok=True)
    (root / "02-specification" / "contracts").mkdir(parents=True, exist_ok=True)
    (root / "00-intent" / "intent.yaml").write_text(yaml.safe_dump(intent or INTENT), encoding="utf-8")
    (root / "02-specification" / "requirements.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    if contract is not None:
        contract_path = root / "02-specification" / "contracts" / "orders.yaml"
        contract_path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    return root


# -- S2.1 contracts ------------------------------------------------------------------------


def test_covered_and_excluded_operations_validate(tmp_path: Path) -> None:
    root = _write(tmp_path / "I001-orders", SPEC)
    assert validate_intent(root, require_plan=False).errors == ()


def test_unnamed_operation_is_addressable_by_method_and_route(tmp_path: Path) -> None:
    root = _write(tmp_path / "I001-orders", SPEC)
    operations, errors = all_operations(root / "02-specification", SPEC)
    assert errors == []
    assert "contracts/orders.yaml#DELETE /orders/{id}" in operations


def test_uncovered_operation_is_an_error(tmp_path: Path) -> None:
    spec = copy.deepcopy(SPEC)
    spec["contracts"][0]["excluded_operations"] = []
    root = _write(tmp_path / "I001-orders", spec)
    errors = validate_contracts(root / "02-specification", spec)
    assert any("DELETE /orders/{id} is covered by no acceptance criterion" in error for error in errors)


def test_unknown_reference_and_double_disposition_are_errors(tmp_path: Path) -> None:
    spec = copy.deepcopy(SPEC)
    spec["acceptance_criteria"][0]["contract_operations"].append("contracts/orders.yaml#cancelOrder")
    spec["contracts"][0]["excluded_operations"].append({"operation": "listOrders", "reason": "later"})
    root = _write(tmp_path / "I001-orders", spec)
    errors = validate_contracts(root / "02-specification", spec)
    assert "AC-001 references unknown contract operation contracts/orders.yaml#cancelOrder" in errors
    assert any("listOrders is both excluded and covered" in error for error in errors)


def test_missing_and_unrecognised_contracts_are_errors(tmp_path: Path) -> None:
    root = _write(tmp_path / "I001-orders", SPEC, contract={"hello": "world"})
    assert any("not a recognised" in error for error in validate_contracts(root / "02-specification", SPEC))
    (root / "02-specification" / "contracts" / "orders.yaml").unlink()
    assert any("does not exist" in error for error in validate_contracts(root / "02-specification", SPEC))


def test_asyncapi_2_and_3_and_json_schema_operations(tmp_path: Path) -> None:
    spec_root = tmp_path / "02-specification"
    (spec_root / "contracts").mkdir(parents=True)
    documents = {
        "contracts/events-v2.yaml": {
            "asyncapi": "2.6.0",
            "channels": {"orders": {"publish": {"operationId": "orderPlaced"}, "subscribe": {}}},
        },
        "contracts/events-v3.yaml": {"asyncapi": "3.0.0", "operations": {"sendOrderPlaced": {"action": "send"}}},
        "contracts/order.schema.json": {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Order"},
    }
    for relative, document in documents.items():
        (spec_root / relative).write_text(json.dumps(document), encoding="utf-8")
    operations, errors = all_operations(spec_root, {"contracts": [{"path": key} for key in documents]})
    assert errors == []
    assert set(operations) == {
        "contracts/events-v2.yaml#orderPlaced",
        "contracts/events-v2.yaml#subscribe orders",
        "contracts/events-v3.yaml#sendOrderPlaced",
        "contracts/order.schema.json#Order",
    }


def test_undeclared_contract_file_is_linted(tmp_path: Path) -> None:
    root = _write(tmp_path / "I001-orders", SPEC)
    (root / "02-specification" / "contracts" / "billing.yaml").write_text("openapi: 3.0.0\n", encoding="utf-8")
    rules = {(item["rule"], item["location"]) for item in lint_intent(root)["findings"]}
    assert ("SQ013", "contracts/billing.yaml") in rules


def test_requirement_hash_is_unchanged_without_contract_references(tmp_path: Path) -> None:
    spec = copy.deepcopy(SPEC)
    for criterion in spec["acceptance_criteria"]:
        del criterion["contract_operations"]
    root = _write(tmp_path / "I001-orders", spec)
    # The pre-S2 formula, verbatim: evidence captured before S2 must not go stale on upgrade.
    requirement = spec["functional_requirements"][0]
    payload = {"requirement": requirement, "criteria": [spec["acceptance_criteria"][0]]}
    expected = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert requirement_hashes(root / "02-specification")["REQ-F-001"] == f"sha256:{expected}"


def test_shared_component_change_moves_only_the_dependent_requirement_hash(tmp_path: Path) -> None:
    root = _write(tmp_path / "I001-orders", SPEC)
    before = requirement_hashes(root / "02-specification")
    changed = copy.deepcopy(OPENAPI)
    changed["components"]["schemas"]["Order"]["properties"]["quantity"] = {"type": "integer"}
    _write(root, SPEC, contract=changed)
    after = requirement_hashes(root / "02-specification")
    assert before["REQ-F-001"] != after["REQ-F-001"]  # createOrder reaches Order through two $refs
    assert before["REQ-F-002"] == after["REQ-F-002"]  # listOrders does not use it


def test_cyclic_reference_does_not_loop(tmp_path: Path) -> None:
    cyclic = copy.deepcopy(OPENAPI)
    cyclic["components"]["schemas"]["Order"]["properties"]["parent"] = {"$ref": "#/components/schemas/Order"}
    root = _write(tmp_path / "I001-orders", SPEC, contract=cyclic)
    assert requirement_hashes(root / "02-specification")["REQ-F-001"].startswith("sha256:")


# -- S2.2 threat model ------------------------------------------------------------------------

THREAT_SPEC = {
    "intent_id": "I001",
    "functional_requirements": [
        {
            "id": "REQ-F-001",
            "statement": "The service shall scope every query to the caller's tenant.",
            "acceptance_criteria": ["AC-001", "AC-002"],
        },
    ],
    "non_functional_requirements": [],
    "acceptance_criteria": [
        {"id": "AC-001", "verification_class": "D", "statement": "Tenant tests pass.", "check": "pytest -k tenant"},
        {
            "id": "AC-002",
            "verification_class": "N",
            "statement": "No cross-tenant read succeeds.",
            "eval": "EVAL-001",
            "dataset": "tenant-isolation",
            "metric": "attack_success",
            "threshold": {"operator": "==", "value": 0},
            "minimum_sample_size": 3,
        },
    ],
    "success_criteria": [{"id": "SC-001", "statement": "Isolated.", "acceptance_criteria": ["AC-002"]}],
}


def _threats(**overrides) -> dict:
    threat = {
        "id": "TM-001",
        "boundary": "TB-001",
        "stride": "information_disclosure",
        "description": "A caller reads another tenant's orders.",
        "disposition": "mitigate",
        "mitigated_by": ["REQ-F-001"],
        "verified_by": ["AC-002"],
    }
    threat.update(overrides)
    return {"intent_id": "I001", "boundaries": [{"id": "TB-001", "name": "Public API"}], "threats": [threat]}


def _threat_root(tmp_path: Path, model: dict | None, intent: dict | None = None) -> Path:
    root = _write(tmp_path / "I001-orders", THREAT_SPEC, intent=intent, contract=None)
    if model is not None:
        (root / "02-specification" / "threat-model.yaml").write_text(yaml.safe_dump(model), encoding="utf-8")
    return root


FULL_TENANT = {**INTENT, "artifact_mode": "full", "trigger_flags": {"multi_tenant": True}}


def test_threat_model_required_only_for_full_risky_intents() -> None:
    assert threat_model_required(FULL_TENANT)
    assert threat_model_required({**INTENT, "artifact_mode": "full", "intent_type": "security"})
    assert not threat_model_required({**FULL_TENANT, "artifact_mode": "compact"})
    assert not threat_model_required({**INTENT, "artifact_mode": "full"})


def test_missing_required_threat_model_is_an_error(tmp_path: Path) -> None:
    root = _threat_root(tmp_path, None, FULL_TENANT)
    errors = validate_threat_model(root, FULL_TENANT, THREAT_SPEC)
    assert any("missing 02-specification/threat-model.yaml" in error for error in errors)


def test_mitigated_threat_with_class_n_is_valid(tmp_path: Path) -> None:
    root = _threat_root(tmp_path, _threats(), FULL_TENANT)
    assert validate_threat_model(root, FULL_TENANT, THREAT_SPEC) == []


def test_mitigation_needs_class_n_not_just_class_d(tmp_path: Path) -> None:
    root = _threat_root(tmp_path, _threats(verified_by=["AC-001"]), FULL_TENANT)
    assert any("no Class N criterion" in e for e in validate_threat_model(root, FULL_TENANT, THREAT_SPEC))


def test_accepted_threat_needs_a_name_and_a_reason(tmp_path: Path) -> None:
    model = _threats(disposition="accept", mitigated_by=[], verified_by=[])
    root = _threat_root(tmp_path, model, FULL_TENANT)
    assert any("without `accepted_by` and `reason`" in e for e in validate_threat_model(root, FULL_TENANT, THREAT_SPEC))
    model["threats"][0].update(accepted_by="ciso", reason="Covered by the gateway WAF.")
    (root / "02-specification" / "threat-model.yaml").write_text(yaml.safe_dump(model), encoding="utf-8")
    assert validate_threat_model(root, FULL_TENANT, THREAT_SPEC) == []


def test_unknown_references_are_errors(tmp_path: Path) -> None:
    root = _threat_root(tmp_path, _threats(boundary="TB-009", mitigated_by=["REQ-F-404"]), FULL_TENANT)
    errors = validate_threat_model(root, FULL_TENANT, THREAT_SPEC)
    assert "TM-001 is on unknown trust boundary TB-009" in errors
    assert "TM-001 is mitigated by unknown requirement REQ-F-404" in errors


def test_standing_check_must_actually_be_inherited(tmp_path: Path) -> None:
    (tmp_path / ".ai-sdlc" / "security").mkdir(parents=True)
    (tmp_path / ".ai-sdlc" / "security" / "standing-checks.yaml").write_text(
        yaml.safe_dump(
            {
                "checks": [
                    {"name": "security/secret-scan", "trigger": None},
                    {"name": "security/static-analysis", "trigger": "has_side_effect"},
                ]
            }
        ),
        encoding="utf-8",
    )
    root = _threat_root(tmp_path, _threats(standing_checks=["security/secret-scan"]), FULL_TENANT)
    assert validate_threat_model(root, FULL_TENANT, THREAT_SPEC) == []
    model = _threats(standing_checks=["security/static-analysis"])  # not inherited: no has_side_effect
    (root / "02-specification" / "threat-model.yaml").write_text(yaml.safe_dump(model), encoding="utf-8")
    assert any("does not inherit" in e for e in validate_threat_model(root, FULL_TENANT, THREAT_SPEC))


def test_stride_silence_is_linted_and_not_applicable_closes_it(tmp_path: Path) -> None:
    model = _threats()
    root = _threat_root(tmp_path, model, FULL_TENANT)
    assert len(stride_gaps(root)["TB-001"]) == 5
    model["boundaries"][0]["not_applicable"] = [
        {"category": category, "reason": "Read-only boundary behind mutual TLS."}
        for category in ("spoofing", "tampering", "repudiation", "denial_of_service", "elevation_of_privilege")
    ]
    (root / "02-specification" / "threat-model.yaml").write_text(yaml.safe_dump(model), encoding="utf-8")
    assert stride_gaps(root) == {}


def test_compact_risky_intent_without_a_model_is_linted(tmp_path: Path) -> None:
    intent = {**INTENT, "trigger_flags": {"has_side_effect": True}}
    root = _threat_root(tmp_path, None, intent)
    assert "SQ015" in {item["rule"] for item in lint_intent(root)["findings"]}


# -- S2.3 requirement-level Gate 2 diff -------------------------------------------------------


def _invoke(tmp_path: Path, *args: str) -> dict:
    result = runner.invoke(app, [*args, "--repo", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_reopened_gate_2_packet_prices_the_change(tmp_path: Path) -> None:
    created = _invoke(
        tmp_path, "new", "--discovery-reason", "fixture", "--title", "Orders", "--type", "feature", "--mode", "compact"
    )
    root = _write(Path(created["path"]), SPEC)
    _invoke(tmp_path, "advance", "I001", "framed")
    _invoke(tmp_path, "gate", "approve", "I001", "G1", "--approver", "human")
    _invoke(tmp_path, "advance", "I001", "specified")
    _invoke(tmp_path, "gate", "approve", "I001", "G2", "--approver", "human")
    assert (tmp_path / ".ai-sdlc" / "runtime" / "gate-snapshots" / "I001-G2.json").is_file()
    # Built work exists by the time a specification is reopened; only its mapping matters here.
    task_dir = root / "04-build" / "I001-P00-compact" / "tasks" / "I001-P00-T001"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        yaml.safe_dump({"id": "I001-P00-T001", "requirements": ["REQ-F-001"]}), encoding="utf-8"
    )

    unchanged = GatePacketService(tmp_path).build(root, "G2")["specification_diff"]
    assert unchanged["requirements"] == {"added": [], "removed": [], "changed": []}

    _invoke(tmp_path, "gate", "reopen", "I001", "G2", "--actor", "human", "--reason", "pricing changed")
    spec = copy.deepcopy(SPEC)
    spec["functional_requirements"][0]["statement"] = "The service shall accept orders with a currency."
    spec["functional_requirements"][1]["priority"] = "wont"
    spec["functional_requirements"].append(
        {"id": "REQ-F-003", "statement": "The service shall quote tax.", "acceptance_criteria": ["AC-001"]}
    )
    _write(root, spec)

    packet = GatePacketService(tmp_path).build(root, "G2")
    change = packet["specification_diff"]
    assert change["requirements"]["added"] == ["REQ-F-003"]
    assert "REQ-F-001" in change["requirements"]["changed"]
    assert change["reprioritised"] == [{"requirement": "REQ-F-002", "from": "should", "to": "wont"}]
    assert change["tasks_losing_evidence"] == [{"task": "I001-P00-T001", "requirements": ["REQ-F-001"]}]
    rendered = GatePacketService.render(packet)
    assert "## Specification changes since Gate 2 was approved" in rendered
    assert "`I001-P00-T001` (REQ-F-001)" in rendered


def test_no_baseline_means_no_diff(tmp_path: Path) -> None:
    created = _invoke(
        tmp_path, "new", "--discovery-reason", "fixture", "--title", "Orders", "--type", "feature", "--mode", "compact"
    )
    root = _write(Path(created["path"]), SPEC)
    assert GatePacketService(tmp_path).build(root, "G2")["specification_diff"] is None
