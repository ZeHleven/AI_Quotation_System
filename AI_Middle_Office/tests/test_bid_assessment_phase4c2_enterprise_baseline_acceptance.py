from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api.v1 import bid_assessment_runtime_lab as runtime_lab_api
from app.core.database import Base
from app.models.bid_assessment_config import (
    BidEnterpriseSnapshot,
    BidEnterpriseSnapshotRecord,
)
from app.models.bid_assessment_eventing import BidAuditLog, BidIdempotencyRecord
from app.schemas.bid_assessment import BidEnterpriseCapabilitySnapshotCreateIn
from app.services import bid_enterprise_capability as enterprise_service
from app.services.bid_enterprise_capability import (
    BidEnterpriseCapabilityError,
    ENTERPRISE_SLOT_CODES,
    _baseline_diff_hash,
    _effective_coverage,
    _prepare_snapshot_candidate,
    freeze_enterprise_snapshot,
)
from app.services.bid_mvp1_authority import _gate_acceptance_projection
from app.services.bid_upload_file_storage import LocalBidUploadObjectStorage


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT.parent / "ai-web" / "src"


def _record(slot_code: str, *, supported: bool = False) -> dict[str, object]:
    values = {
        "I01": {"legal_name": "旗胜建设有限公司"},
        "I02": {"records": [{"code": "装修一级"}]},
        "I03": {"license_no": "粤JZ-001", "status": "active"},
        "I04": {"projects": [{"code": "商业精装修"}]},
        "I05": {"people": [{"role": "项目经理"}]},
        "I06": {"available_cash_cny": 1000000},
        "I07": {"max_bond_cny": 500000, "supported_forms": ["bank_guarantee"]},
        "I08": {"available_person_days": 30},
        "I09": {"rules": []},
        "I10": {"status": "clear"},
        "I11": {"records": []},
    }
    return {
        "slot_code": slot_code,
        "coverage_status": "supported" if supported else "unknown",
        "value": values[slot_code] if supported else None,
        "source_record_id": f"phase4c2:{slot_code}",
        "source_version": "verified-v1",
        "source_status": "verified" if supported else "unknown",
        "source_label": "企业能力台账负责人复核",
        "valid_from": None,
        "valid_to": None,
        "checked_at": (
            datetime(2026, 8, 17, 8, tzinfo=timezone.utc)
            if supported
            else None
        ),
    }


def _command(*, supported_slots: set[str]) -> dict[str, object]:
    return {
        "as_of": datetime(2026, 8, 17, 8, tzinfo=timezone.utc),
        "change_note": "Phase 4C-2 企业能力基线",
        "records": [
            _record(slot_code, supported=slot_code in supported_slots)
            for slot_code in ENTERPRISE_SLOT_CODES
        ],
    }


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


def _runtime_request(*, access_mode: str, path: str) -> Request:
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
            "path": path,
            "headers": [],
            "app": app,
            "state": {"trace_id": "phase4c2-test"},
        }
    )


@pytest.fixture()
def enterprise_runtime(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase4c2.db').as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            BidEnterpriseSnapshot.__table__,
            BidEnterpriseSnapshotRecord.__table__,
            BidAuditLog.__table__,
            BidIdempotencyRecord.__table__,
        ],
    )
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    storage = LocalBidUploadObjectStorage(tmp_path / "objects")
    monkeypatch.setattr(enterprise_service, "settings", _runtime_settings())
    monkeypatch.setattr(
        enterprise_service,
        "get_bid_upload_object_storage",
        lambda: storage,
    )
    monkeypatch.setattr(runtime_lab_api, "settings", _runtime_settings())
    try:
        yield session, storage
    finally:
        session.close()
        engine.dispose()


