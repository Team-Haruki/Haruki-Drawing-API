from src.sekai.deck.drawer import build_recommend_title


def test_build_recommend_title_uses_simulation_label_for_future_wl() -> None:
    assert build_recommend_title("wl", None, "宵崎奏", "multi", "协力") == "WL模拟组卡(协力)"


def test_build_recommend_title_keeps_event_id_for_regular_wl() -> None:
    assert build_recommend_title("wl", 202, "初音未来", "multi", "协力") == "WL活动#202组卡(协力)"


def test_build_recommend_title_keeps_finale_label_without_character() -> None:
    assert build_recommend_title("wl", None, None, "multi", "协力") == "WL终章活动组卡(协力)"
