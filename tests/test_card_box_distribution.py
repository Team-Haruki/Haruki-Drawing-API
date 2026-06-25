from src.sekai.card.drawer import (
    CARD_BOX_GROUP_BY_ATTR,
    _card_box_attr_content_width,
    _fallback_card_box_distribution,
    _single_character_progress,
)
from src.sekai.card.model import (
    CardBasic,
    CardBoxDistribution,
    CardBoxRequest,
    CardDistributionAttributeStat,
    CardDistributionCharacterStat,
    UserCard,
)


def test_card_box_request_accepts_distribution_contract():
    request = CardBoxRequest(
        cards=[
            UserCard(
                card=CardBasic(card_id=1001, character_id=5, rare="rarity_4", attr="cute"),
                has_card=True,
            )
        ],
        region="jp",
        show_id=False,
        show_box=False,
        unowned_only=True,
        group_by=CARD_BOX_GROUP_BY_ATTR,
        character_icon_paths={5: "static_images/chara_icon/mnr.png"},
        distribution=CardBoxDistribution(
            total_count=1,
            owned_count=1,
            owned_data=True,
            max_character_bar_count=1,
            max_attribute_bar_count=1,
            character_stats=[CardDistributionCharacterStat(character_id=5, count=1, owned_count=1, bar_count=1)],
            attribute_stats=[
                CardDistributionAttributeStat(
                    attr="cute",
                    label="可爱",
                    count=1,
                    owned_count=1,
                    bar_count=1,
                    character_stats=[
                        CardDistributionCharacterStat(character_id=5, count=1, owned_count=1, bar_count=1)
                    ],
                )
            ],
        ),
    )

    assert request.group_by == CARD_BOX_GROUP_BY_ATTR
    assert request.unowned_only is True
    assert request.distribution is not None
    assert request.distribution.attribute_stats[0].character_stats[0].character_id == 5


def test_card_box_distribution_fallback_counts_owned_cards():
    request = CardBoxRequest(
        cards=[
            UserCard(
                card=CardBasic(card_id=1001, character_id=5, rare="rarity_4", attr="cute"),
                has_card=True,
            ),
            UserCard(
                card=CardBasic(card_id=1002, character_id=5, rare="rarity_4", attr="cool"),
                has_card=False,
            ),
            UserCard(
                card=CardBasic(card_id=1003, character_id=6, rare="rarity_4", attr="cute"),
                has_card=True,
            ),
        ],
        region="jp",
        character_icon_paths={5: "mnr.png", 6: "hrk.png"},
        character_color_codes={5: "#33AAEE", 6: "#44CC88"},
    )

    distribution = _fallback_card_box_distribution(request)

    assert distribution.total_count == 3
    assert distribution.owned_count == 2
    assert distribution.max_character_bar_count == 2

    character5 = next(stat for stat in distribution.character_stats if stat.character_id == 5)
    assert character5.count == 2
    assert character5.owned_count == 1
    assert character5.bar_count == 2

    cute = next(stat for stat in distribution.attribute_stats if stat.attr == "cute")
    assert cute.count == 2
    assert cute.owned_count == 2
    assert cute.bar_count == 2


def test_single_character_progress_splits_rarity_and_total():
    request = CardBoxRequest(
        cards=[
            UserCard(
                card=CardBasic(card_id=1001, character_id=5, rare="rarity_1", attr="cute"),
                has_card=True,
            ),
            UserCard(
                card=CardBasic(card_id=1002, character_id=5, rare="rarity_2", attr="cool"),
                has_card=False,
            ),
            UserCard(
                card=CardBasic(card_id=1003, character_id=5, rare="rarity_3", attr="pure"),
                has_card=True,
            ),
            UserCard(
                card=CardBasic(card_id=1004, character_id=5, rare="rarity_4", attr="happy"),
                has_card=False,
            ),
            UserCard(
                card=CardBasic(card_id=1005, character_id=5, rare="rarity_birthday", attr="mysterious"),
                has_card=True,
            ),
        ],
        region="jp",
        character_icon_paths={5: "mnr.png"},
        distribution=CardBoxDistribution(total_count=5, owned_count=3, owned_data=True),
    )

    progress = _single_character_progress(request)

    assert progress is not None
    stats = progress["stats"]
    assert progress["show_total"] is True
    assert progress["visible_buckets"] == [
        ("rarity_1", "1"),
        ("rarity_2", "2"),
        ("rarity_3", "3"),
        ("rarity_4", "4"),
        ("birthday", "生日"),
    ]
    assert stats["rarity_1"] == {"owned": 1, "total": 1}
    assert stats["rarity_2"] == {"owned": 0, "total": 1}
    assert stats["rarity_3"] == {"owned": 1, "total": 1}
    assert stats["rarity_4"] == {"owned": 0, "total": 1}
    assert stats["birthday"] == {"owned": 1, "total": 1}
    assert stats["total"] == {"owned": 3, "total": 5}


def test_single_character_progress_uses_only_filtered_rarity_bucket():
    request = CardBoxRequest(
        cards=[
            UserCard(
                card=CardBasic(card_id=1001, character_id=20, rare="rarity_4", attr="cute"),
                has_card=True,
            ),
            UserCard(
                card=CardBasic(card_id=1002, character_id=20, rare="rarity_4", attr="cool"),
                has_card=False,
            ),
        ],
        region="jp",
        character_icon_paths={20: "mzk.png"},
        distribution=CardBoxDistribution(total_count=2, owned_count=1, owned_data=True),
    )

    progress = _single_character_progress(request)

    assert progress is not None
    assert progress["show_total"] is False
    assert progress["visible_buckets"] == [("rarity_4", "4")]
    assert progress["stats"]["rarity_4"] == {"owned": 1, "total": 2}
    assert progress["stats"]["total"] == {"owned": 1, "total": 2}


def test_attribute_group_content_width_uses_longest_group_and_header_minimum():
    width = _card_box_attr_content_width(
        {
            "cute": [(20, [1, 2])],
            "cool": [(20, [1, 2, 3, 4]), (19, [1])],
            "unknown": [(1, list(range(20)))],
        },
        best_height=2,
        sz=48,
        sep=4,
    )

    cool_width = (48 * 2 + 4) + 4 + 48
    header_min_width = 24 + 8 + 90 + 10 + 72 + 10 + 170
    assert width == 16 * 2 + max(cool_width, header_min_width)
