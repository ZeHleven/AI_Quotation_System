from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.services.drawing_three_field_acceptance import (
    ThreeFieldAnswerRow,
    ThreeFieldCandidateRow,
    build_three_field_acceptance_report,
    compare_three_fields,
    load_answer_rows_from_workbook,
    write_three_field_acceptance_outputs,
)


def test_biz2x_three_field_acceptance_loads_manual_answer_rows(tmp_path: Path):
    workbook_path = tmp_path / "answer.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "装修工程量清单"
    sheet.append(["序号", "项目编码", "项目名称", "项目特征", "计量单位", "工程量"])
    sheet.append(["", "", "楼地面工程", "", "", ""])
    sheet.append(["1", "011102003", "块料楼地面", "750x1500灰色地砖", "m2", ""])
    sheet.append(["", "011204003", "块料墙面", "300x600墙面砖", "m2", ""])
    sheet.append(["", "", "合计", "", "", ""])
    workbook.save(workbook_path)

    rows, summaries = load_answer_rows_from_workbook(workbook_path)

    assert [row.item_name for row in rows] == ["块料楼地面", "块料墙面"]
    assert rows[0].section == "楼地面工程"
    assert rows[0].unit == "m2"
    assert rows[1].item_code == "011204003"
    assert summaries[0]["parsed_row_count"] == 2


def test_biz2x_three_field_acceptance_compares_name_feature_unit_only():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="装修工程量清单",
            row_no=3,
            section="楼地面工程",
            seq="1",
            item_code="011102003",
            item_name="块料楼地面",
            feature="750x1500灰色地砖",
            unit="m2",
            quantity="100",
        ),
        ThreeFieldAnswerRow(
            sheet_name="机电工程量清单",
            row_no=4,
            section="配电工程",
            seq="2",
            item_code="030404017",
            item_name="配电箱",
            feature="AL-01",
            unit="台",
        ),
        ThreeFieldAnswerRow(
            sheet_name="装修工程量清单",
            row_no=5,
            section="墙面工程",
            seq="3",
            item_code="011204003",
            item_name="块料墙面",
            feature="300x600墙面砖",
            unit="m2",
        ),
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=1,
            item_name="块料楼地面",
            feature="750x1500 灰色地砖",
            unit="m2",
            quantity="999",
        ),
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=2,
            item_name="配电箱",
            feature="AL-01",
            unit="套",
        ),
    ]

    comparisons, extra_candidates = compare_three_fields(answer_rows, candidate_rows)

    assert [row["status"] for row in comparisons] == [
        "matched_three_fields",
        "unit_conflict",
        "missing_candidate",
    ]
    assert comparisons[0]["answer_item_name"] == "块料楼地面"
    assert comparisons[0]["candidate_item_name"] == "块料楼地面"
    assert comparisons[0]["answer_unit"] == "m2"
    assert comparisons[0]["candidate_unit"] == "m2"
    assert extra_candidates == []


def test_biz2x_three_field_acceptance_accepts_short_fixture_supply_features():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="给排水工程量清单",
            row_no=50,
            section="卫生洁具",
            seq="1",
            item_code="",
            item_name="梳妆镜供货及安装",
            feature="1、梳妆镜供货及安装",
            unit="个",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=1,
            item_name="梳妆镜供货及安装",
            feature="图中右侧人物后方有镜面；梳妆镜供货及安装，含镜面、固定件及收口",
            unit="个",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_three_fields"


def test_biz2x_three_field_acceptance_treats_qingjing_mirror_wall_as_same_candidate():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="装修工程量清单",
            row_no=80,
            section="墙面工程",
            seq="1",
            item_code="",
            item_name="清境墙面MR-02",
            feature="1、木方+15厚阻燃板基层，暗藏灯槽 2、6mm清镜MR-02饰面，黑色拉丝不锈钢包边MT-02",
            unit="㎡",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=248,
            item_name="清镜墙面MR-02 MR02",
            feature="1. 部位：洗手/淋浴立面区域\n2. 材料/规格/做法：MR02; MR02 清镜\n3. 图纸证据：03立面图洗手/淋浴区域可见 MR02 清镜相关标注",
            unit="㎡",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_name_unit_feature_review"
    assert comparisons[0]["candidate_row_no"] == 248


