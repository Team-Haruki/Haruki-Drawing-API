import asyncio
from dataclasses import dataclass
import logging
import time

from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    DIFF_COLORS,
    PLAY_RESULT_COLORS,
    SEKAI_BLUE_BG,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import (
    BLACK,
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    DEFAULT_HEAVY_FONT,
    WHITE,
    LinearGradient,
    get_font,
    get_font_desc,
    get_text_size,
    lerp_color,
)
from src.sekai.base.plot import (
    Canvas,
    FillBg,
    Flow,
    Frame,
    Grid,
    HSplit,
    ImageBox,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
)
from src.sekai.base.timezone import datetime_from_millis
from src.sekai.base.utils import ImageSource, get_asset_image_ref, get_str_display_length
from src.sekai.profile.drawer import get_profile_card
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, RESULT_ASSET_PATH

# =========================== 从.model导入常量和数据类型 =========================== #
from .model import (
    BasicMusicRewardsRequest,
    CustomChartInfo,
    DetailMusicRewardsRequest,
    MusicBriefList,
    MusicBriefListRequest,
    MusicDetailRequest,
    MusicListRequest,
    PlayProgressRequest,
)

logger = logging.getLogger(__name__)

# =========================== 绘图助手 =========================== #


def _draw_rqd_title(rqd):
    """从 rqd 中读取并绘制附加标题"""
    if not rqd.title:
        return

    style = rqd.title_style
    if style is None:
        style = TextStyle(DEFAULT_BOLD_FONT, 28, BLACK)
    elif isinstance(style, dict):
        style = TextStyle(**style)

    if rqd.title_shadow:
        TextBox(
            rqd.title,
            TextStyle(style.font, style.size, style.color, use_shadow=True, shadow_offset=2),
        ).set_padding(16).set_omit_parent_bg(True).set_bg(roundrect_bg(alpha=80))
    else:
        TextBox(rqd.title, style).set_padding(16).set_omit_parent_bg(True).set_bg(roundrect_bg(alpha=80))


def _music_list_group_order(diff: str, level: int) -> tuple[int, int]:
    order = {
        "easy": 1,
        "normal": 2,
        "hard": 3,
        "expert": 4,
        "master": 5,
        "append": 6,
    }
    return (order.get(diff, 99), level)


# =========================== 绘图函数 =========================== #


def _iter_vocal_entries(vocal_info):
    """Normalize music vocal payloads to a list of vocal entries."""
    if not isinstance(vocal_info, dict) or not vocal_info:
        return []

    if "caption" in vocal_info and "characters" in vocal_info:
        return [vocal_info]

    entries = []
    for item in vocal_info.values():
        if isinstance(item, dict) and "caption" in item and "characters" in item:
            entries.append(item)
    return entries


def _build_vocal_group(characters, vocal_logos):
    vocal_group = {"chara_imgs": [], "vocal_names": []}
    for chara_data in characters:
        if not isinstance(chara_data, dict):
            continue
        chara_name = chara_data.get("characterName")
        if not chara_name:
            continue
        target = "chara_imgs" if chara_name in vocal_logos else "vocal_names"
        vocal_group[target].append(vocal_logos.get(chara_name, chara_name))
    return vocal_group


def _build_caption_vocals(vocal_info, vocal_logos):
    """Group vocal entries by caption, matching lunabot's compose logic."""
    caption_vocals = {}
    for item in _iter_vocal_entries(vocal_info):
        caption = str(item.get("caption", "Vocal")).replace("ver.", "").strip() or "Vocal"
        vocal_group = _build_vocal_group(item.get("characters", []), vocal_logos)
        if vocal_group["chara_imgs"] or vocal_group["vocal_names"]:
            caption_vocals.setdefault(caption, []).append(vocal_group)

    return caption_vocals


def _draw_vocal_name_chip(vocal_name: str, max_text_width: int):
    display_len = max(1, get_str_display_length(vocal_name))
    font_size = max(16, int(24 * min(1.0, 50 / display_len)))
    style = TextStyle(font=DEFAULT_FONT, size=font_size, color=(70, 70, 70))
    text_width, _ = get_text_size(get_font(DEFAULT_FONT, font_size), vocal_name)

    with (
        HSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(2)
        .set_padding(4)
        .set_bg(roundrect_bg(fill=(255, 255, 255, 75), radius=8))
    ):
        textbox = TextBox(vocal_name, style, line_count=2, overflow="shrink", use_real_line_count=True)
        if text_width > max_text_width:
            textbox.set_w(max_text_width)


def _draw_vocal_image_chip(chara_imgs: list[ImageSource]):
    with (
        HSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(2)
        .set_padding(4)
        .set_bg(roundrect_bg(fill=(255, 255, 255, 75), radius=8))
    ):
        for img in chara_imgs:
            ImageBox(img, size=(32, 32), use_alpha_blend=True)


