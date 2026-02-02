"""
Gacha 模块数据模型

定义招募/卡池相关的 Pydantic 模型，用于卡池列表和卡池详情的绘制请求。
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from src.sekai.profile.model import CardFullThumbnailRequest

# ========== 基础数据模型 ==========


class GachaFilter(BaseModel):
    """卡池列表筛选器"""

    page: int = 1


class GachaBehavior(BaseModel):
    """卡池抽卡行为

    描述一种抽卡方式的消耗和限制。

    Attributes
    ----------
    type : str
        行为类型（normal/over_rarity_3_once/over_rarity_4_once等）
    spin_count : int
        抽卡次数（1=单抽，10=十连）
    cost_type : Optional[str]
        消耗资源类型
    cost_icon_path : Optional[str]
        消耗资源图标路径
    cost_quantity : Optional[int]
        消耗数量
    execute_limit : Optional[int]
        执行次数限制
    colorful_pass : bool
        是否需要月卡
    """

    type: str
    spin_count: int
    cost_type: str | None = None
    cost_icon_path: str | None = None
    cost_quantity: int | None = None
    execute_limit: int | None = None
    colorful_pass: bool = False


class GachaInfo(BaseModel):
    """卡池详细信息

    Attributes
    ----------
    id : int
        卡池ID
    name : str
        卡池名称
    gacha_type : str
        卡池类型
    summary : str
        卡池简介
    desc : str
        卡池描述
    start_at : int
        开始时间（毫秒时间戳）
    end_at : int
        结束时间（毫秒时间戳）
    asset_name : str
        资源名称
    ceil_item_img_path : Optional[str]
        天井交换物品图片路径
    behaviors : List[GachaBehavior]
        抽卡行为列表
    rarity_1_count : int
        1星卡数量
    rarity_2_count : int
        2星卡数量
    rarity_3_count : int
        3星卡数量
    rarity_4_count : int
        4星卡数量
    rarity_birthday_count : int
        生日卡数量
    pickup_count : int
        UP卡数量
    """

    id: int
    name: str
    gacha_type: str
    summary: str = ""
    desc: str = ""
    start_at: int
    end_at: int
    asset_name: str
    ceil_item_img_path: str | None = None
    behaviors: list[GachaBehavior] = []
    rarity_1_count: int = 0
    rarity_2_count: int = 0
    rarity_3_count: int = 0
    rarity_4_count: int = 0
    rarity_birthday_count: int = 0
    pickup_count: int = 0


class GachaBrief(BaseModel):
    """卡池简要信息

    用于卡池列表显示。

    Attributes
    ----------
    id : int
        卡池ID
    name : str
        卡池名称
    gacha_type : str
        卡池类型
    start_at : datetime
        开始时间
    end_at : datetime
        结束时间
        结束时间
    asset_name : str
        资源名称标识
    """

    id: int
    name: str
    gacha_type: str
    start_at: datetime
    end_at: datetime
    asset_name: str

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def parse_timestamp(cls, v):
        """将毫秒时间戳转换为 datetime 对象"""
        if isinstance(v, int | float | str):
            try:
                timestamp = float(v)
                return datetime.fromtimestamp(timestamp / 1000)
            except (ValueError, TypeError):
                pass
        return v


class GachaCardWeight(BaseModel):
    """卡池卡牌权重信息

    Attributes
    ----------
    id : int
        卡牌ID
    rarity : str
        稀有度
    rate : float
        概率值（0-1）
    thumbnail_request : CardFullThumbnailRequest
        卡牌缩略图请求信息
    """

    id: int
    rarity: str
    rate: float = 0.0
    thumbnail_request: CardFullThumbnailRequest


class GachaWeight(BaseModel):
    """卡池概率信息

    Attributes
    ----------
    rarity_1_rate : Optional[float]
        1星总概率（0-1）
    rarity_2_rate : Optional[float]
        2星总概率（0-1）
    rarity_3_rate : Optional[float]
        3星总概率（0-1）
    rarity_4_rate : Optional[float]
        4星总概率（0-1）
    rarity_birthday_rate : Optional[float]
        生日卡总概率（0-1）
    guaranteed_rates : Dict[str, float]
        各稀有度的保底概率
    """

    rarity_1_rate: float | None = 0.0
    rarity_2_rate: float | None = 0.0
    rarity_3_rate: float | None = 0.0
    rarity_4_rate: float | None = 0.0
    rarity_birthday_rate: float | None = 0.0
    guaranteed_rates: dict[str, float] = {}


# ========== 请求模型 ==========


class GachaListRequest(BaseModel):
    """卡池列表绘制请求

    Attributes
    ----------
    gachas : List[GachaBrief]
        卡池简要信息列表
    page_size : int
        每页显示数量
    region : str
        服务器地区
    gacha_logos : Dict[int, str]
        卡池ID对应的logo图片路径
    filter : GachaFilter
        筛选条件
    """

    gachas: list[GachaBrief]
    page_size: int = 20
    region: str = "jp"
    gacha_logos: dict[int, str] = {}
    filter: GachaFilter = Field(default_factory=GachaFilter)


class GachaDetailRequest(BaseModel):
    """卡池详情绘制请求

    Attributes
    ----------
    gacha : GachaInfo
        卡池详细信息
    weight_info : GachaWeight
        卡池概率信息
    pickup_cards : List[GachaCardWeight]
        当期UP卡牌列表
    logo_img_path : Optional[str]
        Logo图片路径
    banner_img_path : Optional[str]
        Banner图片路径
    bg_img_path : Optional[str]
        背景图片路径
    region : str
        服务器地区
    """

    gacha: GachaInfo
    weight_info: GachaWeight
    pickup_cards: list[GachaCardWeight] = []
    logo_img_path: str | None = None
    banner_img_path: str | None = None
    bg_img_path: str | None = None
    region: str = "jp"


# 兼容性别名
GachaListInfo = GachaBrief
GachaCardWeightInfo = GachaCardWeight
GachaWeightInfo = GachaWeight
