from __future__ import annotations

import json

import pytest

from app.services.quantity_standard_library import (
    ACTIVE_STATUS,
    DEFAULT_STANDARD_LIBRARY_PATH,
    DRAFT_STATUS,
    QuantityStandardLibraryError,
    build_quantity_standard_csv_rows,
    build_quantity_standard_markdown,
    load_quantity_standard_library,
    quantity_standard_summary,
    search_quantity_standard_items,
)


def test_biz2x1_seed_loads_as_draft_only_standard_library():
    library = load_quantity_standard_library(DEFAULT_STANDARD_LIBRARY_PATH)
    summary = quantity_standard_summary(library)

    assert summary["standard_code"] == "GBT50854-2024"
    assert summary["item_count"] >= 10
    assert summary["active_count"] == 0
    assert summary["draft_count"] == summary["item_count"]
    assert summary["safe_for_final_generation"] is False
    assert all(item.status == DRAFT_STATUS for item in library.items)


def test_biz2x1_seed_items_keep_required_feature_fields_and_quantity_rules():
    library = load_quantity_standard_library(DEFAULT_STANDARD_LIBRARY_PATH)

    for item in library.items:
        assert item.item_code
        assert item.item_name
        assert item.unit_options
        assert item.feature_fields
        assert item.required_feature_names
        assert item.quantity_rule.get("rule_text")
        assert item.quantity_rule_status == "candidate_needs_standard_verification"
        assert item.drawing_evidence_requirements


def test_biz2x1_search_returns_draft_candidates_but_can_hide_them():
    library = load_quantity_standard_library(DEFAULT_STANDARD_LIBRARY_PATH)

    draft_matches = search_quantity_standard_items(library, "墙面乳胶漆", include_draft=True)
    active_matches = search_quantity_standard_items(library, "墙面乳胶漆", include_draft=False)

    assert draft_matches
    assert draft_matches[0]["item"]["item_name"] in {"墙面喷刷涂料", "天棚喷刷涂料"}
    assert active_matches == []


def test_biz2x1_active_item_requires_verified_standard_rule(tmp_path):
    raw = json.loads(DEFAULT_STANDARD_LIBRARY_PATH.read_text(encoding="utf-8"))
    raw["items"][0]["status"] = ACTIVE_STATUS
    active_path = tmp_path / "invalid_active_standard.json"
    active_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(QuantityStandardLibraryError, match="verified_against_standard"):
        load_quantity_standard_library(active_path)


def test_biz2x1_active_item_can_explicitly_have_no_standard_feature_fields(tmp_path):
    raw = json.loads(DEFAULT_STANDARD_LIBRARY_PATH.read_text(encoding="utf-8"))
    item = raw["items"][0]
    item["status"] = ACTIVE_STATUS
    item["verification_status"] = "verified_against_standard"
    item["feature_fields"] = []
    item["no_feature_fields_in_standard"] = True
    item["quantity_rule"]["rule_status"] = "verified_against_standard"
    active_path = tmp_path / "valid_active_no_feature_fields.json"
    active_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    library = load_quantity_standard_library(active_path)

    assert library.items[0].status == ACTIVE_STATUS
    assert library.items[0].feature_fields == ()
    assert library.items[0].no_feature_fields_in_standard is True


def test_biz2x1_exports_markdown_and_csv_rows():
    library = load_quantity_standard_library(DEFAULT_STANDARD_LIBRARY_PATH)
    markdown = build_quantity_standard_markdown(library)
    rows = build_quantity_standard_csv_rows(library)

    assert "项目特征必须按标准库 feature_fields 字段口径生成" in markdown
    assert "工程量必须按标准库 quantity_rule 计算规则生成" in markdown
    assert len(rows) == len(library.items)
    assert {"item_code", "item_name", "quantity_rule_text"} <= set(rows[0])
