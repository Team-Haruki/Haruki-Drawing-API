from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

DEFAULT_TIMEZONE = "Asia/Shanghai"


def normalize_timezone(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return DEFAULT_TIMEZONE
    try:
        ZoneInfo(text)
        return text
    except ZoneInfoNotFoundError:
        return DEFAULT_TIMEZONE


def get_timezone(value: str | None) -> ZoneInfo:
    return ZoneInfo(normalize_timezone(value))


def request_now(value: str | None) -> datetime:
    return datetime.now(get_timezone(value))


def normalize_unix_millis(value: float) -> int:
    ts = int(value)
    if ts <= 0:
        return 0
    if abs(ts) < 1_000_000_000_000:
        return ts * 1000
    return ts


def parse_datetime_utc(value: datetime | float | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, int | float):
        ts = normalize_unix_millis(value)
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts / 1000, tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.lstrip("+-").isdigit():
            return parse_datetime_utc(int(text))
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    raise TypeError(f"unsupported datetime value: {type(value)!r}")


def localize_datetime(value: datetime | float | str | None, timezone_name: str | None) -> datetime | None:
    dt = parse_datetime_utc(value)
    if dt is None:
        return None
    return dt.astimezone(get_timezone(timezone_name))


def datetime_from_millis(value: float | str | None, timezone_name: str | None) -> datetime | None:
    return localize_datetime(value, timezone_name)


class TimeZoneRequest(BaseModel):
    timezone: str = Field(default=DEFAULT_TIMEZONE)
    dt: int | None = Field(default=None)

    def model_post_init(self, __context, /) -> None:
        self.timezone = normalize_timezone(self.timezone)
        if self.dt is not None:
            self.dt = normalize_unix_millis(self.dt)

    def apply_timezone(self, *targets) -> None:
        for target in targets:
            _apply_timezone(target, self.timezone)


def _apply_timezone(target, timezone_name: str) -> None:
    if target is None:
        return
    if isinstance(target, list | tuple):
        for item in target:
            _apply_timezone(item, timezone_name)
        return
    if hasattr(target, "timezone"):
        target.timezone = timezone_name
