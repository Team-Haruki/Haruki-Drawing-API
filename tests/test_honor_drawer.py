from pathlib import Path

from PIL import Image, ImageDraw

from src.sekai.base.utils import AssetImageRef
from src.sekai.honor.drawer import compose_full_honor_image_from_loaded_assets
from src.sekai.honor.model import HonorRequest
from src.sekai.honor.widget import build_honor_badge_canvas
from src.sekai.skia_renderer.canvas import build_canvas_ir


def test_event_honor_draws_scroll_level() -> None:
    base = Image.new("RGBA", (180, 80), (0, 0, 0, 0))
    scroll = Image.new("RGBA", (90, 24), (255, 0, 0, 255))
    request = HonorRequest(
        honor_type="normal",
        group_type="event",
        honor_rarity="middle",
        honor_level=3,
        fc_or_ap_level="3",
        is_main_honor=False,
    )

    image = compose_full_honor_image_from_loaded_assets(
        request,
        {
            "honor_img": base,
            "scroll_img": scroll,
        },
    )

    assert image is not None
    assert image.getpixel((38, 4)) == (255, 0, 0, 255)
    text_area = image.crop((37, 46, 137, 74))
    pixels = text_area.load()
    assert any(
        pixels[x, y][0] > 220 and pixels[x, y][1] > 220 and pixels[x, y][2] > 220 and pixels[x, y][3] > 0
        for y in range(text_area.height)
        for x in range(text_area.width)
    )


def _bonds_request(*, main: bool = False) -> HonorRequest:
    return HonorRequest(
        honor_type="bonds",
        honor_rarity="middle",
        honor_level=0,
        is_main_honor=main,
        chara_id="1",
        chara_id2="2",
    )


def _marker_icon(face_x: int, color: tuple[int, int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (160, 136), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((face_x - 2, 0, face_x + 2, 135), fill=color)
    return img


def _marker_x_positions(img: Image.Image, color: tuple[int, int, int]) -> list[int]:
    pixels = img.load()
    positions: list[int] = []
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = pixels[x, y]
            if a > 0 and r >= color[0] and g >= color[1] and b >= color[2]:
                positions.append(x)
    return positions


def test_bonds_honor_background_uses_left_and_right_halves() -> None:
    left_bg = Image.new("RGBA", (180, 80), (10, 20, 200, 255))
    right_bg = Image.new("RGBA", (180, 80), (220, 210, 20, 255))

    image = compose_full_honor_image_from_loaded_assets(
        _bonds_request(),
        {
            "bonds_bg": left_bg,
            "bonds_bg2": right_bg,
        },
    )

    assert image is not None
    assert image.getpixel((89, 40)) == (10, 20, 200, 255)
    assert image.getpixel((92, 40)) == (10, 20, 200, 255)
    assert image.getpixel((93, 40)) == (220, 210, 20, 255)


def test_bonds_honor_places_faces_in_each_half_for_main_and_sub_slots() -> None:
    for main, expected_left, expected_right in ((True, 70, 310), (False, 60, 120)):
        size = (380, 80) if main else (180, 80)
        image = compose_full_honor_image_from_loaded_assets(
            _bonds_request(main=main),
            {
                "bonds_bg": Image.new("RGBA", size, (20, 20, 20, 255)),
                "bonds_bg2": Image.new("RGBA", size, (30, 30, 30, 255)),
                "chara_icon_1": _marker_icon(80, (255, 0, 0, 255)),
                "chara_icon_2": _marker_icon(80, (0, 255, 0, 255)),
            },
        )

        assert image is not None
        red_xs = _marker_x_positions(image, (240, 0, 0))
        green_xs = _marker_x_positions(image, (0, 240, 0))
        assert red_xs
        assert green_xs
        assert min(red_xs) <= expected_left <= max(red_xs)
        assert min(green_xs) <= expected_right <= max(green_xs)


def test_bonds_honor_mask_defines_the_badge_silhouette() -> None:
    """The mask is what ``img.putalpha(mask.split()[3])`` used to do, now expressed as
    Painter.push_mask (alpha multiply / Skia's DstIn). It runs over an OPAQUE background, so the
    badge's alpha must come out as the mask's alpha exactly — including under the chara icons,
    whose anti-aliased edges must not eat into it."""
    mask = Image.new("RGBA", (380, 80), (0, 0, 0, 0))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 379, 79), radius=16, fill=(255, 255, 255, 255))

    image = compose_full_honor_image_from_loaded_assets(
        _bonds_request(main=True),
        {
            "bonds_bg": Image.new("RGBA", (380, 80), (10, 20, 200, 255)),
            "bonds_bg2": Image.new("RGBA", (380, 80), (220, 210, 20, 255)),
            "chara_icon_1": _marker_icon(80, (255, 0, 0, 255)),
            "chara_icon_2": _marker_icon(80, (0, 255, 0, 255)),
            "mask_img": mask,
        },
    )

    assert image is not None
    assert image.getpixel((0, 0))[3] == 0  # corner cut away by the mask
    assert image.getpixel((190, 40))[3] == 255  # centre kept
    # the icons sit at the bottom of the badge; their edges must not punch holes in it
    assert image.getpixel((70, 79))[3] == 255
    assert image.getpixel((310, 79))[3] == 255