def test_candidate_hash_is_stable_and_changes_with_governed_input() -> None:
    now = datetime(2026, 8, 17, 9, tzinfo=timezone.utc)
    command = _command(supported_slots=set(ENTERPRISE_SLOT_CODES))
    first = _prepare_snapshot_candidate(command, current_time=now)
    second = _prepare_snapshot_candidate(command, current_time=now + timedelta(minutes=1))
    assert first[3] == second[3]

    later_command = _command(supported_slots=set(ENTERPRISE_SLOT_CODES))
    later_command["as_of"] = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)
    for record in later_command["records"]:
        record["checked_at"] = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)
    later = _prepare_snapshot_candidate(
        later_command,
        current_time=datetime(2026, 8, 18, 9, tzinfo=timezone.utc),
    )
    assert later[3] != first[3]
    assert _baseline_diff_hash(later[1][0]) == _baseline_diff_hash(first[1][0])

    command["records"][5]["value"]["available_cash_cny"] = 900000
    changed = _prepare_snapshot_candidate(command, current_time=now)
    assert changed[3] != first[3]


def test_candidate_hash_header_fences_form_drift_before_any_write(monkeypatch) -> None:
    monkeypatch.setattr(
        enterprise_service,
        "settings",
        SimpleNamespace(feature_bid_assessment_phase4_enterprise_capability=True),
    )
    now = datetime(2026, 8, 17, 9, tzinfo=timezone.utc)
    with pytest.raises(
        BidEnterpriseCapabilityError,
        match="BID_ENTERPRISE_CANDIDATE_HASH_MISMATCH",
    ):
        freeze_enterprise_snapshot(
            SimpleNamespace(),
            actor_id=1,
            command=_command(supported_slots=set(ENTERPRISE_SLOT_CODES)),
            request_id="phase4c2-candidate-fence",
            now=now,
            expected_snapshot_hash="0" * 64,
        )


def test_effective_coverage_never_promotes_partial_or_expired_to_ready() -> None:
    as_of = datetime(2026, 8, 17, 9, tzinfo=timezone.utc)
    partial = {
        "coverage_status": "partial",
        "provenance": {"source_status": "verified", "valid_from": None, "valid_to": None},
    }
    assert _effective_coverage(partial, as_of=as_of)[0] == "partial"

    expired = {
        "coverage_status": "supported",
        "provenance": {
            "source_status": "verified",
            "valid_from": None,
            "valid_to": "2026-08-16T09:00:00Z",
        },
    }
    assert _effective_coverage(expired, as_of=as_of)[0] == "expired"

    self_reported = {
        "coverage_status": "supported",
        "provenance": {"source_status": "self_reported", "valid_from": None, "valid_to": None},
    }
    assert "ENTERPRISE_SOURCE_SELF_REPORTED" in _effective_coverage(
        self_reported,
        as_of=as_of,
    )[1]


def test_hard_gate_acceptance_explains_missing_enterprise_and_tender_inputs() -> None:
    projection = _gate_acceptance_projection("HG05", status="unknown", facts={})
    assert projection["enterprise_slot_codes"] == ["I06", "I07"]
    assert "enterprise.guarantee.capacity" in projection["unresolved_fact_slots"]
    assert any("企业能力" in action for action in projection["next_actions"])
    assert any("招标资料" in action for action in projection["next_actions"])


