from pathlib import Path
import subprocess
import sys

from src.core.image_payload import EncodedImagePayload


def test_heavy_render_pool_reexports_encoded_image_payload():
    from src.core.heavy_render_pool import EncodedImagePayload as HeavyPoolEncodedImagePayload

    assert HeavyPoolEncodedImagePayload is EncodedImagePayload


def test_heavy_render_pool_import_does_not_load_pillow():
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import src.core.heavy_render_pool; assert 'PIL' not in sys.modules",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
