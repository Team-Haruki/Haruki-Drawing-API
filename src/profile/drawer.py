from datetime import datetime
from pydantic import BaseModel
from PIL import Image, ImageDraw
from typing import Optional
from src.base.configs import ASSETS_BASE_DIR
from src.base.utils import get_img_from_path
from src.base.utils import get_readable_datetime, truncate
from src.base.painter import(
    DEFAULT_FONT,
    DEFAULT_BOLD_FONT,
    BLACK,
    resize_keep_ratio,
    Painter,
    get_font,
    WHITE
)
from src.base.plot import (
    Frame,
    HSplit,
    VSplit,
    TextStyle,
    TextBox,
    colored_text_box,
    ImageBox,
)
from src.base.draw import roundrect_bg

class DetailedProfileCardRequest(BaseModel):
    id: str
    region: str
    nickname: str
    source: str
    update_time: int
    mode: str = None
    is_hide_uid: bool = False
    leader_image_path: str
    has_frame: bool = False
    frame_path: Optional[str] = None

class CardFullThumbnailRequest(BaseModel):
    id: int
    card_thumbnail_path: str
    rare: str
    frame_img_path: str
    attr_img_path: str
    rare_img_path: str
    train_rank: Optional[int]
    train_rank_img_path: Optional[str] = None
    level: Optional[int] = None
    birthday_icon_path: Optional[str] = None
    after_training: bool = None
    custom_text: Optional[str] = None
    card_level: Optional[dict] = None
    is_pcard: bool = False

async def get_card_full_thumbnail(rqd: CardFullThumbnailRequest) -> Image.Image:
    img = await get_img_from_path(ASSETS_BASE_DIR, rqd.card_thumbnail_path)
    rare_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.rare_img_path)
    if rqd.rare == "rarity_birthday":
        rare_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.birthday_icon_path)
        rare_num = 1
    else:
        rare_num = int(rqd.rare)

    img_w, img_h = img.size
    if rqd.custom_text:
        custom_text = rqd.custom_text
    pcard= rqd.is_pcard
    # 如果是profile卡片则绘制等级/加成
    if pcard:
        if custom_text is not None:
            draw = ImageDraw.Draw(img)
            draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
            draw.text((6, img_h - 31), custom_text, font=get_font(DEFAULT_BOLD_FONT, 20), fill=WHITE)
        else:
            level = rqd.level
            draw = ImageDraw.Draw(img)
            draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
            draw.text((6, img_h - 31), f"Lv.{level}", font=get_font(DEFAULT_BOLD_FONT, 20), fill=WHITE)

    # 绘制边框
    frame_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.frame_img_path)
    frame_img = frame_img.resize((img_w, img_h))
    img.paste(frame_img, (0, 0), frame_img)
    # 绘制特训等级
    if pcard:
        rank = rqd.train_rank
        if rank:
            rank_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.train_rank_img_path)
            rank_img = rank_img.resize((int(img_w * 0.35), int(img_h * 0.35)))
            rank_img_w, rank_img_h = rank_img.size
            img.paste(rank_img, (img_w - rank_img_w, img_h - rank_img_h), rank_img)
    # 左上角绘制属性
    attr_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.attr_img_path)
    attr_img = attr_img.resize((int(img_w * 0.22), int(img_h * 0.25)))
    img.paste(attr_img, (1, 0), attr_img)
    # 左下角绘制稀有度
    hoffset, voffset = 6, 6 if not pcard else 24
    scale = 0.17 if not pcard else 0.15
    rare_img = rare_img.resize((int(img_w * scale), int(img_h * scale)))
    rare_w, rare_h = rare_img.size
    for i in range(rare_num):
        img.paste(rare_img, (hoffset + rare_w * i, img_h - rare_h - voffset), rare_img)
    mask = Image.new("L", (img_w, img_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img_w, img_h), radius=10, fill=255)
    img.putalpha(mask)

    return img

