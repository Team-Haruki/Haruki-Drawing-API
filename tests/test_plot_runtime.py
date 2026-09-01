from __future__ import annotations

from PIL import Image, ImageFont
import pytest

from src.sekai.base import plot
from src.sekai.base.painter import ImageTint, Painter


class _RecordingPainter:
    def __init__(self, size=(100, 80)) -> None:
        self.size = size
        self.w, self.h = size
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


class _SizedWidget(plot.Widget):
    def __init__(self, size=(10, 8)) -> None:
        super().__init__()
        self.content_size = size
        self.draw_calls = 0

    def _get_content_size(self):
        return self.content_size

    def _draw_content(self, _p):
        self.draw_calls += 1


def _image(size=(20, 10)):
    return Image.new("RGBA", size, (10, 20, 30, 255))


def test_background_protocols_draw_every_backend_primitive(tmp_path) -> None:
    painter = _RecordingPainter()
    with pytest.raises(NotImplementedError):
        plot.WidgetBg().draw(painter)
    plot.FillBg((1, 2, 3, 4), (5, 6, 7, 8), 2).draw(painter)
    plot.RoundRectBg((1, 2, 3, 4), 5).draw(painter)
    plot.RoundRectBg((1, 2, 3, 4), 5, blur_glass=True, blur_glass_kwargs={"blur": 3}).draw(painter)
    plot.ImageBg(_image(), align="br", mode="fixed", blur=True, fade=0.2).draw(painter)
    plot.RandomTriangleBg(True, 0.5, 0.2).draw(painter)
    assert [name for name, _args, _kwargs in painter.calls] == [
        "rect",
        "roundrect",
        "blurglass_roundrect",
        "image_bg",
        "draw_random_triangle_bg",
    ]

    path = tmp_path / "image.png"
    _image().save(path)
    assert plot.ImageBg(str(path)).img.size == (20, 10)
    with pytest.raises(AssertionError):
        plot.ImageBg(_image(), align="bad")
    with pytest.raises(AssertionError):
        plot.ImageBg(_image(), mode="bad")


def test_widget_context_setters_size_alignment_and_draw_anchors() -> None:
    assert plot.Widget.get_current_widget_stack() is None
    with plot.Frame() as parent:
        child = plot.Spacer(2, 3)
        assert plot.Widget.get_current_widget() is parent
    assert child.parent is parent
    assert plot.Widget.get_current_widget() is None

    widget = _SizedWidget((10, 8))
    assert widget.set_parent(parent) is widget
    assert widget.set_content_align("br").get_content_align() == "br"
    assert widget.set_margin((2, 3)).set_padding((4, 5)).set_offset((30, 40)).set_offset_anchor("c") is widget
    assert widget.set_bg(plot.FillBg((0, 0, 0, 0))).set_omit_parent_bg(True).set_allow_draw_outside(True) is widget
    assert widget.set_size((20, 20)).set_w(22).set_h(24)._get_self_size() == (26, 30)
    assert widget._get_content_pos() == (4, 6)
    called: list[object] = []
    widget.add_draw_func(lambda item, _p: called.append(item)).clear_draw_funcs()
    widget.add_draw_func(lambda item, _p: called.append(item))
    painter = Painter(size=widget._get_self_size())
    widget.draw(painter)
    assert called == [widget]
    assert widget.draw_calls == 1
    with pytest.raises(AssertionError, match="draw once"):
        widget.draw(painter)

    with pytest.raises(ValueError, match="Invalid align"):
        _SizedWidget().set_content_align("bad")
    with pytest.raises(ValueError, match="Invalid anchor"):
        _SizedWidget().set_offset_anchor("bad")
    with pytest.raises(ValueError, match="Content size is too large"):
        _SizedWidget((20, 20)).set_size((5, 5))._get_self_size()


def test_frame_split_and_grid_items_cover_reparent_expand_ratios_and_empty_draws() -> None:
    old = plot.Spacer(2, 2)
    new = plot.Spacer(3, 4)
    frame = plot.Frame([old]).set_content_align("br")
    frame.set_items([new])
    assert old.parent is None
    assert new.parent is frame
    frame.add_item(plot.Spacer(1, 1))
    assert frame._get_content_size() == (3, 4)
    frame._draw_content(Painter(size=(3, 4)))

    for split_cls, extent in ((plot.HSplit, 30), (plot.VSplit, 30)):
        first, second = plot.Spacer(4, 5), plot.Spacer(6, 3)
        split = split_cls([first], ratios=[1], sep=2, item_align="br")
        split.set_items([first, second]).set_ratios([1, 2]).set_item_size_mode("expand")
        split.set_item_align("tl").set_item_bg(plot.FillBg((0, 0, 0, 0)))
        if split_cls is plot.HSplit:
            split.set_w(extent)
        else:
            split.set_h(extent)
        sizes = split._get_item_sizes()
        assert len(sizes) == 2
        split._draw_content(Painter(size=split._get_content_size()))
        assert split.set_sep(3).sep == 3
        assert split.add_item(plot.Spacer()).items[-1].parent is split
        with pytest.raises(ValueError, match="Invalid align"):
            split.set_item_align("bad")
        with pytest.raises(AssertionError):
            split.set_item_size_mode("bad")

        empty = split_cls()
        assert empty._get_content_size() == (0, 0)
        empty._draw_content(Painter(size=(1, 1)))

    with pytest.raises(AssertionError):
        plot.HSplit(item_size_mode="bad")
    with pytest.raises(ValueError, match="Invalid align"):
        plot.VSplit(item_align="bad")

    items = [plot.Spacer(2, 3), plot.Spacer(4, 5), plot.Spacer(1, 1)]
    grid = plot.Grid(items=items, col_count=2, item_align="br").set_sep(2, 3)
    assert grid._get_grid_rc_and_size()[0] == (2, 2)
    grid._draw_content(Painter(size=grid._get_content_size()))
    grid.set_items([plot.Spacer(1, 1)]).set_item_align("tl").set_item_bg(plot.FillBg((0, 0, 0, 0)))
    assert grid.set_row_count(1).set_col_count(None).set_item_size_mode("fixed") is grid
    with pytest.raises(AssertionError):
        plot.Grid(row_count=1, col_count=1)


