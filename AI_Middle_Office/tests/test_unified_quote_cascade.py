import asyncio
from decimal import Decimal
from types import SimpleNamespace

from app.api.v1.quote import _unconfirmed_enterprise_quota_v2_review_rows
from app.services.budget_pricing import _QuotaEntry, _normalize_text, normalize_pricing_unit
from app.services.construction_notes import construction_note_only
from app.services.quote_pricing_cascade import build_quote_pricing_cascade_preview
from app.services.requirement_standardizer import (
    standardization_quote_rows,
    standardize_requirement_text,
)


def _entry(
    item_id: int,
    name: str,
    *,
    price: str,
    version_id: int = 0,
    unit: str = "㎡",
    quota_code: str | None = None,
    work_content: str | None = None,
) -> _QuotaEntry:
    snapshot = {
        "id": item_id,
        "quota_code": quota_code,
        "item_name": name,
        "work_content": work_content,
        "unit": unit,
        "unit_price": price,
        "account_id": 7 if version_id == 0 else None,
        "item_uuid": f"item-{item_id}",
        "revision": 1,
        "section": {"section_code": "S1", "section_name": "装饰工程"} if version_id else None,
    }
    return _QuotaEntry(
        item_id=item_id,
        version_id=version_id,
        quota_code=quota_code,
        item_name=name,
        work_content=work_content,
        worker_or_subtype=None,
        unit=unit,
        normalized_unit=normalize_pricing_unit(unit) or "",
        unit_price=Decimal(price),
        labor_fee=None,
        main_material_fee=None,
        auxiliary_material_fee=None,
        machinery_fee=None,
        name_norm=_normalize_text(name),
        spec_norm=_normalize_text(work_content),
        code_norm=_normalize_text(quota_code),
        snapshot=snapshot,
        full_snapshot=snapshot,
    )


def test_text_standardization_generates_rows_and_keeps_review_warnings():
    result = standardize_requirement_text("1. 地面拆除 10㎡；2. 墙面乳胶漆 20㎡；仅描述未给数量")
    rows = standardization_quote_rows(result)

    assert [row["item_name"] for row in rows[:2]] == ["地面拆除", "墙面乳胶漆"]
    assert rows[0]["quantity"] == 10
    assert rows[0]["unit"] == "㎡"
    assert rows[2]["item_name"] == "仅描述未给数量"
    assert "MISSING_QUANTITY" in rows[2]["warnings"]
    assert rows[2]["requires_confirmation"] is True


def test_construction_note_removes_pricing_source_but_keeps_construction_content():
    assert construction_note_only(
        "报价来源：账户定额；基层含水率达标后施工；账户定额与企业定额均未命中，已使用AI估价。"
    ) == "基层含水率达标后施工。"
    assert construction_note_only(
        "市场行情，含人工及机械清运；拆除前先完成断电确认。",
        pricing_phrases=["市场行情，含人工及机械清运"],
    ) == "拆除前先完成断电确认。"


def test_pricing_cascade_uses_account_then_enterprise_then_ai(monkeypatch):
    account_entries = [_entry(1, "地面拆除", price="50")]
    enterprise_entries = [
        _entry(101, "地面拆除", price="70", version_id=9),
        _entry(102, "墙面乳胶漆", price="80", version_id=9),
    ]
    monkeypatch.setattr(
        "app.services.quote_pricing_cascade._load_account_catalog",
        lambda db, current_user: (
            account_entries,
            {"status": "available", "account_id": 7, "eligible_item_count": 1},
        ),
    )
    monkeypatch.setattr(
        "app.services.quote_pricing_cascade._load_enterprise_catalog",
        lambda db: (
            enterprise_entries,
            {
                "status": "available",
                "version_id": 9,
                "version_code": "V9",
                "version_name": "企业定额V9",
                "eligible_item_count": 2,
            },
        ),
    )

    ai_inputs = []

    async def fake_ai_batch(snapshots, *, current_user):
        ai_inputs.extend(snapshots)
        return {
            snapshots[0]["source_row_key"]: {
                "unit_price": "33.300000",
                "pricing_breakdown": {},
                "confidence": 0.6,
                "basis": "AI测试估价",
                "risks": ["需人工确认"],
                "provider": "test",
            }
        }

    monkeypatch.setattr(
        "app.services.quote_pricing_cascade.generate_budget_pricing_ai_estimate_batch",
        fake_ai_batch,
    )
    rows = [
        {
            "requirement_row_key": "r1",
            "source_sheet": "文字输入",
            "raw_row_index": 1,
            "item_name": "地面拆除",
            "quantity": 2,
            "unit": "㎡",
            "notes": "基层清理并做好成品保护",
        },
        {
            "requirement_row_key": "r2",
            "source_sheet": "文字输入",
            "raw_row_index": 2,
            "item_name": "墙面乳胶漆",
            "quantity": 3,
            "unit": "㎡",
            "notes": "报价来源：企业定额；阴阳角顺直后再涂刷",
        },
        {
            "requirement_row_key": "r3",
            "source_sheet": "文字输入",
            "raw_row_index": 3,
            "item_name": "定制艺术装置",
            "quantity": 4,
            "unit": "㎡",
            "notes": "账户定额与企业定额均未命中，已使用AI估价；现场复核尺寸后下单",
        },
    ]

    result = asyncio.run(
        build_quote_pricing_cascade_preview(
            object(),
            standard_rows=rows,
            current_user=SimpleNamespace(id=11, username="tester"),
        )
    )

    details = result["project_details"]
    assert [row["pricing_tier"] for row in details] == [
        "account_quota",
        "enterprise_quota",
        "ai_estimate",
    ]
    assert [row["unit_price"] for row in details] == [50.0, 80.0, 33.3]
    assert [row["total_price"] for row in details] == [100.0, 240.0, 133.2]
    assert details[0]["cost_reference"]["reference_source"] == "account_quota.active"
    assert details[1]["cost_reference"]["reference_source"] == "enterprise_quota.active"
    assert details[2]["cost_reference"]["matched"] is False
    assert details[2]["manual_unit_price"] == 33.3
    assert [row["notes"] for row in details] == [
        "基层清理并做好成品保护。",
        "阴阳角顺直后再涂刷。",
        "现场复核尺寸后下单。",
    ]
    assert [item["source_row_key"] for item in ai_inputs] == ["r3"]
    assert result["pricing_cascade_summary"]["account_quota_matched_count"] == 1
    assert result["pricing_cascade_summary"]["enterprise_quota_matched_count"] == 1
    assert result["pricing_cascade_summary"]["ai_estimate_count"] == 1


