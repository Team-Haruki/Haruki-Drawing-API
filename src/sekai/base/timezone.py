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


def region_display(region: str | None, region_label: str | None = None) -> str:
    """The region as drawn: the caller's localized ``region_label`` (e.g. ``日服(JP)``), else the upper-cased code.

    Only text uses this. Colours, assets and every other behaviour stay keyed on the raw region code.
    """
    label = (region_label or "").strip()
    return label or (region or "").strip().upper()


def id_with_region(item_id: object, region: str | None, region_label: str | None = None) -> str:
    """``123 · 日服(JP)`` with a region label, else the legacy ``123 (JP)``."""
    label = (region_label or "").strip()
    if label:
        return f"{item_id} · {label}"
    return f"{item_id} ({(region or '').strip().upper()})"


def region_tag(region: str | None, region_label: str | None, suffix: object) -> str:
    """A title tag such as ``日服(JP) 123`` with a region label, else the legacy ``JP-123``."""
    label = (region_label or "").strip()
    if label:
        return f"{label} {suffix}"
    return f"{(region or '').strip().upper()}-{suffix}"


UNKNOWN_TIME_TEXT = "未知时间"


def utc_offset_label(dt: datetime) -> str:
    """``UTC+8``, ``UTC+5:30`` or ``UTC-3`` for an aware datetime (Cloud's ``utcOffset``)."""
    offset = dt.utcoffset()
    seconds = int(offset.total_seconds()) if offset is not None else 0
    sign = "+"
    if seconds < 0:
        sign, seconds = "-", -seconds
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    return f"UTC{sign}{hours}" if minutes == 0 else f"UTC{sign}{hours}:{minutes:02d}"


def format_user_time(value: datetime | float | str | None, timezone_name: str | None = None) -> str:
    """The spec time format, mirroring Cloud's ``i18n.FormatUserTime``: ``2026-10-09 14:05 (UTC+8)``.

    ``value`` is shown in ``timezone_name`` (the request's time zone; default Asia/Shanghai). An aware datetime
    passed without a time zone keeps its own zone. A missing value is ``未知时间``.
    """
    dt = _user_datetime(value, timezone_name)
    if dt is None:
        return UNKNOWN_TIME_TEXT
    return f"{dt:%Y-%m-%d %H:%M} ({utc_offset_label(dt)})"


def format_user_time_range(
    start: datetime | float | str | None, end: datetime | float | str | None, timezone_name: str | None = None
) -> str:
    """``2026-10-09 14:05 ~ 2026-10-12 20:59 (UTC+8)``: one offset when both ends share it."""
    start_dt, end_dt = _user_datetime(start, timezone_name), _user_datetime(end, timezone_name)
    if start_dt is None or end_dt is None:
        return f"{format_user_time(start_dt)} ~ {format_user_time(end_dt)}"
    start_offset, end_offset = utc_offset_label(start_dt), utc_offset_label(end_dt)
    if start_offset != end_offset:
        return f"{format_user_time(start_dt)} ~ {format_user_time(end_dt)}"
    return f"{start_dt:%Y-%m-%d %H:%M} ~ {end_dt:%Y-%m-%d %H:%M} ({start_offset})"


def _user_datetime(value: datetime | float | str | None, timezone_name: str | None) -> datetime | None:
    if isinstance(value, datetime) and timezone_name is None:
        return value if value.tzinfo is not None else localize_datetime(value, None)
    return localize_datetime(value, timezone_name)


def caller_label(request: object, key: str, fallback: str, /, **values: object) -> str:
    """Display text the caller localized for ``key`` (``request.labels``), else Drawing's own ``fallback``.

    Labels are only drawn, never compared. ``{name}`` slots in either text are filled from ``values``.
    """
    labels = getattr(request, "labels", None)
    text = labels.get(key) if isinstance(labels, dict) else None
    if not isinstance(text, str) or not text.strip():
        text = fallback
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


class TimeZoneRequest(BaseModel):
    timezone: str = Field(default=DEFAULT_TIMEZONE)
    dt: int | None = Field(default=None)
    # Localized display name of the request's region code, sent by the caller (Cloud). Optional: without it the
    # drawers fall back to the upper-cased code (see region_display).
    region_label: str | None = Field(default=None)
    # Localized display text for Drawing's own labels, keyed by label key (see caller_label and AGENTS.md
    # "Caller labels and raw keys"). Optional: a missing key falls back to Drawing's text.
    labels: dict[str, str] | None = Field(default=None)

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
