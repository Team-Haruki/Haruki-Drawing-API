from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest

from scripts.parity_payloads.custom_profile_category_fixture import (
    build_category_fixture,
    extract_category_fixture,
    validate_category_fixture,
    write_owner_only_json,
)


def _item(*, layer: int, text: str | None = None) -> dict:
    item = {
        "objectData": {
            "layer": layer,
            "position": {"x": 1.0, "y": 2.0},
            "scale": {"x": 1.0, "y": 1.0},
            "rotation": {"z": 0.0},
        }
    }
    if text is not None:
        item["text"] = text
    return item


def test_extract_category_fixture_keeps_only_selected_bucket_items() -> None:
    payload = {
        "region": "JP",
        "card": {
            "customProfileCardId": 123,
            "customProfileCard": {
                "stamps": [_item(layer=1), _item(layer=2)],
                "texts": [_item(layer=3, text="private")],
            },
        },
        "profile_context": {"user": {"userId": 999}},
        "resources": {"stamps": [{"id": 1}], "cards": [{"id": 2}]},
    }

    fixture = extract_category_fixture(payload, category="stamp", indexes=[1])

    assert fixture == {
        "schema_version": 1,
        "kind": "pjsk_custom_profile_category_fixture",
        "category": "stamp",
        "region": "jp",
        "items": [_item(layer=2)],
        "dependencies": {},
    }
    serialized = json.dumps(fixture)
    assert "customProfileCard" not in serialized
    assert "profile_context" not in serialized
    assert "resources" not in serialized
    assert "private" not in serialized


@pytest.mark.parametrize(
    ("category", "bucket"),
    [
        ("character_icon", "characterIcons"),
        ("material", "materials"),
        ("user_interface_icon", "userInterfaceIcons"),
    ],
)
def test_extract_category_fixture_supports_v67_image_buckets(category: str, bucket: str) -> None:
    item = _item(layer=1)
    payload = {
        "region": "jp",
        "card": {"customProfileCard": {bucket: [item]}},
    }

    fixture = extract_category_fixture(payload, category=category, indexes=[0])

    assert fixture["category"] == category
    assert fixture["items"] == [item]


def test_symbol_fixture_removes_plain_letters_and_numbers_but_keeps_markup_and_symbols() -> None:
    fixture = build_category_fixture(
        category="text_symbol",
        region="cn",
        items=[_item(layer=1, text="Alice42 <size=120%>★好</size>")],
    )

    assert fixture["items"][0]["text"] == "XXXXXXX <size=120%>★X</size>"
    validate_category_fixture(fixture)


@pytest.mark.parametrize(
    "dependencies",
    [
        {"profile_context": {}},
        {"nested": {"userId": 1}},
        {"nested": {"userStoryFavorites": []}},
        {"resources": {"cards": []}},
    ],
)
def test_category_fixture_rejects_full_request_or_user_scoped_dependencies(dependencies: dict) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        build_category_fixture(category="stamp", region="jp", items=[_item(layer=1)], dependencies=dependencies)


def test_category_fixture_rejects_unknown_root_data() -> None:
    fixture = build_category_fixture(category="stamp", region="jp", items=[_item(layer=1)])
    fixture["card"] = {"customProfileCard": {}}

    with pytest.raises(ValueError, match="forbidden root keys"):
        validate_category_fixture(fixture)


def test_category_fixture_is_written_owner_only(tmp_path: Path) -> None:
    fixture = build_category_fixture(category="stamp", region="jp", items=[_item(layer=1)])
    path = tmp_path / "sanitized" / "stamp.json"

    write_owner_only_json(path, fixture)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    validate_category_fixture(json.loads(path.read_text(encoding="utf-8")))
