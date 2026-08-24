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
from app.models.bid_assessment_eventing import BidAuditLog
from app.models.bid_assessment_release import BidEnterpriseBusinessBaseline
from app.schemas.bid_assessment import BidEnterpriseBusinessBaselineCreateIn
from app.services import bid_enterprise_capability as enterprise_service
from app.services.bid_enterprise_business_baseline import (
    BidEnterpriseBusinessBaselineError,
    freeze_enterprise_business_baseline,
    latest_business_snapshot,
    preview_enterprise_business_baseline,
)
from app.services.bid_enterprise_capability import (
    ENTERPRISE_SLOT_CODES,
    freeze_enterprise_snapshot,
)
from app.services.bid_upload_file_storage import LocalBidUploadObjectStorage


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT.parent / "ai-web" / "src"


def _snapshot_record(
    slot_code: str,
    *,
    checked_at: datetime,
    coverage_status: str = "supported",
    valid_to: datetime | None = None,
) -> dict[str, object]:
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
    known = coverage_status in {"supported", "partial"}
    return {
        "slot_code": slot_code,
        "coverage_status": coverage_status,
        "value": values[slot_code] if known else None,
        "source_record_id": f"phase4d1:{slot_code}",
        "source_version": "business-source-v1",
        "source_status": "verified" if known else "unknown",
        "source_label": "企业能力正式台账",
        "valid_from": None,
        "valid_to": valid_to,
        "checked_at": checked_at if known else None,
    }


def _snapshot_command(
    *,
    now: datetime,
    unknown_slots: set[str] | None = None,
    partial_slots: set[str] | None = None,
    valid_to: datetime | None = None,
) -> dict[str, object]:
    unknown_slots = unknown_slots or set()
    partial_slots = partial_slots or set()
    records = []
    for code in ENTERPRISE_SLOT_CODES:
        status = (
            "unknown"
            if code in unknown_slots
            else "partial" if code in partial_slots else "supported"
        )
        records.append(
            _snapshot_record(
                code,
                checked_at=now - timedelta(minutes=1),
                coverage_status=status,
                valid_to=valid_to,
            )
        )
    return {
        "as_of": now - timedelta(minutes=1),
        "change_note": "Phase 4D-1 企业能力候选",
        "records": records,
    }


def _business_command(
    snapshot_id: str,
    *,
    reviewed_as_of: datetime,
    unknown_slots: set[str] | None = None,
    evidence_class: str = "official_document",
) -> dict[str, object]:
    unknown_slots = unknown_slots or set()
    reviews = []
    for code in ENTERPRISE_SLOT_CODES:
        unknown = code in unknown_slots
        reviews.append(
            {
                "slot_code": code,
                "disposition": "confirmed",
                "evidence_class": "not_available" if unknown else evidence_class,
                "evidence_ref": None if unknown else f"enterprise-register:{code}:v1",
                "evidence_hash": (
                    None
                    if unknown or evidence_class == "management_attestation"
                    else (code[-2:] * 32).lower()
                ),
                "note": (
                    "当前没有可用来源，保持 unknown 并跟进"
                    if unknown
                    else "负责人复核，后续补正式系统来源"
                    if evidence_class == "management_attestation"
                    else None
                ),
            }
        )
    return {
        "snapshot_id": snapshot_id,
        "reviewed_as_of": reviewed_as_of,
        "review_note": "逐项复核 I01—I11 的来源、有效期和使用边界",
        "slot_reviews": reviews,
    }


@pytest.fixture()
def phase4d1_runtime(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase4d1.db').as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            BidEnterpriseSnapshot.__table__,
            BidEnterpriseSnapshotRecord.__table__,
            BidEnterpriseBusinessBaseline.__table__,
            BidAuditLog.__table__,
        ],
    )
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    storage = LocalBidUploadObjectStorage(tmp_path / "objects")
    monkeypatch.setattr(
        enterprise_service,
        "settings",
        SimpleNamespace(feature_bid_assessment_phase4_enterprise_capability=True),
    )
    monkeypatch.setattr(
        enterprise_service,
        "get_bid_upload_object_storage",
        lambda: storage,
    )
    try:
        yield session, storage
    finally:
        session.close()
        engine.dispose()


