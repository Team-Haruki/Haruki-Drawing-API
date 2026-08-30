import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from PIL import Image
import pytest

from src.sekai.base.plot import Canvas, TextBox
from src.sekai.sk import drawer
from src.sekai.sk.drawer import _collect_skl_display_ranks, _collect_speed_display_rows

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
DUMMY_IMAGE = Image.new("RGBA", (16, 16), (255, 255, 255, 255))


def _rank(
    rank: int,
    *,
    score: int | None = 100_000,
    time: datetime = NOW,
    name: str = "Player",
    **overrides,
):
    values = {
        "rank": rank,
        "name": name,
        "score": score,
        "time": time,
        "average_round": 3,
        "average_pt": 12_345.5,
        "latest_pt": 12_000,
        "speed": 50_000,
        "min20_times_3_speed": 48_000,
        "hour_round": 20,
        "record_start_at": time - timedelta(hours=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(**overrides):
    values = {
        "timezone": "UTC",
        "dt": NOW_MS,
        "region": "jp",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _tree_texts(widget) -> list[str]:
    texts = [widget.text] if isinstance(widget, TextBox) else []
    for child in getattr(widget, "items", []):
        texts.extend(_tree_texts(child))
    return texts


@pytest.fixture(autouse=True)
def _patch_drawing_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_asset(*_args, **_kwargs):
        return DUMMY_IMAGE

    async def inline_plot(func):
        return func()

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    monkeypatch.setattr(drawer, "request_now", lambda _timezone: NOW)
    monkeypatch.setattr(drawer, "add_request_watermark", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(drawer, "run_matplotlib_plot", inline_plot)
    monkeypatch.setattr(drawer, "plt_fig_to_image", lambda _fig: DUMMY_IMAGE)


def test_collect_skl_display_ranks_uses_payload_ranks_without_default_filter():
    current_ranks = [
        SimpleNamespace(rank=1500),
        SimpleNamespace(rank=10),
    ]
    forecast_columns = [
        SimpleNamespace(
            ranks=[
                SimpleNamespace(rank=2500),
                SimpleNamespace(rank=1500),
            ]
        )
    ]

    assert _collect_skl_display_ranks(current_ranks, forecast_columns) == [10, 1500, 2500]


def test_collect_speed_display_rows_uses_payload_ranks_without_default_filter():
    record_time = datetime(2026, 6, 5, tzinfo=UTC)
    rows = _collect_speed_display_rows(
        [
            SimpleNamespace(rank=1500, score=20, speed=2, record_time=record_time),
            SimpleNamespace(rank=42, score=10, speed=1, record_time=record_time),
        ]
    )

    assert [row[0] for row in rows] == [42, 1500]


def test_basic_sk_formatters_cover_event_and_time_variants() -> None:
    assert drawer.get_event_id_and_name_text("jp", 42, "Event") == "【JP-42】Event"
    assert drawer.get_event_id_and_name_text("en", 3007, "World Link") == "【EN-7-第3章单榜】World Link"
    assert drawer.get_board_rank_str(12345) == "12,345"
    assert drawer.get_board_score_str(None) == "?"
    assert drawer.get_board_score_str(123456, width=10) == "  12.3456w"
    assert drawer._time_to_event_end_text(NOW - timedelta(seconds=1), NOW) == "活动已结束"
    assert drawer._time_to_event_end_text(NOW + timedelta(hours=1), NOW).startswith("距离活动结束还有")
    assert drawer._readable_datetime_or_dash(None) == "-"
    assert drawer._rank_score_or_dash(None) == "-"
    assert drawer._rank_score_or_dash(_rank(1, score=None)) == "-"


def test_draw_day_night_bg_emits_hour_spans_and_handles_empty_window() -> None:
    class Axis:
        def __init__(self):
            self.spans = []

        def axvspan(self, *args, **kwargs):
            self.spans.append((args, kwargs))

    axis = Axis()
    drawer.draw_day_night_bg(axis, NOW, NOW + timedelta(hours=2, minutes=1))
    assert len(axis.spans) == 3

    empty_axis = Axis()
    drawer.draw_day_night_bg(empty_axis, NOW, NOW)
    assert empty_axis.spans == []


def test_build_skl_canvas_covers_current_prediction_and_empty_tables() -> None:
    current = [_rank(100, score=1_000_000), _rank(200, score=900_000, time=NOW - timedelta(minutes=2))]
    base = {
        "id": 42,
        "name": "Event",
        "start_at": NOW_MS - 86_400_000,
        "aggregate_at": NOW_MS + 86_400_000,
        "banner_img_path": "banner.png",
        "wl_cid": None,
        "chara_icon_path": None,
        "full": False,
    }

    current_request = _request(**base, ranks=current, current_ranks=None, forecast_columns=None, prediction_notice=None)
    current_canvas = asyncio.run(drawer._build_skl_canvas(current_request))
    assert {"排名", "分数", "RT"}.issubset(_tree_texts(current_canvas))

    forecast = SimpleNamespace(
        key="forecast",
        name="Forecast",
        ranks=[_rank(100, score=2_000_000), _rank(300, score=None)],
        forecast_time=NOW + timedelta(days=1),
        update_time=None,
    )
    prediction_request = _request(
        **(base | {"wl_cid": 1, "chara_icon_path": "icon.png"}),
        ranks=current,
        current_ranks=current,
        forecast_columns=[forecast],
        prediction_notice=None,
    )
    prediction_canvas = asyncio.run(drawer._build_skl_canvas(prediction_request))
    prediction_texts = _tree_texts(prediction_canvas)
    assert drawer._DEFAULT_PREDICTION_NOTICE in prediction_texts
    assert {"Forecast", "预测时间", "获取时间", "300"}.issubset(prediction_texts)

    empty_request = _request(**base, ranks=[], current_ranks=None, forecast_columns=None, prediction_notice=None)
    assert "暂无榜线数据" in _tree_texts(asyncio.run(drawer._build_skl_canvas(empty_request)))


def test_build_cf_canvas_covers_single_and_multi_player_details() -> None:
    single_rank = _rank(26)
    single = _request(
        eid=42,
        event_name="Event",
        name=" Requested Player ",
        username=None,
        ranks=[single_rank],
        prev_rank=_rank(25, score=110_000),
        next_rank=_rank(27, score=90_000),
        aggregate_at=NOW_MS + 86_400_000,
        update_at=NOW,
        wl_chara_icon_path="icon.png",
    )
    single_texts = _tree_texts(asyncio.run(drawer._build_cf_canvas(single)))
    assert "Requested Player" in single_texts
    assert any("↑1.0000w" in text for text in single_texts)
    assert any("20min×3时速" in text for text in single_texts)

    incomplete = _rank(
        30,
        name="",
        score=None,
        average_round=None,
        average_pt=None,
        latest_pt=None,
        speed=None,
        min20_times_3_speed=None,
        hour_round=None,
        record_start_at=None,
    )
    multi = _request(
        eid=42,
        event_name="Fallback Event",
        name=None,
        username="Alias",
        ranks=[incomplete, _rank(31, name="Second")],
        prev_rank=None,
        next_rank=None,
        aggregate_at=NOW_MS - 2_000,
        update_at=NOW,
        wl_chara_icon_path=None,
    )
    multi_texts = _tree_texts(asyncio.run(drawer._build_cf_canvas(multi)))
    assert "Alias" in multi_texts
    assert "Second" in multi_texts
    assert any("RT: 未知" in text for text in multi_texts)
    assert "活动已结束" in multi_texts

    unknown_gap = drawer._cf_neighbor_text(_rank(1, score=None), _rank(2, score=None), "↓")
    assert unknown_gap.endswith("↓?")


def test_build_sk_canvas_covers_single_multi_neighbors_and_icon() -> None:
    single = _request(
        id=42,
        name="Event",
        aggregate_at=NOW_MS + 86_400_000,
        ranks=[_rank(20, name="Single")],
        prev_ranks=_rank(19, score=110_000),
        next_ranks=_rank(21, score=90_000),
        wl_chara_icon_path="icon.png",
    )
    single_texts = _tree_texts(asyncio.run(drawer._build_sk_canvas(single)))
    assert "Single" in single_texts
    assert any("↑1.0000w" in text for text in single_texts)
    assert any("↓1.0000w" in text for text in single_texts)

    multi = _request(
        id=42,
        name="Event",
        aggregate_at=NOW_MS - 2_000,
        ranks=[_rank(30, name="Thirty"), _rank(10, name="Ten")],
        prev_ranks=None,
        next_ranks=None,
        wl_chara_icon_path=None,
    )
    multi_texts = _tree_texts(asyncio.run(drawer._build_sk_canvas(multi)))
    assert "活动已结束" in multi_texts
    assert multi_texts.index("Ten") < multi_texts.index("Thirty")


def test_csb_helpers_find_stops_extend_days_and_cover_heat_colors() -> None:
    ranks = [_rank(20, time=NOW + timedelta(minutes=index), score=100_000) for index in range(7)]
    ranks.extend(_rank(20, time=NOW + timedelta(minutes=index), score=100_000 + index) for index in range(7, 13))
    start_date, rankcounts, playcounts = drawer._collect_csb_heat_counts(ranks)
    segments = drawer._collect_csb_stop_segments(ranks)
    stop_hours = [[False] * 24 for _ in rankcounts]
    texts = drawer._build_csb_stop_texts(
        ranks[-1],
        ranks[-1].name,
        segments,
        stop_hours,
        start_date,
        drawer.TextStyle(font=drawer.DEFAULT_BOLD_FONT, size=24, color=drawer.BLACK),
        drawer.TextStyle(font=drawer.DEFAULT_FONT, size=20, color=drawer.BLACK),
    )
    assert len(texts) == 2
    assert any(stop_hours[0])
    assert rankcounts[0][NOW.hour] == 12
    assert playcounts[0][NOW.hour] > 0

    drawer._mark_csb_stop_hours(stop_hours, start_date, NOW, NOW + timedelta(days=2, hours=1))
    assert len(stop_hours) == 3
    with Canvas() as canvas:
        drawer._draw_csb_heat_cell(0, 0, False)
        drawer._draw_csb_heat_cell(38, 10, True)
        drawer._draw_csb_heat_cell(20, 10, False)
    assert len(canvas.items) == 3


def test_build_csb_canvas_covers_heatmap_and_no_stop_fallback() -> None:
    ranks = [_rank(20, time=NOW + timedelta(seconds=30 * index), score=100_000 + index) for index in range(12)]
    request = _request(
        eid=42,
        event_name="Event",
        ranks=ranks,
        aggregate_at=NOW_MS + 86_400_000,
        update_at=NOW,
        wl_chara_icon_path="icon.png",
    )
    canvas, scale = asyncio.run(drawer._build_csb_canvas(request))
    texts = _tree_texts(canvas)
    assert "未找到停车区间" in texts
    assert "标注*号的小时存在停车区间" in texts
    assert scale == 1.5

    many_stops = []
    for index in range(10):
        start = NOW + timedelta(minutes=index * 20)
        many_stops.extend(
            [
                _rank(20, time=start, score=100_000 + index),
                _rank(20, time=start + timedelta(minutes=6), score=100_000 + index),
            ]
        )
    large_request = _request(
        eid=42,
        event_name="Event",
        ranks=many_stops,
        aggregate_at=NOW_MS + 86_400_000,
        update_at=NOW,
        wl_chara_icon_path=None,
    )
    _, large_scale = asyncio.run(drawer._build_csb_canvas(large_request))
    assert large_scale == 1.0


def test_build_speed_canvas_covers_rows_missing_speed_wl_and_empty() -> None:
    rows = [
        SimpleNamespace(rank=200, score=900_000, speed=None, record_time=NOW),
        SimpleNamespace(rank=100, score=1_000_000, speed=50_000, record_time=NOW - timedelta(minutes=1)),
    ]
    request = _request(
        event_id=42,
        event_name="Event",
        event_start_at=NOW_MS - 86_400_000,
        event_aggregate_at=NOW_MS + 86_400_000,
        ranks=rows,
        is_wl_event=True,
        request_type="时",
        period=timedelta(hours=1),
        banner_img_path="banner.png",
        wl_chara_icon_path="icon.png",
    )
    texts = _tree_texts(asyncio.run(drawer._build_sks_canvas(request)))
    assert {"排名", "分数", "时速", "RT", "-"}.issubset(texts)

    empty = _request(
        event_id=42,
        event_name="Event",
        event_start_at=NOW_MS - 86_400_000,
        event_aggregate_at=NOW_MS - 2_000,
        ranks=[],
        is_wl_event=False,
        request_type="日",
        period=timedelta(days=1),
        banner_img_path=None,
        wl_chara_icon_path=None,
    )
    empty_texts = _tree_texts(asyncio.run(drawer._build_sks_canvas(empty)))
    assert "暂无时速数据" in empty_texts
    assert "活动已结束" in empty_texts


def test_player_trace_builder_covers_compare_series_and_reference_line() -> None:
    primary = [
        _rank(30, time=NOW - timedelta(hours=2), score=100_000, name="Primary"),
        _rank(20, time=NOW, score=200_000, name="Primary"),
        _rank(101, time=NOW, score=300_000, name="Ignored"),
    ]
    secondary = [
        _rank(40, time=NOW - timedelta(hours=2), score=80_000, name="Secondary"),
        _rank(25, time=NOW, score=180_000, name="Secondary"),
    ]
    compare = [
        _rank(100, time=NOW - timedelta(hours=2), score=70_000),
        _rank(100, time=NOW, score=170_000),
        _rank(100, time=NOW, score=None),
    ]
    request = _request(
        event_id=42,
        wl_chara_icon_path="icon.png",
        ranks=primary,
        ranks2=secondary,
        compare_rank=None,
        compare_rank_trace=compare,
        compare_rank_latest=None,
        compare_rank_line_score=None,
    )
    canvas = asyncio.run(drawer._build_player_trace_canvas(request))
    assert "单榜" in _tree_texts(canvas)

    latest = _rank(100, time=NOW, score=175_000)
    line_request = _request(
        event_id=42,
        wl_chara_icon_path=None,
        ranks=primary[:2],
        ranks2=[_rank(101)],
        compare_rank=100,
        compare_rank_trace=None,
        compare_rank_latest=latest,
        compare_rank_line_score=None,
    )
    assert "单榜" not in _tree_texts(asyncio.run(drawer._build_player_trace_canvas(line_request)))

    invalid = _request(
        event_id=42,
        wl_chara_icon_path=None,
        ranks=[_rank(101)],
        ranks2=None,
        compare_rank=None,
        compare_rank_trace=None,
        compare_rank_latest=None,
        compare_rank_line_score=None,
    )
    with pytest.raises(ValueError, match="top 100"):
        asyncio.run(drawer._build_player_trace_canvas(invalid))


def test_rank_trace_builder_covers_prediction_palette_and_speed_fallback() -> None:
    ranks = [
        _rank(100, time=NOW + timedelta(hours=index), score=100_000 + index * 10_000, name=f"Player {index}")
        for index in range(12)
    ]
    request = _request(
        event_id=42,
        target_rank=100,
        ranks=ranks,
        predict_ranks=_rank(100, score=300_000),
        wl_chara_icon_path="icon.png",
    )
    assert "单榜" in _tree_texts(asyncio.run(drawer._build_rank_trace_canvas(request)))

    short_request = _request(
        event_id=42,
        target_rank=100,
        ranks=[
            _rank(100, time=NOW, score=100_000, name="Same"),
            _rank(100, time=NOW + timedelta(minutes=10), score=110_000, name="Same"),
        ],
        predict_ranks=None,
        wl_chara_icon_path=None,
    )
    asyncio.run(drawer._build_rank_trace_canvas(short_request))

    empty = _request(event_id=42, target_rank=100, ranks=[], predict_ranks=None, wl_chara_icon_path=None)
    with pytest.raises(ValueError, match="must not be empty"):
        asyncio.run(drawer._build_rank_trace_canvas(empty))


def test_build_winrate_canvas_covers_team_labels_winner_and_recruiting(monkeypatch: pytest.MonkeyPatch) -> None:
    teams = [
        SimpleNamespace(
            team_id=2,
            team_name="Blue",
            team_cn_name=None,
            team_icon_path="blue.png",
            win_rate=0.4,
            is_recruiting=False,
        ),
        SimpleNamespace(
            team_id=1,
            team_name="Red",
            team_cn_name="红队",
            team_icon_path="red.png",
            win_rate=0.6,
            is_recruiting=True,
        ),
    ]
    request = _request(
        event_id=42,
        event_name="Carnival",
        event_start_at=NOW_MS - 86_400_000,
        event_aggregate_at=NOW_MS + 86_400_000,
        banner_img_path="banner.png",
        updated_at=NOW,
        team_info=teams,
    )
    texts = _tree_texts(asyncio.run(drawer._build_winrate_predict_canvas(request)))
    assert {"Red (红队)", "Blue", "60.0%", "40.0%", "（急募中）", ""}.issubset(texts)
    assert teams[1].team_name == "Red"

    async def missing_banner(_base, path):
        return None if path is None else DUMMY_IMAGE

    monkeypatch.setattr(drawer, "get_asset_image_ref", missing_banner)
    reverse_request = _request(
        event_id=42,
        event_name="Carnival",
        event_start_at=NOW_MS - 86_400_000,
        event_aggregate_at=NOW_MS - 2_000,
        banner_img_path=None,
        updated_at=NOW,
        team_info=[
            SimpleNamespace(**{**teams[1].__dict__, "win_rate": 0.3}),
            SimpleNamespace(**{**teams[0].__dict__, "win_rate": 0.7}),
        ],
    )
    assert "活动已结束" in _tree_texts(asyncio.run(drawer._build_winrate_predict_canvas(reverse_request)))


class _FakeCanvas:
    def __init__(self):
        self.scales = []

    async def get_img(self, scale=None):
        self.scales.append(scale)
        return DUMMY_IMAGE


@pytest.mark.parametrize(
    ("compose_name", "builder_name", "scale", "tuple_result"),
    [
        ("compose_skl_image", "_build_skl_canvas", None, False),
        ("compose_sk_image", "_build_sk_canvas", 1.5, False),
        ("compose_cf_image", "_build_cf_canvas", 1.5, False),
        ("compose_csb_image", "_build_csb_canvas", 1.25, True),
        ("compose_sks_image", "_build_sks_canvas", None, False),
        ("compose_player_trace_image", "_build_player_trace_canvas", None, False),
        ("compose_rank_trace_image", "_build_rank_trace_canvas", None, False),
        ("compose_winrate_predict_image", "_build_winrate_predict_canvas", 2.0, False),
    ],
)
def test_compose_helpers_render_their_built_canvas(
    monkeypatch: pytest.MonkeyPatch,
    compose_name: str,
    builder_name: str,
    scale: float | None,
    tuple_result: bool,
) -> None:
    canvas = _FakeCanvas()

    async def builder(_request):
        return (canvas, scale) if tuple_result else canvas

    monkeypatch.setattr(drawer, builder_name, builder)
    assert asyncio.run(getattr(drawer, compose_name)(object())) is DUMMY_IMAGE
    assert canvas.scales == [scale]


@pytest.mark.parametrize(
    ("renderer_name", "builder_name", "endpoint", "scale", "tuple_result"),
    [
        ("try_render_skl_payload", "_build_skl_canvas", "sk_line", None, False),
        ("try_render_sk_payload", "_build_sk_canvas", "sk_query", 1.5, False),
        ("try_render_cf_payload", "_build_cf_canvas", "sk_check_room", 1.5, False),
        ("try_render_csb_payload", "_build_csb_canvas", "sk_csb", 1.25, True),
        ("try_render_sks_payload", "_build_sks_canvas", "sk_speed", None, False),
        ("try_render_player_trace_payload", "_build_player_trace_canvas", "sk_player_trace", None, False),
        ("try_render_rank_trace_payload", "_build_rank_trace_canvas", "sk_rank_trace", None, False),
        ("try_render_winrate_predict_payload", "_build_winrate_predict_canvas", "sk_winrate", 2.0, False),
    ],
)
def test_native_render_helpers_honor_gate_and_forward_metadata(
    monkeypatch: pytest.MonkeyPatch,
    renderer_name: str,
    builder_name: str,
    endpoint: str,
    scale: float | None,
    tuple_result: bool,
) -> None:
    canvas = object()
    render_calls = []

    async def builder(_request):
        return (canvas, scale) if tuple_result else canvas

    async def render(received_canvas, **kwargs):
        render_calls.append((received_canvas, kwargs))
        return "payload"

    monkeypatch.setattr(drawer, builder_name, builder)
    monkeypatch.setattr(drawer, "render_canvas_payload", render)
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    assert asyncio.run(getattr(drawer, renderer_name)(object())) is None
    assert render_calls == []

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    assert asyncio.run(getattr(drawer, renderer_name)(object())) == "payload"
    expected = {"endpoint": endpoint}
    if scale is not None:
        expected["scale"] = scale
    assert render_calls == [(canvas, expected)]
