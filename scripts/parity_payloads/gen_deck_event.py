"""Real-payload generator for the deck/event domain (5 endpoints).

Replicates Haruki-Cloud's request construction offline per
``out/payload-specs/deck-event.md``:

- ``deck_recommend``  -> POST /api/pjsk/deck/recommend
- ``event_detail``    -> POST /api/pjsk/event/detail
- ``event_record``    -> POST /api/pjsk/event/record
- ``event_list``      -> POST /api/pjsk/event/list
- ``event_planner``   -> POST /api/pjsk/event/planner

The deck-service recommendation results (deck_data) cannot be reproduced
offline; they are mocked from the suite snapshot's owned cards with the exact
field/ordering/rounding conventions of controller_request.go (see module
constants below for the assumed engine outputs).
"""

from __future__ import annotations

from datetime import datetime
from functools import cache
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import common

from src.sekai.deck.model import DeckRequest
from src.sekai.event.model import (
    EventDetailRequest,
    EventListRequest,
    EventPlannerRequest,
    EventRecordRequest,
)

MD = common.MD
ASSETS = common.ASSETS

# builder_metadata.go:154-165
_EVENT_TYPE_DISPLAY = {"marathon": "马拉松", "cheerful_carnival": "5v5", "world_bloom": "WorldLink"}

# event_planner.go:70-82 boost -> point multiplier
_BOOST_MULTIPLIERS = {0: 1, 1: 5, 2: 10, 3: 15, 4: 20, 5: 25, 6: 27, 7: 29, 8: 31, 9: 33, 10: 35}

_RARITY_WEIGHT = {"rarity_4": 4, "rarity_birthday": 3, "rarity_3": 2, "rarity_2": 1, "rarity_1": 0}
# Mock deck-service skill score-up base per rarity (offline stand-in, see generate() issues).
_SKILL_BASE = {"rarity_4": 100.0, "rarity_birthday": 95.0, "rarity_3": 80.0, "rarity_2": 70.0, "rarity_1": 60.0}


# ---------------------------------------------------------------------------
# Masterdata helpers (render/event/builder_filters.go + provider/local_events.go)
# ---------------------------------------------------------------------------


@cache
def _gcu_by_id() -> dict[int, dict]:
    return {u["id"]: u for u in MD.get("gameCharacterUnits")}


@cache
def _event_cards(event_id: int) -> list[dict]:
    """Event card masters in eventCards.json order (local_loader.go:317-320)."""
    out = []
    for ec in MD.get("eventCards"):
        if ec["eventId"] != event_id:
            continue
        card = MD.card_by_id().get(ec["cardId"])
        if card:
            out.append(card)
    return out


@cache
def _event_bonus_meta(event_id: int) -> tuple[str, frozenset[str], tuple[int, ...]]:
    """(bonus attr lowercase, bonus unit set, sorted bonus character ids)
    per builder_filters.go extractEventBonusMeta."""
    attr = ""
    units: set[str] = set()
    chars: set[int] = set()
    for bonus in MD.get("eventDeckBonuses"):
        if bonus["eventId"] != event_id:
            continue
        if not attr and bonus.get("cardAttr"):
            attr = str(bonus["cardAttr"]).lower()
        gcu_id = bonus.get("gameCharacterUnitId", 0)
        if gcu_id:
            gcu = _gcu_by_id().get(gcu_id)
            if gcu:
                units.add(gcu["unit"])
                chars.add(gcu["gameCharacterId"])
        elif bonus.get("gameCharacterId", 0):
            chars.add(bonus["gameCharacterId"])
    return attr, frozenset(units), tuple(sorted(chars))


def _is_box_event(event_id: int) -> bool:
    """event_box.go:10-30 — exactly one bonus unit."""
    _, units, _ = _event_bonus_meta(event_id)
    return len(units) == 1


@cache
def _banner_character(event_id: int) -> int:
    """local_events.go:218-239 — lowest non-festival event card's character."""
    best_card = None
    for card in _event_cards(event_id):
        if "festival" in common.card_supply_type(card):
            continue
        if best_card is None or card["id"] < best_card["id"]:
            best_card = card
    return best_card["characterId"] if best_card else 0


