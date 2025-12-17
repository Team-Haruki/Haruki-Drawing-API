from pydantic import BaseModel
from typing import Optional, List, Dict, Literal
from src.honor.drawer import HonorRequest


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

class BasicProfile(BaseModel):
    r"""BasicProfile

    玩家基本信息

    Attributes
    ----------
    id : str
        玩家id
    region : str
        服务器
    nickname : str
        昵称
    is_hide_uid : bool = False
        是否隐藏id
    leader_image_path : str
        队长头像图片路径
    has_frame : bool = False
        是否有框
    frame_path : Optional[ str ] = None
        框的路径
    """
    id: str
    region: str
    nickname: str
    is_hide_uid: bool = False
    leader_image_path: str
    has_frame: bool = False
    frame_path: Optional[str] = None

class ProfileDataSource(BaseModel):
    r"""ProfileDataSource

    玩家个人信息数据源

    Attributes
    ----------
    name : str
        数据名称
    source : Optional[ str ] = None
        数据来源
    update_time : Optional[ int ] = None
        数据更新时间
    mode : Optional[ str ] = None
        数据获取模式
    """
    name: str
    source: Optional[str] = None
    update_time: Optional[int] = None
    mode: Optional[str] = None

class ProfileCardRequest(BaseModel):
    r"""ProfileCardRequest

    用于合成玩家个人信息的简单卡片控件

    Attributes
    ----------
    profile : Optional[ BasicProfile ] = None
        玩家个人信息
    data_sources: List[ ProfileDataSource ] = []
        数据源信息
    error_message : Optional[str] = None
        错误或警告
    """
    profile: Optional[BasicProfile] = None
    data_sources: List[ProfileDataSource] = []
    error_message: Optional[str] = None


class CardFullThumbnailRequest(BaseModel):
    card_id: int
    card_thumbnail_path: str
    rare: str
    frame_img_path: str
    attr_img_path: str
    rare_img_path: str
    train_rank: Optional[int]
    train_rank_img_path: Optional[str] = None
    level: Optional[int] = None
    birthday_icon_path: Optional[str] = None
    is_after_training: bool = None
    custom_text: Optional[str] = None
    card_level: Optional[dict] = None
    is_pcard: bool = False

class ProfileBgSettings(BaseModel):
    r"""ProfileBgSettings

    个人信息背景设置

    Attributes
    ----------
    img_path : Optional[ str ] = None
        背景图片路径
    blur : int = 4
        背景模糊度
    alpha : int = 100
        背景透明度
    vertical : bool = False
        是否是竖屏
    """
    img_path: Optional[str] = None
    blur: int =  4
    alpha: int = 100
    vertical: bool = False

class MusicClearCount(BaseModel):
    r"""MusicClearCount

    歌曲完成情况

    Attributes
    ----------
    difficulty : Literal[ "easy", "normal", "hard", "expert", "master", "append" ]
        难度
    clear : int = 0
        已通歌曲数量
    fc : int = 0
        全连歌曲数量
    ap : int = 0
        全P歌曲数量
    """
    difficulty: Literal["easy", "normal", "hard", "expert", "master", "append"]
    clear: int = 0
    fc: int = 0
    ap: int = 0

class CharacterRank(BaseModel):
    r"""CharacterRank

    角色等级

    Attributes
    ----------
    character_id : int
        角色id
    rank : int = 0
        角色等级
    """
    character_id: int 
    rank: int = 0

class SoloLiveRank(BaseModel):
    r"""SoloLiveRank

    挑战live等级

    Attributes
    ----------
    character_id : int
        挑战live的角色id
    score : int = 0
        挑战live的得分
    rank : int = 0
        挑战Live的等级
    """
    character_id: int 
    score: int = 0
    rank: int = 0

class ProfileRequest(BaseModel):
    r"""ProfileRequest

    合成个人信息图片所必须的数据

    Attributes
    ----------
    profile : ProfileCardRequest
        基本个人信息
    rank : int
        玩家等级
    twitter_id : str = ''
        X(推特)的id
    word : str = ''
        玩家留言
    pcards : List[ CardFullThumbnailRequest ]
        玩家使用的卡组
    bg_settings : Optional[ ProfileBgSettings ] = None
        背景设置
    honors : List[ HonorRequest ] = []
        头衔
    music_difficulty_count : List[ MusicClearCount ] = []
        各个难度的歌曲完成情况
    character_rank : List[ CharacterRank ] = []
        角色等级信息
    solo_live : Optional[ SoloLiveRank ] = None
        挑战live等级
    update_time : Optional[ int ] = None
        更新时间
    """
    profile: BasicProfile
    rank: int
    twitter_id: str = ''
    word: str = ''
    pcards: List[CardFullThumbnailRequest]
    bg_settings: Optional[ProfileBgSettings] = None
    honors: List[HonorRequest] = []
    music_difficulty_count: List[MusicClearCount] = []
    character_rank: List[CharacterRank] = []
    solo_live: Optional[SoloLiveRank] = None
    update_time: Optional[int] = None