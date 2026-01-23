"""
Honor 模块数据模型

定义称号/勋章相关的 Pydantic 模型，用于称号图片的绘制请求。
"""

from typing import Optional

from pydantic import BaseModel


class HonorRequest(BaseModel):
    """称号绘制请求
    
    Attributes
    ----------
    honor_type : Optional[str]
        称号类型（normal/bonds/birthday等）
    group_type : Optional[str]
        称号分组类型
    honor_rarity : Optional[str]
        称号稀有度（low/middle/high/highest）
    honor_level : Optional[int]
        称号等级
    fc_or_ap_level : Optional[str]
        FC/AP等级显示
    is_empty : bool
        是否为空称号槽
    is_main_honor : bool
        是否为主称号
    honor_img_path : Optional[str]
        称号图片路径
    rank_img_path : Optional[str]
        排名图片路径
    lv_img_path : Optional[str]
        等级图片路径
    lv6_img_path : Optional[str]
        6级以上等级图片路径
    empty_honor_path : Optional[str]
        空称号槽图片路径
    scroll_img_path : Optional[str]
        滚动图片路径
    word_img_path : Optional[str]
        文字图片路径
    chara_icon_path : Optional[str]
        角色图标路径
    chara_icon_path2 : Optional[str]
        第二角色图标路径
    chara_id : Optional[str]
        角色ID
    chara_id2 : Optional[str]
        第二角色ID
    bonds_bg_path : Optional[str]
        羁绊背景路径
    bonds_bg_path2 : Optional[str]
        第二羁绊背景路径
    mask_img_path : Optional[str]
        遮罩图片路径
    frame_img_path : Optional[str]
        边框图片路径
    frame_degree_level_img_path : Optional[str]
        等级边框图片路径
    """
    honor_type: Optional[str] = None
    group_type: Optional[str] = None
    honor_rarity: Optional[str] = None
    honor_level: Optional[int] = 0
    fc_or_ap_level: Optional[str] = None
    is_empty: bool = False
    is_main_honor: bool = False
    honor_img_path: Optional[str] = None
    rank_img_path: Optional[str] = None
    lv_img_path: Optional[str] = None
    lv6_img_path: Optional[str] = None
    empty_honor_path: Optional[str] = None
    scroll_img_path: Optional[str] = None
    word_img_path: Optional[str] = None
    chara_icon_path: Optional[str] = None
    chara_icon_path2: Optional[str] = None
    chara_id: Optional[str] = None
    chara_id2: Optional[str] = None
    bonds_bg_path: Optional[str] = None
    bonds_bg_path2: Optional[str] = None
    mask_img_path: Optional[str] = None
    frame_img_path: Optional[str] = None
    frame_degree_level_img_path: Optional[str] = None
