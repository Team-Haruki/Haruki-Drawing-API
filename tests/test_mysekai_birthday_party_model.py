"""The optional birthday party progress list on the MySekai resource request."""

from __future__ import annotations

from src.sekai.mysekai.model import MysekaiBirthdayPartyProgress, MysekaiResourceRequest

RESOURCE_PAYLOAD = {
    "profile": {},
    "phenoms": [],
    "gate_id": 1,
    "gate_level": 1,
    "gate_icon_path": "missing/gate.png",
    "visit_characters": [],
}

PARTY = {
    "birthday_party_id": 27,
    "character_unit_id": 6,
    "character_name": "桐谷遥",
    "character_icon_path": "static_images/chara_icon/hrk.png",
    "character_color": "#99ccff",
    "level": 523,
    "max_level": 400,
}


def test_field_is_optional_for_older_cloud_payloads() -> None:
    assert MysekaiResourceRequest.model_validate(RESOURCE_PAYLOAD).birthday_parties is None


def test_parses_parties_and_keeps_levels_above_the_target() -> None:
    rqd = MysekaiResourceRequest.model_validate(RESOURCE_PAYLOAD | {"birthday_parties": [PARTY]})
    assert rqd.birthday_parties == [MysekaiBirthdayPartyProgress.model_validate(PARTY)]
    party = rqd.birthday_parties[0]
    assert (party.level, party.max_level) == (523, 400)
    assert party.character_name == "桐谷遥"


def test_defaults_and_candidate_icon_lists() -> None:
    minimal = MysekaiBirthdayPartyProgress.model_validate({"birthday_party_id": 1, "character_unit_id": 6})
    assert (
        minimal.level,
        minimal.max_level,
        minimal.character_icon_path,
        minimal.character_color,
        minimal.drop_end_at,
        minimal.watering_end_at,
    ) == (0, 400, None, None, None, None)
    candidates = MysekaiBirthdayPartyProgress.model_validate(
        {"birthday_party_id": 1, "character_unit_id": 6, "character_icon_path": ["a.png", "b.png"]}
    )
    assert candidates.character_icon_path == ["a.png", "b.png"]


def test_parses_drop_and_watering_end_times() -> None:
    party = MysekaiBirthdayPartyProgress.model_validate(
        PARTY | {"drop_end_at": 1791126000000, "watering_end_at": 1791385199000}
    )
    assert (party.drop_end_at, party.watering_end_at) == (1791126000000, 1791385199000)
