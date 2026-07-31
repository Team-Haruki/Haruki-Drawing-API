from __future__ import annotations

from copy import deepcopy

from PIL import Image
import pytest

from src.sekai.base.plot import Canvas, ImageBox
from src.sekai.skia_renderer.ir_builder import IRBuilder
from src.sekai.skia_renderer.subtree import NativeSubtree, NativeSubtreeError, lower_canvas_subtree
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR


def _builder(*, default_font: str = DEFAULT_FONT, extra_fonts: dict[str, str] | None = None) -> IRBuilder:
    return IRBuilder(
        64,
        48,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=default_font,
        bold_font=DEFAULT_BOLD_FONT,
        extra_fonts=extra_fonts,
    )


def _fonts(**updates) -> dict:
    result = {
        "dir": str(FONT_DIR),
        "default": DEFAULT_FONT,
        "bold": DEFAULT_BOLD_FONT,
    }
    result.update(updates)
    return result


def _mem_refs(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith("mem:") else []
    if isinstance(value, dict):
        refs: list[str] = []
        for key, item in value.items():
            refs.extend(_mem_refs(key))
            refs.extend(_mem_refs(item))
        return refs
    if isinstance(value, (list, tuple)):
        return [ref for item in value for ref in _mem_refs(item)]
    return []


def _memory_canvas() -> Canvas:
    canvas = Canvas(4, 3)
    canvas.add_item(ImageBox(Image.new("RGBA", (4, 3), (20, 40, 80, 255))))
    return canvas


def test_lower_canvas_subtree_captures_detached_size_nodes_fonts_and_memory():
    subtree = lower_canvas_subtree(_memory_canvas())

    assert subtree.size == (4, 3)
    assert subtree.fonts["default"] == DEFAULT_FONT
    assert list(subtree.mem_images) == ["m0"]
    assert _mem_refs(subtree.nodes) == ["mem:m0"]
    assert not subtree.asset_backed

    # The returned representation is detached from the builder created during lowering.
    copied_nodes = deepcopy(subtree.nodes)
    copied_nodes[0]["path"] = "changed"
    assert _mem_refs(subtree.nodes) == ["mem:m0"]


def test_splice_namespaces_all_nested_memory_references_and_merges_fonts():
    subtree = NativeSubtree(
        size=(8, 8),
        nodes=(
            {
                "type": "Group",
                "offset": [0, 0],
                "size": [8, 8],
                "mask": "mem:m0",
                "children": [
                    {"type": "Image", "path": "mem:m1"},
                    {"type": "SdfQuad", "field": "mem:m0"},
                ],
            },
        ),
        fonts=_fonts(heavy="Heavy", extra={"honor": "HonorFont.ttf"}),
        mem_images={"m0": b"mask", "m1": (1, 1, b"\0\0\0\0")},
        assets_base_dir=str(ASSETS_BASE_DIR),
    )
    parent = _builder()
    sink = {"existing": b"keep"}

    key_map = subtree.splice_into(parent, sink, namespace="honor.0")

    assert key_map == {"m0": "honor.0:m0", "m1": "honor.0:m1"}
    assert sink == {
        "existing": b"keep",
        "honor.0:m0": b"mask",
        "honor.0:m1": (1, 1, b"\0\0\0\0"),
    }
    assert sorted(_mem_refs(parent.build()["root"]["children"])) == [
        "mem:honor.0:m0",
        "mem:honor.0:m0",
        "mem:honor.0:m1",
    ]
    assert parent.build()["fonts"]["heavy"] == "Heavy"
    assert parent.build()["fonts"]["extra"] == {"honor": "HonorFont.ttf"}
    # Splicing rewrites a deep copy, never the reusable source subtree.
    assert sorted(_mem_refs(subtree.nodes)) == ["mem:m0", "mem:m0", "mem:m1"]


def test_asset_backed_requirement_rejects_memory_during_lower_and_splice():
    with pytest.raises(NativeSubtreeError, match="asset-backed subtree required"):
        lower_canvas_subtree(_memory_canvas(), require_asset_backed=True)

    subtree = lower_canvas_subtree(_memory_canvas())
    parent = _builder()
    sink: dict[str, object] = {}
    before = deepcopy(parent.build())
    with pytest.raises(NativeSubtreeError, match="asset-backed subtree required"):
        subtree.splice_into(parent, sink, namespace="honor", require_asset_backed=True)
    assert parent.build() == before
    assert sink == {}


@pytest.mark.parametrize(
    ("subtree", "sink", "message"),
    [
        (
            NativeSubtree(
                size=(4, 4),
                nodes=({"type": "Image", "path": "mem:missing"},),
                fonts=_fonts(),
                mem_images={},
                assets_base_dir=str(ASSETS_BASE_DIR),
            ),
            {},
            "references missing memory image",
        ),
        (
            NativeSubtree(
                size=(4, 4),
                nodes=({"type": "Image", "path": "mem:m0"},),
                fonts=_fonts(),
                mem_images={"m0": b"image"},
                assets_base_dir=str(ASSETS_BASE_DIR),
            ),
            {"taken:m0": b"existing"},
            "memory namespace collision",
        ),
    ],
)
def test_failed_memory_preflight_is_atomic(subtree, sink, message):
    parent = _builder()
    before_parent = deepcopy(parent.build())
    before_sink = deepcopy(sink)

    with pytest.raises(NativeSubtreeError, match=message):
        subtree.splice_into(parent, sink, namespace="taken")

    assert parent.build() == before_parent
    assert sink == before_sink


@pytest.mark.parametrize(
    "fonts",
    [
        _fonts(default="DifferentDefault"),
        _fonts(extra={"shared": "Subtree.ttf"}),
    ],
)
def test_font_conflicts_are_detected_before_parent_or_sink_mutation(fonts):
    parent = _builder(extra_fonts={"shared": "Parent.ttf"})
    subtree = NativeSubtree(
        size=(4, 4),
        nodes=({"type": "Image", "path": "mem:m0"},),
        fonts=fonts,
        mem_images={"m0": b"image"},
        assets_base_dir=str(ASSETS_BASE_DIR),
    )
    sink: dict[str, object] = {}
    before_parent = deepcopy(parent.build())

    with pytest.raises(NativeSubtreeError, match="font"):
        subtree.splice_into(parent, sink, namespace="honor")

    assert parent.build() == before_parent
    assert sink == {}


def test_asset_root_conflict_and_invalid_namespace_are_atomic():
    subtree = NativeSubtree(
        size=(4, 4),
        nodes=(),
        fonts=_fonts(),
        mem_images={},
        assets_base_dir="/different-assets",
    )
    parent = _builder()
    before = deepcopy(parent.build())
    sink: dict[str, object] = {}

    with pytest.raises(NativeSubtreeError, match="namespace"):
        subtree.splice_into(parent, sink, namespace="bad:namespace")
    assert parent.build() == before
    assert sink == {}

    with pytest.raises(NativeSubtreeError, match="asset root conflicts"):
        subtree.splice_into(parent, sink, namespace="valid")
    assert parent.build() == before
    assert sink == {}
