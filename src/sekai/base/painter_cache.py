"""Legacy raster-cache maintenance without importing a raster renderer."""

import glob
import os
import threading
import time

PAINTER_CACHE_DIR = "data/utils/painter_cache/"
painter_disk_cache_lock = threading.RLock()


def cleanup_painter_disk_cache(max_age_days: int = 7, *, cache_dir: str = PAINTER_CACHE_DIR) -> int:
    """Delete expired PNGs under the same lock used by Painter's reads and writes."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    with painter_disk_cache_lock:
        for path in glob.glob(os.path.join(cache_dir, "*.png")):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    return removed
