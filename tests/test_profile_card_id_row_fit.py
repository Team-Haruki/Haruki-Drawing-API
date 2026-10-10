"""The profile card's ID row (region chip + UID + timezone) must fit the card's text column.

An unmasked 19-digit ID plus a long timezone made the row 4 px wider than the column, and
pages that measure the card before layout (MySekai resource) failed with "Content size is too large".
"""

from __future__ import annotations

import asyncio

import pytest

from src.sekai.profile import drawer
from src.sekai.profile.model import BasicProfile, ProfileCardRequest, ProfileDataSource


@pytest.mark.parametrize("timezone", ["Asia/Shanghai", "America/Argentina/Buenos_Aires", "UTC"])
@pytest.mark.parametrize("hide_uid", [False, True])
@pytest.mark.parametrize("region_label", [None, "国际服(EN)"])
def test_profile_card_id_row_never_outgrows_the_card(timezone: str, hide_uid: bool, region_label: str | None) -> None:
    rqd = ProfileCardRequest(
        timezone=timezone,
        profile=BasicProfile(
            id="7485938033335467520",
            region="en",
            nickname="星雲夏希",
            is_hide_uid=hide_uid,
            leader_image_path="static_images/skill_score_up.png",
            has_frame=False,
            region_label=region_label,
        ),
        data_sources=[
            ProfileDataSource(name="Suite数据", source="suite", update_time=1719100000000),
            ProfileDataSource(name="Mysekai数据", source="mysekai", update_time=1719100000000),
        ],
        rank=380,
        mysekai_level=42,
    )
    identity = drawer._build_profile_card_identity_module(rqd, rqd.data_sources)
    width, _ = identity._get_self_size()  # raised "Content size is too large" before the fix
    assert width <= drawer._CARD_TEXT_W
    card = asyncio.run(drawer.get_profile_card(rqd))
    assert card._get_self_size()[0] <= drawer._CARD_W
