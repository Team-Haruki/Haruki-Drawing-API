from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union

from PIL.Image import Image

from ..base import plot
from ..base import painter
from ..base import img_utils


@dataclass
class ServiceEndpoints:
    """定义所有外部API的端点"""
    profile_api_url: Optional[str] = None
    suite_api_url: Optional[str] = None


@dataclass
class ExecutionContext:
    """
    定义单次请求的上下文信息。
    """
    region: str
    user_id: int
    game_uid: str

@dataclass
class UserPreferences:
    """用户的个人设置"""
    hide_game_id: bool = False
    hide_suite_data: bool = False
    data_fetch_mode: str = 'latest'
    profile_bg_blur: int = 4
    profile_bg_alpha: int = 150
    profile_bg_is_vertical: bool = False

@dataclass
class MasterDataCard:
    """卡牌主数据"""
    id: int
    characterId: int
    cardRarityType: str
    attr: str
    supportUnit: str
    assetbundleName: str

@dataclass
class MasterDataHonor:
    """称号主数据"""
    id: int
    honorType: str
    honorGroup: str
    name: str
    assetbundleName: str

@dataclass
class MasterDataCharacter:
    """角色主数据"""
    id: int
    firstName: str
    givenName: str

@dataclass
class MasterDataBundle:
    """MasterData打包"""
    cards: Dict[int, MasterDataCard] = field(default_factory=dict)
    honors: Dict[int, MasterDataHonor] = field(default_factory=dict)
    characters: Dict[int, MasterDataCharacter] = field(default_factory=dict)

@dataclass
class PlayerCard:
    """玩家持有的卡牌信息"""
    cardId: int
    level: int
    masterRank: int
    defaultImage: str
    specialTrainingStatus: str

@dataclass
class PlayerDeck:
    """玩家卡组信息"""
    member1: int
    member2: int
    member3: int
    member4: int
    member5: int

@dataclass
class PlayerProfileInfo:
    """玩家的个人简介信息"""
    word: str
    twitterId: str

@dataclass
class PlayerHonor:
    """玩家佩戴的称号"""
    seq: int
    honorId: int
    honorLevel: int

@dataclass
class PlayerCharacter:
    """玩家的角色等级信息"""
    characterId: int
    characterRank: int


@dataclass
class BasicPlayerProfile:
    """玩家基本信息"""
    user: Dict[str, Any]
    userCards: List[PlayerCard]
    userDeck: PlayerDeck
    userProfile: PlayerProfileInfo
    userProfileHonors: List[PlayerHonor]
    userCharacters: List[PlayerCharacter]
    userMusicDifficultyClearCount: List[Dict[str, Any]]
    userChallengeLiveSoloResult: Optional[Dict[str, Any]] = None
    userChallengeLiveSoloStages: List[Dict[str, Any]] = field(default_factory=list)
    update_time: int

@dataclass
class StaticAssetBundle:
    """
    静态资源包，包含所有需要的图片和字体。
    资源可以用文件名作为key，内容(bytes)作为value。
    """
    images: Dict[str, bytes] = field(default_factory=dict)
    fonts: Dict[str, bytes] = field(default_factory=dict)

@dataclass
class DynamicAssetBundle:
    """
    动态资源包，包含需要实时下载或生成的资源，如卡面缩略图。
    """
    card_thumbnails: Dict[str, bytes] = field(default_factory=dict)
    user_profile_background: Optional[bytes] = None

@dataclass
class ComposeProfileImageRequest:
    """生成个人信息图片的请求"""
    context: ExecutionContext
    preferences: UserPreferences
    player_profile: BasicPlayerProfile
    master_data: MasterDataBundle
    static_assets: StaticAssetBundle
    dynamic_assets: DynamicAssetBundle
    vertical_layout: Optional[bool] = None # 允许覆盖用户偏好

@dataclass
class ComposeProfileImageResponse:
    """生成个人信息图片的响应"""
    image_bytes: bytes
    format: str = "PNG"
    warnings: List[str] = field(default_factory=list)

import io
from dataclasses import dataclass, field
from typing import Dict, Optional

from PIL import Image, ImageDraw, ImageFont

# ===================================================================
# 1. 依赖的 Dataclass 定义
# 这些是你之前设计的通用模型，用于承载所有输入数据。
# ===================================================================

@dataclass
class MasterDataCard:
    """卡牌主数据模型"""
    id: int
    attr: str
    cardRarityType: str
    # 你可以根据需要添加更多字段，如 characterId, supportUnit 等
    # ...

@dataclass
class PlayerCard:
    """玩家持有的卡牌数据模型 (pcard)"""
    cardId: int
    level: int
    masterRank: int
    defaultImage: str
    specialTrainingStatus: str

@dataclass
class ComposeCardThumbnailResponse:
    """函数的返回值模型"""
    image_bytes: bytes
    format: str = "PNG"
    cache_key: Optional[str] = None


# ===================================================================
# 2. 核心绘制函数
# ===================================================================

