from __future__ import annotations

from pathlib import Path

from app.services.drawing_ocr_result_reviewer import build_ocr_result_review_report


def test_ocr_result_reviewer_classifies_material_nonmaterial_and_noise(tmp_path: Path) -> None:
    crop_path = tmp_path / "source_crop.png"
    crop_path.write_bytes(b"fake image")
    execution_plan = {
        "regions": [
            _region("r_material", bucket="primary_selected", bucket_cn="预算内主路径入选区域"),
            _region("r_company", bucket="primary_selected", bucket_cn="预算内主路径入选区域"),
            _region("r_noise", bucket="fallback_recoverable_rejected", bucket_cn="可能误拒绝兜底复核区域"),
            _region("r_overflow", bucket="fallback_overflow_budget_cut", bucket_cn="预算截断兜底复核区域"),
        ],
        "summary": {"actual_execution_region_count": 4},
    }
    quality_report = {
        "summary": {"scored_crop_count": 4},
        "crop_scores": [
            {
                "region_id": "r_material",
                "ocr_quality_label": "high",
                "ocr_quality_score": 0.91,
                "ocr_text_line_count": 3,
                "ocr_useful_line_count": 3,
                "ocr_noise_line_count": 0,
                "ocr_chinese_char_count": 18,
                "ocr_material_code_count": 1,
                "ocr_dimension_count": 1,
                "ocr_material_keyword_count": 2,
                "ocr_text_preview": ["材料名称", "CT-01 白色墙砖400x800横贴"],
                "image_path": str(crop_path),
            },
            {
                "region_id": "r_company",
                "ocr_quality_label": "high",
                "ocr_quality_score": 0.78,
                "ocr_text_line_count": 5,
                "ocr_useful_line_count": 4,
                "ocr_noise_line_count": 0,
                "ocr_chinese_char_count": 30,
                "ocr_material_code_count": 0,
                "ocr_dimension_count": 0,
                "ocr_material_keyword_count": 1,
                "ocr_text_preview": ["建设单位", "宏发建设有限公司", "设计资质证书号码"],
            },
            {
                "region_id": "r_noise",
                "ocr_quality_label": "low",
                "ocr_quality_score": 0.05,
                "ocr_text_line_count": 4,
                "ocr_useful_line_count": 0,
                "ocr_noise_line_count": 4,
                "ocr_chinese_char_count": 0,
                "ocr_material_code_count": 0,
                "ocr_dimension_count": 0,
                "ocr_material_keyword_count": 0,
                "ocr_text_preview": ["A", "B", "1", "2"],
            },
            {
                "region_id": "r_overflow",
                "ocr_quality_label": "medium",
                "ocr_quality_score": 0.55,
                "ocr_text_line_count": 2,
                "ocr_useful_line_count": 1,
                "ocr_noise_line_count": 1,
                "ocr_chinese_char_count": 8,
                "ocr_material_code_count": 0,
                "ocr_dimension_count": 1,
                "ocr_material_keyword_count": 1,
                "ocr_text_preview": ["墙面涂料", "1200"],
            },
        ],
    }

    report = build_ocr_result_review_report(
        execution_plan=execution_plan,
        quality_report=quality_report,
        output_dir=tmp_path / "review",
        business_screenshot_dir=tmp_path / "business_screenshots",
    )

    rows = {row["region_id"]: row for row in report["rows"]}
    assert rows["r_material"]["ocr_effectiveness_label"] == "effective_material_text"
    assert rows["r_company"]["ocr_effectiveness_label"] == "useful_non_material_text"
    assert rows["r_noise"]["ocr_effectiveness_label"] == "low_value_noise"
    assert rows["r_overflow"]["ocr_effectiveness_label"] in {"possible_material_text", "effective_material_text"}
    assert "材料" in rows["r_material"]["material_signal_cn"]
    assert "不要作为材料正样本" in rows["r_company"]["feedback_action_cn"]
    assert "降低同类误拒绝兜底比例" in rows["r_noise"]["feedback_action_cn"]
    assert Path(report["outputs"]["ocr_result_review_csv"]).exists()
    assert Path(report["outputs"]["ocr_result_review_markdown"]).exists()
    assert Path(report["outputs"]["ocr_result_business_review_csv"]).exists()
    assert Path(report["outputs"]["ocr_result_business_review_markdown"]).exists()
    business_markdown = Path(report["outputs"]["ocr_result_business_review_markdown"]).read_text(encoding="utf-8")
    assert "OCR 结果业务审阅简表" in business_markdown
    assert "纳入材料/做法候选" in business_markdown
    assert "人工确认" in business_markdown
    assert "business_screenshots" in business_markdown
    assert Path(report["outputs"]["ocr_result_business_screenshot_dir"]).exists()


def _region(region_id: str, *, bucket: str, bucket_cn: str) -> dict[str, object]:
    return {
        "region_id": region_id,
        "original_region_id": f"orig_{region_id}",
        "source_file": "drawing.pdf",
        "page": 1,
        "ocr_execution_bucket": bucket,
        "ocr_execution_bucket_cn": bucket_cn,
        "budget_bucket_cn": "彩色图签/材料表候选",
        "candidate_decision_cn": "测试候选决策",
        "candidate_reason_cn": "测试候选原因",
        "candidate_signal_cn": "测试候选信号",
        "candidate_risk_cn": "测试候选风险",
        "next_action_cn": "直接 OCR",
    }