def _build_music_detail_leaderboard_cell(
    width: int,
    height: int,
    bg_color,
    rank_text: str,
    value_text: str | None,
    rank_color,
    *,
    padding: int = 8,
    gap: int = 4,
    rank_box_w: int = 42,
):
    rank_font_size = 18
    value_font_size = 12
    rank_font = get_font(DEFAULT_BOLD_FONT, rank_font_size)
    rank_font_desc = get_font_desc(DEFAULT_BOLD_FONT, rank_font_size)
    value_font_desc = get_font_desc(DEFAULT_FONT, value_font_size)
    text_color = (50, 50, 50)
    rank_area_x = padding
    rank_area_w = rank_box_w
    value_area_x = padding + rank_box_w + gap

    def _draw_text(_, p):
        if value_text:
            rank_w, _ = get_text_size(rank_font, rank_text)
            p.text(
                rank_text,
                (rank_area_x + max(0, (rank_area_w - rank_w) // 2), (height - rank_font_size) // 2),
                font=rank_font_desc,
                fill=rank_color,
            )
            p.text(
                value_text,
                (value_area_x, (height - value_font_size) // 2),
                font=value_font_desc,
                fill=text_color,
            )
        else:
            rank_w, _ = get_text_size(rank_font, rank_text)
            p.text(
                rank_text,
                ((width - rank_w) // 2, (height - rank_font_size) // 2),
                font=rank_font_desc,
                fill=rank_color,
            )

    return Frame().set_bg(FillBg(bg_color)).set_size((width, height)).add_draw_func(_draw_text)


def _ordered_music_detail_leaderboard_keys(
    label_map: dict[str, str] | None, preferred_order: tuple[str, ...]
) -> list[str]:
    if not label_map:
        return []
    ordered = [key for key in preferred_order if key in label_map]
    ordered.extend(key for key in label_map if key not in preferred_order)
    return ordered


def _custom_chart_stat_text(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    text = str(value).strip()
    return text or "-"


def _draw_custom_chart_info(rqd: MusicDetailRequest, width: int, height: int):
    info = rqd.custom_chart_info
    if not info:
        return

    fc_rate = "-"
    if info.full_combo_rate is not None:
        rate = info.full_combo_rate * 100 if info.full_combo_rate <= 1 else info.full_combo_rate
        fc_rate = f"{rate:.2f}%"

    rows = [("游玩数", info.play_count), ("评价数", info.review_count), ("FC率", fc_rate)]
    card_w = (width - 24 * 2 - 12 * 2) // 3
    with VSplit().set_padding(16).set_content_align("c").set_item_align("c").set_w(width).set_h(height):
        with HSplit().set_sep(12).set_content_align("c").set_item_align("c"):
            for label, value in rows:
                with VSplit().set_sep(6).set_content_align("c").set_item_align("c").set_w(card_w):
                    TextBox(label, TextStyle(DEFAULT_HEAVY_FONT, 24, (55, 55, 55))).set_content_align("c")
                    TextBox(
                        _custom_chart_stat_text(value),
                        TextStyle(DEFAULT_BOLD_FONT, 30, (70, 60, 80)),
                        line_count=1,
                        overflow="shrink",
                    ).set_w(card_w).set_content_align("c")


def _draw_custom_chart_difficulty(rqd: MusicDetailRequest, width: int, height: int):
    info = rqd.custom_chart_info
    if not info:
        return

    diff = (info.difficulty or "master").lower()
    diff_color = DIFF_COLORS.get(diff, (80, 80, 80))
    level = _custom_chart_stat_text(info.play_level)
    note_count = _custom_chart_stat_text(info.note_count)
    with HSplit().set_padding(16).set_sep(14).set_content_align("c").set_item_align("c").set_w(width).set_h(height):
        TextBox(level, TextStyle(DEFAULT_BOLD_FONT, 34, WHITE)).set_bg(
            roundrect_bg(fill=diff_color, radius=16)
        ).set_size((68, 58)).set_content_align("c").set_overflow("clip")
        with VSplit().set_sep(4).set_content_align("c").set_item_align("c"):
            TextBox(
                diff.upper(),
                TextStyle(
                    DEFAULT_HEAVY_FONT,
                    24,
                    diff_color.c1 if isinstance(diff_color, LinearGradient) else diff_color,
                ),
            )
            TextBox(note_count, TextStyle(DEFAULT_BOLD_FONT, 20, (80, 60, 85))).set_content_align("c")
            TextBox("COMBO", TextStyle(DEFAULT_HEAVY_FONT, 15, (80, 60, 85))).set_content_align("c")


def _draw_custom_chart_tags(info: CustomChartInfo | None, width: int):
    if not info or not info.tags:
        return

    with Flow().set_content_align("lt").set_item_align("lt").set_sep(8, 8).set_padding(16).set_w(width):
        TextBox("TAG", TextStyle(DEFAULT_HEAVY_FONT, 20, (50, 50, 50))).set_padding((8, 4))
        for tag in info.tags:
            text = str(tag).strip()
            if not text:
                continue
            TextBox(text, TextStyle(DEFAULT_BOLD_FONT, 18, (70, 70, 70)), line_count=1, overflow="shrink").set_padding(
                (12, 5)
            ).set_bg(roundrect_bg(fill=(255, 255, 255, 95), radius=10))


@dataclass(frozen=True)
class _MusicDetailAssets:
    cover: ImageSource
    vocal_logos: dict[str, ImageSource]
    event_banner: ImageSource | None


async def _load_music_detail_assets(rqd: MusicDetailRequest) -> _MusicDetailAssets:
    cover = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.music_jacket_path)
    custom_chart = rqd.custom_chart_info
    vocal_logo_paths = {} if custom_chart else rqd.vocal.vocal_assets
    logo_names = list(vocal_logo_paths)
    image_tasks = [get_asset_image_ref(ASSETS_BASE_DIR, path) for path in vocal_logo_paths.values()]
    load_event_banner = bool(rqd.event_banner_path and not custom_chart)
    if load_event_banner:
        image_tasks.append(get_asset_image_ref(ASSETS_BASE_DIR, rqd.event_banner_path))

    started_at = time.perf_counter()
    image_results = await asyncio.gather(*image_tasks) if image_tasks else []
    logger.debug(
        "[perf] compose_music_detail_image preload %d images: %.3fs",
        len(image_tasks),
        time.perf_counter() - started_at,
    )
    vocal_logos = {name: image_results[index] for index, name in enumerate(logo_names) if image_results[index]}
    event_banner = image_results[len(logo_names)] if load_event_banner else None
    return _MusicDetailAssets(cover, vocal_logos, event_banner)


class _MusicDetailRenderer:
    def __init__(self, rqd: MusicDetailRequest, assets: _MusicDetailAssets) -> None:
        self.rqd = rqd
        self.assets = assets
        self.custom_chart = rqd.custom_chart_info
        self.mid = rqd.music_info.id
        self.name = rqd.music_info.title + (" [FULL]" if rqd.music_info.is_full_length else "")
        self.publish_time = datetime_from_millis(rqd.music_info.release_at, rqd.timezone).strftime("%Y-%m-%d %H:%M:%S")
        self.bpm_main = f"{rqd.bpm} BPM" if rqd.bpm else "?"
        if self.custom_chart:
            if self.custom_chart.published_at:
                self.publish_time = datetime_from_millis(self.custom_chart.published_at, rqd.timezone).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            if self.custom_chart.bpm:
                self.bpm_main = f"{self.custom_chart.bpm} BPM"
        self.event_id = None if self.custom_chart else rqd.event_id
        self.caption_vocals = _build_caption_vocals(rqd.vocal.vocal_info, assets.vocal_logos)

    def _draw_heading(self) -> None:
        _draw_rqd_title(self.rqd)
        if self.custom_chart:
            custom_title = self.custom_chart.title or "自定义谱面"
            with VSplit().set_padding(16).set_sep(6).set_content_align("lt").set_item_align("lt").set_w(800):
                TextBox(
                    f"【{self.rqd.region.upper()}-CUSTOM】{self.name} / {custom_title}",
                    TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(20, 20, 20)),
                    line_count=1,
                    overflow="shrink",
                ).set_w(768)
                TextBox(
                    f"ID：{self.custom_chart.score_id}",
                    TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=(20, 20, 20)),
                    line_count=1,
                    overflow="shrink",
                ).set_w(768)
                description = (self.custom_chart.description or "").strip()
                if description:
                    TextBox(
                        f"说明：{description}",
                        TextStyle(font=DEFAULT_FONT, size=20, color=(85, 85, 85)),
                        line_count=2,
                        overflow="shrink",
                        use_real_line_count=True,
                    ).set_w(768)
            return

        name_text = f"【{self.rqd.region.upper()}-{self.mid}】{self.name}"
        if self.rqd.cn_name:
            name_text += f"  ({self.rqd.cn_name})"
        TextBox(
            name_text,
            TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(20, 20, 20)),
            use_real_line_count=True,
        ).set_padding(16).set_w(800)

    def _mv_text(self) -> str:
        labels = {"original": "原版MV", "mv": "3DMV", "mv_2d": "2DMV"}
        parts = [labels[item] for item in self.rqd.music_info.mv_info or [] if item in labels]
        return " & ".join(parts) or "无"

    def _draw_summary_labels(self, style: TextStyle) -> None:
        TextBox("原曲", style)
        if self.custom_chart:
            TextBox("谱面作者", style)
        TextBox("作曲", style)
        if not self.custom_chart:
            TextBox("作词", style)
            TextBox("编曲", style)
        TextBox("MV", style)
        TextBox("时长", style)
        TextBox("发布时间", style)
        TextBox("BPM", style)

    def _draw_summary_values(self, style: TextStyle) -> None:
        TextBox(f"{self.mid}", style)
        if self.custom_chart:
            TextBox(self.custom_chart.author or "-", style)
        TextBox(self.rqd.music_info.composer, style)
        if not self.custom_chart:
            TextBox(self.rqd.music_info.lyricist, style)
            TextBox(self.rqd.music_info.arranger, style)
        TextBox(self._mv_text(), style)
        TextBox(self.rqd.length, style)
        TextBox(self.publish_time, style)
        TextBox(self.bpm_main, style)

    def _draw_summary(self) -> None:
        with HSplit().set_content_align("c").set_item_align("c").set_sep(16):
            with Frame().set_padding(32):
                Spacer(w=300, h=300).set_bg(FillBg((0, 0, 0, 100))).set_offset((4, 4))
                ImageBox(self.assets.cover, size=(None, 300))
            label_style = TextStyle(font=DEFAULT_HEAVY_FONT, size=30, color=(50, 50, 50))
            value_style = TextStyle(font=DEFAULT_FONT, size=30, color=(70, 70, 70))
            with HSplit().set_padding(16).set_sep(32).set_content_align("c").set_item_align("c"):
                with VSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(0):
                    self._draw_summary_labels(label_style)
                with VSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(0):
                    self._draw_summary_values(value_style)

    def _draw_limited_times(self) -> None:
        if not self.rqd.limited_times or self.custom_chart:
            return
        with HSplit().set_content_align("l").set_item_align("l").set_sep(16).set_padding(16):
            TextBox("限定时间", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
            with VSplit().set_content_align("l").set_item_align("l").set_sep(4):
                for start, end in self.rqd.limited_times:
                    start_at = datetime_from_millis(start, self.rqd.timezone)
                    end_at = datetime_from_millis(end, self.rqd.timezone)
                    TextBox(
                        f"{start_at.strftime('%Y-%m-%d %H:%M')} ~ {end_at.strftime('%Y-%m-%d %H:%M')}",
                        TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70)),
                    )

    def _difficulty_order(self) -> tuple[list[str], bool]:
        has_append = self.rqd.difficulty.has_append
        order = self.rqd.difficulty.order or list(DIFF_COLORS)
        if not has_append:
            order = [difficulty for difficulty in order if difficulty != "append"]
        return order, has_append

    def _draw_standard_difficulty(self, diff_order: list[str], has_append: bool) -> None:
        col_count = 6 if has_append else 5
        h_sep = 8 if has_append else 20
        with HSplit().set_content_align("c").set_item_align("c").set_sep(4).set_padding(32).set_h(196):
            with Grid(col_count=col_count, item_size_mode="fixed").set_sep(h_sep=h_sep, v_sep=4):
                for index, difficulty in enumerate(diff_order):
                    if index >= len(self.rqd.difficulty.level) or self.rqd.difficulty.level[index] is None:
                        continue
                    color = DIFF_COLORS.get(difficulty, (80, 80, 80))
                    TextBox(
                        f"{self.rqd.difficulty.level[index]}",
                        TextStyle(font=DEFAULT_BOLD_FONT, size=32, color=WHITE),
                    ).set_bg(roundrect_bg(fill=color, radius=12)).set_size((64, 64)).set_content_align(
                        "c"
                    ).set_overflow("clip")
                for index, count in enumerate(self.rqd.difficulty.note_count):
                    if count is None:
                        continue
                    difficulty = diff_order[index] if index < len(diff_order) else ""
                    color = DIFF_COLORS.get(difficulty, (80, 80, 80))
                    style = TextStyle(
                        DEFAULT_BOLD_FONT,
                        18,
                        (80, 80, 80, 255),
                        use_shadow=True,
                        shadow_offset=1,
                        shadow_color=color.c1 if isinstance(color, LinearGradient) else color,
                    )
                    with VSplit().set_content_align("c").set_item_align("c").set_sep(1):
                        TextBox(f"{count}", style).set_size((64, None)).set_content_align("c").set_overflow("clip")
                        TextBox("combo", style.replace(size=14)).set_size((64, None)).set_content_align(
                            "c"
                        ).set_overflow("clip")

    def _leaderboard_keys(self) -> tuple[list[str], list[str]]:
        live_types = _ordered_music_detail_leaderboard_keys(self.rqd.leaderboard_live_types, ("solo", "multi", "auto"))
        targets = _ordered_music_detail_leaderboard_keys(self.rqd.leaderboard_targets, ("score", "pt", "pt/time"))
        return live_types, targets

    def _leaderboard_cell(self, row: int, column: int):
        matrix = self.rqd.leaderboard_matrix or []
        row_data = matrix[row] if row < len(matrix) else []
        info = row_data[column] if column < len(row_data) else None
        if info:
            music_num = self.rqd.leaderboard_music_num or 1
            return (
                (info.rank - 1) / max(1, music_num - 1),
                f"#{info.rank}",
                info.value,
                DIFF_COLORS.get(info.diff, (50, 50, 50)),
            )
        return 0.5, "-", None, (50, 50, 50)

    @staticmethod
    def _leaderboard_bg(rank_ratio: float):
        green, yellow, red = (200, 255, 200, 75), (255, 200, 150, 75), (255, 150, 150, 50)
        if rank_ratio <= 0.5:
            return lerp_color(green, yellow, rank_ratio)
        return lerp_color(yellow, red, rank_ratio - 0.5)

    def _draw_leaderboard(self, width: int) -> None:
        if not (self.rqd.leaderboard_matrix and self.rqd.leaderboard_live_types and self.rqd.leaderboard_targets):
            return
        live_types, targets = self._leaderboard_keys()
        th_w, th_h = 60, 36
        tr_w, tr_h = 120, 36
        gap = 4
        with VSplit().set_sep(gap).set_padding(16).set_content_align("l").set_item_align("l").set_w(width).set_h(196):
            with HSplit().set_sep(gap).set_content_align("l").set_item_align("c"):
                Spacer(w=th_w, h=th_h).set_bg(FillBg((255, 255, 255, 100)))
                for target in targets:
                    TextBox(
                        self.rqd.leaderboard_targets[target], TextStyle(DEFAULT_BOLD_FONT, 18, (50, 50, 50))
                    ).set_bg(FillBg((255, 255, 255, 100))).set_size((tr_w, th_h)).set_content_align("c")
            for row, live_type in enumerate(live_types):
                with HSplit().set_sep(gap).set_content_align("l").set_item_align("c"):
                    TextBox(
                        self.rqd.leaderboard_live_types[live_type],
                        TextStyle(DEFAULT_BOLD_FONT, 18, (50, 50, 50)),
                    ).set_bg(FillBg((255, 255, 255, 50))).set_size((th_w, th_h)).set_content_align("c")
                    for column, _target in enumerate(targets):
                        rank_ratio, rank_text, value_text, text_color = self._leaderboard_cell(row, column)
                        _build_music_detail_leaderboard_cell(
                            tr_w,
                            tr_h,
                            self._leaderboard_bg(rank_ratio),
                            rank_text,
                            value_text,
                            text_color,
                            padding=8,
                            gap=gap,
                            rank_box_w=42,
                        )

    def _draw_difficulty_and_leaderboard(self) -> None:
        diff_order, has_append = self._difficulty_order()
        total_w = 964
        gap = 8
        diff_width = (
            340
            if self.custom_chart
            else (6 if has_append else 5) * 64 + (5 if has_append else 4) * (8 if has_append else 20) + 64
        )
        leaderboard_width = total_w - diff_width - gap
        with (
            HSplit()
            .set_content_align("lt")
            .set_item_align("lt")
            .set_sep(gap)
            .set_omit_parent_bg(True)
            .set_item_bg(roundrect_bg(alpha=80))
            .set_w(total_w)
        ):
            if self.custom_chart:
                _draw_custom_chart_difficulty(self.rqd, diff_width, 146)
                _draw_custom_chart_info(self.rqd, leaderboard_width, 146)
            else:
                self._draw_standard_difficulty(diff_order, has_append)
                self._draw_leaderboard(leaderboard_width)

    def _draw_aliases(self) -> None:
        if not self.rqd.alias or self.custom_chart:
            return
        alias_text = "，".join(self.rqd.alias)
        font_size = max(10, 24 - get_str_display_length(alias_text) // 40)
        with HSplit().set_content_align("l").set_item_align("l").set_sep(16).set_padding(16):
            TextBox("歌曲别名", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
            TextBox(
                alias_text,
                TextStyle(font=DEFAULT_FONT, size=font_size, color=(70, 70, 70)),
                use_real_line_count=True,
            ).set_w(800)

    def _draw_vocal(self, width: int = 964) -> None:
        content_width = width - 32
        max_text_width = max(120, min(420, content_width - 32))
        max_icons_per_chip = max(1, (content_width - 12) // 34)
        with Flow().set_content_align("lt").set_item_align("lt").set_sep(8, 8).set_padding(16).set_w(width):
            for caption, vocals in sorted(self.caption_vocals.items(), key=lambda item: len(item[1])):
                with (
                    VSplit().set_w(content_width).set_padding(0).set_sep(6).set_content_align("lt").set_item_align("lt")
                ):
                    TextBox(
                        caption + "  ver.",
                        TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)),
                        line_count=2,
                        overflow="shrink",
                        use_real_line_count=True,
                    ).set_w(content_width)
                    with (
                        Flow()
                        .set_content_align("lt")
                        .set_item_align("lt")
                        .set_sep(6, 6)
                        .set_padding(0)
                        .set_w(content_width)
                    ):
                        for vocal in vocals:
                            if vocal_name := vocal.get("vocal_name"):
                                _draw_vocal_name_chip(vocal_name, max_text_width)
                                continue
                            chara_imgs = vocal.get("chara_imgs") or []
                            for index in range(0, len(chara_imgs), max_icons_per_chip):
                                _draw_vocal_image_chip(chara_imgs[index : index + max_icons_per_chip])
                            for vocal_name in vocal.get("vocal_names") or []:
                                _draw_vocal_name_chip(vocal_name, max_text_width)

    def _draw_event(self) -> None:
        with HSplit().set_sep(8).set_content_align("c").set_item_align("c").set_padding(16):
            with VSplit().set_content_align("c").set_item_align("c").set_sep(8):
                TextBox("关联活动", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
                TextBox(f"ID: {self.event_id}", TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70)))
            ImageBox(self.assets.event_banner, size=(None, 100))

    def _draw_related_content(self) -> None:
        if self.custom_chart:
            return
        if self.event_id is None:
            self._draw_vocal()
            return
        with HSplit().set_omit_parent_bg(True).set_item_bg(roundrect_bg(alpha=80)).set_padding(0).set_sep(16):
            self._draw_vocal(600)
            self._draw_event()

    def build_canvas(self) -> Canvas:
        with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16).set_item_bg(roundrect_bg(alpha=80)):
                with (
                    VSplit()
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(8)
                    .set_padding(16)
                    .set_item_bg(roundrect_bg(alpha=80))
                ):
                    self._draw_heading()
                    self._draw_summary()
                    self._draw_limited_times()
                    if self.custom_chart:
                        _draw_custom_chart_tags(self.custom_chart, 964)
                    self._draw_difficulty_and_leaderboard()
                    self._draw_aliases()
                    self._draw_related_content()
        add_request_watermark(canvas, self.rqd)
        return canvas


