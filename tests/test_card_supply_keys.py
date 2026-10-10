"""Card supply behaviour is keyed on the raw supply key, with the legacy display labels as a fallback."""

import pytest

from src.sekai.card import drawer as card


@pytest.mark.parametrize(
    ("key", "label", "expected"),
    [
        ("term_limited", "期间限定", "term_limited"),
        ("unit_event_limited", "WL 限定", "unit_event_limited"),
        ("festival_limited", None, "colorful_festival_limited"),
        ("not_limited", None, "normal"),
        ("Birthday", "whatever", "birthday"),
        # Legacy callers: only the label.
        (None, "期间限定", "term_limited"),
        (None, "WL限定", "unit_event_limited"),
        (None, "WL 限定", "unit_event_limited"),
        (None, "CFes 限定", "colorful_festival_limited"),
        (None, "BFes限定", "bloom_festival_limited"),
        (None, "Fes限定", "colorful_festival_limited"),
        (None, "生日", "birthday"),
        (None, "非限定", "normal"),
        (None, "", "normal"),
        (None, "normal", "normal"),
        (None, "birthday", "birthday"),
    ],
)
def test_supply_type_key(key, label, expected) -> None:
    assert card.supply_type_key(key, label) == expected


def test_birthday_is_not_limited() -> None:
    assert card.is_non_limited_supply_type("birthday")
    assert card.is_non_limited_supply_type(None, "生日")
    assert not card.is_non_limited_supply_type("unit_event_limited", "WL 限定")
    # The key wins over a label that would read differently.
    assert not card.is_non_limited_supply_type("term_limited", "非限定")


def test_limited_icon_kind() -> None:
    assert card._limited_icon_kind("collaboration_limited") == "term"
    assert card._limited_icon_kind("bloom_festival_limited") == "fes"
    assert card._limited_icon_kind(None, "WL 限定") == "term"
    assert card._limited_icon_kind("birthday", "生日") is None
    assert card._limited_icon_kind("normal") is None
    assert card._limited_icon_kind("something_new") is None


def test_rarity_progress_bucket_uses_the_supply_key() -> None:
    assert card._rarity_progress_bucket("rarity_4", "birthday", "生日") == "birthday"
    # The old code compared the localized label to "birthday", so this was missed.
    assert card._rarity_progress_bucket("rarity_4", None, "生日") == "birthday"
    assert card._rarity_progress_bucket("rarity_birthday") == "birthday"
    assert card._rarity_progress_bucket("rarity_3", "term_limited") == "rarity_3"
