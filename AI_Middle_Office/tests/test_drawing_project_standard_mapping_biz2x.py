from __future__ import annotations

from app.services.drawing_project_standard_mapping import (
    MAPPING_STATUS_MERGE,
    MAPPING_STATUS_SUPPLEMENTAL,
    build_project_standard_mapping_from_answer_rows,
    match_source_signal_to_standard_mapping,
)
from app.services.drawing_standard_matcher import DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH
from app.services.quantity_standard_library import load_quantity_standard_library


def _library():
    return load_quantity_standard_library(DEFAULT_ACTIVE_STANDARD_LIBRARY_PATH)


def test_biz2x_standard_mapping_classifies_standard_merge_and_supplemental_rows():
    mapping = build_project_standard_mapping_from_answer_rows(
        [
            {
                "row_no": 30,
                "sheet_name": "人工清单",
                "item_name": "瓷砖地面CT-01",
                "feature": "800*800地砖",
                "unit": "㎡",
                "quantity": "12.5",
                "raw_text": "瓷砖地面CT-01 800*800地砖",
            },
            {
                "row_no": 8,
                "sheet_name": "人工清单",
                "item_name": "拆除单开实木门",
                "feature": "",
                "unit": "套",
                "quantity": "3",
                "raw_text": "拆除单开实木门",
            },
            {
                "row_no": 90,
                "sheet_name": "人工清单",
                "item_name": "台盆供货及安装",
                "feature": "",
                "unit": "套",
                "quantity": "2",
                "raw_text": "台盆供货及安装",
            },
        ],
        library=_library(),
        lexicon={"summary": {"lexicon_entry_count": 0}, "entries": []},
    )

    assert mapping["summary"]["mapping_entry_count"] == 3
    floor = next(row for row in mapping["rows"] if row["manual_item_name"] == "瓷砖地面CT-01")
    assert floor["mapping_status"] == MAPPING_STATUS_MERGE
    assert floor["standard_item_code"] == "011102003"
    assert floor["standard_item_name"] == "块料楼地面"
    assert floor["unit_check_status"] == "matched"
    assert "面层材料品种、规格" in "；".join(floor["standard_feature_fields"])
    assert "CT-01" in floor["feature_text_template"]

    demolition = next(row for row in mapping["rows"] if row["manual_item_name"] == "拆除单开实木门")
    assert demolition["mapping_status"] == MAPPING_STATUS_SUPPLEMENTAL
    assert demolition["standard_item_code"] == ""
    assert demolition["allowed_for_final_quantity"] is False

    basin = next(row for row in mapping["rows"] if row["manual_item_name"] == "台盆供货及安装")
    assert basin["mapping_status"] == MAPPING_STATUS_MERGE
    assert basin["standard_item_code"] == "011505001"
    assert basin["unit_check_status"] == "unit_conflict_needs_confirmation"


def test_biz2x_standard_mapping_matches_drawing_source_signal():
    mapping = build_project_standard_mapping_from_answer_rows(
        [
            {
                "row_no": 30,
                "sheet_name": "人工清单",
                "item_name": "瓷砖地面CT-01",
                "feature": "800*800地砖",
                "unit": "㎡",
                "quantity": "12.5",
                "raw_text": "瓷砖地面CT-01 800*800地砖",
            }
        ],
        library=_library(),
        lexicon={"summary": {"lexicon_entry_count": 0}, "entries": []},
    )

    matches = match_source_signal_to_standard_mapping(
        {
            "source_name": "餐厅瓷砖地面CT-01",
            "evidence_text": "餐厅瓷砖地面CT-01 800*800地砖",
        },
        mapping,
    )

    assert matches
    assert matches[0]["mapping"]["manual_item_name"] == "瓷砖地面CT-01"
    assert matches[0]["mapping"]["standard_item_code"] == "011102003"