# 获取头像框图片，失败返回None
async def get_player_frame_image(frame_path: str, frame_w: int) -> Image.Image | None:
    frame_base_path = ASSETS_BASE_DIR.joinpath(frame_path)
    scale = 1.5
    corner = 20
    corner2 = 50
    w = 700
    border = 100
    border2 = 80
    inner_w = w - 2 * border

    base = await get_img_from_path(frame_base_path, "horizontal/frame_base.png")
    ct = await get_img_from_path(frame_base_path,"vertical/frame_centertop.png")
    lb = await get_img_from_path(frame_base_path,"vertical/frame_leftbottom.png")
    lt = await get_img_from_path(frame_base_path, "vertical/frame_lefttop.png")
    rb = await get_img_from_path(frame_base_path, "vertical/frame_rightbottom.png")
    rt = await get_img_from_path(frame_base_path, "vertical/frame_righttop.png")

    ct = resize_keep_ratio(ct, scale, mode="scale")
    lt = resize_keep_ratio(lt, scale, mode="scale")
    lb = resize_keep_ratio(lb, scale, mode="scale")
    rt = resize_keep_ratio(rt, scale, mode="scale")
    rb = resize_keep_ratio(rb, scale, mode="scale")

    bw = base.width
    base_lt = base.crop((0, 0, corner, corner))
    base_rt = base.crop((bw - corner, 0, bw, corner))
    base_lb = base.crop((0, bw - corner, corner, bw))
    base_rb = base.crop((bw - corner, bw - corner, bw, bw))
    base_l = base.crop((0, corner, corner, bw - corner))
    base_r = base.crop((bw - corner, corner, bw, bw - corner))
    base_t = base.crop((corner, 0, bw - corner, corner))
    base_b = base.crop((corner, bw - corner, bw - corner, bw))

    p = Painter(size=(w, w))

    p.move_region((border, border), (inner_w, inner_w))
    p.paste(base_lt, (0, 0), (corner2, corner2))
    p.paste(base_rt, (inner_w - corner2, 0), (corner2, corner2))
    p.paste(base_lb, (0, inner_w - corner2), (corner2, corner2))
    p.paste(base_rb, (inner_w - corner2, inner_w - corner2), (corner2, corner2))
    p.paste(base_l.resize((corner2, inner_w - 2 * corner2)), (0, corner2))
    p.paste(base_r.resize((corner2, inner_w - 2 * corner2)), (inner_w - corner2, corner2))
    p.paste(base_t.resize((inner_w - 2 * corner2, corner2)), (corner2, 0))
    p.paste(base_b.resize((inner_w - 2 * corner2, corner2)), (corner2, inner_w - corner2))
    p.restore_region()

    p.paste(lb, (border2, w - border2 - lb.height))
    p.paste(rb, (w - border2 - rb.width, w - border2 - rb.height))
    p.paste(lt, (border2, border2))
    p.paste(rt, (w - border2 - rt.width, border2))
    p.paste(ct, ((w - ct.width) // 2, border2 - ct.height // 2))

    img = await p.get()
    img = resize_keep_ratio(img, frame_w / inner_w, mode="scale")
    return img

# 获取带框头像控件
async def get_avatar_widget_with_frame(is_frame: bool, frame_path: str, avatar_img: Image.Image, avatar_w: int, frame_data: list[dict]) -> Frame:
    frame_img = None
    if is_frame:
        frame_img = await get_player_frame_image(frame_path ,avatar_w + 5)

    with Frame().set_size((avatar_w, avatar_w)).set_content_align('c').set_allow_draw_outside(True) as ret:
        ImageBox(avatar_img, size=(avatar_w, avatar_w), use_alpha_blend=False)
        if frame_img:
            ImageBox(frame_img, use_alpha_blend=True)
    return ret

def process_hide_uid(is_hide_uid: bool, uid: str, keep: int=0) -> str:
    if is_hide_uid:
        if keep:
            return "*" * (16 - keep) + str(uid)[-keep:]
        return "*" * 16
    return uid

async def get_detailed_profile_card(rqd: DetailedProfileCardRequest) -> Frame:
    profile = rqd
    with Frame().set_bg(roundrect_bg(alpha=80)).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            if profile:
                mode = profile.mode
                frame_path = profile.frame_path
                has_frame = profile.has_frame
                avatar_img = await get_img_from_path(ASSETS_BASE_DIR, profile.leader_image_path)
                avatar_widget = await get_avatar_widget_with_frame(
                    is_frame=bool(has_frame),
                    frame_path=frame_path,
                    avatar_img=avatar_img,
                    avatar_w=80,
                    frame_data=[]
                )
                with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                    source = profile.source or "?"
                    update_time = datetime.fromtimestamp(rqd.update_time / 1000)
                    update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(update_time, show_original_time=False)})"
                    user_id = process_hide_uid(profile.is_hide_uid,rqd.id, keep=6)
                    colored_text_box(
                        truncate(profile.nickname, 64),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2),
                    )
                    TextBox(f"{rqd.region.upper()}: {user_id} Suite数据", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"更新时间: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"数据来源: {source}  获取模式: {mode}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
    return f