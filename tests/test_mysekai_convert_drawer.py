from __future__ import annotations

import asyncio
import base64

from pydantic import ValidationError
import pytest

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.base.plot import Frame, ImageBox, RoundRectBg, TextBox
from src.sekai.mysekai import convert_drawer as drawer
from src.sekai.mysekai.convert_model import MysekaiConvertRecipe, MysekaiConvertRecipesRequest, MysekaiConvertResource
from src.sekai.skia_renderer.canvas import canvas_size_within_limit, load_native_renderer


def _walk(widget):
    yield widget
    for child in getattr(widget, "items", []):
        yield from _walk(child)


def _slots(canvas):
    return [
        widget
        for widget in _walk(canvas)
        if isinstance(widget, Frame) and isinstance(widget.bg, RoundRectBg) and widget.bg.stroke == (120, 130, 150, 45)
    ]


def _recipe(identifier: int = 1) -> MysekaiConvertRecipe:
    return MysekaiConvertRecipe(
        id=identifier,
        name="转换器完整配方",
        category="普通",
        category_key="normal",
        inputs=[
            MysekaiConvertResource(name=f"材料 {index}", id=index, image_path="item.png", quantity=index * 100)
            for index in range(1, 8)
        ],
        outputs=[
            MysekaiConvertResource(name=f"产物 {index}", id=index, image_path="item.png", quantity=1)
            for index in range(1, 6)
        ],
        duration="1 小时 20 分钟",
        required_level=3,
        limits="每日 5 次；每周 20 次",
    )


@pytest.fixture
def recipe_assets(tmp_path, monkeypatch):
    # A complete PNG fixture; the drawer probes it natively, without making a Pillow raster.
    (tmp_path / "item.png").write_bytes(
        base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    )
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    return tmp_path


def test_convert_model_preserves_unknown_quantity_and_candidate_paths():
    resource = MysekaiConvertResource.model_validate(
        {"name": "随机蓝图", "image_path": ["missing.png", "blueprint.png"], "quantity": None, "id": 42}
    )
    assert resource.quantity is None
    assert resource.image_path == ["missing.png", "blueprint.png"]
    assert MysekaiConvertRecipesRequest().labels == {}
    assert (
        not {"page", "page_count", "previous_command", "next_command"}
        & MysekaiConvertRecipesRequest.model_fields.keys()
    )


@pytest.mark.parametrize(("total", "expected"), [(None, 2), (0, 0), (42, 42)])
def test_omitted_total_uses_recipe_count_but_explicit_total_is_preserved(total, expected, recipe_assets, real_fonts):
    values = {"recipes": [_recipe(1), _recipe(2)]}
    if total is not None:
        values["total"] = total
    canvas = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(MysekaiConvertRecipesRequest(**values)))
    texts = [widget.text for widget in _walk(canvas) if isinstance(widget, TextBox)]
    assert f"共 {expected} 条配方" in texts


def test_resource_names_are_bounded_without_truncating_accepted_names(recipe_assets, real_fonts):
    name = "材" * 128
    with pytest.raises(ValidationError, match="String should have at most 128 characters"):
        MysekaiConvertResource(name=name + "料")
    recipe = _recipe()
    recipe.inputs[0].name = name
    recipe.outputs = [MysekaiConvertResource(name=name, image_path="item.png", quantity=1)]
    canvas = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(MysekaiConvertRecipesRequest(recipes=[recipe])))
    labels = [widget for widget in _walk(canvas) if isinstance(widget, TextBox) and widget.text == name]
    assert len(labels) == 2
    assert all("".join(label._get_lines()) == name for label in labels)
    assert canvas_size_within_limit(canvas._get_self_size())


@pytest.mark.parametrize("input_count", [0, 1, 2, 7])
@pytest.mark.parametrize("output_count", [0, 1, 5])
def test_recipe_draws_only_actual_resource_slots(input_count, output_count, recipe_assets, real_fonts):
    recipe = _recipe()
    recipe.inputs = recipe.inputs[:input_count]
    recipe.outputs = recipe.outputs[:output_count]
    canvas = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(MysekaiConvertRecipesRequest(recipes=[recipe])))
    assert len(_slots(canvas)) == input_count + output_count
    assert canvas_size_within_limit(canvas._get_self_size())


def test_maximum_current_recipe_keeps_every_input_and_output(recipe_assets, real_fonts):
    recipe = _recipe()
    recipe.inputs[0].quantity = None
    recipe.inputs[1].quantity = 0
    request = MysekaiConvertRecipesRequest(
        recipes=[recipe],
        total=1,
        labels={"mysekai.convert.inputs": "Materials", "mysekai.convert.level": "Converter level {level}"},
    )
    canvas = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(request))
    widgets = list(_walk(canvas))
    texts = [widget.text for widget in widgets if isinstance(widget, TextBox)]
    assert all(resource.name in texts for resource in (*recipe.inputs, *recipe.outputs))
    assert len([widget for widget in widgets if isinstance(widget, ImageBox)]) == 12
    assert "?" in texts
    assert "0" in texts
    assert "Materials" in texts
    assert "Converter level 3" in texts
    assert "转换器等级 3" not in texts
    width, height = canvas._get_self_size()
    assert width == drawer.CARD_WIDTH + 2 * drawer.PANEL_PADDING + 2 * drawer.BG_PADDING
    assert height < 4096


