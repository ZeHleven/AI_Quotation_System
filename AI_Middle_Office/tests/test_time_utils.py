from datetime import datetime, timezone

from app.core.time_utils import APP_TIMEZONE_NAME, app_local_naive, as_app_time, as_utc, parse_iso_datetime


def test_app_timezone_name_is_explicit():
    assert APP_TIMEZONE_NAME == "Asia/Shanghai"


def test_aware_utc_converts_to_app_local_naive():
    value = datetime(2026, 6, 8, 1, 30, tzinfo=timezone.utc)

    result = app_local_naive(value)

    assert result == datetime(2026, 6, 8, 9, 30)
    assert result.tzinfo is None


def test_naive_value_is_treated_as_app_local_for_utc_conversion():
    value = datetime(2026, 6, 8, 9, 30)

    result = as_utc(value)

    assert result == datetime(2026, 6, 8, 1, 30, tzinfo=timezone.utc)


def test_parse_iso_datetime_accepts_z_suffix():
    result = parse_iso_datetime("2026-06-08T01:30:00Z")

    assert result == datetime(2026, 6, 8, 1, 30, tzinfo=timezone.utc)
    assert as_app_time(result).hour == 9
