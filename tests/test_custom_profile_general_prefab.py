from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import pytest

from src.sekai.profile.custom_profile.general_prefab import (
    CHARA_LIST,
    GeneralAssetImageOp,
    GeneralFontRef,
    GeneralPrefabDisplayList,
    GeneralPrefabPalette,
    GeneralRoundedRectOp,
    GeneralSpriteChoiceOp,
    GeneralSpriteOp,
    GeneralTextOp,
    GeneralViewportOp,
    PillowGeneralPrefabAdapter,
    build_general_prefab_display_list,
)

FIXTURE_NAME = "<#DAC>星<#B68>雲<#9CF>夏<#FCA>希"
FIXTURE_COMMENT = "<#DAC>瑞希/<#849>ニーゴ/<#8D4>モモジャン"
PALETTE = GeneralPrefabPalette(
    input_tint=(0.921569, 0.921569, 0.94902, 0.8),
    dark_tint=(0.266667, 0.266667, 0.4, 1.0),
    total_line_tint=(0.654902, 0.654902, 0.737255, 1.0),
    text=(58, 65, 82, 255),
    label_text=(72, 84, 104, 255),
)
PROFILE_CONTEXT = {
    "user": {"name": FIXTURE_NAME},
    "userProfile": {"word": FIXTURE_COMMENT},
    "totalPower": {"totalPower": 400477},
}
LABELS = {"comment_title": "个性签名", "total_power": "综合力"}
STATS_LABELS = {
    **LABELS,
    "multi_live_title": "多人演出",
    "multi_live_count_suffix": "次",
    "challenge_live_title": "挑战演出",
    "challenge_live_solo": "单人",
    "character_rank_tab": "角色收藏等级",
    "challenge_stage_tab": "挑战舞台",
    "music_clear": "完成",
    "music_full_combo": "FULL COMBO",
    "music_all_perfect": "ALL PERFECT",
}
MUSIC_DIFFICULTIES = (
    ("easy", "EASY", (73, 211, 111, 255)),
    ("normal", "NORMAL", (65, 198, 222, 255)),
    ("hard", "HARD", (241, 201, 65, 255)),
    ("expert", "EXPERT", (236, 74, 115, 255)),
    ("master", "MASTER", (166, 89, 214, 255)),
    ("append", "APPEND", (218, 116, 221, 255)),
)


class FixtureMetrics:
    """Deterministic widths matching the important decisions in the captured fixture."""

    def text_bbox(
        self,
        text: str,
        font: GeneralFontRef,
        size: int,
    ) -> tuple[float, float, float, float]:
        del font
        if text == FIXTURE_NAME:
            width = 520 if size > 24 else 507
        elif text == "综合力":
            width = 90
        else:
            width = len(text) * 22
        return (0, 0, width, min(size, 30))


def _build(file_name: str, size: tuple[int, int]) -> GeneralPrefabDisplayList:
    display_list = build_general_prefab_display_list(
        file_name,
        size=size,
        profile_context=PROFILE_CONTEXT,
        labels=LABELS,
        metrics=FixtureMetrics(),
        palette=PALETTE,
    )
    assert display_list is not None
    return display_list


def test_edit_user_name_display_list_keeps_fixture_fit_and_unity_geometry() -> None:
    display_list = _build("EditUserName", (548, 64))

    assert display_list.ops == (
        GeneralSpriteOp(
            "bg_base_r16_wh",
            (0.0, 0.0, 548.0, 64.0),
            tint=PALETTE.input_tint,
            sliced_border=(21, 21, 21, 21),
        ),
        GeneralTextOp(FIXTURE_NAME, (38, 32), 24, PALETTE.text, "lm"),
        GeneralSpriteOp(
            "icon_write_wh",
            (490.0, 11.0, 532.0, 53.0),
            tint=PALETTE.dark_tint,
        ),
    )


