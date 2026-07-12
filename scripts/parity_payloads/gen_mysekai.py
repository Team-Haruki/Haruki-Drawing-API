"""Real-payload generator for the mysekai domain (8 endpoints).

Offline replica of Haruki-Cloud's ``internal/pjsk/render/mysekai`` Build*
functions (see ``out/payload-specs/mysekai.md`` for the field-by-field spec,
all Go references below are to that package unless noted otherwise).

Data sources: ``collections.mysekai.json`` (raw path, updatedResources
flattened — resource/map/fixture-list/music-record), ``collections.suite.json``
(door-upgrade suite-only, talk-list suite+mysekai merged) and masterdata
``mysekai*``/``musics``/``musicTags``/``gameCharacter*`` JSON files.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from functools import cache, cmp_to_key
import io
import json
from pathlib import Path
import sys
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import common

from src.sekai.mysekai.model import (
    MysekaiDoorUpgradeRequest,
    MysekaiFixtureDetailRequest,
    MysekaiFixtureListRequest,
    MysekaiHousingCompetitionRequest,
    MysekaiMsrMapRequest,
    MysekaiMusicrecordRequest,
    MysekaiResourceRequest,
    MysekaiTalkListRequest,
)

MD = common.MD
ASSETS = common.ASSETS
JP_TZ = timezone(timedelta(hours=9))
NOW_MS = common.now_ms()

ISSUES: list[str] = []

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _norm_ms(value: Any) -> int:
    """normalizeMySekaiTimestampMs (controller_snapshot.go:294-304)."""
    ts = int(value or 0)
    if ts <= 0:
        return 0
    return ts * 1000 if ts < 1_000_000_000_000 else ts


@cache
def _md_map(name: str) -> dict[int, dict]:
    return {row["id"]: row for row in MD.get(name)}


@cache
def _icon_map(name: str, field: str) -> dict[int, str]:
    """loadIconNameMap / loadFieldMap (controller_resources.go:285-311)."""
    return {row["id"]: row[field] for row in MD.get(name) if row.get(field)}


@cache
def _music_record_jacket_map() -> dict[int, str]:
    """loadMusicRecordJacketMap (controller_resources.go:411-427)."""
    musics = _md_map("musics")
    out: dict[int, str] = {}
    for rec in MD.get("mysekaiMusicRecords"):
        music = musics.get(rec.get("externalId", 0))
        if music and music.get("assetbundleName"):
            out[rec["id"]] = music["assetbundleName"]
    return out


def _pct(a: int, b: int) -> float:
    return a * 100 / b if b else 0.0


def _fmt_qty(quantity: int) -> str:
    """formatMysekaiQuantity (helpers_resources.go:80-88)."""
    if quantity >= 10000:
        return f"{quantity // 1000}k"
    if quantity >= 1000:
        return f"{quantity // 1000}k{(quantity % 1000) // 100}"
    return str(quantity)


# ---------------------------------------------------------------------------
# Snapshot decoding / merging (controller_snapshot.go + snapshot/local_helpers.go)
# ---------------------------------------------------------------------------


@cache
def _raw_mysekai() -> dict:
    """Raw mysekai doc with updatedResources flattened (controller_snapshot.go:168-174)."""
    doc = dict(common.load_mysekai())
    for key, value in (doc.get("updatedResources") or {}).items():
        doc[key] = value
    return doc


_PRESERVED_SUITE_KEYS = {"userGamedata", "userProfile", "userDecks", "userCards", "userAreas",
                         "userCharacters", "userHonors"}


def _preserve_suite_key(key: str) -> bool:
    key = key.strip()
    if not key:
        return False
    if key == "userMysekaiCharacterTalks":
        return True
    if key.startswith("userMysekai") or key.startswith("mysekai"):
        return False
    return key in _PRESERVED_SUITE_KEYS


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, list | dict):
        return len(value) == 0
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _skip_empty_override(base: dict, key: str, value: Any) -> bool:
    existing = base.get(key)
    if key not in base or not isinstance(existing, list) or not existing:
        return False
    return isinstance(value, list) and not value


@cache
def _merged() -> dict:
    """mergeMySekaiData (snapshot/local_helpers.go:17-148), suite + mysekai."""
    base = dict(common.load_suite())
    mysekai = common.load_mysekai()
    updated_keys: set[str] = set()
    for key, value in (mysekai.get("updatedResources") or {}).items():
        if _preserve_suite_key(key):
            continue
        updated_keys.add(key)
        if _skip_empty_override(base, key, value):
            continue
        base[key] = value
    for key, value in mysekai.items():
        if key == "updatedResources" or _preserve_suite_key(key):
            continue
        mergeable = key in ("now", "upload_time", "source", "local_source")
        if not mergeable and not key.startswith(("userMysekai", "mysekai")):
            continue
        if key in updated_keys or _skip_empty_override(base, key, value):
            continue
        if key.startswith(("userMysekai", "mysekai")) and key in base and not _is_empty_value(base[key]):
            continue
        base[key] = value
    return base


def _nested_list(root: dict, key: str) -> list:
    """nestedList (helpers_convert.go:161-174)."""
    value = root.get(key)
    if isinstance(value, list):
        return value
    updated = root.get("updatedResources")
    if isinstance(updated, dict) and isinstance(updated.get(key), list):
        return updated[key]
    return []


def _snapshot_time_ms(merged: dict) -> int:
    """resolveMysekaiSnapshotTimeMs (helpers.go:129-144)."""
    best = _norm_ms(merged.get("now"))
    updated = merged.get("updatedResources")
    if isinstance(updated, dict):
        best = max(best, _norm_ms(updated.get("now")))
    return max(best, _norm_ms(merged.get("upload_time")))


# ---------------------------------------------------------------------------
# Profile card (snapshot/local_service.go:107-131 + controller_snapshot.go:179-292)
# ---------------------------------------------------------------------------


def _profile_card(merged: dict, *, include_suite: bool, suite_name: bool = False) -> dict:
    suite = common.load_suite()
    gamedata = suite.get("userGamedata", {})
    profile: dict[str, Any] = {
        "profile": {
            "id": str(gamedata.get("userId", "")),
            "region": common.REGION.upper(),
            "nickname": gamedata.get("name", ""),
            "is_hide_uid": True,
            "leader_image_path": common._leader_image_path(suite),
            "has_frame": False,
        },
    }
    mysekai_entry: dict[str, Any] = {"name": "Mysekai数据"}
    if _norm_ms(merged.get("upload_time")) > 0:
        mysekai_entry["update_time"] = _norm_ms(merged.get("upload_time"))
    if include_suite:
        # Merged path keeps the suite entry first; its update_time is the merged
        # `now`, which the mysekai delta overrides (local_helpers.go:38-66).
        profile["data_sources"] = [{"name": "Suite数据", "update_time": int(merged.get("now", 0))}, mysekai_entry]
    else:
        profile["data_sources"] = [mysekai_entry]
        if suite_name:  # door-upgrade rename (door_upgrade_builder.go:168-171)
            profile["data_sources"][0]["name"] = "Suite数据"
    rank = int((merged.get("userMysekaiGamedata") or {}).get("mysekaiRank", 0))
    if rank > 0:
        profile["mysekai_level"] = rank
    return profile


# ---------------------------------------------------------------------------
# Phenomena forecast (helpers.go:34-186)
# ---------------------------------------------------------------------------

_BIRTHDAYS = {
    1: (8, 11), 2: (5, 9), 3: (10, 27), 4: (1, 8), 5: (4, 14), 6: (10, 5), 7: (3, 19),
    8: (12, 6), 9: (3, 2), 10: (7, 26), 11: (11, 12), 12: (5, 25), 13: (5, 17),
    14: (9, 9), 15: (7, 20), 16: (6, 24), 17: (2, 10), 18: (1, 27), 19: (4, 30),
    20: (8, 27), 21: (8, 31), 22: (12, 27), 23: (12, 27), 24: (1, 30), 25: (11, 5),
    26: (2, 17),
}


def _next_birthday(now: datetime, month: int, day: int) -> datetime:
    """mysekaiNextBirthdayLocal (helpers.go:319-326), JP region."""
    nb = datetime(now.year, month, day, tzinfo=JP_TZ)
    if nb < now:
        nb = datetime(now.year + 1, month, day, tzinfo=JP_TZ)
    return nb


def _last_refresh_and_reason(now: datetime) -> tuple[datetime, str]:
    """mysekaiLastRefreshTimeAndReason (helpers.go:286-317), JP region (5/17 local).

    Go iterates the birthday map in nondeterministic order; sorted character id
    order is used here (identical unless two birthday windows overlap).
    """
    if now.hour < 5:
        last = now.replace(hour=17, minute=0, second=0, microsecond=0) - timedelta(days=1)
    elif now.hour < 17:
        last = now.replace(hour=5, minute=0, second=0, microsecond=0)
    else:
        last = now.replace(hour=17, minute=0, second=0, microsecond=0)
    for char_id in sorted(_BIRTHDAYS):
        month, day = _BIRTHDAYS[char_id]
        nb = _next_birthday(now - timedelta(hours=24), month, day)
        start, end = nb - timedelta(hours=72), nb
        if last < start and start <= now:
            return start, f"bdstart_{char_id}"
        if last < end and end <= now:
            return end, f"bdend_{char_id}"
    return last, "natural"


def _natural_phenom(icons: dict[int, str], phenom_id: int, start: datetime, current: bool) -> dict:
    icon = (icons.get(phenom_id) or "").strip() or "env_default"
    return {
        "refresh_reason": "natural",
        "image_path": ASSETS.region_asset(f"mysekai/thumbnail/phenomena/{icon}.png"),
        "background_fill": [255, 255, 255, 150] if current else [255, 255, 255, 75],
        "start_at": int(start.timestamp() * 1000),
        "text_fill": [0, 0, 0, 255] if current else [125, 125, 125, 255],
    }


def _birthday_phenom(reason: str, start: datetime, current: bool) -> dict:
    char_id = int(reason.rsplit("_", 1)[-1])
    return {
        "refresh_reason": reason,
        "image_path": ASSETS.region_asset(f"thumbnail/material/material{char_id + 173}.png"),
        "background_fill": [255, 255, 200, 255] if current else [255, 255, 200, 150],
        "start_at": int(start.timestamp() * 1000),
        "text_fill": [0, 0, 0, 255] if current else [125, 125, 125, 255],
    }


def _extract_phenoms(merged: dict) -> list[dict]:
    """extractMysekaiPhenoms (helpers.go:78-127)."""
    schedules = merged.get("mysekaiPhenomenaSchedules")
    if not isinstance(schedules, list):
        return []
    now = datetime.fromtimestamp(_snapshot_time_ms(merged) / 1000, tz=JP_TZ)
    icons = _icon_map("mysekaiPhenomenas", "iconAssetbundleName")
    phenom_start = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if now.hour < 5:
        phenom_start -= timedelta(days=1)
    current_idx = 0 if 5 <= now.hour < 17 else 1

    phenoms: list[dict] = []
    for i, schedule in enumerate(schedules):
        phenom_id = int(schedule.get("mysekaiPhenomenaId", 1) or 1)
        current_start = phenom_start + timedelta(hours=12 * i)
        current = i == current_idx
        phenom_end = current_start + timedelta(hours=11, minutes=59)
        last_refresh, reason = _last_refresh_and_reason(phenom_end)
        mid_current: bool | None = None
        if last_refresh != current_start:
            mid_current = now >= last_refresh
            current = current and not mid_current
        phenoms.append(_natural_phenom(icons, phenom_id, current_start, current))
        if mid_current is not None:
            phenoms.append(_birthday_phenom(reason, last_refresh, mid_current))
    return phenoms


def _current_phenomena_id(merged: dict) -> int:
    """currentMysekaiPhenomenaID (helpers.go:206-234)."""
    schedules = merged.get("mysekaiPhenomenaSchedules")
    if not isinstance(schedules, list) or not schedules:
        return 0
    now = datetime.fromtimestamp(_snapshot_time_ms(merged) / 1000, tz=JP_TZ)
    current_idx = 0 if 5 <= now.hour < 17 else 1
    if current_idx >= len(schedules):
        current_idx = 0
    return int(schedules[current_idx].get("mysekaiPhenomenaId", 0) or 0)


def _parse_color_code(code: str) -> list[int] | None:
    """parseMysekaiColorCode (helpers.go:236-251)."""
    code = (code or "").strip().removeprefix("#").strip()
    if len(code) != 6:
        return None
    try:
        return [int(code[0:2], 16), int(code[2:4], 16), int(code[4:6], 16), 255]
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Resource helpers (controller_resources.go + helpers_resources.go)
# ---------------------------------------------------------------------------

_MOST_RARE = {"mysekai_material_5", "mysekai_material_12", "mysekai_material_20", "mysekai_material_24",
              "mysekai_fixture_121", "material_17", "material_170", "material_173"}
_RARE = {"mysekai_material_32", "mysekai_material_33", "mysekai_material_34", "mysekai_material_61",
         "mysekai_material_64", "mysekai_material_65", "mysekai_material_66"}


def _key_id_in_range(key: str, prefix: str, lo: int, hi: int) -> bool:
    if not key.startswith(prefix):
        return False
    try:
        return lo <= int(key.removeprefix(prefix)) <= hi
    except ValueError:
        return False


def _resource_rarity(key: str) -> int:
    """resourceRarity (helpers_resources.go:20-63)."""
    if key in _MOST_RARE:
        return 2
    if _key_id_in_range(key, "material_", 174, 203) or _key_id_in_range(key, "mysekai_material_", 67, 92):
        return 2
    if key in _RARE:
        return 1
    if key.startswith("mysekai_music_record"):
        return 1
    if key.startswith("mysekai_material_"):
        rarity = _icon_map("mysekaiMaterials", "mysekaiMaterialRarityType").get(int(key.rsplit("_", 1)[-1]), "")
        if rarity == "rarity_3":
            return 2
        if rarity == "rarity_2":
            return 1
    return 0


def _resource_text_color(key: str) -> list[int]:
    return {2: [200, 50, 0], 1: [50, 0, 200]}.get(_resource_rarity(key), [100, 100, 100])


def _resource_sort_score(key: str, count: int) -> int:
    """adjustedResourceSortScore (helpers_resources.go:106-118)."""
    if key.startswith("mysekai_music_record"):
        return count + 10_000_000
    return count + {2: 1_000_000, 1: 100_000}.get(_resource_rarity(key), 0)


def _normalize_resource_type(resource_type: str) -> str:
    """mysekaiNormalizeResourceType (controller_resources.go:317-332)."""
    normalized = (resource_type or "").strip().lower()
    return {
        "mysekai_material": "mysekai_material",
        "material": "material",
        "item": "mysekai_item", "mysekai_item": "mysekai_item",
        "fixture": "mysekai_fixture", "mysekai_fixture": "mysekai_fixture",
        "music_record": "mysekai_music_record", "mysekai_music_record": "mysekai_music_record",
    }.get(normalized, (resource_type or "").strip())


@cache
def _user_music_record_ids() -> set[int]:
    records = _nested_list(_raw_mysekai(), "userMysekaiMusicRecords")
    return {int(item.get("mysekaiMusicRecordId", 0)) for item in records}


def _resource_image_path(key: str) -> tuple[str, bool]:
    """resourceImagePath (controller_resources.go:232-273): (path, has_record)."""
    parts = key.split("_")
    try:
        res_id = int(parts[-1])
    except ValueError:
        return "", False
    type_key = key.removesuffix(f"_{res_id}")
    if type_key == "mysekai_material":
        icon = _icon_map("mysekaiMaterials", "iconAssetbundleName").get(res_id, "")
        if icon:
            return ASSETS.region_asset(f"mysekai/thumbnail/material/{icon}.png"), False
    elif type_key == "material":
        return ASSETS.region_asset(
            f"thumbnail/material/material{res_id}.png",
            f"thumbnail/material_rip/material{res_id}.png",
        ), False
    elif type_key == "mysekai_item":
        icon = _icon_map("mysekaiItems", "iconAssetbundleName").get(res_id, "")
        if icon:
            return ASSETS.region_asset(f"mysekai/thumbnail/item/{icon}.png"), False
    elif type_key == "mysekai_fixture":
        ab = _icon_map("mysekaiFixtures", "assetbundleName").get(res_id, "")
        if ab:
            return ASSETS.region_asset(
                f"mysekai/thumbnail/fixture/{ab}_{res_id}_1.png",
                f"mysekai/thumbnail/fixture/{ab}_1.png",
            ), False
    elif type_key == "mysekai_music_record":
        jacket = _music_record_jacket_map().get(res_id, "")
        if jacket:
            return ASSETS.region_asset(f"music/jacket/{jacket}/{jacket}.png"), res_id in _user_music_record_ids()
    return "", False


def _drop_fields(drop: dict) -> tuple[str, int, str, int, float, float]:
    """(normalized type, id, status, quantity, x, z) with Go fallbacks."""
    resource_type = _normalize_resource_type(drop.get("resourceType") or drop.get("type") or "")
    resource_id = int(drop.get("resourceId") or drop.get("id") or 0)
    status = drop.get("mysekaiSiteHarvestResourceDropStatus") or drop.get("status") or ""
    quantity = int(drop.get("quantity", 1) or 0)
    if quantity <= 0:
        quantity = 1
    x = float(drop.get("positionX", drop.get("position_x", 0)) or 0)
    z = float(drop.get("positionZ", drop.get("position_z", 0)) or 0)
    return resource_type, resource_id, status, quantity, x, z


def _pos_key(x: float, z: float) -> str:
    return f"{x:.3f}_{z:.3f}"


# ---------------------------------------------------------------------------
# Endpoint 1: /api/pjsk/mysekai/resource (resource_builder.go:11-92)
# ---------------------------------------------------------------------------


def _gate_assetbundle_name(gate_id: int, gate_skin_id: int) -> str:
    """resolveGateAssetbundleName (resource_builder.go:45-80)."""
    if gate_skin_id > 0:
        skin = _md_map("mysekaiGateSkins").get(gate_skin_id) or {}
        skin_type_id = int(skin.get("mysekaiGateSkinTypeId", 0))
        if skin_type_id > 0:
            table = {"unit": "mysekaiGateUnitSkins", "common": "mysekaiGateCommonSkins"}.get(
                skin.get("mysekaiGateSkinType", "")
            )
            if table:
                name = (_md_map(table).get(skin_type_id) or {}).get("assetbundleName", "")
                if name:
                    return name
    if gate_id <= 0:
        return ""
    return (_md_map("mysekaiGates").get(gate_id) or {}).get("assetbundleName", "")


def _gate_icon_path(gate_id: int, gate_skin_id: int) -> str:
    ab = _gate_assetbundle_name(gate_id, gate_skin_id)
    if ab:
        return ASSETS.region_asset(f"mysekai/thumbnail/gate_large/{ab}.png")
    return ASSETS.static(f"mysekai/gate_icon/gate_{gate_id}.png")


def _visit_characters(merged: dict) -> list[dict]:
    """extractVisitCharacters (controller_resources.go:90-146)."""
    visit = merged.get("userMysekaiGateCharacterVisit")
    if not isinstance(visit, dict):
        return []
    groups = _md_map("mysekaiGameCharacterUnitGroups")
    units = _md_map("gameCharacterUnits")
    result: list[dict] = []
    seen: set[int] = set()
    for entry in visit.get("userMysekaiGateCharacters") or []:
        group = groups.get(int(entry.get("mysekaiGameCharacterUnitGroupId", 0)))
        if not group or int(group.get("gameCharacterUnitId2", 0)) != 0:
            continue
        unit_id = int(group.get("gameCharacterUnitId1", 0))
        if not unit_id or unit_id in seen:
            continue
        seen.add(unit_id)
        is_reservation = bool(entry.get("isReservation"))
        item: dict[str, Any] = {
            "sd_image_path": ASSETS.region_asset(f"character/character_sd_l/chr_sp_{unit_id}.png"),
            "is_read": False,
            "is_reservation": is_reservation,
        }
        char_id = int((units.get(unit_id) or {}).get("gameCharacterId", 0))
        if char_id > 0:
            item["memoria_image_path"] = ASSETS.region_asset(
                f"mysekai/item_preview/material/item_memoria_{char_id}.png"
            )
        if is_reservation:
            item["reservation_icon_path"] = ASSETS.static("mysekai/invitationcard.png")
        result.append(item)
        if len(result) >= 6:
            break
    return result


def _site_resource_numbers(merged: dict) -> list[dict]:
    """extractSiteResourceNumbers (controller_resources.go:148-230)."""
    harvest_maps = _nested_list(merged, "userMysekaiHarvestMaps")
    if not harvest_maps:
        return []
    counts: dict[int, dict[str, int]] = {5: {}, 7: {}, 6: {}, 8: {}}
    for site_map in harvest_maps:
        site_id = int(site_map.get("mysekaiSiteId", 0))
        counts.setdefault(site_id, {})
        for drop in site_map.get("userMysekaiSiteHarvestResourceDrops") or []:
            resource_type, resource_id, status, quantity, _, _ = _drop_fields(drop)
            if status != "before_drop" or not resource_type or not resource_id:
                continue
            key = f"{resource_type}_{resource_id}"
            counts[site_id][key] = counts[site_id].get(key, 0) + quantity

    result: list[dict] = []
    for site_id in (5, 7, 6, 8):
        res_map = counts.get(site_id, {})
        keys = sorted(res_map, key=lambda k: (-_resource_sort_score(k, res_map[k]), k))
        resources: list[dict] = []
        for key in keys:
            image_path, has_record = _resource_image_path(key)
            if not image_path:
                continue
            entry: dict[str, Any] = {
                "image_path": image_path,
                "number": res_map[key],
                "text_color": _resource_text_color(key),
                "has_music_record": has_record,
            }
            if has_record:
                entry["music_record_icon_path"] = ASSETS.static("mysekai/music_record.png")
            resources.append(entry)
        if not resources:
            continue
        result.append({
            "image_path": ASSETS.region_asset(f"mysekai/site/sitemap/texture/img_harvest_site_{site_id}.png"),
            "resource_numbers": resources,
        })
    return result


def build_resource() -> dict:
    merged = _raw_mysekai()
    visit = merged.get("userMysekaiGateCharacterVisit") or {}
    gate = visit.get("userMysekaiGate") or {}
    gate_id = max(int(gate.get("mysekaiGateId", 1) or 0), 1)
    gate_level = max(int(gate.get("mysekaiGateLevel", 1) or 0), 1)
    gate_skin_id = int(gate.get("mysekaiGateSkinId", 0) or 0)
    return {
        "profile": _profile_card(merged, include_suite=False),
        "phenoms": _extract_phenoms(merged),
        "gate_id": gate_id,
        "gate_level": gate_level,
        "gate_icon_path": _gate_icon_path(gate_id, gate_skin_id),
        "visit_characters": _visit_characters(merged),
        "site_resource_numbers": _site_resource_numbers(merged),
    }


# ---------------------------------------------------------------------------
# Endpoint 2: /api/pjsk/mysekai/map (map_builder.go + map_builder_resources.go)
# ---------------------------------------------------------------------------

_SITE_CONFIGS: dict[int, dict[str, Any]] = {
    5: {"name": "grassland", "grid": 33.333, "ox": 0.0, "oz": -60.0, "dx": -1.0, "dz": -1.0,
        "rev": True, "crop": [300, 0, 1280, 1080]},
    6: {"name": "beach", "grid": 20.513, "ox": 0.0, "oz": 80.0, "dx": 1.0, "dz": -1.0,
        "rev": False, "crop": [300, 0, 1280, 1080]},
    7: {"name": "flowergarden", "grid": 24.806, "ox": -62.015, "oz": 20.672, "dx": -1.0, "dz": -1.0,
        "rev": True, "crop": [350, 0, 1280, 1080]},
    8: {"name": "memorialplace", "grid": 21.333, "ox": 0.0, "oz": -130.0, "dx": 1.0, "dz": -1.0,
        "rev": False, "crop": [200, 0, 1280, 1080]},
}


def _birthday_refresh_icon_path(char_row: dict) -> str:
    """resolveMysekaiBirthdayRefreshIconPath (controller_resources.go:341-405).

    Scans the local asset tree for mysekai/birthday/{name}_{year}/icon_refresh.png,
    preferring the current year, then the most recent past year, then the nearest
    future year. Returns "" when nothing is synced locally (Go behaves the same).
    """
    name = (char_row.get("givenNameEnglish") or "").strip().lower()
    if not name:
        return ""
    current_year = datetime.now(JP_TZ).year
    base_dir = ASSETS.data_dir / "asset" / f"{common.REGION}-assets" / "ondemand" / "mysekai" / "birthday"
    choose, choose_year, choose_future = "", 0, False
    if base_dir.is_dir():
        for entry in base_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith(f"{name}_"):
                continue
            if not (entry / "icon_refresh.png").exists():
                continue
            try:
                year = int(entry.name.removeprefix(f"{name}_"))
            except ValueError:
                continue
            if year == current_year:
                return ASSETS.region_asset(f"mysekai/birthday/{entry.name}/icon_refresh.png")
            is_future = year > current_year
            if (
                not choose
                or (not is_future and choose_future)
                or (not is_future and not choose_future and year > choose_year)
                or (is_future and choose_future and year < choose_year)
            ):
                choose, choose_year, choose_future = entry.name, year, is_future
    if not choose:
        # Construction-time dependency: record for the rsync manifest.
        ASSETS.candidates.add(f"asset/{common.REGION}-assets/ondemand/mysekai/birthday/{name}_{current_year}/icon_refresh.png")
        return ""
    return ASSETS.region_asset(f"mysekai/birthday/{choose}/icon_refresh.png")


def _map_harvest_points(site_map: dict, birthday_char_by_pos: dict[str, int]) -> list[dict]:
    """map_builder.go:113-183."""
    harvest_fixtures = _md_map("mysekaiSiteHarvestFixtures")
    characters = _md_map("gameCharacters")
    points: list[dict] = []
    for point in site_map.get("userMysekaiSiteHarvestFixtures") or []:
        fixture_id = int(point.get("mysekaiSiteHarvestFixtureId", 0))
        meta = harvest_fixtures.get(fixture_id) or {}
        rarity_type = meta.get("mysekaiSiteHarvestFixtureRarityType", "")
        ab = meta.get("assetbundleName", "")
        fixture_type = meta.get("mysekaiSiteHarvestFixtureType", "")
        if not rarity_type or not ab:
            continue
        if fixture_type == "tone_gust" or "tone_gust" in ab.lower():
            continue
        status = (point.get("userMysekaiSiteHarvestFixtureStatus")
                  or point.get("mysekaiSiteHarvestFixtureStatus") or "spawned")
        x = float(point.get("positionX", point.get("position_x", 0)) or 0)
        z = float(point.get("positionZ", point.get("position_z", 0)) or 0)

        image_rel = f"mysekai/harvest_fixture_icon/{rarity_type}/{ab}.png"
        entry: dict[str, Any] = {}
        offset_x, offset_z = 0.0, -48.0
        if fixture_type == "birthday_plant":
            entry["fallback_image_path"] = ASSETS.static(
                "mysekai/harvest_fixture_icon/rarity_1/mdl_site_wood_common_fieldtree01.png"
            )
            char_id = birthday_char_by_pos.get(_pos_key(x, z), 0)
            if char_id > 0:
                birthday_path = _birthday_refresh_icon_path(characters.get(char_id) or {})
                if birthday_path:
                    image_rel = birthday_path
            entry["size"] = 50
            offset_x, offset_z = 7.5, 0.0

        image_path = image_rel if image_rel.startswith("asset/") else ASSETS.static(image_rel)
        point_out: dict[str, Any] = {
            "image_path": image_path,
            "position_x": x,
            "position_z": z,
            "status": status,
            "offset_x": offset_x,
            "offset_z": offset_z,
        }
        if fixture_id > 0:
            point_out["id"] = fixture_id
        point_out.update(entry)
        points.append(point_out)
    return points


def _map_resource_drops(raw_drops: list) -> list[dict]:
    """buildMapResourceDrops (map_builder_resources.go:14-218)."""
    def is_birthday(resource_type: str, resource_id: int) -> bool:
        return resource_type in ("material", "mysekai_material") and 174 <= resource_id <= 199

    grouped_by_pos: dict[str, dict[str, dict]] = {}
    for drop in raw_drops:
        resource_type, resource_id, status, quantity, x, z = _drop_fields(drop)
        if not resource_type or not resource_id:
            continue
        key = f"{resource_type}_{resource_id}"
        image_path, has_record = _resource_image_path(key)
        if not image_path:
            continue
        status = status or "before_drop"
        rarity = max(_resource_rarity(key), 1)
        pos = _pos_key(x, z)
        group = grouped_by_pos.setdefault(pos, {})
        existing = group.get(key)
        if existing is not None:
            existing["quantity"] += quantity
            if has_record and "attachment_image_path" not in existing:
                existing["attachment_image_path"] = ASSETS.static("mysekai/music_record.png")
            continue
        item: dict[str, Any] = {
            "id": resource_id,
            "type": resource_type,
            "image_path": image_path,
            "position_x": x,
            "position_z": z,
            "quantity": quantity,
            "status": status,
            "hide": False,
            "rarity": rarity,
        }
        if has_record:
            item["attachment_image_path"] = ASSETS.static("mysekai/music_record.png")
        group[key] = item

    drops_out: list[dict] = []
    for group in grouped_by_pos.values():
        has_material = has_fixture = is_cotton = is_sapling = False
        for key, item in group.items():
            if key in ("mysekai_material_1", "mysekai_material_6") and item["quantity"] == 6:
                item["hide"] = True
            if key in ("mysekai_material_21", "mysekai_material_22"):
                is_cotton = True
            if key.startswith("mysekai_material_"):
                has_material = True
            if item["type"] == "mysekai_fixture":
                has_fixture = True
            if is_birthday(item["type"], item["id"]) and item["quantity"] > 16:
                is_sapling = True
        for key, item in group.items():
            small_icon, small_icon_set = False, False
            if has_fixture:
                if has_material:
                    small_icon, small_icon_set = not key.startswith("mysekai_material_"), True
                elif item["type"] == "mysekai_fixture":
                    small_icon, small_icon_set = False, True
                else:
                    small_icon, small_icon_set = True, True
            elif not key.startswith("mysekai_material_") and has_material:
                small_icon, small_icon_set = True, True
            if is_cotton and key not in ("mysekai_material_21", "mysekai_material_22"):
                small_icon, small_icon_set = True, True
            if is_sapling:
                small_icon, small_icon_set = not is_birthday(item["type"], item["id"]), True
            elif is_birthday(item["type"], item["id"]):
                item["hide"] = True
            if small_icon_set:
                item["small_icon"] = small_icon

            if item["rarity"] >= 2:
                item["outline_color"] = [255, 50, 50, 150]
                item["outline_width"] = 2
            elif item.get("small_icon"):
                item["outline_color"] = [50, 50, 255, 100]
                item["outline_width"] = 1
            if item["rarity"] >= 2 and not key.startswith("material_"):
                item["light_size"] = 225 if item.get("small_icon") else 315
            drops_out.append(item)

    drops_out.sort(key=lambda d: (d["position_x"], d["position_z"], d["type"], d["id"]))
    return drops_out


def _build_map_body() -> dict:
    merged = _raw_mysekai()
    harvest_by_site = {int(m.get("mysekaiSiteId", 0)): m
                       for m in _nested_list(merged, "userMysekaiHarvestMaps") if int(m.get("mysekaiSiteId", 0))}
    maps: list[dict] = []
    for site_id in (5, 6, 7, 8):  # mysekaiMapSiteOrder (controller.go:15)
        site_map = harvest_by_site.get(site_id)
        config = _SITE_CONFIGS.get(site_id)
        if not site_map or not config:
            continue
        raw_drops = site_map.get("userMysekaiSiteHarvestResourceDrops") or []
        birthday_char_by_pos: dict[str, int] = {}
        for drop in raw_drops:
            resource_id = int(drop.get("resourceId") or drop.get("id") or 0)
            if not 174 <= resource_id <= 199:
                continue
            x = float(drop.get("positionX", drop.get("position_x", 0)) or 0)
            z = float(drop.get("positionZ", drop.get("position_z", 0)) or 0)
            birthday_char_by_pos.setdefault(_pos_key(x, z), resource_id - 173)

        maps.append({
            "map_id": site_id,
            "site": {
                "image_path": ASSETS.static(f"mysekai/site/{config['name']}.png"),
                "grid_size": config["grid"],
                "offset_x": config["ox"],
                "offset_z": config["oz"],
                "dir_x": config["dx"],
                "dir_z": config["dz"],
                "rev_xz": config["rev"],
                "scale": 0.8,
                "crop_bbox": config["crop"],
            },
            "harvest_points": _map_harvest_points(site_map, birthday_char_by_pos),
            "resource_drops": _map_resource_drops(raw_drops),
        })

    body: dict[str, Any] = {
        "maps": maps,
        # Go default is false and the field has no omitempty; drawing-side default
        # is true so it must always be sent explicitly (map_builder.go:34-37).
        "show_harvested": False,
        "spawn_image_path": ASSETS.static("mysekai/mark.png"),
        "spawn_size": 20,
        "rare_light_image_path": ASSETS.static("mysekai/light.png"),
        "large_icon_size": 35,
        "small_icon_size": 17,
        "icon_zoffset": -32,
    }
    ground = _parse_color_code(
        (_md_map("mysekaiPhenomenaBackgroundColors").get(_current_phenomena_id(merged)) or {}).get("groundColor", "")
    )
    if ground:
        body["phenomena_ground_color"] = ground
    return body


def build_map() -> dict:
    """Single-site map request: exercises the drawer's single-map (no-grid) branch."""
    body = _build_map_body()
    body["maps"] = body["maps"][:1]
    return body


