from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from sqlalchemy.dialects import sqlite

from app.api.v1.budget_pricing import (
    PricingLineStatusFilter,
    PricingMatchStatusFilter,
    _line_keyword_predicate,
    router,
)
from app.core.config import settings
from app.models.budget_pricing import (
    PRICING_LINE_STATUS_MISSING_UNIT_PRICE,
    PRICING_LINE_STATUS_PENDING_MATCH,
    PRICING_LINE_STATUS_QUANTITY_UNRESOLVED,
    PRICING_MATCH_AUTO,
    PRICING_MATCH_UNMATCHED,
)
from app.models.enterprise_quota import EnterpriseQuotaItem
from app.models.user import User, UserRole
from app.schemas.budget_pricing import BudgetPricingRunCreate
from app.services.budget_pricing import (
    BudgetPricingError,
    _QuotaEntry,
    _build_catalog_index,
    _match_source,
    _normalize_text,
    _pricing_values,
    _source_row_context,
    normalize_pricing_unit,
    quota_item_is_healthy,
    strict_active_quota_version,
)
from app.services.rbac import (
    can_create_budget_pricing,
    can_view_budget_pricing,
    get_available_modules,
)


def _entry(
    item_id: int,
    *,
    name: str,
    unit: str,
    code: str,
    work_content: str = "",
    price: Decimal | None = Decimal("10"),
) -> _QuotaEntry:
    snapshot = {
        "id": item_id,
        "version_id": 3,
        "quota_code": code,
        "item_name": name,
        "work_content": work_content,
        "unit": unit,
        "unit_price": str(price) if price is not None else None,
    }
    return _QuotaEntry(
        item_id=item_id,
        version_id=3,
        quota_code=code,
        item_name=name,
        work_content=work_content,
        worker_or_subtype=None,
        unit=unit,
        normalized_unit=normalize_pricing_unit(unit) or "",
        unit_price=price,
        labor_fee=Decimal("4"),
        main_material_fee=Decimal("3"),
        auxiliary_material_fee=Decimal("2"),
        machinery_fee=Decimal("1"),
        name_norm=_normalize_text(name),
        spec_norm=_normalize_text(work_content),
        code_norm=_normalize_text(code),
        snapshot=snapshot,
        full_snapshot={**snapshot, "components": []},
    )


def test_source_row_context_exposes_quote_header_context_from_snapshot():
    snapshot_json = (
        '{"standard_row": {'
        '"area": "二层", '
        '"location": "会议室", '
        '"remark": "夜间施工", '
        '"raw_fields": {"区域": "二层", "部位": "会议室"}'
        "}}"
    )

    context = _source_row_context(snapshot_json)

    assert context["region"] == "二层"
    assert context["work_area"] == "会议室"
    assert context["location"] == "会议室"
    assert context["remark"] == "夜间施工"
    assert context["raw_fields"] == {"区域": "二层", "部位": "会议室"}


def _source(*, name: str, unit: str, quantity: str = "2", valid: bool = True) -> dict:
    return {
        "item_name": name,
        "spec": None,
        "unit": unit,
        "normalized_unit": normalize_pricing_unit(unit),
        "quota_code": None,
        "quantity": Decimal(quantity) if valid else Decimal("0.000000"),
        "quantity_status": "valid" if valid else "missing",
        "quantity_resolved": valid,
    }


def test_feature_defaults_and_rbac_module_are_fail_closed():
    assert hasattr(settings, "feature_budget_pricing")
    admin = User(role="admin", is_active=True, role_version=1)
    staff = User(role="user", is_active=True, role_version=1)
    assert can_view_budget_pricing(admin) is True
    assert can_create_budget_pricing(admin) is True

    old_budget = settings.feature_budget_projects
    old_pricing = settings.feature_budget_pricing
    try:
        object.__setattr__(settings, "feature_budget_projects", True)
        object.__setattr__(settings, "feature_budget_pricing", False)
        module = next(item for item in get_available_modules(admin) if item["key"] == "budget_pricing")
        assert module["status"] == "pending"
        object.__setattr__(settings, "feature_budget_pricing", True)
        module = next(item for item in get_available_modules(admin) if item["key"] == "budget_pricing")
        assert module["status"] == "available"
        staff_module = next(item for item in get_available_modules(staff) if item["key"] == "budget_pricing")
        assert staff_module["status"] == "forbidden"
    finally:
        object.__setattr__(settings, "feature_budget_projects", old_budget)
        object.__setattr__(settings, "feature_budget_pricing", old_pricing)


def test_budget_pricing_navigation_requires_budget_and_cost_roles():
    cost_only = User(role="none", is_active=True, role_version=1)
    cost_only.role_assignments = [UserRole(role="cost_viewer")]
    combined = User(role="none", is_active=True, role_version=1)
    combined.role_assignments = [
        UserRole(role="cost_viewer"),
        UserRole(role="project_viewer"),
    ]

    old_budget = settings.feature_budget_projects
    old_pricing = settings.feature_budget_pricing
    try:
        object.__setattr__(settings, "feature_budget_projects", True)
        object.__setattr__(settings, "feature_budget_pricing", True)
        cost_only_module = next(
            item for item in get_available_modules(cost_only) if item["key"] == "budget_pricing"
        )
        combined_module = next(
            item for item in get_available_modules(combined) if item["key"] == "budget_pricing"
        )
        assert cost_only_module["status"] == "pending"
        assert combined_module["status"] == "available"
    finally:
        object.__setattr__(settings, "feature_budget_projects", old_budget)
        object.__setattr__(settings, "feature_budget_pricing", old_pricing)


