"""Real-payload generator: profile / honor / inventory_list.

Replicates Haruki-Cloud's request-body construction offline (Go file refs):
- profile:        render/profile/controller_snapshot.go BuildProfileRequest (snapshot path)
- honor:          render/honor/builder.go BuildHonorRequest (normal + bonds branches)
- inventory_list: render/inventory/controller.go BuildListRequestFromSnapshot (default filter)

Spec: out/payload-specs/profile-honor-inv.md
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
import re
import sys
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import common

from src.sekai.honor.model import HonorRequest
from src.sekai.inventory.model import InventoryListRequest
from src.sekai.profile.model import ProfileRequest

_WORD_TAG = re.compile(r"<#.*?>")  # profile/controller.go:15

_DIFFICULTIES = ["easy", "normal", "hard", "expert", "master", "append"]

# honor/builder_normal.go:239-251
_DIFF_SCORE = {
    3009: ("easy", "fc"),
    3010: ("normal", "fc"),
    3011: ("hard", "fc"),
    3012: ("expert", "fc"),
    3013: ("master", "fc"),
    3014: ("master", "ap"),
    4700: ("append", "fc"),
    4701: ("append", "ap"),
}

# honor/builder_normal.go:226-237
_RARITY_RANK = {"middle": 2, "high": 3, "highest": 4}


# ---------------------------------------------------------------------------
# Masterdata indexes
# ---------------------------------------------------------------------------


@cache
def _honor_by_id() -> dict[int, dict]:
    return {h["id"]: h for h in common.MD.get("honors")}


@cache
def _honor_group_raw_by_id() -> dict[int, dict]:
    return {g["id"]: g for g in common.MD.get("honorGroups")}


@cache
def _bonds_honor_by_id() -> dict[int, dict]:
    return {b["id"]: b for b in common.MD.get("bondsHonors")}


@cache
def _bonds_word_by_id() -> dict[int, dict]:
    return {w["id"]: w for w in common.MD.get("bondsHonorWords")}


@cache
def _gcu_by_id() -> dict[int, dict]:
    return {u["id"]: u for u in common.MD.get("gameCharacterUnits")}


@cache
def _meta_by_id(table: str) -> dict[int, dict]:
    return {m["id"]: m for m in common.MD.get(table)}


def _exists(path: str) -> bool:
    """Local asset probe (assets/helper.go FirstExisting, exact-case subset)."""
    return bool(path) and (common.ASSETS.data_dir / path).exists()


# ---------------------------------------------------------------------------
# Honor builder (render/honor/builder*.go)
# ---------------------------------------------------------------------------


def _birthday_group_matches_character(group_name: str, ch: dict) -> bool:
    """provider/local_honors.go:218-245."""
    name = group_name.strip()
    if not name:
        return False
    first = str(ch.get("firstName", "")).strip()
    given = str(ch.get("givenName", "")).strip()
    first_en = str(ch.get("firstNameEnglish", "")).strip()
    given_en = str(ch.get("givenNameEnglish", "")).strip()
    candidates = (first, given, (first + given).strip(), first_en, given_en, (first_en + given_en).strip())
    if any(c and c in name for c in candidates):
        return True
    nickname = common.character_nickname(ch.get("id", 0))
    return bool(nickname) and nickname.lower() in name.lower()


@cache
def _birthday_assets_for_group(group_name: str) -> tuple[str, str] | None:
    """provider/local_honors.go:190-216 — honor_bg/frame_birthday_01_{cid:02d}."""
    for ch in common.MD.get("gameCharacters"):
        if ch.get("id", 0) <= 0:
            continue
        if _birthday_group_matches_character(group_name, ch):
            suffix = f"01_{ch['id']:02d}"
            return f"honor_bg_birthday_{suffix}", f"honor_frame_birthday_{suffix}"
    return None


def _honor_group(group_id: int) -> dict | None:
    """provider/local_honors.go:159-188 — birthday groups get derived bg/frame names."""
    raw = _honor_group_raw_by_id().get(group_id)
    if raw is None:
        return None
    group = dict(raw)
    if group.get("honorType") == "birthday" and ("backgroundAssetbundleName" not in group or "frameName" not in group):
        derived = _birthday_assets_for_group(group.get("name", ""))
        if derived:
            bg, frame = derived
            if "backgroundAssetbundleName" not in group and bg:
                group["backgroundAssetbundleName"] = bg
            if "frameName" not in group and frame:
                group["frameName"] = frame
    return group


def _resolve_level_visual(levels: list[dict], requested: int) -> dict | None:
    """honor/builder_normal.go:187-216 resolveHonorLevelVisual."""
    best_at_or_below: dict | None = None
    first_usable: dict | None = None
    for level in levels:
        if not level.get("assetbundleName") and not level.get("honorRarity"):
            continue
        if first_usable is None:
            first_usable = level
        if level.get("level") == requested:
            return level
        if requested > 0 and level.get("level", 0) <= requested:
            if best_at_or_below is None or level.get("level", 0) > best_at_or_below.get("level", 0):
                best_at_or_below = level
    return best_at_or_below or first_usable


def _derive_honor_bg_asset_name(asset_name: str) -> str:
    """honor/builder_normal.go:268-278 — honor_top_x_y_suffix -> honor_bg_suffix."""
    asset_name = asset_name.strip()
    if not asset_name.startswith("honor_top_"):
        return ""
    parts = asset_name.split("_", 3)
    if len(parts) != 4:
        return ""
    return "honor_bg_" + parts[3]


def _build_normal_honor(req: dict, honor_id: int, honor_level: int, fc_ap_override: int | None) -> dict | None:
    """honor/builder_normal.go:15-185."""
    info = _honor_by_id()[honor_id]
    group = _honor_group(info.get("groupId", 0))
    if group is None:
        return None

    asset_name = info.get("assetbundleName", "")
    rarity = info.get("honorRarity", "")
    resolved = _resolve_level_visual(info.get("levels") or [], honor_level)
    if resolved is not None:
        if not asset_name and resolved.get("assetbundleName"):
            asset_name = resolved["assetbundleName"]
        if not rarity and resolved.get("honorRarity"):
            rarity = resolved["honorRarity"]
        if honor_level <= 0:
            honor_level = resolved.get("level", 0)
    req["honor_level"] = honor_level

    group_bg = group.get("backgroundAssetbundleName") or ""
    bg_asset_name = group_bg or asset_name
    group_type = group.get("honorType", "")
    if group_type == "world_link" or "event_wl" in bg_asset_name.strip() or "event_wl" in asset_name.strip():
        group_type = "wl_event"
    req["group_type"] = group_type
    req["honor_rarity"] = rarity
    mode = "main" if req["is_main_honor"] else "sub"
    ra = common.ASSETS.region_asset

    if group_type == "rank_match":
        honor_img = ra(f"rank_live/honor/{bg_asset_name}/degree_{mode}.png")
    elif group_bg:
        honor_img = ra(f"honor/{group_bg}/degree_{mode}.png")
    else:
        honor_img = ra(f"honor/{asset_name}/degree_{mode}.png")
    if group_type in ("event", "wl_event") and not _exists(honor_img):
        derived = _derive_honor_bg_asset_name(asset_name)
        if derived:
            candidate = ra(f"honor/{derived}/degree_{mode}.png")
            if _exists(candidate):
                honor_img = candidate
    if group_type in ("event", "wl_event") and not _exists(honor_img):
        fallback = ra(f"honor/{group_bg or asset_name}/rank_{mode}.png")
        if _exists(fallback):
            honor_img = fallback
    req["honor_img_path"] = honor_img

    if asset_name and group_type in ("event", "wl_event", "rank_match", "sekai_echo"):
        if group_type == "rank_match":
            req["rank_img_path"] = ra(f"rank_live/honor/{asset_name}/{mode}.png")
        elif group_type == "sekai_echo":
            candidate = ra(f"honor/{asset_name}/rank_{mode}.png")
            if _exists(candidate):
                req["rank_img_path"] = candidate
        else:  # event / wl_event
            candidate = ra(f"honor/{asset_name}/rank_{mode}.png")
            if candidate != honor_img:
                req["rank_img_path"] = candidate

    frame_name = group.get("frameName") or ""
    honor_type = "normal"
    if (
        group_type == "birthday"
        or frame_name.startswith("honor_frame_birthday")
        or bg_asset_name.startswith("honor_bg_birthday")
        or asset_name.startswith("honor_bg_birthday")
    ):
        honor_type = "birthday"
    req["honor_type"] = honor_type
    rarity_rank = _RARITY_RANK.get(rarity, 1)

    if not (honor_type == "birthday" and rarity_rank <= 1):  # rank-1 birthday: no frame overlays
        if honor_type == "birthday" and not frame_name:
            if bg_asset_name.startswith("honor_bg_birthday_"):
                frame_name = "honor_frame_birthday_" + bg_asset_name.removeprefix("honor_bg_birthday_")
            elif asset_name.startswith("honor_bg_birthday_"):
                frame_name = "honor_frame_birthday_" + asset_name.removeprefix("honor_bg_birthday_")
        if frame_name:
            is_birthday_frame = frame_name.startswith("honor_frame_birthday")
            start_rare = 3 if frame_name.startswith("event") else 2
            frame_path = ra(f"honor_frame/{frame_name}/frame_degree_{mode[0]}_{rarity_rank}.png")
            if (is_birthday_frame or rarity_rank >= start_rare) and _exists(frame_path):
                req["frame_img_path"] = frame_path
            else:
                req["frame_img_path"] = common.ASSETS.static(f"honor/frame_degree_{mode[0]}_{rarity_rank}.png")
            if is_birthday_frame and req["frame_img_path"] == frame_path:
                level_path = ra(f"honor_frame/{frame_name}/frame_degree_level_{rarity_rank}.png")
                if _exists(level_path):
                    req["frame_degree_level_img_path"] = level_path
        else:
            req["frame_img_path"] = common.ASSETS.static(f"honor/frame_degree_{mode[0]}_{rarity_rank}.png")

    has_score = honor_id in _DIFF_SCORE
    if has_score or group_type in ("event", "wl_event"):
        if has_score:
            req["group_type"] = "fc_ap"
        scroll_path = ra(f"honor/{asset_name}/scroll.png")
        if _exists(scroll_path):
            req["scroll_img_path"] = scroll_path
        req["fc_or_ap_level"] = str(honor_level if fc_ap_override is None else fc_ap_override)

    if group_type in ("character", "achievement") or req["group_type"].startswith("fc_ap"):
        req["lv_img_path"] = common.ASSETS.static("honor/icon_degreeLv.png")
        req["lv6_img_path"] = common.ASSETS.static("honor/icon_degreeLv6.png")
    return req


def _resolve_unit_virtual_singer_unit_id(candidate_uid: int, paired_uid: int) -> int:
    """honor/builder_bonds.go:101-117."""
    gcu = _gcu_by_id()
    candidate = gcu.get(candidate_uid)
    if not candidate or candidate.get("gameCharacterId", 0) < 21:
        return candidate_uid
    paired = gcu.get(paired_uid)
    if not paired or not str(paired.get("unit", "")).strip() or paired.get("unit") == "piapro":
        return candidate_uid
    for unit_id in range(27, 57):
        unit = gcu.get(unit_id)
        if (
            unit
            and unit.get("gameCharacterId") == candidate.get("gameCharacterId")
            and unit.get("unit") == paired.get("unit")
        ):
            return unit_id
    return candidate_uid


def _build_bonds_honor(req: dict, bonds: dict, honor_level: int, view_type: str, word_id: int) -> dict:
    """honor/builder_bonds.go:15-86."""
    req["honor_type"] = "bonds"
    req["honor_rarity"] = bonds.get("honorRarity", "")
    req["honor_level"] = honor_level
    mode = "main" if req["is_main_honor"] else "sub"
    bg_suffix = "" if req["is_main_honor"] else "_sub"
    ra = common.ASSETS.region_asset

    gcu = _gcu_by_id()
    cuid1 = bonds.get("gameCharacterUnitId1", 0)
    cuid2 = bonds.get("gameCharacterUnitId2", 0)
    cid1 = gcu.get(cuid1, {}).get("gameCharacterId", 0)
    cid2 = gcu.get(cuid2, {}).get("gameCharacterId", 0)

    slots = [[cuid1, cid1], [cuid2, cid2]]  # (unitId, characterId)
    view = view_type.strip().lower()
    if bonds.get("configurableUnitVirtualSinger") and "unit_virtual_singer" in view:
        slots[0][0] = _resolve_unit_virtual_singer_unit_id(cuid1, cuid2)
        slots[1][0] = _resolve_unit_virtual_singer_unit_id(cuid2, cuid1)
    if view.startswith("reverse"):
        slots[0], slots[1] = slots[1], slots[0]

    req["bonds_bg_path"] = common.ASSETS.static(f"honor/bonds/{slots[0][1]}{bg_suffix}.png")
    req["bonds_bg_path2"] = common.ASSETS.static(f"honor/bonds/{slots[1][1]}{bg_suffix}.png")
    req["chara_icon_path"] = ra(f"bonds_honor/character/chr_sd_{slots[0][0]:02d}_01.png")
    req["chara_icon_path2"] = ra(f"bonds_honor/character/chr_sd_{slots[1][0]:02d}_01.png")
    req["chara_id"] = str(slots[0][0])
    req["chara_id2"] = str(slots[1][0])

    req["mask_img_path"] = common.ASSETS.static(f"honor/mask_degree_{mode}.png")
    req["frame_img_path"] = common.ASSETS.static(
        f"honor/frame_degree_{mode[0]}_{_RARITY_RANK.get(bonds.get('honorRarity', ''), 1)}.png"
    )

    if req["is_main_honor"]:
        wid = word_id or bonds["id"]
        word = _bonds_word_by_id().get(wid)
        if word and str(word.get("assetbundleName", "")).strip():
            tier = max(1, bonds["id"] % 100)
            bundle = f"{str(word['assetbundleName']).strip()}_{tier:02d}"
        elif abs(bonds["id"] - wid) < 100:
            bundle = f"honorname_{cid1:02d}{cid2:02d}_{wid % 100:02d}_01"
        elif wid % 10 == 1:
            bundle = f"honorname_{cid1:02d}{cid2:02d}_default_{cid1:02d}{cid2:02d}_01"
        else:
            bundle = f"honorname_{cid1:02d}{cid2:02d}_default_{cid2:02d}{cid1:02d}_01"
        req["word_img_path"] = ra(f"bonds_honor/word/{bundle}.png")

    req["lv_img_path"] = common.ASSETS.static("honor/icon_degreeLv.png")
    req["lv6_img_path"] = common.ASSETS.static("honor/icon_degreeLv6.png")
    return req


def build_honor_request(
    honor_id: int,
    honor_level: int,
    *,
    is_main: bool = False,
    view_type: str = "",
    word_id: int = 0,
    fc_ap_override: int | None = None,
) -> dict | None:
    """honor/builder.go:17-44 — normal wins when the id exists in both tables."""
    req: dict[str, Any] = {"is_empty": False, "is_main_honor": is_main}
    if honor_id in _honor_by_id():
        return _build_normal_honor(req, honor_id, honor_level, fc_ap_override)
    bonds = _bonds_honor_by_id().get(honor_id)
    if bonds is not None:
        return _build_bonds_honor(req, bonds, honor_level, view_type, word_id)
    return None


# ---------------------------------------------------------------------------
# Profile builder (render/profile/controller_snapshot.go + controller_helpers.go)
# ---------------------------------------------------------------------------


def _find_active_deck(suite: dict) -> dict | None:
    """snapshot/local_helpers.go:244-254 FindActiveDeck."""
    decks = suite.get("userDecks") or []
    active_id = suite.get("userGamedata", {}).get("deck")
    for deck in decks:
        if deck.get("deckId") == active_id:
            return deck
    return decks[0] if decks else None


def _find_user_card(suite: dict, card_id: int) -> dict | None:
    return next((c for c in suite.get("userCards") or [] if c.get("cardId") == card_id), None)


def _card_uses_trained_art(user_card: dict | None) -> bool:
    """live_adapter.go:295-300 (EqualFold special_training)."""
    return bool(user_card) and str(user_card.get("defaultImage", "")).strip().lower() == "special_training"


def _leader_image_path(suite: dict) -> str:
    """snapshot/factory.go leader thumbnail via cards.json (production-equivalent lookup)."""
    deck = _find_active_deck(suite)
    leader_id = (deck or {}).get("leader", 0)
    card = common.MD.card_by_id().get(leader_id)
    if not card or not str(card.get("assetbundleName", "")).strip():
        return common.ASSETS.static("unknown.jpg")
    suffix = "after_training" if _card_uses_trained_art(_find_user_card(suite, leader_id)) else "normal"
    return common.ASSETS.region_asset(f"thumbnail/chara/{card['assetbundleName']}_{suffix}.png")


def _build_frame_paths(suite: dict) -> dict | None:
    """controller_helpers.go:166-196 (fixture has no equipped frame; paths are literal joins in Go)."""
    equipped_id = 0
    for item in suite.get("userPlayerFrames") or []:
        if str(item.get("playerFrameAttachStatus", "")).strip().lower() == "equipped":
            equipped_id = item.get("playerFrameId", 0)
            break
    if not equipped_id:
        return None
    frame = next((f for f in common.MD.get("playerFrames") if f["id"] == equipped_id), None)
    if frame is None:
        return None
    group = next((g for g in common.MD.get("playerFrameGroups") if g["id"] == frame.get("playerFrameGroupId")), None)
    if group is None or not str(group.get("assetbundleName", "")).strip():
        return None
    base = f"player_frame/{group['assetbundleName']}/{equipped_id}"
    return {
        "base": f"{base}/horizontal/frame_base.png",
        "centertop": f"{base}/vertical/frame_centertop.png",
        "leftbottom": f"{base}/vertical/frame_leftbottom.png",
        "lefttop": f"{base}/horizontal/frame_lefttop.png",
        "rightbottom": f"{base}/horizontal/frame_rightbottom.png",
        "righttop": f"{base}/horizontal/frame_righttop.png",
    }


def _build_pcards(suite: dict) -> list[dict]:
    """controller_helpers.go:198-232 — member1..member5 of the active deck."""
    deck = _find_active_deck(suite) or {}
    pcards: list[dict] = []
    for slot in ("member1", "member2", "member3", "member4", "member5"):
        card_id = deck.get(slot, 0)
        if not card_id:
            continue
        card = common.MD.card_by_id().get(card_id)
        if not card or not str(card.get("assetbundleName", "")).strip():
            continue
        user_card = _find_user_card(suite, card_id)
        after = _card_uses_trained_art(user_card)
        training_done = bool(user_card) and str(user_card.get("specialTrainingStatus", "")).strip().lower() == "done"
        pcards.append(
            common.card_thumbnail(
                card,
                thumb_after=after,
                star_after=True if training_done else after,
                train_rank=(user_card or {}).get("masterRank", 0) or 0,
                level=user_card.get("level", 0) if user_card else None,
                is_pcard=True,
            )
        )
    return pcards


def _build_music_counts(suite: dict) -> list[dict]:
    """controller_helpers.go:329-371 — fixture lacks clear counts, so aggregate userMusicResults."""
    clears = suite.get("userMusicDifficultyClearCounts") or []
    result: list[dict] = []
    if clears:
        for difficulty in _DIFFICULTIES:
            entry = {"difficulty": difficulty, "clear": 0, "fc": 0, "ap": 0}
            for item in clears:
                if str(item.get("musicDifficultyType", "")).lower() == difficulty:
                    entry["clear"] = item.get("liveClear", 0)
                    entry["fc"] = item.get("fullCombo", 0)
                    entry["ap"] = item.get("allPerfect", 0)
                    break
            result.append(entry)
        return result

    stats = suite.get("userMusicResults") or []
    for difficulty in _DIFFICULTIES:
        seen: set[int] = set()
        clear = fc = ap = 0
        for item in stats:  # first-win per musicId, array order (order-sensitive)
            if str(item.get("musicDifficultyType", "")).lower() != difficulty:
                continue
            music_id = item.get("musicId")
            if music_id in seen:
                continue
            seen.add(music_id)
            clear += 1  # unconditional, playResult is not consulted
            if item.get("fullComboFlg"):
                fc += 1
            if item.get("fullPerfectFlg"):
                ap += 1
        result.append({"difficulty": difficulty, "clear": clear, "fc": fc, "ap": ap})
    return result


def _build_fc_ap_levels(music_counts: list[dict]) -> dict[int, int]:
    """controller_helpers.go:286-327."""
    if not music_counts:
        return {}
    by_difficulty = {c["difficulty"]: c for c in music_counts}
    overrides: dict[int, int] = {}
    for honor_id, (difficulty, key) in _DIFF_SCORE.items():
        count = by_difficulty.get(difficulty)
        if count is not None:
            overrides[honor_id] = count[key]
    return overrides


def _build_profile_honors(suite: dict, fc_ap: dict[int, int]) -> list[dict]:
    """controller_helpers.go:234-284 — fixture has empty userProfileHonors, falls back to userHonors."""
    selected = [h for h in suite.get("userProfileHonors") or [] if h.get("honorId", 0) > 0 or h.get("honorId2", 0) > 0]
    selected.sort(key=lambda h: h.get("seq", 0))
    requests: list[dict] = []
    for item in selected:
        honor_id = item.get("honorId", 0) or item.get("honorId2", 0)
        req = build_honor_request(
            honor_id,
            item.get("honorLevel", 0),
            is_main=item.get("seq") == 1,
            view_type=str(item.get("bondsHonorViewType", "") or ""),
            word_id=item.get("bondsHonorWordId", 0) or 0,
            fc_ap_override=fc_ap.get(honor_id),
        )
        if req:
            requests.append(req)
    if requests:
        return requests

    for item in suite.get("userHonors") or []:  # array order, first 3 buildable
        if len(requests) >= 3:
            break
        honor_id = item.get("honorId", 0)
        req = build_honor_request(
            honor_id, item.get("level", 0), is_main=not requests, fc_ap_override=fc_ap.get(honor_id)
        )
        if req:
            requests.append(req)
    return requests


def _build_solo_live(suite: dict) -> dict | None:
    """controller_helpers.go:384-402."""
    results = suite.get("userChallengeLiveSoloResults") or []
    if not results:
        return None
    top = sorted(results, key=lambda r: r.get("highScore", 0), reverse=True)[0]
    rank = 1
    for stage in suite.get("userChallengeLiveSoloStages") or []:
        if stage.get("characterId") == top.get("characterId") and stage.get("rank", 0) > rank:
            rank = stage.get("rank", 0)
    return {"character_id": top.get("characterId", 0), "score": top.get("highScore", 0), "rank": rank}


def _build_multi_live(suite: dict) -> dict | None:
    """controller_helpers.go:404-412 — fixture lacks the key, so this yields None."""
    count = suite.get("userMultiLiveTopScoreCount") or {}
    mvp = count.get("mvp", 0)
    super_star = count.get("superStar", 0)
    if mvp <= 0 and super_star <= 0:
        return None
    return {"mvp": mvp, "super_star": super_star}


def build_profile_body() -> dict:
    suite = common.load_suite()
    gamedata = suite.get("userGamedata", {})
    user_profile = suite.get("userProfile") or {}
    frame_paths = _build_frame_paths(suite)
    music_counts = _build_music_counts(suite)
    fc_ap = _build_fc_ap_levels(music_counts)

    profile: dict[str, Any] = {
        "id": str(gamedata.get("userId", "")),
        "region": common.REGION.upper(),
        "nickname": gamedata.get("name", ""),  # color tags kept (factory.go:102-104)
        "is_hide_uid": True,  # snapshot baseProfile is always hidden (factory.go:109)
        "leader_image_path": _leader_image_path(suite),
        "has_frame": frame_paths is not None,
    }
    if frame_paths:
        profile["frame_path"] = frame_paths["base"]

    body: dict[str, Any] = {
        "profile": profile,
        "rank": gamedata.get("rank", 0),
        "twitter_id": user_profile.get("twitterId", ""),
        "word": _WORD_TAG.sub("", user_profile.get("word", "")),
        "pcards": _build_pcards(suite),
        "bg_settings": {"blur": 4, "alpha": 100, "vertical": False},  # controller_helpers.go:67-69
        "honors": _build_profile_honors(suite, fc_ap),
        "music_difficulty_count": music_counts,
        "character_rank": [
            {"character_id": c.get("characterId", 0), "rank": c.get("characterRank", 0)}
            for c in suite.get("userCharacters") or []
        ],
        "update_time": int(suite.get("now", 0)),
        "lv_rank_bg_path": common.ASSETS.static("lv_rank_bg.png"),
        "x_icon_path": common.ASSETS.static("x_icon.png"),
        "icon_clear_path": common.ASSETS.static("icon_clear.png"),
        "icon_fc_path": common.ASSETS.static("icon_fc.png"),
        "icon_ap_path": common.ASSETS.static("icon_ap.png"),
        "chara_rank_icon_path_map": {
            str(cid): common.ASSETS.static(f"chara_rank_icon/{common.character_nickname(cid)}.png")
            for cid in range(1, 27)
        },
    }
    solo_live = _build_solo_live(suite)
    if solo_live:
        body["solo_live"] = solo_live
    multi_live = _build_multi_live(suite)
    if multi_live:
        body["multi_live"] = multi_live
    if frame_paths:
        body["frame_paths"] = frame_paths
    return body


def build_honor_body() -> dict:
    """Standalone /api/pjsk/honor payload: the fixture user's main honor (first buildable userHonors entry)."""
    suite = common.load_suite()
    fc_ap = _build_fc_ap_levels(_build_music_counts(suite))
    for item in suite.get("userHonors") or []:
        honor_id = item.get("honorId", 0)
        req = build_honor_request(honor_id, item.get("level", 0), is_main=True, fc_ap_override=fc_ap.get(honor_id))
        if req:
            return req
    raise RuntimeError("no buildable honor found in suite snapshot")