def test_x_display_list_keeps_sprite_fallback_order_and_shared_text_layout() -> None:
    display_list = build_general_prefab_display_list(
        "X",
        size=(548, 64),
        profile_context={"userProfile": {"twitterId": "@sekai_test"}},
        labels={},
        metrics=FixtureMetrics(),
        palette=PALETTE,
    )

    assert display_list is not None
    assert display_list.ops == (
        GeneralSpriteOp(
            "bg_base_r16_wh",
            (0.0, 0.0, 548.0, 64.0),
            tint=PALETTE.input_tint,
            sliced_border=(21, 21, 21, 21),
        ),
        GeneralSpriteChoiceOp(
            ("x_icon", "icon_twitter_wh"),
            (7.0, 13.0, 45.0, 51.0),
            tint=PALETTE.dark_tint,
            fallback_text=GeneralTextOp("X", (26.0, 32.0), 30, PALETTE.text, "mm"),
        ),
        GeneralTextOp("@sekai_test", (85, 32), 30, PALETTE.text, "lm"),
    )


def test_comment_display_list_keeps_literal_tags_wrap_and_geometry() -> None:
    display_list = _build("Comment", (638, 190))

    assert display_list.ops[:2] == (
        GeneralTextOp("个性签名", pytest.approx((69.7, 13.5)), 22, PALETTE.label_text, "mm"),
        GeneralSpriteOp(
            "bg_base_r16_wh",
            (0.0, 50.0, 638.0, 190.0),
            tint=PALETTE.input_tint,
            sliced_border=(21, 21, 21, 21),
        ),
    )
    assert display_list.ops[2:] == (
        GeneralTextOp("<#DAC>瑞希/<#849>ニーゴ/<#8D4>", (16, 79), 30, PALETTE.text),
        GeneralTextOp("モモジャン", (16, 119), 30, PALETTE.text),
        GeneralSpriteOp(
            "icon_write_wh",
            (579.0, 61.0, 621.0, 103.0),
            tint=PALETTE.dark_tint,
        ),
    )


def test_total_power_display_list_uses_title_width_for_following_children() -> None:
    display_list = _build("TotalPower", (752, 76))

    assert display_list.ops == (
        GeneralTextOp("综合力", (-1.004974365234375, 38.0), 30, PALETTE.label_text, "lm"),
        GeneralSpriteOp(
            "bg_base_wh",
            (101.0, 22.0, 105.0, 54.0),
            tint=PALETTE.total_line_tint,
        ),
        GeneralSpriteOp(
            "icon_deckPower_wh",
            (121.0, 17.0, 157.0, 59.0),
            tint=PALETTE.dark_tint,
        ),
        GeneralTextOp("400477", (306.5, 38.0), 32, PALETTE.text, "rm"),
        GeneralSpriteOp(
            "btn_circle_h56_wh",
            pytest.approx((320.6000061035156, 2.0, 392.6000061035156, 74.0)),
        ),
        GeneralSpriteOp(
            "icon_infomation_wh",
            pytest.approx((352.6000061035156, 22.0, 360.6000061035156, 52.0)),
            tint=PALETTE.dark_tint,
        ),
    )


def test_pillow_adapter_replays_ops_in_order_and_reuses_measured_font() -> None:
    calls: list[tuple[str, tuple[float, float, float, float], Image.Resampling]] = []
    font_calls: list[tuple[int, bool]] = []

    def font_factory(size: int, bold: bool) -> ImageFont.ImageFont:
        font_calls.append((size, bold))
        return ImageFont.load_default()

    def paste_sprite(
        image: Image.Image,
        name: str,
        rect: tuple[float, float, float, float],
        *,
        tint=None,
        sliced_border=None,
        resample=Image.Resampling.LANCZOS,
    ) -> bool:
        del tint, sliced_border
        calls.append((name, rect, resample))
        ImageDraw.Draw(image).rectangle(tuple(round(value) for value in rect), fill=(255, 0, 0, 255))
        return True

    adapter = PillowGeneralPrefabAdapter(font_factory, paste_sprite)
    display_list = GeneralPrefabDisplayList(
        "test",
        (32, 20),
        (
            GeneralSpriteOp("sprite", (0, 0, 4, 4), sampling="bicubic"),
            GeneralTextOp("A", (8, 2), 12, (255, 255, 255, 255)),
            GeneralTextOp("B", (16, 2), 12, (255, 255, 255, 255)),
        ),
    )

    adapter.text_bbox("measure", GeneralFontRef(), 12)
    image = adapter.render(display_list)

    assert calls == [("sprite", (0, 0, 4, 4), Image.Resampling.BICUBIC)]
    assert font_calls == [(12, True)]
    assert image.getbbox() is not None


