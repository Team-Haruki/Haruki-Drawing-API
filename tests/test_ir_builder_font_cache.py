"""Tests for IRBuilder's PIL font cache.

Text layout is measured with PIL on the Python side, so the font cache sits on the hot path of
every Skia render. It must survive across IRBuilder instances (i.e. across requests) and be
bounded — but it must stay PER-THREAD. Pillow guards a FreeTypeFont's state with a per-object
critical section, so one shared font object serializes every measurement in the process on a
free-threaded build (measured 4-5x slower at 8-16 threads). The "one object per thread"
assertion below is what protects that.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from src.sekai.skia_renderer import ir_builder as irb
from src.sekai.skia_renderer.ir_builder import IRBuilder, pil_font_cache_info
from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR


def _builder(default_font: str = DEFAULT_FONT, bold_font: str = DEFAULT_BOLD_FONT) -> IRBuilder:
    """A builder pointed at the real font dir, so measurement uses real FreeType fonts."""
    return IRBuilder(
        120,
        100,
        assets_base_dir="/base",
        font_dir=str(FONT_DIR),
        default_font=default_font,
        bold_font=bold_font,
    )


def _clear_cache() -> None:
    irb._thread_font_cache().clear()


def test_font_cache_survives_across_builder_instances():
    """Two builders on the same thread must hand back the same font, not rebuild it."""
    _clear_cache()
    a, b = _builder(), _builder()

    font_a = a._pil_font("default", 32)
    entries_after_first = pil_font_cache_info()["size"]
    font_b = b._pil_font("default", 32)

    assert font_a is font_b, "font object was rebuilt for the second IRBuilder"
    assert pil_font_cache_info()["size"] == entries_after_first  # pure cache hit, no new entry


def test_font_objects_are_not_shared_across_threads():
    """THE performance invariant. A FreeTypeFont shared between threads serializes every
    getlength()/getbbox() in the process (Pillow takes a per-object critical section), which
    costs 4-5x throughput on the free-threaded build. Each thread must get its own object."""
    fonts: list[int] = []
    lock = threading.Lock()

    def work(_: int) -> None:
        font = _builder()._pil_font("default", 32)
        with lock:
            fonts.append(id(font))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work, range(4)))

    assert len(set(fonts)) == 4, "font object is shared across threads — measurement will serialize"


def test_font_cache_key_is_resolved_name_not_role():
    """Roles resolve through each builder's own font map, so keying on the role would let a
    builder whose "default" is FontX collide with one whose "default" is FontY."""
    _clear_cache()
    regular = _builder(default_font=DEFAULT_FONT, bold_font=DEFAULT_BOLD_FONT)
    swapped = _builder(default_font=DEFAULT_BOLD_FONT, bold_font=DEFAULT_FONT)

    assert regular._pil_font("default", 24) is not swapped._pil_font("default", 24)
    assert regular._pil_font("bold", 24) is swapped._pil_font("default", 24)


def test_font_cache_dedupes_equivalent_pixel_sizes():
    """Sizes that round to the same integer px are the same font; they must share an entry."""
    _clear_cache()
    b = _builder()

    font = b._pil_font("default", 32.0)
    assert b._pil_font("default", 32.4) is font
    assert b._pil_font("default", 31.5001) is font
    assert pil_font_cache_info()["size"] == 1

    assert b._pil_font("default", 33.0) is not font


def test_font_cache_is_bounded():
    """An unbounded cache keyed by a caller-influenced size would be a slow leak."""
    _clear_cache()
    b = _builder()

    for size in range(1, irb._PIL_FONT_CACHE_MAX * 2):
        b._pil_font("default", size)

    assert pil_font_cache_info()["size"] <= irb._PIL_FONT_CACHE_MAX


def test_unresolvable_font_is_not_cached_so_it_can_self_heal(tmp_path):
    """A missing font means a misconfigured deploy (asset volume not mounted yet). PIL's default
    is a 10px bitmap face with no CJK coverage; caching it would freeze wrong metrics in for the
    life of the process instead of picking the real font up once it appears."""
    _clear_cache()
    b = IRBuilder(10, 10, assets_base_dir="/base", font_dir=str(tmp_path), default_font="Later", bold_font="Later")

    missing = b._pil_font("default", 32)
    assert pil_font_cache_info()["size"] == 0, "the fallback font was cached and can never self-heal"

    # The real font shows up (asset volume finishes mounting).
    real = (FONT_DIR / DEFAULT_FONT).with_suffix(".otf")
    if not real.exists():
        real = next(FONT_DIR.glob("*.otf"))
    (tmp_path / "Later.otf").write_bytes(real.read_bytes())

    healed = b._pil_font("default", 32)
    assert healed is not missing
    assert healed.getlength("プロセカ") > missing.getlength("プロセカ")  # real face, real metrics