def _banner_index(char_id: int, event_id: int) -> int:
    """builder_filters.go:314-325 — 1-based index among the character's box events."""
    ban_events = [
        e
        for e in MD.get("events")
        if e["eventType"] in ("marathon", "cheerful_carnival")
        and _banner_character(e["id"]) == char_id
        and _is_box_event(e["id"])
    ]
    ban_events.sort(key=lambda e: e["startAt"])
    for idx, e in enumerate(ban_events):
        if e["id"] == event_id:
            return idx + 1
    return 0


def _event_banner(assetbundle_name: str) -> str:
    """assets/helper.go:291-303 ResolveEventBannerPath candidates."""
    abn = assetbundle_name
    return ASSETS.region_asset(
        f"home/banner/{abn}/{abn}.png",
        f"event/{abn}/banner.png",
        f"event_story/{abn}/screen_image/banner_event_story.png",
    )


def _unit_icon_by_character(char_id: int) -> str | None:
    """builder_metadata.go unitIconPathByCharacter."""
    character = MD.character_by_id().get(char_id)
    if not character:
        return None
    icon = common.UNIT_ICONS.get(character.get("unit", ""))
    return ASSETS.static(icon) if icon else None


def _display_event_type(code: str) -> str:
    return _EVENT_TYPE_DISPLAY.get(code.lower(), code)


def _latest_ended_event() -> dict:
    now = common.now_ms()
    past = [e for e in MD.get("events") if 0 < e.get("aggregateAt", 0) <= now]
    return max(past, key=lambda e: e["startAt"])


# ---------------------------------------------------------------------------
# POST /api/pjsk/event/list (builder_filters.go + builder_metadata.go:83-125)
# ---------------------------------------------------------------------------


def _event_brief(event: dict) -> dict:
    brief: dict = {
        "id": event["id"],
        "event_name": event["name"],
        # list page sends the display name in BOTH fields (builder_metadata.go:87-88)
        "event_type": _display_event_type(event["eventType"]),
        "event_type_name": _display_event_type(event["eventType"]),
        "start_at": event["startAt"],
        "end_at": event["aggregateAt"] + 1000,
        "event_banner_path": _event_banner(event["assetbundleName"]),
    }
    cards = _event_cards(event["id"])
    if cards:
        brief["event_cards"] = [common.card_thumbnail(c, thumb_after=False) for c in cards[:6]]
    attr, _, _ = _event_bonus_meta(event["id"])
    if attr:
        brief["event_attr_path"] = ASSETS.static(f"card/attr_{attr}.png")  # list uses attr_, not attr_icon_
    if event["eventType"].lower() != "world_bloom":
        banner_cid = _banner_character(event["id"])
        if banner_cid:
            brief["event_chara_path"] = ASSETS.chara_icon(banner_cid)
            unit_icon = _unit_icon_by_character(banner_cid)
            if unit_icon:
                brief["event_unit_path"] = unit_icon
    elif 1 <= len(cards) <= 6:
        unit_icon = _unit_icon_by_character(cards[0]["characterId"])
        if unit_icon:
            brief["event_unit_path"] = unit_icon
    return brief


def _build_event_list() -> dict:
    # Emulates "/活动列表 year=2026": startAt year in the server-local timezone
    # (builder_filters.go:32,49 — production runs Asia/Shanghai), sorted by startAt asc.
    tz = ZoneInfo("Asia/Shanghai")
    events = [e for e in MD.get("events") if datetime.fromtimestamp(e["startAt"] / 1000, tz).year == 2026]
    events.sort(key=lambda e: e["startAt"])
    return {"event_info": [_event_brief(e) for e in events]}


# ---------------------------------------------------------------------------
# POST /api/pjsk/event/detail (render/event/builder.go + builder_metadata.go:15-81)
# ---------------------------------------------------------------------------


