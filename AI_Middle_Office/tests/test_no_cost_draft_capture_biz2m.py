import uuid

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.cost_item import (
    CHANGE_TYPE_STATUS,
    COST_SOURCE_AI_SUGGESTED,
    COST_STATUS_ACTIVE,
    COST_STATUS_DRAFT,
    CostItem,
    CostItemHistory,
)
from app.models.user import User
from app.services.no_cost_draft_capture import (
    NO_COST_DRAFT_CATEGORY,
    NO_COST_DRAFT_SUBCATEGORY,
    NO_COST_NOTICE,
    analyze_no_cost_draft_candidates,
    create_no_cost_draft_items,
)


def _user() -> User:
    db = SessionLocal()
    try:
        user = User(
            username=f"biz2m_user_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash("secret123"),
            role="user",
            quota=20,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _payload(item_name: str | None = None, **overrides):
    row = {
        "project_name": item_name or f"无底价项目-{uuid.uuid4().hex[:6]}",
        "spec": "特殊定制规格",
        "quantity": 10,
        "unit": "m",
        "unit_price": 45,
        "total_price": 450,
        "notes": "供应商口头报价",
        "requirement_row_key": "sheet:1",
        "source_sheet": "清单",
        "raw_row_index": 12,
        "cost_reference": {"matched": False},
    }
    row.update(overrides)
    return {"quote_job_id": str(uuid.uuid4()), "project_details": [row]}


def test_analyze_no_cost_candidates_filters_active_reference():
    payload = {
        "project_details": [
            {
                "project_name": "定制异形收口条",
                "unit": "m",
                "unit_price": 45,
                "total_price": 450,
                "cost_reference": {"matched": False},
            },
            {
                "project_name": "窗帘盒灯槽拆除",
                "unit": "m",
                "unit_price": 6,
                "total_price": 60,
                "cost_reference": {"matched": True, "cost_item_id": 1},
            },
            {
                "project_name": "缺单价项目",
                "unit": "m",
                "unit_price": 0,
                "total_price": 0,
                "cost_reference": {"matched": False},
            },
            {
                "project_name": "仅填合计项目",
                "unit": "㎡",
                "quantity": 0,
                "unit_price": 0,
                "total_price": 100,
                "cost_reference": {"matched": False},
            },
        ]
    }

    result = analyze_no_cost_draft_candidates(payload)

    assert result["candidate_count"] == 2
    assert result["candidates"][0]["item_name"] == "定制异形收口条"
    assert result["candidates"][1]["item_name"] == "仅填合计项目"
    assert result["candidates"][1]["unit_price"] == 100
    assert result["candidates"][1]["draft_price_source"] == "confirmed_total_price_fallback"
    reasons = {item["reason"] for item in result["skipped"]}
    assert "has_active_cost_reference" in reasons
    assert "missing_positive_total_price" in reasons


def test_create_no_cost_draft_item_writes_draft_and_history(client):
    user = _user()
    payload = _payload()
    db = SessionLocal()
    try:
        result = create_no_cost_draft_items(
            db,
            user,
            payload,
            quote_job_id=payload["quote_job_id"],
            quote_history_id=123,
        )
        db.commit()

        assert result["created_count"] == 1
        created_id = result["created_items"][0]["cost_item_id"]
        item = db.query(CostItem).filter(CostItem.id == created_id).one()
        assert item.status == COST_STATUS_DRAFT
        assert item.source == COST_SOURCE_AI_SUGGESTED
        assert item.category == NO_COST_DRAFT_CATEGORY
        assert item.subcategory == NO_COST_DRAFT_SUBCATEGORY
        assert item.price == 45
        assert "quote_history_id: 123" in item.notes
        assert NO_COST_NOTICE in item.notes

        history = db.query(CostItemHistory).filter(CostItemHistory.cost_item_id == item.id).one()
        assert history.change_type == CHANGE_TYPE_STATUS
        assert history.old_status is None
        assert history.new_status == COST_STATUS_DRAFT
        assert history.changed_by == user.id
    finally:
        db.close()


def test_total_only_no_cost_row_uses_total_as_draft_price(client):
    user = _user()
    db = SessionLocal()
    try:
        item_name = f"仅填合计无底价项目-{uuid.uuid4().hex[:6]}"
        result = create_no_cost_draft_items(
            db,
            user,
            _payload(item_name=item_name, quantity=0, unit="㎡", unit_price=0, total_price=100),
            quote_history_id=124,
        )
        db.commit()

        assert result["created_count"] == 1
        assert result["created_items"][0]["price"] == 100
        item = db.query(CostItem).filter(CostItem.item_name == item_name).one()
        assert item.price == 100
        assert "draft_price_source: confirmed_total_price_fallback" in item.notes
    finally:
        db.close()


def test_total_with_quantity_derives_draft_unit_price(client):
    user = _user()
    db = SessionLocal()
    try:
        item_name = f"合计反推单价无底价项目-{uuid.uuid4().hex[:6]}"
        result = create_no_cost_draft_items(
            db,
            user,
            _payload(item_name=item_name, quantity=25, unit="㎡", unit_price=0, total_price=100),
            quote_history_id=125,
        )
        db.commit()

        assert result["created_count"] == 1
        assert result["created_items"][0]["price"] == 4
        item = db.query(CostItem).filter(CostItem.item_name == item_name).one()
        assert item.price == 4
        assert "draft_price_source: confirmed_total_divided_by_quantity" in item.notes
    finally:
        db.close()


def test_existing_draft_is_not_created_twice(client):
    user = _user()
    name = f"重复无底价项目-{uuid.uuid4().hex[:6]}"
    payload = _payload(item_name=name)
    db = SessionLocal()
    try:
        first = create_no_cost_draft_items(db, user, payload, quote_history_id=1)
        db.commit()
        second = create_no_cost_draft_items(db, user, payload, quote_history_id=2)
        db.commit()

        assert first["created_count"] == 1
        assert second["created_count"] == 0
        assert second["skipped"][0]["reason"] == "skipped_existing_draft"
        count = db.query(CostItem).filter(CostItem.item_name == name, CostItem.unit == "m").count()
        assert count == 1
    finally:
        db.close()


def test_existing_active_is_not_duplicated_as_draft(client):
    user = _user()
    name = f"已有active项目-{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        active_item = CostItem(
            category="正式成本库",
            subcategory="测试",
            item_name=name,
            spec="特殊定制规格；供应商口头报价",
            unit="m",
            price=99,
            price_type="combined",
            status=COST_STATUS_ACTIVE,
            source="manual",
            created_by=user.id,
        )
        db.add(active_item)
        db.commit()

        result = create_no_cost_draft_items(db, user, _payload(item_name=name), quote_history_id=3)
        db.commit()

        assert result["created_count"] == 0
        assert result["skipped"][0]["reason"] == "skipped_active_duplicate"
        count = db.query(CostItem).filter(CostItem.item_name == name, CostItem.unit == "m").count()
        assert count == 1
    finally:
        db.close()


