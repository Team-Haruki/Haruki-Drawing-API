"""Custom-profile resources on a render node with no rsynced ``<cc>-assets`` tree (``assets.source=mirror``)."""

from io import BytesIO
import math
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from src.assets.mirror import NullMirror, set_asset_mirror
from src.sekai.profile.custom_profile.renderer import (
    PROFILE_RENDER_VIEW_H,
    PROFILE_RENDER_VIEW_W,
    PNGRenderer,
    font_file,
)
from src.sekai.profile.custom_profile.resource_paths import (
    _require_region_path,
    asset_file,
    bucket_asset_key,
    first_asset_file,
)
from src.sekai.skia_renderer.canvas import load_native_renderer
from tests.profile_font_fixture import build_tmp_fixture

STAMP_KEY = "asset/jp-assets/startapp/stamp/stamp0001/stamp0001.png"


def _png(color=(0, 128, 255, 255), size=(8, 8)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _put(mirror, key: str, data: bytes) -> None:
    mirror.store_for("jp").objects[key.removeprefix("asset/")] = data


@pytest.fixture
def null_mirror():
    set_asset_mirror(NullMirror())
    try:
        yield
    finally:
        set_asset_mirror(None)


def _custom_profile_dir(root: Path, region: str = "jp") -> Path:
    return root / "asset" / f"{region}-assets" / "startapp" / "custom_profile"


def _renderer(root: Path, **kwargs) -> PNGRenderer:
    assets = _custom_profile_dir(root)
    return PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=assets / "font",
        tmp_font_metadata=None,
        shape_sprite_dir=assets / "shape",
        unity_ui_sprite_dir=root / "ui",
        region="jp",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("path", "key"),
    [
        (
            "/data/asset/jp-assets/startapp/custom_profile/font/a.otf",
            "asset/jp-assets/startapp/custom_profile/font/a.otf",
        ),
        ("data/mirror/v0/cn-assets/ondemand/music/x.png", "asset/cn-assets/ondemand/music/x.png"),
        ("asset/kr-assets/startapp/stamp/s/s.png", "asset/kr-assets/startapp/stamp/s/s.png"),
        ("/data/static_images/customprofile/bg.png", None),
        ("/data/custom_profile/tmp-font-assets/jp/metadata.json", None),
        ("/data/asset/xx-assets/startapp/a.png", None),
        ("/data/asset/jp-assets/other/a.png", None),
        ("/data/asset/jp-assets/startapp", None),
    ],
)
def test_bucket_asset_key_only_maps_region_asset_paths(path, key) -> None:
    assert bucket_asset_key(path) == key


def test_absent_bucket_backed_dir_is_accepted_only_with_a_mirror(tmp_path, asset_mirror) -> None:
    configured = tmp_path / "asset" / "{region}-assets" / "startapp" / "custom_profile"
    assert _require_region_path("custom_profile_assets_dir", configured, "jp") == _custom_profile_dir(tmp_path)
    # A directory the bucket cannot serve still fails fast.
    with pytest.raises(FileNotFoundError):
        _require_region_path("custom_profile_unity_ui_sprite_dir", tmp_path / "ui", "jp")


def test_absent_bucket_backed_dir_still_fails_fast_without_a_mirror(tmp_path, null_mirror) -> None:
    configured = tmp_path / "asset" / "{region}-assets" / "startapp" / "custom_profile"
    with pytest.raises(FileNotFoundError, match="custom_profile_assets_dir"):
        _require_region_path("custom_profile_assets_dir", configured, "jp")


def test_asset_file_prefers_local_and_fetches_bucket_misses(tmp_path, asset_mirror) -> None:
    local = tmp_path / "asset" / "jp-assets" / "startapp" / "stamp" / "local.png"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"local")
    assert asset_file(local) == local

    _put(asset_mirror, STAMP_KEY, b"remote")
    fetched = asset_file(tmp_path / "anywhere" / STAMP_KEY.removeprefix("asset/"))
    assert fetched is not None
    assert fetched.read_bytes() == b"remote"
    assert fetched.is_relative_to(asset_mirror.mirror_root)

    assert asset_file(tmp_path / "asset" / "jp-assets" / "startapp" / "stamp" / "absent.png") is None
    assert asset_file(tmp_path / "static_images" / "absent.png") is None


