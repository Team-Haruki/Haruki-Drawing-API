from pathlib import Path

from PIL import Image

from src.sekai.profile.custom_profile import drawer as custom_profile_drawer
from src.sekai.profile.custom_profile.drawer import _optional_region_file, _region_path_candidates, _require_region_path
from src.sekai.profile.custom_profile.renderer import (
    NativeContent,
    NativeUnresolvedContent,
    PNGRenderer,
    PreparedLayer,
    RenderedLayer,
    build_arg_parser,
    harden_rgba_alpha,
    resize_rgba_premul,
)
from src.sekai.profile.custom_profile.split import decode_custom_profile_render_request
from src.sekai.profile.custom_profile.svg import TextRun, TextStyle, parse_tmp_text


def _base_tmp_style() -> TextStyle:
    return TextStyle(
        color="#000000",
        alpha=1.0,
        size=24.0,
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


def test_custom_profile_scale_tag_uses_first_tmp_attribute_value() -> None:
    tokens = parse_tmp_text("<scale=3 4><size=300><#9a4d3b>●", _base_tmp_style())
    runs = [token for token in tokens if isinstance(token, TextRun)]

    assert len(runs) == 1
    assert runs[0].text == "●"
    assert runs[0].style.scale_x == 3.0
    assert runs[0].style.size == 300.0
    assert runs[0].style.color == "#9a4d3b"


def test_custom_profile_tmp_parser_tolerates_real_profile_tag_typos() -> None:
    tokens = parse_tmp_text("<scale=1.8.><#FDECEI><pos=35><alpha=61#>●", _base_tmp_style())
    runs = [token for token in tokens if isinstance(token, TextRun)]

    assert len(runs) == 1
    assert runs[0].text == "●"
    assert runs[0].style.scale_x == 1.8
    assert runs[0].style.color == "#fdecef"
    assert runs[0].style.pos == 35.0
    assert 0.37 < runs[0].style.alpha < 0.39


def test_custom_profile_tmp_parser_consumes_pos_tag_between_symbols() -> None:
    tokens = parse_tmp_text("<size=80><scale=0.7><#D56844>▲<pos=35><voffset=-14>▲", _base_tmp_style())
    runs = [token for token in tokens if isinstance(token, TextRun)]

    assert "".join(run.text for run in runs) == "▲▲"
    assert not any("<" in run.text or ">" in run.text for run in runs)


def test_custom_profile_tmp_parser_tolerates_o_in_hex_color() -> None:
    tokens = parse_tmp_text("<size=56><#FOBDBA><scale=6>●", _base_tmp_style())
    runs = [token for token in tokens if isinstance(token, TextRun)]

    assert len(runs) == 1
    assert runs[0].text == "●"
    assert runs[0].style.color == "#ffbdba"


def _write_png(path: Path, size: tuple[int, int] = (3, 2)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (255, 0, 0, 255)).save(path)


def _write_png_color(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def _image_has_content_in_box(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    return image.crop(box).getchannel("A").getbbox() is not None


def _make_renderer(
    tmp_path: Path,
    *,
    profile_context: dict | None = None,
    resources: dict | None = None,
    region: str = "cn",
    **renderer_kwargs: object,
) -> PNGRenderer:
    fonts = tmp_path / "fonts"
    assets = tmp_path / "asset" / f"{region}-assets" / "startapp" / "custom_profile"
    fonts.mkdir(exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    return PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=fonts,
        resources=resources or {},
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=None,
        profile_context=profile_context or {},
        region=region,
        **renderer_kwargs,
    )


def test_custom_profile_decorative_face_only_only_matches_symbol_rich_text(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, tmp_decorative_face_only=True)
    decorative = {
        "text": "<color=#F9D2C0><size=160><scale=2.2>●",
        "outlineSize": 0.08361797034740448,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }
    normal = {
        "text": "<color=#F9D2C0>Hello",
        "outlineSize": 0.08361797034740448,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }

    assert renderer.is_decorative_text_item(decorative)
    assert renderer.decorative_outline_dilate(decorative, decorative["outlineSize"]) == 0.0
    assert not renderer.is_decorative_text_item(normal)
    assert renderer.decorative_outline_dilate(normal, normal["outlineSize"]) == normal["outlineSize"]


def test_custom_profile_decorative_face_only_matches_seq08_symbols(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, tmp_decorative_face_only=True)
    decorative = {
        "text": "<scale=.8>▼〇∽︿>",
        "outlineSize": 0.1,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }

    assert renderer.is_decorative_text_item(decorative)
    assert renderer.decorative_outline_dilate(decorative, decorative["outlineSize"]) == 0.0


def test_custom_profile_decorative_direct_raster_is_default(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    decorative = {
        "text": "<color=#F9D2C0><size=160><scale=2.2>●",
        "outlineSize": 0.08361797034740448,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }

    assert renderer.is_decorative_text_item(decorative)
    assert renderer.decorative_outline_dilate(decorative, decorative["outlineSize"]) == 0.0
    assert renderer.tmp_decorative_face_only
    assert renderer.tmp_decorative_direct_raster
    assert not renderer.premultiply_alpha_transforms


def test_custom_profile_decorative_face_only_can_be_disabled(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, tmp_decorative_face_only=False, tmp_decorative_direct_raster=False)
    decorative = {
        "text": "<color=#F9D2C0><size=160><scale=2.2>●",
        "outlineSize": 0.08361797034740448,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }

    assert renderer.is_decorative_text_item(decorative)
    assert renderer.decorative_outline_dilate(decorative, decorative["outlineSize"]) == decorative["outlineSize"]
    assert not renderer.tmp_decorative_face_only
    assert not renderer.tmp_decorative_direct_raster


def test_custom_profile_cli_uses_decorative_tmp_main_logic_by_default() -> None:
    parser = build_arg_parser()
    args = parser.parse_args([])

    assert args.tmp_decorative_face_only
    assert args.tmp_decorative_direct_raster
    assert not args.premultiply_alpha_transforms

    disabled = parser.parse_args(["--no-tmp-decorative-face-only", "--no-tmp-decorative-direct-raster"])
    assert not disabled.tmp_decorative_face_only
    assert not disabled.tmp_decorative_direct_raster


def test_custom_profile_premul_resize_does_not_bleed_transparent_rgb() -> None:
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    image.putpixel((0, 0), (255, 255, 255, 0))
    image.putpixel((1, 0), (255, 0, 0, 255))

    resized = resize_rgba_premul(image, (1, 1), Image.Resampling.BILINEAR)

    r, g, b, a = resized.getpixel((0, 0))
    assert a > 0
    assert r > 0
    assert g == 0
    assert b == 0


def test_custom_profile_harden_alpha_preserves_layer_opacity() -> None:
    image = Image.new("RGBA", (3, 1), (100, 120, 140, 0))
    image.putpixel((0, 0), (100, 120, 140, 0))
    image.putpixel((1, 0), (100, 120, 140, 32))
    image.putpixel((2, 0), (100, 120, 140, 64))

    hardened = harden_rgba_alpha(image, 8.0)

    assert hardened.getpixel((0, 0))[3] == 0
    assert hardened.getpixel((1, 0))[3] > 32
    assert hardened.getpixel((2, 0))[3] == 64


def test_custom_profile_direct_raster_preserves_mixed_layer_order(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        tmp_decorative_direct_raster=True,
        canvas_w=1,
        canvas_h=1,
        origin_x=0.0,
        origin_y=0.0,
    )
    direct_first = NativeContent(1, "text", {"id": 1}, {"visible": True})
    deferred_middle = NativeContent(2, "shape", {"id": 2}, {"visible": True})
    direct_last = NativeContent(3, "text", {"id": 3}, {"visible": True})
    renderer.build_native_contents = lambda card: [direct_first, deferred_middle, direct_last]  # type: ignore[method-assign]

    def draw_direct(canvas: Image.Image, content: NativeContent) -> bool:
        if content.kind != "text":
            return False
        color = (255, 0, 0, 255) if content.layer == 1 else (0, 0, 255, 255)
        canvas.alpha_composite(Image.new("RGBA", (1, 1), color), (0, 0))
        return True

    def draw_deferred(content: NativeContent) -> RenderedLayer:
        return RenderedLayer(
            content,
            "rendered",
            (Image.new("RGBA", (1, 1), (0, 255, 0, 255)), (0.0, 0.0)),
            PreparedLayer(Image.new("RGBA", (1, 1), (0, 255, 0, 255)), (0, 0)),
        )

    renderer.render_content_direct_on_card = draw_direct  # type: ignore[method-assign]
    renderer.render_and_prepare_content_for_card = draw_deferred  # type: ignore[method-assign]

    rendered = renderer.render_card({"customProfileCard": {}})

    assert rendered.getpixel((0, 0)) == (0, 0, 255, 255)


def test_custom_profile_stamp_uses_cloud_region_asset_layout(tmp_path: Path) -> None:
    stamp_path = tmp_path / "asset" / "cn-assets" / "startapp" / "stamp" / "stamp0230" / "stamp0230.png"
    stamp_path.parent.mkdir(parents=True)
    stamp_path.write_bytes(b"png")

    renderer = _make_renderer(
        tmp_path,
        resources={
            "stamps": {146: {"id": 146, "assetbundleName": "stamp0230"}},
            "stampAssets": {
                146: {
                    "id": 146,
                    "assetbundleName": "stamp0230",
                    "imagePath": "asset/cn-assets/startapp/stamp/stamp0230/stamp0230.png",
                }
            },
        },
    )

    assert renderer.resolve_request_asset_path(renderer.stamp_assets[146]["imagePath"]) == stamp_path


def test_custom_profile_stamp_does_not_use_non_cloud_stamp_filename(tmp_path: Path) -> None:
    stamp_path = tmp_path / "asset" / "cn-assets" / "startapp" / "stamp" / "stamp0230" / "stamp.png"
    stamp_path.parent.mkdir(parents=True)
    stamp_path.write_bytes(b"png")

    renderer = _make_renderer(tmp_path, resources={"stamps": {146: {"id": 146, "assetbundleName": "stamp0230"}}})

    assert renderer.stamp_resource_path(renderer.stamps[146]) is None


def test_custom_profile_resource_path_requires_cloud_image_path_without_masterdata(tmp_path: Path) -> None:
    bg_path = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile" / "bg" / "profile_bg_pattern_0001.png"
    _write_png(bg_path)

    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileGeneralBackgroundResources": {
                1: {
                    "id": 1,
                    "resourceLoadVal": "custom_profile/bg",
                    "fileName": "profile_bg_pattern_0001",
                }
            }
        },
    )
    assert renderer.resource_path(renderer.general_bgs[1]) is None

    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileGeneralBackgroundResources": {
                1: {
                    "id": 1,
                    "resourceLoadVal": "custom_profile/bg",
                    "fileName": "profile_bg_pattern_0001",
                    "imagePath": "asset/cn-assets/startapp/custom_profile/bg/profile_bg_pattern_0001.png",
                }
            }
        },
    )
    assert renderer.resource_path(renderer.general_bgs[1]) == bg_path


