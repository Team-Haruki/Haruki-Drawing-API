import asyncio
from dataclasses import dataclass
import logging
import time

from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    DIFF_COLORS,
    SEKAI_BLUE_BG,
    Canvas,
    TextBox,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import WHITE
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
from src.sekai.base.utils import ImageSource, get_asset_image_ref
from src.sekai.profile.drawer import (
    CardFullThumbnailBox,
    get_card_full_thumbnail_layers,
    get_profile_card,
)
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT

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
    return str(song.music_id or song.music_cover_path or song.title)


def _planner_rows(planner: DeckPlannerInfo) -> list[tuple[DeckPlannerSong, DeckPlannerBoostRow | None]]:
    rows: list[tuple[DeckPlannerSong, DeckPlannerBoostRow | None]] = []
    for song in planner.songs:
        rows.extend((song, row) for row in song.rows or [None])
    return rows


def _draw_planner_summary(planner: DeckPlannerInfo) -> None:
    with HSplit().set_content_align("l").set_item_align("c").set_sep(14).set_padding(0):
        TextBox("活动规划", TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=(50, 50, 50)))
        TextBox(
            f"目标 {format_planner_int(planner.target_point)}pt",
            TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(70, 70, 70)),
        )
        TextBox(
            f"当前 {format_planner_int(planner.current_point)}pt",
            TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(70, 70, 70)),
        )
        TextBox(
            f"还需 {format_planner_int(planner.remaining_point)}pt",
            TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(0, 180, 220)),
        )
        if planner.target_source:
            TextBox(
                f"来源 {planner.target_source}",
                TextStyle(font=DEFAULT_FONT, size=18, color=(90, 90, 90)),
                overflow="shrink",
            ).set_w(240)


def _draw_planner_header(style: TextStyle) -> None:
    with HSplit().set_content_align("l").set_item_align("c").set_sep(16).set_padding(0):
        TextBox("歌曲 / 火数", style).set_w(330).set_h(48).set_content_align("c")
        TextBox("每把PT", style).set_w(140).set_h(48).set_content_align("c")
        TextBox("需要把数", style).set_w(140).set_h(48).set_content_align("c")
        TextBox("体力", style).set_w(120).set_h(48).set_content_align("c")
        TextBox("日速", style).set_w(140).set_h(48).set_content_align("c")


def _draw_planner_song_cell(
    song: DeckPlannerSong,
    row: DeckPlannerBoostRow | None,
    planner_music_imgs: dict[str, ImageSource],
) -> None:
    with HSplit().set_w(330).set_h(76).set_content_align("c").set_item_align("c").set_sep(10):
        with Frame().set_size((56, 56)).set_content_align("c"):
            diff = (song.difficulty or "").lower()
            if diff in DIFF_COLORS:
                Spacer(w=52, h=52).set_bg(FillBg(fill=DIFF_COLORS[diff])).set_offset((3, 3))
            cover = planner_music_imgs.get(song.music_cover_path or planner_cover_key(song))
            if cover is not None:
                ImageBox(cover, size=(52, 52)).set_offset((-2, -2))
            else:
                Spacer(w=52, h=52).set_bg(RoundRectBg((235, 242, 248, 255), 6)).set_offset((-2, -2))

        with VSplit().set_w(230).set_content_align("l").set_item_align("l").set_sep(2).set_padding(0):
            TextBox(
                song.title,
                TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(70, 70, 70)),
                overflow="shrink",
            ).set_w(230)
            with HSplit().set_content_align("l").set_item_align("l").set_sep(6).set_padding(0):
                TextBox(
                    (song.difficulty or "DIFF").upper(),
                    TextStyle(
                        font=DEFAULT_BOLD_FONT,
                        size=12,
                        color=DIFF_COLORS.get(diff, (70, 70, 70)),
                    ),
                ).set_bg(RoundRectBg((255, 255, 255, 180), 4))
                TextBox(
                    f"{row.boost}火" if row else "-",
                    TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=(130, 80, 180)),
                ).set_bg(RoundRectBg((246, 237, 255, 220), 4))


