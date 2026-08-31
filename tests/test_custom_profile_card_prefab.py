from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import pytest

from src.sekai.profile.custom_profile.card_prefab import (
    CardAlphaMaskOp,
    CardCoverArtOp,
    CardDisplayList,
    CardFontRef,
    CardPrefabResources,
    CardRectOp,
    CardSpriteOp,
    CardSpriteRef,
    PillowCardAdapter,
    build_deck_card_display_list,
    build_empty_deck_card_display_list,
    build_full_card_display_list,
)
from src.sekai.profile.custom_profile.general_prefab import rect_transform_box


def _pattern(size: tuple[int, int], seed: int) -> Image.Image:
    image = Image.new("RGBA", size)
    for y in range(size[1]):
        for x in range(size[0]):
            alpha = 0 if (x + y + seed) % 17 == 0 else (80 + x * 11 + y * 7 + seed) % 256
            image.putpixel(
                (x, y),
                (
                    (x * 19 + seed) % 256,
                    (y * 23 + seed * 3) % 256,
                    (x * 5 + y * 13 + seed * 7) % 256,
                    alpha,
                ),
            )
    return image


def _resize_cover(
    source: Image.Image,
    target_size: tuple[float, float],
    *,
    align_x: float = 0.5,
    align_y: float = 0.5,
) -> Image.Image:
    target_w = max(1, round(target_size[0]))
    target_h = max(1, round(target_size[1]))
    scale = max(target_w / source.width, target_h / source.height)
    resized = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = round((resized.width - target_w) * align_x)
    top = round((resized.height - target_h) * align_y)
    return resized.crop((left, top, left + target_w, top + target_h))


def _paste(
    target: Image.Image,
    source: Image.Image,
    rect: tuple[float, float, float, float],
) -> None:
    left, top, right, bottom = rect
    resized = source.resize(
        (max(1, round(right - left)), max(1, round(bottom - top))),
        Image.Resampling.LANCZOS,
    )
    target.alpha_composite(resized, (round(left), round(top)))


def _fixture_resources(art_path: Path) -> CardPrefabResources:
    return CardPrefabResources(
        art_path=art_path,
        frame=CardSpriteRef("frame"),
        attribute=CardSpriteRef("attribute"),
        rarity=CardSpriteRef("rarity"),
        master_rank=CardSpriteRef("master-rank"),
        leader_label=CardSpriteRef("leader"),
    )


def _fixture_adapter(
    art_path: Path,
    art: Image.Image,
    sprites: dict[str, Image.Image],
) -> PillowCardAdapter:
    def paste_sprite(
        image: Image.Image,
        name: str,
        rect: tuple[float, float, float, float],
        *,
        resample: Image.Resampling = Image.Resampling.LANCZOS,
    ) -> bool:
        source = sprites.get(name)
        if source is None:
            return False
        left, top, right, bottom = rect
        resized = source.resize(
            (max(1, round(right - left)), max(1, round(bottom - top))),
            resample,
        )
        image.alpha_composite(resized, (round(left), round(top)))
        return True

    return PillowCardAdapter(
        lambda _size, _bold: ImageFont.load_default(),
        paste_sprite,
        lambda name: sprites.get(name),
        lambda path: art.copy() if path == art_path else None,
    )