def test_all_fifty_eight_recipes_fit_one_canvas_without_dropping_resources(recipe_assets, real_fonts):
    recipes = [_recipe(identifier) for identifier in range(58)]
    for recipe in recipes:
        recipe.inputs[-1].name = "非常长的素材名称完整展示"
        recipe.outputs[-1].name = "多个转换产物中的最后一个"
    canvas = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(MysekaiConvertRecipesRequest(recipes=recipes)))
    widgets = list(_walk(canvas))
    assert sum(isinstance(widget, ImageBox) for widget in widgets) == 58 * 12
    texts = [widget.text for widget in widgets if isinstance(widget, TextBox)]
    assert all(f"#{identifier}" in texts for identifier in range(58))
    width, height = canvas._get_self_size()
    assert width == drawer.CARD_WIDTH * 6 + drawer.CARD_GAP * 5 + 2 * drawer.PANEL_PADDING + 2 * drawer.BG_PADDING
    assert height < width * 2
    assert canvas_size_within_limit((width, height))


def test_groups_use_raw_keys_preserve_order_and_keep_images_with_their_recipe(recipe_assets, real_fonts):
    categories = [("tone", "同一显示名"), ("normal", "同一显示名"), ("tone", "同一显示名"), ("limited", "限时")]
    recipes = []
    for identifier, (key, label) in enumerate(categories):
        recipe = _recipe(identifier)
        recipe.name = f"Recipe {identifier}"
        recipe.category_key, recipe.category = key, label
        (recipe_assets / f"{identifier}.png").write_bytes((recipe_assets / "item.png").read_bytes())
        for resource in (*recipe.inputs, *recipe.outputs):
            resource.image_path = f"{identifier}.png"
        recipes.append(recipe)
    request = MysekaiConvertRecipesRequest(recipes=recipes, labels={"mysekai.convert.group_count": "{count} recipes"})
    canvas = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(request))
    widgets = list(_walk(canvas))
    headings = [
        widget.text for widget in widgets if isinstance(widget, TextBox) and widget.style is drawer.SECTION_STYLE
    ]
    assert headings == ["同一显示名", "同一显示名", "限时"]
    texts = [widget.text for widget in widgets if isinstance(widget, TextBox)]
    assert "2 recipes" in texts
    assert texts.count("1 recipes") == 2
    assert [text for text in texts if text.startswith("Recipe ")] == ["Recipe 0", "Recipe 2", "Recipe 1", "Recipe 3"]
    cards = [
        widget
        for widget in widgets
        if widget.w == drawer.CARD_WIDTH
        and isinstance(widget.bg, RoundRectBg)
        and widget.bg.fill == (255, 255, 255, 125)
    ]
    for card, identifier in zip(cards, [0, 2, 1, 3], strict=True):
        card_images = [widget for widget in _walk(card) if isinstance(widget, ImageBox)]
        assert len(card_images) == 12
        assert all(image.image.path.name == f"{identifier}.png" for image in card_images)
    assert canvas_size_within_limit(canvas._get_self_size())


@pytest.mark.parametrize(("count", "columns"), [(1, 1), (2, 2), (4, 3), (14, 4), (28, 5), (58, 6)])
def test_filtered_results_adapt_the_number_of_columns(count, columns, recipe_assets, real_fonts):
    request = MysekaiConvertRecipesRequest(recipes=[_recipe(identifier) for identifier in range(count)])
    canvas = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(request))
    assert canvas._get_self_size()[0] == (
        drawer.CARD_WIDTH * columns + drawer.CARD_GAP * (columns - 1) + 2 * drawer.PANEL_PADDING + 2 * drawer.BG_PADDING
    )


@pytest.mark.parametrize("recipe_count", [1, 58])
def test_native_recipe_render_has_no_pillow_touches(recipe_count, recipe_assets, real_fonts, monkeypatch):
    try:
        load_native_renderer()
    except ImportError:
        pytest.skip("native renderer is not installed")
    monkeypatch.setenv("HARUKI_BG_TEST_HOUR", "12")
    request = MysekaiConvertRecipesRequest(
        recipes=[_recipe(identifier) for identifier in range(recipe_count)], total=recipe_count, dt=1792886400000
    )
    expected_size = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(request))._get_self_size()
    token = begin_pillow_touch_scope()
    try:
        payload = asyncio.run(drawer.try_render_mysekai_convert_recipes_payload(request))
        pillow_touches = take_pillow_touch_snapshot().counts
    finally:
        end_pillow_touch_scope(token)
    assert payload is not None
    assert payload.backend == "skia"
    assert (payload.image_width, payload.image_height) == expected_size
    assert payload.image_bytes
    assert pillow_touches == {}


def test_single_output_and_empty_result_keep_filter_and_caller_labels(recipe_assets, real_fonts):
    recipe = _recipe()
    recipe.outputs = recipe.outputs[:1]
    request = MysekaiConvertRecipesRequest(
        recipes=[recipe],
        filter="音色",
    )
    canvas = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(request))
    texts = [widget.text for widget in _walk(canvas) if isinstance(widget, TextBox)]
    assert not any("上一页" in text or "下一页" in text or " 页" in text for text in texts)
    assert "筛选：音色" in texts
    assert sum(isinstance(widget, ImageBox) for widget in _walk(canvas)) == 8
    canvas._get_self_size()

    empty = MysekaiConvertRecipesRequest(labels={"mysekai.convert.empty": "No matching recipes"})
    canvas = asyncio.run(drawer.build_mysekai_convert_recipes_canvas(empty))
    assert "No matching recipes" in [widget.text for widget in _walk(canvas) if isinstance(widget, TextBox)]
    canvas._get_self_size()
