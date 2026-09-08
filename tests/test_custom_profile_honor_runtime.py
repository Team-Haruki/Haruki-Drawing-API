from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from src.sekai.profile.custom_profile import renderer as renderer_mod
from src.sekai.profile.custom_profile.renderer import NativeUnresolvedContent, PNGRenderer


def _renderer(tmp_path: Path, profile_context: dict | None = None) -> PNGRenderer:
    fonts = tmp_path / "fonts"
    assets = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile"
    fonts.mkdir()
    assets.mkdir(parents=True)
    return PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=fonts,
        resources={},
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=None,
        profile_context=profile_context or {},
        region="cn",
    )


def test_honor_path_resolution_covers_event_rank_birthday_and_frames(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    captured: list[list[Path]] = []

    def first(rels):
        values = list(rels)
        captured.append(values)
        return tmp_path / values[-1] if values else None

    monkeypatch.setattr(renderer, "first_region_asset", first)
    assert renderer.honor_background_path("rank_match", "bg", "asset", "main").name == "degree_main.png"
    assert any(path.parts[:2] == ("rank_live", "honor") for path in captured[-1])
    assert renderer.honor_background_path("event", "bg", "honor_top_event_name", "sub") is not None
    assert any("honor_bg_name" in path.parts for path in captured[-1])
    assert renderer.honor_background_path("normal", "", "", "main") is None

    assert renderer.derive_honor_background_asset_name(" honor_top_event_name ") == "honor_bg_name"
    assert renderer.derive_honor_background_asset_name("short") == ""
    assert renderer.is_world_link_honor_group("world_link", "", "") is True
    assert renderer.is_world_link_honor_group("event", "event_wl_1", "") is True
    assert renderer.is_world_link_honor_group("event", "", "normal") is False
    assert renderer._resolved_honor_group_type({"honorType": "world_link"}, "", "") == "wl_event"
    assert renderer.honor_type_for_group({"honorType": "birthday"}, "", "") == "birthday"
    assert renderer.honor_type_for_group({"frameName": "honor_frame_birthday_x"}, "", "") == "birthday"
    assert renderer.honor_type_for_group({}, "honor_bg_birthday_x", "") == "birthday"
    assert renderer.honor_type_for_group({}, "", "normal") == "normal"

    honor_path = tmp_path / "same.png"
    monkeypatch.setattr(renderer, "first_region_asset", lambda _rels: honor_path)
    assert renderer.honor_rank_path("event", "asset", "main", honor_path) is None
    other = tmp_path / "other.png"
    monkeypatch.setattr(renderer, "first_region_asset", lambda _rels: other)
    assert renderer.honor_rank_path("rank_match", "asset", "sub", honor_path) == other
    assert renderer.honor_rank_path("normal", "asset", "main", honor_path) is None

    assert renderer._resolved_honor_frame_name("kept", "normal", "", "") == "kept"
    assert renderer._resolved_honor_frame_name("", "birthday", "honor_bg_birthday_miku", "") == (
        "honor_frame_birthday_miku"
    )
    assert renderer._resolved_honor_frame_name("", "birthday", "", "honor_bg_birthday_rin") == (
        "honor_frame_birthday_rin"
    )
    assert renderer._resolved_honor_frame_name("", "birthday", "", "") == ""

    assert renderer._eligible_honor_frame_path("", "normal", "m", 4) is None
    assert renderer._eligible_honor_frame_path("event_frame", "normal", "m", 2) is None
    assert renderer._eligible_honor_frame_path("frame", "normal", "m", 2) == other
    assert renderer.honor_frame_degree_level_path({}, "normal", 2) is None
    assert renderer.honor_frame_degree_level_path({}, "birthday", 2) is None
    assert renderer.honor_frame_degree_level_path({"frameName": "birthday"}, "birthday", 2) == other

    assert renderer.honor_frame_path({"honorType": "birthday"}, "", "", "main", 1) is None
    assert renderer.honor_frame_path({"frameName": "frame"}, "", "", "main", 2) == other
    monkeypatch.setattr(renderer, "_eligible_honor_frame_path", lambda *_args: None)
    static_frame = renderer.static_images / "honor" / "frame_degree_m_2.png"
    static_frame.parent.mkdir(parents=True)
    static_frame.write_bytes(b"frame")
    assert renderer.honor_frame_path({}, "", "", "main", 2) == static_frame


def test_honor_candidates_positions_levels_and_icons(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    monkeypatch.setattr(renderer, "region_asset_candidate_paths", lambda rels: [tmp_path / rel for rel in rels])
    monkeypatch.setattr(renderer, "first_region_asset", lambda rels: tmp_path / next(iter(rels)) if rels else None)
    monkeypatch.setattr(renderer, "static_image_path", lambda *parts: tmp_path.joinpath(*parts))

    assert renderer._honor_scroll_path("") is None
    assert renderer._honor_scroll_path("asset").name == "scroll.png"
    assert renderer._honor_level_icon_paths("normal", "event") == (None, None)
    assert all(renderer._honor_level_icon_paths("fc_ap", "event"))
    assert all(renderer._honor_level_icon_paths("normal", "character"))

    renderer.honor_groups = {3: {"id": 3}}
    assert renderer.honor_group_for({"groupId": 3}) == {"id": 3}
    assert renderer.honor_group_for({"groupId": 9}) is None

    honor = {
        "assetbundleName": "honor_top_event_test",
        "honorRarity": "highest",
        "levels": [
            "bad",
            {"level": 1},
            {"level": 2, "assetbundleName": "two"},
            {"level": 4, "honorRarity": "high"},
        ],
    }
    assert renderer.resolve_honor_level_visual({}, 2) is None
    assert renderer.resolve_honor_level_visual(honor, 4)["level"] == 4
    assert renderer.resolve_honor_level_visual(honor, 3)["level"] == 2
    assert renderer.resolve_honor_level_visual(honor, 0)["level"] == 2

    group = {"backgroundAssetbundleName": "bg", "honorType": "rank_match", "frameName": "event_frame"}
    candidates = renderer.honor_candidate_paths(honor, group, True)
    assert candidates
    assert any("rank_live" in path.parts for path in candidates)
    assert any("honor_bg_test" in path.parts for path in candidates)
    assert renderer.honor_candidate_paths(None, group, True) == []

    base = Image.new("RGBA", (300, 100))
    assert renderer.honor_rank_position(base, Image.new("RGBA", (295, 95)), True, "event", tmp_path / "rank.png") == (
        0,
        0,
    )
    small = Image.new("RGBA", (20, 20))
    assert renderer.honor_rank_position(base, small, True, "rank_match", tmp_path / "rank.png") == (190, 0)
    assert renderer.honor_rank_position(base, small, False, "rank_match", tmp_path / "rank.png") == (17, 42)
    event_path = tmp_path / "honor_top_event_x" / "rank.png"
    assert renderer.honor_rank_position(base, small, False, "event", event_path) == (0, 0)
    assert renderer.honor_rank_position(base, small, False, "normal", tmp_path / "rank.png") == (34, 42)


def test_bonds_honor_keys_configured_images_and_content_fallback(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    monkeypatch.setattr(renderer, "bonds_honor_slot_key", lambda *args: ":".join(map(str, args)))
    keys = renderer._bonds_honor_request_keys(1, 2, True, 3, False, True)
    assert len(keys) == 2
    assert len(renderer._bonds_honor_request_keys(1, 2, True, 3, False, False)) == 1

    image = Image.new("RGBA", (20, 10), "red")
    renderer.bonds_honor_requests = {keys[1]: "configured", "1": "id-fallback"}
    monkeypatch.setattr(renderer, "honor_request_image", lambda value: image if value == "configured" else None)
    assert renderer._configured_bonds_honor_image(1, keys) is image
    monkeypatch.setattr(renderer, "honor_request_image", lambda value: image if value == "id-fallback" else None)
    assert renderer._configured_bonds_honor_image(1, ["missing"]) is image

    monkeypatch.setattr(renderer, "compose_bonds_honor_image", lambda *_args: image)
    assert renderer.render_bonds_honor_content({"id": 1}) == (image, (10.0, 5.0))
    monkeypatch.setattr(renderer, "compose_bonds_honor_image", lambda *_args: None)
    unresolved = renderer.render_bonds_honor_content({"id": 1})
    assert isinstance(unresolved, NativeUnresolvedContent)


def test_compose_bonds_honor_uses_configured_masterdata_and_loaded_assets(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    item = {"id": 7, "wordId": 3, "inverse": True, "useUnitVirtualSinger": True}
    image = Image.new("RGBA", (12, 6), "blue")
    monkeypatch.setattr(renderer, "user_bonds_honor_level_for", lambda _id: 2)
    monkeypatch.setattr(renderer, "_bonds_honor_request_keys", lambda *_args: ["key"])
    monkeypatch.setattr(renderer, "_configured_bonds_honor_image", lambda *_args: image)
    assert renderer.compose_bonds_honor_image(item, True) is image

    monkeypatch.setattr(renderer, "_configured_bonds_honor_image", lambda *_args: None)
    monkeypatch.setattr(renderer, "build_masterdata_bonds_honor_request", lambda *_args: None)
    assert renderer.compose_bonds_honor_image(item, False) is None

    request = SimpleNamespace(
        bonds_bg_path="bg.png",
        bonds_bg_path2=None,
        chara_icon_path=None,
        chara_icon_path2=None,
        mask_img_path=None,
        frame_img_path=None,
        word_img_path=None,
        lv_img_path=None,
        lv6_img_path=None,
    )
    monkeypatch.setattr(renderer, "build_masterdata_bonds_honor_request", lambda *_args: request)
    monkeypatch.setattr(renderer, "open_rgba", lambda path: image if path.name == "bg.png" else None)
    monkeypatch.setattr(renderer_mod, "compose_full_honor_image_from_loaded_assets", lambda req, images: image)
    assert renderer.compose_bonds_honor_image(item, False) is image
    assert renderer._loaded_request_images(request, {"bg": "bonds_bg_path", "other": "bonds_bg_path2"}) == {
        "bg": image,
        "other": None,
    }


def test_build_masterdata_bonds_honor_request_and_virtual_singer_slots(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    item = {"id": 101, "wordId": 102, "inverse": True, "useUnitVirtualSinger": True}
    assert renderer.build_masterdata_bonds_honor_request(item, True) is None

    renderer.masterdata = object()
    renderer.bonds_honors = {}
    assert renderer.build_masterdata_bonds_honor_request(item, True) is None

    honor = {
        "id": 101,
        "honorRarity": "high",
        "gameCharacterUnitId1": 10,
        "gameCharacterUnitId2": 20,
        "configurableUnitVirtualSinger": True,
    }
    renderer.bonds_honors = {101: honor}
    renderer.game_character_units = {10: {"gameCharacterId": 0}, 20: {"gameCharacterId": 2}}
    assert renderer.build_masterdata_bonds_honor_request(item, True) is None

    renderer.game_character_units = {
        10: {"gameCharacterId": 21, "unit": "piapro"},
        20: {"gameCharacterId": 2, "unit": "leo_need"},
        30: {"gameCharacterId": 21, "unit": "leo_need"},
    }
    renderer.bonds_honor_words = {102: {"assetbundleName": "word"}}
    monkeypatch.setattr(renderer, "first_region_asset", lambda rels: tmp_path / next(iter(rels)))
    monkeypatch.setattr(renderer, "static_image_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(renderer, "honor_request_path", lambda path: str(path) if path is not None else None)
    monkeypatch.setattr(renderer, "user_bonds_honor_level_for", lambda _id: 4)
    request = renderer.build_masterdata_bonds_honor_request(item, True)
    assert request is not None
    assert request.honor_level == 4
    assert request.chara_id == "20"
    assert request.chara_id2 == "30"
    assert request.word_img_path is not None

    request_sub = renderer.build_masterdata_bonds_honor_request({**item, "inverse": False}, False)
    assert request_sub is not None
    assert request_sub.word_img_path is None

    assert renderer.game_character_id_for_unit(999) == 0
    assert renderer.game_character_id_for_unit(30) == 21
    assert renderer.unit_virtual_singer_unit_id(999, 20) == 999
    assert renderer.unit_virtual_singer_unit_id(20, 10) == 20
    assert renderer.unit_virtual_singer_unit_id(10, 20) == 30


@pytest.mark.parametrize(
    ("honor_id", "word_id", "words", "expected"),
    [
        (101, 201, {201: {"assetbundleName": "bundle"}}, "bundle_01"),
        (101, 102, {}, "honorname_0102_02_01"),
        (101, 211, {}, "honorname_0102_default_0102_01"),
        (101, 212, {}, "honorname_0102_default_0201_01"),
    ],
)
def test_bonds_honor_word_bundle_fallbacks(tmp_path: Path, honor_id, word_id, words, expected):
    renderer = _renderer(tmp_path)
    renderer.bonds_honor_words = words
    assert renderer.bonds_honor_word_bundle_name({"id": honor_id}, word_id, 1, 2) == expected


def test_profile_lookup_and_generated_honor_data_cover_list_dict_and_fallback_rows(tmp_path: Path, monkeypatch):
    profile = {
        "userCards": [{"cardId": 2, "level": 30}],
        "userHonors": ["bad", [3, 4], {"honorId": 5, "honorLevel": 6}],
        "userProfileHonors": [{"honorId": 7, "honorLevel": 8}],
        "userBondsHonors": [[9, 10], {"bondsHonorId": 11, "bondsHonorLevel": 12}],
        "userHonorMissions": [{"honorId": 5, "missionProgress": 13}],
    }
    renderer = _renderer(tmp_path, profile)
    assert renderer.user_card_for(2)["level"] == 30
    assert renderer.user_card_for(99) is None
    assert renderer.user_honor_level_for(3) == 4
    assert renderer.user_honor_level_for(5) == 6
    assert renderer.user_honor_level_for(7) == 8
    assert renderer.user_honor_level_for(99) == 0
    assert renderer.user_bonds_honor_level_for(9) == 10
    assert renderer.user_bonds_honor_level_for(11) == 12
    assert renderer.user_bonds_honor_level_for(99) == 0
    assert renderer.user_honor_mission_progress_for(5) == 13
    assert renderer.user_honor_mission_progress_for(99) == 0

    assert renderer._list_profile_level([], 1) is None
    assert renderer._list_profile_level([1], 1) == 0
    assert renderer._user_honor_row_level("bad", 1) is None
    assert renderer._user_honor_row_level({"id": 1, "level": 2}, 1) == 2
    assert renderer._user_honor_row_level({"id": 2}, 1) is None
    assert renderer._profile_honor_row_level("bad", 1) is None
    assert renderer._bonds_honor_row_level("bad", 1) is None
    assert renderer._bonds_honor_row_level({"honorId": 1, "level": 2}, 1) == 2
    assert renderer._honor_mission_row_progress("bad", 1) is None

    renderer.honors = {5: {"id": 5, "groupId": 2, "name": "honor"}}
    renderer.honor_groups = {2: {"id": 2, "name": "group"}}
    monkeypatch.setattr(renderer, "honor_candidate_paths", lambda *_args: [tmp_path / "candidate.png"])
    generated = renderer.generate_honor_data({"id": 5, "fullSize": True})
    assert generated["level"] == 6
    assert generated["missionProgress"] == 13
    assert generated["candidatePaths"] == [str(tmp_path / "candidate.png")]
    assert renderer.generate_honor_data({"id": 99})["candidatePaths"] == []
    assert (
        renderer.generate_bonds_honor_data(
            {"id": 9, "fullSize": True, "wordId": 2, "inverse": True, "useUnitVirtualSinger": True}
        )["level"]
        == 10
    )
    assert renderer.generate_collection_data({"id": 1, "targetId": 3}) == {"id": 1, "targetId": 3}
