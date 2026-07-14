"""A cached page must notice when the assets it drew have changed on disk.

The Skia payload cache (card/box, card/list) and the composed cache (event_list_entry) keyed on the
REQUEST alone. When an asset is replaced at a path the request already names, the request does not
change, so the key does not change, so the old picture keeps being served -- for up to the cache TTL,
which is a week. Reproduced before the fix: replace one card thumbnail with a solid red square, and
card/list keeps rendering the old thumbnail while the caches are hot; only a cold render sees it.

The nastiest form of this is not an art update but a MISSING asset: the page draws a "?" placeholder
(``图片素材缺失，已使用问号占位图``), the asset lands on disk a minute later, and the placeholder is
what every subsequent request gets.

honor and the profile modules already stat their assets into the key. These tests pin the mechanism
that generalises it, and pin that the three keys which lacked it now carry it.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from PIL import Image

from src.sekai.base.utils import build_rendered_image_cache_key, collect_asset_signatures


def _png(path: Path, colour: tuple[int, int, int, int], size: tuple[int, int] = (8, 8)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # JPEG has no alpha channel; everything else here keeps one.
    mode = "RGB" if path.suffix.lower() in (".jpg", ".jpeg") else "RGBA"
    Image.new(mode, size, colour[:3] if mode == "RGB" else colour).save(path)
    return path


def test_a_replaced_asset_changes_the_key(tmp_path):
    """The regression, in one assertion: same request, different bytes on disk, different key."""
    _png(tmp_path / "card.png", (0, 0, 255, 255))
    material = {"cards": [{"thumbnail": "card.png"}], "title": "box"}

    before = build_rendered_image_cache_key(
        "card_box", material, asset_signatures=collect_asset_signatures(tmp_path, material)
    )
    _png(tmp_path / "card.png", (255, 0, 0, 255))  # same path, same size, different pixels
    after = build_rendered_image_cache_key(
        "card_box", material, asset_signatures=collect_asset_signatures(tmp_path, material)
    )

    assert before != after, "the asset was replaced on disk and the cache key did not move"


def test_an_untouched_asset_keeps_the_key_stable(tmp_path):
    """The other half: if nothing changed, the key must not move, or the cache never hits."""
    _png(tmp_path / "card.png", (0, 0, 255, 255))
    material = {"cards": [{"thumbnail": "card.png"}]}

    keys = {
        build_rendered_image_cache_key(
            "card_box", material, asset_signatures=collect_asset_signatures(tmp_path, material)
        )
        for _ in range(3)
    }
    assert len(keys) == 1


def test_an_asset_that_appears_later_changes_the_key(tmp_path):
    """The placeholder case. A missing asset must be recorded as missing, not silently skipped --
    otherwise the "?" placeholder render is cached under the same key as the real one."""
    material = {"cards": [{"thumbnail": "late.png"}]}

    missing = collect_asset_signatures(tmp_path, material)
    assert missing["late.png"]["missing"] is True
    key_missing = build_rendered_image_cache_key("card_box", material, asset_signatures=missing)

    _png(tmp_path / "late.png", (0, 255, 0, 255))
    key_present = build_rendered_image_cache_key(
        "card_box", material, asset_signatures=collect_asset_signatures(tmp_path, material)
    )

    assert key_missing != key_present, "the placeholder render and the real render share a cache key"


def test_every_asset_path_in_the_material_is_stat_ed(tmp_path):
    """Walking the material beats hand-listing the fields: honor names its fourteen asset paths one
    by one, which is correct until someone adds a fifteenth. Nested lists and dicts must be reached."""
    _png(tmp_path / "a.png", (1, 2, 3, 255))
    _png(tmp_path / "nested" / "b.jpg", (4, 5, 6, 255))
    _png(tmp_path / "c.webp", (7, 8, 9, 255))
    material = {
        "bg": "a.png",
        "rows": [{"icons": ["nested/b.jpg"]}, {"icons": []}],
        "deep": {"deeper": {"deepest": "c.webp"}},
        "not_an_asset": "card_box",
        "number": 7,
    }

    sigs = collect_asset_signatures(tmp_path, material)

    assert set(sigs) == {"a.png", "nested/b.jpg", "c.webp"}
    assert all(s["mtime_ns"] > 0 for s in sigs.values())


def test_the_keys_that_lacked_signatures_now_carry_them():
    """card/box, card/list and event_list_entry are the three that keyed on the request alone."""
    from src.sekai.card import drawer as card_drawer
    from src.sekai.event import drawer as event_drawer

    for fn in (
        card_drawer._build_card_box_cache_key,
        card_drawer._build_card_list_cache_key,
        event_drawer._build_event_list_entry_cache_key,
    ):
        source = inspect.getsource(fn)
        assert "asset_signatures=collect_asset_signatures(" in source, (
            f"{fn.__qualname__} builds its cache key from the request alone — an asset replaced on "
            "disk will keep serving the old image until the entry expires"
        )
