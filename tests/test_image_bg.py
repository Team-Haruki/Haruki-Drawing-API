from __future__ import annotations

from PIL import Image
import pytest

import src.sekai.base.painter as painter_module
from src.sekai.base.painter import Painter, _iter_image_bg_placements
from src.sekai.skia_renderer.ir_painter import IRPainter, SkiaUnsupported


@pytest.mark.parametrize(
    ("mode", "align", "expected"),
    [
        ("fit", "br", [((-1, 0), (9, 6), (3.0, 3.0), True)]),
        ("fill", "c", [((0, 0), (8, 6), (8 / 3, 3.0), True)]),
        ("fixed", "br", [((5, 4), (3, 2), (1.0, 1.0), False)]),
        (
            "repeat",
            "c",
            [
                ((0, 0), (3, 2), (1.0, 1.0), False),
                ((3, 0), (3, 2), (1.0, 1.0), False),
                ((6, 0), (3, 2), (1.0, 1.0), False),
                ((0, 2), (3, 2), (1.0, 1.0), False),
                ((3, 2), (3, 2), (1.0, 1.0), False),
                ((6, 2), (3, 2), (1.0, 1.0), False),
                ((0, 4), (3, 2), (1.0, 1.0), False),
                ((3, 4), (3, 2), (1.0, 1.0), False),
                ((6, 4), (3, 2), (1.0, 1.0), False),
            ],
        ),
    ],
)
def test_image_bg_placements_cover_all_modes(mode, align, expected) -> None:
    assert list(_iter_image_bg_placements((8, 6), (3, 2), align, mode)) == expected


def test_painter_image_bg_handles_effects_lazy_sources_and_invalid_mode(monkeypatch) -> None:
    source = Image.new("RGBA", (3, 2), (200, 100, 50, 255))
    sentinel = object()
    monkeypatch.setattr(
        painter_module, "resolve_image_source_sync", lambda image: source if image is sentinel else image
    )

    painter = Painter(img=Image.new("RGBA", (8, 6), (0, 0, 0, 0)))
    painter._impl_image_bg(sentinel, align="br", mode="fit", blur=True, fade=0.25)
    assert painter.img.getbbox() == (0, 0, 8, 6)
    assert painter.img.getpixel((4, 3))[0] < 200

    repeated = Painter(img=Image.new("RGBA", (8, 6), (0, 0, 0, 0)))
    repeated._impl_image_bg(source, mode="repeat", fade=0)
    assert repeated.img.getbbox() == (0, 0, 8, 6)

    with pytest.raises(ValueError, match="unsupported image background mode"):
        Painter(img=Image.new("RGBA", (8, 6), (0, 0, 0, 0)))._impl_image_bg(source, mode="unknown")


@pytest.mark.parametrize("mode", ["fit", "fill", "fixed", "repeat"])
def test_ir_painter_image_bg_emits_shared_placements(mode) -> None:
    source = Image.new("RGBA", (3, 2), (200, 100, 50, 255))
    painter = IRPainter(
        (8, 6),
        assets_base_dir="/assets",
        font_dir="/fonts",
        default_font="Regular",
        bold_font="Bold",
    )
    painter.image_bg(source, align="br", mode=mode, blur=True, fade=0.25)

    nodes = painter.builder.build()["root"]["children"]
    placements = list(_iter_image_bg_placements((8, 6), (3, 2), "br", mode))
    assert [(node["pos"], node["size"]) for node in nodes] == [
        ([float(x), float(y)], [float(w), float(h)]) for (x, y), (w, h), _, _ in placements
    ]
    assert all(node["sampling"] == "catmull_rom" for node in nodes)
    assert all(node["tint"]["color"] == [191, 191, 191, 255] for node in nodes)
    assert [node["blur_sigma"] for node in nodes] == [
        [3.0 * scale_x, 3.0 * scale_y] for _, _, (scale_x, scale_y), _ in placements
    ]


def test_ir_painter_image_bg_supports_plain_images_and_rejects_unknown_mode() -> None:
    source = Image.new("RGBA", (3, 2), (200, 100, 50, 255))
    painter = IRPainter(
        (8, 6),
        assets_base_dir="/assets",
        font_dir="/fonts",
        default_font="Regular",
        bold_font="Bold",
    )
    painter.image_bg(source, mode="fixed", blur=False, fade=0)
    node = painter.builder.build()["root"]["children"][0]
    assert "tint" not in node
    assert "blur_sigma" not in node

    with pytest.raises(SkiaUnsupported, match="unsupported image background mode"):
        painter.image_bg(source, mode="unknown")