async def _build_music_detail_canvas(rqd: MusicDetailRequest) -> Canvas:
    assets = await _load_music_detail_assets(rqd)
    return _MusicDetailRenderer(rqd, assets).build_canvas()


async def compose_music_detail_image(rqd: MusicDetailRequest) -> Image.Image:
    return await (await _build_music_detail_canvas(rqd)).get_img()


async def try_render_music_detail_payload(rqd: MusicDetailRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_music_detail_canvas(rqd), endpoint="music_detail")


def _music_brief_release_date(music: MusicBriefList, timezone) -> str:
    if music.music_info is None:
        return ""
    return datetime_from_millis(music.music_info.release_at, timezone).strftime("%Y-%m-%d")


def _music_brief_difficulty_levels(
    music: MusicBriefList,
    required_difficulty: str,
) -> list[tuple[str, int]]:
    if music.difficulty is None:
        return [(required_difficulty, music.level)] if required_difficulty and music.level else []

    difficulty = music.difficulty
    order = difficulty.order or ["easy", "normal", "hard", "expert", "master"]
    if difficulty.has_append:
        order = [*order, "append"]
    return [
        (name, difficulty.level[index])
        for index, name in enumerate(order)
        if index < len(difficulty.level) and difficulty.level[index]
    ]


async def _draw_music_brief_jacket(music: MusicBriefList, jacket: ImageSource | None) -> None:
    with Frame():
        ImageBox(jacket, size=(96, 96), image_size_mode="fill")
        if music.play_result:
            result_path = RESULT_ASSET_PATH + f"/icon_{music.play_result}.png"
            result_image = await get_asset_image_ref(ASSETS_BASE_DIR, result_path)
            if result_image:
                ImageBox(result_image, size=(20, 20), image_size_mode="fill").set_offset((96 - 14, 96 - 14))


