"""Real-payload generator for the card domain (card/detail, card/list, card/box).

Replicates Haruki-Cloud's request construction offline per
``out/payload-specs/card-common.md``. Go references below are relative to
/Users/seiun/GolandProjects/Haruki-Cloud/.
"""

from pathlib import Path
import re
import sys
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import common

from src.sekai.card.model import CardBoxRequest, CardDetailRequest, CardListRequest

MD = common.MD
ASSETS = common.ASSETS

# Rich rarity_4 card: BFes-limited supply, event with unique bonus unit (banner_cid),
# gacha pickup, costumes and a special training skill (special_skill_info).
DETAIL_CARD_ID = 1361

# ---------------------------------------------------------------------------
# skills.json rendering (provider/db_skills.go:65-217, local_skills.go:52-101)
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}")

_skill_index: dict[int, dict] | None = None


def skill_by_id() -> dict[int, dict]:
    global _skill_index
    if _skill_index is None:
        _skill_index = {s["id"]: s for s in MD.get("skills")}
    return _skill_index


def _effect_values(effect: dict) -> list[int]:
    details = effect.get("skillEffectDetails") or []
    if not details:
        return [0]
    return [int(d.get("activateEffectValue", 0)) for d in details]


def _format_effect_values(values: list[int]) -> str:
    if not values:
        return ""
    if all(v == values[0] for v in values):
        return str(values[0])
    seen: set[int] = set()
    unique: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        unique.append(str(v))
    return "/".join(unique)


def _enhanced_values(effect: dict, base: list[int]) -> list[int]:
    out: list[int] = []
    for idx, detail in enumerate(effect.get("skillEffectDetails") or []):
        if detail.get("activateEffectValue2") is not None:
            out.append(int(detail["activateEffectValue2"]))
        elif idx < len(base):
            out.append(base[idx])
    return out


def _skill_enhance_value(effect: dict) -> int:
    return int((effect.get("skillEnhance") or {}).get("activateEffectValue", 0))


def _format_single_effect(effect: dict, mode: str) -> str:
    if mode == "d":
        details = effect.get("skillEffectDetails") or []
        if details:
            return f"{float(details[0].get('activateEffectDuration', 0)):.1f}"
        return "0.0"
    if mode == "v":
        return _format_effect_values(_effect_values(effect))
    if mode == "e":
        return str(_skill_enhance_value(effect))
    if mode == "m":
        enhance = _skill_enhance_value(effect)
        return _format_effect_values([v + enhance * 5 for v in _effect_values(effect)])
    return "?"


def _format_dual_effects(e1: dict, e2: dict, mode: str) -> str:
    values1, values2 = _effect_values(e1), _effect_values(e2)
    if mode == "v":
        return _format_effect_values([a + b for a, b in zip(values1, values2)])
    if mode in ("u", "o"):
        enhanced1 = _enhanced_values(e1, values1)
        enhanced2 = _enhanced_values(e2, values2)
        return _format_effect_values([a + b for a, b in zip(enhanced1, enhanced2)])
    if mode in ("r", "s"):
        return "..."
    return "?"


def render_skill_detail(skill: dict, card_character_id: int) -> str:
    """JP-only line of the {{ids;mode}} template (FormatDescription)."""
    effects_by_id = {e["id"]: e for e in skill.get("skillEffects") or []}

    def repl(match: re.Match) -> str:
        parts = match.group(1).split(";")
        if len(parts) != 2:
            return match.group(0)
        ids: list[int] = []
        for raw in parts[0].split(","):
            try:
                ids.append(int(raw.strip()))
            except ValueError:
                continue
        if not ids:
            return match.group(0)
        if parts[1] == "c":
            character = MD.character_by_id().get(card_character_id)
            if character:
                return character.get("firstName", "") + character.get("givenName", "")
            return "???"
        effects = [effects_by_id[i] for i in ids if i in effects_by_id]
        if len(effects) != len(ids):
            return "?"
        if len(effects) == 1:
            return _format_single_effect(effects[0], parts[1])
        if len(effects) == 2:
            return _format_dual_effects(effects[0], effects[1], parts[1])
        return match.group(0)

    return _PLACEHOLDER.sub(repl, skill.get("description", ""))


