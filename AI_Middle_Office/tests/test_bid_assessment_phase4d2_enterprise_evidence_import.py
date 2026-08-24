from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
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
from app.models.bid_assessment_release import (
    BidEnterpriseBusinessBaseline,
    BidEnterpriseEvidenceItem,
    BidEnterpriseEvidencePackage,
    BidEnterpriseEvidencePackageItem,
)
from app.schemas.bid_assessment import BidEnterpriseEvidencePackageCreateIn
from app.services import (
    bid_enterprise_business_baseline as business_baseline_service,
    bid_enterprise_capability as enterprise_service,
)
from app.services.bid_enterprise_business_baseline import (
    freeze_enterprise_business_baseline,
    latest_business_snapshot,
    preview_enterprise_business_baseline,
)
from app.services.bid_enterprise_capability import ENTERPRISE_SLOT_CODES
from app.services.bid_enterprise_capability import freeze_enterprise_snapshot
from app.services.bid_enterprise_evidence_import import (
    BidEnterpriseEvidenceImportError,
    freeze_enterprise_evidence_package,
    import_enterprise_evidence_item,
    preview_enterprise_evidence_package,
    project_enterprise_evidence_package,
    validate_package_item_reference,
)
from app.services.bid_upload_file_storage import LocalBidUploadObjectStorage


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def phase4d2_runtime(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase4d2.db').as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            BidEnterpriseEvidenceItem.__table__,
            BidEnterpriseEvidencePackage.__table__,
            BidEnterpriseEvidencePackageItem.__table__,
            BidEnterpriseSnapshot.__table__,
            BidEnterpriseSnapshotRecord.__table__,
            BidEnterpriseBusinessBaseline.__table__,
            BidAuditLog.__table__,
            BidIdempotencyRecord.__table__,
        ],
    )
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session, LocalBidUploadObjectStorage(tmp_path / "objects")
    finally:
        session.close()
        engine.dispose()


def _inspection(content: bytes, *, filename: str = "企业资质.pdf") -> SimpleNamespace:
    import hashlib

    return SimpleNamespace(
        filename=filename,
        canonical_mime_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _import_item(
    session,
    storage,
    *,
    now: datetime,
    filename: str = "企业资质.pdf",
    source_record_id: str = "enterprise-register-I02",
):
    content = b"%PDF-1.7\nphase4d2-enterprise-evidence\n%%EOF"
    return import_enterprise_evidence_item(
        session,
        actor_id=1,
        command={
            "evidence_class": "official_document",
            "source_record_id": source_record_id,
            "source_version": "2026-08-17-v1",
            "source_label": "企业资质正式台账",
            "valid_from": now - timedelta(days=1),
            "valid_to": now + timedelta(days=30),
        },
        file_stream=BytesIO(content),
        inspection=_inspection(content, filename=filename),
        request_id="phase4d2-import",
        storage=storage,
        now=now,
    )


def _package_command(
    item_id: str | None,
    *,
    as_of: datetime,
    mapped_slot: str = "I02",
) -> dict[str, object]:
    return {
        "package_label": "企业能力资料基线 2026-08",
        "as_of": as_of,
        "change_note": "首次纳入真实企业能力来源，未覆盖槽位保持 unknown",
        "slots": [
            {
                "slot_code": code,
                "evidence_item_ids": [item_id] if code == mapped_slot and item_id else [],
                "note": (
                    "人工确认该文件支持资质槽位"
                    if code == mapped_slot and item_id
                    else "当前未取得权威资料，保持 unknown"
                ),
            }
            for code in ENTERPRISE_SLOT_CODES
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
        feature_bid_assessment_phase4_mvp_release_candidate=True,
        feature_bid_assessment_phase4_business_baseline=True,
        feature_bid_assessment_phase4_enterprise_evidence_import=True,
    )


def _runtime_request(*, access_mode: str, path: str) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            bid_mvp1_access_mode=access_mode,
            bid_mvp1_worker_enabled=access_mode == "execute",
            bid_mvp1_worker_running=access_mode == "execute",
            bid_mvp1_model_calls_enabled=False,
        )
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "app": app,
            "state": {"trace_id": "phase4d2-test"},
        }
    )


def _snapshot_command(*, now: datetime) -> dict[str, object]:
    supported_values = {
        "I01": {"legal_name": "旗胜建设有限公司"},
        "I02": {"records": [{"code": "装修一级"}]},
    }
    return {
        "as_of": now - timedelta(minutes=1),
        "change_note": "Phase 4D-2 真实来源基线",
        "records": [
            {
                "slot_code": code,
                "coverage_status": "supported" if code in supported_values else "unknown",
                "value": supported_values.get(code),
                "source_record_id": f"phase4d2:{code}",
                "source_version": "business-source-v1",
                "source_status": "verified" if code in supported_values else "unknown",
                "source_label": "企业能力正式台账",
                "valid_from": None,
                "valid_to": None,
                "checked_at": (
                    now - timedelta(minutes=1) if code in supported_values else None
                ),
            }
            for code in ENTERPRISE_SLOT_CODES
        ],
    }


