import asyncio
import logging
import math
import time

from PIL import Image, ImageDraw

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base import (
    ASSETS_BASE_DIR,
    BG_PADDING,
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    SEKAI_BLUE_BG,
    add_request_watermark,
    color_code_to_rgb,
    get_img_from_path,
    roundrect_bg,
)
from src.sekai.base.draw import CHARACTER_COLOR_CODE
from src.sekai.base.painter import get_font, get_text_size
from src.sekai.base.plot import (
    Canvas,
    FillBg,
    Frame,
    Grid,
    HSplit,
    ImageBg,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
)
from src.sekai.base.timezone import datetime_from_millis, request_now
from src.sekai.base.utils import (
    build_rendered_image_cache_key,
    get_composed_image_cached,
    put_composed_image_cache,
)
from src.sekai.profile.drawer import (
    get_card_full_thumbnail,
    get_profile_card,
)
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.sekai.skia_renderer.card_common import get_skia_payload_cached, put_skia_payload_cache
from src.settings import EXPORT_IMAGE_FORMAT, JPG_QUALITY

# 从 model.py 导入数据模型
from .model import (
    CardBoxDistribution,
    CardBoxRequest,
    CardDetailRequest,
    CardDistributionAttributeStat,
    CardDistributionCharacterStat,
    CardListRequest,
)

NON_LIMITED_SUPPLY_TYPES = {"", "normal", "非限定"}
TERM_LIMITED_SUPPLY_TYPES = {"期间限定", "WL限定", "联动限定"}
FES_LIMITED_SUPPLY_TYPES = {"Fes限定", "CFes限定", "BFes限定"}
CARD_BOX_GROUP_BY_ATTR = "attr"
CARD_BOX_ATTR_ORDER = ["cute", "cool", "pure", "happy", "mysterious"]
CARD_BOX_ATTR_LABELS = {
    "cute": "可爱",
    "cool": "帅气",
    "pure": "纯真",
    "happy": "快乐",
    "mysterious": "神秘",
    "unknown": "未分类",
}
CARD_BOX_ATTR_COLORS = {
    "cute": "#FF66AA",
    "cool": "#3D8BFF",
    "pure": "#49C878",
    "happy": "#FFB02E",
    "mysterious": "#9B72FF",
    "unknown": "#9AA0A6",
}
CARD_BOX_RARITY_STAR_PATH = "static_images/card/rare_star_normal.png"
CARD_BOX_BIRTHDAY_RARITY_PATH = "static_images/card/rare_birthday.png"
CARD_BOX_ATTR_LABEL_WIDTH = 90
CARD_BOX_ATTR_COUNT_MIN_WIDTH = 96
CARD_BOX_ATTR_COUNT_HORIZONTAL_PADDING = 40
CARD_BOX_ATTR_BAR_MIN_WIDTH = 170
CARD_BOX_PROGRESS_BUCKETS = [
    ("rarity_1", "1"),
    ("rarity_2", "2"),
    ("rarity_3", "3"),
    ("rarity_4", "4"),
    ("birthday", "生日"),
]

logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("card.draw.perf")


def is_non_limited_supply_type(value: str | None) -> bool:
    return (value or "").strip() in NON_LIMITED_SUPPLY_TYPES


def get_notice_dimensions(content_width: int, min_width: int = 520) -> tuple[int, int]:
    panel_width = max(min_width, content_width)
    text_width = max(240, panel_width - 120)
    return panel_width, text_width


def _build_card_list_cache_key(rqd: CardListRequest) -> str:
    request_payload = {
        "cards": [
            {
                "card_id": card.card_id,
                "release_at": card.release_at,
                "supply_type": card.supply_type,
                "prefix": card.prefix,
                "skill_icon_path": card.skill.skill_type_icon_path if card.skill else None,
                "thumbnail_info": [thumb.model_dump(mode="json") for thumb in (card.thumbnail_info or [])],
            }
            for card in rqd.cards
        ],
        "region": rqd.region,
        "title": rqd.title,
        "timezone": rqd.timezone,
        "background_img_path": rqd.background_img_path,
        "term_limited_icon_path": rqd.term_limited_icon_path,
        "fes_limited_icon_path": rqd.fes_limited_icon_path,
    }
    return build_rendered_image_cache_key("card_list", request_payload)


def _build_card_box_cache_key(rqd: CardBoxRequest) -> str:
    request_payload = {
        "cards": [
            {
                "card": {
                    "card_id": user_card.card.card_id,
                    "character_id": user_card.card.character_id,
                    "release_at": user_card.card.release_at,
                    "supply_type": user_card.card.supply_type,
                    "rare": user_card.card.rare,
                    "thumbnail_info": [
                        thumb.model_dump(mode="json") for thumb in (user_card.card.thumbnail_info or [])
                    ],
                    "is_after_training": user_card.card.is_after_training,
                },
                "has_card": user_card.has_card,
            }
            for user_card in rqd.cards
        ],
        "region": rqd.region,
        "title": rqd.title,
        "timezone": rqd.timezone,
        "show_id": rqd.show_id,
        "show_box": rqd.show_box,
        "unowned_only": rqd.unowned_only,
        "group_by": rqd.group_by,
        "distribution": rqd.distribution.model_dump(mode="json") if rqd.distribution else None,
        "background_img_path": rqd.background_img_path,
        "character_icon_paths": rqd.character_icon_paths,
        "character_color_codes": rqd.character_color_codes,
        "term_limited_icon_path": rqd.term_limited_icon_path,
        "fes_limited_icon_path": rqd.fes_limited_icon_path,
        "user_info": (
            {
                "id": rqd.user_info.id,
                "region": rqd.user_info.region,
                "nickname": rqd.user_info.nickname,
                "source": rqd.user_info.source,
                "update_time": rqd.user_info.update_time,
                "mode": rqd.user_info.mode,
                "is_hide_uid": rqd.user_info.is_hide_uid,
                "leader_image_path": rqd.user_info.leader_image_path,
                "has_frame": rqd.user_info.has_frame,
                "frame_path": rqd.user_info.frame_path,
            }
            if rqd.user_info is not None
            else None
        ),
    }
    return build_rendered_image_cache_key("card_box", request_payload)


def _safe_color(code: str | None, fallback: tuple[int, int, int, int] = (120, 140, 160, 255)):
    if not code:
        return fallback
    try:
        return color_code_to_rgb(code)
    except ValueError:
        return fallback


def _with_alpha(color: tuple[int, ...], alpha: int) -> tuple[int, int, int, int]:
    return int(color[0]), int(color[1]), int(color[2]), alpha


def _normalize_card_box_attr(attr: str | None) -> str:
    attr = (attr or "").strip().lower()
    if attr in CARD_BOX_ATTR_ORDER:
        return attr
    return "unknown"


def _card_box_attr_label(attr: str) -> str:
    return CARD_BOX_ATTR_LABELS.get(attr, attr)


def _card_box_attr_color(attr: str) -> str:
    return CARD_BOX_ATTR_COLORS.get(attr, CARD_BOX_ATTR_COLORS["unknown"])


