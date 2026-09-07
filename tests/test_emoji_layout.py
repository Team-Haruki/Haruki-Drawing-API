"""The shared emoji layout preserves the legacy renderer's segmentation contract."""

from pathlib import Path

from PIL import ImageFont
from pilmoji import getsize
import pytest

from src.sekai.base.emoji_layout import emoji_text_size
from src.sekai.base.font_metrics import get_native_font

FONT_PATH = Path(__file__).resolve().parents[1] / "data/SourceHanSansSC-Regular.otf"
pytestmark = pytest.mark.skipif(not FONT_PATH.is_file(), reason="Source Han fixture font required")


@pytest.mark.parametrize("size", [14, 20, 31])
@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n",
        "plain text",
        "💧露滴掉落时间",
        "🌱浇水 🎂派对",
        "👨‍👩‍👧‍👦 family",
        "👍🏿🇯🇵 1️⃣",
        "☀ ☀️ © ©️",
        "😀\n未来 j AVATAR\n",
        "😀 <:custom:123456789012345678>",
    ],
)
def test_shared_emoji_metrics_match_legacy_for_native_and_pillow_fonts(size, text):
    pillow = ImageFont.truetype(str(FONT_PATH), size, layout_engine=ImageFont.Layout.BASIC)
    expected = getsize(text, font=pillow)
    assert emoji_text_size(pillow, text) == expected
    native = get_native_font(str(FONT_PATH.parent), FONT_PATH.name, size)
    if native is not None:
        assert emoji_text_size(native, text) == expected