# ---------------------------------------------------------------------------
# CardBasic (render/card/builder_helpers.go:15-175)
# ---------------------------------------------------------------------------


def card_unit(card: dict) -> str:
    """provider/local_cards.go:388-406."""
    character = MD.character_by_id().get(card.get("characterId"))
    if not character:
        return ""
    unit = character.get("unit", "")
    if unit and unit != "piapro":
        return unit
    support_unit = card.get("supportUnit", "")
    if support_unit and support_unit != "none":
        return support_unit
    return "piapro"


def calculate_power(card: dict) -> dict:
    """Max power per parameter type + special-training fixed bonus (builder_helpers.go:87-122)."""
    best = {"param1": 0, "param2": 0, "param3": 0}
    params = card.get("cardParameters") or []
    if isinstance(params, dict):  # object-form masterdata (card_parameters.go:67-92)
        for key in best:
            values = params.get(key) or []
            best[key] = max((int(v) for v in values), default=0)
    else:
        for entry in params:
            param_type = entry.get("cardParameterType")
            if param_type in best and int(entry.get("power", 0)) > best[param_type]:
                best[param_type] = int(entry.get("power", 0))
    power1 = best["param1"] + card.get("specialTrainingPower1BonusFixed", 0)
    power2 = best["param2"] + card.get("specialTrainingPower2BonusFixed", 0)
    power3 = best["param3"] + card.get("specialTrainingPower3BonusFixed", 0)
    return {"power_total": power1 + power2 + power3, "power1": power1, "power2": power2, "power3": power3}


def only_after_training(card: dict) -> bool:
    return str(card.get("initialSpecialTrainingStatus", "")).lower() == "done"


def has_after_training(card: dict) -> bool:
    return card.get("cardRarityType") in ("rarity_3", "rarity_4")


def build_skill(skill: dict, card: dict, skill_name: str) -> dict:
    detail = render_skill_detail(skill, card.get("characterId", 0)).strip()
    sprite = skill.get("descriptionSpriteName", "")
    info: dict[str, Any] = {
        "skill_id": skill["id"],
        "skill_name": skill_name,
        "skill_type": sprite,
        "skill_detail": detail,
    }
    if sprite.strip():
        info["skill_type_icon_path"] = ASSETS.static(f"skill_{sprite}.png")
    return info


def build_thumbnail_info(card: dict) -> list[dict]:
    """List/detail thumbnails (builder_helpers.go:68-85)."""
    if only_after_training(card):
        return [common.card_thumbnail(card, thumb_after=True)]
    items = [common.card_thumbnail(card, thumb_after=False)]
    if has_after_training(card):
        items.append(common.card_thumbnail(card, thumb_after=True))
    return items


def build_card_basic(card: dict) -> dict:
    info: dict[str, Any] = {
        "card_id": card["id"],
        "character_id": card.get("characterId"),
        "rare": card.get("cardRarityType"),
        "attr": card.get("attr"),
        "prefix": card.get("prefix"),
        "asset_bundle_name": card.get("assetbundleName"),
        "release_at": card.get("releaseAt"),
        "is_after_training": False,
        "thumbnail_info": build_thumbnail_info(card),
        "power": calculate_power(card),
    }
    character = MD.character_by_id().get(card.get("characterId"))
    if character:
        name = (character.get("firstName", "") + character.get("givenName", "")).strip()
        if name:
            info["character_name"] = name
    unit = card_unit(card)
    if unit.strip():
        info["unit"] = unit
    supply = common.supply_label_for_list(common.card_supply_type(card))
    if supply.strip():
        info["supply_type"] = supply
    skill = skill_by_id().get(card.get("skillId", 0))
    if skill:
        info["skill"] = build_skill(skill, card, card.get("cardSkillName", ""))
    if card.get("specialTrainingSkillId", 0) > 0:
        special = skill_by_id().get(card["specialTrainingSkillId"])
        if special:
            info["special_skill_info"] = build_skill(special, card, card.get("specialTrainingSkillName", ""))
    return info


