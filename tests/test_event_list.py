from PIL import Image

from src.sekai.base.painter import DEFAULT_FONT
from src.sekai.base.plot import Grid, TextBox, TextStyle, VSplit
from src.sekai.event import drawer as event_drawer
from src.sekai.profile.drawer import CardFullThumbnailBox, CardFullThumbnailLayers
from src.sekai.profile.model import CardFullThumbnailRequest


def _layers(card_id: int) -> CardFullThumbnailLayers:
    request = CardFullThumbnailRequest(
        card_id=card_id,
        card_thumbnail_path="card.png",
        rare="rarity_4",
        frame_img_path="frame.png",
        attr_img_path="attr.png",
        rare_img_path="rare.png",
        train_rank=None,
    )
    image = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    return CardFullThumbnailLayers(rqd=request, base=image, rare=image)


def test_event_list_card_cell_places_card_id_below_thumbnail():
    with Grid(col_count=3) as grid:
        event_drawer._add_event_list_card_cell(
            _layers(1234),
            TextStyle(font=DEFAULT_FONT, size=7, color=(70, 70, 70)),
        )

    assert len(grid.items) == 1
    cell = grid.items[0]
    assert isinstance(cell, VSplit)
    assert len(cell.items) == 2
    assert isinstance(cell.items[0], CardFullThumbnailBox)
    assert isinstance(cell.items[1], TextBox)
    assert cell.items[1].text == "#1234"
    assert cell.items[1].w == event_drawer._EVENT_LIST_CARD_ID_WIDTH