def _build_event_detail(event: dict) -> dict:
    event_id = event["id"]
    is_wl = event["eventType"].lower() == "world_bloom"
    attr, _, bonus_chars = _event_bonus_meta(event_id)

    info: dict = {
        "id": event_id,
        "event_type": event["eventType"],  # detail keeps the raw code
        "event_type_name": _display_event_type(event["eventType"]),
        "start_at": event["startAt"],
        "end_at": event["aggregateAt"] + 1000,
        "is_wl_event": is_wl,
        "banner_cid": 0,
        "banner_index": 0,
        "bonus_attr": attr,
    }
    if not is_wl and _is_box_event(event_id):
        banner_cid = _banner_character(event_id)
        if banner_cid:
            info["banner_cid"] = banner_cid
            info["banner_index"] = _banner_index(banner_cid, event_id)
    if bonus_chars:  # Go: []int{} + omitempty -> key dropped when empty
        info["bonus_chara_id"] = list(bonus_chars)

    abn = event["assetbundleName"]
    assets: dict = {
        "event_bg_path": ASSETS.region_asset(f"event/{abn}/screen/bg.png", f"event/{abn}/bg.png"),
        "event_logo_path": ASSETS.region_asset(f"event/{abn}/logo/logo.png", f"event/{abn}/logo.png"),
        "event_story_bg_path": "",
        "event_attr_image_path": "",
        "event_ban_chara_img": "",
        "ban_chara_icon_path": "",
    }
    if not is_wl:
        assets["event_story_bg_path"] = ASSETS.region_asset(f"event_story/{abn}/screen_image/story_bg.png")
        assets["event_ban_chara_img"] = ASSETS.region_asset(f"event/{abn}/screen/character.png")
    if attr:
        assets["event_attr_image_path"] = ASSETS.static(f"card/attr_icon_{attr}.png")
    if info["banner_cid"]:
        assets["ban_chara_icon_path"] = ASSETS.chara_icon(info["banner_cid"])
    if bonus_chars:  # Go: []string{} + omitempty -> dropped when empty
        assets["bonus_chara_path"] = [ASSETS.chara_icon(cid) for cid in bonus_chars]
    if not assets["ban_chara_icon_path"] and bonus_chars:
        assets["ban_chara_icon_path"] = ASSETS.chara_icon(bonus_chars[0])

    return {
        "region": common.REGION,
        "event_info": info,
        "event_assets": assets,
        "event_cards": [common.card_thumbnail(c, thumb_after=False) for c in _event_cards(event_id)],
    }


# ---------------------------------------------------------------------------
# POST /api/pjsk/event/record (handler/event_record_builder.go)
# ---------------------------------------------------------------------------


@cache
def _resource_box_honor_ids(purpose: str) -> dict[int, tuple[int, ...]]:
    """resourceBoxId -> honor resource ids for a purpose (event_record_builder.go:344-412)."""
    out: dict[int, tuple[int, ...]] = {}
    for box in MD.get("resourceBoxes"):
        if box.get("resourceBoxPurpose") != purpose:
            continue
        ids = tuple(
            d["resourceId"]
            for d in box.get("details", [])
            if str(d.get("resourceType", "")).lower() == "honor" and d.get("resourceId", 0) > 0
        )
        if ids:
            out[box["id"]] = ids
    return out


def _event_honor_tiers(event: dict) -> dict[int, int]:
    """honorId -> lowest toRank tier from the event's embedded reward ranges."""
    box_honors = _resource_box_honor_ids("event_ranking_reward")
    tiers: dict[int, int] = {}
    for rng in event.get("eventRankingRewardRanges", []):
        to_rank = rng.get("toRank", 0)
        if to_rank <= 0:
            continue
        ids = [
            d.get("resourceId", 0)
            for d in rng.get("eventRankingRewardDetails", [])
            if str(d.get("resourceType", "")).lower() == "honor"
        ]
        for reward in rng.get("eventRankingRewards", []):
            ids.extend(box_honors.get(reward.get("resourceBoxId", 0), ()))
        for honor_id in ids:
            if honor_id > 0 and (honor_id not in tiers or to_rank < tiers[honor_id]):
                tiers[honor_id] = to_rank
    return tiers


