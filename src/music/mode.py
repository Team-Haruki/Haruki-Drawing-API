# 绘图所需的数据类型
from pydantic import BaseModel
from typing import Any, List, Dict, Literal, Tuple
from src.profile.drawer import DetailedProfileCardRequest, BasicProfileCardRequest

from src.base.plot import TextStyle
from src.base.painter import DEFAULT_BOLD_FONT

# =========================== 全局变量/常量 =========================== #

COMPOSE_MUSIC_REWARDS_IMAGE_GW, COMPOSE_MUSIC_REWARDS_IMAGE_GH = 80, 40
r"""合成歌曲奖励图片的网格宽度和高度"""
COMPOSE_MUSIC_REWARDS_IMAGE_TABLE_HEAD_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50)) 
r"""合成歌曲奖励图片的表头样式"""
COMPOSE_MUSIC_REWARDS_IMAGE_TABLE_ITEM_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(75, 75, 75)) 
r"""合成歌曲奖励图片的表项样式"""


# =========================== 数据类的定义 =========================== #

class MusicMD(BaseModel):
    id: int
    title: str
    composer: str
    lyricist: str
    arranger: str
    mv: list[str] | None = None
    categories: list[str]
    publishedAt: int
    isFullLength: bool

class DifficultyInfo(BaseModel):
    level: list[int]
    note_count: list[int]
    has_append: bool

class MusicVocalInfo(BaseModel):
    vocal_info: dict[str, Any] # {"caption": str, "characters": [{"characterName": str}]}
    vocal_assets: dict[str, str] # {"xxx": path}

class UserProfileInfo(BaseModel):
    uid: str
    region: str
    nickname: str
    data_source: str
    update_time: int

class MusicDetailRequest(BaseModel):
    region: str
    music_info: MusicMD
    bpm: int | None = None
    vocal: MusicVocalInfo
    alias: list[str] | None
    length: str | None = None
    difficulty: DifficultyInfo
    eventId: int | None = None
    cn_name: str | None = None
    music_jacket: str
    event_banner: str | None = None

class MusicBriefList(BaseModel):
    difficulty: DifficultyInfo
    music_info: MusicMD
    music_jacket: str

class MusicBriefListRequest(BaseModel):
    music_list: list[MusicBriefList]
    region: str

class MusicListRequest(BaseModel):
    user_results: Dict[int, Any] # {"musicId": int, "musicDifficultyType": str, "musicDifficulty": str, "playResult": str}
    music_list: List[Dict[str, Any]] # [{"id": int, "difficulty": str}]
    jackets_path_list: Dict[int, str] # {musicId: jacket_path}
    required_difficulties: str
    profile_info: DetailedProfileCardRequest

class PlayProgressCount(BaseModel):
    r"""打歌进度计数类
        
        记录玩家在该定数下的歌曲总数、未通数、已通数、全连数、全P数

        Attributes
        ----------
        level : int
            定数，歌曲等级
        total : int
            记录的歌曲总数
        not_clear : int
            未通歌曲数量
        clear : int
            已通歌曲数量
        fc : int
            全连歌曲数量
        ap : int
            全P歌曲数量
    """

    level: int
    total: int = 0
    not_clear: int = 0
    clear: int = 0
    fc: int = 0
    ap: int = 0

class PlayProgressRequest(BaseModel):
    r"""PlayProgressRequest

        合成打歌进度图片所必须的数据
    
        Attributes
        ----------
        counts : list[ PlayProgressCount ]
            玩家在每个定数的打歌进度
        difficulty : Literal[ "easy", "normal", "hard", "expert", "master", "append" ]
            指定难度，这里只用来指定颜色
        profile_info : DetailedProfileCardRequest
            用于获取玩家详细信息的简单卡片控件
    """

    counts: list[PlayProgressCount]
    difficulty: Literal["easy", "normal", "hard", "expert", "master", "append"] = 'master'
    profile_info: DetailedProfileCardRequest

class DetailMusicRewardsRequest(BaseModel):
    r"""DetailMusicRewardsRequest

    在有抓包数据的情况下合成歌曲奖励图片所必需的数据
    
    Attributes
    ----------
    rank_rewards : int
        乐曲评级奖励，还未达成的乐曲评级(S)可获得的水晶奖励总数
    combo_rewards : Dict[ str, Dict[ int, int ]]
        乐曲连击奖励，不同难度下不同等级的歌曲可获得的连击奖励（水晶或碎片）
    profile_info : DetailedProfileCardRequest
        用于获取玩家详细信息的简单卡片控件
    """

    rank_rewards: int = 0
    combo_rewards: Dict[str, Dict[int, int]] = {
        'hard': {}, 
        'expert': {}, 
        'master': {}, 
        'append': {}
    }
    profile_info: DetailedProfileCardRequest


class BasicMusicRewardsRequest(BaseModel):
    r"""BasicMusicRewardsRequest

    在无抓包数据的情况下合成歌曲奖励图片所必需的数据
    
    Attributes
    ----------
    rank_rewards : str
        乐曲评级奖励，示例：
        8800(110X80首)
    combo_rewards : Dict[ str, str ]
        乐曲连击奖励，不同难度下不同等级的歌曲可获得的连击奖励（水晶或碎片）
        示例：
        ```
        {
            'hard': '11150(50X223首)', 
            'expert': '18130(70X259首)', 
            'master': '41160(70X588首)', 
            'append': '1785(15X119首)'
        }
        ```
    profile_info : BasicProfileCardRequest
        用于获取玩家基本信息的简单卡片控件
    """

    rank_rewards: str = '0'
    combo_rewards: Dict[str, str] = {
        'hard': '0', 
        'expert': '0', 
        'master': '0', 
        'append': '0'
    }
    profile_info: BasicProfileCardRequest