def _draw_music_brief_details(
    music: MusicBriefList,
    release_date: str,
    difficulty_levels: list[tuple[str, int]],
) -> None:
    with VSplit().set_sep(8).set_content_align("lt").set_item_align("lt"):
        TextBox(
            f"【{music.id}】{music.music_info.title if music.music_info else ''}",
            TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK),
            use_real_line_count=True,
        ).set_w(820)
        if release_date:
            TextBox(
                release_date,
                TextStyle(font=DEFAULT_FONT, size=18, color=(90, 90, 90)),
            )
        with HSplit().set_sep(8).set_content_align("c").set_item_align("c"):
            for difficulty_name, level in difficulty_levels:
                TextBox(
                    str(level),
                    TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=WHITE),
                ).set_padding((10, 4)).set_bg(roundrect_bg(fill=DIFF_COLORS.get(difficulty_name, BLACK), radius=12))


async def _draw_music_brief_row(
    music: MusicBriefList,
    jacket: ImageSource | None,
    timezone,
    required_difficulty: str,
) -> None:
    release_date = _music_brief_release_date(music, timezone)
    difficulty_levels = _music_brief_difficulty_levels(music, required_difficulty)
    with (
        HSplit()
        .set_bg(roundrect_bg(alpha=80))
        .set_padding(12)
        .set_sep(12)
        .set_content_align("c")
        .set_item_align("c")
        .set_w(964)
    ):
        await _draw_music_brief_jacket(music, jacket)
        _draw_music_brief_details(music, release_date, difficulty_levels)