def _draw_planner_number_cell(
    text: str,
    sub_text: str,
    width: int,
    style: TextStyle,
    color: tuple[int, int, int] = (70, 70, 70),
) -> None:
    with VSplit().set_w(width).set_h(76).set_content_align("c").set_item_align("c").set_sep(2):
        TextBox(text, style.replace(color=color), overflow="shrink").set_w(width).set_content_align("c")
        TextBox(
            sub_text,
            TextStyle(font=DEFAULT_FONT, size=13, color=(75, 75, 75)),
        ).set_w(width).set_content_align("c")


def _draw_planner_row(
    planner: DeckPlannerInfo,
    song: DeckPlannerSong,
    row: DeckPlannerBoostRow | None,
    planner_music_imgs: dict[str, ImageSource],
    style: TextStyle,
) -> None:
    with HSplit().set_content_align("l").set_item_align("c").set_sep(16).set_padding(0):
        _draw_planner_song_cell(song, row, planner_music_imgs)
        _draw_planner_number_cell(format_planner_int(row.point_per_play if row else 0), "pt/把", 140, style)
        _draw_planner_number_cell(
            format_planner_int(row.plays if row else 0),
            "把",
            140,
            style,
            (0, 180, 220),
        )
        _draw_planner_number_cell(
            format_planner_int(row.energy if row else 0),
            "火",
            120,
            style,
            (142, 94, 190),
        )
        _draw_planner_number_cell(format_planner_optional_int(planner.daily_point), "pt/日", 140, style)


def _draw_planner_tips(planner: DeckPlannerInfo) -> None:
    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
        tip_style = TextStyle(font=DEFAULT_FONT, size=16, color=(20, 20, 20))
        TextBox(
            "活动规划按当前数据估算，实际结算以游戏内和榜线更新为准。",
            tip_style,
            use_real_line_count=True,
        ).set_w(920)
        TextBox(
            "未指定当前 pt 时按 0 计算；不写歌曲时默认虾 EXPERT / 龙 HARD。",
            tip_style,
            use_real_line_count=True,
        ).set_w(920)
        for warning in planner.warnings or []:
            TextBox(
                f"提示: {warning}",
                TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(200, 75, 75)),
                use_real_line_count=True,
            ).set_w(920)


def draw_event_planner_block(planner: DeckPlannerInfo, planner_music_imgs: dict[str, ImageSource]) -> None:
    th_style = TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(75, 75, 75))
    tb_style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(70, 70, 70))
    rows = _planner_rows(planner)

    with (
        VSplit().set_content_align("lt").set_item_align("lt").set_sep(14).set_padding(16).set_bg(roundrect_bg(alpha=80))
    ):
        _draw_planner_summary(planner)
        _draw_planner_header(th_style)

        if rows:
            for song, row in rows:
                _draw_planner_row(planner, song, row, planner_music_imgs, tb_style)
        else:
            TextBox("没有可展示的规划歌曲", TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(255, 50, 50)))

        _draw_planner_tips(planner)


_RECOMMEND_TYPES_WITHOUT_LIVE_SUFFIX = {"mysekai", "challenge", "challenge_all", "bonus", "wl_bonus"}


def _recommend_type_title(recommend_type: str, event_id: int | None, wl_chara_name: str | None) -> str:
    if recommend_type == "mysekai":
        return f"烤森活动#{event_id}组卡" if event_id else "烤森模拟活动组卡"
    if recommend_type in {"challenge", "challenge_all"}:
        return "每日挑战组卡"
    if recommend_type == "bonus":
        return f"活动#{event_id}加成组卡"
    if recommend_type == "wl_bonus":
        return f"WL活动#{event_id}加成组卡"
    if recommend_type == "event":
        return f"活动#{event_id}组卡"
    if recommend_type == "wl":
        if event_id:
            return f"WL活动#{event_id}组卡"
        return "WL模拟组卡" if wl_chara_name else "WL终章活动组卡"
    return {"unit_attr": "团队+颜色模拟活动组卡", "no_event": "无活动组卡"}.get(recommend_type, "")


