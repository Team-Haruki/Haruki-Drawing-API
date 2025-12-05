from pydantic import BaseModel
from typing import (
    Optional,
    List
)
from src.base.painter import Color
from src.profile.model import DetailedProfileCardRequest

class MysekaiInfoCardRequest(DetailedProfileCardRequest):
    r"""MysekaiBasicInfo

    我的世界基础信息

    Extends
    ------
    DetailedProfileCardRequest

    Attributes
    ----------
    mysekai_rank : int
        我的世界等级
    """
    mysekai_rank: int 

class MysekaiPhenomRequest(BaseModel):
    r"""MysekaiPhenomRequest

    绘制我的世界天气

    Attributes
    ----------
    refresh_reason : str
        刷新原因
    image_path : str
        天气缩略图地址
    background_fill : Color
        背景颜色
    text : str
        文字，天气更改时间
    text_fill: Color
        文字颜色
    """
    refresh_reason: str
    image_path: str
    background_fill: Color
    text: str
    text_fill: Color

class MysekaiVisitCharacter(BaseModel):
    r"""MysekaiVisitCharacter

    我的世界到访角色

    Attributes
    ----------
    sd_image_path : str
        角色的sd小人图片路径
    memoria_image_path : Optional[ str ] = None
        角色记忆图片路径
    is_read : bool = False
        已读的角色
    is_reservation : bool = False
        邀请的角色
    """
    sd_image_path: str
    memoria_image_path: Optional[str] = None
    is_read: bool = False
    is_reservation: bool = False

class MysekaiResourceNumber(BaseModel):
    r"""MysekaiResourceNumber

    我的世界资源数量

    Attributes
    ----------
    image_path : str
        资源图片路径
    number : int = 0
        资源的数量
    text_color: Color = (100, 100, 100)
        文字颜色
    has_music_record : bool = False
        已拥有的唱片
    """
    image_path: str
    number: int = 0
    text_color: Color = (100, 100, 100)
    has_music_record: bool = False

class MysekaiSiteResourceNumber(BaseModel):
    r"""MysekaiSiteResourceNumber

    我的世界每个地区的资源数量
    Attributes
    ----------
    image_path : str
        地区图片路径
    resource_numbers : List[ MysekaiResourceNumber ]
        地区中的资源数量列表
    """
    image_path: str
    resource_numbers: List[MysekaiResourceNumber]

class MysekaiResourceRequest(BaseModel):
    r"""MysekaiResourceRequest

    绘制我的世界资源图片所必须的数据

    Attributes
    ----------
    mysekai_info : MysekaiInfoCardRequest
        烤森基础信息
    background_image_path : Optional[ str ] = None
        背景图片路径
    phenoms : List[ MysekaPhenomRequest ]
        天气表，绘制天气预报
    gate_image_path : str
        大门图片路径
    gate_level : int
        大门等级
    visit_characters: List[MysekaiVisitCharacter]
        到访的角色列表
    site_resource_numbers: Optional[ List[ MysekaiSiteResourceNumber ] ] = None
        每个地区的资源数量列表
    error_message : Optional[ str ] = None
        错误信息
    """
    mysekai_info: MysekaiInfoCardRequest
    background_image_path: Optional[str] = None 
    phenoms: List[MysekaiPhenomRequest]
    gate_id: int
    gate_level: int
    visit_characters: List[MysekaiVisitCharacter]
    site_resource_numbers: Optional[List[MysekaiSiteResourceNumber]] = None
    error_message: Optional[str] = None

# 各团代表色，没有VS团！
UNIT_COLORS = [
    (68,85,221,255),
    (136,221,68,255),
    (238,17,102,255),
    (255,153,0,255),
    (136,68,153,255),
]
