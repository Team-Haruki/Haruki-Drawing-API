"""
Card 模块数据模型

定义卡牌相关的 Pydantic 模型，用于卡牌详情、卡牌列表和卡牌收集册的绘制请求。
"""

from datetime import datetime
from typing import List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from src.sekai.profile.model import DetailedProfileCardRequest, CardFullThumbnailRequest


# ========== 基础数据模型 ==========

class CardPower(BaseModel):
    """卡牌综合力信息
    
    Attributes
    ----------
    power_total : int
        综合力总值（0破未读剧情的数值）
    power1 : int
        表现力
    power2 : int
        技术力
    power3 : int
        活力
    """
    power_total: int
    power1: int
    power2: int
    power3: int


class CardSkill(BaseModel):
    """卡牌技能信息
    
    Attributes
    ----------
    skill_id : int
        技能ID
    skill_name : str
        技能名称
    skill_type : str
        技能类型
    skill_detail : str
        技能详情描述
    skill_type_icon_path : Optional[str]
        技能类型图标路径
    skill_detail_cn : Optional[str]
        技能详情中文翻译
    """
    skill_id: int
    skill_name: str
    skill_type: str
    skill_detail: str
    skill_type_icon_path: Optional[str] = None
    skill_detail_cn: Optional[str] = None


class CardEventInfo(BaseModel):
    """卡牌关联活动信息
    
    用于卡牌详情中显示关联的活动信息。
    
    Attributes
    ----------
    event_id : int
        活动ID
    event_name : str
        活动名称
    start_time : Union[datetime, int, str]
        活动开始时间（支持时间戳）
    end_time : Union[datetime, int, str]
        活动结束时间（支持时间戳）
    event_banner_path : str
        活动横幅图片路径
    bonus_attr : Optional[str]
        活动增幅属性
    unit : Optional[str]
        活动增幅组合
    banner_cid : Optional[int]
        横幅角色ID
    """
    event_id: int
    event_name: str
    start_at: Union[datetime, int, str]
    end_at: Union[datetime, int, str]
    event_banner_path: str
    bonus_attr: Optional[str] = None
    unit: Optional[str] = None
    banner_cid: Optional[int] = None

    @field_validator('start_at', 'end_at', mode='before')
    @classmethod
    def parse_timestamp(cls, v):
        """将毫秒时间戳转换为 datetime 对象"""
        if isinstance(v, (int, str)):
            try:
                timestamp = int(v)
                return datetime.fromtimestamp(timestamp / 1000)
            except (ValueError, TypeError):
                raise ValueError(f"无效的时间戳: {v}")
        return v


class CardGachaInfo(BaseModel):
    """卡牌关联招募信息
    
    用于卡牌详情中显示关联的卡池信息。
    
    Attributes
    ----------
    gacha_id : int
        招募ID
    gacha_name : str
        招募名称
    start_time : Union[datetime, int, str]
        开始时间（支持时间戳）
    end_time : Union[datetime, int, str]
        结束时间（支持时间戳）
    gacha_banner_path : str
        招募横幅图片路径
    """
    gacha_id: int
    gacha_name: str
    start_at: Union[datetime, int, str]
    end_at: Union[datetime, int, str]
    gacha_banner_path: str

    @field_validator('start_at', 'end_at', mode='before')
    @classmethod
    def parse_timestamp(cls, v):
        """将毫秒时间戳转换为 datetime 对象"""
        if isinstance(v, (int, str)):
            try:
                timestamp = int(v)
                return datetime.fromtimestamp(timestamp / 1000)
            except (ValueError, TypeError):
                raise ValueError(f"无效的时间戳: {v}")
        return v


