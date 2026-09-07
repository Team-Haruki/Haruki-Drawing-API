"""Preconditions that keep synthetic branch requests from silently testing placeholders."""

from pathlib import Path

PREVIEW_PATH = "utils/retirement_fixtures/costume-preview.png"
PREVIEW_WEBP_PATH = "utils/retirement_fixtures/costume-preview.webp"
DECORATIVE_TEXT = "<color=#7030b0><size=45>●▲〜</size></color>"
ROTATED_DECORATIVE_TEXT = "<color=#7030b0><size=45><rotate=20>●▲〜</rotate></size></color>"
DECORATIVE_ROTATION = {"x": 0.0, "y": 0.0, "z": 0.1993679344171972, "w": 0.9799247046208296}

ROTATED_CHAR_TEXT = "<color=#7030b0><rotate=20>日本語</rotate></color>"

FONT_FALLBACK_FONT = "FOT-RodinNTLGPro-DB"
STATIC_MISSING_FONT = "FOT-RodinNTLGPro-EB-OnDemand"
FONT_FALLBACK_TEXT = "Ag日\u200b本語🙂𠮷á\u200d\t"

STATIC_FONT = "FOT-RodinNTLGPro-DB-OnDemand"
STATIC_TEXT = "日本語世界"

MISSING_PATH = "utils/retirement_fixtures/intentionally-absent.png"


