from __future__ import annotations

import json
from pathlib import Path

from app.services.dxf_low_risk_quantity_mvp import (
    build_low_risk_quantity_mvp_report,
    build_low_risk_quantity_mvp_csv_rows,
    write_low_risk_quantity_mvp_outputs,
)


def _suggestion(
    *,
    key: str,
    quantity_kind: str,
    layer: str,
    business_hint: str,
    block_name: str = "",
    status: str = "suggestion_ready_for_manual_review",
    quantity: float = 1,
    unit: str = "m2",
) -> dict[str, object]:
    return {
        "suggestion_key": key,
        "source_file": "sample.dxf",
        "layer": layer,
        "block_name": block_name,
        "business_hint": business_hint,
        "matched_reason": business_hint,
        "quantity_kind": quantity_kind,
        "suggestion_status": status,
        "suggested_quantity": quantity,
        "suggested_unit": unit,
        "formula": "test_formula",
        "used_candidate_count": 2,
        "skipped_candidate_count": 0,
        "risk_flags": ["manual_review_required_before_final_list"],
        "calculation_trace": {"matched_reason": business_hint},
    }


def test_biz2x9_mvp_keeps_floor_ceiling_lighting_and_sanitary_only():
    report = build_low_risk_quantity_mvp_report(
        {
            "suggestions": [
                _suggestion(
                    key="S-floor",
                    quantity_kind="area",
                    layer="F-地面铺装",
                    business_hint="地面/楼地面面积候选",
                    quantity=25.2,
                    unit="m2",
                ),
                _suggestion(
                    key="S-ceiling",
                    quantity_kind="area",
                    layer="D-吊顶造型",
                    business_hint="天棚/吊顶面积候选",
                    quantity=18.4,
                    unit="m2",
                ),
                _suggestion(
                    key="S-light",
                    quantity_kind="count",
                    layer="天花灯具",
                    block_name="筒灯",
                    business_hint="灯具数量候选",
                    quantity=12,
                    unit="个",
                ),
                _suggestion(
                    key="S-sanitary",
                    quantity_kind="count",
                    layer="洁具",
                    block_name="地漏",
                    business_hint="洁具/地漏数量候选",
                    quantity=3,
                    unit="个",
                ),
                _suggestion(
                    key="S-skirting",
                    quantity_kind="length",
                    layer="踢脚线",
                    business_hint="踢脚线长度候选",
                    quantity=30,
                    unit="m",
                ),
                _suggestion(
                    key="S-door",
                    quantity_kind="count",
                    layer="门窗",
                    block_name="单开门",
                    business_hint="门数量候选",
                    quantity=5,
                    unit="樘",
                ),
                _suggestion(
                    key="S-switch",
                    quantity_kind="count",
                    layer="开关插座",
                    block_name="五孔插座",
                    business_hint="开关插座数量候选",
                    quantity=20,
                    unit="个",
                ),
            ]
        }
    )

    assert report["safe_for_final_quantity_list"] is False
    assert report["requires_manual_review"] is True
    assert report["summary"]["mvp_candidate_count"] == 4
    assert report["summary"]["mvp_ready_for_manual_review_count"] == 4
    assert report["summary"]["excluded_suggestion_count"] == 3
    assert report["summary"]["floor_area_candidate_count"] == 1
    assert report["summary"]["ceiling_area_candidate_count"] == 1
    assert report["summary"]["fixture_count_candidate_count"] == 2
    assert [row["suggestion_key"] for row in report["mvp_rows"]] == [
        "S-floor",
        "S-ceiling",
        "S-light",
        "S-sanitary",
    ]
    assert {row["suggestion_key"] for row in report["excluded_rows"]} == {"S-skirting", "S-door", "S-switch"}


def test_biz2x9_mvp_preserves_blocked_status_for_manual_review_gate():
    report = build_low_risk_quantity_mvp_report(
        {
            "suggestions": [
                _suggestion(
                    key="S-floor-blocked",
                    quantity_kind="area",
                    layer="F-地面铺装",
                    business_hint="地面/楼地面面积候选",
                    status="blocked_no_usable_geometry_value",
                    quantity=0,
                    unit="m2",
                )
            ]
        }
    )

    assert report["summary"]["mvp_candidate_count"] == 1
    assert report["summary"]["mvp_ready_for_manual_review_count"] == 0
    assert report["summary"]["mvp_blocked_count"] == 1
    assert report["mvp_rows"][0]["ready_for_manual_review"] is False


def test_biz2x9_mvp_writes_outputs(tmp_path):
    report = build_low_risk_quantity_mvp_report(
        {
            "suggestions": [
                _suggestion(
                    key="S-ceiling",
                    quantity_kind="area",
                    layer="天棚吊顶",
                    business_hint="天棚/吊顶面积候选",
                    quantity=18.4,
                    unit="m2",
                )
            ]
        }
    )
    rows = build_low_risk_quantity_mvp_csv_rows(report)
    outputs = write_low_risk_quantity_mvp_outputs(report, tmp_path, stem="mvp")

    assert rows[0]["建议编号"] == "S-ceiling"
    assert set(outputs) == {"json", "markdown", "csv"}
    assert json.loads(Path(outputs["json"]).read_text(encoding="utf-8"))["phase"] == "BIZ-2x-9-mvp-floor-ceiling-fixture-quantity"
    assert Path(outputs["markdown"]).read_text(encoding="utf-8").startswith("# BIZ-2x-9")
    assert Path(outputs["csv"]).read_text(encoding="utf-8-sig").startswith("MVP类别")
