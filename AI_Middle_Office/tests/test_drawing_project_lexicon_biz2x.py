from __future__ import annotations

from app.services.drawing_project_lexicon import (
    build_project_lexicon_from_answer_rows,
    classify_project_category,
    extract_material_codes,
    match_source_signal_to_lexicon,
)


def test_biz2x_project_lexicon_classifies_sample_answer_items():
    assert classify_project_category("拆除单开实木门") == "demolition"
    assert classify_project_category("瓷砖地面CT-01") == "floor"
    assert classify_project_category("轻钢龙骨防水石膏板造型吊顶") == "ceiling"
    assert classify_project_category("台盆供货及安装") == "sanitary"
    assert extract_material_codes("瓷砖地面CT-01，人造石窗台石PM-01") == ["CT-01", "PM-01"]


def test_biz2x_project_lexicon_matches_drawing_signal():
    lexicon = build_project_lexicon_from_answer_rows(
        [
            {
                "row_no": 12,
                "sheet_name": "人工清单",
                "item_name": "瓷砖地面CT-01",
                "feature": "800*800地砖",
                "unit": "㎡",
                "quantity": "12.5",
                "raw_text": "瓷砖地面CT-01；800*800地砖",
            },
            {
                "row_no": 2,
                "sheet_name": "人工清单",
                "item_name": "拆除单开实木门",
                "feature": "",
                "unit": "樘",
                "quantity": "3",
                "raw_text": "拆除单开实木门",
            },
        ]
    )

    matches = match_source_signal_to_lexicon(
        {
            "source_name": "餐厅瓷砖地面CT-01",
            "evidence_text": "餐厅瓷砖地面CT-01 800*800地砖",
        },
        lexicon,
    )

    assert matches
    assert matches[0]["entry"]["manual_item_name"] == "瓷砖地面CT-01"
    assert matches[0]["entry"]["category"] == "floor"
    assert "CT-01" in matches[0]["entry"]["material_codes"]
