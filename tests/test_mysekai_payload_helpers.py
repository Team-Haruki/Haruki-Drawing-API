from __future__ import annotations

from pathlib import Path

from scripts.parity_payloads import gen_mysekai as gen


class _FakeAssets:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.candidates: set[str] = set()

    @staticmethod
    def region_asset(*paths: str) -> str:
        return f"asset/{paths[0]}"

    @staticmethod
    def static(path: str) -> str:
        return f"static/{path}"


def test_merge_helpers_preserve_suite_data_and_apply_delta() -> None:
    base = {
        "userProfile": {"name": "suite"},
        "userMysekaiFixtures": ["existing"],
        "userMysekaiMaterials": ["old"],
    }
    mysekai = {
        "updatedResources": {
            "userProfile": {"name": "delta"},
            "userMysekaiFixtures": [],
            "userMysekaiMaterials": ["new"],
        },
        "userMysekaiFixtures": ["fallback"],
        "userMysekaiCharacters": ["character"],
        "unrelated": ["ignored"],
        "now": 123,
    }

    updated = gen._merge_updated_resources(base, mysekai)
    gen._merge_mysekai_fields(base, mysekai, updated)

    assert base["userProfile"] == {"name": "suite"}
    assert base["userMysekaiFixtures"] == ["existing"]
    assert base["userMysekaiMaterials"] == ["new"]
    assert base["userMysekaiCharacters"] == ["character"]
    assert base["now"] == 123
    assert "unrelated" not in base


def test_visit_characters_deduplicates_units_and_caps_at_six(monkeypatch, tmp_path: Path) -> None:
    groups = {index: {"gameCharacterUnitId1": index, "gameCharacterUnitId2": 0} for index in range(1, 8)}
    units = {index: {"gameCharacterId": index + 20} for index in range(1, 8)}
    monkeypatch.setattr(gen, "ASSETS", _FakeAssets(tmp_path))
    monkeypatch.setattr(
        gen,
        "_md_map",
        lambda name: groups if name == "mysekaiGameCharacterUnitGroups" else units,
    )
    rows = [{"mysekaiGameCharacterUnitGroupId": index, "isReservation": index == 1} for index in range(1, 8)]
    rows.insert(1, rows[0])

    result = gen._visit_characters({"userMysekaiGateCharacterVisit": {"userMysekaiGateCharacters": rows}})

    assert len(result) == 6
    assert result[0]["is_reservation"] is True
    assert result[0]["reservation_icon_path"] == "static/mysekai/invitationcard.png"
    assert result[0]["memoria_image_path"].endswith("item_memoria_21.png")


def test_site_resource_helpers_count_and_render_available_resources(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gen, "ASSETS", _FakeAssets(tmp_path))
    maps = [
        {
            "mysekaiSiteId": 5,
            "userMysekaiSiteHarvestResourceDrops": [
                {"resourceType": "material", "resourceId": 1, "status": "before_drop", "quantity": 2},
                {"resourceType": "material", "resourceId": 1, "status": "before_drop", "quantity": 3},
                {"resourceType": "material", "resourceId": 2, "status": "after_drop", "quantity": 9},
            ],
        }
    ]
    counts = gen._resource_counts_by_site(maps)
    monkeypatch.setattr(gen, "_resource_image_path", lambda key: (f"asset/{key}.png", key.endswith("_1")))
    monkeypatch.setattr(gen, "_resource_text_color", lambda _key: [1, 2, 3])

    entries = gen._site_resource_entries(counts[5])

    assert counts[5] == {"material_1": 5}
    assert entries == [
        {
            "image_path": "asset/material_1.png",
            "number": 5,
            "text_color": [1, 2, 3],
            "has_music_record": True,
            "music_record_icon_path": "static/mysekai/music_record.png",
        }
    ]


def test_birthday_refresh_candidates_prefer_current_then_latest_past(tmp_path: Path) -> None:
    for directory in ("miku_2024", "miku_2025", "miku_2027", "miku_invalid"):
        path = tmp_path / directory
        path.mkdir()
        (path / "icon_refresh.png").touch()

    candidates = gen._birthday_refresh_candidates(tmp_path, "miku")

    assert gen._select_birthday_refresh(candidates, 2025) == "miku_2025"
    assert gen._select_birthday_refresh(candidates, 2026) == "miku_2025"
    assert gen._select_birthday_refresh(candidates, 2023) == "miku_2024"


def test_map_harvest_points_builds_birthday_metadata_and_skips_tone_gust(monkeypatch, tmp_path: Path) -> None:
    fixtures = {
        1: {
            "mysekaiSiteHarvestFixtureRarityType": "rarity_1",
            "assetbundleName": "birthday_tree",
            "mysekaiSiteHarvestFixtureType": "birthday_plant",
        },
        2: {
            "mysekaiSiteHarvestFixtureRarityType": "rarity_1",
            "assetbundleName": "tone_gust",
            "mysekaiSiteHarvestFixtureType": "tone_gust",
        },
    }
    monkeypatch.setattr(gen, "ASSETS", _FakeAssets(tmp_path))
    monkeypatch.setattr(gen, "_md_map", lambda name: fixtures if name == "mysekaiSiteHarvestFixtures" else {39: {}})
    monkeypatch.setattr(gen, "_birthday_refresh_icon_path", lambda _row: "asset/birthday.png")
    site_map = {
        "userMysekaiSiteHarvestFixtures": [
            {"mysekaiSiteHarvestFixtureId": 1, "positionX": 1.0, "positionZ": 2.0},
            {"mysekaiSiteHarvestFixtureId": 2, "positionX": 3.0, "positionZ": 4.0},
        ]
    }

    result = gen._map_harvest_points(site_map, {gen._pos_key(1.0, 2.0): 39})

    assert len(result) == 1
    assert result[0]["image_path"] == "asset/birthday.png"
    assert result[0]["fallback_image_path"].endswith("mdl_site_wood_common_fieldtree01.png")
    assert result[0]["size"] == 50
    assert result[0]["offset_x"] == 7.5