def test_custom_profile_card_member_candidates_match_cloud_small_still_paths(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_small"
        / "res010_no034"
        / "card_after_training.png"
    )
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "smallAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_small/res010_no034/card_after_training.png"
                    ),
                }
            },
        },
    )

    candidates = [
        path.as_posix()
        for path in renderer.card_member_image_candidates({"id": 915, "type": 2, "useAfterSpecialTraining": True})
    ]

    assert any(path.endswith("/character/member_small/res010_no034/card_after_training.png") for path in candidates)
    assert not any(path.endswith("/character/member/res010_no034/card_after_training.png") for path in candidates)
    assert not any("/member_cutout/" in path for path in candidates)
    assert not any("/thumbnail/chara/" in path for path in candidates)


def test_custom_profile_card_member_clip_type_prefers_deck_cutout_path(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout"
        / "res010_no034"
        / "after_training.png"
    )
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member"
        / "res010_no034"
        / "card_after_training.png"
    )
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "afterTrainingPath": (
                        "asset/cn-assets/startapp/character/member/res010_no034/card_after_training.png"
                    ),
                    "deckAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_cutout/res010_no034/after_training.png"
                    ),
                }
            },
        },
    )

    candidates = [
        path.as_posix()
        for path in renderer.card_member_image_candidates({"id": 915, "type": 1, "useAfterSpecialTraining": True})
    ]

    assert candidates[0].endswith("/character/member_cutout/res010_no034/after_training.png")
    assert not candidates[0].endswith("/character/member/res010_no034/card_after_training.png")