def mark_unreleased(cards: list[dict], now: int) -> None:
    """controller.go:357-369."""
    for info in cards:
        release_at = info.get("release_at")
        if release_at is None or release_at <= now:
            continue
        for thumb in info.get("thumbnail_info") or []:
            thumb["custom_text"] = "未上线"


# ---------------------------------------------------------------------------
# card/detail extras (render/card/builder.go:28-116)
# ---------------------------------------------------------------------------


def member_image_path(asset_bundle_name: str, filename: str) -> str:
    """common/card_thumbnail.go:14-28."""
    rels = [f"character/member/{asset_bundle_name}/{filename}"]
    if not asset_bundle_name.endswith("_rip"):
        rels.append(f"character/member/{asset_bundle_name}_rip/{filename}")
    return ASSETS.region_asset(*rels)


def event_banner_path(asset_bundle_name: str) -> str:
    """assets/helper.go:291-303."""
    return ASSETS.region_asset(
        f"home/banner/{asset_bundle_name}/{asset_bundle_name}.png",
        f"event/{asset_bundle_name}/banner.png",
        f"event_story/{asset_bundle_name}/screen_image/banner_event_story.png",
    )


def event_banner_character_id(event_id: int) -> int:
    """Min card id among event cards excluding festival supplies (local_events.go:218-239)."""
    selected = None
    for entry in MD.get("eventCards"):
        if entry.get("eventId") != event_id:
            continue
        card = MD.card_by_id().get(entry.get("cardId"))
        if not card or "festival" in common.card_supply_type(card):
            continue
        if selected is None or card["id"] < selected["id"]:
            selected = card
    return selected["characterId"] if selected else 0


def find_gacha_for_card(card_id: int) -> dict | None:
    """First pickup match over gachas sorted by startAt desc, id desc (local_cards.go:100-118,359-372)."""
    gachas = sorted(MD.get("gachas"), key=lambda g: (-g.get("startAt", 0), -g["id"]))
    for gacha in gachas:
        if any(p.get("cardId") == card_id for p in gacha.get("gachaPickups") or []):
            return gacha
    return None


def costume_asset_bundle_name(costume: dict) -> str:
    """builder_helpers.go:200-229."""
    override = str(costume.get("assetbundleName", "")).strip()
    if "_" in override:
        return override
    part_type = str(costume.get("partType", "")).strip()
    if not part_type:
        return override
    base = override or f"{costume['id'] // 1000:04d}"
    name = f"cos{base}_{part_type}"
    if costume.get("colorId", 0) >= 2:
        name += f"_{costume['colorId'] - 1:02d}"
    return name


def costume_image_paths(card_id: int) -> list[str]:
    """cardCostume3ds order preserved (local_cards.go:120-142, builder_helpers.go:177-198)."""
    costume_by_id = {c["id"]: c for c in MD.get("costume3ds")}
    paths: list[str] = []
    for link in MD.get("cardCostume3ds"):
        if link.get("cardId") != card_id:
            continue
        costume = costume_by_id.get(link.get("costume3dId"))
        if not costume:
            continue
        name = costume_asset_bundle_name(costume)
        if not name:
            continue
        paths.append(ASSETS.region_asset(f"thumbnail/costume/{name}.png"))
    return paths