# ---------------------------------------------------------------------------
# Inventory builder (render/inventory/controller.go + categories.go)
# ---------------------------------------------------------------------------

# categories.go:10-22
_SECTION_ORDER = [
    ("currency", "货币"),
    ("boost", "演出能量"),
    ("basic", "基础材料"),
    ("training", "育成材料"),
    ("costume", "服装材料"),
    ("music", "音乐与演唱"),
    ("tickets", "招募与兑换券"),
    ("event", "活动材料"),
    ("memory", "记忆"),
    ("mysekai", "MySekai 材料"),
    ("other", "其他"),
]


def _category_for_material(material_type: str, name: str) -> str:
    """categories.go:24-63."""
    typ = (material_type or "").strip().lower()
    lower_name = (name or "").strip().lower()
    if typ in ("coin", "jewel", "virtual_coin"):
        return "currency"
    if "boost" in typ:
        return "boost"
    if "costume" in typ:
        return "costume"
    if "music" in typ or "vocal" in typ or "song" in typ:
        return "music"
    if "ticket" in typ or "券" in lower_name or "ticket" in lower_name:
        return "tickets"
    if "event" in typ or "活动" in lower_name or "交换所" in lower_name:
        return "event"
    if (
        "special_training" in typ
        or "master_lesson" in typ
        or "skill" in typ
        or "character_rank" in typ
        or "练习" in lower_name
        or "技能" in lower_name
        or "想法" in lower_name
    ):
        return "training"
    if typ == "" or "material" in typ or "piece" in typ or "gem" in typ:
        return "basic"
    return "other"


