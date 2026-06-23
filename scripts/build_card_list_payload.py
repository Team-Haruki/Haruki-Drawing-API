from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

STATIC_IMAGES_DIR = "static_images"
REGION_ASSET_MODE = "startapp"
SKILL_PLACEHOLDER = re.compile(r"\{\{([^{}]+)}}")


def load_master_json(master_dir: Path, filename: str) -> list[dict[str, Any]]:
    path = master_dir / filename
    with path.open("rb") as fp:
        data = json.load(fp)
    if not isinstance(data, list):
        raise TypeError(f"{path} must contain a JSON array")
    return [item for item in data if isinstance(item, dict)]


def load_optional_master_json(master_dir: Path, filename: str) -> list[dict[str, Any]]:
    path = master_dir / filename
    if not path.exists():
        return []
    return load_master_json(master_dir, filename)


def index_by_id(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in items:
        item_id = item.get("id")
        if isinstance(item_id, int):
            result[item_id] = item
    return result


def region_asset_dir(region: str, mode: str = REGION_ASSET_MODE) -> str:
    normalized_region = region.strip().lower() or "jp"
    normalized_mode = mode.strip().lower() or REGION_ASSET_MODE
    return f"asset/{normalized_region}-assets/{normalized_mode}"


def normalize_supply_type(raw: str | None) -> str:
    match (raw or "").strip():
        case "" | "normal" | "not_limited":
            return "normal"
        case "term_limited":
            return "term_limited"
        case "festival_limited" | "colorful_festival_limited":
            return "colorful_festival_limited"
        case "bloom_festival_limited":
            return "bloom_festival_limited"
        case "unit_event_limited":
            return "unit_event_limited"
        case "collaboration_limited":
            return "collaboration_limited"
        case "birthday" | "rarity_birthday":
            return "birthday"
        case other:
            return other


def format_supply_type_for_list(raw: str | None) -> str:
    match normalize_supply_type(raw):
        case "normal" | "":
            return ""
        case "term_limited":
            return "期间限定"
        case "colorful_festival_limited":
            return "CFes限定"
        case "bloom_festival_limited":
            return "BFes限定"
        case "unit_event_limited":
            return "WL限定"
        case "collaboration_limited":
            return "联动限定"
        case "birthday":
            return "生日"
        case other:
            return other.strip()


def world_link3_card_ids(master_dir: Path) -> set[int]:
    events_by_id = index_by_id(load_optional_master_json(master_dir, "events.json"))
    world_link3_events = {
        event_id
        for event_id, event in events_by_id.items()
        if str(event.get("eventType") or "").strip().lower() == "world_bloom"
        and str(event.get("unit") or "").strip().lower() == "none"
    }
    if not world_link3_events:
        return set()

    result: set[int] = set()
    for event_card in load_optional_master_json(master_dir, "eventCards.json"):
        card_id = event_card.get("cardId")
        event_id = event_card.get("eventId")
        if isinstance(card_id, int) and event_id in world_link3_events:
            result.add(card_id)
    return result


def static_card_asset(filename: str) -> str:
    return f"{STATIC_IMAGES_DIR}/card/{filename}"


def static_skill_asset(skill_type: str | None) -> str | None:
    if not skill_type:
        return None
    return f"{STATIC_IMAGES_DIR}/skill_{skill_type}.png"


def card_thumbnail_path(card: dict[str, Any], region: str, *, trained_art: bool) -> str:
    suffix = "_after_training.png" if trained_art else "_normal.png"
    return f"{region_asset_dir(region)}/thumbnail/chara/{card['assetbundleName']}{suffix}"


def rare_image_path(card: dict[str, Any], *, after_training: bool) -> str:
    if card.get("cardRarityType") == "rarity_birthday":
        return static_card_asset("rare_birthday.png")
    filename = "rare_star_after_training.png" if after_training else "rare_star_normal.png"
    return static_card_asset(filename)


def build_thumbnail(card: dict[str, Any], region: str, *, after_training: bool, trained_art: bool) -> dict[str, Any]:
    rarity = str(card.get("cardRarityType") or "")
    attr = str(card.get("attr") or "").lower()
    thumbnail = {
        "card_id": card["id"],
        "card_thumbnail_path": card_thumbnail_path(card, region, trained_art=trained_art),
        "rare": rarity,
        "frame_img_path": static_card_asset(f"frame_{rarity}.png"),
        "attr_img_path": static_card_asset(f"attr_{attr}.png"),
        "rare_img_path": rare_image_path(card, after_training=after_training),
        "train_rank": 0,
        "is_after_training": after_training,
        "is_pcard": False,
    }
    if rarity == "rarity_birthday":
        thumbnail["birthday_icon_path"] = static_card_asset("rare_birthday.png")
    return thumbnail


def build_thumbnail_info(card: dict[str, Any], region: str) -> list[dict[str, Any]]:
    if str(card.get("initialSpecialTrainingStatus") or "").lower() == "done":
        return [build_thumbnail(card, region, after_training=True, trained_art=True)]

    thumbnails = [build_thumbnail(card, region, after_training=False, trained_art=False)]
    if card.get("cardRarityType") in {"rarity_3", "rarity_4"}:
        thumbnails.append(build_thumbnail(card, region, after_training=True, trained_art=True))
    return thumbnails


def power_total(card: dict[str, Any]) -> dict[str, int]:
    powers = {"param1": 0, "param2": 0, "param3": 0}
    for parameter in card.get("cardParameters") or []:
        if not isinstance(parameter, dict):
            continue
        param_type = parameter.get("cardParameterType")
        power = parameter.get("power")
        if param_type in powers and isinstance(power, int):
            powers[param_type] = max(powers[param_type], power)

    power1 = powers["param1"] + int(card.get("specialTrainingPower1BonusFixed") or 0)
    power2 = powers["param2"] + int(card.get("specialTrainingPower2BonusFixed") or 0)
    power3 = powers["param3"] + int(card.get("specialTrainingPower3BonusFixed") or 0)
    return {
        "power1": power1,
        "power2": power2,
        "power3": power3,
        "power_total": power1 + power2 + power3,
    }


def _format_effect(effect: dict[str, Any] | None, token: str) -> str:
    if effect is None:
        return "?"
    details = [detail for detail in effect.get("skillEffectDetails") or [] if isinstance(detail, dict)]
    highest = details[-1] if details else {}
    match token:
        case "d":
            value = highest.get("activateEffectDuration") or effect.get("activateEffectDuration")
        case "v":
            value = highest.get("activateEffectValue") or effect.get("activateEffectValue")
        case "v2":
            value = highest.get("activateEffectValue2") or effect.get("activateEffectValue2")
        case _:
            value = None
    return str(value if value is not None else "?")


def format_skill_description(skill: dict[str, Any], character: dict[str, Any] | None) -> str:
    description = str(skill.get("description") or skill.get("shortDescription") or "")
    effects = {
        effect.get("id"): effect for effect in skill.get("skillEffects") or [] if isinstance(effect, dict)
    }

    def replace(match: re.Match[str]) -> str:
        raw_ids, _, token = match.group(1).partition(";")
        ids = [int(raw_id) for raw_id in raw_ids.split(",") if raw_id.strip().isdigit()]
        if not ids or not token:
            return match.group(0)
        if token == "c":
            if character is None:
                return "???"
            return f"{character.get('firstName') or ''}{character.get('givenName') or ''}"
        if len(ids) == 1:
            return _format_effect(effects.get(ids[0]), token)
        return "/".join(_format_effect(effects.get(effect_id), token) for effect_id in ids)

    return SKILL_PLACEHOLDER.sub(replace, description)


def build_skill(
    skill_id: int | None,
    skill_name: str | None,
    skills_by_id: dict[int, dict[str, Any]],
    character: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not skill_id:
        return None
    skill = skills_by_id.get(skill_id)
    if skill is None:
        return None
    skill_type = skill.get("descriptionSpriteName")
    return {
        "skill_id": skill_id,
        "skill_name": skill_name or "",
        "skill_type": skill_type or "",
        "skill_detail": format_skill_description(skill, character),
        "skill_type_icon_path": static_skill_asset(skill_type),
    }


def resolve_unit(card: dict[str, Any], character: dict[str, Any] | None) -> str | None:
    if character is None:
        return None
    unit = str(character.get("unit") or "")
    support_unit = str(card.get("supportUnit") or "")
    if unit and unit != "piapro":
        return unit
    if support_unit and support_unit != "none":
        return support_unit
    return "piapro"


def build_card_basic(
    card: dict[str, Any],
    *,
    region: str,
    characters_by_id: dict[int, dict[str, Any]],
    skills_by_id: dict[int, dict[str, Any]],
    supplies_by_id: dict[int, str],
    wl3_card_ids: set[int],
) -> dict[str, Any]:
    character = characters_by_id.get(int(card.get("characterId") or 0))
    supply_raw = "birthday" if card.get("cardRarityType") == "rarity_birthday" else supplies_by_id.get(
        int(card.get("cardSupplyId") or 0),
        "normal",
    )
    if supply_raw == "term_limited" and card["id"] in wl3_card_ids:
        supply_raw = "unit_event_limited"
    basic: dict[str, Any] = {
        "card_id": card["id"],
        "character_id": card.get("characterId"),
        "character_name": (
            f"{character.get('firstName') or ''}{character.get('givenName') or ''}" if character else None
        ),
        "unit": resolve_unit(card, character),
        "release_at": card.get("releaseAt"),
        "supply_type": format_supply_type_for_list(supply_raw),
        "rare": card.get("cardRarityType"),
        "attr": card.get("attr"),
        "prefix": card.get("prefix"),
        "asset_bundle_name": card.get("assetbundleName"),
        "skill": build_skill(card.get("skillId"), card.get("cardSkillName"), skills_by_id, character),
        "thumbnail_info": build_thumbnail_info(card, region),
        "is_after_training": False,
        "power": power_total(card),
    }
    special_skill = build_skill(
        card.get("specialTrainingSkillId"),
        card.get("specialTrainingSkillName"),
        skills_by_id,
        character,
    )
    if special_skill is not None:
        basic["special_skill_info"] = special_skill
    return {key: value for key, value in basic.items() if value is not None}


def build_payload(master_dir: Path, *, card_ids: list[int], region: str, title: str | None) -> dict[str, Any]:
    cards_by_id = index_by_id(load_master_json(master_dir, "cards.json"))
    characters_by_id = index_by_id(load_master_json(master_dir, "gameCharacters.json"))
    skills_by_id = index_by_id(load_master_json(master_dir, "skills.json"))
    supplies_by_id = {
        item["id"]: normalize_supply_type(item.get("cardSupplyType"))
        for item in load_master_json(master_dir, "cardSupplies.json")
        if isinstance(item.get("id"), int)
    }
    wl3_card_ids = world_link3_card_ids(master_dir)

    cards = []
    missing = []
    for card_id in card_ids:
        card = cards_by_id.get(card_id)
        if card is None:
            missing.append(card_id)
            continue
        cards.append(
            build_card_basic(
                card,
                region=region,
                characters_by_id=characters_by_id,
                skills_by_id=skills_by_id,
                supplies_by_id=supplies_by_id,
                wl3_card_ids=wl3_card_ids,
            )
        )
    if missing:
        raise ValueError(f"card id(s) not found in masterdata: {missing}")
    if not cards:
        raise ValueError("at least one valid card id is required")

    payload: dict[str, Any] = {
        "cards": cards,
        "region": region,
        "term_limited_icon_path": static_card_asset("term_limited.png"),
        "fes_limited_icon_path": static_card_asset("fes_limited.png"),
    }
    if title is not None:
        payload["title"] = title
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a /api/pjsk/card/list payload from haruki-sekai-master masterdata.",
    )
    parser.add_argument("--master-dir", required=True, type=Path, help="Directory containing master/*.json files.")
    parser.add_argument("--region", default="jp", help="Asset region, such as jp/cn/tw/en/kr.")
    parser.add_argument("--card-id", action="append", type=int, required=True, help="Card ID. Repeatable.")
    parser.add_argument("--title", help="Optional Card List notice/title.")
    parser.add_argument("--output", type=Path, help="Write JSON payload to this file. Defaults to stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(args.master_dir, card_ids=args.card_id, region=args.region, title=args.title)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output is None:
        sys.stdout.write(rendered)
        sys.stdout.write("\n")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
