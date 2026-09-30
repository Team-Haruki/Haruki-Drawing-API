"""Cloud's per-region asset inventories, used to reuse mirrored files across asset revisions.

Cloud's `X-Haruki-Asset-Revision` is ONE digest over all five regions' `indexes/assets/v1/<region>/current.json`
revisions (Haruki-Cloud `assetindex.Manager.revision`), so a publish in any region moves every request into a new
mirror directory. Each pointer lists its shards as `(prefix, sha256)`, and a region revision is a digest over that
list, so two revisions that agree on a region revision, or on the shard covering a key, name byte-identical
objects under that key. That is what lets the mirror hard-link a file from an older revision directory instead of
fetching it again.

Everything here is pure: parsing, digests and the comparison. The mirror does the I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any

POINTER_ROOT = "indexes/assets/v1/"
REGIONS = ("jp", "en", "tw", "kr", "cn")
SIDECAR_NAME = ".asset-index.json"  # at the top of a revision directory; object keys never start with "."
POINTER_MAX_BYTES = 1024 * 1024
_MAX_SHARDS = 4096
_DIGEST = re.compile(r"[0-9a-f]{64}")


def is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def pointer_key(region: str) -> str:
    return f"{POINTER_ROOT}{region}/current.json"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def shards_revision(shards: Iterable[tuple[str, str]]) -> str:
    """Haruki-Cloud `manifestRevision`: sha256 over `prefix\\0sha256\\n` sorted by prefix."""
    return _sha256("".join(f"{prefix}\0{sha}\n" for prefix, sha in sorted(shards)))


def combined_revision(regions: Mapping[str, RegionPointer]) -> str:
    """Haruki-Cloud `Manager.revision` for regions that are not stale: sha256 over sorted `region:revision` lines."""
    return _sha256("\n".join(sorted(f"{region}:{pointer.revision}" for region, pointer in regions.items())))


@dataclass(frozen=True, slots=True)
class RegionPointer:
    revision: str
    shards: tuple[tuple[str, str], ...]  # (prefix, sha256), sorted by prefix

    def shard_for(self, object_key: str) -> tuple[str, str] | None:
        """The longest shard prefix covering `object_key` (Cloud's own rule), or `None`."""
        best: tuple[str, str] | None = None
        for prefix, sha in self.shards:
            if object_key.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, sha)
        return best


def parse_region(value: Any, region: str) -> RegionPointer:
    """Validate one `current.json` (or its sidecar copy); raises `ValueError` on anything Cloud would refuse."""
    if not isinstance(value, Mapping):
        raise ValueError("asset index: pointer is not an object")
    if value.get("version", 1) != 1 or value.get("region", region) != region or value.get("complete", True) is not True:
        raise ValueError("asset index: incomplete or foreign pointer")
    revision = value.get("revision")
    raw_shards = value.get("shards")
    if not is_digest(revision) or not isinstance(raw_shards, list) or not 0 < len(raw_shards) <= _MAX_SHARDS:
        raise ValueError("asset index: invalid pointer")
    shards: dict[str, str] = {}
    region_prefix = f"{region}-assets/"
    for shard in raw_shards:
        if not isinstance(shard, Mapping):
            raise ValueError("asset index: invalid shard")
        prefix, sha = shard.get("prefix"), shard.get("sha256")
        if not isinstance(prefix, str) or not prefix.startswith(region_prefix) or not prefix.endswith("/"):
            raise ValueError("asset index: invalid shard prefix")
        if not is_digest(sha) or prefix in shards:
            raise ValueError("asset index: invalid shard digest")
        shards[prefix] = sha
    ordered = tuple(sorted(shards.items()))
    if shards_revision(ordered) != revision:
        raise ValueError("asset index: shard list does not match the revision")
    return RegionPointer(revision=revision, shards=ordered)


@dataclass(frozen=True, slots=True)
class RevisionIndex:
    """The region pointers behind one combined asset revision."""

    revision: str
    regions: Mapping[str, RegionPointer]

    @classmethod
    def build(cls, regions: Mapping[str, RegionPointer]) -> RevisionIndex:
        frozen = MappingProxyType(dict(regions))
        return cls(revision=combined_revision(frozen), regions=frozen)

    def same_object(self, other: RevisionIndex, region: str, object_key: str) -> bool:
        """Whether both revisions provably name the same bytes at `object_key` (same region or same shard)."""
        mine, theirs = self.regions.get(region), other.regions.get(region)
        if mine is None or theirs is None:
            return False
        if mine.revision == theirs.revision:
            return True
        shard = mine.shard_for(object_key)
        return shard is not None and shard == theirs.shard_for(object_key)

    def to_json(self) -> bytes:
        regions = {
            region: {"revision": pointer.revision, "shards": [{"prefix": p, "sha256": s} for p, s in pointer.shards]}
            for region, pointer in sorted(self.regions.items())
        }
        body = {"revision": self.revision, "regions": regions}
        return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes, expected_revision: str) -> RevisionIndex:
        """Parse a sidecar and recheck every digest, so a torn or foreign file is refused rather than trusted."""
        body = json.loads(data)
        if not isinstance(body, Mapping) or not isinstance(body.get("regions"), Mapping):
            raise ValueError("asset index: invalid sidecar")
        regions = {region: parse_region(value, region) for region, value in body["regions"].items()}
        if not regions or any(region not in REGIONS for region in regions):
            raise ValueError("asset index: invalid sidecar regions")
        index = cls.build(regions)
        if index.revision != expected_revision:
            raise ValueError("asset index: sidecar does not match its revision")
        return index
