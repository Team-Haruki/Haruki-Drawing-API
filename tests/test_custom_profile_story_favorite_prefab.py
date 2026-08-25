from io import BytesIO
import json
from pathlib import Path
import re

from PIL import Image, ImageDraw
import pytest

from src.sekai.profile.custom_profile.general_prefab import (
    GeneralAssetImageOp,
    GeneralRoundedRectOp,
    PillowGeneralPrefabAdapter,
    build_general_prefab_display_list,
    story_favorite_asset_key,
)
from src.sekai.profile.custom_profile.renderer import (
    GENERAL_NATIVE_SIZES,
    GENERAL_PREFAB_PALETTE,
    GENERAL_TEMPLATE_TEXT,
    NativeContent,
    PNGRenderer,
)
import src.sekai.profile.custom_profile.skia as skia_mod
from src.sekai.skia_renderer.ir_builder import IRBuilder, clip_pillow_rrect


def _write_pattern(path: Path, size: tuple[int, int], seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", size)
    image.putdata(
        [
            (
                (x * 17 + seed) % 256,
                (y * 29 + seed * 3) % 256,
                (x * 7 + y * 11 + seed * 5) % 256,
                96 + (x * 5 + y * 3 + seed) % 160,
            )
            for y in range(size[1])
            for x in range(size[0])
        ]
    )
    image.save(path)


def _make_renderer(
    tmp_path: Path,
    *,
    stories: object,
    story_resources: dict[str, dict[str, object]] | None = None,
) -> PNGRenderer:
    fonts = tmp_path / "fonts"
    assets = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile"
    sprites = tmp_path / "unity-sprites"
    fonts.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    for index, (name, size) in enumerate(
        (
            ("bg_base_wh", (13, 9)),
            ("bg_base_r16_wh", (64, 64)),
            ("bg_base_round_vertical_h6_wh", (8, 16)),
            ("bg_base_round_vertical_h8_wh", (10, 18)),
        )
    ):
        _write_pattern(sprites / f"{name}.png", size, index + 1)
    return PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=fonts,
        resources={"storyFavoriteResources": story_resources or {}},
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=sprites,
        profile_context={"userStoryFavorites": stories},
    )


def _legacy_story_favorite(renderer: PNGRenderer) -> Image.Image | None:
    """Frozen wrapper for the pre-display-list Pillow composer."""

    stories = renderer.profile_context.get("userStoryFavorites") or []
    if not isinstance(stories, list):
        return None
    size = GENERAL_NATIVE_SIZES["StoryFavorite"]
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    renderer.draw_story_favorite_header(image)
    if not stories:
        draw.text(
            (size[0] / 2, size[1] / 2),
            renderer.general_text("not_set"),
            font=renderer.general_font(22),
            fill=GENERAL_TEMPLATE_TEXT,
            anchor="mm",
        )
        return image
    ordered_stories = renderer.ordered_story_favorites(stories)
    card_width, card_height = 403, 172
    gap_x, gap_y = 24, 20
    start_x, start_y = 25, 92
    for index, story in enumerate(ordered_stories):
        column = index % 2
        row = index // 2
        left = start_x + column * (card_width + gap_x)
        top = start_y + row * (card_height + gap_y)
        renderer.draw_story_favorite_cell(
            image,
            story,
            (left, top, left + card_width, top + card_height),
        )
    if len(ordered_stories) > 8:
        renderer.draw_general_vertical_scrollbar(image, (size[0] - 23, 92, size[0] - 17, size[1] - 25))
    return image


def test_story_favorite_shared_display_list_matches_empty_legacy_composer(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, stories=[])

    expected = _legacy_story_favorite(renderer)
    actual = renderer.render_general_story_favorite()

    assert expected is not None
    assert actual is not None
    assert actual.tobytes() == expected.tobytes()


