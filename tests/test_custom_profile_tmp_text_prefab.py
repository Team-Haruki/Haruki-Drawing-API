from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

from PIL import Image
import pytest

from src.core.pillow_telemetry import (
    begin_pillow_touch_scope,
    end_pillow_touch_scope,
    take_pillow_touch_snapshot,
)
from src.sekai.profile.custom_profile.tmp_text_prefab import build_simple_tmp_text_display_list

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_FILE = REPO_ROOT / "out" / "parity-payloads" / "custom_profile_card.json"
FONT_METADATA = REPO_ROOT / "data/custom_profile/tmp-font-assets/cn/metadata.json"


@dataclass
class _FakeAsset:
    source_path: Path
    name: str = "FakeFont"
    atlas_population_mode: int = 1
    has_static_glyphs: bool = False
    fallback_names: tuple[str, ...] = ()
    face_scale: float = 2.0
    face_dilate: float = 0.0
    outline_width: float = 0.0
    outline_softness: float = 0.0
    sharpness: float = 0.0
    underlay_softness: float = 0.0
    underlay_offset_x: float = 0.0
    underlay_offset_y: float = 0.0
    normal_spacing_offset: float = 0.0
    weight_normal: float = 0.0


class _FakeFontLibrary:
    def __init__(self, path: Path) -> None:
        self.asset = _FakeAsset(path)
        self.missing_glyph: str | None = None

    def active_asset(self, font_name: str):
        return self.asset if font_name == self.asset.name else None

    def runtime_source_font_path(self, asset: _FakeAsset) -> Path:
        return asset.source_path

    def source_glyph_metrics(self, font_name: str, char: str, font_size: float, *, include_fallback: bool):
        if char == self.missing_glyph:
            return None
        return SimpleNamespace(advance=font_size / 2.0)


class _FakeRenderer:
    text_layout = "tmp"
    text_vertical_mode = "tmp-native"
    tmp_text_render_mode = "sdf"
    tmp_scale_mode = "fx-native"
    tmp_box_mode = "preferred"
    tmp_block_mode = "glyph"
    tmp_metrics_mode = "asset-fallback"
    tmp_dynamic_sdf = True
    tmp_font_scale = 2.0
    tmp_space_width_factor = 1.0
    include_empty_lines = True
    rotation_sign = 1
    position_scale_x = 1.5
    position_scale_y = 0.5
    text_fonts: ClassVar[dict[int, str]] = {6: "FakeFont"}

    def __init__(self, font_path: Path) -> None:
        self.font_path = font_path
        self.tmp_font_library = _FakeFontLibrary(font_path)
        self.decorative = False

    def generate_text_data(self, item):
        return SimpleNamespace(
            text=item.get("text", ""),
            font_id=item.get("fontId", 6),
            outline_size=item.get("outlineSize", 0.0),
        )

    def update_text_mesh_state(self, data, font_name):
        return SimpleNamespace(
            font_color="#112233",
            font_size=24.0,
            tmp_line_spacing=-10.0,
            underlay_dilate=data.outline_size,
            align=0x0201,
        )

    def font_path_for(self, font_name):
        return self.font_path

    def is_decorative_text_item(self, item):
        return self.decorative

    def tmp_native_text_layout(
        self,
        lines,
        font_name,
        font_path,
        base_size,
        line_spacing,
        dominant_size,
        layout_mode,
        outline_dilate,
        margin_width,
        *,
        source_metrics_only=False,
    ):
        assert source_metrics_only is True
        widths = {"Alpha": 50.0, "Beta": 30.0, "": 1.0}
        line_infos = []
        for index, line in enumerate(lines):
            text = line.runs[0].text if line.runs else ""
            width = widths[text]
            run_metrics = [(line.runs[0], 0.0, width)] if line.runs else []
            line_infos.append(SimpleNamespace(width=width, run_metrics=run_metrics))
        return SimpleNamespace(
            lines=line_infos,
            line_layout=SimpleNamespace(),
            dominant_size=24.0,
            preferred_width=56.0,
            preferred_height=50.0,
            content_height=50.0,
        )

    def tmp_resolve_percent_indent_margin_width(self, *args):
        return None

    def tmp_text_box_size(self, dominant_size, content_width, content_height):
        return 120.0, 80.0

    def tmp_native_baseline_downs(self, layout, box_h, vertical_align):
        return [20.0, 45.0]

    def unity_point(self, position):
        return 100.0, 200.0


def _item(text: str = "Alpha\nBeta", *, outline_size: float = 0.0) -> dict:
    return {
        "text": text,
        "fontId": 6,
        "outlineSize": outline_size,
        "objectData": {
            "visible": True,
            "position": {"x": 0.0, "y": 0.0},
            "scale": {"x": 2.0, "y": 3.0},
            "rotation": {"z": 0.0, "w": 1.0},
        },
    }


