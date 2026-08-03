from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.dxf_table_field_convergence import (
    append_drawing_annotation_rows,
    build_drawing_catalog_csv_rows,
    build_drawing_annotation_csv_rows,
    build_material_method_csv_rows,
    converge_table_fields,
    write_field_convergence_outputs,
)


def _cell(text: str) -> dict[str, object]:
    return {
        "text": text,
        "x": 0,
        "y": 0,
        "layer": "W-文字",
        "layout": "Model",
        "line_number": 1,
        "role_tags": [],
    }


def _row(*texts: str) -> dict[str, object]:
    return {
        "row_text": " | ".join(texts),
        "cells": [_cell(text) for text in texts],
    }


def _sample_table_report() -> dict[str, object]:
    return {
        "ok": True,
        "phase": "BIZ-2x-3",
        "summary": {
            "table_candidate_count": 3,
            "table_type_counts": {"drawing_catalog": 1, "material_table": 1, "construction_method": 1},
        },
        "table_candidates": [
            {
                "source_file": "01.前言文件.dxf",
                "table_type": "drawing_catalog",
                "anchor_text": "施工图目录表",
                "rows": [
                    _row("序号", "图纸名称", "图纸编号", "图幅", "序号", "图纸名称", "图纸编号", "图幅"),
                    _row("001", "施工图封面", "F-Z01", "A2", "031", "电气设计说明", "DS-说明", "A2"),
                    _row("014", "职工餐厅地面铺装图", "1F-P04", "A2"),
                ],
            },
            {
                "source_file": "02.通用节点【一】.dxf",
                "table_type": "material_table",
                "anchor_text": "石膏板刮瓷刷无机涂料材料说明",
                "rows": [
                    _row("石膏板刮瓷刷无机涂料材料说明"),
                    _row("9.5厚纸面石膏板", "满刮2厚面层耐水腻子", "无机涂料饰面"),
                ],
            },
            {
                "source_file": "02.通用节点【一】.dxf",
                "table_type": "construction_method",
                "anchor_text": "做法详图",
                "rows": [
                    _row("厨卫地面做法", "20厚1:3水泥砂浆结合层", "1.5厚聚氨酯涂膜防水三遍"),
                    _row("无机涂料与透光软膜做法", "轻钢龙骨吊顶"),
                ],
            },
        ],
    }


def test_biz2x3_converges_catalog_rows_with_repeated_groups():
    report = converge_table_fields(_sample_table_report())

    catalog_rows = report["drawing_catalog_rows"]
    assert report["summary"]["drawing_catalog_row_count"] == 3
    assert [row["drawing_code"] for row in catalog_rows] == ["F-Z01", "1F-P04", "DS-说明"]
    names = {row["drawing_name"] for row in catalog_rows}
    assert {"施工图封面", "电气设计说明", "职工餐厅地面铺装图"} <= names
    assert any(row["drawing_type"] == "design_note" for row in catalog_rows)
    assert any(row["drawing_type"] == "plan" for row in catalog_rows)


def test_biz2x3_converges_material_and_method_rows():
    report = converge_table_fields(_sample_table_report())

    rows = report["material_method_rows"]
    assert report["summary"]["material_method_type_counts"]["material"] >= 3
    assert report["summary"]["material_method_type_counts"]["construction_method"] >= 3
    assert any(row["material_or_method_name"] == "石膏板刮瓷刷无机涂料材料说明" for row in rows)
    assert any("聚氨酯涂膜防水" in row["material_or_method_name"] for row in rows)
    assert any(row["material_or_method_name"] == "厨卫地面做法" for row in rows)


def test_biz2x3_writes_field_convergence_outputs(tmp_path):
    report = converge_table_fields(_sample_table_report())

    outputs = write_field_convergence_outputs(report, tmp_path, stem="fields")
    catalog_rows = build_drawing_catalog_csv_rows(report)
    material_rows = build_material_method_csv_rows(report)

    assert set(outputs) == {"json", "markdown", "drawing_catalog_csv", "material_method_csv", "drawing_annotation_csv"}
    assert catalog_rows[0]["图纸名称"]
    assert material_rows[0]["名称"]
    assert json.loads((tmp_path / "fields.json").read_text(encoding="utf-8"))["ok"] is True
    assert (tmp_path / "fields_图纸目录字段.csv").read_text(encoding="utf-8-sig").startswith("来源文件")


def test_biz2x3_appends_plan_annotation_rows():
    report = converge_table_fields(_sample_table_report())
    parsed = SimpleNamespace(
        file_name="03.施工图.dxf",
        text_records=(
            SimpleNamespace(
                source_file="03.施工图.dxf",
                text="餐厅地面铺装玻化砖",
                layer="A-地面标注",
                layout="Model",
                line_number=88,
                role_tags=("plan",),
                x=10,
                y=20,
            ),
            SimpleNamespace(
                source_file="03.施工图.dxf",
                text="比例 1:100",
                layer="A-图框",
                layout="Model",
                line_number=89,
                role_tags=(),
                x=0,
                y=0,
            ),
        ),
    )

    updated = append_drawing_annotation_rows(report, [parsed])
    rows = build_drawing_annotation_csv_rows(updated)

    assert updated["summary"]["drawing_annotation_row_count"] == 1
    assert updated["drawing_annotation_rows"][0]["material_or_method_name"] == "餐厅地面铺装玻化砖"
    assert rows[0]["识别文字"] == "餐厅地面铺装玻化砖"