def build_map_multi() -> dict:
    """All harvest sites in one request (mysekaiMapSiteOrder, 4 sites) — the
    production request-builder shape exercising the 2-column grid branch."""
    body = _build_map_body()
    if len(body["maps"]) < 4:
        ISSUES.append(f"mysekai_map_multi: expected >=4 site maps, got {len(body['maps'])}")
    return body


# ---------------------------------------------------------------------------
# Endpoint 3: /api/pjsk/mysekai/fixture-list (fixture_builder.go:13-231)
# ---------------------------------------------------------------------------


def _fixture_thumbnail_path(fixture: dict) -> str:
    """fixtureThumbnailPath (helpers_fixture.go:20-33)."""
    ab = fixture.get("assetbundleName", "")
    if not ab:
        return ""
    if fixture.get("mysekaiFixtureType") == "surface_appearance":
        layout = fixture.get("mysekaiSettableLayoutType") or "floor_appearance"
        return ASSETS.region_asset(f"mysekai/thumbnail/surface_appearance/{ab}/tex_{ab}_{layout}_1.png")
    return ASSETS.region_asset(f"mysekai/thumbnail/fixture/{ab}_1.png")


@cache
def _birthday_character_ids() -> dict[str, int]:
    """givenName -> characterId, sorted-id iteration (helpers_fixture.go:10-18)."""
    return {row["givenName"]: row["id"] for row in sorted(MD.get("gameCharacters"), key=lambda r: r["id"])
            if row.get("givenName")}


