from pathlib import Path

from app.services.drawing_pdf_ai_quantity_suggester import (
    apply_ai_quantity_suggestions_to_four_field_rows,
    build_pdf_ai_quantity_suggestion_report,
    write_pdf_ai_quantity_suggestion_outputs,
)


def test_pdf_ai_quantity_suggestion_builds_candidate_rows(tmp_path, monkeypatch):
    image_path = tmp_path / "p001_whole.png"
    image_path.write_bytes(b"fake png bytes")
    seen = {}

    async def fake_call_glm_pdf_quantity_suggest(base64_image, mime_type, *, quantity_context=None, username=None, trace_id=None):
        seen["base64_image"] = base64_image
        seen["mime_type"] = mime_type
        seen["quantity_context"] = quantity_context
        seen["trace_id"] = trace_id
        return {
            "raw_content": "{}",
            "quantity_suggestions": [
                {
                    "item_ref": "PDFITEM-000001",
                    "project_name": "地砖铺贴",
                    "standard_item_name": "块料楼地面",
                    "quantity": 42.6,
                    "unit": "㎡",
                    "formula": "7.10m * 6.00m = 42.60㎡",
                    "quantity_rule": "按设计图示尺寸以面积计算",
                    "evidence_text": "餐厅 CT-02 600X1200",
                    "source_page": 1,
                    "source_tile_id": "p001_whole",
                    "confidence": 0.72,
                    "risk_flags": ["尺寸来自AI视觉推断"],
                    "reason": "图中可见空间名称、材料编号和尺寸标注",
                }
            ],
        }

    monkeypatch.setattr(
        "app.services.drawing_pdf_ai_quantity_suggester.call_glm_pdf_quantity_suggest",
        fake_call_glm_pdf_quantity_suggest,
    )

    report = build_pdf_ai_quantity_suggestion_report(
        parse_report={
            "text_rows": [
                {
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "text": "餐厅 CT-02 600X1200 7100x6000",
                }
            ]
        },
        tile_report={
            "tile_rows": [
                {
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_id": "p001_whole",
                    "tile_type": "whole_page_preview",
                    "image_path": str(image_path),
                    "priority": 100,
                }
            ]
        },
        mapping_rows=[
            {
                "识别编号": "PDFITEM-000001",
                "标准项目编码": "011102003",
                "标准项目名称": "块料楼地面",
                "标准单位": "㎡",
                "项目特征": "面层材料品种、规格、颜色：CT-02 600X1200",
                "source_item": {
                    "图纸项目名称": "地砖铺贴",
                    "空间/部位": "餐厅",
                    "材料编号": "CT-02",
                    "规格/做法": "600X1200",
                    "证据文本": "CT-02 600X1200",
                },
                "standard_candidates": [
                    {
                        "quantity_rule": {
                            "rule_text": "按设计图示尺寸以面积计算",
                            "formula_type": "area",
                        }
                    }
                ],
            }
        ],
        max_visual_images=1,
        trace_id="trace-ai-quantity",
    )

    assert report["summary"]["ai_quantity_status"] == "candidate_ready_for_manual_review"
    assert report["summary"]["ai_quantity_candidate_count"] == 1
    assert report["safe_for_final_quantity_list"] is False
    assert seen["mime_type"] == "image/png"
    assert seen["trace_id"] == "trace-ai-quantity"
    assert seen["quantity_context"]["mapped_items"][0]["识别编号"] == "PDFITEM-000001"

    row = report["suggestion_rows"][0]
    assert row["识别编号"] == "PDFITEM-000001"
    assert row["建议工程量"] == "42.6"
    assert row["工程量显示值"] == "AI建议：42.6㎡，待确认"
    assert row["复核状态"] == "candidate_needs_manual_review"

    outputs = write_pdf_ai_quantity_suggestion_outputs(report, tmp_path, stem="pdf_ai_quantity")
    assert Path(outputs["pdf_ai_quantity_json"]).exists()
    assert Path(outputs["pdf_ai_quantity_markdown"]).exists()
    assert Path(outputs["pdf_ai_quantity_csv"]).read_text(encoding="utf-8-sig").startswith("候选量编号")


def test_ai_quantity_suggestions_fill_four_field_quantity_as_pending_candidate():
    rows = apply_ai_quantity_suggestions_to_four_field_rows(
        [
            {
                "项目名称": "地砖铺贴（块料楼地面）",
                "项目特征": "CT-02 600X1200",
                "单位": "㎡",
                "工程量": "待算量",
            }
        ],
        {
            "suggestion_rows": [
                {
                    "识别编号": "PDFITEM-000001",
                    "工程量显示值": "AI建议：42.6㎡，待确认",
                    "复核状态": "candidate_needs_manual_review",
                }
            ]
        },
    )

    assert rows[0]["工程量"] == "AI建议：42.6㎡，待确认"
def test_ai_quantity_suggestions_fill_missing_rows_with_mvp_rough_quantity():
    rows = apply_ai_quantity_suggestions_to_four_field_rows(
        [
            {
                "项目名称": "地面铺贴地砖（块料楼地面）",
                "项目特征": "CT-02 600X1200",
                "单位": "m²",
                "工程量": "待算量",
            },
            {
                "项目名称": "灯具安装",
                "项目特征": "LED灯具",
                "单位": "个",
                "工程量": "待算量",
            },
        ],
        {"suggestion_rows": []},
    )

    assert rows[0]["工程量"].startswith("AI粗估：")
    assert rows[0]["工程量"].endswith("m²，待确认")
    assert rows[1]["工程量"] == "AI粗估：12个，待确认"