def test_custom_profile_card_member_uses_saved_training_state(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "normal",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
        },
    )

    assert renderer.card_member_after_training({"id": 915, "useAfterSpecialTraining": True})


def test_custom_profile_card_member_full_type_prefers_small_still_path(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_small"
        / "res010_no034"
        / "card_after_training.png"
    )
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member"
        / "res010_no034"
        / "card_after_training.png"
    )
    renderer = _make_renderer(
        tmp_path,
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "afterTrainingPath": (
                        "asset/cn-assets/startapp/character/member/res010_no034/card_after_training.png"
                    ),
                    "smallAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_small/res010_no034/card_after_training.png"
                    ),
                }
            },
        },
    )

    candidates = [
        path.as_posix()
        for path in renderer.card_member_image_candidates({"id": 915, "type": 2, "useAfterSpecialTraining": True})
    ]

    assert candidates[0].endswith("/character/member_small/res010_no034/card_after_training.png")
    assert not any(path.endswith("/character/member/res010_no034/card_after_training.png") for path in candidates)


def test_custom_profile_leader_card_uses_small_still_path(tmp_path: Path) -> None:
    small_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_small"
        / "res010_no034"
        / "card_after_training.png"
    )
    full_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member"
        / "res010_no034"
        / "card_after_training.png"
    )
    _write_png_color(small_path, (940, 530), (0, 255, 0, 255))
    _write_png_color(full_path, (940, 530), (255, 0, 0, 255))
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetBundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetBundleName": "res010_no034",
                    "afterTrainingPath": full_path.as_posix(),
                    "smallAfterTrainingPath": small_path.as_posix(),
                }
            },
        },
    )

    image = renderer.compose_profile_leader_card(915)

    assert image is not None
    assert image.size == (940, 530)
    assert image.getpixel((470, 265))[:3] == (0, 255, 0)


