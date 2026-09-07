import json

from PIL import Image
import pytest

from scripts.parity_payloads.retirement_fixture_contract import MISSING_PATH, PREVIEW_PATH, validate_retirement_branch


def _captured_deck_request(assets_dir):
    for name, size in (("main.png", (380, 80)), ("sub.png", (180, 80))):
        Image.new("RGBA", size, (40, 60, 80, 128)).save(assets_dir / name)
    return {
        "card": {
            "customProfileCard": {
                "generals": [{"type": 6, "objectData": {"visible": True}}],
                "texts": [{"text": "captured", "objectData": {"visible": True}}],
            }
        },
        "profile_context": {"userProfileHonors": [{"seq": i} for i in range(1, 4)]},
        "resources": {
            "profileHonorRequests": {
                f"profile:{i}": {"honor_type": "normal", "honor_img_path": "main.png" if i == 1 else "sub.png"}
                for i in range(1, 4)
            }
        },
    }


@pytest.mark.parametrize("mutation", ["dimensions", "slots", "hidden", "position", "missing"])
def test_honor_deck_resize_fixture_cannot_skip_resize_or_hide_the_result(tmp_path, mutation):
    from scripts.parity_payloads.gen_retirement_branches import build_honor_deck_resized_request

    original = _captured_deck_request(tmp_path)
    request = build_honor_deck_resized_request(original)
    name = "custom_profile_card_honor_deck_resized"
    validate_retirement_branch(name, request, tmp_path)
    assert original["resources"]["profileHonorRequests"]["profile:1"]["honor_img_path"] == "main.png"
    assert original["card"]["customProfileCard"]["texts"]
    if mutation == "dimensions":
        Image.new("RGBA", (380, 80)).save(tmp_path / "sub.png")
    elif mutation == "slots":
        request["profile_context"]["userProfileHonors"].pop()
    elif mutation == "hidden":
        request["card"]["customProfileCard"]["generals"][0]["objectData"]["visible"] = False
    elif mutation == "position":
        request["card"]["customProfileCard"]["generals"][0]["objectData"]["position"]["x"] = 999999
    else:
        (tmp_path / "sub.png").unlink()
    with pytest.raises((ValueError, OSError)):
        validate_retirement_branch(name, request, tmp_path)


def test_generator_recreates_all_registered_retirement_branches_in_a_clean_directory(tmp_path, monkeypatch):
    from scripts.parity_payloads import retirement_fixture_contract as contract
    from scripts.parity_payloads.gen_retirement_branches import generate
    from scripts.skia_parity_sweep import CASES

    monkeypatch.setattr(contract, "validate_static_font_asset", lambda region: None)
    monkeypatch.setattr(contract, "validate_fallback_font_asset", lambda region, font: None)
    inputs = {
        "custom_profile_card": _captured_deck_request(tmp_path),
        "costume_detail": {"costume": {}},
        "gacha_list": {"gachas": [{"id": 1}]},
        "gacha_detail": {"logo_img_path": "original.png"},
    }
    for name, payload in inputs.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(payload))
    written = set(generate(tmp_path, tmp_path))
    # Every synthetic/derived retirement Case must be reproducible, even when no prior out/
    # files survive. Original captured card/collections and uncaptured symbol/stamps are separate.
    expected = {
        case.name
        for case in CASES
        if case.name.startswith("custom_profile_card_")
        and case.name
        not in {"custom_profile_card_collections", "custom_profile_card_symbol", "custom_profile_card_stamps"}
    }
    assert expected <= written
    assert written == {path.stem for path in tmp_path.glob("*.json")} - set(inputs)
    for name in written:
        validate_retirement_branch(name, json.loads((tmp_path / f"{name}.json").read_text()), tmp_path)


def test_missing_or_replaced_preview_cannot_pass_as_placeholder(tmp_path):
    request = {"costume": {"preview_image_path": PREVIEW_PATH}}
    with pytest.raises(ValueError, match="preview is missing"):
        validate_retirement_branch("costume_detail_preview", request, tmp_path)
    path = tmp_path / PREVIEW_PATH
    path.parent.mkdir(parents=True)
    Image.new("RGB", (7, 5)).save(path)
    with pytest.raises(ValueError, match="unexpected dimensions"):
        validate_retirement_branch("costume_detail_preview", request, tmp_path)
    with pytest.raises(ValueError, match="activate"):
        validate_retirement_branch("costume_detail_preview", {"costume": {}}, tmp_path)


