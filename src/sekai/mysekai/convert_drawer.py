from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.asset_key import candidates
from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.paint_types import Color
from src.sekai.base.plot import Canvas, Frame, Grid, HSplit, ImageBox, RoundRectBg, Spacer, TextBox, TextStyle, VSplit
from src.sekai.base.text_layout import ink_centered_text_offset_y
from src.sekai.base.timezone import caller_label
from src.sekai.base.utils import ImageSource, get_asset_image_refs
from src.sekai.base.vector import VectorPath
from src.sekai.mysekai.convert_model import MysekaiConvertRecipe, MysekaiConvertRecipesRequest, MysekaiConvertResource
from src.sekai.skia_renderer.canvas import render_canvas_payload
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT

CARD_WIDTH = 542
CARD_GAP = 12
PANEL_PADDING = 12
CARD_INNER_WIDTH = CARD_WIDTH - 24
SLOT_SIZE = 66
SLOT_GAP = 6
INPUT_WIDTH = SLOT_SIZE * 3 + SLOT_GAP * 2
ARROW_WIDTH = 52
OUTPUT_WIDTH = CARD_INNER_WIDTH - INPUT_WIDTH - ARROW_WIDTH - 32

INK: Color = (40, 44, 64, 255)
GRAY: Color = (50, 50, 50, 255)
DIM: Color = (120, 120, 130, 255)
BLUE: Color = (51, 187, 238, 255)
ACCENTS: dict[str, Color] = {
    "normal": (102, 221, 17, 255),
    "tone": BLUE,
    "mysekai_blueprint": (255, 170, 0, 255),
    "mysekai_music_record": (187, 51, 238, 255),
    "limited": (238, 68, 102, 255),
}
TITLE_STYLE = TextStyle(font=DEFAULT_HEAVY_FONT, size=30, color=(35, 35, 35, 255))
SECTION_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=INK)
NAME_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(45, 48, 64, 255))
INFO_STYLE = TextStyle(font=DEFAULT_FONT, size=16, color=GRAY)
SMALL_STYLE = TextStyle(font=DEFAULT_FONT, size=14, color=DIM)
RESOURCE_STYLE = TextStyle(font=DEFAULT_FONT, size=11, color=GRAY)
CHIP_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=(255, 255, 255, 255))


def _column_count(recipe_count: int) -> int:
    if recipe_count <= 2:
        return max(1, recipe_count)
    for maximum, columns in ((9, 3), (20, 4), (35, 5)):
        if recipe_count <= maximum:
            return columns
    return 6


def _panel(width: int) -> VSplit:
    return (
        VSplit()
        .set_w(width)
        .set_padding(PANEL_PADDING)
        .set_sep(10)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_bg(roundrect_bg(alpha=80))
    )


def _chip(text: str, fill: Color) -> TextBox:
    offset_y = ink_centered_text_offset_y(CHIP_STYLE.font, CHIP_STYLE.size, text, CHIP_STYLE.size)
    return (
        TextBox(text, CHIP_STYLE)
        .set_padding((8, 3))
        .set_text_offset((0, offset_y))
        .set_bg(RoundRectBg(fill, 9, blur_glass=False))
    )


def _resource_slot(resource: MysekaiConvertResource, image: ImageSource, size: int) -> None:
    fill = (255, 255, 255, 190)
    # The game ships category symbols as white silhouettes, unlike the coloured item thumbnails.
    if any("/mysekai/icon/category_icon/" in path for path in candidates(resource.image_path)):
        fill = (60, 64, 80, 235)
    with (
        Frame().set_size((size, size)).set_content_align("c").set_bg(RoundRectBg(fill, 10, stroke=(120, 130, 150, 45)))
    ):
        ImageBox(image, size=(size - 10, size - 10), image_size_mode="fit", use_alpha_blend=True)
        quantity = "?" if resource.quantity is None else f"{resource.quantity:,}"
        font_size = min(18 if size > SLOT_SIZE else 16, max(8, int((size - 16) / (len(quantity) * 0.65))))
        with Frame().set_size((size, size)).set_padding((3, 3)).set_content_align("rb"):
            TextBox(quantity, CHIP_STYLE.replace(size=font_size)).set_padding((4, 1)).set_bg(
                RoundRectBg((45, 48, 64, 210), 4)
            )


