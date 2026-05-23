from pathlib import Path

from PIL import Image

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
