from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field as dataclass_field
import logging
from pathlib import Path
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.asset_key import AssetKey, legacy_key
from src.sekai.base.chrome import (
    AMBER as _AMBER,
    CAPTION_STYLE as _CAPTION_STYLE,
    CHIP_STYLE as _CHIP_STYLE,
    DIM as _DIM,
    FAINT as _FAINT,
    GREEN as _GREEN,
    GREY as _GREY,
    INK as _INK,
    NOTE_STYLE as _NOTE_STYLE,
    PANEL_PAD as _PANEL_PAD,
    PILL_STYLE as _PILL_STYLE,
    RED as _RED,
    SUBTITLE_STYLE as _SUBTITLE_STYLE,
    TEXT as _TEXT,
    TITLE_STYLE as _TITLE_STYLE,
    Color,
    alpha as _alpha,
    chip as _chip,
    fit_style as _fit_style,
    fitted as _fitted,
    ink as _ink,
    mix as _mix,
    panel as _panel,
    pill as _pill,
    section_header as _section_header,
    soft_chip as _soft_chip,
    text_w as _text_w,
)
from src.sekai.base.draw import (
    BG_PADDING,
    DIFF_COLORS,
    SEKAI_BLUE_BG,
    Canvas,
    TextBox,
    add_request_watermark,
)
from src.sekai.base.paint_types import WHITE, ImageTint
from src.sekai.base.plot import (
    FillBg,
    Frame,
    HSplit,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextStyle,
    VSplit,
)
from src.sekai.base.text_layout import get_layout_font, get_text_size, ink_centered_text_offset_y
from src.sekai.base.timezone import region_display
from src.sekai.base.utils import ImageSource, get_asset_image_ref
from src.sekai.profile.custom_profile.font_field import basic_text_field
from src.sekai.profile.drawer import (
    CardFullThumbnailBox,
    get_card_full_thumbnail_layers,
    get_profile_card,
)
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT

logger = logging.getLogger(__name__)

# 从 model.py 导入数据模型
from .model import (
    DeckPlannerBoostRow,
    DeckPlannerInfo,
    DeckPlannerSong,
    DeckRequest,
)

OMAKASE_MUSIC_ID = 10000
OMAKASE_MUSIC_DIFFS = ["master", "expert", "hard"]
_DFS_GA_DISPLAY_NAME = "DFS 预热遗传"
RECOMMEND_ALG_NAMES = {
    "dfs": "暴力搜索",
    "DFS": "暴力搜索",
    "sa": "模拟退火",
    "SA": "模拟退火",
    "ga": "遗传算法",
    "GA": "遗传算法",
    "dfs_ga": _DFS_GA_DISPLAY_NAME,
    "dfs-ga": _DFS_GA_DISPLAY_NAME,
    "dga": _DFS_GA_DISPLAY_NAME,
    "DGA": _DFS_GA_DISPLAY_NAME,
    "rl": "强化学习",
    "RL": "强化学习",
    "all": "全部算法",
    "ALL": "全部算法",
}

BOOST_BONUS_DICT = {
    0: 1,
    1: 5,
    2: 10,
    3: 15,
    4: 20,
    5: 25,
    6: 27,
    7: 29,
    8: 31,
    9: 33,
    10: 35,
}


def format_skill_rate(rate: float) -> str:
    normalized = round(rate, 1)
    return str(int(normalized)) if float(int(normalized)) == normalized else f"{normalized:.1f}"


def format_algorithm_label(alg: str | None) -> str:
    if not alg:
        return ""

    short_names = {
        "dfs": "DFS",
        "sa": "SA",
        "ga": "GA",
        "dfs_ga": "DGA",
        "dfs-ga": "DGA",
        "dga": "DGA",
        "rl": "RL",
        "all": "ALL",
    }
    parts = [part.strip() for part in alg.replace("＋", "+").split("+") if part.strip()]
    labels = [short_names.get(part.lower(), part.upper()) for part in parts]
    return "+".join(labels)


def algorithm_label_font_size(alg: str | None) -> int:
    label = format_algorithm_label(alg)
    if len(label) <= 8:
        return 12
    if len(label) <= 11:
        return 11
    if len(label) <= 14:
        return 10
    return 9


def format_skill_order_text(strategy: str | None) -> str:
    match (strategy or "").strip().lower():
        case "average":
            return "技能顺序: 平均情况"
        case "max":
            return "技能顺序: 最优顺序"
        case "min":
            return "技能顺序: 最差顺序"
        case "specific":
            return "技能顺序: 指定顺序"
        case _:
            return ""


def format_skill_reference_text(strategy: str | None) -> str:
    match (strategy or "").strip().lower():
        case "average":
            return "BloomFes花前吸取: 平均值"
        case "max":
            return "BloomFes花前吸取: 最大值"
        case "min":
            return "BloomFes花前吸取: 最小值"
        case _:
            return ""


def build_algorithm_runtime_text(cost_times: dict | None, wait_times: dict | None) -> str:
    if not cost_times:
        return ""

    wait_times = wait_times or {}
    lines = ["本次组卡使用算法:"]
    for index, (alg, cost) in enumerate(cost_times.items(), start=1):
        alg_name = RECOMMEND_ALG_NAMES.get(alg, alg)
        wait_time = wait_times.get(alg, 0.0)
        lines.append(f"{index}. {alg_name} 等待{wait_time:.2f}s / 耗时{cost:.2f}s")
    return "\n".join(lines)


def format_planner_int(value: int | None) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def format_planner_optional_int(value: int | None) -> str:
    if value is None:
        return "-"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "-"
    if number <= 0:
        return "-"
    return f"{number:,}"


def planner_cover_key(song) -> str:
    return str(song.music_id or legacy_key(song.music_cover_path) or song.title)


def _planner_rows(planner: DeckPlannerInfo) -> list[tuple[DeckPlannerSong, DeckPlannerBoostRow | None]]:
    rows: list[tuple[DeckPlannerSong, DeckPlannerBoostRow | None]] = []
    for song in planner.songs:
        rows.extend((song, row) for row in song.rows or [None])
    return rows


# ---------------------------------------------------------------------------
# Page styles: glass section panels with accent bars, white result tiles, ink-centred chips
# ---------------------------------------------------------------------------

# Shared house style (chips, panels, section headers) lives in src/sekai/base/chrome.py.
_BONUS_ORANGE: Color = (226, 112, 36, 255)

# One accent per recommendation type: the title bar, the hero band and the sort-target values.
_DECK_ACCENTS: dict[str, Color] = {
    "event": (64, 132, 226, 255),
    "wl": (146, 84, 222, 255),
    "bonus": (228, 84, 110, 255),
    "wl_bonus": (228, 84, 110, 255),
    "challenge": (236, 140, 24, 255),
    "challenge_all": (236, 140, 24, 255),
    "mysekai": (44, 170, 110, 255),
    "unit_attr": (30, 168, 190, 255),
    "no_event": (112, 122, 146, 255),
}
_DECK_ACCENT_FALLBACK: Color = (64, 132, 226, 255)

_COL_LABEL_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=_DIM)
_VALUE_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=_INK)

_TILE_RADIUS = 12
_MIN_TILE_W = 860


def _deck_accent(rqd: DeckRequest) -> Color:
    return _DECK_ACCENTS.get(rqd.recommend_type, _DECK_ACCENT_FALLBACK)


def format_deck_int(value: float | None) -> str:
    """Thousand-separated integer; ``-`` for a missing value."""
    if value is None:
        return "-"
    return f"{int(value):,}"


def format_deck_percent(value: float | None, *, digits: int = 2) -> str:
    """``270%`` / ``252.3%`` / ``105.75%``: trailing zeros trimmed, at most ``digits`` decimals."""
    if value is None:
        return "-"
    text = f"{value:,.{digits}f}".rstrip("0").rstrip(".")
    return f"{text}%"


# Mask columns fainter than this are anti-aliasing fringe the eye does not read as part of a digit.
_INK_COLUMN_ALPHA = 32
_ink_columns_cache: dict[tuple, tuple[int, int]] = {}


def _ink_columns(style: TextStyle, text: str) -> tuple[int, int]:
    """``text``'s visible ink as ``[left, right)`` columns from the pen origin, read off its BASIC glyph mask.

    ``getbbox`` is ink-tight vertically but spans the advance horizontally: at 10 px the Source Han digits
    leave their last column empty, so a number padded by its advance box sits a pixel left of centre."""
    font = get_layout_font(style.font, style.size)
    bbox = font.getbbox(text)
    path = getattr(font, "path", None)
    if not isinstance(path, str):
        # Pillow's in-memory default face (CI bundles no fonts) has no file to rasterize; keep the advance box.
        return bbox[0], bbox[2]
    key = (path, style.size, text)
    cached = _ink_columns_cache.get(key)
    if cached is not None:
        return cached
    columns = bbox[0], bbox[2]
    try:
        field, field_bbox = basic_text_field(Path(path), text, style.size)
    except (OSError, RuntimeError, ValueError):
        field = None
    if field is not None:
        width = field.width
        solid = [x for x in range(width) if max(field.pixels[x::width]) >= _INK_COLUMN_ALPHA]
        if solid:
            columns = field_bbox[0] + solid[0], field_bbox[0] + solid[-1] + 1
    if len(_ink_columns_cache) >= 4096:
        _ink_columns_cache.clear()
    _ink_columns_cache[key] = columns
    return columns