def _asset_ref(path: Path) -> AssetImageRef:
    stat = path.stat()
    with Image.open(path) as image:
        return AssetImageRef(
            path=path,
            size=image.size,
            mode=image.mode,
            mtime_ns=stat.st_mtime_ns,
            file_size=stat.st_size,
        )


def _walk_ir(node):
    yield node
    for child in node.get("children", ()):
        yield from _walk_ir(child)


def test_bonds_honor_lazy_sources_lower_to_asset_only_resize_then_clip_ir(tmp_path, monkeypatch) -> None:
    import src.sekai.skia_renderer.canvas as canvas_mod

    assets = {
        "bonds_bg": Image.new("RGBA", (180, 80), (20, 40, 160, 255)),
        "bonds_bg2": Image.new("RGBA", (180, 80), (180, 120, 20, 255)),
        "chara_icon_1": _marker_icon(80, (255, 0, 0, 255)),
        "chara_icon_2": _marker_icon(80, (0, 255, 0, 255)),
        "mask_img": Image.new("RGBA", (180, 80), (255, 255, 255, 255)),
        "frame_img": Image.new("RGBA", (180, 80), (255, 255, 255, 128)),
        "lv_img": Image.new("RGBA", (14, 14), (255, 255, 255, 180)),
    }
    refs = {}
    for key, image in assets.items():
        path = tmp_path / f"{key}.png"
        image.save(path)
        refs[key] = _asset_ref(path)

    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)
    request = HonorRequest(
        honor_type="bonds",
        honor_rarity="middle",
        honor_level=2,
        is_main_honor=False,
        chara_id="1",
        chara_id2="2",
    )
    canvas = build_honor_badge_canvas(request, refs)
    assert canvas is not None

    builder, mem_images = build_canvas_ir(canvas, export_format="png")
    scene = builder.build()
    nodes = list(_walk_ir(scene["root"]))
    images = [node for node in nodes if node.get("type") == "Image"]

    assert mem_images == {}
    assert images
    assert all(not node["path"].startswith("mem:") for node in images)
    assert all("source_rect" not in node for node in images)
    assert any(node.get("blend") == "paste_lerp" for node in images)
    assert sum(node.get("clip", {}).get("kind") == "rect" for node in nodes if node.get("type") == "Group") >= 4
    masked_groups = [node for node in nodes if node.get("type") == "Group" and node.get("mask")]
    assert len(masked_groups) == 1
    assert [child["type"] for child in masked_groups[0]["children"]] == ["UnitySubscene"]