async def _build_music_brief_list_canvas(rqd: MusicBriefListRequest) -> Canvas:
    profile = rqd.profile

    # 预加载封面
    jacket_tasks = [get_asset_image_ref(ASSETS_BASE_DIR, m.music_jacket_path) for m in rqd.music_list]
    _t0 = time.perf_counter()
    loaded_jackets = await asyncio.gather(*jacket_tasks)
    logger.debug(
        "[perf] compose_music_brief_list_image jackets %d: %.3fs",
        len(jacket_tasks),
        time.perf_counter() - _t0,
    )
    jackets = {m.id: img for m, img in zip(rqd.music_list, loaded_jackets)}

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            # 附加标题
            _draw_rqd_title(rqd)

            if profile:
                await get_profile_card(profile)

            with VSplit().set_bg(roundrect_bg(alpha=80)).set_padding(16).set_sep(16):
                for m in rqd.music_list:
                    await _draw_music_brief_row(m, jackets.get(m.id), rqd.timezone, rqd.required_difficulty)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_music_brief_list_image(rqd: MusicBriefListRequest) -> Image.Image:
    return await (await _build_music_brief_list_canvas(rqd)).get_img()


async def try_render_music_brief_list_payload(rqd: MusicBriefListRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_music_brief_list_canvas(rqd), endpoint="music_brief_list")


