"""Real-payload generator: gacha / costume / vlive / education endpoints.

Replicates Haruki-Cloud's request-body construction offline per
``out/payload-specs/gacha-costume-vlive-edu.md``. Go references:
``internal/pjsk/render/{gacha,costume,vlive,education}`` in the Cloud repo.

Fixed clocks (offline reproducibility, see spec CAVEATS):
- gacha/education use ``suite.now`` as "now";
- vlive uses a fixed busy moment so the list is representative.
"""

import copy
import json
import math
from pathlib import Path
import sys

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import common

from src.sekai.costume.model import CostumeDetailRequest, CostumeListRequest
from src.sekai.education.model import (
    AreaItemUpgradeMaterialsRequest,
    BondsRequest,
    ChallengeLiveDetailsRequest,
    CharacterMissionAllRequest,
    CharacterMissionOverviewRequest,
    LeaderCountRequest,
    PowerBonusDetailRequest,
)
from src.sekai.gacha.model import GachaDetailRequest, GachaListRequest
from src.sekai.vlive.model import VLiveListRequest

MD = common.MD
ASSETS = common.ASSETS
SUITE = common.load_suite()
NOW_MS = int(SUITE.get("now", 0))
# 2025-10-25 14:00 JST — moment with the richest concurrent virtual-live window.
VLIVE_NOW_MS = 1_761_372_000_000

MS_7_DAYS = 7 * 24 * 3600 * 1000
MS_30_DAYS = 30 * 24 * 3600 * 1000


def _emit(name: str, model_cls, body: dict) -> str:
    """Finalize, validate against the drawing pydantic model, then write."""
    body = common.finalize(body)
    model_cls.model_validate(copy.deepcopy(body))
    common.write_payload(name, body)
    return name


# ===========================================================================
# gacha (render/gacha/builder.go + builder_detail.go)
# ===========================================================================

GACHA_END_PADDING_MS = 60_000


def _extract_numeric_token(asset_name: str) -> str:
    """Last run of digits in the string (builder_detail.go:315-332)."""
    last, current = "", ""
    for ch in asset_name:
        if ch.isdigit():
            current += ch
            continue
        if current:
            last, current = current, ""
    return current or last


def _gacha_logo_rels(gacha: dict) -> list[str]:
    rels = []
    abn = (gacha.get("assetbundleName") or "").strip()
    if abn:
        rels += [f"gacha/{abn}/logo/logo.png", f"logo/{abn}.png"]
        if digits := _extract_numeric_token(abn):
            rels.append(f"logo/banner_logo{digits}.png")
    rels += [
        f"gacha/ab_gacha_{gacha['id']}/logo/logo.png",
        f"logo/banner_logo{gacha.get('seq', 0)}.png",
        f"logo/banner_logo{gacha['id']}.png",
    ]
    return rels


def _gacha_banner_rels(gacha: dict) -> list[str]:
    gid = gacha["id"]
    abn = gacha.get("assetbundleName", "")
    return [
        f"home/banner/banner_gacha{gid}/banner_gacha{gid}.png",
        f"gacha/ab_gacha_{gid}/screen/texture/bg_gacha{gid}.png",
        f"home/banner/{abn}/{abn}.png",
        f"gacha/{abn}.png",
        f"gacha/banner_gacha{gid}.png",
    ]


def _started_gachas() -> list[dict]:
    """include_past=true, include_future=false at NOW_MS, (startAt, id) asc."""
    items = [g for g in MD.get("gachas") if g.get("startAt", 0) <= NOW_MS]
    items.sort(key=lambda g: (g.get("startAt", 0), g["id"]))
    return items


def build_gacha_list() -> str:
    page, page_size = 48, 20  # "/卡池 p48": a full page of recent pools
    items = _started_gachas()
    total_pages = max(1, math.ceil(len(items) / page_size))
    current_page = total_pages if page <= 0 else min(page, total_pages)
    page_items = items[(current_page - 1) * page_size : (current_page - 1) * page_size + page_size]

    briefs, logos, banners = [], {}, {}
    for g in page_items:
        briefs.append(
            {
                "id": g["id"],
                "name": g.get("name", ""),
                "gacha_type": g.get("gachaType", ""),
                "start_at": g.get("startAt", 0),
                "end_at": g.get("endAt", 0),
                "asset_name": g.get("assetbundleName", ""),
            }
        )
        logos[g["id"]] = ASSETS.region_asset(*_gacha_logo_rels(g))
        banners[g["id"]] = ASSETS.region_asset(*_gacha_banner_rels(g))

    body = {
        "gachas": briefs,
        "page_size": page_size,
        "region": common.REGION,
        "gacha_logos": logos,
        "gacha_banners": banners,
        "current_page": current_page,
        "total_page": total_pages,
        "pre_paginated": True,
        "filter": {"page": current_page},
        "dt": NOW_MS,
    }
    return _emit("gacha_list", GachaListRequest, body)


def _gacha_pickup_order(gacha: dict) -> list[int]:
    pickup_order: list[int] = []
    seen: set[int] = set()
    for pickup in gacha.get("gachaPickups", []):
        card_id = pickup["cardId"]
        if card_id not in seen:
            pickup_order.append(card_id)
            seen.add(card_id)
    return pickup_order


def _gacha_guaranteed_type(gacha: dict) -> str:
    guaranteed_type = ""
    for behavior in gacha.get("gachaBehaviors", []):
        kind = str(behavior.get("gachaBehaviorType", "")).lower()
        if kind == "over_rarity_4_once":
            guaranteed_type = "rarity_4"
        elif kind == "over_rarity_3_once" and guaranteed_type != "rarity_4":
            guaranteed_type = "rarity_3"
    return guaranteed_type


def _gacha_card_weights(gacha: dict) -> tuple[dict[str, int], dict[int, float], dict[int, str], dict[str, float]]:
    rarity_counts = dict.fromkeys(("rarity_1", "rarity_2", "rarity_3", "rarity_4", "rarity_birthday"), 0)
    card_weight: dict[int, float] = {}
    card_rarity: dict[int, str] = {}
    rarity_weights: dict[str, float] = {}
    cards = MD.card_by_id()
    for detail in gacha.get("gachaDetails", []):
        card = cards.get(detail["cardId"])
        if not card:
            continue
        rarity = card["cardRarityType"].lower()
        card_rarity[card["id"]] = rarity
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
        card_weight[card["id"]] = card_weight.get(card["id"], 0.0) + detail.get("weight", 0)
        rarity_weights[rarity] = rarity_weights.get(rarity, 0.0) + detail.get("weight", 0)
    return rarity_counts, card_weight, card_rarity, rarity_weights


def _gacha_normal_rates(gacha: dict, rarity_counts: dict[str, int]) -> tuple[dict[str, float], dict]:
    rarity_fraction: dict[str, float] = {}
    weight_info: dict = {"guaranteed_rates": {}}
    for rate in gacha.get("gachaCardRarityRates", []):
        if str(rate.get("lotteryType", "")).lower() != "normal":
            continue
        rarity = str(rate.get("cardRarityType", "")).lower()
        fraction = rate.get("rate", 0.0) / 100.0
        if rarity in rarity_counts:
            weight_info[f"{rarity}_rate"] = fraction
        rarity_fraction[rarity] = fraction
    return rarity_fraction, weight_info


def _gacha_guaranteed_rates(rarity_fraction: dict[str, float], guaranteed_type: str) -> dict[str, float]:
    guaranteed = dict.fromkeys(("rarity_1", "rarity_2", "rarity_3", "rarity_4", "rarity_birthday"), 0.0)
    guaranteed.update(rarity_fraction)
    guaranteed[guaranteed_type] += guaranteed["rarity_2"]
    guaranteed["rarity_2"] = 0.0
    if guaranteed_type == "rarity_4":
        guaranteed[guaranteed_type] += guaranteed["rarity_3"]
        guaranteed["rarity_3"] = 0.0
    return guaranteed


def _gacha_card_rate(
    card_id: int,
    card_rarity: dict[int, str],
    rarity_weights: dict[str, float],
    rarity_fraction: dict[str, float],
    card_weight: dict[int, float],
) -> float:
    rarity = card_rarity.get(card_id, "")
    total = rarity_weights.get(rarity, 0.0)
    base = rarity_fraction.get(rarity, 0.0)
    if not rarity or total <= 0 or base == 0:
        return 0.0
    return (card_weight.get(card_id, 0.0) / total) * base


def _gacha_pickup_cards(
    pickup_order: list[int],
    card_rarity: dict[int, str],
    rarity_weights: dict[str, float],
    rarity_fraction: dict[str, float],
    card_weight: dict[int, float],
) -> list[dict]:
    pickup_cards = []
    cards = MD.card_by_id()
    for card_id in pickup_order:
        card = cards.get(card_id)
        if not card:
            continue
        card_rarity.setdefault(card_id, card["cardRarityType"].lower())
        pickup_cards.append(
            {
                "id": card["id"],
                "rarity": card["cardRarityType"],
                "rate": _gacha_card_rate(card["id"], card_rarity, rarity_weights, rarity_fraction, card_weight),
                "thumbnail_request": common.card_thumbnail(card, thumb_after=False),
            }
        )
    return pickup_cards


def _gacha_ceil_item_path(gacha: dict) -> str:
    ceil_item_id = gacha.get("gachaCeilItemId") or 0
    if not ceil_item_id:
        return ""
    ceil_item = next((item for item in MD.get("gachaCeilItems") if item["id"] == ceil_item_id), None)
    abn = (ceil_item or {}).get("assetbundleName", "").strip()
    if not abn:
        return ""
    return ASSETS.region_asset(
        f"thumbnail/gacha_item/{abn}.png",
        f"thumbnail/material/{abn}.png",
        f"thumbnail/common_material/{abn}.png",
    )


