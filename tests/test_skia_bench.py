"""Response-byte benchmarks must charge the same route footer on both sides."""

import asyncio
from types import SimpleNamespace

from PIL import Image
import pytest

from scripts.skia_bench import bench_case
from src.sekai.base import draw


@pytest.mark.parametrize("route_watermark", [True, False])
def test_benchmark_checks_full_response_dimensions(monkeypatch, route_watermark):
    calls = []

    async def compose(_request):
        return Image.new("RGBA", (12, 8))

    async def footer(image, request):
        calls.append(request)
        return Image.new("RGBA", (image.width, image.height + 5))

    async def native(_request):
        return SimpleNamespace(image_bytes=b"native", media_type="image/png", image_width=12, image_height=13)

    monkeypatch.setattr(draw, "add_request_watermark_to_image", footer)
    case = SimpleNamespace(name="footer", compose="compose", try_render="native", route_watermark=route_watermark)
    result = bench_case(
        case,
        "request",
        SimpleNamespace(compose=compose),
        SimpleNamespace(native=native),
        reps=1,
        cold=False,
        output_format="png",
        jpg_quality=90,
    )
    if route_watermark:
        assert asyncio.run(result)["endpoint"] == "footer"
        assert calls == ["request", "request"]
    else:
        with pytest.raises(ValueError, match="response dimensions differ"):
            asyncio.run(result)
