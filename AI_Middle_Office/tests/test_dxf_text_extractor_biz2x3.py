from __future__ import annotations

import json

from app.services.dxf_text_extractor import (
    build_dxf_extraction_report,
    build_dxf_text_csv_rows,
    classify_text_roles,
    clean_dxf_text,
    collect_dxf_files,
    parse_dxf_file,
    write_dxf_extraction_outputs,
)


def _sample_dxf_text() -> str:
    return "\n".join(
        [
            "  0",
            "SECTION",
            "  2",
            "HEADER",
            "  9",
            "$ACADVER",
            "  1",
            "AC1032",
            "  9",
            "$DWGCODEPAGE",
            "  3",
            "ANSI_936",
            "  0",
            "ENDSEC",
            "  0",
            "SECTION",
            "  2",
            "TABLES",
            "  0",
            "LAYER",
            "  2",
            "设计说明文字",
            "  0",
            "ENDSEC",
            "  0",
            "SECTION",
            "  2",
            "OBJECTS",
            "  0",
            "LAYOUT",
            "  1",
            "Model",
            "  0",
            "ENDSEC",
            "  0",
            "SECTION",
            "  2",
            "ENTITIES",
            "  0",
            "TEXT",
            "  8",
            "设计说明文字",
            " 10",
            "100.5",
            " 20",
            "200.25",
            " 40",
            "3.5",
            "  1",
            "施工图设计说明",
            "410",
            "Model",
            "  0",
            "MTEXT",
            "  8",
            "材料表",
            " 10",
            "10",
            " 20",
            "20",
            "  3",
            "材料表\\P",
            "  1",
            "地面铺装",
            "410",
            "Layout1",
            "  0",
            "ENDSEC",
            "  0",
            "EOF",
            "",
        ]
    )


def test_biz2x3_parse_dxf_layers_layouts_and_text(tmp_path):
    dxf_path = tmp_path / "sample.dxf"
    dxf_path.write_text(_sample_dxf_text(), encoding="utf-8")

    parsed = parse_dxf_file(dxf_path)

    assert parsed.detected_encoding in {"utf-8", "utf-8-sig"}
    assert parsed.acad_version == "AC1032"
    assert parsed.declared_codepage == "ANSI_936"
    assert "设计说明文字" in parsed.layers
    assert "Model" in parsed.layouts
    assert parsed.text_entity_count == 2
    assert parsed.text_records[0].text == "施工图设计说明"
    assert parsed.text_records[0].x == 100.5
    assert "design_note" in parsed.text_records[0].role_tags
    assert parsed.text_records[1].text == "材料表\n地面铺装"
    assert {"material_table", "plan"} <= set(parsed.text_records[1].role_tags)


def test_biz2x3_detects_gbk_encoded_dxf(tmp_path):
    dxf_path = tmp_path / "gbk.dxf"
    dxf_path.write_bytes(_sample_dxf_text().encode("gb18030"))

    parsed = parse_dxf_file(dxf_path)

    assert parsed.detected_encoding in {"gb18030", "cp936"}
    assert parsed.text_records[0].text == "施工图设计说明"


def test_biz2x3_clean_mtext_formatting_and_classify_roles():
    text = clean_dxf_text(r"{\A1;材料表\P%%c50\H2.0x;\pi-60.001,l60.001,t899.99;地面铺装}")

    assert text == "材料表\nΦ50地面铺装"
    assert {"material_table", "plan"} <= set(classify_text_roles(text))


def test_biz2x3_collects_dxf_files_without_duplicates(tmp_path):
    dxf_path = tmp_path / "a.dxf"
    dxf_path.write_text(_sample_dxf_text(), encoding="utf-8")

    files = collect_dxf_files(tmp_path, [dxf_path])

    assert files == [dxf_path]


def test_biz2x3_writes_report_outputs(tmp_path):
    dxf_path = tmp_path / "sample.dxf"
    dxf_path.write_text(_sample_dxf_text(), encoding="utf-8")
    parsed = parse_dxf_file(dxf_path)

    report = build_dxf_extraction_report([parsed])
    rows = build_dxf_text_csv_rows([parsed])
    outputs = write_dxf_extraction_outputs([parsed], tmp_path / "outputs", stem="extract")

    assert report["summary"]["total_text_entity_count"] == 2
    assert rows[0]["文字"] == "施工图设计说明"
    assert set(outputs) == {"json", "markdown", "csv"}
    assert json.loads((tmp_path / "outputs" / "extract.json").read_text(encoding="utf-8"))["ok"] is True
