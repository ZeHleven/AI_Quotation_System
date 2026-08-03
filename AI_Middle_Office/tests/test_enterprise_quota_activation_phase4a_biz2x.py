import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.client_inquiry import ClientInquiry
from app.models.cost_item import (
    CHANGE_TYPE_STATUS,
    COST_SOURCE_MANUAL,
    COST_STATUS_ACTIVE,
    PRICE_TYPE_COMBINED,
    CostItem,
    CostItemHistory,
    CostRagSyncRun,
)
from app.models.enterprise_quota import (
    IMPORT_BATCH_STATUS_IMPORTED,
    QUOTA_VERSION_STATUS_ACTIVE,
    QUOTA_VERSION_STATUS_DRAFT,
    CostImportBatch,
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.models.quote_cost_evidence import QuoteCostEvidence
from app.models.quote_feedback import QuoteFeedback
from app.models.quote_job import QuoteJob, QuoteJobEvent
from app.models.user import User
from app.services.enterprise_quota_activation import (
    EnterpriseQuotaActivationError,
    build_enterprise_quota_activation_plan,
    run_enterprise_quota_activation,
)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    tables = [
        User.__table__,
        ClientInquiry.__table__,
        QuoteJob.__table__,
        QuoteJobEvent.__table__,
        QuoteFeedback.__table__,
        QuoteCostEvidence.__table__,
        CostItem.__table__,
        CostItemHistory.__table__,
        CostRagSyncRun.__table__,
        CostImportBatch.__table__,
        EnterpriseQuotaVersion.__table__,
        EnterpriseQuotaSection.__table__,
        EnterpriseQuotaItem.__table__,
        EnterpriseCostResource.__table__,
        EnterpriseQuotaComponent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)


def test_activation_plan_reports_safety_gates_and_cleanup_scope(db_session):
    version_id = _seed_enterprise_quota_version(db_session, warning_count=3)
    cost_item = _seed_old_cost_item(db_session)
    _seed_quote_cost_evidence(db_session, cost_item.id)
    db_session.commit()

    plan = build_enterprise_quota_activation_plan(
        db_session,
        version_id,
        clear_old_cost_db=True,
    )

    assert plan["ok"] is True
    assert plan["can_commit"] is True
    assert plan["needs_acknowledge_warnings"] is True
    assert plan["confirmation_code"] == "ACTIVATE-QS-PHASE4A-TEST"
    assert plan["row_counts"] == {"sections": 1, "items": 1, "components": 1, "resources": 1}
    assert plan["old_cost_db"]["items_total"] == 1
    assert plan["old_cost_db"]["items_active"] == 1
    assert plan["old_cost_db"]["history_total"] == 1
    assert plan["old_cost_db"]["quote_cost_evidence_with_cost_item_id"] == 1
    assert plan["old_cost_db"]["clear_tables"] == ["cost_item_history", "cost_items"]
    assert "quote_cost_evidence" in plan["old_cost_db"]["preserve_tables"]
    assert {check["code"] for check in plan["warnings"]} >= {
        "phase0_warnings_acknowledged",
        "old_cost_db_will_be_cleared",
        "quote_cost_evidence_preserved",
    }


def test_activation_dry_run_writes_backup_and_rolls_back_database_mutations(db_session, tmp_path):
    version_id = _seed_enterprise_quota_version(db_session, warning_count=0)
    cost_item = _seed_old_cost_item(db_session)
    db_session.commit()

    result = run_enterprise_quota_activation(
        db_session,
        version_id,
        clear_old_cost_db=True,
        backup_dir=tmp_path,
        acknowledge_warnings=True,
        reason="phase4a dry run test",
        commit=False,
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["deleted"] == {"cost_item_history": 1, "cost_items": 1}
    assert result["target_version"]["status"] == QUOTA_VERSION_STATUS_ACTIVE
    assert result["backup"]["counts"] == {"cost_items": 1, "cost_item_history": 1}
    assert result["backup"]["sha256"]
    assert Path(result["backup"]["path"]).exists()
    assert db_session.query(CostItem).count() == 0

    db_session.rollback()

    restored_cost_item = db_session.get(CostItem, cost_item.id)
    restored_version = db_session.get(EnterpriseQuotaVersion, version_id)
    assert restored_cost_item is not None
    assert restored_version.status == QUOTA_VERSION_STATUS_DRAFT
    assert restored_version.is_active is False


def test_activation_commit_requires_confirmation_code_and_warning_ack(db_session, tmp_path):
    version_id = _seed_enterprise_quota_version(db_session, warning_count=1)
    _seed_old_cost_item(db_session)
    db_session.commit()

    with pytest.raises(EnterpriseQuotaActivationError, match="Confirmation code mismatch"):
        run_enterprise_quota_activation(
            db_session,
            version_id,
            clear_old_cost_db=True,
            backup_dir=tmp_path,
            confirm_code="WRONG",
            acknowledge_warnings=True,
            commit=True,
        )

    with pytest.raises(EnterpriseQuotaActivationError, match="must be acknowledged"):
        run_enterprise_quota_activation(
            db_session,
            version_id,
            clear_old_cost_db=True,
            backup_dir=tmp_path,
            confirm_code="ACTIVATE-QS-PHASE4A-TEST",
            acknowledge_warnings=False,
            commit=True,
        )


def test_activation_blocks_when_quote_jobs_are_still_active(db_session, tmp_path):
    version_id = _seed_enterprise_quota_version(db_session, warning_count=0)
    _seed_old_cost_item(db_session)
    db_session.add(
        QuoteJob(
            job_id=str(uuid.uuid4()),
            username="phase4a_user",
            status="queued",
            stage="queued",
        )
    )
    db_session.commit()

    plan = build_enterprise_quota_activation_plan(db_session, version_id, clear_old_cost_db=True)

    assert plan["ok"] is False
    assert any(check["code"] == "no_active_quote_jobs" for check in plan["blockers"])
    with pytest.raises(EnterpriseQuotaActivationError, match="no_active_quote_jobs"):
        run_enterprise_quota_activation(
            db_session,
            version_id,
            clear_old_cost_db=True,
            backup_dir=tmp_path,
            acknowledge_warnings=True,
            commit=False,
        )


def _seed_enterprise_quota_version(db, *, warning_count: int) -> int:
    summary = {
        "error_count": 0,
        "warning_count": warning_count,
        "phase2_import": {
            "section_count": 1,
            "item_count": 1,
            "component_count": 1,
            "resource_count": 1,
        },
    }
    batch = CostImportBatch(
        batch_uuid=str(uuid.uuid4()),
        source_filename="phase4a-enterprise-quota.xls",
        source_file_sha256="a" * 64,
        parser_version="phase0",
        status=IMPORT_BATCH_STATUS_IMPORTED,
        summary_json=json.dumps(summary),
        issues_json="[]",
        error_count=0,
        warning_count=warning_count,
    )
    version = EnterpriseQuotaVersion(
        version_code="qs-phase4a-test",
        version_name="QS Phase 4A Test",
        status=QUOTA_VERSION_STATUS_DRAFT,
        is_active=False,
        summary_json=json.dumps(summary),
    )
    batch.versions.append(version)
    db.add(batch)
    db.flush()

    section = EnterpriseQuotaSection(
        version_id=version.id,
        section_code="A",
        section_name="Floor works",
        sort_order=1,
    )
    item = EnterpriseQuotaItem(
        version_id=version.id,
        section=section,
        quota_code="QS001",
        item_name="Stone floor",
        unit="m2",
        unit_price=100,
        sort_order=1,
    )
    resource = EnterpriseCostResource(
        version_id=version.id,
        resource_code="R001",
        resource_name="Stone worker",
        resource_type="labor",
        unit="workday",
        price=80,
        sort_order=1,
    )
    component = EnterpriseQuotaComponent(
        version_id=version.id,
        quota_item=item,
        resource=resource,
        parent_quota_code="QS001",
        component_type="RG labor",
        resource_code="R001",
        resource_name="Stone worker",
        unit="workday",
        quantity=1,
        unit_price=80,
        amount=80,
        sort_order=1,
    )
    db.add_all([section, item, resource, component])
    db.flush()
    return version.id


def _seed_old_cost_item(db) -> CostItem:
    item = CostItem(
        category="legacy",
        subcategory="legacy floor",
        item_name="Legacy stone floor",
        unit="m2",
        price=100,
        price_type=PRICE_TYPE_COMBINED,
        status=COST_STATUS_ACTIVE,
        source=COST_SOURCE_MANUAL,
    )
    db.add(item)
    db.flush()
    db.add(
        CostItemHistory(
            cost_item_id=item.id,
            old_status=None,
            new_status=COST_STATUS_ACTIVE,
            change_type=CHANGE_TYPE_STATUS,
            change_reason="seed legacy cost item",
        )
    )
    db.flush()
    return item


def _seed_quote_cost_evidence(db, cost_item_id: int) -> None:
    feedback = QuoteFeedback(
        quote_id=str(uuid.uuid4()),
        username="phase4a_user",
    )
    db.add(feedback)
    db.flush()
    db.add(
        QuoteCostEvidence(
            feedback_id=feedback.id,
            quote_id=feedback.quote_id,
            username=feedback.username,
            item_index=0,
            cost_item_id=cost_item_id,
            cost_item_name_snapshot="Legacy stone floor",
            reference_price=100,
        )
    )
    db.flush()
