import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.cost_item import COST_STATUS_ACTIVE, COST_STATUS_DRAFT, CostItem
from app.services.quote_cost_context import append_quote_cost_context, build_quote_cost_context


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _seed_cost_item(
    db,
    *,
    item_name: str,
    unit: str = "m",
    price: float = 6.0,
    status: str = COST_STATUS_ACTIVE,
) -> CostItem:
    item = CostItem(
        category="BIZ-2h 测试类",
        subcategory="成本前置",
        item_name=item_name,
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


def test_biz2h_builds_cost_context_from_excel_source_rows(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2h 窗帘盒灯槽拆除 {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        cost_item = _seed_cost_item(db, item_name=item_name, unit="m", price=6.0)
        _seed_cost_item(db, item_name=f"{item_name} draft", unit="m", price=1.0, status=COST_STATUS_DRAFT)

        context = build_quote_cost_context(
            db,
            "请根据 Excel 需求单报价",
            source_rows=[{"project_name": item_name, "quantity": "18", "unit": "m"}],
        )
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    assert context.matched_count == 1
    assert context.active_cost_item_count >= 1
    assert context.references[0]["cost_item_id"] == cost_item.id
    assert context.references[0]["quantity"] == 18
    assert context.references[0]["reference_unit_price"] == 6
    assert context.references[0]["reference_total"] == 108
    assert "[成本库底价强参考]" in context.text
    assert "reference_unit_price: 6.00 元/m" in context.text
    assert "reference_total: 108.00 元" in context.text
    assert "匹配类型: fuzzy_item_name" in context.text


def test_biz2h_appends_context_for_text_quote_items(client):
    suffix = uuid.uuid4().hex[:8]
    item_name = f"BIZ2h 墙面乳胶漆 {suffix}"
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", True)
    try:
        _seed_cost_item(db, item_name=item_name, unit="㎡", price=18.5)

        query, context = append_quote_cost_context(db, f"{item_name} 5㎡")
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    assert context.matched_count == 1
    assert query.startswith(f"{item_name} 5㎡")
    assert "[成本库底价强参考]" in query
    assert "数量: 5" in query
    assert "reference_unit_price: 18.50 元/㎡" in query


def test_biz2h_keeps_query_unchanged_when_feature_disabled(client):
    db = SessionLocal()
    old_flag = _set_flag("feature_cost_db", False)
    try:
        query, context = append_quote_cost_context(
            db,
            "BIZ2h 未启用成本库 1m",
            source_rows=[{"project_name": "BIZ2h 未启用成本库", "quantity": "1", "unit": "m"}],
        )
    finally:
        _set_flag("feature_cost_db", old_flag)
        db.close()

    assert query == "BIZ2h 未启用成本库 1m"
    assert context.matched_count == 0
    assert context.text == ""
