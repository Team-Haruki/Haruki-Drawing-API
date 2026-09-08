from __future__ import annotations

import asyncio
from types import SimpleNamespace

from PIL import Image
import pytest

from src.sekai.base.plot import Canvas, HSplit, Spacer, TextBox
from src.sekai.education import drawer


def _profile() -> SimpleNamespace:
    return SimpleNamespace(to_profile_card_request=lambda: object())


def _walk_widgets(widget):
    yield widget
    for item in getattr(widget, "items", ()):  # Frame, split and Canvas share this public collection.
        yield from _walk_widgets(item)


def _texts(widget) -> list[str]:
    return [item.text for item in _walk_widgets(widget) if isinstance(item, TextBox)]


@pytest.fixture
def isolated_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_asset(_base_dir: str, _path: str) -> Image.Image:
        return Image.new("RGBA", (64, 64), (1, 2, 3, 255))

    async def fake_profile(_request: object) -> Spacer:
        return Spacer(100, 40)

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    monkeypatch.setattr(drawer, "get_profile_card", fake_profile)
    monkeypatch.setattr(drawer, "add_request_watermark", lambda _canvas, _request: None)


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        (999, "999"),
        (1_000, "1k"),
        (1_500, "1k5"),
        (10_000, "1w"),
        (15_000, "1w5"),
        (100_000, "10w"),
        (10_000_000, "1kw"),
    ],
)
def test_get_quant_text_boundaries(quantity: int, expected: str) -> None:
    assert drawer._get_quant_text(quantity) == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (2_500_001, (100, 255, 100, 255)),
        (2_000_001, (255, 255, 100, 255)),
        (1_500_001, (255, 200, 100, 255)),
        (1_000_001, (255, 150, 100, 255)),
        (500_001, (255, 100, 100, 255)),
        (500_000, (255, 50, 50, 255)),
    ],
)
def test_challenge_score_color_thresholds(score: int, expected: tuple[int, int, int, int]) -> None:
    assert drawer._challenge_score_color(score) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (50_001, (100, 255, 100, 255)),
        (40_001, (255, 255, 100, 255)),
        (30_001, (255, 200, 100, 255)),
        (20_001, (255, 150, 100, 255)),
        (10_001, (255, 100, 100, 255)),
        (10_000, (255, 50, 50, 255)),
    ],
)
def test_leader_count_color_thresholds(count: int, expected: tuple[int, int, int, int]) -> None:
    assert drawer._leader_count_color(count) == expected


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (1.0, (100, 255, 100, 255)),
        (0.9, (255, 255, 100, 255)),
        (0.7, (255, 200, 100, 255)),
        (0.5, (255, 150, 100, 255)),
        (0.3, (255, 100, 100, 255)),
        (0.2, (255, 50, 50, 255)),
    ],
)
def test_education_progress_color_thresholds(ratio: float, expected: tuple[int, int, int, int]) -> None:
    assert drawer._education_progress_color(ratio) == expected


def test_progress_bar_handles_empty_disabled_and_tick_states() -> None:
    empty = drawer._build_progress_bar(width=100, current=1, maximum=0, fill=(1, 2, 3, 4), tick_step=10)
    disabled = drawer._build_progress_bar(
        width=100,
        current=50,
        maximum=100,
        fill=(1, 2, 3, 4),
        tick_step=10,
        enabled=False,
    )
    progress = drawer._build_progress_bar(
        width=100,
        current=60,
        maximum=100,
        fill=(1, 2, 3, 4),
        tick_step=20,
    )

    assert len(empty.items) == 1
    assert len(disabled.items) == 1
    assert len(progress.items) == 6
    assert progress.items[1].w == 57


def test_challenge_live_builder_loads_optional_assets_and_builds_rows(isolated_assets: None) -> None:
    request = SimpleNamespace(
        profile=_profile(),
        jewel_icon_path="jewel.png",
        shard_icon_path=None,
        max_score=3_000_000,
        character_challenges=[
            SimpleNamespace(
                chara_icon_path="one.png",
                rank=0,
                score=0,
                jewel=10,
                shard=20,
            ),
            SimpleNamespace(
                chara_icon_path="two.png",
                rank=12,
                score=2_600_000,
                jewel=0,
                shard=1,
            ),
        ],
    )

    canvas = asyncio.run(drawer._build_challenge_live_detail_canvas(request))

    assert isinstance(canvas, Canvas)
    root = canvas.items[0]
    table = root.items[1]
    assert len(table.items) == 3
    assert {"角色", "等级", "2,600,000"}.intersection(_texts(canvas)) == {"角色", "等级"}
    assert "2600000" in _texts(canvas)