def test_first_asset_file_keeps_candidate_order_across_local_and_bucket(tmp_path, asset_mirror) -> None:
    fonts = _custom_profile_dir(tmp_path) / "font"
    fonts.mkdir(parents=True)
    (fonts / "Face.otf").write_bytes(b"local otf")
    _put(asset_mirror, "asset/jp-assets/startapp/custom_profile/font/Face.ttf", b"bucket ttf")

    # The earlier candidate exists only in the bucket: it wins, as it would with a full local tree.
    picked = first_asset_file([fonts / "Face.ttf", fonts / "Face.otf"])
    assert picked is not None
    assert picked.read_bytes() == b"bucket ttf"
    assert first_asset_file([fonts / "Face.otf", fonts / "Face.ttf"]) == fonts / "Face.otf"
    assert first_asset_file([fonts / "Missing.otf"]) is None

    # The same key under several data roots reaches the bucket once per lookup.
    store = asset_mirror.store_for("jp")
    store.reads.clear()
    rel = Path("jp-assets/startapp/honor/none/degree_main.png")
    assert first_asset_file([tmp_path / "asset" / rel, tmp_path / rel, tmp_path / "x" / rel]) is None
    assert store.reads == [rel.as_posix()]


def test_first_asset_file_is_plain_exists_without_a_mirror(tmp_path, null_mirror) -> None:
    fonts = _custom_profile_dir(tmp_path) / "font"
    fonts.mkdir(parents=True)
    (fonts / "Face.otf").write_bytes(b"local otf")
    assert first_asset_file([fonts / "Face.ttf", fonts / "Face.otf"]) == fonts / "Face.otf"


def test_font_file_fetches_the_font_and_its_default(tmp_path, asset_mirror) -> None:
    fonts = _custom_profile_dir(tmp_path) / "font"
    _put(asset_mirror, "asset/jp-assets/startapp/custom_profile/font/FOT-RodinNTLGPro-DB.ttf", b"rodin ttf")
    _put(asset_mirror, "asset/jp-assets/startapp/custom_profile/font/FOT-RodinNTLGPro-DB.otf", b"rodin otf")

    assert font_file(fonts, "FOT-RodinNTLGPro-DB", "ttf").read_bytes() == b"rodin ttf"
    assert font_file(fonts, "FOT-RodinNTLGPro-DB", "otf").read_bytes() == b"rodin otf"
    # An unknown face falls back to the default font, which has to be fetched as well.
    assert font_file(fonts, "Unknown-Face").read_bytes() == b"rodin otf"


def test_request_bucket_keys_resolve_through_the_mirror(tmp_path, asset_mirror) -> None:
    renderer = _renderer(tmp_path)
    _put(asset_mirror, STAMP_KEY, _png())

    for raw in (STAMP_KEY, STAMP_KEY.removeprefix("asset/"), f"{STAMP_KEY}/"):
        resolved = renderer.resolve_request_asset_path(raw)
        assert resolved is not None, raw
        assert resolved.is_relative_to(asset_mirror.mirror_root), raw
    # A candidate list resolves its first fetchable candidate.
    missing = "asset/jp-assets/startapp/stamp/stamp0009/stamp0009.png"
    assert renderer.resolve_request_asset_path([missing, STAMP_KEY]) is not None
    # Only a path that IS a bucket key is fetched; one merely containing a key segment, or an absolute
    # path (always local), stays a miss.
    assert renderer.resolve_request_asset_path(f"elsewhere/{STAMP_KEY}") is None
    assert renderer.resolve_request_asset_path(f"/{STAMP_KEY}") is None
    with pytest.raises(ValueError, match="traversal"):
        renderer.resolve_request_asset_path("asset/jp-assets/startapp/../../secret.png")