def _birthday_character_id(fixture_name: str) -> int:
    for given_name, char_id in _birthday_character_ids().items():
        if fixture_name.endswith(f"（{given_name}）"):
            return char_id
    return 0


@cache
def _user_fixture_ids() -> frozenset[int]:
    """userMysekaiFixtureIDs (controller_resources.go:23-35), raw mysekai doc."""
    ids = set()
    for item in _nested_list(_raw_mysekai(), "userMysekaiFixtures"):
        fixture_id = int(item.get("mysekaiFixtureId", 0) or 0)
        if fixture_id:
            ids.add(fixture_id)
    return frozenset(ids)


def _blueprint_fixture_ids(merged: dict) -> frozenset[int]:
    """userMysekaiBlueprintFixtureIDs (controller_resources.go:37-55)."""
    blueprints = _md_map("mysekaiBlueprints")
    ids = set()
    for item in _nested_list(merged, "userMysekaiBlueprints"):
        blueprint = blueprints.get(int(item.get("mysekaiBlueprintId", 0) or 0))
        if not blueprint or blueprint.get("mysekaiCraftType") != "mysekai_fixture":
            continue
        target = int(blueprint.get("craftTargetId", 0))
        if target:
            ids.add(target)
    return frozenset(ids)


_FORCED_SUB_GENRE_MAIN_IDS = {4, 5, 7, 8, 9, 10, 11, 12, 13}


