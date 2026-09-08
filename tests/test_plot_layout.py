from __future__ import annotations

from PIL import Image
import pytest

from src.sekai.base import plot
from src.sekai.base.plot import ColoredTextBox, Flow, ImageBg, ImageBox, Spacer, TextBox
from src.sekai.base.utils import AssetImageRef


def _spacers() -> list[Spacer]:
    return [Spacer(10, 20), Spacer(20, 10), Spacer(15, 8)]


def test_flow_horizontal_geometry_preserves_group_and_item_alignment():
    flow = Flow(_spacers(), item_align="b", h_sep=2, v_sep=3)

    total_size, positions = flow._calc_total_size_and_item_pos_by_layout([[0, 1], [2]])

    assert total_size == (32, 31)
    assert positions == [(0, 0), (12, 10), (8, 23)]
    assert flow._calc_total_size_by_layout_fast([[0, 1], [2]]) == total_size


def test_flow_vertical_geometry_preserves_group_and_item_alignment():
    flow = Flow(_spacers(), item_align="r", h_sep=2, v_sep=3, vertical=True)

    total_size, positions = flow._calc_total_size_and_item_pos_by_layout([[0, 1], [2]])

    assert total_size == (37, 33)
    assert positions == [(10, 0), (0, 23), (22, 12)]
    assert flow._calc_total_size_by_layout_fast([[0, 1], [2]]) == total_size


@pytest.mark.parametrize(
    ("alignment", "expected"),
    [("l", 0), ("t", 0), ("r", 7), ("b", 7), ("c", 3)],
)
def test_flow_alignment_offsets(alignment: str, expected: int):
    assert Flow._alignment_offset(10, 3, alignment) == expected


def test_flow_balances_fixed_rows_and_columns():
    horizontal = Flow([Spacer(10, 5), Spacer(20, 5), Spacer(30, 5), Spacer(40, 5)])
    vertical = Flow(
        [Spacer(5, 10), Spacer(5, 20), Spacer(5, 30), Spacer(5, 40)],
        vertical=True,
    )

    assert horizontal._calc_item_layout(row_count=2) == [[0, 1, 2], [3]]
    assert vertical._calc_item_layout(col_count=2) == [[0, 1, 2], [3]]


@pytest.mark.parametrize("vertical", [False, True])
def test_flow_selects_closest_aspect_ratio(vertical: bool):
    flow = Flow([Spacer(10, 10) for _ in range(4)], h_sep=0, v_sep=0, vertical=vertical)

    assert flow._calc_item_layout(aspect_ratio=1.0) == [[0, 1], [2, 3]]


def test_flow_wraps_to_width_and_height_limits():
    horizontal = Flow([Spacer(10, 10) for _ in range(3)], h_sep=2).set_w(22)
    vertical = Flow([Spacer(10, 10) for _ in range(3)], v_sep=2, vertical=True).set_h(22)

    assert horizontal._calc_item_layout() == [[0, 1], [2]]
    assert vertical._calc_item_layout() == [[0, 1], [2]]


def test_flow_empty_groups_follow_keep_empty_setting():
    compact = Flow(row_count=3)
    retained = Flow(row_count=3, keep_empty_row_or_col=True)

    assert compact._calc_item_layout(row_count=3) == []
    assert retained._calc_item_layout(row_count=3) == [[], [], []]
    assert compact._calc_total_size_by_layout_fast([]) == (0, 0)
    assert compact._calc_total_size_and_item_pos_by_layout([]) == ((0, 0), [])


def test_flow_rejects_incompatible_or_missing_layout_inputs():
    horizontal = Flow([Spacer(10, 10)])
    vertical = Flow([Spacer(10, 10)], vertical=True)

    with pytest.raises(AssertionError, match="Column count only works"):
        horizontal._calc_item_layout(col_count=1)
    with pytest.raises(AssertionError, match="Row count only works"):
        vertical._calc_item_layout(row_count=1)
    with pytest.raises(AssertionError, match="Cannot specify both"):
        horizontal._calc_item_layout(row_count=1, col_count=1)
    with pytest.raises(ValueError, match="must be specified"):
        horizontal._calc_item_layout()