def _resource_cell(resource: MysekaiConvertResource, image: ImageSource, size: int = SLOT_SIZE) -> None:
    with VSplit().set_w(size).set_sep(3).set_content_align("t").set_item_align("t"):
        _resource_slot(resource, image, size)
        TextBox(resource.name or f"#{resource.id}", RESOURCE_STYLE, use_real_line_count=True).set_w(size).set_padding(
            0
        ).set_content_align("t")


def _draw_arrow(_widget, painter) -> None:
    path = VectorPath(
        (
            ("move", (2, 18)),
            ("line", (29, 18)),
            ("line", (29, 6)),
            ("line", (50, 28)),
            ("line", (29, 50)),
            ("line", (29, 38)),
            ("line", (2, 38)),
            ("close", ()),
        ),
        fill=(120, 130, 150, 220),
    )
    painter.vector_path(path)


def _recipe_flow(
    rqd: MysekaiConvertRecipesRequest,
    recipe: MysekaiConvertRecipe,
    input_images: list[ImageSource],
    output_images: list[ImageSource],
) -> None:
    with HSplit().set_sep(16).set_item_align("c"):
        with VSplit().set_w(INPUT_WIDTH).set_sep(8).set_item_align("t"):
            TextBox(caller_label(rqd, "mysekai.convert.inputs", "投入材料"), SMALL_STYLE).set_content_align("c")
            if recipe.inputs:
                with Grid(col_count=min(3, len(recipe.inputs))).set_sep(SLOT_GAP, SLOT_GAP).set_item_align("t"):
                    for resource, image in zip(recipe.inputs, input_images, strict=True):
                        _resource_cell(resource, image)
        Spacer(w=ARROW_WIDTH, h=56).add_draw_func(_draw_arrow)
        with VSplit().set_w(OUTPUT_WIDTH).set_sep(8).set_item_align("t"):
            TextBox(caller_label(rqd, "mysekai.convert.outputs", "转换产物"), SMALL_STYLE).set_content_align("c")
            if len(recipe.outputs) == 1:
                resource = recipe.outputs[0]
                _resource_slot(resource, output_images[0], 108)
                TextBox(resource.name or f"#{resource.id}", INFO_STYLE, use_real_line_count=True).set_w(
                    OUTPUT_WIDTH
                ).set_content_align("t")
            elif recipe.outputs:
                with Grid(col_count=min(3, len(recipe.outputs))).set_sep(SLOT_GAP, SLOT_GAP).set_item_align("t"):
                    for resource, image in zip(recipe.outputs, output_images, strict=True):
                        _resource_cell(resource, image)


def _recipe_card(
    rqd: MysekaiConvertRecipesRequest,
    recipe: MysekaiConvertRecipe,
    input_images: list[ImageSource],
    output_images: list[ImageSource],
) -> None:
    accent = ACCENTS.get(recipe.category_key, BLUE)
    with (
        VSplit()
        .set_w(CARD_WIDTH)
        .set_padding(12)
        .set_sep(10)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_bg(RoundRectBg((255, 255, 255, 125), 10))
    ):
        with HSplit().set_sep(8).set_item_align("c"):
            Spacer(w=6, h=26).set_bg(RoundRectBg(accent, 3))
            TextBox(recipe.name or f"#{recipe.id}", NAME_STYLE, use_real_line_count=True).set_w(CARD_INNER_WIDTH - 78)
            TextBox(f"#{recipe.id}", SMALL_STYLE).set_w(56).set_content_align("r")
        _recipe_flow(rqd, recipe, input_images, output_images)
        with VSplit().set_w(CARD_INNER_WIDTH).set_sep(3).set_item_align("lt"):
            if recipe.duration:
                TextBox(
                    caller_label(rqd, "mysekai.convert.duration", "基础耗时：{duration}", duration=recipe.duration),
                    INFO_STYLE,
                    use_real_line_count=True,
                ).set_w(CARD_INNER_WIDTH)
            if recipe.required_level > 0:
                TextBox(
                    caller_label(rqd, "mysekai.convert.level", "转换器等级 {level}", level=recipe.required_level),
                    INFO_STYLE,
                ).set_w(CARD_INNER_WIDTH)
            if recipe.limits:
                TextBox(
                    caller_label(rqd, "mysekai.convert.limits", "限额：{limits}", limits=recipe.limits),
                    SMALL_STYLE,
                    use_real_line_count=True,
                ).set_w(CARD_INNER_WIDTH)