def build_fixture_list() -> dict:
    """/msf preset: show_id=true, obtained_source="fixture", show_* all true."""
    merged = _raw_mysekai()
    main_genres_md = _md_map("mysekaiFixtureMainGenres")
    sub_genres_md = _md_map("mysekaiFixtureSubGenres")
    obtained_ids = _user_fixture_ids()

    grouped: dict[int, dict[int, list[dict]]] = {}
    main_all: dict[int, int] = {}
    main_obtained: dict[int, int] = {}
    sub_all: dict[int, dict[int, int]] = {}
    sub_obtained: dict[int, dict[int, int]] = {}
    total_all = total_obtained = 0

    for fixture in MD.get("mysekaiFixtures"):
        fixture_id = int(fixture.get("id", 0))
        if not fixture_id or str(fixture.get("mysekaiFixtureType", "")).lower() == "gate":
            continue
        main_id = int(fixture.get("mysekaiFixtureMainGenreId", -1))
        sub_id = int(fixture.get("mysekaiFixtureSubGenreId", -1))
        if fixture_id == 4:
            sub_id = 14
        if main_id in _FORCED_SUB_GENRE_MAIN_IDS:
            sub_id = -1

        grouped.setdefault(main_id, {})
        sub_all.setdefault(main_id, {})
        sub_obtained.setdefault(main_id, {})

        obtained = fixture_id in obtained_ids
        char_id = _birthday_character_id(fixture.get("name", ""))
        row: dict[str, Any] = {
            "id": fixture_id,
            "image_path": _fixture_thumbnail_path(fixture),
            "obtained": obtained,
        }
        if char_id:
            row["character_id"] = char_id
        grouped[main_id].setdefault(sub_id, []).append(row)

        if not char_id:  # birthday fixtures excluded from all progress stats
            total_all += 1
            main_all[main_id] = main_all.get(main_id, 0) + 1
            sub_all[main_id][sub_id] = sub_all[main_id].get(sub_id, 0) + 1
            if obtained:
                total_obtained += 1
                main_obtained[main_id] = main_obtained.get(main_id, 0) + 1
                sub_obtained[main_id][sub_id] = sub_obtained[main_id].get(sub_id, 0) + 1

    main_genres: list[dict] = []
    for main_id in sorted(grouped):
        sub_genres: list[dict] = []
        for sub_id in sorted(grouped[main_id]):
            rows = sorted(grouped[main_id][sub_id], key=lambda r: r["id"])
            if not rows:
                continue
            sub_genre: dict[str, Any] = {"fixtures": rows}
            if sub_id != -1 and len(grouped[main_id]) > 1:
                info = sub_genres_md.get(sub_id)
                if info:
                    sub_genre["name"] = info.get("name", "")
                    sub_genre["image_path"] = ASSETS.region_asset(
                        f"mysekai/icon/category_icon/{info.get('assetbundleName', '')}.png"
                    )
                    total = sub_all[main_id].get(sub_id, 0)
                    if total > 0:
                        done = sub_obtained[main_id].get(sub_id, 0)
                        sub_genre["progress_message"] = f"{done}/{total} ({_pct(done, total):.1f}%)"
            sub_genres.append(sub_genre)
        if not sub_genres:
            continue
        main_info = main_genres_md.get(main_id) or {}
        main_genre: dict[str, Any] = {
            "name": main_info.get("name", ""),
            "image_path": ASSETS.region_asset(f"mysekai/icon/category_icon/{main_info.get('assetbundleName', '')}.png"),
            "sub_genres": sub_genres,
        }
        total = main_all.get(main_id, 0)
        if total > 0:
            done = main_obtained.get(main_id, 0)
            main_genre["progress_message"] = f"{done}/{total} ({_pct(done, total):.1f}%)"
        main_genres.append(main_genre)

    body: dict[str, Any] = {
        "profile": _profile_card(merged, include_suite=False),
        "show_id": True,
        "main_genres": main_genres,
    }
    if total_all > 0:
        body["progress_message"] = (
            f"总收集进度（不含生日家具）: {total_obtained}/{total_all} ({_pct(total_obtained, total_all):.1f}%)"
        )
    return body