def _user_honor_ids(suite: dict) -> set[int]:
    ids = {h["honorId"] for h in suite.get("userHonors", []) if h.get("honorId", 0) > 0}
    for h in suite.get("userProfileHonors", []) or []:
        for key in ("honorId", "honorId2"):
            if h.get(key, 0) > 0:
                ids.add(h[key])
    return ids


def _ranking_settled(event: dict, now: int) -> bool:
    settled_at = event.get("rankingAnnounceAt", 0) or event.get("aggregateAt", 0) or event.get("closedAt", 0)
    return settled_at > 0 and now >= settled_at


def _history_row(event: dict, event_point: int | None) -> dict:
    row: dict = {
        "id": event["id"],
        "event_name": event["name"],
        "start_at": event["startAt"],
        "end_at": event["closedAt"],  # record uses closedAt, not aggregateAt+1000
        "is_wl_event": event["eventType"].lower() == "world_bloom",
        "banner_path": _event_banner(event["assetbundleName"]),
    }
    if event_point is not None:
        row["event_point"] = event_point
    return row


def _sort_event_history(rows: list[dict]) -> None:
    """event_record_builder.go:651-705 — ranked first (asc), then point desc, startAt desc."""

    def rank_value(row: dict) -> int | None:
        if row.get("rank", 0) and row["rank"] > 0:
            return row["rank"]
        if row.get("rank_tier", 0) and row["rank_tier"] > 0:
            return row["rank_tier"]
        display = str(row.get("rank_display") or "").strip().upper()
        if display.startswith("T") and display[1:].isdigit() and int(display[1:]) > 0:
            return int(display[1:])
        return None

    def key(row: dict):
        rank = rank_value(row)
        point = row.get("event_point") or 0
        return (0 if rank is not None else 1, rank or 0, -point, -row["start_at"])

    rows.sort(key=key)