def build_gacha_detail() -> str:
    gacha = _started_gachas()[-1]  # most recent started pool
    pickup_order = _gacha_pickup_order(gacha)
    guaranteed_type = _gacha_guaranteed_type(gacha)
    rarity_counts, card_weight, card_rarity, rarity_weights = _gacha_card_weights(gacha)
    rarity_fraction, weight_info = _gacha_normal_rates(gacha, rarity_counts)
    if guaranteed_type:
        weight_info["guaranteed_rates"] = _gacha_guaranteed_rates(rarity_fraction, guaranteed_type)
    pickup_cards = _gacha_pickup_cards(
        pickup_order,
        card_rarity,
        rarity_weights,
        rarity_fraction,
        card_weight,
    )

    info = {
        "id": gacha["id"],
        "name": gacha.get("name", ""),
        "gacha_type": gacha.get("gachaType", ""),
        "summary": gacha.get("gachaInformation", {}).get("summary", ""),
        "desc": gacha.get("gachaInformation", {}).get("description", ""),
        "start_at": gacha.get("startAt", 0),
        "end_at": gacha.get("endAt", 0) + GACHA_END_PADDING_MS,
        "asset_name": gacha.get("assetbundleName", ""),
        "behaviors": _gacha_behaviors(gacha),
        "rarity_1_count": rarity_counts["rarity_1"],
        "rarity_2_count": rarity_counts["rarity_2"],
        "rarity_3_count": rarity_counts["rarity_3"],
        "rarity_4_count": rarity_counts["rarity_4"],
        "rarity_birthday_count": rarity_counts["rarity_birthday"],
        "pickup_count": len(pickup_order),
    }
    if ceil_item_path := _gacha_ceil_item_path(gacha):
        info["ceil_item_img_path"] = ceil_item_path

    body = {
        "gacha": info,
        "weight_info": weight_info,
        "pickup_cards": pickup_cards,
        "logo_img_path": ASSETS.region_asset(*_gacha_logo_rels(gacha)),
        "banner_img_path": ASSETS.region_asset(*_gacha_banner_rels(gacha)),
        "region": common.REGION,
        "dt": NOW_MS,
    }
    return _emit("gacha_detail", GachaDetailRequest, body)


def _gacha_behaviors(gacha: dict) -> list[dict]:
    """convertBehaviors (builder_detail.go:262-299)."""
    out = []
    for behavior in gacha.get("gachaBehaviors", []):
        item: dict = {
            "type": behavior.get("gachaBehaviorType", ""),
            "spin_count": behavior.get("spinCount", 0),
            "colorful_pass": str(behavior.get("gachaSpinnableType", "")).lower() == "colorful_pass",
        }
        cost_type = behavior.get("costResourceType", "")
        if cost_type:
            item["cost_type"] = cost_type
            lowered = cost_type.lower()
            if "jewel" in lowered:
                item["cost_icon_path"] = ASSETS.static("jewel.png")
            elif "ticket" in lowered:
                item["cost_icon_path"] = ASSETS.region_asset("thumbnail/gacha_ticket/gacha_ticket.png")
        if behavior.get("costResourceQuantity", 0) != 0:
            item["cost_quantity"] = behavior["costResourceQuantity"]
        if behavior.get("executeLimit") is not None:
            item["execute_limit"] = behavior["executeLimit"]
        out.append(item)
    return out


# ===========================================================================
# costume (render/costume/controller.go)
# ===========================================================================

_PART_ORDER = ("body", "head", "hair")
_PART_NAMES = {"body": "服装", "head": "饰品", "hair": "发型"}


def _costume_abn(costume: dict) -> str:
    """Raw abn (with `_assetbundleName` fallback, local_loader.go:255-259)."""
    return (costume.get("assetbundleName") or "").strip() or (costume.get("_assetbundleName") or "").strip()


def _costume_thumbnail_abn(costume: dict) -> str:
    """buildCostumeAssetBundleName (controller.go:926-951)."""
    override = _costume_abn(costume)
    if "_" in override:
        return override
    part = (costume.get("partType") or "").strip()
    if not part:
        return override
    base = override or f"{costume['id'] // 1000:04d}"
    name = f"cos{base}_{part}"
    if costume.get("colorId", 0) >= 2:
        name += f"_{costume['colorId'] - 1:02d}"
    return name


def _costume_thumbnail_path(costume: dict) -> str:
    abn = _costume_thumbnail_abn(costume)
    return ASSETS.region_asset(f"thumbnail/costume/{abn}.png") if abn else ""


def _costume_sort_key(costume: dict):
    published = costume.get("publishedAt", 0) or costume.get("archivePublishedAt", 0)
    return (-published, -costume.get("seq", 0), -costume["id"])


def _character_name(character_id: int) -> str:
    character = MD.character_by_id().get(character_id)
    if not character:
        return f"角色{character_id}"
    name = (character.get("firstName", "").strip() + character.get("givenName", "").strip()).strip()
    return name or character.get("givenName", "").strip() or f"角色{character_id}"


def _costume_optional_fields(costume: dict) -> dict:
    fields = {}
    for src_key, dst_key in (
        ("costume3dRarity", "rarity"),
        ("howToObtain", "how_to_obtain"),
        ("designer", "designer"),
        ("colorName", "color_name"),
    ):
        if costume.get(src_key):
            fields[dst_key] = costume[src_key]
    for src_key, dst_key in (
        ("colorId", "color_id"),
        ("publishedAt", "published_at"),
        ("archivePublishedAt", "archive_published_at"),
    ):
        if costume.get(src_key, 0):
            fields[dst_key] = costume[src_key]
    if asset_bundle_name := _costume_abn(costume):
        fields["asset_bundle_name"] = asset_bundle_name
    return fields


def _costume_variant_source_ids(variants: list[dict], source_cards: dict[int, list[int]]) -> list[int]:
    return sorted({card_id for variant in variants for card_id in source_cards.get(variant["id"], [])})


def _costume_variant_rows(variants: list[dict], source_cards: dict[int, list[int]]) -> list[dict]:
    return [
        {
            "costume_id": variant["id"],
            "color_id": variant.get("colorId", 0),
            "color_name": variant.get("colorName", ""),
            "asset_bundle_name": _costume_abn(variant),
            "thumbnail_path": _costume_thumbnail_path(variant),
            **({"source_card_ids": source_cards[variant["id"]]} if source_cards.get(variant["id"]) else {}),
        }
        for variant in variants
    ]


def _costume_basic(costume: dict, source_cards: dict[int, list[int]], variants: list[dict] | None = None) -> dict:
    character = MD.character_by_id().get(costume.get("characterId", 0))
    name = (costume.get("name") or "").strip() or _costume_abn(costume)
    basic: dict = {
        "costume_id": costume["id"],
        "costume_group_id": costume.get("costume3dGroupId", 0),
        "name": name,
        "part_type": costume.get("partType", ""),
        "part_name": _PART_NAMES.get((costume.get("partType") or "").strip(), costume.get("partType", "")),
        "costume_3d_type": costume.get("costume3dType", ""),
        "character_id": costume.get("characterId", 0),
        "character_name": _character_name(costume.get("characterId", 0)),
        "thumbnail_path": _costume_thumbnail_path(costume),
    }
    if character and character.get("gender"):
        basic["character_gender"] = character["gender"].strip()
    basic.update(_costume_optional_fields(costume))
    if source_cards.get(costume["id"]):
        basic["source_card_ids"] = source_cards[costume["id"]]
    if variants:
        union = _costume_variant_source_ids(variants, source_cards)
        if union:
            basic["source_card_ids"] = union
        else:
            basic.pop("source_card_ids", None)
        basic["variants"] = _costume_variant_rows(variants, source_cards)
    return basic


def _costume_source_cards() -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for link in MD.get("cardCostume3ds"):
        if link.get("costume3dId", 0) > 0 and link.get("cardId", 0) > 0:
            out.setdefault(link["costume3dId"], []).append(link["cardId"])
    for cid in out:
        out[cid].sort()
    return out


def _costumes_by_part(items: list[dict]) -> tuple[dict[str, list[dict]], list[str]]:
    groups: dict[str, list[dict]] = {}
    seen_order: list[str] = []
    for item in items:
        part = (item.get("partType") or "").strip()
        groups.setdefault(part, []).append(item)
        if part not in seen_order:
            seen_order.append(part)
    return groups, seen_order


def _ordered_costume_parts(groups: dict[str, list[dict]], seen_order: list[str]) -> list[str]:
    ordered = [part for part in _PART_ORDER if part in groups]
    ordered.extend(part for part in seen_order if part not in ordered)
    return ordered


def _take_balanced_costume_page(
    groups: dict[str, list[dict]],
    ordered: list[str],
    offsets: dict[str, int],
    page_size: int,
) -> list[dict]:
    current: list[dict] = []
    while len(current) < page_size:
        added = False
        for part in ordered:
            offset = offsets[part]
            if offset >= len(groups[part]):
                continue
            current.append(groups[part][offset])
            offsets[part] += 1
            added = True
            if len(current) >= page_size:
                break
        if not added:
            break
    return current


