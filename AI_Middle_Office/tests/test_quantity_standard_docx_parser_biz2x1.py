from __future__ import annotations

import csv
import json
import zipfile

from app.services.quantity_standard_docx_parser import (
    build_docx_standard_library,
    build_docx_prefill_review_rows,
    parse_quantity_standard_docx,
    quantity_standard_docx_summary,
    split_numbered_items,
    write_docx_prefill_outputs,
)


def test_biz2x1_split_numbered_items_cleans_ocr_spacing():
    items = split_numbered_items(
        "1.找平层厚度、材 料种类及强度  等级 | 2.结合层厚度、材 料种类及强度  等级 | 3.面层材料品种、 规格"
    )

    assert items == [
        "找平层厚度、材料种类及强度等级",
        "结合层厚度、材料种类及强度等级",
        "面层材料品种、规格",
    ]


def test_biz2x1_parse_standard_docx_tables_and_prefill_rows(tmp_path):
    docx_path = tmp_path / "gbt50854_sample.docx"
    _write_minimal_docx(
        docx_path,
        [
            ["项目编码", "项目名称", "项目特征", "计量单位", "工程量计算规则", "工作内容"],
            [
                "011102003",
                "块料楼地面",
                "1.找平层厚度、材 料种类及强度 等级\n2.结合层厚度、材料种类及强度等级\n3.面层材料品种、规格\n4.勾缝材料种类\n5.防护层材料种类\n6.面层处理方式",
                "m²",
                "按设计图示尺寸以面积计算。门洞、空圈、暖气包槽、壁龛的开口部分并人相应的工程量内",
                "1.基层清理\n2.找平层铺设\n3.面层铺设、磨边",
            ],
        ],
    )

    parsed = parse_quantity_standard_docx(docx_path)
    summary = quantity_standard_docx_summary(parsed)
    rows = build_docx_prefill_review_rows(parsed)

    assert summary["standard_table_count"] == 1
    assert summary["standard_item_count"] == 1
    assert summary["feature_field_count"] == 6
    assert summary["auto_corrected_item_count"] == 1
    assert summary["warning_item_count"] == 0
    assert parsed.rows[0].item_code == "011102003"
    assert parsed.rows[0].feature_fields[-1] == "面层处理方式"
    assert parsed.rows[0].warnings == ()
    assert parsed.rows[0].quantity_rule.endswith("并入相应的工程量内")
    assert parsed.rows[0].corrections == ("已按确认规则自动修正：并人->并入",)
    assert len(rows) == 6
    assert rows[0]["官方项目编码（自动识别）"] == "011102003"
    assert rows[0]["官方项目特征字段（自动识别）"] == "找平层厚度、材料种类及强度等级"
    assert rows[0]["自动修正说明"] == "已按确认规则自动修正：并人->并入"
    assert rows[0]["识别风险提示"] == ""
    assert list(rows[0])[-1] == "人工核验结论（通过/有问题）"


def test_biz2x1_parse_header_ocr_variant_and_write_outputs(tmp_path):
    docx_path = tmp_path / "gbt50854_header_variant.docx"
    _write_minimal_docx(
        docx_path,
        [
            ["项日编码", "项日名称", "项日特征", "计量单位", "工程量计算规则", "工作内容"],
            [
                "011102003",
                "块料楼地面",
                "1.找平层厚度、材料种类及强度等级",
                "m²",
                "按设计图示尺寸以面积计算。",
                "1.基层清理",
            ],
        ],
    )

    parsed = parse_quantity_standard_docx(docx_path)
    outputs = write_docx_prefill_outputs(parsed, tmp_path, stem="word_prefill")

    csv_path = tmp_path / "word_prefill.csv"
    json_path = tmp_path / "word_prefill.json"
    md_path = tmp_path / "word_prefill.md"

    assert outputs == {"markdown": str(md_path), "csv": str(csv_path), "json": str(json_path)}
    assert csv_path.exists()
    assert json_path.exists()
    assert md_path.exists()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert csv_rows[0]["识别风险提示"] == ""
    assert csv_rows[0]["自动修正说明"] == ""
    assert payload["summary"]["standard_item_count"] == 1
    assert payload["summary"]["warning_item_count"] == 0
    assert payload["summary"]["auto_corrected_item_count"] == 0


def test_biz2x1_build_active_standard_library_from_docx(tmp_path):
    docx_path = tmp_path / "gbt50854_active_library.docx"
    _write_minimal_docx(
        docx_path,
        [
            ["项目编码", "项目名称", "项目特征", "计量单位", "工程量计算规则", "工作内容"],
            [
                "011102003",
                "块料楼地面",
                "1.找平层厚度、材料种类及强度等级\n2.面层材料品种、规格",
                "m²",
                "按设计图示尺寸以面积计算。",
                "1.基层清理",
            ],
            [
                "011999999",
                "无项目特征示例",
                "",
                "项",
                "按设计图示数量计算。",
                "",
            ],
        ],
    )

    parsed = parse_quantity_standard_docx(docx_path)
    library = build_docx_standard_library(parsed)

    assert library["version"] == "biz2x-gbt50854-2024-standard-v0"
    assert len(library["items"]) == 2
    assert library["items"][0]["status"] == "active"
    assert library["items"][0]["verification_status"] == "verified_against_standard"
    assert library["items"][0]["feature_fields"][0]["name"] == "找平层厚度、材料种类及强度等级"
    assert library["items"][1]["feature_fields"] == []
    assert library["items"][1]["no_feature_fields_in_standard"] is True


def _write_minimal_docx(path, table_rows):
    rows_xml = "\n".join(
        "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{_xml_escape(cell)}</w:t></w:r></w:p></w:tc>" for cell in row) + "</w:tr>"
        for row in table_rows
    )
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      {rows_xml}
    </w:tbl>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


def _xml_escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;")
    )