# ---------------------------------------------------------------------------
# Endpoint 4: /api/pjsk/mysekai/fixture-detail (fixture_builder.go:246-434)
# ---------------------------------------------------------------------------


def _fixture_color_images(fixture: dict) -> list[dict]:
    """fixtureColorImages (helpers_fixture.go:35-78)."""
    base = _fixture_thumbnail_path(fixture)
    if not base:
        return []
    images: list[dict] = [{"image_path": base}]
    if fixture.get("colorCode"):
        images[0]["color_code"] = fixture["colorCode"]
    ab = fixture.get("assetbundleName", "")
    if not ab:
        return images
    for index, color in enumerate(fixture.get("mysekaiFixtureAnotherColors") or []):
        if fixture.get("mysekaiFixtureType") == "surface_appearance":
            layout = fixture.get("mysekaiSettableLayoutType") or "floor_appearance"
            path = ASSETS.region_asset(
                f"mysekai/thumbnail/surface_appearance/{ab}/tex_{ab}_{layout}_{index + 2}.png"
            )
        else:
            path = ASSETS.region_asset(f"mysekai/thumbnail/fixture/{ab}_{index + 2}.png")
        image: dict[str, Any] = {"image_path": path}
        if color.get("colorCode"):
            image["color_code"] = color["colorCode"]
        images.append(image)
    return images


def _fixture_basic_info(fixture: dict) -> list[str]:
    """fixtureBasicInfo (helpers_fixture.go:80-95)."""
    player_action = fixture.get("mysekaiFixturePlayerActionType", "") not in ("", "no_action")
    return [
        "【🔨可制作】" if fixture.get("isAssembled") else "【❌不可制作】",
        "【♻️可回收】" if fixture.get("isDisassembled") else "【❌不可回收】",
        "【👋玩家可交互】" if player_action else "【❌玩家不可交互】",
        "【🎡角色可交互】" if fixture.get("isGameCharacterAction") else "【❌角色无交互】",
    ]


def _fixture_blueprint_info(blueprint: dict) -> list[str]:
    """fixtureBlueprintInfo (helpers_fixture.go:97-114)."""
    info = [
        "【📝蓝图可抄写】" if blueprint.get("isEnableSketch") else "【蓝图不可抄写】",
        "【🎁蓝图可合成】" if blueprint.get("isObtainedByConvert") else "【蓝图不可合成】",
    ]
    limit = int(blueprint.get("craftCountLimit", 0))
    info.append(f"【最多制作{limit}次】" if limit > 0 else "【无制作次数限制】")
    return info


def _fixture_tags(fixture: dict) -> list[str]:
    """fixtureTags (helpers_fixture.go:116-134)."""
    group = fixture.get("mysekaiFixtureTagGroup")
    if not isinstance(group, dict):
        return []
    tags_md = _md_map("mysekaiFixtureTags")
    result = []
    for i in range(1, 6):
        tag = tags_md.get(int(group.get(f"mysekaiFixtureTagId{i}", 0) or 0)) or {}
        if tag.get("name"):
            result.append(tag["name"])
    return result


def _find_fixture_blueprint(fixture_id: int) -> dict | None:
    for blueprint in MD.get("mysekaiBlueprints"):
        if blueprint.get("mysekaiCraftType") != "mysekai_fixture":
            continue
        if int(blueprint.get("craftTargetId", 0)) == fixture_id:
            return blueprint
    return None


def _material_cost_list(rows: list[dict]) -> list[dict]:
    icons = _icon_map("mysekaiMaterials", "iconAssetbundleName")
    result = []
    for row in rows:
        icon = icons.get(int(row.get("mysekaiMaterialId", 0)), "")
        if not icon:
            continue
        result.append({
            "image_path": ASSETS.region_asset(f"mysekai/thumbnail/material/{icon}.png"),
            "quantity": int(row.get("quantity", 0)),
        })
    return result


# charaIconName (helpers_fixture.go:145-160) — differs from common.character_nickname:
# cuids 32-56 map back to the base virtual singers and unknown cuids map to miku.
_CUID_ICON_NAMES = dict(common._NICKNAMES)
_CUID_ICON_NAMES.update(dict.fromkeys(range(32, 37), "rin"))
_CUID_ICON_NAMES.update(dict.fromkeys(range(37, 42), "len"))
_CUID_ICON_NAMES.update(dict.fromkeys(range(42, 47), "luka"))
_CUID_ICON_NAMES.update(dict.fromkeys(range(47, 52), "meiko"))
_CUID_ICON_NAMES.update(dict.fromkeys(range(52, 57), "kaito"))


