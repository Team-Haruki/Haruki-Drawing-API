"""Exercise complete curve drawings, including clipping and both legend axes."""

from datetime import UTC, datetime, timedelta
from io import BytesIO
import json

from PIL import Image
import pytest

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.sk.trace_spec import TraceAnnotation, TraceHorizontalLine, TraceSeries, TraceSpec
from src.sekai.sk.trace_widget import TracePlotBox, build_trace_plot_canvas
from src.sekai.skia_renderer.canvas import build_canvas_ir, load_native_renderer


@pytest.mark.parametrize(
    ("secondary_kind", "score_format", "grid"), [("rank", "board", True), ("speed", "scalar", False)]
)
def test_trace_plot_renders_clipped_series_annotations_and_legends(
    secondary_kind, score_format, grid, real_fonts, monkeypatch
):
    try:
        native = load_native_renderer()
    except ImportError:
        pytest.skip("native renderer required")
    labels = []
    original_text = TracePlotBox._text

    def record_text(self, painter, text, *args, **kwargs):
        labels.append(text)
        return original_text(self, painter, text, *args, **kwargs)

    monkeypatch.setattr(TracePlotBox, "_text", record_text)
    start = datetime(2026, 1, 2, 5, tzinfo=UTC)
    times = tuple(start + timedelta(hours=i * 12) for i in range(4))
    values = (1_000_000.0, 1_100_000.0, 1_250_000.0, 1_300_000.0)
    spec = TraceSpec(
        title="Event score and rank",
        start=start,
        end=times[-1],
        series=(
            TraceSeries("score", times, values, "Score", "royalblue", 3, dashed=True),
            TraceSeries("score", times, values, "Points", "red", 9, scatter_colors=("red", "green", "coral", "gray")),
            TraceSeries("secondary", times, (90, 70, 50, 10), "Rank / speed", "green", 2),
        ),
        annotations=(
            TraceAnnotation("score", times[1], values[1], "Score label", "royalblue"),
            TraceAnnotation("score", start - timedelta(days=3), 0, "CLIPPED", "red"),
            TraceAnnotation("secondary", times[2], 50, "Secondary label", "green", vertical_alignment="bottom"),
        ),
        horizontal_lines=(
            TraceHorizontalLine(1_150_000, "gray", ":", 0.8, label="Forecast"),
            TraceHorizontalLine(1_200_000, "red", "--", 0.6),
        ),
        score_limits=(1_000_000, 1_350_000),
        score_format=score_format,
        secondary_limits=(100, 0) if secondary_kind == "rank" else (0, 100),
        secondary_kind=secondary_kind,
        exact_time_limits=True,
        score_grid=grid,
    )
    token = begin_pillow_touch_scope()
    try:
        canvas = build_trace_plot_canvas(spec)
        builder, memory = build_canvas_ir(canvas)
        scene = builder.build()
        result = native.render_scene(json.dumps(scene).encode(), memory)
        assert take_pillow_touch_snapshot().counts == {}
    finally:
        end_pillow_touch_scope(token)
    assert memory == {}
    output = Image.open(BytesIO(result["image_bytes"]))
    assert output.size == tuple(scene["canvas"][key] for key in ("width", "height"))
    assert output.width > 500
    assert output.height > 300
    assert len(set(output.convert("RGB").get_flattened_data())) > 20
    # The out-of-range annotation is excluded before geometry/renderer submission.
    assert "Score label" in labels
    assert "Secondary label" in labels
    assert "CLIPPED" not in labels


def test_legacy_trace_adapter_preserves_axes_and_annotation_contract(real_fonts, monkeypatch):
    from src.sekai.base import utils
    from src.sekai.sk.matplotlib_backend import render_trace_figure

    start = datetime(2026, 1, 2, tzinfo=UTC)
    end = start + timedelta(days=2)
    spec = TraceSpec(
        score_format="scalar",
        exact_time_limits=True,
        title="Reference trace",
        start=start,
        end=end,
        series=(
            TraceSeries("score", (start, end), (10, 20), "Score", "red", 3, dashed=True),
            TraceSeries("score", (start, end), (10, 20), "Points", "blue", 3, scatter_colors=("red", "blue")),
            TraceSeries("secondary", (start, end), (100, 50), "Rank", "green", 2),
        ),
        annotations=(
            TraceAnnotation("score", start, 10, "Annotated", "red", annotation=True, outline=True),
            TraceAnnotation("secondary", end, 50, "Label", "green", outline=False),
        ),
        horizontal_lines=(TraceHorizontalLine(15, "gray", ":", 0.8, label="Forecast"),),
        score_limits=(0, 25),
        secondary_limits=(110, 0),
        secondary_kind="rank",
        score_grid=True,
    )
    converted = utils.plt_fig_to_image
    captured = {}

    def capture(figure):
        score, secondary = figure.axes
        captured["title"] = score.get_title()
        captured["labels"] = [item.get_text() for item in secondary.get_legend().get_texts()]
        captured["annotations"] = [item.get_text() for axis in figure.axes for item in axis.texts]
        captured["score_limits"] = score.get_ylim()
        captured["rank_limits"] = secondary.get_ylim()
        return converted(figure)

    monkeypatch.setattr(utils, "plt_fig_to_image", capture)
    image = render_trace_figure(spec)
    assert image.width > 500
    assert image.height > 300
    assert captured == {
        "title": "Reference trace",
        "labels": ["Score", "Points", "Forecast", "Rank"],
        "annotations": ["Annotated", "Label"],
        "score_limits": (0, 25),
        "rank_limits": (110, 0),
    }