def test_baseline_preview_is_zero_persistence_and_reports_business_diff(
    enterprise_runtime,
) -> None:
    session, storage = enterprise_runtime
    now = datetime(2026, 8, 17, 9, tzinfo=timezone.utc)
    original = _command(supported_slots=set(ENTERPRISE_SLOT_CODES))
    frozen = freeze_enterprise_snapshot(
        session,
        actor_id=1,
        command=original,
        request_id="phase4c2-original",
        storage=storage,
        now=now,
    )
    session.commit()
    assert frozen.created is True

    counts_before = (
        session.query(BidEnterpriseSnapshot).count(),
        session.query(BidEnterpriseSnapshotRecord).count(),
        session.query(BidAuditLog).count(),
    )
    files_before = sorted(
        (path.relative_to(storage._root).as_posix(), path.read_bytes())
        for path in storage._root.rglob("*")
        if path.is_file()
    )
    candidate = deepcopy(original)
    candidate["records"][5]["value"]["available_cash_cny"] = 900000
    preview = enterprise_service.preview_enterprise_baseline(
        session,
        command=candidate,
        storage=storage,
        now=now,
    )
    assert preview["schema"] == "bid.enterprise.baseline-validation.v1"
    assert preview["changed_slot_count"] == 1
    assert preview["no_change"] is False
    assert [
        item["slot_code"] for item in preview["slots"]
        if item["change_type"] == "changed"
    ] == ["I06"]
    assert preview["coverage_counts"]["supported"] == 11
    assert preview["acceptance_ready"] is True
    assert all(
        gate["status"] in {"ready", "deferred_tender"}
        for gate in preview["hard_gate_readiness"]
    )
    assert counts_before == (
        session.query(BidEnterpriseSnapshot).count(),
        session.query(BidEnterpriseSnapshotRecord).count(),
        session.query(BidAuditLog).count(),
    )
    files_after = sorted(
        (path.relative_to(storage._root).as_posix(), path.read_bytes())
        for path in storage._root.rglob("*")
        if path.is_file()
    )
    assert files_after == files_before


def test_baseline_preview_source_partial_unknown_and_validity_matrix(
    enterprise_runtime,
) -> None:
    session, storage = enterprise_runtime
    now = datetime(2026, 8, 17, 9, tzinfo=timezone.utc)
    candidate = _command(supported_slots=set(ENTERPRISE_SLOT_CODES))
    candidate["records"][1]["coverage_status"] = "partial"
    candidate["records"][2] = _record("I03", supported=False)
    candidate["records"][4]["valid_to"] = datetime(
        2026, 8, 16, 9, tzinfo=timezone.utc
    )
    candidate["records"][5]["valid_from"] = datetime(
        2026, 8, 18, 9, tzinfo=timezone.utc
    )
    preview = enterprise_service.preview_enterprise_baseline(
        session,
        command=candidate,
        storage=storage,
        now=now,
    )
    assert preview["coverage_counts"] == {
        "supported": 7,
        "partial": 1,
        "unknown": 1,
        "not_yet_valid": 1,
        "expired": 1,
    }
    assert preview["can_freeze"] is True
    assert preview["acceptance_ready"] is False
    gate_map = {item["gate_code"]: item for item in preview["hard_gate_readiness"]}
    assert gate_map["HG02"]["unresolved_slot_codes"] == ["I02", "I03"]
    assert gate_map["HG03"]["unresolved_slot_codes"] == ["I05"]
    assert gate_map["HG05"]["unresolved_slot_codes"] == ["I06"]

    self_reported = _command(supported_slots=set(ENTERPRISE_SLOT_CODES))
    for record in self_reported["records"]:
        record["source_status"] = "self_reported"
    self_reported_preview = enterprise_service.preview_enterprise_baseline(
        session,
        command=self_reported,
        storage=storage,
        now=now,
    )
    assert self_reported_preview["coverage_counts"]["supported"] == 11
    assert self_reported_preview["acceptance_ready"] is False
    assert all(
        item["validation_status"] == "review_required"
        for item in self_reported_preview["slots"]
    )