def test_missing_gacha_fixture_requires_nonempty_requests_and_absent_asset(tmp_path):
    request = {"gachas": [{"id": 1}], "gacha_logos": {"1": MISSING_PATH}, "gacha_banners": {"1": MISSING_PATH}}
    validate_retirement_branch("gacha_list_missing_assets", request, tmp_path)
    with pytest.raises(ValueError, match="both missing"):
        validate_retirement_branch("gacha_list_missing_assets", {"gachas": []}, tmp_path)
    path = tmp_path / MISSING_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(b"unexpected asset")
    with pytest.raises(ValueError, match="must not exist"):
        validate_retirement_branch("gacha_list_missing_assets", request, tmp_path)


def test_missing_detail_fixture_requires_image_paths(tmp_path):
    validate_retirement_branch("gacha_detail_missing_assets", {"logo_img_path": MISSING_PATH}, tmp_path)
    with pytest.raises(ValueError, match="missing image paths"):
        validate_retirement_branch("gacha_detail_missing_assets", {"logo_img_path": "actual.png"}, tmp_path)


@pytest.mark.parametrize(
    "change",
    [{"outlineSize": 0}, {"outlineAlpha": 0}, {"text": ""}, {"text": None}, {"objectData": {"visible": False}}],
)
def test_outline_fixture_cannot_silently_become_plain_or_invisible(change):
    from scripts.parity_payloads.gen_retirement_branches import build_outlined_text_request

    original = {
        "card": {
            "customProfileCard": {
                "texts": [{"text": "TMP", "outlineSize": 0, "objectData": {"visible": True, "layer": 1}}],
                "shapes": [{"id": 1}],
            }
        }
    }
    request = build_outlined_text_request(original)
    assert original["card"]["customProfileCard"]["texts"][0]["outlineSize"] == 0
    assert original["card"]["customProfileCard"]["shapes"] == [{"id": 1}]
    validate_retirement_branch("custom_profile_card_outlined_text", request)
    request["card"]["customProfileCard"]["texts"][0].update(change)
    with pytest.raises(ValueError, match="exactly one visible text"):
        validate_retirement_branch("custom_profile_card_outlined_text", request)


@pytest.mark.parametrize("mutation", ["markup", "rotation", "scale", "position"])
def test_decorative_fixture_cannot_turn_into_a_plain_or_invisible_request(mutation):
    from scripts.parity_payloads.gen_retirement_branches import build_decorative_text_request

    original = {"card": {"customProfileCard": {"texts": [{"text": "captured", "objectData": {"visible": True}}]}}}
    request = build_decorative_text_request(original)
    validate_retirement_branch("custom_profile_card_decorative_text", request)
    assert original["card"]["customProfileCard"]["texts"][0]["text"] == "captured"
    text = request["card"]["customProfileCard"]["texts"][0]
    if mutation == "markup":
        text["text"] = "●▲〜"
    elif mutation == "rotation":
        text["objectData"]["rotation"]["z"] = 0
    elif mutation == "scale":
        text["objectData"]["scale"]["y"] = 1.4
    else:
        text["objectData"]["position"]["x"] = 999999
    with pytest.raises(ValueError, match="decorative TMP"):
        validate_retirement_branch("custom_profile_card_decorative_text", request)


@pytest.mark.parametrize("mutation", ["font", "text", "size", "position", "scale", "rotation"])
def test_static_fixture_cannot_silently_switch_font_or_hide_text(monkeypatch, mutation):
    from scripts.parity_payloads import retirement_fixture_contract as contract
    from scripts.parity_payloads.gen_retirement_branches import build_static_text_request

    monkeypatch.setattr(contract, "validate_static_font_asset", lambda region: None)
    original = {"card": {"customProfileCard": {"texts": [{"text": "captured", "objectData": {"visible": True}}]}}}
    request = build_static_text_request(original)
    validate_retirement_branch("custom_profile_card_static_text", request)
    item = request["card"]["customProfileCard"]["texts"][0]
    if mutation == "font":
        request["resources"]["customProfileTextFonts"][0]["fontName"] = "dynamic-font"
    elif mutation == "text":
        item["text"] = "missing-glyph"
    elif mutation == "size":
        item["size"] = 1
    else:
        item["objectData"][mutation]["x"] = 9999
    with pytest.raises(ValueError, match="static TMP fixture"):
        validate_retirement_branch("custom_profile_card_static_text", request)