def _is_mysekai_memory(meta: dict) -> bool:
    """controller.go:526-535."""
    typ = str(meta.get("mysekaiMaterialType", "")).strip().lower()
    icon = str(meta.get("iconAssetbundleName", "")).strip().lower()
    name = str(meta.get("name", "")).strip()
    return typ == "game_character" or "memoria" in icon or "memory" in icon or "メモリア" in name or "记忆" in name


def _clean_description(description: str) -> str:
    """controller.go:537-539 — collapse all whitespace runs to single spaces."""
    return " ".join(str(description or "").split())


def _fallback_seq(seq: int, item_id: int) -> int:
    return seq if seq > 0 else item_id


def _icon_by_asset_name(resource_type: str, asset_name: str) -> str:
    """controller.go:357-384 (region==jp collapses resolveInventoryAssetPath to one resolution)."""
    asset_name = (asset_name or "").strip()
    if not asset_name:
        return ""
    if resource_type == "gacha_ticket":
        return common.ASSETS.region_asset(
            f"thumbnail/gacha_ticket/{asset_name}.png",
            f"thumbnail/material/{asset_name}.png",
            f"thumbnail/common_material/{asset_name}.png",
        )
    if resource_type == "gacha_ceil_item":
        return common.ASSETS.region_asset(
            f"thumbnail/gacha_item/{asset_name}.png",
            f"thumbnail/material/{asset_name}.png",
            f"thumbnail/common_material/{asset_name}.png",
        )
    if resource_type == "mysekai_material":
        return common.ASSETS.region_asset(f"mysekai/thumbnail/material/{asset_name}.png")
    return ""


