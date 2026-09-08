"""The retirement gate exercises cold imports and cannot accept a fallback result."""

from dataclasses import replace

import pytest

from scripts.skia_no_pillow import run_clean_case
from scripts.skia_parity_sweep import CASES


def _case(tmp_path, monkeypatch, body):
    pytest.importorskip("haruki_skia_renderer")
    module = tmp_path / "retirement_fixture.py"
    module.write_text("class Request:\n    @classmethod\n    def model_validate(cls, raw): return raw\n" + body)
    payload = tmp_path / "payload.json"
    payload.write_text("{}")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    case = replace(
        CASES[0],
        drawer="retirement_fixture",
        model_module="retirement_fixture",
        model_cls="Request",
        try_render="render",
        try_render_module=None,
        is_list=False,
    )
    return run_clean_case(case, payload, timeout=30)


def test_clean_process_rejects_pillow_import(tmp_path, monkeypatch):
    result = _case(tmp_path, monkeypatch, "from PIL import Image\nasync def render(req): return None\n")
    assert result["status"] == "blocked"
    assert "rejected import: PIL" in result["error"]


def test_clean_process_rejects_silent_fallback(tmp_path, monkeypatch):
    result = _case(tmp_path, monkeypatch, "async def render(req): return None\n")
    assert result["status"] == "blocked"
    assert "returned fallback" in result["error"]


def test_clean_process_rejects_cached_or_fabricated_native_result(tmp_path, monkeypatch):
    result = _case(tmp_path, monkeypatch, "async def render(req): return object()\n")
    assert result["status"] == "blocked"
    assert "did not successfully execute" in result["error"]


@pytest.mark.parametrize("suppress_import", [False, True])
def test_clean_process_accepts_only_untouched_native_output(tmp_path, monkeypatch, suppress_import):
    result = _case(
        tmp_path,
        monkeypatch,
        ("try:\n    from PIL import Image\nexcept RuntimeError:\n    pass\n" if suppress_import else "")
        + """
import json
import os
from types import SimpleNamespace
import haruki_skia_renderer as native
from src.sekai.profile.custom_profile.cache import GLYPH_SDF_CACHE
assert os.environ["HARUKI_SKIA_TEXT_MASK_CACHE_MB"] == "0"
assert not GLYPH_SDF_CACHE.enabled
async def render(req):
    scene = {
        "version": 2, "assets_base_dir": ".", "export_format": "png",
        "fonts": {"dir": ".", "default": "unused.ttf", "bold": "unused.ttf"},
        "canvas": {"width": 2, "height": 3},
        "root": {"type": "Group", "offset": [0, 0], "size": [2, 3], "children": [
            {"type": "Rect", "pos": [0, 0], "size": [2, 3], "fill": [1, 2, 3, 255]}
        ]}
    }
    result = native.render_scene(json.dumps(scene).encode(), {})
    return SimpleNamespace(**result)
""",
    )
    if suppress_import:
        assert result["status"] == "blocked"
        assert "attempted and suppressed" in result["error"]
    else:
        assert result == {"status": "ok", "native_renders": 1, "size": [2, 3]}


@pytest.mark.parametrize(
    "widget",
    [
        "_circular_progress_avatar(source, 64, 0.25, (255, 0, 0, 255))",
        "AlphaTrimImageBox(source, probe_alpha_bounds(source), 36, size=(64, 64))",
        "PreResizedImageBox(source, pre_size=(40, 40), size=(64, 64)).set_padding(4)",
    ],
)
def test_shared_widgets_render_lazy_encoded_source_without_pillow(tmp_path, monkeypatch, widget):
    from io import BytesIO

    from PIL import Image

    source = BytesIO()
    Image.new("RGBA", (31, 27), (40, 90, 160, 255)).save(source, "PNG")
    result = _case(
        tmp_path,
        monkeypatch,
        f"""
from src.sekai.base.plot import AlphaTrimImageBox, Canvas, PreResizedImageBox
from src.sekai.base.image_info import probe_alpha_bounds
from src.sekai.base.utils import get_encoded_image_ref
from src.sekai.card.drawer import _circular_progress_avatar
from src.sekai.skia_renderer.canvas import render_canvas_payload
async def render(req):
    source = get_encoded_image_ref({source.getvalue()!r})
    with Canvas(w=64, h=64).set_padding(0) as canvas:
        {widget}
    return await render_canvas_payload(canvas, endpoint="test_circle", export_format="png")
""",
    )
    assert result == {"status": "ok", "native_renders": 1, "size": [64, 64]}


def test_help_entrypoint_renders_entire_panel_without_pillow(tmp_path, monkeypatch):
    from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT, FONT_DIR

    if not all(
        any((FONT_DIR / f"{name}{suffix}").is_file() for suffix in (".otf", ".ttf", ".ttc", ""))
        for name in (DEFAULT_FONT, DEFAULT_BOLD_FONT, DEFAULT_HEAVY_FONT)
    ):
        pytest.skip("help fixture fonts required")
    result = _case(
        tmp_path,
        monkeypatch,
        r"""
from src.sekai.misc.drawer import try_render_command_help_payload
from src.sekai.misc.model import CommandHelpRenderRequest
async def render(req):
    return await try_render_command_help_payload(CommandHelpRenderRequest(
        markdown="# 帮助\n## 查询\n- 名称: 未来 Haruki\n\n```\nhelp test 😀\n```"
    ))
""",
    )
    assert result["status"] == "ok", result
    assert result["native_renders"] == 1
