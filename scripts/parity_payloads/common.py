"""Shared infrastructure for the real-payload generators (Skia parity sweep).

Replicates Haruki-Cloud's request-body construction conventions offline:
masterdata from ``out/haruki-sekai-master/master``, user data from
``collections.suite.json`` / ``collections.mysekai.json`` (Mongo extended-JSON
dumps), asset paths resolved the way ``internal/pjsk/render/assets`` does.

Per-domain generators live next to this module as ``gen_<domain>.py``; each
builds request bodies per the specs in ``out/payload-specs/<domain>.md`` and
writes them via :func:`write_payload`. Every asset path that enters a payload
must go through :class:`AssetResolver` so the rsync manifest stays complete.
"""

from __future__ import annotations

from functools import cache
import json
from pathlib import Path
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
MASTER_DIR = REPO_ROOT / "out" / "haruki-sekai-master" / "master"
SUITE_PATH = REPO_ROOT / "collections.suite.json"
MYSEKAI_PATH = REPO_ROOT / "collections.mysekai.json"
OUT_DIR = REPO_ROOT / "out" / "parity-payloads"

REGION = "jp"
TIMEZONE = "Asia/Shanghai"

# ---------------------------------------------------------------------------
# Mongo extended-JSON normalization (snapshot/normalize.go:12-80)
# ---------------------------------------------------------------------------

_EXT_KEYS = {"$numberLong", "$numberInt", "$numberDouble", "$oid", "$date"}


def normalize_extended_json(value: Any) -> Any:
    if isinstance(value, dict):
        if len(value) == 1:
            (k, v), = value.items()
            if k in ("$numberLong", "$numberInt"):
                return int(v)
            if k == "$numberDouble":
                return float(v)
            if k == "$oid":
                return str(v)
            if k == "$date":
                return normalize_extended_json(v)
        return {k: normalize_extended_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_extended_json(v) for v in value]
    return value


@cache
def load_suite() -> dict:
    """The single suite snapshot (normalized, top-level array unwrapped)."""
    raw = json.loads(SUITE_PATH.read_text())
    return normalize_extended_json(raw[0])


@cache
def load_mysekai() -> dict:
    raw = json.loads(MYSEKAI_PATH.read_text())
    return normalize_extended_json(raw[0])


# ---------------------------------------------------------------------------
# Masterdata (provider/local.go; files are plain camelCase JSON)
# ---------------------------------------------------------------------------


class Masterdata:
    def __init__(self, master_dir: Path = MASTER_DIR):
        self.dir = master_dir
        self._cache: dict[str, Any] = {}

    def get(self, name: str) -> Any:
        """Load ``<name>.json`` (cached)."""
        if name not in self._cache:
            self._cache[name] = json.loads((self.dir / f"{name}.json").read_text())
        return self._cache[name]

    @cache
    def cards_sorted(self) -> list[dict]:
        """cards.json sorted by (releaseAt, id) — Cloud sorts by releaseAt only with an
        unstable sort (local_cards.go:56-58); the id tiebreak makes it deterministic and
        matches the strict-filter ordering (visibility.go:35-42)."""
        return sorted(self.get("cards"), key=lambda c: (c.get("releaseAt", 0), c["id"]))

    @cache
    def card_by_id(self) -> dict[int, dict]:
        return {c["id"]: c for c in self.get("cards")}

    @cache
    def character_by_id(self) -> dict[int, dict]:
        return {c["id"]: c for c in self.get("gameCharacters")}

    @cache
    def card_supply_by_id(self) -> dict[int, str]:
        return {s["id"]: s.get("cardSupplyType", "") for s in self.get("cardSupplies")}

    @cache
    def event_by_id(self) -> dict[int, dict]:
        return {e["id"]: e for e in self.get("events")}

    @cache
    def event_id_by_card(self) -> dict[int, int]:
        """cardId -> first eventId occurrence (local_events.go:75-80)."""
        out: dict[int, int] = {}
        for ec in self.get("eventCards"):
            out.setdefault(ec["cardId"], ec["eventId"])
        return out

    @cache
    def character_color_code(self) -> dict[int, str]:
        """gameCharacterId -> first non-empty colorCode (local_characters.go:47-73)."""
        out: dict[int, str] = {}
        for u in self.get("gameCharacterUnits"):
            cc = u.get("colorCode", "")
            if cc and u["gameCharacterId"] not in out:
                out[u["gameCharacterId"]] = cc
        return out


MD = Masterdata()

# ---------------------------------------------------------------------------
# Asset path resolution (assets/helper.go) + rsync manifest collection
# ---------------------------------------------------------------------------

# Top-level dirs whose region assets prefer ondemand over startapp (helper.go:196-251).
_ONDEMAND_FIRST = {"event", "event_story", "gacha", "lottery_game", "mysekai", "unit_story", "virtual_live"}


