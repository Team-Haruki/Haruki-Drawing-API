from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.sekai.base.asset_key import AssetKey
from src.sekai.base.paint_types import Color
from src.sekai.base.timezone import TimeZoneRequest
from src.sekai.profile.model import ProfileCardRequest

# =========================== 绘制资源数量=========================== #


class MysekaiPhenomRequest(BaseModel):
    r"""MysekaiPhenomRequest

    绘制我的世界天气

    Attributes
    ----------
    refresh_reason : str
        刷新原因
    image_path : str
        天气缩略图地址
    background_fill : Color = (255, 255, 255, 75)
        背景颜色
    start_at : int
        天气更改时间（Unix 毫秒时间戳）
    text_fill: Color = (125, 125, 125, 255)
        文字颜色
    """

    refresh_reason: str
    image_path: AssetKey
    background_fill: Color = (255, 255, 255, 75)
    start_at: int
    text_fill: Color = (125, 125, 125, 255)


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
    memoria_image_path: str | None = None
    is_read: bool = False
    is_reservation: bool = False
    reservation_icon_path: str | None = None


class MysekaiResourceNumber(BaseModel):
    r"""MysekaiResourceNumber

    我的世界资源数量

    Attributes
    ----------
    image_path : str
        资源图片路径
    number : int = 0
        资源的数量
    text_color : Color = (100, 100, 100)
        文字颜色
    has_music_record : bool = False
        已拥有的唱片
    """

    image_path: AssetKey
    number: int = 0
    text_color: Color = (100, 100, 100)
    has_music_record: bool = False
    music_record_icon_path: str | None = None


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

    image_path: AssetKey
    resource_numbers: list[MysekaiResourceNumber]


class MysekaiBirthdayPartyProgress(BaseModel):
    r"""MysekaiBirthdayPartyProgress

    正在进行的生日派对进度

    Attributes
    ----------
    birthday_party_id : int
        生日派对id
    character_unit_id : int
        角色队伍id
    character_name : Optional[ str ] = None
        角色名
    character_icon_path : Optional[ AssetKey ] = None
        角色图标路径
    character_color : Optional[ str ] = None
        角色代表色（#rrggbb）
    level : int = 0
        当前等级（obtainedMysekaiMaterialCount，可以超过 max_level）
    max_level : int = 400
        累计奖励的最高要求等级
    """

    birthday_party_id: int
    character_unit_id: int
    character_name: str | None = None
    character_icon_path: AssetKey | None = None
    character_color: str | None = None
    level: int = 0
    max_level: int = 400


class MysekaiResourceRequest(TimeZoneRequest):
    r"""MysekaiResourceRequest

    绘制我的世界资源图片所必须的数据

    Attributes
    ----------
    profile : ProfileCardRequest
        用户个人信息
    background_image_path : Optional[ str ] = None
        背景图片路径
    phenoms : List[ MysekaiPhenomRequest ]
        天气表，绘制天气预报
    gate_id : int
        大门id
    gate_level : int
        大门等级
    visit_characters: List[ MysekaiVisitCharacter ]
        到访的角色列表
    birthday_parties: Optional[ List[ MysekaiBirthdayPartyProgress ] ] = None
        正在进行的生日派对进度；为空时不绘制
    site_resource_numbers: Optional[ List[ MysekaiSiteResourceNumber ] ] = None
        每个地区的资源数量列表
    """

    profile: ProfileCardRequest
    background_image_path: str | None = None
    phenoms: list[MysekaiPhenomRequest]
    gate_id: int
    gate_level: int
    gate_icon_path: str
    visit_characters: list[MysekaiVisitCharacter]
    birthday_parties: list[MysekaiBirthdayPartyProgress] | None = None
    site_resource_numbers: list[MysekaiSiteResourceNumber] | None = None

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.profile.timezone = self.timezone


