from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from starlette.requests import Request

from app.schemas.bid_assessment import BidEnterpriseCapabilitySnapshotCreateIn
from app.api.v1 import bid_assessment_runtime_lab as runtime_lab_api
from app.services import bid_plan_continuation as plan_service
from app.services import bid_mvp1_authority as authority_service
from app.services.bid_enterprise_capability import (
    BidEnterpriseCapabilityError,
    ENTERPRISE_SLOT_CODES,
    _normalize_command_record,
)
from app.services.bid_mvp1_authority import _gate_compare
from app.services.bid_upload_file_storage import LocalBidUploadObjectStorage


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT.parent / "ai-web" / "src"


def _unknown_record(slot_code: str) -> dict[str, object]:
    return {
        "slot_code": slot_code,
        "coverage_status": "unknown",
        "value": None,
        "source_record_id": f"local.{slot_code.lower()}",
        "source_version": "v1",
        "source_status": "unknown",
        "source_label": "本地人工确认",
    }


def _fact(slot: str, value, *, status: str = "supported") -> SimpleNamespace:
    return SimpleNamespace(
        id=f"fact-{slot}",
        fact_slot=slot,
        status=status,
        value_json=value,
    )


def _runtime_request(*, access_mode: str) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            bid_mvp1_access_mode=access_mode,
            bid_mvp1_worker_enabled=access_mode == "execute",
            bid_mvp1_worker_running=access_mode == "execute",
            bid_mvp1_model_calls_enabled=access_mode == "execute",
        )
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/bid-assessment-runtime-lab/enterprise-snapshots",
            "headers": [],
            "app": app,
            "state": {"trace_id": "phase4c1-test"},
        }
    )


def _runtime_settings() -> SimpleNamespace:
    return SimpleNamespace(
        feature_bid_assessment_v1_runtime=True,
        feature_bid_assessment_phase4_mvp=True,
        feature_bid_assessment_phase4_plan_continuation=True,
        feature_bid_assessment_phase4_local_agent=True,
        feature_bid_assessment_phase4_evidence_mcp=True,
        feature_bid_assessment_phase4_model_executor=True,
        feature_bid_assessment_phase4_fact_authority=True,
        feature_bid_assessment_phase4_preliminary_report=True,
        feature_bid_assessment_phase4_mvp0_trace=True,
        feature_bid_assessment_phase4_enterprise_capability=True,
    )


def test_snapshot_command_requires_every_i01_i11_slot_exactly_once() -> None:
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    payload = {
        "as_of": now,
        "change_note": "初始化受治理企业能力快照",
        "records": [_unknown_record(slot) for slot in ENTERPRISE_SLOT_CODES],
    }
    command = BidEnterpriseCapabilitySnapshotCreateIn.model_validate(payload)
    assert [record.slot_code for record in command.records] == list(ENTERPRISE_SLOT_CODES)

    payload["records"][-1] = _unknown_record("I10")
    with pytest.raises(ValidationError, match="every I01-I11 slot exactly once"):
        BidEnterpriseCapabilitySnapshotCreateIn.model_validate(payload)


def test_enterprise_snapshot_post_hides_from_non_admin_and_hard_blocks_view_only(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    payload = BidEnterpriseCapabilitySnapshotCreateIn.model_validate(
        {
            "as_of": now,
            "change_note": "验证 ACL 与 view-only 写阻断",
            "records": [_unknown_record(slot) for slot in ENTERPRISE_SLOT_CODES],
        }
    )
    monkeypatch.setattr(runtime_lab_api, "settings", _runtime_settings())
    user = SimpleNamespace(id=42)

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: False)
    denied = runtime_lab_api.create_runtime_lab_enterprise_snapshot(
        payload,
        _runtime_request(access_mode="execute"),
        idempotency_key="phase4c1-acl-denied-key",
        current_user=user,
        db=None,
    )
    assert denied.status_code == 404
    assert json.loads(denied.body)["error"]["code"] == "BID_RESOURCE_NOT_FOUND"

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: True)
    blocked = runtime_lab_api.create_runtime_lab_enterprise_snapshot(
        payload,
        _runtime_request(access_mode="view-only"),
        idempotency_key="phase4c1-view-only-key",
        current_user=user,
        db=None,
    )
    assert blocked.status_code == 403
    assert json.loads(blocked.body)["error"]["code"] == "BID_MVP1_VIEW_ONLY"


