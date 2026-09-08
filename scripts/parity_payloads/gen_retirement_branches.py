"""Expand existing requests with deterministic preview, missing-asset and text branches.

Run after the normal generators. The preview is synthetic and lives under the configured
asset root; these cases supplement captured requests and keep the same endpoint budgets.
The outlined TMP variant also requires the separately captured custom_profile_card fixture;
when that capture is absent, the strict sweep reports the derivative as missing too.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from scripts.parity_payloads.retirement_fixture_contract import (
    DECORATIVE_ROTATION,
    DECORATIVE_TEXT,
    FONT_FALLBACK_FONT,
    FONT_FALLBACK_TEXT,
    MISSING_PATH,
    PREVIEW_PATH,
    PREVIEW_WEBP_PATH,
    ROTATED_CHAR_TEXT,
    ROTATED_DECORATIVE_TEXT,
    STATIC_FONT,
    STATIC_MISSING_FONT,
    STATIC_TEXT,
    validate_retirement_branch,
)
from src.settings import ASSETS_BASE_DIR


def build_outlined_text_request(payload: dict) -> dict:
    request = deepcopy(payload)
    layout = request["card"]["customProfileCard"]
    item = next(
        (
            text
            for text in layout.get("texts", [])
            if text.get("objectData", {}).get("visible") is True
            and isinstance(text.get("text"), str)
            and text["text"].strip()
        ),
        None,
    )
    if item is None:
        raise ValueError("custom profile capture must contain visible text for the outline fixture")
    for value in layout.values():
        if isinstance(value, list):
            value.clear()
    item["outlineSize"] = 0.25
    item["outlineAlpha"] = 1.0
    layout["texts"] = [item]
    return request


def build_decorative_text_request(payload: dict) -> dict:
    request = build_outlined_text_request(payload)
    item = request["card"]["customProfileCard"]["texts"][0]
    item["text"] = DECORATIVE_TEXT
    item["objectData"].update(
        {
            "rotation": dict(DECORATIVE_ROTATION),
            "scale": {"x": 1.4, "y": 0.7, "z": 1.0},
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
        }
    )
    return request


def build_static_text_request(payload: dict) -> dict:
    request = build_outlined_text_request(payload)
    item = request["card"]["customProfileCard"]["texts"][0]
    item.update(text=STATIC_TEXT, size=45, fontId=1)
    item["objectData"].update(
        position={"x": 0.0, "y": 0.0, "z": 0.0},
        scale={"x": 1.0, "y": 1.0, "z": 1.0},
        rotation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    )
    request.setdefault("resources", {})["customProfileTextFonts"] = [
        {"id": 1, "fontName": STATIC_FONT, "name": "Static TMP"}
    ]
    return request


def build_rotated_characters_request(payload: dict) -> dict:
    request = build_outlined_text_request(payload)
    item = request["card"]["customProfileCard"]["texts"][0]
    item.update(text=ROTATED_CHAR_TEXT, size=45)
    item["objectData"].update(
        position={"x": 0.0, "y": 0.0, "z": 0.0},
        scale={"x": 1.0, "y": 1.0, "z": 1.0},
        rotation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    )
    return request


def build_rotated_decorative_request(payload: dict) -> dict:
    request = build_decorative_text_request(payload)
    request["card"]["customProfileCard"]["texts"][0]["text"] = ROTATED_DECORATIVE_TEXT
    return request


def build_font_fallback_request(payload: dict, *, static_missing: bool = False) -> dict:
    request = build_static_text_request(payload)
    font = STATIC_MISSING_FONT if static_missing else FONT_FALLBACK_FONT
    request["resources"]["customProfileTextFonts"][0]["fontName"] = font
    request["card"]["customProfileCard"]["texts"][0]["text"] = FONT_FALLBACK_TEXT
    return request


def build_honor_deck_resized_request(payload: dict) -> dict:
    """Reuse captured badges at opposite-sized slots, exercising both resize directions."""
    request = deepcopy(payload)
    layout = request["card"]["customProfileCard"]
    generals = [
        item
        for item in layout.get("generals", [])
        if int(item.get("type", item.get("id", 0)) or 0) == 6 and item.get("objectData", {}).get("visible") is True
    ]
    if not generals:
        raise ValueError("custom profile capture must contain a visible HonorDeck")
    for value in layout.values():
        if isinstance(value, list):
            value.clear()
    layout["generals"] = generals[:1]
    generals[0]["objectData"].update(
        position={"x": 0.0, "y": 0.0, "z": 0.0},
        scale={"x": 1.0, "y": 1.0, "z": 1.0},
        rotation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    )
    badges = request["resources"]["profileHonorRequests"]
    badges["profile:1"], badges["profile:2"] = badges["profile:2"], badges["profile:1"]
    return request


def generate(payload_dir: Path = ROOT / "out/parity-payloads", assets_dir: Path = ASSETS_BASE_DIR) -> list[str]:
    def read(name):
        return json.loads((payload_dir / f"{name}.json").read_text())

    if (assets_dir / MISSING_PATH).exists():
        raise ValueError("the deliberately missing retirement fixture must not exist")
    path = assets_dir / PREVIEW_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (1401, 1003), (245, 248, 251, 255))
    ImageDraw.Draw(image).rectangle((420, 80, 830, 970), fill=(80, 60, 55, 255))
    image.save(path)
    image.save(assets_dir / PREVIEW_WEBP_PATH, quality=90)
    costume = read("costume_detail")
    costume["costume"]["preview_image_path"] = PREVIEW_PATH
    costume_webp = deepcopy(costume)
    costume_webp["costume"]["preview_image_path"] = PREVIEW_WEBP_PATH
    gacha_list = read("gacha_list")
    gacha_list["gacha_logos"] = {str(g["id"]): MISSING_PATH for g in gacha_list["gachas"]}
    gacha_list["gacha_banners"] = dict(gacha_list["gacha_logos"])

    def missing_paths(value):
        if isinstance(value, dict):
            return {
                key: MISSING_PATH if key.endswith("_path") and child else missing_paths(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [missing_paths(child) for child in value]
        return value

    requests = {
        "costume_detail_preview": costume,
        "costume_detail_preview_webp": costume_webp,
        "gacha_list_missing_assets": gacha_list,
        "gacha_detail_missing_assets": missing_paths(read("gacha_detail")),
    }
    if (payload_dir / "custom_profile_card.json").is_file():
        requests["custom_profile_card_outlined_text"] = build_outlined_text_request(read("custom_profile_card"))
        requests["custom_profile_card_decorative_text"] = build_decorative_text_request(read("custom_profile_card"))
        requests["custom_profile_card_static_text"] = build_static_text_request(read("custom_profile_card"))
        requests["custom_profile_card_rotated_characters"] = build_rotated_characters_request(
            read("custom_profile_card")
        )
        requests["custom_profile_card_rotated_decorative"] = build_rotated_decorative_request(
            read("custom_profile_card")
        )
        requests["custom_profile_card_font_fallback"] = build_font_fallback_request(read("custom_profile_card"))
        requests["custom_profile_card_static_missing_glyphs"] = build_font_fallback_request(
            read("custom_profile_card"), static_missing=True
        )
        requests["custom_profile_card_honor_deck_resized"] = build_honor_deck_resized_request(
            read("custom_profile_card")
        )
    for name, request in requests.items():
        validate_retirement_branch(name, request, assets_dir)
        (payload_dir / f"{name}.json").write_text(json.dumps(request, ensure_ascii=False, indent=1))
    return list(requests)


if __name__ == "__main__":
    print(json.dumps(generate()))  # noqa: T201
