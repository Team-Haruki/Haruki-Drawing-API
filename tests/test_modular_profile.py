import pytest

from src.sekai.profile.drawer import compose_modular_profile_image
from src.sekai.profile.model import (
    BasicProfile,
    CardFullThumbnailRequest,
    CharacterRank,
    ModularProfileGrid,
    ModularProfilePreset,
    ModularProfileRenderRequest,
    ModularProfileWidget,
    ModularProfileWidgetFrame,
    MusicClearCount,
    ProfileDataSource,
)


def _thumb(card_id: int) -> CardFullThumbnailRequest:
    return CardFullThumbnailRequest(
        card_id=card_id,
        card_thumbnail_path="asset/cn-assets/startapp/thumbnail/chara/res016_no038_normal.png",
        rare="rarity_4",
        frame_img_path="lunabot_static_images/card/frame_rarity_4.png",
        attr_img_path="lunabot_static_images/card/attr_icon_cool.png",
        rare_img_path="lunabot_static_images/card/rare_star_normal.png",
        train_rank=None,
        level=60,
        is_after_training=False,
    )


def _widget(
    widget_id: str,
    widget_type: str,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    data: dict,
) -> ModularProfileWidget:
    return ModularProfileWidget(
        id=widget_id,
        type=widget_type,
        family="medium",
        title=title,
        frame=ModularProfileWidgetFrame(x=x, y=y, w=w, h=h),
        data=data,
    )


def _chara_icon_map() -> dict[str, str]:
    names = {
        1: "ick",
        2: "saki",
        3: "hnm",
        4: "shiho",
        5: "mnr",
        6: "hrk",
        7: "airi",
        8: "szk",
        9: "khn",
        10: "an",
        11: "akt",
        12: "toya",
        13: "tks",
        14: "emu",
        15: "nene",
        16: "rui",
        17: "knd",
        18: "mfy",
        19: "ena",
        20: "mzk",
        21: "miku",
        22: "rin",
        23: "len",
        24: "luka",
        25: "meiko",
        26: "kaito",
    }
    return {
        str(character_id): f"lunabot_static_images/chara_rank_icon/{name}.png" for character_id, name in names.items()
    }


@pytest.mark.anyio
async def test_compose_modular_profile_image():
    cards = [_thumb(1000 + idx) for idx in range(5)]
    character_ranks = [
        CharacterRank(character_id=character_id, rank=20 + character_id)
        for character_id in [
            21,
            22,
            23,
            24,
            25,
            26,
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
        ]
    ]
    request = ModularProfileRenderRequest(
        region="cn",
        profile=BasicProfile(
            id="7488328100774779663",
            region="CN",
            nickname="Tester",
            is_hide_uid=False,
            leader_image_path="res001_no001_normal.png",
            has_frame=False,
        ),
        data_sources=[ProfileDataSource(name="Suite数据", source="suite", update_time=1779378368000)],
        preset=ModularProfilePreset(
            id="default_widgets_v1",
            name="默认模块个人信息",
            source="cloud_default",
            theme={
                "background_color": "#1b1d2e",
                "panel_color": "#2b2d42",
                "panel_alpha": 0.92,
                "accent_color": "#e0175b",
                "text_color": "#f7f8ff",
                "muted_text_color": "#9296ad",
            },
            grid=ModularProfileGrid(columns=4, row_height=156, gutter=16, padding=24),
            widgets=[
                _widget(
                    "profile.summary",
                    "profile_summary",
                    "",
                    0,
                    0,
                    4,
                    1,
                    {"rank": 350, "total_power": 320000},
                ),
                _widget("deck.current", "deck_cards", "", 0, 1, 4, 1, {"cards": cards}),
                _widget(
                    "music.clear",
                    "fc_ap_clear",
                    "",
                    0,
                    2,
                    4,
                    1,
                    {
                        "counts": [
                            MusicClearCount(difficulty="easy", clear=500, fc=498, ap=460),
                            MusicClearCount(difficulty="master", clear=420, fc=320, ap=120),
                        ]
                    },
                ),
                _widget(
                    "character.single",
                    "single_character_rank",
                    "",
                    0,
                    3,
                    2,
                    1,
                    {"character_rank": character_ranks[0]},
                ),
                _widget("card.single", "single_card", "", 2, 3, 2, 1, {"card": cards[0]}),
                _widget(
                    "event.settlement",
                    "event_rank_pt",
                    "",
                    0,
                    4,
                    4,
                    1,
                    {
                        "banner_path": (
                            "asset/cn-assets/ondemand/event_story/event_takeoff_2025"
                            "/screen_image/banner_event_story.png"
                        ),
                        "badge_text": "活动 T500",
                        "event_honor": {
                            "honor_type": "normal",
                            "group_type": "event",
                            "honor_rarity": "middle",
                            "honor_level": 0,
                            "is_main_honor": False,
                            "honor_img_path": "asset/cn-assets/startapp/honor/honor_bg_event_yosoro_cp1/degree_sub.png",
                            "rank_img_path": (
                                "asset/cn-assets/startapp/honor/honor_top_002000_event_yosoro_cp1/rank_sub.png"
                            ),
                            "frame_img_path": "lunabot_static_images/honor/frame_degree_s_2.png",
                        },
                        "rank": 321,
                        "pt": 12_345_678,
                    },
                ),
                _widget(
                    "characters.full_radar",
                    "character_rank_full_radar",
                    "",
                    0,
                    5,
                    4,
                    4,
                    {"character_rank": character_ranks},
                ),
            ],
        ),
        resources={
            "chara_rank_icon_path_map": _chara_icon_map(),
            "lv_rank_bg_path": "lunabot_static_images/lv_rank_bg.png",
        },
    )

    image = await compose_modular_profile_image(request)

    assert image.width >= 720
    assert image.height >= 720
    assert image.getbbox() is not None