def test_text_and_colored_text_setters_draw_alignment_shadow_and_offsets(monkeypatch) -> None:
    font = ImageFont.load_default()
    monkeypatch.setattr(plot, "get_font", lambda *_args: font)
    monkeypatch.setattr(plot, "get_text_size", lambda _font, text: (len(text) * 5, 8))
    style = plot.TextStyle(font="font", size=10, color=(1, 2, 3, 255), use_shadow=True, shadow_offset=(1, 2))
    text = plot.TextBox("ab\ncd", style, line_count=2).set_content_align("br")
    assert text.set_text("ab\ncd").set_style(style).set_line_count(2).set_line_sep(3) is text
    assert text.set_wrap(False) is text
    text.set_overflow("clip")
    assert text.set_text_offset((2, 3)) is text
    p = _RecordingPainter((30, 30))
    text._draw_content(p)
    assert [name for name, _args, _kwargs in p.calls].count("text") == 4

    colored = plot.ColoredTextBox("a<#ff0000>b</#>c", style, line_count=2).set_content_align("c")
    assert colored.set_text(colored.text).set_style(style).set_line_count(2).set_line_sep(3) is colored
    assert colored.set_wrap(True) is colored
    assert colored.set_overflow("shrink").set_text_offset((1, 2)) is colored
    assert colored._get_render_color(None) == style.color
    assert colored._get_render_color((4, 5, 6)) == (4, 5, 6, 255)
    p = _RecordingPainter((50, 30))
    colored._draw_content(p)
    assert any(name == "text" for name, _args, _kwargs in p.calls)
    with pytest.raises(AssertionError):
        colored.set_overflow("bad")


def test_image_canvas_boxes_and_spacer_cover_size_modes_sources_and_draw_paths(tmp_path) -> None:
    image = _image((20, 10))
    original = plot.ImageBox(image)
    assert original._get_content_size() == (20, 10)
    fit = plot.ImageBox(image, size=(10, None), image_size_mode="fit")
    assert fit._get_content_size() == (10, 5)
    fill = plot.ImageBox(image, size=(10, 10), image_size_mode="fill", source_rect=(2, 1, 12, 6))
    assert fill._source_size() == (10, 5)
    assert fill._get_content_size() == (10, 10)
    one_bound_fill = plot.ImageBox(image, size=(None, 10), image_size_mode="fill")
    assert one_bound_fill._get_content_size() == (1_000_000, 500_000)
    assert plot.ImageBox._scaled_size((20, 10), (5, 20), fit=True) == (5, 2)

    p = _RecordingPainter((10, 10))
    fill._draw_content(p)
    alpha = plot.ImageBox(
        image,
        size=(10, 10),
        use_alpha_blend=True,
        shadow=True,
        sampling="linear",
        tint=ImageTint((255, 0, 0)),
    )
    alpha._draw_content(p)
    assert [name for name, _args, _kwargs in p.calls] == ["paste", "paste_with_alpha_blend"]
    with pytest.raises(AssertionError):
        original.set_image_size_mode("bad")

    path = tmp_path / "image.png"
    image.save(path)
    assert original.set_image(str(path)).image.size == (20, 10)
    assert original.set_alpha_adjust(0.5).set_use_alpha_blend(True).set_shadow(True) is original

    canvas = plot.Canvas().set_size((20, 10))
    canvas_box = plot.CanvasImageBox(canvas, size=(10, None), shadow=True, cache_key="k")
    assert canvas_box.natural_size == (20, 10)
    assert canvas_box._get_content_size() == (10, 5)
    canvas_box._draw_content(p)
    assert p.calls[-1][0] == "paste_canvas"
    assert plot.CanvasImageBox(canvas, size=(10, 10), image_size_mode="fill")._get_content_size() == (10, 10)
    with pytest.raises(ValueError, match="unsupported canvas"):
        plot.CanvasImageBox(canvas, image_size_mode="bad")
    spacer = plot.Spacer(3, 4).set_padding(1)
    assert spacer._get_content_size() == (1, 2)