def _legacy_full(
    size: tuple[int, int],
    art: Image.Image,
    sprites: dict[str, Image.Image],
    rarity_count: int,
) -> Image.Image:
    image = _resize_cover(art, size)
    _paste(image, sprites["frame"], (0.0, 0.0, float(size[0]), float(size[1])))
    _paste(
        image,
        sprites["attribute"],
        rect_transform_box(
            size,
            (1.0, 1.0),
            (1.0, 1.0),
            (-40.0, 0.0),
            (88.0, 92.0),
            (1.0, 1.0),
        ),
    )
    positions = (
        (
            24.2 + 0.37,
            size[1] - (17.0 + 10.75998592376709 + 55.7599983215332),
            24.2 + 0.37 + 55.7599983215332,
            size[1] - (17.0 + 10.75998592376709),
        ),
        (
            24.2 + 0.37,
            size[1] - (17.0 + 58.81999969482422 + 55.7599983215332),
            24.2 + 0.37 + 55.7599983215332,
            size[1] - (17.0 + 58.81999969482422),
        ),
        (
            24.2 + 0.37,
            size[1] - (17.0 + 106.88999938964844 + 55.7599983215332),
            24.2 + 0.37 + 55.7599983215332,
            size[1] - (17.0 + 106.88999938964844),
        ),
        (
            24.2 + 0.37,
            size[1] - (17.0 + 154.9600067138672 + 55.7599983215332),
            24.2 + 0.37 + 55.7599983215332,
            size[1] - (17.0 + 154.9600067138672),
        ),
    )
    for rect in positions[:rarity_count]:
        _paste(image, sprites["rarity"], rect)
    _paste(
        image,
        sprites["master-rank"],
        rect_transform_box(
            size,
            (1.0, 0.0),
            (1.0, 0.0),
            (-24.0, 24.0),
            (104.0, 104.0),
            (1.0, 0.0),
        ),
    )
    return image


def _legacy_deck(
    native_size: tuple[int, int],
    art_size: tuple[float, float],
    crop_align_y: float,
    render_size: tuple[int, int] | None,
    art: Image.Image,
    sprites: dict[str, Image.Image],
    *,
    attr_x: float,
    leader: bool,
    rarity_count: int,
    level: int,
) -> Image.Image:
    art_layer = _resize_cover(art, art_size)
    crop_left = max(0, round((art_layer.width - native_size[0]) * 0.5))
    crop_top = max(0, round((art_layer.height - native_size[1]) * crop_align_y))
    image = Image.new("RGBA", native_size, (0, 0, 0, 0))
    image.alpha_composite(
        art_layer.crop(
            (
                crop_left,
                crop_top,
                crop_left + native_size[0],
                crop_top + native_size[1],
            )
        )
    )

    level_rect = rect_transform_box(
        native_size,
        (0.0, 0.0),
        (1.0, 0.0),
        (0.0, 0.0),
        (0.0, 56.38999938964844),
        (0.5, 0.0),
    )
    draw = ImageDraw.Draw(image)
    draw.rectangle(tuple(round(value) for value in level_rect), fill=(38, 39, 62, 230))
    text_rect = rect_transform_box(
        (level_rect[2] - level_rect[0], level_rect[3] - level_rect[1]),
        (0.0, 0.0),
        (0.0, 1.0),
        (12.9, 0.7),
        (117.76000213623047, -9.569999694824219),
        (0.0, 0.5),
    )
    text_rect = (
        text_rect[0] + level_rect[0],
        text_rect[1] + level_rect[1],
        text_rect[2] + level_rect[0],
        text_rect[3] + level_rect[1],
    )
    draw.text(
        (text_rect[0], (text_rect[1] + text_rect[3]) / 2.0),
        f"Lv.{level}",
        font=ImageFont.load_default(),
        fill=(255, 255, 255, 255),
        anchor="lm",
    )

    _paste(image, sprites["frame"], (0.0, 0.0, float(native_size[0]), float(native_size[1])))
    _paste(
        image,
        sprites["attribute"],
        rect_transform_box(
            native_size,
            (0.0, 1.0),
            (0.0, 1.0),
            (attr_x, 0.0),
            (64.0, 68.0),
            (0.0, 1.0),
        ),
    )
    star_size = 56.0 * 0.8
    for index in range(rarity_count):
        left = 5.0 + index * 40.0
        _paste(
            image,
            sprites["rarity"],
            (
                left,
                native_size[1] - 64.0 - star_size,
                left + star_size,
                native_size[1] - 64.0,
            ),
        )
    _paste(
        image,
        sprites["master-rank"],
        rect_transform_box(
            native_size,
            (1.0, 0.0),
            (1.0, 0.0),
            (1.4, 0.8),
            (88.0 * 0.95, 88.0 * 0.95),
            (1.0, 0.0),
        ),
    )
    if leader:
        _paste(
            image,
            sprites["leader"],
            rect_transform_box(
                native_size,
                (1.0, 1.0),
                (1.0, 1.0),
                (0.0, 0.0),
                (164.0, 94.0),
                (1.0, 1.0),
            ),
        )
    return image.resize(render_size, Image.Resampling.LANCZOS) if render_size is not None else image


