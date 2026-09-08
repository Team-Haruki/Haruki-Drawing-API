from __future__ import annotations

import ctypes
from pathlib import Path
import threading

import pytest

from src.sekai.profile.custom_profile import renderer


class _Callable:
    def __init__(self, result=0, callback=None):
        self.result = result
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        if self.callback is not None:
            return self.callback(*args)
        return self.result


class _InitLibrary:
    def __init__(self, init_result=0):
        self.FT_Init_FreeType = _Callable(init_result)
        self.FT_New_Face = _Callable()
        self.FT_Done_Face = _Callable()
        self.FT_Set_Char_Size = _Callable()
        self.FT_Get_Char_Index = _Callable(1)
        self.FT_Load_Glyph = _Callable()
        self.FT_Render_Glyph = _Callable()


def _metrics_with_face() -> tuple[renderer.FreeTypeMetrics, renderer.FTFaceRec, renderer.FTGlyphSlotRec]:
    metrics = object.__new__(renderer.FreeTypeMetrics)
    metrics.lib = _InitLibrary()
    metrics.handle = ctypes.c_void_p()
    metrics._faces = {}
    metrics._lock = threading.Lock()
    slot = renderer.FTGlyphSlotRec()
    slot.metrics.width = 128
    slot.metrics.height = 192
    slot.metrics.horiBearingX = -32
    slot.metrics.horiBearingY = 160
    slot.metrics.horiAdvance = 256
    slot.bitmap_left = -1
    slot.bitmap_top = 3
    face = renderer.FTFaceRec()
    face.glyph = ctypes.pointer(slot)
    metrics._face = lambda _path: ctypes.pointer(face)
    return metrics, face, slot


def test_freetype_initialization_reports_missing_library_and_init_failure(monkeypatch) -> None:
    monkeypatch.setattr(renderer.ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setattr(renderer.ctypes, "CDLL", lambda _path: (_ for _ in ()).throw(OSError("missing")))
    with pytest.raises(OSError, match="libfreetype not found"):
        renderer.FreeTypeMetrics()

    library = _InitLibrary(init_result=1)
    monkeypatch.setattr(renderer.ctypes.util, "find_library", lambda _name: "libfreetype")
    monkeypatch.setattr(renderer.ctypes, "CDLL", lambda _path: library)
    with pytest.raises(OSError, match="FT_Init_FreeType failed"):
        renderer.FreeTypeMetrics()


def test_freetype_initialization_configures_api_and_closes_cached_faces(monkeypatch) -> None:
    library = _InitLibrary()
    monkeypatch.setattr(renderer.ctypes.util, "find_library", lambda _name: "libfreetype")
    monkeypatch.setattr(renderer.ctypes, "CDLL", lambda _path: library)
    metrics = renderer.FreeTypeMetrics()
    metrics._faces[Path("font.ttf")] = ctypes.POINTER(renderer.FTFaceRec)()

    metrics.close()

    assert metrics._faces == {}
    assert library.FT_New_Face.argtypes is not None
    assert library.FT_Render_Glyph.restype is ctypes.c_int


def test_freetype_face_cache_handles_success_and_failure() -> None:
    metrics, _face, _slot = _metrics_with_face()
    metrics._face = renderer.FreeTypeMetrics._face.__get__(metrics)
    created = renderer.FTFaceRec()

    def create_face(_handle, _path, _index, output):
        output_ptr = ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(renderer.FTFaceRec)))
        output_ptr[0] = ctypes.pointer(created)
        return 0

    metrics.lib.FT_New_Face = _Callable(callback=create_face)
    first = metrics._face(Path("font.ttf"))
    second = metrics._face(Path("font.ttf"))
    assert first == second

    metrics.lib.FT_New_Face = _Callable(result=1)
    with pytest.raises(OSError, match="FT_New_Face failed"):
        metrics._face(Path("missing.ttf"))


