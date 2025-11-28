from pydantic import BaseModel
from typing import List

class ScoreData(BaseModel):
    r"""ScoreData

    分数数据，控分图表中的一行

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
    valid_scores: List[ScoreData] = []