def test_supported_enterprise_record_requires_governed_source_and_validity() -> None:
    as_of = datetime(2026, 8, 17, tzinfo=timezone.utc)
    record = _unknown_record("I01")
    record.update(
        coverage_status="supported",
        value={"legal_name": "旗胜建设有限公司"},
        source_status="unknown",
    )
    with pytest.raises(BidEnterpriseCapabilityError, match="SUPPORTED_SOURCE_UNKNOWN"):
        _normalize_command_record(record, as_of=as_of)

    record["source_status"] = "verified"
    record["valid_from"] = as_of + timedelta(days=2)
    record["valid_to"] = as_of + timedelta(days=1)
    with pytest.raises(BidEnterpriseCapabilityError, match="VALIDITY_INVALID"):
        _normalize_command_record(record, as_of=as_of)


def test_phase4c_plan_adds_deterministic_enterprise_task_without_changing_legacy(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        plan_service,
        "settings",
        SimpleNamespace(feature_bid_assessment_phase4_enterprise_capability=False),
    )
    legacy_p1 = plan_service._stage_map()["P1"]
    assert [definition[1] for definition in legacy_p1.definitions][-1] == (
        "resolve_fact_conflicts"
    )
    assert "build_enterprise_snapshot" not in {
        definition[1] for definition in legacy_p1.definitions
    }
    assert plan_service._active_task_registry().catalog_ref == (
        "task-catalog-1.0.0-draft.1.json"
    )

    monkeypatch.setattr(
        plan_service,
        "settings",
        SimpleNamespace(feature_bid_assessment_phase4_enterprise_capability=True),
    )
    phase4c_p1 = plan_service._stage_map()["P1"]
    definitions = {definition[1]: definition for definition in phase4c_p1.definitions}
    assert definitions["build_enterprise_snapshot"][2] == (
        "mvp.p0.08.extract_guarantees_and_fees",
    )
    assert "mvp.p1.06.build_enterprise_snapshot" in definitions[
        "resolve_fact_conflicts"
    ][2]
    phase4c_registry = plan_service._active_task_registry()
    assert phase4c_registry.catalog_ref == "task-catalog-1.1.0-phase4c1.json"
    assert len(phase4c_registry.task_order) == 49


def test_phase4c_fact_catalog_is_selected_without_mutating_legacy(monkeypatch) -> None:
    monkeypatch.setattr(
        authority_service,
        "settings",
        SimpleNamespace(feature_bid_assessment_phase4_enterprise_capability=False),
    )
    legacy = authority_service.load_mvp1_fact_catalog()
    legacy_slots = {item["slot"] for item in legacy["slots"]}
    assert legacy["version"] == "1.0.0"
    assert "enterprise.qualifications.active_records" not in legacy_slots

    monkeypatch.setattr(
        authority_service,
        "settings",
        SimpleNamespace(feature_bid_assessment_phase4_enterprise_capability=True),
    )
    phase4c = authority_service.load_mvp1_fact_catalog()
    phase4c_slots = {item["slot"] for item in phase4c["slots"]}
    assert phase4c["version"] == "1.1.0"
    assert "enterprise.qualifications.active_records" in phase4c_slots