def test_runtime_api_candidate_hash_acl_view_only_and_idempotency(
    enterprise_runtime,
    monkeypatch,
) -> None:
    session, _storage = enterprise_runtime
    api_time = datetime.now(timezone.utc) - timedelta(minutes=1)
    api_command = _command(supported_slots=set(ENTERPRISE_SLOT_CODES))
    api_command["as_of"] = api_time
    for record in api_command["records"]:
        record["checked_at"] = api_time
    payload = BidEnterpriseCapabilitySnapshotCreateIn.model_validate(
        api_command
    )
    user = SimpleNamespace(id=42)
    validate_request = _runtime_request(
        access_mode="execute",
        path="/api/v1/bid-assessment-runtime-lab/enterprise-baseline/validate",
    )

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: False)
    hidden = runtime_lab_api.validate_runtime_lab_enterprise_baseline(
        payload,
        validate_request,
        current_user=user,
        db=session,
    )
    assert hidden.status_code == 404

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: True)
    blocked = runtime_lab_api.validate_runtime_lab_enterprise_baseline(
        payload,
        _runtime_request(
            access_mode="view-only",
            path="/api/v1/bid-assessment-runtime-lab/enterprise-baseline/validate",
        ),
        current_user=user,
        db=session,
    )
    assert blocked.status_code == 403
    assert json.loads(blocked.body)["error"]["code"] == "BID_MVP1_VIEW_ONLY"

    validated = runtime_lab_api.validate_runtime_lab_enterprise_baseline(
        payload,
        validate_request,
        current_user=user,
        db=session,
    )
    candidate_hash = validated["data"]["candidate_snapshot_hash"]
    assert session.query(BidEnterpriseSnapshot).count() == 0

    create_request = _runtime_request(
        access_mode="execute",
        path="/api/v1/bid-assessment-runtime-lab/enterprise-snapshots",
    )
    first = runtime_lab_api.create_runtime_lab_enterprise_snapshot(
        payload,
        create_request,
        idempotency_key="phase4c2-candidate-freeze-key",
        candidate_hash=candidate_hash,
        current_user=user,
        db=session,
    )
    assert first.status_code == 201
    assert first.headers["idempotency-replayed"] == "false"
    assert json.loads(first.body)["data"]["snapshot_hash"] == candidate_hash

    replay = runtime_lab_api.create_runtime_lab_enterprise_snapshot(
        payload,
        create_request,
        idempotency_key="phase4c2-candidate-freeze-key",
        candidate_hash=candidate_hash,
        current_user=user,
        db=session,
    )
    assert replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"
    assert session.query(BidEnterpriseSnapshot).count() == 1

    reused = runtime_lab_api.create_runtime_lab_enterprise_snapshot(
        payload,
        create_request,
        idempotency_key="phase4c2-candidate-freeze-key",
        candidate_hash="f" * 64,
        current_user=user,
        db=session,
    )
    assert reused.status_code == 409
    assert json.loads(reused.body)["error"]["code"] == "BID_IDEMPOTENCY_KEY_REUSED"

    drift = runtime_lab_api.create_runtime_lab_enterprise_snapshot(
        payload,
        create_request,
        idempotency_key="phase4c2-candidate-drift-key",
        candidate_hash="e" * 64,
        current_user=user,
        db=session,
    )
    assert drift.status_code == 409
    assert json.loads(drift.body)["error"]["code"] == (
        "BID_ENTERPRISE_CANDIDATE_HASH_MISMATCH"
    )
    assert session.query(BidEnterpriseSnapshot).count() == 1


def test_phase4c2_contract_schema_and_ui_freeze_validation_before_commit() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "bid_assessment" / "v1" / "enterprise-capability.schema.json")
        .read_text(encoding="utf-8")
    )
    profile = json.loads(
        (ROOT / "contracts" / "bid_assessment" / "v1" / "phase4c2-enterprise-baseline-acceptance-profile.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    assert profile["baseline_validation"]["persistence"] == "none"
    assert profile["compatibility"]["new_database_migration_required"] is False
    assert profile["compatibility"]["alembic_head"] == "20260817_0104"

    frontend = (WEB_ROOT / "BidAssessmentRuntimeLab.vue").read_text(encoding="utf-8")
    api = (WEB_ROOT / "bidAssessmentRuntimeLabApi.js").read_text(encoding="utf-8")
    backend = (
        ROOT / "app" / "api" / "v1" / "bid_assessment_runtime_lab.py"
    ).read_text(encoding="utf-8")
    assert "validateEnterpriseBaseline" in frontend
    assert "X-Enterprise-Candidate-Hash" in api
    assert '"/bid-assessment-runtime-lab/enterprise-baseline/validate"' in backend