def compose_card_thumbnail_from_bundles(
        card_id: int,
        card_art_bytes: bytes,
        master_data: MasterDataBundle,
        static_assets: StaticAssetBundle,
        pcard: Optional[PlayerCard] = None,
        custom_text: Optional[str] = None,
        after_training_override: bool = False
) -> ComposeCardThumbnailResponse:
    """
    一个纯函数，从通用的数据捆绑包中获取信息来合成卡牌缩略图。

    Args:
        card_id (int): 要绘制的卡牌ID。
        card_art_bytes (bytes): 卡牌的基础缩略图（卡面）的二进制数据。
        master_data (MasterDataBundle): 包含卡牌主数据的捆绑包。
        static_assets (StaticAssetBundle): 包含所有UI图片和字体的捆绑包。
        pcard (Optional[PlayerCard]): 玩家的卡牌信息，如果提供，则会绘制等级、大师等级等。
        custom_text (Optional[str]): 自定义文本，会覆盖等级显示。
        after_training_override (bool): 当没有pcard时，强制指定是否使用特训后素材。

    Returns:
        ComposeCardThumbnailResponse: 包含最终图片二进制数据和缓存键的响应对象。

    Raises:
        ValueError: 如果需要的数据或资源在捆绑包中不存在。
    """
    card = master_data.cards.get(card_id)
    if not card:
        raise ValueError(f"Card ID {card_id} not found in MasterDataBundle")

    if not pcard:
        is_trainable = card.cardRarityType in ["rarity_3", "rarity_4"]
        after_training = after_training_override and is_trainable
        rare_image_type = "after_training" if after_training else "normal"
    else:
        after_training = pcard.defaultImage == "special_training"
        rare_image_type = "after_training" if pcard.specialTrainingStatus == "done" else "normal"

    img = Image.open(io.BytesIO(card_art_bytes)).convert("RGBA")

    def get_image_from_bundle(key: str) -> Image.Image:
        img_bytes = static_assets.images.get(key)
        if not img_bytes:
            raise ValueError(f"Asset '{key}' not found in StaticAssetBundle")
        return Image.open(io.BytesIO(img_bytes)).convert("RGBA")

    bold_font_bytes = static_assets.fonts.get("DEFAULT_BOLD_FONT")
    if not bold_font_bytes:
        raise ValueError("DEFAULT_BOLD_FONT not found in StaticAssetBundle")
    font_20 = ImageFont.truetype(io.BytesIO(bold_font_bytes), 20)

    frame_img = get_image_from_bundle(f"card/frame_{card.cardRarityType}.png")
    attr_img = get_image_from_bundle(f"card/attr_{card.attr}.png")

    if card.cardRarityType == "rarity_birthday":
        rare_img = get_image_from_bundle("card/rare_birthday.png")
        rare_num = 1
    else:
        rare_img = get_image_from_bundle(f"card/rare_star_{rare_image_type}.png")
        try:
            rare_num = int(card.cardRarityType.split("_")[1])
        except (IndexError, ValueError):
            raise ValueError(f"Cannot parse rarity number from '{card.cardRarityType}'")

    img = img.copy()
    img_w, img_h = img.size

    if pcard:
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
        text_to_draw = custom_text if custom_text is not None else f"Lv.{pcard.level}"
        draw.text((6, img_h - 31), text_to_draw, font=font_20, fill=(255, 255, 255))

    frame_img = frame_img.resize((img_w, img_h), Image.LANCZOS)
    img.paste(frame_img, (0, 0), frame_img)

    if pcard and pcard.masterRank > 0:
        try:
            rank_img = get_image_from_bundle(f"card/train_rank_{pcard.masterRank}.png")
            rank_img = rank_img.resize((int(img_w * 0.35), int(img_h * 0.35)), Image.LANCZOS)
            rank_img_w, rank_img_h = rank_img.size
            img.paste(rank_img, (img_w - rank_img_w, img_h - rank_img_h), rank_img)
        except ValueError:
            # 如果资源不存在，静默失败，不绘制大师等级
            pass

    attr_img = attr_img.resize((int(img_w * 0.22), int(img_h * 0.25)), Image.LANCZOS)
    img.paste(attr_img, (1, 0), attr_img)

    hoffset, voffset = 6, 6 if not pcard else 24
    scale = 0.17 if not pcard else 0.15
    rare_img = rare_img.resize((int(img_w * scale), int(img_h * scale)), Image.LANCZOS)
    rare_w, rare_h = rare_img.size
    for i in range(rare_num):
        img.paste(rare_img, (hoffset + rare_w * i, img_h - rare_h - voffset), rare_img)

    mask = Image.new('L', (img_w, img_h), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rounded_rectangle((0, 0, img_w, img_h), radius=10, fill=255)
    img.putalpha(mask)

    output_buffer = io.BytesIO()
    img.save(output_buffer, format="PNG")

    cache_key = None
    if not pcard:
        image_type = "after_training" if after_training else "normal"
        cache_key = f"{card.id}_{image_type}.png"

    return ComposeCardThumbnailResponse(
        image_bytes=output_buffer.getvalue(),
        cache_key=cache_key
    )