def test_v2_enterprise_candidate_auto_adopts_recommended_enterprise_price(monkeypatch):
    from app.core.config import settings

    enterprise_entries = [
        _entry(
            201,
            "地面瓷砖及粘接层凿除",
            price="25",
            version_id=9,
            quota_code="ZS00001",
            work_content="瓷砖及粘接层拆除、清理并运至指定地点",
        )
    ]
    monkeypatch.setattr(
        "app.services.quote_pricing_cascade._load_account_catalog",
        lambda db, current_user: (
            [],
            {"status": "available", "account_id": 7, "eligible_item_count": 0},
        ),
    )
    monkeypatch.setattr(
        "app.services.quote_pricing_cascade._load_enterprise_catalog",
        lambda db: (
            enterprise_entries,
            {
                "status": "available",
                "version_id": 9,
                "version_code": "V9",
                "version_name": "企业定额V9",
                "eligible_item_count": 1,
            },
        ),
    )

    async def fake_ai_batch(snapshots, *, current_user):
        return {
            snapshots[0]["source_row_key"]: {
                "unit_price": "33.300000",
                "pricing_breakdown": {},
                "confidence": 0.6,
                "basis": "AI测试估价",
                "risks": ["需人工确认"],
                "provider": "test",
            }
        }

    monkeypatch.setattr(
        "app.services.quote_pricing_cascade.generate_budget_pricing_ai_estimate_batch",
        fake_ai_batch,
    )
    previous_flag = settings.feature_enterprise_quota_v2_review
    object.__setattr__(settings, "feature_enterprise_quota_v2_review", True)
    try:
        result = asyncio.run(
            build_quote_pricing_cascade_preview(
                object(),
                standard_rows=[
                    {
                        "requirement_row_key": "r-v2",
                        "source_sheet": "装修工程量清单",
                        "raw_row_index": 11,
                        "item_name": "地砖拆除",
                        "spec": "地砖及粘接层、垫层拆除及清运",
                        "quantity": 2,
                        "unit": "㎡",
                    }
                ],
                current_user=SimpleNamespace(id=11, username="tester"),
            )
        )
    finally:
        object.__setattr__(settings, "feature_enterprise_quota_v2_review", previous_flag)

    row = result["project_details"][0]
    review = row["enterprise_quota_v2_review"]
    assert row["pricing_tier"] == "enterprise_quota"
    assert row["unit_price"] == 25.0
    assert row["manual_unit_price"] == 25.0
    assert row["total_price"] == 50.0
    assert row["cost_reference"]["matched"] is True
    assert row["cost_reference"]["match_reason"] == "enterprise_semantic_match_auto_adopt"
    assert review["requires_manual_confirmation"] is False
    assert review["manual_confirmation_status"] == "auto_adopted"
    assert review["manual_action"] == "auto_adopt_recommended"
    assert review["selected_candidate_id"] == 201
    assert review["recommended_candidate"]["id"] == 201
    assert review["recommended_candidate"]["quota_code"] == "ZS00001"
    assert review["recommended_candidate"]["price"] == 25.0
    assert result["pricing_cascade_summary"]["enterprise_quota_matched_count"] == 1
    assert result["pricing_cascade_summary"]["ai_estimate_count"] == 0
    assert result["pricing_cascade_summary"]["enterprise_quota_v2_candidate_count"] == 1
    assert result["pricing_cascade_summary"]["enterprise_quota_v2_auto_adopted_count"] == 1
    assert result["pricing_cascade_summary"]["enterprise_quota_v2_pending_confirmation_count"] == 0


def test_v2_enterprise_candidate_push_gate_accepts_only_explicit_manual_decisions():
    base_row = {
        "project_name": "地砖拆除",
        "enterprise_quota_v2_review": {
            "requires_manual_confirmation": True,
            "manual_confirmation_status": "pending",
            "decision": "shadow_auto",
        },
    }
    assert _unconfirmed_enterprise_quota_v2_review_rows([base_row]) == [
        {
            "index": 0,
            "project_name": "地砖拆除",
            "decision": "shadow_auto",
        }
    ]

    adopted = {
        **base_row,
        "enterprise_quota_v2_review": {
            **base_row["enterprise_quota_v2_review"],
            "manual_confirmation_status": "confirmed_adopted",
        },
    }
    rejected = {
        **base_row,
        "enterprise_quota_v2_review": {
            **base_row["enterprise_quota_v2_review"],
            "manual_confirmation_status": "confirmed_rejected",
        },
    }
    assert _unconfirmed_enterprise_quota_v2_review_rows([adopted, rejected]) == []