def test_phase4d2_contract_frontend_and_launcher_are_wired() -> None:
    profile = (
        ROOT
        / "contracts"
        / "bid_assessment"
        / "v1"
        / "phase4d2-enterprise-evidence-import-profile.json"
    ).read_text(encoding="utf-8")
    migration = (
        ROOT
        / "alembic"
        / "versions"
        / "20260817_0107_add_bid_enterprise_evidence_import.py"
    ).read_text(encoding="utf-8")
    frontend = (ROOT.parent / "ai-web" / "src" / "BidAssessmentRuntimeLab.vue").read_text(
        encoding="utf-8"
    )
    launcher = (ROOT / "scripts" / "start_bid_assessment_mvp1_local.ps1").read_text(
        encoding="utf-8"
    )
    assert '"candidate_hash_stable": true' in profile
    assert 'revision: str = "20260817_0107"' in migration
    assert 'down_revision: Union[str, None] = "20260817_0106"' in migration
    assert "validateEnterpriseEvidencePackage" in frontend
    assert "createEnterpriseEvidencePackage" in frontend
    assert "[switch]$EnableEnterpriseEvidenceImport" in launcher


def test_item_and_package_are_immutable_idempotent_and_do_not_infer_slot(
    phase4d2_runtime,
) -> None:
    session, storage = phase4d2_runtime
    now = datetime.now(timezone.utc).replace(microsecond=0)
    imported = _import_item(
        session,
        storage,
        now=now,
        filename="I11_诉讼资料_但人工映射为I02.pdf",
    )
    session.commit()
    replay = _import_item(
        session,
        storage,
        now=now,
        filename="I11_诉讼资料_但人工映射为I02.pdf",
    )
    assert imported.created is True
    assert replay.created is False
    assert "object_ref" not in imported.projection

    command = _package_command(str(imported.item.id), as_of=now)
    preview = preview_enterprise_evidence_package(session, actor_id=1, command=command)
    assert preview["can_freeze"] is True
    frozen = freeze_enterprise_evidence_package(
        session,
        actor_id=1,
        command=command,
        request_id="phase4d2-freeze",
        expected_candidate_hash=str(preview["candidate_hash"]),
        now=now,
    )
    session.commit()
    projection = project_enterprise_evidence_package(session, frozen.package)
    by_slot = {slot["slot_code"]: slot for slot in projection["slots"]}
    assert len(by_slot["I02"]["evidence_items"]) == 1
    assert by_slot["I11"]["evidence_items"] == []
    validate_package_item_reference(
        session,
        package_id=str(frozen.package.id),
        slot_code="I02",
        evidence_item_id=str(imported.item.id),
    )
    with pytest.raises(BidEnterpriseEvidenceImportError):
        validate_package_item_reference(
            session,
            package_id=str(frozen.package.id),
            slot_code="I11",
            evidence_item_id=str(imported.item.id),
        )


def test_candidate_hash_ignores_import_timestamp_but_rejects_command_drift(
    phase4d2_runtime,
) -> None:
    session, storage = phase4d2_runtime
    now = datetime.now(timezone.utc).replace(microsecond=0)
    imported = _import_item(session, storage, now=now)
    session.commit()
    command = _package_command(str(imported.item.id), as_of=now)
    first = preview_enterprise_evidence_package(session, actor_id=1, command=command)
    imported.item.uploaded_at = now + timedelta(hours=3)
    session.flush()
    second = preview_enterprise_evidence_package(session, actor_id=99, command=command)
    assert first["candidate_hash"] == second["candidate_hash"]

    changed = dict(command)
    changed["change_note"] = "业务说明发生变化，应生成新的 Candidate Hash"
    drifted = preview_enterprise_evidence_package(session, actor_id=1, command=changed)
    with pytest.raises(
        BidEnterpriseEvidenceImportError,
        match="BID_ENTERPRISE_EVIDENCE_CANDIDATE_HASH_MISMATCH",
    ):
        freeze_enterprise_evidence_package(
            session,
            actor_id=1,
            command=changed,
            request_id="phase4d2-drift",
            expected_candidate_hash=str(first["candidate_hash"]),
            now=now,
        )
    assert drifted["candidate_hash"] != first["candidate_hash"]


