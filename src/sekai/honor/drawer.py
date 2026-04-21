import asyncio
import logging
import os

from PIL import Image, ImageDraw

from src.sekai.base.painter import WHITE, get_font, get_text_size, resize_keep_ratio
from src.sekai.base.utils import (
    build_rendered_image_cache_key,
    get_composed_image_cached,
    get_image_asset_signature,
    get_img_from_path,
    put_composed_image_cache,
    run_in_pool,
)
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT

# 从 model.py 导入数据模型
from .model import HonorRequest

logger = logging.getLogger(__name__)


def is_world_link_rank_style(group_type: str | None, rank_img_path: str | None) -> bool:
    if not rank_img_path:
        return False
    normalized = rank_img_path.replace("\\", "/").lower()
    folder = os.path.basename(os.path.dirname(normalized))
    return folder.startswith("honor_top_") and "event" in folder


def resolve_event_rank_position(base_img: Image.Image, rank_img: Image.Image, is_main: bool) -> tuple[int, int]:
    base_w, base_h = base_img.size
    rank_w, rank_h = rank_img.size

    # Some special event honors provide a full-width rank overlay instead of the
    # usual compact "TOP xxx" badge. Those assets should cover the whole honor.
    if rank_w >= base_w - 8 and rank_h >= base_h - 8:
        return (0, 0)

    return (190, 0) if is_main else (34, 42)

face_pos = {
    1: 48,
    2: 58,
    3: 47,
    4: 53,
    5: 38,
    6: 50,
    7: 56,
    8: 64,
    9: 65,
    10: 39,
    11: 55,
    12: 42,
    13: 44,
    14: 52,
    15: 56,
    16: 58,
    17: 41,
    18: 39,
    19: 47,
    20: 43,
    21: 63,
    22: 45,
    24: 48,
    25: 45,
    28: 58,
    34: 42,
    35: 51,
    36: 63,
    39: 61,
    45: 40,
    46: 57,
    55: 43,
    56: 52,
}