def test_similar_active_no_cost_item_is_not_created_again(client):
    user = _user()
    name = f"BIZ2r similar active {uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        active_item = CostItem(
            category="official",
            subcategory="test",
            item_name=name,
            spec="night noise protection install and recycle",
            unit="m2",
            price=100,
            price_type="combined",
            status=COST_STATUS_ACTIVE,
            source="manual",
            created_by=user.id,
        )
        db.add(active_item)
        db.commit()

        result = create_no_cost_draft_items(
            db,
            user,
            _payload(item_name=name, spec="night noise protection install recycle", unit="m2"),
            quote_history_id=5,
        )
        db.commit()

        assert result["created_count"] == 0
        assert result["skipped"][0]["reason"] == "skipped_active_duplicate"
        assert result["skipped"][0]["cost_item_id"] == active_item.id
        assert db.query(CostItem).filter(CostItem.item_name == name, CostItem.unit == "m2").count() == 1
    finally:
        db.close()


def test_priced_requirement_placeholder_is_eligible_for_draft(client):
    user = _user()
    db = SessionLocal()
    try:
        result = create_no_cost_draft_items(
            db,
            user,
            _payload(requirement_placeholder=True, quote_source="requirement_placeholder", unit_price=88, total_price=880),
            quote_history_id=4,
        )
        db.commit()

        assert result["created_count"] == 1
        created_id = result["created_items"][0]["cost_item_id"]
        item = db.query(CostItem).filter(CostItem.id == created_id).one()
        assert "requirement_placeholder: true" in item.notes
    finally:
        db.close()