class CardBasic(BaseModel):
    """卡牌基础信息
    
    Attributes
    ----------
    card_id : int
        卡片ID
    character_id : Optional[int]
        角色ID
    character_name : Optional[str]
        角色名称
    unit : Optional[str]
        所属组合
    release_at : Optional[int]
        发布时间（毫秒时间戳）
    supply_type : Optional[str]
        限定类型（非限定/期间限定/Fes限定等）
    card_rarity_type : Optional[str]
        稀有度
    attr : Optional[str]
        属性
    prefix : Optional[str]
        卡名（卡牌称号）
    asset_bundle_name : Optional[str]
        资源名
    skill : Optional[CardSkill]
        技能信息
    special_skill_info : Optional[CardSkill]
        特训后技能信息
    thumbnail_info : Optional[List[CardFullThumbnailRequest]]
        缩略图信息列表
    is_after_training : Optional[bool]
        是否为特训后状态
    """
    card_id: int
    character_id: Optional[int]
    character_name: Optional[str] = None
    unit: Optional[str] = None
    release_at: Optional[int] = None
    supply_type: Optional[str] = None
    rare: Optional[str] = None
    attr: Optional[str] = None
    prefix: Optional[str] = None
    asset_bundle_name: Optional[str] = None
    skill: Optional[CardSkill] = None
    special_skill_info: Optional[CardSkill] = None
    thumbnail_info: Optional[List[CardFullThumbnailRequest]] = None
    is_after_training: Optional[bool] = False
    power: Optional[CardPower] = None


class UserCard(BaseModel):
    """用户卡牌信息
    
    用于表示用户是否拥有某张卡牌。
    
    Attributes
    ----------
    card : CardBasic
        卡牌基础信息
    has_card : bool
        用户是否拥有此卡牌
    """
    card: CardBasic
    has_card: bool


# ========== 请求模型 ==========

class CardDetailRequest(BaseModel):
    """卡牌详情绘制请求
    
    用于生成卡牌详情图片。
    
    Attributes
    ----------
    card_info : CardBasic
        卡牌基础信息
    region : str
        服务器地区
    power_info : CardPower
        卡牌综合力信息
    event_info : Optional[CardEventInfo]
        关联活动信息
    gacha_info : Optional[CardGachaInfo]
        关联招募信息
    card_images_path : List[str]
        卡面图片路径列表
    costume_images_path : List[str]
        服装图片路径列表
    character_icon_path : str
        角色图标路径
    unit_logo_path : str
        团队Logo路径
    background_image_path : Optional[str]
        背景图片路径
    event_attr_icon_path : Optional[str]
        活动增幅属性图标路径
    event_unit_icon_path : Optional[str]
        活动增幅组合图标路径
    event_chara_icon_path : Optional[str]
        活动横幅角色图标路径
    """
    card_info: CardBasic
    region: str
    event_info: Optional[CardEventInfo] = None
    gacha_info: Optional[CardGachaInfo] = None
    card_images_path: List[str] = Field(default_factory=list)
    costume_images_path: List[str] = Field(default_factory=list)
    character_icon_path: str
    unit_logo_path: str
    background_image_path: Optional[str] = None
    event_attr_icon_path: Optional[str] = None
    event_unit_icon_path: Optional[str] = None
    event_chara_icon_path: Optional[str] = None


class CardListRequest(BaseModel):
    """卡牌列表绘制请求
    
    用于生成卡牌列表图片。
    
    Attributes
    ----------
    cards : List[CardBasic]
        卡牌列表
    region : str
        服务器地区
    user_info : Optional[DetailedProfileCardRequest]
        用户信息
    background_img_path : Optional[str]
        背景图片路径
    """
    cards: List[CardBasic]
    region: str
    user_info: Optional[DetailedProfileCardRequest] = None
    background_img_path: Optional[str] = None


class CardBoxRequest(BaseModel):
    """卡牌收集册绘制请求
    
    用于生成按角色分类的卡牌收集册图片。
    
    Attributes
    ----------
    cards : List[UserCard]
        用户卡牌列表
    region : str
        服务器地区
    user_info : Optional[DetailedProfileCardRequest]
        用户信息
    show_id : bool
        是否显示卡牌ID
    show_box : bool
        是否只显示已拥有卡牌
    background_img_path : Optional[str]
        背景图片路径
    character_icon_paths : dict[int, str]
        角色ID到图标路径的映射
    term_limited_icon_path : Optional[str]
        期间限定图标路径
    fes_limited_icon_path : Optional[str]
        Fes限定图标路径
    """
    cards: List[UserCard]
    region: str
    user_info: Optional[DetailedProfileCardRequest] = None
    show_id: bool = False
    show_box: bool = False
    background_img_path: Optional[str] = None
    character_icon_paths: dict[int, str]
    term_limited_icon_path: Optional[str] = None
    fes_limited_icon_path: Optional[str] = None


# 兼容性别名（逐步废弃）
CardPowerInfo = CardPower
SkillInfo = CardSkill
EventInfo = CardEventInfo
GachaInfo = CardGachaInfo
CardBasicInfo = CardBasic
UserCardInfo = UserCard
