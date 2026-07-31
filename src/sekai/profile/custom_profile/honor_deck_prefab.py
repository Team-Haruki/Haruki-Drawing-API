"""Renderer-neutral layout and request-resolution plan for the HonorDeck prefab.

This module deliberately carries no image objects and performs no asset resolution. Pillow
and Skia consumers receive the same natural-space geometry and the same ordered request keys,
then resolve or render those resources in their own backend.

The prefab is composed at 783x179 in Unity's natural coordinate space. The production custom
profile transform subsequently rasterizes that layer to 875x200; keeping both sizes explicit
prevents a consumer from laying out in service pixels and applying the outer scale twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, TypeAlias

HonorDeckSize: TypeAlias = tuple[int, int]
HonorDeckRect: TypeAlias = tuple[float, float, float, float]
HonorDeckRequestSource: TypeAlias = Literal["profile", "ordinary"]

HONOR_DECK_NATURAL_SIZE: HonorDeckSize = (783, 179)
HONOR_DECK_SERVICE_RASTER_SIZE: HonorDeckSize = (875, 200)
HONOR_DECK_MAIN_BADGE_SIZE: HonorDeckSize = (380, 80)
HONOR_DECK_SUB_BADGE_SIZE: HonorDeckSize = (180, 80)
HONOR_DECK_MAX_SLOTS = 3


@dataclass(frozen=True, slots=True)
class HonorDeckPanelPlan:
    """Optional Unity panel attempted behind the badges.

    ``resource_policy="optional"`` and ``fallback=None`` encode the existing behavior: a
    missing ``bg_base_r16_wh`` sprite leaves the background transparent and does not make the
    HonorDeck unresolved.
    """

    sprite_name: str = "bg_base_r16_wh"
    target_rect: HonorDeckRect = (0.0, 0.0, 783.0, 179.0)
    tint: tuple[float, float, float, float] = (0.87451, 0.87451, 0.917647, 0.8)
    sliced_border: tuple[int, int, int, int] = (21, 21, 21, 21)
    resource_policy: Literal["optional"] = "optional"
    fallback: None = None


@dataclass(frozen=True, slots=True)
class HonorDeckRequestCandidates:
    """Ordered resolver input for one expected profile-honor slot.

    Consumers first inspect every ``profile_keys`` entry in ``profileHonorRequests``. Only
    when none produces a badge do they inspect ``ordinary_keys`` in ``honorRequests``. A
    candidate lacking its required base may advance to the next key; a renderable candidate
    that the active backend cannot reproduce must make the whole deck decline rather than be
    silently skipped. Exhausting both groups means the expected slot is missing.
    """

    profile_keys: tuple[str, ...]
    ordinary_keys: tuple[str, ...]

    def ordered(self) -> tuple[tuple[HonorDeckRequestSource, str], ...]:
        """Return the complete map-qualified candidate order."""

        return tuple(("profile", key) for key in self.profile_keys) + tuple(
            ("ordinary", key) for key in self.ordinary_keys
        )

    def iter_ordered(self) -> Iterator[tuple[HonorDeckRequestSource, str]]:
        """Iterate the complete map-qualified candidate order without resolving it."""

        yield from (("profile", key) for key in self.profile_keys)
        yield from (("ordinary", key) for key in self.ordinary_keys)


@dataclass(frozen=True, slots=True)
class HonorDeckSlotPlan:
    """One expected HonorDeck badge in natural prefab coordinates."""

    index: int
    seq: int
    honor_id: int
    honor_level: int
    mode: Literal["main", "sub"]
    natural_size: HonorDeckSize
    target_rect: HonorDeckRect
    request_candidates: HonorDeckRequestCandidates
    profile_row: Mapping[str, Any] = field(compare=False, hash=False, repr=False)

    @property
    def full_size(self) -> bool:
        return self.mode == "main"

    @property
    def target_xy(self) -> tuple[int, int]:
        left, top, _right, _bottom = self.target_rect
        return round(left), round(top)

    @property
    def target_size(self) -> HonorDeckSize:
        left, top, right, bottom = self.target_rect
        return max(1, round(right - left)), max(1, round(bottom - top))


@dataclass(frozen=True, slots=True)
class HonorDeckPlan:
    """Complete pixel-free plan for one visible HonorDeck General element."""

    natural_size: HonorDeckSize
    service_raster_size: HonorDeckSize
    panel: HonorDeckPanelPlan | None
    slots: tuple[HonorDeckSlotPlan, ...]

    @property
    def expected_slot_count(self) -> int:
        return len(self.slots)


_SLOT_LAYOUT: tuple[tuple[Literal["main", "sub"], HonorDeckSize, HonorDeckRect], ...] = (
    ("main", HONOR_DECK_MAIN_BADGE_SIZE, (13.5, 49.5, 393.5, 129.5)),
    ("sub", HONOR_DECK_SUB_BADGE_SIZE, (401.5, 49.5, 581.5, 129.5)),
    ("sub", HONOR_DECK_SUB_BADGE_SIZE, (589.5, 49.5, 769.5, 129.5)),
)


def honor_deck_request_candidates(
    *,
    seq: int,
    honor_id: int,
    honor_level: int,
    full_size: bool,
) -> HonorDeckRequestCandidates:
    """Build the exact profile-then-ordinary request-key order used by HonorDeck."""

    mode = "main" if full_size else "sub"
    slot_key = f"{honor_id}:{honor_level}:{mode}"
    return HonorDeckRequestCandidates(
        profile_keys=(
            f"profile:{seq}",
            f"profile:{honor_id}:{seq}",
            slot_key,
            str(honor_id),
        ),
        ordinary_keys=(slot_key, str(honor_id)),
    )


def build_honor_deck_plan(
    profile_honors: Iterable[Mapping[str, Any]] | None,
    *,
    include_panel: bool = True,
) -> HonorDeckPlan | None:
    """Build an HonorDeck plan from profile rows, or ``None`` when no rows exist.

    Rows are stably sorted by integer ``seq`` and only the first three are visible. Every
    returned slot is expected: a renderer may render the whole plan or decline it, but must
    not call a plan complete after silently dropping an unresolved slot.
    """

    rows = list(profile_honors or ())
    if not rows:
        return None
    if not all(isinstance(row, Mapping) for row in rows):
        raise TypeError("HonorDeck profile rows must be mappings")

    ordered_rows = sorted(rows, key=lambda row: int(row.get("seq", 0) or 0))
    slots: list[HonorDeckSlotPlan] = []
    for index, row in enumerate(ordered_rows[:HONOR_DECK_MAX_SLOTS]):
        seq = int(row.get("seq", 0) or 0)
        honor_id = int(row.get("honorId", 0) or 0)
        honor_level = int(row.get("honorLevel", 0) or 0)
        mode, natural_size, target_rect = _SLOT_LAYOUT[index]
        slots.append(
            HonorDeckSlotPlan(
                index=index,
                seq=seq,
                honor_id=honor_id,
                honor_level=honor_level,
                mode=mode,
                natural_size=natural_size,
                target_rect=target_rect,
                request_candidates=honor_deck_request_candidates(
                    seq=seq,
                    honor_id=honor_id,
                    honor_level=honor_level,
                    full_size=mode == "main",
                ),
                profile_row=MappingProxyType(dict(row)),
            )
        )

    return HonorDeckPlan(
        natural_size=HONOR_DECK_NATURAL_SIZE,
        service_raster_size=HONOR_DECK_SERVICE_RASTER_SIZE,
        panel=HonorDeckPanelPlan() if include_panel else None,
        slots=tuple(slots),
    )


__all__ = [
    "HONOR_DECK_MAIN_BADGE_SIZE",
    "HONOR_DECK_MAX_SLOTS",
    "HONOR_DECK_NATURAL_SIZE",
    "HONOR_DECK_SERVICE_RASTER_SIZE",
    "HONOR_DECK_SUB_BADGE_SIZE",
    "HonorDeckPanelPlan",
    "HonorDeckPlan",
    "HonorDeckRequestCandidates",
    "HonorDeckSlotPlan",
    "build_honor_deck_plan",
    "honor_deck_request_candidates",
]
