from typing import Any

from pydantic import BaseModel


class ScoreData(BaseModel):
    r"""ScoreData

    控分数据，控分图表中的一行

    Attributes
    ----------
    event_bonus : int
        活动加成，控分队伍的活动加成值
    boost : int
        火/能量/体力，需要消耗多少火来打歌
    score_min : int
        分数下限，打歌需要达到的最低分数
    score_max : int
        分数上限，打歌不能超过的最高分数
    """

    event_bonus: int
    boost: int
    score_min: int
    score_max: int


class ScoreControlRequest(BaseModel):
    r"""ScoreControlRequest

    绘制控分图片所必须的数据

    Attributes
    ----------
    music_cover_path : str
        歌曲封面路径
    music_id : int
        歌曲id
    music_title : str
        歌曲标题，歌曲名
    music_basic_point : int
        歌曲的基础pt
    target_point : int
        目标pt，要控的分数
    valid_scores : List[ ScoreData ]
        获取指定活动PT的所有可能分数
    """

    music_cover_path: str
    music_id: int
    music_title: str
    music_basic_point: int
    target_point: int
    valid_scores: list[ScoreData] = []


class CustomRoomScoreRequest(BaseModel):
    r"""CustomRoomScoreRequest

    绘制自定义房间控分图片所必须的数据

    Attributes
    ----------
    target_point : int
        目标PT
    candidate_pairs : List[Tuple[int, int]]
        候选方案列表，每一项为 (event_rate, event_bonus)
    music_list_map : Dict[int, List[Dict[str, Any]]]
        各pt系数对应的歌曲信息列表，key为event_rate，value为包含music_id, music_title, music_cover的字典列表
    """

    target_point: int
    candidate_pairs: list[tuple[int, int]]
    music_list_map: dict[int, list[dict[str, Any]]]


class MusicMetaInfo(BaseModel):
    r"""MusicMetaInfo

    歌曲Meta信息

    Attributes
    ----------
    difficulty : str
        难度（easy, normal, hard, expert, master, append）
    music_time : float
        歌曲时长（描述）
    tap_count : int
        物量
    event_rate : float
        活动PT系数
    base_score : float
        基础分
    base_score_auto : float
        基础分（AUTO）
    skill_score_solo : List[float]
        技能分（单人）列表，对应不同技能等级
    skill_score_auto : List[float]
        技能分（AUTO）列表
    skill_score_multi : List[float]
        技能分（多人）列表
    fever_score : float
        Fever分数
    """

    difficulty: str
    music_time: float
    tap_count: int
    event_rate: float
    base_score: float
    base_score_auto: float
    skill_score_solo: list[float]
    skill_score_auto: list[float]
    skill_score_multi: list[float]
    fever_score: float


class MusicMetaRequest(BaseModel):
    r"""MusicMetaRequest

    绘制歌曲Meta图片所必须的数据（单个歌曲）

    Attributes
    ----------
    music_id : int
        歌曲ID
    music_title : str
        歌曲标题
    music_cover_path : str
        歌曲封面路径
    metas : List[MusicMetaInfo]
        该歌曲所有难度的Meta信息
    """

    music_id: int
    music_title: str
    music_cover_path: str
    metas: list[MusicMetaInfo]


class MusicBoardItem(BaseModel):
    r"""MusicBoardItem

    歌曲排行列表中的单项数据

    Attributes
    ----------
    rank : int
        排名
    music_id : int
        歌曲ID
    difficulty : str
        难度
    level : int
        等级
    music_title : str
        歌曲标题
    music_cover_path : str
        歌曲封面路径
    live_type_pt : Optional[float]
        活动PT
    live_type_real_score : Optional[float]
        实际分数
    live_type_score : Optional[float]
        分数倍率
    live_type_skill_account : Optional[float]
        技能占比
    live_type_pt_per_hour : Optional[float]
        每小时PT
    play_count_per_hour : Optional[float]
        每小时周回数
    event_rate : float
        活动PT系数
    music_time : float
        歌曲时长
    tps : float
        每秒点击数（TPS）
    """

    rank: int
    music_id: int
    difficulty: str
    level: int
    music_title: str
    music_cover_path: str
    live_type_pt: float | None = None
    live_type_real_score: float | None = None
    live_type_score: float | None = None
    live_type_skill_account: float | None = None
    live_type_pt_per_hour: float | None = None
    play_count_per_hour: float | None = None
    event_rate: float
    music_time: float
    tps: float


class MusicBoardRequest(BaseModel):
    r"""MusicBoardRequest

    绘制歌曲排行图片所必须的数据

    Attributes
    ----------
    live_type : str
        Live类型 (solo, multi, auto)
    target : str
        排序目标 (score, pt, pt/time, tps, time)
    ascend : bool
        是否升序
    page : int
        当前页码
    total_page : int
        总页数
    title_text : str
        构建好的标题文本
    items : List[MusicBoardItem]
        本页显示的排行项列表
    spec_mid_diffs : List[Tuple[int, str]]
        特别关注/高亮的歌曲ID和难度列表
    description : str
        底部描述文本，如技能顺序等
    """

    live_type: str
    target: str
    ascend: bool
    page: int
    total_page: int
    title_text: str
    items: list[MusicBoardItem]
    spec_mid_diffs: list[tuple[int, str]] = []
    description: str = ""