def test_pillow_adapter_sprite_choice_uses_first_available_then_text_fallback() -> None:
    attempts: list[str] = []

    def paste_sprite(image, name, rect, **kwargs):
        del rect, kwargs
        attempts.append(name)
        if name != "second":
            return False
        ImageDraw.Draw(image).rectangle((0, 0, 3, 3), fill=(255, 0, 0, 255))
        return True

    adapter = PillowGeneralPrefabAdapter(lambda *_args: ImageFont.load_default(), paste_sprite)
    choice = GeneralSpriteChoiceOp(
        ("first", "second", "third"),
        (0, 0, 4, 4),
        fallback_text=GeneralTextOp("X", (2, 2), 12, (255, 255, 255, 255), "mm"),
    )
    image = adapter.render(GeneralPrefabDisplayList("choice", (8, 8), (choice,)))

    assert attempts == ["first", "second"]
    assert image.getbbox() is not None

    attempts.clear()
    fallback = PillowGeneralPrefabAdapter(
        lambda *_args: ImageFont.load_default(),
        lambda _image, name, _rect, **_kwargs: attempts.append(name) or False,
    ).render(GeneralPrefabDisplayList("fallback", (16, 16), (choice,)))
    assert attempts == ["first", "second", "third"]
    assert fallback.getbbox() is not None


def test_shared_general_prefab_rejects_an_unmigrated_name() -> None:
    with pytest.raises(ValueError, match="unsupported shared"):
        _build("Deck", (783, 242))


def _build_stats(
    file_name: str,
    size: tuple[int, int],
    profile_context: dict,
    *,
    asset_paths: dict[str, Path | None] | None = None,
) -> GeneralPrefabDisplayList | None:
    return build_general_prefab_display_list(
        file_name,
        size=size,
        profile_context=profile_context,
        labels=STATS_LABELS,
        metrics=FixtureMetrics(),
        palette=PALETTE,
        asset_paths=asset_paths,
        music_difficulties=MUSIC_DIFFICULTIES,
    )


def test_multi_and_challenge_display_lists_preserve_noop_conditions_and_resource_policy(tmp_path: Path) -> None:
    assert _build_stats("MultiLive", (860, 166), {"userMultiLiveTopScoreCount": "bad"}) is None
    assert (
        _build_stats(
            "ChallengeLive",
            (860, 178),
            {"userChallengeLiveSoloResult": {"characterId": 0, "highScore": 0}},
        )
        is None
    )

    multi = _build_stats("MultiLive", (860, 166), {"userMultiLiveTopScoreCount": {}})
    assert multi is not None
    assert [op.text for op in multi.ops if isinstance(op, GeneralTextOp)][-2:] == ["SUPER\nSTAR", "0次"]

    icon_path = tmp_path / "chara.png"
    challenge = _build_stats(
        "ChallengeLive",
        (860, 178),
        {"userChallengeLiveSoloResult": {"characterId": 21, "highScore": 123456}},
        asset_paths={"challenge_character_icon": icon_path},
    )
    assert challenge is not None
    asset_ops = [op for op in challenge.ops if isinstance(op, GeneralAssetImageOp)]
    assert asset_ops == [
        GeneralAssetImageOp(
            "challenge_character_icon",
            icon_path,
            (158, 86, 222, 150),
            resource_policy="optional",
        )
    ]


def test_music_display_lists_mark_sprite_fallbacks_and_emit_all_difficulty_cells() -> None:
    context = {
        "userMusicDifficultyClearCount": [
            {"musicDifficultyType": "easy", "liveClear": 11, "fullCombo": 7, "allPerfect": 3},
            {"musicDifficultyType": "append", "liveClear": 5, "fullCombo": 2, "allPerfect": 1},
        ]
    }
    info = _build_stats("MusicClearInfo", (860, 318), context)
    assert info is not None
    fallback_sprites = [op for op in info.ops if isinstance(op, GeneralSpriteOp) and op.resource_policy == "fallback"]
    assert len(fallback_sprites) == 2
    assert all(isinstance(op.fallback, GeneralRoundedRectOp) for op in fallback_sprites)
    values = [op.text for op in info.ops if isinstance(op, GeneralTextOp)]
    assert values.count("11") == 1
    assert values.count("7") == 1
    assert values.count("5") == 1
    assert values.count("2") == 1
    assert sum(text in {"EASY", "NORMAL", "HARD", "EXPERT", "MASTER", "APPEND"} for text in values) == 12

    tabs = _build_stats("MusicClearSelectTabInfo", (860, 166), context)
    assert tabs is not None
    assert len([op for op in tabs.ops if isinstance(op, GeneralRoundedRectOp)]) == 9
    assert [op.text for op in tabs.ops if isinstance(op, GeneralTextOp)][:3] == [
        "完成",
        "FULL COMBO",
        "ALL PERFECT",
    ]


