"""Tests for the custom-profile renderer's process-level caches.

Every request builds a fresh ``PNGRenderer``, so anything cached on the instance dies with it. The
pools in ``src.sekai.profile.custom_profile.cache`` are what survive across requests: the parsed TMP
metadata tables, the glyph contour/SDF L2s, the sprite/atlas decode pool, and the per-thread render
font cache. What these tests pin:

* ``BoundedCache`` semantics — LRU + byte bound, ``MISSING`` vs a cached ``None``, disabled at size 0.
* Sharing — a second renderer/load gets the SAME object back (identity, not equality).
* Invalidation — every key carries file signatures ``(mtime_ns, size)`` for every file the value was
  derived from, including files merely *referenced* by metadata.json, so a replaced asset invalidates
  the entry (the CLAUDE.md cache-key rule).
* Stats — ``get_custom_profile_cache_stats()`` shape and its ``custom_profile_caches`` slot in
  ``get_runtime_cache_stats()``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image
import pytest

from src.sekai.base.utils import get_runtime_cache_stats
from src.sekai.profile.custom_profile.cache import (
    MISSING,
    BoundedCache,
    clear_custom_profile_caches,
    get_custom_profile_cache_stats,
    get_render_font,
)
from src.sekai.profile.custom_profile.renderer import PNGRenderer, TMPFontLibrary
from src.settings import DEFAULT_FONT, FONT_DIR

STAT_FIELDS = {
    "enabled",
    "entries",
    "max_entries",
    "bytes",
    "max_bytes",
    "hits",
    "misses",
    "sets",
    "evictions",
    "hit_rate",
}


@pytest.fixture(autouse=True)
def _clear_process_caches():
    """The pools are process-level singletons; isolate every test from its neighbours."""
    clear_custom_profile_caches()
    yield
    clear_custom_profile_caches()


def _bump_mtime(path: Path) -> None:
    """Force a signature change even when a rewrite kept the same byte count and ns timestamp."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def _write_png_color(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def _copy_real_font(dest: Path) -> Path:
    """Copy a real vector font under tmp_path; skip when data/ has no fonts (CI lint-test)."""
    source = (FONT_DIR / DEFAULT_FONT).with_suffix(".otf")
    if not source.exists():
        source = next(iter(FONT_DIR.glob("*.otf")), None)
        if source is None:
            pytest.skip("no real .otf font under FONT_DIR (CI lint-test has no data/ fonts)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())
    return dest


def _make_renderer(tmp_path: Path, *, unity_ui_sprite_dir: Path | None = None) -> PNGRenderer:
    fonts = tmp_path / "fonts"
    assets = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile"
    fonts.mkdir(exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    return PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=fonts,
        resources={},
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=unity_ui_sprite_dir,
        profile_context={},
        region="cn",
    )


def _write_character_table(path: Path, unicodes: list[int]) -> None:
    path.write_text(
        json.dumps([{"m_Unicode": cp, "m_GlyphIndex": 1, "m_Scale": 1.0} for cp in unicodes]),
        encoding="utf-8",
    )


def _write_tmp_font_metadata(meta_dir: Path) -> Path:
    """Minimal-but-parseable TMP metadata: one asset, one glyph, tables in sibling files."""
    meta_dir.mkdir(parents=True, exist_ok=True)
    _write_character_table(meta_dir / "TestFont_characters.json", [65])
    (meta_dir / "TestFont_glyphs.json").write_text(
        json.dumps(
            [
                {
                    "m_Index": 1,
                    "m_AtlasIndex": 0,
                    "m_Scale": 1.0,
                    "m_Metrics": {
                        "m_Width": 10.0,
                        "m_Height": 12.0,
                        "m_HorizontalBearingX": 1.0,
                        "m_HorizontalBearingY": 10.0,
                        "m_HorizontalAdvance": 11.0,
                    },
                    "m_GlyphRect": {"m_X": 2, "m_Y": 3, "m_Width": 10, "m_Height": 12},
                }
            ]
        ),
        encoding="utf-8",
    )
    metadata_path = meta_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "materials": [{"path_id": 101, "floats": {"_GradientScale": 6.0}}],
                "tmp_font_assets": [
                    {
                        "name": "TestFont",
                        "bundle": "custom_profile_font.bundle",
                        "material": 101,
                        "character_table_path": "TestFont_characters.json",
                        "glyph_table_path": "TestFont_glyphs.json",
                        "atlas_textures": [],
                        "atlas_padding": 5.0,
                        "face_info": {"m_PointSize": 32.0, "m_Scale": 1.0, "m_AscentLine": 30.0, "m_DescentLine": -8.0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return metadata_path


# ---- BoundedCache unit behavior ----------------------------------------------------------------


def test_bounded_cache_counts_hits_misses_and_sets():
    cache = BoundedCache("t", 4, 1024, lambda value: 1)

    assert cache.get("k") is MISSING
    cache.set("k", "v")
    assert cache.get("k") == "v"

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["sets"] == 1
    assert stats["entries"] == 1
    assert stats["hit_rate"] == 0.5


def test_bounded_cache_evicts_least_recently_used_on_max_entries():
    cache = BoundedCache("t", 2, 1024, lambda value: 1)
    cache.set("a", 1)
    cache.set("b", 2)

    assert cache.get("a") == 1  # touch: "a" becomes MRU, "b" is now the eviction candidate
    cache.set("c", 3)

    assert cache.get("b") is MISSING
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert cache.stats()["evictions"] == 1


def test_bounded_cache_evicts_on_byte_bound():
    cache = BoundedCache("t", 10, 10, len)
    cache.set("a", "aaaa")
    cache.set("b", "bbbb")
    cache.set("c", "cccc")  # 12 estimated bytes > 10: oldest entry goes

    assert cache.get("a") is MISSING
    assert cache.get("b") == "bbbb"
    assert cache.get("c") == "cccc"
    stats = cache.stats()
    assert stats["entries"] == 2
    assert stats["bytes"] == 8
    assert stats["evictions"] == 1


def test_bounded_cache_refuses_entry_larger_than_max_bytes():
    cache = BoundedCache("t", 10, 10, len)
    cache.set("small", "xx")
    cache.set("big", "x" * 11)  # storing it would evict the whole pool for one entry

    assert cache.get("big") is MISSING
    assert cache.get("small") == "xx"  # the oversized set must not have evicted anything
    assert cache.stats()["sets"] == 1


def test_bounded_cache_cached_none_is_a_hit_distinct_from_missing():
    cache = BoundedCache("t", 4, 1024, lambda value: 1)
    cache.set("neg", None)  # negative entries are real: "this font cannot produce this glyph"

    value = cache.get("neg")
    assert value is None
    assert value is not MISSING
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 0


def test_bounded_cache_disabled_at_size_zero():
    cache = BoundedCache("t", 0, 1024, lambda value: 1)

    assert not cache.enabled
    cache.set("k", "v")
    assert cache.get("k") is MISSING
    stats = cache.stats()
    assert stats["enabled"] is False
    assert stats["entries"] == 0
    assert stats["misses"] == 0  # a disabled pool records no traffic


# ---- TMP metadata table cache ------------------------------------------------------------------


def test_tmp_metadata_tables_shared_across_loads(tmp_path: Path):
    metadata_path = _write_tmp_font_metadata(tmp_path / "tmp_meta")

    lib1 = TMPFontLibrary.load(metadata_path, source_metadata_path=None)
    lib2 = TMPFontLibrary.load(metadata_path, source_metadata_path=None)

    assert 65 in lib1.assets["TestFont"][0].glyphs  # the parse actually happened
    assert lib2.assets is lib1.assets  # the parsed tables are the shared value...
    assert lib2._source_fonts is not lib1._source_fonts  # ...but per-request mutable state is fresh


def test_tmp_metadata_invalidation_covers_referenced_tables(tmp_path: Path):
    """The signature list must cover every file the parse READ, not just metadata.json: here only
    the character table changes (metadata.json bytes and mtime untouched)."""
    meta_dir = tmp_path / "tmp_meta"
    metadata_path = _write_tmp_font_metadata(meta_dir)
    lib1 = TMPFontLibrary.load(metadata_path, source_metadata_path=None)
    assert 66 not in lib1.assets["TestFont"][0].glyphs

    char_path = meta_dir / "TestFont_characters.json"
    _write_character_table(char_path, [65, 66])
    _bump_mtime(char_path)

    lib3 = TMPFontLibrary.load(metadata_path, source_metadata_path=None)
    assert lib3.assets is not lib1.assets
    assert 66 in lib3.assets["TestFont"][0].glyphs  # reparsed from the rewritten table


# ---- per-thread render font cache --------------------------------------------------------------


def test_get_render_font_caches_per_signature_and_raises_on_missing(tmp_path: Path):
    font_path = _copy_real_font(tmp_path / "RenderFont.otf")

    first = get_render_font(font_path, 24.0)
    assert get_render_font(font_path, 24.0) is first  # same thread, same signature: same object

    _bump_mtime(font_path)
    assert get_render_font(font_path, 24.0) is not first  # replaced file: signature moved the key

    with pytest.raises(OSError, match="cannot open resource"):  # ImageFont.truetype's own error
        get_render_font(tmp_path / "missing.otf", 24.0)


# ---- sprite/atlas decode pool ------------------------------------------------------------------


def test_sprite_decode_shared_across_renderers_and_invalidated_on_replace(tmp_path: Path):
    sprite_dir = tmp_path / "unity_sprites"
    sprite_path = sprite_dir / "haruki_cache_probe.png"
    _write_png_color(sprite_path, (4, 4), (255, 0, 0, 255))

    sprite_a = _make_renderer(tmp_path, unity_ui_sprite_dir=sprite_dir).unity_ui_sprite("haruki_cache_probe")
    sprite_b = _make_renderer(tmp_path, unity_ui_sprite_dir=sprite_dir).unity_ui_sprite("haruki_cache_probe")

    assert sprite_a is not None
    assert sprite_a.getpixel((0, 0)) == (255, 0, 0, 255)
    assert sprite_b is sprite_a  # decoded once, shared read-only across renderers

    _write_png_color(sprite_path, (4, 4), (0, 255, 0, 255))
    _bump_mtime(sprite_path)

    sprite_c = _make_renderer(tmp_path, unity_ui_sprite_dir=sprite_dir).unity_ui_sprite("haruki_cache_probe")
    assert sprite_c is not sprite_a
    assert sprite_c.getpixel((0, 0)) == (0, 255, 0, 255)  # new pixels, not the stale decode


# ---- glyph contour L2 --------------------------------------------------------------------------


def test_glyph_contours_l2_shared_across_renderers_keyed_on_font_signature(tmp_path: Path, monkeypatch):
    ttlib = pytest.importorskip("fontTools.ttLib")
    font_path = _copy_real_font(tmp_path / "GlyphProbe.otf")

    constructions = {"count": 0}
    real_ttfont = ttlib.TTFont

    def counting_ttfont(*args, **kwargs):
        constructions["count"] += 1
        return real_ttfont(*args, **kwargs)

    # tmp_vector_glyph_contours does `from fontTools.ttLib import TTFont` at call time,
    # so patching the module attribute counts every font parse it performs.
    monkeypatch.setattr(ttlib, "TTFont", counting_ttfont)

    result_a = _make_renderer(tmp_path).tmp_vector_glyph_contours(font_path, "永", 32.0)
    assert result_a is not None, "probe glyph missing from the copied font"
    assert constructions["count"] == 1
    packed, _np = result_a
    assert all(not arr.flags.writeable for arr in packed)  # shared across threads: frozen

    result_b = _make_renderer(tmp_path).tmp_vector_glyph_contours(font_path, "永", 32.0)
    assert result_b is result_a  # L2 hit: the identical object, no recompute
    assert constructions["count"] == 1  # zero TTFont constructions on the second renderer's call

    font_path.write_bytes(font_path.read_bytes() + b"\x00")
    _bump_mtime(font_path)
    result_c = _make_renderer(tmp_path).tmp_vector_glyph_contours(font_path, "永", 32.0)
    assert result_c is not result_a  # replaced font file: the L2 key moved, glyph recomputed
    assert constructions["count"] == 2


def test_transient_glyph_failure_is_not_negative_cached_process_wide(tmp_path: Path, monkeypatch):
    """A one-off failure (memory pressure, IO blip) must die with the request's L1, never enter
    the process pool: the font's signature is unchanged, so a pooled None would masquerade as a
    permanent "font cannot produce this glyph" verdict until restart."""
    ttlib = pytest.importorskip("fontTools.ttLib")
    font_path = _copy_real_font(tmp_path / "TransientProbe.otf")

    real_ttfont = ttlib.TTFont
    failures = {"remaining": 1}

    def flaky_ttfont(*args, **kwargs):
        if failures["remaining"] > 0:
            failures["remaining"] -= 1
            raise MemoryError("simulated transient pressure")
        return real_ttfont(*args, **kwargs)

    monkeypatch.setattr(ttlib, "TTFont", flaky_ttfont)

    renderer_a = _make_renderer(tmp_path)
    assert renderer_a.tmp_vector_glyph_contours(font_path, "永", 32.0) is None  # the transient failure
    # L1 keeps the per-request negative verdict (pre-cache behavior preserved)...
    assert renderer_a.tmp_vector_glyph_contours(font_path, "永", 32.0) is None
    # ...but a fresh renderer, with the failure gone and the font untouched, recomputes and succeeds.
    assert _make_renderer(tmp_path).tmp_vector_glyph_contours(font_path, "永", 32.0) is not None


# ---- stats plumbing ----------------------------------------------------------------------------


def test_cache_stats_shape_and_runtime_plumbing():
    stats = get_custom_profile_cache_stats()

    assert set(stats) == {"tmp_metadata", "glyph_sdf", "glyph_contours", "sprite_atlas"}
    for pool_stats in stats.values():
        assert STAT_FIELDS <= set(pool_stats)

    runtime = get_runtime_cache_stats()
    assert "custom_profile_caches" in runtime  # the sixth /cache/stats key
    assert set(runtime["custom_profile_caches"]) == set(stats)


def test_cache_stats_counters_move_with_traffic(tmp_path: Path):
    metadata_path = _write_tmp_font_metadata(tmp_path / "tmp_meta")
    sprite_dir = tmp_path / "unity_sprites"
    _write_png_color(sprite_dir / "stats_probe.png", (2, 2), (1, 2, 3, 255))
    before = get_custom_profile_cache_stats()

    TMPFontLibrary.load(metadata_path, source_metadata_path=None)
    TMPFontLibrary.load(metadata_path, source_metadata_path=None)
    _make_renderer(tmp_path, unity_ui_sprite_dir=sprite_dir).unity_ui_sprite("stats_probe")
    _make_renderer(tmp_path, unity_ui_sprite_dir=sprite_dir).unity_ui_sprite("stats_probe")

    after = get_custom_profile_cache_stats()
    assert after["tmp_metadata"]["misses"] == before["tmp_metadata"]["misses"] + 1
    assert after["tmp_metadata"]["hits"] == before["tmp_metadata"]["hits"] + 1
    assert after["sprite_atlas"]["misses"] == before["sprite_atlas"]["misses"] + 1
    assert after["sprite_atlas"]["sets"] == before["sprite_atlas"]["sets"] + 1
    assert after["sprite_atlas"]["hits"] == before["sprite_atlas"]["hits"] + 1
