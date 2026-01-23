"""
Event 模块数据模型

定义活动相关的 Pydantic 模型，用于活动详情、活动记录和活动列表的绘制请求。
"""

from typing import Any, List, Optional

from pydantic import BaseModel, field_validator
from datetime import datetime

from src.sekai.profile.model import DetailedProfileCardRequest, CardFullThumbnailRequest


# ========== 基础数据模型 ==========

class EventInfo(BaseModel):
    """活动详细信息
    
    Attributes
    ----------
    eid : str
        活动ID
    event_type : str
        活动类型
    start_time : Any
        开始时间（毫秒时间戳）
    end_time : Any
        结束时间（毫秒时间戳）
    is_wl_event : bool
        是否为 World Link 活动
    banner_cid : int
        横幅角色ID
    banner_index : int
        箱活索引
    bonus_attr : str
        加成属性
    bonus_chara_id : Optional[List[int]]
        加成角色ID列表
    wl_time_list : Optional[list[dict[str, Any]]]
        WL章节时间列表
    """
    id: str | int
    event_type: str
    start_at: datetime
    end_at: datetime
    is_wl_event: bool
    banner_cid: int
    banner_index: int
    bonus_attr: str
    bonus_chara_id: Optional[List[int]] = None
    wl_time_list: Optional[list[dict[str, Any]]] = None

    @field_validator('start_at', 'end_at', mode='before')
    @classmethod
    def parse_timestamp(cls, v):
        """将毫秒时间戳转换为 datetime 对象"""
        if isinstance(v, (int, float, str)):
            try:
                timestamp = float(v)
                return datetime.fromtimestamp(timestamp / 1000)
            except (ValueError, TypeError):
                pass
        return v


class EventHistory(BaseModel):
    """活动历史记录
    
    用于活动记录图片中显示用户的活动参与历史。
    
    Attributes
    ----------
    event_id : str
        活动ID
    event_name : str
        活动名称
    event_start_at : int
        活动开始时间（毫秒时间戳）
    event_end_at : int
        活动结束时间（毫秒时间戳）
    rank : Optional[int]
        活动排名
    event_point : int
        活动点数
    is_wl_event : bool
        是否为 WL 活动
    banner_path : str
        横幅图片路径
    wl_chara_icon_path : Optional[str]
        WL 角色图标路径
    """
    id: str | int
    event_name: str
    start_at: datetime
    end_at: datetime
    rank: Optional[int] = None
    event_point: int
    is_wl_event: bool = False
    banner_path: str
    wl_chara_icon_path: Optional[str] = None

    @field_validator('start_at', 'end_at', mode='before')
    @classmethod
    def parse_timestamp(cls, v):
        """将毫秒时间戳转换为 datetime 对象"""
        if isinstance(v, (int, float, str)):
            try:
                timestamp = float(v)
                return datetime.fromtimestamp(timestamp / 1000)
            except (ValueError, TypeError):
                pass
        return v


class EventAssets(BaseModel):
    """活动资源路径
    
    Attributes
    ----------
    event_bg_path : str
        活动背景图片路径
    event_logo_path : str
        活动Logo路径
    event_story_bg_path : str
        活动剧情背景路径
    event_attr_image_path : str
        活动属性图标路径
    event_ban_chara_img : str
        横幅角色图片路径
    ban_chara_icon_path : str
        横幅角色图标路径
    bonus_chara_path : Optional[List[str]]
        加成角色图标路径列表
    """
    event_bg_path: str
    event_logo_path: str
    event_story_bg_path: str
    event_attr_image_path: str
    event_ban_chara_img: str
    ban_chara_icon_path: str
    bonus_chara_path: Optional[List[str]] = None


class EventBrief(BaseModel):
    """活动简要信息
    
    用于活动列表中显示。
    
    Attributes
    ----------
    event_id : int
        活动ID
    event_name : str
        活动名称
    event_type : str
        活动类型
    event_start_at : int
        开始时间（毫秒时间戳）
    event_end_at : int
        结束时间（毫秒时间戳）
    event_banner_path : str
        活动横幅路径
    event_cards : Optional[List[CardFullThumbnailRequest]]
        活动卡牌缩略图信息列表
    event_attr_path : Optional[str]
        活动属性图标路径
    event_chara_path : Optional[str]
        活动角色图标路径
    event_unit_path : Optional[str]
        活动组合图标路径
    """
    id: int
    event_name: str
    event_type: str
    start_at: datetime
    end_at: datetime
    event_banner_path: str
    event_cards: Optional[List[CardFullThumbnailRequest]]
    event_attr_path: Optional[str] = None
    event_chara_path: Optional[str] = None
    event_unit_path: Optional[str] = None

    @field_validator('start_at', 'end_at', mode='before')
    @classmethod
    def parse_timestamp(cls, v):
        """将毫秒时间戳转换为 datetime 对象"""
        if isinstance(v, (int, float, str)):
            try:
                timestamp = float(v)
                return datetime.fromtimestamp(timestamp / 1000)
            except (ValueError, TypeError):
                pass
        return v


# ========== 请求模型 ==========

class EventDetailRequest(BaseModel):
    """活动详情绘制请求
    
    Attributes
    ----------
    region : str
        服务器地区
    event_info : EventInfo
        活动详细信息
    event_assets : EventAssets
        活动资源路径
    event_cards : list[CardFullThumbnailRequest]
        活动卡牌缩略图信息列表
    """
    region: str
    event_info: EventInfo
    event_assets: EventAssets
    event_cards: list[CardFullThumbnailRequest]


class EventRecordRequest(BaseModel):
    """活动记录绘制请求
    
    Attributes
    ----------
    event_info : List[EventHistory]
        普通活动记录列表
    wl_event_info : List[EventHistory]
        WL活动记录列表
    user_info : DetailedProfileCardRequest
        用户信息
    """
    event_info: List[EventHistory]
    wl_event_info: List[EventHistory]
    user_info: DetailedProfileCardRequest


class EventListRequest(BaseModel):
    """活动列表绘制请求
    
    Attributes
    ----------
    event_info : List[EventBrief]
        活动简要信息列表
    """
    event_info: List[EventBrief]


# 兼容性别名
EventHistoryInfo = EventHistory
EventBriefInfo = EventBrief
