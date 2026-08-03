from app.services.enterprise_quota_units import (
    CUBIC_METER_UNIT,
    SQUARE_METER_UNIT,
    normalize_enterprise_quota_unit,
)


def test_normalize_enterprise_quota_unit_converts_m2_aliases_to_square_meter_symbol():
    assert normalize_enterprise_quota_unit("m2") == SQUARE_METER_UNIT
    assert normalize_enterprise_quota_unit("M2") == SQUARE_METER_UNIT
    assert normalize_enterprise_quota_unit("m\u00b2") == SQUARE_METER_UNIT
    assert normalize_enterprise_quota_unit("  m2  ") == SQUARE_METER_UNIT
    assert normalize_enterprise_quota_unit("m") == "m"
    assert normalize_enterprise_quota_unit("") is None
    assert normalize_enterprise_quota_unit(None) is None


def test_normalize_enterprise_quota_unit_converts_m3_aliases_to_cubic_meter_text():
    assert normalize_enterprise_quota_unit("m3") == CUBIC_METER_UNIT
    assert normalize_enterprise_quota_unit("M3") == CUBIC_METER_UNIT
    assert normalize_enterprise_quota_unit("m\u00b3") == CUBIC_METER_UNIT
    assert normalize_enterprise_quota_unit("\u33a5") == CUBIC_METER_UNIT
    assert normalize_enterprise_quota_unit("  m3  ") == CUBIC_METER_UNIT