# =========================== 绘制MSR地图 =========================== #
class MysekaiMsrMapSiteInfo(BaseModel):
    r"""MysekaiMsrMapSiteInfo

    MSR地图站点配置

    Attributes
    ----------
    image_path : str
        地图背景图路径
    grid_size : float
        游戏坐标每格映射到画布像素的基础缩放
    offset_x : float = 0
        X方向偏移
    offset_z : float = 0
        Z方向偏移
    dir_x : float = 1
        X方向乘子（可用于翻转）
    dir_z : float = 1
        Z方向乘子（可用于翻转）
    rev_xz : bool = False
        是否交换X/Z坐标
    scale : float = 1.0
        地图整体缩放倍数
    crop_bbox : Optional[ Tuple[ int, int, int, int ] ] = None
        裁剪框 (x, y, w, h)
    """

    image_path: AssetKey
    grid_size: float
    offset_x: float = 0.0
    offset_z: float = 0.0
    dir_x: float = 1.0
    dir_z: float = 1.0
    rev_xz: bool = False
    scale: float = 1.0
    crop_bbox: tuple[int, int, int, int] | None = None


class MysekaiMsrMapHarvestPoint(BaseModel):
    r"""MysekaiMsrMapHarvestPoint

    MSR地图采集点信息

    Attributes
    ----------
    id : Optional[ int ] = None
        采集点id
    image_path : str
        采集点图标路径
    fallback_image_path : Optional[ str ] = None
        主图缺失时使用的兜底图标路径
    position_x : float
        游戏坐标X
    position_z : float
        游戏坐标Z
    status : str = "spawned"
        采集点状态（可用于过滤已采集点）
    size : Optional[ int ] = None
        图标基础大小（会乘以site.scale）
    offset_x : float = 0
        额外X偏移
    offset_z : float = 0
        额外Z偏移
    alpha : float = 1.0
        图标透明度倍率
    """

    id: int | None = None
    image_path: AssetKey
    fallback_image_path: str | None = None
    position_x: float
    position_z: float
    status: str = "spawned"
    size: int | None = None
    offset_x: float = 0.0
    offset_z: float = 0.0
    alpha: float = 1.0


class MysekaiMsrMapResourceDrop(BaseModel):
    r"""MysekaiMsrMapResourceDrop

    MSR地图资源掉落信息

    Attributes
    ----------
    id : int
        资源id
    type : str
        资源类型，如 mysekai_material / mysekai_item
    image_path : str
        资源图标路径
    position_x : float
        游戏坐标X
    position_z : float
        游戏坐标Z
    quantity : int = 1
        数量
    status : str = "before_drop"
        掉落状态（用于过滤已采集）
    small_icon : Optional[ bool ] = None
        是否使用小图标，None 表示自动判定
    hide : bool = False
        是否隐藏该资源
    rarity : int = 1
        资源稀有度，>=2 时会有稀有效果
    attachment_image_path : Optional[ str ] = None
        资源附件图标路径（如唱片已收集角标）
    outline_color : Optional[ Color ] = None
        图标边框颜色
    outline_width : Optional[ int ] = None
        图标边框宽度
    light_size : Optional[ int ] = None
        发光特效大小（会乘以site.scale）
    """

    id: int
    type: str
    image_path: str
    position_x: float
    position_z: float
    quantity: int = 1
    status: str = "before_drop"
    small_icon: bool | None = None
    hide: bool = False
    rarity: int = 1
    attachment_image_path: str | None = None
    outline_color: Color | None = None
    outline_width: int | None = None
    light_size: int | None = None


class MysekaiMsrMapData(BaseModel):
    r"""MysekaiMsrMapData

    MSR单张地图数据

    Attributes
    ----------
    map_id : int
        地图id
    site : MysekaiMsrMapSiteInfo
        地图配置
    harvest_points : List[ MysekaiMsrMapHarvestPoint ] = []
        采集点列表
    resource_drops : List[ MysekaiMsrMapResourceDrop ] = []
        掉落资源列表
    """

    map_id: int
    site: MysekaiMsrMapSiteInfo
    harvest_points: list[MysekaiMsrMapHarvestPoint] = []
    resource_drops: list[MysekaiMsrMapResourceDrop] = []