def _id_badge(text: str, fill, style: TextStyle, *, radius: int = 4, gap=(4, 2)) -> TextBox:
    """A small tag whose ink sits dead centre, ``gap`` pixels from each edge of the drawn fill.

    Sized for the Skia backend deck pages render through, which fills a rounded rect at exactly the widget
    size, so the box is ``ink + 2 * gap`` in each direction and the text is shifted so its ink starts ``gap``
    in from the near edges. The Pillow fallback draws its fill one pixel past the far edges, leaving one extra
    pixel below and to the right there."""
    gap_x, gap_y = gap
    font = get_layout_font(style.font, style.size)
    _, top, _, bottom = font.getbbox(text)
    left, right = _ink_columns(style, text)
    # Painter.text puts the baseline ink_height("哇") below the line top; getbbox is relative to the ascender top.
    ink_top = get_text_size(font, "哇")[1] + top - font.getmetrics()[0]
    natural_w = right - left + 2 * gap_x
    # Never narrower than the advance box, or TextBox would clip the line to fit.
    width = max(natural_w, get_text_size(font, text)[0])
    return (
        TextBox(text, style)
        .set_padding(0)
        .set_size((width, bottom - top + 2 * gap_y))
        .set_content_align("lt")
        .set_text_offset((gap_x - left + (width - natural_w) // 2, gap_y - ink_top))
        .set_bg(RoundRectBg(fill, radius, blur_glass=False))
    )


def _circle_badge(text: str, fill, diameter: int, style: TextStyle) -> None:
    with (
        Frame()
        .set_size((diameter, diameter))
        .set_content_align("c")
        .set_bg(RoundRectBg(fill, diameter // 2, blur_glass=False))
    ):
        TextBox(text, style).set_text_offset((0, ink_centered_text_offset_y(style.font, style.size, text, style.size)))


# ---------------------------------------------------------------------------
# Event planner block
# ---------------------------------------------------------------------------

_PLANNER_ACCENT: Color = (0, 168, 206, 255)
_PLANNER_ENERGY: Color = (142, 94, 190, 255)
_PLANNER_COLS = (("每把PT", 140), ("需要把数", 128), ("体力", 112), ("日速", 140))
_PLANNER_ROW_H = 68
_PLANNER_COL_SEP = 12
_PLANNER_DEFAULT_W = 960


def _planner_song_w(width: int) -> int:
    fixed = sum(w for _, w in _PLANNER_COLS) + _PLANNER_COL_SEP * len(_PLANNER_COLS) + 2 * 12
    return max(300, width - 2 * _PANEL_PAD - fixed)


def _draw_planner_summary(planner: DeckPlannerInfo) -> None:
    chip_fill = (255, 255, 255, 200)
    chip_style = _CHIP_STYLE.replace(size=15, color=_TEXT)
    _section_header(
        "活动规划",
        _PLANNER_ACCENT,
        captions=[f"来源 {planner.target_source}" if planner.target_source else ""],
    )
    with HSplit().set_content_align("l").set_item_align("c").set_sep(8).set_padding(0):
        _chip(f"目标 {format_planner_int(planner.target_point)} pt", chip_fill, style=chip_style, radius=11)
        _chip(f"当前 {format_planner_int(planner.current_point)} pt", chip_fill, style=chip_style, radius=11)
        _chip(
            f"还需 {format_planner_int(planner.remaining_point)} pt",
            _PLANNER_ACCENT,
            style=chip_style.replace(color=WHITE),
            radius=11,
        )


def _draw_planner_header(song_w: int) -> None:
    with HSplit().set_content_align("l").set_item_align("c").set_sep(_PLANNER_COL_SEP).set_padding((12, 0)):
        TextBox("歌曲 / 火数", _COL_LABEL_STYLE).set_w(song_w).set_content_align("l")
        for label, width in _PLANNER_COLS:
            TextBox(label, _COL_LABEL_STYLE).set_w(width).set_content_align("c")


def _draw_planner_song_cell(
    song: DeckPlannerSong,
    row: DeckPlannerBoostRow | None,
    planner_music_imgs: dict[str, ImageSource],
    song_w: int,
) -> None:
    with HSplit().set_w(song_w).set_h(_PLANNER_ROW_H).set_content_align("l").set_item_align("c").set_sep(10):
        diff = (song.difficulty or "").lower()
        cover = planner_music_imgs.get(legacy_key(song.music_cover_path) or planner_cover_key(song))
        _draw_music_jacket(cover, diff, 50, omakase=song.music_id == OMAKASE_MUSIC_ID)

        text_w = song_w - 64
        with VSplit().set_w(text_w).set_content_align("l").set_item_align("l").set_sep(4).set_padding(0):
            TextBox(
                song.title,
                TextStyle(font=DEFAULT_BOLD_FONT, size=17, color=_INK),
                overflow="shrink",
            ).set_w(text_w)
            with HSplit().set_content_align("l").set_item_align("c").set_sep(6).set_padding(0):
                diff_color = DIFF_COLORS.get(diff)
                _chip(
                    (song.difficulty or "DIFF").upper(),
                    diff_color if isinstance(diff_color, tuple) else _GREY,
                    style=_CHIP_STYLE.replace(size=12),
                    radius=8,
                    padding=(6, 2),
                )
                _soft_chip(f"{row.boost}火" if row else "-", _PLANNER_ENERGY, radius=8, padding=(6, 2))


def _draw_planner_number_cell(
    text: str,
    sub_text: str,
    width: int,
    style: TextStyle,
    color: tuple[int, int, int] | Color = _INK,
) -> None:
    with VSplit().set_w(width).set_h(_PLANNER_ROW_H).set_content_align("c").set_item_align("c").set_sep(0):
        _ink(
            TextBox(text, _fit_style(text, style.replace(color=color), width, min_size=1)).set_w(width)
        ).set_content_align("c")
        TextBox(sub_text, TextStyle(font=DEFAULT_FONT, size=13, color=_DIM)).set_w(width).set_content_align("c")


def _draw_planner_row(
    planner: DeckPlannerInfo,
    song: DeckPlannerSong,
    row: DeckPlannerBoostRow | None,
    planner_music_imgs: dict[str, ImageSource],
    style: TextStyle,
    song_w: int,
) -> None:
    widths = [width for _, width in _PLANNER_COLS]
    with (
        HSplit()
        .set_content_align("l")
        .set_item_align("c")
        .set_sep(_PLANNER_COL_SEP)
        .set_padding((12, 4))
        .set_bg(RoundRectBg((255, 255, 255, 150), 10, blur_glass=False))
    ):
        _draw_planner_song_cell(song, row, planner_music_imgs, song_w)
        _draw_planner_number_cell(format_planner_int(row.point_per_play if row else 0), "pt/把", widths[0], style)
        _draw_planner_number_cell(
            format_planner_int(row.plays if row else 0), "把", widths[1], style, _mix(_PLANNER_ACCENT, _INK, 0.1)
        )
        _draw_planner_number_cell(format_planner_int(row.energy if row else 0), "火", widths[2], style, _PLANNER_ENERGY)
        _draw_planner_number_cell(format_planner_optional_int(planner.daily_point), "pt/日", widths[3], style)


def _draw_planner_tips(planner: DeckPlannerInfo, text_w: int) -> None:
    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(2).set_padding((4, 0)):
        for tip in (
            "活动规划按当前数据估算，实际结算以游戏内和榜线更新为准。",
            "未指定当前 pt 时按 0 计算；不写歌曲时默认虾 EXPERT / 龙 HARD。",
        ):
            TextBox(tip, _NOTE_STYLE, use_real_line_count=True).set_w(text_w)
        for warning in planner.warnings or []:
            TextBox(
                f"提示: {warning}",
                TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=_RED),
                use_real_line_count=True,
            ).set_w(text_w)


def draw_event_planner_block(
    planner: DeckPlannerInfo, planner_music_imgs: dict[str, ImageSource], width: int = _PLANNER_DEFAULT_W
) -> None:
    rows = _planner_rows(planner)
    song_w = _planner_song_w(width)
    style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=_INK)
    with _panel(width).set_sep(8):
        _draw_planner_summary(planner)
        Spacer(h=2)
        _draw_planner_header(song_w)
        if rows:
            for song, row in rows:
                _draw_planner_row(planner, song, row, planner_music_imgs, style, song_w)
        else:
            TextBox("没有可展示的规划歌曲", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=_RED))
        _draw_planner_tips(planner, width - 2 * _PANEL_PAD - 8)


_RECOMMEND_TYPES_WITHOUT_LIVE_SUFFIX = {"mysekai", "challenge", "challenge_all", "bonus", "wl_bonus"}


_DECK_NOUN = "组卡"
_PLANNER_NOUN = "规划"


def _recommend_type_title(
    recommend_type: str, event_id: int | None, wl_chara_name: str | None, noun: str = _DECK_NOUN
) -> str:
    """The title of a recommend type; ``noun`` is what the page is (a deck recommendation or a plan)."""
    if recommend_type == "mysekai":
        return f"烤森活动#{event_id}{noun}" if event_id else f"烤森模拟活动{noun}"
    if recommend_type in {"challenge", "challenge_all"}:
        return f"每日挑战{noun}"
    if recommend_type == "bonus":
        return f"活动#{event_id}加成{noun}"
    if recommend_type == "wl_bonus":
        return f"WL活动#{event_id}加成{noun}"
    if recommend_type == "event":
        return f"活动#{event_id}{noun}"
    if recommend_type == "wl":
        if event_id:
            return f"WL活动#{event_id}{noun}"
        return f"WL模拟{noun}" if wl_chara_name else f"WL终章活动{noun}"
    return {"unit_attr": f"团队+颜色模拟活动{noun}", "no_event": f"无活动{noun}"}.get(recommend_type, "")


# Shown only when the caller sent no live_name for the live_type key.
_LIVE_TYPE_FALLBACK_LABELS = {"multi": "多人", "solo": "单人", "auto": "AUTO"}


def _recommend_live_label(live_type: str | None, live_name: str | None) -> str:
    """The caller's localized ``live_name`` for any live type, else this renderer's label for the ``live_type`` key."""
    if name := (live_name or "").strip():
        return name
    return _LIVE_TYPE_FALLBACK_LABELS.get((live_type or "").strip().lower(), "")


def _recommend_live_suffix(live_type: str | None, live_name: str | None) -> str:
    label = _recommend_live_label(live_type, live_name)
    return f"({label})" if label else ""


def build_recommend_title(
    recommend_type: str,
    event_id: int | None,
    wl_chara_name: str | None,
    live_type: str | None,
    live_name: str | None,
) -> str:
    title = _recommend_type_title(recommend_type, event_id, wl_chara_name)
    if recommend_type in _RECOMMEND_TYPES_WITHOUT_LIVE_SUFFIX:
        return title
    return title + _recommend_live_suffix(live_type, live_name)


@dataclass(frozen=True)
class _DeckRecommendAssets:
    chara_icon: ImageSource | None
    wl_chara_icon: ImageSource | None
    unit_logo: ImageSource | None
    attr_icon: ImageSource | None
    music_cover: ImageSource | None
    canvas_thumbnail: ImageSource | None
    card_layers: dict[tuple, object]
    compare_music_imgs: dict[str, ImageSource]
    planner_music_imgs: dict[str, ImageSource]
    deck_chara_icons: dict[str, ImageSource] = dataclass_field(default_factory=dict)


def _deck_optional_asset_tasks(rqd: DeckRequest) -> dict[str, object]:
    tasks = {}
    optional_paths = {
        "wl_chara": rqd.wl_chara_icon_path,
        "unit_logo": rqd.unit_logo_path,
        "attr_icon": rqd.attr_icon_path,
    }
    if not rqd.music_compare:
        optional_paths["music_cover"] = rqd.music_cover_path
    optional_paths["canvas_thumb"] = rqd.canvas_thumbnail_path
    for key, path in optional_paths.items():
        if path:
            tasks[key] = get_asset_image_ref(ASSETS_BASE_DIR, path)
    return tasks


def _collect_deck_asset_requests(rqd: DeckRequest) -> tuple[list, list[tuple], list[str], list[str]]:
    card_thumb_tasks = []
    card_thumb_keys = []
    compare_cover_paths: list[AssetKey] = []
    planner_cover_paths: list[AssetKey] = []
    for deck in rqd.deck_data:
        if rqd.music_compare and deck.music_cover_path and deck.music_cover_path not in compare_cover_paths:
            compare_cover_paths.append(deck.music_cover_path)
        for card in [*deck.card_data, *deck.support_card_data]:
            card_thumb_tasks.append(get_card_full_thumbnail_layers(card.card_thumbnail))
            card_thumb_keys.append(
                (
                    card.card_thumbnail.card_id,
                    card.card_thumbnail.is_after_training,
                    legacy_key(card.card_thumbnail.card_thumbnail_path),
                )
            )
    if rqd.event_planner:
        planner_cover_paths.extend(
            song.music_cover_path
            for song in rqd.event_planner.songs
            if song.music_cover_path and song.music_cover_path not in planner_cover_paths
        )
    return card_thumb_tasks, card_thumb_keys, compare_cover_paths, planner_cover_paths


async def _load_deck_recommend_assets(rqd: DeckRequest) -> _DeckRecommendAssets:
    chara_icon = None
    if rqd.chara_icon_path:
        chara_icon = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.chara_icon_path)

    deck_tasks = _deck_optional_asset_tasks(rqd)
    card_thumb_tasks, card_thumb_keys, compare_cover_paths, planner_cover_paths = _collect_deck_asset_requests(rqd)

    compare_tasks = [get_asset_image_ref(ASSETS_BASE_DIR, path) for path in compare_cover_paths]
    planner_tasks = [get_asset_image_ref(ASSETS_BASE_DIR, path) for path in planner_cover_paths]
    deck_chara_paths: list[AssetKey] = []
    for deck in rqd.deck_data:
        if deck.chara_icon_path and deck.chara_icon_path not in deck_chara_paths:
            deck_chara_paths.append(deck.chara_icon_path)
    deck_chara_tasks = [get_asset_image_ref(ASSETS_BASE_DIR, path) for path in deck_chara_paths]
    deck_keys = list(deck_tasks)
    started_at = time.perf_counter()
    results = await asyncio.gather(
        *deck_tasks.values(), *card_thumb_tasks, *compare_tasks, *planner_tasks, *deck_chara_tasks
    )
    logger.debug(
        "[perf] compose_deck_recommend_image preload %d items: %.3fs",
        len(deck_keys) + len(card_thumb_tasks) + len(compare_tasks) + len(planner_tasks),
        time.perf_counter() - started_at,
    )

    deck_images = dict(zip(deck_keys, results[: len(deck_keys)]))
    thumbnail_end = len(deck_keys) + len(card_thumb_tasks)
    thumbnail_results = results[len(deck_keys) : thumbnail_end]
    compare_end = thumbnail_end + len(compare_tasks)
    compare_results = results[thumbnail_end:compare_end]
    planner_end = compare_end + len(planner_tasks)
    planner_results = results[compare_end:planner_end]
    deck_chara_results = results[planner_end:]
    return _DeckRecommendAssets(
        chara_icon=chara_icon,
        wl_chara_icon=deck_images.get("wl_chara"),
        unit_logo=deck_images.get("unit_logo"),
        attr_icon=deck_images.get("attr_icon"),
        music_cover=deck_images.get("music_cover"),
        canvas_thumbnail=deck_images.get("canvas_thumb"),
        card_layers=dict(zip(card_thumb_keys, thumbnail_results)),
        compare_music_imgs={legacy_key(path): img for path, img in zip(compare_cover_paths, compare_results)},
        planner_music_imgs={legacy_key(path): img for path, img in zip(planner_cover_paths, planner_results)},
        deck_chara_icons={legacy_key(path): img for path, img in zip(deck_chara_paths, deck_chara_results)},
    )


