from PIL import Image, ImageDraw

from src.sekai.honor.drawer import compose_full_honor_image_from_loaded_assets
from src.sekai.honor.model import HonorRequest


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
