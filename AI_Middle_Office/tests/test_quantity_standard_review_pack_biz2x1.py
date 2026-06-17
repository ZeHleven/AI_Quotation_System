from __future__ import annotations

import csv
import json

from app.services.quantity_standard_library import (
    DEFAULT_STANDARD_LIBRARY_PATH,
    build_quantity_standard_business_review_rows,
    build_quantity_standard_review_markdown,
    build_quantity_standard_review_rows,
    load_quantity_standard_library,
    quantity_standard_review_summary,
    write_quantity_standard_review_pack,
)


def test_biz2x1_review_rows_expand_candidate_feature_fields():
    library = load_quantity_standard_library(DEFAULT_STANDARD_LIBRARY_PATH)
    rows = build_quantity_standard_review_rows(library)

    expected_rows = sum(len(item.feature_fields) for item in library.items)

    assert len(rows) == expected_rows
    assert {row["review_status"] for row in rows} == {"pending"}
    assert {row["activation_gate"] for row in rows} == {"not_ready_pending_standard_review"}
    assert all(row["candidate_feature_field"] for row in rows)
    assert all(row["official_feature_field"] == "" for row in rows)
    assert all(row["official_quantity_rule"] == "" for row in rows)
    assert all(row["review_result"] == "" for row in rows)


def test_biz2x1_business_review_rows_use_chinese_headers_and_status_labels():
    library = load_quantity_standard_library(DEFAULT_STANDARD_LIBRARY_PATH)
    rows = build_quantity_standard_business_review_rows(library)

    assert rows
    assert "核验状态" in rows[0]
    assert "官方项目特征字段（人工填写）" in rows[0]
    assert "官方工程量计算规则（人工填写）" in rows[0]
    assert list(rows[0].keys())[-1] == "核验结论（通过/有问题）"
    assert rows[0]["核验状态"] == "待核验"
    assert rows[0]["启用门禁说明"] == "未完成标准核验，不能启用为正式标准条目"
    assert rows[0]["候选状态"] == "草稿，待标准核验"
    assert rows[0]["核验结论（通过/有问题）"] == ""


def test_biz2x1_review_summary_keeps_final_generation_blocked():
    library = load_quantity_standard_library(DEFAULT_STANDARD_LIBRARY_PATH)
    rows = build_quantity_standard_review_rows(library)
    summary = quantity_standard_review_summary(library, rows)

    assert summary["review_row_count"] == len(rows)
    assert summary["activation_ready"] is False
    assert summary["safe_for_final_generation"] is False
    assert "官方工程量计算规则（人工填写）" in summary["required_manual_review_fields"]
    assert "核验结论（通过/有问题）" in summary["required_manual_review_fields"]


def test_biz2x1_review_markdown_explains_standard_rule_constraints():
    library = load_quantity_standard_library(DEFAULT_STANDARD_LIBRARY_PATH)
    markdown = build_quantity_standard_review_markdown(library)

    assert "BIZ-2x-1" in markdown
    assert "官方项目特征字段" in markdown
    assert "官方工程量计算规则" in markdown
    assert "核验结论（通过/有问题）" in markdown
    assert "verified_against_standard" in markdown


def test_biz2x1_review_pack_writes_csv_json_and_markdown(tmp_path):
    library = load_quantity_standard_library(DEFAULT_STANDARD_LIBRARY_PATH)

    outputs = write_quantity_standard_review_pack(library, tmp_path, stem="标准库人工校对表")

    csv_path = tmp_path / "标准库人工校对表.csv"
    json_path = tmp_path / "标准库人工校对表.json"
    markdown_path = tmp_path / "标准库人工校对表.md"

    assert outputs == {
        "markdown": str(markdown_path),
        "csv": str(csv_path),
        "json": str(json_path),
    }
    assert csv_path.exists()
    assert json_path.exists()
    assert markdown_path.exists()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert len(csv_rows) == payload["summary"]["review_row_count"]
    assert {"候选标准项目编码（系统内部）", "官方项目特征字段（人工填写）", "官方工程量计算规则（人工填写）"} <= set(csv_rows[0])
    assert list(csv_rows[0])[-1] == "核验结论（通过/有问题）"
    assert csv_rows[0]["核验状态"] == "待核验"
    assert payload["business_review_rows"][0]["核验状态"] == "待核验"
    assert payload["summary"]["activation_ready"] is False
