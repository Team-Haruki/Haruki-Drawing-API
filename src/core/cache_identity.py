"""Identity of process-loaded renderer code, native pixels, fonts and local templates.

Content is scanned once, matching the renderer's process-local font/template caches.
Replacing those files requires a restart; remote game assets use Asset-Revision instead.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

_PIXEL_ENV = (
    "HARUKI_SKIA_PNG_ENCODER",
    "HARUKI_SKIA_TEXT_HINTING",
    "HARUKI_SKIA_TEXT_GAMMA",
    "HARUKI_SKIA_RASTER_CACHE_OVERSAMPLE",
    "HARUKI_BG_TEST_HOUR",
)
_PIXEL_SETTINGS = (
    "export_image_format",
    "jpg_quality",
    "use_skia_plot",
    "custom_profile_max_elements",
    "custom_profile_max_scale",
    "custom_profile_max_text_size",
    "custom_profile_max_text_length",
    "custom_profile_max_layer_pixels",
    "custom_profile_max_scene_mb",
)


def _file(digest: Any, label: str, path: Path) -> None:
    digest.update(label.encode())
    digest.update(b"\0")
    if not path.is_file():
        digest.update(b"missing\0")
        return
    content = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            content.update(chunk)
    digest.update(content.digest())


def _tree(digest: Any, label: str, root: Path) -> None:
    digest.update(label.encode())
    if not root.exists():
        digest.update(b"missing\0")
        return
    if root.is_file():
        _file(digest, label, root)
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            _file(digest, str(path.relative_to(root)), path)


@lru_cache(maxsize=1)
def renderer_epoch() -> str | None:
    from src.sekai.base.utils import renderer_code_fingerprint
    from src.settings import settings

    source = renderer_code_fingerprint()
    if source == "unknown":
        return None
    spec = importlib.util.find_spec("haruki_skia_renderer")
    if spec is None or spec.origin is None:
        return None
    origin = Path(spec.origin)
    binaries = sorted(origin.parent.glob("*.so")) if origin.suffix == ".py" else [origin]
    if not binaries:
        return None
    digest = hashlib.sha256(source.encode())
    try:
        for binary in binaries:
            if not binary.is_file():
                return None
            _file(digest, binary.name, binary)
        for role in ("default", "bold", "heavy", "emoji"):
            name = getattr(settings.font, role)
            # Match native resolution candidates, including absent alternatives.
            for suffix in (".otf", ".ttf", ".ttc", ""):
                _file(digest, f"font:{role}:{suffix}", settings.font.dir / (name + suffix))
        _tree(digest, "templates", settings.assets.base_dir / settings.assets.result_asset_path)
        for setting in (
            "custom_profile_assets_dir",
            "custom_profile_fonts_dir",
            "custom_profile_tmp_font_metadata",
            "custom_profile_shape_sprite_dir",
            "custom_profile_unity_ui_sprite_dir",
        ):
            path = getattr(settings.drawing, setting)
            # Region-templated game assets are covered by Cloud's asset revision.
            if path is not None and "{region}" not in str(path):
                _tree(digest, setting, path)
    except OSError:
        return None
    material = {
        "drawing": {name: getattr(settings.drawing, name) for name in _PIXEL_SETTINGS},
        "native": {name: os.environ.get(name, "") for name in _PIXEL_ENV},
    }
    digest.update(json.dumps(material, sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()
