import asyncio
import json
import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.cost_item import COST_STATUS_ACTIVE, COST_STATUS_ARCHIVED, COST_STATUS_DRAFT, CostItem
from app.models.quote_job import QuoteJob, QuoteJobEvent
from app.models.user import User
from app.services import quote_job_runner
from app.services.quote_cost_context import build_quote_cost_context, cost_context_references_as_source_rows
from app.services.quote_cost_matching import enrich_quote_payload_with_cost_refs


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _seed_cost_item(
    db,
    *,
    item_name: str,
    spec: str | None = None,
    status: str = COST_STATUS_ACTIVE,
    price: float = 20.0,
    unit: str = "㎡",
) -> CostItem:
    item = CostItem(
        category="BIZ-2b 测试类",
        subcategory="底价匹配",
        item_name=item_name,
        spec=spec,
        unit=unit,
        price=price,
        subcontract_composite_price=price,
        price_type="combined",
        status=status,
        source="manual",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_enrich_quote_payload_uses_exact_item_and_spec_match(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2b 水泥砂浆找平 {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        cost_item = _seed_cost_item(db, item_name=item_name, spec="厚度:30mm内", price=20.0)
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "spec": "厚度:30mm内",
                    "unit": "㎡",
                    "unit_price": 25,
                    "total_price": 250,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["matched"] is True
    assert reference["match_type"] == "exact_item_spec"
    assert reference["cost_item_id"] == cost_item.id
    assert reference["reference_price"] == 20
    assert reference["price_delta"] == 5
    assert reference["price_delta_rate"] == 0.25
    assert reference["ai_price_source"] == "pre_quote_cost_deviated"
    assert reference["ai_price_source_label"] == "偏离前置成本库"
    assert enriched["project_details"][0]["quote_explanation"]["ai_price_source"] == "pre_quote_cost_deviated"
    assert enriched["cost_reference_summary"]["matched_count"] == 1


def test_enrich_quote_payload_normalizes_n8n_item_and_remark_aliases(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2b alias row {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        cost_item = _seed_cost_item(db, item_name=item_name, unit="m", price=6.0)
        payload = {
            "project_details": [
                {
                    "item": item_name,
                    "quantity": 18,
                    "unit": "m",
                    "unit_price": 6,
                    "total_price": 108,
                    "remark": "keep original craft note",
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    assert row["project_name"] == item_name
    assert row["notes"] == "keep original craft note"
    assert row["cost_reference"]["matched"] is True
    assert row["cost_reference"]["cost_item_id"] == cost_item.id
    assert row["cost_reference"]["reference_price_source_label"] == "劳务发包综合单价"
    assert row["cost_reference"]["evidence_url"] == f"/admin/cost-db?cost_item_id={cost_item.id}"
    assert row["cost_reference"]["source_cost_item"]["item_name"] == item_name
    assert row["quote_explanation"]["ai_price_source"] == "pre_quote_cost_adopted"
    assert row["quote_explanation"]["ai_price_source_label"] == "采纳前置成本库"
    assert "AI 工作流原始返回" in row["quote_explanation"]["ai_basis"]
    assert f"#{cost_item.id}" in row["quote_explanation"]["cost_context_basis"]
    assert enriched["cost_reference_summary"]["matched_count"] == 1
    assert enriched["cost_reference_summary"]["unmatched_count"] == 0


def test_biz2w7_replaces_ai_placeholder_project_name_from_source_row(client):
    suffix = uuid.uuid4().hex[:8]
    matched_name = f"BIZ2w7 matched gypsum ceiling {suffix}"
    unmatched_name = f"BIZ2w7 custom gypsum ceiling {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        cost_item = _seed_cost_item(db, item_name=matched_name, unit="m", price=8.0)
        payload = {
            "project_details": [
                {"project_name": "item_1", "quantity": 0, "unit": "", "unit_price": 8, "total_price": 0},
                {"project_name": "item_2", "quantity": 0, "unit": "", "unit_price": 0, "total_price": 0},
            ]
        }
        enriched = enrich_quote_payload_with_cost_refs(
            db,
            payload,
            source_rows=[
                {"project_name": matched_name, "quantity": "3", "unit": "m"},
                {"project_name": unmatched_name, "quantity": "4", "unit": "m"},
            ],
        )
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    rows = enriched["project_details"]
    assert rows[0]["project_name"] == matched_name
    assert rows[0]["quantity"] == "3"
    assert rows[0]["unit"] == "m"
    assert rows[0]["cost_reference"]["cost_item_id"] == cost_item.id
    assert rows[1]["project_name"] == unmatched_name
    assert rows[1]["quantity"] == "4"
    assert rows[1]["unit"] == "m"
    assert rows[1]["cost_reference"]["matched"] is False


def test_enrich_quote_payload_ignores_excluded_context_match(client):
    suffix = uuid.uuid4().hex[:8]
    target_name = f"楼地面水泥砂浆找平 {suffix}"
    excluded_name = f"地面人字铺贴实木木地板基层（不含地面找平 {suffix}）"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        target_item = _seed_cost_item(db, item_name=target_name, unit="㎡", price=20.76)
        excluded_item = _seed_cost_item(db, item_name=excluded_name, unit="㎡", price=51.84)
        payload = {
            "project_details": [
                {
                    "project_name": f"地面找平 {suffix}",
                    "quantity": 35,
                    "unit": "㎡",
                    "unit_price": 30,
                    "total_price": 1050,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["matched"] is True
    assert reference["cost_item_id"] == target_item.id
    assert reference["cost_item_id"] != excluded_item.id
    assert reference["reference_price"] == 20.76
    assert "不含" not in reference["item_name"]


def test_enrich_quote_payload_uses_active_fuzzy_match_and_ignores_draft(client):
    suffix = uuid.uuid4().hex[:8]
    draft_name = f"BIZ2b 草稿底价 {suffix}"
    active_name = f"BIZ2b 墙面乳胶漆 {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=draft_name, spec="完全相同", status=COST_STATUS_DRAFT, price=1.0)
        active_item = _seed_cost_item(db, item_name=active_name, status=COST_STATUS_ACTIVE, price=18.5)
        payload = {
            "project_details": [
                {"project_name": f"{active_name} 含基层处理", "unit_price": 20.0},
                {"project_name": draft_name, "spec": "完全相同", "unit_price": 9.0},
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    fuzzy_reference = enriched["project_details"][0]["cost_reference"]
    draft_reference = enriched["project_details"][1]["cost_reference"]
    assert fuzzy_reference["matched"] is True
    assert fuzzy_reference["match_type"] == "fuzzy_item_name"
    assert fuzzy_reference["cost_item_id"] == active_item.id
    assert fuzzy_reference["reference_price"] == 18.5
    assert draft_reference["matched"] is False
    assert draft_reference["message"] == "无底价参考"
    assert enriched["project_details"][1]["quote_explanation"]["ai_price_source"] == "model_estimate"
    assert enriched["project_details"][1]["quote_explanation"]["ai_price_source_label"] == "无成本库参考，AI估算"


def test_enrich_quote_payload_flags_multiple_active_candidates(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2r dust enclosure {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        older_item = _seed_cost_item(db, item_name=item_name, spec="night work", unit="m", price=100)
        newer_item = _seed_cost_item(db, item_name=item_name, spec="day work", unit="m", price=100)
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "quantity": 8,
                    "unit": "m",
                    "unit_price": 100,
                    "total_price": 800,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["matched"] is True
    assert reference["cost_item_id"] == newer_item.id
    assert reference["candidate_count"] == 2
    assert reference["requires_manual_cost_candidate_confirmation"] is True
    assert {item["id"] for item in reference["alternative_cost_items"]} == {older_item.id, newer_item.id}
    assert enriched["cost_reference_summary"]["ambiguous_candidate_count"] == 1


def test_biz2w3_source_cost_reference_overrides_ai_rewritten_item(client):
    suffix = uuid.uuid4().hex[:8]
    mineral_name = f"\u8f7b\u94a2\u9f99\u9aa8\u77ff\u68c9\u677f\u540a\u9876 {suffix}"
    gypsum_name = f"\u8f7b\u94a2\u9f99\u9aa8\u77f3\u818f\u677f\u5e73\u9762\u5929\u82b1 {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        mineral_item = _seed_cost_item(
            db,
            item_name=mineral_name,
            spec="8\u5398\u62c9\u6746\u3001600mm*600mm",
            unit="\u33a1",
            price=50.19,
        )
        gypsum_item = _seed_cost_item(db, item_name=gypsum_name, unit="\u33a1", price=95.27)
        payload = {
            "project_details": [
                {
                    "project_name": gypsum_name,
                    "quantity": 10,
                    "unit": "\u33a1",
                    "unit_price": 95.27,
                    "total_price": 952.7,
                    "notes": "\u5ba2\u6237\u9700\u6c42\u4e3a600*600\u77ff\u68c9\u677f\u540a\u9876\uff0cAI\u8fd4\u56de\u4e86\u77f3\u818f\u677f\u9879\u76ee\u3002",
                }
            ]
        }
        source_rows = [
            {
                "project_name": f"600*600\u77ff\u68c9\u677f\u540a\u9876 {suffix}",
                "quantity": 10,
                "unit": "\u33a1",
                "locked_cost_item_id": mineral_item.id,
                "locked_cost_match_type": "fuzzy_item_name",
            }
        ]

        enriched = enrich_quote_payload_with_cost_refs(db, payload, source_rows=source_rows)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    reference = row["cost_reference"]
    assert reference["matched"] is True
    assert reference["cost_item_id"] == mineral_item.id
    assert reference["cost_item_id"] != gypsum_item.id
    assert reference["reference_price"] == 50.19
    assert reference["ai_rewrite_risk"] is True
    assert reference["requires_manual_ai_rewrite_confirmation"] is True
    assert reference["ai_returned_cost_item_id"] == gypsum_item.id
    assert reference["source_requirement_project_name"].startswith("600*600")
    assert reference["match_reason"].startswith("\u539f\u59cb\u9700\u6c42")
    assert "\u53e6\u547d\u4e2d" in reference["match_reason"]
    assert row["quote_explanation"]["cost_context_basis"].startswith("\u62a5\u4ef7\u8bf7\u6c42\u8fdb\u5165 N8N/Dify \u524d\uff0c\u539f\u59cb\u9700\u6c42")
    assert row["quote_explanation"]["ai_price_source"] == "pre_quote_cost_deviated"
    assert enriched["cost_reference_summary"]["ai_rewrite_risk_count"] == 1


def test_biz2w3_text_cost_context_locks_original_cost_reference(client):
    suffix = uuid.uuid4().hex[:8]
    mineral_name = f"\u8f7b\u94a2\u9f99\u9aa8\u77ff\u68c9\u677f\u540a\u9876 {suffix}"
    gypsum_name = f"\u8f7b\u94a2\u9f99\u9aa8\u77f3\u818f\u677f\u5e73\u9762\u5929\u82b1 {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        mineral_item = _seed_cost_item(
            db,
            item_name=mineral_name,
            spec="8\u5398\u62c9\u6746\u3001600mm*600mm",
            unit="\u33a1",
            price=50.19,
        )
        _seed_cost_item(db, item_name=gypsum_name, unit="\u33a1", price=95.27)
        context = build_quote_cost_context(
            db,
            f"600*600\u77ff\u68c9\u677f\u540a\u9876 {suffix}\uff0c10\u33a1",
        )
        source_rows = cost_context_references_as_source_rows(context)
        payload = {
            "project_details": [
                {
                    "project_name": gypsum_name,
                    "quantity": 10,
                    "unit": "\u33a1",
                    "unit_price": 95.27,
                    "total_price": 952.7,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload, source_rows=source_rows)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    assert context.references[0]["cost_item_id"] == mineral_item.id
    assert source_rows[0]["locked_cost_item_id"] == mineral_item.id
    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["cost_item_id"] == mineral_item.id
    assert reference["requires_manual_ai_rewrite_confirmation"] is True


def test_biz2w4_ai_note_conflict_is_sanitized_when_cost_reference_matched(client):
    suffix = uuid.uuid4().hex[:8]
    mineral_name = f"轻钢龙骨矿棉板吊顶 {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    original_note = "当前底层数据集中未包含‘600*600矿棉板吊顶’相关条目，无法提供报价。建议补充对应施工项或联系客服获取定制报价。"
    try:
        mineral_item = _seed_cost_item(
            db,
            item_name=mineral_name,
            spec="8厘拉杆、600mm*600mm",
            unit="㎡",
            price=50.19,
        )
        payload = {
            "project_details": [
                {
                    "project_name": mineral_name,
                    "quantity": 10,
                    "unit": "㎡",
                    "unit_price": 50.19,
                    "total_price": 501.9,
                    "notes": original_note,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    reference = row["cost_reference"]
    assert reference["matched"] is True
    assert reference["cost_item_id"] == mineral_item.id
    assert reference["ai_note_cost_basis_conflict"] is True
    assert reference["requires_manual_ai_note_confirmation"] is True
    assert reference["manual_ai_note_confirmed"] is False
    assert reference["ai_original_notes"] == original_note
    assert "已命中成本库参考" in row["notes"]
    assert "无法提供报价" not in row["notes"]
    assert row["ai_original_notes"] == original_note
    assert row["quote_explanation"]["ai_original_notes"] == original_note
    assert "需人工确认备注处理" in row["quote_explanation"]["comparison"]
    assert enriched["cost_reference_summary"]["ai_note_conflict_count"] == 1


def test_biz2w4_ai_note_conflict_handles_missing_this_project_phrase(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"水泥砂浆止水坎 {suffix}"
    original_note = "底层数据集中无此项目，无法报价，建议补充数据后重新计算。"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        cost_item = _seed_cost_item(
            db,
            item_name=item_name,
            spec="综合",
            unit="m",
            price=20.2,
        )
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "quantity": 16,
                    "unit": "m",
                    "unit_price": 20.2,
                    "total_price": 323.2,
                    "notes": original_note,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    reference = row["cost_reference"]
    assert reference["matched"] is True
    assert reference["cost_item_id"] == cost_item.id
    assert reference["ai_note_cost_basis_conflict"] is True
    assert reference["requires_manual_ai_note_confirmation"] is True
    assert reference["ai_original_notes"] == original_note
    assert row["ai_original_notes"] == original_note
    assert "已命中成本库参考" in row["notes"]
    assert "无此项目" not in row["notes"]
    assert "无法报价" not in row["notes"]
    assert enriched["cost_reference_summary"]["ai_note_conflict_count"] == 1


def test_biz2w4_normal_cost_note_does_not_require_manual_note_confirmation(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2w4 regular note {suffix}"
    original_note = "按600*600矿棉板标准工艺施工，现场复核吊杆间距。"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        cost_item = _seed_cost_item(db, item_name=item_name, unit="㎡", price=50.19)
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "quantity": 10,
                    "unit": "㎡",
                    "unit_price": 50.19,
                    "total_price": 501.9,
                    "notes": original_note,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    reference = row["cost_reference"]
    assert reference["matched"] is True
    assert reference["cost_item_id"] == cost_item.id
    assert reference["ai_note_cost_basis_conflict"] is False
    assert reference["requires_manual_ai_note_confirmation"] is False
    assert row["notes"] == original_note
    assert "ai_original_notes" not in row
    assert enriched["cost_reference_summary"]["ai_note_conflict_count"] == 0


def test_enrich_quote_payload_exact_spec_not_ambiguous_for_same_name(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2r access panel {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        exact_item = _seed_cost_item(db, item_name=item_name, spec="300x300", unit="pcs", price=45)
        _seed_cost_item(db, item_name=item_name, spec="600x600", unit="pcs", price=80)
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "spec": "300x300",
                    "quantity": 6,
                    "unit": "pcs",
                    "unit_price": 45,
                    "total_price": 270,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["matched"] is True
    assert reference["match_type"] == "exact_item_spec"
    assert reference["cost_item_id"] == exact_item.id
    assert reference["requires_manual_cost_candidate_confirmation"] is False


def test_biz2d_matches_symbol_and_unit_variants(client):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        cost_item = _seed_cost_item(
            db,
            item_name=f"窗帘盒/灯槽拆除 {suffix}",
            unit="m",
            price=6.0,
        )
        payload = {
            "project_details": [
                {
                    "project_name": f"窗帘盒及灯槽拆除 {suffix}",
                    "unit": "延米",
                    "unit_price": 6,
                    "total_price": 108,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["matched"] is True
    assert reference["match_type"] == "fuzzy_item_name"
    assert reference["cost_item_id"] == cost_item.id
    assert reference["reference_price"] == 6
    assert reference["price_delta"] == 0
    assert enriched["cost_reference_summary"]["unmatched_count"] == 0


def test_biz2d_matches_reordered_name_tokens(client):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        cost_item = _seed_cost_item(
            db,
            item_name=f"窗帘盒灯槽拆除 {suffix}",
            unit="m",
            price=6.0,
        )
        payload = {
            "project_details": [
                {
                    "project_name": f"拆除 {suffix} 窗帘盒灯槽",
                    "unit": "M",
                    "unit_price": 8,
                    "total_price": 144,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["matched"] is True
    assert reference["cost_item_id"] == cost_item.id
    assert reference["reference_price"] == 6
    assert reference["price_delta"] == 2


def test_biz2d_rejects_unit_incompatible_match(client):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=f"BIZ2d 墙面拆除 {suffix}", unit="㎡", price=12.0)
        payload = {
            "project_details": [
                {"project_name": f"BIZ2d 墙面拆除 {suffix}", "unit": "m", "unit_price": 12.0}
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["matched"] is False
    assert enriched["project_details"][0]["quote_explanation"]["ai_price_source"] == "model_estimate"


def test_biz2d_rejects_different_action_and_archived_rows(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2d 专用弯管 {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=f"{item_name} 安装", unit="m", price=30.0)
        _seed_cost_item(
            db,
            item_name=f"{item_name} 拆除",
            unit="m",
            price=6.0,
            status=COST_STATUS_ARCHIVED,
        )
        payload = {
            "project_details": [
                {"project_name": f"{item_name} 拆除", "unit": "m", "unit_price": 6.0}
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["matched"] is False
    assert enriched["project_details"][0]["quote_explanation"]["ai_price_source"] == "model_estimate"


def test_biz2g_applies_cost_reference_fallback_for_zero_ai_price(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2g fallback window box removal {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=item_name, unit="m", price=6.0)
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "quantity": 18,
                    "unit": "m",
                    "unit_price": 0,
                    "total_price": 0,
                    "notes": "AI未找到对应定额项目",
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    reference = row["cost_reference"]
    assert row["unit_price"] == 6
    assert row["total_price"] == 108
    assert "由成本库参考价兜底生成" in row["notes"]
    assert row["cost_reference_fallback"]["applied"] is True
    assert reference["matched"] is True
    assert reference["fallback_applied"] is True
    assert reference["ai_unit_price_before_fallback"] == 0
    assert reference["ai_unit_price"] == 6
    assert reference["price_delta"] == 0
    assert reference["price_delta_rate"] == 0
    assert reference["ai_price_source"] == "cost_reference_fallback"
    assert row["quote_explanation"]["ai_price_source_label"] == "成本库兜底"
    assert enriched["cost_reference_summary"]["fallback_applied_count"] == 1


def test_biz2g_does_not_override_positive_ai_price(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2g positive price remains ai result {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=item_name, unit="m", price=6.0)
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "quantity": 18,
                    "unit": "m",
                    "unit_price": 8,
                    "total_price": 144,
                    "notes": "AI已给出报价",
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    reference = row["cost_reference"]
    assert row["unit_price"] == 8
    assert row["total_price"] == 144
    assert "cost_reference_fallback" not in row
    assert reference.get("fallback_applied") is None
    assert reference["price_delta"] == 2
    assert enriched["cost_reference_summary"]["fallback_applied_count"] == 0


def test_biz2g_skips_fallback_without_quantity(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2g missing quantity skip fallback {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=item_name, unit="m", price=6.0)
        payload = {"project_details": [{"project_name": item_name, "unit": "m", "unit_price": 0, "total_price": 0}]}

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    reference = row["cost_reference"]
    assert row["unit_price"] == 0
    assert row["total_price"] == 0
    assert "cost_reference_fallback" not in row
    assert reference.get("fallback_applied") is None


def test_biz2g_applies_excel_source_quantity_before_fallback(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2g source row quantity fallback {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=item_name, unit="m", price=6.0)
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "unit_price": 0,
                    "total_price": 0,
                    "notes": "数据集无对应项，单价设为0",
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(
            db,
            payload,
            source_rows=[{"project_name": item_name, "quantity": "18", "unit": "m"}],
        )
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    reference = row["cost_reference"]
    assert row["quantity"] == "18"
    assert row["unit"] == "m"
    assert row["unit_price"] == 6
    assert row["total_price"] == 108
    assert reference["fallback_applied"] is True
    assert enriched["cost_reference_summary"]["fallback_applied_count"] == 1


def test_biz2n_applies_excel_source_quantity_when_ai_returns_zero(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2n source zero quantity fallback {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=item_name, unit="m", price=6.0)
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "quantity": 0,
                    "unit": "m",
                    "unit_price": 0,
                    "total_price": 0,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(
            db,
            payload,
            source_rows=[{"project_name": item_name, "quantity": "18", "unit": "m"}],
        )
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    assert row["quantity"] == "18"
    assert row["unit_price"] == 6
    assert row["total_price"] == 108
    assert row["cost_reference"]["fallback_applied"] is True


def test_biz2n_applies_excel_source_quantity_for_no_cost_row(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2n no cost source quantity {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        payload = {
            "project_details": [
                {
                    "project_name": item_name,
                    "quantity": 0,
                    "unit": "m",
                    "unit_price": 0,
                    "total_price": 100,
                }
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(
            db,
            payload,
            source_rows=[{"project_name": item_name, "quantity": "35", "unit": "m"}],
        )
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    row = enriched["project_details"][0]
    assert row["quantity"] == "35"
    assert row["cost_reference"]["matched"] is False
    assert row["quote_explanation"]["quantity"] == 35


def test_enrich_quote_payload_is_noop_when_feature_disabled(client):
    payload = {"project_details": [{"project_name": "BIZ2b 未启用", "unit_price": 10}]}
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", False)
    try:
        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    assert enriched == payload
    assert "cost_reference" not in enriched["project_details"][0]


def test_enrich_quote_payload_handles_json_string_wrappers(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2b 包装字符串 {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=item_name, status=COST_STATUS_ACTIVE, price=12.0)
        payload = {"message": json.dumps({"project_details": [{"project_name": item_name, "unit_price": 15}]}, ensure_ascii=False)}

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    reference = enriched["project_details"][0]["cost_reference"]
    assert reference["matched"] is True
    assert reference["reference_price"] == 12


def test_quote_job_runner_persists_cost_reference_on_preview(client, monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    username = f"biz2b_runner_{suffix}"
    job_id = str(uuid.uuid4())
    item_name = f"BIZ2b 异步墙砖 {suffix}"

    async def fake_load_file_content(job, db):
        return None

    async def fake_quote_events(**kwargs):
        yield (
            "preview",
            "AI preview ready",
            {
                "stage": "completed",
                "data": {
                    "project_details": [
                        {
                            "project_name": item_name,
                            "spec": "300x600",
                            "unit": "㎡",
                            "unit_price": 42,
                            "total_price": 420,
                        }
                    ]
                },
            },
        )

    old_flag = _set_flag("feature_cost_db", True)
    monkeypatch.setattr(quote_job_runner, "_load_job_file_content", fake_load_file_content)
    monkeypatch.setattr(quote_job_runner, "_iter_quote_events", fake_quote_events)
    monkeypatch.setattr(quote_job_runner, "safe_record_ai_preview", lambda *args, **kwargs: None)

    db = SessionLocal()
    try:
        db.add(User(username=username, hashed_password="x", role="user", quota=5))
        _seed_cost_item(db, item_name=item_name, spec="300x600", price=40.0)
        db.add(
            QuoteJob(
                job_id=job_id,
                username=username,
                status="queued",
                stage="queued",
                message="BIZ-2b 异步报价",
                trace_id="trace-biz2b",
            )
        )
        db.commit()
    finally:
        db.close()

    try:
        asyncio.run(quote_job_runner.run_quote_job_async(job_id))
    finally:
        _set_flag("feature_cost_db", old_flag)

    db = SessionLocal()
    try:
        job = db.query(QuoteJob).filter(QuoteJob.job_id == job_id).one()
        event = db.query(QuoteJobEvent).filter(QuoteJobEvent.quote_job_id == job_id).order_by(QuoteJobEvent.id.desc()).first()
        result = json.loads(job.result_json)
        event_payload = json.loads(event.payload_json)
    finally:
        db.close()

    reference = result["project_details"][0]["cost_reference"]
    event_reference = event_payload["data"]["project_details"][0]["cost_reference"]
    assert job.status == "succeeded"
    assert reference["matched"] is True
    assert reference["match_type"] == "exact_item_spec"
    assert reference["reference_price"] == 40
    assert event_reference["matched"] is True