def _freeze_snapshot(session, storage, *, command, now):
    result = freeze_enterprise_snapshot(
        session,
        actor_id=1,
        command=command,
        request_id="phase4d1-snapshot",
        storage=storage,
        now=now,
    )
    session.commit()
    return result.snapshot


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
            "path": "/api/v1/bid-assessment-runtime-lab/enterprise-business-baselines",
            "headers": [],
            "app": app,
            "state": {"trace_id": "phase4d1-test"},
        }
    )


def test_phase4d1_schema_frontend_and_launcher_contract() -> None:
    schema = json.loads(
        (
            ROOT
            / "schemas"
            / "bid_assessment"
            / "v1"
            / "enterprise-business-baseline.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    payload = BidEnterpriseBusinessBaselineCreateIn.model_validate(
        _business_command(
            "enterprise-phase4d1",
            reviewed_as_of=datetime.now(timezone.utc),
        )
    )
    assert [item.slot_code for item in payload.slot_reviews] == list(
        ENTERPRISE_SLOT_CODES
    )
    frontend = (WEB_ROOT / "BidAssessmentRuntimeLab.vue").read_text(encoding="utf-8")
    api = (WEB_ROOT / "bidAssessmentRuntimeLabApi.js").read_text(encoding="utf-8")
    launcher = (
        ROOT / "scripts" / "start_bid_assessment_mvp1_local.ps1"
    ).read_text(encoding="utf-8")
    assert "validateBusinessBaseline" in frontend
    assert "decision-revalidation" in frontend
    assert "X-Enterprise-Business-Candidate-Hash" in api
    assert "[switch]$EnableBusinessBaseline" in launcher
    assert "FEATURE_BID_ASSESSMENT_PHASE4_BUSINESS_BASELINE" in launcher


def test_business_baseline_post_is_hidden_from_non_admin_and_blocked_view_only(
    monkeypatch,
) -> None:
    payload = BidEnterpriseBusinessBaselineCreateIn.model_validate(
        _business_command(
            "enterprise-phase4d1",
            reviewed_as_of=datetime.now(timezone.utc),
        )
    )
    settings = SimpleNamespace(
        feature_bid_assessment_phase4_business_baseline=True,
        feature_bid_assessment_phase4_mvp=True,
        feature_bid_assessment_phase4_plan_continuation=True,
        feature_bid_assessment_phase4_local_agent=True,
        feature_bid_assessment_phase4_evidence_mcp=True,
        feature_bid_assessment_phase4_model_executor=True,
        feature_bid_assessment_phase4_fact_authority=True,
        feature_bid_assessment_phase4_preliminary_report=True,
        feature_bid_assessment_phase4_mvp0_trace=True,
        feature_bid_assessment_phase4_mvp_release_candidate=True,
        feature_bid_assessment_phase4_enterprise_capability=True,
        feature_bid_assessment_v1_runtime=True,
    )
    monkeypatch.setattr(runtime_lab_api, "settings", settings)
    user = SimpleNamespace(id=1)
    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: False)
    hidden = runtime_lab_api.create_runtime_lab_enterprise_business_baseline(
        payload,
        _runtime_request(access_mode="execute"),
        idempotency_key="phase4d1-acl-denied-key",
        candidate_hash="f" * 64,
        current_user=user,
        db=None,
    )
    assert hidden.status_code == 404

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: True)
    blocked = runtime_lab_api.create_runtime_lab_enterprise_business_baseline(
        payload,
        _runtime_request(access_mode="view-only"),
        idempotency_key="phase4d1-view-only-key",
        candidate_hash="f" * 64,
        current_user=user,
        db=None,
    )
    assert blocked.status_code == 403
    assert json.loads(blocked.body)["error"]["code"] == "BID_MVP1_VIEW_ONLY"


def test_business_baseline_validate_is_zero_persistence_and_hash_stable(
    phase4d1_runtime,
) -> None:
    session, storage = phase4d1_runtime
    now = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _freeze_snapshot(
        session,
        storage,
        command=_snapshot_command(now=now),
        now=now,
    )
    command = _business_command(str(snapshot.id), reviewed_as_of=now)
    counts = (
        session.query(BidEnterpriseBusinessBaseline).count(),
        session.query(BidAuditLog).count(),
    )
    first = preview_enterprise_business_baseline(session, actor_id=1, command=command)
    second = preview_enterprise_business_baseline(session, actor_id=1, command=command)
    assert first["candidate_hash"] == second["candidate_hash"]
    assert first["can_freeze"] is True
    assert first["verification_outcome"] == "verified"
    assert counts == (
        session.query(BidEnterpriseBusinessBaseline).count(),
        session.query(BidAuditLog).count(),
    )