def _deck_score_name(rqd: DeckRequest) -> str:
    return "分数" if rqd.recommend_type in {"challenge", "challenge_all", "no_event"} else "PT"


def _deck_base_title(rqd: DeckRequest) -> str:
    """The title without its live suffix, which is drawn as a chip beside it."""
    noun = _PLANNER_NOUN if rqd.event_planner else _DECK_NOUN
    return _recommend_type_title(rqd.recommend_type, rqd.event_id, rqd.wl_chara_name, noun)


def _deck_live_label(rqd: DeckRequest) -> str:
    if rqd.recommend_type in _RECOMMEND_TYPES_WITHOUT_LIVE_SUFFIX:
        return ""
    return _recommend_live_label(rqd.live_type, rqd.live_name)


# ---------------------------------------------------------------------------
# Layout: which value columns a request shows and how wide every tile is
# ---------------------------------------------------------------------------

_RANK_W = 34
# All-character challenge rows carry the deck's character beside the rank badge.
_ROW_CHARA_ICON = 34
_RANK_CHARA_W = _RANK_W + 6 + _ROW_CHARA_ICON
_MUSIC_W = 176
_SCORE_W = 124
_BONUS_W = 112
_SKILL_W = 100
_POWER_W = 116
_COL_SEP = 12
_ROW_PAD = (12, 6)

# A card cell is wider than its thumbnail so the skill / bonus strips under it never clip digits.
_LIST_THUMB = 76
_LIST_CELL_W = 92
_LIST_CARD_SEP = 6
_HERO_THUMB = 100
_HERO_CELL_W = 112
_HERO_CARD_SEP = 10
_HERO_BAND_H = 40
_HERO_PAD = (18, 12)
_HERO_SEP = 22
_HERO_METRIC_W = 184
_HERO_STATS_W = 200

_SUPPORT_THUMB = 52
_SUPPORT_CELL_W = 60
_SUPPORT_SEP = 6

_METRIC_KEYS = ("score", "bonus", "skill", "power")
_TARGET_METRIC = {"score": "score", "bonus": "bonus", "skill": "skill", "total_power": "power"}
_METRIC_LABELS = {"bonus": "加成", "skill": "实效", "power": "综合力"}
_METRIC_WIDTHS = {"score": _SCORE_W, "bonus": _BONUS_W, "skill": _SKILL_W, "power": _POWER_W}


def _cards_w(thumb: int, sep: int, slots: int) -> int:
    return slots * thumb + max(0, slots - 1) * sep


def _support_columns(count: int) -> int:
    return (count + 1) // 2 if count > 12 else count


def _support_strip_w(count: int) -> int:
    columns = _support_columns(count)
    return columns * _SUPPORT_CELL_W + max(0, columns - 1) * _SUPPORT_SEP