async def build_mysekai_convert_recipes_canvas(rqd: MysekaiConvertRecipesRequest) -> Canvas:
    columns = _column_count(len(rqd.recipes))
    inner_width = CARD_WIDTH * columns + CARD_GAP * (columns - 1)
    panel_width = inner_width + 2 * PANEL_PADDING
    resources = [resource for recipe in rqd.recipes for resource in (*recipe.inputs, *recipe.outputs)]
    images = iter(await get_asset_image_refs(ASSETS_BASE_DIR, [resource.image_path for resource in resources]))
    groups: dict[str, list[tuple[MysekaiConvertRecipe, list[ImageSource], list[ImageSource]]]] = {}
    for recipe in rqd.recipes:
        entry = (recipe, [next(images) for _ in recipe.inputs], [next(images) for _ in recipe.outputs])
        groups.setdefault(recipe.category_key, []).append(entry)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(16).set_content_align("lt").set_item_align("lt"):
            with _panel(panel_width):
                TextBox(rqd.title, TITLE_STYLE, use_real_line_count=True).set_w(inner_width)
                TextBox(caller_label(rqd, "mysekai.convert.total", "共 {total} 条配方", total=rqd.total), INFO_STYLE)
                if rqd.filter:
                    TextBox(
                        caller_label(rqd, "mysekai.convert.filter", "筛选：{filter}", filter=rqd.filter),
                        INFO_STYLE,
                        use_real_line_count=True,
                    ).set_w(inner_width)
            if not groups:
                with _panel(panel_width):
                    TextBox(caller_label(rqd, "mysekai.convert.empty", "暂无匹配的转换配方"), INFO_STYLE)
            for category_key, entries in groups.items():
                accent = ACCENTS.get(category_key, BLUE)
                category = next((recipe.category for recipe, _, _ in entries if recipe.category), "")
                title = category or category_key or caller_label(rqd, "mysekai.convert.other", "其他")
                with _panel(panel_width):
                    with HSplit().set_sep(10).set_item_align("c"):
                        Spacer(w=6, h=26).set_bg(RoundRectBg(accent, 3))
                        TextBox(title, SECTION_STYLE)
                        _chip(
                            caller_label(rqd, "mysekai.convert.group_count", "{count} 条配方", count=len(entries)),
                            accent,
                        )
                    with VSplit().set_sep(CARD_GAP).set_content_align("lt").set_item_align("lt"):
                        for start in range(0, len(entries), columns):
                            with HSplit().set_sep(CARD_GAP).set_item_align("lt"):
                                for recipe, input_images, output_images in entries[start : start + columns]:
                                    _recipe_card(rqd, recipe, input_images, output_images)
            with _panel(panel_width):
                TextBox(
                    caller_label(rqd, "mysekai.convert.note", "数量为单次转换用量，耗时为基础值"),
                    SMALL_STYLE,
                    use_real_line_count=True,
                ).set_w(inner_width)
    add_request_watermark(canvas, rqd)
    return canvas


async def try_render_mysekai_convert_recipes_payload(rqd: MysekaiConvertRecipesRequest) -> EncodedImagePayload | None:
    return await render_canvas_payload(
        lambda: build_mysekai_convert_recipes_canvas(rqd), endpoint="mysekai_convert_recipes"
    )


async def compose_mysekai_convert_recipes_image(rqd: MysekaiConvertRecipesRequest) -> Image.Image:
    """Pixel reference for parity tools; service requests use the native payload entrypoint."""
    return await (await build_mysekai_convert_recipes_canvas(rqd)).get_img()
