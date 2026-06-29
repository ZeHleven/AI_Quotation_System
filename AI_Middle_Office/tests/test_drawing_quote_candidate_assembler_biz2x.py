from __future__ import annotations

import json
from pathlib import Path

from app.services.drawing_quote_candidate_assembler import (
    apply_system_suggestions_to_candidates,
    build_quote_candidates,
)


def test_stage4_merges_duplicate_and_truncated_material_evidence(tmp_path: Path) -> None:
    rows = [
        _row("T001", "防水石膏板刷白色防潮无机涂料", "材料/做法", related=["T010", "T012", "T013"]),
        _row("T002", "防水石膏板刷白色防潮无机涂", "材料/做法"),
        _row("T003", "防水石膏板刷白色防潮无机涂料", "材料/做法"),
        _row("T010", "PB-01 PT-01", "轴号/索引/编号"),
        _row("T011", "750X1500", "规格尺寸", tile_id="p001_r010_c010"),
        _row("T012", "800", "规格尺寸", tile_id="p001_r001_c001"),
        _row("T013", "G", "轴号/索引/编号", tile_id="p001_r001_c001"),
    ]

    report = build_quote_candidates(classifications=rows, output_dir=tmp_path / "stage4")

    candidates = report["quote_candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["candidate_type"] == "材料/做法"
    assert candidate["source_evidence_count"] == 3
    assert set(candidate["primary_evidence_ids"]) == {"T001", "T002", "T003"}
    assert "PB-01 PT-01" in candidate["attached_codes"]
    assert "800" not in candidate["attached_specs"]
    assert "G" not in candidate["attached_codes"]
    assert Path(report["outputs"]["quote_candidates_json"]).exists()
    assert Path(report["outputs"]["quote_candidates_csv"]).exists()


def test_stage4_does_not_cross_merge_demolition_and_component(tmp_path: Path) -> None:
    rows = [
        _row("T001", "拆除实木门，高度2100，宽度900", "拆除项"),
        _row("T002", "定制成品实木门及门套", "设备/构件"),
    ]

    report = build_quote_candidates(classifications=rows, output_dir=tmp_path / "stage4")

    assert report["summary"]["candidate_count"] == 2
    by_type = {candidate["candidate_type"]: candidate for candidate in report["quote_candidates"]}
    assert by_type["拆除项"]["draft_item_name"] == "拆除实木门"
    assert by_type["设备/构件"]["draft_item_name"] == "定制成品实木门及门套"


def test_stage4_attaches_specs_but_specs_are_not_main_candidates(tmp_path: Path) -> None:
    rows = [
        _row("T001", "铝合金玻璃门", "设备/构件", related=["T002"], tile_id="p001_r010_c010"),
        _row("T002", "宽度2200，高度2400", "规格尺寸", tile_id="p001_r010_c010"),
        _row("T003", "5.16m²", "工程量/数量线索", tile_id="p001_r010_c010"),
    ]

    report = build_quote_candidates(classifications=rows, output_dir=tmp_path / "stage4")

    assert report["summary"]["candidate_count"] == 1
    candidate = report["quote_candidates"][0]
    assert candidate["candidate_type"] == "设备/构件"
    assert "宽度2200，高度2400" in candidate["attached_specs"]
    assert "5.16m²" in candidate["attached_quantity_clues"]
    assert "T002" in candidate["related_spec_evidence_ids"]
    assert "T003" in candidate["related_quantity_evidence_ids"]


def test_stage4_prioritizes_candidate_related_vlm_tasks(tmp_path: Path) -> None:
    rows = [
        _row("T001", "黑色拉丝不锈钢踢脚线", "设备/构件", related=["T002"]),
        _row("T002", "50mm", "规格尺寸", needs_vlm=True, tile_id="p001_r010_c010"),
        _row("T003", "A", "轴号/索引/编号", needs_vlm=True, tile_id="p001_r001_c001", bbox=[0.85, 0.85, 0.86, 0.86]),
    ]

    report = build_quote_candidates(classifications=rows, output_dir=tmp_path / "stage4")

    tasks = report["vlm_review_tasks"]
    by_text_id = {task["text_id"]: task for task in tasks}
    assert by_text_id["T002"]["priority"] == "P0"
    assert by_text_id["T002"]["candidate_id"] == "QC0001"
    assert by_text_id["T003"]["priority"] == "P2"
    assert report["quote_candidates"][0]["needs_vlm_review"] is True


def test_stage4_saved_json_contains_summary(tmp_path: Path) -> None:
    rows = [_row("T001", "拆除墙面墙纸", "拆除项")]

    report = build_quote_candidates(classifications=rows, output_dir=tmp_path / "stage4")

    saved = json.loads(Path(report["outputs"]["quote_candidates_json"]).read_text(encoding="utf-8"))
    assert saved["schema_version"] == "drawing_quote_candidate_v1"
    assert saved["summary"]["candidate_count"] == 1


def test_stage4_applies_system_suggestions_to_candidate_review_status() -> None:
    candidates = [
        {
            "candidate_id": "QC0001",
            "candidate_type": "材料/做法",
            "attached_specs": ["600X1200"],
            "attached_quantity_clues": [],
            "needs_vlm_review": False,
        },
        {
            "candidate_id": "QC0002",
            "candidate_type": "设备/构件",
            "attached_specs": ["宽度2200，高度2400"],
            "attached_quantity_clues": [],
            "needs_vlm_review": True,
        },
        {
            "candidate_id": "QC0003",
            "candidate_type": "拆除项",
            "attached_specs": [],
            "attached_quantity_clues": [],
            "needs_vlm_review": False,
        },
    ]

    report = apply_system_suggestions_to_candidates(candidates)

    by_id = {candidate["candidate_id"]: candidate for candidate in report["quote_candidates"]}
    assert by_id["QC0001"]["system_decision_cn"] == "确认有效"
    assert by_id["QC0001"]["system_next_stage_bucket_cn"] == "材料/做法归并"
    assert by_id["QC0002"]["system_decision_cn"] == "待VLM"
    assert by_id["QC0003"]["system_decision_cn"] == "暂缓"
    assert report["summary"]["confirm_effective_count"] == 1
    assert report["summary"]["pending_vlm_count"] == 1
    assert report["summary"]["hold_count"] == 1


def _row(
    text_id: str,
    text: str,
    category: str,
    *,
    related: list[str] | None = None,
    needs_vlm: bool = False,
    tile_id: str = "p001_r010_c010",
    bbox: list[float] | None = None,
) -> dict:
    return {
        "text_id": text_id,
        "current_text": text,
        "primary_category": category,
        "secondary_category": "",
        "is_effective": category != "噪声",
        "confidence": 0.95,
        "reason": "测试证据",
        "related_text_ids": related or [],
        "needs_vlm_review": needs_vlm,
        "vlm_review_reason": "测试需要看图" if needs_vlm else "",
        "noise_reason": "",
        "suggested_usage": [],
        "nearby_texts": [],
        "page": 1,
        "tile_id": tile_id,
        "image_path": f"C:/tmp/{text_id}.png",
        "bbox_ratio": bbox or [0.1, 0.1, 0.2, 0.2],
    }
