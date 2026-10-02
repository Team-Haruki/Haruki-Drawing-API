# This file is a public placeholder. The proprietary implementation of the MySekai
# shop / bulk harvest / blueprint term views is not distributed with this repository.
#
# For deployment, replace or bind-mount this file with the real content_drawer.py:
#   Docker Compose volume:
#     - "/path/to/real/content_drawer.py:/app/haruki_drawing_api/src/sekai/mysekai/content_drawer.py"
#
# The real implementation file should be named content_drawer.real.py locally and is
# listed in .gitignore. If present alongside this file, rename it to content_drawer.py
# before running outside of Docker.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

    from src.core.image_payload import EncodedImagePayload

    from .model import MysekaiBlueprintTermRequest, MysekaiBulkHarvestRequest, MysekaiShopRequest

_NOT_IMPL_MSG = "content_drawer.py is a placeholder. Mount or replace it with the real implementation before running."


async def compose_mysekai_shop_image(rqd: MysekaiShopRequest) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def compose_mysekai_bulk_harvest_image(rqd: MysekaiBulkHarvestRequest) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def compose_mysekai_blueprint_term_image(rqd: MysekaiBlueprintTermRequest) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)


# Skia (IRPainter) render path. There is no Pillow fallback any more: a None payload only
# surfaces as the generic "Native rendering failed" error from require_native_payload. So the
# placeholder raises instead, and the route's 500 names the missing implementation.
async def try_render_mysekai_shop_payload(rqd: MysekaiShopRequest) -> EncodedImagePayload | None:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def try_render_mysekai_bulk_harvest_payload(rqd: MysekaiBulkHarvestRequest) -> EncodedImagePayload | None:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def try_render_mysekai_blueprint_term_payload(rqd: MysekaiBlueprintTermRequest) -> EncodedImagePayload | None:
    raise NotImplementedError(_NOT_IMPL_MSG)
