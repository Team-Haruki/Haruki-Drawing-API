from io import BytesIO
import json
from pathlib import Path
import shutil

import matplotlib
from PIL import Image, ImageChops, ImageDraw, ImageFont
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - native CI job exercises this file
    _native = None

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.profile.custom_profile.collection_prefab import (
    OMIKUJI_RESULT_NATIVE_SIZE,
    OmikujiAssetOp,
    OmikujiDisplayList,
    PillowOmikujiAdapter,
    build_omikuji_display_list,
)
from src.sekai.profile.custom_profile.renderer import PROFILE_RENDER_VIEW_H, PROFILE_RENDER_VIEW_W, PNGRenderer
import src.sekai.profile.custom_profile.skia as skia_mod
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY


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


def _renderer(tmp_path: Path) -> tuple[PNGRenderer, dict, dict[str, Path]]:
    material = tmp_path / "asset" / "jp-assets" / "startapp" / "lottery_game" / "material"
    background = material / "background.png"
    fortune = material / "fortune.png"
    _write_pattern(background, tuple(round(value) for value in OMIKUJI_RESULT_NATIVE_SIZE), 11)
    _write_pattern(fortune, (47, 83), 19)

    fonts = tmp_path / "fonts"
    fonts.mkdir()
    source_font = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
    shutil.copyfile(source_font, fonts / "FOT-RodinNTLGPro-DB.ttf")
    assets = tmp_path / "asset" / "jp-assets" / "startapp" / "custom_profile"
    assets.mkdir(parents=True)
    omikuji = {
        "id": 183,
        "unit": "idol",
        "summary": "あした、晴れる\n迷いーなし",
        "title1": "願 望",
        "description1": "きっと叶う",
        "title2": "健康",
        "description2": "大変良好",
        "title3": "待人",
        "description3": "自ら行く",
        "backgroundImagePath": str(background),
        "fortuneImagePath": str(fortune),
    }
    resources = {
        "customProfileCollectionResources": {
            1000: {
                "id": 1000,
                "customProfileResourceCollectionType": "omikuji",
            }
        },
        "omikujis": {183: omikuji},
    }
    renderer = PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=fonts,
        resources=resources,
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=None,
        profile_context={},
        region="jp",
        clip_canvas_transform=True,
        canvas_w=int(PROFILE_RENDER_VIEW_W),
        canvas_h=int(PROFILE_RENDER_VIEW_H),
        origin_x=PROFILE_RENDER_VIEW_W / 2.0,
        origin_y=PROFILE_RENDER_VIEW_H / 2.0,
    )
    return renderer, omikuji, {"background": background, "fortune": fortune}


def _legacy_omikuji(renderer: PNGRenderer, omikuji: dict, paths: dict[str, Path]) -> Image.Image:
    """Frozen pre-display-list Pillow composer."""

    image = renderer.open_rgba(paths["background"]).copy()
    width, height = image.size
    draw = ImageDraw.Draw(image)
    accent = (136, 221, 68, 255)
    text_fill = (79, 79, 79, 255)

    fortune = renderer.open_rgba(paths["fortune"])
    target_h = max(1, round(height * 300.0 / 490.0))
    if fortune.height != target_h:
        fortune = fortune.resize(
            (max(1, round(fortune.width * target_h / fortune.height)), target_h),
            Image.Resampling.LANCZOS,
        )
    image.alpha_composite(fortune, (round(width * 1309.0 / 1480.0), round(height * 89.0 / 490.0)))

    rotate_chars = {"、", "。", "，", "．", "・", "：", "；", "！", "？", "ー"}
    small_kana = set("ぁぃぅぇぉっゃゅょァィゥェォッャュョ")

    def vertical(x: float, y: float, text: str, font, fill, *, step: float) -> None:
        cursor_y = y
        for char in text:
            if char in {" ", "\u3000"}:
                cursor_y += step * 0.5
                continue
            if char in rotate_chars:
                bbox = draw.textbbox((0, 0), char, font=font)
                glyph = Image.new(
                    "RGBA",
                    (max(1, bbox[2] - bbox[0] + 8), max(1, bbox[3] - bbox[1] + 8)),
                    (0, 0, 0, 0),
                )
                ImageDraw.Draw(glyph).text((4 - bbox[0], 4 - bbox[1]), char, font=font, fill=fill)
                glyph = glyph.rotate(90, expand=True)
                image.alpha_composite(
                    glyph,
                    (round(x - glyph.width / 2), round(cursor_y - glyph.height / 2 + step * 0.28)),
                )
            else:
                draw.text(
                    (
                        x - step * 0.08 if char in small_kana else x,
                        cursor_y + step * 0.16 if char in small_kana else cursor_y,
                    ),
                    char,
                    font=font,
                    fill=fill,
                    anchor="mm",
                )
            cursor_y += step

    summary_font = renderer.omikuji_font(round(height * 36.0 / 490.0))
    for index, line in enumerate(line for line in omikuji["summary"].splitlines() if line):
        vertical(
            width * 1251.0 / 1480.0 - index * width * 44.0 / 1480.0,
            height * 49.0 / 490.0,
            line,
            summary_font,
            text_fill,
            step=height * 29.5 / 490.0,
        )

    rows = (
        (omikuji["title3"], omikuji["description3"]),
        (omikuji["title2"], omikuji["description2"]),
        (omikuji["title1"], omikuji["description1"]),
    )
    title_font = renderer.omikuji_font(round(height * 40.0 / 490.0))
    value_font = renderer.omikuji_font(round(height * 30.0 / 490.0))
    title_lefts = (width * 430.0 / 1480.0, width * 584.0 / 1480.0, width * 736.0 / 1480.0)
    title_top = height * 31.0 / 490.0
    title_w = width * 44.0 / 1480.0
    title_h = height * 94.0 / 490.0
    for (title, value), title_left in zip(rows, title_lefts, strict=True):
        draw.rectangle(
            (
                round(title_left),
                round(title_top),
                round(title_left + title_w),
                round(title_top + title_h),
            ),
            fill=accent,
        )
        vertical(
            title_left + title_w / 2.0,
            title_top + height * 27.0 / 490.0,
            title.replace(" ", ""),
            title_font,
            (255, 255, 255, 255),
            step=height * 39.0 / 490.0,
        )
        vertical(
            title_left - width * 40.0 / 1480.0,
            height * 55.0 / 490.0,
            value,
            value_font,
            text_fill,
            step=height * 25.0 / 490.0,
        )
    return image


