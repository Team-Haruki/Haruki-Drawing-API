"""Run every domain generator in ONE process so the shared AssetResolver
accumulates a complete rsync manifest (separate runs overwrite each other's
``assets-*.txt``). Usage (repo root): ``uv run python scripts/parity_payloads/run_all.py``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import common  # noqa: E402

DOMAINS = [
    "gen_card",
    "gen_profile",
    "gen_deck_event",
    "gen_music_score",
    "gen_sk_misc_stamp",
    "gen_gacha_costume_vlive_edu",
    "gen_mysekai",
]


def main() -> None:
    written: list[str] = []
    failures: list[str] = []
    for name in DOMAINS:
        try:
            mod = importlib.import_module(name)
            names = mod.generate()
            written.extend(names)
            print(f"[ok] {name}: {len(names)} payloads")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"[FAIL] {name}: {exc}")
    common.ASSETS.save_manifest()
    print(f"\ntotal payloads: {len(written)}")
    print(f"assets used: {len(common.ASSETS.used)}, missing: {len(common.ASSETS.missing)}, "
          f"candidates: {len(common.ASSETS.candidates)}")
    if failures:
        print("FAILURES:\n" + "\n".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