def test_biz2x_three_field_acceptance_keeps_hot_cold_faucet_without_hot_cold_evidence_in_review():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="给排水工程量清单",
            row_no=54,
            section="卫生洁具",
            seq="1",
            item_code="",
            item_name="冷热水龙头供货及安装",
            feature="1、水龙头供货及安装",
            unit="套",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=1,
            item_name="龙头供货及安装",
            feature="图中可见水龙头；龙头供货及安装，含本体、软管、角阀及调试",
            unit="套",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_name_unit_feature_review"


def test_biz2x_three_field_acceptance_keeps_hot_cold_faucet_with_single_cold_evidence_in_review():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="给排水工程量清单",
            row_no=54,
            section="卫生洁具",
            seq="1",
            item_code="",
            item_name="冷热水龙头供货及安装",
            feature="1、水龙头供货及安装",
            unit="套",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=1,
            item_name="冷热水龙头供货及安装",
            feature="单冷；单冷水龙头；冷热水龙头供货及安装，含本体、软管、角阀及调试",
            unit="套",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_name_unit_feature_review"


def test_biz2x_three_field_acceptance_blocks_shaped_flat_ceiling_conflict():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="装修工程量清单",
            row_no=3,
            section="天棚工程",
            seq="1",
            item_code="011302002",
            item_name="轻钢龙骨防水石膏板造型吊顶",
            feature="U型50系列轻钢天棚龙骨 跌级；造型处为15厚阻燃板基层，双层9.5mm防水石膏板",
            unit="㎡",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=1,
            item_name="轻钢龙骨防水石膏板平级吊顶",
            feature="U型50系列轻钢天棚龙骨；双层9.5mm防水石膏板，自攻螺丝固定",
            unit="㎡",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_name_unit_feature_review"
    assert "平级/造型" in comparisons[0]["issue"]


def test_biz2x_three_field_acceptance_keeps_specific_feature_conflicts_in_review():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="电气工程量清单",
            row_no=3,
            section="电气部分",
            seq="1",
            item_code="030413001",
            item_name="灯具安装",
            feature="1、名称：LED 筒灯 2、规格、型号：5W/6W/8W/10W/3000K色温",
            unit="套",
        ),
        ThreeFieldAnswerRow(
            sheet_name="拆除工程量清单",
            row_no=4,
            section="拆除工程",
            seq="2",
            item_code="",
            item_name="售卖窗口拆除",
            feature="1、不锈钢售卖窗口拆除及清运等工作",
            unit="㎡",
        ),
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=1,
            item_name="灯具安装",
            feature="LED灯具，功率按图纸型号，3000K色温",
            unit="套",
        ),
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=2,
            item_name="拆除地面",
            feature="拆除地面",
            unit="㎡",
        ),
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert [row["status"] for row in comparisons] == [
        "matched_name_unit_feature_review",
        "matched_name_unit_feature_review",
    ]
    assert all("关键细分差异" in row["issue"] for row in comparisons)


def test_biz2x_three_field_acceptance_blocks_cross_object_fixture_matches():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="给排水",
            row_no=10,
            section="给排水",
            seq="1",
            item_code="",
            item_name="水表供货及安装",
            feature="水表 DN40，铜质",
            unit="个",
        ),
        ThreeFieldAnswerRow(
            sheet_name="给排水",
            row_no=11,
            section="给排水",
            seq="2",
            item_code="",
            item_name="马桶供货及安装",
            feature="马桶供货及安装",
            unit="套",
        ),
        ThreeFieldAnswerRow(
            sheet_name="给排水",
            row_no=12,
            section="给排水",
            seq="3",
            item_code="",
            item_name="台盆供货及安装",
            feature="台盆供货及安装",
            unit="套",
        ),
        ThreeFieldAnswerRow(
            sheet_name="电气",
            row_no=13,
            section="配电",
            seq="4",
            item_code="",
            item_name="配电箱AL",
            feature="成套配电箱20KW，暗装",
            unit="套",
        ),
        ThreeFieldAnswerRow(
            sheet_name="电气",
            row_no=14,
            section="设备",
            seq="5",
            item_code="",
            item_name="电热水器供货及安装",
            feature="电热水器供货及安装",
            unit="套",
        ),
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=1,
            item_name="阀门供货及安装",
            feature="阀门供货及安装",
            unit="个",
        ),
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=2,
            item_name="地漏供货及安装",
            feature="地漏供货及安装",
            unit="个",
        ),
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=3,
            item_name="龙头供货及安装",
            feature="水龙头供货及安装",
            unit="个",
        ),
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=4,
            item_name="配电箱",
            feature="成套配电箱，暗装，包括箱体及元器件",
            unit="台",
        ),
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=5,
            item_name="水表供货及安装",
            feature="水表 DN40，铜质",
            unit="个",
        ),
    ]

    comparisons, extra_candidates = compare_three_fields(answer_rows, candidate_rows)

    assert [row["status"] for row in comparisons] == [
        "matched_three_fields",
        "missing_candidate",
        "missing_candidate",
        "unit_conflict",
        "missing_candidate",
    ]
    assert comparisons[0]["candidate_item_name"] == "水表供货及安装"
    assert comparisons[3]["candidate_item_name"] == "配电箱"
    assert comparisons[4]["candidate_item_name"] == ""
    assert len(extra_candidates) == 3