def test_static_fixture_rejects_missing_or_dynamic_metadata(monkeypatch):
    from types import SimpleNamespace

    from scripts.parity_payloads.retirement_fixture_contract import validate_static_font_asset
    from src.sekai.profile.custom_profile.renderer import TMPFontLibrary

    for asset in (None, SimpleNamespace(atlas_population_mode=1)):
        monkeypatch.setattr(
            TMPFontLibrary, "load", lambda *a, value=asset: SimpleNamespace(active_asset=lambda _: value)
        )
        with pytest.raises(ValueError, match="extracted glyph table"):
            validate_static_font_asset("cn")


@pytest.mark.parametrize("mutation", ["text", "size", "position", "rotation"])
def test_rotated_character_fixture_cannot_lose_its_branch(mutation):
    from scripts.parity_payloads.gen_retirement_branches import build_rotated_characters_request

    original = {"card": {"customProfileCard": {"texts": [{"text": "captured", "objectData": {"visible": True}}]}}}
    request = build_rotated_characters_request(original)
    validate_retirement_branch("custom_profile_card_rotated_characters", request)
    item = request["card"]["customProfileCard"]["texts"][0]
    if mutation == "text":
        item["text"] = "日本語"
    elif mutation == "size":
        item["size"] = 1
    else:
        item["objectData"][mutation]["x"] = 9999
    with pytest.raises(ValueError, match="rotated-character TMP fixture"):
        validate_retirement_branch("custom_profile_card_rotated_characters", request)


def test_rotated_decorative_fixture_cannot_drop_character_rotation():
    from scripts.parity_payloads.gen_retirement_branches import build_rotated_decorative_request
    from scripts.parity_payloads.retirement_fixture_contract import DECORATIVE_TEXT

    original = {"card": {"customProfileCard": {"texts": [{"text": "captured", "objectData": {"visible": True}}]}}}
    request = build_rotated_decorative_request(original)
    validate_retirement_branch("custom_profile_card_rotated_decorative", request)
    request["card"]["customProfileCard"]["texts"][0]["text"] = DECORATIVE_TEXT
    with pytest.raises(ValueError, match="decorative TMP fixture"):
        validate_retirement_branch("custom_profile_card_rotated_decorative", request)


@pytest.mark.parametrize("static_missing", [False, True])
@pytest.mark.parametrize("mutation", ["font", "zero-width", "size", "position"])
def test_font_fallback_fixture_cannot_drop_its_missing_character_branch(monkeypatch, static_missing, mutation):
    from scripts.parity_payloads import retirement_fixture_contract as contract
    from scripts.parity_payloads.gen_retirement_branches import build_font_fallback_request

    monkeypatch.setattr(contract, "validate_fallback_font_asset", lambda *args: None)
    original = {"card": {"customProfileCard": {"texts": [{"text": "captured", "objectData": {"visible": True}}]}}}
    request = build_font_fallback_request(original, static_missing=static_missing)
    name = "custom_profile_card_static_missing_glyphs" if static_missing else "custom_profile_card_font_fallback"
    validate_retirement_branch(name, request)
    item = request["card"]["customProfileCard"]["texts"][0]
    if mutation == "font":
        request["resources"]["customProfileTextFonts"][0]["fontName"] = "wrong-font"
    elif mutation == "zero-width":
        item["text"] = item["text"].replace("\u200b", "")
    elif mutation == "size":
        item["size"] = 1
    else:
        item["objectData"]["position"]["x"] = 9999
    with pytest.raises(ValueError, match="font fallback fixture"):
        validate_retirement_branch(name, request)