@dataclass(frozen=True)
class _DeckLayout:
    music: bool
    metrics: tuple[str, ...]
    primary: str | None
    card_slots: int
    tile_w: int
    rank_w: int = _RANK_W

    @property
    def stats(self) -> tuple[str, ...]:
        return tuple(key for key in self.metrics if key != self.primary)

    def list_fixed_w(self) -> int:
        widths = [self.rank_w, *([_MUSIC_W] if self.music else []), *(_METRIC_WIDTHS[key] for key in self.metrics)]
        return sum(widths) + len(widths) * _COL_SEP + 2 * _ROW_PAD[0]

    def list_widths(self) -> dict[str, int]:
        """Column widths of a list row; spare tile width is shared by the value columns and the cards."""
        widths = {"rank": self.rank_w, **({"music": _MUSIC_W} if self.music else {})}
        widths.update((key, _METRIC_WIDTHS[key]) for key in self.metrics)
        cards = _cards_w(_LIST_CELL_W, _LIST_CARD_SEP, self.card_slots)
        extra = max(0, self.tile_w - self.list_fixed_w() - cards)
        share = extra // (len(self.metrics) + 1)
        for key in self.metrics:
            widths[key] += share
        widths["cards"] = cards + extra - share * len(self.metrics)
        return widths

    def list_cards_x(self) -> int:
        """Left edge of the cards column inside a list row (support strips line up with it)."""
        widths = self.list_widths()
        before = [widths[key] for key in ("rank", "music", "score") if key in widths]
        return _ROW_PAD[0] + sum(before) + len(before) * _COL_SEP

    def hero_fixed_w(self) -> int:
        blocks = [_HERO_METRIC_W, *([_HERO_STATS_W] if self.stats else [])]
        return sum(blocks) + len(blocks) * _HERO_SEP + 2 * _HERO_PAD[0]

    def hero_cards_w(self) -> int:
        return self.tile_w - self.hero_fixed_w()


def _deck_rows_carry_character(rqd: DeckRequest) -> bool:
    """All-character challenge results: one deck per character, each labelled with its character."""
    return any(deck.chara_icon_path or deck.chara_name for deck in rqd.deck_data)


def _deck_layout(rqd: DeckRequest) -> _DeckLayout:
    metrics = tuple(
        key
        for key, shown in zip(
            _METRIC_KEYS,
            (
                rqd.recommend_type not in {"bonus", "wl_bonus"},
                rqd.recommend_type not in {"challenge", "challenge_all", "no_event"},
                rqd.live_type in {"multi", "cheerful"},
                rqd.recommend_type not in {"bonus", "wl_bonus"},
            ),
        )
        if shown
    )
    target = _TARGET_METRIC.get(rqd.target or "")
    primary = target if target in metrics else (metrics[0] if metrics else None)
    slots = max([5, *(len(deck.card_data) for deck in rqd.deck_data)])
    rank_w = _RANK_CHARA_W if _deck_rows_carry_character(rqd) else _RANK_W
    probe = _DeckLayout(rqd.music_compare, metrics, primary, slots, 0, rank_w)
    tile_w = max(
        _MIN_TILE_W,
        probe.hero_fixed_w() + _cards_w(_HERO_CELL_W, _HERO_CARD_SEP, slots),
        probe.list_fixed_w() + _cards_w(_LIST_CELL_W, _LIST_CARD_SEP, slots) if len(rqd.deck_data) > 1 else 0,
        *(_support_strip_w(len(deck.support_card_data)) + 2 * _HERO_PAD[0] for deck in rqd.deck_data),
    )
    if rqd.event_planner:
        tile_w = max(tile_w, 300 + sum(w for _, w in _PLANNER_COLS) + _PLANNER_COL_SEP * len(_PLANNER_COLS) + 24)
    return _DeckLayout(rqd.music_compare, metrics, primary, slots, tile_w, rank_w)


def _deck_page_width(rqd: DeckRequest) -> int:
    return _deck_layout(rqd).tile_w + 2 * _PANEL_PAD


# ---------------------------------------------------------------------------
# Header panel
# ---------------------------------------------------------------------------

_BANNER_H = 64
_HEADER_ICON = 64
_PILL_ICON = 36
_TITLE_CHIP_STYLE = _CHIP_STYLE.replace(size=15)


def _header_banner_types(rqd: DeckRequest) -> bool:
    return rqd.recommend_type in {"event", "wl", "bonus", "wl_bonus", "mysekai"} and bool(rqd.event_id)


def _deck_subtitle(rqd: DeckRequest) -> str:
    if _header_banner_types(rqd) and rqd.event_name:
        return rqd.event_name
    if rqd.recommend_type in {"challenge", "challenge_all"} and rqd.chara_name:
        return rqd.chara_name
    if rqd.recommend_type == "challenge_all":
        return "全部角色 · 每个角色的最佳卡组"
    return ""


def _deck_title_chips(rqd: DeckRequest) -> list[tuple[str, Color]]:
    chips = []
    if live := _deck_live_label(rqd):
        chips.append((live, _deck_accent(rqd)))
    if rqd.is_max_deck:
        chips.append((f"{region_display(rqd.region, rqd.region_label)} 顶配", _RED))
    return chips


def _chip_w(style: TextStyle, text: str, pad_x: int = 8) -> int:
    return _text_w(style, text) + 2 * pad_x


def _deck_title_block_w(rqd: DeckRequest) -> int:
    title_w = _text_w(_TITLE_STYLE, _deck_base_title(rqd)) + 4
    title_w += sum(10 + _chip_w(_TITLE_CHIP_STYLE, text) for text, _ in _deck_title_chips(rqd))
    subtitle = _deck_subtitle(rqd)
    return max(title_w, _text_w(_SUBTITLE_STYLE, subtitle) + 4 if subtitle else 0)


def _draw_deck_header_visual(rqd: DeckRequest, assets: _DeckRecommendAssets, banner: ImageSource | None) -> int:
    """Event banner, the challenge character in a white well, or an accent bar; returns the width it took."""
    if banner is not None:
        width = round(banner.size[0] * _BANNER_H / max(1, banner.size[1]))
        ImageBox(banner, size=(width, _BANNER_H))
        return width
    if rqd.recommend_type in {"challenge", "challenge_all"} and assets.chara_icon is not None:
        with (
            Frame()
            .set_size((_HEADER_ICON, _HEADER_ICON))
            .set_content_align("c")
            .set_bg(RoundRectBg((255, 255, 255, 200), 14, blur_glass=False))
        ):
            ImageBox(assets.chara_icon, size=(_HEADER_ICON - 10, _HEADER_ICON - 10), image_size_mode="fit")
        return _HEADER_ICON
    Spacer(w=6, h=48).set_bg(RoundRectBg(_deck_accent(rqd), 3, blur_glass=False))
    return 6


def _draw_deck_title_block(rqd: DeckRequest, text_w: int) -> None:
    chips = _deck_title_chips(rqd)
    chips_w = sum(10 + _chip_w(_TITLE_CHIP_STYLE, text) for text, _ in chips)
    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(2).set_padding(0):
        with HSplit().set_content_align("l").set_item_align("c").set_sep(10).set_padding(0):
            title = _deck_base_title(rqd)
            title_w = min(_text_w(_TITLE_STYLE, title) + 4, text_w - chips_w)
            _ink(TextBox(title, _TITLE_STYLE, overflow="shrink").set_w(max(60, title_w)))
            for text, fill in chips:
                _chip(text, fill, style=_TITLE_CHIP_STYLE, radius=11, padding=(8, 3))
        if subtitle := _deck_subtitle(rqd):
            TextBox(subtitle, _SUBTITLE_STYLE, overflow="shrink").set_w(
                max(40, min(_text_w(_SUBTITLE_STYLE, subtitle) + 4, text_w))
            )


def _deck_shows_music(rqd: DeckRequest, assets: _DeckRecommendAssets) -> bool:
    if rqd.recommend_type in {"bonus", "wl_bonus", "mysekai"} or rqd.music_compare or rqd.event_planner:
        return False
    return bool(rqd.music_title or assets.music_cover)


def _diff_fill(diff: str | None) -> Color | None:
    fill = DIFF_COLORS.get((diff or "").lower())
    if fill is None:
        return None
    return fill if isinstance(fill, tuple) else fill.c1