def test_freetype_glyph_metrics_cover_success_and_native_failures() -> None:
    metrics, _face, _slot = _metrics_with_face()
    result = metrics.glyph_metrics(Path("font.ttf"), "A", 16)
    assert result is not None
    assert (result.width, result.height, result.bearing_x, result.bearing_y, result.advance) == (2, 3, -0.5, 2.5, 4)

    metrics.lib.FT_Set_Char_Size.result = 1
    assert metrics.glyph_metrics(Path("font.ttf"), "A", 16) is None
    metrics.lib.FT_Set_Char_Size.result = 0
    metrics.lib.FT_Get_Char_Index.result = 0
    assert metrics.glyph_metrics(Path("font.ttf"), "A", 16) is None
    metrics.lib.FT_Get_Char_Index.result = 1
    metrics.lib.FT_Load_Glyph.result = 1
    assert metrics.glyph_metrics(Path("font.ttf"), "A", 16) is None


def test_freetype_glyph_bitmap_covers_pixels_empty_bitmap_negative_pitch_and_failures() -> None:
    metrics, _face, slot = _metrics_with_face()
    buffer = ctypes.create_string_buffer(bytes([1, 2, 3, 4]))
    slot.bitmap.width = 2
    slot.bitmap.rows = 2
    slot.bitmap.pitch = 2
    slot.bitmap.buffer = ctypes.cast(buffer, ctypes.c_void_p)

    image, left, top, layout = metrics.glyph_bitmap(Path("font.ttf"), "A", 16)
    assert [image.getpixel((x, y)) for y in range(2) for x in range(2)] == [1, 2, 3, 4]
    assert (left, top, layout.advance) == (-1, 3, 4)

    slot.bitmap.pitch = -2
    image, *_ = metrics.glyph_bitmap(Path("font.ttf"), "A", 16)
    assert [image.getpixel((x, y)) for y in range(2) for x in range(2)] == [3, 4, 1, 2]

    slot.bitmap.width = 0
    image, *_ = metrics.glyph_bitmap(Path("font.ttf"), "A", 16)
    assert image.size == (1, 1)
    assert image.getpixel((0, 0)) == 0

    metrics.lib.FT_Render_Glyph.result = 1
    assert metrics.glyph_bitmap(Path("font.ttf"), "A", 16) is None
    metrics.lib.FT_Render_Glyph.result = 0
    metrics.lib.FT_Load_Glyph.result = 1
    assert metrics.glyph_bitmap(Path("font.ttf"), "A", 16) is None
    metrics.lib.FT_Load_Glyph.result = 0
    metrics.lib.FT_Get_Char_Index.result = 0
    assert metrics.glyph_bitmap(Path("font.ttf"), "A", 16) is None
    metrics.lib.FT_Get_Char_Index.result = 1
    metrics.lib.FT_Set_Char_Size.result = 1
    assert metrics.glyph_bitmap(Path("font.ttf"), "A", 16) is None


def test_freetype_singleton_caches_success_and_permanent_failure(monkeypatch) -> None:
    marker = object()

    class Success:
        def __new__(cls):
            return marker

    monkeypatch.setattr(renderer, "_FREETYPE_METRICS", None)
    monkeypatch.setattr(renderer, "_FREETYPE_UNAVAILABLE", False)
    monkeypatch.setattr(renderer, "FreeTypeMetrics", Success)
    assert renderer.freetype_metrics() is marker
    assert renderer.freetype_metrics() is marker

    class Failure:
        def __init__(self):
            raise OSError("missing")

    monkeypatch.setattr(renderer, "_FREETYPE_METRICS", None)
    monkeypatch.setattr(renderer, "_FREETYPE_UNAVAILABLE", False)
    monkeypatch.setattr(renderer, "FreeTypeMetrics", Failure)
    assert renderer.freetype_metrics() is None
    assert renderer._FREETYPE_UNAVAILABLE
    assert renderer.freetype_metrics() is None