def test_structured_hard_gates_pass_fail_and_unknown_without_model_judgement() -> None:
    run = SimpleNamespace(evaluation_time=datetime(2026, 8, 17, tzinfo=timezone.utc))
    facts = {
        "tender.qualification.requirements": _fact(
            "tender.qualification.requirements",
            {
                "requirements": [
                    {"requirement_type": "qualification", "code": "建筑装修装饰工程专业承包一级"},
                    {"requirement_type": "safety_license", "name": "安全生产许可证"},
                ]
            },
        ),
        "enterprise.qualifications.active_records": _fact(
            "enterprise.qualifications.active_records",
            {"records": [{"code": "建筑装修装饰工程专业承包一级"}]},
        ),
        "enterprise.safety_license.active_record": _fact(
            "enterprise.safety_license.active_record",
            {"license_no": "粤JZ安许证字-001", "status": "有效"},
        ),
        "tender.guarantee.requirements": _fact(
            "tender.guarantee.requirements",
            {"requirements": [{"amount_cny": 500000, "form": "bank_guarantee"}]},
        ),
        "enterprise.guarantee.capacity": _fact(
            "enterprise.guarantee.capacity",
            {"max_bond_cny": 600000, "supported_forms": ["bank_guarantee"]},
        ),
    }
    status, _, _, summary = _gate_compare("HG02", facts, run)
    assert status == "pass"
    assert summary["comparison_mode"] == "structured_exact_set_v1"

    status, _, _, summary = _gate_compare("HG05", facts, run)
    assert status == "pass"
    assert summary["matched_item_count"] == 1

    facts["enterprise.guarantee.capacity"].value_json["max_bond_cny"] = 100000
    status, _, _, _ = _gate_compare("HG05", facts, run)
    assert status == "fail"

    status, reasons, _, _ = _gate_compare("HG07", {}, run)
    assert status == "unknown"
    assert reasons == ["STRUCTURED_GATE_INPUT_INCOMPLETE"]


def test_hg01_hg03_hg04_hg06_cover_structured_pass_fail_unknown() -> None:
    evaluation_time = datetime(2026, 8, 17, tzinfo=timezone.utc)
    run = SimpleNamespace(evaluation_time=evaluation_time)

    deadline = _fact("tender.submission.deadline", "2026-08-18T00:00:00Z")
    assert _gate_compare("HG01", {deadline.fact_slot: deadline}, run)[0] == "pass"
    deadline.value_json = "2026-08-16T00:00:00Z"
    assert _gate_compare("HG01", {deadline.fact_slot: deadline}, run)[0] == "fail"
    deadline.value_json = "待招标人澄清"
    assert _gate_compare("HG01", {deadline.fact_slot: deadline}, run)[0] == "unknown"

    requirements = _fact(
        "tender.qualification.requirements",
        {
            "requirements": [
                {"requirement_type": "performance", "code": "精装修业绩"},
                {"requirement_type": "personnel", "role": "项目经理"},
            ]
        },
    )
    performance = _fact(
        "enterprise.performance.records",
        {"projects": [{"code": "精装修业绩"}]},
    )
    personnel = _fact(
        "enterprise.personnel.available_records",
        {"people": [{"role": "项目经理"}]},
    )
    facts = {
        requirements.fact_slot: requirements,
        performance.fact_slot: performance,
        personnel.fact_slot: personnel,
    }
    assert _gate_compare("HG03", facts, run)[0] == "pass"
    personnel.value_json = {"people": [{"role": "施工员"}]}
    assert _gate_compare("HG03", facts, run)[0] == "fail"
    personnel.status = "partial"
    assert _gate_compare("HG03", facts, run)[0] == "unknown"

    compliance = _fact("enterprise.compliance.current_records", {"status": "clear"})
    assert _gate_compare("HG04", {compliance.fact_slot: compliance}, run)[0] == "pass"
    compliance.value_json = {"status": "blocked"}
    assert _gate_compare("HG04", {compliance.fact_slot: compliance}, run)[0] == "fail"
    compliance.value_json = {"status": "manual_review"}
    assert _gate_compare("HG04", {compliance.fact_slot: compliance}, run)[0] == "unknown"

    schedule = _fact(
        "tender.schedule.site_constraints",
        {"requirements": [{"required_bid_person_days": 20}]},
    )
    capacity = _fact(
        "enterprise.bid_preparation.capacity",
        {"available_person_days": 30},
    )
    assert _gate_compare(
        "HG06", {schedule.fact_slot: schedule, capacity.fact_slot: capacity}, run
    )[0] == "pass"
    capacity.value_json["available_person_days"] = 10
    assert _gate_compare(
        "HG06", {schedule.fact_slot: schedule, capacity.fact_slot: capacity}, run
    )[0] == "fail"