def _build_event_record() -> dict:
    suite = common.load_suite()
    now = suite.get("now") or common.now_ms()
    events_by_id = MD.event_by_id()
    honor_ids = _user_honor_ids(suite)

    # Rank lookup: userEventResults first, embedded userEvents rank fills gaps (:49-60).
    rank_by_event: dict[int, int] = {}
    for result in suite.get("userEventResults") or []:
        rank_by_event[result["eventId"]] = result.get("rank", 0)
    for ue in suite.get("userEvents") or []:
        if ue.get("eventId", 0) > 0 and ue.get("rank", 0) > 0:
            rank_by_event.setdefault(ue["eventId"], ue["rank"])
    # JP region: no untrusted-rank dropping (:618-632).

    # Regular events' honor T-tiers (:538-575).
    display_by_event: dict[int, int] = {}
    for event in MD.get("events"):
        if not _ranking_settled(event, now):
            continue
        tiers = _event_honor_tiers(event)
        hit = [tiers[h] for h in honor_ids if tiers.get(h, 0) > 0]
        if hit:
            display_by_event[event["id"]] = min(hit)

    event_info: list[dict] = []
    seen_events: set[int] = set()
    for ue in suite.get("userEvents") or []:
        event = events_by_id.get(ue.get("eventId"))
        if not event:
            continue
        row = _history_row(event, ue.get("eventPoint", 0))
        if event["id"] in display_by_event:
            row["rank_display"] = f"T{display_by_event[event['id']]}"
            row["rank_tier"] = display_by_event[event["id"]]
        if event["id"] in rank_by_event:  # exact rank wins and clears the tier display
            row["rank"] = rank_by_event[event["id"]]
            row.pop("rank_display", None)
            row.pop("rank_tier", None)
        event_info.append(row)
        seen_events.add(event["id"])
    for event_id in sorted(display_by_event):  # deterministic stand-in for Go map order
        if event_id in seen_events or event_id not in events_by_id:
            continue
        row = _history_row(events_by_id[event_id], None)
        row["rank_display"] = f"T{display_by_event[event_id]}"
        row["rank_tier"] = display_by_event[event_id]
        if event_id in rank_by_event:
            row["rank"] = rank_by_event[event_id]
            row.pop("rank_display", None)
            row.pop("rank_tier", None)
        event_info.append(row)
        seen_events.add(event_id)
    for event_id in sorted(rank_by_event):
        if event_id in seen_events or event_id not in events_by_id:
            continue
        row = _history_row(events_by_id[event_id], None)
        row["rank"] = rank_by_event[event_id]
        event_info.append(row)
        seen_events.add(event_id)

    # World bloom chapter T-tiers (:419-485).
    wl_box_honors = _resource_box_honor_ids("world_bloom_chapter_ranking_reward")
    chapter_ranges: dict[tuple[int, int], list[dict]] = {}
    for rng in MD.get("worldBloomChapterRankingRewardRanges"):
        chapter_ranges.setdefault((rng["eventId"], rng["gameCharacterId"]), []).append(rng)
    display_by_chapter: dict[tuple[int, int], int] = {}
    for chapter in MD.get("worldBlooms"):
        char_id = chapter.get("gameCharacterId", 0)
        if char_id <= 0:
            continue
        closed_at = chapter.get("chapterEndAt", 0) or chapter.get("aggregateAt", 0)
        if closed_at <= 0 or now < closed_at:
            continue
        best = 0
        for rng in chapter_ranges.get((chapter["eventId"], char_id), []):
            if rng.get("toRank", 0) <= 0 or rng.get("resourceBoxId", 0) <= 0:
                continue
            if not honor_ids.intersection(wl_box_honors.get(rng["resourceBoxId"], ())):
                continue
            if best == 0 or rng["toRank"] < best:
                best = rng["toRank"]
        if best > 0:
            display_by_chapter[(chapter["eventId"], char_id)] = best

    wl_event_info: list[dict] = []
    seen_chapters: set[tuple[int, int]] = set()
    for wb in suite.get("userWorldBlooms") or []:
        event = events_by_id.get(wb.get("eventId"))
        if not event:
            continue
        row = _history_row(event, wb.get("worldBloomChapterPoint", 0))
        row["is_wl_event"] = True
        key = (wb["eventId"], wb.get("gameCharacterId", 0))
        if key in display_by_chapter:
            row["rank_display"] = f"T{display_by_chapter[key]}"
            row["rank_tier"] = display_by_chapter[key]
        if wb.get("rank", 0) > 0:
            row["rank"] = wb["rank"]
            row.pop("rank_display", None)
            row.pop("rank_tier", None)
        if key[1] > 0:
            row["wl_chara_icon_path"] = ASSETS.region_asset(f"character/character_sd_l/chr_sp_{key[1]}.png")
        wl_event_info.append(row)
        seen_chapters.add(key)
    for key in sorted(display_by_chapter):
        if key in seen_chapters or key[0] not in events_by_id:
            continue
        row = _history_row(events_by_id[key[0]], None)
        row["is_wl_event"] = True
        row["rank_display"] = f"T{display_by_chapter[key]}"
        row["rank_tier"] = display_by_chapter[key]
        row["wl_chara_icon_path"] = ASSETS.region_asset(f"character/character_sd_l/chr_sp_{key[1]}.png")
        wl_event_info.append(row)
        seen_chapters.add(key)

    _sort_event_history(event_info)
    _sort_event_history(wl_event_info)

    return {
        "event_info": event_info,
        "wl_event_info": wl_event_info,
        # record keeps source/mode (resolver_profiles.go:170-190); JP -> no rank_note
        "user_info": common.build_user_info(is_hide_uid=True),
    }


# ---------------------------------------------------------------------------
# POST /api/pjsk/deck/recommend (render/deck/controller_request.go + metadata)
# ---------------------------------------------------------------------------


def _normalize_rate(value: float) -> float | int:
    """controller_request.go:163-169 — round to 0.1, integral values collapse to int."""
    rounded = round(value * 10) / 10
    if abs(rounded - round(rounded)) < 1e-9:
        return round(rounded)
    return rounded


@cache
def _user_card_by_id() -> dict[int, dict]:
    return {c["cardId"]: c for c in common.load_suite().get("userCards", [])}