def test_custom_profile_card_member_full_type_renders_small_still_frame(tmp_path: Path) -> None:
    small_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_small"
        / "res010_no034"
        / "card_after_training.png"
    )
    _write_png_color(small_path, (940, 530), (255, 0, 0, 255))
    _write_png_color(tmp_path / "static_images" / "customprofile" / "cardFrame_L_4.png", (940, 530), (0, 255, 0, 255))
    renderer = _make_renderer(
        tmp_path,
        resources={
            "cards": {915: {"id": 915, "assetBundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetBundleName": "res010_no034",
                    "smallAfterTrainingPath": small_path.as_posix(),
                }
            },
        },
    )

    rendered = renderer.render_card_member_content(
        {"id": 915, "type": 2, "useAfterSpecialTraining": True, "showMasterRank": True}
    )

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (940, 530)
    assert rendered[0].getpixel((10, 10))[:3] == (0, 255, 0)


def test_custom_profile_card_member_clip_type_renders_deck_card_frame(tmp_path: Path) -> None:
    clip_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout"
        / "res010_no034"
        / "after_training.png"
    )
    _write_png_color(clip_path, (328, 538), (255, 0, 0, 255))
    _write_png_color(tmp_path / "static_images" / "customprofile" / "cardFrame_M_4.png", (312, 512), (0, 255, 0, 255))
    _write_png_color(tmp_path / "static_images" / "customprofile" / "tex_mask_card_s.png", (174, 212), (0, 0, 0, 255))
    renderer = _make_renderer(
        tmp_path,
        profile_context={"userCards": [{"cardId": 915, "level": 60, "masterRank": 5}]},
        resources={
            "cards": {915: {"id": 915, "assetBundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetBundleName": "res010_no034",
                    "deckAfterTrainingPath": clip_path.as_posix(),
                }
            },
        },
    )

    rendered = renderer.render_card_member_content(
        {"id": 915, "type": 1, "useAfterSpecialTraining": True, "showMasterRank": True}
    )

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (328, 520)
    assert rendered[0].getpixel((10, 10))[:3] == (0, 255, 0)


def test_custom_profile_collection_prefers_image_asset_for_badges(tmp_path: Path) -> None:
    asset_path = (
        tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile" / "collection" / "collab001" / "badge.png"
    )
    _write_png(asset_path, (25, 25))
    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileCollectionResources": {
                801: {
                    "id": 801,
                    "customProfileResourceCollectionType": "can_badge",
                    "imagePath": "asset/cn-assets/startapp/custom_profile/collection/collab001/badge.png",
                }
            }
        },
    )

    rendered = renderer.render_collection_content({"id": 801})

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (25, 25)