def validate_retirement_branch(name: str, payload: dict, assets_dir: Path | None = None) -> None:
    if name == "custom_profile_card_honor_deck_resized":
        validate_honor_deck_resize(payload, assets_dir)
        return
    if name in {
        "custom_profile_card_outlined_text",
        "custom_profile_card_decorative_text",
        "custom_profile_card_static_text",
        "custom_profile_card_rotated_characters",
        "custom_profile_card_rotated_decorative",
        "custom_profile_card_font_fallback",
        "custom_profile_card_static_missing_glyphs",
    }:
        layout = payload.get("card", {}).get("customProfileCard", {})
        texts = layout.get("texts", [])
        if (
            len(texts) != 1
            or not isinstance(texts[0].get("text"), str)
            or not texts[0]["text"].strip()
            or texts[0].get("objectData", {}).get("visible") is not True
            or texts[0].get("outlineSize") != 0.25
            or texts[0].get("outlineAlpha") != 1.0
            or any(value for key, value in layout.items() if key != "texts" and isinstance(value, list))
        ):
            raise ValueError("outlined TMP fixture must contain exactly one visible text with its outline enabled")
        if name in {"custom_profile_card_font_fallback", "custom_profile_card_static_missing_glyphs"}:
            font = STATIC_MISSING_FONT if name.endswith("static_missing_glyphs") else FONT_FALLBACK_FONT
            item = texts[0]
            obj = item.get("objectData", {})
            fonts = payload.get("resources", {}).get("customProfileTextFonts", [])
            if (
                item["text"] != FONT_FALLBACK_TEXT
                or item.get("size") != 45
                or item.get("fontId") != 1
                or len(fonts) != 1
                or fonts[0].get("id") != 1
                or fonts[0].get("fontName") != font
                or obj.get("position") != {"x": 0.0, "y": 0.0, "z": 0.0}
                or obj.get("scale") != {"x": 1.0, "y": 1.0, "z": 1.0}
                or obj.get("rotation") != {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
            ):
                raise ValueError("font fallback fixture must keep its font, invisible/missing characters and placement")
            validate_fallback_font_asset(payload.get("region", "cn"), font)
        if name == "custom_profile_card_rotated_characters":
            item = texts[0]
            obj = item.get("objectData", {})
            if (
                item["text"] != ROTATED_CHAR_TEXT
                or item.get("size") != 45
                or obj.get("position") != {"x": 0.0, "y": 0.0, "z": 0.0}
                or obj.get("scale") != {"x": 1.0, "y": 1.0, "z": 1.0}
                or obj.get("rotation") != {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
            ):
                raise ValueError(
                    "rotated-character TMP fixture must keep its per-character rotation and visible placement"
                )
        if name == "custom_profile_card_static_text":
            item = texts[0]
            obj = item.get("objectData", {})
            fonts = payload.get("resources", {}).get("customProfileTextFonts", [])
            if (
                item["text"] != STATIC_TEXT
                or item.get("size") != 45
                or item.get("fontId") != 1
                or len(fonts) != 1
                or fonts[0].get("id") != 1
                or fonts[0].get("fontName") != STATIC_FONT
                or obj.get("position") != {"x": 0.0, "y": 0.0, "z": 0.0}
                or obj.get("scale") != {"x": 1.0, "y": 1.0, "z": 1.0}
                or obj.get("rotation") != {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
            ):
                raise ValueError("static TMP fixture must keep its atlas font, glyphs and visible placement")
            validate_static_font_asset(payload.get("region", "cn"))
        if name in {"custom_profile_card_decorative_text", "custom_profile_card_rotated_decorative"}:
            item = texts[0]
            obj = item.get("objectData", {})
            text = ROTATED_DECORATIVE_TEXT if name.endswith("rotated_decorative") else DECORATIVE_TEXT
            if (
                item["text"] != text
                or obj.get("rotation") != DECORATIVE_ROTATION
                or obj.get("scale") != {"x": 1.4, "y": 0.7, "z": 1.0}
                or obj.get("position") != {"x": 0.0, "y": 0.0, "z": 0.0}
            ):
                raise ValueError("decorative TMP fixture must keep its markup, rotation and nonuniform scale")
        return
    if name not in {
        "costume_detail_preview_webp",
        "costume_detail_preview",
        "gacha_list_missing_assets",
        "gacha_detail_missing_assets",
    }:
        return
    if assets_dir is None:
        from src.settings import ASSETS_BASE_DIR

        assets_dir = ASSETS_BASE_DIR
    if name in {"costume_detail_preview", "costume_detail_preview_webp"}:
        preview_path = PREVIEW_WEBP_PATH if name.endswith("_webp") else PREVIEW_PATH
        from src.sekai.base.image_info import probe_asset

        if payload.get("costume", {}).get("preview_image_path") != preview_path:
            raise ValueError("costume preview fixture must activate its generated preview")
        if not (assets_dir / preview_path).is_file():
            raise ValueError("generated costume preview is missing; run gen_retirement_branches.py")
        if probe_asset(assets_dir / preview_path)[0] != (1401, 1003):
            raise ValueError("generated costume preview has unexpected dimensions")
        return
    if (assets_dir / MISSING_PATH).exists():
        raise ValueError("the deliberately missing retirement fixture must not exist")
    if name == "gacha_list_missing_assets":
        expected = {str(g["id"]): MISSING_PATH for g in payload.get("gachas", [])}
        if not expected or payload.get("gacha_logos") != expected or payload.get("gacha_banners") != expected:
            raise ValueError("gacha fixture must exercise both missing logo and banner paths")
        return

    def paths(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.endswith("_path") and child:
                    yield child
                else:
                    yield from paths(child)
        elif isinstance(value, list):
            for child in value:
                yield from paths(child)

    used = list(paths(payload))
    if not used or any(path != MISSING_PATH for path in used):
        raise ValueError("gacha detail fixture must exercise its missing image paths")


def validate_honor_deck_resize(payload: dict, assets_dir: Path | None) -> None:
    from src.sekai.base.image_info import probe_asset
    from src.settings import ASSETS_BASE_DIR

    assets_dir = ASSETS_BASE_DIR if assets_dir is None else assets_dir
    layout = payload.get("card", {}).get("customProfileCard", {})
    generals = layout.get("generals", [])
    rows = payload.get("profile_context", {}).get("userProfileHonors", [])
    if (
        len(generals) != 1
        or int(generals[0].get("type", generals[0].get("id", 0)) or 0) != 6
        or generals[0].get("objectData", {}).get("visible") is not True
        or any(value for key, value in layout.items() if key != "generals" and isinstance(value, list))
        or sorted(row.get("seq", 0) for row in rows) != [1, 2, 3]
    ):
        raise ValueError("HonorDeck resize fixture requires one visible deck and all three slots")
    obj = generals[0]["objectData"]
    if (
        obj.get("position") != {"x": 0.0, "y": 0.0, "z": 0.0}
        or obj.get("scale") != {"x": 1.0, "y": 1.0, "z": 1.0}
        or obj.get("rotation") != {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
    ):
        raise ValueError("HonorDeck resize fixture must keep its visible centered placement")
    badges = payload.get("resources", {}).get("profileHonorRequests", {})
    for index, size in enumerate(((180, 80), (380, 80), (180, 80)), start=1):
        badge = badges.get(f"profile:{index}", {})
        path = badge.get("honor_img_path")
        if badge.get("honor_type") not in {"normal", "birthday"} or badge.get("is_empty") or not path:
            raise ValueError("HonorDeck resize fixture must preserve its captured badge requests")
        if probe_asset(assets_dir / path)[0] != size:
            raise ValueError("HonorDeck resize fixture must exercise both upscale and downscale with real assets")


def validate_static_font_asset(region: str) -> None:
    from src.sekai.profile.custom_profile.renderer import TMPFontLibrary
    from src.sekai.profile.custom_profile.resource_paths import _optional_region_file
    from src.settings import CUSTOM_PROFILE_TMP_FONT_METADATA

    metadata = _optional_region_file("custom_profile_tmp_font_metadata", CUSTOM_PROFILE_TMP_FONT_METADATA, region)
    asset = TMPFontLibrary.load(metadata).active_asset(STATIC_FONT)
    if (
        asset is None
        or asset.atlas_population_mode != 0
        or not asset.has_static_glyphs
        or any(ord(char) not in asset.glyphs for char in STATIC_TEXT)
        or not asset.atlas_paths
        or any(not path.is_file() for path in asset.atlas_paths)
    ):
        raise ValueError("static TMP fixture requires its extracted glyph table and atlas assets")


def validate_fallback_font_asset(region: str, font: str) -> None:
    from src.sekai.profile.custom_profile.renderer import TMPFontLibrary
    from src.sekai.profile.custom_profile.resource_paths import _optional_region_file
    from src.settings import CUSTOM_PROFILE_TMP_FONT_METADATA

    metadata = _optional_region_file("custom_profile_tmp_font_metadata", CUSTOM_PROFILE_TMP_FONT_METADATA, region)
    library = TMPFontLibrary.load(metadata)
    asset = library.active_asset(font)
    if asset is None:
        raise ValueError("font fallback fixture requires its extracted font metadata")
    if font == FONT_FALLBACK_FONT:
        if (
            library.runtime_source_font_path(asset) is None
            or library.source_glyph_metrics(font, "\u200b", 90, include_fallback=True) is not None
        ):
            raise ValueError("font fallback fixture requires missing U+200B source metrics")
    elif not asset.atlas_paths or any(not path.is_file() for path in asset.atlas_paths):
        raise ValueError("font fallback fixture requires its static atlas")