def _draw_music_jacket(
    cover: ImageSource | None, diff: str | None, size: int, *, real_song: bool = True, omakase: bool = False
) -> None:
    """Jacket with the difficulty colour peeking out bottom-left, like the in-game song select.

    おまかせ has no jacket, only a small shuffle glyph: with a difficulty it becomes a square tile in that
    colour with the glyph whitened in its middle, sitting where a jacket would."""
    step = max(3, size // 12)
    fill = DIFF_COLORS.get((diff or "").lower()) if real_song else None
    if fill is None:
        # No difficulty tab to make room for: centre the cover in the same footprint so it lines up
        # with the text beside it instead of riding high.
        with Frame().set_size((size + step, size + step)).set_content_align("c"):
            if cover is not None:
                # Size the box to the image's own aspect (the おまかせ glyph is 48x39, so 36x29): a square
                # "fit" box pins a wide image to its top edge.
                src_w, src_h = cover.size
                scale = size / max(src_w, src_h, 1)
                ImageBox(cover, size=(round(src_w * scale), round(src_h * scale)), shadow=not real_song)
            else:
                Spacer(w=size, h=size).set_bg(FillBg((235, 242, 248, 255)))
        return
    with Frame().set_size((size + step, size + step)).set_content_align("lt"):
        if omakase:
            with Frame().set_size((size, size)).set_content_align("lt").set_bg(FillBg(fill)).set_offset((step, 0)):
                if cover is not None:
                    src_w, src_h = cover.size
                    scale = size * 0.68 / max(src_w, src_h, 1)
                    w, h = round(src_w * scale), round(src_h * scale)
                    # Centre on the glyph's ink, not its box: the arrowheads put the mass left of and above
                    # the box centre, so round x to the right and drop y by 8 % of the glyph's height.
                    offset = ((size - w + 1) // 2, (size - h) // 2 + round(h * 0.08))
                    ImageBox(cover, size=(w, h), tint=ImageTint(WHITE, "recolor")).set_offset(offset)
            return
        Spacer(w=size, h=size).set_bg(FillBg(fill)).set_offset((0, step))
        if cover is not None:
            ImageBox(cover, size=(size, size)).set_offset((step, 0))
        else:
            Spacer(w=size, h=size).set_bg(FillBg((235, 242, 248, 255))).set_offset((step, 0))


@dataclass(frozen=True)
class _HeaderPill:
    """A white header pill: its natural width and a drawer that fits it into a width budget."""

    width: int
    draw: Callable[[int], None]


def _music_pill(rqd: DeckRequest, assets: _DeckRecommendAssets) -> _HeaderPill:
    real_song = rqd.music_id is not None and rqd.music_id != OMAKASE_MUSIC_ID
    diff_fill = _diff_fill(rqd.music_diff) if real_song else None
    diff_text = (rqd.music_diff or "").upper()
    title = rqd.music_title or ""
    jacket_w = _PILL_ICON + max(3, _PILL_ICON // 12)
    chip_w = 8 + _chip_w(_CHIP_STYLE, diff_text) if diff_fill else 0
    fixed = 20 + jacket_w + 8 + chip_w

    def draw(max_w: int) -> None:
        with _pill():
            _draw_music_jacket(assets.music_cover, rqd.music_diff, _PILL_ICON, real_song=real_song)
            _ink(TextBox(title, _PILL_STYLE, overflow="shrink")).set_w(
                max(40, min(_text_w(_PILL_STYLE, title) + 4, max_w - fixed))
            )
            if diff_fill:
                _chip(diff_text, diff_fill)

    return _HeaderPill(fixed + _text_w(_PILL_STYLE, title) + 4, draw)


def _icon_pill(icons: list[tuple[ImageSource, tuple[int | None, int]]], text: str | None = None) -> _HeaderPill:
    widths = [size[0] or round(image.size[0] * size[1] / max(1, image.size[1])) for image, size in icons]
    width = 20 + sum(widths) + 8 * max(0, len(icons) - 1) + (8 + _text_w(_PILL_STYLE, text) + 4 if text else 0)

    def draw(_max_w: int) -> None:
        with _pill():
            for image, size in icons:
                ImageBox(image, size=size)
            if text:
                _ink(TextBox(text, _PILL_STYLE))

    return _HeaderPill(width, draw)


def _deck_header_pills(rqd: DeckRequest, assets: _DeckRecommendAssets) -> list[_HeaderPill]:
    """WL chapter, simulated unit + attribute, then the song (filters are drawn with the settings instead)."""
    pills = []
    if rqd.is_wl and rqd.wl_chara_name:
        icons = [(assets.wl_chara_icon, (_PILL_ICON, _PILL_ICON))] if assets.wl_chara_icon is not None else []
        pills.append(_icon_pill(icons, f"{rqd.wl_chara_name} 章节"))
    if assets.unit_logo and assets.attr_icon and not (rqd.unit_filter or rqd.attr_filter):
        pills.append(_icon_pill([(assets.unit_logo, (None, _PILL_ICON)), (assets.attr_icon, (30, 30))]))
    if _deck_shows_music(rqd, assets):
        pills.append(_music_pill(rqd, assets))
    return pills


def _draw_header_pills(pills: list[_HeaderPill], width: int) -> None:
    used = 0
    with HSplit().set_content_align("l").set_item_align("c").set_sep(10).set_padding(0):
        for index, pill in enumerate(pills):
            remaining = width - used - 10 * (len(pills) - 1 - index)
            pill.draw(remaining if index == len(pills) - 1 else pill.width)
            used += pill.width + 10


async def _draw_deck_title_row(rqd: DeckRequest, assets: _DeckRecommendAssets, width: int) -> None:
    """Banner + title block, with the context pills on the same row when they fit (else on the next)."""
    banner = None
    if _header_banner_types(rqd) and rqd.event_banner_path:
        banner = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.event_banner_path)
    pills = _deck_header_pills(rqd, assets)
    pills_w = sum(pill.width for pill in pills) + 10 * max(0, len(pills) - 1)
    visual_w = (
        round(banner.size[0] * _BANNER_H / max(1, banner.size[1]))
        if banner is not None
        else _HEADER_ICON
        if rqd.recommend_type in {"challenge", "challenge_all"} and assets.chara_icon is not None
        else 6
    )
    title_w = _deck_title_block_w(rqd)
    inline = bool(pills) and visual_w + 16 + title_w + 24 + pills_w <= width
    with HSplit().set_content_align("l").set_item_align("c").set_sep(16).set_padding(0) as row:
        _draw_deck_header_visual(rqd, assets, banner)
        _draw_deck_title_block(rqd, width - visual_w - 16 - (pills_w + 16 if inline else 0))
        if inline:
            gap = Spacer(w=0, h=1)
            _draw_header_pills(pills, pills_w)
    if inline:
        # The widths above are layout-font estimates; the text boxes can measure wider (a fallback font,
        # a glyph the estimate missed). Push the pills right by what is actually left, and drop them to
        # their own line when nothing is.
        built = sum(item._get_self_size()[0] for item in row.items if item is not gap)
        left = width - built - 16 * (len(row.items) - 1)
        if left >= 0:
            gap.set_w(left)
        else:
            row.set_items(row.items[:2])
            inline = False
    if pills and not inline:
        _draw_header_pills(pills, width)


def _deck_strategy_texts(rqd: DeckRequest) -> list[str]:
    if rqd.recommend_type in {"bonus", "wl_bonus", "mysekai"}:
        return []
    texts = (
        format_skill_order_text(rqd.skill_order_choose_strategy),
        format_skill_reference_text(rqd.skill_reference_choose_strategy),
    )
    return [text.replace(": ", " ") for text in texts if text]


async def _draw_deck_settings(rqd: DeckRequest, width: int) -> None:
    excluded_cards = rqd.excluded_cards or []
    strategy = _deck_strategy_texts(rqd)
    has_settings = any(
        (
            rqd.unit_filter,
            rqd.attr_filter,
            excluded_cards,
            rqd.multi_live_score_up_lower_bound,
            rqd.keep_after_training_state,
        )
    )
    if not has_settings and not strategy:
        return

    chip_fill: Color = (255, 255, 255, 185)
    chip_style = _CHIP_STYLE.replace(size=14, color=_TEXT)
    calc_style = chip_style.replace(font=DEFAULT_FONT)
    label_w = 64
    if has_settings:
        texts = []
        if rqd.multi_live_score_up_lower_bound:
            texts.append(f"实效≥{int(rqd.multi_live_score_up_lower_bound)}%")
        if rqd.keep_after_training_state:
            texts.append("禁用双技能自动切换")
        with HSplit().set_content_align("l").set_item_align("c").set_sep(8).set_padding(0):
            _ink(TextBox("卡组设置", _CAPTION_STYLE)).set_w(label_w)
            used = label_w + 8
            if rqd.unit_filter or rqd.attr_filter:
                with (
                    HSplit()
                    .set_content_align("l")
                    .set_item_align("c")
                    .set_sep(4)
                    .set_padding((8, 2))
                    .set_bg(RoundRectBg(chip_fill, 11, blur_glass=False))
                ):
                    _ink(TextBox("仅", chip_style))
                    if rqd.unit_filter:
                        unit_logo = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.unit_logo_path)
                        ImageBox(unit_logo, size=(None, 26))
                        used += 26 * unit_logo.size[0] // max(1, unit_logo.size[1]) + 4
                    if rqd.attr_filter:
                        attr_icon = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.attr_icon_path)
                        ImageBox(attr_icon, size=(22, 22))
                        used += 22 + 4
                    _ink(TextBox("上场", chip_style))
                used += 16 + _text_w(chip_style, "仅上场") + 8 + 8
            for text in texts:
                _chip(text, chip_fill, style=chip_style, radius=11)
                used += _text_w(chip_style, text) + 20 + 8
            if excluded_cards:
                text = f"排除 {', '.join(map(str, excluded_cards))}"
                if _text_w(chip_style, text) + 20 <= width - used:
                    _chip(text, chip_fill, style=chip_style, radius=11)
                else:  # a long exclusion list wraps inside the chip instead of widening the panel
                    TextBox(text, chip_style, use_real_line_count=True).set_w(max(80, width - used)).set_padding(
                        (8, 3)
                    ).set_bg(RoundRectBg(chip_fill, 11, blur_glass=False))
    if strategy:
        with HSplit().set_content_align("l").set_item_align("c").set_sep(8).set_padding(0):
            _ink(TextBox("计算设置", _CAPTION_STYLE)).set_w(label_w)
            for text in strategy:
                _chip(text, chip_fill, style=calc_style, radius=11)