def _recommend_live_suffix(live_type: str | None, live_name: str | None) -> str:
    if live_type == "multi":
        return f"({live_name})"
    return {"solo": "(单人)", "auto": "(AUTO)"}.get(live_type, "")


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
    compare_cover_paths = []
    planner_cover_paths = []
    for deck in rqd.deck_data:
        if rqd.music_compare and deck.music_cover_path and deck.music_cover_path not in compare_cover_paths:
            compare_cover_paths.append(deck.music_cover_path)
        for card in deck.card_data:
            card_thumb_tasks.append(get_card_full_thumbnail_layers(card.card_thumbnail))
            card_thumb_keys.append(
                (
                    card.card_thumbnail.card_id,
                    card.card_thumbnail.is_after_training,
                    card.card_thumbnail.card_thumbnail_path,
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
    deck_keys = list(deck_tasks)
    started_at = time.perf_counter()
    results = await asyncio.gather(*deck_tasks.values(), *card_thumb_tasks, *compare_tasks, *planner_tasks)
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
    planner_results = results[compare_end:]
    return _DeckRecommendAssets(
        chara_icon=chara_icon,
        wl_chara_icon=deck_images.get("wl_chara"),
        unit_logo=deck_images.get("unit_logo"),
        attr_icon=deck_images.get("attr_icon"),
        music_cover=deck_images.get("music_cover"),
        canvas_thumbnail=deck_images.get("canvas_thumb"),
        card_layers=dict(zip(card_thumb_keys, thumbnail_results)),
        compare_music_imgs=dict(zip(compare_cover_paths, compare_results)),
        planner_music_imgs=dict(zip(planner_cover_paths, planner_results)),
    )


def _deck_title(rqd: DeckRequest) -> str:
    title = build_recommend_title(
        rqd.recommend_type,
        rqd.event_id,
        rqd.wl_chara_name,
        rqd.live_type,
        rqd.live_name,
    )
    if rqd.event_planner:
        title = title.replace("组卡", "规划")
    return title


def _deck_score_name(rqd: DeckRequest) -> str:
    return "分数" if rqd.recommend_type in {"challenge", "challenge_all", "no_event"} else "PT"


async def _draw_deck_event_banner(rqd: DeckRequest, title: str) -> str:
    if rqd.recommend_type not in {"event", "wl", "bonus", "wl_bonus", "mysekai"} or not rqd.event_id:
        return title
    if not rqd.event_banner_path:
        return rqd.event_name + " " + title
    event_banner = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.event_banner_path)
    ImageBox(event_banner, size=(None, 50))
    return title


def _draw_deck_title_extras(rqd: DeckRequest, assets: _DeckRecommendAssets) -> None:
    if rqd.recommend_type == "challenge":
        if assets.chara_icon:
            ImageBox(assets.chara_icon, size=(None, 50))
        if rqd.chara_name:
            TextBox(
                rqd.chara_name,
                TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(70, 70, 70)),
            )
    if rqd.is_wl and rqd.wl_chara_name:
        if assets.wl_chara_icon is not None:
            ImageBox(assets.wl_chara_icon, size=(None, 50))
        TextBox(
            f"{rqd.wl_chara_name} 章节",
            TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(70, 70, 70)),
        )
    if assets.unit_logo and assets.attr_icon:
        ImageBox(assets.unit_logo, size=(None, 60))
        ImageBox(assets.attr_icon, size=(None, 50))
    if rqd.is_max_deck:
        TextBox(
            f"({rqd.region}顶配)",
            TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(50, 50, 50)),
        )


async def _draw_deck_title_row(rqd: DeckRequest, assets: _DeckRecommendAssets) -> None:
    title = _deck_title(rqd)

    with HSplit().set_content_align("l").set_item_align("l").set_sep(16):
        title = await _draw_deck_event_banner(rqd, title)
        TextBox(
            title,
            TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(50, 50, 50)),
            use_real_line_count=True,
        )
        _draw_deck_title_extras(rqd, assets)