def test_pillow_adapter_replays_fallback_and_optional_asset_policy(tmp_path: Path) -> None:
    loaded_path = tmp_path / "loaded.png"

    def paste_missing(*args, **kwargs) -> bool:
        del args, kwargs
        return False

    def load_asset(path: Path | None) -> Image.Image | None:
        if path == loaded_path:
            return Image.new("RGBA", (2, 2), (0, 255, 0, 255))
        return None

    adapter = PillowGeneralPrefabAdapter(
        lambda size, bold: ImageFont.load_default(),
        paste_missing,
        load_asset,
    )
    display_list = GeneralPrefabDisplayList(
        "resource-policy",
        (16, 8),
        (
            GeneralSpriteOp(
                "missing",
                (0, 0, 4, 4),
                resource_policy="fallback",
                fallback=GeneralRoundedRectOp((0, 0, 4, 4), 0, (255, 0, 0, 255)),
            ),
            GeneralAssetImageOp("optional_missing", None, (4, 0, 8, 4), resource_policy="optional"),
            GeneralAssetImageOp("loaded", loaded_path, (8, 0, 12, 4), resource_policy="required"),
        ),
    )

    image = adapter.render(display_list)

    assert image.getpixel((1, 1)) == (255, 0, 0, 255)
    assert image.getpixel((5, 1)) == (0, 0, 0, 0)
    assert image.getpixel((9, 1)) == (0, 255, 0, 255)

    required_missing = GeneralPrefabDisplayList(
        "required",
        (4, 4),
        (GeneralAssetImageOp("required_missing", None, (0, 0, 4, 4), resource_policy="required"),),
    )
    with pytest.raises(FileNotFoundError, match="required_missing"):
        adapter.render(required_missing)


