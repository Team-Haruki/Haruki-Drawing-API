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


def test_gate_material_helpers_keep_first_gate_on_equal_level_and_accumulate_cost(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gen, "ASSETS", _FakeAssets(tmp_path))
    gate_materials = {1: [[{"material_id": 10, "quantity": 2}], [{"material_id": 10, "quantity": 3}]], 2: [[]]}

    selected = gen._selected_gate_materials(gate_materials, {1: 3, 2: 3})
    levels = gen._gate_level_materials(gate_materials[1], 0, {10: 4}, {10: "material_ten"})

    assert selected == {1: gate_materials[1]}
    assert levels[0]["items"][0]["sum_quantity"] == "4/2"
    assert levels[0]["color"] == [50, 50, 50]
    assert levels[1]["items"][0]["sum_quantity"] == "4/5"
    assert levels[1]["color"] == [200, 0, 0]


def test_music_record_category_counts_missing_asset_and_sorts_obtained_first(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gen, "ASSETS", _FakeAssets(tmp_path))
    category, total, obtained = gen._music_record_category(
        "street",
        [2, 1],
        {1: 10},
        {1},
        {1: {"assetbundleName": "one"}, 2: {"assetbundleName": ""}},
        {"street": "street.png"},
    )

    assert total == 2
    assert obtained == 1
    assert category is not None
    assert category["progress_message"] == "1/2 (50.0%)"
    assert category["musicrecords"] == [{"id": 1, "image_path": "asset/music/jacket/one/one.png", "obtained": True}]


def test_fixture_detail_request_adds_optional_blueprint_data(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gen, "ASSETS", _FakeAssets(tmp_path))
    monkeypatch.setattr(gen, "_fixture_color_images", lambda _fixture: ["image"])
    monkeypatch.setattr(gen, "_fixture_basic_info", lambda _fixture: ["basic"])
    monkeypatch.setattr(gen, "_fixture_tags", lambda _fixture: ["tag"])
    monkeypatch.setattr(gen, "_reaction_character_groups", lambda _fixture_id: [{"number": 1}])
    monkeypatch.setattr(gen, "_material_cost_list", lambda rows: [{"rows": len(rows)}] if rows else [])
    monkeypatch.setattr(gen, "_find_fixture_blueprint", lambda _fixture_id: {"id": 9, "isEnableSketch": True})
    monkeypatch.setattr(gen, "_fixture_blueprint_info", lambda _blueprint: ["blueprint"])
    fixture = {
        "name": "Chair",
        "mysekaiFixtureMainGenreId": 1,
        "mysekaiFixtureSubGenreId": 2,
        "gridSize": {"width": 1, "depth": 2, "height": 3},
    }

    request, supports_sketch = gen._fixture_detail_request(
        1,
        fixture,
        {1: {"name": "Main", "assetbundleName": "main"}},
        {2: {"name": "Sub", "assetbundleName": "sub"}},
        [{"mysekaiBlueprintId": 9}],
        [{"mysekaiFixtureId": 1}],
    )

    assert supports_sketch is True
    assert request["basic_info"] == ["basic", "blueprint"]
    assert request["tags"] == ["tag"]
    assert request["cost_materials"] == [{"rows": 1}]
    assert request["recycle_materials"] == [{"rows": 1}]


def test_resolve_housing_competition_prefers_latest_active(monkeypatch) -> None:
    competitions = [
        {"id": 1, "submitStartAt": 10, "aggregateAt": 100},
        {"id": 2, "reviewStartAt": 20, "aggregateAt": 100},
        {"id": 3, "submitStartAt": 1, "aggregateAt": 5},
    ]

    class _Master:
        @staticmethod
        def get(_name: str) -> list[dict]:
            return competitions

    monkeypatch.setattr(gen, "MD", _Master())
    monkeypatch.setattr(gen, "NOW_MS", 50)

    assert gen._resolve_housing_competition() == (competitions[1], True)


def test_map_resource_drops_combines_and_decorates_position_groups(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gen, "ASSETS", _FakeAssets(tmp_path))
    monkeypatch.setattr(gen, "_resource_image_path", lambda key: (f"asset/{key}", False))
    monkeypatch.setattr(gen, "_resource_rarity", lambda key: 2 if key.endswith("_9") else 1)
    drops = [
        {"resourceType": "mysekai_material", "resourceId": 1, "quantity": 3, "positionX": 1, "positionZ": 1},
        {"resourceType": "mysekai_material", "resourceId": 1, "quantity": 3, "positionX": 1, "positionZ": 1},
        {"resourceType": "mysekai_fixture", "resourceId": 9, "quantity": 1, "positionX": 1, "positionZ": 1},
    ]

    result = gen._map_resource_drops(drops)
    by_type = {item["type"]: item for item in result}

    assert by_type["mysekai_material"]["quantity"] == 6
    assert by_type["mysekai_material"]["hide"] is True
    assert by_type["mysekai_material"]["small_icon"] is False
    assert by_type["mysekai_fixture"]["small_icon"] is True
    assert by_type["mysekai_fixture"]["outline_width"] == 2
    assert by_type["mysekai_fixture"]["light_size"] == 225


def test_fixture_list_catalog_excludes_birthday_progress(monkeypatch, tmp_path: Path) -> None:
    fixtures = [
        {
            "id": 1,
            "name": "Chair",
            "mysekaiFixtureType": "normal",
            "mysekaiFixtureMainGenreId": 1,
            "mysekaiFixtureSubGenreId": 2,
            "assetbundleName": "chair",
        },
        {
            "id": 2,
            "name": "Birthday",
            "mysekaiFixtureType": "normal",
            "mysekaiFixtureMainGenreId": 1,
            "mysekaiFixtureSubGenreId": 3,
            "assetbundleName": "birthday",
        },
    ]

    class _Master:
        @staticmethod
        def get(_name: str) -> list[dict]:
            return fixtures

    monkeypatch.setattr(gen, "MD", _Master())
    monkeypatch.setattr(gen, "ASSETS", _FakeAssets(tmp_path))
    monkeypatch.setattr(gen, "_birthday_character_id", lambda name: 39 if name == "Birthday" else 0)

    grouped, counts = gen._fixture_list_catalog(frozenset({1, 2}))

    assert [row["id"] for rows in grouped[1].values() for row in rows] == [1, 2]
    assert counts["total_all"] == 1
    assert counts["total_obtained"] == 1


def test_talk_read_helpers_separate_single_and_multi_unread_groups(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gen, "ASSETS", _FakeAssets(tmp_path))
    archive_reads = {
        1: {"fixture_ids": [10], "cuids": [17], "has_read": False},
        2: {"fixture_ids": [20], "cuids": [17, 18], "has_read": False},
        3: {"fixture_ids": [20], "cuids": [17, 18], "has_read": True},
    }
    fixture_map = {
        10: {"id": 10, "mysekaiFixtureMainGenreId": 1, "assetbundleName": "single"},
        20: {"id": 20, "mysekaiFixtureMainGenreId": 2, "assetbundleName": "multi"},
    }

    single, multi = gen._group_talk_reads(archive_reads)
    grouped_single = gen._group_single_talk_reads(single, fixture_map, frozenset({10}))
    multi_rows, total, read = gen._multi_talk_reads(multi, fixture_map, frozenset())

    assert grouped_single[1][0]["noread_num"] == 1
    assert grouped_single[1][0]["fixtures"][0]["obtained"] is True
    assert total == 2
    assert read == 1
    assert multi_rows[0]["noread_num"] == 1
    assert multi_rows[0]["character_ids"] == [[17, 18]]
