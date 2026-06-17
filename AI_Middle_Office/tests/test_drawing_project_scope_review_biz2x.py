from __future__ import annotations

from app.services.drawing_project_scope_review import (
    ACTION_CONFIRM_UNIT,
    ACTION_HOLD_OTHER_SPECIALTY,
    ACTION_KEEP_GBT,
    ACTION_KEEP_SUPPLEMENTAL,
    build_project_scope_review,
    find_scope_review_for_mapping_entry,
)
from app.services.drawing_project_standard_mapping import (
    MAPPING_STATUS_MERGE,
    MAPPING_STATUS_SUPPLEMENTAL,
)


def test_biz2x_scope_review_classifies_unit_conflict_and_supplemental_boundaries():
    review = build_project_scope_review(
        {
            "summary": {"mapping_entry_count": 4},
            "rows": [
                {
                    "mapping_id": "BIZ2xMAP-0001",
                    "source_sheet_name": "装修清单",
                    "source_row_no": 12,
                    "manual_item_name": "灯槽",
                    "manual_unit": "m",
                    "category": "ceiling",
                    "mapping_status": MAPPING_STATUS_MERGE,
                    "standard_item_code": "011302003",
                    "standard_item_name": "艺术造型 | 吊顶天棚",
                    "standard_unit_options": ["㎡"],
                    "unit_check_status": "unit_conflict_needs_confirmation",
                },
                {
                    "mapping_id": "BIZ2xMAP-0002",
                    "source_sheet_name": "拆除清单",
                    "source_row_no": 8,
                    "manual_item_name": "拆除单开实木门",
                    "manual_unit": "樘",
                    "category": "demolition",
                    "mapping_status": MAPPING_STATUS_SUPPLEMENTAL,
                    "unit_check_status": "supplemental_uses_manual_unit",
                },
                {
                    "mapping_id": "BIZ2xMAP-0003",
                    "source_sheet_name": "安装清单",
                    "source_row_no": 30,
                    "manual_item_name": "灯具安装",
                    "manual_unit": "套",
                    "category": "lighting_electrical",
                    "mapping_status": MAPPING_STATUS_SUPPLEMENTAL,
                    "unit_check_status": "supplemental_uses_manual_unit",
                },
                {
                    "mapping_id": "BIZ2xMAP-0004",
                    "source_sheet_name": "装修清单",
                    "source_row_no": 42,
                    "manual_item_name": "瓷砖地面CT-01",
                    "manual_unit": "㎡",
                    "category": "floor",
                    "mapping_status": MAPPING_STATUS_MERGE,
                    "standard_item_code": "011102003",
                    "standard_item_name": "块料楼地面",
                    "standard_unit_options": ["㎡"],
                    "unit_check_status": "matched",
                },
            ],
        }
    )

    assert review["summary"]["scope_review_entry_count"] == 4
    assert review["summary"]["issue_row_count"] == 2
    assert review["summary"]["review_action_counts"][ACTION_CONFIRM_UNIT] == 1
    assert review["summary"]["review_action_counts"][ACTION_KEEP_SUPPLEMENTAL] == 1
    assert review["summary"]["review_action_counts"][ACTION_HOLD_OTHER_SPECIALTY] == 1
    assert review["summary"]["review_action_counts"][ACTION_KEEP_GBT] == 1

    unit_conflict = find_scope_review_for_mapping_entry(
        {"mapping_id": "BIZ2xMAP-0001"},
        review,
    )
    assert unit_conflict["review_action"] == ACTION_CONFIRM_UNIT
    assert unit_conflict["scope_bucket"] == "unit_conflict"
    assert unit_conflict["recognition_min_score"] == 0.72
    assert unit_conflict["business_confirmation_required"] is True
    assert "单位口径确认" in unit_conflict["final_quantity_status"]

    demolition = find_scope_review_for_mapping_entry(
        {"source_sheet_name": "拆除清单", "source_row_no": 8, "manual_item_name": "拆除单开实木门"},
        review,
    )
    assert demolition["review_action"] == ACTION_KEEP_SUPPLEMENTAL
    assert demolition["scope_bucket"] == "supplemental_demolition"
    assert demolition["recognition_min_score"] == 0.66

    other_specialty = find_scope_review_for_mapping_entry({"mapping_id": "BIZ2xMAP-0003"}, review)
    assert other_specialty["review_action"] == ACTION_HOLD_OTHER_SPECIALTY
    assert other_specialty["scope_bucket"] == "other_specialty_scope_pending"
    assert other_specialty["recognition_min_score"] == 0.82
    assert other_specialty["business_confirmation_required"] is True

    floor = find_scope_review_for_mapping_entry({"mapping_id": "BIZ2xMAP-0004"}, review)
    assert floor["review_action"] == ACTION_KEEP_GBT
    assert floor["scope_bucket"] == "gbt_standard_or_merge"
    assert floor["business_confirmation_required"] is False
