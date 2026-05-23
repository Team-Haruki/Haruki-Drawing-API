from PIL import Image

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
