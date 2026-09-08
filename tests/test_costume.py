import asyncio

from PIL import Image, ImageDraw
import pytest

import src.sekai.costume.drawer as costume_drawer
from src.sekai.costume.drawer import (
    _costume_detail_id_info,
    _costume_lookup_text,
    _costume_role_ids,
    _published_time_text,
)
from src.sekai.costume.model import CostumeBasic, CostumeColorVariant, CostumeDetailRequest, CostumeListRequest


def _costume(**kwargs) -> CostumeBasic:
    base = {
        "costume_id": 6,
        "costume_group_id": 3,
        "name": "default",
        "part_type": "body",
        "character_id": 3,
        "character_name": "test",
        "thumbnail_path": "",
    }
    base.update(kwargs)
    return CostumeBasic(**base)


def test_costume_publish_time_does_not_fall_back_to_archive_time():
    costume = _costume(published_at=None, archive_published_at=1233284400000)

    assert _published_time_text(costume, "Asia/Tokyo") == "-"


def test_costume_publish_time_uses_published_at_when_present():
    costume = _costume(published_at=1601434800000, archive_published_at=1233284400000)

    assert _published_time_text(costume, "Asia/Tokyo") == "2020-09-30 12:00"


def test_costume_lookup_text_uses_outfit_id_and_selected_role():
    costume = _costume(outfit_id=1, character_3d_id=23, character_3d_ids=[21, 22, 23, 24, 25, 26])

    assert _costume_lookup_text(costume) == "服1 角23"


def test_costume_lookup_text_uses_accessory_id_and_role_range():
    costume = _costume(part_type="head", accessory_id=20, character_3d_ids=[21, 22, 23, 24, 25, 26])

    assert _costume_lookup_text(costume) == "饰20 角21-26"


def test_costume_lookup_text_uses_role_local_hair_id():
    costume = _costume(part_type="hair", hair_id=2, character_3d_id=23)

    assert _costume_lookup_text(costume) == "发2 角23"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"outfit_id": 11}, ("服装ID", "11")),
        ({"accessory_id": 12}, ("饰品ID", "12")),
        ({"hair_id": 13}, ("发型ID", "13")),
        ({}, ("ID", "6")),
    ],
)
def test_costume_detail_id_info_selects_specific_id(kwargs, expected):
    assert _costume_detail_id_info(_costume(**kwargs)) == expected


def test_costume_role_ids_prefers_selected_role():
    costume = _costume(character_3d_id=23, character_3d_ids=[21, 22, 23])

    assert _costume_role_ids(costume) == [23]


def test_costume_role_ids_keeps_supported_roles_without_selection():
    costume = _costume(character_3d_ids=[21, 22, 23])

    assert _costume_role_ids(costume) == [21, 22, 23]


def test_costume_list_canvas_renders_grouped_parts(monkeypatch):
    async def fake_image_loader(_path):
        return Image.new("RGBA", (48, 48), (30, 90, 160, 255))

    monkeypatch.setattr(costume_drawer, "_load_image", fake_image_loader)
    costumes = [
        _costume(costume_id=1, outfit_id=11, name="Body", part_type="body"),
        _costume(costume_id=2, accessory_id=12, name="Head", part_type="head"),
        _costume(costume_id=3, hair_id=13, name="Hair", part_type="hair"),
        _costume(costume_id=4, name="Other", part_type="other"),
    ]
    request = CostumeListRequest(region="jp", costumes=costumes, dt=1_700_000_000_000)

    canvas = asyncio.run(costume_drawer._build_costume_list_canvas(request))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0


def test_costume_list_canvas_renders_empty_result():
    request = CostumeListRequest(region="jp", costumes=[], dt=1_700_000_000_000)

    canvas = asyncio.run(costume_drawer._build_costume_list_canvas(request))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0


def test_costume_detail_canvas_renders_preview_and_variants(monkeypatch):
    preview = Image.new("RGBA", (160, 240), (255, 255, 255, 0))
    ImageDraw.Draw(preview).rectangle((45, 20, 115, 220), fill=(80, 120, 200, 255))

    async def fake_image_loader(_path):
        return Image.new("RGBA", (56, 56), (160, 90, 30, 255))

    async def fake_optional_loader(_path):
        return preview

    async def immediate_pool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(costume_drawer, "_load_image", fake_image_loader)
    monkeypatch.setattr(costume_drawer, "_load_optional_image", fake_optional_loader)
    monkeypatch.setattr(costume_drawer, "run_in_pool", immediate_pool)
    costume = _costume(
        outfit_id=11,
        name="Detailed Costume",
        part_name="服装",
        character_3d_ids=[21, 22, 23],
        color_name="Blue",
        how_to_obtain="Shop",
        designer="Designer",
        published_at=1_601_434_800_000,
        preview_image_path="preview.png",
        source_card_ids=[1, 2, 3, 4, 5, 6, 7],
        variants=[
            CostumeColorVariant(costume_id=6, color_id=1, color_name="Red", thumbnail_path="red.png"),
            CostumeColorVariant(costume_id=6, color_id=2, color_name="", thumbnail_path="blue.png"),
        ],
    )
    request = CostumeDetailRequest(region="jp", costume=costume, dt=1_700_000_000_000)

    canvas = asyncio.run(costume_drawer._build_costume_detail_canvas(request))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0


def test_costume_detail_canvas_renders_placeholder_without_variants(monkeypatch):
    async def no_optional_image(_path):
        return None

    monkeypatch.setattr(costume_drawer, "_load_optional_image", no_optional_image)
    request = CostumeDetailRequest(
        region="jp",
        costume=_costume(preview_image_path=None, variants=[]),
        dt=1_700_000_000_000,
    )

    canvas = asyncio.run(costume_drawer._build_costume_detail_canvas(request))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0