def _paginate_by_part(items: list[dict], page_size: int, page: int) -> list[dict]:
    """Balanced by-part pagination (controller.go:186-236)."""
    groups, seen_order = _costumes_by_part(items)
    ordered = _ordered_costume_parts(groups, seen_order)

    offsets = dict.fromkeys(groups, 0)
    current: list[dict] = []
    for _ in range(page):
        current = _take_balanced_costume_page(groups, ordered, offsets, page_size)
    return current


def build_costume_list() -> str:
    # "/服装列表 miku 每页20": character filter without part -> balanced pagination.
    character_token, character_id = "miku", 21
    page, page_size = 1, 20

    items = [c for c in MD.get("costume3ds") if c.get("characterId") == character_id and c.get("colorId") == 1]
    items.sort(key=_costume_sort_key)
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size))
    page = min(page, total_pages)
    page_items = _paginate_by_part(items, page_size, page)

    source_cards = _costume_source_cards()
    body = {
        "region": common.REGION,
        "title": f"{character_token} 查询结果",
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "filter_label": character_token,
        "costumes": [_costume_basic(c, source_cards) for c in page_items],
        "dt": NOW_MS,
    }
    return _emit("costume_list", CostumeListRequest, body)


def build_costume_detail() -> str:
    costume_id = 2006114  # "/查服装 2006114": recent body costume, 4 color variants
    costume = next(c for c in MD.get("costume3ds") if c["id"] == costume_id)
    variants = [
        c
        for c in MD.get("costume3ds")
        if c.get("costume3dGroupId") == costume["costume3dGroupId"]
        and c.get("partType") == costume["partType"]
        and c.get("characterId") == costume["characterId"]
    ]
    variants.sort(key=lambda v: (v.get("colorId", 0), v["id"]))

    source_cards = _costume_source_cards()
    character = MD.character_by_id().get(costume["characterId"], {})
    body = {
        "region": common.REGION,
        "costume": _costume_basic(costume, source_cards, variants=variants),
        "character_icon_path": ASSETS.chara_icon(costume["characterId"]),
        "dt": NOW_MS,
    }
    unit = (character.get("unit") or "").strip()
    if unit:
        body["unit_logo_path"] = ASSETS.static(f"logo_{unit}.png")
    return _emit("costume_detail", CostumeDetailRequest, body)


# ===========================================================================
# vlive (render/vlive/controller.go)
# ===========================================================================


def _unix_ms(value) -> int:
    """<1e12 is seconds (controller.go:412-421); 0 stays 0 (zero time)."""
    value = int(value or 0)
    if value <= 0:
        return 0
    return value * 1000 if value < 1_000_000_000_000 else value


def _material_icon(resource_type: str, resource_id: int) -> str:
    """Reward/material icon, region hard-coded jp (controller.go:367-391)."""
    kind = (resource_type or "").strip().lower()
    if kind == "paid_jewel":
        kind = "jewel"
    if kind in ("coin", "virtual_coin", "jewel"):
        return ASSETS.region_asset(f"thumbnail/common_material/{kind}.png", region="jp")
    if kind == "material" and resource_id > 0:
        return ASSETS.region_asset(f"thumbnail/material/material{resource_id}.png", region="jp")
    return ""


def _resource_boxes(purpose: str) -> dict[int, dict]:
    return {b["id"]: b for b in MD.get("resourceBoxes") if b.get("resourceBoxPurpose") == purpose}


def _vlive_reward_details(box: dict) -> list[dict]:
    items = []
    for detail in box.get("details", []):
        image_path = _material_icon(detail.get("resourceType", ""), detail.get("resourceId", 0))
        if not image_path.strip():
            continue
        quantity = detail.get("resourceQuantity", 0)
        items.append({"image_path": image_path, "quantity": quantity if quantity > 0 else 1})
    return items


def _vlive_rewards(live: dict, boxes: dict[int, dict]) -> list[dict]:
    for reward in live.get("virtualLiveRewards") or []:
        kind = str(reward.get("virtualLiveType", "")).strip().lower()
        if kind and kind != "normal":
            continue
        box = boxes.get(reward.get("resourceBoxId"))
        if not box:
            continue
        items = _vlive_reward_details(box)
        if items:
            return items
    return []


def _vlive_characters(live: dict, unit_by_id: dict[int, dict]) -> list[dict]:
    items, seen = [], set()
    for character in live.get("virtualLiveCharacters") or []:
        performance = str(character.get("virtualLivePerformanceType", "")).strip().lower()
        if performance not in ("", "main_only", "both"):
            continue
        unit = unit_by_id.get(character.get("gameCharacterUnitId"))
        if not unit or unit.get("gameCharacterId", 0) <= 0:
            continue
        icon = ASSETS.chara_icon(unit["gameCharacterId"])
        if icon in seen:
            continue
        seen.add(icon)
        items.append({"icon_path": icon})
    return items


def _vlive_events() -> dict[int, dict]:
    events = {}
    for event in MD.get("events"):
        if event.get("virtualLiveId"):
            events.setdefault(event["virtualLiveId"], event)
    return events


def _vlive_schedules(live: dict) -> list[tuple[int, int]]:
    windows = (
        (_unix_ms(schedule.get("startAt")), _unix_ms(schedule.get("endAt")))
        for schedule in live.get("virtualLiveSchedules") or []
    )
    return sorted(window for window in windows if window[0] and window[1] and window[0] < window[1])


def _current_vlive_window(
    schedules: list[tuple[int, int]],
    now: int,
) -> tuple[tuple[int, int] | None, bool, int]:
    current = next((window for window in schedules if now < window[1]), None)
    living = bool(current and now >= current[0])
    rest_count = sum(1 for window in schedules if now < window[0])
    return current, living, rest_count


def _resolve_vlive(live: dict, now: int) -> tuple[dict, int, int, tuple[int, int], bool, int] | None:
    start_at, end_at = _unix_ms(live.get("startAt")), _unix_ms(live.get("endAt"))
    if not start_at or not end_at:
        return None
    if now >= end_at or start_at - now >= MS_7_DAYS or end_at - start_at >= MS_30_DAYS:
        return None
    current, living, rest_count = _current_vlive_window(_vlive_schedules(live), now)
    if current is None:
        current = (start_at, end_at)
        living = now >= start_at
    return live, start_at, end_at, current, living, rest_count


def _vlive_banner_path(live: dict, event: dict | None) -> str:
    abn = (live.get("assetbundleName") or "").strip()
    if abn:
        return ASSETS.region_asset(f"virtual_live/select/banner/{abn}/{abn}.png")
    event_abn = ((event or {}).get("assetbundleName") or "").strip()
    if not event_abn:
        return ""
    return ASSETS.region_asset(
        f"home/banner/{event_abn}/{event_abn}.png",
        f"event/{event_abn}/banner.png",
        f"event_story/{event_abn}/screen_image/banner_event_story.png",
    )


def _vlive_brief(
    resolved: tuple[dict, int, int, tuple[int, int], bool, int],
    boxes: dict[int, dict],
    unit_by_id: dict[int, dict],
    event_by_vlive: dict[int, dict],
) -> dict:
    live, start_at, end_at, current, living, rest_count = resolved
    name = (live.get("name") or "").strip()
    brief: dict = {
        "id": live["id"],
        "name": name or f"Virtual Live #{live['id']}",
        "start_at": start_at,
        "end_at": end_at,
        "living": living,
        "rest_count": rest_count,
        "current_start_at": current[0],
        "current_end_at": current[1],
    }
    if banner_path := _vlive_banner_path(live, event_by_vlive.get(live["id"])):
        brief["banner_path"] = banner_path
    if rewards := _vlive_rewards(live, boxes):
        brief["rewards"] = rewards
    if characters := _vlive_characters(live, unit_by_id):
        brief["characters"] = characters
    return brief


def build_vlive_list() -> str:
    now = VLIVE_NOW_MS
    boxes = _resource_boxes("virtual_live_reward")
    unit_by_id = {u["id"]: u for u in MD.get("gameCharacterUnits")}
    event_by_vlive = _vlive_events()

    resolved = []
    for live in MD.get("virtualLives"):
        if entry := _resolve_vlive(live, now):
            resolved.append(entry)

    resolved.sort(key=lambda entry: (entry[1], entry[0]["id"]))
    lives = [_vlive_brief(entry, boxes, unit_by_id, event_by_vlive) for entry in resolved]

    body = {"region": common.REGION, "lives": lives, "timezone": common.TIMEZONE, "dt": now}
    return _emit("vlive_list", VLiveListRequest, body)


# ===========================================================================
# education — shared snapshot context helpers
# ===========================================================================

_UNIT_ORDER = ("light_sound", "idol", "street", "theme_park", "school_refusal", "piapro")
_ATTR_ORDER = ("cute", "cool", "pure", "happy", "mysterious")
_GATE_UNITS = {1: "light_sound", 2: "idol", 3: "street", 4: "theme_park", 5: "school_refusal"}
AREA_COIN_MATERIAL_ID = -1

_EX_MISSION_TYPES = {"play_live_ex", "waiting_room_ex"}