async def _draw_deck_settings(rqd: DeckRequest) -> None:
    excluded_cards = rqd.excluded_cards or []
    if not any(
        (
            rqd.unit_filter,
            rqd.attr_filter,
            excluded_cards,
            rqd.multi_live_score_up_lower_bound,
            rqd.keep_after_training_state,
        )
    ):
        return

    with HSplit().set_content_align("l").set_item_align("l").set_sep(16):
        setting_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
        TextBox("卡组设置:", setting_style)
        if rqd.unit_filter or rqd.attr_filter:
            TextBox("仅", setting_style)
            if rqd.unit_filter:
                unit_logo = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.unit_logo_path)
                ImageBox(unit_logo, size=(None, 40))
            if rqd.attr_filter:
                attr_icon = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.attr_icon_path)
                ImageBox(attr_icon, size=(None, 35))
            TextBox("上场", setting_style)
        if excluded_cards:
            TextBox(f"排除 {','.join(map(str, excluded_cards))}", setting_style)
        if rqd.multi_live_score_up_lower_bound:
            TextBox(f"实效≥{int(rqd.multi_live_score_up_lower_bound)}%", setting_style)
        if rqd.keep_after_training_state:
            TextBox("禁用双技能自动切换", setting_style)


def _draw_deck_warning_or_music(rqd: DeckRequest, assets: _DeckRecommendAssets) -> None:
    if rqd.recommend_type in {"bonus", "wl_bonus"}:
        TextBox(
            "友情提醒：控分前请核对加成和体力设置",
            TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(255, 50, 50)),
        )
        if rqd.recommend_type == "wl_bonus":
            TextBox(
                "WL仅支持自动组主队，支援队请自行配置",
                TextStyle(font=DEFAULT_FONT, size=26, color=(50, 50, 50)),
            )
        return
    if rqd.recommend_type == "mysekai" or rqd.music_compare or rqd.event_planner:
        return

    with HSplit().set_content_align("l").set_item_align("l").set_sep(16):
        with Frame().set_size((50, 50)):
            if rqd.music_id is not None and rqd.music_id != OMAKASE_MUSIC_ID:
                if rqd.music_diff and rqd.music_diff in DIFF_COLORS:
                    Spacer(w=50, h=50).set_bg(FillBg(fill=DIFF_COLORS[rqd.music_diff])).set_offset((6, 6))
                if assets.music_cover:
                    ImageBox(assets.music_cover, size=(50, 50))
            elif assets.music_cover:
                ImageBox(assets.music_cover, size=(50, 50), shadow=True)
        TextBox(
            rqd.music_title or "",
            TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(70, 70, 70)),
        )


def _draw_deck_strategy(rqd: DeckRequest) -> None:
    if rqd.recommend_type in {"bonus", "wl_bonus", "mysekai"}:
        return
    strategy_text = "  ".join(
        part
        for part in (
            format_skill_order_text(rqd.skill_order_choose_strategy),
            format_skill_reference_text(rqd.skill_reference_choose_strategy),
        )
        if part
    ).strip()
    if strategy_text:
        TextBox(
            strategy_text,
            TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(70, 70, 70)),
        )


async def _draw_deck_header(rqd: DeckRequest, assets: _DeckRecommendAssets) -> None:
    with (
        VSplit().set_content_align("lb").set_item_align("lb").set_sep(16).set_padding(16).set_bg(roundrect_bg(alpha=80))
    ):
        await _draw_deck_title_row(rqd, assets)
        await _draw_deck_settings(rqd)
        _draw_deck_warning_or_music(rqd, assets)
        _draw_deck_strategy(rqd)
        if rqd.is_max_deck:
            TextBox(
                "“顶配”为该服截止当前的全卡满养成配置(并非基于你的卡组计算)",
                TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(200, 75, 75)),
                use_real_line_count=True,
            )


_DECK_ROW_HEIGHT = 120
_DECK_VERTICAL_SEP = 12
_DECK_VALUE_OFFSET = 18
_DECK_SCORE_WIDTH = 112
_DECK_BONUS_WIDTH = 102
_DECK_SKILL_WIDTH = 92
_DECK_POWER_WIDTH = 100
_DECK_CARD_WIDTH = 96


