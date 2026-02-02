from PIL import Image, ImageDraw

from src.sekai.base.configs import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT
from src.sekai.base.painter import WHITE, get_font, get_text_size, resize_keep_ratio
from src.sekai.base.utils import get_img_from_path

# 从 model.py 导入数据模型
from .model import HonorRequest

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


async def compose_full_honor_image(rqd: HonorRequest):
    if rqd.is_empty:
        img = await get_img_from_path(ASSETS_BASE_DIR, rqd.empty_honor_path)
        padding = 3
        bg = Image.new("RGBA", (img.size[0] + padding * 2, img.size[1] + padding * 2), (0, 0, 0, 0))
        bg.paste(img, (padding, padding), img)
        return bg
    is_main = rqd.is_main_honor
    htype = rqd.honor_type
    hlv = rqd.honor_level
    if rqd.lv_img_path:
        lv_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.lv_img_path)
    if rqd.lv6_img_path:
        lv6_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.lv6_img_path)
    lv = rqd.fc_or_ap_level

    async def add_frame(img: Image.Image, rarity: str, level: int | None = None):
        RARE_MAP = {"low": 1, "middle": 2, "high": 3, "highest": 4}
        RARE_MAP.get(rarity, 1)
        frame = await get_img_from_path(ASSETS_BASE_DIR, rqd.frame_img_path)
        img.paste(frame, (8, 0) if rarity == "low" else (0, 0), frame)
        # 添加生日牌子的等级标志
        if htype == "birthday":
            icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.frame_img_path)
            w, h = img.size
            sz = 18
            icon = icon.resize((sz, sz))
            for i in range(level):
                img.paste(icon, (int(w / 2 - sz * level / 2 + i * sz), h - sz), icon)

    def add_lv_star(img: Image.Image, lv):
        if lv > 10:
            lv = lv - 10
        for i in range(0, min(lv, 5)):
            img.paste(lv_img, (50 + 16 * i, 61), lv_img)
        for i in range(5, lv):
            img.paste(lv6_img, (50 + 16 * (i - 5), 61), lv6_img)

    def add_fcap_lv(img: Image.Image):
        font = get_font(path=DEFAULT_BOLD_FONT, size=22)
        text_w, _ = get_text_size(font, lv)
        offset = 215 if is_main else 37
        draw = ImageDraw.Draw(img)
        draw.text((offset + 50 - text_w // 2, 46), lv, font=font, fill=WHITE)

    if htype == "normal":
        # 普通牌子
        rarity = rqd.honor_rarity
        gtype = rqd.group_type

        if gtype == "rank_match":
            img = await get_img_from_path(ASSETS_BASE_DIR, rqd.honor_img_path)
            rank_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.rank_img_path)
        else:
            img = await get_img_from_path(ASSETS_BASE_DIR, rqd.honor_img_path)
            if gtype == "wl_event":
                rank_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.rank_img_path)
            else:
                rank_img = None

        await add_frame(img, rarity, hlv)
        if rank_img:
            if gtype == "rank_match":
                img.paste(rank_img, (190, 0) if is_main else (17, 42), rank_img)
            elif gtype == "wl_event":
                img.paste(rank_img, (0, 0) if is_main else (0, 0), rank_img)  # noqa: RUF034
            else:
                img.paste(rank_img, (190, 0) if is_main else (34, 42), rank_img)

        if gtype == "fc_ap":
            scroll_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.scroll_img_path)
            if scroll_img:
                img.paste(scroll_img, (215, 3) if is_main else (37, 3), scroll_img)
            add_fcap_lv(img)
        elif gtype == "character" or gtype == "achievement":
            add_lv_star(img, hlv)
        return img

    elif htype == "bonds":
        # 羁绊牌子
        rarity = rqd.honor_rarity
        img = await get_img_from_path(ASSETS_BASE_DIR, rqd.bonds_bg_path)
        img2 = await get_img_from_path(ASSETS_BASE_DIR, rqd.bonds_bg_path2)
        x = 190 if is_main else 90
        img2 = img2.crop((x, 0, 380, 80))
        img.paste(img2, (x, 0))

        c1_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.chara_icon_path)
        c2_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.chara_icon_path2)

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
        _, _, _, mask = (await get_img_from_path(ASSETS_BASE_DIR, rqd.mask_img_path)).split()
        img.putalpha(mask)

        await add_frame(img, rarity)

        if is_main:
            word_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.word_img_path)
            img.paste(word_img, (int(190 - (word_img.size[0] / 2)), int(40 - (word_img.size[1] / 2))), word_img)

        add_lv_star(img, hlv)
        return img
    return None