class AssetResolver:
    def __init__(self, data_dir: Path = DATA_DIR, region: str = REGION):
        self.data_dir = data_dir
        self.region = region
        self.used: set[str] = set()        # paths that entered payloads
        self.missing: set[str] = set()     # used paths absent locally (rsync these)
        self.candidates: set[str] = set()  # all probe candidates for missing paths

    def _record(self, path: str, extra_candidates: list[str] | None = None) -> str:
        self.used.add(path)
        if not (self.data_dir / path).exists():
            self.missing.add(path)
            for c in extra_candidates or []:
                self.candidates.add(c)
        return path

    def static(self, rel: str) -> str:
        """static_images path (helper.go:172-189; production always sends the join)."""
        return self._record(f"static_images/{rel}")

    def region_asset(self, *rels: str, region: str | None = None) -> str:
        """Region asset with startapp/ondemand candidate ordering + local probing
        (helper.go:253-289). Falls back to the first candidate when nothing exists
        locally, recording every candidate for the rsync manifest."""
        r = region or self.region
        cands: list[str] = []
        for rel in rels:
            top = rel.split("/", 1)[0]
            modes = ("ondemand", "startapp") if top in _ONDEMAND_FIRST else ("startapp", "ondemand")
            for mode in modes:
                cands.append(f"asset/{r}-assets/{mode}/{rel}")
        for cand in cands:
            if (self.data_dir / cand).exists():
                return self._record(cand)
        return self._record(cands[0], extra_candidates=cands)

    def chara_icon(self, character_id: int) -> str:
        return self.static(f"chara_icon/{character_nickname(character_id)}.png")

    def save_manifest(self, out_dir: Path = OUT_DIR) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "assets-used.txt").write_text("\n".join(sorted(self.used)) + "\n")
        (out_dir / "assets-missing.txt").write_text(
            "\n".join(sorted(self.missing | self.candidates)) + "\n"
        )


ASSETS = AssetResolver()

# helper.go:316-343 + builder_helpers.go:231-248
_NICKNAMES = {
    1: "ick", 2: "saki", 3: "hnm", 4: "shiho", 5: "mnr", 6: "hrk", 7: "airi",
    8: "szk", 9: "khn", 10: "an", 11: "akt", 12: "toya", 13: "tks", 14: "emu",
    15: "nene", 16: "rui", 17: "knd", 18: "mfy", 19: "ena", 20: "mzk",
    21: "miku", 22: "rin", 23: "len", 24: "luka", 25: "meiko", 26: "kaito",
    27: "miku_light_sound", 28: "miku_idol", 29: "miku_street",
    30: "miku_theme_park", 31: "miku_school_refusal",
}


def character_nickname(character_id: int) -> str:
    return _NICKNAMES.get(character_id, f"chr_icon_{character_id}")


UNIT_ICONS = {
    "light_sound": "icon_light_sound.png",
    "idol": "icon_idol.png",
    "street": "icon_street.png",
    "theme_park": "icon_theme_park.png",
    "school_refusal": "icon_school_refusal.png",
    "piapro": "icon_piapro.png",
}

# ---------------------------------------------------------------------------
# Supply type (db_cards_supply.go:114-141 + local_cards.go:327-357 + supply.go:26-54)
# ---------------------------------------------------------------------------

_SUPPLY_LABELS = {
    "term_limited": "期间限定",
    "colorful_festival_limited": "CFes限定",
    "bloom_festival_limited": "BFes限定",
    "unit_event_limited": "WL限定",
    "collaboration_limited": "联动限定",
    "birthday": "生日",
}


def normalize_supply_type(raw: str) -> str:
    s = (raw or "").strip()
    if s in ("", "normal", "not_limited"):
        return "normal"
    if s in ("festival_limited", "colorful_festival_limited"):
        return "colorful_festival_limited"
    if s in ("birthday", "rarity_birthday"):
        return "birthday"
    return s


@cache
def _wl3_event_ids() -> set[int]:
    """world_bloom events with unit == none (db_cards_supply.go:135-141)."""
    return {
        e["id"]
        for e in MD.get("events")
        if str(e.get("eventType", "")).lower() == "world_bloom"
        and str(e.get("unit", "none")).lower() == "none"
    }


def card_supply_type(card: dict) -> str:
    """Normalized supply for a card, incl. the WL3 term_limited special case
    (local_cards.go:327-357)."""
    if card.get("cardRarityType") == "rarity_birthday":
        return "birthday"
    supply_id = card.get("cardSupplyId", 0)
    supply = "normal" if not supply_id else normalize_supply_type(MD.card_supply_by_id().get(supply_id, ""))
    if supply == "term_limited":
        event_id = MD.event_id_by_card().get(card["id"])
        if event_id in _wl3_event_ids():
            return "unit_event_limited"
    return supply


def supply_label_for_list(supply: str) -> str:
    """normal -> "" (caller omits the field)."""
    return "" if supply == "normal" else _SUPPLY_LABELS.get(supply, supply)


def supply_label_for_detail(supply: str) -> str:
    return "常驻" if supply == "normal" else _SUPPLY_LABELS.get(supply, supply)