def _inventory_item(
    *,
    item_id: int,
    name: str,
    description: str,
    category: str,
    resource_type: str,
    icon_path: str,
    quantity: int,
    seq: int,
    recovery_value: int | None = None,
) -> dict:
    item: dict[str, Any] = {
        "id": item_id,
        "name": name,
        "category": category,
        "resource_type": resource_type,
        "icon_path": icon_path,
        "quantity": quantity,
        "seq": seq,
    }
    if description:  # Go: json omitempty
        item["description"] = description
    if recovery_value is not None:
        item["recovery_value"] = recovery_value
    return item


def _build_inventory_items(suite: dict) -> list[dict]:
    """controller.go:82-296, in code order."""
    gamedata = suite.get("userGamedata", {})
    charged = suite.get("userChargedCurrency") or {}
    ra = common.ASSETS.region_asset
    items: list[dict] = []

    items.append(
        _inventory_item(
            item_id=0,
            name="金币",
            description="游戏内基础货币，可用于成员育成等消耗。",
            category="currency",
            resource_type="coin",
            icon_path=ra("thumbnail/common_material/coin.png"),
            quantity=gamedata.get("coin", 0),
            seq=0,
        )
    )
    if charged.get("free", 0) > 0:
        items.append(
            _inventory_item(
                item_id=-1,
                name="免费水晶",
                description="免费获得的水晶，可用于招募等用途。",
                category="currency",
                resource_type="jewel",
                icon_path=ra("thumbnail/common_material/jewel.png"),
                quantity=charged["free"],
                seq=1,
            )
        )
    if charged.get("paid", 0) > 0:
        items.append(
            _inventory_item(
                item_id=-2,
                name="付费水晶",
                description="购买获得的付费水晶，可用于招募等用途。",
                category="currency",
                resource_type="jewel",
                icon_path=ra("thumbnail/common_material/jewel.png"),
                quantity=charged["paid"],
                seq=2,
            )
        )
    if gamedata.get("virtualCoin", 0) > 0:
        items.append(
            _inventory_item(
                item_id=-3,
                name="虚拟币",
                description="虚拟演唱会等玩法中使用的货币。",
                category="currency",
                resource_type="virtual_coin",
                icon_path=ra("thumbnail/common_material/virtual_coin.png"),
                quantity=gamedata["virtualCoin"],
                seq=3,
            )
        )

    for mat in suite.get("userMaterials") or []:
        material_id, quantity = mat.get("materialId", 0), mat.get("quantity", 0)
        if material_id <= 0 or quantity <= 0:
            continue
        meta = _meta_by_id("materials").get(material_id, {})
        name = str(meta.get("name", "")).strip() or f"材料 {material_id}"
        items.append(
            _inventory_item(
                item_id=material_id,
                name=name,
                description=_clean_description(meta.get("flavorText", "")),
                category=_category_for_material(meta.get("materialType", ""), name),
                resource_type="material",
                icon_path=ra(f"thumbnail/material/material{material_id}.png"),
                quantity=quantity,
                seq=_fallback_seq(meta.get("seq", 0), material_id),
            )
        )

    for ticket in suite.get("userGachaTickets") or []:
        ticket_id, quantity = ticket.get("gachaTicketId", 0), ticket.get("quantity", 0)
        if ticket_id <= 0 or quantity <= 0:
            continue
        meta = _meta_by_id("gachaTickets").get(ticket_id, {})
        items.append(
            _inventory_item(
                item_id=ticket_id,
                name=str(meta.get("name", "")).strip() or f"招募券 {ticket_id}",
                description=_clean_description(meta.get("flavorText", "")),
                category="tickets",
                resource_type="gacha_ticket",
                icon_path=_icon_by_asset_name("gacha_ticket", meta.get("assetbundleName", "")),
                quantity=quantity,
                seq=_fallback_seq(meta.get("seq", 0), ticket_id),
            )
        )

    for ticket in suite.get("userPracticeTickets") or []:
        ticket_id, quantity = ticket.get("practiceTicketId", 0), ticket.get("quantity", 0)
        if ticket_id <= 0 or quantity <= 0:
            continue
        meta = _meta_by_id("practiceTickets").get(ticket_id, {})
        items.append(
            _inventory_item(
                item_id=ticket_id,
                name=str(meta.get("name", "")).strip() or f"练习乐谱 {ticket_id}",
                description=_clean_description(meta.get("flavorText", "")),
                category="training",
                resource_type="practice_ticket",
                icon_path=ra(f"thumbnail/practice_ticket/ticket{ticket_id}.png"),
                quantity=quantity,
                seq=_fallback_seq(meta.get("characterId", 0) * 1000 + meta.get("exp", 0), ticket_id),
            )
        )

    for ticket in suite.get("userSkillPracticeTickets") or []:
        ticket_id, quantity = ticket.get("skillPracticeTicketId", 0), ticket.get("quantity", 0)
        if ticket_id <= 0 or quantity <= 0:
            continue
        meta = _meta_by_id("skillPracticeTickets").get(ticket_id, {})
        items.append(
            _inventory_item(
                item_id=ticket_id,
                name=str(meta.get("name", "")).strip() or f"技能升级乐谱 {ticket_id}",
                description=_clean_description(meta.get("flavorText", "")),
                category="training",
                resource_type="skill_practice_ticket",
                icon_path=ra(f"thumbnail/skill_practice_ticket/ticket{ticket_id}.png"),
                quantity=quantity,
                seq=_fallback_seq(meta.get("characterId", 0) * 1000 + meta.get("exp", 0), ticket_id),
            )
        )

    for item in suite.get("userGachaCeilItems") or []:
        item_id, quantity = item.get("gachaCeilItemId", 0), item.get("quantity", 0)
        if item_id <= 0 or quantity <= 0:
            continue
        meta = _meta_by_id("gachaCeilItems").get(item_id, {})
        items.append(
            _inventory_item(
                item_id=item_id,
                name=str(meta.get("name", "")).strip() or f"招募贴纸 {item_id}",
                description=_clean_description(meta.get("flavorText", "")),
                category="tickets",
                resource_type="gacha_ceil_item",
                icon_path=_icon_by_asset_name("gacha_ceil_item", meta.get("assetbundleName", "")),
                quantity=quantity,
                seq=_fallback_seq(meta.get("seq", 0), item_id),
            )
        )

    for material in suite.get("userMysekaiMaterials") or []:
        material_id, quantity = material.get("mysekaiMaterialId", 0), material.get("quantity", 0)
        if material_id <= 0 or quantity <= 0:
            continue
        meta = _meta_by_id("mysekaiMaterials").get(material_id, {})
        items.append(
            _inventory_item(
                item_id=material_id,
                name=str(meta.get("name", "")).strip() or f"MySekai 材料 {material_id}",
                description=_clean_description(meta.get("description", "")),
                category="memory" if _is_mysekai_memory(meta) else "mysekai",
                resource_type="mysekai_material",
                icon_path=_icon_by_asset_name("mysekai_material", meta.get("iconAssetbundleName", "")),
                quantity=quantity,
                seq=_fallback_seq(meta.get("seq", 0), material_id),
            )
        )

    for boost in suite.get("userBoostItems") or []:
        boost_id, quantity = boost.get("boostItemId", 0), boost.get("quantity", 0)
        if boost_id <= 0 or quantity <= 0:
            continue
        meta = _meta_by_id("boostItems").get(boost_id, {})
        recovery = meta.get("recoveryValue", 0)
        items.append(
            _inventory_item(
                item_id=boost_id,
                name=str(meta.get("name", "")).strip() or f"演出能量道具 {boost_id}",
                description=_clean_description(meta.get("flavorText", "")),
                category="boost",
                resource_type="boost_item",
                icon_path=ra(f"thumbnail/boost_item/boost_item{boost_id}.png"),
                quantity=quantity,
                seq=_fallback_seq(meta.get("seq", 0), boost_id),
                recovery_value=recovery if recovery > 0 else None,
            )
        )
    return items


