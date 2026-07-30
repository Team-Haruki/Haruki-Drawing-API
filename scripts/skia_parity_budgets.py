"""Accepted Pillow-vs-Skia pixel-diff ceilings for the real parity fixtures.

The 63 ordinary budgets were derived from ``out/parity-sweep-real/results.json``:

* mean: ``max(0.25, accepted_mean * 1.15 + 0.1)``
* p99: ``min(255, accepted_p99 * 1.05 + 2)``

Values are rounded upward (mean to 0.001, p99 to 0.1). The four custom-profile
variants deliberately retain the renderer's wider ``(2, 25)`` rotation budget;
the symbol/stamps fixtures are not captured yet, but still need an explicit
budget before they can enter the strict gate.
"""

from __future__ import annotations

PARITY_BUDGETS: dict[str, tuple[float, float]] = {
    "card_detail": (5.330, 144.8),
    "card_list": (4.137, 71.3),
    "card_box": (5.678, 117.5),
    "chart": (0.250, 2.0),
    "costume_list": (3.822, 78.7),
    "costume_detail": (2.652, 47.2),
    "deck_recommend": (4.096, 100.7),
    "education_challenge_live": (4.370, 113.3),
    "education_power_bonus": (6.009, 129.9),
    "education_area_item": (3.608, 60.8),
    "education_bonds": (4.904, 140.6),
    "education_leader_count": (4.045, 115.4),
    "education_character_mission_overview": (5.610, 152.2),
    "education_character_mission_all": (3.207, 38.8),
    "event_list": (0.934, 12.5),
    "event_detail": (1.906, 19.9),
    "event_record": (5.052, 139.6),
    "event_planner": (3.798, 94.4),
    "gacha_list": (9.154, 134.3),
    "gacha_detail": (5.347, 140.6),
    "honor": (26.927, 247.7),
    "honor_bonds": (31.541, 255.0),
    "honor_birthday": (22.298, 227.8),
    "honor_fcap": (18.513, 255.0),
    "inventory_list": (4.024, 85.0),
    "misc_alias_list": (3.514, 69.2),
    "misc_alias_list_character": (2.869, 48.2),
    "misc_chara_birthday": (11.254, 198.4),
    "help_render": (0.758, 4.1),
    "music_detail": (3.578, 85.0),
    "music_brief_list": (2.916, 38.8),
    "music_list": (3.660, 38.8),
    "music_progress": (2.870, 44.0),
    "music_rewards_detail": (2.874, 46.1),
    "music_rewards_basic": (5.505, 128.0),
    "mysekai_resource": (2.680, 33.5),
    "mysekai_map": (1.460, 13.6),
    "mysekai_map_multi": (1.444, 15.7),
    "mysekai_fixture_list": (5.677, 116.5),
    "mysekai_fixture_detail": (4.512, 104.9),
    "mysekai_door_upgrade": (3.557, 43.0),
    "mysekai_music_record": (6.432, 119.6),
    "mysekai_talk_list": (3.675, 76.6),
    "mysekai_housing_competition": (1.912, 40.9),
    "profile": (2.514, 32.5),
    "custom_profile_card": (2.000, 25.0),
    "custom_profile_card_collections": (2.000, 25.0),
    "custom_profile_card_symbol": (2.000, 25.0),
    "custom_profile_card_stamps": (2.000, 25.0),
    "score_control": (4.565, 57.7),
    "score_custom_room": (4.935, 100.7),
    "score_music_meta": (9.781, 191.0),
    "score_music_board": (3.458, 69.2),
    "sk_line": (4.032, 79.7),
    "sk_line_predict": (3.973, 71.3),
    "sk_query": (13.756, 223.6),
    "sk_check_room": (10.449, 178.4),
    "sk_check_room_multi": (16.769, 235.1),
    "sk_csb": (8.820, 181.6),
    "sk_csb_large": (14.853, 244.6),
    "sk_speed": (4.090, 76.6),
    "sk_speed_daily": (4.131, 77.7),
    "sk_player_trace": (0.693, 7.3),
    "sk_rank_trace": (0.703, 7.3),
    "sk_winrate": (5.942, 107.0),
    "stamp_list": (6.555, 130.1),
    "vlive_list": (1.002, 9.4),
}
