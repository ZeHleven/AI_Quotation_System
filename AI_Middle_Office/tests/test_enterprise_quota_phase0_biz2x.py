from app.services.enterprise_quota_phase0 import SheetRows, analyze_enterprise_quota_rows
from app.services.enterprise_quota_units import CUBIC_METER_UNIT, SQUARE_METER_UNIT


def test_phase0_analyzes_enterprise_quota_sections_items_and_components():
    result = analyze_enterprise_quota_rows(
        [
            SheetRows(
                name="企业定额",
                rows=[
                    ["广东旗胜智能装饰有限公司-企业定额1.0"],
                    ["定额编码", "类型", "项目名称", "项目特征及工作内容", "类型", "单位", "含量", "单价", "人工费", "主材费", "辅材费", "机械费"],
                    ["QS201", "分部", "块料楼地面工程"],
                    ["QS201001", "定额", "石材地面（正铺）", "基层清理", "瓦工", "m2", 1, 71.13, 60, 0, 11.13, 0],
                    ["QS201001", "RG人工", "00RG0016", "瓦工", "瓦工", "m2", 1, 60, 60],
                    ["QS201001", "CB辅材", "09CA0240", "砂子", "", "m3", 0.04, 85, 3.4],
                ],
            ),
            SheetRows(
                name="劳务指导价",
                rows=[
                    [],
                    ["定额编码", "项目名称", "项目特征及工作内容", "", "类型", "单位", "含量", ""],
                    ["", "石材地面（正铺）", "", "", "", "m2", 1, 60],
                ],
            ),
            SheetRows(
                name="材料价格库",
                rows=[
                    [],
                    ["", "02CA0052", "玻璃胶", "", "", "支", 9.22, 7.06],
                ],
            ),
        ]
    )

    assert result["summary"]["enterprise_quota_section_count"] == 1
    assert result["summary"]["enterprise_quota_item_count"] == 1
    assert result["summary"]["enterprise_quota_component_count"] == 2
    assert result["summary"]["labor_guide_candidate_count"] == 1
    assert result["summary"]["material_resource_candidate_count"] == 1
    assert result["enterprise_quota"]["component_type_counts"] == {"RG人工": 1, "CB辅材": 1}
    assert result["enterprise_quota"]["items"][0]["unit"] == SQUARE_METER_UNIT
    assert result["enterprise_quota"]["components"][0]["unit"] == SQUARE_METER_UNIT
    assert result["enterprise_quota"]["components"][1]["unit"] == CUBIC_METER_UNIT
    assert result["labor_guide"]["candidates"][0]["unit"] == SQUARE_METER_UNIT
    assert result["material_price_library"]["manual_mapping_required"] is True


def test_phase0_reports_missing_sheet_and_amount_mismatch():
    result = analyze_enterprise_quota_rows(
        [
            SheetRows(
                name="企业定额",
                rows=[
                    ["定额编码", "类型", "项目名称", "项目特征及工作内容", "类型", "单位", "含量", "单价", "人工费", "主材费", "辅材费", "机械费"],
                    ["QS201001", "定额", "石材地面", "基层清理", "瓦工", "m2", 1, 100, 60, 0, 11.13, 0],
                    ["QS201001", "CB辅材", "09CA0240", "砂子", "", "m3", 0.04, 85, 5],
                ],
            )
        ]
    )

    codes = {issue["code"] for issue in result["issues"]}
    assert result["ok"] is False
    assert "MISSING_SHEET" in codes
    assert "QUOTA_PRICE_SPLIT_MISMATCH" in codes
    assert "COMPONENT_AMOUNT_MISMATCH" in codes
