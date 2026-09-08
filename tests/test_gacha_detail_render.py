"""Render complete gacha pages and check the visible rates and time states."""

import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
import json

from PIL import Image
import pytest

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.base.plot import Canvas, FillBg, TextBox
from src.sekai.base.utils import get_asset_image_ref
from src.sekai.gacha import drawer
from src.sekai.gacha.model import GachaBehavior, GachaCardWeight, GachaDetailRequest, GachaInfo, GachaWeight
from src.sekai.profile.drawer import CardFullThumbnailLayers
from src.sekai.profile.model import CardFullThumbnailRequest
from src.sekai.skia_renderer.canvas import build_canvas_ir, load_native_renderer


@pytest.mark.parametrize(
    ("offset", "state", "assets_available"),
    [(2, "距离开始还有", True), (0, "距离结束还有", False), (-2, "卡池已结束", True)],
)
def test_detail_renders_rates_costs_pickups_and_time_state(
    offset, state, assets_available, tmp_path, monkeypatch, real_fonts
):
    try:
        native = load_native_renderer()
    except ImportError:
        pytest.skip("native renderer required")
    now = datetime(2026, 1, 2, tzinfo=UTC)
    monkeypatch.setattr(drawer, "request_now", lambda timezone: now)
    path = tmp_path / "icon.png"
    Image.new("RGBA", (32, 32), (30, 90, 180, 255)).save(path)
    ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))
    thumbnail = CardFullThumbnailRequest(
        card_id=42,
        card_thumbnail_path=path.name,
        rare="rarity_4",
        frame_img_path="",
        attr_img_path="",
        rare_img_path=path.name,
        train_rank=None,
    )
    request = GachaDetailRequest(
        gacha=GachaInfo(
            id=10,
            name="Guaranteed pickup",
            gacha_type="normal",
            asset_name="gacha",
            start_at=int((now + timedelta(days=offset, hours=-1)).timestamp() * 1000),
            end_at=int((now + timedelta(days=offset, hours=1)).timestamp() * 1000),
            ceil_item_img_path="ceil.png",
            rarity_4_count=20,
            rarity_3_count=10,
            behaviors=[
                GachaBehavior(
                    type="once_a_day",
                    spin_count=1,
                    cost_type="paid_jewel",
                    cost_icon_path="cost.png",
                    cost_quantity=100,
                    colorful_pass=True,
                    execute_limit=1,
                ),
                GachaBehavior(type="once_a_week", spin_count=10),
                GachaBehavior(
                    type="normal", spin_count=10, cost_type="jewel", cost_icon_path="cost.png", cost_quantity=3000
                ),
                GachaBehavior(type="normal", spin_count=10),
            ],
        ),
        weight_info=GachaWeight(
            rarity_4_rate=0.06,
            rarity_3_rate=0.24,
            rarity_2_rate=0.7,
            guaranteed_rates={"rarity_4": 0.2, "rarity_2": 0.8},
        ),
        pickup_cards=[GachaCardWeight(id=42, rarity="rarity_4", rate=0.015, thumbnail_request=thumbnail)],
        bg_img_path="bg.png",
        logo_img_path="logo.png",
        banner_img_path="banner.png",
    )

    async def image_ref(*args, **kwargs):
        return ref if assets_available else None

    async def rarity_image(rarity):
        if not assets_available:
            raise FileNotFoundError(rarity)
        with Canvas(bg=FillBg((255, 180, 20, 255))).set_size((32, 16)) as canvas:
            pass
        return canvas

    async def card_layers(request):
        if not assets_available:
            raise FileNotFoundError("pickup")
        return CardFullThumbnailLayers(request, ref, ref)

    async def fallback():
        return ref

    monkeypatch.setattr(drawer, "get_gacha_image_ref_or_unknown", image_ref)
    monkeypatch.setattr(drawer, "get_rarity_img", rarity_image)
    monkeypatch.setattr(drawer, "get_card_full_thumbnail_layers", card_layers)
    monkeypatch.setattr(drawer, "get_unknown_fallback_image", fallback)
    texts = []
    original_init = TextBox.__init__

    def record_text(self, text, *args, **kwargs):
        texts.append(text)
        original_init(self, text, *args, **kwargs)

    monkeypatch.setattr(TextBox, "__init__", record_text)
    token = begin_pillow_touch_scope()
    try:
        canvas = asyncio.run(drawer._build_gacha_detail_canvas(request))
        builder, memory = build_canvas_ir(canvas)
        result = native.render_scene(json.dumps(builder.build()).encode(), memory)
        assert take_pillow_touch_snapshot().counts == {}
    finally:
        end_pillow_touch_scope(token)
    assert state in texts
    assert "月卡每日/单抽(限1次)" in texts
    assert "每周/十连" in texts
    assert "(付费)" in texts
    assert "免费" in texts
    assert "x3000" in texts
    assert "1.5% / 5% (保底)" in texts
    assert "6% / 20% (保底)" in texts
    assert "70% / 80% (保底)" in texts
    output = Image.open(BytesIO(result["image_bytes"]))
    assert output.width >= 600
    assert output.height >= 500