def test_power_bonus_builder_groups_each_bonus_kind(
    isolated_assets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_asset(_base_dir: str, path: str) -> Image.Image | None:
        if path == "missing.png":
            return None
        return Image.new("RGBA", (64, 64), (1, 2, 3, 255))

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    chara_bonuses = [
        SimpleNamespace(
            chara_icon_path="missing.png" if index == 0 else f"chara-{index}.png",
            total=float(index),
            area_item=1.0,
            rank=2.0,
            fixture=3.0,
        )
        for index in range(5)
    ]
    unit_bonuses = [
        SimpleNamespace(unit_icon_path="unit.png", total=4.0, area_item=1.0, gate=3.0),
    ]
    attr_bonuses = [
        SimpleNamespace(attr_icon_path="attr.png", total=5.0),
    ]
    request = SimpleNamespace(
        profile=_profile(),
        chara_bonuses=chara_bonuses,
        unit_bonuses=unit_bonuses,
        attr_bonuses=attr_bonuses,
    )

    canvas = asyncio.run(drawer._build_power_bonus_detail_canvas(request))

    assert isinstance(canvas, Canvas)
    assert {"0.0%", "4.0%", "5.0%", "区域道具1.0% + 烤森门3.0%"}.issubset(_texts(canvas))


def test_area_item_helpers_and_builder_cover_profile_states(isolated_assets: None) -> None:
    levels = [
        SimpleNamespace(level=1, bonus=1.0, can_upgrade=True, materials=[]),
        SimpleNamespace(
            level=2,
            bonus=2.0,
            can_upgrade=True,
            materials=[
                SimpleNamespace(
                    material_icon_path="material-a.png",
                    quantity=1_500,
                    have_quantity=2_000,
                    sum_quantity=3_000,
                    is_enough=True,
                )
            ],
        ),
        SimpleNamespace(
            level=3,
            bonus=3.0,
            can_upgrade=False,
            materials=[
                SimpleNamespace(
                    material_icon_path="material-b.png",
                    quantity=10_000,
                    have_quantity=0,
                    sum_quantity=10_000,
                    is_enough=False,
                )
            ],
        ),
    ]
    item = SimpleNamespace(
        item_icon_path="item.png",
        target_icon_path="target.png",
        current_level=1,
        levels=levels,
    )
    request = SimpleNamespace(profile=_profile(), area_items=[item], has_profile=True)

    canvas = asyncio.run(drawer._build_area_item_upgrade_materials_canvas(request))
    without_profile = drawer._build_area_item_column(item, {}, False)

    assert drawer._collect_area_item_icon_paths([item]) == [
        "item.png",
        "target.png",
        "material-a.png",
        "material-b.png",
    ]
    assert drawer._area_level_color(1, 1, False, True) == (50, 50, 50)
    assert drawer._area_level_color(2, 1, True, True) == (0, 200, 0)
    assert drawer._area_level_color(2, 1, False, True) == (200, 0, 0)
    assert drawer._area_level_color(2, 1, False, False) == (50, 50, 50)
    assert isinstance(canvas.items[0].items[1], HSplit)
    assert {"x1k5", "2k/3k", "0/1w"}.issubset(_texts(canvas))
    assert {"3k", "1w"}.issubset(_texts(without_profile))


def test_bonds_builder_covers_absent_max_and_progress_states(isolated_assets: None) -> None:
    bonds = [
        SimpleNamespace(
            chara_icon_path1="a.png",
            chara_icon_path2="b.png",
            chara_rank1=1,
            chara_rank2=2,
            bond_level=0,
            need_exp=None,
            has_bond=False,
            color1=(1, 2, 3),
            color2=(4, 5, 6),
        ),
        SimpleNamespace(
            chara_icon_path1="c.png",
            chara_icon_path2="d.png",
            chara_rank1=10,
            chara_rank2=20,
            bond_level=50,
            need_exp=0,
            has_bond=True,
            color1=(1, 2, 3),
            color2=(4, 5, 6),
        ),
        SimpleNamespace(
            chara_icon_path1="e.png",
            chara_icon_path2="f.png",
            chara_rank1=30,
            chara_rank2=40,
            bond_level=10,
            need_exp=123,
            has_bond=True,
            color1=(1, 2, 3),
            color2=(4, 5, 6),
        ),
    ]
    request = SimpleNamespace(profile=_profile(), bonds=bonds, max_level=50)

    canvas = asyncio.run(drawer._build_bonds_canvas(request))

    assert drawer._bond_need_exp_text(bonds[0], 50) == "-"
    assert drawer._bond_need_exp_text(bonds[1], 50) == "MAX"
    assert drawer._bond_need_exp_text(bonds[2], 50) == "123"
    assert "MAX" in _texts(canvas)
    assert "123" in _texts(canvas)


def test_leader_count_builder_covers_zero_and_high_counts(isolated_assets: None) -> None:
    request = SimpleNamespace(
        profile=_profile(),
        max_play_count=60_000,
        leader_counts=[
            SimpleNamespace(chara_icon_path="a.png", play_count=0, ex_level=0, ex_count=0),
            SimpleNamespace(chara_icon_path="b.png", play_count=55_000, ex_level=3, ex_count=2),
        ],
    )

    canvas = asyncio.run(drawer._build_leader_count_canvas(request))

    assert {"55000", "x3", "2"}.issubset(_texts(canvas))
    assert len(canvas.items[0].items[1].items) == 3


def _mission_row(mission_type: str, *, is_ex: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        mission_type=mission_type,
        title=mission_type,
        current=5,
        upper=10,
        ratio=0.5,
        next_need=10,
        next_exp=2,
        ex_display_round_text="EX 2" if is_ex else None,
    )


def test_character_mission_overview_builds_dual_odd_and_empty_panels(isolated_assets: None) -> None:
    achievement_rows = [
        _mission_row("play_live"),
        _mission_row("play_live_ex", is_ex=True),
        _mission_row("waiting_room"),
        _mission_row("waiting_room_ex", is_ex=True),
        _mission_row("other"),
    ]
    request = SimpleNamespace(
        profile=_profile(),
        character_icon_path="character.png",
        character_name="初音未来",
        current_level=10,
        current_exp=1,
        pending_exp=2,
        final_level=11,
        final_exp=3,
        basic_rows=[_mission_row("basic-a"), _mission_row("basic-b"), _mission_row("basic-c")],
        achievement_rows=achievement_rows,
    )
    empty_request = SimpleNamespace(**{**request.__dict__, "basic_rows": [], "achievement_rows": []})

    canvas = asyncio.run(drawer._build_character_mission_overview_canvas(request))
    empty_canvas = asyncio.run(drawer._build_character_mission_overview_canvas(empty_request))

    assert {"基本任务", "成就", "队长次数", "休息室次数", "other"}.issubset(_texts(canvas))
    assert "暂无可显示的基本任务" in _texts(empty_canvas)
    assert "暂无可显示的成就任务" in _texts(empty_canvas)
    assert isinstance(drawer._build_character_mission_card_rows(request.basic_rows, 520)[-1].items[1], Spacer)


def test_character_mission_progress_covers_badge_unbounded_and_completed_states() -> None:
    active = drawer._draw_character_mission_progress(
        "任务",
        5,
        10,
        0.5,
        200,
        next_need=10,
        next_exp=None,
        title_badge="EX 2",
    )
    completed = drawer._draw_character_mission_progress("任务", 10, 10, 0, 200)
    unbounded = drawer._draw_character_mission_progress("任务", 1, None, 2, 200)

    assert {"EX 2", "5/10 (50.0%)", "下一档5/10 EXP+?"}.issubset(_texts(active))
    assert "下一档已满" in _texts(completed)
    assert "1/∞ (-)" in _texts(unbounded)


def _table_row(seq: int) -> SimpleNamespace:
    return SimpleNamespace(seq=seq, requirement=seq * 10, acc_requirement=seq * 20, exp=seq, acc_exp=seq * 2)


def _section(*, is_ex: bool, rows: list[SimpleNamespace], reached_seq: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        is_ex=is_ex,
        current_total=100,
        reached_seq=reached_seq,
        current_round_no=2 if is_ex else None,
        upper=200,
        ratio=0.5,
        next_need=150,
        next_exp=5,
        display_rows=rows,
    )


def test_character_mission_all_builder_chunks_normal_and_ex_tables(isolated_assets: None) -> None:
    normal = _section(is_ex=False, rows=[_table_row(index) for index in range(1, 42)])
    ex = _section(is_ex=True, rows=[_table_row(1), _table_row(2)])
    request = SimpleNamespace(
        profile=_profile(),
        character_icon_path="character.png",
        character_name="初音未来",
        title="角色等级",
        sections=[normal, ex],
    )
    empty_request = SimpleNamespace(**{**request.__dict__, "sections": []})

    canvas = asyncio.run(drawer._build_character_mission_all_canvas(request))
    empty_canvas = asyncio.run(drawer._build_character_mission_all_canvas(empty_request))

    assert drawer._character_mission_normal_column_count([normal, ex]) == 2
    assert drawer._character_mission_normal_column_count([ex]) is None
    assert [len(chunk) for chunk in drawer._character_mission_table_chunks(normal, None)] == [40, 1]
    assert [len(chunk) for chunk in drawer._character_mission_table_chunks(normal, 2)] == [21, 20]
    assert drawer._character_mission_table_chunks(_section(is_ex=False, rows=[]), None) == [[]]
    assert {"普通任务", "EX任务", "当前回目 EX 2", "#1", "#41"}.issubset(_texts(canvas))
    assert "没有可显示的任务表数据" in _texts(empty_canvas)


def test_character_mission_section_header_shows_reached_normal_tier() -> None:
    section = _section(is_ex=False, rows=[_table_row(1)], reached_seq=1)
    header = drawer._build_character_mission_section_header(
        section,
        drawer.TextStyle(),
        drawer.TextStyle(),
        drawer.TextStyle(),
    )

    assert "已达档位 #1" in _texts(header)


def test_native_entry_points_fail_open_when_skia_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    entry_points = [
        drawer.try_render_challenge_live_detail_payload,
        drawer.try_render_power_bonus_detail_payload,
        drawer.try_render_area_item_upgrade_materials_payload,
        drawer.try_render_bonds_payload,
        drawer.try_render_leader_count_payload,
        drawer.try_render_character_mission_overview_payload,
        drawer.try_render_character_mission_all_payload,
    ]

    assert all(asyncio.run(entry_point(object())) is None for entry_point in entry_points)


def test_native_and_compose_entry_points_delegate_to_shared_canvas(monkeypatch: pytest.MonkeyPatch) -> None:
    image = Image.new("RGB", (1, 1))

    class FakeCanvas:
        async def get_img(self) -> Image.Image:
            return image

    fake_canvas = FakeCanvas()
    rendered_endpoints: list[str] = []

    async def fake_builder(_request: object) -> FakeCanvas:
        return fake_canvas

    async def fake_render(canvas: FakeCanvas, *, endpoint: str):
        assert canvas is fake_canvas
        rendered_endpoints.append(endpoint)
        return endpoint

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "render_canvas_payload", fake_render)
    entry_points = [
        (
            "_build_challenge_live_detail_canvas",
            drawer.compose_challenge_live_detail_image,
            drawer.try_render_challenge_live_detail_payload,
            "education_challenge_live",
        ),
        (
            "_build_power_bonus_detail_canvas",
            drawer.compose_power_bonus_detail_image,
            drawer.try_render_power_bonus_detail_payload,
            "education_power_bonus",
        ),
        (
            "_build_area_item_upgrade_materials_canvas",
            drawer.compose_area_item_upgrade_materials_image,
            drawer.try_render_area_item_upgrade_materials_payload,
            "education_area_item",
        ),
        ("_build_bonds_canvas", drawer.compose_bonds_image, drawer.try_render_bonds_payload, "education_bonds"),
        (
            "_build_leader_count_canvas",
            drawer.compose_leader_count_image,
            drawer.try_render_leader_count_payload,
            "education_leader_count",
        ),
        (
            "_build_character_mission_overview_canvas",
            drawer.compose_character_mission_overview_image,
            drawer.try_render_character_mission_overview_payload,
            "education_character_mission_overview",
        ),
        (
            "_build_character_mission_all_canvas",
            drawer.compose_character_mission_all_image,
            drawer.try_render_character_mission_all_payload,
            "education_character_mission_all",
        ),
    ]
    for builder_name, compose, try_render, endpoint in entry_points:
        monkeypatch.setattr(drawer, builder_name, fake_builder)
        assert asyncio.run(compose(object())) is image
        assert asyncio.run(try_render(object())) == endpoint

    assert rendered_endpoints == [entry_point[3] for entry_point in entry_points]


def test_asset_load_helpers_handle_empty_and_optional_paths(isolated_assets: None) -> None:
    assert asyncio.run(drawer._load_asset_ref_cache([], "unused")) == {}
    loaded = asyncio.run(drawer._load_optional_asset_refs("one.png", None, "two.png"))
    assert isinstance(loaded[0], Image.Image)
    assert loaded[1] is None
    assert isinstance(loaded[2], Image.Image)