def _draw_deck_compare_music_column(rqd: DeckRequest, assets: _DeckRecommendAssets, heading_style: TextStyle) -> None:
    with VSplit().set_content_align("c").set_item_align("c").set_sep(_DECK_VERTICAL_SEP).set_padding(8):
        TextBox("歌曲", heading_style).set_h(_DECK_ROW_HEIGHT // 2).set_content_align("c")
        Spacer(h=6)
        for deck in rqd.deck_data:
            with VSplit().set_content_align("c").set_item_align("c").set_sep(4).set_padding(0).set_h(_DECK_ROW_HEIGHT):
                with Frame().set_content_align("c"):
                    if deck.music_diff and deck.music_diff in DIFF_COLORS:
                        Spacer(w=64, h=64).set_bg(FillBg(fill=DIFF_COLORS[deck.music_diff])).set_offset((3, 3))
                    music_img = assets.compare_music_imgs.get(deck.music_cover_path) if deck.music_cover_path else None
                    if music_img:
                        ImageBox(music_img, size=(64, 64)).set_offset((-3, -3))

                title = deck.music_title or ""
                if not title and deck.music_id is not None:
                    title = f"Music {deck.music_id}"
                TextBox(
                    title,
                    TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=(70, 70, 70)),
                    line_count=2,
                    use_real_line_count=True,
                ).set_w(120).set_content_align("c")

                meta_parts = []
                if deck.music_id is not None:
                    meta_parts.append(str(deck.music_id))
                if deck.music_diff:
                    meta_parts.append(deck.music_diff.upper())
                if deck.music_query:
                    TextBox(
                        deck.music_query,
                        TextStyle(font=DEFAULT_FONT, size=11, color=(120, 120, 120)),
                        line_count=1,
                        use_real_line_count=True,
                    ).set_w(120).set_content_align("c")
                if meta_text := " / ".join(meta_parts):
                    TextBox(
                        meta_text,
                        TextStyle(font=DEFAULT_FONT, size=11, color=(120, 120, 120)),
                    ).set_w(120).set_content_align("c")


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


