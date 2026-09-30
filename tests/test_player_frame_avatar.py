"""Frame transport, native outsets and per-slot asset/cache behavior."""

from io import BytesIO

from PIL import Image, ImageChops, ImageDraw
import pytest

from src.sekai.base.image_source import MissingImageRef
from src.sekai.base.plot import Canvas
from src.sekai.profile import drawer
from src.sekai.profile.model import DetailedProfileCardRequest, PlayerFramePaths
from src.sekai.skia_renderer import canvas as canvas_mod


def _paths(kind="single"):
    return PlayerFramePaths(
        frame_type=kind,
        base="base.png",
        centertop="center.png",
        lefttop="corner.png",
        righttop="corner.png",
        leftbottom="corner.png",
        rightbottom="corner.png",
        side_left_top="side.png",
        side_right_top="side.png",
        side_left_bottom="side.png",
        side_right_bottom="side.png",
    )


@pytest.mark.anyio
async def test_embedded_profile_carries_all_frame_parts_and_ignores_missing_art(monkeypatch):
    detail = DetailedProfileCardRequest(
        id="123",
        region="jp",
        nickname="Frame",
        source="suite",
        update_time=0,
        leader_image_path="avatar.png",
        has_frame=True,
        frame_paths=_paths("combination"),
    )
    request = detail.to_profile_card_request()
    assert request.profile.frame_paths == detail.frame_paths
    received = []

    async def asset(_root, path, **_kwargs):
        received.append(path)
        return Image.new("RGBA", (132, 132))

    monkeypatch.setattr(drawer, "get_asset_image_ref", asset)
    avatar = await drawer._build_profile_card_avatar_module(request)
    assert isinstance(avatar.items[1], drawer.PlayerFrameBox)
    assert len(avatar.items[1].layers.ornaments) == 9
    assert received.count("side.png") == 4

    async def missing(_root, path, **_kwargs):
        return MissingImageRef() if path == "side.png" else Image.new("RGBA", (132, 132))

    monkeypatch.setattr(drawer, "get_asset_image_ref", missing)
    avatar = await drawer._build_profile_card_avatar_module(request)
    assert len(avatar.items) == 1  # Preserve the avatar; no placeholder ornaments.
    assert (
        await drawer.get_player_frame_layers(_paths("combination").model_copy(update={"side_left_top": None})) is None
    )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["single", "combination"])
@pytest.mark.parametrize("broken", ["missing", "corrupt"])
async def test_unreadable_frame_silently_preserves_avatar_and_recovers(tmp_path, monkeypatch, caplog, kind, broken):
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    paths = _paths(kind)
    for name in ("base.png", "corner.png", "side.png"):
        Image.new("RGBA", (132, 132)).save(tmp_path / name)
    if broken == "corrupt":
        (tmp_path / "center.png").write_bytes(b"not an image")
    avatar = Image.new("RGBA", (80, 80), "blue")

    widget = await drawer.get_avatar_widget_with_frame(True, paths, avatar, 80, [])
    assert len(widget.items) == 1
    assert widget._get_self_size() == (80, 80)
    assert not [record for record in caplog.records if record.levelno >= 30]

    Image.new("RGBA", (108, 90)).save(tmp_path / "center.png")
    recovered = await drawer.get_avatar_widget_with_frame(True, paths, avatar, 80, [])
    assert isinstance(recovered.items[1], drawer.PlayerFrameBox)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["single", "combination"])
@pytest.mark.parametrize("size", [80, 128])
async def test_native_frame_keeps_outsets_and_slot_colors(tmp_path, monkeypatch, real_fonts, kind, size):
    pytest.importorskip("haruki_skia_renderer")
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)
    specs = {
        "avatar.png": ((128, 128), (20, 40, 60, 255)),
        "corner.png": ((90, 150), (200, 90, 70, 255)),
        "center.png": ((80, 64), (220, 180, 60, 255)),
        "side.png": ((32, 260), (40, 160, 80, 255)),
        "other-side.png": ((32, 260), (140, 40, 220, 255)),
    }
    for name, (dimensions, color) in specs.items():
        Image.new("RGBA", dimensions, color).save(tmp_path / name)
    base = Image.new("RGBA", (132, 132))
    ImageDraw.Draw(base).rectangle((0, 0, 131, 131), outline=(200, 200, 200, 255), width=12)
    base.save(tmp_path / "base.png")

    async def render(paths):
        avatar = await drawer.get_asset_image_ref(tmp_path, "avatar.png")
        with Canvas().set_padding(16) as canvas:
            await drawer.get_avatar_widget_with_frame(True, paths, avatar, size, [])
        payload = await canvas_mod.render_canvas_payload(canvas, export_format="png")
        assert payload is not None
        return Image.open(BytesIO(payload.image_bytes)).convert("RGBA")

    paths = _paths(kind)
    first = await render(paths)
    assert first.size == (size + 32, size + 32)
    bounds = first.getchannel("A").getbbox()
    assert 0 < bounds[0] < 16
    assert 0 < bounds[1] < 16
    assert size + 16 < bounds[2] < size + 32
    assert size + 16 < bounds[3] < size + 32
    assert first.getpixel((16 + size // 2, 16 + size // 2)) == (20, 40, 60, 255)
    assert first.tobytes() == (await render(paths)).tobytes()
    fallback = await render(paths.model_copy(update={"centertop": "missing.png"}))
    assert fallback.tobytes() == (await render(None)).tobytes()
    if kind == "combination":
        changed = await render(paths.model_copy(update={"side_right_top": "other-side.png"}))
        delta = ImageChops.difference(first, changed).convert("RGB").getbbox()
        assert delta is not None
        assert delta[0] > size // 2 + 16
        assert delta[3] < size // 2 + 16