def _card_event_bonus(card: dict, event_id: int) -> float:
    """Best-matching eventDeckBonuses rate + eventCards bonus (offline engine stand-in)."""
    char_id = card["characterId"]
    attr = card["attr"]
    support = card.get("supportUnit", "none")
    best = 0.0
    for bonus in MD.get("eventDeckBonuses"):
        if bonus["eventId"] != event_id:
            continue
        gcu_id = bonus.get("gameCharacterUnitId", 0)
        if gcu_id:
            gcu = _gcu_by_id().get(gcu_id)
            if not gcu or gcu["gameCharacterId"] != char_id:
                continue
            if char_id > 20 and support not in ("none", "") and gcu["unit"] != support:
                continue
        if bonus.get("cardAttr") and bonus["cardAttr"] != attr:
            continue
        if not gcu_id and not bonus.get("cardAttr"):
            continue
        best = max(best, bonus.get("bonusRate", 0.0))
    for ec in MD.get("eventCards"):
        if ec["eventId"] == event_id and ec["cardId"] == card["id"]:
            best += ec.get("bonusRate", 0.0)
            break
    return best


def _deck_card_raw(card_id: int, event_id: int) -> dict:
    card = MD.card_by_id()[card_id]
    uc = _user_card_by_id().get(card_id, {})
    level = uc.get("level", 0)
    if level <= 0:
        level = 60
    master_rank = uc.get("masterRank", 0)
    skill_level = uc.get("skillLevel", 0) or 1
    display_after = uc.get("defaultImage", "") == "special_training"
    training_done = str(uc.get("specialTrainingStatus", "")).lower() == "done"
    episodes = uc.get("episodes", [])
    ep_read = [e.get("scenarioStatus") == "already_read" for e in episodes]
    bonus_raw = _card_event_bonus(card, event_id)
    skill_raw = _SKILL_BASE.get(card["cardRarityType"], 60.0) + 10.0 * (skill_level - 1)
    entry = {
        "card_thumbnail": common.card_thumbnail(
            card,
            thumb_after=display_after,
            star_after=display_after or training_done,  # done forces the after-training star
            train_rank=master_rank,
            level=level,
            is_pcard=True,
        ),
        "chara_id": card["characterId"],
        "skill_level": str(skill_level),
        "is_after_training": display_after,
        "skill_rate": _normalize_rate(skill_raw),
        "event_bonus_rate": _normalize_rate(bonus_raw),
        "is_before_story": bool(ep_read and ep_read[0]),
        "is_after_story": bool(len(ep_read) > 1 and ep_read[1]),
        "has_canvas_bonus": False,
    }
    return {
        "entry": entry,
        "raw_bonus": bonus_raw,
        "master_rank": master_rank,
        "level": level,
        "card_id": card_id,
    }


@cache
def _recommend_pool(event_id: int) -> tuple[int, ...]:
    """Best owned card per character, ranked by event bonus — engine result stand-in."""
    best_per_char: dict[int, tuple] = {}
    for uc in common.load_suite().get("userCards", []):
        card = MD.card_by_id().get(uc["cardId"])
        if not card:
            continue
        key = (
            _card_event_bonus(card, event_id),
            _RARITY_WEIGHT.get(card["cardRarityType"], 0),
            uc.get("level", 0),
            uc.get("masterRank", 0),
            card["id"],
        )
        char_id = card["characterId"]
        if char_id not in best_per_char or key > best_per_char[char_id]:
            best_per_char[char_id] = key
    ranked = sorted(best_per_char.values(), reverse=True)
    return tuple(key[4] for key in ranked[:9])


def _mock_deck(
    card_ids: list[int],
    event_id: int,
    *,
    score: int,
    live_score: int,
    total_power: int,
    skill_up_raw: float,
) -> dict:
    raws = [_deck_card_raw(cid, event_id) for cid in card_ids]
    members = raws[1:]
    # controller_request.go:81-97 — leader stays, members sort by raw engine values.
    members.sort(key=lambda r: (-r["raw_bonus"], -r["master_rank"], -r["level"], -r["card_id"]))
    ordered = [raws[0], *members]
    return {
        "card_data": [r["entry"] for r in ordered],
        "score": score,
        "live_score": live_score,
        "mysekai_event_point": 0,
        "event_bonus_rate": _normalize_rate(sum(r["raw_bonus"] for r in raws)),
        "support_deck_bonus_rate": 0,
        "multi_live_score_up": _normalize_rate(skill_up_raw),
        "total_power": total_power,
        "challenge_score_delta": 0,
    }