def _compose_full_honor_image_sync(rqd: HonorRequest, images: dict[str, Image.Image | None]) -> Image.Image | None:
    is_main = rqd.is_main_honor
    htype = rqd.honor_type
    hlv = rqd.honor_level
    lv = rqd.fc_or_ap_level
    lv_img = images.get("lv_img")
    lv6_img = images.get("lv6_img")

    if rqd.is_empty:
        img = images.get("empty_honor")
        if img is None:
            return None
        padding = 3
        bg = Image.new("RGBA", (img.size[0] + padding * 2, img.size[1] + padding * 2), (0, 0, 0, 0))
        bg.paste(img, (padding, padding), img)
        return bg

    def add_frame(img: Image.Image, rarity: str, level: int | None = None):
        frame = images.get("frame_img")
        if frame is None:
            return
        img.paste(frame, (8, 0) if rarity == "low" else (0, 0), frame)
        if htype == "birthday":
            icon = images.get("frame_degree_level_img")
            if icon is None or not level:
                return
            w, h = img.size
            sz = 18
            icon = icon.resize((sz, sz))
            for i in range(level):
                img.paste(icon, (int(w / 2 - sz * level / 2 + i * sz), h - sz), icon)

    def add_lv_star(img: Image.Image, level: int):
        if level > 10:
            level = level - 10
        if lv_img is not None:
            for i in range(0, min(level, 5)):
                img.paste(lv_img, (50 + 16 * i, 61), lv_img)
        if lv6_img is not None:
            for i in range(5, level):
                img.paste(lv6_img, (50 + 16 * (i - 5), 61), lv6_img)

    def add_fcap_lv(img: Image.Image):
        lv_text = str(lv or "")
        font = get_font(path=DEFAULT_BOLD_FONT, size=22)
        text_w, _ = get_text_size(font, lv_text)
        offset = 215 if is_main else 37
        draw = ImageDraw.Draw(img)
        draw.text((offset + 50 - text_w // 2, 46), lv_text, font=font, fill=WHITE)

    if htype in ("normal", "birthday"):
        rarity = rqd.honor_rarity
        gtype = rqd.group_type
        wl_rank_style = is_world_link_rank_style(gtype, rqd.rank_img_path)
        img = images.get("honor_img")
        if img is None:
            return None
        rank_img = images.get("rank_img")

        add_frame(img, rarity, hlv)
        if rank_img:
            if gtype == "rank_match":
                img.paste(rank_img, (190, 0) if is_main else (17, 42), rank_img)
            elif wl_rank_style:
                img.paste(rank_img, (0, 0) if is_main else (0, 0), rank_img)  # noqa: RUF034
            else:
                img.paste(rank_img, resolve_event_rank_position(img, rank_img, is_main), rank_img)

        if gtype == "fc_ap":
            scroll_img = images.get("scroll_img")
            if scroll_img is not None:
                img.paste(scroll_img, (215, 3) if is_main else (37, 3), scroll_img)
            add_fcap_lv(img)
        elif gtype in ("character", "achievement"):
            add_lv_star(img, hlv)
        return img

    if htype == "bonds":
        rarity = rqd.honor_rarity
        img = images.get("bonds_bg")
        img2 = images.get("bonds_bg2")
        if img is None or img2 is None:
            return None
        x = 190 if is_main else 90
        img2 = img2.crop((x, 0, 380, 80))
        img.paste(img2, (x, 0))

        c1_img = images.get("chara_icon_1")
        c2_img = images.get("chara_icon_2")
        if c1_img is None or c2_img is None:
            return img

        c1_face = face_pos.get(rqd.chara_id, c1_img.size[0] // 2)
        c2_face = face_pos.get(rqd.chara_id2, c2_img.size[0] // 2)

        w, h = img.size
        scale = 0.8
        c1_img = resize_keep_ratio(c1_img, scale, mode="scale")
        c2_img = resize_keep_ratio(c2_img, scale, mode="scale")
        c1w, c1h = c1_img.size
        c2w, c2h = c2_img.size
        c1_face = int(c1_face * scale)
        c2_face = int(c2_face * scale)

        offset_to_mid = 120 if is_main else 30
        mid = w // 2
        c1_face_x = mid - offset_to_mid
        c2_face_x = mid + offset_to_mid

        overlap1 = (c1_face_x - c1_face + c1w) - mid
        if overlap1 > 0:
            c1_img = c1_img.crop((0, 0, c1w - overlap1, c1h))
        overlap2 = mid - (c2_face_x - c2_face)
        if overlap2 > 0:
            c2_img = c2_img.crop((overlap2, 0, c2w, c2h))
            c2_face -= overlap2

        img.paste(c1_img, (c1_face_x - c1_face, h - c1h), c1_img)
        img.paste(c2_img, (c2_face_x - c2_face, h - c2h), c2_img)
        mask_img = images.get("mask_img")
        if mask_img is not None:
            _, _, _, mask = mask_img.split()
            img.putalpha(mask)

        add_frame(img, rarity)

        if is_main:
            word_img = images.get("word_img")
            if word_img is not None:
                img.paste(word_img, (int(190 - (word_img.size[0] / 2)), int(40 - (word_img.size[1] / 2))), word_img)

        add_lv_star(img, hlv)
        return img
    return None


def _build_full_honor_cache_key(rqd: HonorRequest) -> str:
    request_payload = rqd.model_dump(mode="json", exclude_none=False, exclude={"timezone"})
    asset_signatures = {
        "honor_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.honor_img_path),
        "rank_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.rank_img_path),
        "lv_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.lv_img_path),
        "lv6_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.lv6_img_path),
        "empty_honor": get_image_asset_signature(ASSETS_BASE_DIR, rqd.empty_honor_path),
        "scroll_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.scroll_img_path),
        "word_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.word_img_path),
        "chara_icon_1": get_image_asset_signature(ASSETS_BASE_DIR, rqd.chara_icon_path),
        "chara_icon_2": get_image_asset_signature(ASSETS_BASE_DIR, rqd.chara_icon_path2),
        "bonds_bg": get_image_asset_signature(ASSETS_BASE_DIR, rqd.bonds_bg_path),
        "bonds_bg2": get_image_asset_signature(ASSETS_BASE_DIR, rqd.bonds_bg_path2),
        "mask_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.mask_img_path),
        "frame_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.frame_img_path),
        "frame_degree_level_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.frame_degree_level_img_path),
    }
    return build_rendered_image_cache_key(
        "full_honor_image",
        request_payload,
        asset_signatures=asset_signatures,
    )


async def compose_full_honor_image(rqd: HonorRequest):
    cache_key = _build_full_honor_cache_key(rqd)
    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        return cached

    logger.info(
        "compose honor debug: type=%s group=%s main=%s level=%s rarity=%s "
        "honor_img=%s frame=%s frame_level=%s rank=%s scroll=%s word=%s "
        "bonds_bg=%s bonds_bg2=%s mask=%s lv_img=%s lv6_img=%s",
        rqd.honor_type,
        rqd.group_type,
        rqd.is_main_honor,
        rqd.honor_level,
        rqd.honor_rarity,
        rqd.honor_img_path,
        rqd.frame_img_path,
        rqd.frame_degree_level_img_path,
        rqd.rank_img_path,
        rqd.scroll_img_path,
        rqd.word_img_path,
        rqd.bonds_bg_path,
        rqd.bonds_bg_path2,
        rqd.mask_img_path,
        rqd.lv_img_path,
        rqd.lv6_img_path,
    )

    async def load_honor_image(path: str | None):
        return await get_img_from_path(ASSETS_BASE_DIR, path, on_missing="raise")

    async def load_optional_image(path: str | None):
        if not path:
            return None
        try:
            return await load_honor_image(path)
        except (FileNotFoundError, OSError, ValueError):
            logger.warning("optional honor asset missing: %s", path)
            return None

    tasks: dict[str, object] = {}

    if rqd.is_empty and rqd.empty_honor_path:
        tasks["empty_honor"] = load_honor_image(rqd.empty_honor_path)
    if rqd.lv_img_path:
        tasks["lv_img"] = load_honor_image(rqd.lv_img_path)
    if rqd.lv6_img_path:
        tasks["lv6_img"] = load_honor_image(rqd.lv6_img_path)
    if rqd.frame_img_path:
        tasks["frame_img"] = load_honor_image(rqd.frame_img_path)

    htype = rqd.honor_type
    gtype = rqd.group_type
    wl_rank_style = is_world_link_rank_style(gtype, rqd.rank_img_path)
    if htype == "birthday" and rqd.frame_degree_level_img_path:
        tasks["frame_degree_level_img"] = load_honor_image(rqd.frame_degree_level_img_path)

    if htype in ("normal", "birthday"):
        if rqd.honor_img_path:
            tasks["honor_img"] = load_honor_image(rqd.honor_img_path)
        if rqd.rank_img_path and (gtype in ("event", "wl_event", "rank_match") or wl_rank_style):
            tasks["rank_img"] = load_optional_image(rqd.rank_img_path)
        if gtype == "fc_ap" and rqd.scroll_img_path:
            tasks["scroll_img"] = load_honor_image(rqd.scroll_img_path)
    elif htype == "bonds":
        if rqd.bonds_bg_path:
            tasks["bonds_bg"] = load_honor_image(rqd.bonds_bg_path)
        if rqd.bonds_bg_path2:
            tasks["bonds_bg2"] = load_honor_image(rqd.bonds_bg_path2)
        if rqd.chara_icon_path:
            tasks["chara_icon_1"] = load_honor_image(rqd.chara_icon_path)
        if rqd.chara_icon_path2:
            tasks["chara_icon_2"] = load_honor_image(rqd.chara_icon_path2)
        if rqd.mask_img_path:
            tasks["mask_img"] = load_honor_image(rqd.mask_img_path)
        if rqd.word_img_path:
            tasks["word_img"] = load_honor_image(rqd.word_img_path)

    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values()) if tasks else []
    images = dict(zip(keys, values))
    composed = await run_in_pool(_compose_full_honor_image_sync, rqd, images)
    if composed is not None:
        put_composed_image_cache(cache_key, composed)
    return composed
