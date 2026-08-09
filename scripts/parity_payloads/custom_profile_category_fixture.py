"""Extract privacy-bounded Custom Profile category fixtures.

The input may be a complete captured render request, but the output contract is deliberately
incapable of storing that request.  A fixture contains selected elements from one rendering
category plus explicitly supplied, category-specific dependencies.  Full cards, profile
contexts, resource indexes, response envelopes, and user-scoped keys are rejected recursively.

Run this only inside the root-only capture review window, then purge the source capture.  The
result is written as an owner-only file and the command reports counts only.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from typing import Any
import unicodedata

CATEGORY_FIXTURE_KIND = "pjsk_custom_profile_category_fixture"
CATEGORY_FIXTURE_SCHEMA_VERSION = 1

CATEGORY_BUCKETS: dict[str, str] = {
    "bonds_honor": "bondsHonors",
    "card_member_clip": "cardMembers",
    "card_member_full": "cardMembers",
    "character_icon": "characterIcons",
    "material": "materials",
    "stamp": "stamps",
    "story_favorite": "generals",
    "text_symbol": "texts",
    "user_interface_icon": "userInterfaceIcons",
}

_ROOT_KEYS = frozenset({"schema_version", "kind", "category", "region", "items", "dependencies"})
_FORBIDDEN_KEYS = frozenset(
    {
        "card",
        "context",
        "customprofilecard",
        "customprofilecards",
        "profilecontext",
        "request",
        "resources",
        "response",
        "updatedresources",
        "usercustomprofilecards",
    }
)
_DIRECT_IDENTIFIER_KEYS = frozenset(
    {
        "accountid",
        "botid",
        "nickname",
        "openid",
        "playerid",
        "qq",
        "twitterid",
        "uid",
        "userid",
    }
)
_TMP_TAG = re.compile(r"<[^<>]*>")


def _canonical_key(key: object) -> str:
    return "".join(ch for ch in str(key).casefold() if ch.isalnum())


def _validate_privacy_tree(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            canonical = _canonical_key(key)
            if canonical in _FORBIDDEN_KEYS or canonical in _DIRECT_IDENTIFIER_KEYS or canonical.startswith("user"):
                raise ValueError(f"{path} contains forbidden full-request or user-scoped key: {key!r}")
            _validate_privacy_tree(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_privacy_tree(child, path=f"{path}[{index}]")


def _deidentify_symbol_text(text: str) -> str:
    """Keep TMP markup and symbol classes while replacing letters and numbers.

    Rich-text geometry depends on the tags, whitespace and decorative glyph categories.  Plain
    words and digits are not needed for a symbol-category fixture and can carry identifying text.
    """

    pieces: list[str] = []
    cursor = 0
    for match in _TMP_TAG.finditer(text):
        pieces.append(_deidentify_visible_text(text[cursor : match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_deidentify_visible_text(text[cursor:]))
    return "".join(pieces)


def _deidentify_visible_text(text: str) -> str:
    return "".join("X" if unicodedata.category(char).startswith(("L", "N")) else char for char in text)


def build_category_fixture(
    *,
    category: str,
    region: str,
    items: list[dict[str, Any]],
    dependencies: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate a category-only fixture."""

    if category not in CATEGORY_BUCKETS:
        raise ValueError(f"unsupported Custom Profile category: {category!r}")
    normalized_region = region.strip().lower()
    if not normalized_region or not normalized_region.replace("_", "").isalnum():
        raise ValueError("category fixture region is invalid")
    if not items or len(items) > 8 or not all(isinstance(item, dict) for item in items):
        raise ValueError("category fixture must contain between one and eight object items")

    copied_items = deepcopy(items)
    if category == "text_symbol":
        for item in copied_items:
            item["text"] = _deidentify_symbol_text(str(item.get("text", "") or ""))

    copied_dependencies = deepcopy(dependencies or {})
    if not isinstance(copied_dependencies, dict):
        raise ValueError("category fixture dependencies must be an object")
    _validate_privacy_tree(copied_items, path="items")
    _validate_privacy_tree(copied_dependencies, path="dependencies")

    return {
        "schema_version": CATEGORY_FIXTURE_SCHEMA_VERSION,
        "kind": CATEGORY_FIXTURE_KIND,
        "category": category,
        "region": normalized_region,
        "items": copied_items,
        "dependencies": copied_dependencies,
    }


def validate_category_fixture(fixture: dict[str, Any]) -> None:
    """Reject anything outside the narrow category fixture schema."""

    unknown = set(fixture) - _ROOT_KEYS
    if unknown:
        raise ValueError(f"category fixture has forbidden root keys: {sorted(unknown)}")
    if fixture.get("schema_version") != CATEGORY_FIXTURE_SCHEMA_VERSION or fixture.get("kind") != CATEGORY_FIXTURE_KIND:
        raise ValueError("unsupported Custom Profile category fixture schema")
    rebuilt = build_category_fixture(
        category=str(fixture.get("category", "")),
        region=str(fixture.get("region", "")),
        items=fixture.get("items"),
        dependencies=fixture.get("dependencies"),
    )
    if rebuilt != fixture:
        raise ValueError("category fixture is not in canonical de-identified form")


def extract_category_fixture(
    payload: dict[str, Any],
    *,
    category: str,
    indexes: list[int],
    dependencies: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select category elements without copying any other part of a captured request."""

    if category not in CATEGORY_BUCKETS:
        raise ValueError(f"unsupported Custom Profile category: {category!r}")
    if not indexes or len(indexes) != len(set(indexes)):
        raise ValueError("category extraction requires unique item indexes")
    card = payload.get("card")
    layout = card.get("customProfileCard") if isinstance(card, dict) else None
    bucket = layout.get(CATEGORY_BUCKETS[category]) if isinstance(layout, dict) else None
    if not isinstance(bucket, list):
        raise ValueError(f"captured request does not contain category bucket {CATEGORY_BUCKETS[category]!r}")
    try:
        items = [bucket[index] for index in indexes]
    except IndexError as exc:
        raise ValueError("category item index is out of range") from exc
    if not all(isinstance(item, dict) for item in items):
        raise ValueError("selected category item is not an object")
    return build_category_fixture(
        category=category,
        region=str(payload.get("region", "")),
        items=items,
        dependencies=dependencies,
    )


def write_owner_only_json(path: Path, value: dict[str, Any]) -> None:
    """Write one sanitized fixture with 0600 permissions."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Root-only captured render request")
    parser.add_argument("--output", type=Path, required=True, help="Owner-only category fixture")
    parser.add_argument("--category", choices=sorted(CATEGORY_BUCKETS), required=True)
    parser.add_argument("--index", action="append", type=int, required=True, dest="indexes")
    parser.add_argument(
        "--dependencies",
        type=Path,
        help="Optional pre-sanitized category dependency object; full resources/profile context are rejected",
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    dependencies = json.loads(args.dependencies.read_text(encoding="utf-8")) if args.dependencies else None
    fixture = extract_category_fixture(
        payload,
        category=args.category,
        indexes=args.indexes,
        dependencies=dependencies,
    )
    write_owner_only_json(args.output, fixture)
    print(f"category={args.category} items={len(fixture['items'])}")  # noqa: T201


if __name__ == "__main__":
    main()
