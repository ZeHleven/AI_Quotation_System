from __future__ import annotations

from decimal import Decimal

from app.services.budget_pricing import _QuotaEntry, _normalize_text, normalize_pricing_unit
from app.services.budget_pricing_match_v2_shadow import (
    SHADOW_DECISION_AUTO,
    SHADOW_DECISION_NONE,
    SHADOW_DECISION_REVIEW,
    shadow_match_source,
)


def _entry(
    item_id: int,
    *,
    name: str,
    unit: str,
    code: str,
    work_content: str = "",
) -> _QuotaEntry:
    snapshot = {
        "id": item_id,
        "version_id": 8,
        "quota_code": code,
        "item_name": name,
        "work_content": work_content,
        "unit": unit,
        "unit_price": "10.000000",
    }
    return _QuotaEntry(
        item_id=item_id,
        version_id=8,
        quota_code=code,
        item_name=name,
        work_content=work_content,
        worker_or_subtype=None,
        unit=unit,
        normalized_unit=normalize_pricing_unit(unit) or "",
        unit_price=Decimal("10"),
        labor_fee=Decimal("4"),
        main_material_fee=Decimal("3"),
        auxiliary_material_fee=Decimal("2"),
        machinery_fee=Decimal("1"),
        name_norm=_normalize_text(name),
        spec_norm=_normalize_text(work_content),
        code_norm=_normalize_text(code),
        snapshot=snapshot,
        full_snapshot={**snapshot, "components": []},
    )


def _source(*, name: str, unit: str, spec: str = "", sheet: str = "装修工程量清单") -> dict:
    return {
        "item_name": name,
        "spec": spec,
        "unit": unit,
        "source_sheet": sheet,
    }


def test_shadow_matcher_keeps_exact_name_and_unit_as_auto_recommendation():
    match = shadow_match_source(
        _source(name="灯槽", unit="m"),
        [_entry(1, name="灯槽", unit="m", code="ZS00458")],
    )

    assert match["decision"] == SHADOW_DECISION_AUTO
    assert match["selected"]["entry"].item_id == 1


def test_shadow_matcher_understands_door_count_unit_family():
    match = shadow_match_source(
        _source(name="成品实木单开门", unit="套", spec="单开实木门"),
        [
            _entry(1, name="实木复合单开门", unit="樘", code="ZS00292"),
            _entry(2, name="实木复合双开门", unit="樘", code="ZS00293"),
        ],
    )

    assert match["recommended"]["entry"].item_id == 1
    assert match["recommended"]["unit_rule"] == "door_count_family"
    assert match["decision"] in {SHADOW_DECISION_AUTO, SHADOW_DECISION_REVIEW}


def test_shadow_matcher_blocks_installation_candidate_for_demolition_source():
    match = shadow_match_source(
        _source(name="木地板拆除", unit="㎡"),
        [
            _entry(1, name="实木地板安装", unit="㎡", code="ZS00101"),
            _entry(2, name="地板/地胶拆除", unit="㎡", code="ZS00003"),
        ],
    )

    assert match["recommended"]["entry"].item_id == 2
    assert all(candidate["entry"].item_id != 1 for candidate in match["candidates"])


def test_shadow_matcher_uses_candidate_name_action_before_work_content_notes():
    match = shadow_match_source(
        _source(name="马桶拆除", unit="套"),
        [
            _entry(
                1,
                name="连体式座便器安装",
                unit="套",
                code="AZ00084",
                work_content="拆除费用按安装费的一半计取",
            ),
            _entry(2, name="卫生洁具拆除", unit="套", code="ZS00020"),
        ],
    )

    assert all(candidate["entry"].item_id != 1 for candidate in match["candidates"])


def test_shadow_matcher_uses_thickness_to_rank_plaster_candidate():
    match = shadow_match_source(
        _source(name="墙面抹灰", unit="㎡", spec="20厚水泥砂浆抹灰找平"),
        [
            _entry(1, name="15mm厚水泥砂浆墙面抹灰", unit="㎡", code="ZS00162"),
            _entry(2, name="20mm厚水泥砂浆墙面抹灰", unit="㎡", code="ZS00163"),
            _entry(3, name="25mm厚水泥砂浆墙面抹灰", unit="㎡", code="ZS00164"),
        ],
    )

    assert match["recommended"]["entry"].item_id == 2


def test_shadow_matcher_filters_non_installation_catalog_for_mep_sheet():
    match = shadow_match_source(
        _source(name="插座安装", unit="个", spec="五孔保护性插座10A", sheet="机电工程量清单"),
        [
            _entry(1, name="柜门安装", unit="㎡", code="ZS00452"),
            _entry(2, name="普通插座安装86型", unit="套", code="AZ00052"),
        ],
    )

    assert match["recommended"]["entry"].item_id == 2


def test_shadow_matcher_does_not_prefer_information_socket_for_power_socket():
    match = shadow_match_source(
        _source(name="插座安装", unit="个", spec="五孔保护性插座10A", sheet="机电工程量清单"),
        [
            _entry(1, name="信息插座安装", unit="个", code="AZ00103"),
            _entry(2, name="普通插座安装86型", unit="套", code="AZ00052"),
        ],
    )

    assert match["recommended"]["entry"].item_id == 2


def test_shadow_matcher_returns_unmatched_when_only_hard_conflicts_exist():
    match = shadow_match_source(
        _source(name="墙面瓷砖湿贴", unit="㎡"),
        [_entry(1, name="地面瓷砖拆除", unit="㎡", code="ZS00001")],
    )

    assert match["decision"] == SHADOW_DECISION_NONE
    assert match["recommended"] is None