@pytest.fixture
def unit_text_metrics(monkeypatch):
    monkeypatch.setattr(plot.TextBox, "_get_pil_font", lambda _self: object())
    monkeypatch.setattr(plot.ColoredTextBox, "_get_pil_font", lambda _self: object())
    monkeypatch.setattr(plot, "get_text_size", lambda _font, text: (len(text), 1))


def test_text_box_wraps_and_applies_suffix_only_to_the_final_line(unit_text_metrics):
    text = TextBox("abcdef", line_count=2, wrap=True, overflow="shrink").set_padding(0).set_w(4)

    assert text._get_lines() == ["abcd", "e..."]


@pytest.mark.parametrize(
    ("wrap", "overflow", "expected"),
    [(False, "shrink", ["a..."]), (False, "clip", ["abcd"]), (True, "clip", ["abcd"])],
)
def test_text_box_clips_each_overflow_mode(unit_text_metrics, wrap: bool, overflow: str, expected: list[str]):
    text = TextBox("abcdef", line_count=1, wrap=wrap, overflow=overflow).set_padding(0).set_w(4)

    assert text._get_lines() == expected


def test_text_box_without_width_preserves_explicit_lines(unit_text_metrics):
    text = TextBox("first\nsecond\nthird", line_count=2)

    assert text._get_lines() == ["first", "second"]


def test_colored_text_wrap_keeps_color_segments(unit_text_metrics):
    text = ColoredTextBox("ab<#f00>cd", line_count=2, wrap=True).set_padding(0).set_w(2)

    assert text._get_lines() == [
        [{"text": "ab", "color": None}],
        [{"text": "cd", "color": (255, 0, 0)}],
    ]


def test_colored_text_shrink_keeps_suffix_on_last_visible_color(unit_text_metrics):
    text = ColoredTextBox("a<#f00>bcdef", line_count=1, wrap=True, overflow="shrink").set_padding(0).set_w(4)

    assert text._get_lines() == [[{"text": "a...", "color": None}]]


def test_colored_text_stops_on_line_limit_or_disabled_wrap(unit_text_metrics):
    newline_limited = ColoredTextBox("a\nb", line_count=1, overflow="clip")
    no_wrap = ColoredTextBox("abc", line_count=2, wrap=False, overflow="clip").set_padding(0).set_w(2)

    assert newline_limited._get_lines() == [[{"text": "a", "color": None}]]
    assert no_wrap._get_lines() == [[{"text": "ab", "color": None}]]


def test_image_box_content_sizes_cover_original_fit_fill_and_crop():
    image = Image.new("RGBA", (100, 50))

    assert ImageBox(image)._get_content_size() == (100, 50)
    assert ImageBox(image, image_size_mode="fit", size=(40, 40))._get_content_size() == (40, 20)
    assert ImageBox(image, image_size_mode="fill", size=(40, 40))._get_content_size() == (40, 40)
    assert ImageBox(image, source_rect=(10, 5, 70, 25))._get_content_size() == (60, 20)

    unsupported = ImageBox(image)
    unsupported.image_size_mode = "unsupported"
    assert unsupported._get_content_size() is None


def _asset_ref(tmp_path, name: str, size: tuple[int, int]) -> AssetImageRef:
    path = tmp_path / name
    Image.new("RGBA", size, (1, 2, 3, 255)).save(path)
    stat = path.stat()
    return AssetImageRef(path=path, size=size, mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)


def test_collect_asset_refs_traverses_images_backgrounds_extras_and_children(tmp_path):
    image_ref = _asset_ref(tmp_path, "image.png", (40, 20))
    background_ref = _asset_ref(tmp_path, "background.png", (8, 8))
    item_background_ref = _asset_ref(tmp_path, "item-background.png", (9, 9))
    extra_ref = _asset_ref(tmp_path, "extra.png", (10, 10))

    root = Flow([ImageBox(image_ref, image_size_mode="fit", size=(20, 20))], row_count=1)
    root.set_bg(ImageBg(background_ref))
    root.item_bg = ImageBg(item_background_ref)
    root.prefetch_image_sources = [extra_ref, image_ref, object()]
    refs: dict = {}

    plot._collect_asset_refs(root, refs)

    assert {ref.path.name for ref, _, _ in refs.values()} == {
        "image.png",
        "background.png",
        "item-background.png",
        "extra.png",
    }
    assert {hint for ref, hint, _ in refs.values() if ref is image_ref} == {None, (20, 10)}
