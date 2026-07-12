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


def build_gacha_detail() -> str:
    gacha = _started_gachas()[-1]  # most recent started pool

    pickup_order: list[int] = []
    for pickup in gacha.get("gachaPickups", []):
        if pickup["cardId"] not in pickup_order:
            pickup_order.append(pickup["cardId"])

    guaranteed_type = ""
    for behavior in gacha.get("gachaBehaviors", []):
        kind = str(behavior.get("gachaBehaviorType", "")).lower()
        if kind == "over_rarity_4_once":
            guaranteed_type = "rarity_4"
        elif kind == "over_rarity_3_once" and guaranteed_type != "rarity_4":
            guaranteed_type = "rarity_3"

    rarity_counts = dict.fromkeys(("rarity_1", "rarity_2", "rarity_3", "rarity_4", "rarity_birthday"), 0)
    card_weight: dict[int, float] = {}
    card_rarity: dict[int, str] = {}
    rarity_weights: dict[str, float] = {}
    for detail in gacha.get("gachaDetails", []):
        card = MD.card_by_id().get(detail["cardId"])
        if not card:
            continue
        rarity = card["cardRarityType"].lower()
        card_rarity[card["id"]] = rarity
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
        card_weight[card["id"]] = card_weight.get(card["id"], 0.0) + detail.get("weight", 0)
        rarity_weights[rarity] = rarity_weights.get(rarity, 0.0) + detail.get("weight", 0)

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

    if guaranteed_type:
        guaranteed = dict.fromkeys(("rarity_1", "rarity_2", "rarity_3", "rarity_4", "rarity_birthday"), 0.0)
        guaranteed.update(rarity_fraction)
        guaranteed[guaranteed_type] += guaranteed["rarity_2"]
        guaranteed["rarity_2"] = 0.0
        if guaranteed_type == "rarity_4":
            guaranteed[guaranteed_type] += guaranteed["rarity_3"]
            guaranteed["rarity_3"] = 0.0
        weight_info["guaranteed_rates"] = guaranteed

    def card_rate(card_id: int) -> float:
        rarity = card_rarity.get(card_id, "")
        total = rarity_weights.get(rarity, 0.0)
        base = rarity_fraction.get(rarity, 0.0)
        if not rarity or total <= 0 or base == 0:
            return 0.0
        return (card_weight.get(card_id, 0.0) / total) * base

    pickup_cards = []
    for card_id in pickup_order:
        card = MD.card_by_id().get(card_id)
        if not card:
            continue
        card_rarity.setdefault(card_id, card["cardRarityType"].lower())
        pickup_cards.append(
            {
                "id": card["id"],
                "rarity": card["cardRarityType"],
                "rate": card_rate(card["id"]),
                "thumbnail_request": common.card_thumbnail(card, thumb_after=False),
            }
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
    ceil_item_id = gacha.get("gachaCeilItemId") or 0
    if ceil_item_id:
        ceil_item = next((c for c in MD.get("gachaCeilItems") if c["id"] == ceil_item_id), None)
        abn = (ceil_item or {}).get("assetbundleName", "").strip()
        if abn:
            info["ceil_item_img_path"] = ASSETS.region_asset(
                f"thumbnail/gacha_item/{abn}.png",
                f"thumbnail/material/{abn}.png",
                f"thumbnail/common_material/{abn}.png",
            )

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
    for src_key, dst_key in (
        ("costume3dRarity", "rarity"),
        ("howToObtain", "how_to_obtain"),
        ("designer", "designer"),
        ("colorName", "color_name"),
    ):
        if costume.get(src_key):
            basic[dst_key] = costume[src_key]
    if _costume_abn(costume):
        basic["asset_bundle_name"] = _costume_abn(costume)
    if costume.get("colorId", 0):
        basic["color_id"] = costume["colorId"]
    if costume.get("publishedAt", 0):
        basic["published_at"] = costume["publishedAt"]
    if costume.get("archivePublishedAt", 0):
        basic["archive_published_at"] = costume["archivePublishedAt"]
    if source_cards.get(costume["id"]):
        basic["source_card_ids"] = source_cards[costume["id"]]
    if variants:
        union = sorted({cid for v in variants for cid in source_cards.get(v["id"], [])})
        if union:
            basic["source_card_ids"] = union
        elif "source_card_ids" in basic:
            del basic["source_card_ids"]
        basic["variants"] = [
            {
                "costume_id": v["id"],
                "color_id": v.get("colorId", 0),
                "color_name": v.get("colorName", ""),
                "asset_bundle_name": _costume_abn(v),
                "thumbnail_path": _costume_thumbnail_path(v),
                **({"source_card_ids": source_cards[v["id"]]} if source_cards.get(v["id"]) else {}),
            }
            for v in variants
        ]
    return basic


def _costume_source_cards() -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for link in MD.get("cardCostume3ds"):
        if link.get("costume3dId", 0) > 0 and link.get("cardId", 0) > 0:
            out.setdefault(link["costume3dId"], []).append(link["cardId"])
    for cid in out:
        out[cid].sort()
    return out


def _paginate_by_part(items: list[dict], page_size: int, page: int) -> list[dict]:
    """Balanced by-part pagination (controller.go:186-236)."""
    groups: dict[str, list[dict]] = {}
    seen_order: list[str] = []
    for item in items:
        part = (item.get("partType") or "").strip()
        groups.setdefault(part, []).append(item)
        if part not in seen_order:
            seen_order.append(part)
    ordered = [p for p in _PART_ORDER if p in groups]
    ordered += [p for p in seen_order if p not in ordered]

    offsets = dict.fromkeys(groups, 0)
    current: list[dict] = []
    for _ in range(page):
        current = []
        while len(current) < page_size:
            added = False
            for part in ordered:
                group = groups[part]
                if offsets[part] >= len(group):
                    continue
                current.append(group[offsets[part]])
                offsets[part] += 1
                added = True
                if len(current) >= page_size:
                    break
            if not added:
                break
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


def _vlive_rewards(live: dict, boxes: dict[int, dict]) -> list[dict]:
    items: list[dict] = []
    for reward in live.get("virtualLiveRewards") or []:
        kind = str(reward.get("virtualLiveType", "")).strip().lower()
        if kind and kind != "normal":
            continue
        box = boxes.get(reward.get("resourceBoxId"))
        if not box:
            continue
        for detail in box.get("details", []):
            image_path = _material_icon(detail.get("resourceType", ""), detail.get("resourceId", 0))
            if not image_path.strip():
                continue
            quantity = detail.get("resourceQuantity", 0)
            items.append({"image_path": image_path, "quantity": quantity if quantity > 0 else 1})
        if items:
            break
    return items


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


def build_vlive_list() -> str:
    now = VLIVE_NOW_MS
    boxes = _resource_boxes("virtual_live_reward")
    unit_by_id = {u["id"]: u for u in MD.get("gameCharacterUnits")}
    event_by_vlive = {}
    for event in MD.get("events"):
        if event.get("virtualLiveId"):
            event_by_vlive.setdefault(event["virtualLiveId"], event)

    resolved = []
    for live in MD.get("virtualLives"):
        start_at, end_at = _unix_ms(live.get("startAt")), _unix_ms(live.get("endAt"))
        if not start_at or not end_at:
            continue
        if now >= end_at or start_at - now >= MS_7_DAYS or end_at - start_at >= MS_30_DAYS:
            continue

        windows = (
            (_unix_ms(s.get("startAt")), _unix_ms(s.get("endAt"))) for s in live.get("virtualLiveSchedules") or []
        )
        schedules = sorted(w for w in windows if w[0] and w[1] and w[0] < w[1])
        current, living = None, False
        for window in schedules:
            if now < window[1]:
                current = window
                living = now >= window[0]
                break
        rest_count = sum(1 for window in schedules if now < window[0])
        if current is None:
            current = (start_at, end_at)
            living = now >= start_at  # now < end_at is guaranteed by the filter
        resolved.append((live, start_at, end_at, current, living, rest_count))

    resolved.sort(key=lambda entry: (entry[1], entry[0]["id"]))

    lives = []
    for live, start_at, end_at, current, living, rest_count in resolved:
        name = (live.get("name") or "").strip()
        brief: dict = {
            "id": live["id"],
            "name": name or f"Virtual Live #{live['id']}",
            "start_at": start_at,
            "end_at": end_at,
            "living": living,
            "rest_count": rest_count,
        }
        if current is not None:
            brief["current_start_at"], brief["current_end_at"] = current
        abn = (live.get("assetbundleName") or "").strip()
        if abn:
            brief["banner_path"] = ASSETS.region_asset(f"virtual_live/select/banner/{abn}/{abn}.png")
        else:
            event = event_by_vlive.get(live["id"])
            event_abn = ((event or {}).get("assetbundleName") or "").strip()
            if event_abn:
                brief["banner_path"] = ASSETS.region_asset(
                    f"home/banner/{event_abn}/{event_abn}.png",
                    f"event/{event_abn}/banner.png",
                    f"event_story/{event_abn}/screen_image/banner_event_story.png",
                )
        if rewards := _vlive_rewards(live, boxes):
            brief["rewards"] = rewards
        if characters := _vlive_characters(live, unit_by_id):
            brief["characters"] = characters
        lives.append(brief)

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
    matched = False
    is_vs_item = False
    for level in levels:
        if _normalize_unit(level.get("targetUnit", "")) == "piapro":
            is_vs_item = True
            matched = matched or filter_piapro
        target_cid = level.get("targetGameCharacterId", 0)
        if target_cid > 0:
            if target_cid in _PIAPRO_CHARACTER_IDS:
                is_vs_item = True
                matched = matched or filter_piapro
            if filter_cid > 0 and target_cid == filter_cid:
                matched = True
        if filter_attr and _normalize_attr(level.get("targetCardAttr", "")) == filter_attr:
            matched = True
    if filter_tree and item.get("areaId") == _AREA_TREE_AREA_ID:
        matched = True
    if filter_flower and item.get("areaId") == _AREA_FLOWER_AREA_ID:
        matched = True
    if filter_unit and _AREA_FILTER_UNIT_AREA_IDS.get(filter_unit) == item.get("areaId") and not is_vs_item:
        matched = True
    return matched


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


def _area_shop_items(item_ids: list[int], now_ms: int) -> dict[int, dict[int, dict]]:
    """resolveAreaItemShopItems + fillAreaItemShopItemsByShopSequence."""
    item_set = set(item_ids)
    shop_by_box: dict[int, dict] = {}
    for shop_item in MD.get("shopItems"):
        shop_by_box.setdefault(shop_item.get("resourceBoxId"), shop_item)

    result: dict[int, dict[int, dict]] = {}
    for box in MD.get("resourceBoxes"):
        if box.get("resourceBoxPurpose") != "shop_item":
            continue
        shop_item = shop_by_box.get(box["id"])
        if not shop_item or (shop_item.get("startAt", 0) > 0 and shop_item["startAt"] > now_ms):
            continue
        for detail in box.get("details", []):
            if (
                str(detail.get("resourceType", "")).lower() != "area_item"
                or detail.get("resourceId", 0) <= 0
                or detail.get("resourceLevel", 0) <= 0
                or detail["resourceId"] not in item_set
            ):
                continue
            result.setdefault(detail["resourceId"], {}).setdefault(detail["resourceLevel"], shop_item)

    # Fallback: block assignment by shop sequence (snapshot_area.go:347-436).
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

    shop_items_by_shop: dict[int, list[dict]] = {}
    for shop_item in MD.get("shopItems"):
        if shop_item.get("shopId", 0) <= 0:
            continue
        if shop_item.get("startAt", 0) > 0 and shop_item["startAt"] > now_ms:
            continue
        shop_items_by_shop.setdefault(shop_item["shopId"], []).append(shop_item)

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


def build_education_challenge_live() -> str:
    scores = {r["characterId"]: r.get("highScore", 0) for r in SUITE.get("userChallengeLiveSoloResults") or []}
    ranks: dict[int, int] = {}
    for stage in SUITE.get("userChallengeLiveSoloStages") or []:
        if stage.get("rank", 0) > ranks.get(stage.get("characterId", 0), 0):
            ranks[stage["characterId"]] = stage["rank"]
    claimed = {r["challengeLiveHighScoreRewardId"] for r in SUITE.get("userChallengeLiveSoloHighScoreRewards") or []}

    rewards_by_char: dict[int, list[dict]] = {}
    for reward in MD.get("challengeLiveHighScoreRewards"):
        rewards_by_char.setdefault(reward["characterId"], []).append(reward)
    boxes = _resource_boxes("challenge_live_high_score")

    max_user_score = 0
    challenges = []
    for cid in range(1, 27):
        score = scores.get(cid, 0)
        max_user_score = max(max_user_score, score)
        jewel = shard = 0
        for reward in rewards_by_char.get(cid, []):
            if reward["id"] in claimed:
                continue
            box = boxes.get(reward.get("resourceBoxId"))
            if not box:
                continue
            for detail in box.get("details", []):
                kind = str(detail.get("resourceType", "")).lower()
                if kind == "jewel":
                    jewel += detail.get("resourceQuantity", 0)
                elif kind == "material" and detail.get("resourceId") == 15:
                    shard += detail.get("resourceQuantity", 0)
        challenges.append(
            {
                "chara_id": cid,
                "rank": ranks.get(cid, 0),
                "score": score,
                "jewel": jewel,
                "shard": shard,
                "chara_icon_path": ASSETS.chara_icon(cid),
            }
        )

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


def build_education_power_bonus() -> str:
    user_levels = _user_area_levels()
    item_ids = sorted(user_levels)
    caps = _released_caps(item_ids, _area_shop_items(item_ids, NOW_MS))

    level_rows = {(level["areaItemId"], level["level"]): level for level in MD.get("areaItemLevels")}
    chara = {cid: {"area_item": 0.0, "rank": 0.0, "fixture": 0.0} for cid in range(1, 27)}
    unit = {u: {"area_item": 0.0, "gate": 0.0} for u in _UNIT_ORDER}
    attr = {a: {"area_item": 0.0} for a in _ATTR_ORDER}

    for area in SUITE.get("userAreas") or []:
        for item in area.get("areaItems") or []:
            level_value = item.get("level", 0)
            cap = caps.get(item.get("areaItemId", 0), 0)
            if cap > 0 and level_value > cap:
                level_value = cap
            row = level_rows.get((item.get("areaItemId"), level_value))
            if not row:
                continue
            bonus = row.get("power1BonusRate", 0.0)
            if row.get("targetGameCharacterId", 0) > 0 and row["targetGameCharacterId"] in chara:
                chara[row["targetGameCharacterId"]]["area_item"] += bonus
            unit_key = _normalize_unit(row.get("targetUnit", ""))
            if unit_key and unit_key in unit:
                unit[unit_key]["area_item"] += bonus
            attr_key = _normalize_attr(row.get("targetCardAttr", ""))
            if attr_key and attr_key in attr:
                attr[attr_key]["area_item"] += bonus

    rank_rows = {(r["characterId"], r["characterRank"]): r for r in MD.get("characterRanks")}
    for character in SUITE.get("userCharacters") or []:
        row = rank_rows.get((character.get("characterId"), character.get("characterRank")))
        if row and character.get("characterId") in chara:
            chara[character["characterId"]]["rank"] += row.get("power1BonusRate", 0.0)

    for fixture in SUITE.get("userMysekaiFixtureGameCharacterPerformanceBonuses") or []:
        if fixture.get("gameCharacterId") in chara:
            chara[fixture["gameCharacterId"]]["fixture"] += fixture.get("totalBonusRate", 0.0) * 0.1

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

    body = {
        "profile": _profile(),
        "chara_bonuses": [
            {
                "chara_id": cid,
                "chara_icon_path": ASSETS.chara_icon(cid),
                **chara[cid],
                "total": chara[cid]["area_item"] + chara[cid]["rank"] + chara[cid]["fixture"],
            }
            for cid in range(1, 27)
        ],
        "unit_bonuses": [
            {
                "unit": u,
                "unit_icon_path": _unit_icon(u),
                **unit[u],
                "total": unit[u]["area_item"] + unit[u]["gate"],
            }
            for u in _UNIT_ORDER
        ],
        "attr_bonuses": [
            {"attr": a, "attr_icon_path": _attr_icon(a), **attr[a], "total": attr[a]["area_item"]} for a in _ATTR_ORDER
        ],
        "dt": NOW_MS,
    }
    return _emit("education_power_bonus", PowerBonusDetailRequest, body)


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
    materials = {AREA_COIN_MATERIAL_ID: SUITE.get("userGamedata", {}).get("coin", 0)}
    for item in SUITE.get("userMaterials") or []:
        if item.get("materialId", 0) > 0:
            materials[item["materialId"]] = item.get("quantity", 0)

    item_ids = _resolve_area_item_ids(filter_unit="idol")
    shop_map = _area_shop_items(item_ids, NOW_MS)
    caps = _released_caps(item_ids, shop_map)
    area_by_id = {a["id"]: a for a in MD.get("areaItems")}

    states = []
    min_current = -1
    for item_id in item_ids:
        master = area_by_id.get(item_id)
        levels = _area_item_levels(item_id)
        if not master or not levels:
            continue
        current = user_levels.get(item_id, 0)
        cap = caps.get(item_id, 0)
        if cap > 0 and current > cap:
            current = cap
        max_visible = max(current, cap)
        if max_visible <= 0:
            continue
        if min_current == -1 or current < min_current:
            min_current = current
        states.append((item_id, master, levels, current, max_visible))
    min_current = max(min_current, 0)

    area_items = []
    for item_id, master, levels, current, max_visible in states:
        level_map = {level["level"]: level for level in levels}
        shop_levels = shop_map.get(item_id) or {}
        sum_materials: dict[int, int] = {}
        level_infos = []
        for level in range(min_current + 1, max_visible + 1):
            row_master = level_map.get(level)
            if not row_master:
                level_infos.append({"level": level, "bonus": 0.0, "can_upgrade": False, "materials": []})
                continue
            row = {
                "level": level,
                "bonus": row_master.get("power1BonusRate", 0.0),
                "can_upgrade": True,
                "materials": [],
            }
            if level > current:
                shop_item = shop_levels.get(level)
                if not shop_item:
                    row["can_upgrade"] = False
                else:
                    for cost_entry in shop_item.get("costs", []):
                        cost = cost_entry.get("cost", {})
                        material_id = cost.get("resourceId", 0)
                        if str(cost.get("resourceType", "")).lower() == "coin":
                            material_id = AREA_COIN_MATERIAL_ID
                        sum_materials[material_id] = sum_materials.get(material_id, 0) + cost.get("quantity", 0)
                        have = materials.get(material_id, 0)
                        is_enough = have >= sum_materials[material_id]
                        if not is_enough:
                            row["can_upgrade"] = False
                        row["materials"].append(
                            {
                                "material_id": material_id,
                                "material_icon_path": _material_icon(
                                    cost.get("resourceType", ""), cost.get("resourceId", 0)
                                ),
                                "quantity": cost.get("quantity", 0),
                                "have_quantity": have,
                                "sum_quantity": sum_materials[material_id],
                                "is_enough": is_enough,
                            }
                        )
            level_infos.append(row)

        info = {
            "item_id": item_id,
            "current_level": current,
            "item_icon_path": ASSETS.region_asset(
                f"areaitem/{master.get('assetbundleName', '')}/{master.get('assetbundleName', '')}.png"
            ),
            "levels": level_infos,
        }
        if target := _area_item_target_icon(levels):
            info["target_icon_path"] = target
        area_items.append(info)

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


def build_education_bonds() -> str:
    """User-bond view, cid<=0 (snapshot_bonds.go:10-244)."""
    group_pairs = {
        b["groupId"]: (b["characterId1"], b["characterId2"]) for b in MD.get("bonds") if b.get("groupId", 0) > 0
    }
    styles = {
        u["id"]: {"character_id": u.get("gameCharacterId", 0), "color_code": (u.get("colorCode") or "").strip()}
        for u in MD.get("gameCharacterUnits")
    }
    char_ranks = {c["characterId"]: c.get("characterRank", 0) for c in SUITE.get("userCharacters") or []}

    def base_id(game_id: int) -> int:
        style = styles.get(game_id)
        return style["character_id"] if style and style["character_id"] > 0 else game_id

    def icon(game_id: int) -> str:
        return ASSETS.chara_icon(base_id(game_id))

    def color(game_id: int) -> list[int]:
        style = styles.get(game_id)
        code = (style or {}).get("color_code", "").lstrip("#")
        if len(code) != 6:
            return [100, 100, 100]
        try:
            return [int(code[i : i + 2], 16) for i in (0, 2, 4)]
        except ValueError:
            return [100, 100, 100]

    level_total = {}
    max_level = 0
    for row in MD.get("levels"):
        if row.get("levelType", "").lower() == "bonds" and row.get("level", 0) > 0:
            level_total[row["level"]] = row.get("totalExp", 0)
            max_level = max(max_level, row["level"])

    bonds = []
    user_max_rank = 0
    for entry in SUITE.get("userBonds") or []:
        pair = group_pairs.get(entry.get("bondsGroupId"))
        if not pair:
            continue
        rank, exp = entry.get("rank", 0), entry.get("exp", 0)
        user_max_rank = max(user_max_rank, rank)
        info = {
            "chara_id1": pair[0],
            "chara_id2": pair[1],
            "chara_icon_path1": icon(pair[0]),
            "chara_icon_path2": icon(pair[1]),
            "chara_rank1": char_ranks.get(base_id(pair[0]), 0),
            "chara_rank2": char_ranks.get(base_id(pair[1]), 0),
            "bond_level": rank,
            "has_bond": True,
            "color1": color(pair[0]),
            "color2": color(pair[1]),
        }
        if 0 < rank < max_level and rank in level_total and rank + 1 in level_total:
            info["need_exp"] = max(level_total[rank + 1] - level_total[rank] - exp, 0)
        bonds.append(info)

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


def build_education_leader_count() -> str:
    play_count: dict[int, int] = {}
    ex_count: dict[int, int] = {}
    ex_level: dict[int, int] = {}
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

    if not has_play_live:
        for item in SUITE.get("userCharacterLiveUsageCounts") or []:
            if item.get("characterId", 0) > 0 and str(item.get("characterLiveUsageType", "")).lower() == "leader":
                play_count[item["characterId"]] = item.get("usageCount", 0)

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

    leaders = [
        {
            "chara_id": cid,
            "chara_icon_path": ASSETS.chara_icon(cid),
            "play_count": play_count.get(cid, 0),
            "ex_level": ex_level.get(cid, 0),
            "ex_count": ex_count.get(cid, 0),
        }
        for cid in range(1, 27)
    ]
    leaders.sort(key=lambda x: (-(x["play_count"] + x["ex_count"]), x["chara_id"]))

    max_play = max((g.get("requirement", 0) for g in _param_groups(1)), default=0)
    if max_play <= 0:
        max_play = max((x["play_count"] for x in leaders), default=0)

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


def _mission_rows(cid: int) -> tuple[list[dict], int, int, int, int, int]:
    missions = sorted(
        (m for m in MD.get("characterMissionV2s") if m.get("characterId") == cid),
        key=lambda m: m["id"],
    )
    user_char = next((c for c in SUITE.get("userCharacters") or [] if c.get("characterId") == cid), None)
    current_level = (user_char or {}).get("characterRank", 0)
    current_exp = (user_char or {}).get("exp", 0)
    current_total_exp = (user_char or {}).get("totalExp", 0)

    char_levels = sorted(
        (
            (row["level"], row.get("totalExp", 0))
            for row in MD.get("levels")
            if row.get("levelType", "").lower() == "character" and row.get("level", 0) > 0
        ),
    )
    level_total = dict(char_levels)
    if current_level > 0 and current_total_exp > 0:
        base = level_total.get(current_level)
        if base is not None and current_total_exp >= base:
            current_exp = current_total_exp - base

    statuses = [s for s in _mission_statuses() if s.get("characterId") == cid]
    pending_exp = 0
    for status in statuses:
        if str(status.get("missionStatus", "")).strip().lower() == "achieved":
            pending_exp += _step_value(_param_groups(status.get("parameterGroupId", 0)), status.get("seq", 0), "exp")

    final_level, final_exp = current_level, current_exp + pending_exp
    base_total_exp = current_total_exp
    if base_total_exp <= 0 and current_level > 0 and current_level in level_total:
        base_total_exp = level_total[current_level] + current_exp
    if char_levels:
        final_total_exp = max(base_total_exp, 0) + pending_exp
        final_level, level_start = 1, 0
        for level, total_exp in char_levels:
            if total_exp <= final_total_exp:
                final_level, level_start = level, total_exp
                continue
            break
        final_exp = final_total_exp - level_start

    progress_by_type: dict[str, int] = {}
    for item in SUITE.get("userCharacterMissionV2s") or []:
        if item.get("characterId") != cid:
            continue
        kind = item.get("characterMissionType", "")
        progress_by_type[kind] = max(progress_by_type.get(kind, 0), item.get("progress", 0))

    seq_by_mission: dict[int, int] = {}
    seq_by_group: dict[int, int] = {}
    for status in statuses:
        mission_id, group_id, seq = status.get("missionId", 0), status.get("parameterGroupId", 0), status.get("seq", 0)
        seq_by_mission[mission_id] = max(seq_by_mission.get(mission_id, 0), seq)
        seq_by_group[group_id] = max(seq_by_group.get(group_id, 0), seq)

    rows = []
    for mission in missions:
        groups = _param_groups(mission.get("parameterGroupId", 0))
        mission_type = mission.get("characterMissionType", "")
        is_ex = mission_type in _EX_MISSION_TYPES

        current = progress_by_type.get(mission_type, 0)
        received = max(seq_by_mission.get(mission["id"], 0), seq_by_group.get(mission.get("parameterGroupId", 0), 0))
        if is_ex:
            cleared = sum(_step_value(groups, r, "requirement") for r in range(1, received + 1))
            if current > 0:
                if current < cleared:
                    current = cleared + current
            else:
                current = cleared
        elif current <= 0 and received > 0:
            current = _step_value(groups, received, "requirement")

        if is_ex:
            upper = sum(_step_value(groups, r, "requirement") for r in range(1, 31))
        else:
            upper = max((g.get("requirement", 0) for g in groups), default=0)
        ratio = 0.0
        if upper > 0:
            ratio = 1.0 if current > upper else current / upper

        row = {
            "mission_id": mission["id"],
            "mission_type": mission_type,
            "title": _MISSION_TITLES.get(mission_type, mission_type),
            "is_achievement": mission.get("isAchievementMission", False),
            "is_ex": is_ex,
            "current": current,
            "ratio": ratio,
        }
        if upper > 0:
            row["upper"] = upper

        if is_ex:
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
        else:
            for group_row in groups:
                if group_row.get("requirement", 0) > current:
                    if group_row.get("requirement", 0) > 0:
                        row["next_need"] = group_row["requirement"]
                    if group_row.get("exp", 0) > 0:
                        row["next_exp"] = group_row["exp"]
                    break
        rows.append(row)

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


def build_education_character_mission_all() -> str:
    cid, mission_type = MISSION_CID, "play_live"
    rows, *_ = _mission_rows(cid)
    by_type = {row["mission_type"]: row for row in rows}
    section_types = {
        "play_live": ["play_live", "play_live_ex"],
        "waiting_room": ["waiting_room", "waiting_room_ex"],
    }.get(mission_type, [mission_type])

    sections = []
    for section_type in section_types:
        base = by_type[section_type]
        mission = next(
            m
            for m in MD.get("characterMissionV2s")
            if m.get("characterId") == cid and m.get("characterMissionType") == section_type
        )
        groups = _param_groups(mission.get("parameterGroupId", 0))

        display_rows = []
        acc_requirement = acc_exp = 0
        if base["is_ex"]:
            max_round = max(base.get("current_round", 0), max((g.get("seq", 0) for g in groups), default=0))
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
        else:
            for group_row in groups:
                acc_exp += group_row.get("exp", 0)
                display_rows.append(
                    {
                        "seq": group_row.get("seq", 0),
                        "requirement": group_row.get("requirement", 0),
                        # Go keeps acc_requirement == requirement for non-EX rows (spec caveat).
                        "acc_requirement": group_row.get("requirement", 0),
                        "exp": group_row.get("exp", 0),
                        "acc_exp": acc_exp,
                    }
                )

        if base["is_ex"] and base.get("current_round", 0) > 0:
            reached_seq = base["current_round"]
        else:
            reached_seq = 0
            for row in display_rows:
                if row["requirement"] <= base["current"]:
                    reached_seq = row["seq"]
                    continue
                break

        section = {
            "mission_type": base["mission_type"],
            "title": base["title"],
            "is_ex": base["is_ex"],
            "current_total": base["current"],
            "reached_seq": reached_seq,
            "ratio": base["ratio"],
            "display_rows": display_rows,
        }
        for src_key, dst_key in (
            ("current_round", "current_round_no"),
            ("current_round_progress", "current_round_progress"),
            ("current_round_need", "current_round_need"),
            ("upper", "upper"),
            ("next_need", "next_need"),
            ("next_exp", "next_exp"),
        ):
            if src_key in base:
                section[dst_key] = base[src_key]
        sections.append(section)

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
