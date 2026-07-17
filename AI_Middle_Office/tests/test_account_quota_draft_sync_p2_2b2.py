from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 - register complete metadata
from app.core.config import settings
from app.core.database import Base
from app.models.account import Account, AccountBudgetProject, AccountMembership
from app.models.account_quota import AccountQuotaItem, AccountQuotaSyncLine, AccountQuotaSyncRun
from app.models.budget_project import BudgetProjectImportBatch, BudgetProjectImportRevision, BudgetProjectProfile
from app.models.budget_pricing import BudgetProjectPricingRun
from app.models.enterprise_quota import EnterpriseQuotaItem, EnterpriseQuotaVersion
from app.models.project_progress import Project
from app.models.user import User
from app.schemas.budget_pricing import (
    BudgetPricingDraftAccountQuotaSyncConfirmIn,
    BudgetPricingDraftAccountQuotaSyncLineIn,
    BudgetPricingDraftAccountQuotaSyncPreviewIn,
)
from app.services.account_quota_draft_sync import confirm_account_quota_sync, preview_account_quota_sync
from app.services.budget_pricing_drafts import create_or_rebuild_budget_pricing_draft, patch_budget_pricing_draft_line


MIGRATION_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "20260716_0054_add_account_quota_draft_sync.py"


@contextmanager
def _flags():
    names = (
        "feature_budget_projects",
        "feature_budget_pricing",
        "feature_budget_pricing_drafts",
        "feature_account_quotas",
        "feature_account_quota_draft_sync",
    )
    before = {name: getattr(settings, name) for name in names}
    for name in names:
        object.__setattr__(settings, name, True)
    try:
        yield
    finally:
        for name, value in before.items():
            object.__setattr__(settings, name, value)


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return session, engine


def _seed(session):
    user = User(username="quota-sync-admin", hashed_password="x", role="admin", role_version=1, quota=10, is_active=True)
    session.add(user)
    session.flush()
    account = Account(account_uuid=str(uuid4()), account_code="quota-sync", account_name="账户定额同步测试", status="active", created_by=user.id)
    session.add(account)
    session.flush()
    session.add(AccountMembership(account_id=account.id, user_id=user.id, member_role="owner", status="active", is_default=True, created_by=user.id))
    project = Project(project_code="SYNC-PROJECT", name="账户定额同步项目", status="planning", risk_level="normal", progress_percent=0, project_manager_id=user.id, created_by=user.id)
    session.add(project)
    session.flush()
    profile = BudgetProjectProfile(project_id=project.id, workspace_status="active", created_by=user.id, updated_by=user.id)
    session.add(profile)
    session.add(AccountBudgetProject(account_id=account.id, project_id=project.id, created_by=user.id))
    batch = BudgetProjectImportBatch(
        batch_uuid=str(uuid4()), project_id=project.id, source_filename="sync.xlsx", source_file_sha256="s" * 64,
        source_file_size=1, source_storage_mode="metadata_only", parser_version="test", status="active", remap_revision=0,
        sheet_count=1, total_output_row_count=2, standard_item_count=2, valid_quantity_count=2, invalid_quantity_count=0,
        original_preview_json="{}", current_preview_json="{}", issues_json="[]", created_by=user.id, updated_by=user.id,
    )
    session.add(batch)
    session.flush()
    rows = [
        {"row_key": "r-1", "source_sheet": "清单", "sheet_role": "bill", "raw_row_index": 2, "sort_order": 1, "mapping_revision": 0, "row_type": "item", "is_standard_item": True, "quantity_status": "valid", "standard_row": {"item_name": "石材地面铺装", "spec": "600×600×20mm", "unit": "㎡", "calculation_quantity": "2", "quantity_status": "valid", "warnings": []}},
        {"row_key": "r-2", "source_sheet": "清单", "sheet_role": "bill", "raw_row_index": 3, "sort_order": 2, "mapping_revision": 0, "row_type": "item", "is_standard_item": True, "quantity_status": "valid", "standard_row": {"item_name": "未改价项目", "spec": None, "unit": "项", "calculation_quantity": "1", "quantity_status": "valid", "warnings": []}},
    ]
    revision = BudgetProjectImportRevision(
        revision_uuid=str(uuid4()), batch_id=batch.id, revision_number=1, revision_kind="initial", snapshot_sha256="r" * 64,
        preview_json="{}", sheet_mappings_json="[]", standard_rows_json=json.dumps(rows, ensure_ascii=False),
        summary_json="{}", created_by=user.id,
    )
    session.add(revision)
    session.flush()
    batch.current_revision_id = revision.id
    batch.confirmed_revision_id = revision.id
    profile.active_import_batch_id = batch.id
    profile.active_import_revision_id = revision.id
    quota = EnterpriseQuotaVersion(version_code="enterprise-active", version_name="受保护企业主库", status="active", is_active=True, source_file_sha256="q" * 64, created_by=user.id)
    session.add(quota)
    session.flush()
    session.add(EnterpriseQuotaItem(version_id=quota.id, quota_code="E-1", item_name="企业主库受保护项", unit="㎡", unit_price=10, sort_order=1))
    session.commit()
    return user, account, project, profile, batch, revision


