# 绘图所需的数据类型
from pydantic import BaseModel
from typing import Any, List, Dict, Literal, Optional
from src.sekai.profile.drawer import DetailedProfileCardRequest, BasicProfile, ProfileCardRequest

# =========================== 数据类的定义 =========================== #

class MusicMD(BaseModel):
    r"""MusicMD

    歌曲元数据信息

    Attributes
    ----------
    id : int
        歌曲ID
    title : str
        歌曲标题
    composer : str
        作曲家
    lyricist : str
        作词家
    arranger : str
        编曲家
    mv_info : list[str] | None
        MV信息列表
    categories : list[str]
        歌曲分类
    release_at : int
        发布时间戳
    is_full_length : bool
        是否完整版
    """
    id: int
    title: str
    composer: str
    lyricist: str
    arranger: str
    mv_info: list[str] | None = None
    categories: list[str]
    release_at: int
    is_full_length: bool

class DifficultyInfo(BaseModel):
    r"""DifficultyInfo

    歌曲难度信息

    Attributes
    ----------
    level : list[int]
        各难度等级列表
    note_count : list[int]
        各难度Note数量列表
    has_append : bool
        是否有APPEND难度
    """
    level: list[int]
    note_count: list[int]
    has_append: bool

class MusicVocalInfo(BaseModel):
    r"""MusicVocalInfo

    歌曲Vocal信息

    Attributes
    ----------
    vocal_info : dict[str, Any]
        Vocal详细信息，结构如：{"caption": str, "characters": [{"characterName": str}]}
    vocal_assets : dict[str, str]
        Vocal资源路径映射，结构如：{"asset_name": "path/to/asset"}
    """
    vocal_info: dict[str, Any] # {"caption": str, "characters": [{"characterName": str}]}
    vocal_assets: dict[str, str] # {"xxx": path}

class UserProfileInfo(BaseModel):
    r"""UserProfileInfo

    用户基本概要信息

    Attributes
    ----------
    uid : str
        用户ID
    region : str
        服务器区域
    nickname : str
        用户昵称
    data_source : str
        数据来源
    update_time : int
        更新时间戳
    """
    uid: str
    region: str
    nickname: str
    data_source: str
    update_time: int

class LeaderboardInfo(BaseModel):
    r"""LeaderboardInfo

    排行榜单项信息

    Attributes
    ----------
    rank : int
        排名
    diff : str
        难度类型 (easy/normal/hard/expert/master/append)
    value : str
        排行榜数值 (如分数百分比、PT数等)
    """
    rank: int
    diff: str
    value: str

class MusicDetailRequest(BaseModel):
    r"""MusicDetailRequest
    
    绘制歌曲详情图片所必需的数据

    Attributes
    ----------
    region : str
        服务器区域
    music_info : MusicMD
        歌曲元数据
    bpm : int | None
        BPM信息
    vocal : MusicVocalInfo
        Vocal信息
    alias : list[str] | None
        歌曲别名
    length : str | None
        歌曲时长字符串
    difficulty : DifficultyInfo
        难度信息
    event_id : int | None
        关联活动ID
    cn_name : str | None
        中文名称
    music_jacket_path : str
        歌曲封面路径
    event_banner_path : str | None
        活动Banner路径
    limited_times : list[tuple[str, str]] | None
        限定时间列表，每项为 (开始时间, 结束时间) 格式化字符串
    leaderboard_matrix : list[list[LeaderboardInfo | None]] | None
        排行榜矩阵，行为live_type，列为target
    leaderboard_music_num : int | None
        参与排行榜的歌曲总数
    leaderboard_live_types : list[str] | None
        排行榜live_type名称列表
    leaderboard_targets : list[str] | None
        排行榜target名称列表
    """
    region: str
    music_info: MusicMD
    bpm: int | None = None
    vocal: MusicVocalInfo
    alias: list[str] | None
    length: str | None = None
    difficulty: DifficultyInfo
    event_id: int | None = None
    cn_name: str | None = None
    music_jacket_path: str
    event_banner_path: str | None = None
    limited_times: list[tuple[str, str]] | None = None
    leaderboard_matrix: list[list[LeaderboardInfo | None]] | None = None
    leaderboard_music_num: int | None = None
    leaderboard_live_types: dict[str, str] | None = None
    leaderboard_targets: dict[str, str] | None = None