async def _load_music_list_jackets(rqd: MusicListRequest, image_loader) -> dict[int, ImageSource]:
    music_ids = list(rqd.jackets_path_list)
    jacket_tasks = [image_loader(ASSETS_BASE_DIR, rqd.jackets_path_list[music_id]) for music_id in music_ids]
    started_at = time.perf_counter()
    loaded_jackets = await asyncio.gather(*jacket_tasks)
    logger.debug(
        "[perf] compose_music_list_image jackets %d: %.3fs",
        len(jacket_tasks),
        time.perf_counter() - started_at,
    )
    return dict(zip(music_ids, loaded_jackets))


def _group_music_list(rqd: MusicListRequest) -> list[tuple[tuple[str, int], list[dict]]]:
    grouped_musics: dict[tuple[str, int], list[dict]] = {}
    for music in rqd.music_list:
        level = music["difficulty"]
        difficulty = str(music.get("difficulty_type") or rqd.required_difficulties or "").lower()
        grouped_musics.setdefault((difficulty, level), []).append(music)
    return sorted(grouped_musics.items(), key=lambda item: _music_list_group_order(item[0][0], item[0][1]))


def _prepare_music_list_group(rqd: MusicListRequest, musics: list[dict]) -> None:
    musics.sort(key=lambda music: (music["release_at"], music["id"]))
    for music in musics:
        music["play_result"] = rqd.user_results.get(music["id"])


def _music_list_result_icon_path(rqd: MusicListRequest, play_result: str) -> str:
    if rqd.play_result_icon_path_map and play_result in rqd.play_result_icon_path_map:
        return rqd.play_result_icon_path_map[play_result]
    return RESULT_ASSET_PATH + f"/icon_{play_result}.png"


async def _draw_music_list_entry(
    rqd: MusicListRequest,
    music: dict,
    jackets: dict[int, ImageSource],
    image_loader,
) -> None:
    with VSplit().set_sep(2):
        with Frame():
            ImageBox(jackets[music["id"]], size=(64, 64), image_size_mode="fill")
            if play_result := music["play_result"]:
                result_img_path = _music_list_result_icon_path(rqd, play_result)
                result_img = await image_loader(ASSETS_BASE_DIR, result_img_path)
                ImageBox(result_img, size=(16, 16), image_size_mode="fill").set_offset((64 - 10, 64 - 10))
        # 默认始终显示 ID，因为它是列表查询
        TextBox(f"{music['id']}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK)).set_w(64)


async def _draw_music_list_group(
    rqd: MusicListRequest,
    difficulty: str,
    level: int,
    musics: list[dict],
    jackets: dict[int, ImageSource],
    image_loader,
) -> None:
    _prepare_music_list_group(rqd, musics)
    difficulty = difficulty or str(rqd.required_difficulties or "").lower()
    difficulty_color = DIFF_COLORS.get(difficulty, DIFF_COLORS["master"])
    with VSplit().set_bg(roundrect_bg(alpha=80)).set_padding(8).set_item_align("lt").set_sep(8):
        level_text = TextBox(f"{difficulty.upper()} {level}", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=WHITE))
        level_text.set_padding((10, 5)).set_bg(roundrect_bg(fill=difficulty_color, radius=5))

        with Grid(col_count=10).set_sep(5):
            for music in musics:
                await _draw_music_list_entry(rqd, music, jackets, image_loader)


async def _build_music_list_canvas(rqd: MusicListRequest) -> Canvas:
    # Header-only refs: the Skia path emits asset paths into the IR, the Pillow
    # fallback decodes on demand (Canvas.get_img prefetches concurrently).
    image_loader = get_asset_image_ref
    jackets = await _load_music_list_jackets(rqd, image_loader)
    grouped_musics = _group_music_list(rqd)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            # 附加标题
            _draw_rqd_title(rqd)

            if rqd.profile:
                await get_profile_card(rqd.profile.to_profile_card_request())

            with VSplit().set_bg(roundrect_bg(alpha=80)).set_padding(16).set_sep(16):
                for (difficulty, level), musics in grouped_musics:
                    await _draw_music_list_group(rqd, difficulty, level, musics, jackets, image_loader)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_music_list_image(rqd: MusicListRequest) -> Image.Image:
    return await (await _build_music_list_canvas(rqd)).get_img()


async def try_render_music_list_payload(rqd: MusicListRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_music_list_canvas(rqd), endpoint="music_list")