def test_preview_confirm_and_existing_update_are_account_scoped_and_non_polluting():
    session, engine = _db()
    try:
        with _flags():
            user, account, _project, profile, batch, revision = _seed(session)
            protected = {
                "enterprise_versions": session.query(EnterpriseQuotaVersion).count(),
                "enterprise_items": session.query(EnterpriseQuotaItem).count(),
                "formal_runs": session.query(BudgetProjectPricingRun).count(),
            }
            draft = create_or_rebuild_budget_pricing_draft(
                session, profile, user, pricing_mode="account_strict", source_import_batch_id=batch.id,
                source_import_revision_id=revision.id, expected_active_quota_version_id=None, expected_revision=None, reason="create",
            )
            session.commit()
            line = draft.lines[0]
            draft, line = patch_budget_pricing_draft_line(
                session, profile, user, line_identifier=line.line_uuid, expected_revision=draft.revision,
                expected_line_revision=line.line_revision, manual_unit_price="123.456789", reason="manual accepted",
            )
            session.commit()

            preview = preview_account_quota_sync(session, profile, user, BudgetPricingDraftAccountQuotaSyncPreviewIn(expected_revision=draft.revision))
            assert preview["summary"]["requested_count"] == 1
            item = preview["items"][0]
            assert item["suggested_action"] == "create"
            assert item["manual_unit_price"] == "123.456789"
            assert item["target_status"] == "draft"

            confirmed = confirm_account_quota_sync(session, profile, user, BudgetPricingDraftAccountQuotaSyncConfirmIn(
                expected_revision=draft.revision, reason="用户确认沉淀人工改价", items=[
                    BudgetPricingDraftAccountQuotaSyncLineIn(line_identifier=item["line_identifier"], expected_line_revision=item["expected_line_revision"], action="create")
                ],
            ))
            session.commit()
            assert confirmed["created_count"] == 1
            assert confirmed["updated_count"] == 0
            quota = session.query(AccountQuotaItem).one()
            assert quota.account_id == account.id
            assert quota.source == "pricing_draft_sync"
            assert quota.status == "draft"
            assert str(quota.unit_price) == "123.456789"
            assert session.query(AccountQuotaSyncRun).count() == 1
            assert session.query(AccountQuotaSyncLine).count() == 1
            assert session.query(BudgetProjectPricingRun).count() == protected["formal_runs"]
            assert session.query(EnterpriseQuotaVersion).count() == protected["enterprise_versions"]
            assert session.query(EnterpriseQuotaItem).count() == protected["enterprise_items"]

            draft, line = patch_budget_pricing_draft_line(
                session, profile, user, line_identifier=line.line_uuid, expected_revision=draft.revision,
                expected_line_revision=line.line_revision, manual_unit_price="130.000001", reason="manual refined",
            )
            session.commit()
            preview2 = preview_account_quota_sync(session, profile, user, BudgetPricingDraftAccountQuotaSyncPreviewIn(expected_revision=draft.revision))
            item2 = preview2["items"][0]
            assert item2["existing_item"]["id"] == quota.id
            assert item2["suggested_action"] == "skip"
            confirmed2 = confirm_account_quota_sync(session, profile, user, BudgetPricingDraftAccountQuotaSyncConfirmIn(
                expected_revision=draft.revision, reason="用户确认更新已沉淀价格", items=[
                    BudgetPricingDraftAccountQuotaSyncLineIn(
                        line_identifier=item2["line_identifier"], expected_line_revision=item2["expected_line_revision"],
                        expected_target_revision=item2["existing_item"]["revision"], action="update_existing",
                    )
                ],
            ))
            session.commit()
            session.refresh(quota)
            assert confirmed2["updated_count"] == 1
            assert quota.status == "draft"
            assert quota.revision == 2
            assert str(quota.unit_price) == "130.000001"
            assert session.query(AccountQuotaSyncRun).count() == 2
            assert session.query(AccountQuotaSyncLine).count() == 2
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_preview_and_confirm_accept_all_priced_draft_lines_not_only_manual_prices():
    session, engine = _db()
    try:
        with _flags():
            user, account, _project, profile, batch, revision = _seed(session)
            draft = create_or_rebuild_budget_pricing_draft(
                session, profile, user, pricing_mode="account_strict", source_import_batch_id=batch.id,
                source_import_revision_id=revision.id, expected_active_quota_version_id=None,
                expected_revision=None, reason="create",
            )
            ai_line, base_line = draft.lines[:2]
            ai_line.ai_estimated_unit_price = Decimal("77.777777")
            ai_line.effective_unit_price = Decimal("77.777777")
            ai_line.price_source = "ai_estimate"
            ai_line.pricing_status = "priced"
            base_line.base_unit_price = Decimal("33.333333")
            base_line.effective_unit_price = Decimal("33.333333")
            base_line.price_source = "enterprise_quota"
            base_line.pricing_status = "priced"
            session.commit()

            preview = preview_account_quota_sync(
                session,
                profile,
                user,
                BudgetPricingDraftAccountQuotaSyncPreviewIn(expected_revision=draft.revision),
            )

            assert preview["summary"]["requested_count"] == 2
            assert preview["boundary"]["only_manual_prices"] is False
            by_source = {item["sync_price_source"]: item for item in preview["items"]}
            assert by_source["ai_estimate"]["manual_unit_price"] is None
            assert by_source["ai_estimate"]["sync_unit_price"] == "77.777777"
            assert by_source["enterprise_quota"]["manual_unit_price"] is None
            assert by_source["enterprise_quota"]["sync_unit_price"] == "33.333333"

            confirmed = confirm_account_quota_sync(
                session,
                profile,
                user,
                BudgetPricingDraftAccountQuotaSyncConfirmIn(
                    expected_revision=draft.revision,
                    reason="accept priced draft lines",
                    items=[
                        BudgetPricingDraftAccountQuotaSyncLineIn(
                            line_identifier=item["line_identifier"],
                            expected_line_revision=item["expected_line_revision"],
                            action="create",
                        )
                        for item in preview["items"]
                    ],
                ),
            )
            session.commit()

            assert confirmed["created_count"] == 2
            prices = sorted(str(item.unit_price) for item in session.query(AccountQuotaItem).all())
            assert prices == ["33.333333", "77.777777"]
            assert {item.account_id for item in session.query(AccountQuotaItem).all()} == {account.id}
            sync_lines = session.query(AccountQuotaSyncLine).all()
            snapshots = [json.loads(line.source_snapshot_json) for line in sync_lines]
            assert sorted(snapshot["sync_unit_price"] for snapshot in snapshots) == ["33.333333", "77.777777"]
            assert {snapshot["sync_price_source"] for snapshot in snapshots} == {"ai_estimate", "enterprise_quota"}
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_migration_declares_both_sync_audit_tables():
    text = MIGRATION_PATH.read_text(encoding="utf-8")
    assert '"account_quota_sync_runs"' in text
    assert '"account_quota_sync_lines"' in text
    assert 'down_revision: Union[str, None] = "20260716_0053"' in text


