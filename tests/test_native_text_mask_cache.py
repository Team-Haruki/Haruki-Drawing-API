"""Native text cache hits preserve pixels, live fonts and each request's memory limit."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Barrier

from PIL import Image
import pytest

from src.sekai.skia_renderer.ir_builder import IRBuilder

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

FONT_DIR = Path(__file__).resolve().parents[1] / "data"
REGULAR = FONT_DIR / "SourceHanSansSC-Regular.otf"
BOLD = FONT_DIR / "SourceHanSansSC-Bold.otf"
HAS_CACHE = native is not None and "text_mask_cache_max_bytes" in native.renderer_cache_stats()
pytestmark = pytest.mark.skipif(
    not HAS_CACHE or not REGULAR.is_file() or not BOLD.is_file(),
    reason="native text mask cache and Source Han fixture fonts required",
)


def _scene(
    *,
    directory=FONT_DIR,
    font=REGULAR.name,
    text="未来 AVATAR j 123",
    size=24,
    pos=(12, 57),
    color=(20, 40, 80, 173),
    baseline="alphabetic",
    mask_lerp=False,
    scale=1,
):
    builder = IRBuilder(
        380, 90, assets_base_dir=str(directory), font_dir=str(directory), default_font=font, bold_font=font
    )
    builder.rect((0, 0), (380, 90), fill=(224, 237, 245, 119), blend="src")
    builder.text(
        text, pos, "default", size, baseline=baseline, fill=color, engine="freetype_basic", mask_lerp=mask_lerp
    )
    scene = builder.build()
    scene["scale"] = scale
    return scene


def _render(scene):
    return native.render_scene(json.dumps(scene).encode(), {})["image_bytes"]


def _pixels(encoded):
    with Image.open(BytesIO(encoded)) as image:
        return image.convert("RGBA").tobytes()


def _stats():
    return {
        key.removeprefix("text_mask_cache_"): value
        for key, value in native.renderer_cache_stats().items()
        if key.startswith("text_mask_cache_")
    }


@pytest.fixture(autouse=True)
def clear_native_cache():
    native.clear_renderer_caches()
    yield
    native.clear_renderer_caches()


@pytest.fixture
def enabled_cache():
    if _stats()["max_bytes"] == 0:
        pytest.skip("text mask cache disabled in this process")


@pytest.mark.usefixtures("enabled_cache")
@pytest.mark.parametrize(
    "options",
    [
        {},
        {"pos": (-1.5, 25.5), "size": 20.125},
        {"pos": (15.5, 45.5)},
        {"text": ""},
        {"text": "e\u0301 j", "baseline": "cjk_top"},
        {"baseline": "ascender", "scale": 1.5},
        {"mask_lerp": True},
    ],
)
def test_text_mask_hits_match_cold_pixels_and_keep_placement_outside_cache(options):
    scene = _scene(**options)
    cold = _render(scene)
    assert _stats()["misses"] == 1
    assert _pixels(_render(scene)) == _pixels(cold)
    assert _stats()["hits"] == 1
    assert _stats()["entries"] == 1

    # Reusing coverage must leave position, color and baseline to the current draw.
    changed = deepcopy(scene)
    text_node = changed["root"]["children"][-1]
    text_node["pos"] = [53.5, 44.5]
    text_node["fill"] = [188, 15, 100, 220]
    text_node["baseline"] = "alphabetic"
    warm_changed = _render(changed)
    assert _stats()["entries"] == 1
    assert _stats()["hits"] == 2
    native.clear_renderer_caches()
    assert _pixels(_render(changed)) == _pixels(warm_changed)


@pytest.mark.usefixtures("enabled_cache")
def test_text_and_fractional_size_select_different_masks():
    _render(_scene())
    for options in ({"text": "別のテキスト"}, {"size": 25.375}):
        scene = _scene(**options)
        actual = _render(scene)
        assert _stats()["misses"] == 2
        native.clear_renderer_caches()
        assert _pixels(_render(scene)) == _pixels(actual)
        native.clear_renderer_caches()
        _render(_scene())


@pytest.mark.usefixtures("enabled_cache")
def test_font_mtime_and_replacement_invalidate_warm_masks(tmp_path):
    destination = tmp_path / "font.otf"
    shutil.copyfile(REGULAR, destination)
    scene = _scene(directory=tmp_path, font=destination.name)
    original = _render(scene)
    stat = destination.stat()
    os.utime(destination, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert _pixels(_render(scene)) == _pixels(original)
    assert _stats()["misses"] == 2

    replacement = tmp_path / "replacement.otf"
    shutil.copyfile(BOLD, replacement)
    replacement.replace(destination)
    changed = _render(scene)
    assert _stats()["misses"] == 3
    assert _pixels(changed) != _pixels(original)
    native.clear_renderer_caches()
    assert _pixels(_render(scene)) == _pixels(changed)


@pytest.mark.usefixtures("enabled_cache")
def test_higher_priority_font_arrival_and_removal_choose_live_candidate(tmp_path):
    # The .otf candidate wins over .ttf, including when it arrives after a warm request.
    (tmp_path / "choice.ttf").symlink_to(REGULAR)
    scene = _scene(directory=tmp_path, font="choice")
    original = _render(scene)
    higher = tmp_path / "choice.otf"
    higher.symlink_to(BOLD)
    changed = _render(scene)
    assert _stats()["misses"] == 2
    assert _pixels(changed) != _pixels(original)
    higher.unlink()
    assert _pixels(_render(scene)) == _pixels(original)
    assert _stats()["hits"] == 1


@pytest.mark.usefixtures("enabled_cache")
@pytest.mark.parametrize("warm", [False, True])
def test_concurrent_requests_keep_different_memory_budgets_independent(warm):
    large = _scene(text="同じ文章 AVATAR 123")
    expected = _render(large)
    if not warm:
        native.clear_renderer_caches()
    small = deepcopy(large)
    small["limits"] = {"max_node_pixels": 1}
    barrier = Barrier(8)

    def render(index):
        barrier.wait(timeout=20)
        if index % 2:
            with pytest.raises(RuntimeError, match="native text mask exceeds pixel limit"):
                _render(small)
            return None
        return _render(large)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outputs = list(pool.map(render, range(8)))
    assert all(_pixels(output) == _pixels(expected) for output in outputs if output is not None)
    assert _pixels(_render(large)) == _pixels(expected)


_CONFIG_PROBE = """
import json
import sys
import haruki_skia_renderer as native