def test_empty_package_and_source_version_relabel_are_fail_closed(
    phase4d2_runtime,
) -> None:
    session, storage = phase4d2_runtime
    now = datetime.now(timezone.utc).replace(microsecond=0)
    imported = _import_item(session, storage, now=now)
    session.commit()
    empty = preview_enterprise_evidence_package(
        session,
        actor_id=1,
        command=_package_command(None, as_of=now),
    )
    assert empty["can_freeze"] is False
    assert "EVIDENCE_PACKAGE_EMPTY" in empty["blocking_codes"]

    content = b"%PDF-1.7\nphase4d2-enterprise-evidence\n%%EOF"
    with pytest.raises(
        BidEnterpriseEvidenceImportError,
        match="BID_ENTERPRISE_EVIDENCE_SOURCE_VERSION_CONFLICT",
    ):
        import_enterprise_evidence_item(
            session,
            actor_id=1,
            command={
                "evidence_class": "official_document",
                "source_record_id": "enterprise-register-I02",
                "source_version": "2026-08-17-v1",
                "source_label": "试图改写不可变来源名称",
                "valid_from": now - timedelta(days=1),
                "valid_to": now + timedelta(days=30),
            },
            file_stream=BytesIO(content),
            inspection=_inspection(content),
            request_id="phase4d2-relabel",
            storage=storage,
            now=now,
        )
    assert session.get(BidEnterpriseEvidenceItem, imported.item.id) is not None


def test_package_api_acl_view_only_candidate_hash_and_idempotency(
    phase4d2_runtime,
    monkeypatch,
) -> None:
    session, storage = phase4d2_runtime
    now = datetime.now(timezone.utc).replace(microsecond=0)
    imported = _import_item(session, storage, now=now)
    session.commit()
    command = _package_command(str(imported.item.id), as_of=now)
    payload = BidEnterpriseEvidencePackageCreateIn.model_validate(command)
    user = SimpleNamespace(id=1)
    monkeypatch.setattr(runtime_lab_api, "settings", _runtime_settings())

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: False)
    hidden = runtime_lab_api.validate_runtime_lab_enterprise_evidence_package(
        payload,
        _runtime_request(
            access_mode="execute",
            path="/api/v1/bid-assessment-runtime-lab/enterprise-evidence-packages/validate",
        ),
        current_user=user,
        db=session,
    )
    assert hidden.status_code == 404

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: True)
    blocked = runtime_lab_api.validate_runtime_lab_enterprise_evidence_package(
        payload,
        _runtime_request(
            access_mode="view-only",
            path="/api/v1/bid-assessment-runtime-lab/enterprise-evidence-packages/validate",
        ),
        current_user=user,
        db=session,
    )
    assert blocked.status_code == 403
    assert json.loads(blocked.body)["error"]["code"] == "BID_MVP1_VIEW_ONLY"

    execute_request = _runtime_request(
        access_mode="execute",
        path="/api/v1/bid-assessment-runtime-lab/enterprise-evidence-packages/validate",
    )
    validated = runtime_lab_api.validate_runtime_lab_enterprise_evidence_package(
        payload,
        execute_request,
        current_user=user,
        db=session,
    )
    candidate_hash = str(validated["data"]["candidate_hash"])
    assert session.query(BidEnterpriseEvidencePackage).count() == 0

    create_request = _runtime_request(
        access_mode="execute",
        path="/api/v1/bid-assessment-runtime-lab/enterprise-evidence-packages",
    )
    first = runtime_lab_api.create_runtime_lab_enterprise_evidence_package(
        payload,
        create_request,
        idempotency_key="phase4d2-package-freeze-key",
        candidate_hash=candidate_hash,
        current_user=user,
        db=session,
    )
    assert first.status_code == 201
    assert first.headers["idempotency-replayed"] == "false"
    replay = runtime_lab_api.create_runtime_lab_enterprise_evidence_package(
        payload,
        create_request,
        idempotency_key="phase4d2-package-freeze-key",
        candidate_hash=candidate_hash,
        current_user=user,
        db=session,
    )
    assert replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"
    assert session.query(BidEnterpriseEvidencePackage).count() == 1

    drifted = runtime_lab_api.create_runtime_lab_enterprise_evidence_package(
        payload,
        create_request,
        idempotency_key="phase4d2-package-drift-key",
        candidate_hash="f" * 64,
        current_user=user,
        db=session,
    )
    assert drifted.status_code == 409
    assert json.loads(drifted.body)["error"]["code"] == (
        "BID_ENTERPRISE_EVIDENCE_CANDIDATE_HASH_MISMATCH"
    )


