# This file is a public placeholder. The proprietary implementation is not
# distributed with this repository.
#
# For deployment, replace or bind-mount this file with the real drawer.py:
#   Docker Compose volume:
#     - "/path/to/real/drawer.py:/app/haruki_drawing_api/src/sekai/mysekai/drawer.py"
#
# The real implementation file should be named drawer.real.py locally and is
# listed in .gitignore. If present alongside this file, rename it to drawer.py
# before running outside of Docker.

from PIL import Image

from .model import (
    MysekaiDoorUpgradeRequest,
    MysekaiFixtureDetailRequest,
    MysekaiFixtureListRequest,
    MysekaiMsrMapRequest,
    MysekaiMusicrecordRequest,
    MysekaiResourceRequest,
    MysekaiTalkListRequest,
)

_NOT_IMPL_MSG = "drawer.py is a placeholder. Mount or replace it with the real implementation before running."


async def compose_mysekai_resource_image(rqd: MysekaiResourceRequest) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def compose_mysekai_msr_map_image(rqd: MysekaiMsrMapRequest) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def compose_mysekai_fixture_list_image(rqd: MysekaiFixtureListRequest) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def compose_mysekai_fixture_detail_image(rqds: list[MysekaiFixtureDetailRequest]) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def compose_mysekai_door_upgrade_image(rqd: MysekaiDoorUpgradeRequest) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def compose_mysekai_musicrecord_image(rqd: MysekaiMusicrecordRequest) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)


async def compose_mysekai_talk_list_image(rqd: MysekaiTalkListRequest) -> Image.Image:
    raise NotImplementedError(_NOT_IMPL_MSG)
