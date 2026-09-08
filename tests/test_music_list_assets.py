import asyncio
from collections import Counter

from src.sekai.base.image_source import AssetImageRef, MissingImageRef
from src.sekai.base.plot import ImageBox
from src.sekai.music import drawer
from src.sekai.music.model import MusicListRequest


def _walk_widgets(widget):
    yield widget
    for item in getattr(widget, "items", []):
        yield from _walk_widgets(item)


def test_music_list_preserves_icon_overrides_missing_refs_and_sorted_placement(monkeypatch, tmp_path):
    probed_paths = []

    async def fake_refs(base, paths):
        probed_paths.extend(paths)
        return [
            MissingImageRef() if path in {"", "jackets/5.png"} else AssetImageRef(base / path, (64, 64), "RGBA")
            for path in paths
        ]

    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(drawer, "RESULT_ASSET_PATH", "results")
    monkeypatch.setattr(drawer, "get_asset_image_refs", fake_refs)
    request = MusicListRequest(
        music_list=[{"id": i, "difficulty": 30, "release_at": i} for i in [2, 1, 4, 3, 5]],
        jackets_path_list={i: f"jackets/{i}.png" for i in [2, 1, 4, 3, 5]},
        user_results={1: "ap", 2: "fc", 3: "clear", 4: "fc", 5: "", 99: "unused"},
        required_difficulties="master",
        play_result_icon_path_map={"ap": "", "fc": "icons/custom.png", "unused": "icons/not_used.png"},
    )
    canvas = asyncio.run(drawer._build_music_list_canvas(request))
    images = [item.image for item in _walk_widgets(canvas) if isinstance(item, ImageBox)]
    assert [
        image.path.relative_to(tmp_path).as_posix() if isinstance(image, AssetImageRef) else None for image in images
    ] == [
        "jackets/1.png",
        None,  # An explicit empty override remains a missing image, not the default AP icon.
        "jackets/2.png",
        "icons/custom.png",
        "jackets/3.png",
        "results/icon_clear.png",
        "jackets/4.png",
        "icons/custom.png",
        None,  # The missing jacket remains in its original sorted slot.
    ]
    counts = Counter(probed_paths)
    assert counts["icons/custom.png"] == 1
    assert counts[""] == 1
    assert "icons/not_used.png" not in counts
    assert "results/icon_ap.png" not in counts
    assert "results/icon_.png" not in counts
