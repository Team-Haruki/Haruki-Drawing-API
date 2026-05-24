from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CUSTOM_PROFILE_REQUEST_KIND = "pjsk_custom_profile_card"
CUSTOM_PROFILE_REQUEST_SCHEMA_VERSION = 1

# The drawing layer needs these top-level GetAnotherProfileResponse fields for
# General widgets, card/honor lookups, and owned-card render state. It should not
# receive the full card list once Cloud has already selected the target card.
PROFILE_CONTEXT_KEYS = (
    "user",
    "userProfile",
    "userDeck",
    "userCards",
    "userCharacters",
    "userChallengeLiveSoloResult",
    "userChallengeLiveSoloStages",
    "userMusicDifficultyClearCount",
    "userProfileHonors",
    "userHonors",
    "userBondsHonors",
    "userStoryFavorites",
    "userConfig",
    "userMultiLiveTopScoreCount",
    "totalPower",
    "userHonorMissions",
    "isMysekaiOwnerAcceptVisit",
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize_profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    """Unwrap common API envelopes into the GetAnotherProfileResponse object."""
    if "userCustomProfileCards" not in profile and isinstance(profile.get("response"), dict):
        return profile["response"]
    if "userCustomProfileCards" not in profile and isinstance(profile.get("updatedResources"), dict):
        return profile["updatedResources"]
    return profile


def custom_profile_cards(profile: dict[str, Any]) -> list[dict[str, Any]]:
    profile = normalize_profile_payload(profile)
    cards = profile.get("userCustomProfileCards", [])
    if not isinstance(cards, list):
        return []
    return [card for card in cards if isinstance(card, dict)]


def select_custom_profile_cards(
    profile: dict[str, Any],
    *,
    seq: int | None = None,
    custom_profile_id: int | None = None,
    custom_profile_card_id: int | None = None,
    all_cards: bool = False,
) -> list[dict[str, Any]]:
    cards = custom_profile_cards(profile)
    if all_cards:
        return sorted(cards, key=lambda c: int(c.get("seq", 0) or 0))

    if custom_profile_id is not None or custom_profile_card_id is not None:
        result: list[dict[str, Any]] = []
        for card in cards:
            if custom_profile_id is not None and int(card.get("customProfileId", 0) or 0) != custom_profile_id:
                continue
            card_id = int(card.get("customProfileCardId", 0) or 0)
            if custom_profile_card_id is not None and card_id != custom_profile_card_id:
                continue
            result.append(card)
        return result

    target_seq = seq or 1
    return [card for card in cards if int(card.get("seq", 0) or 0) == target_seq]


def build_profile_context(profile: dict[str, Any]) -> dict[str, Any]:
    profile = normalize_profile_payload(profile)
    return {key: profile[key] for key in PROFILE_CONTEXT_KEYS if key in profile}


def infer_profile_region(profile: dict[str, Any]) -> str | None:
    profile = normalize_profile_payload(profile)
    for key in ("region", "server"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    user = profile.get("user")
    if isinstance(user, dict):
        for key in ("region", "server"):
            value = user.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return None


def build_custom_profile_render_request(
    profile: dict[str, Any],
    card: dict[str, Any],
    *,
    region: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_profile_payload(profile)
    return {
        "schema_version": CUSTOM_PROFILE_REQUEST_SCHEMA_VERSION,
        "kind": CUSTOM_PROFILE_REQUEST_KIND,
        "region": (region or infer_profile_region(normalized) or "").lower(),
        "card": card,
        "profile_context": build_profile_context(normalized),
    }


def decode_custom_profile_render_request(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    card = payload.get("card") or payload.get("custom_profile_card") or payload.get("customProfileCard")
    if not isinstance(card, dict):
        raise ValueError("custom profile render request is missing card")

    context = payload.get("profile_context")
    if context is None:
        context = payload.get("context")
    if context is None:
        context = {}
    if not isinstance(context, dict):
        raise ValueError("custom profile render request profile_context must be an object")
    resources = payload.get("resources") or {}
    if not isinstance(resources, dict):
        raise ValueError("custom profile render request resources must be an object")
    return card, context, resources


def custom_profile_output_name(card: dict[str, Any]) -> str:
    seq = int(card.get("seq", 0) or 0)
    cid = int(card.get("customProfileCardId", 0) or 0)
    return f"custom_profile_seq{seq:02d}_card{cid:02d}.png"
