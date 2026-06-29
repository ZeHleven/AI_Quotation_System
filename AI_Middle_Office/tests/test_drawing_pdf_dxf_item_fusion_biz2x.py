from pathlib import Path

from app.services.drawing_pdf_dxf_item_fusion import (
    build_pdf_dxf_item_fusion_report,
    write_pdf_dxf_item_fusion_outputs,
)


def test_pdf_dxf_fusion_prefers_pdf_specific_name_and_keeps_dwg_quantity(tmp_path):
    report = build_pdf_dxf_item_fusion_report(
        dwg_quantity_list_rows=[
            {
                "项目名称": "地砖铺贴（块料楼地面）",
                "项目特征": "DWG材料编号：CT-02",
                "单位": "㎡",
                "工程量": "25.2",
            }
        ],
        dwg_project_rows=[
            {
                "识别项目编号": "BIZ2xP-0001",
                "图纸项目名称": "地砖铺贴",
                "标准项目编码": "011102003",
                "项目名称": "块料楼地面",
                "项目特征": "DWG材料编号：CT-02",
            }
        ],
        pdf_direct_report={
            "standard_mapping_rows": [
                {
                    "识别编号": "PDFITEM-000001",
                    "标准项目编码": "011102003",
                    "标准项目名称": "块料楼地面",
                    "标准单位": "㎡",
                    "项目特征": "PDF识别：CT-02 600x1200灰色地砖",
                    "工程量": "待算量",
                    "source_item": {
                        "图纸项目名称": "地砖铺贴",
                        "材料编号": "CT-02",
                    },
                }
            ]
        },
    )

    assert report["summary"]["fused_quantity_list_count"] == 1
    assert report["summary"]["fusion_duplicate_suppressed_count"] == 1
    assert report["quantity_list_rows"] == [
        {
            "项目名称": "地砖铺贴（块料楼地面）",
            "项目特征": "PDF识别：CT-02 600x1200灰色地砖；DWG材料编号：CT-02",
            "单位": "㎡",
            "工程量": "25.2",
        }
    ]
    assert report["fusion_rows"][0]["融合来源"] == "PDF直接识图+DWG/DXF"

    outputs = write_pdf_dxf_item_fusion_outputs(report, tmp_path, stem="fusion")
    assert Path(outputs["json"]).exists()
    assert Path(outputs["fusion_csv"]).read_text(encoding="utf-8-sig").startswith("融合行号")


def test_pdf_dxf_fusion_keeps_distinct_specific_items_under_same_standard():
    report = build_pdf_dxf_item_fusion_report(
        dwg_quantity_list_rows=[],
        dwg_project_rows=[],
        pdf_direct_report={
            "standard_mapping_rows": [
                {
                    "识别编号": "PDFITEM-000001",
                    "标准项目编码": "011302001",
                    "标准项目名称": "天棚喷刷涂料",
                    "标准单位": "㎡",
                    "项目特征": "涂料品种：白色防潮无机涂料",
                    "工程量": "待算量",
                    "source_item": {"图纸项目名称": "白色防潮无机涂料天棚"},
                },
                {
                    "识别编号": "PDFITEM-000002",
                    "标准项目编码": "011302001",
                    "标准项目名称": "天棚喷刷涂料",
                    "标准单位": "㎡",
                    "项目特征": "涂料品种：黑色防潮无机涂料",
                    "工程量": "待算量",
                    "source_item": {"图纸项目名称": "黑色防潮无机涂料天棚"},
                },
            ]
        },
    )

    assert report["summary"]["fused_quantity_list_count"] == 2
    assert [row["项目名称"] for row in report["quantity_list_rows"]] == [
        "白色防潮无机涂料天棚（天棚喷刷涂料）",
        "黑色防潮无机涂料天棚（天棚喷刷涂料）",
    ]
