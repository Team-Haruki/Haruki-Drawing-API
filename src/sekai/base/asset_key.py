"""Asset keys that may name several candidates (C1).

Cloud used to probe the asset tree and send the one path that exists. Under C1 a field that forks on
existence carries either the plain string (legacy payloads, unchanged) or a JSON array of candidates, and
Drawing takes the first candidate that exists. These helpers are pure: no I/O, no settings.
"""

from __future__ import annotations

from typing import TypeAlias

# A plain alias rather than a `type` statement: ruff targets py311 syntax, and a bare union keeps pydantic
# validation identical to a hand-written `str | list[str]` annotation.
AssetKey: TypeAlias = str | list[str]
"""A single asset path, or candidate paths in preference order. Pydantic v2 keeps a JSON string a `str`."""


def candidates(key: AssetKey | None) -> list[str]:
    """The candidate paths of `key`, in order.

    `None`, a blank string and an empty list give `[]`. A string gives `[key]` exactly as sent, so the
    legacy single-path behaviour is unchanged. A list keeps its non-blank items stripped of surrounding
    whitespace, in their original order, with duplicates dropped.
    """
    if key is None:
        return []
    if isinstance(key, str):
        return [key] if key.strip() else []
    seen: set[str] = set()
    result: list[str] = []
    for item in key:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def first_candidate(key: AssetKey | None) -> str | None:
    """The first candidate, for legacy string operations (logs, `"thumbnail" in path`, suffix checks)."""
    items = candidates(key)
    return items[0] if items else None


def is_candidate_list(key: AssetKey | None) -> bool:
    """Whether `key` arrived as a candidate list rather than a single path."""
    return isinstance(key, list)
