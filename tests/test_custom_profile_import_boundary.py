"""Unity layout construction must work before any Pillow raster adapter loads."""

from pathlib import Path
import subprocess
import sys


def test_shared_prefabs_and_unity_metadata_build_without_pillow(tmp_path):
    code = r"""
import sys
from pathlib import Path
from scripts.skia_no_pillow import _NoPillow
guard = _NoPillow()
sys.meta_path.insert(0, guard)
from src.sekai.profile.custom_profile.card_prefab import build_empty_deck_card_display_list
from src.sekai.profile.custom_profile.general_prefab import GeneralTextOp, build_general_prefab_display_list
from src.sekai.profile.custom_profile.renderer import PNGRenderer, GENERAL_PREFAB_PALETTE
from src.sekai.profile.custom_profile.resource_paths import _require_region_path
from src.sekai.profile.custom_profile import skia, drawer

class Metrics:
    def text_bbox(self, text, font, size):
        return (0, 0, len(text) * size / 2, size)

general = build_general_prefab_display_list(
    "EditUserName", size=(548, 64), profile_context={"user": {"name": "Native layout"}},
    labels={}, metrics=Metrics(), palette=GENERAL_PREFAB_PALETTE,
)
assert any(isinstance(op, GeneralTextOp) and op.text == "Native layout" for op in general.ops)
assert len(build_empty_deck_card_display_list((100, 160)).ops) == 2
root = Path(sys.argv[1])
assert _require_region_path("assets", root, "jp") == root
renderer = PNGRenderer(masterdata=None, assets=root, fonts=root, tmp_font_metadata=None)
card = {"customProfileCard": {"shapes": [
    {"id": 1, "objectData": {"visible": True, "layer": 3}},
    {"id": 2, "objectData": {"visible": True, "layer": 1}},
]}}
assert [content.layer for content in renderer.build_native_contents(card)] == [1, 3]
assert not guard.rejected
assert not any(name == "PIL" or name.startswith("PIL.") for name in sys.modules)
# Deferral must not disguise the legacy renderer as pure native. Executing it
# still reaches the real Pillow import, which the same guard rejects.
try:
    renderer.render_card({"customProfileCard": {}})
except RuntimeError as exc:
    assert "Pillow retirement gate" in str(exc)
else:
    raise AssertionError("Pillow replay did not reach the import boundary")
assert guard.rejected
"""
    result = subprocess.run(
        [sys.executable, "-X", "gil=0", "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