def test_character_rank_display_lists_keep_all_cells_and_exact_scroll_viewport(tmp_path: Path) -> None:
    profile_context = {
        "userCharacters": [
            {"characterId": 21, "characterRank": 86},
            {"characterId": 17, "characterRank": 104},
            {"characterId": 20, "characterRank": 136},
        ],
        # This data is deliberately irrelevant to the selected character-rank tab.
        "userChallengeLiveSoloStages": [{"characterId": 21, "rank": 150}],
    }
    icon_paths = {
        f"character_rank_icon:{character_id}": tmp_path / f"{character_id}.png"
        for _nickname, character_id in CHARA_LIST
        if character_id is not None
    }
    full = _build_stats(
        "CharacterRankAndChallengeStage",
        (908, 813),
        profile_context,
        asset_paths=icon_paths,
    )
    assert full is not None
    assert len(full.ops) == 108
    assert full.ops[:4] == (
        GeneralSpriteOp(
            "bg_base_r16_wh",
            (74.0, 24.0, 834.0, 81.0),
            tint=PALETTE.input_tint,
            sliced_border=(21, 21, 21, 21),
        ),
        GeneralSpriteOp(
            "bg_base_r16_wh",
            (74.0, 24.0, 454.0, 81.0),
            tint=(244, 246, 252, 230),
            sliced_border=(21, 21, 21, 21),
        ),
        GeneralTextOp("角色收藏等级", (264.0, 52.5), 27, PALETTE.text, "mm"),
        GeneralTextOp("挑战舞台", (644.0, 52.5), 27, (255, 255, 255, 230), "mm"),
    )
    assert full.ops[4:8] == (
        GeneralSpriteOp(
            "bg_base_round_h64_wh",
            (55.0, 124.0, 235.0, 189.0),
            tint=(0.266667, 0.866667, 1.0, 1.0),
            sliced_border=(37, 0, 37, 0),
        ),
        GeneralSpriteOp(
            "bg_base_circle_h96_wh",
            (40.0, 104.5, 124.0, 188.5),
            tint=(0.266667, 0.866667, 1.0, 1.0),
        ),
        GeneralAssetImageOp(
            "character_rank_icon:21",
            tmp_path / "21.png",
            (44.0, 108.5, 120.0, 184.5),
            resource_policy="optional",
        ),
        GeneralTextOp("86", (164.5, 157.5), 31, PALETTE.text, "mm"),
    )

    scroll = _build_stats(
        "CharacterRankAndChallengeStageScroll",
        (908, 550),
        profile_context,
        asset_paths=icon_paths,
    )
    assert scroll is not None
    assert len(scroll.ops) == 7
    viewport = scroll.ops[4]
    assert isinstance(viewport, GeneralViewportOp)
    assert viewport.offset == (24.0, 104.0)
    assert viewport.viewport_size == (860, 420)
    assert viewport.content_size == (860, 685)
    assert len(viewport.children) == 104
    assert viewport.children[:4] == (
        GeneralSpriteOp(
            "bg_base_round_h64_wh",
            (31.0, 19.5, 211.0, 84.5),
            tint=(0.266667, 0.866667, 1.0, 1.0),
            sliced_border=(37, 0, 37, 0),
        ),
        GeneralSpriteOp(
            "bg_base_circle_h96_wh",
            (16.0, 0.0, 100.0, 84.0),
            tint=(0.266667, 0.866667, 1.0, 1.0),
        ),
        GeneralAssetImageOp(
            "character_rank_icon:21",
            tmp_path / "21.png",
            (20.0, 4.0, 96.0, 80.0),
            resource_policy="optional",
        ),
        GeneralTextOp("86", (140.5, 53.0), 31, PALETTE.text, "mm"),
    )
    assert scroll.ops[-2:] == (
        GeneralSpriteOp(
            "bg_base_round_vertical_h6_wh",
            (885, 104, 891, 524),
            tint=(0.333333, 0.333333, 0.466667, 0.2),
            sliced_border=(0, 5, 0, 5),
        ),
        GeneralSpriteOp(
            "bg_base_round_vertical_h8_wh",
            (884.0, 122.0, 892.0, 239.60000000000002),
            tint=(0.333333, 0.333333, 0.466667, 1.0),
            sliced_border=(0, 6, 0, 6),
        ),
    )


def test_character_rank_builder_does_not_draw_challenge_stage_values() -> None:
    base_context = {"userCharacters": [{"characterId": 21, "characterRank": 28}]}
    first = _build_stats(
        "CharacterRankAndChallengeStage",
        (908, 813),
        {
            **base_context,
            "userChallengeLiveSoloStages": [{"characterId": 21, "rank": 1}],
        },
    )
    second = _build_stats(
        "CharacterRankAndChallengeStage",
        (908, 813),
        {
            **base_context,
            "userChallengeLiveSoloStages": [{"characterId": 21, "rank": 150}],
        },
    )

    assert first == second


def test_pillow_adapter_replays_all_viewport_children_before_hard_crop() -> None:
    replayed: list[str] = []

    def paste_sprite(
        image: Image.Image,
        name: str,
        rect: tuple[float, float, float, float],
        **kwargs,
    ) -> bool:
        del kwargs
        replayed.append(name)
        ImageDraw.Draw(image).rectangle(rect, fill=(255, 0, 0, 255))
        return True

    adapter = PillowGeneralPrefabAdapter(
        lambda size, bold: ImageFont.load_default(),
        paste_sprite,
    )
    display_list = GeneralPrefabDisplayList(
        "viewport",
        (8, 4),
        (
            GeneralViewportOp(
                offset=(2, 1),
                viewport_size=(4, 2),
                content_size=(4, 6),
                children=(
                    GeneralSpriteOp("visible", (0, 0, 4, 2)),
                    GeneralSpriteOp("fully-clipped", (0, 4, 4, 6)),
                ),
            ),
        ),
    )

    image = adapter.render(display_list)

    assert replayed == ["visible", "fully-clipped"]
    assert image.getpixel((3, 1)) == (255, 0, 0, 255)
    assert image.getpixel((3, 3)) == (0, 0, 0, 0)