_MISSION_TITLES = {
    "play_live": "队长次数",
    "play_live_ex": "队长次数(EX)",
    "waiting_room": "休息室次数",
    "waiting_room_ex": "休息室次数(EX)",
    "collect_costume_3d": "服装",
    "collect_stamp": "表情",
    "read_area_talk": "区域对话",
    "read_card_episode_first": "卡面剧情前篇",
    "read_card_episode_second": "卡面剧情后篇",
    "collect_another_vocal": "Another Vocal",
    "area_item_level_up_character": "单人家具升级次数",
    "area_item_level_up_unit": "团家具升级次数",
    "area_item_level_up_reality_world": "属性道具（树&花）升级次数",
    "collect_member": "卡面",
    "skill_level_up_rare": "技能等级升级次数（★4&生日卡）",
    "skill_level_up_standard": "技能等级升级次数（★1~★3）",
    "master_rank_up_rare": "专精等级升级次数（★4&生日卡）",
    "master_rank_up_standard": "专精等级升级次数（★1~★3）",
    "collect_character_archive_voice": "台词",
    "collect_mysekai_fixture": "MySekai家具数量",
    "collect_mysekai_canvas": "MySekai画布数量",
    "read_mysekai_fixture_unique_character_talk": "MySekai对话",
}

_CHARACTER_CN_NAMES = {
    1: "星乃一歌", 2: "天马咲希", 3: "望月穗波", 4: "日野森志步", 5: "花里实乃理",
    6: "桐谷遥", 7: "桃井爱莉", 8: "日野森雫", 9: "小豆泽心羽", 10: "白石杏",
    11: "东云彰人", 12: "青柳冬弥", 13: "天马司", 14: "凤笑梦", 15: "草薙宁宁",
    16: "神代类", 17: "宵崎奏", 18: "朝比奈真冬", 19: "东云绘名", 20: "晓山瑞希",
    21: "初音未来", 22: "镜音铃", 23: "镜音连", 24: "巡音流歌", 25: "MEIKO", 26: "KAITO",
}  # fmt: skip


def _profile() -> dict:
    return common.build_user_info(is_hide_uid=True)


def _unit_icon(unit: str) -> str:
    return ASSETS.static(common.UNIT_ICONS[unit])


def _attr_icon(attr: str) -> str:
    return ASSETS.static(f"card/attr_icon_{attr}.png")


def _normalize_unit(unit: str) -> str:
    unit = (unit or "").strip().lower()
    return {
        "": "", "any": "",
        "light_sound_club": "light_sound",
        "more_more_jump": "idol",
        "vivid_bad_squad": "street",
        "wonderlands_x_showtime": "theme_park",
        "25_ji_night_cord_de": "school_refusal",
    }.get(unit, unit)  # fmt: skip


def _normalize_attr(attr: str) -> str:
    attr = (attr or "").strip().lower()
    return "" if attr in ("", "any") else attr


def _user_area_levels() -> dict[int, int]:
    """Per-item max level across areas (snapshot_helpers.go:201-214)."""
    levels: dict[int, int] = {}
    for area in SUITE.get("userAreas") or []:
        for item in area.get("areaItems") or []:
            if item.get("areaItemId", 0) > 0 and item.get("level", 0) > levels.get(item["areaItemId"], 0):
                levels[item["areaItemId"]] = item["level"]
    return levels


def _area_item_levels(item_id: int) -> list[dict]:
    return [level for level in MD.get("areaItemLevels") if level.get("areaItemId") == item_id]


_AREA_SHOP_BY_AREA = {5: 5, 7: 6, 8: 7, 9: 8, 10: 9, 11: 10, 13: 11}

_PIAPRO_CHARACTER_IDS = frozenset({21, 22, 23, 24, 25, 26})
_AREA_FILTER_UNIT_AREA_IDS = {"light_sound": 5, "idol": 7, "street": 8, "theme_park": 9, "school_refusal": 10}
_AREA_TREE_AREA_ID = 11
_AREA_FLOWER_AREA_ID = 13


def _area_level_filter_result(
    level: dict,
    filter_attr: str,
    filter_cid: int,
    filter_piapro: bool,
) -> tuple[bool, bool]:
    target_cid = level.get("targetGameCharacterId", 0)
    is_vs_item = _normalize_unit(level.get("targetUnit", "")) == "piapro" or target_cid in _PIAPRO_CHARACTER_IDS
    matched = filter_piapro and is_vs_item
    matched = matched or (filter_cid > 0 and target_cid == filter_cid)
    matched = matched or bool(filter_attr and _normalize_attr(level.get("targetCardAttr", "")) == filter_attr)
    return matched, is_vs_item


def _area_location_matches_filter(
    item: dict,
    filter_unit: str,
    filter_tree: bool,
    filter_flower: bool,
    is_vs_item: bool,
) -> bool:
    area_id = item.get("areaId")
    return (
        (filter_tree and area_id == _AREA_TREE_AREA_ID)
        or (filter_flower and area_id == _AREA_FLOWER_AREA_ID)
        or (bool(filter_unit) and _AREA_FILTER_UNIT_AREA_IDS.get(filter_unit) == area_id and not is_vs_item)
    )


def _area_item_matches_filter(
    item: dict,
    levels: list[dict],
    filter_unit: str,
    filter_attr: str,
    filter_cid: int,
    filter_tree: bool,
    filter_flower: bool,
    filter_piapro: bool,
) -> bool:
    """areaItemMatchesFilter (snapshot_helpers.go:21-80)."""
    results = [_area_level_filter_result(level, filter_attr, filter_cid, filter_piapro) for level in levels]
    matched = any(result[0] for result in results)
    is_vs_item = any(result[1] for result in results)
    return matched or _area_location_matches_filter(item, filter_unit, filter_tree, filter_flower, is_vs_item)


def _resolve_area_item_ids(
    filter_unit: str = "",
    filter_attr: str = "",
    filter_cid: int = 0,
    filter_tree: bool = False,
    filter_flower: bool = False,
) -> list[int]:
    """resolveAreaItemIDs, filtered branch (snapshot_area.go:258-293)."""
    filter_piapro = filter_unit == "piapro"
    if filter_piapro:
        filter_unit = ""
    matched = []
    for item in MD.get("areaItems"):
        if not item or item.get("id", 0) <= 0:
            continue
        levels = _area_item_levels(item["id"])
        if not levels:
            continue
        if _area_item_matches_filter(
            item, levels, filter_unit, filter_attr, filter_cid, filter_tree, filter_flower, filter_piapro
        ):
            matched.append(item["id"])
    return sorted(matched)


def _shop_items_by_resource_box() -> dict[int, dict]:
    shop_by_box: dict[int, dict] = {}
    for shop_item in MD.get("shopItems"):
        shop_by_box.setdefault(shop_item.get("resourceBoxId"), shop_item)
    return shop_by_box


def _shop_item_started(shop_item: dict, now_ms: int) -> bool:
    return shop_item.get("startAt", 0) <= 0 or shop_item["startAt"] <= now_ms


def _add_explicit_area_shop_details(
    result: dict[int, dict[int, dict]],
    box: dict,
    shop_item: dict,
    item_set: set[int],
) -> None:
    for detail in box.get("details", []):
        is_area_item = str(detail.get("resourceType", "")).lower() == "area_item"
        resource_id = detail.get("resourceId", 0)
        resource_level = detail.get("resourceLevel", 0)
        if not is_area_item or resource_id <= 0 or resource_level <= 0 or resource_id not in item_set:
            continue
        result.setdefault(resource_id, {}).setdefault(resource_level, shop_item)


def _explicit_area_shop_items(item_ids: list[int], now_ms: int) -> dict[int, dict[int, dict]]:
    item_set = set(item_ids)
    shop_by_box = _shop_items_by_resource_box()
    result: dict[int, dict[int, dict]] = {}

    for box in MD.get("resourceBoxes"):
        if box.get("resourceBoxPurpose") != "shop_item":
            continue
        shop_item = shop_by_box.get(box["id"])
        if not shop_item or not _shop_item_started(shop_item, now_ms):
            continue
        _add_explicit_area_shop_details(result, box, shop_item, item_set)
    return result


def _area_shop_targets(item_ids: list[int]) -> dict[int, list[tuple[int, list[int]]]]:
    area_by_id = {a["id"]: a for a in MD.get("areaItems")}
    targets_by_shop: dict[int, list[tuple[int, list[int]]]] = {}
    for item_id in item_ids:
        master = area_by_id.get(item_id)
        if not master:
            continue
        shop_id = _AREA_SHOP_BY_AREA.get(master.get("areaId", 0), 0)
        if shop_id <= 0:
            continue
        levels = sorted({level["level"] for level in _area_item_levels(item_id) if level.get("level", 0) > 0})
        if levels:
            targets_by_shop.setdefault(shop_id, []).append((item_id, levels))
    return targets_by_shop


def _started_shop_items_by_shop(now_ms: int) -> dict[int, list[dict]]:
    shop_items_by_shop: dict[int, list[dict]] = {}
    for shop_item in MD.get("shopItems"):
        if shop_item.get("shopId", 0) <= 0:
            continue
        if not _shop_item_started(shop_item, now_ms):
            continue
        shop_items_by_shop.setdefault(shop_item["shopId"], []).append(shop_item)
    return shop_items_by_shop


def _assign_shop_sequence_targets(
    result: dict[int, dict[int, dict]],
    targets_by_shop: dict[int, list[tuple[int, list[int]]]],
    shop_items_by_shop: dict[int, list[dict]],
) -> None:
    for shop_id, targets in targets_by_shop.items():
        shop_items = shop_items_by_shop.get(shop_id, [])
        if not shop_items or not targets:
            continue
        if len(shop_items) < len(targets) or len(shop_items) % len(targets) != 0:
            continue
        targets.sort(key=lambda t: t[0])
        shop_items.sort(key=lambda s: (s.get("seq", 0), s["id"]))
        block = len(shop_items) // len(targets)
        offset = 0
        for item_id, levels in targets:
            slots = result.setdefault(item_id, {})
            for idx in range(min(block, len(levels))):
                slots.setdefault(levels[idx], shop_items[offset + idx])
            offset += block


