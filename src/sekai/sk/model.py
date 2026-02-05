# 绘图所需的数据类型
from datetime import datetime, timedelta

from pydantic import BaseModel


class RankInfo(BaseModel):
    r"""RankInfo

    单个排名数据点信息

    Attributes
    ----------
    rank : int
        排名
    name : str
        玩家名称
    score : Optional[int]
        分数
    time : datetime
        记录时间
    average_round : Optional[int]
        平均周回数
    average_pt : Optional[int]
        平均Pt
    latest_pt : Optional[int]
        最新Pt
    speed : Optional[int]
        时速
    min20_times_3_speed : Optional[int]
        20分钟x3时速
    hour_round : Optional[int]
        本小时周回数
    record_start_at : Optional[datetime]
        记录开始时间
    """

    rank: int
    name: str
    score: int | None = None
    time: datetime
    average_round: int | None = None
    average_pt: int | None = None
    latest_pt: int | None = None
    speed: int | None = None
    min20_times_3_speed: int | None = None
    hour_round: int | None = None
    record_start_at: datetime | None = None


class SpeedInfo(BaseModel):
    r"""SpeedInfo

    时速数据点信息

    Attributes
    ----------
    rank : int
        排名
    score : int
        分数
    speed : Optional[int]
        时速
    record_time : datetime
        记录时间
    """

    rank: int
    score: int
    speed: int | None = None
    record_time: datetime


class SklRequest(BaseModel):
    r"""SklRequest

    绘制活动排名列表图片所必需的数据

    Attributes
    ----------
    id : int
        活动ID
    region : str
        服务器区域
    start_at : int
        活动开始时间戳
    aggregate_at : int
        活动结束时间戳
    name : str
        活动名称
    banner_img_path : str
        活动Banner路径
    wl_cid : Optional[int]
        World Link角色ID
    chara_icon_path : Optional[str]
        角色图标路径
    ranks : list[RankInfo]
        排名列表
    full : bool
        是否显示完整榜线 (True: ALL_RANKS, False: SKL_QUERY_RANKS)
    """

    id: int
    region: str
    start_at: int
    aggregate_at: int
    name: str
    banner_img_path: str
    wl_cid: int | None = None
    chara_icon_path: str | None = None
    ranks: list[RankInfo]
    full: bool = False


class SKRequest(BaseModel):
    r"""SKRequest

    绘制排名查询结果图片所必需的数据

    Attributes
    ----------
    id : int
        活动ID
    region : str
        服务器区域
    name : str
        活动名称
    aggregate_at : int
        活动结束时间戳
    ranks : list[RankInfo]
        排名数据列表
    wl_chara_icon_path : Optional[str]
        World Link角色图标路径
    chara_icon_path : Optional[str]
        角色图标路径
    prev_ranks : Optional[RankInfo]
        上一名排名数据
    next_ranks : Optional[RankInfo]
        下一名排名数据
    """

    id: int
    region: str
    name: str
    aggregate_at: int
    ranks: list[RankInfo]
    wl_chara_icon_path: str | None = None
    chara_icon_path: str | None = None
    prev_ranks: RankInfo | None = None
    next_ranks: RankInfo | None = None


class CFRequest(BaseModel):
    r"""CFRequest

    绘制查房结果图片所必需的数据

    Attributes
    ----------
    eid : int
        活动ID
    event_name : str
        活动名称
    region : str
        服务器区域
    ranks : list[RankInfo]
        排名数据列表
    prev_rank : RankInfo | None
        上一名排名数据
    next_rank : RankInfo | None
        下一名排名数据
    aggregate_at : int
        活动结束时间戳
    update_at : datetime
        数据更新时间
    wl_chara_icon_path : str | None
        World Link角色图标路径
    """

    eid: int
    event_name: str
    region: str
    ranks: list[RankInfo]
    prev_rank: RankInfo | None = None
    next_rank: RankInfo | None = None
    aggregate_at: int
    update_at: datetime
    wl_chara_icon_path: str | None = None


class SpeedRequest(BaseModel):
    r"""SpeedRequest

    绘制时速分析图片所必需的数据

    Attributes
    ----------
    event_id : int
        活动ID
    region : str
        服务器区域
    event_name : str
        活动名称
    event_start_at : int
        活动开始时间戳
    event_aggregate_at : int
        活动结束时间戳
    ranks : list[SpeedInfo]
        时速数据列表
    is_wl_event : bool
        是否是World Link活动
    request_type : str
        请求类型说明
    period : timedelta
        时速统计周期
    banner_img_path : str | None
        活动Banner路径
    wl_chara_icon_path : str | None
        World Link角色图标路径
    """

    event_id: int
    region: str
    event_name: str
    event_start_at: int
    event_aggregate_at: int
    ranks: list[SpeedInfo]
    is_wl_event: bool
    request_type: str
    period: timedelta
    banner_img_path: str | None = None
    wl_chara_icon_path: str | None = None


class PlayerTraceRequest(BaseModel):
    r"""PlayerTraceRequest

    绘制玩家排名追踪图片所必需的数据

    Attributes
    ----------
    event_id : int
        活动ID
    region : str
        服务器区域
    wl_chara_icon_path : str | None
        World Link角色图标路径
    ranks : list[RankInfo]
        排名历史数据
    ranks2 : list[RankInfo] | None
        对比玩家的排名历史数据（可选）
    """

    event_id: int
    region: str
    wl_chara_icon_path: str | None = None
    ranks: list[RankInfo]
    ranks2: list[RankInfo] | None = None


class RankTraceRequest(BaseModel):
    r"""RankTraceRequest

    绘制排名档位追踪图片所必需的数据

    Attributes
    ----------
    event_id : int
        活动ID
    region : str
        服务器区域
    wl_chara_icon_path : str | None
        World Link角色图标路径
    target_rank : int
        目标排名
    ranks : list[RankInfo]
        排名历史数据
    predict_ranks : RankInfo | None
        预测排名数据
    """

    event_id: int
    region: str
    wl_chara_icon_path: str | None = None
    target_rank: int
    ranks: list[RankInfo]
    predict_ranks: RankInfo | None = None


class TeamInfo(BaseModel):
    r"""TeamInfo

    团队战队伍信息

    Attributes
    ----------
    team_id : int
        队伍ID
    team_name : str
        队伍名称
    win_rate : float
        队伍胜率 (0.0 - 1.0)
    is_recruiting : bool
        是否急募中
    team_cn_name : Optional[str]
        队伍中文名称
    team_icon_path : Optional[str]
        队伍图标路径
    """

    team_id: int
    team_name: str
    win_rate: float
    is_recruiting: bool
    team_cn_name: str | None = None
    team_icon_path: str | None = None


class WinRateRequest(BaseModel):
    r"""WinRateRequest

    绘制胜率预测图片所必需的数据

    Attributes
    ----------
    event_id : int
        活动ID
    event_name : str
        活动名称
    region : str
        服务器区域
    wl_chara_icon_path : Optional[str]
        World Link角色图标路径
    updated_at : datetime
        预测更新时间
    event_start_at : int
        活动开始时间戳
    event_aggregate_at : int
        活动结束时间戳
    banner_img_path : Optional[str]
        活动Banner路径
    team_info : List[TeamInfo]
        队伍信息列表
    """

    event_id: int
    event_name: str
    region: str
    wl_chara_icon_path: str | None = None
    updated_at: datetime
    event_start_at: int
    event_aggregate_at: int
    banner_img_path: str | None = None
    team_info: list[TeamInfo]
