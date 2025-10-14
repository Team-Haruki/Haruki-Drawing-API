from datetime import datetime
from pydantic import BaseModel
from PIL import Image
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
from src.base.configs import ASSETS_BASE_DIR
from src.base.utils import get_readable_datetime, truncate, get_img_from_path
from src.base.painter import(
    DEFAULT_FONT,
    DEFAULT_BOLD_FONT,
    BLACK,
    resize_keep_ratio,
    Painter
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
from src.base.card_utils import get_card_full_thumbnail

class DetailedProfileCardRequest(BaseModel):
    """用户信息模型 - 扩展版本，支持完整的游戏数据"""
    # 基本信息字段
    id: str
    region: str
    nickname: str
    source: str
    update_time: int
    mode: Optional[str] = None
    is_hide_uid: bool = False
    leader_image_path: str
    has_frame: bool = False
    frame_path: Optional[str] = None

    user_cards: Optional[List[Dict]] = None  # 用户拥有的卡牌列表
    user_decks: Optional[List[Dict]] = None  # 用户卡组信息
    user_gamedata: Optional[Dict] = None     # 用户游戏数据

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

    base = get_img_from_path(frame_base_path, "horizontal/frame_base.png")
    ct = get_img_from_path(frame_base_path,"vertical/frame_centertop.png")
    lb = get_img_from_path(frame_base_path,"vertical/frame_leftbottom.png")
    lt = get_img_from_path(frame_base_path, "vertical/frame_lefttop.png")
    rb = get_img_from_path(frame_base_path, "vertical/frame_rightbottom.png")
    rt = get_img_from_path(frame_base_path, "vertical/frame_righttop.png")

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
    # 修复：让内部空间正好匹配所需尺寸，而不是整个边框匹配
    # 这样边框会环绕头像，而不是被压缩到头像大小
    scale_ratio = frame_w / inner_w
    final_size = int(w * scale_ratio)
    img = resize_keep_ratio(img, final_size / img.width, mode="scale")
    return img

# 获取带框头像控件
async def get_avatar_widget_with_frame(is_frame: bool, frame_path: str, avatar_img: Image.Image, avatar_w: int, frame_data: list[dict]) -> Frame:
    frame_img = None
    if is_frame:
        # 生成边框，边框内部空间正好匹配头像尺寸
        frame_img = await get_player_frame_image(frame_path, avatar_w)

    if frame_img:
        # 如果有边框，容器大小要适应边框的大小
        container_size = frame_img.width  # 边框是正方形的
        with Frame().set_size((container_size, container_size)).set_content_align('c') as ret:
            # 先绘制头像（自动居中）
            ImageBox(avatar_img, size=(avatar_w, avatar_w), use_alpha_blend=False)
            # 再绘制边框覆盖在头像上
            ImageBox(frame_img, use_alpha_blend=True)
    else:
        # 没有边框时使用原始逻辑
        with Frame().set_size((avatar_w, avatar_w)).set_content_align('c') as ret:
            ImageBox(avatar_img, size=(avatar_w, avatar_w), use_alpha_blend=False)
    return ret

def process_hide_uid(is_hide_uid: bool, uid: str, keep: int = 0) -> str:
    """处理UID隐藏"""
    if is_hide_uid:
        if keep:
            return "*" * (16 - keep) + str(uid)[-keep:]
        return "*" * 16
    return uid


def get_user_card_ids(user_info: DetailedProfileCardRequest) -> List[int]:
    """
    从用户信息中提取卡牌ID列表

    Args:
        user_info: 用户信息

    Returns:
        用户拥有的卡牌ID列表
    """
    if not user_info.user_cards:
        return []

    # 从user_cards中提取cardId
    card_ids = []
    for card in user_info.user_cards:
        if isinstance(card, dict) and 'cardId' in card:
            card_ids.append(card['cardId'])
        elif isinstance(card, int):
            card_ids.append(card)

    return card_ids

async def generate_user_avatar(user_info: DetailedProfileCardRequest, avatar_size: int = 80) -> Optional[Image.Image]:
    """获取用户头像（使用固定路径）"""
    try:
        # 使用固定的leader_image_path
        try:
            return get_img_from_path(ASSETS_BASE_DIR, user_info.leader_image_path)
        except FileNotFoundError:
            # 如果找不到指定头像，使用默认头像
            try:
                return get_img_from_path(ASSETS_BASE_DIR, "user/default_avatar.png")
            except FileNotFoundError:
                # 如果都找不到，返回None让调用方处理
                return None

    except Exception as e:
        print(f"获取用户头像失败: {e}")
        return None


async def get_detailed_profile_card(rqd: DetailedProfileCardRequest) -> Frame:
    """获取详细用户信息卡片"""
    profile = rqd
    with Frame().set_bg(roundrect_bg()).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            if profile:
                mode = profile.mode
                frame_path = profile.frame_path
                has_frame = profile.has_frame

                # 动态生成用户头像
                avatar_img = await generate_user_avatar(profile)
                if avatar_img is None:
                    # 如果生成失败，创建一个简单的彩色头像作为备选
                    from src.base.painter import Painter
                    from PIL import ImageColor
                    p = Painter(size=(80, 80))
                    # 创建一个珊瑚色的背景
                    color = ImageColor.getrgb('cornflowerblue')
                    bg_image = Image.new('RGBA', (80, 80), color + (255,))  # 添加alpha通道
                    avatar_img = bg_image

                avatar_widget = await get_avatar_widget_with_frame(
                    is_frame=bool(has_frame),
                    frame_path=frame_path,
                    avatar_img=avatar_img,
                    avatar_w=80,
                    frame_data=[]
                )
                with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                    source = profile.source or '?'
                    update_time = datetime.fromtimestamp(rqd.update_time / 1000)
                    update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(update_time, show_original_time=False)})"
                    user_id = process_hide_uid(profile.is_hide_uid, rqd.id, keep=6)
                    colored_text_box(
                        truncate(profile.nickname, 64),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2),
                    )
                    TextBox(f"{rqd.region.upper()}: {user_id} Suite数据", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"更新时间: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"数据来源: {source}  获取模式: {mode}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
    return f


# ==================== 从user/drawer.py整合的功能 ====================

class UserCardInfo(BaseModel):
    """用户卡牌信息模型"""
    card_id: int
    level: int = 1
    skill_level: int = 1
    master_rank: int = 0
    episodes: List[Dict[str, Any]] = []
    story1_unlocked: bool = False
    story2_unlocked: bool = False
    default_card: bool = False


class UserCharacterInfo(BaseModel):
    """用户角色信息模型"""
    character_id: int
    rank: int = 1
    friendship_level: int = 0
    is_unlocked: bool = True


class ChallengeInfo(BaseModel):
    """挑战信息模型"""
    solo_challenge_progress: Dict[str, Any] = {}
    team_challenge_progress: Dict[str, Any] = {}


class AreaItemInfo(BaseModel):
    """区域道具信息模型"""
    area_item_id: int
    level: int = 0
    is_purchased: bool = False


class UserInfoRequest(BaseModel):
    """用户信息请求模型 - 基于luna-bot数据结构"""
    # 基础信息
    user_id: str
    region: str
    name: str
    source: str
    update_time: int
    mode: Optional[str] = None
    is_hide_uid: bool = False
    local_source: Optional[str] = None

    # 游戏基础信息
    level: Optional[int] = None
    rank: Optional[int] = None
    profile_image_path: Optional[str] = None

    # 框架相关
    has_frame: bool = False
    frame_path: Optional[str] = None

    # 用户卡牌列表
    user_cards: Optional[List[UserCardInfo]] = None

    # 用户角色信息
    user_characters: Optional[List[UserCharacterInfo]] = None

    # 挑战信息
    challenge_info: Optional[ChallengeInfo] = None

    # 区域道具
    area_items: Optional[List[AreaItemInfo]] = None

    # 其他游戏数据
    platinum_rank: Optional[int] = None
    last_login_time: Optional[int] = None
    trainer_level: Optional[int] = None


def process_hide_uid_v2(is_hide_uid: bool, uid: str, keep: int = 0) -> str:
    """处理UID隐藏 - v2版本"""
    if is_hide_uid:
        if keep:
            return "*" * (len(str(uid)) - keep) + str(uid)[-keep:]
        return "*" * len(str(uid))
    return uid


def _create_user_card_grid(user_cards: List[UserCardInfo], cards_per_row: int = 8, card_size: int = 60) -> Frame:
    """创建用户卡牌网格"""
    if not user_cards:
        return Frame()

    with Frame().set_content_align('c').set_item_align('c').set_sep(8) as card_frame:
        # 将卡牌按行分组
        for i in range(0, len(user_cards), cards_per_row):
            row_cards = user_cards[i:i + cards_per_row]
            with HSplit().set_content_align('c').set_item_align('c').set_sep(4):
                for card_info in row_cards:
                    # 创建占位卡牌图片（实际使用中可以从assets获取）
                    with Frame().set_size((card_size, card_size)) as card_placeholder:
                        pass

    return card_frame


async def _create_profile_section_v2(user_info: UserInfoRequest) -> Frame:
    """创建用户信息展示区域 - v2版本"""
    with VSplit().set_content_align('l').set_item_align('l').set_sep(8):
        # 用户名和等级
        level_text = f"Lv.{user_info.level}" if user_info.level else ""
        colored_text_box(
            truncate(user_info.name, 32),
            TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK, use_shadow=True),
        )

        if level_text:
            TextBox(level_text, TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))

        # UID和服务器信息
        user_id_display = process_hide_uid_v2(user_info.is_hide_uid, user_info.user_id, keep=6)
        TextBox(f"{user_info.region.upper()}: {user_id_display}",
                TextStyle(font=DEFAULT_FONT, size=14, color=BLACK))

        # 排名信息
        if user_info.rank is not None:
            TextBox(f"排名: #{user_info.rank:,}", TextStyle(font=DEFAULT_FONT, size=14, color=BLACK))

        # 更新时间和数据源
        if user_info.update_time:
            update_time = datetime.fromtimestamp(user_info.update_time / 1000)
            update_time_text = update_time.strftime('%m-%d %H:%M:%S')
            TextBox(f"更新: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=12, color=BLACK))

        # 数据来源
        source_text = user_info.source
        if user_info.mode:
            source_text += f" ({user_info.mode})"
        TextBox(f"数据源: {source_text}", TextStyle(font=DEFAULT_FONT, size=12, color=BLACK))


async def _create_stats_section_v2(user_info: UserInfoRequest) -> Frame:
    """创建用户统计信息区域 - v2版本"""
    stats = []

    # 收集统计信息
    if user_info.user_cards:
        stats.append(f"卡牌: {len(user_info.user_cards)}张")

    if user_info.user_characters:
        unlocked_chars = sum(1 for char in user_info.user_characters if char.is_unlocked)
        stats.append(f"角色: {unlocked_chars}/{len(user_info.user_characters)}")

    if user_info.area_items:
        purchased_items = sum(1 for item in user_info.area_items if item.is_purchased)
        stats.append(f"道具: {purchased_items}/{len(user_info.area_items)}")

    if user_info.trainer_level:
        stats.append(f"训练等级: {user_info.trainer_level}")

    if user_info.platinum_rank:
        stats.append(f"白金排名: #{user_info.platinum_rank:,}")

    if not stats:
        return Frame()

    with Frame().set_content_align('l').set_item_align('l'):
        with HSplit().set_content_align('l').set_item_align('l').set_sep(15):
            for stat in stats:
                TextBox(f"• {stat}", TextStyle(font=DEFAULT_FONT, size=14, color=BLACK))


async def compose_user_info_card_v2(user_info: UserInfoRequest) -> Image.Image:
    """
    绘制用户信息卡片主函数 - v2版本

    Args:
        user_info: 用户信息请求对象

    Returns:
        PIL.Image: 生成的用户信息卡片图片
    """
    # 主容器
    with Frame().set_bg(roundrect_bg()).set_padding(20) as main_frame:
        with VSplit().set_content_align('c').set_item_align('c').set_sep(16):
            # 顶部：头像和基本信息
            with HSplit().set_content_align('c').set_item_align('l').set_sep(16):
                # 头像区域
                if user_info.profile_image_path:
                    try:
                        avatar_img = await get_img_from_path(ASSETS_BASE_DIR, user_info.profile_image_path)
                        avatar_img = resize_keep_ratio(avatar_img, 100/avatar_img.width, mode="scale")
                        with Frame().set_size((100, 100)):
                            ImageBox(avatar_img, size=(100, 100))
                    except:
                        # 头像加载失败时使用占位符
                        with Frame().set_size((100, 100)).set_bg(roundrect_bg()):
                            pass

                # 基本信息区域
                profile_section = await _create_profile_section_v2(user_info)

            # 分隔线
            with Frame().set_size((600, 1)).set_bg(lambda w, h: Image.new('RGB', (w, h), (200, 200, 200))):
                pass

            # 统计信息区域
            stats_section = await _create_stats_section_v2(user_info)
            if stats_section:
                stats_section

            # 用户卡牌展示区域（如果有卡牌数据）
            if user_info.user_cards and len(user_info.user_cards) > 0:
                with VSplit().set_content_align('c').set_item_align('c').set_sep(8):
                    TextBox(f"拥有卡牌 ({len(user_info.user_cards)}张)",
                           TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=BLACK))

                    # 显示前24张卡牌作为预览
                    preview_cards = user_info.user_cards[:24]
                    card_grid = _create_user_card_grid(preview_cards, cards_per_row=8)
                    card_grid

                    if len(user_info.user_cards) > 24:
                        TextBox(f"...还有{len(user_info.user_cards) - 24}张卡牌",
                               TextStyle(font=DEFAULT_FONT, size=12, color=BLACK))

    # 转换为PIL图片
    painter = Painter(size=(640, 800))  # 默认尺寸
    frame_image = await main_frame.render(painter)

    # 获取最终图片
    final_image = await painter.get()
    return final_image


