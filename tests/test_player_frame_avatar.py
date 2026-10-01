"""Player frames: Cloud path transport, client prefab geometry, silent fallback and both backends."""

from io import BytesIO

from PIL import Image, ImageChops
import pytest

from src.sekai.base.plot import Canvas, Spacer
from src.sekai.profile import drawer
from src.sekai.profile.model import DetailedProfileCardRequest, PlayerFramePaths, ProfileCardRequest
from src.sekai.skia_renderer import canvas as canvas_mod

ROOT = "asset/jp-assets/startapp/player_frame"


def _single(fid=10001):
    # exactly what Cloud sends today: the vertical cell's sprites
    r = f"{ROOT}/frame_0001/{fid}/vertical/frame_"
    return PlayerFramePaths(
        base=r + "base.png",
        centertop=r + "centertop.png",
        lefttop=r + "lefttop.png",
        righttop=r + "righttop.png",
        leftbottom=r + "leftbottom.png",
        rightbottom=r + "rightbottom.png",
    )


def _combo(parts=(20021, 20017, 20013, 20009, 20005, 20001)):
    p = lambda i, name: f"{ROOT}/frame_0002/20001/{parts[i - 1]}/vertical/frame_{name}.png"  # noqa: E731
    return PlayerFramePaths(
        frame_type="combination",
        base=p(1, "base"),
        lefttop=p(1, "parts1_left"),
        righttop=p(1, "parts1_right"),
        centertop=p(1, "parts1_center"),
        side_left_top=p(2, "parts2_left"),
        side_right_top=p(3, "parts3_right"),
        side_left_bottom=p(4, "parts4_left"),
        side_right_bottom=p(5, "parts5_right"),
        leftbottom=p(6, "parts6_left"),
        rightbottom=p(6, "parts6_right"),
    )


def _sprite_size(path: str) -> tuple[int, int]:
    name = path.rsplit("/", 1)[-1]
    if name == "frame_base.png":
        return (60, 60)
    if "center" in name:
        return (62, 36)
    return (536, 82)


@pytest.mark.anyio
async def test_cloud_vertical_paths_resolve_to_horizontal_row_sprites(monkeypatch):
    requested = []

    async def asset(_root, path, **_kwargs):
        requested.append(path)
        return Image.new("RGBA", _sprite_size(path))

    monkeypatch.setattr(drawer, "get_asset_image_ref", asset)
    single = await drawer.get_player_frame_layers(_single())
    # FriendListCell prefab sibling order: TL, TR, BR, BL, then the crown on top
    assert requested == [
        f"{ROOT}/frame_0001/10001/horizontal/frame_{name}.png"
        for name in ("base", "lefttop", "righttop", "rightbottom", "leftbottom", "centertop")
    ]
    assert [slot for _, slot in single.ornaments] == ["tl", "tr", "br", "bl", "tc"]

    requested.clear()
    combo = await drawer.get_player_frame_layers(_combo())
    # PlayerFrameCombination6PartsView.LoadPartSpritesHorizontal: part2/3 on the left strips,
    # part4/5 on the right ones, part6 owns the right corners, part1 the base, left corners and crown.
    by_sprite = {path.rsplit("/", 3)[-3] + ":" + path.rsplit("/", 1)[-1]: path for path in requested}
    assert set(by_sprite) == {
        "20021:frame_base.png",
        "20021:frame_parts1_top.png",
        "20021:frame_parts1_bottom.png",
        "20021:frame_parts1_center.png",
        "20017:frame_parts2_top.png",
        "20013:frame_parts3_bottom.png",
        "20009:frame_parts4_top.png",
        "20005:frame_parts5_bottom.png",
        "20001:frame_parts6_top.png",
        "20001:frame_parts6_bottom.png",
    }
    assert [slot for _, slot in combo.ornaments] == ["br", "bl", "tr", "tl", "tl", "tr", "br", "bl", "tc"]

    vertical = await drawer.get_player_frame_layers(_single(), "vertical")
    assert vertical.cell == "vertical"
    # a path that does not end in <bundle>/<cell>/<sprite> or a missing part is no frame, not a guess
    assert await drawer.get_player_frame_layers(_single().model_copy(update={"base": "frame_base.png"})) is None
    assert await drawer.get_player_frame_layers(_combo().model_copy(update={"side_left_top": None})) is None