def _deck_profile() -> dict:
    """sanitizeDeckProfile (controller_resolve.go:35-43): empty source, no mode."""
    profile = common.build_user_info(is_hide_uid=True)
    profile["source"] = ""
    profile.pop("mode", None)
    return profile


def _mock_decks(event_id: int, limit: int, base_score: int) -> list[dict]:
    pool = list(_recommend_pool(event_id))
    combos = [
        (0, 1, 2, 3, 4),
        (0, 1, 2, 3, 5),
        (0, 1, 2, 4, 5),
        (0, 1, 3, 4, 5),
        (0, 2, 3, 4, 5),
        (0, 1, 2, 3, 6),
    ][:limit]
    return [
        _mock_deck(
            [pool[i] for i in combo],
            event_id,
            score=base_score - 4 * n,
            live_score=2_612_000 - 17_000 * n,
            total_power=337_450 - 2_150 * n,
            skill_up_raw=252.3 - 1.7 * n,
        )
        for n, combo in enumerate(combos)
    ]


def _deck_request_body(
    event: dict,
    *,
    limit: int,
    base_score: int,
    music: dict,
    model_name: list[str],
    cost_times: dict[str, float],
    wait_times: dict[str, float],
) -> dict:
    body: dict = {
        "region": common.REGION,
        "profile": _deck_profile(),
        "deck_data": _mock_decks(event["id"], limit, base_score),
        "is_max_deck": False,
        "recommend_type": "event",
        "is_wl": False,
        "keep_after_training_state": False,
        "target": "score",
        "model_name": model_name,
        "cost_times": cost_times,
        "wait_times": wait_times,
        "canvas_thumbnail_path": ASSETS.region_asset("mysekai/icon/category_icon/icon_canvas.png"),
        **music,
        # normalizeRecommendLiveOptions defaults for live_type=multi
        "multi_live_teammate_power": 250_000,
        "multi_live_teammate_score_up": 200,
        "skill_order_choose_strategy": "average",
        "skill_reference_choose_strategy": "average",
        "live_type": "multi",
        "live_name": "协力",
        "event_id": event["id"],
        "event_name": event["name"],
        "event_banner_path": _event_banner(event["assetbundleName"]),
    }
    return body


_OMAKASE_MUSIC = {
    "music_id": 10000,
    "music_title": "おまかせ (所有歌曲平均)",
    "music_cover_path": "static_images/omakase.png",
    "music_diff": "master",
}


def _resolved_music(music_id: int, diff: str) -> dict:
    music = next(m for m in MD.get("musics") if m["id"] == music_id)
    abn = music["assetbundleName"]
    return {
        "music_id": music_id,
        "music_title": music["title"],
        "music_cover_path": ASSETS.region_asset(f"music/jacket/{abn}/{abn}.png"),
        "music_diff": diff,
    }


def _build_deck_recommend(event: dict) -> dict:
    ASSETS.static("omakase.png")  # keep the hardcoded omakase cover in the rsync manifest
    return _deck_request_body(
        event,
        limit=6,
        base_score=561,
        music=_OMAKASE_MUSIC,
        model_name=["RL", "DGA+RL", "DGA", "RL", "DGA", "RL"],
        cost_times={"DGA": 2.184, "RL": 3.052},
        wait_times={"DGA": 0.0, "RL": 0.113},
    )


# ---------------------------------------------------------------------------
# POST /api/pjsk/event/planner (handler/event_planner.go)
# ---------------------------------------------------------------------------


def _fmt_rate(value: float) -> str:
    """formatEventPlannerRate — 1 decimal, integral values without the fraction."""
    rounded = round(value * 10) / 10
    if abs(rounded - round(rounded)) < 1e-9:
        return str(round(rounded))
    return f"{rounded:.1f}"