def test_biz2x_three_field_acceptance_rejects_generic_specific_feature_placeholder():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="给排水",
            row_no=46,
            section="给排水",
            seq="1",
            item_code="",
            item_name="水表供货及安装",
            feature="1、类型:水表 DN40 2、材质:铜质",
            unit="个",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=1,
            item_name="水表供货及安装",
            feature="具体型号和规格；水表供货及安装，规格、连接方式及配件按图纸及材料表",
            unit="个",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_name_unit_feature_review"
    assert "关键细分差异" in comparisons[0]["issue"]


def test_biz2x_three_field_acceptance_prefers_exact_spec_candidate_before_generic_name():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="给排水",
            row_no=40,
            section="给排水",
            seq="1",
            item_code="",
            item_name="给水管",
            feature="1、材质：SUS304 薄壁不锈钢管 2、规格、型号：DN15",
            unit="m",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=1,
            item_name="给水管",
            feature="PPR",
            unit="m",
        ),
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=2,
            item_name="给水管 DN15",
            feature="DN15；给水管",
            unit="m",
        ),
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_name_unit_feature_review"
    assert comparisons[0]["candidate_row_no"] == 2


def test_biz2x_three_field_acceptance_blocks_demolition_cross_object_weak_match():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="装修",
            row_no=11,
            section="拆除",
            seq="1",
            item_code="",
            item_name="地砖拆除",
            feature="地砖及粘接层、垫层拆除及清运",
            unit="㎡",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=1,
            item_name="拆除门",
            feature="拆除门套、门扇及五金并清运",
            unit="套",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "missing_candidate"
    assert comparisons[0]["candidate_item_name"] == ""


def test_biz2x_three_field_acceptance_reuses_review_candidate_for_variants():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="electrical",
            row_no=3,
            section="electrical",
            seq="1",
            item_code="030412001",
            item_name="cable wiring",
            feature="WDZC-BYJ-4",
            unit="m",
        ),
        ThreeFieldAnswerRow(
            sheet_name="electrical",
            row_no=4,
            section="electrical",
            seq="2",
            item_code="030412001",
            item_name="cable wiring",
            feature="WDZC-BYJ-6",
            unit="m",
        ),
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=1,
            item_name="cable wiring",
            feature="WDZC-BYJ conductor, exact size by drawing",
            unit="m",
        )
    ]

    comparisons, extra_candidates = compare_three_fields(answer_rows, candidate_rows)

    assert [row["status"] for row in comparisons] == [
        "matched_name_unit_feature_review",
        "matched_name_unit_feature_review",
    ]
    assert extra_candidates == []


def test_biz2x_three_field_acceptance_prefers_passing_specific_candidate():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="electrical",
            row_no=17,
            section="electrical",
            seq="1",
            item_code="",
            item_name="电气配线",
            feature="1、配线形式:管内穿线 2、导线型号、材质、规格:WDZC-BYJ-6",
            unit="m",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=1,
            item_name="电气配线",
            feature="管内穿线，WDZC-BYJ导线，规格按图纸型号",
            unit="m",
        ),
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=2,
            item_name="电气配线",
            feature="WDZC-BYJ-6",
            unit="m",
        ),
    ]

    comparisons, extra_candidates = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_three_fields"
    assert comparisons[0]["candidate_row_no"] == 2
    assert len(extra_candidates) == 1


