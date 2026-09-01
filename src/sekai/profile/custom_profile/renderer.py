#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import ctypes.util
import json
import math
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from src.core.path_safety import resolve_cli_path

Image.MAX_IMAGE_PIXELS = None

from src.sekai.honor.drawer import compose_full_honor_image_from_loaded_assets, honor_group_uses_scroll_level
from src.sekai.honor.model import HonorRequest
from src.sekai.profile.custom_profile.cache import (
    GLYPH_CONTOUR_CACHE,
    GLYPH_SDF_CACHE,
    MISSING,
    SPRITE_ATLAS_CACHE,
    file_signature,
    get_render_font,
    get_tmp_font_tables,
    optional_file_signature,
)
from src.sekai.profile.custom_profile.card_prefab import (
    CardAlphaMaskOp,
    CardDisplayList,
    CardFontRef,
    CardPrefabResources,
    CardSpriteRef,
    PillowCardAdapter,
    build_card_rarity_ops,
    build_deck_card_display_list as build_deck_card_prefab_display_list,
    build_deck_card_level_ops,
    build_deck_card_overlay_ops,
    build_deck_leader_label_op,
    build_empty_deck_card_display_list,
    build_full_card_display_list as build_full_card_prefab_display_list,
    build_full_card_overlay_ops,
)
from src.sekai.profile.custom_profile.collection_prefab import (
    OMIKUJI_RESULT_NATIVE_SIZE,
    PillowOmikujiAdapter,
    build_omikuji_display_list,
)
from src.sekai.profile.custom_profile.general_prefab import (
    CHARA_LIST,
    CHARACTER_RANK_CELL_SIZE,
    GeneralPrefabPalette,
    PillowGeneralPrefabAdapter,
    build_general_prefab_display_list,
    ordered_story_favorites as order_story_favorites,
    story_favorite_asset_key,
    story_favorite_key as build_story_favorite_key,
    story_favorite_title as resolve_story_favorite_title,
)
from src.sekai.profile.custom_profile.honor_deck_prefab import build_honor_deck_plan
from src.sekai.profile.custom_profile.limits import RasterSizeLimitError, ensure_raster_size
from src.sekai.profile.custom_profile.svg import (
    CANVAS_H,
    CANVAS_W,
    DEFAULT_ASSETS,
    DEFAULT_FONTS,
    DEFAULT_MASTERDATA,
    DEFAULT_PROFILE,
    GAME_VIEWPORT_H,
    SHAPE_OUTLINE_SCALE_FACTOR,
    TextBreak,
    TextRun,
    TextStyle,
    TextStyleMarker,
    color_or,
    load_index,
    load_json,
    parse_tmp_text,
    split_runs_by_line,
    unity_point,
    unity_rotation_degrees,
)
from src.sekai.profile.custom_profile.split import (
    build_custom_profile_render_request,
    build_profile_context,
    custom_profile_output_name,
    decode_custom_profile_render_request,
    normalize_profile_payload,
    select_custom_profile_cards,
    write_json,
)


TMP_EM_BLOCK_CHARS = {"■", "█"}
TMP_SPACE_EQUIVALENT_CHARS = {" ", "\u00a0"}
TMP_MISSING_GLYPH_CHAR = "□"
TMP_DECORATIVE_TEXT_CHARS = frozenset("●○■█▲△▼▽◣◢◤◥⌒～〜∽︵︶︿()（）【】、，,.-·|^*/\\I丶>〇 ")
DEFAULT_TMP_DECORATIVE_FACE_ONLY = True
DEFAULT_TMP_DECORATIVE_DIRECT_RASTER = True
DEFAULT_PREMULTIPLY_ALPHA_TRANSFORMS = False
DEFAULT_MAX_SCENE_BYTES = 256 * 1024 * 1024
_CARD_ASSET_STATE_KEYS = {
    ("deck", False): ("deckNormalPath", "deck_normal_path"),
    ("deck", True): ("deckAfterTrainingPath", "deck_after_training_path"),
    ("clip", False): ("deckNormalPath", "deck_normal_path", "clipNormalPath", "clip_normal_path"),
    ("clip", True): (
        "deckAfterTrainingPath",
        "deck_after_training_path",
        "clipAfterTrainingPath",
        "clip_after_training_path",
    ),
    ("small", False): ("smallNormalPath", "small_normal_path"),
    ("small", True): ("smallAfterTrainingPath", "small_after_training_path"),
    ("full", False): ("normalPath", "normal_path"),
    ("full", True): ("afterTrainingPath", "after_training_path"),
}
TMP_DEFAULT_TEXT_BOX_W = 108.0
TMP_TEXT_BOX_W_SIZE_FACTOR = 1.6
TMP_LINE_HEIGHT_FACTOR = 1.0
DEFAULT_TMP_TEXT_RENDER_MODE = "sdf"
DEFAULT_TMP_DYNAMIC_SDF = True
# Native TextContentView writes outlineSize to TMP _UnderlayDilate, not to a
# geometric stroke width. The old PIL stroke factor is kept only as a probe.
DEFAULT_TMP_PILLOW_STROKE_FACTOR = 0.0
# CustomProfileUtility.RefreshParams default for
# custom_profile_text_line_spacing_factor, confirmed in UnityFramework ASM.
TMP_LINE_SPACING_FACTOR = 1.325
# TextContentView.LateUpdate writes the active TMP and outer content
# RectTransform.sizeDelta to (preferredWidth + 64, preferredHeight + 64).
TMP_PREFERRED_PADDING_X = 64.0
TMP_PREFERRED_PADDING_Y = 64.0
TMP_FACE_POINT_SIZE = 75.0
TMP_FACE_SCALE = 2.0
TMP_FACE_LINE_HEIGHT = 150.0
TMP_PREFAB_FONT_SIZE = 5.050000190734863
TMP_PREFAB_TEXT_BOX_W = 8.460000038146973
TMP_PREFAB_TEXT_BOX_H = 5.650000095367432
TMP_PREFAB_LINE_SPACING_DELTA = 0.0
TMP_PREFAB_PARAGRAPH_SPACING = 0.0
TMP_SHADER_RATIO_CLAMP = 1.0
TMP_SHADER_PADDING_CONSTANT = 1.25
TMP_EXTRA_PADDING = 4.0
TMP_VERTEX_PADDING_EXTRA = TMP_SHADER_PADDING_CONSTANT
# The exact value is written by TMP's generated mesh in TEXCOORD1.y. Static
# captures are not available yet; faceInfo.scale is the reverse-backed fallback
# for the temporary SDF path and keeps the shader formula explicit.
# The native TMP quad path needs a sharper fallback scale than the old 2.0
# probe; 10.0 matches captured symbol edges and removes the faint SDF halo.
DEFAULT_TMP_TEXCOORD1_Y = 10.0
TMP_LARGE_POSITIVE_FLOAT = 32767.0
TMP_LARGE_NEGATIVE_FLOAT = -32767.0

# Confirmed from resources.assets:
# ScreenLayerCustomProfileViewer/ProfileCardView RectTransform is 1830 x 813.
# The official cropped screenshots used by the lab are normalized to 2048 x 909.
PROFILE_CARD_VIEW_W = 1830.0
PROFILE_CARD_VIEW_H = 813.0
PROFILE_RENDER_VIEW_W = float(CANVAS_W)
PROFILE_RENDER_VIEW_H = float(GAME_VIEWPORT_H)
PROFILE_POSITION_SCALE_X = PROFILE_RENDER_VIEW_W / PROFILE_CARD_VIEW_W
PROFILE_POSITION_SCALE_Y = PROFILE_RENDER_VIEW_H / PROFILE_CARD_VIEW_H
PROFILE_CAPTURE_DISPLAY_SCALE = min(PROFILE_POSITION_SCALE_X, PROFILE_POSITION_SCALE_Y)

DEFAULT_POSITION_SCALE_X = PROFILE_CAPTURE_DISPLAY_SCALE
DEFAULT_POSITION_SCALE_Y = PROFILE_CAPTURE_DISPLAY_SCALE
DEFAULT_POSITION_SCALE = (DEFAULT_POSITION_SCALE_X + DEFAULT_POSITION_SCALE_Y) / 2.0
DEFAULT_TEXT_PIVOT = "center"
DEFAULT_TMP_SCALE_MODE = "fx-native"
# TMP local images are already emitted in screen-raster orientation, so the
# native RectTransform z rotation can be applied with the same sign.
DEFAULT_ROTATION_SIGN = 1
DEFAULT_TMP_FONT_SCALE = TMP_FACE_SCALE
# Isolated oversized filled-square glyphs are normal TMP glyphs in the native
# path. The em/source block modes remain explicit historical probes only.
DEFAULT_TMP_BLOCK_MODE = "glyph"
DEFAULT_TEXT_VERTICAL_MODE = "tmp-native"
DEFAULT_TMP_SPACE_WIDTH_FACTOR = 1.0
DEFAULT_TMP_NATIVE_LINE_GAP = True
# The current offline SDFAA approximation matches Unity's runtime atlas more
# closely when it is generated at the native point-size atlas resolution.
TMP_DYNAMIC_SDF_SUPERSAMPLE = 1.0
TMP_DYNAMIC_SDF_MAX_CHARACTER_SCALE = 2.0
# TextCore SDFAA atlases generated by Unity use the no-hinting FreeType metrics
# below, but the alpha cutoff that best matches runtime atlas pixels is above
# the usual 0.5 coverage threshold and varies slightly by source font.
TMP_DYNAMIC_SDF_ALPHA_THRESHOLD = 160
TMP_DYNAMIC_SDF_ALPHA_THRESHOLD_BY_FONT = {
    "FOT-RodinNTLGPro-DB": 144,
    "FOT-PopHappinessStd-EB": 160,
    "FOT-SkipProN-B": 176,
}
TMP_DYNAMIC_SDF_DISTANCE_MASK_SIZE = 5
# Unity TextCore SDFAA is generated from the font outline, not from a
# thresholded bitmap. The small spread bias below matches captured runtime
# atlases where _GradientScale is 6 for padding 5.
TMP_DYNAMIC_SDF_VECTOR_SPREAD_BIAS = 0.1
TMP_DYNAMIC_SDF_VECTOR_CURVE_STEPS = 24
TMP_PERCENT_INDENT_MAX_MARGIN_WIDTH = 50000.0
SHAPE_SDF_RATIO_SCALE = 1.0
# ShapeContentView.set_OutlineSize writes
# DistanceFieldImage.OutlineFillRatio = clamp(outlineSize, 0, 1) * 0.95.
SHAPE_NATIVE_OUTLINE_FILL_RATIO_FACTOR = 0.95
# Sekai/UI/DistanceField uses _OuterFillRatio directly in the fragment shader.
# ShapeContentView.LateUpdate then writes FaceDilate as
# -0.5 * clamp(OutlineFillRatio, 0, 1), which is -0.475 * outlineSize after
# set_OutlineSize.
SHAPE_NATIVE_FACE_DILATE_FACTOR = -0.5 * SHAPE_NATIVE_OUTLINE_FILL_RATIO_FACTOR
SHAPE_SDF_OUTER_FACTOR = 1.0
SHAPE_SDF_FACE_FACTOR = SHAPE_NATIVE_FACE_DILATE_FACTOR
# resources.assets DistanceFieldImage prefab serializes OutlineSoftness as 0
# and DistanceFieldImage.Update pushes that field to the material every frame.
SHAPE_SDF_SOFTNESS = 0.0
SHAPE_SDF_SCREEN_FWIDTH = True
DEFAULT_MAX_LAYER_PIXELS = 8 * 1024 * 1024
LAYER_ROTATION_SUPERSAMPLE = 2.0
RODIN_FONT_VARIANTS = ("ttf", "otf", "auto")
TMP_BLOCK_MODES = ("large-em", "source-glyph", "em", "glyph")
TMP_TEXT_RENDER_MODES = ("sdf", "pil")
TMP_BOX_MODES = ("size", "size-full", "fixed", "content", "preferred", "prefab")
TMP_SCALE_MODES = ("x", "uniform", "fx-center", "fx-native")
DRAW_ORDER_MODES = ("global", "shapes-first", "white-text-last")
SHAPE_OUTLINE_MODES = ("ring", "underlay", "dilate", "sdf")
TRIANGLE_MODES = ("asset", "sprite", "sharp")
SHAPE_SDF_SOURCES = ("rgb", "alpha")
TEXT_VERTICAL_MODES = (
    "tmp-native",
    "tmp-native-top",
    "bbox-center",
    "anchor-middle",
    "font-ascent",
    "font-metrics",
    "pil-mm",
)
TMP_METRIC_MODES = ("pil", "asset", "asset-fallback")
DEFAULT_TMP_LINE_MODE = "asset-face-scale"
DEFAULT_TMP_METRICS_MODE = "asset-fallback"
DEFAULT_TMP_FONT_METADATA = Path(__file__).resolve().parent / "out" / "tmp-font-assets" / "cn" / "metadata.json"
DEFAULT_TMP_BASE_FONT_METADATA = Path(__file__).resolve().parent / "out" / "tmp-font-assets" / "cn" / "metadata.json"
DEFAULT_SHAPE_SPRITE_DIR = Path(__file__).resolve().parent / "out" / "shape-bundle" / "sprites-native"
DEFAULT_UNITY_UI_SPRITE_DIR = Path(__file__).resolve().parent / "out" / "unity-ui-sprites-cn" / "sprites-native"
DEFAULT_TRIANGLE_MODE = "asset"
DEFAULT_PARALLEL_WORKERS = 1

TMP_HORIZONTAL_CENTER = 0x002
TMP_HORIZONTAL_RIGHT = 0x004
TMP_HORIZONTAL_JUSTIFIED = 0x008
TMP_HORIZONTAL_FLUSH = 0x010
TMP_HORIZONTAL_GEOMETRY = 0x020
TMP_VERTICAL_TOP = 0x0100
TMP_VERTICAL_BOTTOM = 0x0400
TMP_VERTICAL_BASELINE = 0x0800
TMP_VERTICAL_GEOMETRY = 0x1000
TMP_VERTICAL_CAPLINE = 0x2000

CONTENT_TYPES: dict[str, tuple[int, str]] = {
    "general": (1, "General"),
    "general_background": (2, "GeneralBackground"),
    "story_background": (3, "StoryBackground"),
    "stand_member": (4, "StandMember"),
    "card_member": (5, "CardMember"),
    "honor": (6, "Honor"),
    "bonds_honor": (6, "Honor"),
    "text": (7, "Text"),
    "other": (8, "Other"),
    "shape": (9, "Shape"),
    "collection": (10, "Collection"),
    "stamp": (11, "Stamp"),
    "mini_chara": (12, "DynamicMiniChara"),
    "screen_filter": (12, "DynamicScreenFilter"),
    "character_icon": (13, "CharacterIcon"),
    "material": (14, "Material"),
    "user_interface_icon": (15, "UserInterfaceIcon"),
}
STATIC_IMAGE_CONTENT_KINDS = {
    "general_background",
    "story_background",
    "stand_member",
    "collection",
    "other",
    "character_icon",
    "material",
    "user_interface_icon",
}
REGION_ASSET_STARTAPP = "startapp"
REGION_ASSET_ONDEMAND = "ondemand"
ONDEMAND_PREFERRED_TOP_LEVEL = {
    "event",
    "event_story",
    "gacha",
    "lottery_game",
    "mysekai",
    "virtual_live",
}
FC_AP_HONOR_IDS = {
    3009,
    3010,
    3011,
    3012,
    3013,
    3014,
    4700,
    4701,
}
IMAGE_CONTENT_TYPE_NAMES = {
    "general_background": "GeneralBackground",
    "story_background": "StoryBackground",
    "stand_member": "StandMember",
    "collection": "Collection",
    "other": "Other",
    "stamp": "Stamp",
    "character_icon": "CharacterIcon",
    "material": "Material",
    "user_interface_icon": "UserInterfaceIcon",
}
PREFAB_NATIVE_SIZES: dict[str, tuple[float, float]] = {
    "ClipSizeCardContentView": (328.0, 520.0),
    "FullSizeCardContentView": (940.0, 530.0),
    "HonorContentView": (380.0, 80.0),
    "ImageContentView": (178.0, 154.0),
    "CollectionCustomPrefabContentView": (178.0, 154.0),
    "DynamicProfileContentView": (178.0, 154.0),
}
CLIP_CARD_MEMBER_ART_SIZE = (328.0, 538.2559814453125)
CLIP_CARD_MEMBER_NATIVE_SIZE = (
    round(PREFAB_NATIVE_SIZES["ClipSizeCardContentView"][0]),
    round(PREFAB_NATIVE_SIZES["ClipSizeCardContentView"][1]),
)
FULL_CARD_MEMBER_NATIVE_SIZE = (
    round(PREFAB_NATIVE_SIZES["FullSizeCardContentView"][0]),
    round(PREFAB_NATIVE_SIZES["FullSizeCardContentView"][1]),
)
GENERAL_NATIVE_SIZES: dict[str, tuple[int, int]] = {
    # Static data.unity3d prefab RectTransform.sizeDelta values, extracted
    # into out/general-template-prefabs-cn/metadata.json.
    "X": (548, 64),
    "EditUserName": (548, 64),
    "Comment": (638, 190),
    "TotalPower": (752, 76),
    "Deck": (783, 242),
    "LeaderCard": (940, 530),
    "HonorDeck": (783, 179),
    "MultiLive": (860, 166),
    "ChallengeLive": (860, 178),
    "CharacterRankAndChallengeStage": (908, 813),
    "CharacterRankAndChallengeStageScroll": (908, 550),
    "MusicClearInfo": (860, 318),
    "MusicClearSelectTabInfo": (860, 166),
    "StoryFavorite": (909, 813),
}
GENERAL_LABELS: dict[str, dict[str, str]] = {
    "cn": {
        "comment_title": "个性签名",
        "total_power": "综合力",
        "multi_live_title": "多人演出",
        "multi_live_count_suffix": "次",
        "challenge_live_title": "挑战演出",
        "challenge_live_solo": "单人",
        "character_rank_tab": "角色收藏等级",
        "challenge_stage_tab": "挑战舞台",
        "music_clear": "完成",
        "music_full_combo": "FULL COMBO",
        "music_all_perfect": "ALL PERFECT",
        "story_favorite_title": "最喜欢的剧情",
        "not_set": "未设置",
    },
    "jp": {
        "comment_title": "ひと言",
        "total_power": "総合力",
        "multi_live_title": "MVP / SUPER STAR",
        "multi_live_count_suffix": "回",
        "challenge_live_title": "チャレンジライブ",
        "challenge_live_solo": "ソロ",
        "character_rank_tab": "キャラクターランク",
        "challenge_stage_tab": "チャレンジステージ",
        "music_clear": "クリア",
        "music_full_combo": "フルコンボ",
        "music_all_perfect": "ALL PERFECT",
        "story_favorite_title": "お気に入りストーリー",
        "not_set": "未設定",
    },
}
GENERAL_MUSIC_DIFFICULTIES: tuple[tuple[str, str, tuple[int, int, int, int]], ...] = (
    ("easy", "EASY", (73, 211, 111, 255)),
    ("normal", "NORMAL", (65, 198, 222, 255)),
    ("hard", "HARD", (241, 201, 65, 255)),
    ("expert", "EXPERT", (236, 74, 115, 255)),
    ("master", "MASTER", (166, 89, 214, 255)),
    ("append", "APPEND", (218, 116, 221, 255)),
)
CHARA_ID2NICKNAME = {character_id: nickname for nickname, character_id in CHARA_LIST if nickname and character_id}
GENERAL_TEMPLATE_UNIT1_POSITIONS: dict[int, tuple[float, float]] = {
    # custom_profile/template/templatelayout.json unit 1.
    13: (-598.0, 330.0),
    1: (-461.5, 224.0),
    6: (483.0, 277.0),
    5: (-402.0, -120.0),
    4: (410.0, 49.75),
    2: (467.0, -99.0),
    3: (482.0, -262.0),
}
GENERAL_TEMPLATE_UNIT1_REQUIRED_IDS = {13, 6, 5, 4, 2, 3}
GENERAL_TEMPLATE_BG_COLOR = (255, 255, 255, 255)
GENERAL_TEMPLATE_PANEL_RADIUS = 16
GENERAL_TEMPLATE_FIELD_FILL = (255, 255, 255, 255)
GENERAL_TEMPLATE_FIELD_OUTLINE = (181, 235, 230, 255)
GENERAL_TEMPLATE_TITLE_FILL = (22, 197, 190, 255)
GENERAL_TEMPLATE_TEXT = (58, 65, 82, 255)
GENERAL_TEMPLATE_LABEL_TEXT = (72, 84, 104, 255)
UNITY_UI_DARK_TINT = (0.266667, 0.266667, 0.4, 1.0)
UNITY_UI_INPUT_TINT = (0.921569, 0.921569, 0.94902, 0.8)
UNITY_UI_HONOR_TINT = (0.87451, 0.87451, 0.917647, 0.8)
UNITY_UI_TOTAL_LINE_TINT = (0.654902, 0.654902, 0.737255, 1.0)
GENERAL_PREFAB_PALETTE = GeneralPrefabPalette(
    input_tint=UNITY_UI_INPUT_TINT,
    dark_tint=UNITY_UI_DARK_TINT,
    total_line_tint=UNITY_UI_TOTAL_LINE_TINT,
    text=GENERAL_TEMPLATE_TEXT,
    label_text=GENERAL_TEMPLATE_LABEL_TEXT,
)
GENERAL_DECK_CARD_NATIVE_SIZE = (330, 512)
GENERAL_DECK_CARD_ART_SIZE = (330, 541.5380249023438)
GENERAL_DECK_CARD_SCALE = 0.47269999980926514
GENERAL_DECK_CARD_RENDER_SIZE = (
    round(GENERAL_DECK_CARD_NATIVE_SIZE[0] * GENERAL_DECK_CARD_SCALE),
    round(GENERAL_DECK_CARD_NATIVE_SIZE[1] * GENERAL_DECK_CARD_SCALE),
)
_CARDS_FILENAME = "cards.json"
_BUILD_IMAGE_CONTENT_METHOD = "CustomProfileUtility.BuildImageContentViewInternal"
_INSTANTIATE_IMAGE_CONTENT_METHOD = "CustomProfileUtility.InstantiateImageContent"
_IMAGE_CONTENT_REFRESH_METHOD = "ImageContentView.Refresh"
_ALT_OTF_SUFFIX = "-alt.otf"
_DEFAULT_FONT_FILENAME = "FOT-RodinNTLGPro-DB.otf"
_DEFAULT_ALT_FONT_FILENAME = "FOT-RodinNTLGPro-DB" + _ALT_OTF_SUFFIX
_OMIKUJI_FILENAME = "omikujis.json"
_DECK_IMAGE_FILENAME = "deck.png"
_OMIKUJI_FONT_FILENAME = "FOT-Omikuji_4956192661917990345.otf"
_TMP_TEXT_LABEL = "custom profile TMP text"
_NATIVE_IMAGE_CONTENT_METHODS = (
    _BUILD_IMAGE_CONTENT_METHOD,
    _INSTANTIATE_IMAGE_CONTENT_METHOD,
    _IMAGE_CONTENT_REFRESH_METHOD,
)
GENERAL_VIEW_REQUIRED_INPUTS: dict[str, tuple[str, ...]] = {
    "X": ("userProfile.twitterId",),
    "EditUserName": ("user.name",),
    "TotalPower": ("totalPower",),
    "Deck": ("userDeck", "userCards", _CARDS_FILENAME, "card thumbnail assets"),
    "Comment": ("userProfile.word",),
    "LeaderCard": ("userDeck.leader", "userCards", _CARDS_FILENAME, "card thumbnail assets"),
    "HonorDeck": ("userProfileHonors", "userHonors", "honor assets"),
    "MultiLive": ("userMultiLiveTopScoreCount",),
    "ChallengeLive": ("userChallengeLiveSoloResult",),
    "CharacterRankAndChallengeStage": ("userCharacters", "userChallengeLiveSoloStages"),
    "CharacterRankAndChallengeStageScroll": ("userCharacters", "userChallengeLiveSoloStages"),
    "MusicClearInfo": ("userMusicDifficultyClearCount",),
    "MusicClearSelectTabInfo": ("userMusicDifficultyClearCount",),
    "StoryFavorite": ("userStoryFavorites",),
}
NATIVE_METHODS_BY_KIND: dict[str, tuple[str, ...]] = {
    "general": (
        "CustomProfileUtility.InstantiateGeneralContent",
        "GeneralContentViewBase.Setup",
    ),
    "general_background": _NATIVE_IMAGE_CONTENT_METHODS,
    "story_background": _NATIVE_IMAGE_CONTENT_METHODS,
    "character_icon": _NATIVE_IMAGE_CONTENT_METHODS,
    "material": _NATIVE_IMAGE_CONTENT_METHODS,
    "user_interface_icon": _NATIVE_IMAGE_CONTENT_METHODS,
    "stand_member": _NATIVE_IMAGE_CONTENT_METHODS,
    "card_member": (
        "CustomProfileUtility.InstantiateCardMemberContent",
        "CardMemberContentViewBase.Refresh",
        "ClipSizeCardContentView.OnRefresh",
        "FullSizeCardContentView.OnRefresh",
    ),
    "honor": (
        "CustomProfileUtility.AddUserProfileHonors",
        "CustomProfileUtility.InstantiateHonorContent",
        "HonorContentView.Refresh",
    ),
    "bonds_honor": (
        "CustomProfileUtility.AddUserProfileBondsHonors",
        "CustomProfileUtility.InstantiateHonorContent",
        "HonorContentView.Refresh",
    ),
    "collection": (
        "CustomProfileUtility.InstantiateCollectionContent",
        "CollectionCustomPrefabContentView.RefreshAsync",
        "CustomProfileUtility.RefreshCollectionObjectViewTo",
    ),
    "stamp": (
        "CustomProfileUtility.InstantiateStampContent",
        _IMAGE_CONTENT_REFRESH_METHOD,
    ),
    "shape": (
        "CustomProfileUtility.InstantiateShapeContent",
        "ShapeContentView.Refresh",
    ),
    "text": (
        "CustomProfileUtility.InstantiateTextContent",
        "TextContentView.Refresh",
        "TextContentView.UpdateTextMesh",
    ),
    "mini_chara": (
        "DynamicContentView.Refresh",
        "DynamicContentView.SetViewInfo",
    ),
    "screen_filter": (
        "DynamicContentView.Refresh",
        "DynamicContentView.SetViewInfo",
    ),
}


@dataclass
class StyledLine:
    runs: list[TextRun]
    style: TextStyle
    trailing_newline_count: int = 0


@dataclass(frozen=True)
class NativeContent:
    # Mirrors the tuple produced by CustomProfileUtility.BuildCardObjectsAsync:
    # ProfileContentViewBase plus the ObjectData later consumed by
    # ApplyBuildConentInCardView.
    layer: int
    kind: str
    item: dict[str, Any]
    object_data: dict[str, Any]


@dataclass(frozen=True)
class NativeUnresolvedContent:
    kind: str
    content_type_id: int
    content_type: str
    reason: str
    item: dict[str, Any]
    generated_data: dict[str, Any]
    resource: dict[str, Any] | None
    expected_view: str
    expected_size: tuple[float, float] | None
    required_inputs: tuple[str, ...]
    native_methods: tuple[str, ...]

    def to_audit_dict(self, content: NativeContent, card_ref: dict[str, int]) -> dict[str, Any]:
        return {
            **card_ref,
            "kind": self.kind,
            "contentTypeId": self.content_type_id,
            "contentType": self.content_type,
            "layer": content.layer,
            "visible": bool(content.object_data.get("visible", False)),
            "dataId": content_data_id(self.kind, self.item),
            "reason": self.reason,
            "expectedView": self.expected_view,
            "expectedSize": (
                {"x": self.expected_size[0], "y": self.expected_size[1]} if self.expected_size is not None else None
            ),
            "generatedData": self.generated_data,
            "resource": summarize_resource(self.resource),
            "requiredInputs": list(self.required_inputs),
            "nativeMethods": list(self.native_methods),
        }


@dataclass(frozen=True)
class PreparedLayer:
    image: Image.Image
    xy: tuple[int, int]


@dataclass(frozen=True)
class LayerTransformInputs:
    layer: Image.Image  # trimmed local raster (before any resize)
    pivot: tuple[float, float]  # pivot in trimmed-layer coords
    object_scale: tuple[float, float]  # (1.0, 1.0) when scale_consumed or absent
    position_scale: tuple[float, float]  # (renderer.position_scale_x, position_scale_y)
    angle: float  # degrees, rotation_sign already applied
    anchor: tuple[float, float]  # unity_point(position) — canvas-space anchor


@dataclass(frozen=True)
class RenderedLayer:
    content: NativeContent
    status: str
    result: (
        tuple[Image.Image, tuple[float, float]]
        | tuple[Image.Image, tuple[float, float], bool]
        | NativeUnresolvedContent
        | None
    )
    prepared: PreparedLayer | None = None


@dataclass(frozen=True)
class TMPNativeLineLayout:
    baselines: list[float]
    max_ascender: float
    max_descender: float
    content_height: float


@dataclass(frozen=True)
class TMPNativeCharacterInfo:
    index: int
    char: str
    line_index: int
    x_origin: float
    x_advance: float
    glyph_origin_x: float
    bottom_left_x: float
    bottom_left_y: float
    top_left_x: float
    top_left_y: float
    top_right_x: float
    top_right_y: float
    bottom_right_x: float
    bottom_right_y: float
    vertex_padding: float
    raw_left_x: float
    raw_right_x: float
    raw_top_y: float
    raw_bottom_y: float
    baseline: float
    ascender: float
    descender: float
    adjusted_ascender: float
    adjusted_descender: float
    visible: bool
    style: TextStyle
    metrics: TMPGlyphMetrics
    sdf_scale: float


@dataclass(frozen=True)
class TMPNativeLineInfo:
    index: int
    styled_line: StyledLine
    run_metrics: list[tuple[TextRun, float, float]]
    first_character_index: int
    last_character_index: int
    visible_character_count: int
    baseline: float
    ascender: float
    descender: float
    line_height: float
    width: float
    max_advance: float
    line_extents_min_x: float
    line_extents_max_x: float
    y_down: float


@dataclass(frozen=True)
class TMPNativeTextLayout:
    layout_mode: str
    lines: list[TMPNativeLineInfo]
    characters: list[TMPNativeCharacterInfo]
    preferred_width: float
    preferred_height: float
    content_height: float
    max_ascender: float
    max_descender: float
    accumulated_line_height: float
    dominant_size: float
    base_scale: float
    current_em_scale: float
    raw_line_gap: float
    line_spacing_delta: float
    paragraph_spacing: float

    @property
    def line_layout(self) -> TMPNativeLineLayout:
        return TMPNativeLineLayout(
            baselines=[line.baseline for line in self.lines],
            max_ascender=self.max_ascender,
            max_descender=self.max_descender,
            content_height=self.content_height,
        )


@dataclass(frozen=True)
class _TMPNativeLayoutConfig:
    font_name: str
    font_path: Path
    layout_mode: str
    base_scale: float
    current_em_scale: float
    raw_line_gap: float
    line_spacing: float
    line_spacing_delta: float
    paragraph_spacing: float
    outline_dilate: float
    margin_width: float
    source_metrics_only: bool


@dataclass
class _TMPNativeLayoutState:
    dominant_size: float
    line_offset: float = 0.0
    start_of_line_ascender: float = 0.0
    element_descender: float = 0.0
    is_driven_line_spacing: bool = False
    max_text_ascender: float | None = None
    rendered_width: float = 0.0
    accumulated_line_height: float = 0.0
    lines: list[TMPNativeLineInfo] = field(default_factory=list)
    characters: list[TMPNativeCharacterInfo] = field(default_factory=list)


@dataclass
class _TMPNativeLineState:
    x_advance: float
    max_ascender: float = TMP_LARGE_NEGATIVE_FLOAT
    max_descender: float = TMP_LARGE_POSITIVE_FLOAT
    visible_character_count: int = 0
    has_character: bool = False
    line_break_adjusted_ascender: float | None = None


@dataclass(frozen=True)
class TMPRunMeasure:
    advance: float
    visual_left: float
    visual_right: float
    visual_top: float
    visual_bottom: float

    @property
    def visual_width(self) -> float:
        return max(0.0, self.visual_right - self.visual_left)

    @property
    def visual_height(self) -> float:
        return max(0.0, self.visual_bottom - self.visual_top)


@dataclass
class TMPVisualBounds:
    left: float | None = None
    right: float | None = None
    top: float | None = None
    bottom: float | None = None

    def include(self, left: float, right: float, top: float, bottom: float) -> None:
        self.left = left if self.left is None else min(self.left, left)
        self.right = right if self.right is None else max(self.right, right)
        self.top = top if self.top is None else min(self.top, top)
        self.bottom = bottom if self.bottom is None else max(self.bottom, bottom)

    def include_horizontal(self, left: float, right: float) -> None:
        self.left = left if self.left is None else min(self.left, left)
        self.right = right if self.right is None else max(self.right, right)

    def resolved(self, fallback: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        fallback_left, fallback_right, fallback_top, fallback_bottom = fallback
        return (
            fallback_left if self.left is None else self.left,
            fallback_right if self.right is None else self.right,
            fallback_top if self.top is None else self.top,
            fallback_bottom if self.bottom is None else self.bottom,
        )


@dataclass(frozen=True)
class TMPGeneratedTextData:
    # Mirrors Sekai.CustomProfile.GenerateTextData as consumed by
    # TextContentView.UpdateTextMesh.
    text: str
    font_id: int
    align: int
    font_size: float
    outline_size: float
    outline_color_id: int
    font_color_id: int
    line_spacing: float


@dataclass(frozen=True)
class TMPUpdateMeshState:
    font_name: str
    font_asset_name: str | None
    text: str
    font_size: float
    font_color: str
    align: int
    tmp_line_spacing: float
    underlay_color: str
    underlay_dilate: float


@dataclass(frozen=True)
class TMPGlyphMetrics:
    width: float
    height: float
    bearing_x: float
    bearing_y: float
    advance: float
    rect_x: int
    rect_y: int
    rect_w: int
    rect_h: int
    glyph_scale: float
    atlas_index: int


@dataclass(frozen=True)
class TMPShaderMaterial:
    gradient_scale: float
    face_dilate: float
    outline_width: float
    outline_softness: float
    weight_normal: float
    weight_bold: float
    underlay_offset_x: float
    underlay_offset_y: float
    underlay_softness: float
    glow_offset: float
    glow_outer: float
    sharpness: float
    scale_ratio_a: float
    scale_ratio_b: float
    scale_ratio_c: float


@dataclass(frozen=True)
class PILTextLineMetrics:
    runs: list[tuple[TextRun, float, float]]
    y: float
    height: float


@dataclass(frozen=True)
class PILTextLayoutMetrics:
    lines: list[PILTextLineMetrics]
    min_x: float
    max_x: float
    total_height: float


@dataclass(frozen=True)
class TMPRunVisualMetrics:
    advance: float
    left: float
    right: float
    top: float
    bottom: float


@dataclass(frozen=True)
class TMPDynamicRunGlyph:
    image: Image.Image
    bbox: tuple[int, int, int, int]
    pad: int
    origin_x: float


@dataclass(frozen=True)
class TMPDynamicGlyphSDF:
    field: Image.Image
    bbox: tuple[int, int, int, int]
    pad: int
    sample_size: float


@dataclass
class _TMPGlyphContourBuilder:
    scale: float
    contours: list[list[tuple[float, float]]] = field(default_factory=list)
    contour: list[tuple[float, float]] = field(default_factory=list)
    pos: tuple[float, float] | None = None
    start: tuple[float, float] | None = None

    def _append_point(self, point: tuple[float, float]) -> None:
        self.contour.append((float(point[0]) * self.scale, float(point[1]) * self.scale))

    def _close_contour(self) -> None:
        if len(self.contour) >= 2:
            self.contours.append(self.contour)
        self.contour = []

    def _flatten_quadratic(
        self,
        p0: tuple[float, float],
        p1: tuple[float, float],
        p2: tuple[float, float],
    ) -> None:
        for step in range(1, TMP_DYNAMIC_SDF_VECTOR_CURVE_STEPS + 1):
            t = step / TMP_DYNAMIC_SDF_VECTOR_CURVE_STEPS
            u = 1.0 - t
            self._append_point(
                (
                    u * u * p0[0] + 2.0 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2.0 * u * t * p1[1] + t * t * p2[1],
                )
            )

    def _flatten_cubic(
        self,
        p0: tuple[float, float],
        p1: tuple[float, float],
        p2: tuple[float, float],
        p3: tuple[float, float],
    ) -> None:
        for step in range(1, TMP_DYNAMIC_SDF_VECTOR_CURVE_STEPS + 1):
            t = step / TMP_DYNAMIC_SDF_VECTOR_CURVE_STEPS
            u = 1.0 - t
            self._append_point(
                (
                    u * u * u * p0[0] + 3.0 * u * u * t * p1[0] + 3.0 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3.0 * u * u * t * p1[1] + 3.0 * u * t * t * p2[1] + t * t * t * p3[1],
                )
            )

    def _consume_cubic(self, args: tuple[Any, ...]) -> None:
        assert self.pos is not None
        curve_points = [tuple(point) for point in args]
        for index in range(0, len(curve_points), 3):
            if index + 2 >= len(curve_points):
                break
            p1, p2, p3 = curve_points[index : index + 3]
            self._flatten_cubic(self.pos, p1, p2, p3)
            self.pos = p3

    @staticmethod
    def _quadratic_points(args: tuple[Any, ...]) -> tuple[list[tuple[float, float]], tuple[float, float]] | None:
        points = [None if point is None else tuple(point) for point in args]
        if not points:
            return None
        if points[-1] is None:
            off_curves = [point for point in points[:-1] if point is not None]
            if not off_curves:
                return None
            final = (
                (off_curves[0][0] + off_curves[-1][0]) * 0.5,
                (off_curves[0][1] + off_curves[-1][1]) * 0.5,
            )
            points = [*off_curves, final]
        final_point = points[-1]
        if final_point is None:
            return None
        return [point for point in points[:-1] if point is not None], final_point

    def _consume_quadratic(self, args: tuple[Any, ...]) -> None:
        assert self.pos is not None
        resolved = self._quadratic_points(args)
        if resolved is None:
            return
        off_curves, final_point = resolved
        if not off_curves:
            self.pos = final_point
            self._append_point(self.pos)
            return
        current = self.pos
        for index, control in enumerate(off_curves):
            end = final_point if index == len(off_curves) - 1 else self._midpoint(control, off_curves[index + 1])
            self._flatten_quadratic(current, control, end)
            current = end
        self.pos = final_point

    @staticmethod
    def _midpoint(first: tuple[float, float], second: tuple[float, float]) -> tuple[float, float]:
        return (first[0] + second[0]) * 0.5, (first[1] + second[1]) * 0.5

    def consume(self, op: str, args: tuple[Any, ...]) -> None:
        if op == "moveTo":
            self._close_contour()
            self.pos = tuple(args[0])
            self.start = self.pos
            self._append_point(self.pos)
        elif op == "lineTo" and self.pos is not None:
            self.pos = tuple(args[0])
            self._append_point(self.pos)
        elif op == "curveTo" and self.pos is not None:
            self._consume_cubic(args)
        elif op == "qCurveTo" and self.pos is not None:
            self._consume_quadratic(args)
        elif op == "closePath":
            self._close_contour()
            self.pos = self.start
        elif op == "endPath":
            self._close_contour()

    def finish(self) -> list[list[tuple[float, float]]]:
        self._close_contour()
        return self.contours


@dataclass(frozen=True)
class TMPSdfUnderlayScalars:
    """Underlay half of the TMP shading scalars; shift is the PRE-ROUNDED integer field
    translation (banker's rounding, (0, 0) when |offset| < 0.5 short-circuits)."""

    scale: float
    w: float
    shift_x: int
    shift_y: int
    color: tuple[int, int, int]


@dataclass(frozen=True)
class TMPSdfShadingScalars:
    """Scalar half of shade_tmp_sdf_field, shared verbatim with the Skia SdfQuad node: the
    per-pixel evaluators on both backends compute ``clip(field*face_scale - face_w, 0, 1)*alpha``
    (plus the underlay pass) from exactly these numbers. Scalars stay float64 here; the pixel
    loops cast them to float32 before the elementwise math (numpy's implicit behavior)."""

    face_scale: float
    face_w: float
    alpha: float
    face_color: tuple[int, int, int]
    underlay: TMPSdfUnderlayScalars | None


@dataclass(frozen=True)
class DirectSdfQuad:
    """One decorative-path glyph, ready for per-pixel shading: ``field`` is the L field ALREADY
    warped to display size (the exact array Pillow would shade), ``(left, top)`` the canvas-space
    integer paste position, ``scalars`` the frozen shading scalars. This is the renderer half of
    the Skia ``SdfQuad`` IR node — the node does zero geometric resampling."""

    field: Image.Image
    left: int
    top: int
    scalars: TMPSdfShadingScalars


@dataclass(frozen=True)
class TMPStaticAtlasField:
    """Static TMP atlas glyph before pixel work.

    ``crop`` uses Pillow's possibly out-of-bounds crop coordinates and ``field_size`` is the
    BICUBIC-resized native glyph quad. The Skia path can carry this descriptor to Rust instead
    of decoding, cropping, and resizing the atlas through Pillow.
    """

    atlas_path: Path
    atlas_size: tuple[int, int]
    crop: tuple[int, int, int, int]
    field_size: tuple[int, int]


@dataclass(frozen=True)
class TMPDynamicFontField:
    """Dynamic TMP glyph before pixel work.

    Python selects the source font/asset and preserves the existing TMP layout geometry. Rust
    resolves ``codepoint`` from the registered font, flattens its outline, builds the signed
    distance field, crops it to ``crop_padding``, and resizes it to ``field_size``. No glyph
    bitmap, NumPy contour grid, Pillow L image, or A8 ``mem:`` payload is created in Python.
    """

    font_path: Path
    codepoint: int
    sample_size: float
    bbox: tuple[int, int, int, int]
    padding: int
    crop_padding: int
    field_size: tuple[int, int]
    spread: float


@dataclass(frozen=True)
class _TMPPreparedCharacterField:
    field: Image.Image | TMPStaticAtlasField | TMPDynamicFontField
    glyph_asset: TMPFontAsset | None
    bbox: tuple[int, int, int, int]
    pad_x: int
    pad_y: int
    native_quad_sized: bool

    def result(
        self,
    ) -> tuple[
        Image.Image | TMPStaticAtlasField | TMPDynamicFontField,
        TMPFontAsset | None,
        tuple[int, int, int, int],
        int,
        int,
    ]:
        return self.field, self.glyph_asset, self.bbox, self.pad_x, self.pad_y


@dataclass(frozen=True)
class TMPFieldWarpPlan:
    """Pillow AFFINE inverse matrix plus its clipped destination rectangle."""

    affine: tuple[float, float, float, float, float, float]
    size: tuple[int, int]
    left: int
    top: int


@dataclass(frozen=True)
class DirectSdfAtlasQuad:
    """Static-atlas decorative glyph whose complete pixel pipeline runs in Rust."""

    atlas_path: Path
    atlas_size: tuple[int, int]
    crop: tuple[int, int, int, int]
    field_size: tuple[int, int]
    size: tuple[int, int]
    affine: tuple[float, float, float, float, float, float]
    left: int
    top: int
    scalars: TMPSdfShadingScalars


@dataclass(frozen=True)
class DirectSdfFontQuad:
    """Dynamic source-font decorative glyph whose complete pixel pipeline runs in Rust."""

    font_path: Path
    codepoint: int
    sample_size: float
    bbox: tuple[int, int, int, int]
    padding: int
    crop_padding: int
    field_size: tuple[int, int]
    spread: float
    size: tuple[int, int]
    affine: tuple[float, float, float, float, float, float]
    left: int
    top: int
    scalars: TMPSdfShadingScalars


# frozen: instances are shared PROCESS-WIDE across requests/threads via the TMP metadata table
# cache (see TMPFontLibrary.load). Attribute rebinding is forbidden by the dataclass; the
# atlas_paths/fallback_names/glyphs containers are still technically mutable — never mutate them
# after construction.
@dataclass(frozen=True)
class TMPFontAsset:
    name: str
    bundle: str
    source_font_path: Path | None
    atlas_paths: list[Path]
    atlas_population_mode: int
    atlas_width: float
    atlas_height: float
    atlas_padding: float
    point_size: float
    face_scale: float
    line_height: float
    ascent_line: float
    descent_line: float
    tab_width: float
    gradient_scale: float
    weight_normal: float
    weight_bold: float
    face_dilate: float
    outline_width: float
    outline_softness: float
    sharpness: float
    normal_spacing_offset: float
    bold_spacing: float
    scale_ratio_a: float
    scale_ratio_b: float
    scale_ratio_c: float
    glow_offset: float
    glow_outer: float
    underlay_softness: float
    underlay_offset_x: float
    underlay_offset_y: float
    fallback_names: list[str]
    glyphs: dict[int, TMPGlyphMetrics]

    @property
    def has_static_glyphs(self) -> bool:
        return bool(self.glyphs)


class FTVector(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class FTBBox(ctypes.Structure):
    _fields_ = [
        ("xMin", ctypes.c_long),
        ("yMin", ctypes.c_long),
        ("xMax", ctypes.c_long),
        ("yMax", ctypes.c_long),
    ]


class FTGlyphMetrics(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_long),
        ("height", ctypes.c_long),
        ("horiBearingX", ctypes.c_long),
        ("horiBearingY", ctypes.c_long),
        ("horiAdvance", ctypes.c_long),
        ("vertBearingX", ctypes.c_long),
        ("vertBearingY", ctypes.c_long),
        ("vertAdvance", ctypes.c_long),
    ]


class FTBitmap(ctypes.Structure):
    _fields_ = [
        ("rows", ctypes.c_uint),
        ("width", ctypes.c_uint),
        ("pitch", ctypes.c_int),
        ("buffer", ctypes.c_void_p),
        ("num_grays", ctypes.c_ushort),
        ("pixel_mode", ctypes.c_ubyte),
        ("palette_mode", ctypes.c_ubyte),
        ("palette", ctypes.c_void_p),
    ]


class FTOutline(ctypes.Structure):
    _fields_ = [
        ("n_contours", ctypes.c_short),
        ("n_points", ctypes.c_short),
        ("points", ctypes.POINTER(FTVector)),
        ("tags", ctypes.POINTER(ctypes.c_char)),
        ("contours", ctypes.POINTER(ctypes.c_short)),
        ("flags", ctypes.c_int),
    ]


class FTGlyphSlotRec(ctypes.Structure):
    _fields_ = [
        ("library", ctypes.c_void_p),
        ("face", ctypes.c_void_p),
        ("next", ctypes.c_void_p),
        ("glyph_index", ctypes.c_uint),
        ("generic_data", ctypes.c_void_p),
        ("generic_finalizer", ctypes.c_void_p),
        ("metrics", FTGlyphMetrics),
        ("linearHoriAdvance", ctypes.c_long),
        ("linearVertAdvance", ctypes.c_long),
        ("advance", FTVector),
        ("format", ctypes.c_uint),
        ("bitmap", FTBitmap),
        ("bitmap_left", ctypes.c_int),
        ("bitmap_top", ctypes.c_int),
        ("outline", FTOutline),
    ]


class FTFaceRec(ctypes.Structure):
    _fields_ = [
        ("num_faces", ctypes.c_long),
        ("face_index", ctypes.c_long),
        ("face_flags", ctypes.c_long),
        ("style_flags", ctypes.c_long),
        ("num_glyphs", ctypes.c_long),
        ("family_name", ctypes.c_char_p),
        ("style_name", ctypes.c_char_p),
        ("num_fixed_sizes", ctypes.c_int),
        ("available_sizes", ctypes.c_void_p),
        ("num_charmaps", ctypes.c_int),
        ("charmaps", ctypes.c_void_p),
        ("generic_data", ctypes.c_void_p),
        ("generic_finalizer", ctypes.c_void_p),
        ("bbox", FTBBox),
        ("units_per_EM", ctypes.c_ushort),
        ("ascender", ctypes.c_short),
        ("descender", ctypes.c_short),
        ("height", ctypes.c_short),
        ("max_advance_width", ctypes.c_short),
        ("max_advance_height", ctypes.c_short),
        ("underline_position", ctypes.c_short),
        ("underline_thickness", ctypes.c_short),
        ("glyph", ctypes.POINTER(FTGlyphSlotRec)),
    ]


class FreeTypeMetrics:
    FT_LOAD_NO_HINTING = 2
    FT_RENDER_MODE_NORMAL = 0

    def __init__(self) -> None:
        lib_path = ctypes.util.find_library("freetype")
        candidates = [lib_path, "/opt/homebrew/lib/libfreetype.dylib", "/usr/local/lib/libfreetype.dylib"]
        self.lib = None
        for candidate in candidates:
            if not candidate:
                continue
            try:
                self.lib = ctypes.CDLL(candidate)
                break
            except OSError:
                continue
        if self.lib is None:
            raise OSError("libfreetype not found")
        self.handle = ctypes.c_void_p()
        if self.lib.FT_Init_FreeType(ctypes.byref(self.handle)) != 0:
            raise OSError("FT_Init_FreeType failed")
        self.lib.FT_New_Face.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_long,
            ctypes.POINTER(ctypes.POINTER(FTFaceRec)),
        ]
        self.lib.FT_New_Face.restype = ctypes.c_int
        self.lib.FT_Done_Face.argtypes = [ctypes.POINTER(FTFaceRec)]
        self.lib.FT_Done_Face.restype = ctypes.c_int
        self.lib.FT_Set_Char_Size.argtypes = [
            ctypes.POINTER(FTFaceRec),
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self.lib.FT_Set_Char_Size.restype = ctypes.c_int
        self.lib.FT_Get_Char_Index.argtypes = [ctypes.POINTER(FTFaceRec), ctypes.c_ulong]
        self.lib.FT_Get_Char_Index.restype = ctypes.c_uint
        self.lib.FT_Load_Glyph.argtypes = [ctypes.POINTER(FTFaceRec), ctypes.c_uint, ctypes.c_int32]
        self.lib.FT_Load_Glyph.restype = ctypes.c_int
        self.lib.FT_Render_Glyph.argtypes = [ctypes.POINTER(FTGlyphSlotRec), ctypes.c_int]
        self.lib.FT_Render_Glyph.restype = ctypes.c_int
        self._faces: dict[Path, ctypes.POINTER(FTFaceRec)] = {}
        # The singleton is shared process-wide and FT_Set_Char_Size/FT_Load_Glyph mutate shared
        # FT_Face state, so concurrent requests must serialize. Cheap in practice: with the
        # process-level glyph caches, this path only runs on a cold glyph.
        self._lock = threading.Lock()

    def close(self) -> None:
        for face in self._faces.values():
            self.lib.FT_Done_Face(face)
        self._faces.clear()

    def _face(self, path: Path) -> ctypes.POINTER(FTFaceRec):
        face = self._faces.get(path)
        if face is not None:
            return face
        face = ctypes.POINTER(FTFaceRec)()
        if self.lib.FT_New_Face(self.handle, str(path).encode("utf-8"), 0, ctypes.byref(face)) != 0:
            raise OSError(f"FT_New_Face failed: {path}")
        self._faces[path] = face
        return face

    def glyph_metrics(self, path: Path, ch: str, font_size: float) -> TMPGlyphMetrics | None:
        with self._lock:
            return self._glyph_metrics_locked(path, ch, font_size)

    def _glyph_metrics_locked(self, path: Path, ch: str, font_size: float) -> TMPGlyphMetrics | None:
        face = self._face(path)
        if self.lib.FT_Set_Char_Size(face, 0, int(round(font_size * 64.0)), 72, 72) != 0:
            return None
        glyph_index = self.lib.FT_Get_Char_Index(face, ord(ch))
        if glyph_index == 0:
            return None
        if self.lib.FT_Load_Glyph(face, glyph_index, self.FT_LOAD_NO_HINTING) != 0:
            return None
        slot = face.contents.glyph.contents
        metrics = slot.metrics
        return TMPGlyphMetrics(
            width=max(0.0, metrics.width / 64.0),
            height=max(0.0, metrics.height / 64.0),
            bearing_x=metrics.horiBearingX / 64.0,
            bearing_y=metrics.horiBearingY / 64.0,
            advance=max(0.0, metrics.horiAdvance / 64.0),
            rect_x=0,
            rect_y=0,
            rect_w=0,
            rect_h=0,
            glyph_scale=1.0,
            atlas_index=0,
        )

    def glyph_bitmap(
        self,
        path: Path,
        ch: str,
        font_size: float,
    ) -> tuple[Image.Image, int, int, TMPGlyphMetrics] | None:
        with self._lock:
            return self._glyph_bitmap_locked(path, ch, font_size)

    def _glyph_bitmap_locked(
        self,
        path: Path,
        ch: str,
        font_size: float,
    ) -> tuple[Image.Image, int, int, TMPGlyphMetrics] | None:
        face = self._face(path)
        if self.lib.FT_Set_Char_Size(face, 0, int(round(font_size * 64.0)), 72, 72) != 0:
            return None
        glyph_index = self.lib.FT_Get_Char_Index(face, ord(ch))
        if glyph_index == 0:
            return None
        if self.lib.FT_Load_Glyph(face, glyph_index, self.FT_LOAD_NO_HINTING) != 0:
            return None
        slot_ptr = face.contents.glyph
        slot = slot_ptr.contents
        metrics = slot.metrics
        layout_metrics = TMPGlyphMetrics(
            width=max(0.0, metrics.width / 64.0),
            height=max(0.0, metrics.height / 64.0),
            bearing_x=metrics.horiBearingX / 64.0,
            bearing_y=metrics.horiBearingY / 64.0,
            advance=max(0.0, metrics.horiAdvance / 64.0),
            rect_x=0,
            rect_y=0,
            rect_w=0,
            rect_h=0,
            glyph_scale=1.0,
            atlas_index=0,
        )
        if self.lib.FT_Render_Glyph(slot_ptr, self.FT_RENDER_MODE_NORMAL) != 0:
            return None
        slot = slot_ptr.contents
        bitmap = slot.bitmap
        width = int(bitmap.width)
        rows = int(bitmap.rows)
        if width <= 0 or rows <= 0 or not bitmap.buffer:
            return Image.new("L", (1, 1), 0), int(slot.bitmap_left), int(slot.bitmap_top), layout_metrics

        import numpy as np

        pitch = int(bitmap.pitch)
        stride = abs(pitch)
        raw = ctypes.string_at(bitmap.buffer, stride * rows)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(rows, stride)[:, :width]
        if pitch < 0:
            arr = arr[::-1]
        return Image.fromarray(arr.copy(), "L"), int(slot.bitmap_left), int(slot.bitmap_top), layout_metrics


_FREETYPE_METRICS: FreeTypeMetrics | None = None
_FREETYPE_UNAVAILABLE = False
_FREETYPE_INIT_LOCK = threading.Lock()


def freetype_metrics() -> FreeTypeMetrics | None:
    global _FREETYPE_METRICS, _FREETYPE_UNAVAILABLE
    if _FREETYPE_UNAVAILABLE:
        return None
    if _FREETYPE_METRICS is None:
        with _FREETYPE_INIT_LOCK:
            if _FREETYPE_METRICS is None and not _FREETYPE_UNAVAILABLE:
                try:
                    _FREETYPE_METRICS = FreeTypeMetrics()
                except Exception:
                    _FREETYPE_UNAVAILABLE = True
                    return None
    return _FREETYPE_METRICS


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    return value or {}


def _default_if_none(value: Any, default: Any) -> Any:
    return default if value is None else value


def _choice_or_default(value: str, choices: set[str] | frozenset[str], default: str) -> str:
    return value if value in choices else default


def _positive_int(value: Any, default: int = 1) -> int:
    return max(1, int(value or default))


def _positive_float(value: Any, default: float = 1.0) -> float:
    return max(1.0, float(value or default))


def _game_assets_root(assets: Path) -> Path:
    return assets.parent if assets.name == "custom_profile" else assets


def _record_or_noop(record):
    return (lambda path: None) if record is None else record


def _first_truthy(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value:
            return value
    return default


def _float_first(*values: Any, default: float = 0.0) -> float:
    return float(_first_truthy(*values, default=default))


def _int_first(*values: Any, default: int = 0) -> int:
    return int(_first_truthy(*values, default=default))


def _nonempty_strings(values: Any) -> list[str]:
    return [text for value in values or [] if (text := str(value))]


class TMPFontLibrary:
    def __init__(
        self,
        assets: dict[str, list[TMPFontAsset]],
        source_assets: dict[str, list[TMPFontAsset]] | None = None,
        runtime_fonts_dir: Path | None = None,
    ) -> None:
        self.assets = assets
        self.source_assets = source_assets or assets
        self.runtime_fonts_dir = runtime_fonts_dir
        self._source_fonts: dict[Path, Any] = {}
        self._source_metrics: dict[tuple[Path, int, float], TMPGlyphMetrics | None] = {}

    @classmethod
    def load(
        cls,
        metadata_path: Path | None,
        source_metadata_path: Path | None = DEFAULT_TMP_BASE_FONT_METADATA,
        runtime_fonts_dir: Path | None = None,
    ) -> "TMPFontLibrary":
        if metadata_path is None or not metadata_path.exists():
            return cls({}, runtime_fonts_dir=runtime_fonts_dir)
        metadata_path = metadata_path.resolve()
        source_metadata_path = (
            source_metadata_path.resolve()
            if source_metadata_path is not None and source_metadata_path.exists()
            else None
        )

        # The parsed asset tables are shared process-wide: TMPFontAsset/TMPGlyphMetrics are
        # frozen dataclasses (containers inside TMPFontAsset rely on the never-mutate-after-load
        # convention); only this per-request library instance with its private
        # _source_fonts/_source_metrics is fresh. The loader records every file it reads or
        # probes so a replaced (or late-arriving) table invalidates the entry.
        def _loader(record) -> tuple[dict[str, list[TMPFontAsset]], dict[str, list[TMPFontAsset]] | None]:
            source: dict[str, list[TMPFontAsset]] | None = None
            if source_metadata_path is not None and source_metadata_path != metadata_path:
                source = cls._load_assets(source_metadata_path, record=record)
            return cls._load_assets(metadata_path, record=record), source

        assets, source_assets = get_tmp_font_tables(metadata_path, source_metadata_path, _loader)
        return cls(assets, source_assets, runtime_fonts_dir=runtime_fonts_dir)

    @classmethod
    def _load_assets(cls, metadata_path: Path, record=None) -> dict[str, list[TMPFontAsset]]:
        record = _record_or_noop(record)
        record(metadata_path)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        base = metadata_path.parent
        materials = cls._materials_by_path_id(metadata)
        assets: dict[str, list[TMPFontAsset]] = {}
        for row in metadata.get("tmp_font_assets", []):
            asset = cls._asset_from_metadata_row(base, row, materials, record)
            assets.setdefault(asset.name, []).append(asset)
        cls._sort_assets(assets)
        return assets

    @staticmethod
    def _materials_by_path_id(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            str(row.get("path_id")): row
            for row in metadata.get("materials", [])
            if isinstance(row, dict) and row.get("path_id") is not None
        }

    @classmethod
    def _asset_from_metadata_row(
        cls,
        base: Path,
        row: dict[str, Any],
        materials: dict[str, dict[str, Any]],
        record,
    ) -> TMPFontAsset:
        face = _mapping_or_empty(row.get("face_info"))
        creation = _mapping_or_empty(row.get("creation_settings"))
        material = _mapping_or_empty(materials.get(str(row.get("material"))))
        floats = _mapping_or_empty(material.get("floats"))
        atlas_padding = _float_first(row.get("atlas_padding"), default=5.0)
        return TMPFontAsset(
            name=str(row.get("name", "")),
            bundle=str(row.get("bundle", "")),
            source_font_path=cls._source_font_path(base, row, record),
            atlas_paths=cls._atlas_paths(base, row, record),
            atlas_population_mode=_int_first(row.get("atlas_population_mode")),
            atlas_width=_float_first(row.get("atlas_width"), floats.get("_TextureWidth")),
            atlas_height=_float_first(row.get("atlas_height"), floats.get("_TextureHeight")),
            atlas_padding=atlas_padding,
            point_size=_float_first(face.get("m_PointSize"), creation.get("pointSize"), default=1.0),
            face_scale=_float_first(face.get("m_Scale"), default=1.0),
            line_height=_float_first(face.get("m_LineHeight")),
            ascent_line=_float_first(face.get("m_AscentLine")),
            descent_line=_float_first(face.get("m_DescentLine")),
            tab_width=_float_first(face.get("m_TabWidth")),
            gradient_scale=_float_first(floats.get("_GradientScale"), default=atlas_padding + 1.0),
            weight_normal=_float_first(floats.get("_WeightNormal")),
            weight_bold=_float_first(floats.get("_WeightBold"), default=0.75),
            face_dilate=_float_first(floats.get("_FaceDilate")),
            outline_width=_float_first(floats.get("_OutlineWidth")),
            outline_softness=_float_first(floats.get("_OutlineSoftness")),
            sharpness=_float_first(floats.get("_Sharpness")),
            normal_spacing_offset=_float_first(row.get("normal_spacing_offset")),
            bold_spacing=_float_first(row.get("bold_spacing")),
            scale_ratio_a=_float_first(floats.get("_ScaleRatioA"), default=1.0),
            scale_ratio_b=_float_first(floats.get("_ScaleRatioB"), default=1.0),
            scale_ratio_c=_float_first(floats.get("_ScaleRatioC"), default=1.0),
            glow_offset=_float_first(floats.get("_GlowOffset")),
            glow_outer=_float_first(floats.get("_GlowOuter")),
            underlay_softness=_float_first(floats.get("_UnderlaySoftness")),
            underlay_offset_x=_float_first(floats.get("_UnderlayOffsetX")),
            underlay_offset_y=_float_first(floats.get("_UnderlayOffsetY")),
            fallback_names=_nonempty_strings(row.get("fallback_font_asset_names")),
            glyphs=cls._load_character_table(base, row, record),
        )

    @staticmethod
    def _sort_assets(assets: dict[str, list[TMPFontAsset]]) -> None:
        for rows in assets.values():
            rows.sort(key=lambda asset: (asset.bundle != "custom_profile_font.bundle", asset.name))

    @staticmethod
    def _source_font_path(base: Path, row: dict[str, Any], record=None) -> Path | None:
        rel = row.get("source_font_data_path")
        if not rel:
            return None
        path = Path(str(rel)).expanduser()
        candidates = [path] if path.is_absolute() else [base / path, Path(__file__).resolve().parent / path]
        for candidate in candidates:
            if record is not None:
                record(candidate)  # a font that ARRIVES later must invalidate the cached tables
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _atlas_paths(base: Path, row: dict[str, Any], record=None) -> list[Path]:
        atlas_dir = base / "atlases"
        if record is not None:
            record(atlas_dir)  # glob result baked into the tables; dir mtime moves on add/remove
        paths: list[Path] = []
        for path_id in row.get("atlas_textures", []) or []:
            matches = sorted(atlas_dir.glob(f"*_{path_id}.png"))
            if matches:
                paths.append(matches[0])
        return paths

    @classmethod
    def _load_character_table(cls, base: Path, row: dict[str, Any], record=None) -> dict[int, TMPGlyphMetrics]:
        tables = cls._character_table_rows(base, row, record)
        if tables is None:
            return {}
        chars, glyphs = tables
        glyph_by_index = {_int_first(glyph.get("m_Index")): glyph for glyph in glyphs}
        out: dict[int, TMPGlyphMetrics] = {}
        for char in chars:
            glyph = glyph_by_index.get(_int_first(char.get("m_GlyphIndex")))
            if glyph:
                out[_int_first(char.get("m_Unicode"))] = cls._glyph_metrics_from_rows(char, glyph)
        return out

    @staticmethod
    def _character_table_rows(
        base: Path, row: dict[str, Any], record=None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        char_rel = row.get("character_table_path")
        glyph_rel = row.get("glyph_table_path")
        if not char_rel or not glyph_rel:
            return None
        char_path = base / str(char_rel)
        glyph_path = base / str(glyph_rel)
        if record is not None:
            record(char_path)
            record(glyph_path)
        if not char_path.exists() or not glyph_path.exists():
            return None
        return json.loads(char_path.read_text(encoding="utf-8")), json.loads(glyph_path.read_text(encoding="utf-8"))

    @staticmethod
    def _glyph_metrics_from_rows(char: dict[str, Any], glyph: dict[str, Any]) -> TMPGlyphMetrics:
        metrics = _mapping_or_empty(glyph.get("m_Metrics"))
        rect = _mapping_or_empty(glyph.get("m_GlyphRect"))
        return TMPGlyphMetrics(
            width=_float_first(metrics.get("m_Width")),
            height=_float_first(metrics.get("m_Height")),
            bearing_x=_float_first(metrics.get("m_HorizontalBearingX")),
            bearing_y=_float_first(metrics.get("m_HorizontalBearingY")),
            advance=_float_first(metrics.get("m_HorizontalAdvance")),
            rect_x=_int_first(rect.get("m_X")),
            rect_y=_int_first(rect.get("m_Y")),
            rect_w=_int_first(rect.get("m_Width")),
            rect_h=_int_first(rect.get("m_Height")),
            glyph_scale=_float_first(char.get("m_Scale"), glyph.get("m_Scale"), default=1.0),
            atlas_index=_int_first(glyph.get("m_AtlasIndex")),
        )

    def active_asset(self, font_name: str) -> TMPFontAsset | None:
        rows = self.assets.get(font_name, [])
        return rows[0] if rows else None

    def source_font_path(self, font_name: str) -> Path | None:
        active = self.active_asset(font_name)
        if active is None:
            return None
        return self.runtime_source_font_path(active)

    def runtime_source_font_path(self, asset: TMPFontAsset) -> Path | None:
        if asset.source_font_path is not None and asset.source_font_path.exists():
            return asset.source_font_path
        if self.runtime_fonts_dir is None:
            return None

        base_names = [asset.name]
        if asset.name.endswith("-OnDemand"):
            base_names.append(asset.name.removesuffix("-OnDemand"))
        for name in list(base_names):
            lower = name.lower()
            if lower not in base_names:
                base_names.append(lower)

        for name in base_names:
            for suffix in (".otf", ".ttf", _ALT_OTF_SUFFIX):
                candidate = self.runtime_fonts_dir / f"{name}{suffix}"
                if candidate.exists():
                    return candidate
        return None

    def source_asset_candidates(self, font_name: str, include_fallback: bool) -> list[TMPFontAsset]:
        if self.source_assets is self.assets:
            return self.metric_asset_candidates(font_name, include_fallback)
        candidates: list[TMPFontAsset] = []
        active = self.active_asset(font_name)
        for asset in self.source_assets.get(font_name, []):
            candidates.append(asset)
        if include_fallback and active is not None:
            for fallback in active.fallback_names:
                candidates.extend(self.source_assets.get(fallback, []))
        deduped: list[TMPFontAsset] = []
        seen: set[tuple[str, str]] = set()
        for asset in candidates:
            key = (asset.bundle, asset.name)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(asset)
        return deduped

    def static_asset(self, name: str) -> TMPFontAsset | None:
        for asset in self.assets.get(name, []):
            if asset.has_static_glyphs:
                return asset
        return None

    def metric_asset_candidates(self, font_name: str, include_fallback: bool) -> list[TMPFontAsset]:
        candidates: list[TMPFontAsset] = []
        active = self.active_asset(font_name)
        if active is not None:
            candidates.append(active)
        on_demand = self.static_asset(font_name + "-OnDemand")
        if on_demand is not None:
            candidates.append(on_demand)
        if include_fallback and active is not None:
            for fallback in active.fallback_names:
                fallback_asset = self.active_asset(fallback)
                if fallback_asset is not None:
                    candidates.append(fallback_asset)
                fallback_on_demand = self.static_asset(fallback + "-OnDemand")
                if fallback_on_demand is not None:
                    candidates.append(fallback_on_demand)
        deduped: list[TMPFontAsset] = []
        seen: set[tuple[str, str]] = set()
        for asset in candidates:
            key = (asset.bundle, asset.name)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(asset)
        return deduped

    def glyph_metrics(
        self, font_name: str, ch: str, font_size: float, include_fallback: bool
    ) -> TMPGlyphMetrics | None:
        codepoint = ord(ch)
        for asset in self.metric_asset_candidates(font_name, include_fallback):
            metrics = asset.glyphs.get(codepoint)
            if metrics is None:
                continue
            scale = font_size / max(1.0, asset.point_size)
            return TMPGlyphMetrics(
                width=metrics.width * scale,
                height=metrics.height * scale,
                bearing_x=metrics.bearing_x * scale,
                bearing_y=metrics.bearing_y * scale,
                advance=metrics.advance * scale,
                rect_x=metrics.rect_x,
                rect_y=metrics.rect_y,
                rect_w=metrics.rect_w,
                rect_h=metrics.rect_h,
                glyph_scale=metrics.glyph_scale,
                atlas_index=metrics.atlas_index,
            )
        return None

    def glyph_asset_for(
        self,
        font_name: str,
        ch: str,
        include_fallback: bool,
    ) -> tuple[TMPFontAsset, TMPGlyphMetrics] | None:
        if not ch:
            return None
        codepoint = ord(ch[0])
        for asset in self.metric_asset_candidates(font_name, include_fallback):
            metrics = asset.glyphs.get(codepoint)
            if metrics is not None:
                return asset, metrics
        return None

    def source_glyph_metrics(
        self,
        font_name: str,
        ch: str,
        font_size: float,
        include_fallback: bool = False,
    ) -> TMPGlyphMetrics | None:
        if not ch:
            return None
        for asset in self.source_asset_candidates(font_name, include_fallback):
            if self.runtime_source_font_path(asset) is None:
                continue
            metrics = self._source_glyph_metrics_for_asset(asset, ch, font_size)
            if metrics is not None:
                return metrics
        return None

    def _source_glyph_metrics_for_asset(
        self,
        asset: TMPFontAsset,
        ch: str,
        font_size: float,
    ) -> TMPGlyphMetrics | None:
        path = self.runtime_source_font_path(asset)
        if path is None or not ch:
            return None
        key = (path, ord(ch[0]), round(font_size, 4))
        if key in self._source_metrics:
            return self._source_metrics[key]
        if asset.point_size > 0 and abs(font_size - asset.point_size) > 1.0e-4:
            base_metrics = self._source_glyph_metrics_for_asset(asset, ch, asset.point_size)
            if base_metrics is None:
                self._source_metrics[key] = None
                return None
            scale = font_size / asset.point_size
            metrics = TMPGlyphMetrics(
                width=base_metrics.width * scale,
                height=base_metrics.height * scale,
                bearing_x=base_metrics.bearing_x * scale,
                bearing_y=base_metrics.bearing_y * scale,
                advance=base_metrics.advance * scale,
                rect_x=base_metrics.rect_x,
                rect_y=base_metrics.rect_y,
                rect_w=base_metrics.rect_w,
                rect_h=base_metrics.rect_h,
                glyph_scale=base_metrics.glyph_scale,
                atlas_index=base_metrics.atlas_index,
            )
            self._source_metrics[key] = metrics
            return metrics
        try:
            metrics = self._load_source_glyph_metrics(path, ch[0], font_size)
        except Exception:
            metrics = None
        self._source_metrics[key] = metrics
        return metrics

    def _load_source_glyph_metrics(self, path: Path, ch: str, font_size: float) -> TMPGlyphMetrics | None:
        ft = freetype_metrics()
        if ft is not None:
            metrics = ft.glyph_metrics(path, ch, font_size)
            if metrics is not None:
                return metrics

        try:
            from fontTools.pens.boundsPen import BoundsPen
            from fontTools.ttLib import TTFont
        except ImportError:
            return None

        font = self._source_fonts.get(path)
        if font is None:
            font = TTFont(str(path))
            self._source_fonts[path] = font
        cmap = font.getBestCmap() or {}
        glyph_name = cmap.get(ord(ch))
        if not glyph_name:
            return None
        units_per_em = float(font["head"].unitsPerEm or 1000)
        advance_width, _ = font["hmtx"][glyph_name]
        glyph_set = font.getGlyphSet()
        pen = BoundsPen(glyph_set)
        glyph_set[glyph_name].draw(pen)
        if pen.bounds is None:
            x_min = y_min = x_max = y_max = 0.0
        else:
            x_min, y_min, x_max, y_max = (float(v) for v in pen.bounds)
        scale = font_size / max(1.0, units_per_em)
        return TMPGlyphMetrics(
            width=max(0.0, (x_max - x_min) * scale),
            height=max(0.0, (y_max - y_min) * scale),
            bearing_x=x_min * scale,
            bearing_y=y_max * scale,
            advance=max(0.0, float(advance_width) * scale),
            rect_x=0,
            rect_y=0,
            rect_w=0,
            rect_h=0,
            glyph_scale=1.0,
            atlas_index=0,
        )

    def line_height(
        self, font_name: str, style_size: float, font_scale: float, divide_face_scale: bool
    ) -> float | None:
        asset = self.active_asset(font_name)
        if asset is None or asset.point_size <= 0 or asset.line_height <= 0:
            return None
        scale = font_scale / max(1.0, asset.face_scale) if divide_face_scale else 1.0
        return style_size * (asset.line_height / asset.point_size) * scale

    def face_extents(self, font_name: str, style_size: float, font_scale: float) -> tuple[float, float] | None:
        asset = self.active_asset(font_name)
        if asset is None or asset.point_size <= 0:
            return None
        scale = style_size * font_scale / asset.point_size
        return asset.ascent_line * scale, asset.descent_line * scale

    def em_scale(self, font_name: str, style_size: float, font_scale: float) -> float | None:
        asset = self.active_asset(font_name)
        if asset is None or asset.point_size <= 0:
            return None
        return style_size * font_scale / asset.point_size

    def tab_advance(self, font_name: str, font_size: float) -> float | None:
        asset = self.active_asset(font_name)
        if asset is None or asset.point_size <= 0 or asset.tab_width <= 0:
            return None
        return asset.tab_width * font_size / asset.point_size

    def bold_spacing_advance(self, font_name: str, font_size: float) -> float:
        asset = self.active_asset(font_name)
        if asset is None or asset.point_size <= 0:
            return 0.0
        return asset.bold_spacing * font_size / asset.point_size

    def normal_spacing_advance(self, font_name: str, font_size: float) -> float:
        asset = self.active_asset(font_name)
        if asset is None or asset.point_size <= 0:
            return 0.0
        return asset.normal_spacing_offset * font_size / asset.point_size


def hex_to_rgba(color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    color = color_or("#ffffff", color).lstrip("#")
    return (
        int(color[0:2], 16),
        int(color[2:4], 16),
        int(color[4:6], 16),
        max(0, min(255, round(alpha * 255))),
    )


def unity_tint_rgba(
    tint: tuple[float, float, float, float] | tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Normalize Unity's 0..1 float or 0..255 integer tint representation."""

    if any(isinstance(value, float) and value <= 1.0 for value in tint):
        return tuple(max(0, min(255, round(float(value) * 255.0))) for value in tint)
    return tuple(max(0, min(255, round(float(value)))) for value in tint)


def content_type_for_kind(kind: str) -> tuple[int, str]:
    return CONTENT_TYPES.get(kind, (0, "Invalid"))


def content_data_id(kind: str, item: dict[str, Any]) -> int:
    if kind == "general":
        return int(item.get("type", item.get("id", 0)) or 0)
    if kind == "stamp":
        return int(item.get("id", item.get("stampId", 0)) or 0)
    return int(item.get("id", 0) or 0)


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resource_entries(value: Any) -> list[tuple[Any, Any]]:
    if isinstance(value, dict):
        wrapped_items = value.get("items")
        if isinstance(wrapped_items, list):
            return [(None, item) for item in wrapped_items]
        return list(value.items())
    if isinstance(value, list):
        return [(None, item) for item in value]
    return []


def _coerced_resource_entry(key: Any, item: Any) -> tuple[int, dict[str, Any]] | None:
    if not isinstance(item, dict):
        return None
    item_id = int_or_none(item.get("id"))
    if item_id is None:
        item_id = int_or_none(key)
    return None if item_id is None else (item_id, item)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _png_resource_filename(resource: dict[str, Any]) -> str | None:
    file_name = str(resource.get("fileName", "")).strip("/")
    if not file_name:
        return None
    return file_name if file_name.lower().endswith(".png") else f"{file_name}.png"


def _general_text_tokens(raw_line: str) -> list[str]:
    tokens: list[str] = []
    ascii_token = ""
    for char in raw_line:
        if char.isascii() and (char.isalnum() or char in "._-@:/#"):
            ascii_token += char
        else:
            if ascii_token:
                tokens.append(ascii_token)
                ascii_token = ""
            tokens.append(char)
    if ascii_token:
        tokens.append(ascii_token)
    return tokens


def _append_oversized_general_token(token: str, line: str, max_width: int, text_width, lines: list[str]) -> str:
    for char in token:
        trial = line + char
        if line and text_width(trial) > max_width:
            lines.append(line)
            line = char
        else:
            line = trial
    return line


def _append_general_token(token: str, line: str, max_width: int, text_width, lines: list[str]) -> str:
    if line and text_width(line + token) > max_width:
        lines.append(line)
        line = ""
    if text_width(token) > max_width:
        return _append_oversized_general_token(token, line, max_width, text_width, lines)
    return line + token


def summarize_resource(resource: dict[str, Any] | None) -> dict[str, Any] | None:
    if not resource:
        return None
    keys = (
        "customProfileResourceType",
        "id",
        "seq",
        "name",
        "resourceLoadType",
        "resourceLoadVal",
        "assetbundleName",
        "fileName",
        "customProfileResourceCollectionType",
        "groupId",
        "characterId",
    )
    return {key: resource[key] for key in keys if key in resource}


def summarize_card_master(card: dict[str, Any] | None) -> dict[str, Any] | None:
    if not card:
        return None
    keys = (
        "id",
        "characterId",
        "assetbundleName",
        "assetBundleName",
        "cardRarityType",
        "attr",
        "prefix",
    )
    return {key: card[key] for key in keys if key in card}


def summarize_honor_master(honor: dict[str, Any] | None) -> dict[str, Any] | None:
    if not honor:
        return None
    keys = ("id", "seq", "groupId", "honorRarity", "name", "assetbundleName")
    return {key: honor[key] for key in keys if key in honor}


def summarize_honor_group(group: dict[str, Any] | None) -> dict[str, Any] | None:
    if not group:
        return None
    keys = ("id", "name", "honorType", "backgroundAssetbundleName", "backgroundAssetBundleName", "frameName")
    return {key: group[key] for key in keys if key in group}


def bool_from_profile(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "done"}
    return bool(value)


def tmp_dynamic_sdf_alpha_threshold(asset: "TMPFontAsset | None") -> int:
    if asset is None:
        return TMP_DYNAMIC_SDF_ALPHA_THRESHOLD
    name = asset.name.removesuffix("-OnDemand")
    return TMP_DYNAMIC_SDF_ALPHA_THRESHOLD_BY_FONT.get(name, TMP_DYNAMIC_SDF_ALPHA_THRESHOLD)


def edt_1d_squared(values: Any) -> Any:
    import numpy as np

    n = int(values.shape[0])
    if n <= 0:
        return values.astype(np.float32, copy=True)
    v = np.zeros(n, dtype=np.int32)
    z = np.empty(n + 1, dtype=np.float64)
    out = np.empty(n, dtype=np.float32)
    k = 0
    v[0] = 0
    z[0] = -np.inf
    z[1] = np.inf
    for q in range(1, n):
        while True:
            p = int(v[k])
            denom = 2.0 * (q - p)
            s = ((float(values[q]) + q * q) - (float(values[p]) + p * p)) / denom
            if s > z[k] or k == 0:
                break
            k -= 1
        if s <= z[k]:
            s = np.inf
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = np.inf
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        p = int(v[k])
        out[q] = (q - p) * (q - p) + values[p]
    return out


def edt_to_features(features: Any) -> Any:
    import numpy as np

    features = np.asarray(features, dtype=bool)
    height, width = features.shape
    if not features.any():
        return np.full((height, width), np.inf, dtype=np.float32)
    inf = float(height * height + width * width + 1)
    data = np.where(features, 0.0, inf).astype(np.float32)
    temp = np.empty_like(data)
    for x in range(width):
        temp[:, x] = edt_1d_squared(data[:, x])
    dist2 = np.empty_like(data)
    for y in range(height):
        dist2[y, :] = edt_1d_squared(temp[y, :])
    return np.sqrt(dist2, dtype=np.float32)


def alpha_mask_to_sdf_field(
    mask: Image.Image, spread: float, alpha_threshold: int = TMP_DYNAMIC_SDF_ALPHA_THRESHOLD
) -> Any:
    import numpy as np

    alpha = np.asarray(mask.convert("L"), dtype=np.uint8)
    binary = alpha >= alpha_threshold
    try:
        import cv2

        binary_u8 = binary.astype(np.uint8) * 255
        inside = cv2.distanceTransform(binary_u8, cv2.DIST_L2, TMP_DYNAMIC_SDF_DISTANCE_MASK_SIZE)
        outside = cv2.distanceTransform(255 - binary_u8, cv2.DIST_L2, TMP_DYNAMIC_SDF_DISTANCE_MASK_SIZE)
    except ImportError:
        inside = edt_to_features(~binary)
        outside = edt_to_features(binary)
    signed = inside - outside
    aa = (alpha.astype(np.float32) / 255.0) - binary.astype(np.float32)
    return np.clip(0.5 + (signed + aa) / max(1.0, 2.0 * spread), 0.0, 1.0)


def rgba_from_premul(rgb_premul: Any, alpha: Any) -> Any:
    import numpy as np

    rgb = np.zeros_like(rgb_premul)
    visible = alpha > 1.0e-6
    rgb[visible] = rgb_premul[visible] / alpha[visible][:, None]
    rgba = np.empty((*alpha.shape, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)
    rgba[:, :, 3] = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
    return rgba


def premultiply_rgba_image(image: Image.Image) -> Image.Image:
    import numpy as np

    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[:, :, 3:4] / 255.0
    rgba[:, :, :3] *= alpha
    return Image.fromarray(np.clip(np.rint(rgba), 0, 255).astype(np.uint8), "RGBA")


def unpremultiply_rgba_image(image: Image.Image) -> Image.Image:
    import numpy as np

    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[:, :, 3] / 255.0
    rgb_premul = rgba[:, :, :3] / 255.0
    return Image.fromarray(rgba_from_premul(rgb_premul, alpha), "RGBA")


def harden_rgba_alpha(image: Image.Image, strength: float) -> Image.Image:
    if strength <= 1.0 or image.mode != "RGBA":
        return image

    import numpy as np

    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[:, :, 3]
    max_alpha = float(alpha.max()) if alpha.size else 0.0
    if max_alpha <= 0.0:
        return image
    coverage = np.clip(alpha / max_alpha, 0.0, 1.0)
    hardened = 1.0 - np.power(1.0 - coverage, strength)
    rgba[:, :, 3] = np.clip(np.rint(hardened * max_alpha), 0, 255)
    return Image.fromarray(rgba.astype(np.uint8), "RGBA")


def resize_rgba_premul(image: Image.Image, size: tuple[int, int], resample: Image.Resampling) -> Image.Image:
    if image.mode != "RGBA" or image.getchannel("A").getextrema() == (255, 255):
        return image.resize(size, resample)
    return unpremultiply_rgba_image(premultiply_rgba_image(image).resize(size, resample))


def transform_rgba_premul(
    image: Image.Image,
    size: tuple[int, int],
    method: Image.Transform,
    data: tuple[float, float, float, float, float, float],
    resample: Image.Resampling,
    fillcolor: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> Image.Image:
    if image.mode != "RGBA" or image.getchannel("A").getextrema() == (255, 255):
        return image.transform(size, method, data, resample, fillcolor=fillcolor)
    fill_alpha = fillcolor[3] / 255.0
    premul_fill = (
        round(fillcolor[0] * fill_alpha),
        round(fillcolor[1] * fill_alpha),
        round(fillcolor[2] * fill_alpha),
        fillcolor[3],
    )
    transformed = premultiply_rgba_image(image).transform(size, method, data, resample, fillcolor=premul_fill)
    return unpremultiply_rgba_image(transformed)


def font_file(fonts: Path, font_name: str, rodin_font: str = "ttf") -> Path:
    if font_name == "FOT-RodinNTLGPro-DB":
        if rodin_font == "otf":
            suffixes = (".otf", ".ttf", _ALT_OTF_SUFFIX)
        else:
            suffixes = (".ttf", ".otf", _ALT_OTF_SUFFIX)
    else:
        suffixes = (".otf", ".ttf", _ALT_OTF_SUFFIX)
    for suffix in suffixes:
        candidate = fonts / (font_name + suffix)
        if candidate.exists():
            return candidate
    return fonts / _DEFAULT_FONT_FILENAME


def sharp_triangle_alpha(size: tuple[int, int]) -> Image.Image:
    scale = 4
    hi_size = (size[0] * scale, size[1] * scale)
    img = Image.new("L", hi_size, 0)
    draw = ImageDraw.Draw(img)
    w, h = hi_size
    draw.polygon(
        ((w / 2, 0), (w, h), (0, h)),
        fill=255,
    )
    return img.resize(size, Image.Resampling.LANCZOS)


def sharp_triangle_distance(size: tuple[int, int]) -> Image.Image:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return sharp_triangle_alpha(size)

    mask = np.zeros((size[1], size[0]), dtype=np.uint8)
    points = np.array(
        [
            (size[0] // 2, 0),
            (size[0] - 1, size[1] - 1),
            (0, size[1] - 1),
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(mask, [points], 255)
    inside = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    outside = cv2.distanceTransform(255 - mask, cv2.DIST_L2, 5)
    signed = inside - outside
    # Match the extracted SDF assets roughly: 0.5 at the edge and a wide falloff.
    spread = min(size) * 0.125
    sdf = np.clip(0.5 + signed / (2.0 * spread), 0.0, 1.0)
    return Image.fromarray((sdf * 255.0).round().astype(np.uint8), "L")


def largest_component_mask(source: Image.Image, threshold: int = 16) -> Image.Image:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return Image.new("L", source.size, 255)

    arr = np.array(source.convert("L"))
    mask = (arr >= threshold).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return Image.new("L", source.size, 255)
    label = max(range(1, count), key=lambda idx: int(stats[idx, cv2.CC_STAT_AREA]))
    keep = (labels == label).astype(np.uint8) * 255
    return Image.fromarray(keep, "L")


def load_font(path: Path, size: float) -> ImageFont.FreeTypeFont:
    # Process-lifetime per-thread cache; previously this reopened the font file on every call
    # (200-400 times per request), the second-largest cold-render cost.
    return get_render_font(path, size)


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 1.0 if value >= edge1 else 0.0
    t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def sdf_threshold_alpha(alpha: Image.Image, threshold: float, softness: float) -> Image.Image:
    edge0 = threshold - softness
    edge1 = threshold + softness
    lut = [round(smoothstep(edge0, edge1, v / 255.0) * 255) for v in range(256)]
    return alpha.point(lut)


def tmp_horizontal_alignment(tmp_type: int) -> str:
    horizontal = tmp_type & 0x00FF
    if horizontal == TMP_HORIZONTAL_CENTER:
        return "center"
    if horizontal == TMP_HORIZONTAL_RIGHT:
        return "right"
    if horizontal == TMP_HORIZONTAL_JUSTIFIED:
        return "justified"
    if horizontal == TMP_HORIZONTAL_FLUSH:
        return "flush"
    if horizontal == TMP_HORIZONTAL_GEOMETRY:
        return "geometry"
    return "left"


def tmp_vertical_alignment(tmp_type: int) -> str:
    vertical = tmp_type & 0xFF00
    if vertical == TMP_VERTICAL_TOP:
        return "top"
    if vertical == TMP_VERTICAL_BOTTOM:
        return "bottom"
    if vertical == TMP_VERTICAL_BASELINE:
        return "baseline"
    if vertical == TMP_VERTICAL_GEOMETRY:
        return "geometry"
    if vertical == TMP_VERTICAL_CAPLINE:
        return "capline"
    return "middle"


def tmp_line_offset_x(horizontal: str, box_w: float, line_w: float) -> float:
    remaining = box_w - line_w
    if horizontal == "center":
        return remaining / 2
    if horizontal == "right":
        return remaining
    return 0.0


def tmp_content_offset_y(vertical: str, box_h: float, content_h: float) -> float:
    remaining = box_h - content_h
    if vertical == "top":
        return 0.0
    if vertical == "bottom":
        return remaining
    return remaining / 2


def tmp_native_anchor_y(vertical: str, box_h: float, max_ascender: float, max_descender: float) -> float:
    if vertical == "top":
        return box_h / 2 - max_ascender
    if vertical == "bottom":
        return -box_h / 2 - max_descender
    if vertical == "baseline":
        return 0.0
    return -(max_ascender + max_descender) / 2


class PNGRenderer:
    def __init__(
        self,
        masterdata: Path | None,
        assets: Path,
        fonts: Path,
        text_pivot: str = DEFAULT_TEXT_PIVOT,
        tmp_scale_mode: str = DEFAULT_TMP_SCALE_MODE,
        rotation_sign: int = DEFAULT_ROTATION_SIGN,
        tmp_font_scale: float = DEFAULT_TMP_FONT_SCALE,
        text_layout: str = "tmp",
        position_scale: float | None = None,
        tmp_line_mode: str = DEFAULT_TMP_LINE_MODE,
        tmp_box_mode: str = "preferred",
        include_empty_lines: bool = True,
        tmp_box_width: float = TMP_DEFAULT_TEXT_BOX_W,
        tmp_box_width_factor: float = TMP_TEXT_BOX_W_SIZE_FACTOR,
        tmp_line_height_factor: float = TMP_LINE_HEIGHT_FACTOR,
        tmp_line_spacing_factor: float = TMP_LINE_SPACING_FACTOR,
        tmp_preferred_padding_x: float = TMP_PREFERRED_PADDING_X,
        tmp_preferred_padding_y: float = TMP_PREFERRED_PADDING_Y,
        rodin_font: str = "auto",
        tmp_block_mode: str = DEFAULT_TMP_BLOCK_MODE,
        draw_order: str = "global",
        shape_outline_mode: str = "sdf",
        triangle_mode: str = DEFAULT_TRIANGLE_MODE,
        text_vertical_mode: str = DEFAULT_TEXT_VERTICAL_MODE,
        tmp_space_width_factor: float = DEFAULT_TMP_SPACE_WIDTH_FACTOR,
        tmp_text_render_mode: str = DEFAULT_TMP_TEXT_RENDER_MODE,
        tmp_dynamic_sdf: bool = DEFAULT_TMP_DYNAMIC_SDF,
        tmp_pillow_stroke_factor: float = DEFAULT_TMP_PILLOW_STROKE_FACTOR,
        shape_sdf_ratio_scale: float = SHAPE_SDF_RATIO_SCALE,
        shape_sdf_outer_factor: float = SHAPE_SDF_OUTER_FACTOR,
        shape_sdf_face_factor: float = SHAPE_SDF_FACE_FACTOR,
        shape_sdf_softness: float = SHAPE_SDF_SOFTNESS,
        shape_sdf_source: str = "rgb",
        shape_sdf_screen_fwidth: bool = SHAPE_SDF_SCREEN_FWIDTH,
        position_scale_x: float | None = None,
        position_scale_y: float | None = None,
        tmp_font_metadata: Path | None = DEFAULT_TMP_FONT_METADATA,
        tmp_metrics_mode: str = DEFAULT_TMP_METRICS_MODE,
        shape_sprite_dir: Path | None = DEFAULT_SHAPE_SPRITE_DIR,
        tmp_native_line_gap: bool = DEFAULT_TMP_NATIVE_LINE_GAP,
        profile_context: dict[str, Any] | None = None,
        resources: dict[str, Any] | None = None,
        canvas_w: int = CANVAS_W,
        canvas_h: int = CANVAS_H,
        origin_x: float | None = None,
        origin_y: float | None = None,
        parallel_workers: int = 1,
        parallel_stage: str = "transform",
        clip_canvas_transform: bool = True,
        unity_ui_sprite_dir: Path | None = DEFAULT_UNITY_UI_SPRITE_DIR,
        region: str = "cn",
        tmp_decorative_face_only: bool = DEFAULT_TMP_DECORATIVE_FACE_ONLY,
        premultiply_alpha_transforms: bool = DEFAULT_PREMULTIPLY_ALPHA_TRANSFORMS,
        tmp_decorative_direct_raster: bool = DEFAULT_TMP_DECORATIVE_DIRECT_RASTER,
        tmp_decorative_alpha_harden: float = 1.0,
        max_layer_pixels: int = DEFAULT_MAX_LAYER_PIXELS,
        max_scene_bytes: int = DEFAULT_MAX_SCENE_BYTES,
    ) -> None:
        self.masterdata = masterdata
        self.resources = _optional_dict(resources)
        self.assets = assets
        self.game_assets = _game_assets_root(assets)
        self.region = self.normalize_region(region)
        self.fonts = fonts
        self.profile_context = _optional_dict(profile_context)
        self.canvas_w = canvas_w
        self.canvas_h = canvas_h
        self.origin_x = _default_if_none(origin_x, float(canvas_w) / 2.0)
        self.origin_y = _default_if_none(origin_y, float(canvas_h) / 2.0)
        self.parallel_workers = _positive_int(parallel_workers)
        self.parallel_stage = _choice_or_default(parallel_stage, {"serial", "transform", "full"}, "transform")
        self.clip_canvas_transform = clip_canvas_transform
        self.tmp_decorative_face_only = tmp_decorative_face_only
        self.premultiply_alpha_transforms = premultiply_alpha_transforms
        self.tmp_decorative_direct_raster = tmp_decorative_direct_raster
        self.tmp_decorative_alpha_harden = _positive_float(tmp_decorative_alpha_harden)
        self.max_layer_pixels = _positive_int(max_layer_pixels)
        self.max_scene_bytes = _positive_int(max_scene_bytes)
        self.text_pivot = text_pivot
        self.tmp_scale_mode = tmp_scale_mode
        self.rotation_sign = rotation_sign
        self.tmp_font_scale = tmp_font_scale
        self.text_layout = text_layout
        base_scale_x = _default_if_none(position_scale, DEFAULT_POSITION_SCALE_X)
        base_scale_y = _default_if_none(position_scale, DEFAULT_POSITION_SCALE_Y)
        self.position_scale = _default_if_none(position_scale, DEFAULT_POSITION_SCALE)
        self.position_scale_x = _default_if_none(position_scale_x, base_scale_x)
        self.position_scale_y = _default_if_none(position_scale_y, base_scale_y)
        self.tmp_line_mode = tmp_line_mode
        self.tmp_box_mode = tmp_box_mode
        self.include_empty_lines = include_empty_lines
        self.tmp_box_width = tmp_box_width
        self.tmp_box_width_factor = tmp_box_width_factor
        self.tmp_line_height_factor = tmp_line_height_factor
        self.tmp_line_spacing_factor = tmp_line_spacing_factor
        self.tmp_preferred_padding_x = tmp_preferred_padding_x
        self.tmp_preferred_padding_y = tmp_preferred_padding_y
        self.rodin_font = rodin_font
        self.tmp_block_mode = tmp_block_mode
        self.draw_order = draw_order
        self.shape_outline_mode = shape_outline_mode
        self.triangle_mode = triangle_mode
        self.text_vertical_mode = text_vertical_mode
        self.tmp_space_width_factor = tmp_space_width_factor
        self.tmp_text_render_mode = _choice_or_default(
            tmp_text_render_mode, TMP_TEXT_RENDER_MODES, DEFAULT_TMP_TEXT_RENDER_MODE
        )
        self.tmp_dynamic_sdf = tmp_dynamic_sdf
        self.tmp_pillow_stroke_factor = tmp_pillow_stroke_factor
        self.shape_sdf_ratio_scale = shape_sdf_ratio_scale
        self.shape_sdf_outer_factor = shape_sdf_outer_factor
        self.shape_sdf_face_factor = shape_sdf_face_factor
        self.shape_sdf_softness = shape_sdf_softness
        self.shape_sdf_source = _choice_or_default(shape_sdf_source, SHAPE_SDF_SOURCES, "rgb")
        self.shape_sdf_screen_fwidth = shape_sdf_screen_fwidth
        self.tmp_metrics_mode = _choice_or_default(tmp_metrics_mode, TMP_METRIC_MODES, "pil")
        self.tmp_font_library = TMPFontLibrary.load(tmp_font_metadata, runtime_fonts_dir=fonts)
        self.shape_sprite_dir = shape_sprite_dir
        self.tmp_native_line_gap = tmp_native_line_gap
        text_colors = self.load_resource_index(
            "customProfileTextColors", "custom_profile_text_colors", filename="customProfileTextColors.json"
        )
        text_fonts = self.load_resource_index(
            "customProfileTextFonts", "custom_profile_text_fonts", filename="customProfileTextFonts.json"
        )
        self.colors = {item_id: color_or("#444466", item.get("colorCode")) for item_id, item in text_colors.items()}
        self.text_fonts = {item_id: str(item.get("fontName", "")).strip() for item_id, item in text_fonts.items()}
        self.shapes = self.load_resource_index(
            "customProfileShapeResources", "custom_profile_shape_resources", filename="customProfileShapeResources.json"
        )
        self.player_infos = self.load_resource_index(
            "customProfilePlayerInfoResources",
            "custom_profile_player_info_resources",
            filename="customProfilePlayerInfoResources.json",
        )
        self.general_bgs = self.load_resource_index(
            "customProfileGeneralBackgroundResources",
            "custom_profile_general_background_resources",
            filename="customProfileGeneralBackgroundResources.json",
        )
        self.story_bgs = self.load_resource_index(
            "customProfileStoryBackgroundResources",
            "custom_profile_story_background_resources",
            filename="customProfileStoryBackgroundResources.json",
        )
        self.stand_members = self.load_resource_index(
            "customProfileMemberStandingPictureResources",
            "custom_profile_member_standing_picture_resources",
            filename="customProfileMemberStandingPictureResources.json",
        )
        self.collections = self.load_resource_index(
            "customProfileCollectionResources",
            "custom_profile_collection_resources",
            filename="customProfileCollectionResources.json",
        )
        self.omikujis = self.load_resource_index(
            "omikujis",
            "omikujiResources",
            "omikuji_resources",
            filename=_OMIKUJI_FILENAME,
        )
        self.others = self.load_resource_index(
            "customProfileEtcResources", "custom_profile_etc_resources", filename="customProfileEtcResources.json"
        )
        self.character_icons = self.load_resource_index(
            "customProfileCharacterIconResources",
            "custom_profile_character_icon_resources",
            filename="customProfileCharacterIconResources.json",
        )
        self.materials = self.load_resource_index(
            "customProfileMaterialResources",
            "custom_profile_material_resources",
            filename="customProfileMaterialResources.json",
        )
        self.user_interface_icons = self.load_resource_index(
            "customProfileUserInterfaceIconResources",
            "custom_profile_user_interface_icon_resources",
            filename="customProfileUserInterfaceIconResources.json",
        )
        self.stamps = self.load_resource_index("stamps", filename="stamps.json")
        self.cards = self.load_resource_index("cards", filename=_CARDS_FILENAME)
        self.honors = self.load_resource_index("honors", filename="honors.json")
        self.honor_groups = self.load_resource_index("honorGroups", "honor_groups", filename="honorGroups.json")
        self.bonds_honors = self.load_resource_index("bondsHonors", "bonds_honors", filename="bondsHonors.json")
        self.bonds_honor_words = self.load_resource_index(
            "bondsHonorWords", "bonds_honor_words", filename="bondsHonorWords.json"
        )
        self.game_character_units = self.load_resource_index(
            "gameCharacterUnits", "game_character_units", filename="gameCharacterUnits.json"
        )
        self.stamp_assets = self.load_resource_index("stampAssets", "stamp_assets")
        self.card_assets = self.load_resource_index("cardAssets", "card_assets")
        self.honor_requests = self.load_string_resource_map("honorRequests", "honor_requests")
        self.profile_honor_requests = self.load_string_resource_map("profileHonorRequests", "profile_honor_requests")
        self.bonds_honor_requests = self.load_string_resource_map("bondsHonorRequests", "bonds_honor_requests")
        self.story_favorite_resources = self.load_string_resource_map(
            "storyFavoriteResources", "story_favorite_resources"
        )
        self.chara_rank_icon_path_map = self.coerce_string_map(
            self.resources.get("charaRankIconPathMap", self.resources.get("chara_rank_icon_path_map", {}))
        )
        self.static_images = self.resolve_static_images_root()
        self.unity_ui_sprite_dir = unity_ui_sprite_dir or DEFAULT_UNITY_UI_SPRITE_DIR
        self._unity_ui_sprite_path_cache: dict[str, Path | None] = {}
        self._unity_ui_sprite_cache: dict[str, Image.Image | None] = {}
        self._shape_alpha_cache: dict[tuple[Path, str], Image.Image] = {}
        self._shape_field_cache: dict[tuple[Path, str, str], Image.Image] = {}
        self._sdf_alpha_cache: dict[tuple[Path, str, str, float, float], Image.Image] = {}
        self._shape_shader_basis_cache: dict[tuple[Path, str, str], tuple[Any, Any, Any]] = {}
        self._tmp_atlas_cache: dict[Path, Image.Image] = {}
        # L1 in front of the process-level GLYPH_SDF_CACHE / GLYPH_CONTOUR_CACHE pools: lock-free
        # per-request dicts keyed WITHOUT the font-file signature (this instance pins one asset
        # snapshot). The L2 keys add the signature plus the metadata-derived shading inputs.
        self._tmp_dynamic_glyph_cache: dict[tuple[str, str, str, float], TMPDynamicGlyphSDF | None] = {}
        self._tmp_vector_glyph_cache: dict[tuple[str, str, float], tuple[list[Any], Any] | None] = {}
        self._font_signature_memo: dict[str, tuple[int, int]] = {}
        self._tmp_render_char_cache: dict[tuple[str, str, bool], str] = {}
        self.native_audit: list[dict[str, Any]] = []
        self.tmp_layout_audit: list[dict[str, Any]] = []
        self._current_card_ref: dict[str, int] = {}

    def _reserve_retained_raster_bytes(self, current: int, additional: int, *, label: str) -> int:
        """Reserve bytes for a list of live user-derived rasters before creating the next one."""

        total = max(0, int(current)) + max(0, int(additional))
        if total > self.max_scene_bytes:
            raise ValueError(f"{label} would retain {total} bytes; limit is {self.max_scene_bytes}")
        return total

    def load_resource_index(self, *names: str, filename: str | None = None) -> dict[int, dict[str, Any]]:
        for name in names:
            value = self.resources.get(name)
            if value is not None:
                return self.coerce_resource_index(value)
        if filename is not None and self.masterdata is not None:
            return load_index(self.masterdata / filename)
        return {}

    def load_string_resource_map(self, *names: str) -> dict[str, dict[str, Any]]:
        for name in names:
            value = self.resources.get(name)
            if value is not None:
                return self.coerce_string_resource_map(value)
        return {}

    def coerce_resource_index(self, value: Any) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for key, item in _resource_entries(value):
            if entry := _coerced_resource_entry(key, item):
                item_id, resource = entry
                result[item_id] = resource
        return result

    def coerce_string_resource_map(self, value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for key, item in value.items():
            if isinstance(item, dict):
                result[str(key)] = item
        return result

    def coerce_string_map(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, str] = {}
        for key, item in value.items():
            text = str(item or "").strip()
            if text:
                result[str(key)] = text
        return result

    def resolve_static_images_root(self) -> Path:
        candidates: list[Path] = []
        for base in (self.assets, self.game_assets):
            for parent in (base, *base.parents[:6]):
                candidates.append(parent / "static_images")
                candidates.append(parent / "lunabot_static_images")
        candidates.extend(
            (
                Path("/app/haruki_drawing_api/data/static_images"),
                Path("/Users/deseer/PycharmProjects/Haruki-Drawing-API/data/static_images"),
                Path("/Users/deseer/PycharmProjects/Haruki-Drawing-API/data/lunabot_static_images"),
            )
        )
        seen: set[Path] = set()
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            if not path.exists():
                continue
            if (path / "card").exists() or (path / "honor").exists():
                return path
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    def normalize_region(self, region: str | None) -> str:
        region = str(region or "").strip().lower()
        if region:
            return region
        package_name = self.game_assets.parent.name
        if package_name.endswith("-assets"):
            return package_name.removesuffix("-assets")
        return "cn"

    def region_asset_package_name(self) -> str:
        return f"{self.region}-assets"

    def region_asset_roots(self, mode: str | None = None) -> list[Path]:
        mode = (mode or self.game_assets.name or REGION_ASSET_STARTAPP).strip().lower()
        if mode not in {REGION_ASSET_STARTAPP, REGION_ASSET_ONDEMAND}:
            mode = REGION_ASSET_STARTAPP

        package_name = self.region_asset_package_name()
        roots: list[Path] = []
        current_mode = self.game_assets.name
        current_package = self.game_assets.parent.name
        current_container = self.game_assets.parent.parent

        if current_mode == mode and current_package == package_name:
            roots.append(self.game_assets)

        if current_package.endswith("-assets"):
            if current_container.name == "asset":
                data_root = current_container.parent
                roots.append(data_root / "asset" / package_name / mode)
                roots.append(data_root / package_name / mode)
            else:
                data_root = current_container
                roots.append(data_root / package_name / mode)
                roots.append(data_root / "asset" / package_name / mode)

        roots.append(self.game_assets.parent.parent / "asset" / package_name / mode)
        roots.append(self.game_assets.parent.parent / package_name / mode)

        seen: set[Path] = set()
        result: list[Path] = []
        for root in roots:
            if root not in seen:
                result.append(root)
                seen.add(root)
        return result

    def preferred_region_asset_modes(self, rel: Path) -> tuple[str, ...]:
        top_level = rel.parts[0] if rel.parts else ""
        if top_level in ONDEMAND_PREFERRED_TOP_LEVEL:
            return (REGION_ASSET_ONDEMAND, REGION_ASSET_STARTAPP)
        return (REGION_ASSET_STARTAPP, REGION_ASSET_ONDEMAND)

    def region_asset_candidate_paths(self, rels: list[Path] | tuple[Path, ...]) -> list[Path]:
        candidates: list[Path] = []
        seen: set[Path] = set()
        for rel in rels:
            if not rel.parts:
                continue
            for mode in self.preferred_region_asset_modes(rel):
                for root in self.region_asset_roots(mode):
                    path = root / rel
                    if path not in seen:
                        candidates.append(path)
                        seen.add(path)
        return candidates

    def first_region_asset(self, rels: list[Path] | tuple[Path, ...]) -> Path | None:
        for path in self.region_asset_candidate_paths(rels):
            if path.exists():
                return path
        return None

    def static_image_path(self, *parts: str) -> Path | None:
        path = self.static_images.joinpath(*parts)
        return path if path.exists() else None

    def open_rgba(self, path: Path | None) -> Image.Image | None:
        if path is None or not path.exists():
            return None
        return self.open_checked_image(path, "RGBA")

    def open_checked_image(self, path: Path, mode: str) -> Image.Image:
        """Decode one custom-profile asset only after its header passes the layer budget."""

        with Image.open(path) as image:
            ensure_raster_size(
                image.size,
                max_pixels=self.max_layer_pixels,
                label=f"custom profile source asset {path.name}",
            )
            image.load()
            return image.convert(mode)

    def data_root_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for base in (self.assets, self.game_assets, self.static_images):
            for parent in (base, *base.parents[:8]):
                if parent.name == "asset":
                    candidates.append(parent.parent)
                    candidates.append(parent)
                if (parent / "asset").exists() or (parent / "static_images").exists():
                    candidates.append(parent)
        seen: set[Path] = set()
        result: list[Path] = []
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            result.append(path)
        return result

    def request_asset_candidates(self, raw_path: str | None) -> list[Path]:
        raw = str(raw_path or "").strip()
        if not raw:
            return []
        path = Path(raw)
        if path.is_absolute():
            return [path]
        clean = raw.strip("/")
        return _dedupe_paths(self._relative_request_asset_candidates(clean))

    def _relative_request_asset_candidates(self, clean: str) -> list[Path]:
        rel = Path(clean)
        candidates: list[Path] = []
        if clean.startswith("asset/"):
            without_asset = Path(clean.removeprefix("asset/"))
            for root in self.data_root_candidates():
                candidates.append(root / clean)
                candidates.append(root / without_asset)
        elif clean.startswith(f"{self.region_asset_package_name()}/"):
            for root in self.data_root_candidates():
                candidates.append(root / "asset" / rel)
                candidates.append(root / rel)
        elif clean.startswith("static_images/"):
            inner = Path(clean.removeprefix("static_images/"))
            candidates.append(self.static_images.parent / rel)
            candidates.append(self.static_images / inner)
            for root in self.data_root_candidates():
                candidates.append(root / rel)
        else:
            candidates.append(Path(clean))
            for root in (self.assets, self.game_assets, self.static_images.parent, *self.data_root_candidates()):
                candidates.append(root / rel)
        return candidates

    def resolve_request_asset_path(self, raw_path: str | None) -> Path | None:
        raw = str(raw_path or "").strip()
        if not raw:
            return None
        requested = Path(raw)
        if any(part == ".." for part in requested.parts):
            raise ValueError(f"custom profile asset path traversal is not allowed: {raw!r}")

        allowed_roots: list[Path] = []
        for root in (self.assets, self.game_assets, self.static_images, *self.data_root_candidates()):
            try:
                resolved_root = root.resolve(strict=True)
            except OSError:
                continue
            if resolved_root not in allowed_roots:
                allowed_roots.append(resolved_root)

        rejected_existing: Path | None = None
        for path in self.request_asset_candidates(raw_path):
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
                return resolved
            rejected_existing = resolved
        if rejected_existing is not None:
            raise ValueError(f"custom profile asset path is outside configured data roots: {raw!r}")
        return None

    def open_request_rgba(self, raw_path: str | None) -> Image.Image | None:
        return self.open_rgba(self.resolve_request_asset_path(raw_path))

    def honor_request_path(self, path: Path | None) -> str | None:
        if path is None:
            return None
        return path.as_posix()

    def resource_path(self, resource: dict[str, Any], fallback_dir: str | None = None) -> Path | None:
        if path := self._explicit_resource_path(resource):
            return path
        if self.masterdata is None:
            return None
        file_name = _png_resource_filename(resource)
        if file_name is None:
            return None
        rels = self._resource_relative_dirs(resource, fallback_dir)
        return self._existing_resource_path(rels, file_name)

    def _explicit_resource_path(self, resource: dict[str, Any]) -> Path | None:
        for key in ("imagePath", "image_path", "resourcePath", "resource_path", "filePath", "file_path"):
            if path := self.resolve_request_asset_path(str(resource.get(key, "") or "")):
                return path
        return None

    @staticmethod
    def _resource_relative_dirs(resource: dict[str, Any], fallback_dir: str | None) -> list[Path]:
        load_val = str(resource.get("resourceLoadVal", "")).strip("/")
        if load_val.startswith("custom_profile/"):
            return [Path(load_val.removeprefix("custom_profile/")), Path(load_val)]
        if load_val == "custom_profile":
            return [Path("."), Path("custom_profile")]
        return [Path(fallback_dir)] if fallback_dir else [Path(load_val)]

    def _existing_resource_path(self, rels: list[Path], file_name: str) -> Path | None:
        roots = [self.assets]
        if self.game_assets != self.assets:
            roots.append(self.game_assets)
        for root in roots:
            for rel in rels:
                path = root / rel / file_name
                if path.exists():
                    return path
        return None

    def stamp_resource_path(self, resource: dict[str, Any]) -> Path | None:
        if self.masterdata is None:
            return None
        for path in self.stamp_resource_candidates(resource):
            if path.exists():
                return path
        return None

    def stamp_resource_candidates(self, resource: dict[str, Any]) -> list[Path]:
        assetbundle_name = str(resource.get("assetbundleName", "")).strip("/")
        if not assetbundle_name:
            return []
        return self.region_asset_candidate_paths([Path("stamp") / assetbundle_name / f"{assetbundle_name}.png"])

    def shape_resource_path(self, resource: dict[str, Any]) -> Path | None:
        for key in ("imagePath", "image_path", "resourcePath", "resource_path", "filePath", "file_path"):
            if path := self.resolve_request_asset_path(str(resource.get(key, "") or "")):
                return path
        if self.masterdata is None:
            return None

        file_name = str(resource.get("fileName", "")).strip("/")
        if not file_name:
            return None
        if not file_name.lower().endswith(".png"):
            file_name += ".png"
        if self.shape_sprite_dir is not None:
            candidate = self.shape_sprite_dir / file_name
            if candidate.exists():
                return candidate
        return self.resource_path(resource, "shape")

    def render_card(self, card: dict[str, Any]) -> Image.Image:
        img = Image.new("RGBA", (self.canvas_w, self.canvas_h), (255, 255, 255, 255))
        card_ref = self.native_card_ref(card)
        previous_card_ref = self._current_card_ref
        self._current_card_ref = card_ref
        try:
            self._render_card_contents(img, card_ref, self.build_native_contents(card))
        finally:
            self._current_card_ref = previous_card_ref
        return img

    def _render_card_contents(self, img: Image.Image, card_ref: dict[str, int], contents: list[NativeContent]) -> None:
        if self.tmp_decorative_direct_raster:
            self._render_decorative_card_contents(img, card_ref, contents)
            return
        if self.parallel_stage == "full" and self.parallel_workers > 1 and len(contents) > 1:
            self._render_parallel_card_contents(img, card_ref, contents)
            return
        self._render_serial_card_contents(img, card_ref, contents)

    def _render_decorative_card_contents(
        self, img: Image.Image, card_ref: dict[str, int], contents: list[NativeContent]
    ) -> None:
        for content in contents:
            self._render_decorative_card_content(img, card_ref, content)

    def _render_decorative_card_content(
        self, img: Image.Image, card_ref: dict[str, int], content: NativeContent
    ) -> None:
        if self.render_content_direct_on_card(img, content):
            self.record_native_audit(card_ref, content, "rendered-direct", None)
            return
        try:
            rendered = self.render_and_prepare_content_for_card(content)
        except RasterSizeLimitError as exc:
            if self.render_oversized_tmp_text_direct(img, content, exc):
                self.record_native_audit(card_ref, content, "rendered-direct", None)
                return
            raise
        self._record_and_composite_prepared(img, card_ref, rendered)

    def _render_parallel_card_contents(
        self, img: Image.Image, card_ref: dict[str, int], contents: list[NativeContent]
    ) -> None:
        for rendered in self.render_contents_for_card_parallel(contents):
            self._record_and_composite_prepared(img, card_ref, rendered)

    def _record_and_composite_prepared(
        self, img: Image.Image, card_ref: dict[str, int], rendered: RenderedLayer
    ) -> None:
        self.record_native_audit(card_ref, rendered.content, rendered.status, rendered.result)
        if rendered.prepared is not None:
            img.alpha_composite(rendered.prepared.image, rendered.prepared.xy)

    def _render_serial_card_contents(
        self, img: Image.Image, card_ref: dict[str, int], contents: list[NativeContent]
    ) -> None:
        rendered_layers: list[
            tuple[
                NativeContent,
                tuple[Image.Image, tuple[float, float]] | tuple[Image.Image, tuple[float, float], bool],
            ]
        ] = []
        for content in contents:
            rendered = self.render_content_for_card(content)
            self.record_native_audit(card_ref, content, rendered.status, rendered.result)
            if isinstance(rendered.result, tuple):
                rendered_layers.append((content, rendered.result))
        for prepared in self.prepare_layers_for_card(rendered_layers):
            if prepared is not None:
                img.alpha_composite(prepared.image, prepared.xy)

    def render_content_direct_on_card(self, canvas: Image.Image, content: NativeContent) -> bool:
        if not self.tmp_decorative_direct_raster:
            return False
        if content.kind != "text" or not content.object_data.get("visible", False):
            return False
        if self.text_layout != "tmp" or self.tmp_text_render_mode != "sdf":
            return False
        if not self.is_decorative_text_item(content.item):
            return False
        return self.render_tmp_decorative_text_direct(canvas, content.item, content.object_data)

    def render_oversized_tmp_text_direct(
        self,
        canvas: Image.Image,
        content: NativeContent,
        exc: RasterSizeLimitError,
    ) -> bool:
        """Render a sparse TMP layer by glyph after its full local surface exceeds the budget."""

        if exc.label != "custom profile TMP text layer":
            return False
        if not self.tmp_decorative_direct_raster:
            return False
        if content.kind != "text" or not content.object_data.get("visible", False):
            return False
        if self.text_layout != "tmp" or self.tmp_text_render_mode != "sdf":
            return False
        return self.render_tmp_text_direct(canvas, content.item, content.object_data)

    def render_content_for_card(self, content: NativeContent) -> RenderedLayer:
        if not content.object_data.get("visible", False):
            return RenderedLayer(content, "hidden", None)
        layer = self.refresh_native_content(content)
        if isinstance(layer, NativeUnresolvedContent):
            return RenderedLayer(content, "unresolved", layer)
        if layer is None:
            return RenderedLayer(content, "missing", None)
        return RenderedLayer(content, "rendered", layer)

    def render_and_prepare_content_for_card(self, content: NativeContent) -> RenderedLayer:
        rendered = self.render_content_for_card(content)
        if not isinstance(rendered.result, tuple):
            return rendered
        return RenderedLayer(
            content=rendered.content,
            status=rendered.status,
            result=rendered.result,
            prepared=self.prepare_content_layer(content, rendered.result),
        )

    def render_contents_for_card_parallel(self, contents: list[NativeContent]) -> list[RenderedLayer]:
        max_workers = min(self.parallel_workers, len(contents))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="custom-profile-layer") as executor:
            return list(executor.map(self.render_and_prepare_content_for_card, contents))

    def prepare_layers_for_card(
        self,
        rendered_layers: list[
            tuple[
                NativeContent, tuple[Image.Image, tuple[float, float]] | tuple[Image.Image, tuple[float, float], bool]
            ]
        ],
    ) -> list[PreparedLayer | None]:
        if self.parallel_stage != "transform" or self.parallel_workers <= 1 or len(rendered_layers) <= 1:
            return [self.prepare_content_layer(content, layer) for content, layer in rendered_layers]
        max_workers = min(self.parallel_workers, len(rendered_layers))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="custom-profile-transform") as executor:
            return list(executor.map(lambda item: self.prepare_content_layer(item[0], item[1]), rendered_layers))

    def prepare_content_layer(
        self,
        content: NativeContent,
        layer: tuple[Image.Image, tuple[float, float]] | tuple[Image.Image, tuple[float, float], bool],
    ) -> PreparedLayer | None:
        prepared = self.prepare_transformed_layer(
            layer,
            content.object_data,
            content.kind,
            self.should_supersample_content_transform(content),
        )
        if (
            prepared is not None
            and content.kind == "text"
            and self.tmp_decorative_alpha_harden > 1.0
            and self.is_decorative_text_item(content.item)
        ):
            prepared = PreparedLayer(
                harden_rgba_alpha(prepared.image, self.tmp_decorative_alpha_harden),
                prepared.xy,
            )
        return prepared

    def native_card_ref(self, card: dict[str, Any]) -> dict[str, int]:
        return {
            "seq": int(card.get("seq", 0) or 0),
            "customProfileCardId": int(card.get("customProfileCardId", 0) or 0),
        }

    def build_native_contents(self, card: dict[str, Any]) -> list[NativeContent]:
        layout = card.get("customProfileCard") or {}
        buckets = (
            ("general", "generals"),
            ("general_background", "generalBackgrounds"),
            ("story_background", "storyBackgrounds"),
            ("stand_member", "standMembers"),
            ("card_member", "cardMembers"),
            ("honor", "honors"),
            ("bonds_honor", "bondsHonors"),
            ("collection", "collections"),
            ("other", "others"),
            ("character_icon", "characterIcons"),
            ("material", "materials"),
            ("user_interface_icon", "userInterfaceIcons"),
            ("stamp", "stamps"),
            ("shape", "shapes"),
            ("text", "texts"),
            ("mini_chara", "miniCharas"),
            ("screen_filter", "screenFilters"),
        )
        contents: list[NativeContent] = []
        for kind, key in buckets:
            for item in layout.get(key) or []:
                object_data = item["objectData"]
                contents.append(NativeContent(int(object_data["layer"]), kind, item, object_data))
        return sorted(contents, key=self.native_draw_order_key)

    def is_official_general_template(self, card: dict[str, Any]) -> bool:
        layout = card.get("customProfileCard") or {}
        generals = layout.get("generals") or []
        found_ids: set[int] = set()
        found_positions: set[int] = set()
        for item in generals:
            if not isinstance(item, dict):
                continue
            resource_id = int(item.get("playerInfoResourceId", item.get("type", item.get("id", 0))) or 0)
            found_ids.add(resource_id)
            object_data = item.get("objectData") or {}
            position = object_data.get("position") or {}
            expected = GENERAL_TEMPLATE_UNIT1_POSITIONS.get(resource_id)
            if (
                expected is not None
                and abs(float(position.get("x", 0.0)) - expected[0]) < 0.01
                and abs(float(position.get("y", 0.0)) - expected[1]) < 0.01
            ):
                found_positions.add(resource_id)
        return GENERAL_TEMPLATE_UNIT1_REQUIRED_IDS.issubset(found_ids) and GENERAL_TEMPLATE_UNIT1_REQUIRED_IDS.issubset(
            found_positions
        )

    def refresh_native_content(
        self,
        content: NativeContent,
    ) -> (
        tuple[Image.Image, tuple[float, float]]
        | tuple[Image.Image, tuple[float, float], bool]
        | NativeUnresolvedContent
        | None
    ):
        if content.kind == "shape":
            return self.render_shape(content.item)
        if content.kind == "text":
            return self.render_text(content.item)
        if content.kind == "general":
            return self.render_general_content(content.item)
        if content.kind in STATIC_IMAGE_CONTENT_KINDS:
            if content.kind == "collection":
                return self.render_collection_content(content.item)
            return self.render_image_content(content.kind, content.item)
        if content.kind == "stamp":
            return self.render_stamp_content(content.item)
        if content.kind == "card_member":
            return self.render_card_member_content(content.item)
        if content.kind == "honor":
            return self.render_honor_content(content.item)
        if content.kind == "bonds_honor":
            return self.render_bonds_honor_content(content.item)
        if content.kind in {"mini_chara", "screen_filter"}:
            return self.render_dynamic_content(content.kind, content.item)
        return self.native_unresolved(content.kind, content.item, "no native route is registered for this content kind")

    def render_general_template_shell(self) -> Image.Image:
        image = Image.new("RGBA", (CANVAS_W, CANVAS_H), GENERAL_TEMPLATE_BG_COLOR)
        draw = ImageDraw.Draw(image)
        # The screenshot crop already removes most of the outer game chrome.
        # This shell keeps the profile-view white panel and right-side widgets
        # aligned to the same capture origin as the template thumbnail.
        return image

    def apply_build_content_in_card_view(
        self,
        canvas: Image.Image,
        content: NativeContent,
        layer: tuple[Image.Image, tuple[float, float]] | tuple[Image.Image, tuple[float, float], bool],
    ) -> None:
        self.composite_transformed(
            canvas,
            layer,
            content.object_data,
            content.kind,
            self.should_supersample_content_transform(content),
        )

    def should_supersample_content_transform(self, content: NativeContent) -> bool:
        if content.kind == "shape":
            return True
        return self.should_supersample_text_transform(content)

    def should_supersample_text_transform(self, content: NativeContent) -> bool:
        if content.kind != "text" or not self.tmp_dynamic_sdf:
            return False
        data = self.generate_text_data(content.item)
        font_name = self.text_fonts.get(data.font_id, "")
        asset = self.tmp_font_library.active_asset(font_name)
        return asset is not None and asset.atlas_population_mode == 1 and not asset.has_static_glyphs

    def is_decorative_text_item(self, item: dict[str, Any]) -> bool:
        data = self.generate_text_data(item)
        raw_text = data.text
        if "<" not in raw_text:
            return False
        font_name = self.text_fonts.get(data.font_id, "FOT-RodinNTLGPro-DB") or "FOT-RodinNTLGPro-DB"
        mesh_state = self.update_text_mesh_state(data, font_name)
        base_style = TextStyle(
            color=mesh_state.font_color,
            alpha=1.0,
            size=mesh_state.font_size,
            scale_x=1.0,
            cspace=0.0,
            mspace=None,
            indent=0.0,
            line_indent=0.0,
            line_height=None,
            rotate=0.0,
            voffset=0.0,
            mark_color=None,
            bold=False,
            italic=False,
            underline=False,
            strike=False,
        )
        tokens = parse_tmp_text(raw_text, base_style)
        visible_chars = [ch for token in tokens if isinstance(token, TextRun) for ch in token.text if not ch.isspace()]
        return bool(visible_chars) and all(ch in TMP_DECORATIVE_TEXT_CHARS for ch in visible_chars)

    def decorative_outline_dilate(self, item: dict[str, Any], outline_dilate: float) -> float:
        if self.tmp_decorative_face_only and abs(outline_dilate) > 1.0e-6 and self.is_decorative_text_item(item):
            return 0.0
        return outline_dilate

    def draw_order_key(self, element: tuple[int, str, dict[str, Any]]) -> tuple[int, int, int]:
        layer, kind, item = element
        if self.draw_order == "shapes-first":
            return (1 if kind == "text" else 0, layer, 0)
        if self.draw_order == "white-text-last" and kind == "text" and self.is_white_text(item):
            return (1, layer, 0)
        return (0, layer, 0)

    def native_draw_order_key(self, content: NativeContent) -> tuple[int, int, int]:
        return self.draw_order_key((content.layer, content.kind, content.item))

    def is_white_text(self, item: dict[str, Any]) -> bool:
        return self.colors.get(int(item.get("colorId", 0)), "").lower() == "#ffffff"

    def image_resource_for(self, kind: str, item: dict[str, Any]) -> dict[str, Any]:
        item_id = content_data_id(kind, item)
        if kind == "general":
            return self.player_infos.get(item_id, {})
        if kind == "general_background":
            return self.general_bgs.get(item_id, {})
        if kind == "story_background":
            return self.story_bgs.get(item_id, {})
        if kind == "stand_member":
            return self.stand_members.get(item_id, {})
        if kind == "collection":
            return self.collections.get(item_id, {})
        if kind == "other":
            return self.others.get(item_id, {})
        if kind == "character_icon":
            return self.character_icons.get(item_id, {})
        if kind == "material":
            return self.materials.get(item_id, {})
        if kind == "user_interface_icon":
            return self.user_interface_icons.get(item_id, {})
        if kind == "stamp":
            return self.stamps.get(item_id, {})
        return {}

    def render_image_content(
        self,
        kind: str,
        item: dict[str, Any],
    ) -> tuple[Image.Image, tuple[float, float]] | NativeUnresolvedContent | None:
        resource = self.image_resource_for(kind, item)
        path = self.resource_path(resource)
        if not path:
            return self.native_unresolved(
                kind,
                item,
                "direct ImageContentView resource exists in native data but the PNG asset is not available locally",
                resource=resource,
                expected_view="ImageContentView",
                expected_size=PREFAB_NATIVE_SIZES["ImageContentView"],
                required_inputs=("MasterResource", "asset bundle sprite PNG"),
                generated_data=self.generate_image_data(kind, item, resource),
            )
        image = self.open_checked_image(path, "RGBA")
        return image, (image.width / 2, image.height / 2)

    def render_general_content(
        self,
        item: dict[str, Any],
    ) -> tuple[Image.Image, tuple[float, float]] | NativeUnresolvedContent:
        resource = self.image_resource_for("general", item)
        file_name = str(resource.get("fileName", "") or "")
        renderers = {
            "X": self.render_general_x,
            "EditUserName": self.render_general_user_name,
            "Comment": self.render_general_comment,
            "TotalPower": self.render_general_total_power,
            "LeaderCard": self.render_general_leader_card,
            "Deck": self.render_general_deck,
            "HonorDeck": self.render_general_honor_deck,
            "MultiLive": self.render_general_multi_live,
            "ChallengeLive": self.render_general_challenge_live,
            "CharacterRankAndChallengeStage": lambda: self.render_general_character_rank_and_challenge_stage(
                scroll=False
            ),
            "CharacterRankAndChallengeStageScroll": lambda: self.render_general_character_rank_and_challenge_stage(
                scroll=True
            ),
            "MusicClearInfo": self.render_general_music_clear_info,
            "MusicClearSelectTabInfo": self.render_general_music_clear_select_tab_info,
            "StoryFavorite": self.render_general_story_favorite,
        }
        renderer = renderers.get(file_name)
        if renderer is not None:
            rendered = renderer()
            if rendered is not None:
                return rendered, (rendered.width / 2, rendered.height / 2)
        view_name = "StoryFavoriteContentView" if file_name == "StoryFavorite" else f"{file_name}ContentView"
        return self.native_unresolved(
            "general",
            item,
            "GeneralContentView subclasses are prefab UI widgets and need their Setup child renderers",
            resource=resource,
            expected_view=view_name,
            expected_size=None,
            required_inputs=GENERAL_VIEW_REQUIRED_INPUTS.get(file_name, ("GetUserProfileResponse",)),
            generated_data=self.generate_general_data(item, resource),
        )

    def general_text(self, key: str) -> str:
        labels = GENERAL_LABELS.get(self.region)
        if labels is None and self.region == "ja":
            labels = GENERAL_LABELS["jp"]
        return (labels or GENERAL_LABELS["cn"]).get(key, GENERAL_LABELS["cn"].get(key, key))

    def general_font_candidates(self) -> list[Path]:
        """Ordered GeneralContentView font candidates shared by both render backends."""

        if self.region in {"jp", "ja"}:
            return [
                self.fonts / _DEFAULT_FONT_FILENAME,
                self.fonts / _DEFAULT_ALT_FONT_FILENAME,
                self.fonts / "FOT-RodinNTLGPro-DB.ttf",
                self.font_path_for("FOT-RodinNTLGPro-DB"),
                Path("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"),
            ]
        return [
            self.font_path_for("FOT-RodinNTLGPro-DB"),
            self.fonts / "FOT-RodinNTLGPro-DB.ttf",
            self.fonts / _DEFAULT_FONT_FILENAME,
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        ]

    def general_font_path(self) -> Path | None:
        """Resolve the first configured GeneralContentView font without opening it."""

        for path in self.general_font_candidates():
            try:
                if path.is_file():
                    return path
            except OSError:
                continue
        return None

    def general_font(self, size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for path in self.general_font_candidates():
            try:
                if path.exists():
                    return ImageFont.truetype(str(path), size)
            except OSError:
                continue
        return ImageFont.load_default()

    def render_shared_general_prefab(self, file_name: str) -> Image.Image | None:
        """Replay one migrated GeneralContentView from its renderer-neutral display list."""

        adapter = PillowGeneralPrefabAdapter(self.general_font, self.paste_unity_sprite, self.open_rgba)
        display_list = build_general_prefab_display_list(
            file_name,
            size=GENERAL_NATIVE_SIZES[file_name],
            profile_context=self.profile_context,
            labels=self._general_prefab_labels(),
            metrics=adapter,
            palette=GENERAL_PREFAB_PALETTE,
            asset_paths=self._general_prefab_asset_paths(file_name),
            music_difficulties=GENERAL_MUSIC_DIFFICULTIES,
            story_favorite_resources=self.story_favorite_resources,
        )
        return adapter.render(display_list) if display_list is not None else None

    def _general_prefab_asset_paths(self, file_name: str) -> dict[str, Path | None]:
        builders = {
            "ChallengeLive": self._challenge_live_prefab_assets,
            "CharacterRankAndChallengeStage": self._character_rank_prefab_assets,
            "CharacterRankAndChallengeStageScroll": self._character_rank_prefab_assets,
            "StoryFavorite": self._story_favorite_prefab_assets,
        }
        builder = builders.get(file_name)
        return builder() if builder is not None else {}

    def _challenge_live_prefab_assets(self) -> dict[str, Path | None]:
        data = _mapping_or_empty(self.profile_context.get("userChallengeLiveSoloResult") or {})
        character_id = _int_first(data.get("characterId"))
        return {"challenge_character_icon": self.chara_icon_path(character_id)}

    def _character_rank_prefab_assets(self) -> dict[str, Path | None]:
        return {
            f"character_rank_icon:{character_id}": self.chara_icon_path(character_id)
            for _nickname, character_id in CHARA_LIST
            if character_id is not None
        }

    def _story_favorite_prefab_assets(self) -> dict[str, Path | None]:
        stories = self.profile_context.get("userStoryFavorites")
        if not isinstance(stories, list):
            return {}
        return {
            story_favorite_asset_key(story): self.story_favorite_image_path(story)
            for story in stories
            if isinstance(story, dict)
        }

    def _general_prefab_labels(self) -> dict[str, str]:
        keys = (
            "comment_title",
            "total_power",
            "multi_live_title",
            "multi_live_count_suffix",
            "challenge_live_title",
            "challenge_live_solo",
            "character_rank_tab",
            "challenge_stage_tab",
            "music_clear",
            "music_full_combo",
            "music_all_perfect",
            "story_favorite_title",
            "not_set",
        )
        return {key: self.general_text(key) for key in keys}

    def rect_transform_box(
        self,
        parent_size: tuple[float, float],
        anchor_min: tuple[float, float],
        anchor_max: tuple[float, float],
        anchored_position: tuple[float, float],
        size_delta: tuple[float, float],
        pivot: tuple[float, float],
    ) -> tuple[float, float, float, float]:
        parent_w, parent_h = parent_size
        ax0, ay0 = anchor_min
        ax1, ay1 = anchor_max
        pos_x, pos_y = anchored_position
        size_x, size_y = size_delta
        pivot_x, pivot_y = pivot
        width = (ax1 - ax0) * parent_w + size_x
        height = (ay1 - ay0) * parent_h + size_y
        anchor_ref_x = ax0 * parent_w + (ax1 - ax0) * parent_w * pivot_x
        anchor_ref_y = ay0 * parent_h + (ay1 - ay0) * parent_h * pivot_y
        pivot_unity_x = anchor_ref_x + pos_x
        pivot_unity_y = anchor_ref_y + pos_y
        left = pivot_unity_x - width * pivot_x
        bottom = pivot_unity_y - height * pivot_y
        return (left, parent_h - (bottom + height), left + width, parent_h - bottom)

    def center_rect(
        self,
        parent_size: tuple[float, float],
        center: tuple[float, float],
        size: tuple[float, float],
    ) -> tuple[float, float, float, float]:
        parent_w, parent_h = parent_size
        cx, cy = center
        w, h = size
        center_x = parent_w / 2.0 + cx
        center_y = parent_h / 2.0 - cy
        return (center_x - w / 2.0, center_y - h / 2.0, center_x + w / 2.0, center_y + h / 2.0)

    def paste_in_rect(
        self,
        image: Image.Image,
        child: Image.Image,
        rect: tuple[float, float, float, float],
        *,
        resample: Image.Resampling = Image.Resampling.LANCZOS,
    ) -> None:
        left, top, right, bottom = rect
        w = max(1, round(right - left))
        h = max(1, round(bottom - top))
        resized = child.resize((w, h), resample)
        image.alpha_composite(resized, (round(left), round(top)))

    def customprofile_sprite_dirs(self) -> list[Path]:
        candidates: list[Path] = []
        for base in (self.assets, self.game_assets, self.static_images.parent):
            for parent in (base, *base.parents[:6]):
                candidates.append(parent / "static_images" / "customprofile")
        if self.unity_ui_sprite_dir is not None:
            candidates.append(self.unity_ui_sprite_dir)
        seen: set[Path] = set()
        result: list[Path] = []
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            result.append(path)
        return result

    def static_unity_ui_sprite_candidates(self, name: str) -> list[Path]:
        if name.startswith("masterRank_"):
            rank = name.rsplit("_", 1)[-1]
            return [self.static_images / "card" / f"train_rank_{rank}.png"]
        if name.startswith("icon_attribute_"):
            parts = name.split("_")
            if len(parts) >= 4:
                attr = parts[2]
                return [
                    self.static_images / "card" / f"attr_icon_{attr}.png",
                    self.static_images / "card" / f"attr_{attr}.png",
                    self.static_images / "honor" / "chara" / f"icon_attribute_{attr}.png",
                ]
        if name == "rarity_birthday":
            return [
                self.static_images / "card" / "rare_birthday.png",
                self.static_images / "honor" / "chara" / "rarity_birthday.png",
            ]
        if name == "rarity_star_afterTraining":
            return [
                self.static_images / "card" / "rare_star_after_training.png",
                self.static_images / "honor" / "chara" / "rarity_star_afterTraining.png",
            ]
        if name == "rarity_star_normal":
            return [
                self.static_images / "card" / "rare_star_normal.png",
                self.static_images / "honor" / "chara" / "rarity_star_normal.png",
            ]
        if name.startswith("cardFrame_S_"):
            suffix = name.rsplit("_", 1)[-1]
            rarity = "birthday" if suffix == "bd" else suffix
            return [
                self.static_images / "card" / f"frame_rarity_{rarity}.png",
                self.static_images / "honor" / "chara" / f"cardFrame_rarity_{rarity}.png",
            ]
        return []

    def unity_ui_sprite_candidates(self, name: str) -> list[Path]:
        candidates = self.static_unity_ui_sprite_candidates(name)
        candidates = [path / f"{name}.png" for path in self.customprofile_sprite_dirs()] + candidates
        if self.unity_ui_sprite_dir is not None:
            candidates.append(self.unity_ui_sprite_dir / f"{name}.png")
        seen: set[Path] = set()
        result: list[Path] = []
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            result.append(path)
        return result

    def unity_ui_sprite_path(self, name: str) -> Path | None:
        """Resolve a prefab sprite path without decoding image pixels."""

        if name in self._unity_ui_sprite_path_cache:
            return self._unity_ui_sprite_path_cache[name]
        for path in self.unity_ui_sprite_candidates(name):
            try:
                if path.is_file():
                    self._unity_ui_sprite_path_cache[name] = path
                    return path
            except OSError:
                continue
        self._unity_ui_sprite_path_cache[name] = None
        return None

    def unity_ui_sprite(self, name: str) -> Image.Image | None:
        # The name -> path resolution (and the cached None verdict) stays instance-level: the
        # candidate list depends on this renderer's region/sprite dirs. Only the decode goes
        # through the process pool, keyed by file signature, shared read-only across requests.
        cached = self._unity_ui_sprite_cache.get(name)
        if cached is not None or name in self._unity_ui_sprite_cache:
            return cached
        path = self.unity_ui_sprite_path(name)
        if path is not None:
            sprite = self._decode_shared_image(path, "rgba")
            self._unity_ui_sprite_cache[name] = sprite
            return sprite
        self._unity_ui_sprite_cache[name] = None
        return None

    def _decode_shared_image(self, path: Path, variant: str) -> Image.Image:
        """Decode ``path`` via the process-level sprite/atlas pool (variant-aware, see cache.py).

        A separate pool rather than the global image cache in src.sekai.base.utils: these are
        decoded VARIANTS (full-RGBA convert, atlas alpha channel) and the global 6-tuple key has
        no variant dimension, while its copy-on-get is pure waste for shared immutable images.
        """
        sig = optional_file_signature(path)
        if sig == (-1, -1):  # deleted between exists() and stat: keep the historical error path
            return self._decode_image_variant(path, variant)
        cache_key = (str(path), *sig, variant)
        image = SPRITE_ATLAS_CACHE.get(cache_key)
        if image is MISSING:
            image = self._decode_image_variant(path, variant)
            SPRITE_ATLAS_CACHE.set(cache_key, image)
        return image

    def _decode_image_variant(self, path: Path, variant: str) -> Image.Image:
        if variant == "atlas_alpha":
            return self.open_checked_image(path, "RGBA").getchannel("A")
        return self.open_checked_image(path, "RGBA")

    def tint_image(
        self,
        image: Image.Image,
        tint: tuple[float, float, float, float] | tuple[int, int, int, int],
    ) -> Image.Image:
        rgba = image.convert("RGBA")
        r, g, b, a = unity_tint_rgba(tint)
        alpha = ImageChops.multiply(rgba.getchannel("A"), Image.new("L", rgba.size, a))
        tinted = Image.new("RGBA", rgba.size, (r, g, b, 0))
        tinted.putalpha(alpha)
        return tinted

    def resize_sliced_sprite(
        self,
        sprite: Image.Image,
        target_size: tuple[int, int],
        border: tuple[int, int, int, int],
    ) -> Image.Image:
        target_w, target_h = target_size
        left, bottom, right, top = border
        src_w, src_h = sprite.size
        left = max(0, min(left, src_w))
        right = max(0, min(right, src_w - left))
        top = max(0, min(top, src_h))
        bottom = max(0, min(bottom, src_h - top))
        if target_w <= left + right and left + right > 0:
            scale = target_w / (left + right)
            left = math.floor(left * scale)
            right = target_w - left
        if target_h <= top + bottom and top + bottom > 0:
            scale = target_h / (top + bottom)
            top = math.floor(top * scale)
            bottom = target_h - top

        out = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        mid_src_w = max(0, src_w - left - right)
        mid_src_h = max(0, src_h - top - bottom)
        mid_dst_w = max(0, target_w - left - right)
        mid_dst_h = max(0, target_h - top - bottom)

        def paste_region(src_box: tuple[int, int, int, int], dst_box: tuple[int, int, int, int]) -> None:
            dst_w = dst_box[2] - dst_box[0]
            dst_h = dst_box[3] - dst_box[1]
            if dst_w <= 0 or dst_h <= 0:
                return
            src_left, src_top, src_right, src_bottom = src_box
            if src_right <= src_left:
                src_left = max(0, min(src_w - 1, src_left - 1))
                src_right = src_left + 1
            if src_bottom <= src_top:
                src_top = max(0, min(src_h - 1, src_top - 1))
                src_bottom = src_top + 1
            region = sprite.crop((src_left, src_top, src_right, src_bottom)).resize(
                (dst_w, dst_h), Image.Resampling.BICUBIC
            )
            out.alpha_composite(region, (dst_box[0], dst_box[1]))

        sx0, sx1, sx2, sx3 = 0, left, src_w - right, src_w
        sy0, sy1, sy2, sy3 = 0, top, src_h - bottom, src_h
        dx0, dx1, dx2, dx3 = 0, left, target_w - right, target_w
        dy0, dy1, dy2, dy3 = 0, top, target_h - bottom, target_h

        for src_y0, src_y1, dst_y0, dst_y1 in ((sy0, sy1, dy0, dy1), (sy1, sy2, dy1, dy2), (sy2, sy3, dy2, dy3)):
            for src_x0, src_x1, dst_x0, dst_x1 in ((sx0, sx1, dx0, dx1), (sx1, sx2, dx1, dx2), (sx2, sx3, dx2, dx3)):
                paste_region((src_x0, src_y0, src_x1, src_y1), (dst_x0, dst_y0, dst_x1, dst_y1))
        return out

    def paste_unity_sprite(
        self,
        image: Image.Image,
        name: str,
        rect: tuple[float, float, float, float],
        *,
        tint: tuple[float, float, float, float] | tuple[int, int, int, int] | None = None,
        sliced_border: tuple[int, int, int, int] | None = None,
        resample: Image.Resampling = Image.Resampling.LANCZOS,
    ) -> bool:
        sprite = self.unity_ui_sprite(name)
        if sprite is None:
            return False
        if tint is not None:
            sprite = self.tint_image(sprite, tint)
        left, top, right, bottom = rect
        size = (max(1, round(right - left)), max(1, round(bottom - top)))
        if sliced_border is not None:
            sprite = self.resize_sliced_sprite(sprite, size, sliced_border)
            image.alpha_composite(sprite, (round(left), round(top)))
        else:
            self.paste_in_rect(image, sprite, rect, resample=resample)
        return True

    def draw_rounded_rect(
        self,
        draw: ImageDraw.ImageDraw,
        rect: tuple[float, float, float, float],
        *,
        radius: float,
        fill: tuple[int, int, int, int],
        outline: tuple[int, int, int, int] | None = None,
        width: int = 1,
    ) -> None:
        box = tuple(round(v) for v in rect)
        draw.rounded_rectangle(box, radius=round(radius), fill=fill, outline=outline, width=width)

    def draw_template_chip(
        self, size: tuple[int, int], fill: tuple[int, int, int, int], outline: tuple[int, int, int, int], radius: int
    ) -> Image.Image:
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=fill, outline=outline, width=2)
        return image

    def draw_template_panel(self, size: tuple[int, int], radius: int = GENERAL_TEMPLATE_PANEL_RADIUS) -> Image.Image:
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (0, 0, size[0] - 1, size[1] - 1),
            radius=radius,
            fill=(255, 255, 251, 255),
            outline=GENERAL_TEMPLATE_FIELD_OUTLINE,
            width=3,
        )
        return image

    def draw_general_panel(self, size: tuple[int, int], title: str | None = None) -> Image.Image:
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        w, h = size
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=14, fill=(255, 255, 255, 232))
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=14, outline=(112, 201, 211, 255), width=3)
        if title:
            font = self.general_font(18)
            draw.rounded_rectangle((12, 10, 126, 36), radius=12, fill=(72, 196, 207, 255))
            draw.text((22, 11), title, font=font, fill=(255, 255, 255, 255))
        return image

    def draw_template_input(self, size: tuple[int, int]) -> Image.Image:
        return self.draw_template_chip(size, GENERAL_TEMPLATE_FIELD_FILL, GENERAL_TEMPLATE_FIELD_OUTLINE, 8)

    def draw_template_title(self, image: Image.Image, text: str, x: int, y: int, w: int | None = None) -> None:
        draw = ImageDraw.Draw(image)
        font = self.general_font(26)
        if w is None:
            bbox = draw.textbbox((0, 0), text, font=font)
            w = max(112, (bbox[2] - bbox[0]) + 48)
        draw.rounded_rectangle((x, y, x + w, y + 54), radius=27, fill=GENERAL_TEMPLATE_TITLE_FILL)
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(
            (x + (w - (bbox[2] - bbox[0])) // 2, y + (54 - (bbox[3] - bbox[1])) // 2 - 2),
            text,
            font=font,
            fill=(255, 255, 255, 255),
        )

    def draw_fit_text(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        text: str,
        *,
        max_size: int,
        min_size: int = 12,
        fill: tuple[int, int, int, int] = (58, 65, 82, 255),
        anchor: str = "lm",
        stroke_width: int = 0,
        stroke_fill: tuple[int, int, int, int] | None = None,
    ) -> None:
        left, top, right, bottom = box
        text = text or ""
        if anchor.startswith("r"):
            x = right
        elif anchor.startswith("m"):
            x = (left + right) // 2
        else:
            x = left
        for size in range(max_size, min_size - 1, -1):
            font = self.general_font(size)
            bbox = draw.textbbox((0, 0), text, font=font)
            if bbox[2] - bbox[0] <= right - left and bbox[3] - bbox[1] <= bottom - top:
                y = (top + bottom) // 2 if anchor.endswith("m") else top
                draw.text(
                    (x, y),
                    text,
                    font=font,
                    fill=fill,
                    anchor=anchor,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill,
                )
                return
        font = self.general_font(min_size)
        draw.text(
            (x, (top + bottom) // 2),
            text,
            font=font,
            fill=fill,
            anchor=anchor,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )

    def draw_fit_text_rect(
        self,
        draw: ImageDraw.ImageDraw,
        rect: tuple[float, float, float, float],
        text: str,
        *,
        max_size: int,
        min_size: int = 12,
        fill: tuple[int, int, int, int] = GENERAL_TEMPLATE_TEXT,
        anchor: str = "lm",
        stroke_width: int = 0,
        stroke_fill: tuple[int, int, int, int] | None = None,
    ) -> None:
        left, top, right, bottom = rect
        self.draw_fit_text(
            draw,
            (round(left), round(top), round(right), round(bottom)),
            text,
            max_size=max_size,
            min_size=min_size,
            fill=fill,
            anchor=anchor,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )

    def draw_center_text_rect(
        self,
        draw: ImageDraw.ImageDraw,
        rect: tuple[float, float, float, float],
        text: str,
        *,
        size: int,
        fill: tuple[int, int, int, int] = GENERAL_TEMPLATE_TEXT,
    ) -> None:
        font = self.general_font(size)
        left, top, right, bottom = rect
        draw.text(((left + right) / 2.0, (top + bottom) / 2.0), text, font=font, fill=fill, anchor="mm")

    def wrap_general_text(self, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        text = text or ""
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

        def text_width(value: str) -> int:
            bbox = draw.textbbox((0, 0), value, font=font)
            return bbox[2] - bbox[0]

        lines: list[str] = []
        for raw_line in text.splitlines() or [""]:
            line = ""
            for token in _general_text_tokens(raw_line):
                line = _append_general_token(token, line, max_width, text_width, lines)
            lines.append(line)
        return lines

    def draw_edit_mark(self, image: Image.Image, rect: tuple[float, float, float, float]) -> None:
        self.paste_unity_sprite(image, "icon_write_wh", rect, tint=UNITY_UI_DARK_TINT)

    def draw_total_power_icon(self, image: Image.Image, rect: tuple[float, float, float, float]) -> None:
        self.paste_unity_sprite(image, "icon_deckPower_wh", rect, tint=UNITY_UI_DARK_TINT)

    def draw_info_button(self, image: Image.Image, rect: tuple[float, float, float, float]) -> None:
        self.paste_unity_sprite(image, "btn_circle_h56_wh", rect)
        icon_rect = self.rect_transform_box(
            (rect[2] - rect[0], rect[3] - rect[1]),
            (0.5, 0.5),
            (0.5, 0.5),
            (0.0, 1.0),
            (8.0, 30.0),
            (0.5, 0.5),
        )
        icon_rect = (
            icon_rect[0] + rect[0],
            icon_rect[1] + rect[1],
            icon_rect[2] + rect[0],
            icon_rect[3] + rect[1],
        )
        self.paste_unity_sprite(image, "icon_infomation_wh", icon_rect, tint=UNITY_UI_DARK_TINT)

    def render_general_x(self) -> Image.Image:
        image = self.render_shared_general_prefab("X")
        if image is None:  # pragma: no cover - X has no missing-data no-op contract
            raise RuntimeError("shared X GeneralContentView unexpectedly produced no display list")
        return image

    def render_general_user_name(self) -> Image.Image:
        return self.render_shared_general_prefab("EditUserName")

    def render_general_comment(self) -> Image.Image:
        return self.render_shared_general_prefab("Comment")

    def render_general_total_power(self) -> Image.Image:
        return self.render_shared_general_prefab("TotalPower")

    def render_general_leader_card(self) -> Image.Image | None:
        deck = self.profile_context.get("userDeck") or {}
        card_id = int(deck.get("leader", 0) or 0) if isinstance(deck, dict) else 0
        if card_id <= 0:
            return None
        return self.compose_profile_leader_card(card_id)

    def render_general_deck(self) -> Image.Image | None:
        deck = self.profile_context.get("userDeck") or {}
        if not isinstance(deck, dict):
            return None
        image = Image.new("RGBA", GENERAL_NATIVE_SIZES["Deck"], (0, 0, 0, 0))
        card_ids = [int(deck.get(f"member{i}", 0) or 0) for i in range(1, 6)]
        card_w, card_h = GENERAL_DECK_CARD_RENDER_SIZE
        gap = max(0.0, (image.width - card_w * 5) / 4.0)
        total_w = card_w * 5 + gap * 4
        start_x = max(0.0, (image.width - total_w) / 2.0)
        y = image.height - card_h
        for idx, card_id in enumerate(card_ids):
            card = self.compose_profile_deck_card(card_id, leader=idx == 0)
            if card is None:
                card = self.empty_profile_deck_card((card_w, card_h))
            image.alpha_composite(card, (round(start_x + idx * (card_w + gap)), round(y)))
        return image

    def render_general_honor_deck(self) -> Image.Image | None:
        plan = build_honor_deck_plan(self.profile_context.get("userProfileHonors", []) or [])
        if plan is None:
            return None
        image = Image.new("RGBA", plan.natural_size, (0, 0, 0, 0))
        if plan.panel is not None:
            self.paste_unity_sprite(
                image,
                plan.panel.sprite_name,
                plan.panel.target_rect,
                tint=plan.panel.tint,
                sliced_border=plan.panel.sliced_border,
            )
        for slot in plan.slots:
            row = dict(slot.profile_row)
            badge = self.compose_profile_honor_image(row, full_size=slot.full_size)
            if badge is None:
                badge = self.compose_honor_image(slot.honor_id, slot.honor_level, full_size=slot.full_size)
            if badge is None:
                continue
            self.paste_in_rect(image, badge, slot.target_rect)
        return image

    def render_general_music_clear_info(self) -> Image.Image:
        return self.render_shared_general_prefab("MusicClearInfo")

    def render_general_music_clear_select_tab_info(self) -> Image.Image:
        return self.render_shared_general_prefab("MusicClearSelectTabInfo")

    def render_general_multi_live(self) -> Image.Image | None:
        return self.render_shared_general_prefab("MultiLive")

    def render_general_challenge_live(self) -> Image.Image | None:
        return self.render_shared_general_prefab("ChallengeLive")

    def render_general_character_rank_and_challenge_stage(self, scroll: bool = True) -> Image.Image:
        size_key = "CharacterRankAndChallengeStageScroll" if scroll else "CharacterRankAndChallengeStage"
        image = self.render_shared_general_prefab(size_key)
        assert image is not None
        return image

    def render_general_story_favorite(self) -> Image.Image | None:
        return self.render_shared_general_prefab("StoryFavorite")

    def draw_character_rank_tabs(self, image: Image.Image, *, scroll: bool) -> None:
        draw = ImageDraw.Draw(image)
        tab_w = 828.0 if scroll else 760.0
        left = (image.width - tab_w) / 2.0
        top = 23.5 if scroll else 24.0
        bottom = top + 57.0
        mid = left + tab_w / 2.0
        self.paste_unity_sprite(
            image,
            "bg_base_r16_wh",
            (left, top, left + tab_w, bottom),
            tint=UNITY_UI_INPUT_TINT,
            sliced_border=(21, 21, 21, 21),
        )
        self.paste_unity_sprite(
            image,
            "bg_base_r16_wh",
            (left, top, mid, bottom),
            tint=(244, 246, 252, 230),
            sliced_border=(21, 21, 21, 21),
        )
        self.draw_center_text_rect(
            draw, (left, top, mid, bottom), self.general_text("character_rank_tab"), size=27, fill=GENERAL_TEMPLATE_TEXT
        )
        self.draw_center_text_rect(
            draw,
            (mid, top, left + tab_w, bottom),
            self.general_text("challenge_stage_tab"),
            size=27,
            fill=(255, 255, 255, 230),
        )

    def character_rank_cell_top_left(self, center_x: float, center_y: float) -> tuple[float, float]:
        cell_w, cell_h = CHARACTER_RANK_CELL_SIZE
        return center_x - cell_w / 2.0, center_y - cell_h / 2.0

    def draw_profile_rank_and_stage_cell(
        self,
        image: Image.Image,
        top_left: tuple[float, float],
        character_id: int,
        rank: int,
    ) -> None:
        draw = ImageDraw.Draw(image)
        x, y = top_left
        cell_w, cell_h = CHARACTER_RANK_CELL_SIZE
        root_center_x = x + cell_w / 2.0
        root_center_y = y + cell_h / 2.0
        tint = (0.266667, 0.866667, 1.0, 1.0)
        base_center_x = root_center_x + 7.5
        base_center_y = root_center_y + 10.0
        self.paste_unity_sprite(
            image,
            "bg_base_round_h64_wh",
            (base_center_x - 90.0, base_center_y - 32.5, base_center_x + 90.0, base_center_y + 32.5),
            tint=tint,
            sliced_border=(37, 0, 37, 0),
        )
        circle_rect = (
            root_center_x - 97.5,
            root_center_y - 42.0,
            root_center_x - 13.5,
            root_center_y + 42.0,
        )
        self.paste_unity_sprite(image, "bg_base_circle_h96_wh", circle_rect, tint=tint)
        if icon := self.open_rgba(self.chara_icon_path(character_id)):
            icon_rect = (
                root_center_x - 93.5,
                root_center_y - 38.0,
                root_center_x - 17.5,
                root_center_y + 38.0,
            )
            self.paste_in_rect(image, icon, icon_rect)
        rank_rect = (
            root_center_x - 39.0,
            root_center_y - 13.5,
            root_center_x + 93.0,
            root_center_y + 35.5,
        )
        self.draw_center_text_rect(draw, rank_rect, str(rank), size=31, fill=GENERAL_TEMPLATE_TEXT)

    def draw_story_favorite_header(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        title_rect = (47, 10, 547, 66)
        self.draw_fit_text_rect(
            draw,
            title_rect,
            self.general_text("story_favorite_title"),
            max_size=30,
            min_size=18,
            fill=GENERAL_TEMPLATE_TEXT,
        )
        self.paste_unity_sprite(
            image,
            "bg_base_wh",
            (40, 66, image.width - 40, 70),
            tint=UNITY_UI_TOTAL_LINE_TINT,
        )

    def draw_story_favorite_cell(
        self,
        image: Image.Image,
        story: dict[str, Any],
        rect: tuple[float, float, float, float],
    ) -> None:
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = rect
        width = max(1, round(right - left))
        height = max(1, round(bottom - top))
        title = self.story_favorite_title(story)
        banner = self.open_rgba(self.story_favorite_image_path(story))
        if banner is not None:
            tile = self.resize_cover_aligned(banner, (width, height), align_x=0.5, align_y=0.5)
            mask = Image.new("L", tile.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=10, fill=255)
            tile.putalpha(ImageChops.multiply(tile.getchannel("A"), mask))
            image.alpha_composite(tile, (round(left), round(top)))
            draw.rounded_rectangle(
                (round(left), round(top), round(right), round(bottom)),
                radius=10,
                outline=(235, 242, 255, 210),
                width=2,
            )
            return
        self.paste_unity_sprite(
            image,
            "bg_base_r16_wh",
            rect,
            tint=UNITY_UI_INPUT_TINT,
            sliced_border=(21, 21, 21, 21),
        )
        self.draw_fit_text_rect(
            draw,
            (left + 18, top + 12, right - 18, bottom - 12),
            title,
            max_size=24,
            min_size=13,
            fill=GENERAL_TEMPLATE_TEXT,
        )

    def draw_general_vertical_scrollbar(self, image: Image.Image, rect: tuple[float, float, float, float]) -> None:
        left, top, right, bottom = rect
        self.paste_unity_sprite(
            image,
            "bg_base_round_vertical_h6_wh",
            rect,
            tint=(0.333333, 0.333333, 0.466667, 0.2),
            sliced_border=(0, 5, 0, 5),
        )
        handle_h = min(220.0, max(80.0, (bottom - top) * 0.28))
        handle_rect = (left - 1, top + 18, right + 1, top + 18 + handle_h)
        self.paste_unity_sprite(
            image,
            "bg_base_round_vertical_h8_wh",
            handle_rect,
            tint=(0.333333, 0.333333, 0.466667, 1.0),
            sliced_border=(0, 6, 0, 6),
        )

    def ordered_story_favorites(self, stories: list[Any]) -> list[dict[str, Any]]:
        return order_story_favorites(stories)

    def story_favorite_key(self, story: dict[str, Any]) -> str:
        return build_story_favorite_key(story)

    def story_favorite_resource(self, story: dict[str, Any]) -> dict[str, Any]:
        key = self.story_favorite_key(story)
        return self.story_favorite_resources.get(key) or {}

    def story_favorite_title(self, story: dict[str, Any]) -> str:
        return resolve_story_favorite_title(story, self.story_favorite_resources)

    def story_favorite_image_path(self, story: dict[str, Any]) -> Path | None:
        resource = self.story_favorite_resource(story)
        raw_path = str(resource.get("imagePath", "") or "").strip()
        if raw_path:
            return self.resolve_request_asset_path(raw_path)
        return None

    def draw_general_panel(self, image: Image.Image, rect: tuple[float, float, float, float]) -> None:
        if not self.paste_unity_sprite(
            image,
            "bg_base_r16_wh",
            rect,
            tint=UNITY_UI_HONOR_TINT,
            sliced_border=(21, 21, 21, 21),
        ):
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                tuple(round(v) for v in rect),
                radius=GENERAL_TEMPLATE_PANEL_RADIUS,
                fill=(238, 240, 248, 210),
            )

    def music_clear_count_map(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for row in self.profile_context.get("userMusicDifficultyClearCount", []) or []:
            if not isinstance(row, dict):
                continue
            difficulty = str(row.get("musicDifficultyType", "") or "").lower()
            if difficulty:
                result[difficulty] = {
                    "liveClear": int(row.get("liveClear", 0) or 0),
                    "fullCombo": int(row.get("fullCombo", 0) or 0),
                    "allPerfect": int(row.get("allPerfect", 0) or 0),
                }
        return result

    def draw_music_clear_row(
        self,
        image: Image.Image,
        rect: tuple[float, float, float, float],
        label: str,
        key: str,
        counts: dict[str, dict[str, int]],
        *,
        header_h: int = 54,
        value_inset_x: float = 15.0,
        value_top_gap: float = 9.0,
    ) -> None:
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = rect
        header_rect = (left, top, right, top + header_h)
        if not self.paste_unity_sprite(
            image,
            "bg_base_r16_wh",
            header_rect,
            tint=UNITY_UI_TOTAL_LINE_TINT,
            sliced_border=(21, 21, 21, 21),
        ):
            draw.rounded_rectangle(
                tuple(round(v) for v in header_rect),
                radius=12,
                fill=(167, 167, 188, 220),
            )
        self.draw_center_text_rect(
            draw, header_rect, label, size=min(31, max(20, header_h - 12)), fill=(255, 255, 255, 255)
        )
        self.draw_music_clear_value_strip(
            image,
            (left + value_inset_x, top + header_h + value_top_gap, right - value_inset_x, bottom),
            key,
            counts,
        )

    def draw_music_clear_value_strip(
        self,
        image: Image.Image,
        rect: tuple[float, float, float, float],
        key: str,
        counts: dict[str, dict[str, int]],
        *,
        cell_gap: float = 8.0,
        tag_h: float = 34.0,
    ) -> None:
        cell_count = len(GENERAL_MUSIC_DIFFICULTIES)
        left, top, right, bottom = rect
        cell_w = (right - left - cell_gap * (cell_count - 1)) / cell_count
        for idx, (difficulty, text, color) in enumerate(GENERAL_MUSIC_DIFFICULTIES):
            x = left + idx * (cell_w + cell_gap)
            self.draw_difficulty_count_cell(
                image, (x, top, x + cell_w, bottom), text, color, counts.get(difficulty, {}).get(key, 0), tag_h=tag_h
            )

    def draw_difficulty_count_cell(
        self,
        image: Image.Image,
        rect: tuple[float, float, float, float],
        label: str,
        color: tuple[int, int, int, int],
        value: int,
        *,
        tag_h: float = 34.0,
    ) -> None:
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = rect
        draw.rounded_rectangle((left, top, right, top + tag_h), radius=6, fill=color)
        self.draw_center_text_rect(draw, (left, top, right, top + tag_h), label, size=20, fill=(255, 255, 255, 255))
        self.draw_center_text_rect(draw, (left, top + tag_h + 2, right, bottom), str(value), size=28)

    def character_rank_map(self) -> dict[int, int]:
        result: dict[int, int] = {}
        for row in self.profile_context.get("userCharacters", []) or []:
            if isinstance(row, dict):
                character_id = int(row.get("characterId", 0) or 0)
                if character_id:
                    result[character_id] = int(row.get("characterRank", 0) or 0)
        return result

    def challenge_live_stage_map(self) -> dict[int, int]:
        result: dict[int, int] = {}
        for row in self.profile_context.get("userChallengeLiveSoloStages", []) or []:
            if not isinstance(row, dict):
                continue
            character_id = int(row.get("characterId", 0) or 0)
            if character_id:
                result[character_id] = max(result.get(character_id, 0), int(row.get("rank", 0) or 0))
        return result

    def challenge_live_rank_for(self, character_id: int) -> int:
        return self.challenge_live_stage_map().get(character_id, 0)

    def chara_icon_path(self, character_id: int) -> Path | None:
        raw_path = self.chara_rank_icon_path_map.get(str(character_id))
        if raw_path:
            path = self.resolve_request_asset_path(raw_path)
            if path is not None:
                return path
        return None

    def chara_rank_icon_path(self, character_id: int) -> Path | None:
        return self.chara_icon_path(character_id)

    def honor_request_image(self, payload: dict[str, Any] | None) -> Image.Image | None:
        if not payload:
            return None
        request = HonorRequest.model_validate(payload)
        images = {
            "honor_img": self.open_request_rgba(request.honor_img_path),
            "rank_img": self.open_request_rgba(request.rank_img_path),
            "frame_img": self.open_request_rgba(request.frame_img_path),
            "frame_degree_level_img": self.open_request_rgba(request.frame_degree_level_img_path),
            "scroll_img": self.open_request_rgba(request.scroll_img_path),
            "lv_img": self.open_request_rgba(request.lv_img_path),
            "lv6_img": self.open_request_rgba(request.lv6_img_path),
            "empty_honor": self.open_request_rgba(request.empty_honor_path),
            "bonds_bg": self.open_request_rgba(request.bonds_bg_path),
            "bonds_bg2": self.open_request_rgba(request.bonds_bg_path2),
            "chara_icon_1": self.open_request_rgba(request.chara_icon_path),
            "chara_icon_2": self.open_request_rgba(request.chara_icon_path2),
            "mask_img": self.open_request_rgba(request.mask_img_path),
            "word_img": self.open_request_rgba(request.word_img_path),
        }
        return compose_full_honor_image_from_loaded_assets(request, images)

    def honor_slot_key(self, honor_id: int, level: int, full_size: bool) -> str:
        return f"{honor_id}:{level}:{'main' if full_size else 'sub'}"

    def bonds_honor_slot_key(
        self,
        honor_id: int,
        level: int,
        full_size: bool,
        word_id: int,
        inverse: bool,
        use_unit_virtual_singer: bool = False,
    ) -> str:
        mode = "main" if full_size else "sub"
        direction = "reverse" if inverse else "normal"
        suffix = ":unit_vs" if use_unit_virtual_singer else ""
        return f"{honor_id}:{level}:{mode}:{word_id}:{direction}{suffix}"

    def compose_profile_honor_image(self, row: dict[str, Any], full_size: bool) -> Image.Image | None:
        seq = int(row.get("seq", 0) or 0)
        honor_id = int(row.get("honorId", 0) or 0)
        level = int(row.get("honorLevel", 0) or 0)
        keys = [
            f"profile:{seq}",
            f"profile:{honor_id}:{seq}",
            self.honor_slot_key(honor_id, level, full_size),
            str(honor_id),
        ]
        for key in keys:
            if image := self.honor_request_image(self.profile_honor_requests.get(key)):
                return image
        return None

    def card_default_after_training(self, card_id: int) -> bool:
        user_card = self.user_card_for(card_id) or {}
        default_image = str(user_card.get("defaultImage", "") or "").strip().lower()
        if default_image in {"special_training", "after_training"}:
            return True
        if default_image in {"original", "normal"}:
            return False
        return str(user_card.get("specialTrainingStatus", "") or "").strip().lower() == "done"

    def card_image_path_for_state(self, card_id: int, after_training: bool, kind: str = "full") -> Path | None:
        if path := self.card_asset_path_for_state(card_id, after_training, kind):
            return path
        if self.masterdata is None:
            return None
        bundle = self.card_asset_bundle_name(card_id)
        if not bundle:
            return None
        full_file = "card_after_training.png" if after_training else "card_normal.png"
        cutout_file = "after_training.png" if after_training else "normal.png"
        cutout_trim_file = "card_after_training_trim.png" if after_training else "card_normal_trim.png"
        rels: list[Path] = []
        if kind == "deck":
            rels.extend(
                [
                    Path("character") / "member_cutout" / bundle / cutout_file,
                    Path("character") / "member_cutout" / bundle / cutout_trim_file,
                    Path("character") / "member_cutout" / f"{bundle}_rip" / cutout_file,
                    Path("character") / "member_cutout" / f"{bundle}_rip" / cutout_trim_file,
                    Path("character") / "member_cutout" / bundle / _DECK_IMAGE_FILENAME,
                    Path("character") / "member_cutout" / f"{bundle}_rip" / _DECK_IMAGE_FILENAME,
                ]
            )
        elif kind == "clip":
            rels.extend(
                [
                    Path("character") / "member_cutout" / bundle / cutout_file,
                    Path("character") / "member_cutout" / bundle / cutout_trim_file,
                    Path("character") / "member_cutout" / f"{bundle}_rip" / cutout_file,
                    Path("character") / "member_cutout" / f"{bundle}_rip" / cutout_trim_file,
                    Path("character") / "member_cutout_trm" / bundle / cutout_file,
                    Path("character") / "member_cutout_trm" / bundle / cutout_trim_file,
                    Path("character") / "member_cutout_trm" / f"{bundle}_rip" / cutout_file,
                    Path("character") / "member_cutout_trm" / f"{bundle}_rip" / cutout_trim_file,
                ]
            )
        elif kind == "small":
            rels.extend(
                [
                    Path("character") / "member_small" / bundle / full_file,
                    Path("character") / "member_small" / f"{bundle}_rip" / full_file,
                ]
            )
        else:
            rels.extend(
                [
                    Path("character") / "member" / bundle / full_file,
                    Path("character") / "member" / f"{bundle}_rip" / full_file,
                    Path("thumbnail") / "chara" / f"{bundle}_{'after_training' if after_training else 'normal'}.png",
                ]
            )
        return self.first_region_asset(rels)

    def resize_cover_aligned(
        self,
        source: Image.Image,
        target_size: tuple[int, int] | tuple[float, float],
        *,
        align_x: float = 0.5,
        align_y: float = 0.5,
    ) -> Image.Image:
        target_w = max(1, round(target_size[0]))
        target_h = max(1, round(target_size[1]))
        scale = max(target_w / source.width, target_h / source.height)
        resized = source.resize(
            (max(1, round(source.width * scale)), max(1, round(source.height * scale))), Image.Resampling.LANCZOS
        )
        left = round((resized.width - target_w) * align_x)
        top = round((resized.height - target_h) * align_y)
        return resized.crop((left, top, left + target_w, top + target_h))

    def rarity_star_count(self, card: dict[str, Any]) -> int:
        rarity = str(card.get("cardRarityType", "") or "")
        if rarity == "rarity_birthday":
            return 1
        digits = "".join(ch for ch in rarity if ch.isdigit())
        return max(1, min(4, int(digits or "1")))

    def card_frame_sprite_name(self, card: dict[str, Any], size: str) -> str:
        rarity = str(card.get("cardRarityType", "") or "")
        suffix = "bd" if rarity == "rarity_birthday" else ("".join(ch for ch in rarity if ch.isdigit()) or "1")
        return f"cardFrame_{size}_{suffix}"

    def card_attr_sprite_name(self, card: dict[str, Any], icon_size: int) -> str:
        return f"icon_attribute_{str(card.get('attr', '') or '')}_{icon_size}"

    def card_star_sprite_name(self, card_id: int) -> str:
        card = self.card_master_for(card_id) or {}
        if str(card.get("cardRarityType", "") or "") == "rarity_birthday":
            return "rarity_birthday"
        return "rarity_star_afterTraining" if self.card_default_after_training(card_id) else "rarity_star_normal"

    def card_master_rank_sprite_name(self, card_id: int, size: str) -> str:
        return f"masterRank_{size}_{self.card_master_rank(card_id)}"

    def card_master_rank(self, card_id: int) -> int:
        return max(0, min(5, int((self.user_card_for(card_id) or {}).get("masterRank", 0) or 0)))

    def card_overlay_paths(self, card_id: int) -> tuple[Path, Path, Path, Path]:
        card = self.card_master_for(card_id) or {}
        attr = str(card.get("attr", "") or "")
        frame_path = self.static_images / "card" / self.card_frame_file(card)
        attr_path = self.static_images / "card" / f"attr_icon_{attr}.png"
        if not attr_path.exists():
            attr_path = self.static_images / "card" / f"attr_{attr}.png"
        star_path = (
            self.static_images
            / "card"
            / ("rare_star_after_training.png" if self.card_default_after_training(card_id) else "rare_star_normal.png")
        )
        master_rank = self.card_master_rank(card_id)
        rank_path = self.static_images / "card" / f"train_rank_{master_rank}.png"
        return frame_path, attr_path, star_path, rank_path

    def card_sprite_ref(
        self,
        name: str,
        fallback_path: Path | None = None,
    ) -> CardSpriteRef:
        return CardSpriteRef(
            name=name,
            path=self.unity_ui_sprite_path(name),
            fallback_path=fallback_path,
        )

    def card_prefab_resources(
        self,
        card_id: int,
        art_path: Path | None,
        *,
        frame_size: str,
        attr_size: int,
        rank_size: str,
        include_leader_label: bool = False,
    ) -> CardPrefabResources:
        card = self.card_master_for(card_id) or {}
        frame_path, attr_path, star_path, rank_path = self.card_overlay_paths(card_id)
        master_rank = self.card_master_rank(card_id)
        return CardPrefabResources(
            art_path=art_path,
            frame=self.card_sprite_ref(self.card_frame_sprite_name(card, frame_size), frame_path),
            attribute=self.card_sprite_ref(self.card_attr_sprite_name(card, attr_size), attr_path),
            rarity=self.card_sprite_ref(self.card_star_sprite_name(card_id), star_path),
            master_rank=(
                self.card_sprite_ref(self.card_master_rank_sprite_name(card_id, rank_size), rank_path)
                if master_rank > 0
                else None
            ),
            leader_label=(self.card_sprite_ref("label_mark_leader_L_pk") if include_leader_label else None),
        )

    def card_pillow_adapter(self) -> PillowCardAdapter:
        return PillowCardAdapter(
            self.general_font,
            self.paste_unity_sprite,
            self.unity_ui_sprite,
            self.open_rgba,
        )

    def render_card_display_list(self, display_list: CardDisplayList) -> Image.Image:
        return self.card_pillow_adapter().render(display_list)

    def draw_card_rarity(
        self,
        image: Image.Image,
        card_id: int,
        star_path: Path | None,
        positions: list[tuple[float, float, float, float]],
    ) -> None:
        count = self.rarity_star_count(self.card_master_for(card_id) or {})
        resource = self.card_sprite_ref(self.card_star_sprite_name(card_id), star_path)
        self.card_pillow_adapter().apply_ops(
            image,
            build_card_rarity_ops(resource, positions, count),
        )

    def draw_deck_leader_label(self, image: Image.Image) -> None:
        self.card_pillow_adapter().apply_ops(
            image,
            (
                build_deck_leader_label_op(
                    image.size,
                    self.card_sprite_ref("label_mark_leader_L_pk"),
                ),
            ),
        )

    def card_level(self, card_id: int) -> int:
        return max(1, int((self.user_card_for(card_id) or {}).get("level", 1) or 1))

    def draw_deck_card_level(self, image: Image.Image, card_id: int) -> None:
        self.card_pillow_adapter().apply_ops(
            image,
            build_deck_card_level_ops(
                image.size,
                self.card_level(card_id),
                font=CardFontRef(path=self.general_font_path()),
            ),
        )

    def apply_card_frame_mask(self, image: Image.Image, sprite_name: str = "tex_mask_card_s") -> Image.Image:
        return self.card_pillow_adapter().apply_ops(
            image,
            (CardAlphaMaskOp(self.card_sprite_ref(sprite_name)),),
        )

    def draw_deck_card_view_overlays(
        self,
        image: Image.Image,
        card_id: int,
        *,
        leader: bool = False,
        show_detail: bool = True,
        attr_x: float = 3.70001220703125,
    ) -> None:
        if not show_detail:
            return
        resources = self.card_prefab_resources(
            card_id,
            None,
            frame_size="M",
            attr_size=64,
            rank_size="S",
            include_leader_label=leader,
        )
        self.card_pillow_adapter().apply_ops(
            image,
            build_deck_card_overlay_ops(
                image.size,
                resources,
                self.rarity_star_count(self.card_master_for(card_id) or {}),
                attr_x=attr_x,
                leader=leader,
            ),
        )

    def compose_deck_card_view(
        self,
        card_id: int,
        path: Path,
        *,
        native_size: tuple[int, int],
        art_size: tuple[float, float],
        crop_align_y: float,
        leader: bool = False,
        show_detail: bool = True,
        attr_x: float = 3.70001220703125,
        mask_sprite_name: str | None = "tex_mask_card_s",
        render_size: tuple[int, int] | None = None,
    ) -> Image.Image:
        return self.render_card_display_list(
            self.build_deck_card_display_list(
                card_id,
                path,
                native_size=native_size,
                art_size=art_size,
                crop_align_y=crop_align_y,
                leader=leader,
                show_detail=show_detail,
                attr_x=attr_x,
                mask_sprite_name=mask_sprite_name,
                render_size=render_size,
            )
        )

    def build_deck_card_display_list(
        self,
        card_id: int,
        path: Path,
        *,
        native_size: tuple[int, int],
        art_size: tuple[float, float],
        crop_align_y: float,
        leader: bool = False,
        show_detail: bool = True,
        attr_x: float = 3.70001220703125,
        mask_sprite_name: str | None = "tex_mask_card_s",
        render_size: tuple[int, int] | None = None,
    ) -> CardDisplayList:
        resources = self.card_prefab_resources(
            card_id,
            path,
            frame_size="M",
            attr_size=64,
            rank_size="S",
            include_leader_label=leader,
        )
        return build_deck_card_prefab_display_list(
            native_size=native_size,
            art_size=art_size,
            crop_align_y=crop_align_y,
            resources=resources,
            rarity_count=self.rarity_star_count(self.card_master_for(card_id) or {}),
            level=self.card_level(card_id),
            leader=leader,
            show_detail=show_detail,
            attr_x=attr_x,
            mask=(self.card_sprite_ref(mask_sprite_name) if mask_sprite_name is not None else None),
            render_size=render_size,
            font=CardFontRef(path=self.general_font_path()),
        )

    def draw_small_still_card_overlays(self, image: Image.Image, card_id: int, *, show_detail: bool = True) -> None:
        if not show_detail:
            return
        resources = self.card_prefab_resources(
            card_id,
            None,
            frame_size="L",
            attr_size=88,
            rank_size="L",
        )
        self.card_pillow_adapter().apply_ops(
            image,
            build_full_card_overlay_ops(
                image.size,
                resources,
                self.rarity_star_count(self.card_master_for(card_id) or {}),
            ),
        )

    def compose_profile_small_still_card(
        self,
        card_id: int,
        path: Path,
        *,
        target_size: tuple[int, int] = FULL_CARD_MEMBER_NATIVE_SIZE,
        show_detail: bool = True,
    ) -> Image.Image:
        return self.render_card_display_list(
            self.build_small_still_card_display_list(
                card_id,
                path,
                target_size=target_size,
                show_detail=show_detail,
            )
        )

    def build_small_still_card_display_list(
        self,
        card_id: int,
        path: Path,
        *,
        target_size: tuple[int, int] = FULL_CARD_MEMBER_NATIVE_SIZE,
        show_detail: bool = True,
    ) -> CardDisplayList:
        resources = self.card_prefab_resources(
            card_id,
            path,
            frame_size="L",
            attr_size=88,
            rank_size="L",
        )
        return build_full_card_prefab_display_list(
            size=target_size,
            resources=resources,
            rarity_count=self.rarity_star_count(self.card_master_for(card_id) or {}),
            show_detail=show_detail,
        )

    def compose_profile_card_still(self, card_id: int, target_size: tuple[int, int]) -> Image.Image | None:
        display_list = self.build_profile_card_still_display_list(card_id, target_size)
        return self.render_card_display_list(display_list) if display_list is not None else None

    def build_profile_card_still_display_list(
        self,
        card_id: int,
        target_size: tuple[int, int],
    ) -> CardDisplayList | None:
        path = self.card_image_path_for_state(card_id, self.card_default_after_training(card_id), "small")
        if path is None:
            return None
        return self.build_small_still_card_display_list(
            card_id,
            path,
            target_size=target_size,
            show_detail=True,
        )

    def compose_profile_deck_card(self, card_id: int, leader: bool = False) -> Image.Image | None:
        display_list = self.build_profile_deck_card_display_list(card_id, leader=leader)
        return self.render_card_display_list(display_list) if display_list is not None else None

    def build_profile_deck_card_display_list(
        self,
        card_id: int,
        *,
        leader: bool = False,
    ) -> CardDisplayList | None:
        path = self.card_image_path_for_state(card_id, self.card_default_after_training(card_id), "deck")
        if path is None:
            return None
        return self.build_deck_card_display_list(
            card_id,
            path,
            native_size=GENERAL_DECK_CARD_NATIVE_SIZE,
            art_size=GENERAL_DECK_CARD_ART_SIZE,
            crop_align_y=0.0,
            leader=leader,
            show_detail=True,
            attr_x=3.70001220703125,
            mask_sprite_name=None,
            render_size=GENERAL_DECK_CARD_RENDER_SIZE,
        )

    def card_frame_file(self, card: dict[str, Any]) -> str:
        rarity = str(card.get("cardRarityType", "") or "")
        if rarity == "rarity_birthday":
            return "frame_rarity_birthday.png"
        digits = "".join(ch for ch in rarity if ch.isdigit()) or "1"
        return f"frame_rarity_{digits}.png"

    def empty_profile_deck_card(self, target_size: tuple[int, int]) -> Image.Image:
        return self.render_card_display_list(self.build_empty_profile_deck_card_display_list(target_size))

    def build_empty_profile_deck_card_display_list(self, target_size: tuple[int, int]) -> CardDisplayList:
        return build_empty_deck_card_display_list(target_size)

    def compose_profile_leader_card(self, card_id: int) -> Image.Image | None:
        display_list = self.build_profile_leader_card_display_list(card_id)
        return self.render_card_display_list(display_list) if display_list is not None else None

    def build_profile_leader_card_display_list(self, card_id: int) -> CardDisplayList | None:
        path = self.card_image_path_for_state(card_id, self.card_default_after_training(card_id), "small")
        if path is None:
            return None
        return self.build_small_still_card_display_list(
            card_id,
            path,
            target_size=GENERAL_NATIVE_SIZES["LeaderCard"],
            show_detail=True,
        )

    def render_card_member_content(
        self,
        item: dict[str, Any],
    ) -> tuple[Image.Image, tuple[float, float]] | NativeUnresolvedContent:
        card_member_type = int(item.get("type", 0) or 0)
        expected_view = "ClipSizeCardContentView" if card_member_type == 1 else "FullSizeCardContentView"
        generated = self.generate_card_member_data(item)
        display_list = self.build_card_member_display_list(item)
        if display_list is not None:
            image = self.render_card_display_list(display_list)
            return image, (image.width / 2, image.height / 2)
        return self.native_unresolved(
            "card_member",
            item,
            "card member content route is known, but the required card sprite PNG is not available locally",
            expected_view=expected_view,
            expected_size=PREFAB_NATIVE_SIZES.get(expected_view),
            required_inputs=("userCards", _CARDS_FILENAME, "character/member card assets"),
            generated_data=generated,
        )

    def build_card_member_display_list(self, item: dict[str, Any]) -> CardDisplayList | None:
        card_member_type = int(item.get("type", 0) or 0)
        card_id = content_data_id("card_member", item)
        path = self.card_member_image_path(item)
        if path is None:
            return None
        show_detail = bool_from_profile(item.get("showMasterRank", False))
        if card_member_type == 1:
            return self.build_deck_card_display_list(
                card_id,
                path,
                native_size=CLIP_CARD_MEMBER_NATIVE_SIZE,
                art_size=CLIP_CARD_MEMBER_ART_SIZE,
                crop_align_y=0.5,
                leader=False,
                show_detail=show_detail,
                attr_x=8.0,
                mask_sprite_name=None,
            )
        return self.build_small_still_card_display_list(
            card_id,
            path,
            target_size=FULL_CARD_MEMBER_NATIVE_SIZE,
            show_detail=show_detail,
        )

    def render_honor_content(
        self,
        item: dict[str, Any],
    ) -> tuple[Image.Image, tuple[float, float]] | NativeUnresolvedContent:
        honor_id = content_data_id("honor", item)
        image = self.compose_honor_image(
            honor_id,
            self.user_honor_level_for(honor_id),
            bool_from_profile(item.get("fullSize", False)),
        )
        if image is not None:
            return image, (image.width / 2, image.height / 2)
        return self.native_unresolved(
            "honor",
            item,
            "honor content route is known, but one or more honor badge assets are not available locally",
            expected_view="HonorContentView",
            expected_size=PREFAB_NATIVE_SIZES["HonorContentView"],
            required_inputs=("userHonors", "userHonorMissions", "honors.json", "honor assets"),
            generated_data=self.generate_honor_data(item),
        )

    def compose_honor_image(self, honor_id: int, level: int, full_size: bool) -> Image.Image | None:
        if image := self.honor_request_image(self.honor_requests.get(self.honor_slot_key(honor_id, level, full_size))):
            return image
        if image := self.honor_request_image(self.honor_requests.get(str(honor_id))):
            return image

        request = self.build_masterdata_honor_request(honor_id, level, full_size)
        if request is None:
            return None
        images = {
            "honor_img": self.open_rgba(Path(request.honor_img_path)) if request.honor_img_path else None,
            "rank_img": self.open_rgba(Path(request.rank_img_path)) if request.rank_img_path else None,
            "frame_img": self.open_rgba(Path(request.frame_img_path)) if request.frame_img_path else None,
            "frame_degree_level_img": (
                self.open_rgba(Path(request.frame_degree_level_img_path))
                if request.frame_degree_level_img_path
                else None
            ),
            "scroll_img": self.open_rgba(Path(request.scroll_img_path)) if request.scroll_img_path else None,
            "lv_img": self.open_rgba(Path(request.lv_img_path)) if request.lv_img_path else None,
            "lv6_img": self.open_rgba(Path(request.lv6_img_path)) if request.lv6_img_path else None,
        }
        return compose_full_honor_image_from_loaded_assets(request, images)

    def build_masterdata_honor_request(
        self,
        honor_id: int,
        level: int,
        full_size: bool,
    ) -> HonorRequest | None:
        """Derive an honor request from loaded masterdata without decoding or composing images."""

        if self.masterdata is None:
            return None

        honor = self.honors.get(honor_id)
        if not honor:
            return None
        group = self.honor_group_for(honor)
        if not group:
            return None
        visual = self.resolve_honor_level_visual(honor, level)
        asset_name, rarity, level = self._honor_asset_details(honor, visual, level)
        bg_asset_name = self._honor_background_asset_name(group, asset_name)
        group_type = self._resolved_honor_group_type(group, bg_asset_name, asset_name)
        mode = "main" if full_size else "sub"
        honor_path = self.honor_background_path(group_type, bg_asset_name, asset_name, mode)
        if honor_path is None:
            return None
        rarity_rank = self.honor_rarity_rank(rarity)
        frame_path = self.honor_frame_path(group, bg_asset_name, asset_name, mode, rarity_rank)
        rank_path = self.honor_rank_path(group_type, asset_name, mode, honor_path)
        honor_type = self.honor_type_for_group(group, bg_asset_name, asset_name)
        frame_degree_level_path = self.honor_frame_degree_level_path(group, honor_type, rarity_rank)
        request_group_type = "fc_ap" if honor_id in FC_AP_HONOR_IDS else group_type

        scroll_path = self._honor_scroll_path(asset_name)
        lv_path, lv6_path = self._honor_level_icon_paths(request_group_type, group_type)

        request = HonorRequest(
            honor_type=honor_type,
            group_type=request_group_type,
            honor_rarity=rarity,
            honor_level=level,
            fc_or_ap_level=str(level) if honor_group_uses_scroll_level(request_group_type) else None,
            is_main_honor=full_size,
            honor_img_path=self.honor_request_path(honor_path),
            rank_img_path=self.honor_request_path(rank_path),
            frame_img_path=self.honor_request_path(frame_path),
            frame_degree_level_img_path=self.honor_request_path(frame_degree_level_path),
            scroll_img_path=self.honor_request_path(scroll_path),
            lv_img_path=self.honor_request_path(lv_path),
            lv6_img_path=self.honor_request_path(lv6_path),
        )
        return request

    @staticmethod
    def _honor_asset_details(
        honor: dict[str, Any], visual: dict[str, Any] | None, requested_level: int
    ) -> tuple[str, str, int]:
        asset_name = str(honor.get("assetbundleName", "") or "")
        rarity = str(honor.get("honorRarity", "") or "")
        if visual is None:
            return asset_name, rarity, requested_level
        asset_name = str(_first_truthy(asset_name, visual.get("assetbundleName"), default=""))
        rarity = str(_first_truthy(rarity, visual.get("honorRarity"), default=""))
        level = _int_first(visual.get("level")) if requested_level <= 0 else requested_level
        return asset_name, rarity, level

    @staticmethod
    def _honor_background_asset_name(group: dict[str, Any], asset_name: str) -> str:
        configured = group.get("backgroundAssetbundleName", group.get("backgroundAssetBundleName", ""))
        return str(_first_truthy(configured, asset_name, default=""))

    def _resolved_honor_group_type(self, group: dict[str, Any], bg_asset_name: str, asset_name: str) -> str:
        group_type = str(group.get("honorType", "") or "")
        return "wl_event" if self.is_world_link_honor_group(group_type, bg_asset_name, asset_name) else group_type

    def _honor_scroll_path(self, asset_name: str) -> Path | None:
        if not asset_name:
            return None
        return self.first_region_asset([Path("honor") / asset_name / "scroll.png"])

    def _honor_level_icon_paths(self, request_group_type: str, group_type: str) -> tuple[Path | None, Path | None]:
        if request_group_type != "fc_ap" and group_type not in {"character", "achievement"}:
            return None, None
        return (
            self.static_image_path("honor", "icon_degreeLv.png"),
            self.static_image_path("honor", "icon_degreeLv6.png"),
        )

    def honor_group_for(self, honor: dict[str, Any]) -> dict[str, Any] | None:
        return self.honor_groups.get(int(honor.get("groupId", 0) or 0))

    def resolve_honor_level_visual(self, honor: dict[str, Any], requested_level: int) -> dict[str, Any] | None:
        levels = self._usable_honor_level_visuals(honor)
        if not levels:
            return None
        for visual in levels:
            if self._honor_visual_level(visual) == requested_level:
                return visual
        if requested_level > 0:
            eligible = [visual for visual in levels if self._honor_visual_level(visual) <= requested_level]
            if eligible:
                return max(eligible, key=self._honor_visual_level)
        return levels[0]

    @staticmethod
    def _usable_honor_level_visuals(honor: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            level
            for level in honor.get("levels", []) or []
            if isinstance(level, dict) and (level.get("assetbundleName") or level.get("honorRarity"))
        ]

    @staticmethod
    def _honor_visual_level(visual: dict[str, Any]) -> int:
        return _int_first(visual.get("level"))

    def honor_background_path(self, group_type: str, bg_asset_name: str, asset_name: str, mode: str) -> Path | None:
        rels: list[Path] = []
        if group_type == "rank_match":
            rels.append(Path("rank_live") / "honor" / bg_asset_name / f"degree_{mode}.png")
        if bg_asset_name:
            rels.append(Path("honor") / bg_asset_name / f"degree_{mode}.png")
        if asset_name and asset_name != bg_asset_name:
            rels.append(Path("honor") / asset_name / f"degree_{mode}.png")
        if group_type in {"event", "wl_event"}:
            derived = self.derive_honor_background_asset_name(asset_name)
            if derived:
                rels.append(Path("honor") / derived / f"degree_{mode}.png")
            rels.append(Path("honor") / bg_asset_name / f"rank_{mode}.png")
            rels.append(Path("honor") / asset_name / f"rank_{mode}.png")
        return self.first_region_asset(rels)

    def derive_honor_background_asset_name(self, asset_name: str) -> str:
        asset_name = asset_name.strip()
        if asset_name.startswith("honor_top_"):
            parts = asset_name.split("_", 3)
            if len(parts) == 4:
                return "honor_bg_" + parts[3]
        return ""

    def is_world_link_honor_group(self, group_type: str, bg_asset_name: str, asset_name: str) -> bool:
        if group_type == "world_link":
            return True
        return "event_wl" in bg_asset_name.strip() or "event_wl" in asset_name.strip()

    def honor_type_for_group(self, group: dict[str, Any], bg_asset_name: str, asset_name: str) -> str:
        frame_name = str(group.get("frameName", "") or "")
        if (
            str(group.get("honorType", "") or "") == "birthday"
            or frame_name.startswith("honor_frame_birthday")
            or bg_asset_name.startswith("honor_bg_birthday")
            or asset_name.startswith("honor_bg_birthday")
        ):
            return "birthday"
        return "normal"

    def honor_rank_path(self, group_type: str, asset_name: str, mode: str, honor_path: Path) -> Path | None:
        if not asset_name or group_type not in {"event", "wl_event", "rank_match"}:
            return None
        rel = (
            Path("rank_live") / "honor" / asset_name / f"{mode}.png"
            if group_type == "rank_match"
            else Path("honor") / asset_name / f"rank_{mode}.png"
        )
        path = self.first_region_asset([rel])
        if path == honor_path:
            return None
        return path

    def honor_frame_path(
        self,
        group: dict[str, Any],
        bg_asset_name: str,
        asset_name: str,
        mode: str,
        rarity_rank: int,
    ) -> Path | None:
        frame_name = str(group.get("frameName", "") or "")
        honor_type = self.honor_type_for_group(group, bg_asset_name, asset_name)
        if honor_type == "birthday" and rarity_rank <= 1:
            return None
        mode_short = mode[0]
        static_path = self.static_images / "honor" / f"frame_degree_{mode_short}_{rarity_rank}.png"
        frame_name = self._resolved_honor_frame_name(frame_name, honor_type, bg_asset_name, asset_name)
        region_path = self._eligible_honor_frame_path(frame_name, honor_type, mode_short, rarity_rank)
        if region_path is not None:
            return region_path
        return static_path if static_path.exists() else None

    @staticmethod
    def _resolved_honor_frame_name(frame_name: str, honor_type: str, bg_asset_name: str, asset_name: str) -> str:
        if honor_type != "birthday" or frame_name:
            return frame_name
        if bg_asset_name.startswith("honor_bg_birthday_"):
            return "honor_frame_birthday_" + bg_asset_name.removeprefix("honor_bg_birthday_")
        if asset_name.startswith("honor_bg_birthday_"):
            return "honor_frame_birthday_" + asset_name.removeprefix("honor_bg_birthday_")
        return ""

    def _eligible_honor_frame_path(
        self, frame_name: str, honor_type: str, mode_short: str, rarity_rank: int
    ) -> Path | None:
        if not frame_name:
            return None
        start_rare = 3 if frame_name.startswith("event") else 2
        if honor_type != "birthday" and rarity_rank < start_rare:
            return None
        rel = Path("honor_frame") / frame_name / f"frame_degree_{mode_short}_{rarity_rank}.png"
        return self.first_region_asset([rel])

    def honor_frame_degree_level_path(
        self,
        group: dict[str, Any],
        honor_type: str,
        rarity_rank: int,
    ) -> Path | None:
        if honor_type != "birthday":
            return None
        frame_name = str(group.get("frameName", "") or "")
        if not frame_name:
            return None
        return self.first_region_asset([Path("honor_frame") / frame_name / f"frame_degree_level_{rarity_rank}.png"])

    def honor_candidate_paths(
        self,
        honor: dict[str, Any] | None,
        group: dict[str, Any] | None,
        full_size: bool,
    ) -> list[Path]:
        if not honor or not group:
            return []
        mode = "main" if full_size else "sub"
        asset_name = str(honor.get("assetbundleName", "") or "")
        bg_asset_name = (
            str(group.get("backgroundAssetbundleName", group.get("backgroundAssetBundleName", "")) or "") or asset_name
        )
        group_type = str(group.get("honorType", "") or "")
        rels: list[Path] = [
            Path("honor") / bg_asset_name / f"degree_{mode}.png",
            Path("honor") / asset_name / f"degree_{mode}.png",
            Path("honor") / asset_name / f"rank_{mode}.png",
        ]
        derived = self.derive_honor_background_asset_name(asset_name)
        if derived:
            rels.append(Path("honor") / derived / f"degree_{mode}.png")
        if group_type == "rank_match":
            rels.extend(
                [
                    Path("rank_live") / "honor" / bg_asset_name / f"degree_{mode}.png",
                    Path("rank_live") / "honor" / asset_name / f"{mode}.png",
                ]
            )
        paths = self.region_asset_candidate_paths(rels)
        rarity_rank = self.honor_rarity_rank(str(honor.get("honorRarity", "") or ""))
        paths.append(self.static_images / "honor" / f"frame_degree_{mode[0]}_{rarity_rank}.png")
        frame_name = str(group.get("frameName", "") or "")
        if frame_name:
            paths.extend(
                self.region_asset_candidate_paths(
                    [Path("honor_frame") / frame_name / f"frame_degree_{mode[0]}_{rarity_rank}.png"]
                )
            )
        return paths

    def honor_rarity_rank(self, rarity: str) -> int:
        if rarity == "middle":
            return 2
        if rarity == "high":
            return 3
        if rarity == "highest":
            return 4
        return 1

    def honor_rank_position(
        self,
        base: Image.Image,
        rank: Image.Image,
        full_size: bool,
        group_type: str,
        rank_path: Path,
    ) -> tuple[int, int]:
        if rank.width >= base.width - 8 and rank.height >= base.height - 8:
            return (0, 0)
        if group_type == "rank_match":
            return (190, 0) if full_size else (17, 42)
        folder = rank_path.parent.name.lower()
        if folder.startswith("honor_top_") and "event" in folder:
            return (0, 0)
        return (190, 0) if full_size else (34, 42)

    def render_bonds_honor_content(
        self,
        item: dict[str, Any],
    ) -> tuple[Image.Image, tuple[float, float]] | NativeUnresolvedContent:
        image = self.compose_bonds_honor_image(
            item,
            bool_from_profile(item.get("fullSize", False)),
        )
        if image is not None:
            return image, (image.width / 2, image.height / 2)
        return self.native_unresolved(
            "bonds_honor",
            item,
            "bonds honor content delegates to HonorUtility.Instantiate bonds-honor child layout",
            expected_view="HonorContentView",
            expected_size=PREFAB_NATIVE_SIZES["HonorContentView"],
            required_inputs=("userBondsHonors", "bondsHonors.json", "bonds honor assets"),
            generated_data=self.generate_bonds_honor_data(item),
        )

    def compose_bonds_honor_image(self, item: dict[str, Any], full_size: bool) -> Image.Image | None:
        honor_id = content_data_id("bonds_honor", item)
        level = self.user_bonds_honor_level_for(honor_id)
        word_id = int(item.get("wordId", 0) or 0)
        inverse = bool_from_profile(item.get("inverse", False))
        use_unit_virtual_singer = bool_from_profile(item.get("useUnitVirtualSinger", False))
        request_keys = self._bonds_honor_request_keys(
            honor_id, level, full_size, word_id, inverse, use_unit_virtual_singer
        )
        if image := self._configured_bonds_honor_image(honor_id, request_keys):
            return image

        request = self.build_masterdata_bonds_honor_request(item, full_size)
        if request is None:
            return None
        images = self._loaded_request_images(
            request,
            {
                "bonds_bg": "bonds_bg_path",
                "bonds_bg2": "bonds_bg_path2",
                "chara_icon_1": "chara_icon_path",
                "chara_icon_2": "chara_icon_path2",
                "mask_img": "mask_img_path",
                "frame_img": "frame_img_path",
                "word_img": "word_img_path",
                "lv_img": "lv_img_path",
                "lv6_img": "lv6_img_path",
            },
        )
        return compose_full_honor_image_from_loaded_assets(request, images)

    def _bonds_honor_request_keys(
        self,
        honor_id: int,
        level: int,
        full_size: bool,
        word_id: int,
        inverse: bool,
        use_unit_virtual_singer: bool,
    ) -> list[str]:
        keys = [self.bonds_honor_slot_key(honor_id, level, full_size, word_id, inverse, use_unit_virtual_singer)]
        if use_unit_virtual_singer:
            keys.append(self.bonds_honor_slot_key(honor_id, level, full_size, word_id, inverse))
        return keys

    def _configured_bonds_honor_image(self, honor_id: int, request_keys: list[str]) -> Image.Image | None:
        for key in request_keys:
            if image := self.honor_request_image(self.bonds_honor_requests.get(key)):
                return image
        return self.honor_request_image(self.bonds_honor_requests.get(str(honor_id)))

    def _loaded_request_images(self, request: HonorRequest, fields: dict[str, str]) -> dict[str, Image.Image | None]:
        images: dict[str, Image.Image | None] = {}
        for image_key, path_field in fields.items():
            raw_path = getattr(request, path_field)
            images[image_key] = self.open_rgba(Path(raw_path)) if raw_path else None
        return images

    def build_masterdata_bonds_honor_request(
        self,
        item: dict[str, Any],
        full_size: bool,
    ) -> HonorRequest | None:
        """Derive a bonds-honor request from loaded masterdata without decoding or composing images."""

        if self.masterdata is None:
            return None

        honor_id = content_data_id("bonds_honor", item)
        level = self.user_bonds_honor_level_for(honor_id)
        honor = self.bonds_honors.get(honor_id)
        if not honor:
            return None

        rarity = str(honor.get("honorRarity", "") or "")
        rarity_rank = self.honor_rarity_rank(rarity)
        mode = "main" if full_size else "sub"
        bg_suffix = "" if full_size else "_sub"

        unit_id1 = int(honor.get("gameCharacterUnitId1", honor.get("gameCharacterUnitID1", 0)) or 0)
        unit_id2 = int(honor.get("gameCharacterUnitId2", honor.get("gameCharacterUnitID2", 0)) or 0)
        character_id1 = self.game_character_id_for_unit(unit_id1)
        character_id2 = self.game_character_id_for_unit(unit_id2)
        if character_id1 <= 0 or character_id2 <= 0:
            return None

        display_slots = self.bonds_honor_display_slots(honor, item, unit_id1, character_id1, unit_id2, character_id2)
        if bool_from_profile(item.get("inverse", False)):
            display_slots[0], display_slots[1] = display_slots[1], display_slots[0]

        bg_path = self.static_image_path("honor", "bonds", f"{display_slots[0][1]}{bg_suffix}.png")
        bg2_path = self.static_image_path("honor", "bonds", f"{display_slots[1][1]}{bg_suffix}.png")
        chara_icon_path = self.first_region_asset(
            [Path("bonds_honor") / "character" / f"chr_sd_{display_slots[0][0]:02d}_01.png"]
        )
        chara_icon_path2 = self.first_region_asset(
            [Path("bonds_honor") / "character" / f"chr_sd_{display_slots[1][0]:02d}_01.png"]
        )
        mask_path = self.static_image_path("honor", f"mask_degree_{mode}.png")
        frame_path = self.static_image_path("honor", f"frame_degree_{mode[0]}_{rarity_rank}.png")
        lv_path = self.static_image_path("honor", "icon_degreeLv.png")
        lv6_path = self.static_image_path("honor", "icon_degreeLv6.png")

        word_path: Path | None = None
        if full_size:
            word_id = int(item.get("wordId", 0) or 0) or honor_id
            bundle_name = self.bonds_honor_word_bundle_name(honor, word_id, character_id1, character_id2)
            if bundle_name:
                word_path = self.first_region_asset([Path("bonds_honor") / "word" / f"{bundle_name}.png"])

        request = HonorRequest(
            honor_type="bonds",
            honor_rarity=rarity,
            honor_level=level,
            is_main_honor=full_size,
            chara_id=str(display_slots[0][0]),
            chara_id2=str(display_slots[1][0]),
            bonds_bg_path=self.honor_request_path(bg_path),
            bonds_bg_path2=self.honor_request_path(bg2_path),
            chara_icon_path=self.honor_request_path(chara_icon_path),
            chara_icon_path2=self.honor_request_path(chara_icon_path2),
            mask_img_path=self.honor_request_path(mask_path),
            frame_img_path=self.honor_request_path(frame_path),
            word_img_path=self.honor_request_path(word_path),
            lv_img_path=self.honor_request_path(lv_path),
            lv6_img_path=self.honor_request_path(lv6_path),
        )
        return request

    def game_character_id_for_unit(self, unit_id: int) -> int:
        unit = self.game_character_units.get(unit_id)
        if not unit:
            return 0
        return int(unit.get("gameCharacterId", 0) or 0)

    def bonds_honor_display_slots(
        self,
        honor: dict[str, Any],
        item: dict[str, Any],
        unit_id1: int,
        character_id1: int,
        unit_id2: int,
        character_id2: int,
    ) -> list[tuple[int, int]]:
        if bool_from_profile(honor.get("configurableUnitVirtualSinger", False)) and bool_from_profile(
            item.get("useUnitVirtualSinger", False)
        ):
            original_unit_id1 = unit_id1
            original_unit_id2 = unit_id2
            unit_id1 = self.unit_virtual_singer_unit_id(original_unit_id1, original_unit_id2)
            unit_id2 = self.unit_virtual_singer_unit_id(original_unit_id2, original_unit_id1)
            character_id1 = self.game_character_id_for_unit(unit_id1) or character_id1
            character_id2 = self.game_character_id_for_unit(unit_id2) or character_id2
        return [(unit_id1, character_id1), (unit_id2, character_id2)]

    def unit_virtual_singer_unit_id(self, candidate_unit_id: int, paired_unit_id: int) -> int:
        candidate = self.game_character_units.get(candidate_unit_id)
        paired = self.game_character_units.get(paired_unit_id)
        if not candidate or not paired:
            return candidate_unit_id
        candidate_character_id = int(candidate.get("gameCharacterId", 0) or 0)
        paired_unit = str(paired.get("unit", "") or "")
        if candidate_character_id < 21 or paired_unit == "piapro":
            return candidate_unit_id
        for unit_id, unit in self.game_character_units.items():
            if (
                int(unit.get("gameCharacterId", 0) or 0) == candidate_character_id
                and str(unit.get("unit", "") or "") == paired_unit
            ):
                return int(unit_id)
        return candidate_unit_id

    def bonds_honor_word_bundle_name(
        self,
        honor: dict[str, Any],
        word_id: int,
        character_id1: int,
        character_id2: int,
    ) -> str:
        honor_id = int(honor.get("id", 0) or 0)
        word = self.bonds_honor_words.get(word_id)
        if word is not None and str(word.get("assetbundleName", "") or "").strip():
            tier_suffix = max(1, honor_id % 100)
            return f"{str(word.get('assetbundleName')).strip()}_{tier_suffix:02d}"
        if abs(honor_id - word_id) < 100:
            return f"honorname_{character_id1:02d}{character_id2:02d}_{word_id % 100:02d}_01"
        if word_id % 10 == 1:
            return f"honorname_{character_id1:02d}{character_id2:02d}_default_{character_id1:02d}{character_id2:02d}_01"
        return f"honorname_{character_id1:02d}{character_id2:02d}_default_{character_id2:02d}{character_id1:02d}_01"

    def render_collection_content(
        self,
        item: dict[str, Any],
    ) -> tuple[Image.Image, tuple[float, float]] | NativeUnresolvedContent | None:
        resource = self.image_resource_for("collection", item)
        collection_type = str(resource.get("customProfileResourceCollectionType", "none") or "none")
        path = self.resource_path(resource)
        if path:
            image = self.open_checked_image(path, "RGBA")
            return image, (image.width / 2, image.height / 2)
        if collection_type == "omikuji":
            return self.render_omikuji_collection_content(item, resource)
        if collection_type in {"none", "other", ""}:
            return self.render_image_content("collection", item)
        return self.native_unresolved(
            "collection",
            item,
            f"collection resource type {collection_type!r} uses a dynamic child UI/material path",
            resource=resource,
            expected_view="ImageContentView+collection material",
            expected_size=PREFAB_NATIVE_SIZES["CollectionCustomPrefabContentView"],
            required_inputs=("MasterResource", "GenerateCollectionData", "collection child prefab/material assets"),
            generated_data=self.generate_collection_data(item),
        )

    def render_omikuji_collection_content(
        self,
        item: dict[str, Any],
        resource: dict[str, Any],
    ) -> tuple[Image.Image, tuple[float, float]] | NativeUnresolvedContent:
        target_id = int(item.get("targetId", 0) or 0)
        omikuji = self.omikujis.get(target_id)
        if not omikuji:
            return self.native_unresolved(
                "collection",
                item,
                f"omikuji collection needs the target {_OMIKUJI_FILENAME} row",
                resource=resource,
                expected_view="CollectionCustomPrefabContentView",
                expected_size=OMIKUJI_RESULT_NATIVE_SIZE,
                required_inputs=("MasterResource", "GenerateCollectionData", _OMIKUJI_FILENAME),
                generated_data=self.generate_collection_data(item),
            )

        asset_paths = {
            "background": self.omikuji_background_asset_path(omikuji),
            "fortune": self.omikuji_asset_path(omikuji, "fortune"),
        }
        missing_assets = [name for name, path in asset_paths.items() if path is None or not path.exists()]
        if missing_assets:
            return self.native_unresolved(
                "collection",
                item,
                f"omikuji collection needs material asset(s): {', '.join(missing_assets)}",
                resource=resource,
                expected_view="CollectionCustomPrefabContentView",
                expected_size=OMIKUJI_RESULT_NATIVE_SIZE,
                required_inputs=(
                    "MasterResource",
                    "GenerateCollectionData",
                    _OMIKUJI_FILENAME,
                    "lottery_game material assets",
                ),
                generated_data=self.generate_collection_data(item),
            )

        image = self.draw_omikuji_result_view(omikuji, asset_paths)
        return image, (image.width / 2, image.height / 2)

    def omikuji_background_asset_path(self, omikuji: dict[str, Any]) -> Path | None:
        for key in (
            "backgroundImagePath",
            "background_imagePath",
            "backgroundPath",
            "background_path",
            "resultImagePath",
            "result_imagePath",
        ):
            if path := self.resolve_request_asset_path(str(omikuji.get(key, "") or "")):
                return path

        bundle = str(omikuji.get("omikujiCoverAssetbundleName", "") or "").strip("/")
        cover_file = str(omikuji.get("omikujiCoverFilePath", "") or "").strip("/")
        if not bundle or not cover_file:
            return None
        cover_stem = Path(cover_file).stem
        if cover_stem.startswith("omikuji_"):
            background_stem = "bg_" + cover_stem
        else:
            background_stem = f"bg_omikuji_{cover_stem}"
        return self.first_region_asset((Path(bundle) / f"{background_stem}.png",))

    def omikuji_asset_path(self, omikuji: dict[str, Any], prefix: str) -> Path | None:
        for key in (
            f"{prefix}ImagePath",
            f"{prefix}_imagePath",
            f"{prefix}Path",
            f"{prefix}_path",
        ):
            if path := self.resolve_request_asset_path(str(omikuji.get(key, "") or "")):
                return path
        bundle = str(omikuji.get(f"{prefix}AssetbundleName", "") or "").strip("/")
        file_path = str(omikuji.get(f"{prefix}FilePath", "") or "").strip("/")
        if not bundle or not file_path:
            return None
        file_name = file_path if file_path.lower().endswith(".png") else f"{file_path}.png"
        return self.first_region_asset((Path(bundle) / file_name,))

    def omikuji_font_candidates(self, *, decorative: bool = False) -> list[Path]:
        names = (
            ["FOT-Omikuji", "FOT-UDMinchoPro-B", "FOT-RodinNTLGPro-DB"]
            if decorative
            else [
                "FOT-UDMinchoPro-B",
                "FOT-RodinNTLGPro-DB",
            ]
        )
        candidates: list[Path] = []
        for name in names:
            path = self.tmp_font_library.source_font_path(name)
            if path is not None:
                candidates.append(path)
            candidates.append(self.fonts / f"{name}.otf")
            candidates.append(self.fonts / f"{name}.ttf")
        for base in self.data_root_candidates():
            candidates.extend(
                (
                    base / "custom_profile" / "tmp-font-assets" / self.region / "source-fonts" / _OMIKUJI_FONT_FILENAME,
                    base / "custom_profile" / "tmp-font-assets" / "cn" / "source-fonts" / _OMIKUJI_FONT_FILENAME,
                    base / "custom_profile" / "tmp-font-assets" / "kr" / "source-fonts" / _OMIKUJI_FONT_FILENAME,
                )
            )
        return candidates

    def omikuji_font_path(self, *, decorative: bool = False) -> Path | None:
        for path in self.omikuji_font_candidates(decorative=decorative):
            try:
                if path.is_file():
                    return path
            except OSError:
                continue
        return self.general_font_path()

    def omikuji_font(self, size: int, *, decorative: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        path = self.omikuji_font_path(decorative=decorative)
        if path is not None:
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                pass
        return self.general_font(size, bold=not decorative)

    def draw_omikuji_result_view(self, omikuji: dict[str, Any], asset_paths: dict[str, Path]) -> Image.Image:
        background_path = asset_paths["background"]
        fortune_path = asset_paths["fortune"]
        background = self.open_rgba(background_path)
        fortune = self.open_rgba(fortune_path)
        if background is None or fortune is None:
            raise FileNotFoundError("required omikuji result-view assets are missing")
        display_list = build_omikuji_display_list(
            omikuji,
            background_path=background_path,
            background_size=background.size,
            fortune_path=fortune_path,
            fortune_size=fortune.size,
        )
        loaded = {background_path.resolve(): background, fortune_path.resolve(): fortune}
        adapter = PillowOmikujiAdapter(
            lambda size, decorative: self.omikuji_font(size, decorative=decorative),
            lambda path: loaded.get(path.resolve()) or self.open_rgba(path),
        )
        return adapter.render(display_list)

    def render_stamp_content(
        self,
        item: dict[str, Any],
    ) -> tuple[Image.Image, tuple[float, float]] | NativeUnresolvedContent | None:
        stamp_id = content_data_id("stamp", item)
        stamp_asset = self.stamp_assets.get(stamp_id, {})
        if image_path := str(stamp_asset.get("imagePath", stamp_asset.get("image_path", "")) or "").strip():
            path = self.resolve_request_asset_path(image_path)
            if path is not None:
                image = self.open_checked_image(path, "RGBA")
                return image, (image.width / 2, image.height / 2)

        resource = self.image_resource_for("stamp", item)
        path = self.stamp_resource_path(resource)
        if not path:
            return self.native_unresolved(
                "stamp",
                item,
                "stamp ImageContentView route is known, but the stamp sprite PNG is not available locally",
                resource=resource,
                expected_view="ImageContentView",
                expected_size=PREFAB_NATIVE_SIZES["ImageContentView"],
                required_inputs=("stamps.json", "stamp sprite asset bundle"),
                generated_data=self.generate_stamp_data(item, resource),
            )
        image = self.open_checked_image(path, "RGBA")
        return image, (image.width / 2, image.height / 2)

    def render_dynamic_content(self, kind: str, item: dict[str, Any]) -> NativeUnresolvedContent:
        return self.native_unresolved(
            kind,
            item,
            "DynamicContentView needs DynamicAtlasStudio texture/uvRect generation before standalone rasterization",
            expected_view="DynamicProfileContentView",
            expected_size=PREFAB_NATIVE_SIZES["DynamicProfileContentView"],
            required_inputs=("DynamicAtlasStudio", "renderer texture", "uvRect", "viewportSize"),
            generated_data={"id": content_data_id(kind, item)},
        )

    def native_unresolved(
        self,
        kind: str,
        item: dict[str, Any],
        reason: str,
        resource: dict[str, Any] | None = None,
        expected_view: str = "",
        expected_size: tuple[float, float] | None = None,
        required_inputs: tuple[str, ...] = (),
        generated_data: dict[str, Any] | None = None,
    ) -> NativeUnresolvedContent:
        content_type_id, content_type = content_type_for_kind(kind)
        return NativeUnresolvedContent(
            kind=kind,
            content_type_id=content_type_id,
            content_type=content_type,
            reason=reason,
            item=item,
            generated_data=generated_data
            if generated_data is not None
            else self.generate_data_for(kind, item, resource),
            resource=resource,
            expected_view=expected_view,
            expected_size=expected_size,
            required_inputs=required_inputs,
            native_methods=NATIVE_METHODS_BY_KIND.get(kind, ()),
        )

    def record_native_audit(
        self,
        card_ref: dict[str, int],
        content: NativeContent,
        status: str,
        result: tuple[Image.Image, tuple[float, float]]
        | tuple[Image.Image, tuple[float, float], bool]
        | NativeUnresolvedContent
        | None,
    ) -> None:
        content_type_id, content_type = content_type_for_kind(content.kind)
        resource = self.image_resource_for(content.kind, content.item)
        entry: dict[str, Any] = {
            **card_ref,
            "kind": content.kind,
            "contentTypeId": content_type_id,
            "contentType": content_type,
            "layer": content.layer,
            "visible": bool(content.object_data.get("visible", False)),
            "status": status,
            "dataId": content_data_id(content.kind, content.item),
            "generatedData": self.generate_data_for(content.kind, content.item, resource),
            "resource": summarize_resource(resource),
            "nativeMethods": list(NATIVE_METHODS_BY_KIND.get(content.kind, ())),
        }
        if isinstance(result, NativeUnresolvedContent):
            entry.update(result.to_audit_dict(content, card_ref))
            entry["status"] = status
        elif isinstance(result, tuple):
            entry["localSize"] = {"x": result[0].width, "y": result[0].height}
            entry["pivot"] = {"x": result[1][0], "y": result[1][1]}
            entry["scaleConsumed"] = len(result) >= 3 and bool(result[2])
        self.native_audit.append(entry)

    def generate_data_for(
        self,
        kind: str,
        item: dict[str, Any],
        resource: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resource = resource or self.image_resource_for(kind, item)
        if kind == "general":
            return self.generate_general_data(item, resource)
        if kind in IMAGE_CONTENT_TYPE_NAMES and kind != "stamp":
            return self.generate_image_data(kind, item, resource)
        if kind == "stamp":
            return {"stampId": content_data_id("stamp", item)}
        if kind == "card_member":
            return self.generate_card_member_data(item)
        if kind == "honor":
            return self.generate_honor_data(item)
        if kind == "bonds_honor":
            return self.generate_bonds_honor_data(item)
        if kind == "text":
            return self.tmp_generated_text_data_dict(self.generate_text_data(item))
        if kind == "shape":
            return self.generate_shape_data(item)
        if kind in {"mini_chara", "screen_filter"}:
            return {"id": content_data_id(kind, item)}
        return {"id": content_data_id(kind, item)}

    def generate_image_data(self, kind: str, item: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": content_data_id(kind, item),
            "assetBundleName": resource.get("resourceLoadVal"),
            "fileName": resource.get("fileName"),
            "viewName": resource.get("name"),
        }

    def generate_general_data(self, item: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "resourceFolderPath": resource.get("resourceLoadVal"),
            "fileName": resource.get("fileName"),
            "viewName": resource.get("name"),
            "resourceID": content_data_id("general", item),
        }

    def generate_shape_data(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": content_data_id("shape", item),
            "colorId": int(item.get("colorId", 0) or 0),
            "outlineColorId": int(item.get("outlineColorId", 0) or 0),
            "alpha": float(item.get("alpha", 1.0) if item.get("alpha") is not None else 1.0),
            "outlineAlpha": float(item.get("outlineAlpha", 0.0) or 0.0),
            "outlineSize": float(item.get("outlineSize", 0.0) or 0.0),
        }

    def generate_card_member_data(self, item: dict[str, Any]) -> dict[str, Any]:
        card_id = content_data_id("card_member", item)
        card = self.card_master_for(card_id)
        return {
            "UserCard": self.user_card_for(card_id),
            "MasterCard": summarize_card_master(card),
            "type": int(item.get("type", 0) or 0),
            "useAfterSpecialTraining": bool_from_profile(item.get("useAfterSpecialTraining", False)),
            "resolvedAfterTraining": self.card_member_after_training(item),
            "showDetail": bool_from_profile(item.get("showMasterRank", False)),
            "candidatePaths": [str(path) for path in self.card_member_image_candidates(item)],
        }

    def generate_stamp_data(self, item: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
        stamp_id = content_data_id("stamp", item)
        stamp_asset = self.stamp_assets.get(stamp_id, {})
        return {
            "stampId": stamp_id,
            "assetBundleName": str(stamp_asset.get("assetbundleName", resource.get("assetbundleName", "")) or ""),
            "imagePath": stamp_asset.get("imagePath", stamp_asset.get("image_path")),
            "candidatePaths": [str(path) for path in self.stamp_resource_candidates(resource)],
        }

    def card_master_for(self, card_id: int) -> dict[str, Any] | None:
        return self.cards.get(card_id) or self.card_assets.get(card_id)

    def card_asset_bundle_name(self, card_id: int) -> str:
        card = self.card_master_for(card_id) or {}
        return str(card.get("assetbundleName", card.get("assetBundleName", "")) or "").strip("/")

    def card_asset_path_for_state(self, card_id: int, after_training: bool, kind: str = "full") -> Path | None:
        asset = self.card_assets.get(card_id, {})
        if not asset:
            return None
        keys = _CARD_ASSET_STATE_KEYS.get((kind, after_training), _CARD_ASSET_STATE_KEYS[("full", after_training)])
        for key in keys:
            if path := self.resolve_request_asset_path(str(asset.get(key, "") or "")):
                return path
        return None

    def card_member_after_training(self, item: dict[str, Any]) -> bool:
        return bool_from_profile(item.get("useAfterSpecialTraining", False))

    def card_member_image_candidates(self, item: dict[str, Any]) -> list[Path]:
        card_id = content_data_id("card_member", item)
        after_training = self.card_member_after_training(item)
        card_member_type = int(item.get("type", 0) or 0)
        kind = "clip" if card_member_type == 1 else "small"
        if path := self.card_asset_path_for_state(card_id, after_training, kind):
            return [path]
        if self.masterdata is None:
            return []
        bundle = self.card_asset_bundle_name(card_id)
        if not bundle:
            return []
        full_file = "card_after_training.png" if after_training else "card_normal.png"
        cutout_file = "after_training.png" if after_training else "normal.png"
        cutout_trim_file = "card_after_training_trim.png" if after_training else "card_normal_trim.png"
        rels: list[Path] = []
        if card_member_type == 1:
            rels.extend(
                [
                    Path("character") / "member_cutout" / bundle / cutout_file,
                    Path("character") / "member_cutout" / bundle / cutout_trim_file,
                    Path("character") / "member_cutout" / f"{bundle}_rip" / cutout_file,
                    Path("character") / "member_cutout" / f"{bundle}_rip" / cutout_trim_file,
                    Path("character") / "member_cutout" / bundle / _DECK_IMAGE_FILENAME,
                    Path("character") / "member_cutout" / f"{bundle}_rip" / _DECK_IMAGE_FILENAME,
                    Path("character") / "member_cutout_trm" / bundle / cutout_file,
                    Path("character") / "member_cutout_trm" / bundle / cutout_trim_file,
                    Path("character") / "member_cutout_trm" / f"{bundle}_rip" / cutout_file,
                    Path("character") / "member_cutout_trm" / f"{bundle}_rip" / cutout_trim_file,
                ]
            )
        elif card_member_type == 2:
            rels.extend(
                [
                    Path("character") / "member_small" / bundle / full_file,
                    Path("character") / "member_small" / f"{bundle}_rip" / full_file,
                ]
            )
        else:
            rels.append(Path("character") / "member" / bundle / full_file)
            if not bundle.endswith("_rip"):
                rels.append(Path("character") / "member" / f"{bundle}_rip" / full_file)
            rels.append(
                Path("thumbnail") / "chara" / f"{bundle}_{'after_training' if after_training else 'normal'}.png"
            )
        return self.region_asset_candidate_paths(rels)

    def card_member_image_path(self, item: dict[str, Any]) -> Path | None:
        for path in self.card_member_image_candidates(item):
            if path.exists():
                return path
        return None

    def compose_card_member_image(
        self, path: Path, target_size: tuple[float, float], contain: bool = False
    ) -> Image.Image:
        target_w = max(1, round(target_size[0]))
        target_h = max(1, round(target_size[1]))
        src = self.open_checked_image(path, "RGBA")
        scale = (
            min(target_w / src.width, target_h / src.height)
            if contain
            else max(target_w / src.width, target_h / src.height)
        )
        resized = src.resize(
            (max(1, round(src.width * scale)), max(1, round(src.height * scale))),
            Image.Resampling.LANCZOS,
        )
        image = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        if contain:
            image.alpha_composite(resized, ((target_w - resized.width) // 2, (target_h - resized.height) // 2))
        else:
            left = max(0, (resized.width - target_w) // 2)
            top = max(0, (resized.height - target_h) // 2)
            image = resized.crop((left, top, left + target_w, top + target_h))
        mask = Image.new("L", image.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle(
            (0, 0, target_w, target_h), radius=max(1, round(min(target_w, target_h) * 0.03)), fill=255
        )
        alpha = ImageChops.multiply(image.getchannel("A"), mask)
        image.putalpha(alpha)
        return image

    def generate_honor_data(self, item: dict[str, Any]) -> dict[str, Any]:
        honor_id = content_data_id("honor", item)
        honor = self.honors.get(honor_id)
        group = self.honor_group_for(honor) if honor else None
        level = self.user_honor_level_for(honor_id)
        full_size = bool_from_profile(item.get("fullSize", False))
        return {
            "honorType": 1,
            "id": honor_id,
            "level": level,
            "fullSize": full_size,
            "wordId": 0,
            "inverse": False,
            "useUnitVirtualSinger": False,
            "missionProgress": self.user_honor_mission_progress_for(honor_id),
            "MasterHonor": summarize_honor_master(honor),
            "MasterHonorGroup": summarize_honor_group(group),
            "candidatePaths": [str(path) for path in self.honor_candidate_paths(honor, group, full_size)]
            if honor and group
            else [],
        }

    def generate_bonds_honor_data(self, item: dict[str, Any]) -> dict[str, Any]:
        honor_id = content_data_id("bonds_honor", item)
        return {
            "honorType": 2,
            "id": honor_id,
            "level": self.user_bonds_honor_level_for(honor_id),
            "fullSize": bool_from_profile(item.get("fullSize", False)),
            "wordId": int(item.get("wordId", 0) or 0),
            "inverse": bool_from_profile(item.get("inverse", False)),
            "useUnitVirtualSinger": bool_from_profile(item.get("useUnitVirtualSinger", False)),
            "missionProgress": 0,
        }

    def generate_collection_data(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": content_data_id("collection", item),
            "targetId": int(item.get("targetId", 0) or 0),
        }

    def user_card_for(self, card_id: int) -> dict[str, Any] | None:
        for row in self.profile_context.get("userCards", []) or []:
            if int(row.get("cardId", 0) or 0) == card_id:
                return row
        return None

    def user_honor_level_for(self, honor_id: int) -> int:
        for row in self.profile_context.get("userHonors", []) or []:
            if (level := self._user_honor_row_level(row, honor_id)) is not None:
                return level
        for row in self.profile_context.get("userProfileHonors", []) or []:
            if (level := self._profile_honor_row_level(row, honor_id)) is not None:
                return level
        return 0

    @staticmethod
    def _list_profile_level(row: Any, honor_id: int) -> int | None:
        if not isinstance(row, list) or not row or _int_first(row[0]) != honor_id:
            return None
        return _int_first(row[1]) if len(row) > 1 else 0

    @classmethod
    def _user_honor_row_level(cls, row: Any, honor_id: int) -> int | None:
        if (level := cls._list_profile_level(row, honor_id)) is not None:
            return level
        if not isinstance(row, dict):
            return None
        row_id = _int_first(row.get("honorId", row.get("id", 0)))
        return _int_first(row.get("honorLevel", row.get("level", 0))) if row_id == honor_id else None

    @staticmethod
    def _profile_honor_row_level(row: Any, honor_id: int) -> int | None:
        if not isinstance(row, dict) or _int_first(row.get("honorId")) != honor_id:
            return None
        return _int_first(row.get("honorLevel"))

    def user_bonds_honor_level_for(self, bonds_honor_id: int) -> int:
        for row in self.profile_context.get("userBondsHonors", []) or []:
            if (level := self._bonds_honor_row_level(row, bonds_honor_id)) is not None:
                return level
        return 0

    @classmethod
    def _bonds_honor_row_level(cls, row: Any, bonds_honor_id: int) -> int | None:
        if (level := cls._list_profile_level(row, bonds_honor_id)) is not None:
            return level
        if not isinstance(row, dict):
            return None
        row_id = _int_first(row.get("bondsHonorId", row.get("honorId", row.get("id", 0))))
        return _int_first(row.get("bondsHonorLevel", row.get("level", 0))) if row_id == bonds_honor_id else None

    def user_honor_mission_progress_for(self, honor_id: int) -> int:
        for row in self.profile_context.get("userHonorMissions", []) or []:
            if (progress := self._honor_mission_row_progress(row, honor_id)) is not None:
                return progress
        return 0

    @staticmethod
    def _honor_mission_row_progress(row: Any, honor_id: int) -> int | None:
        if not isinstance(row, dict) or _int_first(row.get("honorId", row.get("id", 0))) != honor_id:
            return None
        return _int_first(row.get("missionProgress", row.get("progress", 0)))

    def shape_alpha_mask(self, path: Path, resource_file: str) -> Image.Image:
        key = (path, self.triangle_mode if resource_file == "triangle" else "asset")
        cached = self._shape_alpha_cache.get(key)
        if cached is not None:
            return cached

        mask_img = self.open_checked_image(path, "RGBA")
        if resource_file == "triangle" and self.triangle_mode == "sharp":
            alpha = sharp_triangle_alpha(mask_img.size)
        elif resource_file == "triangle" and self.triangle_mode == "sprite":
            field = mask_img.convert("RGB").getchannel("R")
            alpha = sdf_threshold_alpha(
                ImageChops.multiply(field, largest_component_mask(field, threshold=16)), 0.5, 0.02
            )
        else:
            alpha = mask_img.getchannel("A")
        self._shape_alpha_cache[key] = alpha
        return alpha

    def shape_distance_field(self, path: Path, resource_file: str) -> Image.Image:
        mode = self.triangle_mode if resource_file == "triangle" else "asset"
        key = (path, mode, self.shape_sdf_source)
        cached = self._shape_field_cache.get(key)
        if cached is not None:
            return cached

        if self.shape_sdf_source == "alpha":
            field = self.shape_alpha_mask(path, resource_file)
        else:
            if resource_file == "triangle" and self.triangle_mode == "sharp":
                field = sharp_triangle_distance(self.open_checked_image(path, "RGBA").size)
            elif resource_file == "triangle" and self.triangle_mode == "sprite":
                source = self.open_checked_image(path, "RGB").getchannel("R")
                field = ImageChops.multiply(source, largest_component_mask(source, threshold=16))
            else:
                field = self.open_checked_image(path, "RGB").getchannel("R")
        self._shape_field_cache[key] = field
        return field

    def shape_sdf_alpha(self, path: Path, resource_file: str, threshold: float, softness: float) -> Image.Image:
        key = (
            path,
            self.triangle_mode if resource_file == "triangle" else "asset",
            self.shape_sdf_source,
            round(threshold, 6),
            round(softness, 6),
        )
        cached = self._sdf_alpha_cache.get(key)
        if cached is not None:
            return cached
        alpha = sdf_threshold_alpha(self.shape_distance_field(path, resource_file), threshold, softness)
        self._sdf_alpha_cache[key] = alpha
        return alpha

    def shape_shader_basis(self, path: Path, resource_file: str) -> tuple[Any, Any, Any]:
        key = (
            path,
            self.triangle_mode if resource_file == "triangle" else "asset",
            self.shape_sdf_source,
        )
        cached = self._shape_shader_basis_cache.get(key)
        if cached is not None:
            return cached

        import numpy as np

        field = np.asarray(self.shape_distance_field(path, resource_file), dtype=np.float32) / 255.0
        alpha = np.asarray(self.shape_alpha_mask(path, resource_file), dtype=np.float32) / 255.0
        grad_y, grad_x = np.gradient(field)
        fwidth = np.abs(grad_x) + np.abs(grad_y)
        cached = (field, alpha, fwidth)
        self._shape_shader_basis_cache[key] = cached
        return cached

    def shape_shader_arrays(
        self,
        path: Path,
        resource_file: str,
        output_size: tuple[int, int] | None,
    ) -> tuple[Any, Any, Any]:
        source_size = self.shape_alpha_mask(path, resource_file).size
        if output_size is None or output_size == source_size:
            return self.shape_shader_basis(path, resource_file)

        import numpy as np

        field_img = self.shape_distance_field(path, resource_file).resize(output_size, Image.Resampling.BILINEAR)
        alpha_img = self.shape_alpha_mask(path, resource_file).resize(output_size, Image.Resampling.BILINEAR)
        field = np.asarray(field_img, dtype=np.float32) / 255.0
        alpha = np.asarray(alpha_img, dtype=np.float32) / 255.0
        grad_y, grad_x = np.gradient(field)
        fwidth = np.abs(grad_x) + np.abs(grad_y)
        return field, alpha, fwidth

    def render_distance_field_shape(
        self,
        path: Path,
        resource_file: str,
        fill_color: str,
        fill_alpha: float,
        outline_color: str,
        outline_alpha: float,
        outline_size: float,
        output_size: tuple[int, int] | None = None,
    ) -> Image.Image:
        import numpy as np

        field, texture_alpha, fwidth = self.shape_shader_arrays(path, resource_file, output_size)
        native_outline_size = max(0.0, min(1.0, outline_size))
        native_outer_fill_ratio = native_outline_size * SHAPE_NATIVE_OUTLINE_FILL_RATIO_FACTOR
        outer_fill_ratio = max(
            0.0,
            min(1.0, native_outer_fill_ratio * self.shape_sdf_ratio_scale * self.shape_sdf_outer_factor),
        )
        face_dilate = max(
            -1.0,
            min(
                1.0,
                native_outline_size * self.shape_sdf_ratio_scale * self.shape_sdf_face_factor,
            ),
        )
        sharpness = 0.0
        softness = max(0.0, self.shape_sdf_softness)

        half_width = softness * 0.5 + fwidth * (1.0 - sharpness)
        edge0 = 0.5 - half_width
        edge1 = 0.5 + half_width
        span = np.maximum(edge1 - edge0, 1.0e-6)
        t = np.clip((field - edge0) / span, 0.0, 1.0)
        smooth = t * t * (3.0 - 2.0 * t)

        face = np.where(smooth >= 0.899999976, texture_alpha * smooth * fill_alpha, 0.0)
        outline_distance = field + smooth * 0.5 + face_dilate * 0.5
        outline_t = np.clip(outline_distance * 10.0, 0.0, 1.0)
        outline_smooth = outline_t * outline_t * (3.0 - 2.0 * outline_t)
        outline = texture_alpha * outline_smooth * outline_alpha
        outline_mask = (outline_distance >= (1.0 - outer_fill_ratio)) & (outline_distance < 1.0)
        alpha = np.where(outline_mask, outline, face)

        fill_rgba = np.array(hex_to_rgba(fill_color, 1.0), dtype=np.float32)
        outline_rgba = np.array(hex_to_rgba(outline_color, 1.0), dtype=np.float32)
        rgb = np.where(outline_mask[:, :, None], outline_rgba[:3], fill_rgba[:3])
        rgba = np.empty((*field.shape, 4), dtype=np.uint8)
        rgba[:, :, :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
        rgba[:, :, 3] = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")

    def composite_transformed(
        self,
        canvas: Image.Image,
        local: tuple[Image.Image, tuple[float, float]] | tuple[Image.Image, tuple[float, float], bool],
        object_data: dict[str, Any],
        content_kind: str | None = None,
        allow_rotation_supersample: bool = False,
    ) -> None:
        prepared = self.prepare_transformed_layer(local, object_data, content_kind, allow_rotation_supersample)
        if prepared is not None:
            canvas.alpha_composite(prepared.image, prepared.xy)

    def resize_layer_for_transform(
        self, layer: Image.Image, size: tuple[int, int], resample: Image.Resampling
    ) -> Image.Image:
        size = ensure_raster_size(size, max_pixels=self.max_layer_pixels, label="custom profile transformed layer")
        if self.premultiply_alpha_transforms:
            return resize_rgba_premul(layer, size, resample)
        return layer.resize(size, resample)

    def affine_transform_layer(
        self,
        layer: Image.Image,
        size: tuple[int, int],
        data: tuple[float, float, float, float, float, float],
        resample: Image.Resampling,
    ) -> Image.Image:
        size = ensure_raster_size(size, max_pixels=self.max_layer_pixels, label="custom profile affine layer")
        if self.premultiply_alpha_transforms:
            return transform_rgba_premul(layer, size, Image.Transform.AFFINE, data, resample)
        return layer.transform(size, Image.Transform.AFFINE, data, resample, fillcolor=(0, 0, 0, 0))

    def rotate_layer_for_transform(
        self, layer: Image.Image, pivot: tuple[float, float], angle: float
    ) -> tuple[Image.Image, tuple[float, float]]:
        return rotate_layer_about_pivot(layer, pivot, angle, premultiply_alpha=self.premultiply_alpha_transforms)

    def layer_transform_inputs(
        self,
        local: tuple[Image.Image, tuple[float, float]] | tuple[Image.Image, tuple[float, float], bool],
        object_data: dict[str, Any],
        content_kind: str | None = None,
    ) -> LayerTransformInputs:
        layer, pivot = local[0], local[1]
        scale_consumed = len(local) >= 3 and bool(local[2])
        if content_kind not in {"general", "honor", "bonds_honor"}:
            layer, pivot = trim_layer_to_content(layer, pivot)
        scale = object_data.get("scale", {})
        sx = float(scale.get("x") or 1.0)
        sy = float(scale.get("y") or sx or 1.0)
        if scale_consumed:
            sx = 1.0
            sy = 1.0
        angle = self.rotation_sign * unity_rotation_degrees(object_data.get("rotation", {}))
        anchor = self.unity_point(object_data.get("position", {}))
        return LayerTransformInputs(
            layer=layer,
            pivot=pivot,
            object_scale=(sx, sy),
            position_scale=(self.position_scale_x, self.position_scale_y),
            angle=angle,
            anchor=anchor,
        )

    def prepare_transformed_layer(
        self,
        local: tuple[Image.Image, tuple[float, float]] | tuple[Image.Image, tuple[float, float], bool],
        object_data: dict[str, Any],
        content_kind: str | None = None,
        allow_rotation_supersample: bool = False,
    ) -> PreparedLayer | None:
        inputs = self.layer_transform_inputs(local, object_data, content_kind)
        layer, pivot = inputs.layer, inputs.pivot
        sx, sy = inputs.object_scale
        if not math.isclose(sx, 1.0, abs_tol=1.0e-9) or not math.isclose(sy, 1.0, abs_tol=1.0e-9):
            new_w = max(1, round(layer.width * sx))
            new_h = max(1, round(layer.height * sy))
            layer = self.resize_layer_for_transform(layer, (new_w, new_h), Image.Resampling.BICUBIC)
            pivot = (pivot[0] * sx, pivot[1] * sy)

        # Object positions are stored in the profile card's Unity units, while
        # the offline screenshot target is the normalized profile capture size.
        # Scale the rendered local layer by the same unit-to-pixel ratio before
        # applying the RectTransform rotation, matching the Canvas capture path.
        psx, psy = inputs.position_scale
        if abs(psx - 1.0) >= 1.0e-6 or abs(psy - 1.0) >= 1.0e-6:
            new_w = max(1, round(layer.width * psx))
            new_h = max(1, round(layer.height * psy))
            layer = self.resize_layer_for_transform(layer, (new_w, new_h), Image.Resampling.BICUBIC)
            pivot = (pivot[0] * psx, pivot[1] * psy)

        angle = inputs.angle
        x, y = inputs.anchor
        if self.clip_canvas_transform:
            return self.prepare_canvas_clipped_transformed_layer(layer, pivot, angle, x, y, allow_rotation_supersample)

        return self.prepare_full_transformed_layer(layer, pivot, angle, x, y, allow_rotation_supersample)

    def prepare_full_transformed_layer(
        self,
        layer: Image.Image,
        pivot: tuple[float, float],
        angle: float,
        x: float,
        y: float,
        allow_rotation_supersample: bool,
    ) -> PreparedLayer:
        rotation_supersample = max(1.0, LAYER_ROTATION_SUPERSAMPLE)
        if allow_rotation_supersample and rotation_supersample > 1.0 and abs(angle % 360.0) >= 1.0e-6:
            hi_w = max(1, round(layer.width * rotation_supersample))
            hi_h = max(1, round(layer.height * rotation_supersample))
            hi_layer = self.resize_layer_for_transform(layer, (hi_w, hi_h), Image.Resampling.BICUBIC)
            hi_pivot = (pivot[0] * rotation_supersample, pivot[1] * rotation_supersample)
            rotated_hi, rotated_hi_pivot = self.rotate_layer_for_transform(hi_layer, hi_pivot, angle)
            out_w = max(1, round(rotated_hi.width / rotation_supersample))
            out_h = max(1, round(rotated_hi.height / rotation_supersample))
            rotated = self.resize_layer_for_transform(rotated_hi, (out_w, out_h), Image.Resampling.LANCZOS)
            rotated_pivot = (rotated_hi_pivot[0] / rotation_supersample, rotated_hi_pivot[1] / rotation_supersample)
        else:
            rotated, rotated_pivot = self.rotate_layer_for_transform(layer, pivot, angle)
        paste_x = round(x - rotated_pivot[0])
        paste_y = round(y - rotated_pivot[1])
        return PreparedLayer(rotated, (paste_x, paste_y))

    def prepare_canvas_clipped_transformed_layer(
        self,
        layer: Image.Image,
        pivot: tuple[float, float],
        angle: float,
        x: float,
        y: float,
        allow_rotation_supersample: bool,
    ) -> PreparedLayer | None:
        angle = angle % 360.0
        if abs(angle) < 1.0e-9:
            paste_x = round(x - pivot[0])
            paste_y = round(y - pivot[1])
            src_left = max(0, -paste_x)
            src_top = max(0, -paste_y)
            src_right = min(layer.width, self.canvas_w - paste_x)
            src_bottom = min(layer.height, self.canvas_h - paste_y)
            if src_left >= src_right or src_top >= src_bottom:
                return None
            if src_left == 0 and src_top == 0 and src_right == layer.width and src_bottom == layer.height:
                return PreparedLayer(layer, (paste_x, paste_y))
            return PreparedLayer(
                layer.crop((src_left, src_top, src_right, src_bottom)),
                (paste_x + src_left, paste_y + src_top),
            )

        theta = math.radians(angle)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        corners = (
            (0.0, 0.0),
            (float(layer.width), 0.0),
            (float(layer.width), float(layer.height)),
            (0.0, float(layer.height)),
        )
        rotated_corners = [
            (
                x + (px - pivot[0]) * cos_t - (py - pivot[1]) * sin_t,
                y + (px - pivot[0]) * sin_t + (py - pivot[1]) * cos_t,
            )
            for px, py in corners
        ]
        pad = 2
        left = max(0, math.floor(min(px for px, _ in rotated_corners)) - pad)
        right = min(self.canvas_w, math.ceil(max(px for px, _ in rotated_corners)) + pad)
        top = max(0, math.floor(min(py for _, py in rotated_corners)) - pad)
        bottom = min(self.canvas_h, math.ceil(max(py for _, py in rotated_corners)) + pad)
        if left >= right or top >= bottom:
            return None

        inv_theta = -theta
        a = math.cos(inv_theta)
        b = -math.sin(inv_theta)
        d = math.sin(inv_theta)
        e = math.cos(inv_theta)
        c = pivot[0] + a * (left - x) + b * (top - y)
        f = pivot[1] + d * (left - x) + e * (top - y)
        out_w = max(1, right - left)
        out_h = max(1, bottom - top)

        rotation_supersample = max(1.0, LAYER_ROTATION_SUPERSAMPLE)
        if allow_rotation_supersample and rotation_supersample > 1.0:
            hi_w = max(1, round(out_w * rotation_supersample))
            hi_h = max(1, round(out_h * rotation_supersample))
            hi = self.affine_transform_layer(
                layer,
                (hi_w, hi_h),
                (
                    a / rotation_supersample,
                    b / rotation_supersample,
                    c,
                    d / rotation_supersample,
                    e / rotation_supersample,
                    f,
                ),
                Image.Resampling.BICUBIC,
            )
            return PreparedLayer(
                self.resize_layer_for_transform(hi, (out_w, out_h), Image.Resampling.LANCZOS), (left, top)
            )

        transformed = self.affine_transform_layer(
            layer,
            (out_w, out_h),
            (a, b, c, d, e, f),
            Image.Resampling.BICUBIC,
        )
        return PreparedLayer(transformed, (left, top))

    def unity_point(self, position: dict[str, Any]) -> tuple[float, float]:
        return (
            self.origin_x + float(position.get("x", 0)) * self.position_scale_x,
            self.origin_y - float(position.get("y", 0)) * self.position_scale_y,
        )

    def render_shape(self, item: dict[str, Any]) -> tuple[Image.Image, tuple[float, float], bool] | None:
        resource = self.shapes.get(int(item.get("id", 0)), {})
        path = self.shape_resource_path(resource)
        if not path:
            return None
        resource_file = str(resource.get("fileName", "")).strip().lower()
        alpha_mask = self.shape_alpha_mask(path, resource_file)
        size = ensure_raster_size(
            alpha_mask.size,
            max_pixels=self.max_layer_pixels,
            label=f"custom profile shape {resource_file or path.name}",
        )
        fill_color = self.colors.get(int(item.get("colorId", 0)), "#ffffff")
        fill_alpha_value = float(item.get("alpha", 1.0))
        fill = Image.new("RGBA", size, hex_to_rgba(fill_color, fill_alpha_value))
        outline_alpha = float(item.get("outlineAlpha", 0.0))
        outline_size = float(item.get("outlineSize", 0.0))
        if self.shape_outline_mode == "sdf":
            output_size = None
            scale_consumed = False
            if self.shape_sdf_screen_fwidth:
                scale = item.get("objectData", {}).get("scale", {})
                sx = float(scale.get("x") or 1.0)
                sy = float(scale.get("y") or sx or 1.0)
                output_size = ensure_raster_size(
                    (max(1, round(size[0] * sx)), max(1, round(size[1] * sy))),
                    max_pixels=self.max_layer_pixels,
                    label=f"custom profile scaled shape {resource_file or path.name}",
                )
                scale_consumed = True
            base = self.render_distance_field_shape(
                path,
                resource_file,
                fill_color,
                fill_alpha_value,
                self.colors.get(int(item.get("outlineColorId", 0)), "#ffffff"),
                outline_alpha,
                outline_size,
                output_size,
            )
            return base, (base.width / 2, base.height / 2), scale_consumed
        fill.putalpha(ImageChops.multiply(alpha_mask, Image.new("L", size, round(255 * fill_alpha_value))))
        if outline_alpha > 0 and outline_size > 0:
            if self.shape_outline_mode == "dilate":
                radius = max(1, round(outline_size * SHAPE_OUTLINE_SCALE_FACTOR * max(size)))
                kernel = radius * 2 + 1
                out_alpha = alpha_mask.filter(ImageFilter.MaxFilter(kernel))
                fill_alpha = alpha_mask
                ring_alpha = ImageChops.subtract(out_alpha, fill_alpha)
                outline = Image.new(
                    "RGBA",
                    size,
                    hex_to_rgba(self.colors.get(int(item.get("outlineColorId", 0)), "#ffffff"), outline_alpha),
                )
                outline.putalpha(ImageChops.multiply(ring_alpha, Image.new("L", size, round(255 * outline_alpha))))
                base = Image.new("RGBA", size, (0, 0, 0, 0))
                base.alpha_composite(outline, (0, 0))
                base.alpha_composite(fill, (0, 0))
                return base, (base.width / 2, base.height / 2), False
            factor = 1.0 + outline_size * SHAPE_OUTLINE_SCALE_FACTOR
            out_size = (round(size[0] * factor), round(size[1] * factor))
            out_alpha = alpha_mask.resize(out_size, Image.Resampling.BICUBIC)
            fill_offset = ((out_size[0] - size[0]) // 2, (out_size[1] - size[1]) // 2)
            if self.shape_outline_mode == "ring":
                fill_alpha = Image.new("L", out_size, 0)
                fill_alpha.paste(alpha_mask, fill_offset)
                out_alpha = ImageChops.subtract(out_alpha, fill_alpha)
            outline = Image.new(
                "RGBA",
                out_size,
                hex_to_rgba(self.colors.get(int(item.get("outlineColorId", 0)), "#ffffff"), outline_alpha),
            )
            outline.putalpha(ImageChops.multiply(out_alpha, Image.new("L", out_size, round(255 * outline_alpha))))
            base = Image.new("RGBA", out_size, (0, 0, 0, 0))
            base.alpha_composite(outline, (0, 0))
            base.alpha_composite(fill, fill_offset)
            return base, (base.width / 2, base.height / 2), False
        return fill, (fill.width / 2, fill.height / 2), False

    def render_text(self, item: dict[str, Any]) -> tuple[Image.Image, tuple[float, float]] | None:
        data = self.generate_text_data(item)
        raw_text = data.text
        if not raw_text.strip():
            return None
        font_name = self.text_fonts.get(data.font_id, "FOT-RodinNTLGPro-DB") or "FOT-RodinNTLGPro-DB"
        mesh_state = self.update_text_mesh_state(data, font_name)
        font_path = self.font_path_for(font_name)
        base_size = mesh_state.font_size
        base_style = self.base_text_style(mesh_state)
        tokens = parse_tmp_text(raw_text, base_style)
        lines = split_runs_by_line(tokens)
        styled_lines = split_runs_by_line_with_style(tokens, base_style)
        if not lines:
            return None
        if self.text_layout == "tmp":
            return self.render_tmp_text_box(item, font_name, font_path, base_style, styled_lines)
        outline_color = mesh_state.underlay_color
        outline_dilate = self.decorative_outline_dilate(item, mesh_state.underlay_dilate)
        outline_width = max(0, round(outline_dilate * base_size * self.tmp_pillow_stroke_factor))
        metrics = self.measure_pil_text_layout(
            lines,
            font_name,
            font_path,
            base_size,
            mesh_state.tmp_line_spacing,
        )
        pad = self.text_pad(base_size, outline_width)
        content_w = max(1.0, metrics.max_x - metrics.min_x)
        image_size = ensure_raster_size(
            (math.ceil(content_w + pad * 2), math.ceil(max(1.0, metrics.total_height) + pad * 2)),
            max_pixels=self.max_layer_pixels,
            label="custom profile text layer",
        )
        img = Image.new("RGBA", image_size, (0, 0, 0, 0))
        self.draw_pil_text_layout(
            img,
            metrics,
            font_name,
            font_path,
            pad,
            outline_color,
            outline_width,
            outline_dilate,
        )
        return img, self.pil_text_pivot(img, pad, metrics.min_x)

    def base_text_style(self, mesh_state: TMPUpdateMeshState) -> TextStyle:
        return TextStyle(
            color=mesh_state.font_color,
            alpha=1.0,
            size=mesh_state.font_size,
            scale_x=1.0,
            cspace=0.0,
            mspace=None,
            indent=0.0,
            line_indent=0.0,
            line_height=None,
            rotate=0.0,
            voffset=0.0,
            mark_color=None,
            bold=False,
            italic=False,
            underline=False,
            strike=False,
        )

    def measure_pil_text_layout(
        self,
        lines: list[list[TextRun]],
        font_name: str,
        font_path: Path,
        base_size: float,
        line_spacing: float,
    ) -> PILTextLayoutMetrics:
        metrics: list[PILTextLineMetrics] = []
        min_x = 0.0
        max_x = 1.0
        total_h = 0.0
        for line in lines:
            line_metrics, line_h, line_min_x, line_max_x = self.measure_pil_text_line(
                line,
                font_name,
                font_path,
                base_size,
            )
            min_x = min(min_x, line_min_x)
            max_x = max(max_x, line_max_x)
            metrics.append(PILTextLineMetrics(line_metrics, total_h, line_h))
            line_style_size = max((run.style.size for run in line), default=base_size)
            total_h += self.apply_tmp_line_spacing(line_h, line_spacing, font_name, line_style_size)
        return PILTextLayoutMetrics(metrics, min_x, max_x, total_h)

    def measure_pil_text_line(
        self,
        line: list[TextRun],
        font_name: str,
        font_path: Path,
        base_size: float,
    ) -> tuple[list[tuple[TextRun, float, float]], float, float, float]:
        line_metrics: list[tuple[TextRun, float, float]] = []
        line_h = base_size * self.tmp_font_scale
        min_x = 0.0
        max_x = 1.0
        x = 0.0
        for run in line:
            scaled_size = run.style.size * self.tmp_font_scale
            raw_w, raw_h, raw_advance = self.measure_pil_text_run(run, font_name, font_path, scaled_size)
            scale_x = self.tmp_mesh_layout_scale_x(run.style)
            run_w = raw_w * scale_x
            run_h = raw_h * self.tmp_layout_scale_y(run.style)
            line_h = self.pil_text_line_height(line_h, run_h, run.style)
            line_metrics.append((run, x, run_w))
            min_x = min(min_x, x)
            max_x = max(max_x, x + run_w)
            spacing = self.tmp_character_spacing_advance(run.style, font_name, scaled_size)
            x += raw_advance * scale_x + len(run.text) * spacing
            min_x = min(min_x, x)
            max_x = max(max_x, x)
        return line_metrics, max(1.0, line_h), min_x, max_x

    def measure_pil_text_run(
        self,
        run: TextRun,
        font_name: str,
        font_path: Path,
        scaled_size: float,
    ) -> tuple[float, float, float]:
        font = load_font(font_path, scaled_size)
        if self.use_em_block(run):
            source_metrics = self.tmp_source_block_metrics(font_name, run, scaled_size)
            raw_width = source_metrics.advance if source_metrics is not None else scaled_size
            return raw_width, scaled_size, raw_width
        measure = self.measure_tmp_run(font, run, font_name, scaled_size)
        return max(1.0, measure.advance), max(1.0, measure.visual_height), measure.advance

    def pil_text_line_height(self, line_height: float, run_height: float, style: TextStyle) -> float:
        if style.line_height is not None:
            return self.tmp_explicit_line_height(style.line_height)
        return max(line_height, run_height)

    def draw_pil_text_layout(
        self,
        image: Image.Image,
        metrics: PILTextLayoutMetrics,
        font_name: str,
        font_path: Path,
        pad: int,
        outline_color: str,
        outline_width: int,
        outline_dilate: float,
    ) -> None:
        for line in metrics.lines:
            for run, x, _ in line.runs:
                self.draw_run(
                    image,
                    font_name,
                    font_path,
                    run,
                    pad + x - metrics.min_x,
                    pad + line.y,
                    line.height,
                    outline_color,
                    outline_width,
                    outline_dilate,
                )

    def pil_text_pivot(self, image: Image.Image, pad: int, min_x: float) -> tuple[float, float]:
        if self.text_pivot == "center":
            return image.width / 2, image.height / 2
        return pad - min_x, image.height / 2

    def render_tmp_text_box(
        self,
        item: dict[str, Any],
        font_name: str,
        font_path: Path,
        base_style: TextStyle,
        lines: list[StyledLine],
    ) -> tuple[Image.Image, tuple[float, float]] | None:
        text_data = self.generate_text_data(item)
        mesh_state = self.update_text_mesh_state(text_data, font_name)
        base_size = mesh_state.font_size
        line_spacing = mesh_state.tmp_line_spacing
        outline_color = mesh_state.underlay_color
        outline_dilate = self.decorative_outline_dilate(item, mesh_state.underlay_dilate)
        outline_width = max(0, round(outline_dilate * base_size * self.tmp_pillow_stroke_factor))
        dominant_size = max((line.style.size for line in lines), default=base_size)

        align_type = mesh_state.align
        horizontal_align = tmp_horizontal_alignment(align_type)
        vertical_align = tmp_vertical_alignment(align_type)

        layout_lines = [line for line in lines if self.include_empty_lines or line.runs]
        layouts = self.resolve_tmp_text_box_layouts(
            layout_lines,
            font_name,
            font_path,
            base_size,
            line_spacing,
            dominant_size,
            outline_dilate,
        )
        if layouts is None:
            return None
        native_text_layout, mesh_text_layout = layouts
        metrics = [
            (
                line.styled_line,
                line.run_metrics,
                line.y_down,
                line.line_height,
                line.width,
            )
            for line in mesh_text_layout.lines
        ]
        native_layout = (
            mesh_text_layout.line_layout if self.text_vertical_mode in {"tmp-native", "tmp-native-top"} else None
        )
        total_h = mesh_text_layout.accumulated_line_height
        content_h = native_text_layout.content_height
        pad = self.text_pad(base_size, outline_width)
        box_w, box_h = self.tmp_text_box_size(
            native_text_layout.dominant_size,
            native_text_layout.preferred_width,
            content_h,
        )
        native_baselines = (
            self.tmp_native_baseline_downs(
                native_layout,
                box_h,
                "top" if self.text_vertical_mode == "tmp-native-top" else vertical_align,
            )
            if native_layout is not None
            else None
        )
        content_y = 0.0 if native_baselines is not None else tmp_content_offset_y(vertical_align, box_h, total_h)
        mesh_bounds = (
            self.tmp_native_mesh_pixel_bounds(
                mesh_text_layout,
                native_baselines,
                horizontal_align,
                box_w,
                box_h,
            )
            if native_baselines is not None
            else (0.0, 0.0, box_w, box_h)
        )
        mesh_left, mesh_top, mesh_right, mesh_bottom = mesh_bounds
        rect_origin_x = pad - mesh_left
        rect_origin_y = pad - mesh_top
        img_w = math.ceil(mesh_right - mesh_left + pad * 2)
        img_h = math.ceil(mesh_bottom - mesh_top + pad * 2)
        image_size = ensure_raster_size(
            (max(1, img_w), max(1, img_h)),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP text layer",
        )
        img = Image.new("RGBA", image_size, (0, 0, 0, 0))
        self.record_tmp_layout_audit(
            item,
            text_data,
            mesh_state,
            metrics,
            font_name,
            font_path,
            native_text_layout.preferred_width,
            native_text_layout.preferred_height,
            content_h,
            total_h,
            box_w,
            box_h,
            native_layout,
            native_baselines,
            native_text_layout,
            mesh_text_layout,
            mesh_bounds,
            (rect_origin_x, rect_origin_y),
            (img.width, img.height),
        )

        self.draw_tmp_text_box_content(
            img,
            font_name,
            font_path,
            mesh_text_layout,
            native_baselines,
            horizontal_align,
            box_w,
            rect_origin_x,
            rect_origin_y,
            content_y,
            outline_color,
            outline_width,
            outline_dilate,
        )

        return img, (rect_origin_x + box_w / 2, rect_origin_y + box_h / 2)

    def resolve_tmp_text_box_layouts(
        self,
        layout_lines: list[StyledLine],
        font_name: str,
        font_path: Path,
        base_size: float,
        line_spacing: float,
        dominant_size: float,
        outline_dilate: float,
        *,
        source_metrics_only: bool = False,
    ) -> tuple[TMPNativeTextLayout, TMPNativeTextLayout] | None:
        native_text_layout = self.tmp_native_text_layout(
            layout_lines,
            font_name,
            font_path,
            base_size,
            line_spacing,
            dominant_size,
            "preferred",
            outline_dilate,
            None,
            source_metrics_only=source_metrics_only,
        )
        percent_margin_width = self.tmp_resolve_percent_indent_margin_width(
            layout_lines,
            font_name,
            font_path,
            base_size,
            line_spacing,
            dominant_size,
            outline_dilate,
            native_text_layout,
            source_metrics_only=source_metrics_only,
        )
        if percent_margin_width is not None:
            native_text_layout = self.tmp_native_text_layout(
                layout_lines,
                font_name,
                font_path,
                base_size,
                line_spacing,
                dominant_size,
                "preferred",
                outline_dilate,
                percent_margin_width,
                source_metrics_only=source_metrics_only,
            )
        if native_text_layout is None:
            return None
        mesh_text_layout = self.tmp_native_text_layout(
            layout_lines,
            font_name,
            font_path,
            base_size,
            line_spacing,
            dominant_size,
            "mesh",
            outline_dilate,
            percent_margin_width,
            source_metrics_only=source_metrics_only,
        )
        if mesh_text_layout is None:
            return None
        return native_text_layout, mesh_text_layout

    def draw_tmp_text_box_content(
        self,
        image: Image.Image,
        font_name: str,
        font_path: Path,
        mesh_text_layout: TMPNativeTextLayout,
        native_baselines: list[float] | None,
        horizontal_align: str,
        box_w: float,
        rect_origin_x: float,
        rect_origin_y: float,
        content_y: float,
        outline_color: str,
        outline_width: int,
        outline_dilate: float,
    ) -> None:
        if native_baselines is not None and self.tmp_text_render_mode == "sdf":
            self.draw_tmp_native_characters(
                image,
                font_name,
                font_path,
                mesh_text_layout,
                native_baselines,
                horizontal_align,
                box_w,
                rect_origin_x,
                rect_origin_y,
                outline_color,
                outline_width,
                outline_dilate,
            )
            return
        self.draw_tmp_text_box_runs(
            image,
            font_name,
            font_path,
            mesh_text_layout,
            native_baselines,
            horizontal_align,
            box_w,
            rect_origin_x,
            rect_origin_y,
            content_y,
            outline_color,
            outline_width,
            outline_dilate,
        )

    def draw_tmp_text_box_runs(
        self,
        image: Image.Image,
        font_name: str,
        font_path: Path,
        mesh_text_layout: TMPNativeTextLayout,
        native_baselines: list[float] | None,
        horizontal_align: str,
        box_w: float,
        rect_origin_x: float,
        rect_origin_y: float,
        content_y: float,
        outline_color: str,
        outline_width: int,
        outline_dilate: float,
    ) -> None:
        for line_index, line_info in enumerate(mesh_text_layout.lines):
            line_x = tmp_line_offset_x(horizontal_align, box_w, line_info.width)
            for run, x, _ in line_info.run_metrics:
                draw_x = rect_origin_x + line_x + x
                if native_baselines is not None:
                    self.draw_run_at_baseline(
                        image,
                        font_name,
                        font_path,
                        run,
                        draw_x,
                        rect_origin_y + native_baselines[line_index],
                        outline_color,
                        outline_width,
                        outline_dilate,
                    )
                    continue
                self.draw_run(
                    image,
                    font_name,
                    font_path,
                    run,
                    draw_x,
                    rect_origin_y + content_y + line_info.y_down,
                    line_info.line_height,
                    outline_color,
                    outline_width,
                    outline_dilate,
                )

    def _tmp_append_native_layout_character(
        self,
        char: str,
        style: TextStyle,
        line_index: int,
        first_character_index: int,
        line_state: _TMPNativeLineState,
        state: _TMPNativeLayoutState,
        config: _TMPNativeLayoutConfig,
    ) -> TMPNativeCharacterInfo:
        (
            char_info,
            line_state.x_advance,
            line_state.max_ascender,
            line_state.max_descender,
            line_state.visible_character_count,
        ) = self.tmp_native_layout_character(
            char,
            style,
            config.font_name,
            config.font_path,
            line_index,
            len(state.characters),
            line_state.x_advance,
            state.line_offset,
            first_character_index,
            line_state.max_ascender,
            line_state.max_descender,
            line_state.visible_character_count,
            config.layout_mode,
            config.current_em_scale,
            config.outline_dilate,
            source_metrics_only=config.source_metrics_only,
        )
        state.characters.append(char_info)
        return char_info

    def _tmp_native_layout_runs(
        self,
        line: StyledLine,
        line_index: int,
        first_character_index: int,
        state: _TMPNativeLayoutState,
        config: _TMPNativeLayoutConfig,
    ) -> _TMPNativeLineState:
        line_state = _TMPNativeLineState(self.tmp_native_line_initial_x(line, config.margin_width))
        for run_index, run in enumerate(line.runs):
            for char in run.text:
                if char in {"\r", "\n"}:
                    continue
                line_state.has_character = True
                self._tmp_append_native_layout_character(
                    char,
                    run.style,
                    line_index,
                    first_character_index,
                    line_state,
                    state,
                    config,
                )
            next_run = line.runs[run_index + 1] if run_index + 1 < len(line.runs) else None
            next_style = next_run.style if next_run is not None else line.style
            if self.tmp_closes_cspace_before_next_run(run.style, next_style):
                line_state.x_advance -= self.tmp_cspace_advance(run.style.cspace)
                if state.characters:
                    state.characters[-1] = replace(state.characters[-1], x_advance=line_state.x_advance)
        return line_state

    def _tmp_native_layout_line_breaks(
        self,
        line: StyledLine,
        line_index: int,
        first_character_index: int,
        line_state: _TMPNativeLineState,
        state: _TMPNativeLayoutState,
        config: _TMPNativeLayoutConfig,
    ) -> None:
        for break_index in range(line.trailing_newline_count):
            line_state.has_character = True
            char_info = self._tmp_append_native_layout_character(
                "\n",
                line.style,
                line_index,
                first_character_index,
                line_state,
                state,
                config,
            )
            line_state.line_break_adjusted_ascender = char_info.adjusted_ascender
            if break_index + 1 < line.trailing_newline_count:
                line_state.x_advance = self.tmp_native_line_initial_x(line, config.margin_width)

    def _tmp_resolve_native_line_extents(
        self,
        line: StyledLine,
        line_state: _TMPNativeLineState,
        config: _TMPNativeLayoutConfig,
    ) -> None:
        if not line_state.has_character:
            line_state.max_ascender, line_state.max_descender = self.tmp_native_style_extents(
                config.font_name,
                line.style,
            )
        if line_state.max_ascender <= TMP_LARGE_NEGATIVE_FLOAT:
            line_state.max_ascender = 0.0
        if line_state.max_descender >= TMP_LARGE_POSITIVE_FLOAT:
            line_state.max_descender = 0.0

    @staticmethod
    def _tmp_adjust_native_line_offset(
        state: _TMPNativeLayoutState,
        line_state: _TMPNativeLineState,
    ) -> None:
        if state.line_offset <= 0.0 or state.is_driven_line_spacing:
            return
        baseline_adjustment_delta = line_state.max_ascender - state.start_of_line_ascender
        if abs(baseline_adjustment_delta) > 0.01:
            state.element_descender -= baseline_adjustment_delta
            state.line_offset += baseline_adjustment_delta

    @staticmethod
    def _tmp_record_native_line(
        line: StyledLine,
        line_index: int,
        first_character_index: int,
        run_metrics: list[tuple[TextRun, float, float]],
        line_width: float,
        line_min_x: float,
        line_max_x: float,
        line_state: _TMPNativeLineState,
        state: _TMPNativeLayoutState,
        config: _TMPNativeLayoutConfig,
    ) -> None:
        baseline = -state.line_offset
        line_ascender = line_state.max_ascender - state.line_offset
        line_descender = line_state.max_descender - state.line_offset
        # TMP keeps m_ElementDescender as the current generated line's descender,
        # not the union minimum. Later lines can move upward with negative spacing.
        state.element_descender = line_descender
        if state.max_text_ascender is None:
            state.max_text_ascender = line_ascender
        line_height = line_ascender - line_descender + config.raw_line_gap * config.base_scale
        line_width = max(1.0, line_width)
        state.rendered_width = max(state.rendered_width, line_width)
        state.accumulated_line_height = max(state.accumulated_line_height, -baseline + line_height)
        state.lines.append(
            TMPNativeLineInfo(
                index=line_index,
                styled_line=line,
                run_metrics=run_metrics,
                first_character_index=first_character_index,
                last_character_index=max(first_character_index, len(state.characters) - 1),
                visible_character_count=line_state.visible_character_count,
                baseline=baseline,
                ascender=line_ascender,
                descender=line_descender,
                line_height=line_height,
                width=line_width,
                max_advance=line_width,
                line_extents_min_x=line_min_x,
                line_extents_max_x=line_max_x,
                y_down=-baseline,
            )
        )

    def _tmp_advance_native_line(
        self,
        line: StyledLine,
        line_state: _TMPNativeLineState,
        state: _TMPNativeLayoutState,
        config: _TMPNativeLayoutConfig,
    ) -> None:
        if line.style.line_height is None:
            line_break_ascender = (
                line_state.line_break_adjusted_ascender
                if line_state.line_break_adjusted_ascender is not None
                else line_state.max_ascender
            )
            state.line_offset += (
                0.0
                - line_state.max_descender
                + line_break_ascender
                + (config.raw_line_gap + config.line_spacing_delta) * config.base_scale
                + (config.line_spacing + config.paragraph_spacing) * config.current_em_scale
            )
            state.start_of_line_ascender = line_break_ascender
            state.is_driven_line_spacing = False
            return
        state.line_offset += (
            self.tmp_explicit_line_height(line.style.line_height)
            + (config.line_spacing + config.paragraph_spacing) * config.current_em_scale
        )
        state.is_driven_line_spacing = True

    def _tmp_native_layout_line(
        self,
        line: StyledLine,
        line_index: int,
        line_count: int,
        state: _TMPNativeLayoutState,
        config: _TMPNativeLayoutConfig,
    ) -> None:
        run_metrics, line_width, line_min_x, line_max_x, line_dominant_size = self.tmp_native_measure_line_runs(
            line,
            config.font_name,
            config.font_path,
            line_index,
            config.layout_mode,
            config.current_em_scale,
            config.outline_dilate,
            config.margin_width,
            source_metrics_only=config.source_metrics_only,
        )
        state.dominant_size = max(state.dominant_size, line_dominant_size)
        first_character_index = len(state.characters)
        line_state = self._tmp_native_layout_runs(line, line_index, first_character_index, state, config)
        self._tmp_native_layout_line_breaks(line, line_index, first_character_index, line_state, state, config)
        self._tmp_resolve_native_line_extents(line, line_state, config)
        self._tmp_adjust_native_line_offset(state, line_state)
        self._tmp_record_native_line(
            line,
            line_index,
            first_character_index,
            run_metrics,
            line_width,
            line_min_x,
            line_max_x,
            line_state,
            state,
            config,
        )
        if line_index + 1 < line_count:
            self._tmp_advance_native_line(line, line_state, state, config)

    def tmp_native_text_layout(
        self,
        lines: list[StyledLine],
        font_name: str,
        font_path: Path,
        base_size: float,
        line_spacing: float,
        dominant_size: float,
        layout_mode: str = "preferred",
        outline_dilate: float = 0.0,
        margin_width: float | None = None,
        *,
        source_metrics_only: bool = False,
    ) -> TMPNativeTextLayout | None:
        if not lines:
            return None

        base_scale = self.tmp_native_element_scale(font_name, base_size)
        current_em_scale = self.tmp_native_current_em_scale(base_size)
        config = _TMPNativeLayoutConfig(
            font_name=font_name,
            font_path=font_path,
            layout_mode=layout_mode,
            base_scale=base_scale,
            current_em_scale=current_em_scale,
            raw_line_gap=self.tmp_native_raw_line_gap(font_name) if self.tmp_native_line_gap else 0.0,
            line_spacing=line_spacing,
            line_spacing_delta=TMP_PREFAB_LINE_SPACING_DELTA,
            paragraph_spacing=TMP_PREFAB_PARAGRAPH_SPACING if layout_mode == "preferred" else 0.0,
            outline_dilate=outline_dilate,
            margin_width=max(0.0, float(margin_width or 0.0)),
            source_metrics_only=source_metrics_only,
        )
        state = _TMPNativeLayoutState(dominant_size)

        for line_index, line in enumerate(lines):
            self._tmp_native_layout_line(line, line_index, len(lines), state, config)

        if not state.lines:
            return None
        max_text_ascender = state.max_text_ascender or 0.0
        content_height = max(1.0, max_text_ascender - state.element_descender)
        preferred_width = self.tmp_preferred_width(max(1.0, state.rendered_width))
        preferred_height = self.tmp_preferred_height(
            content_height,
            (asset.ascent_line - asset.descent_line) * base_scale
            if (asset := self.tmp_font_library.active_asset(font_name)) is not None
            else None,
        )
        return TMPNativeTextLayout(
            layout_mode=layout_mode,
            lines=state.lines,
            characters=state.characters,
            preferred_width=preferred_width,
            preferred_height=preferred_height,
            content_height=preferred_height,
            max_ascender=max_text_ascender,
            max_descender=state.element_descender,
            accumulated_line_height=max(1.0, state.accumulated_line_height),
            dominant_size=state.dominant_size,
            base_scale=base_scale,
            current_em_scale=current_em_scale,
            raw_line_gap=config.raw_line_gap,
            line_spacing_delta=config.line_spacing_delta,
            paragraph_spacing=config.paragraph_spacing,
        )

    def tmp_native_measure_line_runs(
        self,
        line: StyledLine,
        font_name: str,
        font_path: Path,
        line_index: int,
        layout_mode: str,
        current_em_scale: float,
        outline_dilate: float,
        margin_width: float,
        *,
        source_metrics_only: bool = False,
    ) -> tuple[list[tuple[TextRun, float, float]], float, float, float, float]:
        run_metrics: list[tuple[TextRun, float, float]] = []
        x = self.tmp_native_line_initial_x(line, margin_width)
        line_min_x: float | None = None
        line_max_x: float | None = None
        dominant_size = line.style.size
        for run_index, run in enumerate(line.runs):
            vertex_padding = self.tmp_native_vertex_padding(font_name, run.style, outline_dilate)
            dominant_size = max(dominant_size, run.style.size)
            scaled_size = run.style.size * self.tmp_font_scale
            visual = self.tmp_native_run_visual_metrics(
                run,
                font_name,
                font_path,
                scaled_size,
                current_em_scale,
                source_metrics_only=source_metrics_only,
            )
            advance_scale_x = self.tmp_native_layout_advance_scale_x(run.style, layout_mode)
            vertex_scale_x = self.tmp_native_vertex_scale_x(run.style)
            run_w = self.tmp_native_run_advance(
                run,
                font_name,
                font_path,
                current_em_scale,
                advance_scale_x,
                source_metrics_only=source_metrics_only,
            )
            raw_bbox_left, raw_bbox_right = self.tmp_native_padded_horizontal_bounds(
                visual,
                run.style,
                vertex_padding,
                vertex_scale_x,
            )
            run_metrics.append((run, x, run_w))
            line_min_x = x + raw_bbox_left if line_min_x is None else min(line_min_x, x + raw_bbox_left)
            line_max_x = x + raw_bbox_right if line_max_x is None else max(line_max_x, x + raw_bbox_right)
            x += run_w
            next_run = line.runs[run_index + 1] if run_index + 1 < len(line.runs) else None
            if next_run is not None:
                x += self.tmp_inter_run_spacing_advance(
                    run.style,
                    next_run.style,
                    font_name,
                    scaled_size,
                    current_em_scale,
                )
        line_width = max(1.0, x)
        negative_cspace_width = max(
            (abs(run.style.cspace) for run, _, _ in run_metrics if run.style.cspace < 0.0),
            default=0.0,
        )
        if negative_cspace_width > 0.0:
            line_width = max(line_width, negative_cspace_width)
        if line_min_x is None or line_max_x is None:
            line_min_x = min(0.0, x)
            line_max_x = max(0.0, x)
        return run_metrics, line_width, line_min_x, line_max_x, dominant_size

    def tmp_native_run_visual_metrics(
        self,
        run: TextRun,
        font_name: str,
        font_path: Path,
        scaled_size: float,
        current_em_scale: float,
        *,
        source_metrics_only: bool = False,
    ) -> TMPRunVisualMetrics:
        if self.use_em_block(run):
            source_metrics = self.tmp_source_block_metrics(font_name, run, scaled_size)
            raw_advance = source_metrics.advance if source_metrics is not None else scaled_size
            raw_left = source_metrics.bearing_x if source_metrics is not None else 0.0
            raw_right = raw_left + (source_metrics.width if source_metrics is not None else raw_advance)
            raw_top, raw_bottom = self.tmp_native_style_extents(font_name, run.style)
            return TMPRunVisualMetrics(raw_advance, raw_left, raw_right, -raw_top, -raw_bottom)
        if source_metrics_only:
            measure = self.measure_tmp_source_run(run, font_name, scaled_size, current_em_scale)
        else:
            font = load_font(font_path, scaled_size)
            measure = self.measure_tmp_run(font, run, font_name, scaled_size, current_em_scale)
        return TMPRunVisualMetrics(
            measure.advance,
            measure.visual_left,
            measure.visual_right,
            measure.visual_top,
            measure.visual_bottom,
        )

    def tmp_native_padded_horizontal_bounds(
        self,
        visual: TMPRunVisualMetrics,
        style: TextStyle,
        vertex_padding: float,
        vertex_scale_x: float,
    ) -> tuple[float, float]:
        if self.tmp_scale_mode == "fx-native":
            quad = self.tmp_native_fx_quad(
                visual.left - vertex_padding,
                visual.right + vertex_padding,
                -visual.top + vertex_padding,
                -visual.bottom - vertex_padding,
                style,
                vertex_scale_x,
            )
            xs = (quad[0], quad[2], quad[4], quad[6])
            return min(xs), max(xs)
        return self.tmp_scale_x_bounds(
            visual.left - vertex_padding,
            visual.right + vertex_padding,
            vertex_scale_x,
        )

    def tmp_native_layout_character(
        self,
        char: str,
        style: TextStyle,
        font_name: str,
        font_path: Path,
        line_index: int,
        char_index: int,
        x_advance: float,
        line_offset: float,
        first_character_index: int,
        max_line_ascender: float,
        max_line_descender: float,
        visible_character_count: int,
        layout_mode: str,
        current_em_scale: float,
        outline_dilate: float,
        *,
        source_metrics_only: bool = False,
    ) -> tuple[TMPNativeCharacterInfo, float, float, float, int]:
        metrics = self.tmp_native_glyph_metrics(
            font_name,
            font_path,
            char,
            style,
            source_metrics_only=source_metrics_only,
        )
        baseline_offset = self.tmp_native_baseline_offset(style)
        element_ascender, element_descender = self.tmp_native_style_extents(font_name, style)
        element_ascender += baseline_offset
        element_descender += baseline_offset
        adjusted_ascender = element_ascender
        adjusted_descender = element_descender
        if abs(baseline_offset) > 0.0:
            adjusted_ascender = max(element_ascender - baseline_offset, adjusted_ascender)
            adjusted_descender = min(element_descender - baseline_offset, adjusted_descender)

        is_white_space = char.isspace()
        is_first_character_of_line = char_index == first_character_index
        if is_first_character_of_line or not is_white_space:
            max_line_ascender = max(max_line_ascender, adjusted_ascender)
            max_line_descender = min(max_line_descender, adjusted_descender)
            char_ascender = element_ascender - line_offset
            char_descender = element_descender - line_offset
            char_adjusted_ascender = adjusted_ascender
            char_adjusted_descender = adjusted_descender
        else:
            char_adjusted_ascender = max_line_ascender
            char_adjusted_descender = max_line_descender
            char_ascender = max_line_ascender - line_offset
            char_descender = max_line_descender - line_offset

        visible = self.tmp_native_visible_character(char)
        if visible:
            visible_character_count += 1
        advance_scale_x = self.tmp_native_layout_advance_scale_x(style, layout_mode)
        vertex_scale_x = self.tmp_native_vertex_scale_x(style)
        sdf_scale = self.tmp_native_character_sdf_scale(font_name, style)
        vertex_padding = self.tmp_native_vertex_padding(font_name, style, outline_dilate)
        glyph_origin_x = x_advance
        if style.mspace is not None:
            mono_advance = self.tmp_mspace_advance(style.mspace)
            glyph_origin_x += (mono_advance - metrics.advance) * 0.5
        raw_left_x = glyph_origin_x + metrics.bearing_x
        raw_right_x = raw_left_x + metrics.width
        raw_top_y = metrics.bearing_y
        raw_bottom_y = metrics.bearing_y - metrics.height
        raw_padded_left_x = raw_left_x - vertex_padding
        raw_padded_right_x = raw_right_x + vertex_padding
        top_y = raw_top_y + vertex_padding
        bottom_y = raw_bottom_y - vertex_padding
        (
            bottom_left_x,
            bottom_left_y,
            top_left_x,
            top_left_y,
            top_right_x,
            top_right_y,
            bottom_right_x,
            bottom_right_y,
        ) = self.tmp_native_fx_quad(
            raw_padded_left_x,
            raw_padded_right_x,
            top_y,
            bottom_y,
            style,
            vertex_scale_x,
        )
        next_x_advance = self.tmp_native_next_x_advance(
            x_advance,
            char,
            style,
            font_name,
            metrics,
            advance_scale_x,
            current_em_scale,
        )
        character_info_x_advance = self.tmp_native_character_info_x_advance(
            x_advance,
            next_x_advance,
            style,
        )
        info = TMPNativeCharacterInfo(
            index=char_index,
            char=char,
            line_index=line_index,
            x_origin=x_advance,
            x_advance=character_info_x_advance,
            glyph_origin_x=glyph_origin_x,
            bottom_left_x=bottom_left_x,
            bottom_left_y=bottom_left_y,
            top_left_x=top_left_x,
            top_left_y=top_left_y,
            top_right_x=top_right_x,
            top_right_y=top_right_y,
            bottom_right_x=bottom_right_x,
            bottom_right_y=bottom_right_y,
            vertex_padding=vertex_padding,
            raw_left_x=raw_left_x,
            raw_right_x=raw_right_x,
            raw_top_y=raw_top_y,
            raw_bottom_y=raw_bottom_y,
            baseline=-line_offset + baseline_offset,
            ascender=char_ascender,
            descender=char_descender,
            adjusted_ascender=char_adjusted_ascender,
            adjusted_descender=char_adjusted_descender,
            visible=visible,
            style=style,
            metrics=metrics,
            sdf_scale=sdf_scale,
        )
        return info, next_x_advance, max_line_ascender, max_line_descender, visible_character_count

    def tmp_native_character_sdf_scale(self, font_name: str, style: TextStyle) -> float:
        # TMP stores the per-character SDF scale in uv2.y, then TextMeshProUGUI
        # multiplies it by the canvas scale delta before sending the mesh. The
        # runtime dumps show this as currentElementScale / 540 for our
        # ScreenSpace camera setup (for example 26.6667 / 540 == 0.0493827).
        return abs(self.tmp_native_element_scale(font_name, style.size)) / 540.0

    def tmp_native_next_x_advance(
        self,
        x_advance: float,
        char: str,
        style: TextStyle,
        font_name: str,
        metrics: TMPGlyphMetrics,
        advance_scale_x: float,
        current_em_scale: float,
    ) -> float:
        font_size = style.size * self.tmp_font_scale
        if char == "\t":
            tab_size = self.tmp_font_library.tab_advance(font_name, font_size) or max(1.0, style.size * 4.0)
            tabs = math.ceil(x_advance / tab_size) * tab_size
            return tabs if tabs > x_advance else x_advance + tab_size
        if style.mspace is not None:
            return x_advance + self.tmp_mspace_advance(style.mspace)
        return (
            x_advance
            + metrics.advance * advance_scale_x
            + self.tmp_character_spacing_advance(style, font_name, font_size, current_em_scale)
        )

    def tmp_native_run_advance(
        self,
        run: TextRun,
        font_name: str,
        font_path: Path,
        current_em_scale: float,
        advance_scale_x: float,
        *,
        source_metrics_only: bool = False,
    ) -> float:
        if self.use_em_block(run):
            font_size = run.style.size * self.tmp_font_scale
            metrics = self.tmp_source_block_metrics(font_name, run, font_size)
            advance = metrics.advance if metrics is not None else font_size
            if not run.text:
                return max(0.0, advance * advance_scale_x)
            spacing = self.tmp_character_spacing_advance(run.style, font_name, font_size, current_em_scale)
            return max(0.0, len(run.text) * advance * advance_scale_x + max(0, len(run.text) - 1) * spacing)

        x = 0.0
        last_index = len(run.text) - 1
        for idx, char in enumerate(run.text):
            metrics = self.tmp_native_glyph_metrics(
                font_name,
                font_path,
                char,
                run.style,
                source_metrics_only=source_metrics_only,
            )
            next_x = self.tmp_native_next_x_advance(
                x,
                char,
                run.style,
                font_name,
                metrics,
                advance_scale_x,
                current_em_scale,
            )
            if idx == last_index:
                next_x -= self.tmp_character_spacing_advance(
                    run.style,
                    font_name,
                    run.style.size * self.tmp_font_scale,
                    current_em_scale,
                )
            x = next_x
        return max(0.0, x)

    def tmp_inter_run_spacing_advance(
        self,
        style: TextStyle,
        next_style: TextStyle,
        font_name: str,
        font_size: float,
        current_em_scale: float | None = None,
    ) -> float:
        spacing = self.tmp_normal_spacing_advance(font_name, font_size, current_em_scale)
        if style.bold:
            spacing += self.tmp_bold_spacing_advance(font_name, font_size, current_em_scale)
        if next_style.cspace == style.cspace:
            spacing += self.tmp_cspace_advance(style.cspace)
        return spacing

    def tmp_closes_cspace_before_next_run(self, style: TextStyle, next_style: TextStyle) -> bool:
        # TMP's </cspace> subtracts the active cspace from m_xAdvance and then
        # clears it. The parser maps native cspace closes to a nonzero -> zero
        # run-style transition, so character-level layout mirrors that rollback.
        return abs(style.cspace) >= 1.0e-6 and abs(next_style.cspace) < 1.0e-6

    def tmp_native_glyph_metrics(
        self,
        font_name: str,
        font_path: Path,
        char: str,
        style: TextStyle,
        *,
        source_metrics_only: bool = False,
    ) -> TMPGlyphMetrics:
        if char in {"\r", "\n", "\x03"}:
            return self.tmp_zero_glyph_metrics()
        font_size = style.size * self.tmp_font_scale
        if source_metrics_only:
            metric_char = self.tmp_render_glyph_char(font_name, char, font_size)
            # Keep strict native layout on the same glyph candidate as SdfFontQuad generation.
            # A base font can legitimately delegate one character to its TMP fallback chain.
            metrics = self.tmp_font_library.source_glyph_metrics(
                font_name,
                metric_char,
                font_size,
                include_fallback=True,
            )
            if metrics is None:
                raise ValueError(f"source font metrics are unavailable for U+{ord(char):04X}")
            return metrics
        font = load_font(font_path, font_size)
        return self.glyph_layout_metrics(font, char, font_name, font_size)

    def tmp_zero_glyph_metrics(self) -> TMPGlyphMetrics:
        return TMPGlyphMetrics(
            width=0.0,
            height=0.0,
            bearing_x=0.0,
            bearing_y=0.0,
            advance=0.0,
            rect_x=0,
            rect_y=0,
            rect_w=0,
            rect_h=0,
            glyph_scale=1.0,
            atlas_index=0,
        )

    def tmp_native_visible_character(self, char: str) -> bool:
        return char == "\t" or (not char.isspace() and char not in {"\u200b", "\u00ad", "\x03"})

    def tmp_native_line_initial_x(self, line: StyledLine, margin_width: float = 0.0) -> float:
        return line.style.indent + line.style.line_indent + self.tmp_style_percent_indent(line.style, margin_width)

    def tmp_style_percent_indent(self, style: TextStyle, margin_width: float) -> float:
        percent = (style.indent_percent or 0.0) + (style.line_indent_percent or 0.0)
        return margin_width * percent

    def tmp_line_indent_percent(self, line: StyledLine) -> float:
        return (line.style.indent_percent or 0.0) + (line.style.line_indent_percent or 0.0)

    def tmp_lines_have_percent_indent(self, lines: list[StyledLine]) -> bool:
        return any(abs(self.tmp_line_indent_percent(line)) > 1.0e-8 for line in lines)

    def tmp_resolve_percent_indent_margin_width(
        self,
        lines: list[StyledLine],
        font_name: str,
        font_path: Path,
        base_size: float,
        line_spacing: float,
        dominant_size: float,
        outline_dilate: float,
        zero_margin_layout: TMPNativeTextLayout | None,
        *,
        source_metrics_only: bool = False,
    ) -> float | None:
        if not self.tmp_lines_have_percent_indent(lines) or zero_margin_layout is None:
            return None
        if self.tmp_box_mode == "preferred":
            return self.tmp_preferred_percent_indent_margin_width(zero_margin_layout)
        return self.tmp_iterative_percent_indent_margin_width(
            lines,
            font_name,
            font_path,
            base_size,
            line_spacing,
            dominant_size,
            outline_dilate,
            zero_margin_layout,
            source_metrics_only=source_metrics_only,
        )

    def tmp_preferred_percent_indent_margin_width(self, layout: TMPNativeTextLayout) -> float:
        padding_x = max(0.0, self.tmp_preferred_padding_x)
        margin_width = layout.preferred_width + padding_x
        for line in layout.lines:
            percent = self.tmp_line_indent_percent(line.styled_line)
            if abs(percent) < 1.0e-8:
                margin_width = max(margin_width, line.width + padding_x)
                continue
            if percent >= 1.0:
                margin_width = TMP_PERCENT_INDENT_MAX_MARGIN_WIDTH
                continue
            margin_width = max(margin_width, (line.width + padding_x) / max(1.0e-6, 1.0 - percent))
        return min(TMP_PERCENT_INDENT_MAX_MARGIN_WIDTH, max(1.0, margin_width))

    def tmp_iterative_percent_indent_margin_width(
        self,
        lines: list[StyledLine],
        font_name: str,
        font_path: Path,
        base_size: float,
        line_spacing: float,
        dominant_size: float,
        outline_dilate: float,
        zero_margin_layout: TMPNativeTextLayout,
        *,
        source_metrics_only: bool = False,
    ) -> float:
        margin_width = self.tmp_text_box_size(
            zero_margin_layout.dominant_size,
            zero_margin_layout.preferred_width,
            zero_margin_layout.content_height,
        )[0]
        for _ in range(64):
            layout = self.tmp_native_text_layout(
                lines,
                font_name,
                font_path,
                base_size,
                line_spacing,
                dominant_size,
                "preferred",
                outline_dilate,
                margin_width,
                source_metrics_only=source_metrics_only,
            )
            if layout is None:
                return margin_width
            next_width = self.tmp_text_box_size(layout.dominant_size, layout.preferred_width, layout.content_height)[0]
            next_width = min(TMP_PERCENT_INDENT_MAX_MARGIN_WIDTH, max(1.0, next_width))
            if abs(next_width - margin_width) < 0.01:
                return next_width
            margin_width = next_width
        return margin_width

    def tmp_native_current_em_scale(self, font_size: float) -> float:
        return font_size * 0.01

    def tmp_native_element_scale(self, font_name: str, style_size: float) -> float:
        asset = self.tmp_font_library.active_asset(font_name)
        if asset is None or asset.point_size <= 0:
            return style_size * self.tmp_font_scale / TMP_FACE_POINT_SIZE
        return style_size / asset.point_size * asset.face_scale

    def tmp_native_style_extents(self, font_name: str, style: TextStyle) -> tuple[float, float]:
        asset = self.tmp_font_library.active_asset(font_name)
        if asset is not None and asset.point_size > 0:
            scale = self.tmp_native_element_scale(font_name, style.size)
            return asset.ascent_line * scale, asset.descent_line * scale
        fallback_size = max(1.0, style.size * self.tmp_font_scale)
        return fallback_size * 0.9, -fallback_size * 0.1

    def tmp_native_baseline_offset(self, style: TextStyle) -> float:
        return style.voffset

    def tmp_native_raw_line_gap(self, font_name: str) -> float:
        asset = self.tmp_font_library.active_asset(font_name)
        if asset is None:
            return 0.0
        return asset.line_height - (asset.ascent_line - asset.descent_line)

    def tmp_preferred_round(self, value: float) -> float:
        return int(max(0.0, value) * 100.0 + 1.0) / 100.0

    def tmp_preferred_width(self, value: float) -> float:
        return self.tmp_preferred_round(value)

    def tmp_preferred_height(self, value: float, base_line_height: float | None = None) -> float:
        value = max(0.0, value)
        if base_line_height is not None and value <= base_line_height + 1.0e-4:
            return value
        return self.tmp_preferred_round(value)

    def tmp_native_baseline_downs(
        self,
        layout: TMPNativeLineLayout,
        box_h: float,
        vertical_align: str,
    ) -> list[float]:
        anchor_y = tmp_native_anchor_y(vertical_align, box_h, layout.max_ascender, layout.max_descender)
        return [box_h / 2 - (anchor_y + baseline) for baseline in layout.baselines]

    def tmp_native_mesh_pixel_bounds(
        self,
        layout: TMPNativeTextLayout,
        native_baselines: list[float] | None,
        horizontal_align: str,
        box_w: float,
        box_h: float,
    ) -> tuple[float, float, float, float]:
        left = 0.0
        top = 0.0
        right = box_w
        bottom = box_h
        if native_baselines is None:
            return left, top, right, bottom
        for char in layout.characters:
            if not char.visible:
                continue
            line = layout.lines[char.line_index]
            line_x = tmp_line_offset_x(horizontal_align, box_w, line.width)
            baseline_y = native_baselines[char.line_index]
            xs = (
                char.bottom_left_x,
                char.top_left_x,
                char.top_right_x,
                char.bottom_right_x,
            )
            ys = (
                char.bottom_left_y,
                char.top_left_y,
                char.top_right_y,
                char.bottom_right_y,
            )
            left = min(left, line_x + min(xs))
            right = max(right, line_x + max(xs))
            top = min(top, baseline_y - max(ys))
            bottom = max(bottom, baseline_y - min(ys))
        return left, top, right, bottom

    def tmp_line_face_extents(
        self,
        font_name: str,
        line: StyledLine,
        line_metrics: list[tuple[TextRun, float, float]],
    ) -> tuple[float, float]:
        sizes = [run.style.size for run, _, _ in line_metrics] or [line.style.size]
        ascenders: list[float] = []
        descenders: list[float] = []
        for size in sizes:
            extents = self.tmp_font_library.face_extents(font_name, size, self.tmp_font_scale)
            if extents is None:
                fallback_size = max(1.0, size * self.tmp_font_scale)
                extents = (fallback_size * 0.9, -fallback_size * 0.1)
            ascenders.append(extents[0])
            descenders.append(extents[1])
        return max(ascenders), min(descenders)

    def font_path_for(self, font_name: str) -> Path:
        if font_name == "FOT-RodinNTLGPro-DB" and self.rodin_font in {"ttf", "otf"}:
            return font_file(self.fonts, font_name, self.rodin_font)
        return self.tmp_font_library.source_font_path(font_name) or font_file(self.fonts, font_name, self.rodin_font)

    def generate_text_data(self, item: dict[str, Any]) -> TMPGeneratedTextData:
        return TMPGeneratedTextData(
            text=str(item.get("text", "")),
            font_id=int(item.get("fontId", 0) or 0),
            align=int(item.get("type", 513) or 513),
            font_size=float(item.get("size", 24.0) or 24.0),
            outline_size=float(item.get("outlineSize", 0.0) or 0.0),
            outline_color_id=int(item.get("outlineColorId", 0) or 0),
            font_color_id=int(item.get("colorId", 0) or 0),
            line_spacing=float(item.get("lineSpacing", 0.0) or 0.0),
        )

    def update_text_mesh_state(
        self,
        data: TMPGeneratedTextData,
        font_name: str,
    ) -> TMPUpdateMeshState:
        asset = self.tmp_font_library.active_asset(font_name)
        face_scale = asset.face_scale if asset is not None else self.tmp_font_scale
        return TMPUpdateMeshState(
            font_name=font_name,
            font_asset_name=asset.name if asset is not None else None,
            text=data.text,
            font_size=data.font_size,
            font_color=self.colors.get(data.font_color_id, "#444466"),
            align=data.align,
            tmp_line_spacing=data.line_spacing * face_scale * self.tmp_line_spacing_factor,
            underlay_color=self.colors.get(data.outline_color_id, "#444466"),
            underlay_dilate=data.outline_size,
        )

    def tmp_generated_text_data_dict(self, data: TMPGeneratedTextData) -> dict[str, Any]:
        return {
            "text": data.text,
            "fontId": data.font_id,
            "align": data.align,
            "fontSize": data.font_size,
            "outlineSize": data.outline_size,
            "outlineColorId": data.outline_color_id,
            "fontColorId": data.font_color_id,
            "lineSpacing": data.line_spacing,
        }

    def tmp_update_mesh_state_dict(self, state: TMPUpdateMeshState) -> dict[str, Any]:
        return {
            "fontName": state.font_name,
            "fontAssetName": state.font_asset_name,
            "text": state.text,
            "fontSize": state.font_size,
            "fontColor": state.font_color,
            "align": state.align,
            "tmpLineSpacing": state.tmp_line_spacing,
            "underlayColor": state.underlay_color,
            "underlayDilate": state.underlay_dilate,
        }

    def tmp_style_audit_dict(self, style: TextStyle) -> dict[str, Any]:
        return {
            "color": style.color,
            "alpha": style.alpha,
            "size": style.size,
            "scaleX": style.scale_x,
            "cspace": style.cspace,
            "mspace": style.mspace,
            "indent": style.indent,
            "indentPercent": style.indent_percent,
            "lineIndent": style.line_indent,
            "lineIndentPercent": style.line_indent_percent,
            "lineHeight": style.line_height,
            "rotate": style.rotate,
            "voffset": style.voffset,
            "markColor": style.mark_color,
            "bold": style.bold,
            "italic": style.italic,
            "underline": style.underline,
            "strike": style.strike,
        }

    def tmp_font_asset_audit_dict(self, font_name: str) -> dict[str, Any] | None:
        asset = self.tmp_font_library.active_asset(font_name)
        if asset is None:
            return None
        runtime_source = self.tmp_font_library.runtime_source_font_path(asset)
        return {
            "name": asset.name,
            "bundle": asset.bundle,
            "atlasPopulationMode": asset.atlas_population_mode,
            "sourceFontPath": str(asset.source_font_path) if asset.source_font_path is not None else None,
            "runtimeSourceFontPath": str(runtime_source) if runtime_source is not None else None,
            "pointSize": asset.point_size,
            "faceScale": asset.face_scale,
            "lineHeight": asset.line_height,
            "ascentLine": asset.ascent_line,
            "descentLine": asset.descent_line,
            "tabWidth": asset.tab_width,
            "gradientScale": asset.gradient_scale,
            "atlasPadding": asset.atlas_padding,
            "weightNormal": asset.weight_normal,
            "weightBold": asset.weight_bold,
            "normalSpacingOffset": asset.normal_spacing_offset,
            "boldSpacing": asset.bold_spacing,
            "fallbacks": list(asset.fallback_names),
        }

    def record_tmp_layout_audit(
        self,
        item: dict[str, Any],
        text_data: TMPGeneratedTextData,
        mesh_state: TMPUpdateMeshState,
        metrics: list[tuple[StyledLine, list[tuple[TextRun, float, float]], float, float, float]],
        font_name: str,
        font_path: Path,
        preferred_width: float,
        preferred_height: float,
        content_height: float,
        total_height: float,
        box_w: float,
        box_h: float,
        native_layout: TMPNativeLineLayout | None,
        native_baselines: list[float] | None,
        native_text_layout: TMPNativeTextLayout | None = None,
        mesh_text_layout: TMPNativeTextLayout | None = None,
        mesh_bounds: tuple[float, float, float, float] | None = None,
        rect_origin: tuple[float, float] | None = None,
        local_image_size: tuple[int, int] | None = None,
    ) -> None:
        object_data = item.get("objectData", {}) or {}
        line_entries = [
            self.tmp_line_layout_audit_entry(
                line_index,
                line_metric,
                font_name,
                font_path,
                native_text_layout,
                native_baselines,
            )
            for line_index, line_metric in enumerate(metrics)
        ]
        self.tmp_layout_audit.append(
            {
                **self._current_card_ref,
                "kind": "text",
                "layer": int(object_data.get("layer", 0) or 0),
                "dataId": content_data_id("text", item),
                "generatedTextData": self.tmp_generated_text_data_dict(text_data),
                "updateMeshState": self.tmp_update_mesh_state_dict(mesh_state),
                "fontAsset": self.tmp_font_asset_audit_dict(font_name),
                "align": {
                    "raw": mesh_state.align,
                    "horizontal": tmp_horizontal_alignment(mesh_state.align),
                    "vertical": tmp_vertical_alignment(mesh_state.align),
                },
                "layout": self.tmp_layout_audit_metadata(
                    preferred_width,
                    preferred_height,
                    content_height,
                    total_height,
                    box_w,
                    box_h,
                    native_layout,
                    native_baselines,
                    native_text_layout,
                    mesh_text_layout,
                    mesh_bounds,
                    rect_origin,
                    local_image_size,
                ),
                "lines": line_entries,
            }
        )

    def tmp_run_layout_audit_entry(
        self,
        run: TextRun,
        x: float,
        run_w: float,
        font_name: str,
        font_path: Path,
        native_text_layout: TMPNativeTextLayout | None,
    ) -> dict[str, Any]:
        scaled_size = run.style.size * self.tmp_font_scale
        font = load_font(font_path, scaled_size)
        current_em_scale = native_text_layout.current_em_scale if native_text_layout is not None else None
        measure = self.measure_tmp_run(font, run, font_name, scaled_size, current_em_scale)
        spacing = self.tmp_character_spacing_advance(run.style, font_name, scaled_size, current_em_scale)
        return {
            "text": run.text,
            "style": self.tmp_style_audit_dict(run.style),
            "x": x,
            "meshWidth": run_w,
            "advance": measure.advance,
            "visualBounds": {
                "left": measure.visual_left,
                "right": measure.visual_right,
                "top": measure.visual_top,
                "bottom": measure.visual_bottom,
            },
            "characterSpacingAdvance": spacing,
            "fontSizeAfterFaceScale": scaled_size,
            "glyphs": self.tmp_run_glyph_audit(font, run, font_name, scaled_size, current_em_scale),
        }

    def tmp_line_layout_audit_entry(
        self,
        line_index: int,
        line_metric: tuple[StyledLine, list[tuple[TextRun, float, float]], float, float, float],
        font_name: str,
        font_path: Path,
        native_text_layout: TMPNativeTextLayout | None,
        native_baselines: list[float] | None,
    ) -> dict[str, Any]:
        line, line_metrics, line_y, line_h, line_w = line_metric
        runs = [
            self.tmp_run_layout_audit_entry(run, x, run_w, font_name, font_path, native_text_layout)
            for run, x, run_w in line_metrics
        ]
        native_baseline = native_baselines[line_index] if native_baselines is not None else None
        return {
            "index": line_index,
            "style": self.tmp_style_audit_dict(line.style),
            "lineY": line_y,
            "lineHeight": line_h,
            "lineWidth": line_w,
            "nativeBaselineDown": native_baseline,
            "runs": runs,
        }

    def tmp_layout_audit_metadata(
        self,
        preferred_width: float,
        preferred_height: float,
        content_height: float,
        total_height: float,
        box_w: float,
        box_h: float,
        native_layout: TMPNativeLineLayout | None,
        native_baselines: list[float] | None,
        native_text_layout: TMPNativeTextLayout | None,
        mesh_text_layout: TMPNativeTextLayout | None,
        mesh_bounds: tuple[float, float, float, float] | None,
        rect_origin: tuple[float, float] | None,
        local_image_size: tuple[int, int] | None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "preferredWidth": preferred_width,
            "preferredHeight": preferred_height,
            "contentHeight": content_height,
            "accumulatedLineHeight": total_height,
            "lateUpdateSizeDelta": {"x": box_w, "y": box_h},
            "meshPixelBounds": None,
            "localImage": None,
            "nativeLineLayout": None,
            "nativeTextInfo": None,
            "meshNativeTextInfo": None,
        }
        if mesh_bounds is not None:
            metadata["meshPixelBounds"] = {
                "left": mesh_bounds[0],
                "top": mesh_bounds[1],
                "right": mesh_bounds[2],
                "bottom": mesh_bounds[3],
            }
        if rect_origin is not None and local_image_size is not None:
            metadata["localImage"] = {
                "width": local_image_size[0],
                "height": local_image_size[1],
                "rectOriginX": rect_origin[0],
                "rectOriginY": rect_origin[1],
            }
        if native_layout is not None:
            metadata["nativeLineLayout"] = {
                "baselines": native_layout.baselines,
                "maxAscender": native_layout.max_ascender,
                "maxDescender": native_layout.max_descender,
                "contentHeight": native_layout.content_height,
            }
        if native_text_layout is not None:
            metadata["nativeTextInfo"] = self.tmp_native_text_layout_audit_dict(native_text_layout)
        if mesh_text_layout is not None:
            metadata["meshNativeTextInfo"] = self.tmp_native_text_layout_audit_dict(
                mesh_text_layout,
                rect_box_w=box_w,
                rect_box_h=box_h,
                native_baselines=native_baselines,
            )
        return metadata

    def tmp_native_text_layout_audit_dict(
        self,
        layout: TMPNativeTextLayout,
        rect_box_w: float | None = None,
        rect_box_h: float | None = None,
        native_baselines: list[float] | None = None,
    ) -> dict[str, Any]:
        first_line_width = layout.lines[0].width if layout.lines else layout.preferred_width
        if rect_box_w is None:
            rect_box_w = layout.preferred_width + max(0.0, self.tmp_preferred_padding_x)
        if rect_box_h is None:
            rect_box_h = layout.preferred_height + max(0.0, self.tmp_preferred_padding_y)
        if native_baselines is None:
            native_baselines = self.tmp_native_baseline_downs(layout.line_layout, rect_box_h, "middle")
        return {
            "layoutMode": layout.layout_mode,
            "preferredWidth": layout.preferred_width,
            "preferredHeight": layout.preferred_height,
            "lateUpdateSizeDelta": {
                "x": rect_box_w,
                "y": rect_box_h,
            },
            "contentHeight": layout.content_height,
            "maxAscender": layout.max_ascender,
            "maxDescender": layout.max_descender,
            "baseScale": layout.base_scale,
            "currentEmScale": layout.current_em_scale,
            "rawLineGap": layout.raw_line_gap,
            "lineSpacingDelta": layout.line_spacing_delta,
            "paragraphSpacing": layout.paragraph_spacing,
            "lineInfo": [
                {
                    "index": line.index,
                    "firstCharacterIndex": line.first_character_index,
                    "lastCharacterIndex": line.last_character_index,
                    "visibleCharacterCount": line.visible_character_count,
                    "baseline": line.baseline,
                    "ascender": line.ascender,
                    "descender": line.descender,
                    "lineHeight": line.line_height,
                    "width": line.width,
                    "maxAdvance": line.max_advance,
                    "lineExtents": {
                        "minX": line.line_extents_min_x,
                        "maxX": line.line_extents_max_x,
                    },
                }
                for line in layout.lines
            ],
            "characterInfo": [
                {
                    "index": char.index,
                    "char": char.char,
                    "codepoint": ord(char.char) if char.char else None,
                    "lineIndex": char.line_index,
                    "origin": char.x_origin,
                    "xAdvance": char.x_advance,
                    "glyphOriginX": char.glyph_origin_x,
                    "baseline": char.baseline,
                    "ascender": char.ascender,
                    "descender": char.descender,
                    "adjustedAscender": char.adjusted_ascender,
                    "adjustedDescender": char.adjusted_descender,
                    "isVisible": char.visible,
                    "bottomLeft": {"x": char.bottom_left_x, "y": char.bottom_left_y},
                    "topLeft": {"x": char.top_left_x, "y": char.top_left_y},
                    "topRight": {"x": char.top_right_x, "y": char.top_right_y},
                    "bottomRight": {"x": char.bottom_right_x, "y": char.bottom_right_y},
                    "vertexPadding": char.vertex_padding,
                    "rawBounds": {
                        "left": char.raw_left_x,
                        "right": char.raw_right_x,
                        "top": char.raw_top_y,
                        "bottom": char.raw_bottom_y,
                    },
                    "style": self.tmp_style_audit_dict(char.style),
                    "rectLocal": self.tmp_character_rect_local_audit_dict(
                        char,
                        layout,
                        rect_box_w,
                        native_baselines,
                        first_line_width,
                    ),
                    "metrics": {
                        "width": char.metrics.width,
                        "height": char.metrics.height,
                        "bearingX": char.metrics.bearing_x,
                        "bearingY": char.metrics.bearing_y,
                        "advance": char.metrics.advance,
                    },
                    "sdfScale": char.sdf_scale,
                }
                for char in layout.characters
            ],
        }

    def tmp_character_rect_local_audit_dict(
        self,
        char: TMPNativeCharacterInfo,
        layout: TMPNativeTextLayout,
        rect_box_w: float,
        native_baselines: list[float],
        first_line_width: float,
    ) -> dict[str, Any]:
        # Unity TMP characterInfo vertices are in the centered RectTransform's
        # local space. The raw layout above is line-local with baseline-up y.
        line = layout.lines[char.line_index]
        line_x = tmp_line_offset_x("left", rect_box_w, line.width)
        local_x = line_x - rect_box_w / 2.0
        baseline_local_y = (layout.preferred_height + max(0.0, self.tmp_preferred_padding_y)) / 2.0 - native_baselines[
            char.line_index
        ]

        def point(x: float, y: float) -> dict[str, float]:
            return {"x": local_x + x, "y": baseline_local_y + y}

        # Use TMP's baseline-up convention directly: y vertices stay relative to
        # the character baseline while x shifts by the centered RectTransform.
        # The baseline value is exposed separately for dynamic log comparison.
        return {
            "lineLeft": local_x,
            "baseline": baseline_local_y,
            "origin": local_x + char.x_origin,
            "xAdvance": local_x + char.x_advance,
            "topLeft": point(char.top_left_x, char.top_left_y),
            "bottomLeft": point(char.bottom_left_x, char.bottom_left_y),
            "topRight": point(char.top_right_x, char.top_right_y),
            "bottomRight": point(char.bottom_right_x, char.bottom_right_y),
            "firstLineWidth": first_line_width,
        }

    def tmp_line_height(self, base_size: float, style_size: float, font_name: str) -> float:
        if self.tmp_line_mode == "base":
            return max(1.0, base_size * self.tmp_font_scale * self.tmp_line_height_factor)
        if self.tmp_line_mode == "base-glyph":
            return max(1.0, base_size * self.tmp_font_scale * self.tmp_line_height_factor)
        if self.tmp_line_mode == "style-only":
            return max(1.0, style_size * self.tmp_font_scale * self.tmp_line_height_factor)
        if self.tmp_line_mode == "asset-face":
            value = self.tmp_font_library.line_height(font_name, style_size, self.tmp_font_scale, False)
            if value is not None:
                return max(1.0, value)
        if self.tmp_line_mode == "asset-face-scale":
            value = self.tmp_font_library.line_height(font_name, style_size, self.tmp_font_scale, True)
            if value is not None:
                return max(1.0, value)
        if self.tmp_line_mode == "face":
            ratio = TMP_FACE_LINE_HEIGHT / TMP_FACE_POINT_SIZE
            return max(1.0, style_size * ratio)
        if self.tmp_line_mode == "face-scale":
            ratio = TMP_FACE_LINE_HEIGHT / TMP_FACE_POINT_SIZE
            return max(1.0, style_size * ratio * self.tmp_font_scale / TMP_FACE_SCALE)
        return max(1.0, (base_size + style_size) * self.tmp_font_scale * self.tmp_line_height_factor)

    def tmp_explicit_line_height(self, value: float) -> float:
        if self.tmp_line_mode in {"face", "face-scale", "asset-face", "asset-face-scale"}:
            return max(0.0, value)
        return max(0.0, value * self.tmp_font_scale)

    def tmp_line_spacing_pixels(self, font_name: str, style_size: float, tmp_line_spacing: float) -> float:
        # TextContentView.UpdateTextMesh first writes TMP_Text.lineSpacing as
        # data.lineSpacing * faceInfo.scale * TextLineSpacingFactor. TMP then
        # multiplies this stored value by currentEmScale when inserting a line.
        return tmp_line_spacing

    def apply_tmp_line_spacing(
        self,
        line_h: float,
        tmp_line_spacing: float,
        font_name: str,
        style_size: float,
    ) -> float:
        return max(1.0, line_h + self.tmp_line_spacing_pixels(font_name, style_size, tmp_line_spacing))

    def tmp_cspace_advance(self, cspace: float) -> float:
        # GenerateTextMesh adds TMP's rich-text cspace accumulator directly to
        # m_xAdvance after the glyph and character-spacing terms.
        return cspace

    def tmp_character_spacing_advance(
        self,
        style: TextStyle,
        font_name: str,
        font_size: float,
        current_em_scale: float | None = None,
    ) -> float:
        spacing = self.tmp_normal_spacing_advance(font_name, font_size, current_em_scale)
        spacing += self.tmp_cspace_advance(style.cspace)
        if style.bold:
            spacing += self.tmp_bold_spacing_advance(font_name, font_size, current_em_scale)
        return spacing

    def tmp_normal_spacing_advance(
        self,
        font_name: str,
        font_size: float,
        current_em_scale: float | None = None,
    ) -> float:
        if current_em_scale is None:
            return self.tmp_font_library.normal_spacing_advance(font_name, font_size)
        asset = self.tmp_font_library.active_asset(font_name)
        if asset is None:
            return 0.0
        return asset.normal_spacing_offset * current_em_scale

    def tmp_bold_spacing_advance(
        self,
        font_name: str,
        font_size: float,
        current_em_scale: float | None = None,
    ) -> float:
        if current_em_scale is None:
            return self.tmp_font_library.bold_spacing_advance(font_name, font_size)
        asset = self.tmp_font_library.active_asset(font_name)
        if asset is None:
            return 0.0
        return asset.bold_spacing * current_em_scale

    def tmp_mspace_advance(self, mspace: float) -> float:
        # GenerateTextMesh treats m_monoSpacing as a raw TMP layout width, with
        # a separate glyph-centering correction. It is not multiplied by
        # faceInfo.scale a second time.
        return mspace

    def tmp_layout_scale_x(self, style: TextStyle) -> float:
        return self.tmp_preferred_layout_scale_x(style)

    def tmp_preferred_layout_scale_x(self, style: TextStyle) -> float:
        return style.scale_x if self.tmp_scale_mode in {"x", "fx-center"} else 1.0

    def tmp_mesh_layout_scale_x(self, style: TextStyle) -> float:
        return self.tmp_native_advance_scale_x(style)

    def tmp_native_advance_scale_x(self, style: TextStyle) -> float:
        return style.scale_x if self.tmp_scale_mode in {"x", "fx-center"} else 1.0

    def tmp_native_layout_advance_scale_x(self, style: TextStyle, layout_mode: str) -> float:
        if self.tmp_scale_mode == "fx-native" and layout_mode == "mesh":
            return style.scale_x
        return self.tmp_native_advance_scale_x(style)

    def tmp_native_vertex_scale_x(self, style: TextStyle) -> float:
        return style.scale_x if self.tmp_scale_mode in {"x", "fx-center", "fx-native"} else 1.0

    def tmp_scale_x_bounds(self, left: float, right: float, scale_x: float) -> tuple[float, float]:
        if abs(scale_x - 1.0) < 1.0e-6:
            return left, right
        center_x = (left + right) * 0.5
        scaled_left = center_x + (left - center_x) * scale_x
        scaled_right = center_x + (right - center_x) * scale_x
        return min(scaled_left, scaled_right), max(scaled_left, scaled_right)

    def tmp_native_fx_quad(
        self,
        left: float,
        right: float,
        top: float,
        bottom: float,
        style: TextStyle,
        scale_x: float,
    ) -> tuple[float, float, float, float, float, float, float, float]:
        center_x = (left + right) * 0.5
        center_y = (top + bottom) * 0.5
        points = [
            (left, bottom),
            (left, top),
            (right, top),
            (right, bottom),
        ]
        if abs(scale_x - 1.0) >= 1.0e-6:
            points = [(center_x + (x - center_x) * scale_x, y) for x, y in points]
        if abs(style.rotate) >= 1.0e-6:
            angle = math.radians(style.rotate)
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            points = [
                (
                    center_x + (x - center_x) * cos_a - (y - center_y) * sin_a,
                    center_y + (x - center_x) * sin_a + (y - center_y) * cos_a,
                )
                for x, y in points
            ]
        (
            (bottom_left_x, bottom_left_y),
            (top_left_x, top_left_y),
            (top_right_x, top_right_y),
            (bottom_right_x, bottom_right_y),
        ) = points
        return (
            bottom_left_x,
            bottom_left_y,
            top_left_x,
            top_left_y,
            top_right_x,
            top_right_y,
            bottom_right_x,
            bottom_right_y,
        )

    def tmp_native_character_info_x_advance(
        self,
        x_origin: float,
        next_x_advance: float,
        style: TextStyle,
    ) -> float:
        return next_x_advance

    def tmp_native_unrotated_quad_size(self, char_info: TMPNativeCharacterInfo) -> tuple[int, int]:
        style = char_info.style
        left = char_info.raw_left_x - char_info.vertex_padding
        right = char_info.raw_right_x + char_info.vertex_padding
        top = char_info.raw_top_y + char_info.vertex_padding
        bottom = char_info.raw_bottom_y - char_info.vertex_padding
        scale_x = self.tmp_native_vertex_scale_x(style)
        width = max(1.0, abs(right - left) * abs(scale_x))
        height = max(1.0, top - bottom)
        return max(1, round(width)), max(1, round(height))

    def tmp_direct_sdf_field_size(self, geometry_size: tuple[int, int]) -> tuple[int, int]:
        """Bound a direct glyph's raster while preserving its separate logical geometry.

        A TMP ``<scale>`` tag can make the logical quad much wider than the canvas.  Rasterizing
        that entire off-screen quad is wasteful: the following affine pass clips it back to the
        canvas.  One canvas diagonal per source axis retains enough samples for any rotation;
        the warp plan still uses ``geometry_size`` for the destination corners.
        """

        geometry_w, geometry_h = geometry_size
        if geometry_w <= 0 or geometry_h <= 0:
            raise ValueError("custom profile TMP direct glyph geometry must be positive")

        axis_limit = max(1, math.ceil(math.hypot(self.canvas_w, self.canvas_h)))
        field_w = min(geometry_w, axis_limit)
        field_h = min(geometry_h, axis_limit)
        if field_w * field_h > self.max_layer_pixels:
            scale = math.sqrt(self.max_layer_pixels / (field_w * field_h))
            field_w = max(1, math.floor(field_w * scale))
            field_h = max(1, math.floor(field_h * scale))
            if field_w * field_h > self.max_layer_pixels:
                if field_w >= field_h:
                    field_w = max(1, self.max_layer_pixels // field_h)
                else:
                    field_h = max(1, self.max_layer_pixels // field_w)
        return ensure_raster_size(
            (field_w, field_h),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP direct glyph field",
        )

    def tmp_layout_scale_y(self, style: TextStyle) -> float:
        return 1.0 if self.tmp_scale_mode in {"x", "fx-center", "fx-native"} else style.scale_x

    def tmp_fx_scale_x(self, style: TextStyle) -> float:
        return style.scale_x if self.tmp_scale_mode in {"x", "fx-center", "fx-native"} else 1.0

    def tmp_fx_advance_scale_x(self, style: TextStyle) -> float:
        return style.scale_x if self.tmp_scale_mode == "fx-center" else 1.0

    def tmp_run_font_size(self, style: TextStyle) -> float:
        base_font_size = style.size * self.tmp_font_scale
        return base_font_size * style.scale_x if self.tmp_scale_mode == "uniform" else base_font_size

    def tmp_text_box_width(self, dominant_size: float, content_width: float) -> float:
        return self.tmp_text_box_size(dominant_size, content_width, 1.0)[0]

    def tmp_text_box_size(
        self, dominant_size: float, content_width: float, content_height: float
    ) -> tuple[float, float]:
        content_width = max(1.0, content_width)
        content_height = max(1.0, content_height)
        if self.tmp_box_mode == "preferred":
            return (
                content_width + max(0.0, self.tmp_preferred_padding_x),
                content_height + max(0.0, self.tmp_preferred_padding_y),
            )
        if self.tmp_box_mode == "prefab":
            scale = dominant_size / max(1.0, TMP_PREFAB_FONT_SIZE)
            return TMP_PREFAB_TEXT_BOX_W * scale, TMP_PREFAB_TEXT_BOX_H * scale
        if self.tmp_box_mode == "fixed":
            return max(1.0, self.tmp_box_width), content_height
        if self.tmp_box_mode == "content":
            return content_width, content_height
        if self.tmp_box_mode == "size-full":
            box = self.tmp_box_width + dominant_size * self.tmp_font_scale * self.tmp_box_width_factor
            return max(box, content_width), content_height
        box = self.tmp_box_width + max(0.0, dominant_size - 10.0) * self.tmp_font_scale * self.tmp_box_width_factor
        return max(box, content_width), content_height

    def use_em_block(self, run: TextRun) -> bool:
        if not is_tmp_block_char(run):
            return False
        if self.tmp_block_mode == "glyph":
            return False
        if self.tmp_block_mode == "em":
            return True
        return run.style.size >= 300

    def tmp_source_block_metrics(
        self,
        font_name: str,
        run: TextRun,
        font_size: float,
    ) -> TMPGlyphMetrics | None:
        if self.tmp_block_mode != "source-glyph" or not is_tmp_block_char(run):
            return None
        return self.tmp_font_library.source_glyph_metrics(font_name, run.text, font_size)

    def tmp_face_baseline_offset(self, font_name: str, style_size: float, line_h: float) -> float:
        extents = self.tmp_font_library.face_extents(font_name, style_size, self.tmp_font_scale)
        if extents is None:
            return line_h * 0.9
        ascender, descender = extents
        return (line_h + ascender + descender) * 0.5

    def render_em_block_glyph(
        self,
        font_name: str,
        run: TextRun,
        base_font_size: float,
        outline_color: str,
        outline_width: int,
    ) -> tuple[Image.Image, float, TMPGlyphMetrics | None]:
        style = run.style
        scale_x = self.tmp_fx_scale_x(style)
        scale_y = self.tmp_layout_scale_y(style)
        metrics = self.tmp_source_block_metrics(font_name, run, base_font_size)
        if metrics is None:
            w = max(1, round(base_font_size * scale_x + outline_width * 2))
            h = max(1, round(base_font_size * scale_y + outline_width * 2))
            x_offset = (
                (base_font_size - base_font_size * scale_x) * 0.5 - outline_width
                if self.tmp_scale_mode in {"fx-center", "fx-native"}
                else -outline_width
            )
        else:
            w = max(1, round(metrics.width * scale_x + outline_width * 2))
            h = max(1, round(metrics.height * scale_y + outline_width * 2))
            if self.tmp_scale_mode in {"fx-center", "fx-native"}:
                x_offset = metrics.bearing_x + (metrics.width - metrics.width * scale_x) * 0.5 - outline_width
            else:
                x_offset = metrics.bearing_x * scale_x - outline_width

        glyph = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(glyph)
        if outline_width > 0:
            draw.rectangle((0, 0, w - 1, h - 1), fill=hex_to_rgba(outline_color, style.alpha))
            inset = outline_width
            if inset * 2 < w and inset * 2 < h:
                draw.rectangle((inset, inset, w - inset - 1, h - inset - 1), fill=hex_to_rgba(style.color, style.alpha))
        else:
            draw.rectangle((0, 0, w - 1, h - 1), fill=hex_to_rgba(style.color, style.alpha))
        if style.rotate:
            glyph = glyph.rotate(-style.rotate, resample=Image.Resampling.BICUBIC, expand=True)
        return glyph, x_offset, metrics

    def text_pad(self, base_size: float, outline_width: int) -> int:
        return max(64, round(max(base_size * self.tmp_font_scale, outline_width) * 2))

    def text_bbox(self, font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int, int, int]:
        if math.isclose(self.tmp_space_width_factor, 1.0, abs_tol=1.0e-9) or " " not in text:
            return font.getbbox(text or " ")

        cursor = 0.0
        bounds = TMPVisualBounds(left=0.0, right=0.0)
        for ch in text:
            metric_char = " " if ch in TMP_SPACE_EQUIVALENT_CHARS else ch
            if not ch.isspace():
                bbox = font.getbbox(metric_char)
                bounds.include(cursor + bbox[0], cursor + bbox[2], float(bbox[1]), float(bbox[3]))
            cursor += self.tmp_adjusted_space_advance(font, metric_char)
        bounds.include_horizontal(0.0, cursor)
        fallback_bbox = font.getbbox(text or " ")
        left, right, top, bottom = bounds.resolved((0.0, cursor, float(fallback_bbox[1]), float(fallback_bbox[3])))
        return (math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom))

    def tmp_adjusted_space_advance(self, font: ImageFont.FreeTypeFont, metric_char: str) -> float:
        advance = float(font.getlength(metric_char))
        return advance * self.tmp_space_width_factor if metric_char == " " else advance

    def tmp_asset_layout_metrics(
        self,
        font_name: str,
        metric_char: str,
        font_size: float,
    ) -> tuple[TMPGlyphMetrics, str] | None:
        if self.tmp_metrics_mode == "pil" or not font_name or font_size <= 0:
            return None
        include_fallback = self.tmp_metrics_mode == "asset-fallback"
        active = self.tmp_font_library.active_asset(font_name)
        if active is not None and active.atlas_population_mode == 1 and self.tmp_dynamic_sdf:
            source_metrics = self.tmp_font_library.source_glyph_metrics(
                font_name,
                metric_char,
                font_size,
                include_fallback=include_fallback,
            )
            return (source_metrics, "source-font-dynamic") if source_metrics is not None else None
        metrics = self.tmp_font_library.glyph_metrics(
            font_name,
            metric_char,
            font_size,
            include_fallback=include_fallback,
        )
        if metrics is not None:
            return metrics, "tmp-character-table"
        source_metrics = self.tmp_font_library.source_glyph_metrics(
            font_name,
            metric_char,
            font_size,
            include_fallback=include_fallback,
        )
        return (source_metrics, "source-font-fallback") if source_metrics is not None else None

    def glyph_advance(
        self,
        font: ImageFont.FreeTypeFont,
        ch: str,
        font_name: str = "",
        font_size: float = 0.0,
    ) -> float:
        if ch == "\t" and font_name and font_size > 0:
            tab_advance = self.tmp_font_library.tab_advance(font_name, font_size)
            if tab_advance is not None:
                return tab_advance
        metric_char = self.tmp_render_glyph_char(font_name, ch, font_size)
        asset_metrics = self.tmp_asset_layout_metrics(font_name, metric_char, font_size)
        if asset_metrics is not None:
            return max(0.0, asset_metrics[0].advance)
        return self.tmp_adjusted_space_advance(font, metric_char)

    def tmp_has_renderable_glyph(self, font_name: str, ch: str, font_size: float, include_fallback: bool) -> bool:
        if not font_name or font_size <= 0:
            return True
        if self.tmp_font_library.glyph_metrics(font_name, ch, font_size, include_fallback=include_fallback) is not None:
            return True
        return (
            self.tmp_font_library.source_glyph_metrics(
                font_name,
                ch,
                font_size,
                include_fallback=include_fallback,
            )
            is not None
        )

    def tmp_render_glyph_char(self, font_name: str, ch: str, font_size: float) -> str:
        if ch in TMP_SPACE_EQUIVALENT_CHARS:
            return " "
        if not ch or not self.tmp_native_visible_character(ch):
            return ch
        include_fallback = self.tmp_metrics_mode == "asset-fallback"
        key = (font_name, ch, include_fallback)
        cached = self._tmp_render_char_cache.get(key)
        if cached is not None:
            return cached
        render_char = ch
        if not self.tmp_has_renderable_glyph(font_name, ch, font_size, include_fallback):
            if ch != TMP_MISSING_GLYPH_CHAR and self.tmp_has_renderable_glyph(
                font_name,
                TMP_MISSING_GLYPH_CHAR,
                font_size,
                include_fallback,
            ):
                render_char = TMP_MISSING_GLYPH_CHAR
        self._tmp_render_char_cache[key] = render_char
        return render_char

    def glyph_layout_metrics_with_source(
        self,
        font: ImageFont.FreeTypeFont,
        ch: str,
        font_name: str,
        font_size: float,
    ) -> tuple[TMPGlyphMetrics, str]:
        if ch == "\t":
            advance = self.glyph_advance(font, ch, font_name, font_size)
            return (
                TMPGlyphMetrics(
                    width=0.0,
                    height=0.0,
                    bearing_x=0.0,
                    bearing_y=0.0,
                    advance=advance,
                    rect_x=0,
                    rect_y=0,
                    rect_w=0,
                    rect_h=0,
                    glyph_scale=1.0,
                    atlas_index=0,
                ),
                "face-tab-width",
            )
        metric_char = self.tmp_render_glyph_char(font_name, ch, font_size)
        asset_metrics = self.tmp_asset_layout_metrics(font_name, metric_char, font_size)
        if asset_metrics is not None:
            return asset_metrics
        bbox = font.getbbox(metric_char or " ")
        advance = self.glyph_advance(font, metric_char or " ", font_name, font_size)
        return (
            TMPGlyphMetrics(
                width=max(0.0, float(bbox[2] - bbox[0])),
                height=max(0.0, float(bbox[3] - bbox[1])),
                bearing_x=float(bbox[0]),
                bearing_y=-float(bbox[1]),
                advance=max(0.0, advance),
                rect_x=0,
                rect_y=0,
                rect_w=0,
                rect_h=0,
                glyph_scale=1.0,
                atlas_index=0,
            ),
            "pillow-fallback",
        )

    def glyph_layout_metrics(
        self,
        font: ImageFont.FreeTypeFont,
        ch: str,
        font_name: str,
        font_size: float,
    ) -> TMPGlyphMetrics:
        return self.glyph_layout_metrics_with_source(font, ch, font_name, font_size)[0]

    def measure_tmp_source_run(
        self,
        run: TextRun,
        font_name: str,
        font_size: float,
        current_em_scale: float | None = None,
    ) -> TMPRunMeasure:
        """Measure a strict dynamic-font run without constructing a Pillow font object."""
        return self.measure_tmp_run_from_metrics(
            run,
            font_name,
            font_size,
            current_em_scale,
            lambda ch: self.tmp_font_library.source_glyph_metrics(
                font_name,
                self.tmp_render_glyph_char(font_name, ch, font_size),
                font_size,
                include_fallback=True,
            ),
        )

    def measure_tmp_run(
        self,
        font: ImageFont.FreeTypeFont,
        run: TextRun,
        font_name: str,
        font_size: float,
        current_em_scale: float | None = None,
    ) -> TMPRunMeasure:
        return self.measure_tmp_run_from_metrics(
            run,
            font_name,
            font_size,
            current_em_scale,
            lambda ch: self.glyph_layout_metrics(font, ch, font_name, font_size),
        )

    def measure_tmp_run_from_metrics(
        self,
        run: TextRun,
        font_name: str,
        font_size: float,
        current_em_scale: float | None,
        metrics_for_char: Callable[[str], TMPGlyphMetrics | None],
    ) -> TMPRunMeasure:
        metric_text = run.text or " "
        cursor = 0.0
        bounds = TMPVisualBounds()
        last_index = len(metric_text) - 1
        for idx, ch in enumerate(metric_text):
            metrics = metrics_for_char(ch)
            if metrics is None:
                raise ValueError(f"source font metrics are unavailable for U+{ord(ch):04X}")
            glyph_origin_x, advance = cursor, metrics.advance
            if run.style.mspace is not None:
                mono_advance = self.tmp_mspace_advance(run.style.mspace)
                glyph_origin_x += (mono_advance - advance) * 0.5
                advance = mono_advance
            if run.text and self.tmp_native_visible_character(ch) and metrics.width > 0 and metrics.height > 0:
                raw_left = glyph_origin_x + metrics.bearing_x
                raw_right = raw_left + metrics.width
                top = -metrics.bearing_y
                bottom = top + metrics.height
                bounds.include(raw_left, raw_right, top, bottom)
            cursor += advance
            if idx != last_index:
                cursor += self.tmp_character_spacing_advance(run.style, font_name, font_size, current_em_scale)
        if not run.text:
            return TMPRunMeasure(cursor, 0.0, cursor, 0.0, 0.0)
        visual_left, visual_right, visual_top, visual_bottom = bounds.resolved((0.0, 0.0, 0.0, 0.0))
        return TMPRunMeasure(cursor, visual_left, visual_right, visual_top, visual_bottom)

    def tmp_run_glyph_audit(
        self,
        font: ImageFont.FreeTypeFont,
        run: TextRun,
        font_name: str,
        font_size: float,
        current_em_scale: float | None = None,
    ) -> list[dict[str, Any]]:
        glyphs: list[dict[str, Any]] = []
        cursor = 0.0
        last_index = len(run.text) - 1
        advance_scale_x = self.tmp_native_advance_scale_x(run.style)
        vertex_scale_x = self.tmp_native_vertex_scale_x(run.style)
        for idx, ch in enumerate(run.text):
            metrics, source = self.glyph_layout_metrics_with_source(font, ch, font_name, font_size)
            advance = metrics.advance
            glyph_origin_x = cursor
            if run.style.mspace is not None:
                mono_advance = self.tmp_mspace_advance(run.style.mspace)
                glyph_origin_x += (mono_advance - advance) * 0.5
                advance = mono_advance
            spacing = (
                self.tmp_character_spacing_advance(run.style, font_name, font_size, current_em_scale)
                if idx != last_index
                else 0.0
            )
            glyphs.append(
                {
                    "index": idx,
                    "char": ch,
                    "codepoint": ord(ch),
                    "metricSource": source,
                    "originX": glyph_origin_x,
                    "advance": advance,
                    "advanceScaleX": advance_scale_x,
                    "vertexScaleX": vertex_scale_x,
                    "postCharacterSpacing": spacing,
                    "metrics": {
                        "width": metrics.width,
                        "height": metrics.height,
                        "bearingX": metrics.bearing_x,
                        "bearingY": metrics.bearing_y,
                        "advance": metrics.advance,
                    },
                }
            )
            cursor += advance * advance_scale_x + spacing
        return glyphs

    def run_base_advance(
        self,
        font: ImageFont.FreeTypeFont,
        run: TextRun,
        font_name: str = "",
        font_size: float = 0.0,
    ) -> float:
        """Run glyph advance before TMP's explicit xAdvance spacing term."""
        if not run.text:
            return self.glyph_advance(font, " ", font_name, font_size)
        if run.style.mspace is not None:
            base = self.tmp_mspace_advance(run.style.mspace) * len(run.text)
        else:
            base = sum(self.glyph_advance(font, ch, font_name, font_size) for ch in run.text)
        return max(1.0, base)

    def run_bbox(
        self,
        font: ImageFont.FreeTypeFont,
        run: TextRun,
        font_name: str = "",
        font_size: float = 0.0,
    ) -> tuple[int, int, int, int]:
        return self.tmp_run_bbox(font, run, font_name, font_size, fx_scale=False)

    def run_fx_bbox(
        self,
        font: ImageFont.FreeTypeFont,
        run: TextRun,
        font_name: str = "",
        font_size: float = 0.0,
    ) -> tuple[int, int, int, int]:
        return self.tmp_run_bbox(font, run, font_name, font_size, fx_scale=True)

    def tmp_run_bbox(
        self,
        font: ImageFont.FreeTypeFont,
        run: TextRun,
        font_name: str,
        font_size: float,
        *,
        fx_scale: bool,
    ) -> tuple[int, int, int, int]:
        if not run.text:
            return self.text_bbox(font, " ")
        cursor = 0.0
        bounds = TMPVisualBounds(left=0.0, right=0.0)
        last_index = len(run.text) - 1
        for idx, ch in enumerate(run.text):
            metric_char = self.tmp_render_glyph_char(font_name, ch, font_size)
            bbox = font.getbbox(metric_char)
            if self.tmp_native_visible_character(ch):
                left, right = self.tmp_run_glyph_horizontal_bounds(cursor, bbox, run.style, fx_scale)
                bounds.include(left, right, float(bbox[1]), float(bbox[3]))
            cursor += self.tmp_run_character_advance(font, run, ch, font_name, font_size, fx_scale)
            if idx != last_index:
                cursor += self.tmp_character_spacing_advance(run.style, font_name, font_size)
        bounds.include_horizontal(0.0, cursor)
        fallback_bbox = self.text_bbox(font, " ")
        left, right, top, bottom = bounds.resolved((0.0, cursor, float(fallback_bbox[1]), float(fallback_bbox[3])))
        return (math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom))

    def tmp_run_glyph_horizontal_bounds(
        self,
        cursor: float,
        bbox: tuple[int, int, int, int],
        style: TextStyle,
        fx_scale: bool,
    ) -> tuple[float, float]:
        raw_left = cursor + bbox[0]
        raw_right = cursor + bbox[2]
        if not fx_scale:
            return raw_left, raw_right
        center_x = (raw_left + raw_right) * 0.5
        scale_x = self.tmp_fx_scale_x(style)
        return (
            center_x + (raw_left - center_x) * scale_x,
            center_x + (raw_right - center_x) * scale_x,
        )

    def tmp_run_character_advance(
        self,
        font: ImageFont.FreeTypeFont,
        run: TextRun,
        ch: str,
        font_name: str,
        font_size: float,
        fx_scale: bool,
    ) -> float:
        advance = (
            self.tmp_mspace_advance(run.style.mspace)
            if run.style.mspace is not None
            else self.glyph_advance(font, ch, font_name, font_size)
        )
        return advance * self.tmp_fx_advance_scale_x(run.style) if fx_scale else advance

    def draw_text_run(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[float, float],
        run: TextRun,
        font: ImageFont.FreeTypeFont,
        fill: tuple[int, int, int, int],
        stroke_width: int,
        stroke_fill: tuple[int, int, int, int],
        font_name: str = "",
        font_size: float = 0.0,
    ) -> None:
        cursor = 0.0
        base_x, base_y = xy
        last_index = len(run.text) - 1
        for idx, ch in enumerate(run.text):
            metric_char = self.tmp_render_glyph_char(font_name, ch, font_size)
            if self.tmp_native_visible_character(ch):
                draw.text(
                    (base_x + cursor, base_y),
                    metric_char,
                    font=font,
                    fill=fill,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill,
                )
            if run.style.mspace is not None:
                advance = self.tmp_mspace_advance(run.style.mspace)
            else:
                advance = self.glyph_advance(font, ch, font_name, font_size)
            cursor += advance
            if idx != last_index:
                cursor += self.tmp_character_spacing_advance(run.style, font_name, font_size)

    def draw_text_mask_run(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[float, float],
        run: TextRun,
        font: ImageFont.FreeTypeFont,
        font_name: str,
        font_size: float,
    ) -> None:
        cursor = 0.0
        base_x, base_y = xy
        last_index = len(run.text) - 1
        for idx, ch in enumerate(run.text):
            metric_char = self.tmp_render_glyph_char(font_name, ch, font_size)
            if self.tmp_native_visible_character(ch):
                draw.text((base_x + cursor, base_y), metric_char, font=font, fill=255)
            if run.style.mspace is not None:
                advance = self.tmp_mspace_advance(run.style.mspace)
            else:
                advance = self.glyph_advance(font, ch, font_name, font_size)
            cursor += advance
            if idx != last_index:
                cursor += self.tmp_character_spacing_advance(run.style, font_name, font_size)

    def draw_text_mask_run_fx(
        self,
        target: Image.Image,
        xy: tuple[float, float],
        run: TextRun,
        font: ImageFont.FreeTypeFont,
        font_name: str,
        font_size: float,
    ) -> None:
        scale_x = self.tmp_fx_scale_x(run.style)
        if abs(scale_x - 1.0) < 1.0e-6:
            self.draw_text_mask_run(ImageDraw.Draw(target), xy, run, font, font_name, font_size)
            return
        cursor = 0.0
        base_x, base_y = xy
        last_index = len(run.text) - 1
        for idx, ch in enumerate(run.text):
            metric_char = self.tmp_render_glyph_char(font_name, ch, font_size)
            bbox = font.getbbox(metric_char)
            if self.tmp_native_visible_character(ch):
                raw_w = max(1, bbox[2] - bbox[0])
                raw_h = max(1, bbox[3] - bbox[1])
                glyph = Image.new("L", (raw_w, raw_h), 0)
                glyph_draw = ImageDraw.Draw(glyph)
                glyph_draw.text((-bbox[0], -bbox[1]), metric_char, font=font, fill=255)
                scaled_w = max(1, round(raw_w * scale_x))
                if scaled_w != raw_w:
                    glyph = glyph.resize((scaled_w, raw_h), Image.Resampling.BICUBIC)
                raw_left = base_x + cursor + bbox[0]
                raw_right = base_x + cursor + bbox[2]
                center_x = (raw_left + raw_right) * 0.5
                px = round(center_x - glyph.width * 0.5)
                py = round(base_y + bbox[1])
                region = target.crop((px, py, px + glyph.width, py + glyph.height))
                target.paste(ImageChops.lighter(region, glyph), (px, py))
            if run.style.mspace is not None:
                advance = self.tmp_mspace_advance(run.style.mspace)
            else:
                advance = self.glyph_advance(font, ch, font_name, font_size)
            cursor += advance * self.tmp_fx_advance_scale_x(run.style)
            if idx != last_index:
                cursor += self.tmp_character_spacing_advance(run.style, font_name, font_size)

    def tmp_sdf_asset(self, font_name: str) -> TMPFontAsset | None:
        return self.tmp_font_library.active_asset(font_name)

    def tmp_static_sdf_asset(self, font_name: str, run: TextRun) -> TMPFontAsset | None:
        active = self.tmp_font_library.active_asset(font_name)
        if active is not None and active.atlas_population_mode == 1 and self.tmp_dynamic_sdf:
            return None
        for asset in self.tmp_font_library.metric_asset_candidates(font_name, include_fallback=False):
            if not asset.has_static_glyphs or not asset.atlas_paths:
                continue
            if all(ch == " " or asset.glyphs.get(ord(ch)) is not None for ch in run.text):
                return asset
        return None

    def tmp_atlas_alpha(self, path: Path) -> Image.Image:
        cached = self._tmp_atlas_cache.get(path)
        if cached is not None:
            return cached
        atlas = self._decode_shared_image(path, "atlas_alpha")
        self._tmp_atlas_cache[path] = atlas
        return atlas

    def tmp_sdf_spread(self, asset: TMPFontAsset | None, font_size: float, scale_x: float) -> float:
        if asset is None:
            return 6.0
        return max(1.0, asset.gradient_scale * max(1.0, scale_x))

    def tmp_mesh_texcoord1_y(self, style: TextStyle) -> float:
        rich_scale = style.scale_x if self.tmp_scale_mode in {"x", "fx-center", "fx-native"} else 1.0
        value = DEFAULT_TMP_TEXCOORD1_Y * max(1.0, rich_scale)
        return -value if style.bold else value

    def tmp_shader_material(self, asset: TMPFontAsset | None) -> TMPShaderMaterial:
        if asset is None:
            return TMPShaderMaterial(
                gradient_scale=6.0,
                face_dilate=0.0,
                outline_width=0.0,
                outline_softness=0.0,
                weight_normal=0.0,
                weight_bold=0.75,
                underlay_offset_x=0.0,
                underlay_offset_y=0.0,
                underlay_softness=0.0,
                glow_offset=0.0,
                glow_outer=0.0,
                sharpness=0.0,
                scale_ratio_a=1.0,
                scale_ratio_b=1.0,
                scale_ratio_c=1.0,
            )
        return TMPShaderMaterial(
            gradient_scale=asset.gradient_scale,
            face_dilate=asset.face_dilate,
            outline_width=asset.outline_width,
            outline_softness=asset.outline_softness,
            weight_normal=asset.weight_normal,
            weight_bold=asset.weight_bold,
            underlay_offset_x=asset.underlay_offset_x,
            underlay_offset_y=asset.underlay_offset_y,
            underlay_softness=asset.underlay_softness,
            glow_offset=asset.glow_offset,
            glow_outer=asset.glow_outer,
            sharpness=asset.sharpness,
            scale_ratio_a=asset.scale_ratio_a,
            scale_ratio_b=asset.scale_ratio_b,
            scale_ratio_c=asset.scale_ratio_c,
        )

    def tmp_shader_ratios(
        self,
        asset: TMPFontAsset | None,
        outline_dilate: float,
        has_underlay: bool = True,
        has_glow: bool = False,
        has_ratios_keyword: bool = False,
    ) -> tuple[float, float, float]:
        material = self.tmp_shader_material(asset)
        gradient_scale = max(1.0e-6, material.gradient_scale)

        if has_ratios_keyword:
            return material.scale_ratio_a, material.scale_ratio_b, material.scale_ratio_c

        available = max(0.0, gradient_scale - TMP_SHADER_RATIO_CLAMP)
        weight_extent = max(material.weight_normal, material.weight_bold) * 0.25
        face_extent = material.face_dilate + weight_extent
        base_extent = max(1.0, face_extent + material.outline_width + material.outline_softness)
        ratio_a = available / (gradient_scale * base_extent)

        glow_extent = max(1.0, material.glow_offset + material.glow_outer)
        ratio_b = max(0.0, available - face_extent * available) / (gradient_scale * glow_extent)

        ratio_c = material.scale_ratio_c
        if has_underlay:
            underlay_extent = max(
                1.0,
                max(abs(material.underlay_offset_x), abs(material.underlay_offset_y))
                + outline_dilate
                + material.underlay_softness,
            )
            ratio_c = max(0.0, available - face_extent * available) / (gradient_scale * underlay_extent)

        return ratio_a, ratio_b, ratio_c

    def tmp_shader_padding(
        self,
        asset: TMPFontAsset | None,
        outline_dilate: float,
        enable_extra_padding: bool = False,
        has_underlay: bool = True,
        has_glow: bool = False,
    ) -> float:
        gradient_scale = asset.gradient_scale if asset is not None else 6.0
        face_dilate = asset.face_dilate if asset is not None else 0.0
        outline_width = asset.outline_width if asset is not None else 0.0
        outline_softness = asset.outline_softness if asset is not None else 0.0
        underlay_offset_x = asset.underlay_offset_x if asset is not None else 0.0
        underlay_offset_y = asset.underlay_offset_y if asset is not None else 0.0
        underlay_softness = asset.underlay_softness if asset is not None else 0.0
        glow_offset = asset.glow_offset if asset is not None else 0.0
        glow_outer = asset.glow_outer if asset is not None else 0.0

        scale_ratio_a, scale_ratio_b, scale_ratio_c = self.tmp_shader_ratios(
            asset,
            outline_dilate,
            has_underlay=has_underlay,
            has_glow=has_glow,
        )
        face_pad = face_dilate * scale_ratio_a
        base_pad = face_pad + (outline_width + outline_softness) * scale_ratio_a
        if has_glow:
            base_pad = max(base_pad, face_pad + (glow_offset + glow_outer) * scale_ratio_b)

        pads = [base_pad, base_pad, base_pad, base_pad]
        if has_underlay:
            underlay_base = face_pad + (outline_dilate + underlay_softness) * scale_ratio_c
            underlay_x = underlay_offset_x * scale_ratio_c
            underlay_y = underlay_offset_y * scale_ratio_c
            pads = [
                max(base_pad, underlay_base - underlay_x, 0.0),
                max(base_pad, underlay_base + underlay_x, 0.0),
                max(base_pad, underlay_base - underlay_y, 0.0),
                max(base_pad, underlay_base + underlay_y, 0.0),
            ]

        extra = TMP_EXTRA_PADDING if enable_extra_padding else 0.0
        return max(min(pad + extra, 1.0) * gradient_scale for pad in pads) + TMP_SHADER_PADDING_CONSTANT

    def tmp_native_vertex_padding(self, font_name: str, style: TextStyle, outline_dilate: float) -> float:
        asset = self.tmp_sdf_asset(font_name)
        padding = self.tmp_shader_padding(asset, outline_dilate, enable_extra_padding=True)
        element_scale = self.tmp_native_element_scale(font_name, style.size)
        return max(0.0, padding - TMP_VERTEX_PADDING_EXTRA) * element_scale

    def tmp_native_atlas_padding(self, asset: TMPFontAsset, style: TextStyle, outline_dilate: float) -> int:
        padding = max(
            0.0,
            self.tmp_shader_padding(asset, outline_dilate, enable_extra_padding=True) - TMP_VERTEX_PADDING_EXTRA,
        )
        return max(0, round(padding))

    def tmp_display_padding(self, asset: TMPFontAsset | None, outline_dilate: float, font_size: float) -> int:
        point_size = max(1.0, asset.point_size if asset is not None else TMP_FACE_POINT_SIZE)
        font_scale = max(0.0, font_size / point_size)
        return max(1, math.ceil(self.tmp_shader_padding(asset, outline_dilate, enable_extra_padding=True) * font_scale))

    def shifted_sdf_field(self, field: Any, dx: float, dy: float) -> Any:
        import numpy as np

        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return field
        shift_x = int(round(dx))
        shift_y = int(round(dy))
        out = np.zeros_like(field)
        height, width = field.shape
        src_x0 = max(0, shift_x)
        src_y0 = max(0, shift_y)
        src_x1 = min(width, width + shift_x)
        src_y1 = min(height, height + shift_y)
        dst_x0 = max(0, -shift_x)
        dst_y0 = max(0, -shift_y)
        dst_x1 = dst_x0 + max(0, src_x1 - src_x0)
        dst_y1 = dst_y0 + max(0, src_y1 - src_y0)
        if src_x1 > src_x0 and src_y1 > src_y0:
            out[dst_y0:dst_y1, dst_x0:dst_x1] = field[src_y0:src_y1, src_x0:src_x1]
        return out

    def tmp_sdf_shading_scalars(
        self,
        asset: TMPFontAsset | None,
        style: TextStyle,
        outline_color: str,
        outline_dilate: float,
        sdf_scale: float | None = None,
    ) -> TMPSdfShadingScalars:
        """The scalar half of shade_tmp_sdf_field — everything derived from asset/style before the
        per-pixel math. Single source for the Pillow shader below AND the Skia SdfQuad node
        (Phase 2): the fragile TMP material semantics live here once; both backends are dumb
        ``clip(field*scale - w)`` evaluators of these numbers.
        """
        material = self.tmp_shader_material(asset)
        scale_ratio_a, _, scale_ratio_c = self.tmp_shader_ratios(asset, outline_dilate)

        texcoord_scale = abs(sdf_scale) if sdf_scale is not None else abs(self.tmp_mesh_texcoord1_y(style))
        raw_scale = texcoord_scale * material.gradient_scale * (material.sharpness + 1.0)
        face_scale = raw_scale / (material.outline_softness * scale_ratio_a * raw_scale + 1.0)
        weight = material.weight_bold if style.bold else material.weight_normal
        bias = 0.5 - 0.5 * (weight * 0.25 + material.face_dilate) * scale_ratio_a
        face_w = bias * face_scale - 0.5

        underlay = self.tmp_sdf_underlay_scalars(
            material,
            outline_color,
            outline_dilate,
            scale_ratio_c,
            raw_scale,
            bias,
        )
        return TMPSdfShadingScalars(
            face_scale=face_scale,
            face_w=face_w,
            alpha=style.alpha,
            face_color=hex_to_rgba(style.color, 1.0)[:3],
            underlay=underlay,
        )

    def tmp_sdf_underlay_scalars(
        self,
        material: TMPShaderMaterial,
        outline_color: str,
        outline_dilate: float,
        scale_ratio_c: float,
        raw_scale: float,
        bias: float,
    ) -> TMPSdfUnderlayScalars | None:
        if abs(outline_dilate) <= 1.0e-6:
            return None
        underlay_scale = raw_scale / (material.underlay_softness * scale_ratio_c * raw_scale + 1.0)
        underlay_width = outline_dilate * scale_ratio_c * underlay_scale
        underlay_w = bias * underlay_scale - 0.5 - underlay_width * 0.5
        offset_x = -material.underlay_offset_x * scale_ratio_c * material.gradient_scale
        offset_y = -material.underlay_offset_y * scale_ratio_c * material.gradient_scale
        shift_x, shift_y = self.tmp_sdf_field_shift(offset_x, offset_y)
        return TMPSdfUnderlayScalars(
            scale=underlay_scale,
            w=underlay_w,
            shift_x=shift_x,
            shift_y=shift_y,
            color=hex_to_rgba(outline_color, 1.0)[:3],
        )

    def tmp_sdf_field_shift(self, offset_x: float, offset_y: float) -> tuple[int, int]:
        # shifted_sdf_field semantics, pre-resolved: |both| < 0.5 short-circuits to no shift,
        # otherwise banker's-rounded integer pixel translation (zero fill happens per-pixel).
        if abs(offset_x) < 0.5 and abs(offset_y) < 0.5:
            return 0, 0
        return int(round(offset_x)), int(round(offset_y))

    def shade_tmp_sdf_field(
        self,
        field: Any,
        asset: TMPFontAsset | None,
        style: TextStyle,
        outline_color: str,
        outline_dilate: float,
        sdf_scale: float | None = None,
    ) -> Image.Image:
        scalars = self.tmp_sdf_shading_scalars(asset, style, outline_color, outline_dilate, sdf_scale)
        return self._shade_field_with_scalars(field, scalars)

    def _shade_field_with_scalars(self, field: Any, scalars: TMPSdfShadingScalars) -> Image.Image:
        """Array half of shade_tmp_sdf_field: per-pixel evaluation of the frozen scalars over a
        float32 [0, 1] field. Single source for the Pillow shader AND the byte-identity reference
        for the Skia SdfQuad node — the direct decorative path calls this with DirectSdfQuad."""
        import numpy as np

        face_alpha = np.clip(field * scalars.face_scale - scalars.face_w, 0.0, 1.0) * scalars.alpha

        if scalars.underlay is not None:
            u = scalars.underlay
            # Re-enters shifted_sdf_field with the pre-rounded integer shift: int(round(int)) is
            # exact and the <0.5 short-circuit maps to shift (0, 0), so behavior is unchanged.
            underlay_field = self.shifted_sdf_field(field, float(u.shift_x), float(u.shift_y))
            underlay_alpha = np.clip(underlay_field * u.scale - u.w, 0.0, 1.0) * scalars.alpha
        else:
            underlay_alpha = np.zeros_like(face_alpha)

        face_rgba = np.array([*scalars.face_color, 255], dtype=np.float32) / 255.0
        # When underlay is None, underlay_alpha is all zeros, so the color term is multiplied out;
        # (0, 0, 0) keeps the math bit-identical to the pre-split code's unused outline color.
        underlay_color = scalars.underlay.color if scalars.underlay is not None else (0, 0, 0)
        underlay_rgba = np.array([*underlay_color, 255], dtype=np.float32) / 255.0
        face_a = face_alpha * face_rgba[3]
        underlay_a = underlay_alpha * underlay_rgba[3]
        out_a = face_a + underlay_a * (1.0 - face_a)
        rgb_premul = face_rgba[:3] * face_a[:, :, None] + underlay_rgba[:3] * underlay_a[:, :, None] * (
            1.0 - face_a[:, :, None]
        )
        return Image.fromarray(rgba_from_premul(rgb_premul, out_a), "RGBA")

    def render_tmp_static_atlas_run(
        self,
        font_name: str,
        run: TextRun,
        font_size: float,
        outline_color: str,
        outline_dilate: float,
    ) -> tuple[Image.Image, tuple[int, int, int, int], int] | None:
        asset = self.tmp_static_sdf_asset(font_name, run)
        if asset is None:
            return None
        style = run.style
        prepared = self.tmp_static_atlas_placements(font_name, run, font_size, asset)
        if prepared is None:
            return None
        placements, bbox = prepared
        pad = self.tmp_display_padding(asset, outline_dilate, font_size)
        field_img = self.tmp_static_atlas_field(asset, placements, bbox, pad)

        if self.tmp_scale_mode == "x" and not math.isclose(style.scale_x, 1.0, abs_tol=1.0e-9):
            scaled_size = ensure_raster_size(
                (max(1, round(field_img.width * style.scale_x)), field_img.height),
                max_pixels=self.max_layer_pixels,
                label="custom profile scaled TMP static SDF field",
            )
            field_img = field_img.resize(scaled_size, Image.Resampling.BICUBIC)

        import numpy as np

        field = np.asarray(field_img, dtype=np.float32) / 255.0
        return self.shade_tmp_sdf_field(field, asset, style, outline_color, outline_dilate), bbox, pad

    def tmp_static_atlas_placements(
        self,
        font_name: str,
        run: TextRun,
        font_size: float,
        asset: TMPFontAsset,
    ) -> tuple[list[tuple[TMPGlyphMetrics, float, float, float, float]], tuple[int, int, int, int]] | None:
        style = run.style
        placements: list[tuple[TMPGlyphMetrics, float, float, float, float]] = []
        font_scale = font_size / max(1.0, asset.point_size)
        cursor = 0.0
        min_x = 0.0
        min_y = 0.0
        max_x = 1.0
        max_y = max(1.0, (asset.ascent_line - asset.descent_line) * font_scale)
        fx_scale_x = self.tmp_fx_scale_x(style) if self.tmp_scale_mode in {"fx-center", "fx-native"} else 1.0
        last_index = len(run.text) - 1
        for idx, ch in enumerate(run.text):
            glyph_char = self.tmp_render_glyph_char(font_name, ch, font_size)
            metrics = asset.glyphs.get(ord(glyph_char))
            if metrics is None:
                return None
            scale = font_scale * metrics.glyph_scale
            if self.tmp_native_visible_character(ch) and metrics.rect_w > 0 and metrics.rect_h > 0:
                x = cursor + metrics.bearing_x * font_scale
                y = (asset.ascent_line - metrics.bearing_y) * font_scale
                w = metrics.rect_w * scale
                h = metrics.rect_h * scale
                center_x = x + w * 0.5
                x = center_x - w * fx_scale_x * 0.5
                w *= fx_scale_x
                placements.append((metrics, x, y, w, h))
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x + w)
                max_y = max(max_y, y + h)
            if style.mspace is not None:
                advance = self.tmp_mspace_advance(style.mspace)
            else:
                advance = metrics.advance * font_scale
            cursor += advance * self.tmp_fx_advance_scale_x(style)
            if idx != last_index:
                cursor += self.tmp_character_spacing_advance(style, font_name, font_size)
        max_x = max(max_x, cursor)
        if not placements:
            return None
        bbox = (math.floor(min_x), math.floor(min_y), math.ceil(max_x), math.ceil(max_y))
        return placements, bbox

    def tmp_static_atlas_field(
        self,
        asset: TMPFontAsset,
        placements: list[tuple[TMPGlyphMetrics, float, float, float, float]],
        bbox: tuple[int, int, int, int],
        pad: int,
    ) -> Image.Image:
        field_size = ensure_raster_size(
            (max(1, bbox[2] - bbox[0] + pad * 2), max(1, bbox[3] - bbox[1] + pad * 2)),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP static SDF field",
        )
        field_img = Image.new("L", field_size, 0)
        for metrics, x, y, w, h in placements:
            atlas_path = asset.atlas_paths[min(metrics.atlas_index, len(asset.atlas_paths) - 1)]
            atlas = self.tmp_atlas_alpha(atlas_path)
            top = max(0, round(atlas.height - metrics.rect_y - metrics.rect_h))
            left = max(0, metrics.rect_x)
            crop = atlas.crop((left, top, left + metrics.rect_w, top + metrics.rect_h))
            glyph = crop.resize((max(1, round(w)), max(1, round(h))), Image.Resampling.BICUBIC)
            px = round(pad + x - bbox[0])
            py = round(pad + y - bbox[1])
            region = field_img.crop((px, py, px + glyph.width, py + glyph.height))
            field_img.paste(ImageChops.lighter(region, glyph), (px, py))
        return field_img

    def render_tmp_dynamic_sdf_run(
        self,
        font_name: str,
        font_path: Path,
        run: TextRun,
        font_size: float,
        outline_color: str,
        outline_dilate: float,
    ) -> tuple[Image.Image, tuple[int, int, int, int], int] | None:
        asset = self.tmp_sdf_asset(font_name)
        if asset is None:
            return None
        source_path = self.tmp_font_library.runtime_source_font_path(asset)
        if source_path is None:
            return None
        style = run.style
        sample_size = max(1.0, asset.point_size)
        supersample = max(1.0, TMP_DYNAMIC_SDF_SUPERSAMPLE)
        raster_size = sample_size * supersample
        raster_run = scale_tmp_spacing(run, supersample)
        sample_font = load_font(source_path, raster_size)
        sample_bbox = (
            self.run_fx_bbox(sample_font, raster_run, font_name, raster_size)
            if self.tmp_scale_mode in {"fx-center", "fx-native"}
            else self.run_bbox(sample_font, raster_run, font_name, raster_size)
        )
        sample_pad = max(
            1,
            math.ceil(self.tmp_shader_padding(asset, outline_dilate, enable_extra_padding=True) * supersample),
        )
        w = max(1, sample_bbox[2] - sample_bbox[0] + sample_pad * 2)
        h = max(1, sample_bbox[3] - sample_bbox[1] + sample_pad * 2)
        w, h = ensure_raster_size(
            (w, h),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP dynamic SDF mask",
        )
        mask = Image.new("L", (w, h), 0)
        if self.tmp_scale_mode in {"fx-center", "fx-native"}:
            self.draw_text_mask_run_fx(
                mask,
                (sample_pad - sample_bbox[0], sample_pad - sample_bbox[1]),
                raster_run,
                sample_font,
                font_name,
                raster_size,
            )
        else:
            self.draw_text_mask_run(
                ImageDraw.Draw(mask),
                (sample_pad - sample_bbox[0], sample_pad - sample_bbox[1]),
                raster_run,
                sample_font,
                font_name,
                raster_size,
            )
        try:
            field = alpha_mask_to_sdf_field(
                mask,
                asset.gradient_scale * supersample,
                tmp_dynamic_sdf_alpha_threshold(asset),
            )
        except ImportError:
            return None

        import numpy as np

        display_scale = font_size / raster_size
        scale_x = style.scale_x if self.tmp_scale_mode == "x" else 1.0
        field_img = Image.fromarray(np.clip(np.rint(field * 255.0), 0, 255).astype(np.uint8), "L")
        display_size = ensure_raster_size(
            (
                max(1, round(field_img.width * display_scale * scale_x)),
                max(1, round(field_img.height * display_scale)),
            ),
            max_pixels=self.max_layer_pixels,
            label="custom profile displayed TMP dynamic SDF field",
        )
        field_img = field_img.resize(display_size, Image.Resampling.BICUBIC)
        field = np.asarray(field_img, dtype=np.float32) / 255.0
        bbox = (
            math.floor(sample_bbox[0] * display_scale * scale_x),
            math.floor(sample_bbox[1] * display_scale),
            math.ceil(sample_bbox[2] * display_scale * scale_x),
            math.ceil(sample_bbox[3] * display_scale),
        )
        pad = max(1, round(sample_pad * display_scale))
        return self.shade_tmp_sdf_field(field, asset, style, outline_color, outline_dilate), bbox, pad

    def _font_signature(self, path: Path) -> tuple[int, int]:
        """Per-instance memo of one os.stat per font file per request (L2 key component)."""
        text = str(path)
        sig = self._font_signature_memo.get(text)
        if sig is None:
            sig = optional_file_signature(path)
            self._font_signature_memo[text] = sig
        return sig

    def _store_vector_glyph(self, key, l2_key, value):
        # Negative results stay L1-only (per-request, the pre-cache behavior): the None sites
        # sit behind broad except blocks, so a TRANSIENT failure (MemoryError/EMFILE/IO blip)
        # would otherwise be laundered into a process-lifetime "font cannot produce this glyph"
        # verdict under an unchanged file signature. Re-probing a genuinely missing glyph costs
        # one fontTools/FT round per request — the price of never poisoning the pool.
        self._tmp_vector_glyph_cache[key] = value
        if value is not None:
            GLYPH_CONTOUR_CACHE.set(l2_key, value)
        return value

    def _store_dynamic_glyph(self, key, l2_key, value):
        # Same rule as _store_vector_glyph: only successful renders enter the process pool.
        self._tmp_dynamic_glyph_cache[key] = value
        if value is not None:
            GLYPH_SDF_CACHE.set(l2_key, value)
        return value

    def tmp_vector_glyph_contours(
        self,
        source_path: Path,
        ch: str,
        sample_size: float,
    ) -> tuple[list[Any], Any] | None:
        key = (str(source_path), ch[0], round(sample_size, 4))
        if key in self._tmp_vector_glyph_cache:
            return self._tmp_vector_glyph_cache[key]
        l2_key = (key[0], *self._font_signature(source_path), key[1], key[2])
        l2_cached = GLYPH_CONTOUR_CACHE.get(l2_key)
        if l2_cached is not MISSING:
            self._tmp_vector_glyph_cache[key] = l2_cached
            return l2_cached
        try:
            import numpy as np
            from fontTools.pens.recordingPen import DecomposingRecordingPen
            from fontTools.ttLib import TTFont
        except ImportError:
            self._store_vector_glyph(key, l2_key, None)
            return None

        try:
            font = TTFont(source_path)
            glyph_set = font.getGlyphSet()
            glyph_name = font.getBestCmap().get(ord(ch[0]))
            if not glyph_name:
                self._store_vector_glyph(key, l2_key, None)
                return None
            pen = DecomposingRecordingPen(glyph_set)
            glyph_set[glyph_name].draw(pen)
            units_per_em = float(font["head"].unitsPerEm or 1000)
        except Exception:
            self._store_vector_glyph(key, l2_key, None)
            return None

        builder = _TMPGlyphContourBuilder(sample_size / max(1.0, units_per_em))
        for op, args in pen.value:
            builder.consume(op, args)
        contours = builder.finish()
        if not contours:
            self._store_vector_glyph(key, l2_key, None)
            return None
        packed = [np.asarray(contour, dtype=np.float32) for contour in contours if len(contour) >= 2]
        if not packed:
            self._store_vector_glyph(key, l2_key, None)
            return None
        for arr in packed:
            arr.flags.writeable = False  # shared across threads via the process pool
        result = (tuple(packed), np)  # tuple: the container is shared too
        self._store_vector_glyph(key, l2_key, result)
        return result

    def tmp_vector_glyph_sdf_field(
        self,
        source_path: Path,
        ch: str,
        sample_size: float,
        bbox: tuple[int, int, int, int],
        pad: int,
        asset: TMPFontAsset,
    ) -> Image.Image | None:
        outlines = self.tmp_vector_glyph_contours(source_path, ch, sample_size)
        if outlines is None:
            return None
        contours, np = outlines
        width, height = ensure_raster_size(
            (max(1, bbox[2] - bbox[0] + pad * 2), max(1, bbox[3] - bbox[1] + pad * 2)),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP vector glyph field",
        )
        xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
        px = float(bbox[0] - pad) + xs + 0.5
        py = -(float(bbox[1] - pad) + ys + 0.5)
        min_distance = np.full((height, width), 1.0e9, dtype=np.float32)
        winding = np.zeros((height, width), dtype=np.int16)

        for contour in contours:
            next_points = np.roll(contour, -1, axis=0)
            for (ax, ay), (bx, by) in zip(contour, next_points):
                vx = bx - ax
                vy = by - ay
                wx = px - ax
                wy = py - ay
                length_sq = vx * vx + vy * vy
                if length_sq <= 1.0e-12:
                    distance = np.sqrt(wx * wx + wy * wy)
                else:
                    t = np.clip((wx * vx + wy * vy) / length_sq, 0.0, 1.0)
                    dx = px - (ax + t * vx)
                    dy = py - (ay + t * vy)
                    distance = np.sqrt(dx * dx + dy * dy)
                min_distance = np.minimum(min_distance, distance)

                cross = vx * (py - ay) - (px - ax) * vy
                winding += ((ay <= py) & (by > py) & (cross > 0.0)).astype(np.int16)
                winding -= ((ay > py) & (by <= py) & (cross < 0.0)).astype(np.int16)

        signed_distance = np.where(winding != 0, min_distance, -min_distance)
        spread = max(1.0, asset.gradient_scale - TMP_DYNAMIC_SDF_VECTOR_SPREAD_BIAS)
        field = np.clip(0.5 + signed_distance / (2.0 * spread), 0.0, 1.0)
        return Image.fromarray(np.clip(np.rint(field * 255.0), 0, 255).astype(np.uint8), "L")

    def tmp_dynamic_font_field(
        self,
        font_name: str,
        ch: str,
        style: TextStyle,
        outline_dilate: float,
        char_info: TMPNativeCharacterInfo,
        native_field_size: tuple[int, int] | None = None,
    ) -> tuple[TMPDynamicFontField, TMPFontAsset] | None:
        """Build the pixel-free native descriptor for one dynamic/fallback TMP glyph."""

        if self.use_em_block(TextRun(ch, style)):
            return None
        active = self.tmp_sdf_asset(font_name)
        if active is None or not ch or ch == " ":
            return None

        glyph_char = ch[0]
        selected: tuple[TMPFontAsset, Path, float, TMPGlyphMetrics] | None = None
        for candidate in self.tmp_font_library.metric_asset_candidates(font_name, include_fallback=True):
            source_path = self.tmp_font_library.runtime_source_font_path(candidate)
            if source_path is None:
                continue
            sample_size = max(1.0, candidate.point_size)
            metrics = self.tmp_font_library._source_glyph_metrics_for_asset(candidate, glyph_char, sample_size)
            if metrics is not None:
                selected = candidate, source_path, sample_size, metrics
                break
        if selected is None:
            return None

        asset, source_path, sample_size, metrics = selected
        bbox = (
            math.floor(metrics.bearing_x),
            math.floor(-metrics.bearing_y),
            math.ceil(metrics.bearing_x + metrics.width),
            math.ceil(-metrics.bearing_y + metrics.height),
        )
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return None
        padding = max(1, math.ceil(asset.atlas_padding + 1.0))
        active_point_size = max(1.0, active.point_size)
        atlas_pad = self.tmp_native_atlas_padding(asset, style, outline_dilate)
        crop_padding = min(padding, max(0, round(atlas_pad * sample_size / active_point_size)))
        field_size = ensure_raster_size(
            native_field_size or self.tmp_native_unrotated_quad_size(char_info),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP native dynamic glyph quad",
        )
        ensure_raster_size(
            (bbox[2] - bbox[0] + padding * 2, bbox[3] - bbox[1] + padding * 2),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP native dynamic glyph source",
        )
        return (
            TMPDynamicFontField(
                font_path=source_path,
                codepoint=ord(glyph_char),
                sample_size=sample_size,
                bbox=bbox,
                padding=padding,
                crop_padding=crop_padding,
                field_size=field_size,
                spread=max(1.0, asset.gradient_scale - TMP_DYNAMIC_SDF_VECTOR_SPREAD_BIAS),
            ),
            asset,
        )

    def tmp_dynamic_glyph_sdf(
        self,
        font_name: str,
        font_path: Path,
        ch: str,
        style: TextStyle | None = None,
    ) -> tuple[TMPDynamicGlyphSDF, TMPFontAsset] | None:
        if style is not None and self.use_em_block(TextRun(ch, style)):
            return None
        source = self.tmp_dynamic_glyph_source(font_name, ch)
        if source is None:
            return None
        asset, source_path, sample_size, glyph_char = source
        key, l2_key = self.tmp_dynamic_glyph_cache_keys(source_path, asset, glyph_char, sample_size)
        cache_hit, cached = self.tmp_cached_dynamic_glyph(key, l2_key)
        if cache_hit:
            return (cached, asset) if cached is not None else None

        cached = self.build_tmp_dynamic_glyph_sdf(source_path, glyph_char, sample_size, asset)
        self._store_dynamic_glyph(key, l2_key, cached)
        return (cached, asset) if cached is not None else None

    def tmp_dynamic_glyph_source(
        self,
        font_name: str,
        ch: str,
    ) -> tuple[TMPFontAsset, Path, float, str] | None:
        active = self.tmp_sdf_asset(font_name)
        if active is None or not ch or ch == " ":
            return None
        asset = active
        sample_size = max(1.0, active.point_size)
        for candidate in self.tmp_font_library.metric_asset_candidates(font_name, include_fallback=True):
            if self.tmp_font_library.runtime_source_font_path(candidate) is None:
                continue
            if self.tmp_font_library._source_glyph_metrics_for_asset(candidate, ch[0], sample_size) is not None:
                asset = candidate
                sample_size = max(1.0, candidate.point_size)
                break
        source_path = self.tmp_font_library.runtime_source_font_path(asset)
        if source_path is None:
            return None
        return asset, source_path, sample_size, ch[0]

    def tmp_dynamic_glyph_cache_keys(
        self,
        source_path: Path,
        asset: TMPFontAsset,
        glyph_char: str,
        sample_size: float,
    ) -> tuple[tuple[str, str, str, float], tuple[Any, ...]]:
        key = (str(source_path), asset.name, glyph_char, round(sample_size, 4))
        l2_key = (
            key[0],
            *self._font_signature(source_path),
            key[1],
            key[2],
            key[3],
            round(asset.gradient_scale, 4),
            round(asset.atlas_padding, 4),
        )
        return key, l2_key

    def tmp_cached_dynamic_glyph(
        self,
        key: tuple[str, str, str, float],
        l2_key: tuple[Any, ...],
    ) -> tuple[bool, TMPDynamicGlyphSDF | None]:
        if key in self._tmp_dynamic_glyph_cache:
            return True, self._tmp_dynamic_glyph_cache[key]
        # L2 adds what the instance key pins implicitly: the font file's signature plus the
        # metadata floats that enter the SDF math (gradient_scale) and padding (atlas_padding);
        # asset.name stays because tmp_dynamic_sdf_alpha_threshold maps name -> threshold.
        l2_cached = GLYPH_SDF_CACHE.get(l2_key)
        if l2_cached is MISSING:
            return False, None
        self._tmp_dynamic_glyph_cache[key] = l2_cached
        return True, l2_cached

    def tmp_dynamic_glyph_bounds(
        self,
        ft: Any,
        source_path: Path,
        glyph_char: str,
        size: float,
    ) -> tuple[tuple[int, int, int, int], Image.Image | None, int, int] | None:
        rendered = ft.glyph_bitmap(source_path, glyph_char, size) if ft is not None else None
        if rendered is not None:
            glyph_mask, bitmap_left, bitmap_top, metrics = rendered
            bbox_left = math.floor(min(metrics.bearing_x, float(bitmap_left)))
            bbox_top = math.floor(min(-metrics.bearing_y, float(-bitmap_top)))
            bbox_right = math.ceil(max(metrics.bearing_x + metrics.width, float(bitmap_left + glyph_mask.width)))
            bbox_bottom = math.ceil(max(-metrics.bearing_y + metrics.height, float(-bitmap_top + glyph_mask.height)))
            return (bbox_left, bbox_top, bbox_right, bbox_bottom), glyph_mask, bitmap_left, bitmap_top

        sample_font = load_font(source_path, size)
        bbox = sample_font.getbbox(glyph_char)
        if bbox is None:
            return None
        return (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])), None, 0, 0

    def build_tmp_dynamic_glyph_sdf(
        self,
        source_path: Path,
        glyph_char: str,
        sample_size: float,
        asset: TMPFontAsset,
    ) -> TMPDynamicGlyphSDF | None:
        ft = freetype_metrics()
        native_bounds = self.tmp_dynamic_glyph_bounds(ft, source_path, glyph_char, sample_size)
        if native_bounds is None:
            return None
        native_bbox, _, _, _ = native_bounds
        # Runtime TMP atlas rects behave like atlas_padding + 1 around the
        # cropped glyph field.
        native_pad = max(1, math.ceil(asset.atlas_padding + 1.0))
        vector_field = self.tmp_vector_glyph_sdf_field(
            source_path,
            glyph_char,
            sample_size,
            native_bbox,
            native_pad,
            asset,
        )
        if vector_field is not None:
            return TMPDynamicGlyphSDF(
                field=vector_field,
                bbox=(int(native_bbox[0]), int(native_bbox[1]), int(native_bbox[2]), int(native_bbox[3])),
                pad=native_pad,
                sample_size=sample_size,
            )

        supersample = max(1.0, TMP_DYNAMIC_SDF_SUPERSAMPLE)
        raster_size = sample_size * supersample
        raster_pad = max(1, math.ceil(asset.atlas_padding * supersample))
        raster_bounds = self.tmp_dynamic_glyph_bounds(ft, source_path, glyph_char, raster_size)
        if raster_bounds is None:
            return None
        mask = self.tmp_dynamic_glyph_mask(source_path, glyph_char, raster_size, raster_pad, raster_bounds)
        try:
            field = alpha_mask_to_sdf_field(
                mask,
                asset.gradient_scale * supersample,
                tmp_dynamic_sdf_alpha_threshold(asset),
            )
        except ImportError:
            return None

        import numpy as np

        field_img = Image.fromarray(np.clip(np.rint(field * 255.0), 0, 255).astype(np.uint8), "L")
        native_field_size = ensure_raster_size(
            (
                max(1, native_bbox[2] - native_bbox[0] + native_pad * 2),
                max(1, native_bbox[3] - native_bbox[1] + native_pad * 2),
            ),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP native glyph field",
        )
        if field_img.size != native_field_size:
            field_img = field_img.resize(native_field_size, Image.Resampling.BICUBIC)
        return TMPDynamicGlyphSDF(
            field=field_img,
            bbox=(int(native_bbox[0]), int(native_bbox[1]), int(native_bbox[2]), int(native_bbox[3])),
            pad=native_pad,
            sample_size=sample_size,
        )

    def tmp_dynamic_glyph_mask(
        self,
        source_path: Path,
        glyph_char: str,
        raster_size: float,
        raster_pad: int,
        raster_bounds: tuple[tuple[int, int, int, int], Image.Image | None, int, int],
    ) -> Image.Image:
        raster_bbox, raster_glyph_mask, bitmap_left, bitmap_top = raster_bounds
        bbox_left, bbox_top, bbox_right, bbox_bottom = raster_bbox
        size = ensure_raster_size(
            (
                max(1, bbox_right - bbox_left + raster_pad * 2),
                max(1, bbox_bottom - bbox_top + raster_pad * 2),
            ),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP dynamic glyph mask",
        )
        mask = Image.new("L", size, 0)
        if raster_glyph_mask is not None:
            mask.paste(raster_glyph_mask, (raster_pad + bitmap_left - bbox_left, raster_pad - bitmap_top - bbox_top))
            return mask
        sample_font = load_font(source_path, raster_size)
        draw = ImageDraw.Draw(mask)
        draw.text((raster_pad - bbox_left, raster_pad - bbox_top), glyph_char, font=sample_font, fill=255)
        return mask

    def render_tmp_dynamic_sdf_run_from_glyphs(
        self,
        font_name: str,
        font_path: Path,
        run: TextRun,
        font_size: float,
        outline_color: str,
        outline_dilate: float,
    ) -> tuple[Image.Image, tuple[int, int, int, int], int] | None:
        asset = self.tmp_sdf_asset(font_name)
        if asset is None or asset.atlas_population_mode != 1 or not self.tmp_dynamic_sdf:
            return None
        style = run.style
        glyphs: list[TMPDynamicRunGlyph] = []
        retained_glyph_bytes = 0
        cursor = 0.0
        min_x = 0.0
        min_y = 0.0
        max_x = 1.0
        max_y = 1.0
        max_pad = 1
        last_index = len(run.text) - 1
        display_font = load_font(font_path, font_size)
        fx_scale_x = self.tmp_fx_scale_x(style) if self.tmp_scale_mode in {"fx-center", "fx-native"} else 1.0
        advance_scale_x = self.tmp_fx_advance_scale_x(style)
        for idx, ch in enumerate(run.text):
            glyph_char = self.tmp_render_glyph_char(font_name, ch, font_size)
            glyph_origin_x, advance = self.tmp_dynamic_run_glyph_advance(
                display_font,
                ch,
                font_name,
                font_size,
                style,
                cursor,
            )
            glyph = self.prepare_tmp_dynamic_run_glyph(
                font_name,
                font_path,
                glyph_char,
                style,
                font_size,
                glyph_origin_x,
                fx_scale_x,
                outline_color,
                outline_dilate,
            )
            if glyph is not None:
                retained_glyph_bytes = self._reserve_retained_raster_bytes(
                    retained_glyph_bytes,
                    glyph.image.width * glyph.image.height * 4,
                    label="custom profile TMP run",
                )
                glyphs.append(glyph)
                min_x = min(min_x, glyph.origin_x + glyph.bbox[0])
                min_y = min(min_y, float(glyph.bbox[1]))
                max_x = max(max_x, glyph.origin_x + glyph.bbox[2])
                max_y = max(max_y, float(glyph.bbox[3]))
                max_pad = max(max_pad, glyph.pad)
            cursor += advance * advance_scale_x
            if idx != last_index:
                cursor += self.tmp_character_spacing_advance(style, font_name, font_size)
        max_x = max(max_x, cursor)
        if not glyphs:
            return None

        bbox = (math.floor(min_x), math.floor(min_y), math.ceil(max_x), math.ceil(max_y))
        return self.compose_tmp_dynamic_run_glyphs(glyphs, bbox, max_pad, style)

    def tmp_dynamic_run_glyph_advance(
        self,
        display_font: ImageFont.FreeTypeFont,
        char: str,
        font_name: str,
        font_size: float,
        style: TextStyle,
        cursor: float,
    ) -> tuple[float, float]:
        glyph_origin_x = cursor
        advance = self.glyph_advance(display_font, char, font_name, font_size)
        if style.mspace is not None:
            mono_advance = self.tmp_mspace_advance(style.mspace)
            glyph_origin_x += (mono_advance - advance) * 0.5
            advance = mono_advance
        return glyph_origin_x, advance

    def prepare_tmp_dynamic_run_glyph(
        self,
        font_name: str,
        font_path: Path,
        glyph_char: str,
        style: TextStyle,
        font_size: float,
        glyph_origin_x: float,
        fx_scale_x: float,
        outline_color: str,
        outline_dilate: float,
    ) -> TMPDynamicRunGlyph | None:
        dynamic = self.tmp_dynamic_glyph_sdf(font_name, font_path, glyph_char, style)
        if dynamic is None:
            return None
        cached, glyph_asset = dynamic
        display_scale = font_size / max(1.0, cached.sample_size)
        bbox = (
            math.floor(cached.bbox[0] * display_scale),
            math.floor(cached.bbox[1] * display_scale),
            math.ceil(cached.bbox[2] * display_scale),
            math.ceil(cached.bbox[3] * display_scale),
        )
        pad = max(1, round(cached.pad * display_scale))
        field_size = ensure_raster_size(
            (
                max(1, round(cached.field.width * display_scale)),
                max(1, round(cached.field.height * display_scale)),
            ),
            max_pixels=self.max_layer_pixels,
            label="custom profile displayed TMP glyph field",
        )
        field_img = cached.field.resize(field_size, Image.Resampling.BICUBIC)
        if abs(fx_scale_x - 1.0) >= 1.0e-6:
            bbox, field_img = self.scale_tmp_dynamic_run_field(bbox, field_img, fx_scale_x)

        import numpy as np

        field = np.asarray(field_img, dtype=np.float32) / 255.0
        glyph = self.shade_tmp_sdf_field(field, glyph_asset, style, outline_color, outline_dilate)
        return TMPDynamicRunGlyph(glyph, bbox, pad, glyph_origin_x)

    def scale_tmp_dynamic_run_field(
        self,
        bbox: tuple[int, int, int, int],
        field_img: Image.Image,
        scale_x: float,
    ) -> tuple[tuple[int, int, int, int], Image.Image]:
        scaled_left, scaled_right = self.tmp_scale_x_bounds(float(bbox[0]), float(bbox[2]), scale_x)
        scaled_bbox = (math.floor(scaled_left), bbox[1], math.ceil(scaled_right), bbox[3])
        scaled_size = ensure_raster_size(
            (max(1, round(field_img.width * scale_x)), field_img.height),
            max_pixels=self.max_layer_pixels,
            label="custom profile scaled TMP glyph field",
        )
        return scaled_bbox, field_img.resize(scaled_size, Image.Resampling.BICUBIC)

    def compose_tmp_dynamic_run_glyphs(
        self,
        glyphs: list[TMPDynamicRunGlyph],
        bbox: tuple[int, int, int, int],
        max_pad: int,
        style: TextStyle,
    ) -> tuple[Image.Image, tuple[int, int, int, int], int]:
        image_size = ensure_raster_size(
            (max(1, bbox[2] - bbox[0] + max_pad * 2), max(1, bbox[3] - bbox[1] + max_pad * 2)),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP dynamic glyph run",
        )
        image = Image.new(
            "RGBA",
            image_size,
            (0, 0, 0, 0),
        )
        for glyph in glyphs:
            px = round(max_pad + glyph.origin_x + glyph.bbox[0] - glyph.pad - bbox[0])
            py = round(max_pad + glyph.bbox[1] - glyph.pad - bbox[1])
            image.alpha_composite(glyph.image, (px, py))

        if self.tmp_scale_mode == "x" and not math.isclose(style.scale_x, 1.0, abs_tol=1.0e-9):
            scaled_size = ensure_raster_size(
                (max(1, round(image.width * style.scale_x)), image.height),
                max_pixels=self.max_layer_pixels,
                label="custom profile scaled TMP dynamic glyph run",
            )
            image = image.resize(scaled_size, Image.Resampling.BICUBIC)
            bbox = (
                math.floor(bbox[0] * style.scale_x),
                bbox[1],
                math.ceil(bbox[2] * style.scale_x),
                bbox[3],
            )
        return image, bbox, max_pad

    def render_tmp_sdf_run(
        self,
        font_name: str,
        font_path: Path,
        run: TextRun,
        font_size: float,
        outline_color: str,
        outline_dilate: float,
    ) -> tuple[Image.Image, tuple[int, int, int, int], int] | None:
        style = run.style
        static_atlas = self.render_tmp_static_atlas_run(font_name, run, font_size, outline_color, outline_dilate)
        if static_atlas is not None:
            return static_atlas

        if self.tmp_dynamic_sdf:
            dynamic_glyphs = self.render_tmp_dynamic_sdf_run_from_glyphs(
                font_name,
                font_path,
                run,
                font_size,
                outline_color,
                outline_dilate,
            )
            if dynamic_glyphs is not None:
                return dynamic_glyphs
            dynamic_atlas = self.render_tmp_dynamic_sdf_run(
                font_name, font_path, run, font_size, outline_color, outline_dilate
            )
            if dynamic_atlas is not None:
                return dynamic_atlas

        font = load_font(font_path, font_size)
        bbox = (
            self.run_fx_bbox(font, run, font_name, font_size)
            if self.tmp_scale_mode in {"fx-center", "fx-native"}
            else self.run_bbox(font, run, font_name, font_size)
        )
        asset = self.tmp_sdf_asset(font_name)
        scale_x = self.tmp_fx_scale_x(style)
        spread = self.tmp_sdf_spread(asset, font_size, scale_x)
        pad = self.tmp_display_padding(asset, outline_dilate, font_size)
        w = max(1, bbox[2] - bbox[0] + pad * 2)
        h = max(1, bbox[3] - bbox[1] + pad * 2)
        w, h = ensure_raster_size(
            (w, h),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP SDF run mask",
        )
        mask = Image.new("L", (w, h), 0)
        if self.tmp_scale_mode in {"fx-center", "fx-native"}:
            self.draw_text_mask_run_fx(mask, (pad - bbox[0], pad - bbox[1]), run, font, font_name, font_size)
        else:
            self.draw_text_mask_run(
                ImageDraw.Draw(mask), (pad - bbox[0], pad - bbox[1]), run, font, font_name, font_size
            )
            if self.tmp_scale_mode == "x" and not math.isclose(style.scale_x, 1.0, abs_tol=1.0e-9):
                new_w = max(1, round(mask.width * style.scale_x))
                scaled_size = ensure_raster_size(
                    (new_w, mask.height),
                    max_pixels=self.max_layer_pixels,
                    label="custom profile scaled TMP SDF run mask",
                )
                mask = mask.resize(scaled_size, Image.Resampling.BICUBIC)

        try:
            field = alpha_mask_to_sdf_field(mask, spread, tmp_dynamic_sdf_alpha_threshold(asset))
        except ImportError:
            return None

        return self.shade_tmp_sdf_field(field, asset, style, outline_color, outline_dilate), bbox, pad

    def _tmp_native_static_character_field(
        self,
        glyph_asset: TMPFontAsset,
        metrics: TMPGlyphMetrics,
        atlas_path: Path,
        style: TextStyle,
        outline_dilate: float,
        char_info: TMPNativeCharacterInfo,
        defer_static_atlas: bool,
        native_field_size: tuple[int, int] | None,
    ) -> _TMPPreparedCharacterField | None:
        quad_w, quad_h = ensure_raster_size(
            native_field_size or self.tmp_native_unrotated_quad_size(char_info),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP native glyph quad",
        )
        atlas_pad = self.tmp_native_atlas_padding(glyph_asset, style, outline_dilate)
        atlas_left = metrics.rect_x - atlas_pad
        atlas_right = metrics.rect_x + metrics.rect_w + atlas_pad
        atlas_bottom_unity = metrics.rect_y - atlas_pad
        atlas_top_unity = metrics.rect_y + metrics.rect_h + atlas_pad
        if defer_static_atlas:
            atlas_width = round(glyph_asset.atlas_width)
            atlas_height = round(glyph_asset.atlas_height)
            if atlas_width <= 0 or atlas_height <= 0:
                return None
            atlas = None
        else:
            atlas = self.tmp_atlas_alpha(atlas_path)
            atlas_width = atlas.width
            atlas_height = atlas.height
        crop_box = (
            atlas_left,
            atlas_height - atlas_top_unity,
            atlas_right,
            atlas_height - atlas_bottom_unity,
        )
        ensure_raster_size(
            (atlas_right - atlas_left, atlas_top_unity - atlas_bottom_unity),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP atlas glyph crop",
        )
        if defer_static_atlas:
            field_image: Image.Image | TMPStaticAtlasField = TMPStaticAtlasField(
                atlas_path,
                (atlas_width, atlas_height),
                crop_box,
                (quad_w, quad_h),
            )
        else:
            assert atlas is not None
            field_image = atlas.crop(crop_box)
            if field_image.size != (quad_w, quad_h):
                field_image = field_image.resize((quad_w, quad_h), Image.Resampling.BICUBIC)
        return _TMPPreparedCharacterField(field_image, glyph_asset, (0, 0, quad_w, quad_h), 0, 0, True)

    def _tmp_raster_static_character_field(
        self,
        glyph_asset: TMPFontAsset,
        metrics: TMPGlyphMetrics,
        atlas_path: Path,
        font_size: float,
        outline_dilate: float,
    ) -> _TMPPreparedCharacterField:
        atlas = self.tmp_atlas_alpha(atlas_path)
        atlas_top = max(0, round(atlas.height - metrics.rect_y - metrics.rect_h))
        atlas_left = max(0, metrics.rect_x)
        crop = atlas.crop((atlas_left, atlas_top, atlas_left + metrics.rect_w, atlas_top + metrics.rect_h))
        font_scale = font_size / max(1.0, glyph_asset.point_size)
        glyph_scale = font_scale * metrics.glyph_scale
        left = metrics.bearing_x * font_scale
        top = -metrics.bearing_y * font_scale
        width = max(1, round(metrics.rect_w * glyph_scale))
        height = max(1, round(metrics.rect_h * glyph_scale))
        bbox = (
            math.floor(left),
            math.floor(top),
            math.ceil(left + width),
            math.ceil(top + height),
        )
        pad = self.tmp_display_padding(glyph_asset, outline_dilate, font_size)
        field_size = ensure_raster_size(
            (max(1, bbox[2] - bbox[0] + pad * 2), max(1, bbox[3] - bbox[1] + pad * 2)),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP atlas glyph field",
        )
        field_image = Image.new("L", field_size, 0)
        glyph_size = ensure_raster_size(
            (width, height),
            max_pixels=self.max_layer_pixels,
            label="custom profile TMP atlas glyph",
        )
        glyph = crop.resize(glyph_size, Image.Resampling.BICUBIC)
        field_image.paste(glyph, (pad + math.floor(left) - bbox[0], pad + math.floor(top) - bbox[1]))
        return _TMPPreparedCharacterField(field_image, glyph_asset, bbox, pad, pad, False)

    def _tmp_raster_dynamic_character_field(
        self,
        font_name: str,
        style: TextStyle,
        outline_dilate: float,
        cached: TMPDynamicGlyphSDF,
        glyph_asset: TMPFontAsset,
        char_info: TMPNativeCharacterInfo | None,
        native_field_size: tuple[int, int] | None,
    ) -> _TMPPreparedCharacterField:
        active = self.tmp_sdf_asset(font_name)
        asset_point_size = max(1.0, active.point_size if active is not None else glyph_asset.point_size)
        native_element_scale = self.tmp_native_element_scale(font_name, style.size)
        display_scale = native_element_scale * asset_point_size / max(1.0, cached.sample_size)
        display_scale = min(display_scale, TMP_DYNAMIC_SDF_MAX_CHARACTER_SCALE)
        sample_crop_pad = cached.pad
        if char_info is not None:
            atlas_pad = self.tmp_native_atlas_padding(glyph_asset, style, outline_dilate)
            sample_crop_pad = max(0, round(atlas_pad * cached.sample_size / asset_point_size))
        sample_crop_pad = min(sample_crop_pad, cached.pad)
        crop_box = (
            max(0, cached.pad - sample_crop_pad),
            max(0, cached.pad - sample_crop_pad),
            min(cached.field.width, cached.field.width - cached.pad + sample_crop_pad),
            min(cached.field.height, cached.field.height - cached.pad + sample_crop_pad),
        )
        field_source = cached.field.crop(crop_box)
        if char_info is not None:
            quad_w, quad_h = ensure_raster_size(
                native_field_size or self.tmp_native_unrotated_quad_size(char_info),
                max_pixels=self.max_layer_pixels,
                label="custom profile TMP native glyph quad",
            )
            field_image = field_source.resize((quad_w, quad_h), Image.Resampling.BICUBIC)
            return _TMPPreparedCharacterField(
                field_image,
                glyph_asset,
                (0, 0, field_image.width, field_image.height),
                0,
                0,
                True,
            )
        bbox = (
            math.floor((cached.bbox[0] - sample_crop_pad) * display_scale),
            math.floor((cached.bbox[1] - sample_crop_pad) * display_scale),
            math.ceil((cached.bbox[2] + sample_crop_pad) * display_scale),
            math.ceil((cached.bbox[3] + sample_crop_pad) * display_scale),
        )
        pad = max(0, round(sample_crop_pad * display_scale))
        field_size = ensure_raster_size(
            (
                max(1, round(field_source.width * display_scale)),
                max(1, round(field_source.height * display_scale)),
            ),
            max_pixels=self.max_layer_pixels,
            label="custom profile displayed TMP glyph field",
        )
        return _TMPPreparedCharacterField(
            field_source.resize(field_size, Image.Resampling.BICUBIC),
            glyph_asset,
            bbox,
            pad,
            pad,
            False,
        )

    def _tmp_dynamic_character_field(
        self,
        font_name: str,
        font_path: Path,
        glyph_char: str,
        style: TextStyle,
        outline_dilate: float,
        char_info: TMPNativeCharacterInfo | None,
        defer_dynamic_font: bool,
        native_field_size: tuple[int, int] | None,
    ) -> _TMPPreparedCharacterField | None:
        if defer_dynamic_font and char_info is not None:
            native_field = self.tmp_dynamic_font_field(
                font_name,
                glyph_char,
                style,
                outline_dilate,
                char_info,
                native_field_size,
            )
            if native_field is None:
                return None
            field_image, glyph_asset = native_field
            return _TMPPreparedCharacterField(
                field_image,
                glyph_asset,
                (0, 0, field_image.field_size[0], field_image.field_size[1]),
                0,
                0,
                True,
            )
        dynamic = self.tmp_dynamic_glyph_sdf(font_name, font_path, glyph_char, style)
        if dynamic is None:
            return None
        cached, glyph_asset = dynamic
        return self._tmp_raster_dynamic_character_field(
            font_name,
            style,
            outline_dilate,
            cached,
            glyph_asset,
            char_info,
            native_field_size,
        )

    def _tmp_scaled_character_field(
        self,
        prepared: _TMPPreparedCharacterField,
        style: TextStyle,
    ) -> _TMPPreparedCharacterField:
        scale_x = self.tmp_native_vertex_scale_x(style)
        if prepared.native_quad_sized or abs(scale_x - 1.0) < 1.0e-6:
            return prepared
        assert isinstance(prepared.field, Image.Image)
        scaled_left, scaled_right = self.tmp_scale_x_bounds(
            float(prepared.bbox[0]),
            float(prepared.bbox[2]),
            scale_x,
        )
        bbox = (
            math.floor(scaled_left),
            prepared.bbox[1],
            math.ceil(scaled_right),
            prepared.bbox[3],
        )
        pad_x = max(1, round(prepared.pad_x * abs(scale_x)))
        scaled_size = ensure_raster_size(
            (
                max(1, round(prepared.field.width * abs(scale_x))),
                prepared.field.height,
            ),
            max_pixels=self.max_layer_pixels,
            label="custom profile scaled TMP glyph field",
        )
        return _TMPPreparedCharacterField(
            prepared.field.resize(scaled_size, Image.Resampling.BICUBIC),
            prepared.glyph_asset,
            bbox,
            pad_x,
            prepared.pad_y,
            False,
        )

    def render_tmp_sdf_character_field(
        self,
        font_name: str,
        font_path: Path,
        char: str,
        style: TextStyle,
        font_size: float,
        outline_color: str,
        outline_dilate: float,
        char_info: TMPNativeCharacterInfo | None = None,
        *,
        defer_static_atlas: bool = False,
        defer_dynamic_font: bool = False,
        native_field_size: tuple[int, int] | None = None,
    ) -> (
        tuple[
            Image.Image | TMPStaticAtlasField | TMPDynamicFontField,
            TMPFontAsset | None,
            tuple[int, int, int, int],
            int,
            int,
        ]
        | None
    ):
        run = TextRun(char, style)
        glyph_char = self.tmp_render_glyph_char(font_name, char, font_size)
        glyph_asset = self.tmp_static_sdf_asset(font_name, run)
        has_static_glyph = bool(
            glyph_asset is not None and glyph_asset.atlas_paths and glyph_char and glyph_char != " "
        )
        if has_static_glyph:
            assert glyph_asset is not None
            metrics = glyph_asset.glyphs.get(ord(glyph_char[0]))
            if metrics is None or metrics.rect_w <= 0 or metrics.rect_h <= 0:
                return None
            atlas_path = glyph_asset.atlas_paths[min(metrics.atlas_index, len(glyph_asset.atlas_paths) - 1)]
            if char_info is not None:
                prepared = self._tmp_native_static_character_field(
                    glyph_asset,
                    metrics,
                    atlas_path,
                    style,
                    outline_dilate,
                    char_info,
                    defer_static_atlas,
                    native_field_size,
                )
            else:
                prepared = self._tmp_raster_static_character_field(
                    glyph_asset,
                    metrics,
                    atlas_path,
                    font_size,
                    outline_dilate,
                )
        else:
            prepared = self._tmp_dynamic_character_field(
                font_name,
                font_path,
                glyph_char,
                style,
                outline_dilate,
                char_info,
                defer_dynamic_font,
                native_field_size,
            )
        if prepared is None:
            return None
        return self._tmp_scaled_character_field(prepared, style).result()

    def render_tmp_sdf_character_image(
        self,
        font_name: str,
        font_path: Path,
        char: str,
        style: TextStyle,
        font_size: float,
        outline_color: str,
        outline_dilate: float,
        char_info: TMPNativeCharacterInfo | None = None,
    ) -> tuple[Image.Image, tuple[int, int, int, int], int, int] | None:
        character_field = self.render_tmp_sdf_character_field(
            font_name,
            font_path,
            char,
            style,
            font_size,
            outline_color,
            outline_dilate,
            char_info,
        )
        if character_field is None:
            return None
        field_img, glyph_asset, bbox, pad_x, pad_y = character_field
        assert isinstance(field_img, Image.Image)
        import numpy as np

        field = np.asarray(field_img, dtype=np.float32) / 255.0
        return (
            self.shade_tmp_sdf_field(
                field,
                glyph_asset,
                style,
                outline_color,
                outline_dilate,
                None,
            ),
            bbox,
            pad_x,
            pad_y,
        )

    def draw_tmp_native_characters(
        self,
        target: Image.Image,
        font_name: str,
        font_path: Path,
        layout: TMPNativeTextLayout,
        native_baselines: list[float],
        horizontal_align: str,
        box_w: float,
        rect_origin_x: float,
        rect_origin_y: float,
        outline_color: str,
        outline_width: int,
        outline_dilate: float,
    ) -> None:
        characters_by_line: dict[int, list[TMPNativeCharacterInfo]] = {}
        for char_info in layout.characters:
            characters_by_line.setdefault(char_info.line_index, []).append(char_info)

        for line_info in layout.lines:
            line_x = tmp_line_offset_x(horizontal_align, box_w, line_info.width)
            x_origin = rect_origin_x + line_x
            baseline_y = rect_origin_y + native_baselines[line_info.index]
            for char_info in characters_by_line.get(line_info.index, []):
                self.draw_tmp_native_character(
                    target,
                    font_name,
                    font_path,
                    char_info,
                    x_origin,
                    baseline_y,
                    outline_color,
                    outline_width,
                    outline_dilate,
                )

    def draw_tmp_native_character(
        self,
        target: Image.Image,
        font_name: str,
        font_path: Path,
        char_info: TMPNativeCharacterInfo,
        x_origin: float,
        baseline_y: float,
        outline_color: str,
        outline_width: int,
        outline_dilate: float,
    ) -> None:
        if not char_info.visible:
            return
        style = char_info.style
        char = char_info.char
        run = TextRun(char, style)
        base_font_size = style.size * self.tmp_font_scale
        if self.use_em_block(run):
            glyph, x_offset, source_metrics = self.render_em_block_glyph(
                font_name,
                run,
                base_font_size,
                outline_color,
                outline_width,
            )
            if source_metrics is not None:
                y = (
                    baseline_y
                    - source_metrics.bearing_y * self.tmp_layout_scale_y(style)
                    - outline_width
                    - self.tmp_native_baseline_offset(style)
                )
            else:
                y = baseline_y - glyph.height - self.tmp_native_baseline_offset(style)
            target.alpha_composite(glyph, (round(x_origin + char_info.x_origin + x_offset), round(y)))
            return

        glyph = self.render_tmp_sdf_character_image(
            font_name,
            font_path,
            char,
            style,
            self.tmp_run_font_size(style),
            outline_color,
            outline_dilate,
            char_info,
        )
        if glyph is None:
            self.draw_run_at_baseline(
                target,
                font_name,
                font_path,
                run,
                x_origin + char_info.x_origin,
                baseline_y,
                outline_color,
                outline_width,
                outline_dilate,
            )
            return

        glyph_image, bbox, pad_x, pad_y = glyph
        quad_w, quad_h = self.tmp_native_unrotated_quad_size(char_info)
        if glyph_image.size != (quad_w, quad_h):
            glyph_image = glyph_image.resize((quad_w, quad_h), Image.Resampling.BICUBIC)
        if style.rotate:
            center_local_x = (
                char_info.bottom_left_x + char_info.top_left_x + char_info.top_right_x + char_info.bottom_right_x
            ) * 0.25
            center_local_y = (
                char_info.bottom_left_y + char_info.top_left_y + char_info.top_right_y + char_info.bottom_right_y
            ) * 0.25
            center_x = x_origin + center_local_x
            center_y = baseline_y - center_local_y
            glyph_image = glyph_image.rotate(style.rotate, resample=Image.Resampling.BICUBIC, expand=True)
            left = center_x - glyph_image.width * 0.5
            top = center_y - glyph_image.height * 0.5
        else:
            left = x_origin + min(
                char_info.bottom_left_x,
                char_info.top_left_x,
                char_info.top_right_x,
                char_info.bottom_right_x,
            )
            top = baseline_y - max(
                char_info.bottom_left_y,
                char_info.top_left_y,
                char_info.top_right_y,
                char_info.bottom_right_y,
            )
        target.alpha_composite(glyph_image, (round(left), round(top)))

    def render_tmp_decorative_text_direct(
        self,
        canvas: Image.Image,
        item: dict[str, Any],
        object_data: dict[str, Any],
    ) -> bool:
        return self.render_tmp_text_direct(canvas, item, object_data)

    def render_tmp_text_direct(
        self,
        canvas: Image.Image,
        item: dict[str, Any],
        object_data: dict[str, Any],
    ) -> bool:
        import numpy as np

        quads = self.prepare_direct_sdf_quads(item, object_data)
        if quads is None:
            return False
        for quad in quads:
            field = np.asarray(quad.field, dtype=np.float32) / 255.0
            canvas.alpha_composite(self._shade_field_with_scalars(field, quad.scalars), (quad.left, quad.top))
        return True

    def prepare_direct_sdf_quads(
        self,
        item: dict[str, Any],
        object_data: dict[str, Any],
        *,
        defer_static_atlas: bool = False,
        defer_dynamic_font: bool = False,
        source_metrics_only: bool = False,
    ) -> list[DirectSdfQuad | DirectSdfAtlasQuad | DirectSdfFontQuad] | None:
        """Layout + per-glyph warp half of the sparse/direct TMP path. Returns None when the
        element is not eligible for the direct path (caller falls back to the raster path);
        degenerate/fully clipped glyphs are skipped exactly as the composite path skips them.
        Shading/compositing stays out: Pillow feeds each raster quad to _shade_field_with_scalars;
        the Skia emitter ships static descriptors as SdfAtlasQuad and dynamic fields as SdfQuad."""
        text_data = self.generate_text_data(item)
        if not text_data.text.strip():
            return None
        font_name = self.text_fonts.get(text_data.font_id, "FOT-RodinNTLGPro-DB") or "FOT-RodinNTLGPro-DB"
        font_path = self.font_path_for(font_name)
        mesh_state = self.update_text_mesh_state(text_data, font_name)
        base_size = mesh_state.font_size
        base_style = self.base_text_style(mesh_state)
        tokens = parse_tmp_text(text_data.text, base_style)
        lines = split_runs_by_line_with_style(tokens, base_style)
        if not lines:
            return None

        line_spacing = mesh_state.tmp_line_spacing
        outline_color = mesh_state.underlay_color
        outline_dilate = self.decorative_outline_dilate(item, mesh_state.underlay_dilate)
        outline_width = max(0, round(outline_dilate * base_size * self.tmp_pillow_stroke_factor))
        dominant_size = max((line.style.size for line in lines), default=base_size)
        align_type = mesh_state.align
        horizontal_align = tmp_horizontal_alignment(align_type)
        vertical_align = tmp_vertical_alignment(align_type)
        layout_lines = [line for line in lines if self.include_empty_lines or line.runs]
        layouts = self.resolve_tmp_text_box_layouts(
            layout_lines,
            font_name,
            font_path,
            base_size,
            line_spacing,
            dominant_size,
            outline_dilate,
            source_metrics_only=source_metrics_only,
        )
        if layouts is None:
            return None
        native_text_layout, mesh_text_layout = layouts

        native_layout = (
            mesh_text_layout.line_layout if self.text_vertical_mode in {"tmp-native", "tmp-native-top"} else None
        )
        if native_layout is None:
            return None
        total_h = mesh_text_layout.accumulated_line_height
        content_h = native_text_layout.content_height
        pad = self.text_pad(base_size, outline_width)
        box_w, box_h = self.tmp_text_box_size(
            native_text_layout.dominant_size,
            native_text_layout.preferred_width,
            content_h,
        )
        native_baselines = self.tmp_native_baseline_downs(
            native_layout,
            box_h,
            "top" if self.text_vertical_mode == "tmp-native-top" else vertical_align,
        )
        mesh_bounds = self.tmp_native_mesh_pixel_bounds(
            mesh_text_layout,
            native_baselines,
            horizontal_align,
            box_w,
            box_h,
        )
        mesh_left, mesh_top, mesh_right, mesh_bottom = mesh_bounds
        rect_origin_x = pad - mesh_left
        rect_origin_y = pad - mesh_top
        metrics = [
            (
                line.styled_line,
                line.run_metrics,
                line.y_down,
                line.line_height,
                line.width,
            )
            for line in mesh_text_layout.lines
        ]
        self.record_tmp_layout_audit(
            item,
            text_data,
            mesh_state,
            metrics,
            font_name,
            font_path,
            native_text_layout.preferred_width,
            native_text_layout.preferred_height,
            content_h,
            total_h,
            box_w,
            box_h,
            native_layout,
            native_baselines,
            native_text_layout,
            mesh_text_layout,
            mesh_bounds,
            (rect_origin_x, rect_origin_y),
            (math.ceil(mesh_right - mesh_left + pad * 2), math.ceil(mesh_bottom - mesh_top + pad * 2)),
        )

        pivot = (rect_origin_x + box_w / 2, rect_origin_y + box_h / 2)
        direct_glyphs = self.prepare_tmp_direct_sdf_glyphs(
            font_name,
            font_path,
            mesh_text_layout,
            native_baselines,
            horizontal_align,
            box_w,
            rect_origin_x,
            rect_origin_y,
            outline_color,
            outline_dilate,
            defer_static_atlas=defer_static_atlas,
            defer_dynamic_font=defer_dynamic_font,
        )
        if direct_glyphs is None:
            return None
        quads: list[DirectSdfQuad | DirectSdfAtlasQuad | DirectSdfFontQuad] = []
        retained_field_bytes = sum(
            (
                field.field_size[0] * field.field_size[1]
                if isinstance(field, (TMPStaticAtlasField, TMPDynamicFontField))
                else field.width * field.height
            )
            for field, *_ in direct_glyphs
        )
        for direct_glyph in direct_glyphs:
            quad, retained_field_bytes = self.prepare_direct_sdf_quad(
                direct_glyph,
                pivot,
                object_data,
                outline_color,
                outline_dilate,
                retained_field_bytes,
            )
            if quad is not None:
                quads.append(quad)
        return quads

    def prepare_direct_sdf_quad(
        self,
        direct_glyph: tuple[
            Image.Image | TMPStaticAtlasField | TMPDynamicFontField,
            TMPFontAsset | None,
            TextStyle,
            float,
            float,
            tuple[int, int],
            tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None,
        ],
        pivot: tuple[float, float],
        object_data: dict[str, Any],
        outline_color: str,
        outline_dilate: float,
        retained_field_bytes: int,
    ) -> tuple[DirectSdfQuad | DirectSdfAtlasQuad | DirectSdfFontQuad | None, int]:
        field, glyph_asset, style, local_left, local_top, geometry_size, geometry_corners = direct_glyph
        if isinstance(field, (TMPStaticAtlasField, TMPDynamicFontField)):
            return self.prepare_deferred_direct_sdf_quad(
                field,
                glyph_asset,
                style,
                local_left,
                local_top,
                geometry_size,
                geometry_corners,
                pivot,
                object_data,
                outline_color,
                outline_dilate,
                retained_field_bytes,
            )
        warped = self.warp_tmp_sdf_field_direct(
            field,
            local_left,
            local_top,
            pivot,
            object_data,
            geometry_size=geometry_size,
            geometry_corners=geometry_corners,
            max_output_bytes=self.max_scene_bytes - retained_field_bytes,
        )
        if warped is None:
            return None, retained_field_bytes
        warped_field, left, top = warped
        retained_field_bytes = self._reserve_retained_raster_bytes(
            retained_field_bytes,
            warped_field.width * warped_field.height,
            label=_TMP_TEXT_LABEL,
        )
        scalars = self.tmp_sdf_shading_scalars(glyph_asset, style, outline_color, outline_dilate, None)
        return DirectSdfQuad(field=warped_field, left=left, top=top, scalars=scalars), retained_field_bytes

    def prepare_deferred_direct_sdf_quad(
        self,
        field: TMPStaticAtlasField | TMPDynamicFontField,
        glyph_asset: TMPFontAsset | None,
        style: TextStyle,
        local_left: float,
        local_top: float,
        geometry_size: tuple[int, int],
        geometry_corners: (
            tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None
        ),
        pivot: tuple[float, float],
        object_data: dict[str, Any],
        outline_color: str,
        outline_dilate: float,
        retained_field_bytes: int,
    ) -> tuple[DirectSdfAtlasQuad | DirectSdfFontQuad | None, int]:
        plan = self.tmp_sdf_field_warp_plan(
            field.field_size,
            local_left,
            local_top,
            pivot,
            object_data,
            geometry_size=geometry_size,
            geometry_corners=geometry_corners,
            max_output_bytes=self.max_scene_bytes - retained_field_bytes,
        )
        if plan is None:
            return None, retained_field_bytes
        retained_field_bytes = self._reserve_retained_raster_bytes(
            retained_field_bytes,
            plan.size[0] * plan.size[1],
            label=_TMP_TEXT_LABEL,
        )
        scalars = self.tmp_sdf_shading_scalars(glyph_asset, style, outline_color, outline_dilate, None)
        if isinstance(field, TMPDynamicFontField):
            return (
                DirectSdfFontQuad(
                    font_path=field.font_path,
                    codepoint=field.codepoint,
                    sample_size=field.sample_size,
                    bbox=field.bbox,
                    padding=field.padding,
                    crop_padding=field.crop_padding,
                    field_size=field.field_size,
                    spread=field.spread,
                    size=plan.size,
                    affine=plan.affine,
                    left=plan.left,
                    top=plan.top,
                    scalars=scalars,
                ),
                retained_field_bytes,
            )
        return (
            DirectSdfAtlasQuad(
                atlas_path=field.atlas_path,
                atlas_size=field.atlas_size,
                crop=field.crop,
                field_size=field.field_size,
                size=plan.size,
                affine=plan.affine,
                left=plan.left,
                top=plan.top,
                scalars=scalars,
            ),
            retained_field_bytes,
        )

    def prepare_tmp_direct_sdf_glyphs(
        self,
        font_name: str,
        font_path: Path,
        layout: TMPNativeTextLayout,
        native_baselines: list[float],
        horizontal_align: str,
        box_w: float,
        rect_origin_x: float,
        rect_origin_y: float,
        outline_color: str,
        outline_dilate: float,
        *,
        defer_static_atlas: bool = False,
        defer_dynamic_font: bool = False,
    ) -> (
        list[
            tuple[
                Image.Image | TMPStaticAtlasField | TMPDynamicFontField,
                TMPFontAsset | None,
                TextStyle,
                float,
                float,
                tuple[int, int],
                tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None,
            ]
        ]
        | None
    ):
        characters_by_line = self.tmp_characters_by_line(layout.characters)
        direct_glyphs: list[
            tuple[
                Image.Image | TMPStaticAtlasField | TMPDynamicFontField,
                TMPFontAsset | None,
                TextStyle,
                float,
                float,
                tuple[int, int],
                tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None,
            ]
        ] = []
        retained_field_bytes = 0
        for line_info in layout.lines:
            line_x = tmp_line_offset_x(horizontal_align, box_w, line_info.width)
            x_origin = rect_origin_x + line_x
            baseline_y = rect_origin_y + native_baselines[line_info.index]
            for char_info in characters_by_line.get(line_info.index, []):
                if not char_info.visible:
                    continue
                prepared = self.prepare_tmp_direct_sdf_glyph(
                    font_name,
                    font_path,
                    char_info,
                    x_origin,
                    baseline_y,
                    outline_color,
                    outline_dilate,
                    retained_field_bytes,
                    defer_static_atlas,
                    defer_dynamic_font,
                )
                if prepared is None:
                    return None
                direct_glyph, retained_field_bytes = prepared
                direct_glyphs.append(direct_glyph)
        return direct_glyphs

    def tmp_characters_by_line(
        self,
        characters: list[TMPNativeCharacterInfo],
    ) -> dict[int, list[TMPNativeCharacterInfo]]:
        characters_by_line: dict[int, list[TMPNativeCharacterInfo]] = {}
        for char_info in characters:
            characters_by_line.setdefault(char_info.line_index, []).append(char_info)
        return characters_by_line

    def prepare_tmp_direct_sdf_glyph(
        self,
        font_name: str,
        font_path: Path,
        char_info: TMPNativeCharacterInfo,
        x_origin: float,
        baseline_y: float,
        outline_color: str,
        outline_dilate: float,
        retained_field_bytes: int,
        defer_static_atlas: bool,
        defer_dynamic_font: bool,
    ) -> (
        tuple[
            tuple[
                Image.Image | TMPStaticAtlasField | TMPDynamicFontField,
                TMPFontAsset | None,
                TextStyle,
                float,
                float,
                tuple[int, int],
                tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None,
            ],
            int,
        ]
        | None
    ):
        style = char_info.style
        if self.use_em_block(TextRun(char_info.char, style)):
            return None
        geometry_size = self.tmp_native_unrotated_quad_size(char_info)
        field_size = self.tmp_direct_sdf_field_size(geometry_size)
        retained_field_bytes = self._reserve_retained_raster_bytes(
            retained_field_bytes,
            field_size[0] * field_size[1],
            label=_TMP_TEXT_LABEL,
        )
        character_field = self.render_tmp_sdf_character_field(
            font_name,
            font_path,
            char_info.char,
            style,
            self.tmp_run_font_size(style),
            outline_color,
            outline_dilate,
            char_info,
            defer_static_atlas=defer_static_atlas,
            defer_dynamic_font=defer_dynamic_font,
            native_field_size=field_size,
        )
        if character_field is None:
            return None
        field_img, glyph_asset, _, _, _ = character_field
        if isinstance(field_img, Image.Image) and field_img.size != field_size:
            field_img = field_img.resize(field_size, Image.Resampling.BICUBIC)
        local_left = x_origin + min(
            char_info.bottom_left_x,
            char_info.top_left_x,
            char_info.top_right_x,
            char_info.bottom_right_x,
        )
        local_top = baseline_y - max(
            char_info.bottom_left_y,
            char_info.top_left_y,
            char_info.top_right_y,
            char_info.bottom_right_y,
        )
        geometry_corners = self.tmp_direct_sdf_geometry_corners(char_info, x_origin, baseline_y)
        direct_glyph = (field_img, glyph_asset, style, local_left, local_top, geometry_size, geometry_corners)
        return direct_glyph, retained_field_bytes

    def tmp_direct_sdf_geometry_corners(
        self,
        char_info: TMPNativeCharacterInfo,
        x_origin: float,
        baseline_y: float,
    ) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None:
        if abs(char_info.style.rotate) < 1.0e-6:
            return None
        return (
            (x_origin + char_info.top_left_x, baseline_y - char_info.top_left_y),
            (x_origin + char_info.top_right_x, baseline_y - char_info.top_right_y),
            (x_origin + char_info.bottom_right_x, baseline_y - char_info.bottom_right_y),
            (x_origin + char_info.bottom_left_x, baseline_y - char_info.bottom_left_y),
        )

    def transformed_local_point(
        self,
        object_data: dict[str, Any],
        pivot: tuple[float, float],
        local_x: float,
        local_y: float,
    ) -> tuple[float, float]:
        scale = object_data.get("scale", {})
        sx = float(scale.get("x") or 1.0) * self.position_scale_x
        sy = float(scale.get("y") or (scale.get("x") or 1.0)) * self.position_scale_y
        angle = self.rotation_sign * unity_rotation_degrees(object_data.get("rotation", {}))
        theta = math.radians(angle % 360.0)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        x, y = self.unity_point(object_data.get("position", {}))
        dx = (local_x - pivot[0]) * sx
        dy = (local_y - pivot[1]) * sy
        return x + dx * cos_t - dy * sin_t, y + dx * sin_t + dy * cos_t

    def tmp_sdf_field_warp_plan(
        self,
        source_size: tuple[int, int],
        local_left: float,
        local_top: float,
        pivot: tuple[float, float],
        object_data: dict[str, Any],
        *,
        geometry_size: tuple[int, int] | None = None,
        geometry_corners: (
            tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None
        ) = None,
        max_output_bytes: int | None = None,
    ) -> TMPFieldWarpPlan | None:
        """Build the pixel-free geometry plan shared by Pillow and native atlas fields."""
        src_w, src_h = source_size
        if src_w <= 0 or src_h <= 0:
            return None
        geometry_w, geometry_h = geometry_size or source_size
        if geometry_w <= 0 or geometry_h <= 0:
            return None

        if geometry_corners is None:
            local_corners = (
                (local_left, local_top),
                (local_left + geometry_w, local_top),
                (local_left + geometry_w, local_top + geometry_h),
                (local_left, local_top + geometry_h),
            )
        else:
            local_corners = geometry_corners
        p00, p10, p11, p01 = (self.transformed_local_point(object_data, pivot, x, y) for x, y in local_corners)
        corners = (p00, p10, p11, p01)
        pad = 2
        left = max(0, math.floor(min(x for x, _ in corners)) - pad)
        right = min(self.canvas_w, math.ceil(max(x for x, _ in corners)) + pad)
        top = max(0, math.floor(min(y for _, y in corners)) - pad)
        bottom = min(self.canvas_h, math.ceil(max(y for _, y in corners)) + pad)
        if left >= right or top >= bottom:
            return None

        m00 = (p10[0] - p00[0]) / src_w
        m10 = (p10[1] - p00[1]) / src_w
        m01 = (p01[0] - p00[0]) / src_h
        m11 = (p01[1] - p00[1]) / src_h
        det = m00 * m11 - m01 * m10
        if abs(det) < 1.0e-9:
            return None
        inv00 = m11 / det
        inv01 = -m01 / det
        inv10 = -m10 / det
        inv11 = m00 / det
        c = inv00 * (left - p00[0]) + inv01 * (top - p00[1])
        f = inv10 * (left - p00[0]) + inv11 * (top - p00[1])
        out_w = max(1, right - left)
        out_h = max(1, bottom - top)
        out_w, out_h = ensure_raster_size(
            (out_w, out_h),
            max_pixels=self.max_layer_pixels,
            label="custom profile warped TMP glyph field",
        )
        if max_output_bytes is not None and out_w * out_h > max(0, int(max_output_bytes)):
            raise ValueError(
                f"custom profile warped TMP glyph field would retain {out_w * out_h} bytes; "
                f"remaining limit is {max(0, int(max_output_bytes))}"
            )
        return TMPFieldWarpPlan(
            affine=(inv00, inv01, c, inv10, inv11, f),
            size=(out_w, out_h),
            left=left,
            top=top,
        )

    def warp_tmp_sdf_field_direct(
        self,
        field_img: Image.Image,
        local_left: float,
        local_top: float,
        pivot: tuple[float, float],
        object_data: dict[str, Any],
        *,
        geometry_size: tuple[int, int] | None = None,
        geometry_corners: (
            tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None
        ) = None,
        max_output_bytes: int | None = None,
    ) -> tuple[Image.Image, int, int] | None:
        """Warp an L field with Pillow's BICUBIC affine path using the shared geometry plan."""
        plan = self.tmp_sdf_field_warp_plan(
            field_img.size,
            local_left,
            local_top,
            pivot,
            object_data,
            geometry_size=geometry_size,
            geometry_corners=geometry_corners,
            max_output_bytes=max_output_bytes,
        )
        if plan is None:
            return None
        transformed_field = field_img.transform(
            plan.size,
            Image.Transform.AFFINE,
            plan.affine,
            Image.Resampling.BICUBIC,
            fillcolor=0,
        )
        return transformed_field, plan.left, plan.top

    def composite_tmp_sdf_field_direct(
        self,
        canvas: Image.Image,
        field_img: Image.Image,
        glyph_asset: TMPFontAsset | None,
        style: TextStyle,
        outline_color: str,
        outline_dilate: float,
        local_left: float,
        local_top: float,
        pivot: tuple[float, float],
        object_data: dict[str, Any],
    ) -> None:
        warped = self.warp_tmp_sdf_field_direct(field_img, local_left, local_top, pivot, object_data)
        if warped is None:
            return
        transformed_field, left, top = warped

        import numpy as np

        field = np.asarray(transformed_field, dtype=np.float32) / 255.0
        patch = self.shade_tmp_sdf_field(field, glyph_asset, style, outline_color, outline_dilate, None)
        canvas.alpha_composite(patch, (left, top))

    def draw_run(
        self,
        target: Image.Image,
        font_name: str,
        font_path: Path,
        run: TextRun,
        x: float,
        line_top: float,
        line_h: float,
        outline_color: str,
        outline_width: int,
        outline_dilate: float,
    ) -> None:
        style = run.style
        base_font_size = style.size * self.tmp_font_scale
        font_size = self.tmp_run_font_size(style)
        if self.use_em_block(run):
            glyph, x_offset, source_metrics = self.render_em_block_glyph(
                font_name,
                run,
                base_font_size,
                outline_color,
                outline_width,
            )
            if source_metrics is not None:
                baseline_y = line_top + self.tmp_face_baseline_offset(font_name, style.size, line_h)
                y = (
                    baseline_y
                    - source_metrics.bearing_y * self.tmp_layout_scale_y(style)
                    - outline_width
                    - self.tmp_native_baseline_offset(style)
                )
            else:
                y = self.run_y(line_top, line_h, glyph.height, style, None, 0, 0)
            target.alpha_composite(
                glyph,
                (
                    round(x + x_offset),
                    round(y),
                ),
            )
            return

        font = load_font(font_path, font_size)
        bbox = self.run_bbox(font, run, font_name, font_size)
        if self.tmp_text_render_mode == "sdf":
            sdf = self.render_tmp_sdf_run(font_name, font_path, run, font_size, outline_color, outline_dilate)
            if sdf is not None:
                glyph, bbox, glyph_pad = sdf
                if style.rotate:
                    glyph = glyph.rotate(-style.rotate, resample=Image.Resampling.BICUBIC, expand=True)
                target.alpha_composite(
                    glyph,
                    (
                        round(x),
                        round(self.run_y(line_top, line_h, glyph.height, style, font, bbox[1], glyph_pad)),
                    ),
                )
                return

        glyph_pad = outline_width * 2 + 4
        w = max(1, bbox[2] - bbox[0] + glyph_pad * 2)
        h = max(1, bbox[3] - bbox[1] + glyph_pad * 2)
        glyph = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(glyph)
        self.draw_text_run(
            draw,
            (glyph_pad - bbox[0], glyph_pad - bbox[1]),
            run,
            font,
            hex_to_rgba(style.color, style.alpha),
            outline_width,
            hex_to_rgba(outline_color, style.alpha),
            font_name,
            font_size,
        )
        if self.tmp_scale_mode == "x" and not math.isclose(style.scale_x, 1.0, abs_tol=1.0e-9):
            glyph = glyph.resize((max(1, round(glyph.width * style.scale_x)), glyph.height), Image.Resampling.BICUBIC)
        if style.rotate:
            glyph = glyph.rotate(-style.rotate, resample=Image.Resampling.BICUBIC, expand=True)
        target.alpha_composite(
            glyph,
            (
                round(x),
                round(self.run_y(line_top, line_h, glyph.height, style, font, bbox[1], glyph_pad)),
            ),
        )

    def draw_run_at_baseline(
        self,
        target: Image.Image,
        font_name: str,
        font_path: Path,
        run: TextRun,
        x: float,
        baseline_y: float,
        outline_color: str,
        outline_width: int,
        outline_dilate: float,
    ) -> None:
        style = run.style
        base_font_size = style.size * self.tmp_font_scale
        font_size = self.tmp_run_font_size(style)
        if self.use_em_block(run):
            glyph, x_offset, source_metrics = self.render_em_block_glyph(
                font_name,
                run,
                base_font_size,
                outline_color,
                outline_width,
            )
            if source_metrics is not None:
                y = (
                    baseline_y
                    - source_metrics.bearing_y * self.tmp_layout_scale_y(style)
                    - outline_width
                    - self.tmp_native_baseline_offset(style)
                )
            else:
                y = baseline_y - glyph.height - self.tmp_native_baseline_offset(style)
            target.alpha_composite(
                glyph,
                (
                    round(x + x_offset),
                    round(y),
                ),
            )
            return

        font = load_font(font_path, font_size)
        bbox = self.run_bbox(font, run, font_name, font_size)
        if self.tmp_text_render_mode == "sdf":
            sdf = self.render_tmp_sdf_run(font_name, font_path, run, font_size, outline_color, outline_dilate)
            if sdf is not None:
                glyph, bbox, glyph_pad = sdf
                if style.rotate:
                    glyph = glyph.rotate(-style.rotate, resample=Image.Resampling.BICUBIC, expand=True)
                target.alpha_composite(
                    glyph,
                    (
                        round(x),
                        round(self.run_y_from_baseline(baseline_y, style, font, bbox[1], glyph_pad)),
                    ),
                )
                return

        glyph_pad = outline_width * 2 + 4
        w = max(1, bbox[2] - bbox[0] + glyph_pad * 2)
        h = max(1, bbox[3] - bbox[1] + glyph_pad * 2)
        glyph = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(glyph)
        self.draw_text_run(
            draw,
            (glyph_pad - bbox[0], glyph_pad - bbox[1]),
            run,
            font,
            hex_to_rgba(style.color, style.alpha),
            outline_width,
            hex_to_rgba(outline_color, style.alpha),
            font_name,
            font_size,
        )
        if self.tmp_scale_mode == "x" and not math.isclose(style.scale_x, 1.0, abs_tol=1.0e-9):
            glyph = glyph.resize((max(1, round(glyph.width * style.scale_x)), glyph.height), Image.Resampling.BICUBIC)
        if style.rotate:
            glyph = glyph.rotate(-style.rotate, resample=Image.Resampling.BICUBIC, expand=True)
        target.alpha_composite(
            glyph,
            (
                round(x),
                round(self.run_y_from_baseline(baseline_y, style, font, bbox[1], glyph_pad)),
            ),
        )

    def run_y_from_baseline(
        self,
        baseline_y: float,
        style: TextStyle,
        font: ImageFont.FreeTypeFont,
        bbox_top: int,
        glyph_pad_top: int,
    ) -> float:
        ascent, _ = font.getmetrics()
        voffset = self.tmp_native_baseline_offset(style)
        return baseline_y - float(ascent) + bbox_top - glyph_pad_top - voffset

    def run_y(
        self,
        line_top: float,
        line_h: float,
        glyph_h: float,
        style: TextStyle,
        font: ImageFont.FreeTypeFont | None,
        bbox_top: int,
        glyph_pad_top: int,
    ) -> float:
        voffset = self.tmp_native_baseline_offset(style)
        if self.text_vertical_mode == "font-metrics" and font is not None:
            ascent, descent = font.getmetrics()
            line_center = line_top + line_h / 2
            metrics_h = max(1.0, float(ascent + descent))
            ascender_anchor = line_center - metrics_h / 2
            return ascender_anchor + bbox_top - glyph_pad_top - voffset
        if self.text_vertical_mode == "pil-mm" and font is not None:
            anchor_bbox = font.getbbox("Hg", anchor="mm")
            line_center = line_top + line_h / 2
            anchor_offset = (anchor_bbox[1] + anchor_bbox[3]) / 2
            return line_center + anchor_offset - glyph_h / 2 - voffset
        if self.text_vertical_mode == "font-ascent" and font is not None:
            ascent, _ = font.getmetrics()
            line_center = line_top + line_h / 2
            ascender_anchor = line_center - float(ascent) / 2
            return ascender_anchor + bbox_top - glyph_pad_top - voffset
        if self.text_vertical_mode == "anchor-middle" and font is not None:
            anchor_bbox = font.getbbox("H", anchor="mm")
            line_center = line_top + line_h / 2
            anchor_offset = (anchor_bbox[1] + anchor_bbox[3]) / 2
            return line_center + anchor_offset - glyph_h / 2 - voffset
        return line_top + line_h / 2 - glyph_h / 2 - voffset


def is_tmp_em_block(run: TextRun) -> bool:
    return is_tmp_block_char(run) and run.style.size >= 300


def is_tmp_block_char(run: TextRun) -> bool:
    return run.text in TMP_EM_BLOCK_CHARS


def split_runs_by_line_with_style(
    tokens: list[TextBreak | TextRun | TextStyleMarker], base_style: TextStyle
) -> list[StyledLine]:
    lines = [StyledLine([], base_style)]
    current_style = base_style

    def add_break(style: TextStyle) -> None:
        lines[-1].trailing_newline_count += 1
        lines.append(StyledLine([], style))

    for token in tokens:
        if isinstance(token, TextBreak):
            add_break(current_style)
            continue
        if isinstance(token, TextStyleMarker):
            current_style = token.style
            lines[-1].style = token.style
            continue
        parts = token.text.split("\n")
        for idx, part in enumerate(parts):
            if idx:
                add_break(token.style)
            current_style = token.style
            lines[-1].style = token.style
            if part:
                lines[-1].runs.append(TextRun(part, token.style))
    while len(lines) > 1 and not lines[-1].runs and lines[-1].trailing_newline_count == 0:
        lines.pop()
    return lines


def unity_draw_order(
    elements: list[tuple[int, str, dict[str, Any]]], mode: str
) -> list[tuple[int, str, dict[str, Any]]]:
    if mode == "shapes-first":
        return sorted(elements, key=lambda element: (1 if element[1] == "text" else 0, element[0]))
    if mode == "white-text-last":
        return sorted(elements, key=lambda element: (element[0], 0 if element[1] == "shape" else 1))
    return sorted(elements, key=lambda element: element[0])


def is_large_background_block(lines: list[StyledLine]) -> bool:
    runs = [run for line in lines for run in line.runs]
    return len(runs) == 1 and is_tmp_em_block(runs[0]) and runs[0].style.size >= 300


def font_line_height(path: Path, size: float) -> float:
    font = load_font(path, size)
    ascent, descent = font.getmetrics()
    return max(1.0, float(ascent + descent))


def tmp_line_height(base_size: float, style_size: float, font_scale: float) -> float:
    return max(1.0, (base_size + style_size) * font_scale * TMP_LINE_HEIGHT_FACTOR)


def scale_tmp_spacing(run: TextRun, factor: float) -> TextRun:
    if abs(factor - 1.0) < 1.0e-6:
        return run
    style = run.style
    return TextRun(
        run.text,
        TextStyle(
            color=style.color,
            alpha=style.alpha,
            size=style.size,
            scale_x=style.scale_x,
            cspace=style.cspace * factor,
            mspace=None if style.mspace is None else style.mspace * factor,
            indent=style.indent * factor,
            line_indent=style.line_indent * factor,
            line_height=None if style.line_height is None else style.line_height * factor,
            rotate=style.rotate,
            voffset=style.voffset * factor,
            mark_color=style.mark_color,
            bold=style.bold,
            italic=style.italic,
            underline=style.underline,
            strike=style.strike,
            indent_percent=style.indent_percent,
            line_indent_percent=style.line_indent_percent,
        ),
    )


def select_cards(
    profile: dict[str, Any], seq: int | None, card_id: int | None, all_cards: bool
) -> list[dict[str, Any]]:
    cards = list(profile.get("userCustomProfileCards", []))
    if all_cards:
        return sorted(cards, key=lambda c: int(c.get("seq", 0)))
    if card_id is not None:
        return [c for c in cards if int(c.get("customProfileCardId", 0)) == card_id]
    target_seq = seq or 1
    return [c for c in cards if int(c.get("seq", 0)) == target_seq]


def rotate_layer_about_pivot(
    layer: Image.Image,
    pivot: tuple[float, float],
    angle: float,
    premultiply_alpha: bool = False,
) -> tuple[Image.Image, tuple[float, float]]:
    angle = angle % 360.0
    if abs(angle) < 1.0e-9:
        return layer, pivot

    theta = math.radians(angle)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    corners = (
        (0.0, 0.0),
        (float(layer.width), 0.0),
        (float(layer.width), float(layer.height)),
        (0.0, float(layer.height)),
    )
    rotated_corners = [
        (
            (x - pivot[0]) * cos_t - (y - pivot[1]) * sin_t,
            (x - pivot[0]) * sin_t + (y - pivot[1]) * cos_t,
        )
        for x, y in corners
    ]
    min_x = math.floor(min(x for x, _ in rotated_corners))
    max_x = math.ceil(max(x for x, _ in rotated_corners))
    min_y = math.floor(min(y for _, y in rotated_corners))
    max_y = math.ceil(max(y for _, y in rotated_corners))
    out_w = max(1, max_x - min_x)
    out_h = max(1, max_y - min_y)

    inv_theta = -theta
    a = math.cos(inv_theta)
    b = -math.sin(inv_theta)
    d = math.sin(inv_theta)
    e = math.cos(inv_theta)
    c = pivot[0] + a * min_x + b * min_y
    f = pivot[1] + d * min_x + e * min_y
    if premultiply_alpha:
        rotated = transform_rgba_premul(
            layer,
            (out_w, out_h),
            Image.Transform.AFFINE,
            (a, b, c, d, e, f),
            Image.Resampling.BICUBIC,
        )
    else:
        rotated = layer.transform(
            (out_w, out_h),
            Image.Transform.AFFINE,
            (a, b, c, d, e, f),
            Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )
    return rotated, (-min_x, -min_y)


def trim_layer_to_content(
    layer: Image.Image, pivot: tuple[float, float], pad: int = 4
) -> tuple[Image.Image, tuple[float, float]]:
    bbox = layer.getchannel("A").getbbox()
    if bbox is None:
        return layer, pivot
    left, top, right, bottom = bbox
    left = math.floor(min(left, pivot[0])) - pad
    top = math.floor(min(top, pivot[1])) - pad
    right = math.ceil(max(right, pivot[0])) + pad
    bottom = math.ceil(max(bottom, pivot[1])) + pad
    left = max(0, left)
    top = max(0, top)
    right = min(layer.width, right)
    bottom = min(layer.height, bottom)
    if left <= 0 and top <= 0 and right >= layer.width and bottom >= layer.height:
        return layer, pivot
    return layer.crop((left, top, right, bottom)), (pivot[0] - left, pivot[1] - top)


def dict_int_set(obj: dict[str, Any], keys: set[int]) -> set[int]:
    present: set[int] = set()
    for key in obj.keys():
        try:
            value = int(key)
        except (TypeError, ValueError):
            continue
        if value in keys:
            present.add(value)
    return present


def deprecated_probe_args(args: argparse.Namespace) -> list[str]:
    checks = (
        (args.text_pivot != DEFAULT_TEXT_PIVOT, f"--text-pivot={args.text_pivot}"),
        (args.tmp_scale_mode != DEFAULT_TMP_SCALE_MODE, f"--tmp-scale-mode={args.tmp_scale_mode}"),
        (args.rotation_sign != DEFAULT_ROTATION_SIGN, f"--rotation-sign={args.rotation_sign}"),
        (args.text_layout != "tmp", f"--text-layout={args.text_layout}"),
        (args.position_scale is not None, "--position-scale"),
        (args.position_scale_x is not None, "--position-scale-x"),
        (args.position_scale_y is not None, "--position-scale-y"),
        (args.tmp_font_scale != DEFAULT_TMP_FONT_SCALE, "--tmp-font-scale"),
        (args.tmp_line_mode != DEFAULT_TMP_LINE_MODE, f"--tmp-line-mode={args.tmp_line_mode}"),
        (args.tmp_box_mode != "preferred", f"--tmp-box-mode={args.tmp_box_mode}"),
        (args.tmp_box_width != TMP_DEFAULT_TEXT_BOX_W, "--tmp-box-width"),
        (args.tmp_box_width_factor != TMP_TEXT_BOX_W_SIZE_FACTOR, "--tmp-box-width-factor"),
        (args.tmp_line_height_factor != TMP_LINE_HEIGHT_FACTOR, "--tmp-line-height-factor"),
        (args.tmp_line_spacing_factor != TMP_LINE_SPACING_FACTOR, "--tmp-line-spacing-factor"),
        (args.tmp_preferred_padding_x != TMP_PREFERRED_PADDING_X, "--tmp-preferred-padding-x"),
        (args.tmp_preferred_padding_y != TMP_PREFERRED_PADDING_Y, "--tmp-preferred-padding-y"),
        (args.rodin_font != "auto", f"--rodin-font={args.rodin_font}"),
        (args.tmp_block_mode != DEFAULT_TMP_BLOCK_MODE, f"--tmp-block-mode={args.tmp_block_mode}"),
        (args.draw_order != "global", f"--draw-order={args.draw_order}"),
        (args.shape_outline_mode != "sdf", f"--shape-outline-mode={args.shape_outline_mode}"),
        (args.triangle_mode != DEFAULT_TRIANGLE_MODE, f"--triangle-mode={args.triangle_mode}"),
        (args.text_vertical_mode != DEFAULT_TEXT_VERTICAL_MODE, f"--text-vertical-mode={args.text_vertical_mode}"),
        (args.tmp_space_width_factor != DEFAULT_TMP_SPACE_WIDTH_FACTOR, "--tmp-space-width-factor"),
        (
            args.tmp_text_render_mode != DEFAULT_TMP_TEXT_RENDER_MODE,
            f"--tmp-text-render-mode={args.tmp_text_render_mode}",
        ),
        (args.tmp_dynamic_sdf != DEFAULT_TMP_DYNAMIC_SDF, "--tmp-dynamic-sdf"),
        (args.premultiply_alpha_transforms, "--premultiply-alpha-transforms"),
        (args.tmp_pillow_stroke_factor != DEFAULT_TMP_PILLOW_STROKE_FACTOR, "--tmp-pillow-stroke-factor"),
        (args.shape_sdf_ratio_scale != SHAPE_SDF_RATIO_SCALE, "--shape-sdf-ratio-scale"),
        (args.shape_sdf_outer_factor != SHAPE_SDF_OUTER_FACTOR, "--shape-sdf-outer-factor"),
        (args.shape_sdf_face_factor != SHAPE_SDF_FACE_FACTOR, "--shape-sdf-face-factor"),
        (args.shape_sdf_softness != SHAPE_SDF_SOFTNESS, "--shape-sdf-softness"),
        (args.shape_sdf_source != "rgb", f"--shape-sdf-source={args.shape_sdf_source}"),
        (args.shape_sdf_screen_fwidth != SHAPE_SDF_SCREEN_FWIDTH, "--no-shape-sdf-screen-fwidth"),
        (args.tmp_metrics_mode != DEFAULT_TMP_METRICS_MODE, f"--tmp-metrics-mode={args.tmp_metrics_mode}"),
        (
            args.tmp_native_line_gap != DEFAULT_TMP_NATIVE_LINE_GAP,
            "--tmp-native-line-gap" if args.tmp_native_line_gap else "--no-tmp-native-line-gap",
        ),
        (args.no_shape_sprites, "--no-shape-sprites"),
        (args.skip_empty_lines, "--skip-empty-lines"),
    )
    return [probe for enabled, probe in checks if enabled]


def warn_deprecated_probe_args(args: argparse.Namespace) -> None:
    probes = deprecated_probe_args(args)
    if probes:
        print(
            "warning: deprecated probe/visual-fit options are enabled; "
            "the native-reverse default path is disabled for this run: " + ", ".join(probes),
            file=sys.stderr,
        )


@dataclass(frozen=True)
class RenderTargetConfig:
    canvas_w: int
    canvas_h: int
    origin_x: float | None
    origin_y: float | None
    position_scale_x: float | None
    position_scale_y: float | None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--request", type=Path, help="Split render request JSON with card + profile_context.")
    parser.add_argument("--masterdata", type=Path, default=DEFAULT_MASTERDATA)
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--fonts", type=Path, default=DEFAULT_FONTS)
    parser.add_argument("--region", default="cn")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out" / "png-render")
    parser.add_argument(
        "--export-request", type=Path, help="Write the selected card + profile_context request and exit."
    )
    parser.add_argument("--seq", type=int)
    parser.add_argument("--card-id", type=int)
    parser.add_argument("--custom-profile-id", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--text-pivot", choices=["left", "center"], default=DEFAULT_TEXT_PIVOT)
    parser.add_argument("--tmp-scale-mode", choices=TMP_SCALE_MODES, default=DEFAULT_TMP_SCALE_MODE)
    parser.add_argument("--rotation-sign", choices=[-1, 1], type=int, default=DEFAULT_ROTATION_SIGN)
    parser.add_argument("--viewer-viewport", action="store_true")
    parser.add_argument(
        "--full-canvas",
        action="store_true",
        help="Export the old 2048x1024 logical canvas for debugging instead of the default 2048x909 game viewport.",
    )
    parser.add_argument("--tmp-font-scale", type=float, default=DEFAULT_TMP_FONT_SCALE)
    parser.add_argument("--text-layout", choices=["bbox", "tmp"], default="tmp")
    parser.add_argument("--position-scale", type=float)
    parser.add_argument("--position-scale-x", type=float)
    parser.add_argument("--position-scale-y", type=float)
    parser.add_argument(
        "--tmp-line-mode",
        choices=[
            "style",
            "base",
            "base-glyph",
            "style-only",
            "glyph",
            "max-glyph",
            "face",
            "face-scale",
            "asset-face",
            "asset-face-scale",
        ],
        default=DEFAULT_TMP_LINE_MODE,
    )
    parser.add_argument("--tmp-box-mode", choices=TMP_BOX_MODES, default="preferred")
    parser.add_argument("--tmp-box-width", type=float, default=TMP_DEFAULT_TEXT_BOX_W)
    parser.add_argument("--tmp-box-width-factor", type=float, default=TMP_TEXT_BOX_W_SIZE_FACTOR)
    parser.add_argument("--tmp-line-height-factor", type=float, default=TMP_LINE_HEIGHT_FACTOR)
    parser.add_argument("--tmp-line-spacing-factor", type=float, default=TMP_LINE_SPACING_FACTOR)
    parser.add_argument("--tmp-preferred-padding-x", type=float, default=TMP_PREFERRED_PADDING_X)
    parser.add_argument("--tmp-preferred-padding-y", type=float, default=TMP_PREFERRED_PADDING_Y)
    parser.add_argument("--rodin-font", choices=RODIN_FONT_VARIANTS, default="auto")
    parser.add_argument("--tmp-block-mode", choices=TMP_BLOCK_MODES, default=DEFAULT_TMP_BLOCK_MODE)
    parser.add_argument("--draw-order", choices=DRAW_ORDER_MODES, default="global")
    parser.add_argument("--shape-outline-mode", choices=SHAPE_OUTLINE_MODES, default="sdf")
    parser.add_argument("--triangle-mode", choices=TRIANGLE_MODES, default=DEFAULT_TRIANGLE_MODE)
    parser.add_argument("--text-vertical-mode", choices=TEXT_VERTICAL_MODES, default=DEFAULT_TEXT_VERTICAL_MODE)
    parser.add_argument("--tmp-space-width-factor", type=float, default=DEFAULT_TMP_SPACE_WIDTH_FACTOR)
    parser.add_argument("--tmp-text-render-mode", choices=TMP_TEXT_RENDER_MODES, default=DEFAULT_TMP_TEXT_RENDER_MODE)
    parser.add_argument("--tmp-dynamic-sdf", dest="tmp_dynamic_sdf", action="store_true")
    parser.add_argument("--no-tmp-dynamic-sdf", dest="tmp_dynamic_sdf", action="store_false")
    parser.add_argument(
        "--tmp-decorative-face-only",
        dest="tmp_decorative_face_only",
        action="store_true",
        default=DEFAULT_TMP_DECORATIVE_FACE_ONLY,
        help="Render decorative rich-text symbols without TMP underlay/outline.",
    )
    parser.add_argument(
        "--no-tmp-decorative-face-only",
        dest="tmp_decorative_face_only",
        action="store_false",
        help="Debug: keep TMP underlay/outline on decorative rich-text symbols.",
    )
    parser.add_argument(
        "--premultiply-alpha-transforms",
        action="store_true",
        help=(
            "Experimental: resample transformed RGBA layers in premultiplied alpha space. "
            "This can change semi-transparent artwork colors and is not a global custom-profile fix."
        ),
    )
    parser.add_argument(
        "--tmp-decorative-direct-raster",
        dest="tmp_decorative_direct_raster",
        action="store_true",
        default=DEFAULT_TMP_DECORATIVE_DIRECT_RASTER,
        help="Draw decorative TMP symbols by transforming SDF glyphs directly to the final canvas.",
    )
    parser.add_argument(
        "--no-tmp-decorative-direct-raster",
        dest="tmp_decorative_direct_raster",
        action="store_false",
        help="Debug: use the legacy Pillow layer-transform path for decorative TMP symbols.",
    )
    parser.add_argument(
        "--tmp-decorative-alpha-harden",
        type=float,
        default=1.0,
        help="Experimental: strengthen decorative rich-text alpha coverage after transforms; 1 disables it.",
    )
    parser.add_argument("--tmp-pillow-stroke-factor", type=float, default=DEFAULT_TMP_PILLOW_STROKE_FACTOR)
    parser.add_argument("--shape-sdf-ratio-scale", type=float, default=SHAPE_SDF_RATIO_SCALE)
    parser.add_argument("--shape-sdf-outer-factor", type=float, default=SHAPE_SDF_OUTER_FACTOR)
    parser.add_argument("--shape-sdf-face-factor", type=float, default=SHAPE_SDF_FACE_FACTOR)
    parser.add_argument("--shape-sdf-softness", type=float, default=SHAPE_SDF_SOFTNESS)
    parser.add_argument("--shape-sdf-source", choices=SHAPE_SDF_SOURCES, default="rgb")
    parser.add_argument("--shape-sdf-screen-fwidth", dest="shape_sdf_screen_fwidth", action="store_true")
    parser.add_argument("--no-shape-sdf-screen-fwidth", dest="shape_sdf_screen_fwidth", action="store_false")
    parser.add_argument("--tmp-font-metadata", type=Path, default=DEFAULT_TMP_FONT_METADATA)
    parser.add_argument("--tmp-metrics-mode", choices=TMP_METRIC_MODES, default=DEFAULT_TMP_METRICS_MODE)
    parser.add_argument(
        "--tmp-native-line-gap", dest="tmp_native_line_gap", action="store_true", default=DEFAULT_TMP_NATIVE_LINE_GAP
    )
    parser.add_argument("--no-tmp-native-line-gap", dest="tmp_native_line_gap", action="store_false")
    parser.add_argument("--shape-sprite-dir", type=Path, default=DEFAULT_SHAPE_SPRITE_DIR)
    parser.add_argument("--no-shape-sprites", action="store_true")
    parser.add_argument("--parallel-workers", type=int, default=DEFAULT_PARALLEL_WORKERS)
    parser.add_argument("--parallel-stage", choices=["serial", "transform", "full"], default="transform")
    parser.add_argument("--canvas-clip-transform", dest="canvas_clip_transform", action="store_true")
    parser.add_argument("--no-canvas-clip-transform", dest="canvas_clip_transform", action="store_false")
    parser.add_argument("--skip-empty-lines", action="store_true")
    parser.add_argument("--dump-tmp-layout", type=Path)
    parser.add_argument("--dump-native-audit", type=Path)
    parser.set_defaults(shape_sdf_screen_fwidth=SHAPE_SDF_SCREEN_FWIDTH)
    parser.set_defaults(tmp_dynamic_sdf=DEFAULT_TMP_DYNAMIC_SDF)
    parser.set_defaults(canvas_clip_transform=True)
    return parser


def resolve_render_target(args: argparse.Namespace) -> RenderTargetConfig:
    position_scale_x = args.position_scale_x
    position_scale_y = args.position_scale_y
    # The primary offline output matches the game-visible profile area. The
    # saved profile positions are still interpreted in ProfileCardView units.
    canvas_w = int(PROFILE_RENDER_VIEW_W)
    canvas_h = int(PROFILE_RENDER_VIEW_H)
    origin_x: float | None = PROFILE_RENDER_VIEW_W / 2.0
    origin_y: float | None = PROFILE_RENDER_VIEW_H / 2.0
    if args.full_canvas:
        return RenderTargetConfig(
            canvas_w=CANVAS_W,
            canvas_h=CANVAS_H,
            origin_x=None,
            origin_y=None,
            position_scale_x=position_scale_x,
            position_scale_y=position_scale_y,
        )
    if args.viewer_viewport:
        if args.position_scale is None and position_scale_x is None:
            position_scale_x = PROFILE_POSITION_SCALE_X
        if args.position_scale is None and position_scale_y is None:
            position_scale_y = PROFILE_POSITION_SCALE_Y
    return RenderTargetConfig(
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        origin_x=origin_x,
        origin_y=origin_y,
        position_scale_x=position_scale_x,
        position_scale_y=position_scale_y,
    )


def build_renderer(
    args: argparse.Namespace,
    profile: dict[str, Any],
    target: RenderTargetConfig,
    resources: dict[str, Any] | None = None,
) -> PNGRenderer:
    return PNGRenderer(
        args.masterdata,
        args.assets,
        args.fonts,
        args.text_pivot,
        args.tmp_scale_mode,
        args.rotation_sign,
        args.tmp_font_scale,
        args.text_layout,
        args.position_scale,
        args.tmp_line_mode,
        args.tmp_box_mode,
        not args.skip_empty_lines,
        args.tmp_box_width,
        args.tmp_box_width_factor,
        args.tmp_line_height_factor,
        args.tmp_line_spacing_factor,
        args.tmp_preferred_padding_x,
        args.tmp_preferred_padding_y,
        args.rodin_font,
        args.tmp_block_mode,
        args.draw_order,
        args.shape_outline_mode,
        args.triangle_mode,
        args.text_vertical_mode,
        args.tmp_space_width_factor,
        args.tmp_text_render_mode,
        args.tmp_dynamic_sdf,
        args.tmp_pillow_stroke_factor,
        args.shape_sdf_ratio_scale,
        args.shape_sdf_outer_factor,
        args.shape_sdf_face_factor,
        args.shape_sdf_softness,
        args.shape_sdf_source,
        args.shape_sdf_screen_fwidth,
        target.position_scale_x,
        target.position_scale_y,
        args.tmp_font_metadata,
        args.tmp_metrics_mode,
        None if args.no_shape_sprites else args.shape_sprite_dir,
        args.tmp_native_line_gap,
        profile,
        resources=resources,
        canvas_w=target.canvas_w,
        canvas_h=target.canvas_h,
        origin_x=target.origin_x,
        origin_y=target.origin_y,
        parallel_workers=args.parallel_workers,
        parallel_stage=args.parallel_stage,
        clip_canvas_transform=args.canvas_clip_transform,
        unity_ui_sprite_dir=getattr(args, "unity_ui_sprite_dir", DEFAULT_UNITY_UI_SPRITE_DIR),
        region=getattr(args, "region", "cn"),
        tmp_decorative_face_only=getattr(args, "tmp_decorative_face_only", DEFAULT_TMP_DECORATIVE_FACE_ONLY),
        premultiply_alpha_transforms=getattr(
            args, "premultiply_alpha_transforms", DEFAULT_PREMULTIPLY_ALPHA_TRANSFORMS
        ),
        tmp_decorative_direct_raster=getattr(
            args,
            "tmp_decorative_direct_raster",
            DEFAULT_TMP_DECORATIVE_DIRECT_RASTER,
        ),
        tmp_decorative_alpha_harden=getattr(args, "tmp_decorative_alpha_harden", 1.0),
    )


def validate_cli_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.full_canvas and args.viewer_viewport:
        parser.error("--full-canvas and --viewer-viewport are mutually exclusive")
    if args.request is not None and args.export_request is not None:
        parser.error("--request and --export-request are mutually exclusive")
    if args.request is not None and (
        args.seq is not None or args.card_id is not None or args.custom_profile_id is not None or args.all
    ):
        parser.error("--request already contains the selected card; do not pass selectors")
    if args.parallel_stage == "full":
        print(
            "warning: --parallel-stage full is experimental and may be non-deterministic with TMP/SDF caches; use transform for production",
            file=sys.stderr,
        )


def load_cli_render_job(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]] | None:
    if args.request is not None:
        card, profile_context, resources = decode_custom_profile_render_request(load_json(args.request))
        return profile_context, [card], resources
    profile = normalize_profile_payload(load_json(args.profile))
    cards = select_custom_profile_cards(
        profile,
        seq=args.seq,
        custom_profile_id=args.custom_profile_id,
        custom_profile_card_id=args.card_id,
        all_cards=args.all,
    )
    if args.export_request is not None:
        if len(cards) != 1:
            parser.error("--export-request requires a selector that resolves exactly one card")
        write_json(args.export_request, build_custom_profile_render_request(profile, cards[0]))
        print(args.export_request, flush=True)
        return None
    return build_profile_context(profile), cards, {}


def render_cli_cards(renderer: PNGRenderer, cards: list[dict[str, Any]], out_dir: Path) -> None:
    output_root = out_dir.resolve()
    for card in cards:
        img = renderer.render_card(card)
        filename = custom_profile_output_name(card)
        path = resolve_cli_path(output_root / filename)
        if path.parent != output_root or Path(filename).name != filename:
            raise ValueError(f"unsafe custom profile output filename: {filename!r}")
        img.save(path)
        print(path, flush=True)


def write_cli_audit(path: Path, entries: list[dict[str, Any]]) -> None:
    safe_path = resolve_cli_path(path)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    with safe_path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(safe_path, flush=True)


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    validate_cli_args(parser, args)
    warn_deprecated_probe_args(args)

    args.out = resolve_cli_path(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    render_job = load_cli_render_job(parser, args)
    if render_job is None:
        return
    profile_context, cards, resources = render_job
    renderer = build_renderer(args, profile_context, resolve_render_target(args), resources)
    render_cli_cards(renderer, cards, args.out)
    if args.dump_tmp_layout is not None:
        write_cli_audit(args.dump_tmp_layout, renderer.tmp_layout_audit)
    if args.dump_native_audit is not None:
        write_cli_audit(args.dump_native_audit, renderer.native_audit)


if __name__ == "__main__":
    main()
