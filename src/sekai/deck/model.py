"""
Deck 模块数据模型

定义组卡推荐相关的 Pydantic 模型，用于组卡推荐图片的绘制请求。
"""

from typing import Optional, List

from pydantic import BaseModel

from src.sekai.profile.model import DetailedProfileCardRequest, CardFullThumbnailRequest


# ========== 基础数据模型 ==========

class DeckCardData(BaseModel):
    """卡组中的卡牌数据
    
    Attributes
    ----------
    card_thumbnail : CardFullThumbnailRequest
        卡牌缩略图请求信息
    chara_id : int
        角色ID
    skill_level : str
        技能等级
    is_after_training : bool
        是否为特训后状态
    skill_rate : float
        技能加成率
    event_bonus_rate : float
        活动加成率
    is_before_story : bool
        是否已读前篇剧情
    is_after_story : bool
        是否已读后篇剧情
    has_canvas_bonus : bool
        是否有烤森加成
    """
    card_thumbnail: CardFullThumbnailRequest
    chara_id: int
    skill_level: str
    is_after_training: bool = False
    skill_rate: float
    event_bonus_rate: float
    is_before_story: bool = False
    is_after_story: bool = False
    has_canvas_bonus: bool = False


class DeckData(BaseModel):
    """卡组数据
    
    Attributes
    ----------
    card_data : List[DeckCardData]
        卡组中的卡牌列表
    pt : Optional[int]
        活动点数
    event_bonus_rate : Optional[float]
        活动加成率
    score_up : Optional[float]
        分数提升率
    total_power : Optional[int]
        综合力
    challenge_score_delta : Optional[int]
        挑战分数差距
    score : Optional[int]
        分数
    live_score : Optional[int]
        Live分数
    mysekai_event_point : Optional[int]
        烤森活动点数
    support_deck_bonus_rate : Optional[float]
        支援卡组加成率
    multi_live_score_up : Optional[float]
        多人Live分数提升率
    """
    card_data: List[DeckCardData]
    pt: Optional[int] = None
    event_bonus_rate: Optional[float] = None
    score_up: Optional[float] = None
    total_power: Optional[int] = None
    challenge_score_delta: Optional[int] = None
    score: Optional[int] = None
    live_score: Optional[int] = None
    mysekai_event_point: Optional[int] = None
    support_deck_bonus_rate: Optional[float] = None
    multi_live_score_up: Optional[float] = None


class DeckRequest(BaseModel):
    """组卡推荐绘制请求
    
    Attributes
    ----------
    region : str
        服务器地区
    profile : DetailedProfileCardRequest
        用户信息
    deck_data : List[DeckData]
        推荐卡组列表
    event_name : Optional[str]
        活动名称
    music_title : Optional[str]
        歌曲标题
    music_id : Optional[int]
        歌曲ID
    music_diff : Optional[str]
        歌曲难度
    event_banner_path : Optional[str]
        活动横幅路径
    music_cover_path : Optional[str]
        歌曲封面路径
    is_max_deck : bool
        是否为顶配卡组
    recommend_type : str
        推荐类型
    wl_chara_name : Optional[str]
        WL角色名称
    wl_chara_icon_path : Optional[str]
        WL角色图标路径
    event_id : Optional[int]
        活动ID
    live_type : Optional[str]
        Live类型
    live_name : Optional[str]
        Live名称
    chara_icon_path : Optional[str]
        角色图标路径
    chara_name : Optional[str]
        角色名称
    unit_logo_path : Optional[str]
        组合Logo路径
    attr_icon_path : Optional[str]
        属性图标路径
    is_wl : bool
        是否为WL活动
    multi_live_teammate_power : Optional[int]
        队友综合力
    multi_live_teammate_score_up : Optional[float]
        队友分数提升率
    target : Optional[str]
        优化目标
    unit_filter : Optional[str]
        组合筛选
    attr_filter : Optional[str]
        属性筛选
    excluded_cards : Optional[List[int]]
        排除的卡牌ID列表
    multi_live_score_up_lower_bound : Optional[float]
        多人Live分数提升下限
    keep_after_training_state : bool
        是否保持特训状态
    model_name : Optional[List]
        算法名称列表
    canvas_thumbnail_path : Optional[str]
        烤森缩略图路径
    fixed_cards_id : Optional[List[int]]
        固定卡牌ID列表
    fixed_characters_id : Optional[List[int]]
        固定角色ID列表
    cost_times : Optional[dict]
        算法耗时
    wait_times : Optional[dict]
        等待时间
    """
    region: str
    profile: DetailedProfileCardRequest
    deck_data: List[DeckData]
    event_name: Optional[str] = None
    music_title: Optional[str] = None
    music_id: Optional[int] = None
    music_diff: Optional[str] = None
    event_banner_path: Optional[str] = None
    music_cover_path: Optional[str] = None
    is_max_deck: bool = False
    recommend_type: str = ""
    wl_chara_name: Optional[str] = None
    wl_chara_icon_path: Optional[str] = None
    event_id: Optional[int] = None
    live_type: Optional[str] = None
    live_name: Optional[str] = None
    chara_icon_path: Optional[str] = None
    chara_name: Optional[str] = None
    unit_logo_path: Optional[str] = None
    attr_icon_path: Optional[str] = None
    is_wl: bool = False
    multi_live_teammate_power: Optional[int] = None
    multi_live_teammate_score_up: Optional[float] = None
    target: Optional[str] = None
    unit_filter: Optional[str] = None
    attr_filter: Optional[str] = None
    excluded_cards: Optional[List[int]] = None
    multi_live_score_up_lower_bound: Optional[float] = None
    keep_after_training_state: bool = False
    model_name: Optional[List] = None
    canvas_thumbnail_path: Optional[str] = None
    fixed_cards_id: Optional[List[int]] = None
    fixed_characters_id: Optional[List[int]] = None
    cost_times: Optional[dict] = None
    wait_times: Optional[dict] = None


# 兼容性别名
CardData = DeckCardData
