from __future__ import annotations

from typing import Any


SQUARE_METER_UNIT = "\u33a1"
CUBIC_METER_UNIT = "m\u00b3"
_AREA_UNIT_ALIASES = {"m2", "M2", "m\u00b2", "M\u00b2", SQUARE_METER_UNIT}
_VOLUME_UNIT_ALIASES = {"m3", "M3", "m\u00b3", "M\u00b3", "\u33a5"}


def normalize_enterprise_quota_unit(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    compact = text.replace(" ", "")
    if compact in _AREA_UNIT_ALIASES:
        return SQUARE_METER_UNIT
    if compact in _VOLUME_UNIT_ALIASES:
        return CUBIC_METER_UNIT
    return text
