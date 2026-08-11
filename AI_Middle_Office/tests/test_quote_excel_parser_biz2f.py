from io import BytesIO

import pytest
import xlwt
from openpyxl import Workbook

from app.services.quote_excel_parser import (
    QuoteExcelParseError,
    is_quote_excel_file,
    parse_quote_excel_bytes,
)


def _workbook_bytes(rows, *, sheet_title="需求单") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _legacy_workbook_bytes(rows, *, sheet_title="需求单") -> bytes:
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet(sheet_title)
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if value is not None:
                sheet.write(row_index, column_index, value)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_parse_quote_excel_bytes_reads_standard_headers():
    content = _workbook_bytes(
        [
            ["客户需求单"],
            ["施工项目", "数量", "单位", "备注"],
            ["拆除复合木地板", 20, "㎡", "不含清运"],
            ["拆除木脚线", 30, "m", None],
            ["合计", None, None, None],
        ]
    )

    result = parse_quote_excel_bytes(content, filename="quote.xlsx")

    assert result.sheet_name == "需求单"
    assert result.item_count == 2
    assert result.text == "拆除复合木地板，数量：20，单位：㎡，备注：不含清运；拆除木脚线，数量：30，单位：m"
    assert result.items == (
        {"project_name": "拆除复合木地板", "quantity": "20", "unit": "㎡", "notes": "不含清运"},
        {"project_name": "拆除木脚线", "quantity": "30", "unit": "m"},
    )


def test_parse_quote_excel_bytes_reads_common_alias_headers():
    content = _workbook_bytes(
        [
            ["序号", "工作内容", "工程量", "计量单位", "特征描述"],
            [1, "窗帘盒/灯槽拆除", 18, "m", "拆除至指定堆放点"],
        ]
    )

    result = parse_quote_excel_bytes(content, filename="quote.xlsx")

    assert result.item_count == 1
    assert result.text == "窗帘盒/灯槽拆除，规格/特征：拆除至指定堆放点，数量：18，单位：m"
    assert result.items == (
        {
            "project_name": "窗帘盒/灯槽拆除",
            "quantity": "18",
            "unit": "m",
            "spec": "拆除至指定堆放点",
        },
    )


def test_parse_quote_excel_bytes_reads_legacy_xls():
    content = _legacy_workbook_bytes(
        [
            ["施工项目", "数量", "单位", "备注"],
            ["旧版清单墙面拆除", 15, "㎡", "人工复核"],
        ]
    )

    assert is_quote_excel_file("legacy.xls", "application/vnd.ms-excel") is True
    result = parse_quote_excel_bytes(content, filename="legacy.xls")

    assert result.sheet_name == "需求单"
    assert result.item_count == 1
    assert result.items[0]["project_name"] == "旧版清单墙面拆除"
    assert result.items[0]["quantity"] == "15"


def test_parse_quote_excel_bytes_rejects_sheet_without_quote_items():
    content = _workbook_bytes([["客户", "备注"], ["张三", "仅说明，无施工项目"]])

    with pytest.raises(QuoteExcelParseError, match="未识别到可报价项目"):
        parse_quote_excel_bytes(content, filename="empty.xlsx")
