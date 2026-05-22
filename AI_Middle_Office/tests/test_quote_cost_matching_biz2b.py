import asyncio
import json
import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.cost_item import COST_STATUS_ACTIVE, COST_STATUS_ARCHIVED, COST_STATUS_DRAFT, CostItem
from app.models.quote_job import QuoteJob, QuoteJobEvent
from app.models.user import User
from app.services import quote_job_runner
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