def test_card_member_image_resolves_through_the_mirror(tmp_path, asset_mirror) -> None:
    key = "asset/jp-assets/startapp/character/member_cutout/res001_no001/normal.png"
    _put(asset_mirror, key, _png())
    renderer = _renderer(tmp_path, resources={"cardAssets": {"1": {"id": 1, "deckNormalPath": key}}})

    path = renderer.card_member_image_path({"id": 1, "type": 1})
    assert path is not None
    assert path.is_relative_to(asset_mirror.mirror_root)


def test_native_scene_initialises_without_local_custom_profile_dirs(tmp_path, monkeypatch, asset_mirror) -> None:
    from src.sekai.profile.custom_profile import skia
    import src.settings as settings_mod

    root = tmp_path / "asset" / "{region}-assets" / "startapp" / "custom_profile"
    (tmp_path / "ui").mkdir()
    monkeypatch.setattr(settings_mod, "CUSTOM_PROFILE_ASSETS_DIR", root)
    monkeypatch.setattr(settings_mod, "CUSTOM_PROFILE_FONTS_DIR", root / "font")
    monkeypatch.setattr(settings_mod, "CUSTOM_PROFILE_SHAPE_SPRITE_DIR", root / "shape")
    monkeypatch.setattr(settings_mod, "CUSTOM_PROFILE_TMP_FONT_METADATA", None)
    monkeypatch.setattr(settings_mod, "CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR", tmp_path / "ui")

    class _Native:
        def render_scene(self, ir_json, mem_images):
            return {"rendered": True}

    result, report = skia._render_custom_profile_native_scene(_Native(), {"customProfileCard": {}}, {}, {}, "jp")
    assert result == {"rendered": True}
    assert report.complete

    set_asset_mirror(NullMirror())
    with pytest.raises(skia._CustomProfileSkiaStageError, match="renderer_init") as exc_info:
        skia._render_custom_profile_native_scene(_Native(), {"customProfileCard": {}}, {}, {}, "jp")
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


def test_native_stamp_from_the_bucket_matches_the_local_tree(tmp_path, monkeypatch, asset_mirror) -> None:
    from src.sekai.profile.custom_profile import skia

    try:
        native = load_native_renderer()
    except ImportError:
        pytest.skip("native renderer required")
    fixture = build_tmp_fixture(tmp_path / "fixture")
    layer = Image.new("RGBA", (64, 64))
    layer.putdata([(x * 4, y * 4, 128, 255 if 8 < x < 55 and 8 < y < 55 else 0) for y in range(64) for x in range(64)])
    buffer = BytesIO()
    layer.save(buffer, format="PNG")
    fixture.resources["stampAssets"] = [{"id": 1, "imagePath": STAMP_KEY}]
    monkeypatch.setattr(skia, "ASSETS_BASE_DIR", tmp_path)
    width, height = int(PROFILE_RENDER_VIEW_W), int(PROFILE_RENDER_VIEW_H)
    half = math.radians(17) / 2
    card = {
        "customProfileCard": {
            "stamps": [
                {
                    "id": 1,
                    "objectData": {
                        "position": {"x": 120, "y": 80},
                        "scale": {"x": 1.3, "y": 0.7},
                        "rotation": {"z": math.sin(half), "w": math.cos(half)},
                        "visible": True,
                        "layer": 1,
                    },
                }
            ]
        },
    }

    def render() -> tuple[np.ndarray, int]:
        renderer = _renderer(
            tmp_path,
            resources=fixture.resources,
            canvas_w=width,
            canvas_h=height,
            origin_x=width / 2,
            origin_y=height / 2,
        )
        scene, memory, report = skia._build_scene(renderer, card)
        assert report.complete
        result = native.render_scene(scene, memory)
        return np.asarray(Image.open(BytesIO(result["image_bytes"])).convert("RGBA")), report.native_elements

    _put(asset_mirror, STAMP_KEY, buffer.getvalue())
    from_bucket, native_elements = render()
    assert native_elements == 1
    assert (asset_mirror.mirror_root / "v0" / STAMP_KEY.removeprefix("asset/")).is_file()

    local = tmp_path / STAMP_KEY
    local.parent.mkdir(parents=True)
    local.write_bytes(buffer.getvalue())
    set_asset_mirror(NullMirror())
    from_local, _ = render()
    assert np.array_equal(from_bucket, from_local)