def test_run_schema_accepts_both_explicit_quota_version_names_and_rejects_conflict_signal():
    documented = BudgetPricingRunCreate(
        source_import_batch_id=16,
        source_import_revision_id=19,
        expected_active_quota_version_id=3,
    )
    frontend = BudgetPricingRunCreate(
        source_import_batch_id=16,
        source_import_revision_id=19,
        expected_enterprise_quota_version_id=3,
    )
    conflicting = BudgetPricingRunCreate(
        source_import_batch_id=16,
        source_import_revision_id=19,
        expected_active_quota_version_id=3,
        expected_enterprise_quota_version_id=4,
    )
    assert documented.expected_quota_version_id() == 3
    assert frontend.expected_quota_version_id() == 3
    assert conflicting.has_conflicting_version_ids() is True


def test_quota_health_excludes_empty_and_mojibake_rows_but_not_missing_price_identity():
    healthy = EnterpriseQuotaItem(item_name="石材地面（正铺）", quota_code="QS201001", unit="㎡", unit_price=71.13)
    missing_name = EnterpriseQuotaItem(item_name=None, quota_code=None, unit="㎡", unit_price=5.25)
    corrupt = EnterpriseQuotaItem(
        item_name="乱码\x97定额" + "x" * 245,
        quota_code="QS202032\x80broken",
        unit="㎡",
        unit_price=6.5,
    )
    missing_price = EnterpriseQuotaItem(item_name="完整名称", quota_code="QS999001", unit="m", unit_price=None)
    assert quota_item_is_healthy(healthy) is True
    assert quota_item_is_healthy(missing_name) is False
    assert quota_item_is_healthy(corrupt) is False
    assert quota_item_is_healthy(missing_price) is True


class _VersionQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        self.locked = True
        return self

    def all(self):
        return self.rows


class _VersionDb:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *_args, **_kwargs):
        return _VersionQuery(self.rows)


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        ([], "BUDGET_PRICING_ACTIVE_QUOTA_REQUIRED"),
        (
            [
                SimpleNamespace(id=1, status="active", is_active=True),
                SimpleNamespace(id=2, status="active", is_active=True),
            ],
            "BUDGET_PRICING_ACTIVE_QUOTA_AMBIGUOUS",
        ),
        (
            [SimpleNamespace(id=1, status="active", is_active=False)],
            "BUDGET_PRICING_ACTIVE_QUOTA_INCONSISTENT",
        ),
        (
            [SimpleNamespace(id=1, status="draft", is_active=True)],
            "BUDGET_PRICING_ACTIVE_QUOTA_INCONSISTENT",
        ),
    ],
)
def test_strict_active_quota_gate_blocks_zero_multiple_and_split_states(rows, code):
    with pytest.raises(BudgetPricingError) as exc_info:
        strict_active_quota_version(_VersionDb(rows))
    assert exc_info.value.code == code


def test_strict_active_quota_gate_returns_exactly_one_consistent_version():
    active = SimpleNamespace(id=3, status="active", is_active=True)
    assert strict_active_quota_version(_VersionDb([active])) is active


def test_strict_active_quota_can_lock_the_selected_version_set():
    db = _VersionDb([SimpleNamespace(id=3, status="active", is_active=True)])
    assert strict_active_quota_version(db, for_update=True).id == 3


def test_matcher_auto_selects_unique_compatible_name_and_never_selects_unit_conflict():
    compatible = _entry(1, name="石材地面（正铺）", unit="㎡", code="QS201001", price=Decimal("71.13"))
    incompatible = _entry(2, name="石材地面（正铺）", unit="m", code="QS201002", price=Decimal("21.67"))
    source = _source(name="石材地面（正铺）", unit="m²")
    match = _match_source(source, [incompatible, compatible])
    assert match["match_status"] == PRICING_MATCH_AUTO
    assert match["selected"]["entry"].item_id == compatible.item_id
    assert match["selected"]["unit_compatibility"] == "compatible"


def test_quantity_unresolved_keeps_unit_cost_but_excludes_zero_amount():
    entry = _entry(1, name="石材地面（正铺）", unit="㎡", code="QS201001", price=Decimal("71.13"))
    source = _source(name="石材地面（正铺）", unit="㎡", valid=False)
    match = _match_source(source, [entry])
    pricing = _pricing_values(source, match)
    assert match["match_status"] == PRICING_MATCH_AUTO
    assert pricing["pricing_status"] == PRICING_LINE_STATUS_QUANTITY_UNRESOLVED
    assert pricing["unit_price"] == Decimal("71.13")
    assert pricing["line_total"] == Decimal("0.000000")
    assert pricing["amount_included"] is False


