"""C3: exactly one `asset/`-strip site in the runtime asset-resolution code, and it is `src/assets/keymap.py`.

Scanned: `src/sekai/base` and `src/assets` (the plan's scope) plus the rest of `src/` and `scripts/` so a new
strip site anywhere is caught. Documented exclusions (drawing-plan §13.3):
- `src/sekai/profile/custom_profile/renderer.py` -- bidirectional `asset/` <-> `<cc>-assets/` expansion for
  pre-provisioned local custom-profile directory layouts; never produces a bucket key (owner question in §13.3).
- `scripts/sync_card_list_assets.py` -- an rsync helper, not a runtime path.
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
SCOPES = ("src/sekai/base", "src/assets")
EXTRA_SCOPES = ("src", "scripts")
EXCLUDED = frozenset(
    {
        "src/sekai/profile/custom_profile/renderer.py",  # §13.3: local directory layouts, not bucket keys
        "scripts/sync_card_list_assets.py",  # §13.3: rsync helper, not a runtime path
    }
)
STRIP_PATTERNS = (
    re.compile(r"""removeprefix\(\s*["']asset/"""),
    re.compile(r"""lstrip\(\s*["']asset"""),
    re.compile(r"""\[\s*len\(\s*["']asset/["']\s*\)\s*:"""),
    re.compile(r"""replace\(\s*["']asset/["']\s*,\s*["']["']"""),
    re.compile(r"""re\.sub\(\s*r?["']\^?asset/"""),
    re.compile(r"""startswith\(\s*["']asset/["']\s*\)[^\n]*\[\s*6\s*:"""),
)


def _strip_sites(scopes: tuple[str, ...]) -> list[tuple[str, int]]:
    hits: set[tuple[str, int]] = set()
    for scope in scopes:
        for path in sorted((ROOT / scope).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in EXCLUDED:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if any(p.search(line) for p in STRIP_PATTERNS):
                    hits.add((rel, lineno))
    return sorted(hits)


def test_exactly_one_strip_site_in_keymap() -> None:
    hits = _strip_sites(SCOPES)
    assert len(hits) == 1, hits
    assert hits[0][0] == "src/assets/keymap.py"


def test_no_other_strip_site_in_src_or_scripts() -> None:
    hits = _strip_sites(EXTRA_SCOPES)
    assert [rel for rel, _ in hits] == ["src/assets/keymap.py"], hits


def test_exclusions_still_exist_and_are_the_documented_ones() -> None:
    # If an excluded file loses its strip, drop the exclusion rather than let it silently widen the allowlist.
    for rel in EXCLUDED:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert any(p.search(text) for p in STRIP_PATTERNS), rel


def test_patterns_detect_known_strip_shapes() -> None:
    samples = [
        'key.removeprefix("asset/")',
        "key.lstrip('asset/')",
        'key[len("asset/"):]',
        'key.replace("asset/", "")',
        're.sub(r"^asset/", "", key)',
        'if key.startswith("asset/"): key = key[6:]',
    ]
    for sample in samples:
        assert any(p.search(sample) for p in STRIP_PATTERNS), sample
    assert not any(p.search('logical = "asset/jp-assets/startapp/x.png"') for p in STRIP_PATTERNS)