def test_business_baseline_requires_same_package_slot_source_hash_and_validity(
    phase4d2_runtime,
    monkeypatch,
) -> None:
    session, storage = phase4d2_runtime
    now = datetime.now(timezone.utc).replace(microsecond=0)
    runtime_settings = _runtime_settings()
    monkeypatch.setattr(enterprise_service, "settings", runtime_settings)
    monkeypatch.setattr(business_baseline_service, "settings", runtime_settings)
    monkeypatch.setattr(
        enterprise_service,
        "get_bid_upload_object_storage",
        lambda: storage,
    )
    snapshot = freeze_enterprise_snapshot(
        session,
        actor_id=1,
        command=_snapshot_command(now=now),
        request_id="phase4d2-snapshot",
        storage=storage,
        now=now,
    ).snapshot
    imported = _import_item(
        session,
        storage,
        now=now,
        source_record_id="enterprise-register-I01",
    )
    imported_i02 = _import_item(
        session,
        storage,
        now=now,
        source_record_id="enterprise-register-I02",
    )
    package_command = _package_command(
        str(imported.item.id),
        as_of=now,
        mapped_slot="I01",
    )
    package_command["slots"][1]["evidence_item_ids"] = [str(imported_i02.item.id)]
    package_command["slots"][1]["note"] = "人工确认该文件支持资质槽位"
    package_preview = preview_enterprise_evidence_package(
        session,
        actor_id=1,
        command=package_command,
    )
    package = freeze_enterprise_evidence_package(
        session,
        actor_id=1,
        command=package_command,
        request_id="phase4d2-package",
        expected_candidate_hash=str(package_preview["candidate_hash"]),
        now=now,
    ).package
    session.commit()

    reviews = []
    for code in ENTERPRISE_SLOT_CODES:
        if code in {"I01", "I02"}:
            evidence_item = imported.item if code == "I01" else imported_i02.item
            reviews.append(
                {
                    "slot_code": code,
                    "disposition": "confirmed",
                    "evidence_class": "official_document",
                    "evidence_ref": (
                        f"enterprise-register-{code}@2026-08-17-v1"
                    ),
                    "evidence_hash": str(evidence_item.content_sha256),
                    "evidence_item_id": str(evidence_item.id),
                    "note": None,
                }
            )
        else:
            reviews.append(
                {
                    "slot_code": code,
                    "disposition": "confirmed",
                    "evidence_class": "not_available",
                    "evidence_ref": None,
                    "evidence_hash": None,
                    "evidence_item_id": None,
                    "note": "当前没有可用权威来源，保持 unknown 并跟进",
                }
            )
    command = {
        "snapshot_id": str(snapshot.id),
        "evidence_package_id": str(package.id),
        "reviewed_as_of": now,
        "review_note": "业务负责人逐项确认企业资料来源和使用边界",
        "slot_reviews": reviews,
    }
    preview = preview_enterprise_business_baseline(
        session,
        actor_id=1,
        command=command,
    )
    assert preview["can_freeze"] is True
    assert preview["source_hashes"]["evidence_package_hash"] == package.package_hash
    frozen = freeze_enterprise_business_baseline(
        session,
        actor_id=1,
        command=command,
        request_id="phase4d2-business-baseline",
        expected_candidate_hash=str(preview["candidate_hash"]),
        now=now,
    )
    session.commit()
    assert frozen.baseline.evidence_package_id == package.id
    assert frozen.baseline.evidence_package_hash == package.package_hash
    assert latest_business_snapshot(session, effective_at=now) is not None
    original_package_hash = str(package.package_hash)
    package.package_hash = "e" * 64
    session.flush()
    assert latest_business_snapshot(session, effective_at=now) is None
    package.package_hash = original_package_hash
    session.flush()
    assert latest_business_snapshot(
        session,
        effective_at=now + timedelta(days=31),
    ) is None

    wrong_slot = json.loads(json.dumps(command, default=str))
    wrong_slot["reviewed_as_of"] = now
    wrong_slot["slot_reviews"][1]["evidence_class"] = "official_document"
    wrong_slot["slot_reviews"][1]["evidence_ref"] = (
        "enterprise-register-I01@2026-08-17-v1"
    )
    wrong_slot["slot_reviews"][1]["evidence_hash"] = str(imported.item.content_sha256)
    wrong_slot["slot_reviews"][1]["evidence_item_id"] = str(imported.item.id)
    blocked = preview_enterprise_business_baseline(
        session,
        actor_id=1,
        command=wrong_slot,
    )
    assert "I02_BUSINESS_REVIEW_BLOCKED" in blocked["blocking_codes"]
    assert "BUSINESS_EVIDENCE_ITEM_NOT_IN_PACKAGE_SLOT" in next(
        item["reason_codes"]
        for item in blocked["slot_reviews"]
        if item["slot_code"] == "I02"
    )