class MysekaiMsrMapRequest(TimeZoneRequest):
    r"""MysekaiMsrMapRequest

    绘制MSR地图所需数据

    Attributes
    ----------
    maps : List[ MysekaiMsrMapData ]
        多地图数据列表（至少1张）
    show_harvested : bool = True
        是否显示已采集内容
    phenomena_ground_color : Color = (255, 255, 255, 255)
        地面染色
    spawn_position_x : float = 0
        出生点X坐标
    spawn_position_z : float = 0
        出生点Z坐标
    spawn_image_path : Optional[ str ] = None
        出生点图标路径（默认使用内置mark）
    spawn_size : int = 20
        出生点图标大小（会乘以site.scale）
    rare_light_image_path : Optional[ str ] = None
        稀有资源发光图标路径（默认使用内置light）
    large_icon_size : int = 35
        资源大图标基础大小（会乘以site.scale）
    small_icon_size : int = 17
        资源小图标基础大小（会乘以site.scale）
    icon_zoffset : int = -32
        资源图标整体Z方向偏移（会乘以site.scale）
    draw_bg_fill : Color = (255, 255, 255, 255)
        最底层背景色
    """

    maps: list[MysekaiMsrMapData] = Field(min_length=1)
    show_harvested: bool = True
    phenomena_ground_color: Color = (255, 255, 255, 255)
    spawn_position_x: float = 0.0
    spawn_position_z: float = 0.0
    spawn_image_path: str | None = None
    spawn_size: int = 20
    rare_light_image_path: str | None = None
    large_icon_size: int = 35
    small_icon_size: int = 17
    icon_zoffset: int = -32
    draw_bg_fill: Color = (255, 255, 255, 255)


# =========================== 绘制家具列表 =========================== #


class MysekaiFixture(BaseModel):
    r"""MysekaiFixture

    我的世界单个家具信息

    Attributes
    ----------
    id : int
        家具的id
    image_path : str
        家具的图片
    character_id : Optional[ int ] = None
        角色id，如果是生日家具，在上面绘制对应的角色图片
    obtained : bool
        是否已拥有家具，未拥有的家具将显示为灰色
    """

    id: int
    image_path: str
    character_id: int | None = None
    chara_icon_path: str | None = None
    obtained: bool


class MysekaiFixtureSubGenre(BaseModel):
    r"""MysekaiFixtureSubGenre

    我的世界家具子分类信息

    Attributes
    ----------
    name : Optional[ str ] = None
        分类名，标签
    image_path : Optional[ str ] = None
        分类图片
    progress_message : Optional[ str ] = None
        分类收集进度信息
    fixtures : List[ MysekaiFixture ] = [ ]
        分类中的家具列表
    """

    name: str | None = None
    image_path: str | None = None
    progress_message: str | None = None
    fixtures: list[MysekaiFixture] = []


class MysekaiFixtureMainGenre(BaseModel):
    r"""MysekaiFixtureMainGenre

    我的世界家具主分类信息

    Attributes
    ----------
    name : str
        分类名，标签
    image_path : str
        分类图片
    progress_message : Optional[ str ] = None
        分类收集进度信息
    sub_genres : List[ MysekaiFixtureSubGenre ] = [ ]
        分类中的子分类列表
    """

    name: str
    image_path: AssetKey
    progress_message: str | None = None
    sub_genres: list[MysekaiFixtureSubGenre] = []


class MysekaiFixtureListRequest(TimeZoneRequest):
    r"""MysekaiFixtureListRequest

    绘制我的世界家具列表图片所必需的数据

    Attributes
    ----------
    profile : Optional[ ProfileCardRequest ] = None
        用户个人信息
    progress_message : Optinal[ str ] = None
        收集进度信息
    show_id : bool = False
        是否绘制家具的id
    main_genres : List[ MysekaiFixtureMainGenre ] = [ ]
        家具分类列表
    """

    profile: ProfileCardRequest | None = None
    progress_message: str | None = None
    show_id: bool = False
    main_genres: list[MysekaiFixtureMainGenre] = Field(default_factory=list)

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.apply_timezone(self.profile)


# =========================== 绘制家具详情 =========================== #


class MysekaiFixtureColorImage(BaseModel):
    r"""MysekaiFixtureColorImage

    我的世界家具不同配色的图片

    Attributes
    ----------
    image_path : str
        该配色的家具图片路径
    color_code : Optional[ str ] = None
        颜色代码
    """

    image_path: AssetKey
    color_code: str | None = None