def test_biz2x_three_field_acceptance_ignores_candidate_feature_boilerplate_when_answer_is_contained():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="finish",
            row_no=26,
            section="floor",
            seq="1",
            item_code="",
            item_name="brick platform",
            feature="lobby brick platform raised 240mm",
            unit="m3",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=1,
            item_name="brick platform",
            feature=(
                "1. Material/spec: lobby brick platform raised 240mm\n"
                "2. Drawing evidence: lobby brick platform raised 240mm\n"
                "3. Quote scope: supply, install, accessories and protection; review required."
            ),
            unit="m3",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_three_fields"
    assert comparisons[0]["feature_score"] >= 0.95


def test_biz2x_three_field_acceptance_ignores_answer_list_markers_for_containment():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="finish",
            row_no=66,
            section="wall",
            seq="1",
            item_code="",
            item_name="artificial stone sill PM-01",
            feature="1. adhesive bonding 2. white artificial stone",
            unit="m2",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=1,
            item_name="artificial stone sill PM-01",
            feature=(
                "1. Material/spec: adhesive bonding; white artificial stone\n"
                "2. Drawing evidence: PM-01 white artificial stone"
            ),
            unit="m2",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_three_fields"
    assert comparisons[0]["feature_score"] >= 0.95


def test_biz2x_three_field_acceptance_does_not_use_containment_for_different_items():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="finish",
            row_no=26,
            section="floor",
            seq="1",
            item_code="",
            item_name="brick platform",
            feature="raised 240mm",
            unit="m3",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=1,
            item_name="glass partition",
            feature="Drawing evidence: raised 240mm",
            unit="m3",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "missing_candidate"


def test_biz2x_three_field_acceptance_ignores_parenthetical_location_for_item_name_core():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="finish",
            row_no=68,
            section="wall",
            seq="1",
            item_code="",
            item_name="partition base (booth area)",
            feature="steel frame and stone base 1470*240*200mm",
            unit="m",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=1,
            item_name="partition base ST-1",
            feature="steel frame and stone base 1470*240*200mm",
            unit="m",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_three_fields"
    assert comparisons[0]["name_score"] >= 0.82


def test_biz2x_three_field_acceptance_writes_review_pack(tmp_path: Path):
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="装修工程量清单",
            row_no=3,
            section="楼地面工程",
            seq="1",
            item_code="011102003",
            item_name="块料楼地面",
            feature="750x1500灰色地砖",
            unit="m2",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="03.pdf",
            row_no=1,
            item_name="块料楼地面",
            feature="750x1500灰色地砖",
            unit="m2",
        )
    ]

    report = build_three_field_acceptance_report(
        answer_rows=answer_rows,
        candidate_rows=candidate_rows,
        source_name="sample",
    )
    outputs = write_three_field_acceptance_outputs(report, tmp_path, stem="three_field")

    assert report["summary"]["matched_three_fields_count"] == 1
    assert Path(outputs["json"]).exists()
    assert Path(outputs["markdown"]).exists()
    assert Path(outputs["xlsx"]).exists()


def test_biz2x_three_field_acceptance_ignores_empty_feature_labels():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="电气工程量清单",
            row_no=35,
            section="电气部分",
            seq="1",
            item_code="",
            item_name="电热水器供货及安装",
            feature="1、名称、型号:电热水器 2、型号:",
            unit="套",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=233,
            item_name="电热水器供货及安装",
            feature="名称、型号：电热水器；暗装三孔保护型插座(16A)，H+2.20(挂机空调/热水器)",
            unit="套",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_three_fields"


def test_biz2x_three_field_acceptance_keeps_filled_model_label_required():
    answer_rows = [
        ThreeFieldAnswerRow(
            sheet_name="电气工程量清单",
            row_no=17,
            section="电气部分",
            seq="1",
            item_code="",
            item_name="电气配线",
            feature="1、配线形式:管内穿线 2、导线型号、材质、规格:WDZC-BYJ-6",
            unit="m",
        )
    ]
    candidate_rows = [
        ThreeFieldCandidateRow(
            source="04.pdf",
            row_no=30,
            item_name="电气配线",
            feature="管内穿线，WDZC-BYJ导线，规格按图纸型号",
            unit="m",
        )
    ]

    comparisons, _ = compare_three_fields(answer_rows, candidate_rows)

    assert comparisons[0]["status"] == "matched_name_unit_feature_review"
