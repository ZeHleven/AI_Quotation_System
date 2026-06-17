from __future__ import annotations

import json

from app.services.drawing_project_region_binder import (
    build_project_region_binding_report,
    write_project_region_binding_outputs,
)


def test_biz2x_project_region_binder_links_waterproof_to_wet_room(tmp_path):
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-001",
                "图纸项目名称": "洗手间墙面防水高度1800",
                "项目名称": "墙面防水",
                "项目特征": "防水高度：1800mm",
                "单位": "㎡",
                "来源文件": "餐厅平面.dxf",
                "识别证据": "洗手间墙面防水高度1800",
            }
        ]
    }
    region_label_report = {
        "region_rows": [
            {
                "区域编号": "BIZ2xR-00001",
                "来源文件": "餐厅平面.dxf",
                "CAD面积": 2.52,
                "CAD周长": 6.4,
                "图层": "A-湿区",
                "实体类型": "LWPOLYLINE",
                "区域内文字": "洗手间 防水",
                "附近文字": "",
                "房间/空间标签": "洗手间",
                "项目标签": "防水",
                "区域类型建议": "湿区/防水区域候选",
                "绑定状态": "已绑定区域文字",
            }
        ]
    }

    report = build_project_region_binding_report(project_report=project_report, region_label_report=region_label_report)

    row = report["binding_rows"][0]
    assert row["区域绑定状态"] == "建议绑定区域，需复核"
    assert row["推荐区域编号"] == "BIZ2xR-00001"
    assert row["区域周长"] == 6.4
    assert "防水高度 1.8m" in row["工程量计算方式建议"]
    assert report["summary"]["binding_ready_project_count"] == 1

    outputs = write_project_region_binding_outputs(report, tmp_path, stem="project_region")
    assert set(outputs) == {"json", "markdown", "binding_csv", "candidate_csv"}
    assert json.loads((tmp_path / "project_region.json").read_text(encoding="utf-8"))["phase"] == "BIZ-2x-project-region-binding"


def test_biz2x_project_region_binder_marks_baseboard_as_perimeter_quantity():
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-002",
                "图纸项目名称": "水泥砂浆踢脚线",
                "项目名称": "水泥砂浆踢脚线",
                "项目特征": "材料种类：水泥砂浆",
                "单位": "m",
                "来源文件": "餐厅平面.dxf",
                "识别证据": "水泥砂浆踢脚线",
            }
        ]
    }
    region_label_report = {
        "region_rows": [
            {
                "区域编号": "BIZ2xR-00002",
                "来源文件": "餐厅平面.dxf",
                "CAD面积": 11.02,
                "CAD周长": 13.52,
                "图层": "A-ROOM",
                "实体类型": "LWPOLYLINE",
                "区域内文字": "餐厅 踢脚线",
                "附近文字": "",
                "房间/空间标签": "餐厅",
                "项目标签": "踢脚线",
                "区域类型建议": "房间边界候选",
                "绑定状态": "已绑定区域文字",
            }
        ]
    }

    report = build_project_region_binding_report(project_report=project_report, region_label_report=region_label_report)

    row = report["binding_rows"][0]
    assert row["区域绑定状态"] == "建议绑定区域，需复核"
    assert row["区域周长"] == 13.52
    assert "区域周长" in row["工程量计算方式建议"]
    assert "门洞扣减" in row["工程量计算方式建议"]


def test_biz2x_project_region_binder_penalizes_detail_noise():
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-003",
                "图纸项目名称": "石膏板饰面吊顶",
                "项目名称": "吊顶天棚",
                "项目特征": "面层材料：石膏板",
                "单位": "㎡",
                "来源文件": "餐厅天棚.dxf",
                "识别证据": "石膏板饰面吊顶",
            }
        ]
    }
    region_label_report = {
        "region_rows": [
            {
                "区域编号": "BIZ2xR-NOISE",
                "来源文件": "餐厅天棚.dxf",
                "CAD面积": 1.2,
                "CAD周长": 4.2,
                "图层": "A-DETAIL",
                "实体类型": "LWPOLYLINE",
                "区域内文字": "门洞砌墙示意图 石膏板 节点 大样",
                "附近文字": "",
                "房间/空间标签": "",
                "项目标签": "石膏板",
                "区域类型建议": "节点/大样候选",
                "绑定状态": "已绑定区域文字",
            },
            {
                "区域编号": "BIZ2xR-CEILING",
                "来源文件": "餐厅天棚.dxf",
                "CAD面积": 16.8,
                "CAD周长": 18.4,
                "图层": "A-CEILING",
                "实体类型": "LWPOLYLINE",
                "区域内文字": "餐厅 石膏板饰面吊顶",
                "附近文字": "",
                "房间/空间标签": "餐厅",
                "项目标签": "吊顶；石膏板",
                "区域类型建议": "吊顶/天棚区域候选",
                "绑定状态": "已绑定区域文字",
            },
        ]
    }

    report = build_project_region_binding_report(project_report=project_report, region_label_report=region_label_report)

    row = report["binding_rows"][0]
    assert row["推荐区域编号"] == "BIZ2xR-CEILING"
    assert report["candidate_rows"][0]["区域编号"] == "BIZ2xR-CEILING"