scenes = json.load(sys.stdin)
native.clear_renderer_caches()
images = [native.render_scene(json.dumps(scene).encode(), {})['image_bytes'].hex() for scene in scenes]
stats = native.renderer_cache_stats()
native.clear_renderer_caches()
cleared = native.renderer_cache_stats()
print(json.dumps({'images': images, 'stats': stats, 'cleared': cleared}))
"""


def _config_probe(megabytes, scenes):
    completed = subprocess.run(
        [sys.executable, "-X", "gil=0", "-c", _CONFIG_PROBE],
        input=json.dumps(scenes),
        text=True,
        capture_output=True,
        env={**os.environ, "HARUKI_SKIA_TEXT_MASK_CACHE_MB": str(megabytes)},
        check=True,
        timeout=45,
    )
    return json.loads(completed.stdout)


def test_zero_cache_setting_preserves_pixels_without_retaining_masks():
    scene = _scene(mask_lerp=True)
    enabled = _config_probe(64, [scene, scene])
    disabled = _config_probe(0, [scene, scene])
    assert enabled["stats"]["text_mask_cache_hits"] == 1
    for actual, expected in zip(disabled["images"], enabled["images"], strict=True):
        assert _pixels(bytes.fromhex(actual)) == _pixels(bytes.fromhex(expected))
    stats = disabled["stats"]
    assert stats["text_mask_cache_max_bytes"] == 0
    assert stats["text_mask_cache_entries"] == stats["text_mask_cache_bytes"] == 0
    assert stats["text_mask_cache_hits"] == stats["text_mask_cache_misses"] == 0
    assert stats["text_mask_cache_bypasses"] == 2


def test_oversized_mask_is_not_admitted_and_clear_resets_observability():
    scene = _scene(text="W" * 180, size=128)
    probe = _config_probe(1, [scene, scene])
    stats = probe["stats"]
    assert stats["text_mask_cache_max_bytes"] == 1024 * 1024
    assert stats["text_mask_cache_entries"] == stats["text_mask_cache_bytes"] == 0
    assert stats["text_mask_cache_bypasses"] == 2
    assert stats["text_mask_cache_misses"] == 2
    assert _pixels(bytes.fromhex(probe["images"][0])) == _pixels(bytes.fromhex(probe["images"][1]))
    cleared = probe["cleared"]
    assert all(cleared[f"text_mask_cache_{key}"] == 0 for key in ("entries", "bytes", "hits", "misses", "bypasses"))
