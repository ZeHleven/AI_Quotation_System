from __future__ import annotations

import json
from pathlib import Path

from app.services import drawing_quantity_confirmation as confirmation
from app.services.drawing_low_risk_mvp_finalizer import (
    MVP_BINDING_ID_COLUMN,
    build_low_risk_mvp_binding_pack,
    build_low_risk_mvp_finalization,
    write_low_risk_mvp_binding_outputs,
    write_low_risk_mvp_finalization_outputs,
)


def _listing_report() -> dict[str, object]:
    return {
        "item_rows": [
            {
                "序号": 1,
                "标准项目编码": "011102003",
                "项目名称": "地砖铺贴（块料楼地面）",
                "单位": "㎡",
                "匹配置信度": 0.88,
                "图纸识别名称": "CT-01 地砖铺贴",
                "图纸识别规格或做法": "餐厅 600x1200 灰色地砖",
                "项目特征字段": "部位；面层材料品种、规格、颜色",
                "工程量计算规则": "按设计图示尺寸以面积计算",
                "来源文件": "sample.dxf",
                "来源证据": "CT-01 600x1200 灰色地砖",
                "CAD候选列表": [
                    {
                        "建议编号": "S-floor",
                        "标准项目编码": "011102003",
                        "标准项目名称": "块料楼地面",
                        "建议工程量": 25.2,
                        "建议单位": "㎡",
                        "trace状态": "standard_rule_trace_ready_for_manual_review",
                        "是否可复核": "是",
                        "绑定置信度": "高",
                        "推荐原因": "同标准项目且地面图层语义匹配",
                        "推荐说明": "候选与列项来源线索较接近",
                        "CAD公式": "sum(CAD_area_mm2) * area_to_square_meter_factor",
                        "CAD来源图元行号": "10、20",
                        "CAD来源": "sample.dxf / 面积候选 / F-地面铺装",
                        "算量证据": "CAD 地面铺装面积 25.2㎡",
                    }
                ],
            },
            {
                "序号": 2,
                "标准项目编码": "011302003",
                "项目名称": "艺术造型吊顶（吊顶天棚）",
                "单位": "㎡",
                "项目特征": "部位：餐厅；面层材料：石膏板",
                "CAD候选列表": [],
            },
        ],
        "quantity_list_rows": [
            {
                "项目名称": "地砖铺贴（块料楼地面）",
                "项目特征": "餐厅 600x1200 灰色地砖",
                "单位": "㎡",
                "工程量": "待算量",
            },
            {
                "项目名称": "艺术造型吊顶（吊顶天棚）",
                "项目特征": "部位：餐厅；面层材料：石膏板",
                "单位": "㎡",
                "工程量": "待算量",
            },
        ],
        "low_risk_quantity_mvp_rows": [
            {
                "mvp_category": "floor_area",
                "mvp_category_label": "地面面积",
                "suggestion_key": "S-floor",
                "source_file": "sample.dxf",
                "layer": "F-地面铺装",
                "block_name": "",
                "ready_for_manual_review": True,
                "suggested_quantity": 25.2,
                "suggested_unit": "㎡",
                "formula": "sum(CAD_area_mm2) * area_to_square_meter_factor",
                "risk_flags": ["manual_review_required_before_final_list"],
            }
        ],
    }


def test_biz2x_low_risk_mvp_binds_candidate_to_project_row():
    pack = build_low_risk_mvp_binding_pack(_listing_report())

    assert pack["summary"]["mvp_candidate_count"] == 1
    assert pack["summary"]["ready_binding_count"] == 1
    assert pack["summary"]["confirmation_row_count"] == 1
    assert pack["binding_rows"][0]["绑定状态"] == "ready_for_manual_confirmation"
    assert pack["binding_rows"][0]["项目行序号"] == "1"
    assert pack["confirmation_rows"][0]["确认行号"] == "BIZ2xMVPB-0001-01"
    assert pack["confirmation_rows"][0][confirmation.ADOPT_COLUMN] == "待确认"
    assert pack["confirmation_rows"][0][confirmation.MANUAL_QUANTITY_COLUMN] == "25.2"


def test_biz2x_low_risk_mvp_finalization_backfills_quantity_list():
    review = {
        MVP_BINDING_ID_COLUMN: "BIZ2xMVPB-0001-01",
        confirmation.ADOPT_COLUMN: "是",
        confirmation.REVIEW_COLUMN: "通过",
        confirmation.MANUAL_QUANTITY_COLUMN: "25.2",
        confirmation.MANUAL_UNIT_COLUMN: "㎡",
        confirmation.MANUAL_FEATURE_COLUMN: "餐厅 600x1200 灰色地砖",
        confirmation.QUANTITY_SOURCE_COLUMN: "按低风险 MVP 地面面积候选复核采用",
    }

    finalization = build_low_risk_mvp_finalization(_listing_report(), [review])

    assert finalization["ok"] is True
    assert finalization["safe_for_final_quantity_list"] is True
    assert finalization["summary"]["merged_updated_row_count"] == 1
    assert finalization["summary"]["merged_appended_row_count"] == 0
    assert finalization["quantity_list_rows"] == [
        {
            "项目名称": "地砖铺贴（块料楼地面）",
            "项目特征": "餐厅 600x1200 灰色地砖",
            "单位": "㎡",
            "工程量": "25.2",
        },
        {
            "项目名称": "艺术造型吊顶（吊顶天棚）",
            "项目特征": "部位：餐厅；面层材料：石膏板",
            "单位": "㎡",
            "工程量": "待算量",
        },
    ]


def test_biz2x_low_risk_mvp_outputs(tmp_path):
    pack = build_low_risk_mvp_binding_pack(_listing_report())
    binding_outputs = write_low_risk_mvp_binding_outputs(pack, tmp_path, stem="mvp-binding")
    finalization = build_low_risk_mvp_finalization(
        _listing_report(),
        [
            {
                MVP_BINDING_ID_COLUMN: "BIZ2xMVPB-0001-01",
                confirmation.ADOPT_COLUMN: "是",
                confirmation.REVIEW_COLUMN: "通过",
                confirmation.MANUAL_QUANTITY_COLUMN: "25.2",
                confirmation.MANUAL_UNIT_COLUMN: "㎡",
                confirmation.MANUAL_FEATURE_COLUMN: "餐厅 600x1200 灰色地砖",
                confirmation.QUANTITY_SOURCE_COLUMN: "按低风险 MVP 地面面积候选复核采用",
            }
        ],
    )
    final_outputs = write_low_risk_mvp_finalization_outputs(finalization, tmp_path, stem="mvp-final")

    assert Path(binding_outputs["confirmation_confirmation_xlsx"]).exists()
    assert Path(final_outputs["validation_final_xlsx"]).exists()
    assert Path(final_outputs["validation_final_csv"]).read_text(encoding="utf-8-sig").startswith("项目名称")
    assert json.loads(Path(final_outputs["json"]).read_text(encoding="utf-8"))["phase"] == "BIZ-2x-low-risk-mvp-finalization"