def test_story_favorite_shared_display_list_matches_banner_fallback_and_scroll(tmp_path: Path) -> None:
    banner = tmp_path / "asset" / "cn-assets" / "startapp" / "event_story" / "banner.png"
    _write_pattern(banner, (97, 53), 17)
    stories: list[object] = [
        {"shareNo": share_no, "storyType": "event_story", "storyId": story_id, "comment": f"Fallback {story_id}"}
        for share_no, story_id in zip(range(9, 0, -1), range(101, 110), strict=True)
    ]
    stories.insert(3, "invalid row")
    story_resources: dict[str, dict[str, object]] = {
        "event_story:109": {"title": "Banner", "imagePath": banner.as_posix()},
        "event_story:108": {"title": "Cloud title without a banner"},
    }
    renderer = _make_renderer(tmp_path, stories=stories, story_resources=story_resources)

    expected = _legacy_story_favorite(renderer)
    actual = renderer.render_general_story_favorite()

    assert expected is not None
    assert actual is not None
    assert actual.tobytes() == expected.tobytes()


def test_story_favorite_banner_dependency_is_required_and_describes_exact_composition(tmp_path: Path) -> None:
    banner = tmp_path / "asset" / "cn-assets" / "startapp" / "event_story" / "banner.png"
    _write_pattern(banner, (97, 53), 23)
    story = {"shareNo": 1, "storyType": "event_story", "storyId": 10}
    renderer = _make_renderer(
        tmp_path,
        stories=[story],
        story_resources={"event_story:10": {"title": "Banner", "imagePath": banner.as_posix()}},
    )
    adapter = PillowGeneralPrefabAdapter(renderer.general_font, renderer.paste_unity_sprite, renderer.open_rgba)

    display_list = build_general_prefab_display_list(
        "StoryFavorite",
        size=GENERAL_NATIVE_SIZES["StoryFavorite"],
        profile_context=renderer.profile_context,
        labels={
            "story_favorite_title": renderer.general_text("story_favorite_title"),
            "not_set": renderer.general_text("not_set"),
        },
        metrics=adapter,
        palette=GENERAL_PREFAB_PALETTE,
        asset_paths={story_favorite_asset_key(story): renderer.story_favorite_image_path(story)},
        story_favorite_resources=renderer.story_favorite_resources,
    )

    assert display_list is not None
    banner_op = next(op for op in display_list.ops if isinstance(op, GeneralAssetImageOp))
    assert banner_op.resource_key == "story_favorite:event_story:10"
    assert banner_op.resource_policy == "required"
    assert banner_op.fit == "cover"
    assert banner_op.align == (0.5, 0.5)
    assert banner_op.clip_radius == 10
    outline = next(
        op for op in display_list.ops if isinstance(op, GeneralRoundedRectOp) and op.outline == (235, 242, 255, 210)
    )
    assert outline.fill is None
    assert outline.width == 2


def test_story_favorite_non_list_preserves_noop_contract(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, stories={"storyId": 10})

    assert renderer.render_general_story_favorite() is None


class _NativeMetricsStub:
    def text_bbox(self, text, font, size):
        return 0.0, 0.0, float(len(text) * size) * 0.55, float(size)

    def text_placement(self, op):
        align = {"l": "left", "m": "center", "r": "right"}[(op.anchor or "la")[0]]
        return align, float(op.pos[1] + op.size)


def _native_story_scene(tmp_path: Path):
    builder = IRBuilder(
        2048,
        909,
        assets_base_dir=str(tmp_path),
        font_dir=str(tmp_path),
        default_font="unused.ttf",
        bold_font="unused.ttf",
    )
    return builder, skia_mod._SceneAssembler(builder, (2048, 909), 128 * 1024 * 1024)


def _walk_ir(node):
    yield node
    for child in node.get("children", ()):
        yield from _walk_ir(child)