def _draw_alert(text: str, color, width: int, *, size: int = 17) -> None:
    with (
        HSplit()
        .set_content_align("l")
        .set_item_align("c")
        .set_sep(10)
        .set_padding((12, 7))
        .set_bg(RoundRectBg(_alpha(color, 30), 10, blur_glass=False))
    ):
        Spacer(w=4, h=size + 2).set_bg(RoundRectBg(color, 2, blur_glass=False))
        style = TextStyle(font=DEFAULT_BOLD_FONT, size=size, color=color)
        if _text_w(style, text) + 8 <= width - 38:
            _ink(TextBox(text, style))
        else:
            TextBox(text, style, use_real_line_count=True).set_w(width - 38)


def _draw_deck_warnings(rqd: DeckRequest, width: int) -> None:
    if rqd.recommend_type in {"bonus", "wl_bonus"}:
        _draw_alert("友情提醒：控分前请核对加成和体力设置", _RED, width)
        if rqd.recommend_type == "wl_bonus" and not any(deck.support_card_data for deck in rqd.deck_data):
            _draw_alert("WL仅支持自动组主队，支援队请自行配置", _TEXT, width)
    if rqd.is_max_deck:
        _draw_alert("“顶配”为该服截止当前的全卡满养成配置(并非基于你的卡组计算)", _RED, width)


async def _draw_deck_header(rqd: DeckRequest, assets: _DeckRecommendAssets, width: int) -> None:
    inner = width - 2 * _PANEL_PAD
    with _panel(width):
        await _draw_deck_title_row(rqd, assets, inner)
        await _draw_deck_settings(rqd, inner)
        _draw_deck_warnings(rqd, inner)


# ---------------------------------------------------------------------------
# Result tiles: the best deck as a hero tile, the rest as aligned rows
# ---------------------------------------------------------------------------


def _deck_score(rqd: DeckRequest, deck, target_score: bool, boost_bonus: int) -> int:
    if rqd.recommend_type == "no_event":
        score = deck.live_score or 0
    elif rqd.recommend_type == "mysekai":
        score = deck.mysekai_event_point or 0
    else:
        score = deck.score or 0
    if rqd.boost is not None and target_score:
        score = int(score * boost_bonus)
    return score


def _boost_bonus(rqd: DeckRequest) -> int:
    return BOOST_BONUS_DICT.get(rqd.boost or 0, 1) if rqd.boost is not None else 1


def _metric_label(rqd: DeckRequest, key: str) -> str:
    return _deck_score_name(rqd) if key == "score" else _METRIC_LABELS[key]


def _metric_value(rqd: DeckRequest, deck, key: str) -> str:
    if key == "score":
        return format_deck_int(_deck_score(rqd, deck, rqd.target == "score", _boost_bonus(rqd)))
    if key == "bonus":
        total = (deck.event_bonus_rate or 0) + ((deck.support_deck_bonus_rate or 0) if rqd.is_wl else 0)
        return format_deck_percent(total)
    if key == "skill":
        return format_deck_percent(deck.multi_live_score_up, digits=1)
    return format_deck_int(deck.total_power)


def _metric_sub(rqd: DeckRequest, deck, key: str) -> str:
    """WL bonus split into main + support; nothing for the other metrics."""
    if key == "bonus" and rqd.is_wl:
        main = format_deck_percent(deck.event_bonus_rate or 0)[:-1]
        return f"{main} + {format_deck_percent(deck.support_deck_bonus_rate or 0)[:-1]}"
    return ""


def _challenge_delta(rqd: DeckRequest, deck) -> tuple[str, Color] | None:
    if rqd.recommend_type not in {"challenge", "challenge_all"}:
        return None
    delta = deck.challenge_score_delta or 0
    return f"{delta:+,d}", (_GREEN if delta > 0 else _RED)


def _deck_card_is_fixed(rqd: DeckRequest, card_id: int, character_id: int) -> bool:
    return bool(
        (rqd.fixed_cards_id and card_id in rqd.fixed_cards_id)
        or (rqd.fixed_characters_id and character_id in rqd.fixed_characters_id)
    )


def _story_read_color(value: bool | None) -> tuple[int, int, int, int]:
    if value is None:
        return (255, 255, 255, 255)
    return (50, 150, 50, 255) if value else (150, 50, 50, 255)


def _story_chip(text: str, value: bool | None, size: int) -> None:
    color = _FAINT if value is None else _story_read_color(value)
    _soft_chip(text, color, size=size, radius=4, padding=(2, 0), alpha=40)


def _format_event_bonus(event_bonus: float) -> str:
    return f"+{event_bonus:.1f}%" if int(event_bonus) != event_bonus else f"+{int(event_bonus)}%"


def _draw_card_strip(fill, width: int, height: int) -> HSplit:
    return (
        HSplit()
        .set_size((width, height))
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(0)
        .set_padding((4, 0))
        .set_bg(RoundRectBg(fill, 5, blur_glass=False))
    )


def _draw_deck_card(
    rqd: DeckRequest, assets: _DeckRecommendAssets, card, *, thumb: int, cell_w: int, strip_fill
) -> None:
    """Thumbnail with its ID tag, then two strips: skill level | skill value, event bonus | story reads."""
    card_id = card.card_thumbnail.card_id
    card_key = (card_id, card.card_thumbnail.is_after_training, legacy_key(card.card_thumbnail.card_thumbnail_path))
    scale = thumb / 80
    info_size = 11 if thumb < 90 else 13
    strip_h = info_size + 6
    inner = cell_w - 8
    event_bonus = card.event_bonus_rate
    with VSplit().set_w(cell_w).set_content_align("c").set_item_align("c").set_sep(3).set_padding(0):
        with Frame().set_size((thumb, thumb)).set_content_align("rt"):
            CardFullThumbnailBox(assets.card_layers[card_key], size=(None, thumb))
            fixed = _deck_card_is_fixed(rqd, card_id, card.chara_id)
            id_style = TextStyle(font=DEFAULT_FONT, size=10 if thumb < 90 else 11, color=WHITE if fixed else _TEXT)
            _id_badge(str(card_id), (214, 64, 84, 230) if fixed else (255, 255, 255, 215), id_style).set_offset((-2, 2))
            if card.has_canvas_bonus:
                icon = round(11 * scale)
                ImageBox(assets.canvas_thumbnail, size=(icon, icon)).set_offset((round(-32 * scale), round(65 * scale)))

        level_text = f"SLv.{card.skill_level}"
        rate_text = f"↑{format_skill_rate(card.skill_rate)}%"
        level_style = TextStyle(font=DEFAULT_FONT, size=info_size, color=_DIM)
        rate_style = TextStyle(font=DEFAULT_BOLD_FONT, size=info_size, color=_INK)
        # Both halves step down together so neither the level nor the skill value loses characters.
        while level_style.size > 8 and (_text_w(level_style, level_text) + _text_w(rate_style, rate_text) + 8 > inner):
            level_style = level_style.replace(size=level_style.size - 1)
            rate_style = rate_style.replace(size=rate_style.size - 1)
        level_w = _text_w(level_style, level_text) + 4
        with _draw_card_strip(strip_fill, cell_w, strip_h):
            _ink(TextBox(level_text, level_style)).set_w(level_w).set_content_align("l")
            _fitted(rate_text, rate_style, inner - level_w, align="r", min_size=8)

        story_size = info_size - 1
        with _draw_card_strip(strip_fill, cell_w, strip_h):
            if event_bonus > 0:
                story_w = 2 * (_text_w(TextStyle(font=DEFAULT_BOLD_FONT, size=story_size), "前") + 4) + 2
                _fitted(
                    _format_event_bonus(event_bonus),
                    TextStyle(font=DEFAULT_BOLD_FONT, size=info_size, color=_BONUS_ORANGE),
                    inner - story_w,
                    align="l",
                    min_size=9,
                )
                with HSplit().set_content_align("r").set_item_align("c").set_sep(2).set_padding(0):
                    _story_chip("前", card.is_before_story, story_size)
                    _story_chip("后", card.is_after_story, story_size)
            else:
                with HSplit().set_content_align("c").set_item_align("c").set_sep(6).set_padding(0):
                    _story_chip("前篇", card.is_before_story, story_size)
                    _story_chip("后篇", card.is_after_story, story_size)


def _draw_deck_cards(rqd: DeckRequest, assets: _DeckRecommendAssets, deck, *, width: int, hero: bool) -> None:
    thumb, cell_w, sep = (
        (_HERO_THUMB, _HERO_CELL_W, _HERO_CARD_SEP) if hero else (_LIST_THUMB, _LIST_CELL_W, _LIST_CARD_SEP)
    )
    strip_fill = (240, 241, 247, 255) if hero else (255, 255, 255, 170)
    with Frame().set_w(width).set_content_align("c"):
        with HSplit().set_content_align("c").set_item_align("t").set_sep(sep).set_padding(0):
            for card in deck.card_data:
                _draw_deck_card(rqd, assets, card, thumb=thumb, cell_w=cell_w, strip_fill=strip_fill)