def _is_special_item(item: dict) -> bool:
    """controller.go:504-517 — excluded from the default view."""
    return item["resource_type"] in ("jewel", "boost_item") or item["category"] in ("mysekai", "memory")


def _build_sections(items: list[dict]) -> list[dict]:
    """controller.go:402-437."""
    grouped: dict[str, list[dict]] = {}
    for item in items:
        if item["quantity"] < 0:
            continue
        grouped.setdefault(item["category"].strip() or "other", []).append(item)
    sections: list[dict] = []
    for key, title in _SECTION_ORDER:
        group = grouped.get(key)
        if not group:
            continue
        group.sort(key=lambda i: (i["seq"], i["id"], i["name"]))  # stable
        sections.append({"key": key, "title": title, "items": group})
    return sections


def build_inventory_body() -> dict:
    """Default filter (the plain /背包一览 view): jewel/boost/mysekai/memory items are excluded."""
    suite = common.load_suite()
    items = [i for i in _build_inventory_items(suite) if not _is_special_item(i)]
    sections = _build_sections(items)
    if not sections:
        raise RuntimeError("user snapshot has no inventory data")
    return {
        "profile": common.build_user_info(is_hide_uid=True),
        "sections": sections,
        "total_items": sum(len(s["items"]) for s in sections),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate() -> list[str]:
    written: list[str] = []

    profile_body = build_profile_body()
    ProfileRequest.model_validate(profile_body)
    common.write_payload("profile", profile_body)
    written.append("profile")

    honor_body = build_honor_body()
    HonorRequest.model_validate(honor_body)
    common.write_payload("honor", honor_body)
    written.append("honor")

    inventory_body = build_inventory_body()
    InventoryListRequest.model_validate(inventory_body)
    common.write_payload("inventory_list", inventory_body)
    written.append("inventory_list")

    return written


if __name__ == "__main__":
    names = generate()
    print("written:", names)  # noqa: T201
    common.ASSETS.save_manifest()
    print("missing assets:", len(common.ASSETS.missing))  # noqa: T201