def test_unmatched_and_missing_price_are_not_disguised_as_zero_cost():
    source = _source(name="墙面乳胶漆", unit="㎡")
    unmatched = _match_source(source, [])
    unmatched_pricing = _pricing_values(source, unmatched)
    assert unmatched["match_status"] == PRICING_MATCH_UNMATCHED
    assert unmatched_pricing["pricing_status"] == PRICING_LINE_STATUS_PENDING_MATCH
    assert unmatched_pricing["unit_price"] is None
    assert unmatched_pricing["line_total"] is None

    no_price_entry = _entry(2, name="墙面乳胶漆", unit="㎡", code="QS999001", price=None)
    matched = _match_source(source, [no_price_entry])
    missing_price = _pricing_values(source, matched)
    assert matched["match_status"] == PRICING_MATCH_AUTO
    assert missing_price["pricing_status"] == PRICING_LINE_STATUS_MISSING_UNIT_PRICE
    assert missing_price["line_total"] is None


def test_decimal_pricing_uses_round_half_up_to_six_places():
    entry = _entry(1, name="精确计价", unit="m", code="QS999002", price=Decimal("2.345679"))
    source = _source(name="精确计价", unit="m", quantity="1.234568")
    pricing = _pricing_values(source, _match_source(source, [entry]))
    expected = (Decimal("1.234568") * Decimal("2.345679")).quantize(
        Decimal("0.000001"),
        rounding=ROUND_HALF_UP,
    )
    assert pricing["line_total"] == expected


def test_pricing_overflow_becomes_reviewable_partial_line():
    entry = _entry(1, name="overflow", unit="m", code="X", price=Decimal("99999999999999"))
    pricing = _pricing_values(_source(name="overflow", unit="m", quantity="100000"), _match_source(_source(name="overflow", unit="m", quantity="100000"), [entry]))
    assert pricing["pricing_status"] == "numeric_overflow"
    assert pricing["line_total"] is None
    assert pricing["amount_included"] is False


def test_exact_catalog_index_preserves_exact_match_result():
    entry = _entry(1, name="indexed", unit="m", code="IDX")
    source = _source(name="indexed", unit="m")
    assert _match_source(source, [entry], _build_catalog_index([entry]))["selected"]["entry"] is entry


def test_run_builder_records_latest_run_as_parent():
    source = (Path(__file__).resolve().parents[1] / "app" / "services" / "budget_pricing.py").read_text(encoding="utf-8")
    assert "parent_run_id=previous_run.id if previous_run else None" in source
    assert '"candidates": result_candidates' in source
    assert "run.ready_at = db.execute(select(func.now())).scalar_one()" in source


def test_line_filter_enums_match_persisted_status_contract():
    assert set(get_args(PricingMatchStatusFilter)) == {
        "auto_matched",
        "manual_matched",
        "ambiguous",
        "unmatched",
        "unit_conflict",
    }
    assert set(get_args(PricingLineStatusFilter)) == {
        "priced",
        "quantity_unresolved",
        "missing_unit_price",
        "pending_match",
        "unit_conflict",
        "numeric_overflow",
    }
    assert "data_error" not in get_args(PricingMatchStatusFilter)


def test_keyword_searches_selected_and_candidate_quota_snapshots_without_join_duplicates():
    sql = str(
        _line_keyword_predicate("%QS201001%").compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert "selected_quota_item_snapshot_json like" in sql
    assert "exists (select" in sql
    assert "budget_project_pricing_match_candidates.quota_item_snapshot_json like" in sql
    assert " join " not in sql


def test_pricing_router_exposes_only_p2_read_and_create_contract():
    paths = {route.path for route in router.routes}
    assert "/admin/budget-projects/{project_id}/pricing-readiness" in paths
    assert "/admin/budget-projects/{project_id}/pricing-runs" in paths
    assert "/admin/budget-projects/pricing-runs/{run_identifier}" in paths
    assert "/admin/budget-projects/pricing-runs/{run_identifier}/lines" in paths
    assert "/admin/budget-projects/pricing-runs/{run_identifier}/lines/{line_identifier}/candidates" in paths
    assert "/admin/budget-projects/pricing-runs/{run_identifier}/events" in paths
    assert {method for route in router.routes for method in route.methods} <= {"GET", "POST", "PATCH"}
    assert "/admin/budget-projects/{project_id}/pricing-draft/current" in paths
    assert "/admin/budget-projects/{project_id}/pricing-draft" in paths
    assert "/admin/budget-projects/{project_id}/pricing-draft/lines" in paths
    assert "/admin/budget-projects/{project_id}/pricing-draft/lines/{line_identifier}" in paths


def test_pricing_service_has_no_legacy_or_external_chain_imports():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "services" / "budget_pricing.py").read_text(encoding="utf-8").lower()
    api_source = (root / "app" / "api" / "v1" / "budget_pricing.py").read_text(encoding="utf-8").lower()
    blocked = (
        "app.models.cost_item",
        "cost_measurement",
        "project_cost_import",
        "cost_rag",
        "enterprise_quota_activation",
        "n8n",
        "dify",
    )
    for token in blocked:
        assert token not in source
        assert token not in api_source