@pytest.mark.parametrize("size", [(0, 10), (10, 0), (-1, 10)])
def test_omikuji_display_list_rejects_non_positive_asset_dimensions(size: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="positive dimensions"):
        build_omikuji_display_list(
            {},
            background_path="background.png",
            background_size=size,
            fortune_path="fortune.png",
            fortune_size=(10, 10),
        )


def test_omikuji_display_list_skips_empty_text_rows() -> None:
    display_list = build_omikuji_display_list(
        {},
        background_path="background.png",
        background_size=(148, 49),
        fortune_path="fortune.png",
        fortune_size=(10, 20),
    )

    assert display_list.size == (148, 49)
    assert [type(op) for op in display_list.ops] == [OmikujiAssetOp, OmikujiAssetOp]


def test_omikuji_adapter_reports_missing_required_asset() -> None:
    adapter = PillowOmikujiAdapter(lambda _size, _decorative: ImageFont.load_default(), lambda _path: None)
    display_list = OmikujiDisplayList(
        (4, 4),
        (OmikujiAssetOp("background", "missing.png", (0, 0, 4, 4), sampling="nearest", blend="src"),),
    )

    with pytest.raises(FileNotFoundError, match="background"):
        adapter.render(display_list)


def _rgb_diff_metrics(reference: Image.Image, rendered: Image.Image) -> tuple[float, int]:
    histogram = ImageChops.difference(reference, rendered).convert("RGB").histogram()
    channel_pixels = reference.width * reference.height * 3
    mean = sum(value * histogram[channel * 256 + value] for channel in range(3) for value in range(256))
    mean /= channel_pixels
    threshold = channel_pixels * 0.99
    cumulative = 0
    for value in range(256):
        cumulative += sum(histogram[channel * 256 + value] for channel in range(3))
        if cumulative >= threshold:
            return mean, value
    return mean, 255


def test_omikuji_shared_display_list_matches_legacy_pillow_composer(tmp_path: Path) -> None:
    renderer, omikuji, paths = _renderer(tmp_path)

    expected = _legacy_omikuji(renderer, omikuji, paths)
    actual = renderer.draw_omikuji_result_view(omikuji, paths)

    assert actual.tobytes() == expected.tobytes()


@pytest.mark.skipif(
    _native is None
    or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY
    or getattr(_native, "ASSET_INFO_CAPABILITY", 0) < 1
    or getattr(_native, "TEXT_METRICS_CAPABILITY", 0) < 1,
    reason="current native custom-profile renderer is required",
)
def test_omikuji_category_is_native_without_pillow_pixels(tmp_path: Path, monkeypatch) -> None:
    renderer, _omikuji, _paths = _renderer(tmp_path)
    card = {
        "seq": 1,
        "customProfileCard": {
            "collections": [
                {
                    "id": 1000,
                    "targetId": 183,
                    "objectData": {
                        "visible": True,
                        "layer": 1,
                        "position": {"x": 0, "y": 0},
                        "scale": {"x": 1, "y": 1},
                        "rotation": {"z": 0, "w": 1},
                    },
                }
            ]
        },
    }
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _native)

    token = begin_pillow_touch_scope()
    try:
        ir_json, mem_images, report = skia_mod._build_scene(renderer, card)
        pillow_touches = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert report.complete
    assert report.native_elements == 1
    assert mem_images == {}
    assert pillow_touches.counts == {}
    scene = json.loads(ir_json)
    nodes = list(_walk_nodes(scene["root"]))
    assert any(node["type"] == "Image" and node.get("blend") == "src" for node in nodes)
    assert any(node["type"] == "Text" for node in nodes)

    native_result = _native.render_scene(ir_json, mem_images)
    native_image = Image.open(BytesIO(native_result["image_bytes"])).convert("RGBA")
    pillow_image = renderer.render_card(card).convert("RGBA")
    mean, p99 = _rgb_diff_metrics(pillow_image, native_image)
    assert mean <= 1.0, mean
    assert p99 <= 12, p99
    assert ImageChops.difference(pillow_image.getchannel("A"), native_image.getchannel("A")).getbbox() is None


def _walk_nodes(node: dict):
    yield node
    for child in node.get("children", ()):
        yield from _walk_nodes(child)
