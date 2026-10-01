"""Player frames: Cloud path transport, client prefab geometry, silent fallback and both backends."""

from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageChops
import pytest

from src.sekai.base.plot import Canvas, FillBg, Frame, HSplit, Spacer
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


def _panel_ctx(paths):
    profile = SimpleNamespace(has_frame=paths is not None, frame_paths=paths)
    return SimpleNamespace(request=SimpleNamespace(profile=profile, frame_paths=None))


@pytest.mark.anyio
async def test_profile_info_panel_frame_takes_the_panel_slot_at_card_thickness(tmp_path, monkeypatch):
    """/profile frames the whole info panel with the row sprites, scaled as on the profile card."""
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    paths = _combo()
    _write_sprites(tmp_path, paths, {})
    with HSplit() as row:
        panel = Spacer(600, 700)
        other = Spacer(100, 100)
    framed = await drawer._frame_profile_info_panel(_panel_ctx(paths), panel)
    assert row.items == [framed, other]
    assert framed.items[0] is panel
    box = framed.items[1]
    assert isinstance(box, drawer.PlayerFrameBox)
    assert box.layers.cell == "horizontal"
    assert framed._get_self_size() == (600, 700)
    card_scale = drawer.frame_scale_for(box.layers, (drawer._CARD_W, drawer._PROFILE_PANEL_FRAME_REFERENCE_H))
    # the card's proportions, enlarged uniformly: ring, corners and ornaments share one scale
    assert drawer._PROFILE_PANEL_FRAME_SCALE_FACTOR > 1
    assert box.frame_scale == pytest.approx(card_scale * drawer._PROFILE_PANEL_FRAME_SCALE_FACTOR)
    # no frame, or no readable frame: the panel stays where it was
    plain = Spacer(600, 700)
    assert await drawer._frame_profile_info_panel(_panel_ctx(None), plain) is plain
    (tmp_path / drawer._frame_part_dirs(paths)[1] / "horizontal" / "frame_base.png").unlink()
    assert await drawer._frame_profile_info_panel(_panel_ctx(paths), plain) is plain


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["single", "combination"])
@pytest.mark.parametrize("backend", ["skia", "pillow"])
async def test_profile_info_panel_frame_on_both_backends(tmp_path, monkeypatch, real_fonts, kind, backend):
    if backend == "skia":
        pytest.importorskip("haruki_skia_renderer")
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)
    paths = _single() if kind == "single" else _combo()
    _write_sprites(tmp_path, paths, {})

    async def render(p, scale=1.5):
        async def build():
            with Canvas().set_padding(16) as canvas:
                panel = Frame().set_size((400, 300)).set_bg(FillBg((20, 40, 60, 255)))
            await drawer._frame_profile_info_panel(_panel_ctx(p), panel)
            return canvas

        if backend == "skia":
            payload = await canvas_mod.render_canvas_payload(await build(), export_format="png", scale=scale)
            assert payload is not None
            return Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
        return (await (await build()).get_img(scale)).convert("RGBA")

    framed, plain = await render(paths), await render(None)
    assert framed.size == plain.size == (648, 498)
    # the base ring runs along all four panel edges; the panel centre is untouched
    assert framed.getpixel((324, 249)) == plain.getpixel((324, 249)) == (20, 40, 60, 255)
    for edge in ((324, 25), (324, 472), (25, 249), (622, 249)):
        assert framed.getpixel(edge) != plain.getpixel(edge), edge


@pytest.mark.anyio
async def test_explicit_local_parts_load_independently_without_bundle_derivation(monkeypatch):
    fields = ("base", "lefttop", "righttop", "rightbottom", "leftbottom", "centertop")
    paths = {field: f"static_images/mixed/{i}/chosen.png" for i, field in enumerate(fields)}
    request = _single().model_copy(update={"horizontal": None})
    request = PlayerFramePaths.model_validate({**request.model_dump(), "horizontal": paths})
    requested = []

    async def asset(_root, path, **_kwargs):
        requested.append(path)
        return Image.new("RGBA", (60, 60))

    monkeypatch.setattr(drawer, "get_asset_image_ref", asset)
    layers = await drawer.get_player_frame_layers(request)
    assert requested == [paths[field] for field in fields]
    assert [slot for _, slot in layers.ornaments] == ["tl", "tr", "br", "bl", "tc"]

    async def missing(_root, path, **_kwargs):
        if path == paths["rightbottom"]:
            raise FileNotFoundError(path)
        return Image.new("RGBA", (60, 60))

    monkeypatch.setattr(drawer, "get_asset_image_ref", missing)
    assert await drawer.get_player_frame_layers(request) is None


@pytest.mark.anyio
async def test_wrap_scale_factor_multiplies_whatever_scale_was_chosen(tmp_path, monkeypatch):
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    paths = _single()
    _write_sprites(tmp_path, paths, {})
    plain = await drawer.wrap_with_player_frame(Spacer(400, 300), paths, scale_reference=(470, 126))
    bigger = await drawer.wrap_with_player_frame(Spacer(400, 300), paths, scale_reference=(470, 126), scale_factor=2)
    fixed = await drawer.wrap_with_player_frame(Spacer(400, 300), paths, scale=0.3, scale_factor=2)
    assert bigger.items[1].frame_scale == pytest.approx(2 * plain.items[1].frame_scale)
    assert fixed.items[1].frame_scale == pytest.approx(0.6)