async def _build_play_progress_canvas(rqd: PlayProgressRequest) -> Canvas:
    r"""compose_play_progress_image

    合成打歌进度图片

    TODO:
        TextBox shadow 暂未实现
    """
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if rqd.profile:
                await get_profile_card(rqd.profile)

            bar_h, item_h, w = 200, 48, 48
            font_sz = 24

            with (
                HSplit()
                .set_content_align("c")
                .set_item_align("c")
                .set_bg(roundrect_bg(alpha=80))
                .set_padding(64)
                .set_sep(8)
            ):

                async def draw_icon(path):
                    path = await get_asset_image_ref(ASSETS_BASE_DIR, RESULT_ASSET_PATH + f"/{path}")
                    with Frame().set_size((w, item_h)).set_content_align("c"):
                        ImageBox(path, size=(w // 2, w // 2))

                # 第一列：进度条的占位 难度占位 not_clear clear fc ap 图标
                with VSplit().set_content_align("c").set_item_align("c").set_sep(8):
                    Spacer(w=w, h=bar_h)
                    Spacer(w=w, h=item_h)
                    await draw_icon("icon_not_clear.png")
                    await draw_icon("icon_clear.png")
                    await draw_icon("icon_fc.png")
                    await draw_icon("icon_ap.png")

                # 之后的几列：进度条 难度 各个类型的数量
                for c in rqd.counts:
                    with VSplit().set_content_align("c").set_item_align("c").set_sep(8):
                        # 进度条
                        def draw_bar(color, h, blur_glass=False):
                            return (
                                Frame()
                                .set_size((w, h))
                                .set_bg(roundrect_bg(fill=color, radius=4, blur_glass=blur_glass))
                            )

                        with draw_bar(PLAY_RESULT_COLORS["not_clear"], bar_h, blur_glass=True).set_content_align("b"):
                            if c.clear:
                                draw_bar(PLAY_RESULT_COLORS["clear"], int(bar_h * c.clear / c.total))
                            if c.fc:
                                draw_bar(PLAY_RESULT_COLORS["fc"], int(bar_h * c.fc / c.total))
                            if c.ap:
                                draw_bar(PLAY_RESULT_COLORS["ap"], int(bar_h * c.ap / c.total))

                        # 难度
                        TextBox(
                            f"{c.level}", TextStyle(font=DEFAULT_BOLD_FONT, size=font_sz, color=WHITE), overflow="clip"
                        ).set_bg(roundrect_bg(fill=DIFF_COLORS[rqd.difficulty], radius=16)).set_size(
                            (w, item_h)
                        ).set_content_align("c")
                        # 数量 (第一行虽然图标是not_clear但是实际上是total)
                        color = PLAY_RESULT_COLORS["not_clear"]
                        ap = c.ap
                        fc = c.fc - c.ap
                        clear = c.clear - c.fc
                        total = c.total - c.clear
                        style = TextStyle(DEFAULT_BOLD_FONT, font_sz, color, use_shadow=False)
                        TextBox(f"{total}", style, overflow="clip").set_size((w, item_h)).set_content_align("c").set_bg(
                            roundrect_bg(alpha=80)
                        )
                        style = TextStyle(
                            DEFAULT_BOLD_FONT,
                            font_sz,
                            color,
                            use_shadow=True,
                            shadow_color=PLAY_RESULT_COLORS["clear"],
                            shadow_offset=2,
                        )
                        TextBox(f"{clear}", style, overflow="clip").set_size((w, item_h)).set_content_align("c").set_bg(
                            roundrect_bg(alpha=80)
                        )
                        style = TextStyle(
                            DEFAULT_BOLD_FONT,
                            font_sz,
                            color,
                            use_shadow=True,
                            shadow_color=PLAY_RESULT_COLORS["fc"],
                            shadow_offset=2,
                        )
                        TextBox(f"{fc}", style, overflow="clip").set_size((w, item_h)).set_content_align("c").set_bg(
                            roundrect_bg(alpha=80)
                        )
                        style = TextStyle(
                            DEFAULT_BOLD_FONT,
                            font_sz,
                            color,
                            use_shadow=True,
                            shadow_color=PLAY_RESULT_COLORS["ap"],
                            shadow_offset=2,
                        )
                        TextBox(f"{ap}", style, overflow="clip").set_size((w, item_h)).set_content_align("c").set_bg(
                            roundrect_bg(alpha=80)
                        )

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_play_progress_image(rqd: PlayProgressRequest) -> Image.Image:
    return await (await _build_play_progress_canvas(rqd)).get_img()


async def try_render_play_progress_payload(rqd: PlayProgressRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_play_progress_canvas(rqd), endpoint="music_progress")


def draw_text_icon(text: str, icon: ImageSource, style: TextStyle) -> HSplit:
    r"""draw_text_icon

    绘制文字和图标，
    只在合成歌曲奖励图片的两个函数中使用

    Args
    ----
    text : str
        要绘制的文字
    icon : ImageSource
        要绘制的图标
    style : TextStyle
        绘制的文字样式

    Return
    ------
    HSplit
    """
    with HSplit().set_content_align("c").set_item_align("c").set_sep(4) as hs:
        if text is not None:
            TextBox(str(text), style, overflow="clip")
        ImageBox(icon, size=(None, 40))
    return hs


async def _build_detail_music_rewards_canvas(rqd: DetailMusicRewardsRequest) -> Canvas:
    r"""compose_detail_music_rewards_image

    在有抓包数据的情况下合成歌曲奖励图片

    Args
    ----
    rqd : DetailMusicRewardsRequest
        在有抓包数据的情况下合成歌曲奖励图片所必需的数据

    Return
    ------
    PIL.Image.Image
    """
    # 网格宽度和高度
    gw, gh = 80, 40
    # 样式
    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(75, 75, 75))
    # 奖励的icon
    j_path = rqd.jewel_icon_path or RESULT_ASSET_PATH + "/jewel.png"
    s_path = rqd.shard_icon_path or RESULT_ASSET_PATH + "/shard.png"
    jewel_icon: ImageSource = await get_asset_image_ref(ASSETS_BASE_DIR, j_path)
    shard_icon: ImageSource = await get_asset_image_ref(ASSETS_BASE_DIR, s_path)

    # 绘图
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_profile_card(rqd.profile)
            with (
                VSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(16)
                .set_padding(16)
                .set_bg(roundrect_bg(alpha=80))
            ):
                # 乐曲评级奖励
                with (
                    HSplit()
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(24)
                    .set_padding(16)
                    .set_bg(roundrect_bg(alpha=80))
                ):
                    TextBox("歌曲评级奖励(S)", style1).set_size((None, gh)).set_content_align("c")
                    draw_text_icon(rqd.rank_rewards, jewel_icon, style2).set_size((None, gh))
                # 连击奖励
                with (
                    HSplit()
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(16)
                    .set_item_bg(roundrect_bg(alpha=80))
                ):
                    for diff in ("hard", "expert", "master", "append"):  # 因为go的map是无序的，用这个保证顺序
                        with HSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(16):
                            # 难度
                            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8):
                                Spacer(w=gw, h=gh)
                                for combo_reward in rqd.combo_rewards[diff]:  # slice是有序的，所以不用再排序
                                    TextBox(
                                        str(combo_reward.level),
                                        TextStyle(DEFAULT_BOLD_FONT, 24, WHITE),
                                        overflow="clip",
                                    ).set_size((gh, gh)).set_content_align("c").set_bg(
                                        roundrect_bg(fill=DIFF_COLORS[diff], radius=8)
                                    )
                            # 奖励
                            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8):
                                ImageBox(jewel_icon if diff != "append" else shard_icon, size=(None, gh))
                                for combo_reward in rqd.combo_rewards[diff]:
                                    TextBox(str(combo_reward.reward), style2, overflow="clip").set_size(
                                        (gw, gh)
                                    ).set_content_align("l")
                            # 累计奖励
                            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8):
                                TextBox("累计", style1).set_size((gw, gh)).set_content_align("l")
                                acc = 0
                                for combo_reward in rqd.combo_rewards[diff]:
                                    acc += combo_reward.reward
                                    TextBox(str(acc), style2, overflow="clip").set_size((gw, gh)).set_content_align("l")

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_detail_music_rewards_image(rqd: DetailMusicRewardsRequest) -> Image.Image:
    return await (await _build_detail_music_rewards_canvas(rqd)).get_img()


