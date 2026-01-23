"""
Education 模块数据模型

定义挑战Live、加成详情、区域道具、羁绊等级、队长次数相关的 Pydantic 模型。
"""

from typing import List, Dict, Optional

from pydantic import BaseModel

from src.sekai.profile.model import DetailedProfileCardRequest


# ========== 挑战Live详情 ==========

class CharacterChallengeInfo(BaseModel):
    """角色挑战信息
    
    Attributes
    ----------
    chara_id : int
        角色ID
    rank : int
        挑战等级
    score : int
        最高分数
    jewel : int
        剩余可获取的宝石数量
    shard : int
        剩余可获取的碎片数量
    chara_icon_path : str
        角色图标路径
    """
    chara_id: int
    rank: int
    score: int
    jewel: int
    shard: int
    chara_icon_path: str


class ChallengeLiveDetailsRequest(BaseModel):
    """挑战Live详情绘制请求
    
    Attributes
    ----------
    profile : DetailedProfileCardRequest
        用户信息
    character_challenges : List[CharacterChallengeInfo]
        各角色的挑战信息列表
    max_score : int
        分数上限（用于进度条显示）
    jewel_icon_path : Optional[str]
        宝石图标路径
    shard_icon_path : Optional[str]
        碎片图标路径
    """
    profile: DetailedProfileCardRequest
    character_challenges: List[CharacterChallengeInfo]
    max_score: int
    jewel_icon_path: Optional[str] = None
    shard_icon_path: Optional[str] = None


# ========== 加成详情 ==========

class CharacterBonus(BaseModel):
    """角色加成信息
    
    Attributes
    ----------
    chara_id : int
        角色ID
    chara_icon_path : str
        角色图标路径
    area_item : float
        区域道具加成
    rank : float
        角色等级加成
    fixture : float
        烤森玩偶加成
    total : float
        总加成
    """
    chara_id: int
    chara_icon_path: str
    area_item: float
    rank: float
    fixture: float
    total: float


class UnitBonus(BaseModel):
    """组合加成信息
    
    Attributes
    ----------
    unit : str
        组合名称
    unit_icon_path : str
        组合图标路径
    area_item : float
        区域道具加成
    gate : float
        烤森门加成
    total : float
        总加成
    """
    unit: str
    unit_icon_path: str
    area_item: float
    gate: float
    total: float


class AttrBonus(BaseModel):
    """属性加成信息
    
    Attributes
    ----------
    attr : str
        属性名称
    attr_icon_path : str
        属性图标路径
    area_item : float
        区域道具加成
    total : float
        总加成
    """
    attr: str
    attr_icon_path: str
    area_item: float
    total: float


class PowerBonusDetailRequest(BaseModel):
    """加成详情绘制请求
    
    Attributes
    ----------
    profile : DetailedProfileCardRequest
        用户信息
    chara_bonuses : List[CharacterBonus]
        角色加成列表
    unit_bonuses : List[UnitBonus]
        组合加成列表
    attr_bonuses : List[AttrBonus]
        属性加成列表
    """
    profile: DetailedProfileCardRequest
    chara_bonuses: List[CharacterBonus]
    unit_bonuses: List[UnitBonus]
    attr_bonuses: List[AttrBonus]


# ========== 区域道具升级材料 ==========

class AreaItemMaterial(BaseModel):
    """区域道具升级材料
    
    Attributes
    ----------
    material_id : int
        材料ID
    material_icon_path : str
        材料图标路径
    quantity : int
        需要数量
    have_quantity : int
        拥有数量
    sum_quantity : int
        累计需要数量
    is_enough : bool
        是否足够
    """
    material_id: int
    material_icon_path: str
    quantity: int
    have_quantity: int
    sum_quantity: int
    is_enough: bool


class AreaItemLevel(BaseModel):
    """区域道具等级信息
    
    Attributes
    ----------
    level : int
        等级
    bonus : float
        加成率
    can_upgrade : bool
        是否可升级
    materials : List[AreaItemMaterial]
        升级材料列表
    """
    level: int
    bonus: float
    can_upgrade: bool
    materials: List[AreaItemMaterial]


class AreaItemInfo(BaseModel):
    """区域道具信息
    
    Attributes
    ----------
    item_id : int
        道具ID
    current_level : int
        当前等级
    item_icon_path : str
        道具图标路径
    target_icon_path : Optional[str]
        目标图标路径（角色/组合/属性）
    levels : List[AreaItemLevel]
        等级信息列表
    """
    item_id: int
    current_level: int
    item_icon_path: str
    target_icon_path: Optional[str] = None
    levels: List[AreaItemLevel]


class AreaItemUpgradeMaterialsRequest(BaseModel):
    """区域道具升级材料绘制请求
    
    Attributes
    ----------
    profile : Optional[DetailedProfileCardRequest]
        用户信息（可选）
    area_items : List[AreaItemInfo]
        区域道具列表
    has_profile : bool
        是否有用户信息
    """
    profile: Optional[DetailedProfileCardRequest] = None
    area_items: List[AreaItemInfo]
    has_profile: bool = False


# ========== 羁绊等级 ==========

class BondInfo(BaseModel):
    """羁绊信息
    
    Attributes
    ----------
    chara_id1 : int
        角色1 ID
    chara_id2 : int
        角色2 ID
    chara_icon_path1 : str
        角色1图标路径
    chara_icon_path2 : str
        角色2图标路径
    chara_rank1 : int
        角色1等级
    chara_rank2 : int
        角色2等级
    bond_level : int
        羁绊等级
    need_exp : Optional[int]
        升级所需经验
    has_bond : bool
        是否有羁绊
    color1 : tuple
        角色1颜色 RGB
    color2 : tuple
        角色2颜色 RGB
    """
    chara_id1: int
    chara_id2: int
    chara_icon_path1: str
    chara_icon_path2: str
    chara_rank1: int
    chara_rank2: int
    bond_level: int
    need_exp: Optional[int] = None
    has_bond: bool = True
    color1: tuple = (100, 100, 100)
    color2: tuple = (100, 100, 100)


class BondsRequest(BaseModel):
    """羁绊等级绘制请求
    
    Attributes
    ----------
    profile : DetailedProfileCardRequest
        用户信息
    bonds : List[BondInfo]
        羁绊信息列表
    max_level : int
        最大羁绊等级
    """
    profile: DetailedProfileCardRequest
    bonds: List[BondInfo]
    max_level: int


# ========== 队长次数 ==========

class LeaderCountInfo(BaseModel):
    """队长次数信息
    
    Attributes
    ----------
    chara_id : int
        角色ID
    chara_icon_path : str
        角色图标路径
    play_count : int
        队长次数
    ex_level : int
        EX等级
    ex_count : int
        EX次数
    """
    chara_id: int
    chara_icon_path: str
    play_count: int
    ex_level: int
    ex_count: int


class LeaderCountRequest(BaseModel):
    """队长次数绘制请求
    
    Attributes
    ----------
    profile : DetailedProfileCardRequest
        用户信息
    leader_counts : List[LeaderCountInfo]
        队长次数列表
    max_play_count : int
        最大队长次数
    """
    profile: DetailedProfileCardRequest
    leader_counts: List[LeaderCountInfo]
    max_play_count: int