def _draw_deck_score_column(
    rqd: DeckRequest,
    heading_style: TextStyle,
    secondary_heading_style: TextStyle,
    value_style: TextStyle,
) -> None:
    target_score = rqd.target == "score"
    boost_bonus = BOOST_BONUS_DICT.get(rqd.boost or 0, 1) if rqd.boost is not None else 1
    with VSplit().set_content_align("c").set_item_align("c").set_sep(_DECK_VERTICAL_SEP).set_padding(8):
        text = _deck_score_name(rqd) + ("∇" if target_score else "")
        style = heading_style if target_score else secondary_heading_style
        with Frame().set_h(_DECK_ROW_HEIGHT // 2).set_content_align("c"):
            TextBox(text, style).set_w(_DECK_SCORE_WIDTH).set_content_align("c")
            if rqd.boost is not None and target_score:
                TextBox(
                    f"{rqd.boost}🔥(x{boost_bonus})",
                    TextStyle(font=DEFAULT_FONT, size=18, color=(75, 75, 75)),
                ).set_w(_DECK_SCORE_WIDTH).set_content_align("c").set_offset((0, 28))
        Spacer(h=6)
        algorithms = rqd.model_name or [""] * len(rqd.deck_data)
        for deck, algorithm in zip(rqd.deck_data, algorithms):
            with Frame().set_content_align("rb").set_w(_DECK_SCORE_WIDTH).set_h(_DECK_ROW_HEIGHT):
                algorithm_offset = 0
                if rqd.recommend_type in {"challenge", "challenge_all"}:
                    algorithm_offset = 20
                    delta = deck.challenge_score_delta or 0
                    color = (50, 150, 50) if delta > 0 else (150, 50, 50)
                    TextBox(
                        f"{delta:+d}",
                        TextStyle(font=DEFAULT_FONT, size=15, color=color),
                    ).set_w(_DECK_SCORE_WIDTH).set_content_align("c").set_offset((0, -8 - _DECK_VALUE_OFFSET * 2))
                TextBox(
                    format_algorithm_label(algorithm),
                    TextStyle(
                        font=DEFAULT_FONT,
                        size=algorithm_label_font_size(algorithm),
                        color=(125, 125, 125),
                    ),
                ).set_w(_DECK_SCORE_WIDTH).set_content_align("c").set_offset(
                    (0, -8 - _DECK_VALUE_OFFSET * 2 + algorithm_offset)
                )
                with Frame().set_content_align("c"):
                    TextBox(str(_deck_score(rqd, deck, target_score, boost_bonus)), value_style).set_w(
                        _DECK_SCORE_WIDTH
                    ).set_h(_DECK_ROW_HEIGHT).set_content_align("c").set_offset((0, -_DECK_VALUE_OFFSET))


def _deck_card_is_fixed(rqd: DeckRequest, card_id: int, character_id: int) -> bool:
    return bool(
        (rqd.fixed_cards_id and card_id in rqd.fixed_cards_id)
        or (rqd.fixed_characters_id and character_id in rqd.fixed_characters_id)
    )


def _story_read_color(value: bool | None) -> tuple[int, int, int, int]:
    if value is None:
        return (255, 255, 255, 255)
    return (50, 150, 50, 255) if value else (150, 50, 50, 255)


def _draw_deck_card(rqd: DeckRequest, assets: _DeckRecommendAssets, card) -> None:
    card_id = card.card_thumbnail.card_id
    card_key = (
        card_id,
        card.card_thumbnail.is_after_training,
        card.card_thumbnail.card_thumbnail_path,
    )
    event_bonus = card.event_bonus_rate
    show_event_bonus = event_bonus > 0
    with (
        VSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(4)
        .set_padding(0)
        .set_size((_DECK_CARD_WIDTH, _DECK_ROW_HEIGHT))
    ):
        with Frame().set_w(_DECK_CARD_WIDTH).set_content_align("c"):
            with Frame().set_content_align("rt"):
                CardFullThumbnailBox(assets.card_layers[card_key], size=(None, 80))
                fixed = _deck_card_is_fixed(rqd, card_id, card.chara_id)
                card_id_style = TextStyle(
                    font=DEFAULT_FONT,
                    size=10,
                    color=WHITE if fixed else (75, 75, 75),
                )
                card_id_bg = (200, 50, 50, 200) if fixed else (255, 255, 255, 200)
                TextBox(str(card_id), card_id_style).set_bg(RoundRectBg(card_id_bg, 2)).set_offset((-2, 0))
                if card.has_canvas_bonus:
                    ImageBox(assets.canvas_thumbnail, size=(11, 11)).set_offset((-32, 65))

        info_bg = RoundRectBg((255, 255, 255, 150), 2)
        with HSplit().set_w(_DECK_CARD_WIDTH).set_content_align("c").set_item_align("c").set_sep(3).set_padding(0):
            TextBox(
                f"SLv.{card.skill_level}",
                TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50)),
            ).set_bg(info_bg)
            TextBox(
                f"↑{format_skill_rate(card.skill_rate)}%",
                TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50)),
            ).set_bg(info_bg)

        with HSplit().set_w(_DECK_CARD_WIDTH).set_content_align("c").set_item_align("c").set_sep(3).set_padding(0):
            if show_event_bonus:
                event_bonus_str = f"+{event_bonus:.1f}%" if int(event_bonus) != event_bonus else f"+{int(event_bonus)}%"
                TextBox(
                    event_bonus_str,
                    TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50)),
                ).set_bg(info_bg)
            TextBox(
                "前" if show_event_bonus else "前篇",
                TextStyle(font=DEFAULT_FONT, size=12, color=_story_read_color(card.is_before_story)),
            ).set_bg(info_bg)
            TextBox(
                "后" if show_event_bonus else "后篇",
                TextStyle(font=DEFAULT_FONT, size=12, color=_story_read_color(card.is_after_story)),
            ).set_bg(info_bg)


