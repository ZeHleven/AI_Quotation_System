import json
from io import BytesIO
from types import SimpleNamespace

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, PatternFill, Side

from app.services.budget_pricing_original_export import (
    BudgetPricingOriginalExportSource,
    render_budget_pricing_original_export,
)


def _source(*, line, header_row_index=1):
    return BudgetPricingOriginalExportSource(
        draft=SimpleNamespace(pricing_mode="enterprise_ai", revision=3),
        batch=SimpleNamespace(source_filename="甲方清单.xlsx", batch_uuid="batch-1"),
        source_file=SimpleNamespace(),
        sheet_mappings=(SimpleNamespace(sheet_name="清单", header_row_index=header_row_index),),
        lines=(line,),
    )


def _line():
    return SimpleNamespace(
        source_sheet="清单",
        source_raw_row_index=2,
        item_name="轻钢龙骨吊顶",
        spec="600*600 矿棉板",
        calculation_quantity="12.5",
        effective_unit_price="88.8",
        line_total="1110",
        pricing_breakdown_json=json.dumps(
            {
                "material_supply_mode": "乙供",
                "labor_unit_cost": "20",
                "main_material_unit_cost": "40",
                "auxiliary_material_unit_cost": "8",
                "tax_amount": "6.12",
                "main_material_without_loss": "38",
                "loss_rate": "0.05",
                "machinery_unit_cost": "2",
                "comprehensive_unit_cost": "1",
                "management_unit_cost": "5",
                "profit_unit_cost": "4",
                "measure_unit_cost": "2",
                "owner_material_unit_price": "0",
                "owner_material_loss_amount": "0",
            }
        ),
        source_row_snapshot_json=json.dumps({"region": "一层", "raw_fields": {}}),
    )


def _workbook_bytes(headers):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "清单"
    for index, value in enumerate(headers, start=1):
        sheet.cell(row=1, column=index, value=value)
    sheet.cell(row=2, column=1, value="客户原项目")
    sheet.cell(row=2, column=2, value=12.5)
    sheet.cell(row=2, column=2).fill = PatternFill("solid", fgColor="FFF2CC")
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _headers(sheet):
    return [sheet.cell(row=1, column=index).value for index in range(1, sheet.max_column + 1)]


def test_export_appends_missing_system_fields_at_the_far_right_without_moving_original_columns():
    result = render_budget_pricing_original_export(
        _workbook_bytes(["项目名称", "工程量"]),
        _source(line=_line()),
        project_name="测试项目",
    )

    workbook = load_workbook(BytesIO(result.content), data_only=False)
    sheet = workbook["清单"]
    headers = _headers(sheet)

    assert headers[:2] == ["项目名称", "工程量"]
    assert headers[2:7] == ["特征描述", "区域", "主材采购方式", "不含税综合单价", "不含税综合合价"]
    assert sheet.cell(row=2, column=1).value == "客户原项目"
    assert sheet.cell(row=2, column=2).value == 12.5
    assert sheet.cell(row=2, column=6).value == 88.8
    assert sheet.cell(row=2, column=7).value == 1110
    loss_rate_column = headers.index("损耗率") + 1
    assert sheet.cell(row=2, column=loss_rate_column).number_format == "0.00%"
    assert sheet.cell(row=2, column=3).fill.fgColor.rgb == sheet.cell(row=2, column=2).fill.fgColor.rgb
    assert "系统估算说明" in workbook.sheetnames


def test_export_fills_existing_unit_price_alias_instead_of_adding_a_duplicate_column():
    result = render_budget_pricing_original_export(
        _workbook_bytes(["项目名称", "工程量", "综合单价"]),
        _source(line=_line()),
        project_name="测试项目",
    )

    workbook = load_workbook(BytesIO(result.content), data_only=False)
    sheet = workbook["清单"]
    headers = _headers(sheet)

    assert headers.count("不含税综合单价") == 0
    assert sheet.cell(row=2, column=3).value == 88.8


def test_export_uses_real_table_edge_and_preserves_two_level_header_styles():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "清单"
    thin_black = Side(style="thin", color="000000")
    formal_border = Border(left=thin_black, right=thin_black, top=thin_black, bottom=thin_black)
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for row_index in range(1, 4):
        for column_index in range(1, 9):
            cell = sheet.cell(row=row_index, column=column_index)
            cell.border = formal_border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            if row_index <= 2:
                cell.fill = header_fill

    sheet.merge_cells("A1:A2")
    sheet.merge_cells("B1:B2")
    sheet.merge_cells("C1:C2")
    sheet.merge_cells("D1:E1")
    sheet.merge_cells("F1:F2")
    sheet["A1"] = "项目名称"
    sheet["B1"] = "项目特征描述"
    sheet["C1"] = "工程量"
    sheet["D1"] = "不含税金额（元）"
    sheet["D2"] = "综合单价"
    sheet["E2"] = "综合合价"
    sheet["F1"] = "备注"
    sheet["A3"] = "客户原项目"
    sheet["B3"] = "矿棉板吊顶"
    sheet["C3"] = 12.5
    sheet["D3"] = 0
    sheet["D3"].number_format = "¥#,##0.000"
    sheet["E3"] = 0

    output = BytesIO()
    workbook.save(output)
    line = _line()
    line.source_raw_row_index = 3
    result = render_budget_pricing_original_export(
        output.getvalue(),
        _source(line=line, header_row_index=1),
        project_name="测试项目",
    )

    rendered = load_workbook(BytesIO(result.content), data_only=False)
    sheet = rendered["清单"]
    appended_start = 7  # G: the first truly empty column after original F, not styled G:H's far edge.

    assert sheet.cell(row=1, column=appended_start).value == "系统报价补充"
    assert sheet.cell(row=2, column=appended_start).value == "区域"
    assert any(
        merged_range.min_row == 1
        and merged_range.min_col == appended_start
        and merged_range.max_col > appended_start
        for merged_range in sheet.merged_cells.ranges
    )
    assert sheet.cell(row=1, column=appended_start).fill.fgColor.rgb == sheet["D1"].fill.fgColor.rgb
    assert sheet.cell(row=2, column=appended_start).border.left.style == sheet["D2"].border.left.style
    assert sheet.cell(row=3, column=appended_start).border.left.style == sheet["B3"].border.left.style
    assert sheet.cell(row=3, column=appended_start).value == "一层"
    assert sheet["D3"].value == 88.8
    assert sheet["E3"].value == 1110
    assert sheet["D3"].number_format == "¥#,##0.000"
    assert sheet.cell(row=2, column=appended_start + 3).value != "不含税综合单价"
