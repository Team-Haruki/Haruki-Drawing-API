from pathlib import Path

from PIL import Image

from src.sekai.profile.custom_profile import drawer as custom_profile_drawer
from src.sekai.profile.custom_profile.drawer import _optional_region_file, _require_region_path
from src.sekai.profile.custom_profile.renderer import PNGRenderer


def _write_png(path: Path, size: tuple[int, int] = (3, 2)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (255, 0, 0, 255)).save(path)


def _make_renderer(
    tmp_path: Path,
    *,
    profile_context: dict | None = None,
    resources: dict | None = None,
) -> PNGRenderer:
    fonts = tmp_path / "fonts"
    assets = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile"
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
        region="cn",
    )


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


def test_custom_profile_card_member_candidates_match_cloud_member_paths(tmp_path: Path) -> None:
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
                }
            },
        },
    )

    candidates = [
        path.as_posix() for path in renderer.card_member_image_candidates({"id": 915, "useAfterSpecialTraining": True})
    ]

    assert any(path.endswith("/character/member/res010_no034/card_after_training.png") for path in candidates)
    assert not any("/member_cutout/" in path for path in candidates)
    assert not any("/thumbnail/chara/" in path for path in candidates)


def test_custom_profile_card_member_clip_type_uses_cloud_cutout_path(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout_trm"
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
                        "asset/cn-assets/startapp/character/member_cutout_trm/res010_no034/after_training.png"
                    ),
                }
            },
        },
    )

    candidates = [
        path.as_posix()
        for path in renderer.card_member_image_candidates({"id": 915, "type": 1, "useAfterSpecialTraining": True})
    ]

    assert candidates[0].endswith("/character/member_cutout_trm/res010_no034/after_training.png")
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
    assert not candidates[0].endswith("/character/member/res010_no034/card_after_training.png")


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

    assert image.size == (752, 250)
    assert renderer.music_clear_count_map()["master"]["fullCombo"] == 5


def test_custom_profile_chara_rank_icons_can_be_passed_by_cloud(tmp_path: Path) -> None:
    icon_path = tmp_path / "static_images" / "chara_rank_icon" / "miku.png"
    _write_png(icon_path, (9, 4))
    renderer = _make_renderer(
        tmp_path,
        resources={"charaRankIconPathMap": {"21": "static_images/chara_rank_icon/miku.png"}},
    )

    assert renderer.chara_rank_icon_path(21) == icon_path


def test_custom_profile_general_deck_card_uses_still_art(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_small"
        / "res010_no034"
        / "card_after_training.png",
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
                        "asset/cn-assets/startapp/character/member_cutout_trm/res010_no034/after_training.png"
                    ),
                }
            },
        },
    )

    assert (
        renderer.card_image_path_for_state(915, True, "small")
        .as_posix()
        .endswith("/character/member_small/res010_no034/card_after_training.png")
    )
    assert renderer.compose_profile_deck_card(915) is not None


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