def test_full_display_list_covers_leader_components_without_a_mask() -> None:
    display_list = build_full_card_display_list(
        size=(940, 530),
        resources=_fixture_resources(Path("full-art.png")),
        rarity_count=4,
        show_detail=True,
    )

    assert isinstance(display_list, CardDisplayList)
    assert isinstance(display_list.ops[0], CardCoverArtOp)
    assert display_list.ops[0].blend == "src"
    assert [op.resource.name for op in display_list.ops if isinstance(op, CardSpriteOp)] == [
        "frame",
        "attribute",
        "rarity",
        "rarity",
        "rarity",
        "rarity",
        "master-rank",
    ]
    assert not any(isinstance(op, CardAlphaMaskOp) for op in display_list.ops)


def test_deck_display_list_preserves_level_src_and_only_adds_explicit_mask() -> None:
    resources = _fixture_resources(Path("deck-art.png"))
    display_list = build_deck_card_display_list(
        native_size=(330, 512),
        art_size=(330.0, 512.0),
        crop_align_y=0.0,
        resources=resources,
        rarity_count=4,
        level=60,
        leader=True,
        show_detail=True,
        attr_x=3.70001220703125,
        mask=None,
        render_size=(156, 242),
        font=CardFontRef(path="font.ttf"),
    )

    assert display_list.render_size == (156, 242)
    assert isinstance(display_list.ops[0], CardCoverArtOp)
    assert display_list.ops[0].blend == "src_over"
    assert isinstance(display_list.ops[1], CardRectOp)
    assert display_list.ops[1].blend == "src"
    assert not any(isinstance(op, CardAlphaMaskOp) for op in display_list.ops)

    masked = build_deck_card_display_list(
        native_size=(328, 520),
        art_size=(328.0, 538.2559814453125),
        crop_align_y=0.5,
        resources=resources,
        rarity_count=4,
        level=60,
        leader=False,
        show_detail=True,
        attr_x=8.0,
        mask=CardSpriteRef("tex_mask_card_s"),
    )
    assert sum(isinstance(op, CardAlphaMaskOp) for op in masked.ops) == 1


def test_missing_deck_member_placeholder_replays_byte_identically() -> None:
    display_list = build_empty_deck_card_display_list((156, 242))
    adapter = PillowCardAdapter(
        lambda _size, _bold: ImageFont.load_default(),
        lambda _image, _name, _rect, *, resample: False,
        lambda _name: None,
        lambda _path: None,
    )
    actual = adapter.render(display_list)
    expected = Image.new("RGBA", (156, 242), (0, 0, 0, 0))
    draw = ImageDraw.Draw(expected)
    draw.rounded_rectangle((0, 0, 155, 241), radius=8, fill=(226, 232, 240, 255))
    draw.rounded_rectangle(
        (0, 0, 155, 241),
        radius=8,
        outline=(170, 183, 198, 255),
        width=2,
    )

    assert actual.tobytes() == expected.tobytes()


