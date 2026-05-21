import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.cost_item import COST_STATUS_ACTIVE, CostItem
from app.services.quote_cost_matching import enrich_quote_payload_with_cost_refs


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _seed_cost_item(
    db,
    *,
    item_name: str,
    price: float = 5.0,
    unit: str = "m",
) -> CostItem:
    item = CostItem(
        category="BIZ-2e 测试类",
        subcategory="漏项检测",
        item_name=item_name,
        spec=None,
        unit=unit,
        price=price,
        subcontract_composite_price=price,
        price_type="combined",
        status=COST_STATUS_ACTIVE,
        source="manual",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_biz2e_suggests_baseboard_when_floor_removed(client):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        baseboard = _seed_cost_item(db, item_name=f"BIZ2e 拆除木脚线 {suffix}", price=4.0)
        payload = {
            "project_details": [
                {"project_name": f"BIZ2e 拆除复合木地板 {suffix}", "unit": "㎡", "unit_price": 12, "total_price": 120}
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    suggestions = enriched["omission_suggestions"]
    assert enriched["omission_summary"]["enabled"] is True
    assert enriched["omission_summary"]["suggestion_count"] == 1
    assert suggestions[0]["rule_id"] == "biz2e_floor_remove_baseboard"
    assert suggestions[0]["cost_item_id"] == baseboard.id
    assert suggestions[0]["reference_price"] == 4
    assert suggestions[0]["trigger_row_no"] == 1


def test_biz2e_suppresses_when_companion_item_already_present(client):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=f"BIZ2e 拆除木脚线 {suffix}", price=4.0)
        payload = {
            "project_details": [
                {"project_name": f"BIZ2e 拆除复合木地板 {suffix}", "unit": "㎡", "unit_price": 12},
                {"project_name": f"BIZ2e 拆除木脚线 {suffix}", "unit": "m", "unit_price": 4},
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    assert enriched["omission_summary"]["suggestion_count"] == 0
    assert enriched["omission_suggestions"] == []


def test_biz2e_keeps_curtain_box_light_groove_removal_quiet(client):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=f"BIZ2e 垃圾清运 {suffix}", price=200.0, unit="项")
        payload = {
            "project_details": [
                {"project_name": f"BIZ2e 窗帘盒/灯槽拆除 {suffix}", "unit": "m", "unit_price": 6, "total_price": 108}
            ]
        }

        enriched = enrich_quote_payload_with_cost_refs(db, payload)
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    assert enriched["omission_summary"]["suggestion_count"] == 0
    assert enriched["omission_suggestions"] == []
