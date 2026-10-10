"""JP 7.0.0 solo virtual lives: grouped list briefs and the vlive detail page."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest

from src.sekai.base.plot import Canvas, TextBox
from src.sekai.vlive import drawer
from src.sekai.vlive.model import VLiveBrief, VLiveDetailRequest, VLiveTotalCheerPointReward

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
START_MS = int((NOW - timedelta(days=1)).timestamp() * 1000)
END_MS = int((NOW + timedelta(days=20)).timestamp() * 1000)


def _walk(widget):
    yield widget
    for item in getattr(widget, "items", ()):
        yield from _walk(item)


def _texts(widget) -> list[str]:
    return [item.text for item in _walk(widget) if isinstance(item, TextBox)]


def _detail_payload(**updates) -> dict:
    payload = {
        "region": "jp",
        "timezone": "UTC",
        "id": 2,
        "title": "Solo Live",
        "virtual_live_type": "solo_virtual_live",
        "start_at": START_MS,
        "end_at": END_MS,
        "lives": [
            {
                "id": 491,
                "name": "Solo Live（一歌）",
                "character_icon_path": "icon/1.png",
                "current_start_at": int((NOW + timedelta(hours=2)).timestamp() * 1000),
                "rest_count": 10,
                "schedule_count": 12,
            },
            {"id": 492, "name": "Solo Live（咲希）", "living": True, "rest_count": 3},
        ],
        "total_cheer_point": 1000,
        "total_cheer_point_rewards": [
            {"threshold": 300, "received": True, "rewards": [{"image_path": "r/jewel.png", "quantity": 50}]},
            {"threshold": 900, "rewards": [{"image_path": "r/coin.png", "quantity": 300}]},
            {"threshold": 1800, "rewards": [{"image_path": "r/coin.png"}]},
        ],
        "surplus_reward": {"base_point": 10, "received_count": 3, "rewards": [{"image_path": "r/coin.png"}]},
        "override_cost": {
            "name": "Cheer Coin",
            "image_path": "m/282.png",
            "resource_type": "material",
            "have_quantity": 30,
        },
    }
    payload.update(updates)
    return payload


@pytest.fixture
def fake_assets(monkeypatch: pytest.MonkeyPatch) -> list:
    seen = []

    async def fake_asset(_base, path):
        seen.append(path)
        return Image.new("RGBA", (8, 8), (1, 2, 3, 255))

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    return seen


def test_vlive_brief_group_fields_are_optional_and_parse() -> None:
    plain = VLiveBrief(id=1, name="Live", start_at=START_MS, end_at=END_MS)
    assert (plain.virtual_live_type, plain.group_id, plain.group_name, plain.group_count) == (None,) * 4
    grouped = VLiveBrief.model_validate(
        {
            "id": 2,
            "name": "member",
            "start_at": START_MS,
            "end_at": END_MS,
            "virtual_live_type": "solo_virtual_live",
            "group_id": 2,
            "group_name": "Solo Live",
            "group_count": 26,
        }
    )
    assert drawer._vlive_entry_title(plain) == "【1】Live"
    assert drawer._vlive_entry_title(grouped) == "【2】Solo Live（共 26 场个人 Live）"


def test_vlive_type_labels_hide_normal_and_keep_unknown_types() -> None:
    assert drawer.vlive_type_label(None) is None
    assert drawer.vlive_type_label("") is None
    assert drawer.vlive_type_label("normal") is None
    assert drawer.vlive_type_label("solo_virtual_live") == "个人 Live"
    assert drawer.vlive_type_label("future_type") == "future_type"


def test_vlive_list_entry_without_new_fields_has_no_tag_or_group_text() -> None:
    plain = VLiveBrief(id=1, name="Live", start_at=NOW, end_at=NOW + timedelta(hours=1))
    texts = _texts(drawer._build_vlive_entry_canvas(plain, {}, NOW))
    assert texts[0] == "【1】Live"
    assert not any("个人 Live" in text for text in texts)

    grouped = plain.model_copy(update={"virtual_live_type": "solo_virtual_live", "group_name": "G", "group_count": 3})
    texts = _texts(drawer._build_vlive_entry_canvas(grouped, {}, NOW))
    assert texts[:2] == ["个人 Live", "【1】G（共 3 场个人 Live）"]


def test_vlive_detail_request_localizes_times() -> None:
    request = VLiveDetailRequest.model_validate(_detail_payload(timezone="Asia/Tokyo"))
    assert request.start_at.utcoffset() == timedelta(hours=9)
    assert request.lives[0].current_start_at.utcoffset() == timedelta(hours=9)
    assert request.lives[1].current_start_at is None
    assert request.total_cheer_point_rewards[1].received is False
    assert request.total_cheer_point_rewards[2].rewards[0].quantity == 1


def test_vlive_detail_minimal_request_defaults() -> None:
    request = VLiveDetailRequest.model_validate(
        {"region": "jp", "id": 1, "title": "t", "start_at": START_MS, "end_at": END_MS}
    )
    assert request.lives == []
    assert request.total_cheer_point_rewards is None
    assert request.surplus_reward is None
    assert request.override_cost is None


def test_cheer_reward_state_and_live_texts() -> None:
    reward = VLiveTotalCheerPointReward(threshold=900)
    assert drawer._cheer_reward_state(reward.model_copy(update={"received": True}), 0)[0] == "已领取"
    assert drawer._cheer_reward_state(reward, 900)[0] == "已达成"
    assert drawer._cheer_reward_state(reward, 899)[0] == "未达成"
    assert drawer._cheer_reward_state(reward, None)[0] == "未达成"

    request = VLiveDetailRequest.model_validate(_detail_payload())
    first, second = request.lives
    assert drawer._detail_live_name(first, request.title) == "【491】（一歌）"
    assert drawer._detail_live_name(first.model_copy(update={"name": "Solo Live"}), request.title) == "【491】Solo Live"
    # The caller's short_name wins: no comparison of the two names.
    assert drawer._detail_live_name(first.model_copy(update={"short_name": "一歌"}), "unrelated") == "【491】一歌"
    assert drawer._detail_live_count_text(first) == "剩余 10/12 场"
    assert drawer._detail_live_count_text(second) == "剩余 3 场"
    assert drawer._detail_live_status_text(second, NOW) == "当前 Live 进行中"
    assert drawer._detail_live_status_text(first, NOW).startswith("下一场 10-01 14:00")
    assert drawer._detail_live_status_text(first.model_copy(update={"current_start_at": None}), NOW) == "已结束"
    assert drawer._detail_summary_text(request, NOW) == "2 场 Live | 1 场进行中 | 剩余场次：13"


@pytest.mark.anyio
async def test_vlive_detail_canvas_contains_every_section(fake_assets) -> None:
    request = VLiveDetailRequest.model_validate(_detail_payload())
    canvas = await drawer._build_vlive_detail_canvas(request, NOW)
    texts = _texts(canvas)
    for expected in (
        "个人 Live",
        "【2】Solo Live",
        "场次一览",
        "【491】（一歌）",
        "累计应援点奖励（当前累计 1,000 pt）",
        "300 pt",
        "已领取",
        "已达成",
        "未达成",
        "超额应援点奖励",
        "之后每 10 pt（已领取 3 次）",
        "虚拟道具消耗",
        "Cheer Coin  持有 30",
    ):
        assert expected in texts, expected
    # the member without an icon loads nothing; every other image loads once
    assert fake_assets.count("icon/1.png") == 1
    assert "m/282.png" in fake_assets


@pytest.mark.anyio
async def test_vlive_detail_canvas_omits_absent_sections(fake_assets) -> None:
    request = VLiveDetailRequest.model_validate(
        {"region": "jp", "id": 7, "title": "Plain", "start_at": START_MS, "end_at": END_MS}
    )
    texts = _texts(await drawer._build_vlive_detail_canvas(request, NOW))
    assert "场次一览" not in texts
    assert not any("应援点" in text for text in texts)
    assert "虚拟道具消耗" not in texts
    assert fake_assets == []


@pytest.mark.anyio
async def test_vlive_detail_native_entry_respects_gate(monkeypatch) -> None:
    request = VLiveDetailRequest.model_validate(_detail_payload())
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    assert await drawer.try_render_vlive_detail_payload(request) is None

    captured = {}

    async def fake_render(factory, *, endpoint):
        captured["endpoint"] = endpoint
        captured["canvas"] = await factory()
        return "payload"

    async def fake_build(rqd, now=None):
        return Canvas(w=1, h=1)

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "render_canvas_payload", fake_render)
    monkeypatch.setattr(drawer, "_build_vlive_detail_canvas", fake_build)
    assert await drawer.try_render_vlive_detail_payload(request) == "payload"
    assert captured["endpoint"] == "vlive_detail"


def test_vlive_detail_endpoint_renders_png() -> None:
    pytest.importorskip("haruki_skia_renderer")
    from src.core.main import app

    async def post():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/pjsk/vlive/detail", json=_detail_payload())

    response = asyncio.run(post())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG")