def _draw_deck_support_strip(deck, assets: _DeckRecommendAssets, x: int, fill) -> None:
    """WL support cards under the main five, each with its own bonus; wraps into two rows past 12."""
    cards = deck.support_card_data
    if not cards:
        return
    columns = _support_columns(len(cards))
    with HSplit().set_content_align("l").set_item_align("c").set_sep(0).set_padding(0):
        if x > 0:
            Spacer(w=x, h=1)
        with (
            VSplit()
            .set_content_align("lt")
            .set_item_align("lt")
            .set_sep(4)
            .set_padding((6, 5))
            .set_bg(RoundRectBg(fill, 10, blur_glass=False))
        ):
            for start in range(0, len(cards), columns):
                with HSplit().set_content_align("l").set_item_align("t").set_sep(_SUPPORT_SEP).set_padding(0):
                    for card in cards[start : start + columns]:
                        thumb = card.card_thumbnail
                        key = (thumb.card_id, thumb.is_after_training, thumb.card_thumbnail_path)
                        with VSplit().set_content_align("c").set_item_align("c").set_sep(1).set_w(_SUPPORT_CELL_W):
                            CardFullThumbnailBox(assets.card_layers[key], size=(_SUPPORT_THUMB, _SUPPORT_THUMB))
                            _fitted(
                                f"{card.event_bonus_rate:g}%",
                                TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=(55, 100, 140, 255)),
                                _SUPPORT_CELL_W,
                            )


def _draw_compare_music(deck, assets: _DeckRecommendAssets, width: int, *, compact: bool) -> None:
    music_img = assets.compare_music_imgs.get(legacy_key(deck.music_cover_path)) if deck.music_cover_path else None
    title = deck.music_title or (f"Music {deck.music_id}" if deck.music_id is not None else "")
    # おまかせ's 10000 is an internal id, not a song number; show only its difficulty.
    shown_id = deck.music_id if deck.music_id not in (None, OMAKASE_MUSIC_ID) else None
    meta = " · ".join(
        part for part in (str(shown_id) if shown_id is not None else "", (deck.music_diff or "").upper()) if part
    )
    jacket = 48 if compact else 40
    text_w = width - jacket - jacket // 12 - 8
    with HSplit().set_w(width).set_content_align("l").set_item_align("c").set_sep(8).set_padding(0):
        _draw_music_jacket(music_img, deck.music_diff, jacket, omakase=deck.music_id == OMAKASE_MUSIC_ID)
        with VSplit().set_w(text_w).set_content_align("lt").set_item_align("lt").set_sep(1).set_padding(0):
            TextBox(
                title,
                TextStyle(font=DEFAULT_BOLD_FONT, size=13 if compact else 14, color=_INK),
                line_count=2 if compact else 1,
                use_real_line_count=True,
                overflow="shrink",
            ).set_w(text_w)
            if meta:
                TextBox(meta, TextStyle(font=DEFAULT_FONT, size=11, color=_DIM), overflow="shrink").set_w(text_w)
            if deck.music_query:
                TextBox(deck.music_query, TextStyle(font=DEFAULT_FONT, size=11, color=_FAINT), overflow="shrink").set_w(
                    text_w
                )


def _algorithm_chip(algorithm: str | None, width: int, *, size: int = 11, padding=(5, 1), radius: int = 6) -> None:
    """One small chip per algorithm that produced the deck ("DGA+RL" draws DGA and RL side by side)."""
    labels = [label for label in format_algorithm_label(algorithm).split("+") if label]
    if not labels:
        return
    sep = 4
    # Each chip adds its horizontal padding twice; shrink the shared font until the whole row fits.
    budget = width - (2 * padding[0] + sep) * len(labels) + sep
    style = _fit_style(" ".join(labels), TextStyle(font=DEFAULT_BOLD_FONT, size=size, color=_DIM), budget, min_size=8)
    with HSplit().set_content_align("c").set_item_align("c").set_sep(sep).set_padding(0):
        for label in labels:
            _chip(label, _alpha(_GREY, 36), style=style, radius=radius, padding=padding)


def _hero_band(rqd: DeckRequest, width: int, rank_label: str, chara_icon: ImageSource | None = None) -> None:
    """A flat tinted strip on top of the hero tile: rank badge + label in the recommendation accent."""
    accent = _deck_accent(rqd)
    with (
        HSplit()
        .set_size((width, _HERO_BAND_H))
        .set_content_align("l")
        .set_item_align("c")
        .set_sep(10)
        .set_padding((14, 0))
        .set_bg(
            RoundRectBg(_mix(accent, WHITE, 0.8), _TILE_RADIUS, corners=(True, True, False, False), blur_glass=False)
        )
    ):
        _circle_badge("1", accent, 26, TextStyle(font=DEFAULT_HEAVY_FONT, size=15, color=WHITE))
        if chara_icon is not None:
            ImageBox(chara_icon, size=(30, 30), image_size_mode="fit")
        _ink(TextBox(rank_label, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=_mix(accent, _INK, 0.35))))


def _draw_hero_metric(
    rqd: DeckRequest, assets: _DeckRecommendAssets, deck, layout: _DeckLayout, algorithm: str | None
) -> None:
    accent = _deck_accent(rqd)
    key = layout.primary
    with VSplit().set_w(_HERO_METRIC_W).set_content_align("lt").set_item_align("lt").set_sep(2).set_padding(0):
        if key is not None:
            label = _metric_label(rqd, key)
            TextBox(label, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=_DIM))
            _fitted(
                _metric_value(rqd, deck, key),
                TextStyle(font=DEFAULT_HEAVY_FONT, size=46, color=_mix(accent, _INK, 0.12)),
                _HERO_METRIC_W,
                align="l",
                min_size=20,
            )
            if sub := _metric_sub(rqd, deck, key):
                TextBox(sub, TextStyle(font=DEFAULT_FONT, size=14, color=_DIM))
        with HSplit().set_content_align("l").set_item_align("c").set_sep(6).set_padding(0):
            if delta := _challenge_delta(rqd, deck):
                # Same size, padding and radius as the algorithm chips beside it, so the pair reads as one row.
                _soft_chip(delta[0], delta[1], size=13, radius=8, padding=(6, 2), alpha=36)
            if "score" in layout.metrics:
                _algorithm_chip(algorithm, _HERO_METRIC_W // 2, size=13, padding=(6, 2), radius=8)
        if layout.music:
            Spacer(h=4)
            _draw_compare_music(deck, assets, _HERO_METRIC_W, compact=False)


def _draw_hero_stats(rqd: DeckRequest, deck, layout: _DeckLayout) -> None:
    if not layout.stats:
        return
    label_w = 64
    value_w = _HERO_STATS_W - 2 * 12 - label_w
    with VSplit().set_w(_HERO_STATS_W).set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(0):
        for key in layout.stats:
            sub = _metric_sub(rqd, deck, key)
            with (
                HSplit()
                .set_w(_HERO_STATS_W)
                .set_content_align("l")
                .set_item_align("c")
                .set_sep(0)
                .set_padding((12, 5))
                .set_bg(RoundRectBg((240, 241, 247, 255), 9, blur_glass=False))
            ):
                _ink(TextBox(_metric_label(rqd, key), _COL_LABEL_STYLE)).set_w(label_w).set_content_align("l")
                with VSplit().set_w(value_w).set_content_align("r").set_item_align("r").set_sep(0).set_padding(0):
                    _fitted(
                        _metric_value(rqd, deck, key),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=_INK),
                        value_w,
                        align="r",
                        min_size=14,
                    )
                    if sub:
                        _fitted(sub, TextStyle(font=DEFAULT_FONT, size=13, color=_DIM), value_w, align="r")


def _draw_hero_tile(
    rqd: DeckRequest, assets: _DeckRecommendAssets, layout: _DeckLayout, deck, algorithm: str | None
) -> None:
    width = layout.tile_w
    body_fill = (255, 255, 255, 205)
    with VSplit().set_w(width).set_content_align("lt").set_item_align("lt").set_sep(0).set_padding(0):
        label = f"最佳卡组 · {deck.chara_name}" if deck.chara_name else "最佳卡组"
        _hero_band(rqd, width, label, _deck_chara_icon(assets, deck))
        with (
            VSplit()
            .set_w(width)
            .set_content_align("lt")
            .set_item_align("lt")
            .set_sep(10)
            .set_padding(_HERO_PAD)
            .set_bg(RoundRectBg(body_fill, _TILE_RADIUS, corners=(False, False, True, True), blur_glass=False))
        ):
            with HSplit().set_content_align("l").set_item_align("c").set_sep(_HERO_SEP).set_padding(0):
                _draw_hero_metric(rqd, assets, deck, layout, algorithm)
                _draw_deck_cards(rqd, assets, deck, width=layout.hero_cards_w(), hero=True)
                _draw_hero_stats(rqd, deck, layout)
            if deck.support_card_data:
                inner = width - 2 * _HERO_PAD[0]
                x = min(_HERO_METRIC_W + _HERO_SEP, inner - _support_strip_w(len(deck.support_card_data)) - 12)
                _draw_deck_support_strip(deck, assets, max(0, x), (240, 241, 247, 255))


def _draw_list_header(rqd: DeckRequest, layout: _DeckLayout) -> None:
    accent = _deck_accent(rqd)
    widths = layout.list_widths()
    with HSplit().set_content_align("l").set_item_align("c").set_sep(_COL_SEP).set_padding((_ROW_PAD[0], 0)):
        TextBox("#", _COL_LABEL_STYLE).set_w(layout.rank_w).set_content_align("c")
        if layout.music:
            TextBox("歌曲", _COL_LABEL_STYLE).set_w(_MUSIC_W).set_content_align("c")

        def label(key: str) -> None:
            if key == layout.primary:
                TextBox(f"{_metric_label(rqd, key)} ▼", _COL_LABEL_STYLE.replace(color=accent)).set_w(
                    widths[key]
                ).set_content_align("c")
            else:
                TextBox(_metric_label(rqd, key), _COL_LABEL_STYLE).set_w(widths[key]).set_content_align("c")

        if "score" in layout.metrics:
            label("score")
        TextBox("卡组", _COL_LABEL_STYLE).set_w(widths["cards"]).set_content_align("c")
        for key in layout.metrics:
            if key != "score":
                label(key)