class MysekaiFixtureMaterial(BaseModel):
    r"""MysekaiFixtureMaterial

    我的世界家具材料，制作材料或回收素材

    Attributes
    ----------
    image_path : str
        图标路径
    quantity : int
        制作所需或回收所得的材料数量
    """

    image_path: AssetKey
    quantity: int


class MysekaiReactionCharacterGroups(BaseModel):
    r"""MysekaiReactionCharacterGroups

    我的世界互动角色组，和某个家具互动的角色们

    与家具互动

    Attributes
    ----------
    number : int
        每组的角色数量
    character_uint_id_groups : Optional[ List[ List[ int ] ] ] = None
        角色id列表，按组分
    chara_icon_path_groups : Optional[ List[ List[ str ] ] ] = None
        角色图片路径列表，按组分
    """

    number: int
    character_uint_id_groups: list[list[int]] | None = None
    chara_icon_path_groups: list[list[str]] | None = None


class MysekaiFixtureDetailRequest(TimeZoneRequest):
    r"""MysekaiFixtureDetailRequest

    绘制我的世界家具详细信息所必需的数据

    Attributes
    ----------
    title : str
        家具标题（名称、id、译名等）
    images : List[ MysekaiFixtureColorImage ]
        家具各配色的图片列表
    main_genre_name : str
        主分类名
    main_genre_image_path : str
        主分类图标路径
    sub_genre_name : Optional[ str ] = None
        子分类名
    sub_genre_image_path : Optional[ str ] = None
        子分类图标路径
    size : Dict[ Literal[ 'width', 'depth', 'height' ] ]
        大小
    first_put_cost : int = 0
        首次放置消耗
    second_put_cost : int = 0
        重复放置消耗
    basic_info: Optional[ List[ str ] ] = None
        其它基本信息，使用Flow布局
    cost_materials : Optional[ List[ MysekaiFixtureMaterial ] ] = None
        制造家具所需的素材
    recycle_materials : Optional[ List[ MysekaiFixtureMaterial ] ] = None
        回收家具返还的素材
    reaction_character_groups : Optional[ List[ MysekaiReactionCharacterGroups ] ] = None
        互动角色组，与家具互动的角色们
    tags : Optional[ List[ str ] ] = None
        家具标签，使用Flow布局
    friendcodes: Optional[ List[ str ] ] = None
        可抄写家具的好友码，使用Flow布局
    friendcode_source: Optional[ str ] = None
        好友码来源
    """

    title: str
    images: list[MysekaiFixtureColorImage]
    main_genre_name: str
    main_genre_image_path: str
    sub_genre_name: str | None = None
    sub_genre_image_path: str | None = None
    size: dict[Literal["width", "depth", "height"], int]
    first_put_cost: int = 0
    second_put_cost: int = 0
    basic_info: list[str] | None = None
    cost_materials: list[MysekaiFixtureMaterial] | None = None
    recycle_materials: list[MysekaiFixtureMaterial] | None = None
    reaction_character_groups: list[MysekaiReactionCharacterGroups] | None = None
    tags: list[str] | None = None
    friendcodes: list[str] | None = None
    friendcode_source: str = ""


# =========================== 绘制大门升级 =========================== #


class MysekaiGateMaterialItem(BaseModel):
    r"""MysekaiGateMaterialItem

    我的世界大门的某个材料

    Attributes
    ----------
    image_path : str
        材料图片路径
    quantity : int
        所需的材料数量
    color : Color = ( 50, 50, 50 )
        文字的颜色（所需的总数）
    sum_quantity : str
        所需的总数（字符串，原始的所需总数或者与用户已有材料比较后的内容）
    """

    image_path: str
    quantity: int
    color: Color = (50, 50, 50)
    sum_quantity: str


class MysekaiGateLevelMaterials(BaseModel):
    r"""MysekaiGateLevelMaterials

    我的世界大门某个等级的材料

    Attributes
    ----------
    level : int
        当前等级
    color : Color = ( 50, 50, 50 )
        文字的颜色（当前等级）
    items: List[ MysekaiGateMaterialItem ]
        当前等级所需的材料
    """

    level: int
    color: Color = (50, 50, 50)
    items: list[MysekaiGateMaterialItem]


