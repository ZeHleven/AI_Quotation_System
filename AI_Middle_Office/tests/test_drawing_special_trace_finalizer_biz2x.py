from __future__ import annotations

import json

from openpyxl import load_workbook

from app.services import drawing_quantity_confirmation as confirmation
from app.services.drawing_special_trace_finalizer import (
    build_special_trace_confirmation_pack,
    build_special_trace_finalization,
    write_special_trace_finalization_outputs,
)
from app.services.drawing_standard_rule_executor import READY_STATUS


def test_biz2x_a6_special_trace_finalizer_generates_final_four_field_excel(tmp_path):
    report = _special_report()
    pack = build_special_trace_confirmation_pack(report)
    finalization = build_special_trace_finalization(
        report,
        [
            {
                "专项算量编号": "BIZ2xSQ-00001",
                "是否采用": "是",
                "核验结论": "通过",
                "项目名称": "平面吊顶天棚",
                "项目特征": "吊顶形式：石膏板；基层材料种类：轻钢龙骨",
                "单位": "㎡",
                "工程量": "16.8",
                "工程量来源说明": "专项trace复核通过，按绑定区域CAD面积",
                "扣减/合并规则复核": "本项按水平投影面积计算，未发现需扣减洞口",
            }
        ],
    )
    outputs = write_special_trace_finalization_outputs(finalization, tmp_path, stem="special_final")

    assert pack["summary"]["ready_special_trace_count"] == 1
    assert finalization["ok"] is True
    assert finalization["summary"]["final_ready_count"] == 1
    assert set(outputs) >= {"json", "markdown", "converted_confirmation_csv", "validation_final_xlsx"}
    assert json.loads((tmp_path / "special_final.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-special-trace-finalization"

    workbook = load_workbook(outputs["validation_final_xlsx"])
    sheet = workbook[confirmation.FINAL_SHEET_NAME]
    assert [cell.value for cell in sheet[1]] == ["项目名称", "项目特征", "单位", "工程量"]
    assert sheet["A2"].value == "平面吊顶天棚"
    assert sheet["D2"].value == "16.8"


def test_biz2x_a6_special_trace_finalizer_blocks_unready_standard_rule_trace():
    report = _special_report(
        {
            "专项算量编号": "BIZ2xSQ-00002",
            "trace状态": "blocked_standard_rule_requires_expanded_area",
            "是否可复核": "否",
            "建议工程量": "",
            "标准规则执行状态": "blocked_standard_rule_requires_expanded_area",
            "阻断原因": "标准规则要求展开面积",
        }
    )
    finalization = build_special_trace_finalization(
        report,
        [
            {
                "专项算量编号": "BIZ2xSQ-00002",
                "是否采用": "是",
                "核验结论": "通过",
                "项目特征": "涂料品种：无机涂料",
                "单位": "㎡",
                "工程量": "12",
                "扣减/合并规则复核": "已复核",
            }
        ],
    )

    assert finalization["ok"] is False
    assert finalization["summary"]["final_ready_count"] == 0
    assert "标准规则执行状态未通过" in finalization["issues"][0]["问题说明"]


def _special_report(override: dict | None = None) -> dict:
    row = {
        "专项算量编号": "BIZ2xSQ-00001",
        "识别项目编号": "P-001",
        "标准项目编码": "011302001",
        "项目名称": "平面吊顶天棚",
        "图纸项目名称": "石膏板饰面吊顶",
        "专项类型": "吊顶/天棚水平投影面积",
        "建议工程量": 16.8,
        "建议单位": "㎡",
        "trace状态": "special_quantity_trace_ready_for_manual_review",
        "是否可复核": "是",
        "标准工程量计算规则": "按设计图示尺寸以水平投影面积计算",
        "标准规则模板": "area_horizontal_projection",
        "标准规则执行状态": READY_STATUS,
        "计算公式": "绑定区域 CAD 面积",
        "计算输入": "区域面积=16.8㎡",
        "区域编号": "BIZ2xR-00001",
        "房间编号": "",
        "房间/空间名称": "",
        "阻断原因": "",
        "未解决事项": "",
        "calculation_trace": {
            "project_feature_text": "吊顶形式：石膏板；基层材料种类：轻钢龙骨",
            "region_binding": {"来源文件": "sample.dxf"},
        },
    }
    row.update(override or {})
    return {
        "ok": True,
        "phase": "BIZ-2x-special-quantity-calculation-trace",
        "special_quantity_trace_rows": [row],
        "summary": {"special_quantity_trace_count": 1},
    }
