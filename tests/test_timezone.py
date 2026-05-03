from datetime import UTC, datetime

from src.sekai.base.timezone import (
    DEFAULT_TIMEZONE,
    TimeZoneRequest,
    localize_datetime,
    normalize_timezone,
    parse_datetime_utc,
)


def test_parse_datetime_utc_accepts_seconds_millis_and_iso_strings():
    expected = datetime.fromtimestamp(1_700_000_000, tz=UTC)

    assert parse_datetime_utc(1_700_000_000) == expected
    assert parse_datetime_utc(1_700_000_000_000) == expected
    assert parse_datetime_utc("1700000000000") == expected
    assert parse_datetime_utc("2023-11-14T22:13:20Z") == expected


def test_localize_datetime_uses_requested_timezone():
    localized = localize_datetime("2024-01-01T00:00:00Z", "Asia/Shanghai")

    assert localized is not None
    assert localized.tzinfo is not None
    assert localized.hour == 8
    assert localized.tzinfo.key == "Asia/Shanghai"


def test_timezone_request_normalizes_bad_timezone_and_seconds_dt():
    request = TimeZoneRequest(timezone="Not/AZone", dt=1_700_000_000)

    assert normalize_timezone("Not/AZone") == DEFAULT_TIMEZONE
    assert request.timezone == DEFAULT_TIMEZONE
    assert request.dt == 1_700_000_000_000