class MysekaiGateMaterials(BaseModel):
    r"""MysekaiGateMaterials

    我的世界大门升级材料

    Attributes
    ----------
    id : int
        大门id
    level : Optional[ int ] = None
        大门的当前等级
    gate_icon_path : Optional[ str ] = None
        大门图标路径
    level_materials : List[ MysekaiGateLevelMaterials ]
        大门各个等级所需的材料
    """

    id: int
    level: int | None = None
    gate_icon_path: str | None = None
    level_materials: list[MysekaiGateLevelMaterials]


class MysekaiDoorUpgradeRequest(TimeZoneRequest):
    r"""MysekaiDoorUpgradeRequest

    绘制我的世界大门升级图所必须的数据

    Attributes
    ----------
    profile : Optional[ ProfileCardRequest ] = None
        用户个人信息
    gate_materials : List[ MysekaiGateMaterials ]
        各个大门升级所需的材料
    """

    profile: ProfileCardRequest | None = None
    gate_materials: list[MysekaiGateMaterials]

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.apply_timezone(self.profile)


# =========================== 绘制唱片列表 =========================== #


class MysekaiMusicrecord(BaseModel):
    r"""MysekaiMusicrecord

    我的世界唱片信息

    Attributes
    ----------
    id : Optional[ int ] = None
        当提供id时，会显示id
    image_path : str
        歌曲封面的路径
    obtained : bool
        是否已收集，（未收集将显示为灰色）
    """

    id: int | None = None
    image_path: str
    obtained: bool


class MysekaiCategoryMusicrecord(BaseModel):
    r"""MysekaiCategoryMusicrecord

    我的世界唱片收集列表，同一标签的唱片

    Attributes
    ----------
    tag : str
        标签
    progress_message : Optional[ str ] = None
        收集进度信息
    musicrecords : List[ MysekaiMusicrecord ]
        唱片列表
    """

    tag: str
    tag_icon_path: str
    progress_message: str | None = None
    musicrecords: list[MysekaiMusicrecord]


class MysekaiMusicrecordRequest(TimeZoneRequest):
    r"""MysekaiMusicrecordRequest

    绘制我的世界唱片收集图所必需的数据

    Attributes
    ----------
    profile : ProfileCardRequest
        用户个人信息
    progress_message : Optional[ str ] = None
        收集进度信息
    category_musicrecords : List[ MysekaiCategoryMusicrecord ]
        按tag分类的唱片列表
    """

    profile: ProfileCardRequest
    progress_message: str | None = None
    category_musicrecords: list[MysekaiCategoryMusicrecord]

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.apply_timezone(self.profile)


# =========================== 绘制角色对话列表 =========================== #


class MysekaiTalkFixtures(BaseModel):
    r"""MysekaiTalkFixtures

    我的世界家具组合，未读数量

    Attributes
    ----------
    fixtures : List[ MysekaiFixture ] = []
        家具组合，一个对话可以由多个家具触发
    noread_num : int
        未读的对话数量
    character_ids: Optional[ List[ List[ int ] ] ] = None
        参与对话的角色（当多人对话时需要）
    """

    fixtures: list[MysekaiFixture] = []
    noread_num: int
    character_ids: list[list[int]] | None = None
    chara_icon_path_groups: list[list[str]] | None = None


class MysekaiSingleTalkMainGenre(BaseModel):
    r"""MysekaiSingleTalkMainGenre

    我的世界单人对话家具一级分类

    Attributes
    ----------
    name : str
        主分类名
    image_path : str
        主分类图标路径
    sub_genres: List[ List[ MysekaiTalkFixtures ] ] = []
        单人对话家具组合和未读情况，按子分类分组，每个子分类下是多个家具组合
    """

    name: str
    image_path: str
    sub_genres: list[list[MysekaiTalkFixtures]] = []