def _draw_deck_cards_column(rqd: DeckRequest, assets: _DeckRecommendAssets, heading_style: TextStyle) -> None:
    with VSplit().set_content_align("c").set_item_align("c").set_sep(_DECK_VERTICAL_SEP).set_padding(8):
        TextBox("卡组", heading_style).set_h(_DECK_ROW_HEIGHT // 2).set_content_align("c")
        Spacer(h=6)
        for deck in rqd.deck_data:
            with HSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(0):
                for card in deck.card_data:
                    _draw_deck_card(rqd, assets, card)


def _draw_deck_bonus_column(rqd: DeckRequest, heading_style: TextStyle, value_style: TextStyle) -> None:
    with VSplit().set_content_align("c").set_item_align("c").set_sep(_DECK_VERTICAL_SEP).set_padding(8):
        TextBox("加成", heading_style).set_w(_DECK_BONUS_WIDTH).set_h(_DECK_ROW_HEIGHT // 2).set_content_align("c")
        Spacer(h=6)
        for deck in rqd.deck_data:
            if rqd.is_wl:
                bonus = f"{deck.event_bonus_rate:.1f}+{deck.support_deck_bonus_rate:.1f}%"
                total = f"{deck.event_bonus_rate + deck.support_deck_bonus_rate:.1f}%"
            else:
                bonus = None
                total = f"{deck.event_bonus_rate:.1f}%"
            with Frame().set_content_align("rb").set_w(_DECK_BONUS_WIDTH).set_h(_DECK_ROW_HEIGHT):
                if bonus is not None:
                    TextBox(
                        bonus,
                        TextStyle(font=DEFAULT_FONT, size=14, color=(150, 150, 150)),
                    ).set_w(_DECK_BONUS_WIDTH).set_content_align("c").set_offset((0, -6 - _DECK_VALUE_OFFSET * 2))
                with Frame().set_content_align("c"):
                    TextBox(total, value_style).set_w(_DECK_BONUS_WIDTH).set_h(_DECK_ROW_HEIGHT).set_content_align(
                        "c"
                    ).set_offset((0, -_DECK_VALUE_OFFSET))


def _draw_deck_skill_column(
    rqd: DeckRequest,
    heading_style: TextStyle,
    secondary_heading_style: TextStyle,
    value_style: TextStyle,
) -> None:
    target_skill = rqd.target == "skill"
    with VSplit().set_content_align("c").set_item_align("c").set_sep(_DECK_VERTICAL_SEP).set_padding(8):
        TextBox(
            "实效" + ("∇" if target_skill else ""),
            heading_style if target_skill else secondary_heading_style,
        ).set_w(_DECK_SKILL_WIDTH).set_h(_DECK_ROW_HEIGHT // 2).set_content_align("c")
        Spacer(h=6)
        for deck in rqd.deck_data:
            with Frame().set_content_align("rb").set_w(_DECK_SKILL_WIDTH).set_h(_DECK_ROW_HEIGHT):
                if rqd.multi_live_teammate_score_up is not None:
                    TextBox(
                        f"队友 {int(rqd.multi_live_teammate_score_up)}",
                        TextStyle(font=DEFAULT_FONT, size=14, color=(125, 125, 125)),
                    ).set_w(_DECK_SKILL_WIDTH).set_content_align("c").set_offset((0, -8 - _DECK_VALUE_OFFSET * 2))
                with Frame().set_content_align("c"):
                    TextBox(f"{deck.multi_live_score_up:.1f}%", value_style).set_w(_DECK_SKILL_WIDTH).set_h(
                        _DECK_ROW_HEIGHT
                    ).set_content_align("c").set_offset((0, -_DECK_VALUE_OFFSET))


def _draw_deck_power_column(
    rqd: DeckRequest,
    heading_style: TextStyle,
    secondary_heading_style: TextStyle,
    value_style: TextStyle,
) -> None:
    target_power = rqd.target == "total_power"
    with VSplit().set_content_align("c").set_item_align("c").set_sep(_DECK_VERTICAL_SEP).set_padding(8):
        TextBox(
            "综合力" + ("∇" if target_power else ""),
            heading_style if target_power else secondary_heading_style,
        ).set_w(_DECK_POWER_WIDTH).set_h(_DECK_ROW_HEIGHT // 2).set_content_align("c")
        Spacer(h=6)
        for deck in rqd.deck_data:
            with Frame().set_content_align("rb").set_w(_DECK_POWER_WIDTH).set_h(_DECK_ROW_HEIGHT):
                if rqd.multi_live_teammate_power is not None:
                    TextBox(
                        f"队友 {int(rqd.multi_live_teammate_power)}",
                        TextStyle(font=DEFAULT_FONT, size=14, color=(125, 125, 125)),
                    ).set_w(_DECK_POWER_WIDTH).set_content_align("c").set_offset((0, -8 - _DECK_VALUE_OFFSET * 2))
                with Frame().set_content_align("c"):
                    TextBox(str(deck.total_power), value_style).set_w(_DECK_POWER_WIDTH).set_h(
                        _DECK_ROW_HEIGHT
                    ).set_content_align("c").set_offset((0, -_DECK_VALUE_OFFSET))


def _draw_deck_results_table(rqd: DeckRequest, assets: _DeckRecommendAssets) -> None:
    with VSplit().set_content_align("c").set_item_align("c").set_sep(16).set_padding(16).set_bg(roundrect_bg(alpha=80)):
        if not rqd.deck_data:
            TextBox("未找到符合条件的卡组", TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(255, 50, 50)))
            return

        with HSplit().set_content_align("c").set_item_align("c").set_sep(16).set_padding(0):
            heading_style = TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=(0, 0, 0))
            secondary_heading_style = TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=(75, 75, 75))
            value_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(70, 70, 70))
            if rqd.music_compare:
                _draw_deck_compare_music_column(rqd, assets, secondary_heading_style)
            if rqd.recommend_type not in {"bonus", "wl_bonus"}:
                _draw_deck_score_column(rqd, heading_style, secondary_heading_style, value_style)
            _draw_deck_cards_column(rqd, assets, secondary_heading_style)
            if rqd.recommend_type not in {"challenge", "challenge_all", "no_event"}:
                _draw_deck_bonus_column(rqd, secondary_heading_style, value_style)
            if rqd.live_type in {"multi", "cheerful"}:
                _draw_deck_skill_column(rqd, heading_style, secondary_heading_style, value_style)
            if rqd.recommend_type not in {"bonus", "wl_bonus"}:
                _draw_deck_power_column(rqd, heading_style, secondary_heading_style, value_style)