def test_biz2x_project_region_binder_filters_door_schedule_regions():
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-004",
                "图纸项目名称": "石膏板饰面吊顶",
                "项目名称": "平面吊顶 | 天棚",
                "项目特征": "面板材料品种：石膏板",
                "单位": "㎡",
                "来源文件": "01_01平面图.dxf",
                "识别证据": "石膏板饰面吊顶",
            }
        ]
    }
    region_label_report = {
        "region_rows": [
            {
                "区域编号": "BIZ2xR-PLAN",
                "来源文件": "01_01平面图.dxf",
                "CAD面积": 11.0168,
                "CAD周长": 13.52,
                "图层": "D◆02【造型细线】",
                "实体类型": "LWPOLYLINE",
                "区域内文字": "",
                "附近文字": "",
                "房间/空间标签": "",
                "项目标签": "",
                "区域类型建议": "吊顶/天棚区域候选",
                "绑定状态": "已绑定区域文字",
            },
            {
                "区域编号": "BIZ2xR-DOOR",
                "来源文件": "05_05门表.dxf",
                "CAD面积": 8.532,
                "CAD周长": 11.94,
                "图层": "D◆02【造型细线】",
                "实体类型": "LWPOLYLINE",
                "区域内文字": "天花；地面；门表",
                "附近文字": "",
                "房间/空间标签": "",
                "项目标签": "天花",
                "区域类型建议": "吊顶/天棚区域候选",
                "绑定状态": "已绑定区域文字",
            },
        ]
    }

    report = build_project_region_binding_report(project_report=project_report, region_label_report=region_label_report)

    row = report["binding_rows"][0]
    assert row["区域绑定状态"] == "建议绑定区域，需复核"
    assert row["推荐区域编号"] == "BIZ2xR-PLAN"
    assert report["summary"]["binding_ready_project_count"] == 1
    assert all(item["区域编号"] != "BIZ2xR-DOOR" for item in report["candidate_rows"])


def test_biz2x_project_region_binder_allows_clear_medium_leader():
    project_report = {
        "project_rows": [
            {
                "识别项目编号": "P-005",
                "图纸项目名称": "石膏板白色乳胶漆",
                "项目名称": "天棚喷刷涂料",
                "项目特征": "基层类型：石膏板；涂料品种：乳胶漆",
                "单位": "㎡",
                "来源文件": "04_04大样图.dxf",
                "识别证据": "石膏板白色乳胶漆",
            }
        ]
    }
    region_label_report = {
        "region_rows": [
            {
                "区域编号": "BIZ2xR-CEILING",
                "来源文件": "01_01平面图.dxf",
                "CAD面积": 11.0168,
                "CAD周长": 13.52,
                "图层": "D◆02【造型细线】",
                "实体类型": "LWPOLYLINE",
                "区域内文字": "",
                "附近文字": "",
                "房间/空间标签": "",
                "项目标签": "",
                "区域类型建议": "吊顶/天棚区域候选",
                "绑定状态": "已绑定区域文字",
            },
            {
                "区域编号": "BIZ2xR-NOTE",
                "来源文件": "04_04大样图.dxf",
                "CAD面积": 1.2,
                "CAD周长": 4.4,
                "图层": "A-DETAIL",
                "实体类型": "LWPOLYLINE",
                "区域内文字": "乳胶漆节点大样",
                "附近文字": "",
                "房间/空间标签": "",
                "项目标签": "乳胶漆",
                "区域类型建议": "节点/大样候选",
                "绑定状态": "已绑定区域文字",
            },
        ]
    }

    report = build_project_region_binding_report(project_report=project_report, region_label_report=region_label_report)

    row = report["binding_rows"][0]
    assert row["区域绑定状态"] == "建议绑定区域，需复核"
    assert row["推荐区域编号"] == "BIZ2xR-CEILING"