def _area_shop_items(item_ids: list[int], now_ms: int) -> dict[int, dict[int, dict]]:
    """resolveAreaItemShopItems + fillAreaItemShopItemsByShopSequence."""
    result = _explicit_area_shop_items(item_ids, now_ms)
    _assign_shop_sequence_targets(result, _area_shop_targets(item_ids), _started_shop_items_by_shop(now_ms))
    return result


def _released_caps(item_ids: list[int], shop_map: dict[int, dict[int, dict]]) -> dict[int, int]:
    """resolveReleasedAreaItemLevelCaps (snapshot_area.go:472-515)."""
    caps: dict[int, int] = {}
    if not item_ids or not shop_map:
        return caps
    for item_id in item_ids:
        shop_levels = shop_map.get(item_id) or {}
        if not shop_levels:
            continue
        level_set = {level["level"] for level in _area_item_levels(item_id) if level.get("level", 0) > 0}
        if 1 not in level_set:
            caps[item_id] = 0
            continue
        cap = 1
        level = 2
        while level in level_set and shop_levels.get(level) is not None:
            cap = level
            level += 1
        caps[item_id] = cap
    return caps


# ===========================================================================
# education endpoints
# ===========================================================================


def _challenge_scores_and_ranks() -> tuple[dict[int, int], dict[int, int]]:
    scores = {row["characterId"]: row.get("highScore", 0) for row in SUITE.get("userChallengeLiveSoloResults") or []}
    ranks: dict[int, int] = {}
    for stage in SUITE.get("userChallengeLiveSoloStages") or []:
        character_id = stage.get("characterId", 0)
        ranks[character_id] = max(ranks.get(character_id, 0), stage.get("rank", 0))
    return scores, ranks


def _challenge_rewards_by_character() -> dict[int, list[dict]]:
    rewards_by_char: dict[int, list[dict]] = {}
    for reward in MD.get("challengeLiveHighScoreRewards"):
        rewards_by_char.setdefault(reward["characterId"], []).append(reward)
    return rewards_by_char


def _challenge_reward_totals(
    rewards: list[dict],
    claimed: set[int],
    boxes: dict[int, dict],
) -> tuple[int, int]:
    jewel = shard = 0
    for reward in rewards:
        if reward["id"] in claimed:
            continue
        box = boxes.get(reward.get("resourceBoxId"))
        box_jewel, box_shard = _challenge_box_totals(box or {})
        jewel += box_jewel
        shard += box_shard
    return jewel, shard


def _challenge_box_totals(box: dict) -> tuple[int, int]:
    jewel = shard = 0
    for detail in box.get("details", []):
        kind = str(detail.get("resourceType", "")).lower()
        if kind == "jewel":
            jewel += detail.get("resourceQuantity", 0)
        elif kind == "material" and detail.get("resourceId") == 15:
            shard += detail.get("resourceQuantity", 0)
    return jewel, shard


def _challenge_rows(
    scores: dict[int, int],
    ranks: dict[int, int],
    rewards_by_char: dict[int, list[dict]],
    claimed: set[int],
    boxes: dict[int, dict],
) -> tuple[list[dict], int]:
    rows = []
    max_user_score = 0
    for character_id in range(1, 27):
        score = scores.get(character_id, 0)
        max_user_score = max(max_user_score, score)
        jewel, shard = _challenge_reward_totals(rewards_by_char.get(character_id, []), claimed, boxes)
        rows.append(
            {
                "chara_id": character_id,
                "rank": ranks.get(character_id, 0),
                "score": score,
                "jewel": jewel,
                "shard": shard,
                "chara_icon_path": ASSETS.chara_icon(character_id),
            }
        )
    return rows, max_user_score


def build_education_challenge_live() -> str:
    scores, ranks = _challenge_scores_and_ranks()
    claimed = {r["challengeLiveHighScoreRewardId"] for r in SUITE.get("userChallengeLiveSoloHighScoreRewards") or []}
    rewards_by_char = _challenge_rewards_by_character()
    boxes = _resource_boxes("challenge_live_high_score")
    challenges, max_user_score = _challenge_rows(scores, ranks, rewards_by_char, claimed, boxes)

    master_max = max((r.get("highScore", 0) for r in MD.get("challengeLiveHighScoreRewards")), default=0)
    max_score = max(max_user_score, master_max, 3_000_000)

    body = {
        "profile": _profile(),
        "character_challenges": challenges,
        "max_score": max_score,
        "jewel_icon_path": ASSETS.static("jewel.png"),
        "shard_icon_path": ASSETS.static("shard.png"),
        "dt": NOW_MS,
    }
    return _emit("education_challenge_live", ChallengeLiveDetailsRequest, body)


def _empty_power_bonuses() -> tuple[dict[int, dict], dict[str, dict], dict[str, dict]]:
    chara = {cid: {"area_item": 0.0, "rank": 0.0, "fixture": 0.0} for cid in range(1, 27)}
    unit = {unit_name: {"area_item": 0.0, "gate": 0.0} for unit_name in _UNIT_ORDER}
    attr = {attr_name: {"area_item": 0.0} for attr_name in _ATTR_ORDER}
    return chara, unit, attr


def _user_area_items() -> list[dict]:
    return [item for area in SUITE.get("userAreas") or [] for item in area.get("areaItems") or []]


def _area_power_level_row(
    item: dict,
    caps: dict[int, int],
    level_rows: dict[tuple[int, int], dict],
) -> dict | None:
    item_id = item.get("areaItemId", 0)
    cap = caps.get(item_id, 0)
    level_value = min(item.get("level", 0), cap) if cap > 0 else item.get("level", 0)
    return level_rows.get((item_id, level_value))


def _apply_area_power_row(
    row: dict,
    chara: dict[int, dict],
    unit: dict[str, dict],
    attr: dict[str, dict],
) -> None:
    bonus = row.get("power1BonusRate", 0.0)
    character_id = row.get("targetGameCharacterId", 0)
    if character_id > 0 and character_id in chara:
        chara[character_id]["area_item"] += bonus
    unit_key = _normalize_unit(row.get("targetUnit", ""))
    if unit_key and unit_key in unit:
        unit[unit_key]["area_item"] += bonus
    attr_key = _normalize_attr(row.get("targetCardAttr", ""))
    if attr_key and attr_key in attr:
        attr[attr_key]["area_item"] += bonus


def _apply_area_item_power_bonus(
    caps: dict[int, int],
    chara: dict[int, dict],
    unit: dict[str, dict],
    attr: dict[str, dict],
) -> None:
    level_rows = {(level["areaItemId"], level["level"]): level for level in MD.get("areaItemLevels")}
    for item in _user_area_items():
        if row := _area_power_level_row(item, caps, level_rows):
            _apply_area_power_row(row, chara, unit, attr)


def _apply_character_power_bonuses(chara: dict[int, dict]) -> None:
    rank_rows = {(r["characterId"], r["characterRank"]): r for r in MD.get("characterRanks")}
    for character in SUITE.get("userCharacters") or []:
        row = rank_rows.get((character.get("characterId"), character.get("characterRank")))
        if row and character.get("characterId") in chara:
            chara[character["characterId"]]["rank"] += row.get("power1BonusRate", 0.0)

    for fixture in SUITE.get("userMysekaiFixtureGameCharacterPerformanceBonuses") or []:
        if fixture.get("gameCharacterId") in chara:
            chara[fixture["gameCharacterId"]]["fixture"] += fixture.get("totalBonusRate", 0.0) * 0.1


def _apply_gate_power_bonuses(unit: dict[str, dict]) -> None:
    gate_rows = {(g["mysekaiGateId"], g["level"]): g for g in MD.get("mysekaiGateLevels")}
    max_gate_bonus = 0.0
    for gate in SUITE.get("userMysekaiGates") or []:
        row = gate_rows.get((gate.get("mysekaiGateId"), gate.get("mysekaiGateLevel")))
        if not row:
            continue
        rate = row.get("powerBonusRate", 0.0)
        unit_key = _GATE_UNITS.get(gate.get("mysekaiGateId", 0))
        if unit_key in unit:
            unit[unit_key]["gate"] += rate
        max_gate_bonus = max(max_gate_bonus, rate)
    unit["piapro"]["gate"] += max_gate_bonus


def _character_power_rows(chara: dict[int, dict]) -> list[dict]:
    return [
        {
            "chara_id": character_id,
            "chara_icon_path": ASSETS.chara_icon(character_id),
            **chara[character_id],
            "total": (chara[character_id]["area_item"] + chara[character_id]["rank"] + chara[character_id]["fixture"]),
        }
        for character_id in range(1, 27)
    ]


def _unit_power_rows(unit: dict[str, dict]) -> list[dict]:
    return [
        {
            "unit": unit_name,
            "unit_icon_path": _unit_icon(unit_name),
            **unit[unit_name],
            "total": unit[unit_name]["area_item"] + unit[unit_name]["gate"],
        }
        for unit_name in _UNIT_ORDER
    ]


def _attr_power_rows(attr: dict[str, dict]) -> list[dict]:
    return [
        {
            "attr": attr_name,
            "attr_icon_path": _attr_icon(attr_name),
            **attr[attr_name],
            "total": attr[attr_name]["area_item"],
        }
        for attr_name in _ATTR_ORDER
    ]