def _draw_deck_notes(rqd: DeckRequest) -> None:
    note_text_width = 920 if rqd.music_compare else 760
    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
        tip_style = TextStyle(font=DEFAULT_FONT, size=16, color=(20, 20, 20))
        if rqd.recommend_type not in {"bonus", "wl_bonus"}:
            TextBox(
                "12星卡默认全满，34星及生日卡默认满级，oc的bfes花前技能活动组卡为平均值，挑战组卡为最大值",
                tip_style,
                use_real_line_count=True,
            ).set_w(note_text_width)
        TextBox(
            "功能移植并修改自33Kit https://3-3.dev/sekai/deck-recommend 算错概不负责",
            tip_style,
            use_real_line_count=True,
        ).set_w(note_text_width)
        if algorithm_runtime_text := build_algorithm_runtime_text(rqd.cost_times, rqd.wait_times):
            TextBox(
                algorithm_runtime_text,
                tip_style,
                use_real_line_count=True,
            ).set_w(note_text_width)


async def _build_deck_recommend_canvas(rqd: DeckRequest) -> Canvas:
    assets = await _load_deck_recommend_assets(rqd)
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16).set_padding(16):
            if not rqd.is_max_deck:
                await get_profile_card(rqd.profile.to_profile_card_request())

            with (
                VSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(16)
                .set_padding(16)
                .set_bg(roundrect_bg(alpha=80))
            ):
                await _draw_deck_header(rqd, assets)

                _draw_deck_results_table(rqd, assets)

                if rqd.event_planner:
                    draw_event_planner_block(rqd.event_planner, assets.planner_music_imgs)
                _draw_deck_notes(rqd)

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