@pytest.mark.anyio
async def test_frame_scale_never_lets_the_row_strips_overlap(monkeypatch):
    async def asset(_root, path, **_kwargs):
        return Image.new("RGBA", _sprite_size(path))

    monkeypatch.setattr(drawer, "get_asset_image_ref", asset)
    layers = await drawer.get_player_frame_layers(_combo())
    # the client row (1542x146) maps 1:1
    assert drawer.frame_scale_for(layers, (1542, 146)) == pytest.approx(1.0)
    # a narrow card is limited by width: 536-8 per side must fit
    s = drawer.frame_scale_for(layers, (470, 120))
    assert s == pytest.approx(470 / (2 * (536 - 8)))
    assert 2 * (536 - 8) * s <= 470 + 1e-6


@pytest.mark.anyio
@pytest.mark.parametrize("broken", ["missing", "corrupt"])
async def test_unreadable_frame_silently_keeps_the_card_and_recovers(tmp_path, monkeypatch, caplog, broken):
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    paths = _single()
    for name in ("base", "lefttop", "righttop", "leftbottom", "rightbottom"):
        target = tmp_path / f"{ROOT}/frame_0001/10001/horizontal/frame_{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", _sprite_size(target.name)).save(target)
    center = tmp_path / f"{ROOT}/frame_0001/10001/horizontal/frame_centertop.png"
    if broken == "corrupt":
        center.write_bytes(b"not an image")

    card = Spacer(200, 60)
    assert await drawer.wrap_with_player_frame(card, paths) is card
    assert not [record for record in caplog.records if record.levelno >= 30]

    Image.new("RGBA", (62, 36)).save(center)
    framed = await drawer.wrap_with_player_frame(Spacer(200, 60), paths)
    assert isinstance(framed.items[1], drawer.PlayerFrameBox)
    assert framed._get_self_size() == (200, 60)


def _write_sprites(tmp_path, paths: PlayerFramePaths, colors: dict[str, tuple[int, int, int, int]]):
    """Solid sprites in their real sizes; ``colors`` maps a part directory to its fill."""
    for kind in ("horizontal",):
        dirs = drawer._frame_part_dirs(paths)
        for part, name, _slot in [(1, "base", None), *drawer._FRAME_SPRITES[paths.frame_type][kind]]:
            target = tmp_path / f"{dirs[part]}/{kind}/frame_{name}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            color = colors.get(dirs[part].rsplit("/", 1)[-1], (0, 0, 0, 0))
            if name == "base":
                color = (200, 200, 200, 255)
                image = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
                image.paste(color, (0, 0, 60, 4))
                image.paste(color, (0, 56, 60, 60))
                image.paste(color, (0, 0, 4, 60))
                image.paste(color, (56, 0, 60, 60))
            else:
                image = Image.new("RGBA", _sprite_size(target.name), color)
            image.save(target)


