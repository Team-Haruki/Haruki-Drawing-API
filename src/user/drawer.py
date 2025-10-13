#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用户信息卡片绘制模块
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from PIL import Image
from pathlib import Path
import json

from src.base.configs import ASSETS_BASE_DIR
from src.base.utils import get_readable_datetime, truncate, get_img_from_path
from src.base.painter import (
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


def process_hide_uid(is_hide_uid: bool, uid: str, keep: int = 0) -> str:
    """处理UID隐藏"""
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


async def _create_profile_section(user_info: UserInfoRequest) -> Frame:
    """创建用户信息展示区域"""
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
        user_id_display = process_hide_uid(user_info.is_hide_uid, user_info.user_id, keep=6)
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


async def _create_stats_section(user_info: UserInfoRequest) -> Frame:
    """创建用户统计信息区域"""
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


async def compose_box_image(user_info: UserInfoRequest) -> Image.Image:
    """
    绘制用户信息卡片主函数

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
                profile_section = await _create_profile_section(user_info)

            # 分隔线
            with Frame().set_size((600, 1)).set_bg(lambda w, h: Image.new('RGB', (w, h), (200, 200, 200))):
                pass

            # 统计信息区域
            stats_section = await _create_stats_section(user_info)
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


# 保持向后兼容
compose_user_info_card = compose_box_image


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