def _build_event_planner(event: dict) -> dict:
    target_point = 10_000_000
    current_point = 2_345_678
    remaining = max(target_point - current_point, 0)
    # Event already ended -> daily point over the full event window (event_planner.go:470-488).
    duration_days = (event["aggregateAt"] - event["startAt"]) / 86_400_000
    daily_point = math.ceil(target_point / duration_days)

    # Default songs: 虾 expert / 龙 hard (226) / 野车 master (10000) (event_planner.go:456-460).
    # "虾" resolves through the online music-alias service; offline we pin it to
    # music 276 シャンティ (see generate() issues).
    songs_spec = [
        ("虾", _resolved_music(276, "expert"), 561),
        ("龙", _resolved_music(226, "hard"), 537),
        ("野车", dict(_OMAKASE_MUSIC), 512),
    ]
    boosts = [5, 10]  # default 火数 (event_planner.go:113-114)

    songs = []
    deck_request: dict | None = None
    for query, music, base_score in songs_spec:
        if deck_request is None:
            deck_request = _deck_request_body(
                event,
                limit=1,
                base_score=base_score,
                music=music,
                model_name=["RL"],  # baseQuery defaults Algorithm="rl"
                cost_times={"RL": 2.734},
                wait_times={"RL": 0.0},
            )
        rows = []
        for boost in boosts:
            point = base_score * _BOOST_MULTIPLIERS[boost]
            plays = math.ceil(remaining / point) if remaining > 0 else 0
            rows.append({"boost": boost, "point_per_play": point, "plays": plays, "energy": plays * boost})
        songs.append(
            {
                "music_id": music["music_id"],
                "query": query,
                "title": music["music_title"],
                "music_cover_path": music["music_cover_path"],
                "difficulty": music["music_diff"],
                "rows": rows,
            }
        )

    assert deck_request is not None
    first_deck = deck_request["deck_data"][0]
    deck_total_power = first_deck["total_power"]
    deck_event_bonus = first_deck["event_bonus_rate"]
    deck_skill_up = first_deck["multi_live_score_up"]
    deck_summary = (
        f"最优组卡 / 综合力 {deck_total_power:,}"
        f" / 活动加成 {_fmt_rate(deck_event_bonus)}%"
        f" / 协力实效 {_fmt_rate(deck_skill_up)}%"
    )

    return {
        "title": "活动规划",
        "region": common.REGION,
        "event_id": event["id"],
        "event_name": event["name"],
        "event_banner_path": deck_request["event_banner_path"],
        "live_name": deck_request["live_name"],
        "profile": deck_request["profile"],
        "target_point": target_point,
        "current_point": current_point,
        "remaining_point": remaining,
        "daily_point": daily_point,
        "target_source": "直接输入",
        "deck_summary": deck_summary,
        "deck_cards": [
            {
                "card_thumbnail": card["card_thumbnail"],
                "skill_level": card["skill_level"],
                "skill_rate": card["skill_rate"],
                "event_bonus_rate": card["event_bonus_rate"],
            }
            for card in first_deck["card_data"]
        ],
        "deck_total_power": deck_total_power,
        "deck_event_bonus": deck_event_bonus,
        "deck_skill_up": deck_skill_up,
        "songs": songs,
        # nested deck_request is NOT re-injected with timezone/dt (request_dt.go root-only)
        "deck_request": deck_request,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate() -> list[str]:
    event = _latest_ended_event()
    payloads = [
        ("event_list", _build_event_list(), EventListRequest),
        ("event_detail", _build_event_detail(event), EventDetailRequest),
        ("event_record", _build_event_record(), EventRecordRequest),
        ("deck_recommend", _build_deck_recommend(event), DeckRequest),
        ("event_planner", _build_event_planner(event), EventPlannerRequest),
    ]
    written = []
    for name, body, model in payloads:
        model.model_validate(body)  # gate: drawing-side pydantic model must accept the body
        common.write_payload(name, body)
        written.append(name)
    return written


if __name__ == "__main__":
    names = generate()
    print("written:", names)  # noqa: T201
    common.ASSETS.save_manifest()
    print("missing assets:", len(common.ASSETS.missing))  # noqa: T201
