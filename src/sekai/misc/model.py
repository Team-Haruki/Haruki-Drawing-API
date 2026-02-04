from pydantic import BaseModel


class CharaBirthdayCard(BaseModel):
    r"""CharaBirthdayCard

    角色生日卡牌信息

    Attributes
    ----------
    id : int
        卡牌ID
    thumbnail_path : str
        卡牌缩略图路径
    """

    id: int
    thumbnail_path: str


class BirthdayEventTime(BaseModel):
    r"""BirthdayEventTime

    生日事件时间跨度

    Attributes
    ----------
    start_text : str
        开始时间文本（已格式化）
    end_text : str
        结束时间文本（已格式化）
    """

    start_text: str
    end_text: str


class CharaBirthdayData(BaseModel):
    r"""CharaBirthdayData

    单个角色的生日信息

    Attributes
    ----------
    cid : int
        角色ID
    month : int
        生日月份
    day : int
        生日日期
    icon_path : str
        角色图标路径
    """

    cid: int
    month: int
    day: int
    icon_path: str


class CharaBirthdayRequest(BaseModel):
    r"""CharaBirthdayRequest

    绘制角色生日图片所必需的数据

    Attributes
    ----------
    cid : int
        当前查看的角色ID
    month : int
        生日月份
    day : int
        生日日期
    region_name : str
        服务器名称
    days_until_birthday : int
        距离下次生日的天数
    color_code : str
        角色应援色
    sd_image_path : str
        角色SD图片路径
    title_image_path : str
        角色名称横幅图片路径
    card_image_path : str
        背景卡牌图片路径
    cards : List[CharaBirthdayCard]
        生日卡牌列表

    is_fifth_anniv : bool
        是否是五周年（决定是否包含露滴、浇水、派对时间）

    gacha_time : BirthdayEventTime
        卡池开放时间
    live_time : BirthdayEventTime
        虚拟LIVE时间
    drop_time : Optional[BirthdayEventTime] = None
        露滴掉落时间
    flower_time : Optional[BirthdayEventTime] = None
        浇水开放时间
    party_time : Optional[BirthdayEventTime] = None
        派对开放时间

    all_characters : List[CharaBirthdayData]
        所有角色生日信息（用于底部日历显示）
    """

    cid: int
    month: int
    day: int
    region_name: str
    days_until_birthday: int
    color_code: str
    sd_image_path: str
    title_image_path: str
    card_image_path: str
    cards: list[CharaBirthdayCard]

    is_fifth_anniv: bool

    gacha_time: BirthdayEventTime
    live_time: BirthdayEventTime
    drop_time: BirthdayEventTime | None = None
    flower_time: BirthdayEventTime | None = None
    party_time: BirthdayEventTime | None = None

    all_characters: list[CharaBirthdayData]