def test_frontend_exposes_account_quota_draft_sync_contract():
    project_root = MIGRATION_PATH.parents[2]
    pricing_page = (project_root.parent / "ai-web" / "src" / "BudgetProjectPricing.vue").read_text(encoding="utf-8")
    quota_page = (project_root.parent / "ai-web" / "src" / "AccountQuotaLibrary.vue").read_text(encoding="utf-8")
    api_client = (project_root.parent / "ai-web" / "src" / "budgetProjectApi.js").read_text(encoding="utf-8")

    assert "同步到账户定额" in pricing_page
    assert "确认同步为账户定额草稿" in pricing_page
    assert "previewAccountQuotaSync" in pricing_page
    assert "confirmAccountQuotaSync" in pricing_page
    assert "pricing_draft_synced: '报价草稿同步'" in quota_page
    assert "pricing-draft/account-quota-sync/preview" in api_client
    assert "pricing-draft/account-quota-sync/confirm" in api_client


def test_account_strict_mode_reads_only_current_account_active_items():
    session, engine = _db()
    try:
        with _flags():
            user, account, _project, profile, batch, revision = _seed(session)
            enterprise_item = session.query(EnterpriseQuotaItem).one()
            first_source_name = json.loads(revision.standard_rows_json)[0]["standard_row"]["item_name"]
            first_source_spec = json.loads(revision.standard_rows_json)[0]["standard_row"]["spec"]
            first_source_unit = json.loads(revision.standard_rows_json)[0]["standard_row"]["unit"]
            enterprise_item.item_name = first_source_name
            enterprise_item.unit = first_source_unit
            enterprise_item.unit_price = Decimal("999.000000")
            session.commit()

            initial = create_or_rebuild_budget_pricing_draft(
                session, profile, user, pricing_mode="account_strict", source_import_batch_id=batch.id,
                source_import_revision_id=revision.id, expected_active_quota_version_id=None,
                expected_revision=None, reason="before account quota activation",
            )
            session.commit()
            assert initial.lines[0].effective_unit_price is None
            assert initial.lines[0].selected_enterprise_quota_item_id is None
            assert initial.lines[0].selected_account_quota_item_id is None

            active_item = AccountQuotaItem(
                item_uuid=str(uuid4()), account_id=account.id, quota_code=None,
                item_name=first_source_name, item_features=None, spec=first_source_spec,
                unit=first_source_unit, unit_price=Decimal("88.123456"), fingerprint="a" * 64,
                source="manual", status="active", notes=None, revision=1,
                created_by=user.id, updated_by=user.id,
            )
            session.add(active_item)
            session.commit()

            rebuilt = create_or_rebuild_budget_pricing_draft(
                session, profile, user, pricing_mode="account_strict", source_import_batch_id=batch.id,
                source_import_revision_id=revision.id, expected_active_quota_version_id=None,
                expected_revision=initial.revision, reason="active account quota available",
            )
            session.commit()
            matched = rebuilt.lines[0]
            assert matched.selected_account_quota_item_id == active_item.id
            assert matched.selected_enterprise_quota_item_id is None
            assert str(matched.base_unit_price) == "88.123456"
            assert str(matched.effective_unit_price) == "88.123456"
            assert matched.price_source == "account_quota"
            assert matched.match_status == "auto_matched"
            assert rebuilt.enterprise_quota_version_id is None
            assert rebuilt.account_quota_catalog_sha256
            assert session.query(BudgetProjectPricingRun).count() == 0
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_p2_2b3_migration_declares_account_match_evidence_columns():
    migration = MIGRATION_PATH.parents[0] / "20260716_0055_add_account_quota_draft_matching.py"
    text = migration.read_text(encoding="utf-8")
    assert '"account_quota_catalog_sha256"' in text
    assert '"selected_account_quota_item_id"' in text
    assert 'down_revision: Union[str, None] = "20260716_0054"' in text

    rebuild_fix = migration.parent / "20260716_0056_allow_pricing_draft_rebuild_after_quota_sync.py"
    rebuild_text = rebuild_fix.read_text(encoding="utf-8")
    assert 'ondelete="SET NULL"' in rebuild_text
    assert AccountQuotaSyncLine.__table__.c.draft_line_id.nullable is True