class MysekaiTalkListRequest(TimeZoneRequest):
    r"""MysekaiTalkListRequest

    绘制我的世界对话列表所必需的数据

    Attributes
    ----------
    profile : Optional[ ProfileCardRequest ] = None
        个人信息（烤森数据来源，suite数据来源）
    sd_image_path : str
        角色的小人图片路径
    progress_message : Optional[ str ] = None
        收集进度信息
    prompt_message : Optional[str] = None
        提示信息，如：
        *仅展示未读对话家具，灰色表示未获得蓝图
    show_id : bool = False
        是否显示家具id
    single_main_genres : List[ MysekaiSingleTalkMainGenre ] = []
        单人对话，按一级分类分组
    multi_reads : List[ MysekaiTalkFixtures ] = []
        多人对话，按家具组合分组
    """

    profile: ProfileCardRequest | None = None
    sd_image_path: str
    progress_message: str | None = None
    prompt_message: str | None = None
    show_id: bool = False
    single_main_genres: list[MysekaiSingleTalkMainGenre] = Field(default_factory=list)
    multi_reads: list[MysekaiTalkFixtures] = Field(default_factory=list)

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.apply_timezone(self.profile)


class MysekaiHousingCompetitionEntry(BaseModel):
    rank: int
    review_count: int
    owner_user_name: str = ""
    name: str = ""
    word: str = ""
    thumbnail_path: str | None = None
    thumbnail_image_base64: str | None = None
    previous_review_count: int | None = None
    previous_delta: int | None = None
    next_review_count: int | None = None
    next_delta: int | None = None
    submitted_at: int | None = None


class MysekaiHousingCompetitionRequest(TimeZoneRequest):
    competition_id: int
    region: str = "jp"
    name: str
    description: str | None = None
    banner_image_path: str | None = None
    banner_image_base64: str | None = None
    sample_count: int = 0
    unique_count: int = 0
    sampled_at: int | None = None
    entries: list[MysekaiHousingCompetitionEntry] = Field(default_factory=list)


# =========================== 7.0.0: 烤森商店 =========================== #


class MysekaiShopCost(BaseModel):
    """One cost line of a shop item (``mysekaiShopCosts``)."""

    image_path: AssetKey
    quantity: int
    have_quantity: int | None = None


class MysekaiShopItem(BaseModel):
    """One exchangeable entry of a MySekai shop (``mysekaiShops`` / ``mysekaiBlueprintShops`` + its resource box).

    The five state fields are optional and were added after the first contract: an older caller
    that omits all of them gets its state derived from ``exchange_limit_*`` / ``exchanged_count``
    and the request's ``pass_active`` exactly as before.

    - ``available``: the caller's verdict on whether the item can be bought right now.
    - ``owned``: blueprint already in the player's collection (buying is still allowed).
    - ``is_bought``: blueprint bought in the current daily/weekly period.
    - ``remaining_count``: exchanges left in the current limit window.
    - ``material_capacity_count``: how many more exchanges fit in the material warehouse; ``0`` = full.
    """

    id: int
    name: str | None = None
    image_path: AssetKey
    quantity: int = 1
    costs: list[MysekaiShopCost] = Field(default_factory=list)
    exchange_limit_type: str = "none"
    exchange_limit_value: int | None = None
    exchanged_count: int | None = None
    available: bool | None = None
    owned: bool | None = None
    is_bought: bool | None = None
    remaining_count: int | None = None
    material_capacity_count: int | None = None

    @property
    def has_state_fields(self) -> bool:
        """Whether the caller sent any structured state (so status text in ``name`` is redundant)."""

        return any(
            value is not None
            for value in (
                self.available,
                self.owned,
                self.is_bought,
                self.remaining_count,
                self.material_capacity_count,
            )
        )


class MysekaiShop(BaseModel):
    """A shop tab (``mysekaiShopType``: ``material`` / ``tool`` / future types)."""

    shop_type: str
    title: str | None = None
    items: list[MysekaiShopItem] = Field(default_factory=list)


class MysekaiShopRequest(TimeZoneRequest):
    """``POST /api/pjsk/mysekai/shop``."""

    profile: ProfileCardRequest | None = None
    title: str | None = None
    pass_active: bool | None = None
    shops: list[MysekaiShop] = Field(default_factory=list)

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.apply_timezone(self.profile)


# =========================== 7.0.0: 一键采集 =========================== #