def test_story_favorite_native_emitter_uses_discrete_rounded_banner_mask(
    tmp_path: Path,
    monkeypatch,
) -> None:
    banner = tmp_path / "asset" / "cn-assets" / "startapp" / "event_story" / "banner.png"
    _write_pattern(banner, (97, 53), 29)
    story = {"shareNo": 1, "storyType": "event_story", "storyId": 10}
    renderer = _make_renderer(
        tmp_path,
        stories=[story],
        story_resources={"event_story:10": {"title": "Banner", "imagePath": banner.as_posix()}},
    )
    renderer.image_resource_for = lambda kind, item: {"fileName": "StoryFavorite"}
    renderer.general_font_path = lambda: tmp_path / "font.ttf"
    (tmp_path / "font.ttf").touch()
    content = NativeContent(
        1,
        "general",
        {"id": 1},
        {"visible": True, "position": {}, "scale": {"x": 1, "y": 1}, "rotation": {}},
    )
    builder, scene = _native_story_scene(tmp_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(
        skia_mod._NativeGeneralTextMetrics,
        "create",
        staticmethod(lambda font_path: _NativeMetricsStub()),
    )

    assert skia_mod._emit_native_general(renderer, content, scene) == "native"
    assert scene.mem_images == {}
    nodes = list(_walk_ir(builder.build()["root"]))
    mask_group = next(node for node in nodes if node.get("clip", {}).get("kind") == "pillow_rrect")
    assert mask_group["clip"]["radius"] == 10.0
    assert any(node["type"] == "Image" for node in _walk_ir(mask_group))


@pytest.mark.parametrize(("size", "radius"), [((31, 23), 7), ((403, 112), 10), ((12, 12), 99)])
def test_native_discrete_rounded_mask_matches_pillow(tmp_path: Path, size: tuple[int, int], radius: int) -> None:
    native = pytest.importorskip("haruki_skia_renderer")
    builder = IRBuilder(
        size[0],
        size[1],
        assets_base_dir=str(tmp_path),
        font_dir=str(tmp_path),
        default_font="unused.ttf",
        bold_font="unused.ttf",
    )
    with builder.group(size=size, clip=clip_pillow_rrect(radius)):
        builder.rect((0, 0), size, fill=(255, 255, 255, 255))

    result = native.render_scene(json.dumps(builder.build()).encode(), {})
    actual = Image.open(BytesIO(result["image_bytes"])).convert("RGBA").getchannel("A")
    expected = Image.new("L", size, 0)
    ImageDraw.Draw(expected).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=radius,
        fill=255,
    )
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize(
    ("builder_limits", "message"),
    [
        ({"max_node_pixels": 100}, "pillow_rrect mask 20x20 (400 pixels) exceeds limit 100"),
        (
            {"max_scene_bytes": 4_000},
            "pillow_rrect mask construction requires 3200 bytes; only 2400 bytes remain",
        ),
    ],
)
def test_native_discrete_rounded_mask_obeys_scene_limits(
    tmp_path: Path,
    builder_limits: dict[str, int],
    message: str,
) -> None:
    native = pytest.importorskip("haruki_skia_renderer")
    builder = IRBuilder(
        20,
        20,
        assets_base_dir=str(tmp_path),
        font_dir=str(tmp_path),
        default_font="unused.ttf",
        bold_font="unused.ttf",
        **builder_limits,
    )
    with builder.group(size=(20, 20), clip=clip_pillow_rrect(4)):
        builder.rect((0, 0), (20, 20), fill=(255, 255, 255, 255))

    with pytest.raises(RuntimeError, match=re.escape(message)):
        native.render_scene(json.dumps(builder.build()).encode(), {})


def test_story_favorite_without_banner_can_use_existing_native_general_primitives(
    tmp_path: Path,
    monkeypatch,
) -> None:
    renderer = _make_renderer(
        tmp_path,
        stories=[{"shareNo": 1, "storyType": "event_story", "storyId": 1, "comment": "Fallback"}],
    )
    renderer.image_resource_for = lambda kind, item: {"fileName": "StoryFavorite"}
    renderer.general_font_path = lambda: tmp_path / "font.ttf"
    (tmp_path / "font.ttf").touch()
    content = NativeContent(
        1,
        "general",
        {"id": 1},
        {"visible": True, "position": {}, "scale": {"x": 1, "y": 1}, "rotation": {}},
    )
    builder, scene = _native_story_scene(tmp_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(
        skia_mod._NativeGeneralTextMetrics,
        "create",
        staticmethod(lambda font_path: _NativeMetricsStub()),
    )

    assert skia_mod._emit_native_general(renderer, content, scene) == "native"
    assert scene.mem_images == {}
    nodes = list(_walk_ir(builder.build()["root"]))
    assert any(node["type"] == "UnitySubscene" for node in nodes)
    assert not any(node.get("sampling") == "pillow_lanczos" for node in nodes)
