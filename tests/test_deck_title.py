from types import SimpleNamespace

import pytest

from src.sekai.deck.drawer import (
    _recommend_live_label,
    _recommend_type_title,
    format_skill_order_text,
    format_skill_reference_text,
    recommend_algorithm_name,
)


@pytest.mark.parametrize(
    ("recommend_type", "event_id", "character", "expected"),
    [
        ("mysekai", 10, None, "烤森活动 #10 组卡"),
        ("mysekai", None, None, "烤森模拟活动组卡"),
        ("challenge", None, None, "每日挑战组卡"),
        ("challenge_all", None, None, "每日挑战组卡"),
        ("bonus", 20, None, "活动 #20 加成组卡"),
        ("wl_bonus", 21, None, "WL 活动 #21 加成组卡"),
        ("event", 22, None, "活动 #22 组卡"),
        ("wl", 202, "初音未来", "WL 活动 #202 组卡"),
        ("wl", None, "宵崎奏", "WL 模拟组卡"),
        ("wl", None, None, "WL 终章活动组卡"),
        ("unit_attr", None, None, "团队+颜色模拟活动组卡"),
        ("no_event", None, None, "无活动组卡"),
        ("unknown", None, None, ""),
    ],
)
def test_recommend_type_title_covers_every_type(
    recommend_type: str, event_id: int | None, character: str | None, expected: str
) -> None:
    assert _recommend_type_title(recommend_type, event_id, character) == expected


def test_planner_title_swaps_the_noun_not_the_text() -> None:
    assert _recommend_type_title("event", 5, None, "规划") == "活动 #5 规划"
    assert _recommend_type_title("wl", None, "x", "规划") == "WL 模拟规划"
    assert _recommend_type_title("no_event", None, None) == "无活动组卡"


def test_recommend_title_draws_the_caller_labels() -> None:
    request = SimpleNamespace(
        labels={"deck.title.event": "Event #{event_id} {noun}", "deck.noun.deck": "Deck"},
    )
    assert _recommend_type_title("event", 5, None, request=request) == "Event #5 Deck"
    # A key the caller did not send falls back to Drawing's text, with the caller's noun.
    assert _recommend_type_title("no_event", None, None, request=request) == "无活动Deck"


@pytest.mark.parametrize(
    ("live_type", "live_name", "expected"),
    [
        # The caller's live_name is drawn for every live type, not only multi.
        ("solo", "单人 Live", "单人 Live"),
        ("auto", "自动 Live", "自动 Live"),
        # Without one, the live_type key picks this renderer's label (multi used to print "(None)").
        ("multi", None, "多人"),
        ("multi", "  ", "多人"),
        ("auto", None, "自动"),
        (None, None, ""),
    ],
)
def test_recommend_live_label(live_type: str | None, live_name: str | None, expected: str) -> None:
    assert _recommend_live_label(live_type, live_name) == expected


def test_algorithm_and_strategy_labels() -> None:
    assert recommend_algorithm_name("DGA") == "DFS 预热遗传"
    assert recommend_algorithm_name("dfs-ga") == "DFS 预热遗传"
    assert recommend_algorithm_name("custom") == "custom"
    labelled = SimpleNamespace(labels={"deck.algorithm.sa": "SA", "deck.skill_order.max": "Best order"})
    assert recommend_algorithm_name("SA", labelled) == "SA"
    assert format_skill_order_text("max") == "技能顺序：最优顺序"
    assert format_skill_order_text("max", labelled) == "Best order"
    assert format_skill_order_text("unknown") == ""
    assert format_skill_reference_text("average") == "BFes 花前吸取：平均值"
    assert format_skill_reference_text("specific") == ""
