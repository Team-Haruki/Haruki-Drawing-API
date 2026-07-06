from src.sekai.costume.drawer import _published_time_text
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