def _card_request(paths) -> ProfileCardRequest:
    return DetailedProfileCardRequest(
        id="123456789",
        region="jp",
        nickname="Frame",
        source="suite",
        update_time=0,
        leader_image_path="avatar.png",
        has_frame=paths is not None,
        frame_paths=paths,
    ).to_profile_card_request()


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["skia", "pillow"])
async def test_framed_card_renders_on_both_backends_with_parts_on_their_sides(
    tmp_path, monkeypatch, real_fonts, backend
):
    if backend == "skia":
        pytest.importorskip("haruki_skia_renderer")
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)
    Image.new("RGBA", (128, 128), (20, 40, 60, 255)).save(tmp_path / "avatar.png")
    paths = _combo()
    left, right = (220, 40, 40, 255), (40, 40, 220, 255)
    # parts 2/3 dress the left strips, 4/5 the right ones (horizontal cell)
    _write_sprites(tmp_path, paths, {"20017": left, "20013": left, "20009": right, "20005": right})

    async def render(p, scale=1.5):
        async def build():
            with Canvas().set_padding(16) as canvas:
                await drawer.get_profile_card(_card_request(p))
            return canvas

        if backend == "skia":
            payload = await canvas_mod.render_canvas_payload(await build(), export_format="png", scale=scale)
            assert payload is not None
            return Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
        return (await (await build()).get_img(scale)).convert("RGBA")  # must not trip on fractional sizes

    framed, plain = await render(paths), await render(None)
    assert framed.size == plain.size  # the frame never changes the card's layout
    delta = ImageChops.difference(framed, plain).getbbox()
    assert delta is not None
    w = framed.width
    reds = [x for x in range(w) if framed.getpixel((x, 16 * 3 // 2))[:3] == left[:3]]
    blues = [x for x in range(w) if framed.getpixel((x, 16 * 3 // 2))[:3] == right[:3]]
    assert reds
    assert blues
    assert max(reds) <= w // 2 + 1 < min(blues) + 2


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["skia", "pillow"])
async def test_framed_card_keeps_the_avatar_well(tmp_path, monkeypatch, real_fonts, backend):
    if backend == "skia":
        pytest.importorskip("haruki_skia_renderer")
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    Image.new("RGBA", (128, 128), (20, 40, 60, 255)).save(tmp_path / "avatar.png")
    _write_sprites(tmp_path, _single(), {})
    framed = await drawer._build_profile_card_avatar_module(_card_request(_single()))
    plain = await drawer._build_profile_card_avatar_module(_card_request(None))
    well = drawer._CARD_AVATAR_WELL
    assert framed._get_self_size() == plain._get_self_size() == (well, well)


def _write_vertical_sprites(tmp_path, paths: PlayerFramePaths, color=(220, 40, 40, 255)):
    dirs = drawer._frame_part_dirs(paths)
    sizes = {"base": (132, 132), "centertop": (108, 90), "parts1_center": (108, 90)}
    for part, name, _slot in [(1, "base", None), *drawer._FRAME_SPRITES[paths.frame_type]["vertical"]]:
        target = tmp_path / f"{dirs[part]}/vertical/frame_{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        size = sizes.get(name, (130, 352))
        if name == "base":
            image = Image.new("RGBA", size, (0, 0, 0, 0))
            for box in ((0, 0, 132, 8), (0, 124, 132, 132), (0, 0, 8, 132), (124, 0, 132, 132)):
                image.paste((200, 200, 200, 255), box)
        else:
            image = Image.new("RGBA", size, color)
        image.save(target)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["single", "combination"])
@pytest.mark.parametrize("backend", ["skia", "pillow"])
async def test_profile_page_avatar_frame_on_both_backends(tmp_path, monkeypatch, real_fonts, kind, backend):
    """/profile keeps the vertical sprites around the 128 px avatar; scale 1.5 is the page's."""
    if backend == "skia":
        pytest.importorskip("haruki_skia_renderer")
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)
    Image.new("RGBA", (128, 128), (20, 40, 60, 255)).save(tmp_path / "avatar.png")
    paths = _single() if kind == "single" else _combo()
    _write_vertical_sprites(tmp_path, paths)

    async def render(p, scale=1.5):
        async def build():
            avatar = await drawer.get_asset_image_ref(tmp_path, "avatar.png")
            with Canvas().set_padding(16) as canvas:
                await drawer.get_avatar_widget_with_frame(p is not None, p, avatar, 128, [])
            return canvas

        if backend == "skia":
            payload = await canvas_mod.render_canvas_payload(await build(), export_format="png", scale=scale)
            assert payload is not None
            return Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
        return (await (await build()).get_img(scale)).convert("RGBA")

    framed, plain = await render(paths), await render(None)
    assert framed.size == plain.size == (240, 240)
    # ornaments overhang the avatar (outset + the prefab's +32 / -16 offsets) but stay in the padding
    bounds = framed.getchannel("A").getbbox()
    assert 0 < bounds[0] < 24
    assert 0 < bounds[1] < 24
    assert 216 < bounds[2] <= 240
    assert 216 < bounds[3] <= 240
    assert framed.getpixel((120, 120)) == plain.getpixel((120, 120)) == (20, 40, 60, 255)
    assert ImageChops.difference(framed, plain).getbbox() is not None

    # a missing sprite silently leaves the bare avatar
    (tmp_path / drawer._frame_part_dirs(paths)[1] / "vertical" / "frame_base.png").unlink()
    assert (await render(paths)).tobytes() == plain.tobytes()