def test_custom_profile_omikuji_collection_uses_target_master_row(tmp_path: Path) -> None:
    material_dir = tmp_path / "asset" / "jp-assets" / "startapp" / "lottery_game" / "new_year_2026_material"
    _write_png(material_dir / "bg_omikuji_MORE MORE JUMP.png", (1480, 490))
    _write_png(material_dir / "unsei_daikichi.png", (24, 80))
    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileCollectionResources": {
                1000: {
                    "id": 1000,
                    "customProfileResourceCollectionType": "omikuji",
                    "resourceLoadVal": "lottery_game/new_year_2026",
                    "fileName": "Prefabs/Omikuji",
                }
            },
            "omikujis": {
                183: {
                    "id": 183,
                    "unit": "idol",
                    "fortuneType": "grate_fortune",
                    "summary": "過去の悔恨が晴れる\n年になるでしょう\n迷いは捨て挑むべし",
                    "title1": "願望",
                    "description1": "必ず叶う",
                    "title2": "健康",
                    "description2": "大変良好",
                    "title3": "待人",
                    "description3": "自ら行くがよし",
                    "unitAssetbundleName": "lottery_game/new_year_2026_material",
                    "fortuneAssetbundleName": "lottery_game/new_year_2026_material",
                    "omikujiCoverAssetbundleName": "lottery_game/new_year_2026_material",
                    "unitFilePath": "bird_MORE MORE JUMP",
                    "fortuneFilePath": "unsei_daikichi",
                    "omikujiCoverFilePath": "omikuji_MORE MORE JUMP",
                }
            },
        },
        region="jp",
    )

    rendered = renderer.render_collection_content({"id": 1000, "targetId": 183})

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (1480, 490)
    assert rendered[0].getchannel("A").getbbox() is not None


def test_custom_profile_omikuji_collection_requires_material_assets(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileCollectionResources": {
                1000: {
                    "id": 1000,
                    "customProfileResourceCollectionType": "omikuji",
                    "resourceLoadVal": "lottery_game/new_year_2026",
                    "fileName": "Prefabs/Omikuji",
                }
            },
            "omikujis": {
                183: {
                    "id": 183,
                    "unit": "idol",
                    "fortuneType": "grate_fortune",
                    "summary": "過去の悔恨が晴れる",
                    "unitAssetbundleName": "lottery_game/new_year_2026_material",
                    "fortuneAssetbundleName": "lottery_game/new_year_2026_material",
                    "omikujiCoverAssetbundleName": "lottery_game/new_year_2026_material",
                    "unitFilePath": "bird_MORE MORE JUMP",
                    "fortuneFilePath": "unsei_daikichi",
                    "omikujiCoverFilePath": "omikuji_MORE MORE JUMP",
                }
            },
        },
        region="jp",
    )

    rendered = renderer.render_collection_content({"id": 1000, "targetId": 183})

    assert isinstance(rendered, NativeUnresolvedContent)
    assert rendered.reason == "omikuji collection needs material asset(s): background, fortune"


def test_custom_profile_omikuji_collection_requires_target_master_row(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileCollectionResources": {
                1000: {
                    "id": 1000,
                    "customProfileResourceCollectionType": "omikuji",
                }
            }
        },
        region="jp",
    )

    rendered = renderer.render_collection_content({"id": 1000, "targetId": 183})

    assert isinstance(rendered, NativeUnresolvedContent)
    assert rendered.reason == "omikuji collection needs the target omikujis.json row"


def test_custom_profile_music_clear_info_uses_profile_counts(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userMusicDifficultyClearCount": [
                {"musicDifficultyType": "easy", "liveClear": 1, "fullCombo": 2, "allPerfect": 3},
                {"musicDifficultyType": "master", "liveClear": 4, "fullCombo": 5, "allPerfect": 6},
            ],
        },
    )

    image = renderer.render_general_music_clear_info()

    assert image.size == (860, 318)
    assert renderer.music_clear_count_map()["master"]["fullCombo"] == 5
    assert renderer.music_clear_count_map()["master"]["allPerfect"] == 6
    assert _image_has_content_in_box(image, (344, 12, 516, 50))
    assert _image_has_content_in_box(image, (344, 174, 516, 212))
    assert not _image_has_content_in_box(image, (344, 224, 516, 238))


def test_custom_profile_music_clear_select_tab_info_draws_value_panel(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)

    image = renderer.render_general_music_clear_select_tab_info()

    assert image.size == (860, 166)
    assert _image_has_content_in_box(image, (32, 80, 828, 158))
    assert not _image_has_content_in_box(image, (32, 58, 828, 72))


def test_custom_profile_general_x_uses_twitter_id(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, profile_context={"userProfile": {"twitterId": "sekai_test"}})

    image = renderer.render_general_x()

    assert image.size == (548, 64)
    assert _image_has_content_in_box(image, (20, 12, 74, 52))
    assert _image_has_content_in_box(image, (95, 12, 420, 52))


def test_custom_profile_jp_general_labels_are_localized(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, region="jp")

    assert renderer.general_text("comment_title") == "ひと言"
    assert renderer.general_text("total_power") == "総合力"
    assert renderer.general_text("character_rank_tab") == "キャラクターランク"


