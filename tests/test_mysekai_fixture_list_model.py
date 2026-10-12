"""The optional first-craft flag on MySekai fixture list items and the list's caller labels."""

from __future__ import annotations

from src.sekai.base.timezone import caller_label
from src.sekai.mysekai.model import MysekaiFixture, MysekaiFixtureListRequest

FIXTURE = {"id": 1, "image_path": "mysekai/thumbnail/fixture/a_1.png", "obtained": False}
LIST_PAYLOAD = {
    "main_genres": [{"name": "一般", "image_path": "icon.png", "sub_genres": [{"fixtures": [FIXTURE]}]}],
}
LABELS = {"mysekai.fixture_list.legend": "灰色为未获得过的家具", "mysekai.fixture_list.first_craft_badge": "+1000"}


def test_first_craft_is_optional_for_older_cloud_payloads() -> None:
    assert MysekaiFixture.model_validate(FIXTURE).first_craft is False
    rqd = MysekaiFixtureListRequest.model_validate(LIST_PAYLOAD)
    assert rqd.main_genres[0].sub_genres[0].fixtures[0].first_craft is False


def test_first_craft_parses_and_is_independent_of_obtained() -> None:
    fixture = MysekaiFixture.model_validate(FIXTURE | {"first_craft": True})
    assert fixture.first_craft is True
    assert fixture.obtained is False


def test_fixture_list_labels_are_optional_and_only_drawn() -> None:
    plain = MysekaiFixtureListRequest.model_validate(LIST_PAYLOAD)
    assert plain.labels is None
    assert caller_label(plain, "mysekai.fixture_list.legend", "默认") == "默认"
    assert caller_label(plain, "mysekai.fixture_list.first_craft_badge", "首次") == "首次"

    labelled = MysekaiFixtureListRequest.model_validate(LIST_PAYLOAD | {"labels": LABELS})
    assert caller_label(labelled, "mysekai.fixture_list.legend", "默认") == "灰色为未获得过的家具"
    assert caller_label(labelled, "mysekai.fixture_list.first_craft_badge", "首次") == "+1000"
