"""Backend-neutral recipe for missing assets; no pixels or renderer imports."""

from dataclasses import dataclass

SIZES = {
    "square": (512, 512),
    "portrait": (512, 768),
    "landscape": (768, 432),
    "event_banner": (900, 400),
    "wide": (960, 320),
    "gacha_unknown": (256, 256),
}


def placeholder_variant(path: str | None) -> str:
    normalized = (path or "").replace("\\", "/").lower()
    if (
        "banner_event" in normalized
        or "event_banner" in normalized
        or ("/banner/" in normalized and "event" in normalized)
    ):
        return "event_banner"
    if any(token in normalized for token in ("banner", "logo", "header", "title", "word_img", "word/")):
        return "wide"
    if any(token in normalized for token in ("background", "story_bg", "event_bg", "/bg/", "_bg", "bg_")):
        return "landscape"
    if any(token in normalized for token in ("portrait", "standing", "fullbody", "full_body")):
        return "portrait"
    return "square"


@dataclass(frozen=True, slots=True)
class PlaceholderRecipe:
    size: tuple[int, int]
    background: tuple[int, int, int, int]
    roundrects: tuple
    lines: tuple
    texts: tuple


def placeholder_recipe(variant: str) -> PlaceholderRecipe:
    width, height = SIZES.get(variant, SIZES["square"])
    if variant == "gacha_unknown":
        return PlaceholderRecipe((width, height), (220, 220, 220, 255), (), (), ())
    short = min(width, height)
    outer = max(18, short // 20)
    inner = max(12, short // 14)
    border = max(3, short // 96)
    line_width = max(4, short // 72)
    left, top = outer + inner, outer + inner
    right, bottom = width - outer - inner, height - outer - inner
    qmark_size = max(48, int(short * 0.56))
    qmark_center = (width / 2, height / 2 - short * 0.04)
    return PlaceholderRecipe(
        size=(width, height),
        background=(244, 247, 250, 255),
        roundrects=(
            ((0, 0, width - 1, height - 1), max(24, short // 10), (236, 240, 245, 255), (210, 216, 224, 255), border),
            (
                (outer, outer, width - outer - 1, height - outer - 1),
                max(18, short // 14),
                (251, 252, 253, 255),
                (190, 198, 208, 255),
                border,
            ),
        ),
        lines=(
            ((left, top, right, bottom), (228, 232, 238, 255), line_width),
            ((left, bottom, right, top), (228, 232, 238, 255), line_width),
        ),
        texts=(
            ("?", (qmark_center[0] + 4, qmark_center[1] + 6), qmark_size, (255, 255, 255, 220)),
            ("?", qmark_center, qmark_size, (118, 128, 140, 255)),
            ("MISSING", (width / 2, height - outer - short * 0.1), max(16, int(short * 0.08)), (142, 150, 160, 255)),
        ),
    )