def test_plain_tmp_plan_uses_baseline_ops_without_allocating_pillow_images(tmp_path, monkeypatch) -> None:
    font_path = tmp_path / "font.ttf"
    font_path.touch()
    renderer = _FakeRenderer(font_path)

    def _no_image(*args, **kwargs):  # pragma: no cover - only called on regression
        raise AssertionError("the TMP display-list builder must not allocate a Pillow image")

    monkeypatch.setattr(Image, "new", _no_image)
    plan = build_simple_tmp_text_display_list(renderer, _item())

    assert plan is not None
    assert plan.box_size == (120.0, 80.0)
    assert plan.preferred_size == (56.0, 50.0)
    assert plan.line_widths == (50.0, 30.0)
    assert plan.baselines == (20.0, 45.0)
    assert [op.text for op in plan.ops] == ["Alpha", "Beta"]
    assert [op.pos for op in plan.ops] == [(-60.0, -20.0), (-60.0, 5.0)]
    assert all(op.size == 48.0 and op.fill == (17, 34, 51, 255) for op in plan.ops)
    assert plan.transform.matrix == pytest.approx((3.0, 0.0, 100.0, 0.0, 1.5, 200.0))
    assert plan.canvas_pos(plan.ops[0]) == pytest.approx((-80.0, 170.0))


def test_plain_tmp_plan_explicitly_declines_unsupported_text_and_font_features(tmp_path) -> None:
    font_path = tmp_path / "font.ttf"
    font_path.touch()

    assert build_simple_tmp_text_display_list(_FakeRenderer(font_path), _item("<rotate=20>Alpha</rotate>")) is None
    assert build_simple_tmp_text_display_list(_FakeRenderer(font_path), _item(outline_size=0.1)) is None
    assert build_simple_tmp_text_display_list(_FakeRenderer(font_path), _item("Alpha\tBeta")) is None

    decorative = _FakeRenderer(font_path)
    decorative.decorative = True
    assert build_simple_tmp_text_display_list(decorative, _item()) is None

    static_asset = _FakeRenderer(font_path)
    static_asset.tmp_font_library.asset.has_static_glyphs = True
    assert build_simple_tmp_text_display_list(static_asset, _item()) is None

    fallback = _FakeRenderer(font_path)
    fallback.tmp_font_library.missing_glyph = "A"
    assert build_simple_tmp_text_display_list(fallback, _item()) is None


@pytest.mark.skipif(
    not PAYLOAD_FILE.is_file() or not FONT_METADATA.is_file(),
    reason="custom-profile parity fixture and extracted TMP metadata are required",
)
def test_main_fixture_plain_tmp_plan_pins_native_integration_geometry() -> None:
    from src.sekai.profile.custom_profile import drawer
    from src.sekai.profile.custom_profile.renderer import (
        PROFILE_RENDER_VIEW_H,
        PROFILE_RENDER_VIEW_W,
        PNGRenderer,
    )
    from src.sekai.profile.model import CustomProfileCardRenderRequest
    from src.settings import (
        CUSTOM_PROFILE_ASSETS_DIR,
        CUSTOM_PROFILE_FONTS_DIR,
        CUSTOM_PROFILE_SHAPE_SPRITE_DIR,
        CUSTOM_PROFILE_TMP_FONT_METADATA,
        CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR,
    )

    request = CustomProfileCardRenderRequest.model_validate_json(PAYLOAD_FILE.read_text(encoding="utf-8"))
    renderer = PNGRenderer(
        masterdata=None,
        assets=drawer._require_region_path("assets", CUSTOM_PROFILE_ASSETS_DIR, request.region),
        fonts=drawer._require_region_path("fonts", CUSTOM_PROFILE_FONTS_DIR, request.region),
        resources=dict(request.resources),
        tmp_font_metadata=drawer._optional_region_file(
            "tmp_font_metadata",
            CUSTOM_PROFILE_TMP_FONT_METADATA,
            request.region,
        ),
        shape_sprite_dir=drawer._require_region_path(
            "shape_sprite_dir",
            CUSTOM_PROFILE_SHAPE_SPRITE_DIR,
            request.region,
        ),
        profile_context=dict(request.profile_context),
        canvas_w=int(PROFILE_RENDER_VIEW_W),
        canvas_h=int(PROFILE_RENDER_VIEW_H),
        origin_x=PROFILE_RENDER_VIEW_W / 2.0,
        origin_y=PROFILE_RENDER_VIEW_H / 2.0,
        unity_ui_sprite_dir=drawer._require_region_path(
            "unity_ui_sprite_dir",
            CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR,
            request.region,
        ),
        region=request.region,
    )
    item = request.card["customProfileCard"]["texts"][0]

    token = begin_pillow_touch_scope()
    try:
        plan = build_simple_tmp_text_display_list(renderer, item)
        snapshot = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert plan is not None
    assert snapshot.counts == {}
    assert plan.box_size == pytest.approx((592.01, 211.15))
    assert plan.preferred_size == pytest.approx((528.01, 147.15))
    assert plan.line_widths == pytest.approx((478.32, 240.0, 528.0))
    assert plan.baselines == pytest.approx((74.243, 123.815, 173.387))
    assert plan.transform.anchor == pytest.approx((412.0341934260407, 203.37904526826642))
    assert plan.transform.matrix == pytest.approx(
        (1.118081180811808, 0.0, 412.0341934260407, 0.0, 1.118081180811808, 203.37904526826642)
    )
    assert [op.size for op in plan.ops] == [48.0, 48.0, 48.0]
    assert [op.fill for op in plan.ops] == [(255, 187, 204, 255)] * 3
    assert [plan.canvas_pos(op) for op in plan.ops] == pytest.approx(
        [
            (81.07657349984146, 168.34732571107085),
            (81.07657349984146, 223.7728460062738),
            (81.07657349984146, 279.19836630147677),
        ]
    )