def test_custom_profile_general_content_maps_jp_x(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={"userProfile": {"twitterId": "sekai_test"}},
        resources={"customProfilePlayerInfoResources": {1: {"id": 1, "fileName": "X"}}},
        region="jp",
    )

    rendered = renderer.render_general_content({"type": 1})

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (548, 64)


def test_custom_profile_chara_rank_icons_can_be_passed_by_cloud(tmp_path: Path) -> None:
    icon_path = tmp_path / "static_images" / "chara_icon" / "miku.png"
    _write_png(icon_path, (9, 4))
    (tmp_path / "static_images" / "card").mkdir(parents=True)
    renderer = _make_renderer(
        tmp_path,
        resources={"charaRankIconPathMap": {"21": "static_images/chara_icon/miku.png"}},
    )

    assert renderer.chara_rank_icon_path(21) == icon_path


def test_custom_profile_character_rank_component_keeps_challenge_stage_off_rank_tab(
    tmp_path: Path, monkeypatch
) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCharacters": [{"characterId": 21, "characterRank": 28}],
            "userChallengeLiveSoloStages": [
                {"characterId": 21, "rank": 1},
                {"characterId": 21, "rank": 7},
            ],
        },
    )
    calls = []

    def capture_cell(self, image, top_left, character_id, rank):
        calls.append((round(top_left[0], 1), round(top_left[1], 1), character_id, rank))
        image.alpha_composite(
            Image.new("RGBA", (8, 8), (255, 0, 0, 255)),
            (round(top_left[0]), round(top_left[1])),
        )

    monkeypatch.setattr(PNGRenderer, "draw_profile_rank_and_stage_cell", capture_cell)

    image = renderer.render_general_character_rank_and_challenge_stage(scroll=True)

    assert image.size == (908, 550)
    assert renderer.character_rank_map()[21] == 28
    assert renderer.challenge_live_stage_map()[21] == 7
    assert renderer.challenge_live_rank_for(21) == 7
    assert calls[0] == (15.5, -0.5, 21, 28)


def test_custom_profile_character_rank_scroll_masks_fifth_row_text(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)

    def draw_marker(self, image, top_left, character_id, rank):
        if character_id == 17:
            image.alpha_composite(
                Image.new("RGBA", (16, 16), (255, 0, 0, 255)),
                (100, 400),
            )
        if character_id == 21:
            image.alpha_composite(
                Image.new("RGBA", (16, 16), (0, 0, 255, 255)),
                (100, 500),
            )

    monkeypatch.setattr(PNGRenderer, "draw_profile_rank_and_stage_cell", draw_marker)

    image = renderer.render_general_character_rank_and_challenge_stage(scroll=True)

    assert image.size == (908, 550)
    assert _image_has_content_in_box(image, (120, 500, 140, 520))
    assert not _image_has_content_in_box(image, (120, 604, 140, 624))


def test_custom_profile_character_rank_full_size_is_bottom_aligned(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)

    image = renderer.render_general_character_rank_and_challenge_stage(scroll=False)

    assert image.size == (908, 813)
    assert _image_has_content_in_box(image, (100, 760, 830, 790))


def test_custom_profile_character_rank_value_text_matches_prefab_rect(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    calls = []

    def capture_text_rect(self, draw, rect, text, *, size, fill):
        calls.append((rect, text, size))

    monkeypatch.setattr(PNGRenderer, "draw_center_text_rect", capture_text_rect)

    image = Image.new("RGBA", (196, 85), (0, 0, 0, 0))
    renderer.draw_profile_rank_and_stage_cell(image, (0.0, 0.0), 21, 28)

    assert calls == [((59.0, 29.0, 191.0, 78.0), "28", 31)]


def test_custom_profile_chara_rank_icons_require_cloud_path(tmp_path: Path) -> None:
    icon_path = tmp_path / "static_images" / "chara_icon" / "miku.png"
    _write_png(icon_path, (9, 4))
    (tmp_path / "static_images" / "card").mkdir(parents=True)
    renderer = _make_renderer(tmp_path)

    assert renderer.chara_rank_icon_path(21) is None


def test_custom_profile_story_favorite_uses_cloud_resources(tmp_path: Path) -> None:
    banner_path = tmp_path / "asset" / "cn-assets" / "startapp" / "event_story" / "event_test" / "screen_image"
    _write_png(banner_path / "banner_event_story.png", (128, 64))
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userStoryFavorites": [
                {"shareNo": 2, "storyType": "event_story", "storyId": 20},
                {"shareNo": 1, "storyType": "event_story", "storyId": 10},
            ]
        },
        resources={
            "storyFavoriteResources": {
                "event_story:10": {
                    "title": "First",
                    "imagePath": "asset/cn-assets/startapp/event_story/event_test/screen_image/banner_event_story.png",
                }
            }
        },
    )

    image = renderer.render_general_story_favorite()

    assert image is not None
    assert image.size == (909, 813)
    assert renderer.ordered_story_favorites(renderer.profile_context["userStoryFavorites"])[0]["storyId"] == 10


