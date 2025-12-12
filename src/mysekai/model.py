from pydantic import BaseModel
from typing import (
    Optional,
    List,
    Dict,
    Literal
)
from src.base.painter import Color
from src.profile.model import ProfileCardRequest

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
    text : str
        文字，天气更改时间
    text_fill: Color = (125, 125, 125, 255)
        文字颜色
    """
    refresh_reason: str
    image_path: str
    background_fill: Color = (255, 255, 255, 75)
    text: str
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
    text_color : Color = (100, 100, 100)
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
    profile : ProfileCardRequest
        用户个人信息
    background_image_path : Optional[ str ] = None
        背景图片路径
    phenoms : List[ MysekaPhenomRequest ]
        天气表，绘制天气预报
    gate_id : int
        大门id
    gate_level : int
        大门等级
    visit_characters: List[ MysekaiVisitCharacter ]
        到访的角色列表
    site_resource_numbers: Optional[ List[ MysekaiSiteResourceNumber ] ] = None
        每个地区的资源数量列表
    error_message : Optional[ str ] = None
        错误信息
    """
    profile: ProfileCardRequest
    background_image_path: Optional[str] = None 
    phenoms: List[MysekaiPhenomRequest]
    gate_id: int
    gate_level: int
    visit_characters: List[MysekaiVisitCharacter]
    site_resource_numbers: Optional[List[MysekaiSiteResourceNumber]] = None
    error_message: Optional[str] = None

class MysekaiSingleFixture(BaseModel):
    r"""MysekaiSingleFixture

    我的世界单个家具信息

    Attributes
    ----------
    id : int
        家具的id
    image_path : str
        家具的图片
    character_id : Optional[ int ] = None
        角色id，如果是生日家具，在上面绘制对应的角色图片
    obtained : bool = True
        是否已拥有家具，未拥有的家具将显示为灰色
    """
    id: int
    image_path: str
    character_id: Optional[int] = None
    obtained: bool = True

class MysekaiFixtureSubGenre(BaseModel):
    r"""MysekaiFixtureSubGenre

    我的世界家具子分类信息

    Attributes
    ----------
    title : Optional[ str ] = None
        分类标题，标签
    image_path : Optional[ str ] = None
        分类图片
    progress_message : Optional[ str ] = None
        分类收集进度信息
    fixtures : List[ MysekaiSingleFixture ] = [ ]
        分类中的家具列表
    """
    title: Optional[str] = None
    image_path: Optional[str] = None
    progress_message: Optional[str] = None
    fixtures: List[MysekaiSingleFixture] = []

class MysekaiFixtureMainGenre(BaseModel):
    r"""MysekaiFixtureMainGenre

    我的世界家具主分类信息

    Attributes
    ----------
    title : str
        分类标题，标签
    image_path : str
        分类图片
    progress_message : Optional[ str ] = None
        分类收集进度信息
    sub_genres : List[ MysekaiFixtureSubGenre ] = [ ]
        分类中的子分类列表
    """
    title: str
    image_path: str
    progress_message: Optional[str] = None
    sub_genres: List[MysekaiFixtureSubGenre] = []

class MysekaiFixtureListRequest(BaseModel):
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
    error_message : Optional[ str ] = None
        错误信息
    """
    profile: Optional[ProfileCardRequest] = None
    progress_message: Optional[str] = None
    show_id: bool = False
    main_genres: List[MysekaiFixtureMainGenre] = []
    error_message: Optional[str] = None

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
    image_path: str
    color_code: Optional[str] = None

class MysekaiFixtureMaterial(BaseModel):
    r"""MysekaiFixtureMaterial

    我的世界家具材料，制作材料或回收素材

    Attributes
    ----------
    image_path : str
        图标路径
    text : str
        文本内容
    """
    image_path: str
    text: str

class MysekaiReactionCharacterGroups(BaseModel):
    r"""MysekaiReactionCharacterGroups

    我的世界互动角色组，和某个家具互动的角色们
    
    与家具互动

    Attributes
    ----------
    number : int
        每组的角色数量
    character_uint_id_groups : List[ List[ int ] ]
        角色id列表，按组分
    """
    number: int
    character_uint_id_groups: List[List[int]]

class MysekaiFixtureDetailRequest(BaseModel):
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
    basic_info: Optional[ List[ List[ str ] ] ] = None
        其它基本信息，可写多行，每行一个字符串，每行可写多个
    cost_materials : Optional[ List[ MysekaiFixtureMaterial ] ] = None
        制造家具所需的素材
    recycle_materials : Optional[ List[ MysekaiFixtureMaterial ] ] = None
        回收家具返还的素材
    reaction_character_groups : Optional[ List[ MysekaiReactionCharacterGroups ] ] = None
        互动角色组，与家具互动的角色们
    tags : Optional[ List[ List[ str ] ] ] = None
        家具标签，可写多行，每行一个字符串，每行可写多个标签
    friendcodes: Optional[ List[ List[ str ] ] ] = None
        可抄写家具的好友码，可写多行，每行一个字符串，每行可写多个
    friendcode_source: Optional[ str ] = None
        好友码来源
    """
    title: str
    images: List[MysekaiFixtureColorImage]
    main_genre_name: str
    main_genre_image_path: str
    sub_genre_name: Optional[str] = None
    sub_genre_image_path: Optional[str] = None
    size: Dict[Literal['width', 'depth', 'height'], int]
    basic_info: Optional[List[List[str]]] = None
    cost_materials: Optional[List[MysekaiFixtureMaterial]] = None
    recycle_materials: Optional[List[MysekaiFixtureMaterial]] = None
    reaction_character_groups: Optional[List[MysekaiReactionCharacterGroups]] = None
    tags: Optional[List[List[str]]] = None
    friendcodes: Optional[List[List[str]]] = None
    friendcode_source: Optional[str] = None

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
    items: List[MysekaiGateMaterialItem]

class MysekaiGateMaterials(BaseModel):
    r"""MysekaiGateMaterials

    我的世界大门升级材料

    Attributes
    ----------
    id : int
        大门id
    level : Optional[ int ] = None
        大门的当前等级
    level_materials : List[ MysekaiGateLevelMaterials ]
        大门各个等级所需的材料
    """
    id: int
    level: Optional[int] = None
    level_materials: List[MysekaiGateLevelMaterials]


class MysekaiDoorUpgradeRequest(BaseModel):
    r"""MysekaiDoorUpgradeRequest

    绘制我的世界大门升级图所必须的数据

    Attributes
    ----------
    profile : Optional[ ProfileCardRequest ] = None
        用户个人信息
    gate_materials : List[ MysekaiGateMaterials ]
        各个大门升级所需的材料
    """
    profile: Optional[ProfileCardRequest] = None
    gate_materials: List[MysekaiGateMaterials]

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
    id: Optional[int] = None
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
    progress_message: Optional[str] = None
    musicrecords: List[MysekaiMusicrecord]

class MysekaiMusicrecordRequest(BaseModel):
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
    progress_message: Optional[str] = None
    category_musicrecords: List[MysekaiCategoryMusicrecord]

# 各团代表色，没有VS团！
UNIT_COLORS = [
    (68,85,221,255),
    (136,221,68,255),
    (238,17,102,255),
    (255,153,0,255),
    (136,68,153,255),
]

# 唱片tag到团名映射
MUSIC_TAG_UNIT_MAP = {
    'light_music_club': 'light_sound',
    'street': 'street',
    'idol': 'idol',
    'theme_park': 'theme_park',
    'school_refusal': 'school_refusal',
    'vocaloid': 'piapro',
    'other': None,
}