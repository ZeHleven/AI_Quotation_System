from __future__ import annotations

import json
from pathlib import Path

from app.services.drawing_quantity_list_draft import (
    build_quantity_list_draft,
    read_system_processed_candidates_json,
)


def test_stage5_builds_four_field_rows_only_from_confirmed_candidates(tmp_path: Path) -> None:
    candidates = [
        _candidate("QC0001", "材料/做法", "+50mm黑色拉丝不锈钢踢脚线", specs=["50mm"], decision="确认有效"),
        _candidate("QC0002", "材料/做法", "X1200白色墙面砖", specs=["600X1200"], decision="待VLM"),
        _candidate("QC0003", "拆除项", "原有10mm钢化磨砂玻璃", decision="暂缓"),
    ]

    report = build_quantity_list_draft(candidates=candidates, output_dir=tmp_path / "stage5")

    rows = report["quantity_list_draft_rows"]
    assert report["summary"]["selected_candidate_count"] == 1
    assert len(rows) == 1
    assert rows[0]["candidate_id"] == "QC0001"
    assert rows[0]["项目名称"] == "+50mm黑色拉丝不锈钢踢脚线"
    assert rows[0]["单位"] == "m"
    assert rows[0]["工程量"] == ""
    assert rows[0]["generation_status_cn"] == "缺工程量"
    assert Path(report["outputs"]["quantity_list_draft_json"]).exists()
    assert Path(report["outputs"]["quantity_list_four_fields_csv"]).exists()


def test_stage5_extracts_single_quantity_and_marks_usable_draft(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "QC0001",
            "拆除项",
            "拆除墙面墙纸",
            specs=["墙面"],
            quantities=["17.39㎡"],
            decision="确认有效",
        )
    ]

    report = build_quantity_list_draft(candidates=candidates, output_dir=tmp_path / "stage5")

    row = report["quantity_list_draft_rows"][0]
    assert row["单位"] == "㎡"
    assert row["工程量"] == "17.39㎡"
    assert row["generation_status_cn"] == "可用草案"
    assert report["summary"]["usable_draft_count"] == 1


def test_stage5_does_not_guess_when_multiple_quantities_exist(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "QC0001",
            "设备/构件",
            "铝合金玻璃门",
            specs=["宽度2200，高度2400"],
            quantities=["2.10㎡；5.16㎡"],
            decision="确认有效",
        )
    ]

    report = build_quantity_list_draft(candidates=candidates, output_dir=tmp_path / "stage5")

    row = report["quantity_list_draft_rows"][0]
    assert row["单位"] == "樘"
    assert row["工程量"] == ""
    assert row["quantity_status_cn"] == "待确认"
    assert row["generation_status_cn"] == "工程量待确认"


def test_stage5_unit_inference_prefers_item_name_over_attached_specs(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "QC0001",
            "材料/做法",
            "成品木饰面",
            specs=["线型灯规格3.0（宽）×1.2（高）×100（长）cm"],
            decision="确认有效",
        )
    ]

    report = build_quantity_list_draft(candidates=candidates, output_dir=tmp_path / "stage5")

    row = report["quantity_list_draft_rows"][0]
    assert row["单位"] == "㎡"
    assert row["generation_status_cn"] == "缺工程量"


def test_stage5_marks_note_like_candidate_name_as_pending_even_with_quantity(tmp_path: Path) -> None:
    candidates = [
        _candidate(
            "QC0001",
            "材料/做法",
            "注:1、所有隔墙高度均置顶",
            quantities=["17.39㎡"],
            decision="确认有效",
        )
    ]

    report = build_quantity_list_draft(candidates=candidates, output_dir=tmp_path / "stage5")

    row = report["quantity_list_draft_rows"][0]
    assert row["工程量"] == "17.39㎡"
    assert row["name_quality_cn"] == "待确认"
    assert row["generation_status_cn"] == "项目名称待确认"


def test_stage5_reads_system_processed_candidate_json(tmp_path: Path) -> None:
    path = tmp_path / "quote_candidates_system_processed.json"
    path.write_text(
        json.dumps({"quote_candidates": [_candidate("QC0001", "材料/做法", "墙面砖", decision="确认有效")]}, ensure_ascii=False),
        encoding="utf-8",
    )

    rows = read_system_processed_candidates_json(path)

    assert len(rows) == 1
    assert rows[0]["candidate_id"] == "QC0001"


def _candidate(
    candidate_id: str,
    candidate_type: str,
    name: str,
    *,
    specs: list[str] | None = None,
    quantities: list[str] | None = None,
    codes: list[str] | None = None,
    decision: str = "确认有效",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "draft_item_name": name,
        "draft_item_feature": "；".join([name, *(specs or []), *(codes or [])]),
        "attached_specs": specs or [],
        "attached_quantity_clues": quantities or [],
        "attached_codes": codes or [],
        "primary_evidence_ids": [f"T-{candidate_id}"],
        "image_files": [f"{candidate_id}.png"],
        "system_decision_cn": decision,
        "system_next_stage_bucket_cn": "材料/做法归并" if decision == "确认有效" else "暂缓补证据",
    }
