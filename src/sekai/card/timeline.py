"""Acquisition timeline using the existing Card Box widgets and thumbnail renderer."""

from collections import OrderedDict
from datetime import datetime
from typing import Any

from src.sekai.base import DEFAULT_BOLD_FONT, DEFAULT_FONT
from src.sekai.base.plot import FillBg, Frame, Grid, HSplit, RoundRectBg, Spacer, TextBox, TextStyle, VSplit
from src.sekai.base.timezone import datetime_from_millis

from .panel import card_box_panel_bg

CARD_SIZE = 72
PADDING = 5
GAP = 6
COLUMN_WIDTH = (CARD_SIZE + PADDING * 2) * 2 + GAP
MAX_COLUMNS = 9
CARDS_PER_COLUMN = 48


def acquired_datetime(record: dict, timezone: str) -> datetime | None:
    timestamp = record.get("acquired_at")
    if not isinstance(timestamp, int) or timestamp <= 0:
        return None
    try:
        return datetime_from_millis(timestamp, timezone)
    except (ValueError, OverflowError, OSError):
        return None


def timeline_columns(records: list[dict], timezone: str) -> list[tuple[str, list[dict]]]:
    owned = [record for record in records if record.get("has", record.get("has_card", False))]
    dated = [(record, acquired_datetime(record, timezone)) for record in owned]
    dated.sort(key=lambda item: (item[1] is None, item[1].timestamp() if item[1] else 0, item[0]["card"]["card_id"]))
    months: dict[str, list[dict]] = OrderedDict()
    for record, date in dated:
        months.setdefault(date.strftime("%Y.%m") if date else "获取时间未知", []).append(record)
    columns = []
    for month, cards in months.items():
        for start in range(0, len(cards), CARDS_PER_COLUMN):
            label = month if start == 0 else f"{month} · 续{start // CARDS_PER_COLUMN + 1}"
            columns.append((label, cards[start : start + CARDS_PER_COLUMN]))
    return columns


def timeline_width(columns: list[tuple[str, list[dict]]]) -> int:
    count = max(1, min(MAX_COLUMNS, len(columns)))
    return max(480, COLUMN_WIDTH * count + 12 * (count - 1) + 32)


def draw_timeline(renderer: Any, columns: list[tuple[str, list[dict]]]) -> None:
    with (
        VSplit()
        .set_w(renderer.panel_width)
        .set_padding(16)
        .set_sep(16)
        .set_bg(card_box_panel_bg(alpha=80))
        .set_content_align("lt")
        .set_item_align("lt")
    ):
        with HSplit().set_sep(20).set_item_align("c"):
            TextBox("卡牌一览", TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(45, 52, 62)))
            total = sum(len(cards) for _, cards in columns)
            TextBox(f"获取时间 · {total}张", TextStyle(font=DEFAULT_FONT, size=17, color=(88, 97, 116)))
        if not columns:
            TextBox("没有可展示的已拥有卡牌", TextStyle(font=DEFAULT_FONT, size=18, color=(88, 97, 116)))
        for offset in range(0, len(columns), MAX_COLUMNS):
            with HSplit().set_sep(12).set_content_align("lt").set_item_align("lt"):
                for label, records in columns[offset : offset + MAX_COLUMNS]:
                    _draw_month(renderer, label, records)
        TextBox(
            "按获取时间由早到晚排列；同月逐行从左到右。底色对应角色。",
            TextStyle(font=DEFAULT_FONT, size=13, color=(91, 101, 119)),
        ).set_w(renderer.panel_width - 32)


def _draw_month(renderer: Any, label: str, records: list[dict]) -> None:
    with VSplit().set_w(COLUMN_WIDTH).set_sep(8).set_content_align("t").set_item_align("c"):
        TextBox(label, TextStyle(font=DEFAULT_BOLD_FONT, size=19, color=(55, 68, 88)), overflow="shrink").set_w(
            COLUMN_WIDTH
        ).set_content_align("c")
        with Frame().set_size((COLUMN_WIDTH, 18)).set_content_align("c"):
            Spacer(w=COLUMN_WIDTH, h=2).set_bg(FillBg((109, 129, 152, 115)))
            Spacer(w=8, h=8).set_bg(RoundRectBg((100, 126, 150, 230), 4))
        TextBox(f"{len(records)}张", TextStyle(font=DEFAULT_FONT, size=12, color=(103, 112, 128))).set_w(
            COLUMN_WIDTH
        ).set_content_align("c")
        with Grid(col_count=2).set_sep(GAP, GAP).set_content_align("lt"):
            for record in records:
                color = renderer._character_color(record["card"]["character_id"])
                tint = (*color[:3], 45)
                with (
                    VSplit()
                    .set_padding(PADDING)
                    .set_sep(2)
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_bg(RoundRectBg(tint, 7))
                ):
                    renderer._draw_card(record)
                    date = acquired_datetime(record, renderer.rqd.timezone)
                    TextBox(
                        f"{date.day:02d}日" if date else "未知",
                        TextStyle(font=DEFAULT_FONT, size=11, color=(73, 81, 97, 220)),
                    ).set_w(CARD_SIZE).set_content_align("c")
