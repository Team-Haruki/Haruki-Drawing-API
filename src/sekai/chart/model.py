from pydantic import BaseModel
from typing import Union, Optional, Literal
class GenerateMusicChartRequest(BaseModel):
    r"""GenerateMusicChartRequest

    生成谱面图片所必需的数据

    Attributes
    ----------
    music_id : Union[ str, int ]
        歌曲id
    title : str
        歌曲标题
    artist : str
        歌曲作者
    difficulty : Literal[ 'easy', 'normal', 'hard', 'expert', 'master', 'append' ]
        歌曲难度
    play_level : Union[ str, int ]
        歌曲等级
    skill : bool = False = False
        是否显示技能覆盖情况
    jacket_path : str
        歌曲封面路径
    sus_path : str
        谱面数据路径
    style_path : Optional[ str ] = None
        css样式路径
    note_host : str
        note图片根路径
    music_meta : Optional[ dict ] = None
        歌曲元数据
    target_segment_seconds : Optional[ float ] = None
        谱面切分时长，影响高度
    """
    music_id: Union[str, int]
    title: str
    artist: str
    difficulty: Literal['easy', 'normal', 'hard', 'expert', 'master', 'append']
    play_level: Union[str, int]
    skill: bool = False
    jacket_path: str
    sus_path: str
    style_path: Optional[str] = None
    note_host: str
    music_meta: Optional[dict] = None
    target_segment_seconds: Optional[float] = None
    pass