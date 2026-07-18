"""Build custom-profile-card parity payloads from a captured GetAnotherProfileResponse.

The service path (``compose_custom_profile_card_image``) runs with ``masterdata=None`` and
resolves every image through the request's Cloud-inlined ``resources`` index. Cloud builds that
index from masterdata plus derived asset paths — which means we can rebuild an equivalent one
offline: inline the same masterdata JSON files under the same keys, and let a *masterdata-mode*
probe renderer derive the ``imagePath`` / ``cardAssets`` path entries the masterdata-less service
mode requires (see ``PNGRenderer.resource_path`` — without masterdata only the ``imagePath``-style
keys resolve).

Inputs (all local): ``response.json`` at the repo root (a real profile response carrying
``userCustomProfileCards``) and the pulled CN masterdata + asset trees under ``data/``.

Output: ``out/parity-payloads/custom_profile_card*.json`` — request bodies that
``CustomProfileCardRenderRequest`` validates and the parity sweep renders through the REAL
service path, fully resolved (verified by the resolution probe in this repo's Stage A2 work).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.sekai.profile.custom_profile import renderer as R
from src.sekai.profile.custom_profile.split import (
    build_custom_profile_render_request,
    build_profile_context,
    custom_profile_cards,
    normalize_profile_payload,
)

RESPONSE_JSON = REPO_ROOT / "response.json"
MASTERDATA = REPO_ROOT / "data/masterdata/haruki-sekai-sc-master/master"
ASSETS = REPO_ROOT / "data/asset/cn-assets/startapp/custom_profile"
OUT_DIR = REPO_ROOT / "out" / "parity-payloads"
REGION = "cn"

# resources key -> masterdata filename, exactly the pairs PNGRenderer.__init__ loads
# (see load_resource_index calls). Keys absent from this list have no masterdata fallback
# (stampAssets/cardAssets/honorRequests are Cloud-derived; cardAssets is synthesized below).
INDEX_FILES: tuple[tuple[str, str], ...] = (
    ("customProfileTextColors", "customProfileTextColors.json"),
    ("customProfileTextFonts", "customProfileTextFonts.json"),
    ("customProfileShapeResources", "customProfileShapeResources.json"),
    ("customProfilePlayerInfoResources", "customProfilePlayerInfoResources.json"),
    ("customProfileGeneralBackgroundResources", "customProfileGeneralBackgroundResources.json"),
    ("customProfileStoryBackgroundResources", "customProfileStoryBackgroundResources.json"),
    ("customProfileMemberStandingPictureResources", "customProfileMemberStandingPictureResources.json"),
    ("customProfileCollectionResources", "customProfileCollectionResources.json"),
    ("customProfileEtcResources", "customProfileEtcResources.json"),
    ("omikujis", "omikujis.json"),
    ("stamps", "stamps.json"),
    ("cards", "cards.json"),
    ("honors", "honors.json"),
    ("honorGroups", "honorGroups.json"),
    ("bondsHonors", "bondsHonors.json"),
    ("bondsHonorWords", "bondsHonorWords.json"),
    ("gameCharacterUnits", "gameCharacterUnits.json"),
)

_CARD_ASSET_KEYS: tuple[tuple[str, bool, str], ...] = (
    # cardAssets entry key, after_training, path_for_state kind
    ("normalPath", False, "full"),
    ("afterTrainingPath", True, "full"),
    ("deckNormalPath", False, "deck"),
    ("deckAfterTrainingPath", True, "deck"),
    ("smallNormalPath", False, "small"),
    ("smallAfterTrainingPath", True, "small"),
)


def _request_path(path: Path) -> str:
    """A resolved local path, re-expressed in the request-asset form the service resolves
    (``asset/...`` under a data root — the same shape Cloud emits)."""
    resolved = path.resolve()
    data_root = (REPO_ROOT / "data").resolve()
    try:
        return resolved.relative_to(data_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _probe_renderer(profile_context: dict[str, Any]) -> Any:
    parser = R.build_arg_parser()
    args = parser.parse_args(
        [
            "--masterdata",
            str(MASTERDATA),
            "--assets",
            str(ASSETS),
            "--fonts",
            str(ASSETS / "font"),
            "--tmp-font-metadata",
            str(REPO_ROOT / "data/custom_profile/tmp-font-assets" / REGION / "metadata.json"),
            "--shape-sprite-dir",
            str(ASSETS / "shape"),
            "--region",
            REGION,
            "--out",
            str(OUT_DIR),
        ]
    )
    return R.build_renderer(args, profile_context, R.resolve_render_target(args), {})


def _inline_indexes(probe: Any) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    for key, filename in INDEX_FILES:
        path = MASTERDATA / filename
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                # Only resource rows with a fileName can carry a derivable image path.
                if "fileName" in row and (resolved := probe.resource_path(row)):
                    row["imagePath"] = _request_path(resolved)
        resources[key] = rows
    return resources


def _card_assets_for(probe: Any, profile: dict[str, Any]) -> dict[str, dict[str, str]]:
    deck = profile.get("userDeck") or {}
    card_ids = {int(deck.get(k, 0) or 0) for k in ("leader", "subLeader", *(f"member{i}" for i in range(1, 6)))}
    card_ids |= {int(row.get("cardId", 0) or 0) for row in profile.get("userCards", []) or []}
    entries: dict[str, dict[str, str]] = {}
    for card_id in sorted(card_ids):
        if card_id <= 0:
            continue
        entry: dict[str, str] = {}
        for key, after_training, kind in _CARD_ASSET_KEYS:
            path = probe.card_image_path_for_state(card_id, after_training, kind)
            if path is not None:
                entry[key] = _request_path(path)
        if entry:
            entries[str(card_id)] = entry
    return entries


def _profile_honor_requests_for(probe: Any, profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Serialized HonorRequest per profile-honor slot, keyed ``profile:{seq}`` — the map Cloud
    ships as ``profileHonorRequests`` and ``honor_request_image`` consumes. Captured by letting
    the masterdata-mode probe derive every asset path, intercepting the HonorRequest it builds,
    and re-expressing the absolute paths in request-asset form."""
    captured: list[Any] = []
    real_honor_request = R.HonorRequest

    class _Recording(real_honor_request):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            captured.append(self)

    entries: dict[str, dict[str, Any]] = {}
    R.HonorRequest = _Recording
    try:
        rows = sorted(profile.get("userProfileHonors", []) or [], key=lambda row: int(row.get("seq", 0) or 0))
        for idx, row in enumerate(rows):
            honor_id = int(row.get("honorId", 0) or 0)
            level = int(row.get("honorLevel", 0) or 0)
            captured.clear()
            if probe.compose_honor_image(honor_id, level, full_size=idx == 0) is None or not captured:
                continue
            payload = captured[-1].model_dump(mode="json")
            for key, value in payload.items():
                if key.endswith(("_path", "_path2")) and isinstance(value, str) and value:
                    payload[key] = _request_path(Path(value))
            entries[f"profile:{int(row.get('seq', 0) or 0)}"] = payload
    finally:
        R.HonorRequest = real_honor_request
    return entries


def generate() -> list[Path]:
    profile = normalize_profile_payload(json.loads(RESPONSE_JSON.read_text(encoding="utf-8")))
    context = build_profile_context(profile)
    probe = _probe_renderer(context)
    resources = _inline_indexes(probe)
    resources["cardAssets"] = _card_assets_for(probe, profile)
    resources["profileHonorRequests"] = _profile_honor_requests_for(probe, profile)

    cards = custom_profile_cards(profile)
    names = {1: "custom_profile_card", 2: "custom_profile_card_collections"}
    written: list[Path] = []
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for card in cards:
        seq = int(card.get("seq", 0) or 0)
        name = names.get(seq)
        if name is None:
            continue
        request = build_custom_profile_render_request(profile, card, region=REGION)
        request["resources"] = resources
        out = OUT_DIR / f"{name}.json"
        out.write_text(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        written.append(out)
        print(out)  # noqa: T201
    return written


if __name__ == "__main__":
    generate()
