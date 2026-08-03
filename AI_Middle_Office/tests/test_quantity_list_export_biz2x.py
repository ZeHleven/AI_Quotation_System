from __future__ import annotations

import csv

from openpyxl import load_workbook

from app.services.quantity_list_export import QUANTITY_LIST_HEADERS, write_quantity_list_outputs


def test_quantity_list_export_writes_four_field_csv_and_xlsx(tmp_path):
    rows = [
        {
            "项目名称": "地砖铺贴（块料楼地面）",
            "项目特征": "面层材料品种、规格、颜色：600x1200灰色地砖",
            "单位": "㎡",
            "工程量": "12.5",
            "额外字段": "不应导出",
        }
    ]

    outputs = write_quantity_list_outputs(rows, tmp_path, stem="quantity-list")

    with open(outputs["csv"], "r", encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))

    assert list(csv_rows[0].keys()) == QUANTITY_LIST_HEADERS
    assert csv_rows[0] == {
        "项目名称": "地砖铺贴（块料楼地面）",
        "项目特征": "面层材料品种、规格、颜色：600x1200灰色地砖",
        "单位": "㎡",
        "工程量": "12.5",
    }

    workbook = load_workbook(outputs["xlsx"])
    sheet = workbook.active

    assert sheet.title == "识图四字段清单"
    assert [cell.value for cell in sheet[1]] == QUANTITY_LIST_HEADERS
    assert [cell.value for cell in sheet[2]] == [
        "地砖铺贴（块料楼地面）",
        "面层材料品种、规格、颜色：600x1200灰色地砖",
        "㎡",
        "12.5",
    ]