# ---------------------------------------------------------------------------
# Card thumbnail (common/card_thumbnail.go:58-105 -> CardFullThumbnailRequest)
# ---------------------------------------------------------------------------


def card_thumbnail(
    card: dict,
    *,
    thumb_after: bool,
    star_after: bool | None = None,
    train_rank: int = 0,
    level: int | None = None,
    is_pcard: bool = False,
    custom_text: str | None = None,
) -> dict:
    rare = card["cardRarityType"]
    star_after = thumb_after if star_after is None else star_after
    suffix = "after_training" if thumb_after else "normal"
    thumb: dict[str, Any] = {
        "card_id": card["id"],
        "card_thumbnail_path": ASSETS.region_asset(
            f"thumbnail/chara/{card['assetbundleName']}_{suffix}.png"
        ),
        "rare": rare,
        "frame_img_path": ASSETS.static(f"card/frame_{rare}.png"),
        "attr_img_path": ASSETS.static(f"card/attr_{card['attr'].lower()}.png"),
        "rare_img_path": (
            ASSETS.static("card/rare_birthday.png")
            if rare == "rarity_birthday"
            else ASSETS.static(f"card/rare_star_{'after_training' if star_after else 'normal'}.png")
        ),
        "train_rank": train_rank,
        "is_after_training": thumb_after,
        "is_pcard": is_pcard,
    }
    if rare == "rarity_birthday":
        thumb["birthday_icon_path"] = ASSETS.static("card/rare_birthday.png")
    if train_rank > 0:
        thumb["train_rank_img_path"] = ASSETS.static(f"card/train_rank_{train_rank}.png")
    if level is not None:
        thumb["level"] = level
    if custom_text is not None:
        thumb["custom_text"] = custom_text
    return thumb


# ---------------------------------------------------------------------------
# user_info / DetailedProfileCardRequest (snapshot/factory.go:74-141)
# ---------------------------------------------------------------------------


def build_user_info(*, is_hide_uid: bool = False) -> dict:
    suite = load_suite()
    gamedata = suite.get("userGamedata", {})
    info: dict[str, Any] = {
        "id": str(gamedata.get("userId", "")),
        "nickname": gamedata.get("name", ""),
        "region": REGION.upper(),
        "source": "suite_dump",
        "update_time": int(suite.get("now", 0)),
        "is_hide_uid": is_hide_uid,
        "has_frame": False,
        "leader_image_path": _leader_image_path(suite),
        "user_cards": _suite_user_cards(suite),
    }
    mode = (suite.get("userProfile") or {}).get("profileImageType")
    if mode:
        info["mode"] = mode
    return info


def _leader_image_path(suite: dict) -> str:
    decks = suite.get("userDecks") or []
    current = next(
        (d for d in decks if d.get("deckId") == suite.get("userGamedata", {}).get("deck")),
        decks[0] if decks else None,
    )
    if not current:
        return ASSETS.static("unknown.jpg")
    leader_id = current.get("leader")
    user_card = next((c for c in suite.get("userCards", []) if c.get("cardId") == leader_id), None)
    card = MD.card_by_id().get(leader_id)
    if not card:
        return ASSETS.static("unknown.jpg")
    after = bool(user_card and user_card.get("defaultImage") == "special_training")
    suffix = "after_training" if after else "normal"
    return ASSETS.region_asset(f"thumbnail/chara/{card['assetbundleName']}_{suffix}.png")


def _suite_user_cards(suite: dict) -> list[dict]:
    """Deduped by cardId keeping the first, camelCase keys (local_helpers.go:191-215)."""
    seen: set[int] = set()
    out: list[dict] = []
    for c in suite.get("userCards", []):
        cid = c.get("cardId")
        if cid in seen:
            continue
        seen.add(cid)
        entry = {
            "cardId": cid,
            "level": c.get("level", 0),
            "masterRank": c.get("masterRank", 0),
            "defaultImage": c.get("defaultImage", ""),
            "specialTrainingStatus": c.get("specialTrainingStatus", ""),
        }
        if c.get("skillLevel", 0) > 0:
            entry["skillLevel"] = c["skillLevel"]
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Payload finalization (request_dt.go:14-105)
# ---------------------------------------------------------------------------


def now_ms() -> int:
    return int(time.time() * 1000)


def finalize(body: dict) -> dict:
    body.setdefault("timezone", TIMEZONE)
    body.setdefault("dt", now_ms())
    user_info = body.get("user_info")
    if isinstance(user_info, dict):
        ut = user_info.get("update_time", 0) or 0
        if ut <= 0:
            user_info["update_time"] = now_ms()
        elif ut < 1e11:  # seconds -> ms
            user_info["update_time"] = int(ut * 1000)
    return body


def write_payload(name: str, body: dict) -> Path:
    """Finalize and write ``out/parity-payloads/<name>.json`` (name: e.g. card_list)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(finalize(body), ensure_ascii=False, indent=1))
    return path
