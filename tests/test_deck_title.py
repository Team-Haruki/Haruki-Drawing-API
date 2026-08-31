import pytest

from src.sekai.deck.drawer import build_recommend_title


def test_build_recommend_title_uses_simulation_label_for_future_wl() -> None:
    assert build_recommend_title("wl", None, "宵崎奏", "multi", "协力") == "WL模拟组卡(协力)"


def test_build_recommend_title_keeps_event_id_for_regular_wl() -> None:
    assert build_recommend_title("wl", 202, "初音未来", "multi", "协力") == "WL活动#202组卡(协力)"


def test_build_recommend_title_keeps_finale_label_without_character() -> None:
    assert build_recommend_title("wl", None, None, "multi", "协力") == "WL终章活动组卡(协力)"


@pytest.mark.parametrize(
    ("recommend_type", "event_id", "character", "live_type", "live_name", "expected"),
    [
        ("mysekai", 10, None, "multi", "协力", "烤森活动#10组卡"),
        ("mysekai", None, None, "solo", None, "烤森模拟活动组卡"),
        ("challenge", None, None, "auto", None, "每日挑战组卡"),
        ("challenge_all", None, None, "multi", "协力", "每日挑战组卡"),
        ("bonus", 20, None, "solo", None, "活动#20加成组卡"),
        ("wl_bonus", 21, None, "auto", None, "WL活动#21加成组卡"),
        ("event", 22, None, "solo", None, "活动#22组卡(单人)"),
        ("unit_attr", None, None, "auto", None, "团队+颜色模拟活动组卡(AUTO)"),
        ("no_event", None, None, None, None, "无活动组卡"),
        ("unknown", None, None, "multi", "协力", "(协力)"),
    ],
)
def test_build_recommend_title_covers_all_type_and_live_labels(
    recommend_type: str,
    event_id: int | None,
    character: str | None,
    live_type: str | None,
    live_name: str | None,
    expected: str,
) -> None:
    assert build_recommend_title(recommend_type, event_id, character, live_type, live_name) == expected