def test_custom_profile_story_favorite_requires_cloud_image_path(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        resources={
            "storyFavoriteResources": {
                "event_story:10": {
                    "title": "First",
                    "bannerPath": "asset/cn-assets/startapp/event_story/event_test/screen_image/banner_event_story.png",
                }
            }
        },
    )

    assert renderer.story_favorite_image_path({"storyType": "event_story", "storyId": 10}) is None


def test_custom_profile_render_request_decodes_resources() -> None:
    card, context, resources = decode_custom_profile_render_request(
        {
            "card": {"seq": 1},
            "profile_context": {"user": {"userId": 1}},
            "resources": {"storyFavoriteResources": {"event_story:10": {"imagePath": "asset/path.png"}}},
        }
    )

    assert card["seq"] == 1
    assert context["user"]["userId"] == 1
    assert resources["storyFavoriteResources"]["event_story:10"]["imagePath"] == "asset/path.png"


def test_custom_profile_honor_transform_keeps_native_canvas(tmp_path: Path) -> None:
    layer = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    layer.putpixel((10, 10), (255, 0, 0, 255))
    (tmp_path / "fonts").mkdir()
    (tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile").mkdir(parents=True)
    renderer = PNGRenderer(
        masterdata=None,
        assets=tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile",
        fonts=tmp_path / "fonts",
        resources={},
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=None,
        profile_context={},
        region="cn",
        position_scale=1.0,
        clip_canvas_transform=False,
    )

    prepared = renderer.prepare_transformed_layer(
        (layer, (10, 10)),
        {"position": {"x": 0, "y": 0}, "rotation": {"z": 0}, "scale": {"x": 1, "y": 1}},
        "bonds_honor",
    )

    assert prepared is not None
    assert prepared.image.size == (20, 20)


def test_custom_profile_general_deck_card_uses_deck_cutout_art(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout"
        / "res010_no034"
        / "after_training.png",
        (330, 512),
    )
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout_trm"
        / "res010_no034"
        / "after_training.png",
        (330, 512),
    )
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                    "level": 60,
                    "masterRank": 5,
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "smallAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_small/res010_no034/card_after_training.png"
                    ),
                    "deckAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_cutout/res010_no034/after_training.png"
                    ),
                    "clipAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_cutout_trm/res010_no034/after_training.png"
                    ),
                }
            },
        },
    )

    assert (
        renderer.card_image_path_for_state(915, True, "deck")
        .as_posix()
        .endswith("/character/member_cutout/res010_no034/after_training.png")
    )
    image = renderer.compose_profile_deck_card(915)
    assert image is not None
    assert image.size == (156, 242)
    assert image.getpixel((4, 4))[3] == 255


def test_custom_profile_general_deck_card_does_not_apply_slanted_mask(tmp_path: Path) -> None:
    deck_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout"
        / "res010_no034"
        / "after_training.png"
    )
    _write_png_color(deck_path, (330, 512), (255, 0, 0, 255))
    mask = Image.new("RGBA", (330, 512), (0, 0, 0, 255))
    for y in range(mask.height):
        for x in range(80):
            mask.putpixel((x, y), (0, 0, 0, 0))
    mask_path = tmp_path / "static_images" / "customprofile" / "tex_mask_card_s.png"
    mask_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(mask_path)
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "level": 60,
                    "masterRank": 0,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "deckAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_cutout/res010_no034/after_training.png"
                    ),
                }
            },
        },
    )

    image = renderer.compose_profile_deck_card(915)

    assert image is not None
    assert image.getpixel((4, 4))[3] == 255


def test_custom_profile_card_master_rank_zero_is_not_drawn(tmp_path: Path) -> None:
    _write_png_color(tmp_path / "static_images" / "card" / "train_rank_0.png", (88, 88), (0, 255, 0, 255))
    renderer = _make_renderer(
        tmp_path,
        profile_context={"userCards": [{"cardId": 915, "level": 60, "masterRank": 0}]},
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
        },
    )
    image = Image.new("RGBA", (330, 512), (255, 0, 0, 255))

    renderer.draw_deck_card_view_overlays(image, 915)

    assert image.getpixel((250, 8))[:3] == (255, 0, 0)


