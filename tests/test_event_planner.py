import pytest

from src.sekai.event.drawer import compose_event_planner_image
from src.sekai.event.model import (
    EventPlannerBoostRow,
    EventPlannerDeckCard,
    EventPlannerRequest,
    EventPlannerSong,
)
from src.sekai.profile.model import CardFullThumbnailRequest, DetailedProfileCardRequest


def _thumb(card_id: int) -> CardFullThumbnailRequest:
    return CardFullThumbnailRequest(
        card_id=card_id,
        card_thumbnail_path="res001_no001_normal.png",
        rare="rarity_4",
        frame_img_path="lunabot_static_images/card/frame_rarity_4.png",
        attr_img_path="lunabot_static_images/card/attr_icon_cool.png",
        rare_img_path="lunabot_static_images/card/rare_star_normal.png",
        train_rank=None,
        level=60,
        is_after_training=False,
    )


@pytest.mark.anyio
async def test_compose_event_planner_image():
    request = EventPlannerRequest(
        title="活动规划",
        region="cn",
        event_id=154,
        event_name="测试活动",
        profile=DetailedProfileCardRequest(
            id="123456",
            region="CN",
            nickname="Tester",
            source="suite",
            update_time=1779378368000,
            is_hide_uid=False,
            leader_image_path="res001_no001_normal.png",
        ),
        target_point=12_000_000,
        current_point=3_200_000,
        remaining_point=8_800_000,
        daily_point=1_600_000,
        target_source="直接输入",
        deck_summary="当前主队 / 综合力 320,000 / 活动加成 315% / 协力实效 150%",
        deck_total_power=320_000,
        deck_event_bonus=315,
        deck_skill_up=150,
        deck_cards=[
            EventPlannerDeckCard(
                card_thumbnail=_thumb(1000 + idx), skill_level="4", skill_rate=150, event_bonus_rate=75
            )
            for idx in range(5)
        ],
        songs=[
            EventPlannerSong(
                music_id=1,
                query="虾",
                title="独りんぼエンヴィー",
                music_cover_path="jacket_s_001.png",
                difficulty="expert",
                rows=[
                    EventPlannerBoostRow(boost=5, point_per_play=59_800, plays=148, energy=740),
                    EventPlannerBoostRow(boost=10, point_per_play=91_000, plays=97, energy=970),
                ],
            )
        ],
        warnings=["未指定活动，已使用当前活动"],
    )

    image = await compose_event_planner_image(request)

    assert image.width > 0
    assert image.height > 0
