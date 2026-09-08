"""Verify built wheels actually decode supported sources without importing Pillow."""

import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.skia_no_pillow import _NoPillow


def main():
    guard = _NoPillow()
    sys.meta_path.insert(0, guard)
    import haruki_skia_renderer as native

    assert native.GRAY_FIELD_CAPABILITY >= 2
    assert native.ALPHA_FIELD_CAPABILITY >= 1
    assert native.asset_alpha_field(
        str(ROOT / "rust/haruki_skia_renderer/tests/fixtures"), "webp_smoke.webp", 17 * 13
    ) == (17, 13, bytes([255]) * (17 * 13))
    # Fixed gray8 bicubic edge golden; exercise actual filtering, not a same-size copy.
    assert native.resize_gray8_bicubic(bytes([0, 255]), (2, 1), (3, 1)) == bytes([0, 128, 255])
    assert native.transform_gray8_bicubic(bytes([0, 255]), (2, 1), (1, 1), (0, 0, 1, 0, 0, 0.5)) == bytes([127])
    source = (ROOT / "rust/haruki_skia_renderer/tests/fixtures/webp_smoke.webp").read_bytes()
    info = native.encoded_image_info(source)
    assert (info["width"], info["height"]) == (17, 13)
    assert native.encoded_alpha_bounds(source) == [0, 0, 17, 13]
    assert native.encoded_foreground_bounds(source, 700) is None
    scene = {
        "version": 2,
        "assets_base_dir": ".",
        "export_format": "png",
        "fonts": {"dir": ".", "default": "unused", "bold": "unused"},
        "canvas": {"width": 17, "height": 13},
        "root": {"type": "Group", "offset": [0, 0], "size": [17, 13], "children": []},
    }
    blank = native.render_scene(json.dumps(scene).encode(), {})["image_bytes"]
    scene["root"]["children"].append({"type": "Image", "pos": [0, 0], "size": [17, 13], "path": "mem:webp"})
    rendered = native.render_scene(json.dumps(scene).encode(), {"webp": source})["image_bytes"]
    assert rendered != blank, "native renderer silently dropped the WebP source"
    scene["post_resize"] = {"width": 29, "height": 23}
    resized = native.render_scene(json.dumps(scene).encode(), {"webp": source})["image_bytes"]
    resized_info = native.encoded_image_info(resized)
    assert (resized_info["width"], resized_info["height"]) == (29, 23)
    scene.pop("post_resize")
    scene["root"]["children"] = [
        {
            "type": "VectorPath",
            "fill": [10, 20, 30, 255],
            "commands": [
                {"op": "move", "points": [3, 4]},
                {"op": "line", "points": [12, 4]},
                {"op": "line", "points": [12, 10]},
                {"op": "line", "points": [3, 10]},
                {"op": "close"},
            ],
        }
    ]
    vector = native.render_scene(json.dumps(scene).encode(), {})["image_bytes"]
    assert native.encoded_alpha_bounds(vector) == [3, 4, 12, 10], "native vector path was omitted or misplaced"
    scene["root"]["children"][0]["commands"] = [{"op": "ellipse", "points": [3, 4, 12, 10]}]
    ellipse = native.render_scene(json.dumps(scene).encode(), {})["image_bytes"]
    assert native.encoded_alpha_bounds(ellipse) == [3, 4, 12, 10]
    assert ellipse != vector, "native ellipse command was dropped or replayed as a rectangle"
    assert native.RAW_BUFFER_CAPABILITY >= 3
    scene["root"]["children"] = [
        {
            "type": "SdfQuad",
            "pos": [0, 0],
            "field": "mem:field",
            "shading": {"face_color": [20, 60, 90], "face_scale": 1000, "face_w": 500, "alpha": 1},
        }
    ]
    float_glyph = native.render_scene(
        json.dumps(scene).encode(), {"field": (1, 1, 4, "f32le", "unpremul", struct.pack("<f", 0.5005))}
    )["image_bytes"]
    quantized_glyph = native.render_scene(
        json.dumps(scene).encode(), {"field": (1, 1, 1, "a8", "unpremul", bytes([128]))}
    )["image_bytes"]
    assert native.encoded_alpha_bounds(float_glyph) == [0, 0, 1, 1]
    assert float_glyph != quantized_glyph, "float distance samples were quantized to A8"
    assert native.TEXT_METRICS_CAPABILITY >= 3
    assert native.TEXT_MASK_CAPABILITY >= 1
    try:
        native.basic_text_mask(".", "unused", "hello", 0, 100)
    except ValueError:
        pass
    else:
        raise AssertionError("BASIC mask API did not validate size before font access")
    try:
        native.vector_text_geometry(".", "unused", "hello", 0)
    except ValueError:
        pass
    else:
        raise AssertionError("vector font API did not validate size before font access")
    assert not guard.rejected
    assert not any(name == "PIL" or name.startswith("PIL.") for name in sys.modules)
    print("Native WebP, analysis, resize, vector paths and font API passed without Pillow")  # noqa: T201


if __name__ == "__main__":
    main()