def build_card_detail_body(card: dict) -> dict:
    card_info = build_card_basic(card)
    supply = common.supply_label_for_detail(common.card_supply_type(card))
    if supply.strip():
        card_info["supply_type"] = supply

    body: dict[str, Any] = {"card_info": card_info, "region": common.REGION}

    event_id = MD.event_id_by_card().get(card["id"])
    event = MD.event_by_id().get(event_id) if event_id else None
    if event:
        event_info: dict[str, Any] = {
            "event_id": event["id"],
            "event_name": event.get("name", ""),
            "start_at": event.get("startAt"),
            "end_at": event.get("aggregateAt", 0) + 1000,
            "event_banner_path": event_banner_path(event.get("assetbundleName", "")),
        }
        bonuses = [b for b in MD.get("eventDeckBonuses") if b.get("eventId") == event["id"]]
        for bonus in bonuses:  # last non-empty cardAttr wins (builder.go:54-61)
            attr = bonus.get("cardAttr")
            if attr:
                event_info["bonus_attr"] = attr
                body["event_attr_icon_path"] = ASSETS.static(f"card/attr_icon_{attr}.png")
        unit_by_gcu = {u["id"]: u.get("unit", "") for u in MD.get("gameCharacterUnits")}
        units = {
            unit_by_gcu[b["gameCharacterUnitId"]]
            for b in bonuses
            if b.get("gameCharacterUnitId", 0) > 0 and b["gameCharacterUnitId"] in unit_by_gcu
        }
        if len(units) == 1:
            unit = next(iter(units))
            event_info["unit"] = unit
            icon = common.UNIT_ICONS.get(unit)
            if icon:
                body["event_unit_icon_path"] = ASSETS.static(icon)
            banner_cid = event_banner_character_id(event["id"])
            if banner_cid:
                event_info["banner_cid"] = banner_cid
                body["event_chara_icon_path"] = ASSETS.chara_icon(banner_cid)
        body["event_info"] = event_info

    gacha = find_gacha_for_card(card["id"])
    if gacha:
        body["gacha_info"] = {
            "gacha_id": gacha["id"],
            "gacha_name": gacha.get("name", ""),
            "start_at": gacha.get("startAt"),
            "end_at": (gacha.get("endAt", 0) // 1000 + 1) * 1000,
            "gacha_banner_path": ASSETS.region_asset(
                f"home/banner/banner_gacha{gacha['id']}/banner_gacha{gacha['id']}.png",
                f"gacha/banner_gacha{gacha['id']}.png",
            ),
        }

    abn = card.get("assetbundleName", "")
    card_images: list[str] = []
    if not only_after_training(card):
        card_images.append(member_image_path(abn, "card_normal.png"))
    if has_after_training(card):
        card_images.append(member_image_path(abn, "card_after_training.png"))
    body["card_images_path"] = card_images
    body["costume_images_path"] = costume_image_paths(card["id"])
    body["character_icon_path"] = ASSETS.chara_icon(card["characterId"])
    unit = card_unit(card)
    body["unit_logo_path"] = ASSETS.static(f"logo_{unit}.png") if unit else ""
    return body


# ---------------------------------------------------------------------------
# card/box (render/card/builder.go:147-291 + controller.go:181-250)
# ---------------------------------------------------------------------------


def extract_owned_cards(user_cards: list[dict]) -> dict[int, dict]:
    """builder.go:210-231 (values already trimmed like stringValueAny)."""
    owned: dict[int, dict] = {}
    for entry in user_cards:
        card_id = int(entry.get("cardId") or entry.get("card_id") or 0)
        if card_id <= 0:
            continue
        owned[card_id] = {
            "cardId": card_id,
            "level": int(entry.get("level", 0)),
            "masterRank": int(entry.get("masterRank", entry.get("master_rank", 0)) or 0),
            "specialTrainingStatus": str(
                entry.get("specialTrainingStatus", entry.get("special_training_status", "")) or ""
            ).strip(),
            "defaultImage": str(entry.get("defaultImage", entry.get("default_image", "")) or "").strip(),
        }
    return owned


def box_thumbnail_info(card: dict, state: dict | None, use_after_training: bool) -> list[dict]:
    """builder.go:253-291."""
    if state is not None:
        after = state["defaultImage"].lower() == "special_training" and has_after_training(card)
        star_after = state["specialTrainingStatus"].lower() == "done"
        return [
            common.card_thumbnail(
                card,
                thumb_after=after,
                star_after=star_after,
                train_rank=state["masterRank"],
                level=state["level"],
                is_pcard=True,
            )
        ]
    after = use_after_training and has_after_training(card)
    if only_after_training(card):
        after = True
    return [common.card_thumbnail(card, thumb_after=after)]


def resolve_box_after_training(card: dict, state: dict | None, use_after_training: bool) -> bool:
    """Controller's second-pass card.is_after_training override (builder.go:233-252)."""
    if card.get("cardRarityType") not in ("rarity_3", "rarity_4", "rarity_birthday"):
        return False
    if state is None:
        return use_after_training
    if not state["defaultImage"] and not state["specialTrainingStatus"]:
        return use_after_training
    if state["specialTrainingStatus"].lower() != "done":
        return False
    return state["defaultImage"].lower() == "special_training"


# ---------------------------------------------------------------------------
# distribution (render/card/builder_distribution.go)
# ---------------------------------------------------------------------------

_ATTR_ORDER = ["cute", "cool", "pure", "happy", "mysterious"]
_ATTR_LABELS = {
    "cute": "可爱",
    "cool": "帅气",
    "pure": "纯真",
    "happy": "快乐",
    "mysterious": "神秘",
    "unknown": "未分类",
}
_ATTR_COLORS = {
    "cute": "#FF66AA",
    "cool": "#3D8BFF",
    "pure": "#49C878",
    "happy": "#FFB02E",
    "mysterious": "#9B72FF",
    "unknown": "#9AA0A6",
}


def _normalize_distribution_attr(attr: str) -> str:
    attr = attr.strip().lower()
    if attr in _ATTR_ORDER:
        return attr
    return "unknown"


def _add_bucket(buckets: dict, key, owned: bool) -> None:
    bucket = buckets.setdefault(key, [0, 0])
    bucket[0] += 1
    if owned:
        bucket[1] += 1


def _character_stat_list(buckets: dict, icon_paths: dict, color_codes: dict, owned_data: bool) -> list[dict]:
    stats = []
    for character_id in sorted(buckets):
        count, owned = buckets[character_id]
        stat: dict[str, Any] = {
            "character_id": character_id,
            "count": count,
            "owned_count": owned,
            "bar_count": owned if owned_data else count,
            "bar_ratio": 0.0,
            "share": 0.0,
        }
        if color_codes.get(character_id):
            stat["color_code"] = color_codes[character_id]
        if icon_paths.get(character_id):
            stat["icon_path"] = icon_paths[character_id]
        stats.append(stat)
    return stats


def _apply_ratios(stats: list[dict], max_count: int, denominator: int) -> None:
    for stat in stats:
        if max_count > 0:
            stat["bar_ratio"] = stat["bar_count"] / max_count
        if denominator > 0:
            stat["share"] = stat["bar_count"] / denominator


def build_distribution(items: list[dict], icon_paths: dict, color_codes: dict, owned_data: bool) -> dict:
    dist: dict[str, Any] = {
        "total_count": 0,
        "owned_count": 0,
        "owned_data": owned_data,
        "max_character_bar_count": 0,
        "max_attribute_bar_count": 0,
    }
    character_buckets: dict[int, list[int]] = {}
    attribute_buckets: dict[str, list[int]] = {}
    attr_character_buckets: dict[str, dict[int, list[int]]] = {}
    for item in items:
        dist["total_count"] += 1
        if item["has_card"]:
            dist["owned_count"] += 1
        character_id = item["card"].get("character_id") or 0
        if character_id > 0:
            _add_bucket(character_buckets, character_id, item["has_card"])
        attr = _normalize_distribution_attr(item["card"].get("attr") or "")
        _add_bucket(attribute_buckets, attr, item["has_card"])
        if character_id > 0:
            _add_bucket(attr_character_buckets.setdefault(attr, {}), character_id, item["has_card"])

    denominator = dist["owned_count"] if owned_data else dist["total_count"]

    character_stats = _character_stat_list(character_buckets, icon_paths, color_codes, owned_data)
    dist["max_character_bar_count"] = max((s["bar_count"] for s in character_stats), default=0)
    _apply_ratios(character_stats, dist["max_character_bar_count"], denominator)
    dist["character_stats"] = character_stats

    attrs = list(_ATTR_ORDER) + [a for a in attribute_buckets if a not in _ATTR_ORDER]
    attribute_stats = []
    for attr in attrs:
        count, owned = attribute_buckets.get(attr, [0, 0])
        bar_count = owned if owned_data else count
        char_stats = _character_stat_list(attr_character_buckets.get(attr, {}), icon_paths, color_codes, owned_data)
        max_char = max((s["bar_count"] for s in char_stats), default=0)
        _apply_ratios(char_stats, max_char, bar_count)
        stat: dict[str, Any] = {
            "attr": attr,
            "label": _ATTR_LABELS.get(attr) or attr,
            "count": count,
            "owned_count": owned,
            "bar_count": bar_count,
            "bar_ratio": 0.0,
            "share": 0.0,
            "color_code": _ATTR_COLORS.get(attr) or _ATTR_COLORS["unknown"],
        }
        if attr in _ATTR_ORDER:
            stat["attr_icon_path"] = ASSETS.static(f"card/attr_icon_{attr}.png")
        if char_stats:
            stat["character_stats"] = char_stats
        attribute_stats.append(stat)
    dist["max_attribute_bar_count"] = max((s["bar_count"] for s in attribute_stats), default=0)
    _apply_ratios(attribute_stats, dist["max_attribute_bar_count"], denominator)
    dist["attribute_stats"] = attribute_stats
    return dist


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def build_card_list_body(now: int) -> dict:
    """Explicit card_ids scenario: latest 12 suite-owned cards, (releaseAt, id) ascending
    (< 90 cards so no auto-box; controller.go:110-143 + builder.go:118-145)."""
    user_info = common.build_user_info()
    owned_ids = {entry["cardId"] for entry in user_info["user_cards"]}
    owned_cards = sorted(
        (MD.card_by_id()[i] for i in owned_ids if i in MD.card_by_id()),
        key=lambda c: (c.get("releaseAt", 0), c["id"]),
    )
    cards = [build_card_basic(card) for card in owned_cards[-12:]]
    mark_unreleased(cards, now)
    return {
        "cards": cards,
        "region": common.REGION,
        "user_info": user_info,
        "term_limited_icon_path": ASSETS.static("card/term_limited.png"),
        "fes_limited_icon_path": ASSETS.static("card/fes_limited.png"),
    }


def build_card_box_body(now: int) -> dict:
    """Empty-query full catalog: releaseAt <= now, (releaseAt, id) ascending
    (controller.go:181-250; user_info required since suite has userCards)."""
    user_info = common.build_user_info()
    owned = extract_owned_cards(user_info["user_cards"])
    use_after_training = True

    items: list[dict] = []
    icon_paths: dict[int, str] = {}
    color_codes: dict[int, str] = {}
    for card in MD.cards_sorted():
        if card.get("releaseAt", 0) > now:
            continue
        state = owned.get(card["id"])
        info = build_card_basic(card)
        info["thumbnail_info"] = box_thumbnail_info(card, state, use_after_training)
        info["is_after_training"] = resolve_box_after_training(card, state, use_after_training)
        items.append({"card": info, "has_card": state is not None})
        character_id = card["characterId"]
        icon_paths[character_id] = ASSETS.chara_icon(character_id)
        color_code = MD.character_color_code().get(character_id)
        if color_code:
            color_codes[character_id] = color_code

    return {
        "cards": items,
        "region": common.REGION,
        "user_info": user_info,
        "show_id": False,
        "show_box": False,
        # unowned_only=false and group_by="" are omitted by Go's omitempty (models_card.go:142-157)
        "distribution": build_distribution(items, icon_paths, color_codes, owned_data=bool(owned)),
        "character_icon_paths": icon_paths,
        "character_color_codes": color_codes,
        "term_limited_icon_path": ASSETS.static("card/term_limited.png"),
        "fes_limited_icon_path": ASSETS.static("card/fes_limited.png"),
    }


def generate() -> list[str]:
    now = common.now_ms()
    written: list[str] = []

    detail_body = common.finalize(build_card_detail_body(MD.card_by_id()[DETAIL_CARD_ID]))
    CardDetailRequest.model_validate(detail_body)
    common.write_payload("card_detail", detail_body)
    written.append("card_detail")

    list_body = common.finalize(build_card_list_body(now))
    CardListRequest.model_validate(list_body)
    common.write_payload("card_list", list_body)
    written.append("card_list")

    box_body = common.finalize(build_card_box_body(now))
    CardBoxRequest.model_validate(box_body)
    common.write_payload("card_box", box_body)
    written.append("card_box")

    return written


if __name__ == "__main__":
    names = generate()
    print("written:", names)  # noqa: T201
    common.ASSETS.save_manifest()
    print("missing assets:", len(common.ASSETS.missing))  # noqa: T201
