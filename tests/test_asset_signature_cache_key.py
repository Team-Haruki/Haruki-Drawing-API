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


def test_the_key_that_lacked_signatures_now_carries_them():
    """event_list_entry keyed on the request alone.

    card/box and card/list had the same hole and no longer have a key at all: their page caches were
    removed, because the page bakes in the wall clock (the ``DT:`` footer and the 未上线 badge) and a
    key honest about that could never hit. See test_card_pages_are_not_cached below."""
    from src.sekai.event import drawer as event_drawer

    source = inspect.getsource(event_drawer._build_event_list_entry_cache_key)
    assert "asset_signatures=collect_asset_signatures(" in source, (
        "the event_list_entry cache key is built from the request alone — an asset replaced on disk "
        "will keep serving the old image until the entry expires"
    )


def test_card_pages_are_not_cached():
    """The card pages must not grow a page-level cache back.

    Both bake the wall clock into their pixels -- ``add_request_watermark`` stamps a ``DT:``
    timestamp, and the 未上线 badge is decided by ``request_now()`` against each card's release_at.
    Reproduced when they were cached: two requests differing only in ``dt`` shared a key and the
    second was served the first one's footer; and a card that had gone live an hour earlier still
    rendered 未上线 out of the cache. A key honest about the clock cannot hit (``dt`` is millisecond
    wall-clock), so there is nothing to cache here -- not a key to fix."""
    from src.sekai.card import drawer as card_drawer

    source = inspect.getsource(card_drawer)
    for forbidden in ("get_skia_payload_cached", "put_skia_payload_cache", "get_composed_image_cached"):
        assert forbidden not in source, (
            f"card/drawer.py uses {forbidden} again — the card pages render the wall clock, so a "
            "cache hit serves someone else's timestamp and a stale 未上线 badge"
        )