def test_hg05_not_applicable_and_hg07_exact_counterparty_boundary() -> None:
    run = SimpleNamespace(evaluation_time=datetime(2026, 8, 17, tzinfo=timezone.utc))
    guarantee = _fact(
        "tender.guarantee.requirements",
        {"requirements": [{"not_applicable": True}]},
    )
    capacity = _fact(
        "enterprise.guarantee.capacity",
        {"max_bond_cny": 0, "supported_forms": []},
    )
    status, _, _, _ = _gate_compare(
        "HG05", {guarantee.fact_slot: guarantee, capacity.fact_slot: capacity}, run
    )
    assert status == "not_applicable"

    rules = _fact("enterprise.prohibited_risk.rules", {"rules": []})
    risks = _fact(
        "enterprise.client_risk.current_records",
        {"records": [{"client_name": "某建设集团", "risk_level": "high"}]},
    )
    overview = _fact("tender.overview", {"client_name": "某建设集团"})
    facts = {row.fact_slot: row for row in (rules, risks, overview)}
    assert _gate_compare("HG07", facts, run)[0] == "fail"
    risks.value_json = {"records": []}
    assert _gate_compare("HG07", facts, run)[0] == "pass"
    risks.value_json = {"records": [{"client_name": "某建设集团", "risk_level": "high"}]}
    facts.pop("tender.overview")
    assert _gate_compare("HG07", facts, run)[0] == "unknown"


def test_phase4c_schemas_and_runtime_lab_ui_freeze_local_only_boundary() -> None:
    enterprise_schema = json.loads(
        (ROOT / "schemas" / "bid_assessment" / "v1" / "enterprise-capability.schema.json")
        .read_text(encoding="utf-8")
    )
    preflight_schema = json.loads(
        (ROOT / "schemas" / "bid_assessment" / "v1" / "execute-preflight-v2.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(enterprise_schema)
    Draft202012Validator.check_schema(preflight_schema)
    assert preflight_schema["properties"]["schema"]["const"] == (
        "bid.runtime.execute-preflight.v2"
    )
    frontend = (WEB_ROOT / "BidAssessmentRuntimeLab.vue").read_text(encoding="utf-8")
    api = (WEB_ROOT / "bidAssessmentRuntimeLabApi.js").read_text(encoding="utf-8")
    backend = (
        ROOT / "app" / "api" / "v1" / "bid_assessment_runtime_lab.py"
    ).read_text(encoding="utf-8")
    assert "canConfigureEnterprise" in frontend
    assert "createEnterpriseSnapshot" in frontend
    assert "enterpriseSnapshot:" in api
    assert '"/bid-assessment-runtime-lab/enterprise-snapshots"' in backend
    launcher = (
        ROOT / "scripts" / "start_bid_assessment_mvp1_local.ps1"
    ).read_text(encoding="utf-8")
    assert '$phase4cLab = $AccessMode -eq "execute"' in launcher
    assert "-match '-phase4c(?:1|3)(?:-|$)'" in launcher
    assert "-match '-phase4c3(?:-|$)'" in launcher
    assert 'if ($phase4cLab) { "true" } else { "false" }' in launcher


def test_local_upload_atomic_temp_name_does_not_repeat_long_object_filename(
    tmp_path,
) -> None:
    storage = LocalBidUploadObjectStorage(tmp_path / ("long-phase4c-root-" + "x" * 40))
    content = b"phase4c1-local-upload"
    object_key = (
        "bid-assessment/uploading/v1/2026/08/17/"
        "ee88d63c-18a2-4a2c-bac0-0322e69d738f/"
        "0e930f2e-825f-4783-8d9f-ec9406c95461"
    )
    stored = storage.put(
        stream=BytesIO(content),
        object_key=object_key,
        size_bytes=len(content),
        mime_type="text/plain",
    )
    assert stored.object_key == object_key
    with storage.open_read(object_key=object_key) as stream:
        assert stream.read() == content