async def try_render_detail_music_rewards_payload(rqd: DetailMusicRewardsRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_detail_music_rewards_canvas(rqd), endpoint="music_rewards_detail")


async def _build_basic_music_rewards_canvas(rqd: BasicMusicRewardsRequest) -> Canvas:
    r"""compose_basic_music_rewards_image

    在仅基础数据的情况下合成歌曲奖励图片

    Args
    ----
    rqd : BasicMusicRewardsRequest
        在仅基础数据的情况下合成歌曲奖励图片所必需的数据

    Return
    ------
    PIL.Image.Image
    """
    # 网格宽度和高度
    _gw, gh = 80, 40
    # 样式
    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(75, 75, 75))
    # 奖励的icon
    j_path = rqd.jewel_icon_path or f"{RESULT_ASSET_PATH}/jewel.png"
    s_path = rqd.shard_icon_path or f"{RESULT_ASSET_PATH}/shard.png"
    jewel_icon: ImageSource = await get_asset_image_ref(ASSETS_BASE_DIR, j_path)
    shard_icon: ImageSource = await get_asset_image_ref(ASSETS_BASE_DIR, s_path)
    # 绘图
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_profile_card(rqd.profile)
            with (
                VSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(16)
                .set_padding(16)
                .set_bg(roundrect_bg(alpha=80))
            ):
                # 说明
                TextBox(
                    "仅显示简略估计数据（假设Clear的歌曲都是S评级，未FC的歌曲都没拿到连击奖励）",
                    TextStyle(DEFAULT_FONT, 20, (200, 75, 75)),
                    use_real_line_count=True,
                ).set_w(480)
                # 乐曲评级奖励
                with (
                    HSplit()
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(24)
                    .set_padding(16)
                    .set_bg(roundrect_bg(alpha=80))
                ):
                    TextBox("歌曲评级奖励(S)", style1).set_size((None, gh)).set_content_align("c")
                    draw_text_icon(rqd.rank_rewards, jewel_icon, style2).set_size((None, gh))
                # 连击奖励
                with (
                    VSplit()
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(8)
                    .set_item_bg(roundrect_bg(alpha=80))
                    .set_padding(16)
                    .set_bg(roundrect_bg(alpha=80))
                ):
                    for diff in ["hard", "expert", "master", "append"]:
                        with HSplit().set_content_align("lt").set_item_align("lt").set_sep(24):
                            TextBox(f"{diff.upper()}", TextStyle(DEFAULT_BOLD_FONT, 24, WHITE), overflow="clip").set_bg(
                                roundrect_bg(fill=DIFF_COLORS[diff], radius=8)
                            ).set_size((120, gh)).set_content_align("c")
                            TextBox("连击奖励", style1).set_size((None, gh)).set_content_align("l")
                            draw_text_icon(
                                rqd.combo_rewards[diff], jewel_icon if diff != "append" else shard_icon, style2
                            ).set_size((None, gh))

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_basic_music_rewards_image(rqd: BasicMusicRewardsRequest) -> Image.Image:
    return await (await _build_basic_music_rewards_canvas(rqd)).get_img()


async def try_render_basic_music_rewards_payload(rqd: BasicMusicRewardsRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_basic_music_rewards_canvas(rqd), endpoint="music_rewards_basic")