def build_education_power_bonus() -> str:
    user_levels = _user_area_levels()
    item_ids = sorted(user_levels)
    caps = _released_caps(item_ids, _area_shop_items(item_ids, NOW_MS))
    chara, unit, attr = _empty_power_bonuses()
    _apply_area_item_power_bonus(caps, chara, unit, attr)
    _apply_character_power_bonuses(chara)
    _apply_gate_power_bonuses(unit)

    body = {
        "profile": _profile(),
        "chara_bonuses": _character_power_rows(chara),
        "unit_bonuses": _unit_power_rows(unit),
        "attr_bonuses": _attr_power_rows(attr),
        "dt": NOW_MS,
    }
    return _emit("education_power_bonus", PowerBonusDetailRequest, body)


def _user_area_materials() -> dict[int, int]:
    materials = {AREA_COIN_MATERIAL_ID: SUITE.get("userGamedata", {}).get("coin", 0)}
    for item in SUITE.get("userMaterials") or []:
        if item.get("materialId", 0) > 0:
            materials[item["materialId"]] = item.get("quantity", 0)
    return materials


def _visible_area_item_states(
    item_ids: list[int],
    user_levels: dict[int, int],
    caps: dict[int, int],
) -> tuple[list[tuple[int, dict, list[dict], int, int]], int]:
    area_by_id = {item["id"]: item for item in MD.get("areaItems")}
    states: list[tuple[int, dict, list[dict], int, int]] = []
    for item_id in item_ids:
        master = area_by_id.get(item_id)
        levels = _area_item_levels(item_id)
        if not master or not levels:
            continue
        cap = caps.get(item_id, 0)
        current = min(user_levels.get(item_id, 0), cap) if cap > 0 else user_levels.get(item_id, 0)
        max_visible = max(current, cap)
        if max_visible > 0:
            states.append((item_id, master, levels, current, max_visible))
    min_current = max(min((state[3] for state in states), default=0), 0)
    return states, min_current


def _area_upgrade_material(
    cost_entry: dict,
    sum_materials: dict[int, int],
    materials: dict[int, int],
) -> dict:
    cost = cost_entry.get("cost", {})
    resource_type = cost.get("resourceType", "")
    resource_id = cost.get("resourceId", 0)
    material_id = AREA_COIN_MATERIAL_ID if str(resource_type).lower() == "coin" else resource_id
    quantity = cost.get("quantity", 0)
    sum_materials[material_id] = sum_materials.get(material_id, 0) + quantity
    have = materials.get(material_id, 0)
    return {
        "material_id": material_id,
        "material_icon_path": _material_icon(resource_type, resource_id),
        "quantity": quantity,
        "have_quantity": have,
        "sum_quantity": sum_materials[material_id],
        "is_enough": have >= sum_materials[material_id],
    }


def _area_item_level_info(
    level: int,
    current: int,
    row_master: dict | None,
    shop_item: dict | None,
    sum_materials: dict[int, int],
    materials: dict[int, int],
) -> dict:
    if not row_master:
        return {"level": level, "bonus": 0.0, "can_upgrade": False, "materials": []}
    row = {
        "level": level,
        "bonus": row_master.get("power1BonusRate", 0.0),
        "can_upgrade": True,
        "materials": [],
    }
    if level <= current:
        return row
    if not shop_item:
        row["can_upgrade"] = False
        return row
    row["materials"] = [
        _area_upgrade_material(cost_entry, sum_materials, materials) for cost_entry in shop_item.get("costs", [])
    ]
    row["can_upgrade"] = all(material["is_enough"] for material in row["materials"])
    return row


def _area_item_level_infos(
    levels: list[dict],
    shop_levels: dict[int, dict],
    current: int,
    min_current: int,
    max_visible: int,
    materials: dict[int, int],
) -> list[dict]:
    level_map = {level["level"]: level for level in levels}
    sum_materials: dict[int, int] = {}
    return [
        _area_item_level_info(
            level,
            current,
            level_map.get(level),
            shop_levels.get(level),
            sum_materials,
            materials,
        )
        for level in range(min_current + 1, max_visible + 1)
    ]


def _area_item_info(
    state: tuple[int, dict, list[dict], int, int],
    min_current: int,
    shop_map: dict[int, dict[int, dict]],
    materials: dict[int, int],
) -> dict:
    item_id, master, levels, current, max_visible = state
    asset_name = master.get("assetbundleName", "")
    info = {
        "item_id": item_id,
        "current_level": current,
        "item_icon_path": ASSETS.region_asset(f"areaitem/{asset_name}/{asset_name}.png"),
        "levels": _area_item_level_infos(
            levels,
            shop_map.get(item_id) or {},
            current,
            min_current,
            max_visible,
            materials,
        ),
    }
    if target := _area_item_target_icon(levels):
        info["target_icon_path"] = target
    return info


def build_education_area_item() -> str:
    """Snapshot mode with a unit filter (snapshot_area.go:55-256).

    Production always carries a filter: the bot command rejects a bare
    「/区域道具」 with a usage error (Cloud handler/education.go:199-214,
    buildEducationAreaQuery), so snapshot payloads only ever contain the
    filtered subset from resolveAreaItemIDs (snapshot_area.go:258-293) —
    never every item the user owns. Mirror the documented example query
    「/区域道具 mmj」 (unit idol → areaId 7, VS items excluded).
    """
    user_levels = _user_area_levels()
    materials = _user_area_materials()
    item_ids = _resolve_area_item_ids(filter_unit="idol")
    shop_map = _area_shop_items(item_ids, NOW_MS)
    caps = _released_caps(item_ids, shop_map)
    states, min_current = _visible_area_item_states(item_ids, user_levels, caps)
    area_items = [_area_item_info(state, min_current, shop_map, materials) for state in states]

    body = {"profile": _profile(), "area_items": area_items, "has_profile": True, "dt": NOW_MS}
    return _emit("education_area_item", AreaItemUpgradeMaterialsRequest, body)


def _area_item_target_icon(levels: list[dict]) -> str:
    for level in levels:
        if level.get("targetGameCharacterId", 0) > 0:
            return ASSETS.chara_icon(level["targetGameCharacterId"])
        if unit := _normalize_unit(level.get("targetUnit", "")):
            return _unit_icon(unit) if unit in common.UNIT_ICONS else ""
        if attr := _normalize_attr(level.get("targetCardAttr", "")):
            return _attr_icon(attr)
    return ""


def _bond_styles() -> dict[int, dict]:
    return {
        u["id"]: {"character_id": u.get("gameCharacterId", 0), "color_code": (u.get("colorCode") or "").strip()}
        for u in MD.get("gameCharacterUnits")
    }


def _bond_base_id(game_id: int, styles: dict[int, dict]) -> int:
    style = styles.get(game_id)
    return style["character_id"] if style and style["character_id"] > 0 else game_id


