from src.sekai.costume.drawer import _costume_lookup_text, _published_time_text
from src.sekai.costume.model import CostumeBasic


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