def test_custom_profile_unity_sprite_reuses_static_card_assets(tmp_path: Path) -> None:
    _write_png(tmp_path / "static_images" / "card" / "train_rank_0.png", (7, 6))
    _write_png(tmp_path / "static_images" / "card" / "attr_icon_cute.png", (8, 8))
    _write_png(tmp_path / "static_images" / "card" / "rare_star_after_training.png", (9, 7))
    _write_png(tmp_path / "static_images" / "card" / "frame_rarity_4.png", (10, 10))

    renderer = _make_renderer(tmp_path)

    assert renderer.unity_ui_sprite("masterRank_L_0").size == (7, 6)
    assert renderer.unity_ui_sprite("icon_attribute_cute_64").size == (8, 8)
    assert renderer.unity_ui_sprite("rarity_star_afterTraining").size == (9, 7)
    assert renderer.unity_ui_sprite("cardFrame_S_4").size == (10, 10)


def test_custom_profile_unity_sprite_loads_customprofile_static_assets(tmp_path: Path) -> None:
    _write_png(tmp_path / "static_images" / "customprofile" / "label_mark_leader_L_pk.png", (11, 5))

    renderer = _make_renderer(tmp_path)

    assert renderer.unity_ui_sprite("label_mark_leader_L_pk").size == (11, 5)


def test_custom_profile_region_path_expands_region_placeholder(tmp_path: Path) -> None:
    target = tmp_path / "asset" / "jp-assets" / "startapp" / "custom_profile"
    target.mkdir(parents=True)

    assert (
        _require_region_path(
            "custom_profile_assets_dir",
            tmp_path / "asset" / "{region}-assets" / "startapp" / "custom_profile",
            "jp",
        )
        == target
    )


def test_custom_profile_region_path_replaces_literal_region_segment(tmp_path: Path) -> None:
    target = tmp_path / "fonts" / "jp"
    target.mkdir(parents=True)

    assert _require_region_path("custom_profile_fonts_dir", tmp_path / "fonts" / "cn", "jp") == target
    assert _region_path_candidates(tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile", "jp")[0] == (
        tmp_path / "asset" / "jp-assets" / "startapp" / "custom_profile"
    )


def test_custom_profile_tmp_font_metadata_is_optional(tmp_path: Path) -> None:
    path = tmp_path / "custom_profile" / "tmp-font-assets" / "{region}" / "metadata.json"

    assert _optional_region_file("custom_profile_tmp_font_metadata", path, "cn") is None


def test_custom_profile_api_uses_cropped_profile_viewport(tmp_path: Path, monkeypatch) -> None:
    assets = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile"
    fonts = tmp_path / "fonts" / "cn"
    shape_sprites = tmp_path / "shape-sprites"
    ui_sprites = tmp_path / "unity-ui-sprites"
    for path in (assets, fonts, shape_sprites, ui_sprites):
        path.mkdir(parents=True)
    captured: dict[str, object] = {}

    class FakePNGRenderer:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def render_card(self, card: dict) -> Image.Image:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    monkeypatch.setattr(
        custom_profile_drawer,
        "CUSTOM_PROFILE_ASSETS_DIR",
        tmp_path / "asset" / "{region}-assets" / "startapp" / "custom_profile",
    )
    monkeypatch.setattr(custom_profile_drawer, "CUSTOM_PROFILE_FONTS_DIR", tmp_path / "fonts" / "{region}")
    monkeypatch.setattr(custom_profile_drawer, "CUSTOM_PROFILE_SHAPE_SPRITE_DIR", shape_sprites)
    monkeypatch.setattr(custom_profile_drawer, "CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR", ui_sprites)
    monkeypatch.setattr(custom_profile_drawer, "CUSTOM_PROFILE_TMP_FONT_METADATA", None)
    monkeypatch.setattr(custom_profile_drawer, "PNGRenderer", FakePNGRenderer)

    image = custom_profile_drawer._render_custom_profile_card_sync({}, {}, {}, "cn")

    assert image.size == (1, 1)
    assert captured["canvas_w"] == 2048
    assert captured["canvas_h"] == 909
    assert captured["origin_x"] == 1024.0
    assert captured["origin_y"] == 454.5