def test_full_pillow_replay_is_byte_identical_to_the_legacy_composer() -> None:
    art_path = Path("full-art.png")
    art = _pattern((173, 109), 1)
    sprites = {
        "frame": _pattern((37, 29), 2),
        "attribute": _pattern((13, 17), 3),
        "rarity": _pattern((9, 11), 4),
        "master-rank": _pattern((15, 19), 5),
        "leader": _pattern((21, 12), 6),
    }
    resources = _fixture_resources(art_path)
    display_list = build_full_card_display_list(
        size=(188, 106),
        resources=resources,
        rarity_count=4,
        show_detail=True,
    )

    actual = _fixture_adapter(art_path, art, sprites).render(display_list)
    expected = _legacy_full((188, 106), art, sprites, 4)

    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize(
    ("native_size", "art_size", "crop_align_y", "render_size", "attr_x", "leader"),
    [
        ((330, 512), (330.0, 512.0), 0.0, (156, 242), 3.70001220703125, True),
        ((328, 520), (328.0, 538.2559814453125), 0.5, None, 8.0, False),
    ],
    ids=("general-deck", "clip-card-member"),
)
def test_deck_and_clip_pillow_replay_are_byte_identical_to_legacy(
    native_size: tuple[int, int],
    art_size: tuple[float, float],
    crop_align_y: float,
    render_size: tuple[int, int] | None,
    attr_x: float,
    leader: bool,
) -> None:
    art_path = Path("deck-art.png")
    art = _pattern((181, 277), 7)
    sprites = {
        "frame": _pattern((41, 59), 8),
        "attribute": _pattern((13, 17), 9),
        "rarity": _pattern((9, 11), 10),
        "master-rank": _pattern((15, 19), 11),
        "leader": _pattern((21, 12), 12),
    }
    resources = _fixture_resources(art_path)
    display_list = build_deck_card_display_list(
        native_size=native_size,
        art_size=art_size,
        crop_align_y=crop_align_y,
        resources=resources,
        rarity_count=4,
        level=60,
        leader=leader,
        show_detail=True,
        attr_x=attr_x,
        mask=None,
        render_size=render_size,
    )

    actual = _fixture_adapter(art_path, art, sprites).render(display_list)
    expected = _legacy_deck(
        native_size,
        art_size,
        crop_align_y,
        render_size,
        art,
        sprites,
        attr_x=attr_x,
        leader=leader,
        rarity_count=4,
        level=60,
    )

    assert actual.tobytes() == expected.tobytes()


def test_card_blend_values_are_closed() -> None:
    with pytest.raises(ValueError, match="unsupported card art blend"):
        CardCoverArtOp(Path("art.png"), (10, 10), blend="multiply")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported card rect blend"):
        CardRectOp((0, 0, 1, 1), (0, 0, 0, 0), blend="multiply")  # type: ignore[arg-type]


def test_pillow_adapter_reports_missing_required_card_art() -> None:
    adapter = PillowCardAdapter(
        lambda _size, _bold: ImageFont.load_default(),
        lambda _image, _name, _rect, *, resample: False,
        lambda _name: None,
        lambda _path: None,
    )
    display_list = CardDisplayList(
        "full",
        (8, 8),
        (CardCoverArtOp(Path("missing.png"), (8, 8)),),
    )

    with pytest.raises(FileNotFoundError, match="required card art"):
        adapter.render(display_list)


def test_pillow_adapter_uses_rounded_mask_fallback() -> None:
    adapter = PillowCardAdapter(
        lambda _size, _bold: ImageFont.load_default(),
        lambda _image, _name, _rect, *, resample: False,
        lambda _name: None,
        lambda _path: None,
    )
    image = Image.new("RGBA", (20, 20), (10, 20, 30, 255))
    masked = adapter.apply_ops(image, (CardAlphaMaskOp(CardSpriteRef("missing-mask"), 0.25),))

    assert masked.getpixel((0, 0))[3] == 0
    assert masked.getpixel((10, 10))[3] == 255


def test_pillow_adapter_rejects_missing_required_sprite() -> None:
    adapter = PillowCardAdapter(
        lambda _size, _bold: ImageFont.load_default(),
        lambda _image, _name, _rect, *, resample: False,
        lambda _name: None,
        lambda _path: None,
    )
    op = CardSpriteOp(CardSpriteRef("required", resource_policy="required"), (0, 0, 4, 4))

    with pytest.raises(FileNotFoundError, match="required card sprite"):
        adapter.apply_ops(Image.new("RGBA", (4, 4)), (op,))