class MusicBriefList(BaseModel):
    r"""MusicBriefList

    简略歌曲列表项数据

    Attributes
    ----------
    difficulty : DifficultyInfo
        难度信息
    music_info : MusicMD
        歌曲元数据
    music_jacket_path : str
        歌曲封面路径
    """
    difficulty: DifficultyInfo
    music_info: MusicMD
    music_jacket_path: str

class MusicBriefListRequest(BaseModel):
    r"""MusicBriefListRequest

    绘制简略歌曲列表图片所必需的数据

    Attributes
    ----------
    music_list : list[MusicBriefList]
        歌曲列表
    region : str
        服务器区域
    """
    music_list: list[MusicBriefList]
    region: str

class MusicListRequest(BaseModel):
    r"""MusicListRequest

    绘制歌曲查询列表图片所必需的数据

    Attributes
    ----------
    user_results : Dict[int, Any]
        用户打歌结果，key为musicId
    music_list : List[Dict[str, Any]]
        查询到的歌曲列表
    jackets_path_list : Dict[int, str]
        封面路径映射
    required_difficulties : str
        查询的难度类型
    profile : DetailedProfileCardRequest
        用户个人信息
    play_result_icon_path_map : Optional[Dict[str, str]]
        打歌结果图标路径映射
    """
    user_results: Dict[int, Any] # {"musicId": int, "musicDifficultyType": str, "musicDifficulty": str, "playResult": str}
    music_list: List[Dict[str, Any]] # [{"id": int, "difficulty": str}]
    jackets_path_list: Dict[int, str] # {musicId: jacket_path}
    required_difficulties: str
    profile: DetailedProfileCardRequest
    play_result_icon_path_map: Optional[Dict[str, str]] = None

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
        profile : DetailedProfileCardRequest
            用于获取玩家详细信息的简单卡片控件
    """

    counts: list[PlayProgressCount]
    difficulty: Literal["easy", "normal", "hard", "expert", "master", "append"] = 'master'
    profile: ProfileCardRequest

class MusicComboReward(BaseModel):
    r"""MusicComboRewards

    歌曲连击奖励，某个难度某个等级下的可获得的连击奖励（水晶或碎片）

    Attributes
    ----------
    level : int
        歌曲等级，定数
    reward : int = 0
        剩余可获得的奖励数量(水晶或碎片)
    """
    level: int
    reward: int = 0

class DetailMusicRewardsRequest(BaseModel):
    r"""DetailMusicRewardsRequest

    在有抓包数据的情况下合成歌曲奖励图片所必需的数据
    
    Attributes
    ----------
    rank_rewards : int
        乐曲评级奖励，还未达成的乐曲评级(S)可获得的水晶奖励总数
    combo_rewards : Dict[ Literal[ 'hard', 'expert', 'master', 'append' ], List[ MusicComboRewards ] ]
        乐曲连击奖励，不同难度下不同等级的歌曲可获得的连击奖励（水晶或碎片）
    profile : DetailedProfileCardRequest
        用于获取玩家详细信息的简单卡片控件
    """

    rank_rewards: int = 0
    combo_rewards: Dict[Literal['hard', 'expert', 'master', 'append'], List[MusicComboReward]] = {
        'hard': [], 
        'expert': [], 
        'master': [], 
        'append': []
    }
    profile: ProfileCardRequest
    jewel_icon_path: Optional[str] = None
    shard_icon_path: Optional[str] = None


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
    profile : BasicProfile
        用于获取玩家基本信息的简单卡片控件
    """

    rank_rewards: str = '0'
    combo_rewards: Dict[str, str] = {
        'hard': '0', 
        'expert': '0', 
        'master': '0', 
        'append': '0'
    }
    profile: ProfileCardRequest
    jewel_icon_path: Optional[str] = None
    shard_icon_path: Optional[str] = None