def _chara_icon_path(cuid: int) -> str:
    return ASSETS.static(f"chara_icon/{_CUID_ICON_NAMES.get(cuid, 'miku')}.png")


@cache
def _fixture_reactions() -> list | None:
    """fixture_reaction_data asset object (fixture_detail_sources.go:32-49), or None."""
    rel = "mysekai/system/fixture_reaction_data/fixture_reaction_data.json"
    candidates = [
        common.MASTER_DIR / rel,
        ASSETS.data_dir / "asset" / f"{common.REGION}-assets" / "ondemand" / rel,
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text()).get("FixturerRactions") or []
    ASSETS.candidates.add(f"asset/{common.REGION}-assets/ondemand/{rel}")
    return None


def _reaction_character_groups(fixture_id: int) -> list[dict] | None:
    """fixtureReactionCharacterGroups (fixture_builder.go:378-434)."""
    reactions = _fixture_reactions()
    if reactions is None:
        return None
    grouped: dict[int, list[list[int]]] = {}
    for item in reactions:
        if int(item.get("FixtureId", 0)) != fixture_id:
            continue
        for entry in item.get("ReactionCharacter") or []:
            cuids = [int(c) for c in entry.get("CharacterUnitIds") or [] if int(c)]
            if cuids:
                grouped.setdefault(len(cuids), []).append(cuids)
    return [
        {
            "number": count,
            "character_uint_id_groups": grouped[count],
            "chara_icon_path_groups": [[_chara_icon_path(cuid) for cuid in group] for group in grouped[count]],
        }
        for count in sorted(grouped)
    ] or None


def _pick_fixture_detail_ids() -> list[int]:
    """Representative ids covering the builder's branches."""
    fixtures = MD.get("mysekaiFixtures")
    craftable = {int(b.get("craftTargetId", 0)) for b in MD.get("mysekaiBlueprints")
                 if b.get("mysekaiCraftType") == "mysekai_fixture"}
    ids: list[int] = [1, 4]  # blueprint w/ craft limit; forced-subgenre special case
    ids.extend(int(r["mysekaiFixtureId"]) for r in MD.get("mysekaiFixtureOnlyDisassembleMaterials")[:2])
    for fixture in fixtures:  # first surface_appearance fixture
        if fixture.get("mysekaiFixtureType") == "surface_appearance":
            ids.append(fixture["id"])
            break
    for fixture in fixtures:  # first multi-color fixture
        if fixture.get("mysekaiFixtureAnotherColors"):
            ids.append(fixture["id"])
            break
    for fixture in fixtures:  # first fixture without a blueprint
        if fixture["id"] not in craftable and fixture.get("mysekaiFixtureType") != "gate":
            ids.append(fixture["id"])
            break
    for fixture in fixtures:  # first birthday fixture (name ends with （givenName）)
        if _birthday_character_id(fixture.get("name", "")):
            ids.append(fixture["id"])
            break
    seen: set[int] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def build_fixture_details() -> list[dict]:
    fixture_map = _md_map("mysekaiFixtures")
    main_genres = _md_map("mysekaiFixtureMainGenres")
    sub_genres = _md_map("mysekaiFixtureSubGenres")
    costs = MD.get("mysekaiBlueprintMysekaiMaterialCosts")
    disassemble = MD.get("mysekaiFixtureOnlyDisassembleMaterials")

    requests: list[dict] = []
    fabricated_friendcodes = False
    for fixture_id in _pick_fixture_detail_ids():
        fixture = fixture_map.get(fixture_id)
        if not fixture:
            continue
        main = main_genres.get(int(fixture.get("mysekaiFixtureMainGenreId", 0))) or {}
        grid = fixture.get("gridSize") or {}
        request: dict[str, Any] = {
            "title": f"【{common.REGION.upper()}-{fixture_id}】{fixture.get('name', '')}",
            "images": _fixture_color_images(fixture),
            "main_genre_name": main.get("name", ""),
            "main_genre_image_path": ASSETS.region_asset(
                f"mysekai/icon/category_icon/{main.get('assetbundleName', '')}.png"
            ),
            "size": {k: int(grid.get(k, 0) or 0) for k in ("width", "depth", "height")},
            "first_put_cost": int(fixture.get("firstPutCost", 0) or 0),
            "second_put_cost": int(fixture.get("secondPutCost", 0) or 0),
            "basic_info": _fixture_basic_info(fixture),
        }
        tags = _fixture_tags(fixture)
        if tags:
            request["tags"] = tags
        reaction_groups = _reaction_character_groups(fixture_id)
        if reaction_groups:
            request["reaction_character_groups"] = reaction_groups
        recycle = _material_cost_list([r for r in disassemble if int(r.get("mysekaiFixtureId", 0)) == fixture_id])
        if recycle:
            request["recycle_materials"] = recycle
        sub_id = int(fixture.get("mysekaiFixtureSubGenreId", 0) or 0)
        if sub_id != 0:
            sub = sub_genres.get(sub_id) or {}
            request["sub_genre_name"] = sub.get("name", "")
            request["sub_genre_image_path"] = ASSETS.region_asset(
                f"mysekai/icon/category_icon/{sub.get('assetbundleName', '')}.png"
            )
        blueprint = _find_fixture_blueprint(fixture_id)
        if blueprint:
            request["basic_info"] = request["basic_info"] + _fixture_blueprint_info(blueprint)
            cost = _material_cost_list([r for r in costs
                                        if int(r.get("mysekaiBlueprintId", 0)) == int(blueprint.get("id", 0))])
            if cost:
                request["cost_materials"] = cost
            if blueprint.get("isEnableSketch") and not fabricated_friendcodes:
                # External source (pjsk-static.8823.eu.org) — fabricated offline.
                request["friendcodes"] = ["1145141919810", "8931145141919", "4545145141919", "1919810893931"]
                request["friendcode_source"] = "sekai.8823.eu.org"
                fabricated_friendcodes = True
        requests.append(request)
    return requests


# ---------------------------------------------------------------------------
# Endpoint 5: /api/pjsk/mysekai/door-upgrade (door_upgrade_builder.go:11-178)
# ---------------------------------------------------------------------------

_GATE_MAX_LEVEL = 40


def build_door_upgrade() -> dict:
    """Default query: no gate id — picks the highest-level gate below 40 (suite-only)."""
    merged = dict(common.load_suite())  # suite-only path (handler/mysekai.go:603-607)

    user_materials = {int(i.get("mysekaiMaterialId", 0)): int(i.get("quantity", 0))
                      for i in _nested_list(merged, "userMysekaiMaterials")}
    spec_levels = {int(i.get("mysekaiGateId", 0)): int(i.get("mysekaiGateLevel", 0))
                   for i in _nested_list(merged, "userMysekaiGates") if int(i.get("mysekaiGateId", 0))}

    gate_temp: dict[int, list[list[dict]]] = {}
    for item in MD.get("mysekaiGateMaterialGroups"):
        group_id = int(item.get("groupId", 0))
        gate_id, level = group_id // 1000, group_id % 1000
        if not group_id or not gate_id or not 1 <= level <= _GATE_MAX_LEVEL:
            continue
        gate_temp.setdefault(gate_id, [[] for _ in range(_GATE_MAX_LEVEL)])
        gate_temp[gate_id][level - 1].append(
            {"material_id": int(item.get("mysekaiMaterialId", 0)), "quantity": int(item.get("quantity", 0))}
        )

    spec_gate_id, best_level = 0, 0
    for gate_id in sorted(spec_levels):
        level = spec_levels[gate_id]
        if level == _GATE_MAX_LEVEL or level <= best_level:
            continue
        best_level, spec_gate_id = level, gate_id
    if spec_gate_id and spec_gate_id in gate_temp:
        gate_temp = {spec_gate_id: gate_temp[spec_gate_id]}

    material_icons = _icon_map("mysekaiMaterials", "iconAssetbundleName")
    green, red, gray = [0, 200, 0], [200, 0, 0], [50, 50, 50]

    gate_materials: list[dict] = []
    for gate_id in sorted(gate_temp):
        level_mats = gate_temp[gate_id]
        current_level = spec_levels.get(gate_id, 0)
        if 0 < current_level < len(level_mats):
            level_mats = level_mats[current_level:]
        elif current_level >= len(level_mats):
            level_mats = []

        sum_materials: dict[int, int] = {}
        out_levels: list[dict] = []
        for index, items in enumerate(level_mats):
            if not items:
                continue
            level_color = gray
            out_items: list[dict] = []
            for item in items:
                material_id = item["material_id"]
                sum_materials[material_id] = sum_materials.get(material_id, 0) + item["quantity"]
                user_qty = user_materials.get(material_id, 0)
                color = green
                if user_qty < sum_materials[material_id]:
                    color = red
                    level_color = red
                out_items.append({
                    "image_path": ASSETS.region_asset(
                        f"mysekai/thumbnail/material/{material_icons.get(material_id, '')}.png"
                    ),
                    "quantity": item["quantity"],
                    "color": color,
                    "sum_quantity": f"{_fmt_qty(user_qty)}/{sum_materials[material_id]}",
                })
            out_levels.append({"level": current_level + index + 1, "color": level_color, "items": out_items})
        gate_materials.append({
            "id": gate_id,
            "level": current_level,
            "gate_icon_path": _gate_icon_path(gate_id, 0),
            "level_materials": out_levels,
        })

    return {
        "profile": _profile_card(merged, include_suite=False, suite_name=True),
        "gate_materials": gate_materials,
    }