def _stat_bar(width: int, height: int, ratio: float, color: tuple[int, int, int, int]) -> Frame:
    ratio = max(0.0, min(1.0, ratio or 0.0))
    frame = Frame().set_size((width, height))

    def draw(_widget, p):
        radius = max(1, height // 2)
        p.roundrect((0, 0), (width, height), (214, 218, 226, 180), radius)
        fill_width = int(width * ratio)
        if fill_width <= 0:
            return
        fill = _with_alpha(color, 255)
        p.roundrect((0, 0), (max(2, fill_width), height), fill, radius)

    return frame.add_draw_func(draw)


def _full_color_bar(width: int, height: int, color: tuple[int, int, int, int]) -> Frame:
    frame = Frame().set_size((width, height))

    def draw(_widget, p):
        radius = max(1, height // 2)
        p.roundrect((0, 0), (width, height), _with_alpha(color, 235), radius)
        if height >= 8:
            p.roundrect((1, 1), (max(1, width - 2), max(1, height // 3)), (255, 255, 255, 60), radius)

    return frame.add_draw_func(draw)


def _mini_vertical_bar(width: int, height: int, ratio: float, color: tuple[int, int, int, int]) -> Frame:
    ratio = max(0.0, min(1.0, ratio or 0.0))
    frame = Frame().set_size((width, height))

    def draw(_widget, p):
        radius = max(2, width // 2)
        p.roundrect((0, 0), (width, height), (214, 218, 226, 175), radius)
        fill_height = int(height * ratio)
        if fill_height <= 0:
            return
        p.roundrect((0, height - fill_height), (width, max(2, fill_height)), _with_alpha(color, 235), radius)
        if fill_height >= 6:
            p.roundrect(
                (1, height - fill_height + 1),
                (max(1, width - 2), max(1, fill_height // 3)),
                (255, 255, 255, 70),
                radius,
            )

    return frame.add_draw_func(draw)


def _circular_progress_avatar(
    avatar_img: Image.Image | None,
    size: int,
    ratio: float,
    color: tuple[int, int, int, int],
) -> Frame:
    ratio = max(0.0, min(1.0, ratio or 0.0))
    ring_width = max(4, size // 12)
    padding = ring_width + max(2, size // 28)
    inner_size = max(1, size - padding * 2)
    frame = Frame().set_size((size, size)).set_content_align("c")

    def draw(_widget, p):
        avatar = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(avatar)
        avatar_pos = (padding, padding)
        if avatar_img is not None:
            cropped_avatar = avatar_img.convert("RGBA").resize((inner_size, inner_size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (inner_size, inner_size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, inner_size - 1, inner_size - 1), fill=255)
            avatar.paste(cropped_avatar, avatar_pos, mask)
        else:
            d.ellipse(
                (
                    avatar_pos[0],
                    avatar_pos[1],
                    avatar_pos[0] + inner_size - 1,
                    avatar_pos[1] + inner_size - 1,
                ),
                fill=(218, 218, 218, 255),
            )
        box = (
            ring_width // 2,
            ring_width // 2,
            size - ring_width // 2 - 1,
            size - ring_width // 2 - 1,
        )
        d.arc(box, start=-90, end=270, fill=(183, 188, 198, 190), width=ring_width)
        if ratio > 0:
            d.arc(box, start=-90, end=-90 + 360 * ratio, fill=color, width=ring_width)
        p.paste(avatar, (0, 0))

    frame.add_draw_func(draw)
    return frame


def _vertical_stat_bar(width: int, height: int, ratio: float, color: tuple[int, int, int, int]) -> Frame:
    ratio = max(0.0, min(1.0, ratio or 0.0))
    frame = Frame().set_size((width, height)).set_bg(RoundRectBg((255, 255, 255, 145), max(2, width // 2)))

    def draw(_widget, p):
        fill_height = int(height * ratio)
        if fill_height <= 0:
            return
        p.roundrect((0, height - fill_height), (width, max(2, fill_height)), color, max(2, width // 2))

    return frame.add_draw_func(draw)


def _attribute_bar_chart_frame(
    width: int,
    height: int,
    stats: list[CardDistributionAttributeStat],
) -> Frame:
    frame = Frame().set_size((width, height)).set_bg(RoundRectBg((255, 255, 255, 120), 8))

    def draw(_widget, p):
        useful_stats = [stat for stat in stats if stat.count > 0]
        if not useful_stats:
            return
        slot_width = width / len(useful_stats)
        bar_width = max(10, int(slot_width * 0.48))
        for index, stat in enumerate(useful_stats):
            color = _safe_color(stat.color_code or _card_box_attr_color(stat.attr))
            x = int(index * slot_width + (slot_width - bar_width) / 2)
            track_height = height - 8
            p.roundrect((x, 4), (bar_width, track_height), (255, 255, 255, 150), max(3, bar_width // 3))
            fill_height = int(track_height * max(0.0, min(1.0, stat.bar_ratio or 0.0)))
            if fill_height <= 0:
                continue
            p.roundrect(
                (x, 4 + track_height - fill_height),
                (bar_width, max(2, fill_height)),
                color,
                max(3, bar_width // 3),
            )

    return frame.add_draw_func(draw)


def _stacked_character_bar(
    width: int,
    height: int,
    stats: list[CardDistributionCharacterStat],
    fallback_color: tuple[int, int, int, int],
) -> Frame:
    frame = Frame().set_size((width, height)).set_bg(RoundRectBg((255, 255, 255, 145), max(1, height // 2)))

    def draw(_widget, p):
        useful_stats = [stat for stat in stats if stat.bar_count > 0]
        total = sum(stat.bar_count for stat in useful_stats)
        if total <= 0:
            return
        x = 0
        for index, stat in enumerate(useful_stats):
            if index == len(useful_stats) - 1:
                seg_width = width - x
            else:
                seg_width = max(1, int(width * stat.bar_count / total))
            color = _safe_color(stat.color_code, fallback_color)
            p.rect((x, 0), (seg_width, height), color)
            x += seg_width
            if x >= width:
                break

    return frame.add_draw_func(draw)


def _stat_count_text(count: int, owned_count: int, owned_data: bool) -> str:
    if owned_data:
        return f"{owned_count}/{count}"
    return str(count)


def _card_box_attr_count_text(attr_stat: CardDistributionAttributeStat, owned_data: bool, unowned_only: bool) -> str:
    if unowned_only and owned_data:
        missing_count = max(0, attr_stat.count - attr_stat.owned_count)
        return f"{missing_count}/{attr_stat.count}"
    return _stat_count_text(attr_stat.count, attr_stat.owned_count, owned_data)


def _card_box_attr_count_width(count_texts: list[str] | tuple[str, ...] | None = None) -> int:
    if not count_texts:
        return CARD_BOX_ATTR_COUNT_MIN_WIDTH
    font = get_font(DEFAULT_BOLD_FONT, 18)
    measured_width = max((get_text_size(font, text)[0] for text in count_texts if text), default=0)
    return max(CARD_BOX_ATTR_COUNT_MIN_WIDTH, measured_width + CARD_BOX_ATTR_COUNT_HORIZONTAL_PADDING)


def _collection_ratio(stat: CardDistributionCharacterStat | CardDistributionAttributeStat, owned_data: bool) -> float:
    if stat.count <= 0:
        return 0.0
    if not owned_data:
        return 1.0
    return max(0.0, min(1.0, stat.owned_count / stat.count))


def _card_box_attr_content_width(
    attr_chara_cards: dict,
    best_height: int,
    sz: int,
    sep: int,
    count_texts: list[str] | tuple[str, ...] | None = None,
) -> int:
    def card_group_width(card_count: int) -> int:
        col_num = max(1, math.ceil(card_count / best_height))
        return sz * col_num + sep * (col_num - 1)

    def card_group_row_width(groups) -> int:
        widths = [card_group_width(len(group_cards)) for _, group_cards in groups]
        return sum(widths) + max(0, len(widths) - 1) * 4

    attr_header_min_width = (
        24
        + 8
        + CARD_BOX_ATTR_LABEL_WIDTH
        + 10
        + _card_box_attr_count_width(count_texts)
        + 10
        + CARD_BOX_ATTR_BAR_MIN_WIDTH
    )
    attr_row_widths = [
        card_group_row_width(groups)
        for attr, groups in attr_chara_cards.items()
        if attr in CARD_BOX_ATTR_ORDER and groups
    ]
    return 16 * 2 + max(max(attr_row_widths or [0]), attr_header_min_width)


def _rarity_progress_bucket(rare: str | None, supply_type: str | None = None) -> str | None:
    rare = (rare or "").strip().lower()
    supply_type = (supply_type or "").strip().lower()
    if rare == "rarity_birthday" or supply_type == "birthday":
        return "birthday"
    if rare in {"rarity_1", "rarity_2", "rarity_3", "rarity_4"}:
        return rare
    return None


def _single_character_progress(rqd: CardBoxRequest) -> dict | None:
    distribution = rqd.distribution or _fallback_card_box_distribution(rqd)
    if not distribution.owned_data:
        return None
    character_ids = {user_card.card.character_id for user_card in rqd.cards if user_card.card.character_id is not None}
    if len(character_ids) != 1:
        return None

    stats = {"total": {"owned": 0, "total": 0}}
    stats.update({bucket: {"owned": 0, "total": 0} for bucket, _ in CARD_BOX_PROGRESS_BUCKETS})
    for user_card in rqd.cards:
        bucket = _rarity_progress_bucket(user_card.card.rare, user_card.card.supply_type)
        if bucket is None:
            continue
        stats[bucket]["total"] += 1
        stats["total"]["total"] += 1
        if user_card.has_card:
            stats[bucket]["owned"] += 1
            stats["total"]["owned"] += 1
    if stats["total"]["total"] <= 0:
        return None
    visible_buckets = [(bucket, label) for bucket, label in CARD_BOX_PROGRESS_BUCKETS if stats[bucket]["total"] > 0]
    show_total = len(visible_buckets) == len(CARD_BOX_PROGRESS_BUCKETS)
    return {
        "character_id": next(iter(character_ids)),
        "stats": stats,
        "visible_buckets": visible_buckets,
        "show_total": show_total,
    }


def _character_stat_map(distribution: CardBoxDistribution | None) -> dict[int, CardDistributionCharacterStat]:
    if distribution is None:
        return {}
    return {stat.character_id: stat for stat in distribution.character_stats}


def _attribute_stat_map(distribution: CardBoxDistribution | None) -> dict[str, CardDistributionAttributeStat]:
    if distribution is None:
        return {}
    return {stat.attr: stat for stat in distribution.attribute_stats}


def _fallback_card_box_distribution(rqd: CardBoxRequest) -> CardBoxDistribution:
    owned_data = rqd.user_info is not None
    character_buckets: dict[int, dict[str, int]] = {}
    attribute_buckets: dict[str, dict[str, int]] = {
        attr: {"count": 0, "owned_count": 0} for attr in CARD_BOX_ATTR_ORDER
    }
    attribute_character_buckets: dict[str, dict[int, dict[str, int]]] = {}
    total_count = 0
    owned_count = 0

    for user_card in rqd.cards:
        total_count += 1
        has_card = bool(user_card.has_card)
        if has_card:
            owned_count += 1
        character_id = user_card.card.character_id
        if character_id is not None:
            bucket = character_buckets.setdefault(character_id, {"count": 0, "owned_count": 0})
            bucket["count"] += 1
            bucket["owned_count"] += int(has_card)

        attr = _normalize_card_box_attr(user_card.card.attr)
        bucket = attribute_buckets.setdefault(attr, {"count": 0, "owned_count": 0})
        bucket["count"] += 1
        bucket["owned_count"] += int(has_card)
        if character_id is not None:
            char_bucket = attribute_character_buckets.setdefault(attr, {}).setdefault(
                character_id, {"count": 0, "owned_count": 0}
            )
            char_bucket["count"] += 1
            char_bucket["owned_count"] += int(has_card)

    denominator = owned_count if owned_data else total_count

    character_stats: list[CardDistributionCharacterStat] = []
    max_character_bar_count = 0
    for character_id in sorted(character_buckets):
        bucket = character_buckets[character_id]
        bar_count = bucket["owned_count"] if owned_data else bucket["count"]
        max_character_bar_count = max(max_character_bar_count, bar_count)
        character_stats.append(
            CardDistributionCharacterStat(
                character_id=character_id,
                count=bucket["count"],
                owned_count=bucket["owned_count"],
                bar_count=bar_count,
                color_code=rqd.character_color_codes.get(character_id),
                icon_path=rqd.character_icon_paths.get(character_id),
            )
        )
    for stat in character_stats:
        stat.bar_ratio = stat.bar_count / max_character_bar_count if max_character_bar_count > 0 else 0.0
        stat.share = stat.bar_count / denominator if denominator > 0 else 0.0

    attribute_stats: list[CardDistributionAttributeStat] = []
    max_attribute_bar_count = 0
    for attr in [*CARD_BOX_ATTR_ORDER, *sorted(k for k in attribute_buckets if k not in CARD_BOX_ATTR_ORDER)]:
        bucket = attribute_buckets[attr]
        bar_count = bucket["owned_count"] if owned_data else bucket["count"]
        max_attribute_bar_count = max(max_attribute_bar_count, bar_count)
        group_character_stats: list[CardDistributionCharacterStat] = []
        group_max = 0
        for character_id in sorted(attribute_character_buckets.get(attr, {})):
            char_bucket = attribute_character_buckets[attr][character_id]
            char_bar_count = char_bucket["owned_count"] if owned_data else char_bucket["count"]
            group_max = max(group_max, char_bar_count)
            group_character_stats.append(
                CardDistributionCharacterStat(
                    character_id=character_id,
                    count=char_bucket["count"],
                    owned_count=char_bucket["owned_count"],
                    bar_count=char_bar_count,
                    color_code=rqd.character_color_codes.get(character_id),
                    icon_path=rqd.character_icon_paths.get(character_id),
                )
            )
        for stat in group_character_stats:
            stat.bar_ratio = stat.bar_count / group_max if group_max > 0 else 0.0
            stat.share = stat.bar_count / bar_count if bar_count > 0 else 0.0
        attribute_stats.append(
            CardDistributionAttributeStat(
                attr=attr,
                label=_card_box_attr_label(attr),
                count=bucket["count"],
                owned_count=bucket["owned_count"],
                bar_count=bar_count,
                color_code=_card_box_attr_color(attr),
                character_stats=group_character_stats,
            )
        )
    for stat in attribute_stats:
        stat.bar_ratio = stat.bar_count / max_attribute_bar_count if max_attribute_bar_count > 0 else 0.0
        stat.share = stat.bar_count / denominator if denominator > 0 else 0.0

    return CardBoxDistribution(
        total_count=total_count,
        owned_count=owned_count,
        owned_data=owned_data,
        max_character_bar_count=max_character_bar_count,
        max_attribute_bar_count=max_attribute_bar_count,
        character_stats=character_stats,
        attribute_stats=attribute_stats,
    )


# ========== 主要函数 ==========


async def _build_card_detail_canvas(
    rqd: CardDetailRequest, title: str | None = None, title_style: TextStyle = None, title_shadow: bool = False
) -> Canvas:
    """
    合成卡牌详情图片（构建 plot.py widget 树，供 Pillow 与 Skia 影子层共用）
    """
    card_info = rqd.card_info
    region = rqd.region
    power_info = rqd.card_info.power
    skill_info = rqd.card_info.skill
    sp_skill_info = rqd.card_info.special_skill_info
    # 获取图片（并行）
    _img_tasks = [
        *[get_img_from_path(ASSETS_BASE_DIR, path) for path in rqd.card_images_path],
        *[get_img_from_path(ASSETS_BASE_DIR, path) for path in rqd.costume_images_path],
        *[get_card_full_thumbnail(thumbnail) for thumbnail in rqd.card_info.thumbnail_info],
        get_img_from_path(ASSETS_BASE_DIR, rqd.character_icon_path),
        get_img_from_path(ASSETS_BASE_DIR, rqd.unit_logo_path),
        get_img_from_path(ASSETS_BASE_DIR, skill_info.skill_type_icon_path),
    ]
    if sp_skill_info:
        _img_tasks.append(get_img_from_path(ASSETS_BASE_DIR, sp_skill_info.skill_type_icon_path))
    _t0 = time.perf_counter()
    _img_results = await asyncio.gather(*_img_tasks)
    logger.debug(
        "[perf] compose_card_detail_image preload %d images: %.3fs",
        len(_img_tasks),
        time.perf_counter() - _t0,
    )

    _n_cards = len(rqd.card_images_path)
    _n_costumes = len(rqd.costume_images_path)
    _n_thumbs = len(rqd.card_info.thumbnail_info)
    _offset = 0
    card_images = list(_img_results[_offset : _offset + _n_cards])
    _offset += _n_cards
    costume_images = list(_img_results[_offset : _offset + _n_costumes])
    _offset += _n_costumes
    thumbnail_images = list(_img_results[_offset : _offset + _n_thumbs])
    _offset += _n_thumbs
    character_icon = _img_results[_offset]
    _offset += 1
    unit_logo = _img_results[_offset]
    _offset += 1
    skill_type_icon = _img_results[_offset]
    _offset += 1
    if sp_skill_info:
        sp_skill_type_icon = _img_results[_offset]

    # 处理事件横幅
    event_detail = None
    if rqd.event_info:
        event_detail = rqd.event_info

    # 处理卡池横幅
    gacha_detail = None
    if rqd.gacha_info:
        gacha_detail = rqd.gacha_info

    # 预加载关联活动/卡池图片（并行）
    _extra_tasks = {}
    if event_detail:
        _extra_tasks["event_banner"] = get_img_from_path(ASSETS_BASE_DIR, event_detail.event_banner_path)
        if event_detail.bonus_attr and rqd.event_attr_icon_path:
            _extra_tasks["event_attr"] = get_img_from_path(ASSETS_BASE_DIR, rqd.event_attr_icon_path)
        if event_detail.unit and rqd.event_unit_icon_path:
            _extra_tasks["event_unit"] = get_img_from_path(ASSETS_BASE_DIR, rqd.event_unit_icon_path)
        if event_detail.banner_cid and rqd.event_chara_icon_path:
            _extra_tasks["event_chara"] = get_img_from_path(ASSETS_BASE_DIR, rqd.event_chara_icon_path)
    if gacha_detail:
        _extra_tasks["gacha_banner"] = get_img_from_path(ASSETS_BASE_DIR, gacha_detail.gacha_banner_path)
    _extra_keys = list(_extra_tasks.keys())
    _extra_imgs = dict(zip(_extra_keys, await asyncio.gather(*_extra_tasks.values()))) if _extra_tasks else {}

    # 时间格式化
    release_time = datetime_from_millis(card_info.release_at, rqd.timezone)

    # 样式定义
    title_style_def = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(0, 0, 0))
    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))
    small_style = TextStyle(font=DEFAULT_FONT, size=18, color=(70, 70, 70))
    tip_style = TextStyle(font=DEFAULT_FONT, size=18, color=(0, 0, 0))  # noqa: F841

    # 使用传入的背景图片，如果没有则使用默认蓝色背景
    if rqd.background_image_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_image_path, on_missing="raise")
            bg = ImageBg(bg_img)
        except (FileNotFoundError, OSError, ValueError):
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with HSplit().set_sep(16).set_content_align("lt").set_item_align("lt"):
            # 左侧: 卡面+关联活动+关联卡池+提示
            with (
                VSplit()
                .set_padding(0)
                .set_sep(16)
                .set_content_align("lt")
                .set_item_align("lt")
                .set_item_bg(roundrect_bg(alpha=80))
            ):
                # 卡面
                with VSplit().set_padding(16).set_sep(8).set_content_align("lt").set_item_align("lt"):
                    for img in card_images:
                        ImageBox(img, size=(500, None))

                # 关联活动
                if event_detail:
                    with VSplit().set_padding(16).set_sep(12).set_content_align("lt").set_item_align("lt"):
                        with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                            TextBox("当期活动", label_style)
                            TextBox(f"【{event_detail.event_id}】{event_detail.event_name}", small_style).set_w(360)
                        with HSplit().set_padding(0).set_sep(8).set_content_align("lt").set_item_align("lt"):
                            ImageBox(
                                _extra_imgs["event_banner"],
                                size=(250, None),
                            )
                            with VSplit().set_content_align("c").set_item_align("c").set_sep(6):
                                TextBox(f"开始时间: {event_detail.start_at.strftime('%Y-%m-%d %H:%M')}", small_style)
                                TextBox(f"结束时间: {event_detail.end_at.strftime('%Y-%m-%d %H:%M')}", small_style)
                                Spacer(h=4)
                                with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                                    # 属性、团队、角色图标
                                    if event_detail.bonus_attr and rqd.event_attr_icon_path:
                                        ImageBox(
                                            _extra_imgs["event_attr"],
                                            size=(32, None),
                                        )
                                    if event_detail.unit and rqd.event_unit_icon_path:
                                        ImageBox(
                                            _extra_imgs["event_unit"],
                                            size=(32, None),
                                        )
                                    if event_detail.banner_cid and rqd.event_chara_icon_path:
                                        ImageBox(
                                            _extra_imgs["event_chara"],
                                            size=(32, None),
                                        )

                # 关联卡池
                if gacha_detail:
                    with VSplit().set_padding(16).set_sep(12).set_content_align("lt").set_item_align("lt"):
                        with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                            TextBox("当期卡池", label_style)
                            TextBox(f"【{gacha_detail.gacha_id}】{gacha_detail.gacha_name}", small_style).set_w(360)
                        with HSplit().set_padding(0).set_sep(8).set_content_align("lt").set_item_align("lt"):
                            ImageBox(
                                _extra_imgs["gacha_banner"],
                                size=(250, None),
                            )
                            with VSplit().set_content_align("c").set_item_align("c").set_sep(6):
                                TextBox(f"开始时间: {gacha_detail.start_at.strftime('%Y-%m-%d %H:%M')}", small_style)
                                TextBox(f"结束时间: {gacha_detail.end_at.strftime('%Y-%m-%d %H:%M')}", small_style)

            # 右侧: 标题+限定类型+综合力+技能+发布时间+缩略图+衣装
            w = 600
            with (
                VSplit()
                .set_padding(0)
                .set_sep(16)
                .set_content_align("lt")
                .set_item_align("lt")
                .set_item_bg(roundrect_bg(alpha=80))
            ):
                # 标题
                with HSplit().set_padding(16).set_sep(32).set_content_align("c").set_item_align("c").set_w(w):
                    ImageBox(unit_logo, size=(None, 64))
                    with VSplit().set_content_align("c").set_item_align("c").set_sep(12):
                        TextBox(card_info.prefix, title_style_def).set_w(w - 260).set_content_align("c")
                        with HSplit().set_content_align("c").set_item_align("c").set_sep(8):
                            ImageBox(character_icon, size=(None, 32))
                            TextBox(card_info.character_name, title_style_def)

                with (
                    VSplit()
                    .set_padding(16)
                    .set_sep(8)
                    .set_item_bg(roundrect_bg(alpha=80))
                    .set_content_align("l")
                    .set_item_align("l")
                ):
                    # 卡牌ID 限定类型
                    with HSplit().set_padding(16).set_sep(8).set_content_align("l").set_item_align("l"):
                        TextBox("ID", label_style)
                        TextBox(f"{card_info.card_id} ({region.upper()})", text_style)
                        Spacer(w=32)
                        TextBox("限定类型", label_style)
                        TextBox(card_info.supply_type, text_style)

                    # 综合力
                    with HSplit().set_padding(16).set_sep(8).set_content_align("lb").set_item_align("lb"):
                        TextBox("综合力", label_style)
                        TextBox(
                            f"{power_info.power_total} "
                            f"({power_info.power1}/{power_info.power2}/{power_info.power3}) "
                            "(满级0破无剧情)",
                            text_style,
                        )

                    # 技能
                    with VSplit().set_padding(16).set_sep(8).set_content_align("l").set_item_align("l"):
                        with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                            TextBox("技能", label_style)
                            if skill_type_icon:
                                ImageBox(skill_type_icon, size=(32, 32))
                            TextBox(skill_info.skill_name, text_style).set_w(w - 24 * 2 - 32 - 16)
                        TextBox(skill_info.skill_detail, text_style, use_real_line_count=True).set_w(w)
                        if skill_info.skill_detail_cn:
                            TextBox(
                                skill_info.skill_detail_cn.removesuffix("。"), text_style, use_real_line_count=True
                            ).set_w(w)

                    # 特训技能
                    if sp_skill_info:
                        with VSplit().set_padding(16).set_sep(8).set_content_align("l").set_item_align("l"):
                            with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                                TextBox("特训后技能", label_style)
                                if sp_skill_type_icon:
                                    ImageBox(sp_skill_type_icon, size=(32, 32))
                                TextBox(sp_skill_info.skill_name, text_style).set_w(w - 24 * 5 - 32 - 16)
                            TextBox(sp_skill_info.skill_detail, text_style, use_real_line_count=True).set_w(w)
                            if sp_skill_info.skill_detail_cn:
                                TextBox(
                                    sp_skill_info.skill_detail_cn.removesuffix("。"),
                                    text_style,
                                    use_real_line_count=True,
                                ).set_w(w)

                    # 发布时间
                    with HSplit().set_padding(16).set_sep(8).set_content_align("lb").set_item_align("lb"):
                        TextBox("发布时间", label_style)
                        TextBox(release_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)

                    # 缩略图
                    with HSplit().set_padding(16).set_sep(16).set_content_align("l").set_item_align("l"):
                        TextBox("缩略图", label_style)
                        for img in thumbnail_images:
                            ImageBox(img, size=(100, None))

                    # 衣装
                    if len(costume_images) > 0:
                        with HSplit().set_padding(16).set_sep(16).set_content_align("l").set_item_align("l"):
                            TextBox("衣装", label_style)
                            with Grid(col_count=5).set_sep(8, 8):
                                for img in costume_images:
                                    ImageBox(img, size=(80, None))

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_card_detail_image(
    rqd: CardDetailRequest, title: str | None = None, title_style: TextStyle = None, title_shadow: bool = False
):
    """合成卡牌详情图片（Pillow 路径）"""
    canvas = await _build_card_detail_canvas(rqd, title, title_style, title_shadow)
    return await canvas.get_img()


async def try_render_card_detail_payload(
    rqd: CardDetailRequest, title: str | None = None, title_style: TextStyle = None, title_shadow: bool = False
) -> EncodedImagePayload | None:
    """Skia 影子层路径；未启用或不可表达时返回 None 回退 Pillow。"""
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_card_detail_canvas(rqd, title, title_style, title_shadow))


async def compose_card_list_image(
    rqd: CardListRequest, title: str | None = None, title_style: TextStyle = None, title_shadow: bool = False
):
    """
    合成卡牌列表图片
    """
    cache_key = _build_card_list_cache_key(rqd)
    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        _perf_logger.info("card/list cache hit: cards=%d", len(rqd.cards))
        return cached

    _t_total = time.perf_counter()
    cards = rqd.cards
    region = rqd.region  # noqa: F841
    # 如果只有一张卡，调用详情函数

    async def get_card_list_thumbs(card):
        thumbnails = card.thumbnail_info or []
        if not thumbnails:
            return []
        if len(thumbnails) == 1:
            img = await get_card_full_thumbnail(thumbnails[0])
            return [img] if img is not None else []
        normal, after = await asyncio.gather(
            get_card_full_thumbnail(thumbnails[0]),
            get_card_full_thumbnail(thumbnails[1]),
        )
        return [img for img in (normal, after) if img is not None]

    _t0 = time.perf_counter()
    thumbs = await asyncio.gather(*[get_card_list_thumbs(card) for card in rqd.cards])
    _t_thumbs = time.perf_counter() - _t0

    # 并行获取所有缩略图
    card_and_thumbs = [(card, thumb_group) for card, thumb_group in zip(cards, thumbs) if thumb_group]

    # 按发布时间和ID排序
    card_and_thumbs.sort(key=lambda x: (x[0].release_at, x[0].card_id), reverse=True)

    # 样式定义
    name_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(0, 0, 0))
    id_style = TextStyle(font=DEFAULT_FONT, size=20, color=(0, 0, 0))
    leak_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(200, 0, 0))
    notice_label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(166, 90, 0))
    notice_text_style = TextStyle(font=DEFAULT_FONT, size=22, color=(98, 68, 0))

    # 使用传入的背景图片，如果没有则使用默认背景
    if rqd.background_img_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_img_path, on_missing="raise")
            bg = ImageBg(bg_img)
        except (FileNotFoundError, OSError, ValueError):
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG

    skill_icon_paths = sorted(
        {
            card.skill.skill_type_icon_path
            for card, _ in card_and_thumbs
            if card.skill and card.skill.skill_type_icon_path
        }
    )

    preload_tasks: dict[str, asyncio.Future] = {}
    if rqd.term_limited_icon_path:
        preload_tasks["term_img"] = get_img_from_path(ASSETS_BASE_DIR, rqd.term_limited_icon_path)
    if rqd.fes_limited_icon_path:
        preload_tasks["fes_img"] = get_img_from_path(ASSETS_BASE_DIR, rqd.fes_limited_icon_path)
    for path in skill_icon_paths:
        preload_tasks[f"skill::{path}"] = get_img_from_path(ASSETS_BASE_DIR, path)

    _t0 = time.perf_counter()
    preloaded: dict[str, object] = {}
    if preload_tasks:
        preload_keys = list(preload_tasks.keys())
        preload_results = await asyncio.gather(*preload_tasks.values(), return_exceptions=True)
        preloaded = dict(zip(preload_keys, preload_results))
    _t_preload = time.perf_counter() - _t0

    term_img = preloaded.get("term_img")
    if isinstance(term_img, BaseException):
        term_img = None
    fes_img = preloaded.get("fes_img")
    if isinstance(fes_img, BaseException):
        fes_img = None
    skill_icon_cache = {
        path: img
        for path in skill_icon_paths
        if (img := preloaded.get(f"skill::{path}")) is not None and not isinstance(img, BaseException)
    }

    list_panel_width, list_notice_text_width = get_notice_dimensions(300 * 3 + 16 * 2, min_width=300 * 3 + 16 * 2)

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(16).set_content_align("lt").set_item_align("lt"):
            now = request_now(rqd.timezone)
            if rqd.title:
                with (
                    HSplit()
                    .set_bg(roundrect_bg(fill=(255, 246, 219, 220)))
                    .set_padding(14)
                    .set_sep(12)
                    .set_content_align("l")
                    .set_item_align("c")
                    .set_w(list_panel_width)
                ):
                    TextBox("提示", notice_label_style)
                    TextBox(rqd.title, notice_text_style, use_real_line_count=True).set_w(list_notice_text_width)
            # 卡牌网格
            with Grid(col_count=3).set_bg(roundrect_bg(alpha=80)).set_padding(16):
                for i, (card, thumb_group) in enumerate(card_and_thumbs):
                    # 背景设置 - 确保毛玻璃效果启用
                    if not is_non_limited_supply_type(card.supply_type):
                        # 限定卡牌：使用淡黄色背景，确保有足够的透明度
                        bg = roundrect_bg(fill=(255, 250, 220, 200), blur_glass=True)
                    else:
                        # 普通卡牌：使用默认的半透明白色背景
                        bg = roundrect_bg(alpha=80)  # 默认已经是半透明+毛玻璃效果

                    with Frame().set_content_align("lb").set_bg(bg):
                        # 检查是否为未来卡牌
                        release_time = datetime_from_millis(card.release_at, rqd.timezone)
                        if release_time > now:
                            TextBox("未上线", leak_style).set_offset((4, -4))

                        # 技能图标区域
                        with Frame().set_content_align("rb"):
                            # 根据skill_type自动匹配技能图标
                            if card.skill and card.skill.skill_type:
                                skill_icon_path = card.skill.skill_type_icon_path
                                skill_img = skill_icon_cache.get(skill_icon_path)
                                if skill_img is not None:
                                    ImageBox(skill_img, image_size_mode="fit").set_w(32).set_margin(8)

                            # 卡牌信息区域
                            with VSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(8):
                                GW = 300
                                with HSplit().set_content_align("c").set_w(GW).set_padding(8).set_sep(16):
                                    supply_name = card.supply_type or ""
                                    for thumb in thumb_group:
                                        with Frame().set_content_align("rt"):
                                            ImageBox(thumb, size=(100, 100), image_size_mode="fill", shadow=True)
                                            limited_icon_width = 75
                                            if supply_name in TERM_LIMITED_SUPPLY_TYPES:
                                                if term_img:
                                                    ImageBox(term_img, size=(limited_icon_width, None))
                                            elif supply_name in FES_LIMITED_SUPPLY_TYPES:
                                                if fes_img:
                                                    ImageBox(fes_img, size=(limited_icon_width, None))

                                # 卡牌名称
                                name_text = card.prefix
                                TextBox(name_text, name_style).set_w(GW).set_content_align("c")

                                # ID和限定类型
                                id_text = f"ID:{card.card_id}"
                                if not is_non_limited_supply_type(card.supply_type):
                                    id_text += f"【{card.supply_type}】"
                                TextBox(id_text, id_style).set_w(GW).set_content_align("c")

    add_request_watermark(canvas, rqd)
    _t0 = time.perf_counter()
    image = await canvas.get_img()
    _t_render = time.perf_counter() - _t0
    put_composed_image_cache(cache_key, image)
    _perf_logger.info(
        "card/list total: %.3fs (thumbs=%.3fs, preload=%.3fs, render=%.3fs, "
        "cards=%d, rendered=%d, skills=%d, image=%dx%d)",
        time.perf_counter() - _t_total,
        _t_thumbs,
        _t_preload,
        _t_render,
        len(rqd.cards),
        len(card_and_thumbs),
        len(skill_icon_paths),
        image.width,
        image.height,
    )
    return image


async def _build_box_canvas(rqd: CardBoxRequest) -> Canvas:
    """构建卡牌一览的 widget 树（按角色分类的卡牌收集册）。

    两个后端共用：Pillow 走 :func:`compose_box_image`（canvas.get_img），Skia 走
    :func:`try_render_box_payload`（IRPainter 影子层）。
    """
    _t_total = time.perf_counter()
    cards = rqd.cards
    region = rqd.region  # noqa: F841
    user_info = rqd.user_info
    show_id = rqd.show_id
    show_box = rqd.show_box
    unowned_only = rqd.unowned_only
    group_by_attr = (rqd.group_by or "").strip().lower() == CARD_BOX_GROUP_BY_ATTR
    distribution = rqd.distribution or _fallback_card_box_distribution(rqd)
    character_stats = _character_stat_map(distribution)
    single_progress = _single_character_progress(rqd)

    async def get_box_thumb(card):
        thumbnails = card.card.thumbnail_info or []
        if not thumbnails:
            return None
        if len(thumbnails) == 1:
            return await get_card_full_thumbnail(thumbnails[0])
        if card.card.is_after_training:
            return await get_card_full_thumbnail(thumbnails[1])
        return await get_card_full_thumbnail(thumbnails[0])

    _t0 = time.perf_counter()
    thumbs = await asyncio.gather(*[get_box_thumb(card) for card in cards])
    _t_thumbs = time.perf_counter() - _t0

    card_records = []
    for card, img in zip(cards, thumbs):
        if not img:
            continue
        card_data = {
            **card.model_dump(),
            "img": img,
            "has": card.has_card,  # 恢复拥有状态判断
        }
        if show_box and not card_data["has"]:
            continue
        if unowned_only and card_data["has"]:
            continue
        card_records.append(card_data)

    def sort_card_records(group_cards):
        group_cards.sort(key=lambda x: (x["card"]["rare"], x["card"]["release_at"], x["card"]["card_id"]))
        return group_cards

    # 按角色收集卡牌
    chara_cards_by_id = {}
    attr_chara_cards_by_attr = {}
    for card_data in card_records:
        chara_id = card_data["card"]["character_id"] or 0
        attr = _normalize_card_box_attr(card_data["card"].get("attr"))
        chara_cards_by_id.setdefault(chara_id, []).append(card_data)
        attr_chara_cards_by_attr.setdefault(attr, {}).setdefault(chara_id, []).append(card_data)

    chara_cards = sorted(
        (chara_id, sort_card_records(group_cards)) for chara_id, group_cards in chara_cards_by_id.items()
    )
    attr_chara_cards = {}
    for attr, attr_groups in attr_chara_cards_by_attr.items():
        attr_chara_cards[attr] = sorted(
            (chara_id, sort_card_records(group_cards)) for chara_id, group_cards in attr_groups.items()
        )

    # 计算最佳高度限制以优化布局
    max_card_num = max([len(cards) for _, cards in chara_cards]) if chara_cards else 0
    best_height, best_value = 10000, 1e9
    for i in range(1, max_card_num + 1):
        # 计算优化目标：max(h,w)越小越好，空白越少越好
        max_height = 0
        total_width = 0
        for _, cards in chara_cards:
            max_height = max(max_height, min(len(cards), i))
        total, space = 0, 0
        for _, cards in chara_cards:
            width = math.ceil(len(cards) / i)
            total_width += width
            total += max_height * width
            space += max_height * width - len(cards)
        # value = max(total_width, max_height) * total / (total - space)
        value = max(total_width, max_height * 0.5) if total_width > 9 else max(total_width * 0.5, max_height)
        if value < best_value:
            best_height, best_value = i, value

    # 计算总宽度并决定绘制卡牌的大小
    total_width = 0
    for _, cards in chara_cards:
        width = max(1, math.ceil(len(cards) / best_height))
        total_width += width
    area = total_width * (best_height + 4)

    start_area, start_sz, start_sep = 9 * 5, 100, 8
    end_area, end_sz, end_sep = 26 * 50, 48, 4
    interp = min(1.0, max(0.0, (area - start_area) / (end_area - start_area)))
    sep = int(start_sep + (end_sep - start_sep) * interp)
    sz = int(start_sz + (end_sz - start_sz) * interp)

    def card_group_width(card_count: int) -> int:
        col_num = max(1, math.ceil(card_count / best_height))
        return sz * col_num + sep * (col_num - 1)

    def card_group_row_width(groups) -> int:
        widths = [card_group_width(len(group_cards)) for _, group_cards in groups]
        return sum(widths) + max(0, len(widths) - 1) * 4

    attr_count_texts = [
        _card_box_attr_count_text(stat, distribution.owned_data, unowned_only)
        for stat in distribution.attribute_stats
        if stat.count > 0
    ]
    attr_count_width = _card_box_attr_count_width(attr_count_texts)
    if group_by_attr:
        box_content_width = _card_box_attr_content_width(attr_chara_cards, best_height, sz, sep, attr_count_texts)
    else:
        box_content_width = 16 * 2
        if chara_cards:
            box_content_width += card_group_row_width(chara_cards)
    panel_width, panel_text_width = get_notice_dimensions(box_content_width)

    preload_tasks: dict[str, asyncio.Future] = {}
    if rqd.term_limited_icon_path:
        preload_tasks["term_img"] = get_img_from_path(ASSETS_BASE_DIR, rqd.term_limited_icon_path)
    if rqd.fes_limited_icon_path:
        preload_tasks["fes_img"] = get_img_from_path(ASSETS_BASE_DIR, rqd.fes_limited_icon_path)
    if rqd.character_icon_paths:
        for chara_id, path in rqd.character_icon_paths.items():
            preload_tasks[f"chara::{chara_id}"] = get_img_from_path(ASSETS_BASE_DIR, path)
    for attr_stat in distribution.attribute_stats:
        if attr_stat.attr_icon_path:
            preload_tasks[f"attr::{attr_stat.attr}"] = get_img_from_path(ASSETS_BASE_DIR, attr_stat.attr_icon_path)
    if single_progress is not None:
        preload_tasks["rarity_star"] = get_img_from_path(ASSETS_BASE_DIR, CARD_BOX_RARITY_STAR_PATH)
        preload_tasks["rarity_birthday"] = get_img_from_path(ASSETS_BASE_DIR, CARD_BOX_BIRTHDAY_RARITY_PATH)

    _t0 = time.perf_counter()
    preloaded: dict[str, object] = {}
    if preload_tasks:
        preload_keys = list(preload_tasks.keys())
        preload_results = await asyncio.gather(*preload_tasks.values(), return_exceptions=True)
        preloaded = dict(zip(preload_keys, preload_results))
    _t_preload = time.perf_counter() - _t0

    term_img = preloaded.get("term_img")
    if isinstance(term_img, BaseException):
        term_img = None
    fes_img = preloaded.get("fes_img")
    if isinstance(fes_img, BaseException):
        fes_img = None

    chara_icons = {}
    if rqd.character_icon_paths:
        for chara_id in rqd.character_icon_paths:
            img = preloaded.get(f"chara::{chara_id}")
            if img is not None and not isinstance(img, BaseException):
                chara_icons[chara_id] = img
    attr_icons = {}
    for attr_stat in distribution.attribute_stats:
        img = preloaded.get(f"attr::{attr_stat.attr}")
        if img is not None and not isinstance(img, BaseException):
            attr_icons[attr_stat.attr] = img
    rarity_star_img = preloaded.get("rarity_star")
    if isinstance(rarity_star_img, BaseException):
        rarity_star_img = None
    birthday_rarity_img = preloaded.get("rarity_birthday")
    if isinstance(birthday_rarity_img, BaseException):
        birthday_rarity_img = None

    # 绘制单张卡
    def draw_card(card_data):
        # 卡图与卡号 ID 必须包裹在同一个容器里，否则 show_id 为真时 ID 文本会被注册成 Grid
        # 的独立单元，导致每张卡占两格、列数与整体宽度翻倍（触发 watermark 的尺寸越界报错）。
        with VSplit().set_content_align("rt").set_sep(0):
            with Frame().set_content_align("rt"):
                ImageBox(card_data["img"], size=(sz, sz))

                # 限定类型图标
                supply_name = card_data["card"].get("supply_type", "")
                limited_icon_width = int(sz * 0.75)
                if supply_name in TERM_LIMITED_SUPPLY_TYPES:
                    if term_img:
                        ImageBox(term_img, size=(limited_icon_width, None))
                elif supply_name in FES_LIMITED_SUPPLY_TYPES:
                    if fes_img:
                        ImageBox(fes_img, size=(limited_icon_width, None))

                # 如果用户没有此卡牌，添加遮罩
                if not card_data["has"] and user_info:
                    Spacer(w=sz, h=sz).set_bg(RoundRectBg(fill=(0, 0, 0, 120), radius=2))

            if show_id:
                TextBox(
                    f"{card_data['card']['card_id']}",
                    TextStyle(font=DEFAULT_FONT, size=12, color=(0, 0, 0)),
                ).set_w(sz)

    profile_card = None
    if user_info:
        profile_card = await get_profile_card(user_info.to_profile_card_request())
        panel_width = max(panel_width, profile_card._get_self_size()[0])
        panel_text_width = max(240, panel_width - 120)

    # 使用传入的背景图片，如果没有则使用默认背景
    if rqd.background_img_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_img_path, on_missing="raise")
            bg = ImageBg(bg_img)
        except (FileNotFoundError, OSError, ValueError):
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG

    def get_character_color(chara_id: int):
        color_code = rqd.character_color_codes.get(chara_id) or CHARACTER_COLOR_CODE.get(chara_id, "#7C8DA5")
        return _safe_color(color_code)

    def draw_single_character_progress_panel(progress: dict):
        chara_id = progress["character_id"]
        stats = progress["stats"]
        color = get_character_color(chara_id)
        avatar_size = 56
        content_width = panel_width - 32
        detail_width = max(260, content_width - avatar_size - 16)
        cell_sep = 8
        visible_buckets = progress.get("visible_buckets") or CARD_BOX_PROGRESS_BUCKETS
        bucket_count = len(visible_buckets)
        cell_width = max(54, (detail_width - cell_sep * (bucket_count - 1)) // bucket_count)
        label_height = 24
        total = stats["total"]
        bucket_icons = {
            "rarity_1": 1,
            "rarity_2": 2,
            "rarity_3": 3,
            "rarity_4": 4,
        }

        def draw_bucket_label(bucket: str, label: str):
            with Frame().set_w(cell_width).set_h(label_height).set_content_align("c"):
                if bucket in bucket_icons and rarity_star_img is not None:
                    icon_size = max(10, min(14, (cell_width - 4) // bucket_icons[bucket]))
                    with HSplit().set_content_align("c").set_item_align("c").set_sep(0).set_w(cell_width):
                        for _ in range(bucket_icons[bucket]):
                            ImageBox(rarity_star_img, size=(icon_size, icon_size))
                    return
                if bucket == "birthday" and birthday_rarity_img is not None:
                    icon_size = max(16, min(22, cell_width // 2))
                    with HSplit().set_content_align("c").set_item_align("c").set_w(cell_width):
                        ImageBox(birthday_rarity_img, size=(icon_size, icon_size))
                    return
                TextBox(
                    label,
                    TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=(68, 76, 88)),
                ).set_w(cell_width).set_content_align("c")

        with (
            HSplit()
            .set_bg(roundrect_bg(alpha=80))
            .set_content_align("l")
            .set_item_align("c")
            .set_padding(16)
            .set_sep(16)
            .set_w(panel_width)
        ):
            chara_icon = chara_icons.get(chara_id)
            if chara_icon is not None:
                ImageBox(chara_icon, size=(avatar_size, avatar_size))
            else:
                Spacer(w=avatar_size, h=avatar_size).set_bg(RoundRectBg(_with_alpha(color, 160), avatar_size // 2))
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_w(detail_width):
                with HSplit().set_content_align("l").set_item_align("c").set_sep(10).set_w(detail_width):
                    TextBox(
                        "收集进度",
                        TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(45, 52, 62)),
                    )
                    if progress.get("show_total", True):
                        TextBox(
                            f"全卡 {total['owned']}/{total['total']}",
                            TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(68, 76, 88)),
                        )
                with HSplit().set_content_align("lt").set_item_align("lt").set_sep(cell_sep):
                    for bucket, label in visible_buckets:
                        item = stats[bucket]
                        ratio = item["owned"] / item["total"] if item["total"] > 0 else 0.0
                        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4).set_w(cell_width):
                            draw_bucket_label(bucket, label)
                            TextBox(
                                f"{item['owned']}/{item['total']}",
                                TextStyle(font=DEFAULT_FONT, size=12, color=(68, 76, 88)),
                            ).set_w(cell_width).set_content_align("c")
                            _stat_bar(cell_width, 8, ratio, color)

    def draw_character_column(
        chara_id: int,
        group_cards,
        height_limit: int,
        stat: CardDistributionCharacterStat | None = None,
    ):
        chara_icon = chara_icons.get(chara_id)
        color = get_character_color(chara_id)
        col_num = max(1, len(range(0, len(group_cards), height_limit)))
        row_num = max(1, min(height_limit, len(group_cards)))
        group_width = sz * col_num + sep * (col_num - 1)
        stat = stat or character_stats.get(chara_id)
        if stat is None:
            count_value = len(group_cards)
        elif unowned_only or show_box:
            count_value = len(group_cards)
        elif distribution.owned_data:
            count_value = stat.owned_count
        else:
            count_value = stat.count
        count_text = str(count_value)
        progress_ratio = _collection_ratio(stat, distribution.owned_data) if stat else 1.0
        with VSplit().set_content_align("t").set_item_align("c").set_sep(3):
            if single_progress is None:
                TextBox(
                    count_text,
                    TextStyle(font=DEFAULT_BOLD_FONT, size=max(11, int(sz * 0.2)), color=(45, 52, 62)),
                ).set_w(group_width).set_content_align("c")
            _circular_progress_avatar(chara_icon, sz, progress_ratio, color)
            Spacer(w=group_width, h=max(4, sep)).set_bg(FillBg(_with_alpha(color, 235)))
            with (
                Grid(row_count=row_num, vertical=row_num > col_num)
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(sep, sep)
            ):
                for card_data in group_cards:
                    draw_card(card_data)

    def attribute_progress_values(attr_stat: CardDistributionAttributeStat):
        color = _safe_color(attr_stat.color_code or _card_box_attr_color(attr_stat.attr))
        if unowned_only and distribution.owned_data:
            missing_count = max(0, attr_stat.count - attr_stat.owned_count)
            count_text = _card_box_attr_count_text(attr_stat, distribution.owned_data, unowned_only)
            progress_ratio = missing_count / attr_stat.count if attr_stat.count > 0 else 0.0
        else:
            count_text = _card_box_attr_count_text(attr_stat, distribution.owned_data, unowned_only)
            progress_ratio = _collection_ratio(attr_stat, distribution.owned_data)
        return count_text, progress_ratio, color

    def draw_attribute_header(attr_stat: CardDistributionAttributeStat, content_width: int):
        count_text, progress_ratio, color = attribute_progress_values(attr_stat)
        label_width = CARD_BOX_ATTR_LABEL_WIDTH
        count_width = attr_count_width
        fixed_width = 24 + 8 + label_width + 10 + count_width + 10
        bar_width = max(120, min(260, content_width - fixed_width))
        with HSplit().set_content_align("l").set_item_align("c").set_sep(8).set_w(content_width):
            attr_icon = attr_icons.get(attr_stat.attr)
            if attr_icon is not None:
                ImageBox(attr_icon, size=(24, 24))
            else:
                Spacer(w=8, h=22).set_bg(RoundRectBg(color, 4))
            TextBox(
                attr_stat.label or _card_box_attr_label(attr_stat.attr),
                TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(45, 52, 62)),
                overflow="shrink",
            ).set_w(label_width)
            TextBox(
                count_text,
                TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(0, 0, 0)),
            ).set_w(count_width).set_content_align("r")
            _stat_bar(bar_width, 12, progress_ratio, color)

    def draw_normal_card_box_grid():
        with (
            HSplit()
            .set_bg(roundrect_bg(alpha=80))
            .set_content_align("lt")
            .set_item_align("lt")
            .set_padding(16)
            .set_sep(4)
            .set_w(panel_width)
        ):
            for chara_id, group_cards in chara_cards:
                draw_character_column(chara_id, group_cards, best_height)

    def draw_attribute_card_box_grid():
        attr_panel_width = max(360, box_content_width)
        attr_content_width = max(240, attr_panel_width - 32)
        ordered_attr_stats = [stat for stat in distribution.attribute_stats if stat.count > 0]
        if not ordered_attr_stats:
            ordered_attr_stats = [
                CardDistributionAttributeStat(
                    attr=attr,
                    label=_card_box_attr_label(attr),
                    count=sum(len(group_cards) for _, group_cards in attr_chara_cards.get(attr, [])),
                    color_code=_card_box_attr_color(attr),
                )
                for attr in CARD_BOX_ATTR_ORDER
                if attr in attr_chara_cards
            ]
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(12):
            for attr_stat in ordered_attr_stats:
                attr = attr_stat.attr
                group_cards_by_chara = attr_chara_cards.get(attr, [])
                if not group_cards_by_chara:
                    continue
                attr_color = _safe_color(attr_stat.color_code or _card_box_attr_color(attr))
                attr_character_stats = {stat.character_id: stat for stat in attr_stat.character_stats}
                with (
                    HSplit()
                    .set_bg(
                        roundrect_bg(
                            fill=_with_alpha(attr_color, 38),
                            radius=10,
                            blur_glass_kwargs={"shadow_alpha": 0.18},
                        )
                    )
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_padding(16)
                    .set_sep(8)
                    .set_w(attr_panel_width)
                ):
                    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_w(attr_content_width):
                        draw_attribute_header(attr_stat, attr_content_width)
                        with HSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                            for chara_id, group_cards in group_cards_by_chara:
                                draw_character_column(
                                    chara_id,
                                    group_cards,
                                    best_height,
                                    attr_character_stats.get(chara_id),
                                )

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if rqd.title:
                with (
                    HSplit()
                    .set_bg(roundrect_bg(fill=(255, 246, 219, 220)))
                    .set_padding(14)
                    .set_sep(12)
                    .set_content_align("l")
                    .set_item_align("c")
                    .set_w(panel_width)
                ):
                    TextBox("提示", TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(166, 90, 0)))
                    TextBox(
                        rqd.title,
                        TextStyle(font=DEFAULT_FONT, size=22, color=(98, 68, 0)),
                        use_real_line_count=True,
                    ).set_w(panel_text_width)
            if profile_card:
                with HSplit().set_content_align("l").set_item_align("l").set_w(panel_width) as profile_panel:
                    profile_panel.add_item(profile_card)
            if single_progress is not None:
                draw_single_character_progress_panel(single_progress)
            # 卡牌网格
            if group_by_attr:
                draw_attribute_card_box_grid()
            else:
                draw_normal_card_box_grid()

    add_request_watermark(canvas, rqd)
    _perf_logger.info(
        "card/box build: %.3fs (thumbs=%.3fs, preload=%.3fs, cards=%d, visible=%d, groups=%d)",
        time.perf_counter() - _t_total,
        _t_thumbs,
        _t_preload,
        len(rqd.cards),
        sum(len(group_cards) for _, group_cards in chara_cards),
        len(chara_cards),
    )
    return canvas


async def compose_box_image(rqd: CardBoxRequest):
    """合成卡牌一览图片（Pillow 路径）。"""
    cache_key = _build_card_box_cache_key(rqd)
    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        _perf_logger.info("card/box cache hit: cards=%d", len(rqd.cards))
        return cached
    canvas = await _build_box_canvas(rqd)
    _t0 = time.perf_counter()
    image = await canvas.get_img()
    _perf_logger.info(
        "card/box pillow render: %.3fs (cards=%d, image=%dx%d)",
        time.perf_counter() - _t0,
        len(rqd.cards),
        image.width,
        image.height,
    )
    put_composed_image_cache(cache_key, image)
    return image


async def try_render_box_payload(rqd: CardBoxRequest) -> EncodedImagePayload | None:
    """Skia 路径：同一棵 widget 树经 IRPainter 渲染（user_info profile card、收集统计、
    属性分组全部随 widget 树自然覆盖）。取代早期为逐像素对齐手写的 card_render box 场景
    构建器。不可用时返回 None 回退 Pillow。"""
    if not skia_plot_enabled():
        return None
    cache_key = f"{_build_card_box_cache_key(rqd)}|skia|{EXPORT_IMAGE_FORMAT}|{JPG_QUALITY}"
    cached = get_skia_payload_cached(cache_key)
    if cached is not None:
        return cached
    canvas = await _build_box_canvas(rqd)
    _t0 = time.perf_counter()
    payload = await render_canvas_payload(canvas)
    if payload is not None:
        _perf_logger.info(
            "card/box backend=skia render: %.3fs (cards=%d, image=%dx%d)",
            time.perf_counter() - _t0,
            len(rqd.cards),
            payload.image_width,
            payload.image_height,
        )
        put_skia_payload_cache(cache_key, payload, len(payload.image_bytes))
    return payload