def _draw_list_value(rqd: DeckRequest, deck, key: str, layout: _DeckLayout, algorithm: str | None) -> None:
    width = layout.list_widths()[key]
    color = _mix(_deck_accent(rqd), _INK, 0.12) if key == layout.primary else _INK
    with VSplit().set_w(width).set_content_align("c").set_item_align("c").set_sep(3).set_padding(0):
        _fitted(
            _metric_value(rqd, deck, key), _VALUE_STYLE.replace(color=color, size=24 if key == "score" else 22), width
        )
        if sub := _metric_sub(rqd, deck, key):
            _fitted(sub, TextStyle(font=DEFAULT_FONT, size=12, color=_DIM), width)
        if key == "score":
            if delta := _challenge_delta(rqd, deck):
                _fitted(delta[0], TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=delta[1]), width)
            _algorithm_chip(algorithm, width)


def _deck_chara_icon(assets: _DeckRecommendAssets, deck) -> ImageSource | None:
    return assets.deck_chara_icons.get(legacy_key(deck.chara_icon_path)) if deck.chara_icon_path else None


def _rank_fill(rqd: DeckRequest, rank: int) -> Color:
    accent = _deck_accent(rqd)
    return _mix(accent, WHITE, 0.25) if rank <= 3 else _alpha(_GREY, 200)


def _draw_list_row(
    rqd: DeckRequest, assets: _DeckRecommendAssets, layout: _DeckLayout, deck, algorithm: str | None, rank: int
) -> None:
    with (
        VSplit()
        .set_w(layout.tile_w)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(6)
        .set_padding(_ROW_PAD)
        .set_bg(RoundRectBg((255, 255, 255, 135), _TILE_RADIUS, blur_glass=False))
    ):
        with HSplit().set_content_align("l").set_item_align("c").set_sep(_COL_SEP).set_padding(0):
            with HSplit().set_w(layout.rank_w).set_content_align("c").set_item_align("c").set_sep(6).set_padding(0):
                _circle_badge(
                    str(rank), _rank_fill(rqd, rank), 30, TextStyle(font=DEFAULT_HEAVY_FONT, size=16, color=WHITE)
                )
                if layout.rank_w != _RANK_W:
                    chara_icon = _deck_chara_icon(assets, deck)
                    if chara_icon is not None:
                        ImageBox(chara_icon, size=(_ROW_CHARA_ICON, _ROW_CHARA_ICON), image_size_mode="fit")
                    else:
                        Spacer(w=_ROW_CHARA_ICON, h=_ROW_CHARA_ICON)
            if layout.music:
                _draw_compare_music(deck, assets, _MUSIC_W, compact=True)
            if "score" in layout.metrics:
                _draw_list_value(rqd, deck, "score", layout, algorithm)
            _draw_deck_cards(rqd, assets, deck, width=layout.list_widths()["cards"], hero=False)
            for key in layout.metrics:
                if key != "score":
                    _draw_list_value(rqd, deck, key, layout, algorithm)
        if deck.support_card_data:
            inner = layout.tile_w - 2 * _ROW_PAD[0]
            x = min(layout.list_cards_x() - _ROW_PAD[0], inner - _support_strip_w(len(deck.support_card_data)) - 12)
            _draw_deck_support_strip(deck, assets, max(0, x), (255, 255, 255, 150))


def _deck_results_captions(rqd: DeckRequest) -> list[str]:
    parts = []
    if rqd.multi_live_teammate_score_up is not None:
        parts.append(f"实效 {format_deck_percent(rqd.multi_live_teammate_score_up, digits=1)}")
    if rqd.multi_live_teammate_power is not None:
        parts.append(f"综合力 {format_deck_int(rqd.multi_live_teammate_power)}")
    return [f"队友 {' · '.join(parts)}"] if parts else []


def _draw_deck_results(rqd: DeckRequest, assets: _DeckRecommendAssets, layout: _DeckLayout, width: int) -> None:
    accent = _deck_accent(rqd)
    with _panel(width):
        chips = [(f"{len(rqd.deck_data)} 组", accent)] if rqd.deck_data else []
        boost = []
        if rqd.boost is not None and rqd.target == "score":
            boost.append((f"{rqd.boost}🔥 PT×{_boost_bonus(rqd)}", _BONUS_ORANGE))
        _section_header("推荐卡组", accent, chips=chips, soft_chips=boost, captions=_deck_results_captions(rqd))
        if not rqd.deck_data:
            TextBox("未找到符合条件的卡组", TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=_RED))
            return
        algorithms = list(rqd.model_name or [])
        algorithms += [""] * (len(rqd.deck_data) - len(algorithms))
        _draw_hero_tile(rqd, assets, layout, rqd.deck_data[0], algorithms[0])
        if len(rqd.deck_data) > 1:
            Spacer(h=2)
            _draw_list_header(rqd, layout)
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(0):
                for rank, (deck, algorithm) in enumerate(zip(rqd.deck_data[1:], algorithms[1:]), start=2):
                    _draw_list_row(rqd, assets, layout, deck, algorithm, rank)


# ---------------------------------------------------------------------------
# Notes and page
# ---------------------------------------------------------------------------


def _draw_runtime_rows(rqd: DeckRequest, width: int) -> None:
    if not rqd.cost_times:
        return
    wait_times = rqd.wait_times or {}
    chip_style = _CHIP_STYLE.replace(size=12)
    items = []
    for alg, cost in rqd.cost_times.items():
        name = RECOMMEND_ALG_NAMES.get(alg, alg)
        detail = f"耗时 {cost:.2f}s · 等待 {wait_times.get(alg, 0.0):.2f}s"
        items.append((name, detail, _text_w(chip_style, name) + 22 + 6 + _text_w(_NOTE_STYLE, detail) + 4 + 16))
    label = "本次组卡使用算法"
    label_w = _text_w(_NOTE_STYLE, label) + 4 + 10
    rows: list[list[tuple[str, str, int]]] = [[]]
    used = label_w
    for item in items:
        if rows[-1] and used + item[2] > width:
            rows.append([])
            used = label_w
        rows[-1].append(item)
        used += item[2]
    for index, row in enumerate(rows):
        with HSplit().set_content_align("l").set_item_align("c").set_sep(6).set_padding(0):
            _ink(TextBox(label if index == 0 else "", _NOTE_STYLE)).set_w(label_w - 6)
            for name, detail, _ in row:
                _chip(name, _alpha(_GREY, 210), style=chip_style, radius=8, padding=(8, 4))
                _ink(TextBox(detail, _NOTE_STYLE))
                Spacer(w=4, h=1)


def _draw_deck_notes(rqd: DeckRequest, width: int | None = None) -> None:
    width = width or _deck_page_width(rqd)
    inner = width - 2 * _PANEL_PAD
    with _panel(width).set_sep(4):
        if rqd.auto_score_notice:
            _draw_alert(rqd.auto_score_notice, _AMBER, inner, size=16)
            Spacer(h=2)
        if rqd.recommend_type not in {"bonus", "wl_bonus"}:
            TextBox(
                "12星卡默认全满，34星及生日卡默认满级，oc的bfes花前技能活动组卡为平均值，挑战组卡为最大值",
                _NOTE_STYLE,
                use_real_line_count=True,
            ).set_w(inner)
        TextBox(
            "功能移植并修改自33Kit https://3-3.dev/sekai/deck-recommend 算错概不负责",
            _NOTE_STYLE,
            use_real_line_count=True,
        ).set_w(inner)
        _draw_runtime_rows(rqd, inner)


async def _build_deck_recommend_canvas(rqd: DeckRequest) -> Canvas:
    assets = await _load_deck_recommend_assets(rqd)
    layout = _deck_layout(rqd)
    width = layout.tile_w + 2 * _PANEL_PAD
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(14).set_padding(16):
            if not rqd.is_max_deck:
                await get_profile_card(rqd.profile.to_profile_card_request())
            await _draw_deck_header(rqd, assets, width)
            _draw_deck_results(rqd, assets, layout, width)
            if rqd.event_planner:
                draw_event_planner_block(rqd.event_planner, assets.planner_music_imgs, width)
            _draw_deck_notes(rqd, width)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_deck_recommend_image(rqd: DeckRequest) -> Image.Image:
    """合成组队推荐图片 (Pillow 路径)。"""
    return await (await _build_deck_recommend_canvas(rqd)).get_img()


async def try_render_deck_recommend_payload(
    rqd: DeckRequest, *, endpoint: str = "deck_recommend"
) -> EncodedImagePayload | None:
    """Skia 路径：经 IRPainter 渲染同一棵 widget 树；不可用时返回 None 回退 Pillow。

    ``endpoint`` names the caller for /render-stats. It defaults to the deck route, which renders
    inside a heavy worker process (see ``heavy_render_pool``) — those child-process counters are
    replayed in the parent from ``payload.backend``. The event planner delegates to this same
    canvas but renders in-process, so it must pass its own name; otherwise its renders would be
    counted as ``deck_recommend`` in the parent's /render-stats.
    """
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_deck_recommend_canvas(rqd), endpoint=endpoint)
