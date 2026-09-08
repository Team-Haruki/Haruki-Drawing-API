import json

import pytest

from src.sekai.profile.custom_profile.split import (
    build_custom_profile_render_request,
    build_profile_context,
    custom_profile_cards,
    custom_profile_output_name,
    decode_custom_profile_render_request,
    infer_profile_region,
    load_json,
    normalize_profile_payload,
    select_custom_profile_cards,
    write_json,
)


def _profile() -> dict:
    return {
        "region": "JP",
        "user": {"userId": 1, "server": "EN"},
        "userProfile": {"word": "hello"},
        "ignored": {"secret": True},
        "userCustomProfileCards": [
            {"seq": 3, "customProfileId": 10, "customProfileCardId": 103},
            {"seq": 1, "customProfileId": 10, "customProfileCardId": 101},
            {"seq": 2, "customProfileId": 20, "customProfileCardId": 102},
            "invalid",
        ],
    }


def test_profile_envelopes_and_card_shapes_are_normalized() -> None:
    profile = _profile()

    assert normalize_profile_payload({"response": profile}) is profile
    assert normalize_profile_payload({"updatedResources": profile}) is profile
    assert normalize_profile_payload(profile) is profile
    assert custom_profile_cards(profile) == profile["userCustomProfileCards"][:3]
    assert custom_profile_cards({"userCustomProfileCards": {}}) == []


def test_select_custom_profile_cards_supports_every_selector() -> None:
    profile = _profile()

    assert [card["seq"] for card in select_custom_profile_cards(profile, all_cards=True)] == [1, 2, 3]
    assert [card["seq"] for card in select_custom_profile_cards(profile)] == [1]
    assert [card["seq"] for card in select_custom_profile_cards(profile, seq=2)] == [2]
    assert [card["seq"] for card in select_custom_profile_cards(profile, custom_profile_id=10)] == [3, 1]
    assert [card["seq"] for card in select_custom_profile_cards(profile, custom_profile_card_id=102)] == [2]
    assert select_custom_profile_cards(profile, custom_profile_id=10, custom_profile_card_id=102) == []


def test_profile_region_context_and_render_request_are_minimal() -> None:
    profile = _profile()
    card = profile["userCustomProfileCards"][0]

    assert infer_profile_region(profile) == "jp"
    assert infer_profile_region({"user": {"server": " EN "}}) == "en"
    assert infer_profile_region({}) is None
    assert build_profile_context(profile) == {
        "user": profile["user"],
        "userProfile": profile["userProfile"],
    }
    request = build_custom_profile_render_request(profile, card)
    assert request["region"] == "jp"
    assert request["card"] is card
    assert request["profile_context"] == build_profile_context(profile)

    override = build_custom_profile_render_request({"response": profile}, card, region="EN")
    assert override["region"] == "en"


def test_decode_custom_profile_render_request_accepts_compatibility_keys() -> None:
    card = {"seq": 1}

    assert decode_custom_profile_render_request({"card": card}) == (card, {}, {})
    assert decode_custom_profile_render_request({"custom_profile_card": card, "context": {"a": 1}}) == (
        card,
        {"a": 1},
        {},
    )
    assert decode_custom_profile_render_request(
        {"customProfileCard": card, "profile_context": {"b": 2}, "resources": {"c": 3}}
    ) == (card, {"b": 2}, {"c": 3})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "missing card"),
        ({"card": {"seq": 1}, "profile_context": []}, "profile_context must be an object"),
        ({"card": {"seq": 1}, "resources": [1]}, "resources must be an object"),
    ],
)
def test_decode_custom_profile_render_request_rejects_invalid_shapes(payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        decode_custom_profile_render_request(payload)


def test_custom_profile_json_helpers_round_trip_and_name_output(tmp_path) -> None:
    path = tmp_path / "nested" / "request.json"
    value = {"message": "你好", "seq": 2}

    write_json(path, value)

    assert load_json(path) == value
    assert json.loads(path.read_text(encoding="utf-8")) == value
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert custom_profile_output_name({"seq": 2, "customProfileCardId": 17}) == "custom_profile_seq02_card17.png"
    assert custom_profile_output_name({}) == "custom_profile_seq00_card00.png"