# ---------------------------------------------------------------------------
# Endpoint 6: /api/pjsk/mysekai/music-record (music_record_builder.go:12-175)
# ---------------------------------------------------------------------------

_MUSIC_TAG_ORDER = ["light_music_club", "street", "idol", "theme_park", "school_refusal", "vocaloid", "other"]


def build_music_record() -> dict:
    """show_id=true (the `/mss id` variant) so record ids are drawn too."""
    merged = _raw_mysekai()
    obtained_records = {int(i.get("mysekaiMusicRecordId", 0)): int(i.get("obtainedAt", 0))
                        for i in _nested_list(merged, "userMysekaiMusicRecords")}
    musics = _md_map("musics")
    limited_by_music: dict[int, list[dict]] = {}
    for item in MD.get("limitedTimeMusics"):
        music_id = int(item.get("musicId", 0))
        if music_id:
            limited_by_music.setdefault(music_id, []).append(item)
    tag_by_music: dict[int, str] = {}
    for item in MD.get("musicTags"):
        music_id, tag = int(item.get("musicId", 0)), item.get("musicTag", "")
        if not music_id or not tag or tag in ("all", "vocaloid"):
            continue
        tag_by_music.setdefault(music_id, tag)

    category_music_ids: dict[str, list[int]] = {tag: [] for tag in _MUSIC_TAG_ORDER}
    music_obtained_at: dict[int, int] = {}
    music_present: set[int] = set()
    for record in MD.get("mysekaiMusicRecords"):
        if record.get("mysekaiMusicTrackType") != "music":
            continue
        record_id, music_id = int(record.get("id", 0)), int(record.get("externalId", 0))
        if not record_id or not music_id or music_id in (241, 290):
            continue
        music = musics.get(music_id)
        if not music or int(music.get("publishedAt", 0)) > NOW_MS:
            continue
        windows = limited_by_music.get(music_id, [])
        if windows and not any(int(w.get("startAt", 0)) <= NOW_MS <= int(w.get("endAt", 0)) for w in windows):
            continue
        if record_id in obtained_records:
            music_obtained_at[music_id] = obtained_records[record_id]
            music_present.add(music_id)
        tag = tag_by_music.get(music_id) or "vocaloid"
        category_music_ids[tag].append(music_id)

    tag_icons = {
        "light_music_club": ASSETS.static("icon_light_sound.png"),
        "idol": ASSETS.static("icon_idol.png"),
        "street": ASSETS.static("icon_street.png"),
        "theme_park": ASSETS.static("icon_theme_park.png"),
        "school_refusal": ASSETS.static("icon_school_refusal.png"),
        "vocaloid": ASSETS.static("icon_piapro.png"),
        "other": "",
    }

    total_count = obtained_count = 0
    categories: list[dict] = []
    for tag in _MUSIC_TAG_ORDER:
        music_ids = sorted(
            category_music_ids[tag],
            key=lambda m: (0, music_obtained_at.get(m, 0), m) if m in music_present else (1, 0, m),
        )
        category_total, category_obtained = len(music_ids), 0
        records: list[dict] = []
        for music_id in music_ids:
            total_count += 1
            if music_obtained_at.get(music_id, 0) != 0:
                obtained_count += 1
                category_obtained += 1
            ab = musics[music_id].get("assetbundleName", "")
            if not ab:  # counted above but skipped (music_record_builder.go:133-142)
                continue
            records.append({
                "id": music_id,
                "image_path": ASSETS.region_asset(f"music/jacket/{ab}/{ab}.png"),
                "obtained": music_obtained_at.get(music_id, 0) != 0,
            })
        if not category_total:
            continue
        categories.append({
            "tag": tag,
            "tag_icon_path": tag_icons[tag],
            "progress_message": (
                f"{category_obtained}/{category_total} ({_pct(category_obtained, category_total):.1f}%)"
            ),
            "musicrecords": records,
        })

    body: dict[str, Any] = {
        "profile": _profile_card(merged, include_suite=False),
        "category_musicrecords": categories,
    }
    if total_count > 0:
        body["progress_message"] = (
            f"总收集进度: {obtained_count}/{total_count} ({_pct(obtained_count, total_count):.1f}%)"
        )
    return body


# ---------------------------------------------------------------------------
# Endpoint 7: /api/pjsk/mysekai/talk-list (talk_builder.go:13-311)
# ---------------------------------------------------------------------------

TALK_CHARACTER_ID = 17  # 宵崎奏 (knd)


def _extract_group_cuids(group: dict) -> list[int]:
    return [int(group.get(f"gameCharacterUnitId{i}", 0) or 0)
            for i in range(1, 10) if int(group.get(f"gameCharacterUnitId{i}", 0) or 0)]


def build_talk_list() -> dict:
    merged = _merged()  # suite+mysekai merged path; talks always from suite
    character_unit_id = next(
        u["id"] for u in MD.get("gameCharacterUnits") if u.get("gameCharacterId") == TALK_CHARACTER_ID
    )

    obtained_fixture_ids = _blueprint_fixture_ids(merged)
    fixture_map = _md_map("mysekaiFixtures")
    main_genres_md = _md_map("mysekaiFixtureMainGenres")
    unit_groups = _md_map("mysekaiGameCharacterUnitGroups")
    archive_groups = _md_map("characterArchiveMysekaiCharacterTalkGroups")

    user_talk_reads = {
        int(i.get("mysekaiCharacterTalkId", 0)): bool(i.get("isRead"))
        for i in _nested_list(merged, "userMysekaiCharacterTalks") if int(i.get("mysekaiCharacterTalkId", 0))
    }

    condition_ids_by_fixture: dict[int, list[int]] = {}
    for condition in MD.get("mysekaiCharacterTalkConditions"):
        if condition.get("mysekaiCharacterTalkConditionType") != "mysekai_fixture_id":
            continue
        fixture_id = int(condition.get("mysekaiCharacterTalkConditionTypeValue", 0))
        if fixture_id:
            condition_ids_by_fixture.setdefault(fixture_id, []).append(int(condition.get("id", 0)))
    group_ids_by_condition: dict[int, list[int]] = {}
    for group in MD.get("mysekaiCharacterTalkConditionGroups"):
        group_ids_by_condition.setdefault(
            int(group.get("mysekaiCharacterTalkConditionId", 0)), []
        ).append(int(group.get("id", 0)))
    talks_by_group: dict[int, list[dict]] = {}
    for talk in MD.get("mysekaiCharacterTalks"):
        talks_by_group.setdefault(int(talk.get("mysekaiCharacterTalkConditionGroupId", 0)), []).append(talk)

    archive_reads: dict[int, dict] = {}
    for fixture in MD.get("mysekaiFixtures"):
        fixture_id = int(fixture.get("id", 0))
        if not fixture_id or fixture.get("mysekaiFixtureType") == "gate":
            continue
        group_ids: set[int] = set()
        for condition_id in condition_ids_by_fixture.get(fixture_id, []):
            group_ids.update(group_ids_by_condition.get(condition_id, []))
        for group_id in sorted(group_ids):
            for talk in talks_by_group.get(group_id, []):
                group = unit_groups.get(int(talk.get("mysekaiGameCharacterUnitGroupId", 0)))
                if not group:
                    continue
                cuids = _extract_group_cuids(group)
                if character_unit_id not in cuids:
                    continue
                archive_id = int(talk.get("characterArchiveMysekaiCharacterTalkGroupId", 0))
                archive = archive_groups.get(archive_id)
                if archive and archive.get("archiveDisplayType") != "normal":
                    continue
                read = archive_reads.setdefault(archive_id, {"fixture_ids": [], "cuids": [], "has_read": False})
                if fixture_id not in read["fixture_ids"]:
                    read["fixture_ids"].append(fixture_id)
                read["cuids"] = cuids
                if user_talk_reads.get(int(talk.get("id", 0))):
                    read["has_read"] = True

    single_reads: dict[str, dict] = {}
    multi_reads_map: dict[str, dict] = {}
    # Go iterates archiveReads in map order (nondeterministic); sorted archive id
    # order is used here, which fixes cuidsSet ordering inside each group.
    for archive_id in sorted(archive_reads):
        item = archive_reads[archive_id]
        fixture_ids = sorted(item["fixture_ids"])
        key = " ".join(str(i) for i in fixture_ids)
        target = multi_reads_map if len(item["cuids"]) > 1 else single_reads
        entry = target.setdefault(key, {"fixture_ids": fixture_ids, "read": 0, "total": 0, "cuids_set": []})
        entry["fixture_ids"] = fixture_ids
        entry["total"] += 1
        if item["has_read"]:
            entry["read"] += 1
            continue
        if len(item["cuids"]) > 1 and item["cuids"] not in entry["cuids_set"]:
            entry["cuids_set"].append(item["cuids"])

    def fixture_row(fixture_id: int) -> dict:
        return {
            "id": fixture_id,
            "image_path": _fixture_thumbnail_path(fixture_map.get(fixture_id) or {}),
            "obtained": fixture_id in obtained_fixture_ids,
        }

    grouped_single: dict[int, list[dict]] = {}
    for key in sorted(single_reads):
        item = single_reads[key]
        if item["total"] == item["read"]:
            continue
        fixture_ids = item["fixture_ids"]
        main_genre_id = int((fixture_map.get(fixture_ids[0]) or {}).get("mysekaiFixtureMainGenreId", 0))
        grouped_single.setdefault(main_genre_id, []).append({
            "fixtures": [fixture_row(fid) for fid in fixture_ids],
            "noread_num": item["total"] - item["read"],
        })

    def single_cmp(left: dict, right: dict) -> int:
        lf, rf = left["fixtures"], right["fixtures"]
        if len(lf) != len(rf):
            return -1 if len(lf) > len(rf) else 1
        for a, b in zip(lf, rf, strict=False):
            if a["id"] != b["id"]:
                return -1 if a["id"] > b["id"] else 1
        if left["noread_num"] != right["noread_num"]:
            return -1 if left["noread_num"] > right["noread_num"] else 1
        return 0

    single_main_genres: list[dict] = []
    for main_genre_id in sorted(grouped_single):
        info = main_genres_md.get(main_genre_id) or {}
        groups = sorted(grouped_single[main_genre_id], key=cmp_to_key(single_cmp))
        single_main_genres.append({
            "name": info.get("name", ""),
            "image_path": ASSETS.region_asset(f"mysekai/icon/category_icon/{info.get('assetbundleName', '')}.png"),
            "sub_genres": [groups],
        })

    total_talks = sum(i["total"] for i in single_reads.values())
    total_reads = sum(i["read"] for i in single_reads.values())

    multi_reads: list[dict] = []
    for key in sorted(multi_reads_map):
        item = multi_reads_map[key]
        total_talks += item["total"]
        total_reads += item["read"]
        if item["total"] == item["read"]:
            continue
        multi_reads.append({
            "fixtures": [fixture_row(fid) for fid in item["fixture_ids"]],
            "noread_num": item["total"] - item["read"],
            "character_ids": item["cuids_set"],
            "chara_icon_path_groups": [[_chara_icon_path(cuid) for cuid in cuids] for cuids in item["cuids_set"]],
        })
    multi_reads.sort(key=lambda m: (-len(m["fixtures"]), m["fixtures"][0]["id"] if m["fixtures"] else 0))

    return {
        "profile": _profile_card(merged, include_suite=True),
        "sd_image_path": ASSETS.region_asset(f"character/character_sd_l/chr_sp_{character_unit_id}.png"),
        "progress_message": (
            f"未读对话家具列表 - 进度: {total_reads}/{total_talks} ({_pct(total_reads, total_talks):.1f}%)"
        ),
        "prompt_message": "*仅展示未读对话家具，灰色表示未获得蓝图",
        "show_id": True,
        "single_main_genres": single_main_genres,
        "multi_reads": multi_reads,
    }


