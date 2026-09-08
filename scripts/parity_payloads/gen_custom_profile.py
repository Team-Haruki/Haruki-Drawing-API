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
An optional sanitized HonorDeck capture supplies the exact Cloud requests for slots that are
ahead of the local CN masterdata::

    --honor-capture <root>/honordeck-sanitized-*.json
    <root>/<region>/assets/<honor bundle>/*.png

The capture is matched against the profile's slots before its requests are merged. Only its
declared public assets are copied into previously absent, ignored
``data/asset/<region>-assets/startapp`` paths; existing files are never overwritten. The
optional ``--honor-overlay-root`` overrides the capture's parent as the public-asset source.
This keeps a real JP fallback request distinct from a locally inferred request and does not
mutate either masterdata checkout.

Output: ``out/parity-payloads/custom_profile_card*.json`` — request bodies that
``CustomProfileCardRenderRequest`` validates and the parity sweep renders through the REAL
service path, fully resolved (verified by the resolution probe in this repo's Stage A2 work).
"""

from __future__ import annotations

import argparse
import filecmp
import json
from pathlib import Path
import shutil
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
_HONOR_SLOT_FIELDS: tuple[str, ...] = (
    "seq",
    "honorId",
    "honorLevel",
    "profileHonorType",
    "bondsHonorWordId",
    "bondsHonorViewType",
)

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
        _inline_resource_image_paths(probe, rows)
        resources[key] = rows
    return resources


def _inline_resource_image_paths(probe: Any, rows: Any) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Only resource rows with a fileName can carry a derivable image path.
        if "fileName" in row and (resolved := probe.resource_path(row)):
            row["imagePath"] = _request_path(resolved)


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


def _profile_honor_requests_for(
    probe: Any,
    profile: dict[str, Any],
) -> dict[str, dict[str, Any]]:
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


def _honor_slots(profile: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sorted(profile.get("userProfileHonors", []) or [], key=lambda row: int(row.get("seq", 0) or 0))
    return [{key: row.get(key) for key in _HONOR_SLOT_FIELDS} for row in rows[:3]]


def _captured_asset_pair(overlay_root: Path, raw_path: str) -> tuple[Path, Path]:
    """Map one captured request path to its narrow public-overlay source and data target."""

    parts = Path(raw_path).parts
    if (
        len(parts) != 6
        or parts[0] != "asset"
        or not parts[1].endswith("-assets")
        or parts[2] != "startapp"
        or parts[3] not in {"honor", "honor_frame"}
        or not parts[4].startswith("honor_bg_" if parts[3] == "honor" else "honor_frame_")
        or Path(raw_path).is_absolute()
        or ".." in parts
    ):
        raise ValueError(f"unsupported captured HonorDeck asset path: {raw_path!r}")
    region = parts[1].removesuffix("-assets")
    source = overlay_root / region / "assets" / parts[4] / parts[5]
    target = REPO_ROOT / "data" / Path(raw_path)
    return source, target


def _load_honor_capture(
    capture_path: Path,
    *,
    overlay_root: Path,
    profile: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate a sanitized real capture, stage its public assets, and return exact requests."""

    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    if not isinstance(capture, dict) or capture.get("schema_version") != 1:
        raise ValueError(f"unsupported HonorDeck capture schema: {capture_path}")
    if capture.get("slots") != _honor_slots(profile):
        raise ValueError("HonorDeck capture slots do not match response.json")
    capture_region = _capture_region(capture)
    requests = _validated_honor_capture_requests(capture, profile)
    required_assets = _validated_honor_capture_assets(capture, requests)
    _stage_honor_capture_assets(required_assets, overlay_root, capture_region)
    return {str(key): dict(value) for key, value in requests.items()}


def _capture_region(capture: dict[str, Any]) -> str:
    region = str(capture.get("region", "") or "").strip().lower()
    if not region or not region.replace("_", "").isalnum():
        raise ValueError("HonorDeck capture region is invalid")
    return region


def _validated_honor_capture_requests(capture: dict[str, Any], profile: dict[str, Any]) -> dict[str, dict]:
    requests = capture.get("profileHonorRequests")
    if not isinstance(requests, dict) or set(requests) != {"profile:2", "profile:3"}:
        raise ValueError("HonorDeck capture must contain exact profile:2/profile:3 requests")
    slots_by_seq = {int(slot["seq"]): slot for slot in _honor_slots(profile)}
    for seq, payload in requests.items():
        if not isinstance(payload, dict):
            raise ValueError(f"HonorDeck capture request {seq} is not an object")
        slot_seq = int(seq.removeprefix("profile:"))
        request = R.HonorRequest.model_validate(payload)
        if (
            request.honor_level != int(slots_by_seq[slot_seq]["honorLevel"])
            or request.is_main_honor
            or request.honor_type != "birthday"
        ):
            raise ValueError(f"HonorDeck capture request {seq} does not match its sub slot")
    return requests


def _validated_honor_capture_assets(capture: dict[str, Any], requests: dict[str, dict]) -> list[str]:
    required_assets = capture.get("required_assets")
    if not isinstance(required_assets, list) or not all(isinstance(path, str) for path in required_assets):
        raise ValueError("HonorDeck capture required_assets must be a string list")
    request_assets = {
        value
        for payload in requests.values()
        for key, value in payload.items()
        if key.endswith(("_path", "_path2")) and isinstance(value, str) and value
    }
    if set(required_assets) != request_assets:
        raise ValueError("HonorDeck capture required_assets do not exactly match its requests")
    return required_assets


def _stage_honor_capture_assets(required_assets: list[str], overlay_root: Path, capture_region: str) -> None:
    asset_pairs: list[tuple[Path, Path]] = []
    for raw_path in required_assets:
        source, target = _captured_asset_pair(overlay_root, raw_path)
        if Path(raw_path).parts[1] != f"{capture_region}-assets":
            raise ValueError(f"HonorDeck capture asset region does not match {capture_region!r}: {raw_path}")
        if not source.is_file():
            raise FileNotFoundError(f"captured HonorDeck public asset is missing: {source}")
        if target.exists():
            if not target.is_file() or not filecmp.cmp(source, target, shallow=False):
                raise FileExistsError(f"HonorDeck capture target already exists with different content: {target}")
            continue
        asset_pairs.append((source, target))

    for source, target in asset_pairs:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def generate(
    *,
    honor_capture: Path | None = None,
    honor_overlay_root: Path | None = None,
) -> list[Path]:
    profile = normalize_profile_payload(json.loads(RESPONSE_JSON.read_text(encoding="utf-8")))
    context = build_profile_context(profile)
    probe = _probe_renderer(context)
    resources = _inline_indexes(probe)
    resources["cardAssets"] = _card_assets_for(probe, profile)
    honor_requests = _profile_honor_requests_for(probe, profile)
    if honor_capture is not None:
        capture_path = honor_capture.resolve()
        root = honor_overlay_root.resolve() if honor_overlay_root is not None else capture_path.parent
        honor_requests.update(_load_honor_capture(capture_path, overlay_root=root, profile=profile))
        expected_keys = {f"profile:{int(row['seq'])}" for row in _honor_slots(profile)}
        if set(honor_requests) != expected_keys:
            raise ValueError(f"HonorDeck requests are incomplete: expected {sorted(expected_keys)}")
    elif honor_overlay_root is not None:
        raise ValueError("--honor-overlay-root requires --honor-capture")
    resources["profileHonorRequests"] = honor_requests

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--honor-capture",
        type=Path,
        help="Optional sanitized real HonorDeck capture to merge into the generated fixture.",
    )
    parser.add_argument(
        "--honor-overlay-root",
        type=Path,
        help="Optional public-asset root; defaults to the HonorDeck capture's parent directory.",
    )
    cli_args = parser.parse_args()
    generate(
        honor_capture=cli_args.honor_capture,
        honor_overlay_root=cli_args.honor_overlay_root,
    )