def test_business_baseline_freeze_is_immutable_and_candidate_hash_fenced(
    phase4d1_runtime,
) -> None:
    session, storage = phase4d1_runtime
    now = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _freeze_snapshot(
        session,
        storage,
        command=_snapshot_command(now=now),
        now=now,
    )
    command = _business_command(str(snapshot.id), reviewed_as_of=now)
    preview = preview_enterprise_business_baseline(session, actor_id=1, command=command)
    result = freeze_enterprise_business_baseline(
        session,
        actor_id=1,
        command=command,
        request_id="phase4d1-freeze",
        expected_candidate_hash=preview["candidate_hash"],
        now=now,
    )
    session.commit()
    assert result.created is True
    assert result.projection["verification_outcome"] == "verified"
    replay = freeze_enterprise_business_baseline(
        session,
        actor_id=1,
        command=command,
        request_id="phase4d1-replay",
        expected_candidate_hash=preview["candidate_hash"],
        now=now,
    )
    assert replay.created is False
    assert replay.baseline.id == result.baseline.id

    drifted = deepcopy(command)
    drifted["review_note"] = "候选内容已改变"
    with pytest.raises(
        BidEnterpriseBusinessBaselineError,
        match="BID_ENTERPRISE_BUSINESS_CANDIDATE_HASH_MISMATCH",
    ):
        freeze_enterprise_business_baseline(
            session,
            actor_id=1,
            command=drifted,
            request_id="phase4d1-drift",
            expected_candidate_hash=preview["candidate_hash"],
            now=now,
        )


def test_unknown_and_management_attestation_remain_follow_up(
    phase4d1_runtime,
) -> None:
    session, storage = phase4d1_runtime
    now = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _freeze_snapshot(
        session,
        storage,
        command=_snapshot_command(now=now, unknown_slots={"I11"}),
        now=now,
    )
    command = _business_command(
        str(snapshot.id),
        reviewed_as_of=now,
        unknown_slots={"I11"},
        evidence_class="management_attestation",
    )
    preview = preview_enterprise_business_baseline(session, actor_id=1, command=command)
    assert preview["can_freeze"] is True
    assert preview["verification_outcome"] == "verified_with_follow_up"
    assert "I11_BUSINESS_FOLLOW_UP" in preview["follow_up_codes"]


def test_hashed_source_review_time_and_run_validity_fail_closed(
    phase4d1_runtime,
) -> None:
    session, storage = phase4d1_runtime
    now = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _freeze_snapshot(
        session,
        storage,
        command=_snapshot_command(now=now, valid_to=now + timedelta(minutes=2)),
        now=now,
    )
    command = _business_command(str(snapshot.id), reviewed_as_of=now)
    command["slot_reviews"][0]["evidence_hash"] = None
    missing_hash = preview_enterprise_business_baseline(
        session,
        actor_id=1,
        command=command,
    )
    assert "I01_BUSINESS_REVIEW_BLOCKED" in missing_hash["blocking_codes"]

    stale = _business_command(
        str(snapshot.id),
        reviewed_as_of=now - timedelta(minutes=20),
    )
    with pytest.raises(
        BidEnterpriseBusinessBaselineError,
        match="BID_ENTERPRISE_BUSINESS_REVIEW_TIME_STALE",
    ):
        preview_enterprise_business_baseline(session, actor_id=1, command=stale)

    ready = _business_command(str(snapshot.id), reviewed_as_of=now)
    preview = preview_enterprise_business_baseline(session, actor_id=1, command=ready)
    freeze_enterprise_business_baseline(
        session,
        actor_id=1,
        command=ready,
        request_id="phase4d1-validity",
        expected_candidate_hash=preview["candidate_hash"],
        now=now,
    )
    session.commit()
    assert latest_business_snapshot(session, effective_at=now) is not None
    assert latest_business_snapshot(
        session,
        effective_at=now + timedelta(minutes=3),
    ) is None