# ---------------------------------------------------------------------------
# Endpoint 8: /api/pjsk/mysekai/housing-competition (housing_competition.go:88-162)
# ---------------------------------------------------------------------------


def _resolve_housing_competition() -> tuple[dict, bool]:
    """resolveHousingCompetition (housing_competition.go:312-370): active first,
    else fall back to the most recent past competition."""
    active: dict | None = None
    latest_past: dict | None = None

    def start_at(item: dict) -> int:
        return int(item.get("reviewStartAt", 0) or 0) or int(item.get("submitStartAt", 0) or 0)

    for item in MD.get("mysekaiHousingCompetitions"):
        start, aggregate = start_at(item), int(item.get("aggregateAt", 0) or 0)
        if start <= 0 or aggregate <= 0:
            continue
        if start <= NOW_MS < aggregate:
            if active is None or (start_at(active), active["id"]) < (start, item["id"]):
                active = item
        elif start <= NOW_MS:
            if latest_past is None or (start_at(latest_past), latest_past["id"]) < (start, item["id"]):
                latest_past = item
    if active:
        return active, True
    return latest_past or MD.get("mysekaiHousingCompetitions")[-1], False


def _fake_thumbnail_b64(color: tuple[int, int, int]) -> str:
    from PIL import Image  # local import: only needed for fabricated thumbnails

    image = Image.new("RGB", (160, 90), color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def build_housing_competition() -> dict:
    competition, active = _resolve_housing_competition()
    competition_id = int(competition["id"])
    name = competition.get("name", "") or f"第{competition_id}期"
    if not active:
        ISSUES.append(
            f"housing-competition: 当前无进行中的百景期数,退回最近一期 id={competition_id}(生产会直接报错)"
        )

    bg = competition.get("backgroundImageAssetbundleFileName", "")
    if bg:
        banner_path = ASSETS.region_asset(
            f"mysekai/effect/ui_anim/mysekai_housing_competition/lottery_result/{bg}.png"
        )
    else:
        banner_path = ASSETS.static("unknown.jpg")

    banner_b64: str | None = None
    banner_file = ASSETS.data_dir / banner_path
    if banner_file.exists():
        banner_b64 = base64.b64encode(banner_file.read_bytes()).decode()

    # Entries come from the external SekaiAPI list endpoint
    # (/api/{server}/user/mysekai/housing-competition/{id}/list?isLottery=true) —
    # fully fabricated offline with the parseHousingCompetitionEntries shape.
    palette = [(96, 160, 255), (255, 160, 96), (128, 208, 128), (208, 128, 208), (255, 208, 96)]
    unique_count = 24
    base_review, base_submit = 98765, NOW_MS - 86_400_000 * 3
    all_entries = [
        {
            "review_count": base_review - index * (137 + index * 11),
            "owner_user_name": f"すたーだすと{index + 1:02d}",
            "name": f"ワンダショなセカイ #{index + 1}",
            "word": "みんなでわいわい作りました！見ていってね～",
            "thumbnail_path": f"mysekai-housing/thumbnail/upload/{competition_id}/entry_{index + 1:04d}.png",
            "submitted_at": base_submit + index * 3_600_000,
        }
        for index in range(unique_count)
    ]

    entries: list[dict] = []
    for rank in (1, 2, 3, 4, 5):
        index = rank - 1
        entry = dict(all_entries[index])
        entry["rank"] = rank
        entry["thumbnail_image_base64"] = _fake_thumbnail_b64(palette[index % len(palette)])
        if index > 0:
            prev_score = all_entries[index - 1]["review_count"]
            entry["previous_review_count"] = prev_score
            entry["previous_delta"] = prev_score - entry["review_count"]
        if index + 1 < len(all_entries):
            next_score = all_entries[index + 1]["review_count"]
            entry["next_review_count"] = next_score
            entry["next_delta"] = entry["review_count"] - next_score
        entries.append(entry)

    body: dict[str, Any] = {
        "competition_id": competition_id,
        "region": common.REGION,
        "name": f"烤森百景 {name}".strip(),
        "description": "基于统计得出结果并不一定精确，仅供参考",
        "banner_image_path": banner_path,
        "sample_count": 1,
        "unique_count": unique_count,
        "sampled_at": NOW_MS,
        "entries": entries,
    }
    if banner_b64:
        body["banner_image_base64"] = banner_b64
    else:
        ISSUES.append("housing-competition: banner 资产本地缺失,banner_image_base64 置空(rsync 后可补)")
    return body


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _write_list_payload(name: str, items: list[dict]) -> None:
    """fixture-detail body is a JSON array (client.go:385); finalize each element."""
    finalized = [common.finalize(dict(item)) for item in items]
    common.OUT_DIR.mkdir(parents=True, exist_ok=True)
    (common.OUT_DIR / f"{name}.json").write_text(json.dumps(finalized, ensure_ascii=False, indent=1))


def generate() -> list[str]:
    written: list[str] = []

    for name, builder, model in (
        ("mysekai_resource", build_resource, MysekaiResourceRequest),
        ("mysekai_map", build_map, MysekaiMsrMapRequest),
        ("mysekai_map_multi", build_map_multi, MysekaiMsrMapRequest),
        ("mysekai_fixture_list", build_fixture_list, MysekaiFixtureListRequest),
        ("mysekai_door_upgrade", build_door_upgrade, MysekaiDoorUpgradeRequest),
        ("mysekai_music_record", build_music_record, MysekaiMusicrecordRequest),
        ("mysekai_talk_list", build_talk_list, MysekaiTalkListRequest),
        ("mysekai_housing_competition", build_housing_competition, MysekaiHousingCompetitionRequest),
    ):
        body = builder()
        model.model_validate(body)
        common.write_payload(name, body)
        written.append(name)

    details = build_fixture_details()
    for item in details:
        MysekaiFixtureDetailRequest.model_validate(item)
    _write_list_payload("mysekai_fixture_detail", details)
    written.append("mysekai_fixture_detail")
    return written


if __name__ == "__main__":
    names = generate()
    print("written:", names)  # noqa: T201
    for issue in ISSUES:
        print("issue:", issue)  # noqa: T201
    common.ASSETS.save_manifest()
    print("missing assets:", len(common.ASSETS.missing))  # noqa: T201