def _bond_color(game_id: int, styles: dict[int, dict]) -> list[int]:
    code = (styles.get(game_id) or {}).get("color_code", "").lstrip("#")
    if len(code) != 6:
        return [100, 100, 100]
    try:
        return [int(code[i : i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return [100, 100, 100]


def _bond_level_totals() -> tuple[dict[int, int], int]:
    level_total = {}
    max_level = 0
    for row in MD.get("levels"):
        if row.get("levelType", "").lower() == "bonds" and row.get("level", 0) > 0:
            level_total[row["level"]] = row.get("totalExp", 0)
            max_level = max(max_level, row["level"])
    return level_total, max_level


def _bond_info(
    entry: dict,
    pair: tuple[int, int],
    styles: dict[int, dict],
    char_ranks: dict[int, int],
    level_total: dict[int, int],
    max_level: int,
) -> dict:
    rank, exp = entry.get("rank", 0), entry.get("exp", 0)
    base_id1 = _bond_base_id(pair[0], styles)
    base_id2 = _bond_base_id(pair[1], styles)
    info = {
        "chara_id1": pair[0],
        "chara_id2": pair[1],
        "chara_icon_path1": ASSETS.chara_icon(base_id1),
        "chara_icon_path2": ASSETS.chara_icon(base_id2),
        "chara_rank1": char_ranks.get(base_id1, 0),
        "chara_rank2": char_ranks.get(base_id2, 0),
        "bond_level": rank,
        "has_bond": True,
        "color1": _bond_color(pair[0], styles),
        "color2": _bond_color(pair[1], styles),
    }
    if 0 < rank < max_level and rank in level_total and rank + 1 in level_total:
        info["need_exp"] = max(level_total[rank + 1] - level_total[rank] - exp, 0)
    return info


def _bond_rows(
    group_pairs: dict[int, tuple[int, int]],
    styles: dict[int, dict],
    char_ranks: dict[int, int],
    level_total: dict[int, int],
    max_level: int,
) -> tuple[list[dict], int]:
    bonds = []
    user_max_rank = 0
    for entry in SUITE.get("userBonds") or []:
        pair = group_pairs.get(entry.get("bondsGroupId"))
        if not pair:
            continue
        rank = entry.get("rank", 0)
        user_max_rank = max(user_max_rank, rank)
        bonds.append(_bond_info(entry, pair, styles, char_ranks, level_total, max_level))
    return bonds, user_max_rank


def build_education_bonds() -> str:
    """User-bond view, cid<=0 (snapshot_bonds.go:10-244)."""
    group_pairs = {
        b["groupId"]: (b["characterId1"], b["characterId2"]) for b in MD.get("bonds") if b.get("groupId", 0) > 0
    }
    styles = _bond_styles()
    char_ranks = {c["characterId"]: c.get("characterRank", 0) for c in SUITE.get("userCharacters") or []}
    level_total, max_level = _bond_level_totals()
    bonds, user_max_rank = _bond_rows(group_pairs, styles, char_ranks, level_total, max_level)

    if max_level == 0:
        max_level = user_max_rank
    bonds.sort(key=lambda b: (-b["bond_level"], b["chara_id1"], b["chara_id2"]))
    bonds = bonds[:20]

    body = {"profile": _profile(), "bonds": bonds, "max_level": max_level, "dt": NOW_MS}
    return _emit("education_bonds", BondsRequest, body)


def _mission_statuses() -> list[dict]:
    """Standard status array (suite has no compact/legacy variants)."""
    return SUITE.get("userCharacterMissionV2Statuses") or []


def _param_groups(group_id: int) -> list[dict]:
    """Parameter-group rows keyed by masterdata ``id``, seq asc (DB semantics)."""
    rows = [g for g in MD.get("characterMissionV2ParameterGroups") if g.get("id") == group_id]
    rows.sort(key=lambda g: g.get("seq", 0))
    return rows


def _step_value(groups: list[dict], seq: int, key: str) -> int:
    """Last row with row.seq <= seq (stair-step lookup); 0 when seq <= 0."""
    if seq <= 0:
        return 0
    value = 0
    for row in groups:
        if row.get("seq", 0) > seq:
            break
        value = row.get(key, 0)
    return value


def _leader_mission_progress() -> tuple[dict[int, int], dict[int, int], dict[int, bool], bool]:
    play_count: dict[int, int] = {}
    ex_count: dict[int, int] = {}
    has_play_live_ex: dict[int, bool] = {}
    has_play_live = False
    for item in SUITE.get("userCharacterMissionV2s") or []:
        cid = item.get("characterId", 0)
        if cid <= 0:
            continue
        kind = str(item.get("characterMissionType", "")).strip().lower()
        if kind == "play_live":
            play_count[cid] = item.get("progress", 0)
            has_play_live = True
        elif kind == "play_live_ex":
            ex_count[cid] = item.get("progress", 0)
            has_play_live_ex[cid] = True
    return play_count, ex_count, has_play_live_ex, has_play_live


def _fallback_leader_play_counts(play_count: dict[int, int], has_play_live: bool) -> None:
    if has_play_live:
        return
    for item in SUITE.get("userCharacterLiveUsageCounts") or []:
        is_leader = str(item.get("characterLiveUsageType", "")).lower() == "leader"
        if item.get("characterId", 0) > 0 and is_leader:
            play_count[item["characterId"]] = item.get("usageCount", 0)


def _leader_ex_statuses(
    ex_count: dict[int, int],
    has_play_live_ex: dict[int, bool],
) -> dict[int, int]:
    ex_level: dict[int, int] = {}
    requirements = _param_groups(101)
    for status in _mission_statuses():
        cid = status.get("characterId", 0)
        if cid <= 0 or status.get("parameterGroupId") != 101:
            continue
        ex_level[cid] = max(ex_level.get(cid, 0), status.get("seq", 0))
        ex_count[cid] = ex_count.get(cid, 0) + _step_value(requirements, status.get("seq", 0), "requirement")

    for cid in range(1, 27):
        if has_play_live_ex.get(cid):
            ex_level[cid] = ex_level.get(cid, 0) + 1
    return ex_level


def _leader_rows(play_count: dict[int, int], ex_count: dict[int, int], ex_level: dict[int, int]) -> list[dict]:
    rows = [
        {
            "chara_id": cid,
            "chara_icon_path": ASSETS.chara_icon(cid),
            "play_count": play_count.get(cid, 0),
            "ex_level": ex_level.get(cid, 0),
            "ex_count": ex_count.get(cid, 0),
        }
        for cid in range(1, 27)
    ]
    rows.sort(key=lambda row: (-(row["play_count"] + row["ex_count"]), row["chara_id"]))
    return rows


def _leader_max_play(leaders: list[dict]) -> int:
    max_play = max((g.get("requirement", 0) for g in _param_groups(1)), default=0)
    return max_play if max_play > 0 else max((row["play_count"] for row in leaders), default=0)


def build_education_leader_count() -> str:
    play_count, ex_count, has_play_live_ex, has_play_live = _leader_mission_progress()
    _fallback_leader_play_counts(play_count, has_play_live)
    ex_level = _leader_ex_statuses(ex_count, has_play_live_ex)
    leaders = _leader_rows(play_count, ex_count, ex_level)
    max_play = _leader_max_play(leaders)

    body = {"profile": _profile(), "leader_counts": leaders, "max_play_count": max_play, "dt": NOW_MS}
    return _emit("education_leader_count", LeaderCountRequest, body)


# ---------------------------------------------------------------------------
# character missions (snapshot_character_missions.go)
# ---------------------------------------------------------------------------

MISSION_CID = 6  # 桐谷遥: has play_live_ex progress mid-round -> exercises EX arithmetic


def _current_round(groups: list[dict], total: int) -> tuple[int, int, int]:
    total = max(total, 0)
    round_no = 1
    while True:
        requirement = _step_value(groups, round_no, "requirement")
        if requirement <= 0 or total < requirement:
            return round_no, total, requirement
        total -= requirement
        round_no += 1


def _character_missions(cid: int) -> list[dict]:
    return sorted(
        (mission for mission in MD.get("characterMissionV2s") if mission.get("characterId") == cid),
        key=lambda mission: mission["id"],
    )


def _character_level_totals() -> tuple[list[tuple[int, int]], dict[int, int]]:
    rows = sorted(
        (
            (row["level"], row.get("totalExp", 0))
            for row in MD.get("levels")
            if row.get("levelType", "").lower() == "character" and row.get("level", 0) > 0
        ),
    )
    return rows, dict(rows)


def _current_character_level(cid: int, level_total: dict[int, int]) -> tuple[int, int, int]:
    user_char = next(
        (character for character in SUITE.get("userCharacters") or [] if character.get("characterId") == cid), None
    )
    current_level = (user_char or {}).get("characterRank", 0)
    current_exp = (user_char or {}).get("exp", 0)
    current_total_exp = (user_char or {}).get("totalExp", 0)
    base = level_total.get(current_level)
    if current_level > 0 and current_total_exp > 0 and base is not None and current_total_exp >= base:
        current_exp = current_total_exp - base
    return current_level, current_exp, current_total_exp


def _character_mission_statuses(cid: int) -> list[dict]:
    return [status for status in _mission_statuses() if status.get("characterId") == cid]


def _pending_mission_exp(statuses: list[dict]) -> int:
    return sum(
        _step_value(_param_groups(status.get("parameterGroupId", 0)), status.get("seq", 0), "exp")
        for status in statuses
        if str(status.get("missionStatus", "")).strip().lower() == "achieved"
    )


def _level_for_total_exp(char_levels: list[tuple[int, int]], total_exp: int) -> tuple[int, int]:
    final_level, level_start = 1, 0
    for level, level_total_exp in char_levels:
        if level_total_exp > total_exp:
            break
        final_level, level_start = level, level_total_exp
    return final_level, total_exp - level_start


def _final_character_level(
    current_level: int,
    current_exp: int,
    current_total_exp: int,
    pending_exp: int,
    char_levels: list[tuple[int, int]],
    level_total: dict[int, int],
) -> tuple[int, int]:
    if not char_levels:
        return current_level, current_exp + pending_exp
    base_total_exp = current_total_exp
    if base_total_exp <= 0 and current_level > 0 and current_level in level_total:
        base_total_exp = level_total[current_level] + current_exp
    return _level_for_total_exp(char_levels, max(base_total_exp, 0) + pending_exp)


def _mission_progress_by_type(cid: int) -> dict[str, int]:
    progress_by_type: dict[str, int] = {}
    for item in SUITE.get("userCharacterMissionV2s") or []:
        if item.get("characterId") != cid:
            continue
        mission_type = item.get("characterMissionType", "")
        progress_by_type[mission_type] = max(progress_by_type.get(mission_type, 0), item.get("progress", 0))
    return progress_by_type


def _mission_sequences(statuses: list[dict]) -> tuple[dict[int, int], dict[int, int]]:
    seq_by_mission: dict[int, int] = {}
    seq_by_group: dict[int, int] = {}
    for status in statuses:
        mission_id = status.get("missionId", 0)
        group_id = status.get("parameterGroupId", 0)
        seq = status.get("seq", 0)
        seq_by_mission[mission_id] = max(seq_by_mission.get(mission_id, 0), seq)
        seq_by_group[group_id] = max(seq_by_group.get(group_id, 0), seq)
    return seq_by_mission, seq_by_group


def _mission_received_seq(mission: dict, seq_by_mission: dict[int, int], seq_by_group: dict[int, int]) -> int:
    return max(
        seq_by_mission.get(mission["id"], 0),
        seq_by_group.get(mission.get("parameterGroupId", 0), 0),
    )


def _mission_current(groups: list[dict], current: int, received: int, is_ex: bool) -> int:
    if is_ex:
        cleared = sum(_step_value(groups, round_no, "requirement") for round_no in range(1, received + 1))
        if 0 < current < cleared:
            return cleared + current
        return current if current > 0 else cleared
    if current <= 0 and received > 0:
        return _step_value(groups, received, "requirement")
    return current


def _mission_upper(groups: list[dict], is_ex: bool) -> int:
    if is_ex:
        return sum(_step_value(groups, round_no, "requirement") for round_no in range(1, 31))
    return max((group.get("requirement", 0) for group in groups), default=0)


def _mission_ratio(current: int, upper: int) -> float:
    if upper <= 0:
        return 0.0
    return 1.0 if current > upper else current / upper


def _add_ex_mission_progress(row: dict, groups: list[dict], current: int) -> None:
    round_no, in_round, round_need = _current_round(groups, current)
    if round_need > 0:
        next_need = current + max(round_need - in_round, 0)
        next_exp = _step_value(groups, round_no, "exp")
        if next_need > 0:
            row["next_need"] = next_need
        if next_exp > 0:
            row["next_exp"] = next_exp
    if round_no > 0:
        row["current_round"] = round_no
    if in_round > 0:
        row["current_round_progress"] = in_round
    if round_need > 0:
        row["current_round_need"] = round_need
    row["ex_display_round_text"] = f"EX {round_no} 回目"


def _add_standard_mission_progress(row: dict, groups: list[dict], current: int) -> None:
    next_group = next((group for group in groups if group.get("requirement", 0) > current), None)
    if not next_group:
        return
    if next_group.get("requirement", 0) > 0:
        row["next_need"] = next_group["requirement"]
    if next_group.get("exp", 0) > 0:
        row["next_exp"] = next_group["exp"]


def _mission_row(
    mission: dict,
    progress_by_type: dict[str, int],
    seq_by_mission: dict[int, int],
    seq_by_group: dict[int, int],
) -> dict:
    groups = _param_groups(mission.get("parameterGroupId", 0))
    mission_type = mission.get("characterMissionType", "")
    is_ex = mission_type in _EX_MISSION_TYPES
    received = _mission_received_seq(mission, seq_by_mission, seq_by_group)
    current = _mission_current(groups, progress_by_type.get(mission_type, 0), received, is_ex)
    upper = _mission_upper(groups, is_ex)
    row = {
        "mission_id": mission["id"],
        "mission_type": mission_type,
        "title": _MISSION_TITLES.get(mission_type, mission_type),
        "is_achievement": mission.get("isAchievementMission", False),
        "is_ex": is_ex,
        "current": current,
        "ratio": _mission_ratio(current, upper),
    }
    if upper > 0:
        row["upper"] = upper
    if is_ex:
        _add_ex_mission_progress(row, groups, current)
    else:
        _add_standard_mission_progress(row, groups, current)
    return row


def _mission_rows(cid: int) -> tuple[list[dict], int, int, int, int, int]:
    missions = _character_missions(cid)
    char_levels, level_total = _character_level_totals()
    current_level, current_exp, current_total_exp = _current_character_level(cid, level_total)
    statuses = _character_mission_statuses(cid)
    pending_exp = _pending_mission_exp(statuses)
    final_level, final_exp = _final_character_level(
        current_level,
        current_exp,
        current_total_exp,
        pending_exp,
        char_levels,
        level_total,
    )
    progress_by_type = _mission_progress_by_type(cid)
    seq_by_mission, seq_by_group = _mission_sequences(statuses)
    rows = [_mission_row(mission, progress_by_type, seq_by_mission, seq_by_group) for mission in missions]
    return rows, current_level, current_exp, pending_exp, final_level, final_exp


_BASIC_ROW_ORDER = (
    "collect_member",
    "collect_stamp",
    "collect_costume_3d",
    "collect_character_archive_voice",
    "collect_another_vocal",
    "read_mysekai_fixture_unique_character_talk",
    "read_area_talk",
)
_ACHIEVEMENT_ROW_ORDER = (
    "play_live",
    "play_live_ex",
    "waiting_room",
    "waiting_room_ex",
    "read_card_episode_first",
    "read_card_episode_second",
    "area_item_level_up_character",
    "area_item_level_up_unit",
    "area_item_level_up_reality_world",
    "skill_level_up_rare",
    "skill_level_up_standard",
    "master_rank_up_rare",
    "master_rank_up_standard",
    "collect_mysekai_fixture",
    "collect_mysekai_canvas",
)


def build_education_character_mission_overview() -> str:
    cid = MISSION_CID
    rows, level, exp, pending, final_level, final_exp = _mission_rows(cid)
    by_type = {row["mission_type"]: row for row in rows}
    body = {
        "profile": _profile(),
        "character_id": cid,
        "character_name": _CHARACTER_CN_NAMES.get(cid, f"角色{cid}"),
        "character_icon_path": ASSETS.chara_icon(cid),
        "current_level": level,
        "current_exp": exp,
        "pending_exp": pending,
        "final_level": final_level,
        "final_exp": final_exp,
        "basic_rows": [copy.deepcopy(by_type[t]) for t in _BASIC_ROW_ORDER if t in by_type],
        "achievement_rows": [copy.deepcopy(by_type[t]) for t in _ACHIEVEMENT_ROW_ORDER if t in by_type],
        "dt": NOW_MS,
    }
    return _emit("education_character_mission_overview", CharacterMissionOverviewRequest, body)


def _mission_section_types(mission_type: str) -> list[str]:
    return {
        "play_live": ["play_live", "play_live_ex"],
        "waiting_room": ["waiting_room", "waiting_room_ex"],
    }.get(mission_type, [mission_type])


def _mission_master(cid: int, mission_type: str) -> dict:
    return next(
        mission
        for mission in MD.get("characterMissionV2s")
        if mission.get("characterId") == cid and mission.get("characterMissionType") == mission_type
    )


def _ex_mission_display_rows(base: dict, groups: list[dict]) -> list[dict]:
    max_round = max(base.get("current_round", 0), max((group.get("seq", 0) for group in groups), default=0))
    display_rows = []
    acc_requirement = acc_exp = 0
    for round_no in range(1, max_round + 1):
        requirement = _step_value(groups, round_no, "requirement")
        exp = _step_value(groups, round_no, "exp")
        acc_requirement += requirement
        acc_exp += exp
        display_rows.append(
            {
                "seq": round_no,
                "requirement": requirement,
                "acc_requirement": acc_requirement,
                "exp": exp,
                "acc_exp": acc_exp,
            }
        )
    return display_rows


def _standard_mission_display_rows(groups: list[dict]) -> list[dict]:
    display_rows = []
    acc_exp = 0
    for group in groups:
        acc_exp += group.get("exp", 0)
        display_rows.append(
            {
                "seq": group.get("seq", 0),
                "requirement": group.get("requirement", 0),
                # Go keeps acc_requirement == requirement for non-EX rows (spec caveat).
                "acc_requirement": group.get("requirement", 0),
                "exp": group.get("exp", 0),
                "acc_exp": acc_exp,
            }
        )
    return display_rows


def _mission_display_rows(base: dict, groups: list[dict]) -> list[dict]:
    return _ex_mission_display_rows(base, groups) if base["is_ex"] else _standard_mission_display_rows(groups)


def _mission_reached_seq(base: dict, display_rows: list[dict]) -> int:
    if base["is_ex"] and base.get("current_round", 0) > 0:
        return base["current_round"]
    reached_seq = 0
    for row in display_rows:
        if row["requirement"] > base["current"]:
            break
        reached_seq = row["seq"]
    return reached_seq


_MISSION_SECTION_FIELDS = (
    ("current_round", "current_round_no"),
    ("current_round_progress", "current_round_progress"),
    ("current_round_need", "current_round_need"),
    ("upper", "upper"),
    ("next_need", "next_need"),
    ("next_exp", "next_exp"),
)


def _copy_mission_section_fields(base: dict, section: dict) -> None:
    for src_key, dst_key in _MISSION_SECTION_FIELDS:
        if src_key in base:
            section[dst_key] = base[src_key]


def _mission_section(cid: int, base: dict) -> dict:
    mission = _mission_master(cid, base["mission_type"])
    groups = _param_groups(mission.get("parameterGroupId", 0))
    display_rows = _mission_display_rows(base, groups)
    section = {
        "mission_type": base["mission_type"],
        "title": base["title"],
        "is_ex": base["is_ex"],
        "current_total": base["current"],
        "reached_seq": _mission_reached_seq(base, display_rows),
        "ratio": base["ratio"],
        "display_rows": display_rows,
    }
    _copy_mission_section_fields(base, section)
    return section


def build_education_character_mission_all() -> str:
    cid, mission_type = MISSION_CID, "play_live"
    rows, *_ = _mission_rows(cid)
    by_type = {row["mission_type"]: row for row in rows}
    sections = [_mission_section(cid, by_type[section_type]) for section_type in _mission_section_types(mission_type)]

    body = {
        "profile": _profile(),
        "character_id": cid,
        "character_name": _CHARACTER_CN_NAMES.get(cid, f"角色{cid}"),
        "character_icon_path": ASSETS.chara_icon(cid),
        "title": _MISSION_TITLES.get(mission_type, mission_type),
        "sections": sections,
        "dt": NOW_MS,
    }
    return _emit("education_character_mission_all", CharacterMissionAllRequest, body)


# ===========================================================================


def generate() -> list[str]:
    return [
        build_gacha_list(),
        build_gacha_detail(),
        build_costume_list(),
        build_costume_detail(),
        build_vlive_list(),
        build_education_challenge_live(),
        build_education_power_bonus(),
        build_education_area_item(),
        build_education_bonds(),
        build_education_leader_count(),
        build_education_character_mission_overview(),
        build_education_character_mission_all(),
    ]


if __name__ == "__main__":
    names = generate()
    print(json.dumps(names, indent=1))  # noqa: T201
    common.ASSETS.save_manifest()
    print(f"missing assets: {len(common.ASSETS.missing)}")  # noqa: T201