def load_user_info_from_json(json_file_path: str) -> UserInfoRequest:
    """
    从JSON文件加载用户信息

    Args:
        json_file_path: JSON文件路径

    Returns:
        UserInfoRequest: 用户信息对象
    """
    with open(json_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 处理不同的JSON格式
    if 'user_info' in data:
        user_data = data['user_info']
    else:
        user_data = data

    # 转换卡牌信息
    if 'user_cards' in user_data:
        user_data['user_cards'] = [UserCardInfo(**card) for card in user_data['user_cards']]

    # 转换角色信息
    if 'user_characters' in user_data:
        user_data['user_characters'] = [UserCharacterInfo(**char) for char in user_data['user_characters']]

    # 转换挑战信息
    if 'challenge_info' in user_data:
        user_data['challenge_info'] = ChallengeInfo(**user_data['challenge_info'])

    # 转换区域道具
    if 'area_items' in user_data:
        user_data['area_items'] = [AreaItemInfo(**item) for item in user_data['area_items']]

    return UserInfoRequest(**user_data)


def create_user_info_from_dict(user_data: Dict[str, Any]) -> UserInfoRequest:
    """
    从字典创建用户信息对象

    Args:
        user_data: 用户信息字典

    Returns:
        UserInfoRequest: 用户信息对象
    """
    # 转换嵌套对象
    if 'user_cards' in user_data:
        user_data['user_cards'] = [UserCardInfo(**card) for card in user_data['user_cards']]

    if 'user_characters' in user_data:
        user_data['user_characters'] = [UserCharacterInfo(**char) for char in user_data['user_characters']]

    if 'challenge_info' in user_data:
        user_data['challenge_info'] = ChallengeInfo(**user_data['challenge_info'])

    if 'area_items' in user_data:
        user_data['area_items'] = [AreaItemInfo(**item) for item in user_data['area_items']]

    return UserInfoRequest(**user_data)


# 向后兼容的别名
process_hide_uid_legacy = process_hide_uid  # 保留原有函数
compose_box_image = compose_user_info_card_v2  # 提供更完整的实现