class MysekaiBulkHarvestTarget(BaseModel):
    """``mysekaiSiteBulkHarvestTargets`` row available on a site."""

    id: int
    name: str
    image_path: AssetKey | None = None
    checked: bool | None = None
    fixture_count: int | None = None


class MysekaiBulkHarvestTargetGroup(BaseModel):
    """``mysekaiSiteBulkHarvestTargetGroups`` row (with its required tool)."""

    id: int
    name: str
    required_tool_name: str | None = None
    required_tool_image_path: AssetKey | None = None
    targets: list[MysekaiBulkHarvestTarget] = Field(default_factory=list)


class MysekaiBulkHarvestSite(BaseModel):
    site_id: int
    name: str
    image_path: AssetKey | None = None
    groups: list[MysekaiBulkHarvestTargetGroup] = Field(default_factory=list)


class MysekaiBulkHarvestRequest(TimeZoneRequest):
    """``POST /api/pjsk/mysekai/bulk-harvest``."""

    profile: ProfileCardRequest | None = None
    sites: list[MysekaiBulkHarvestSite] = Field(default_factory=list)

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.apply_timezone(self.profile)


# =========================== 7.0.0: 期间限定蓝图 =========================== #


class MysekaiBlueprintTermMaterial(BaseModel):
    """``mysekaiBlueprintTermMysekaiMaterialCosts`` row."""

    image_path: AssetKey
    quantity: int
    have_quantity: int | None = None


class MysekaiBlueprintTermEntry(BaseModel):
    id: int
    name: str
    image_path: AssetKey
    start_at: int | None = None
    end_at: int | None = None
    craft_limit: int | None = None
    craft_count: int | None = None
    cost_materials: list[MysekaiBlueprintTermMaterial] = Field(default_factory=list)


class MysekaiBlueprintTermTab(BaseModel):
    """``mysekaiBlueprintTermTabType`` tab: ``limited_term`` / ``birthday_anniversary`` / future types."""

    tab_type: str
    title: str | None = None
    blueprints: list[MysekaiBlueprintTermEntry] = Field(default_factory=list)


class MysekaiBlueprintTermRequest(TimeZoneRequest):
    """``POST /api/pjsk/mysekai/blueprint-term``."""

    profile: ProfileCardRequest | None = None
    tabs: list[MysekaiBlueprintTermTab] = Field(default_factory=list)

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.apply_timezone(self.profile)


# 超出团色表的大门（例如 JP 7.0.0 的 6 号「交わるセカイのゲート」，mysekaiGateType=shuffle）使用的颜色。
GATE_FALLBACK_COLOR: Color = (51, 204, 187, 255)


class _GateColorList(list):
    """Unit colour table indexed by ``gate_id - 1`` that tolerates gates beyond the table.

    The (out-of-repo) MySekai drawer indexes ``UNIT_COLORS[gate_id - 1]``. Gate ids are data, not a
    fixed five: a non-negative index past the end returns :data:`GATE_FALLBACK_COLOR` instead of
    raising. Negative indexes and slices keep plain ``list`` semantics, so ``gate_id == 0`` still
    resolves exactly as before.
    """

    def __getitem__(self, index):
        if isinstance(index, int) and not isinstance(index, bool) and index >= len(self):
            return GATE_FALLBACK_COLOR
        return super().__getitem__(index)


# 各团代表色，没有VS团！
UNIT_COLORS = _GateColorList(
    [
        (68, 85, 221, 255),
        (136, 221, 68, 255),
        (238, 17, 102, 255),
        (255, 153, 0, 255),
        (136, 68, 153, 255),
    ]
)


def gate_color(gate_id: int) -> Color:
    """Colour of a MySekai gate; gates without a unit colour (shuffle, future ids) get the fallback."""

    if gate_id < 1:
        return GATE_FALLBACK_COLOR
    return UNIT_COLORS[gate_id - 1]


# 唱片tag到团名映射
MUSIC_TAG_UNIT_MAP = {
    "light_music_club": "light_sound",
    "street": "street",
    "idol": "idol",
    "theme_park": "theme_park",
    "school_refusal": "school_refusal",
    "vocaloid": "piapro",
    "other": None,
}
