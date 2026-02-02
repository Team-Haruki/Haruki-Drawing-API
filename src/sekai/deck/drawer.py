from PIL import Image

from src.sekai.base.configs import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT
from src.sekai.base.draw import BG_PADDING, DIFF_COLORS, SEKAI_BLUE_BG, Canvas, TextBox, add_watermark, roundrect_bg
from src.sekai.base.painter import WHITE
from src.sekai.base.plot import (
    FillBg,
    Frame,
    HSplit,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextStyle,
    VSplit,
)
from src.sekai.base.utils import get_img_from_path
from src.sekai.profile.drawer import (
    get_card_full_thumbnail,
    get_detailed_profile_card,
)

# 从 model.py 导入数据模型
from .model import (
    DeckRequest,
)

OMAKASE_MUSIC_ID = 10000
OMAKASE_MUSIC_DIFFS = ["master", "expert", "hard"]
RECOMMEND_ALG_NAMES = {
    "dfs": "暴力搜索",
    "sa": "模拟退火",
    "ga": "遗传算法",
}


async def compose_deck_recommend_image(rqd: DeckRequest) -> Image.Image:
    # 数据准备区
    use_max_profile = rqd.is_max_deck
    recommend_type = rqd.recommend_type
    event_id = rqd.event_id
    wl_chara_name = rqd.wl_chara_name
    live_type = rqd.live_type
    live_name = rqd.live_name
    chara_name = rqd.chara_name
    chara_icon = None
    if rqd.chara_icon_path:
        chara_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.chara_icon_path)
    wl_chara_icon = None
    if rqd.wl_chara_icon_path:
        wl_chara_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.wl_chara_icon_path)
    unit_logo = None
    if rqd.unit_logo_path:
        unit_logo = await get_img_from_path(ASSETS_BASE_DIR, rqd.unit_logo_path)
    attr_icon = None
    if rqd.attr_icon_path:
        attr_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.attr_icon_path)
    music_cover = None
    if rqd.music_cover_path:
        music_cover = await get_img_from_path(ASSETS_BASE_DIR, rqd.music_cover_path)
    canvas_thumbnail = None
    if rqd.canvas_thumbnail_path:
        canvas_thumbnail = await get_img_from_path(ASSETS_BASE_DIR, rqd.canvas_thumbnail_path)
    unit_filter = rqd.unit_filter
    attr_filter = rqd.attr_filter
    excluded_cards = rqd.excluded_cards or []
    result_decks = rqd.deck_data
    result_algs = rqd.model_name
    # 获取卡组卡牌缩略图
    card_imgs, card_keys = [], []

    for deck in rqd.deck_data:
        for card in deck.card_data:
            card_img = await get_card_full_thumbnail(card.card_thumbnail)
            card_imgs.append(card_img)
            card_keys.append(card.card_thumbnail.card_id)
    card_imgs = dict(zip(card_keys, card_imgs))

    # 绘图
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16).set_padding(16):
            if not use_max_profile:
                await get_detailed_profile_card(rqd.profile)

            with (
                VSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(16)
                .set_padding(16)
                .set_bg(roundrect_bg(alpha=80))
            ):
                # 标题
                with (
                    VSplit()
                    .set_content_align("lb")
                    .set_item_align("lb")
                    .set_sep(16)
                    .set_padding(16)
                    .set_bg(roundrect_bg(alpha=80))
                ):
                    title = ""

                    if recommend_type == "mysekai":
                        if event_id:
                            title += f"烤森活动#{event_id}组卡"
                        else:
                            title += "烤森模拟活动组卡"
                    elif recommend_type in ["challenge", "challenge_all"]:
                        title += "每日挑战组卡"
                    elif recommend_type in ["bonus", "wl_bonus"]:
                        if recommend_type == "bonus":
                            title += f"活动#{event_id}加成组卡"
                        elif recommend_type == "wl_bonus":
                            title += f"WL活动#{event_id}加成组卡"
                    else:
                        if recommend_type == "event":
                            title += f"活动#{event_id}组卡"
                        elif recommend_type == "wl":
                            if wl_chara_name:
                                title += f"WL活动#{event_id}组卡"
                            else:
                                title += "WL终章活动组卡"
                        elif recommend_type == "unit_attr":
                            title += "团队+颜色模拟活动组卡"
                        elif recommend_type == "no_event":
                            title += "无活动组卡"

                        if live_type == "multi":
                            title += f"({live_name})"
                        elif live_type == "solo":
                            title += "(单人)"
                        elif live_type == "auto":
                            title += "(AUTO)"

                    score_name = "PT"
                    if recommend_type in ["challenge", "challenge_all", "no_event"]:
                        score_name = "分数"

                    with HSplit().set_content_align("l").set_item_align("l").set_sep(16):
                        if recommend_type in ["event", "wl", "bonus", "wl_bonus", "mysekai"] and rqd.event_id:
                            if rqd.event_banner_path:
                                event_banner = await get_img_from_path(ASSETS_BASE_DIR, rqd.event_banner_path)
                                ImageBox(event_banner, size=(None, 50))
                            else:
                                title = rqd.event_name + " " + title

                        TextBox(
                            title,
                            TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(50, 50, 50)),
                            use_real_line_count=True,
                        )

                        if recommend_type == "challenge":
                            ImageBox(chara_icon, size=(None, 50))
                            TextBox(f"{chara_name}", TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(70, 70, 70)))
                        if recommend_type in ["wl"] and wl_chara_name:
                            ImageBox(wl_chara_icon, size=(None, 50))
                            TextBox(
                                f"{wl_chara_name} 章节", TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(70, 70, 70))
                            )
                        if unit_logo and attr_icon:
                            ImageBox(unit_logo, size=(None, 60))
                            ImageBox(attr_icon, size=(None, 50))

                        if use_max_profile:
                            TextBox(
                                f"({rqd.region}顶配)", TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(50, 50, 50))
                            )

                    if any(
                        [
                            unit_filter,
                            attr_filter,
                            excluded_cards,
                            rqd.multi_live_score_up_lower_bound,
                            rqd.keep_after_training_state,
                        ]
                    ):
                        with HSplit().set_content_align("l").set_item_align("l").set_sep(16):
                            setting_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
                            TextBox("卡组设置:", setting_style)
                            if unit_filter or attr_filter:
                                TextBox("仅", setting_style)
                                if unit_filter:
                                    ImageBox(
                                        await get_img_from_path(ASSETS_BASE_DIR, rqd.unit_logo_path), size=(None, 40)
                                    )
                                if attr_filter:
                                    ImageBox(
                                        await get_img_from_path(ASSETS_BASE_DIR, rqd.attr_icon_path), size=(None, 35)
                                    )
                                TextBox("上场", setting_style)
                            if excluded_cards:
                                TextBox(f"排除 {','.join(map(str, excluded_cards))}", setting_style)
                            if rqd.multi_live_score_up_lower_bound:
                                TextBox(f"实效≥{int(rqd.multi_live_score_up_lower_bound)}%", setting_style)
                            if rqd.keep_after_training_state:
                                TextBox("禁用双技能自动切换", setting_style)

                    if recommend_type in ["bonus", "wl_bonus"]:
                        TextBox(
                            "友情提醒：控分前请核对加成和体力设置",
                            TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(255, 50, 50)),
                        )
                        if recommend_type == "wl_bonus":
                            TextBox(
                                "WL仅支持自动组主队，支援队请自行配置",
                                TextStyle(font=DEFAULT_FONT, size=26, color=(50, 50, 50)),
                            )
                    elif recommend_type != "mysekai":
                        with HSplit().set_content_align("l").set_item_align("l").set_sep(16):
                            with Frame().set_size((50, 50)):
                                if rqd.music_id != OMAKASE_MUSIC_ID:
                                    Spacer(w=50, h=50).set_bg(FillBg(fill=DIFF_COLORS[rqd.music_diff])).set_offset(
                                        (6, 6)
                                    )
                                    if music_cover:
                                        ImageBox(music_cover, size=(50, 50))
                                else:
                                    if music_cover:
                                        ImageBox(music_cover, size=(50, 50), shadow=True)
                            TextBox(
                                rqd.music_title or "", TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(70, 70, 70))
                            )

                    info_text = ""

                    if use_max_profile:
                        info_text += "“顶配”为该服截止当前的全卡满养成配置(并非基于你的卡组计算)\n"

                    if info_text:
                        TextBox(
                            info_text.strip(),
                            TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(200, 75, 75)),
                            use_real_line_count=True,
                        )

                # 表格
                gh, vsp, voffset = 120, 12, 18
                with (
                    VSplit()
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_sep(16)
                    .set_padding(16)
                    .set_bg(roundrect_bg(alpha=80))
                ):
                    if len(rqd.deck_data) > 0:
                        with HSplit().set_content_align("c").set_item_align("c").set_sep(16).set_padding(0):
                            th_style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=(0, 0, 0))
                            th_style2 = TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=(75, 75, 75))
                            th_main_sign = "∇"
                            tb_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(70, 70, 70))

                            # 分数
                            if recommend_type not in ["bonus", "wl_bonus"]:
                                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsp).set_padding(8):
                                    target_score = rqd.target == "score"
                                    text = score_name + th_main_sign if target_score else score_name
                                    style = th_style1 if target_score else th_style2
                                    TextBox(text, style).set_h(gh // 2).set_content_align("c")
                                    Spacer(h=6)
                                    for i, (deck, alg) in enumerate(zip(result_decks, result_algs)):
                                        with Frame().set_content_align("rb"):
                                            alg_offset = 0
                                            # 挑战分数差距
                                            if recommend_type in ["challenge", "challenge_all"]:
                                                alg_offset = 20
                                                dlt = rqd.deck_data[i].challenge_score_delta or 0
                                                color = (50, 150, 50) if dlt > 0 else (150, 50, 50)
                                                TextBox(
                                                    f"{dlt:+d}", TextStyle(font=DEFAULT_FONT, size=15, color=color)
                                                ).set_offset((0, -8 - voffset * 2))
                                            # 算法
                                            TextBox(
                                                alg.upper(),
                                                TextStyle(font=DEFAULT_FONT, size=12, color=(125, 125, 125)),
                                            ).set_offset((0, -8 - voffset * 2 + alg_offset))
                                            # 分数
                                            score = deck.score
                                            if recommend_type == "no_event":
                                                score = deck.live_score
                                            elif recommend_type == "mysekai":
                                                score = deck.mysekai_event_point
                                            with Frame().set_content_align("c"):
                                                TextBox(str(score), tb_style).set_h(gh).set_content_align(
                                                    "c"
                                                ).set_offset((0, -voffset))

                            # 卡片
                            with VSplit().set_content_align("c").set_item_align("c").set_sep(vsp).set_padding(8):
                                TextBox("卡组", th_style2).set_h(gh // 2).set_content_align("c")
                                Spacer(h=6)
                                for deck in result_decks:
                                    with HSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(0):
                                        for card in deck.card_data:
                                            card_id = card.card_thumbnail.card_id
                                            character_id = card.chara_id
                                            event_bonus = card.event_bonus_rate
                                            ep1_read, ep2_read = card.is_before_story, card.is_after_story
                                            slv, sup = card.skill_level, int(card.skill_rate)

                                            with (
                                                VSplit()
                                                .set_content_align("c")
                                                .set_item_align("c")
                                                .set_sep(4)
                                                .set_padding(0)
                                                .set_h(gh)
                                            ):
                                                with Frame().set_content_align("rt"):
                                                    card_key = card_id
                                                    ImageBox(card_imgs[card_key], size=(None, 80))
                                                    if (rqd.fixed_cards_id and card_id in rqd.fixed_cards_id) or (
                                                        rqd.fixed_characters_id
                                                        and character_id in rqd.fixed_characters_id
                                                    ):
                                                        TextBox(
                                                            str(card_id),
                                                            TextStyle(font=DEFAULT_FONT, size=10, color=WHITE),
                                                        ).set_bg(RoundRectBg((200, 50, 50, 200), 2)).set_offset((-2, 0))
                                                    else:
                                                        TextBox(
                                                            str(card_id),
                                                            TextStyle(font=DEFAULT_FONT, size=10, color=(75, 75, 75)),
                                                        ).set_bg(RoundRectBg((255, 255, 255, 200), 2)).set_offset(
                                                            (-2, 0)
                                                        )
                                                    if card.has_canvas_bonus:
                                                        ImageBox(canvas_thumbnail, size=(11, 11)).set_offset((-32, 65))

                                                info_bg = RoundRectBg((255, 255, 255, 150), 2)
                                                with (
                                                    HSplit()
                                                    .set_content_align("c")
                                                    .set_item_align("c")
                                                    .set_sep(3)
                                                    .set_padding(0)
                                                ):
                                                    TextBox(
                                                        f"SLv.{slv}",
                                                        TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50)),
                                                    ).set_bg(info_bg)
                                                    TextBox(
                                                        f"↑{sup}%",
                                                        TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50)),
                                                    ).set_bg(info_bg)

                                                with (
                                                    HSplit()
                                                    .set_content_align("c")
                                                    .set_item_align("c")
                                                    .set_sep(3)
                                                    .set_padding(0)
                                                ):
                                                    show_event_bonus = event_bonus > 0
                                                    if show_event_bonus:
                                                        event_bonus_str = (
                                                            f"+{event_bonus:.1f}%"
                                                            if int(event_bonus) != event_bonus
                                                            else f"+{int(event_bonus)}%"
                                                        )
                                                        TextBox(
                                                            event_bonus_str,
                                                            TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50)),
                                                        ).set_bg(info_bg)
                                                    read_fg, read_bg = (50, 150, 50, 255), (255, 255, 255, 255)
                                                    noread_fg, noread_bg = (150, 50, 50, 255), (255, 255, 255, 255)
                                                    none_fg, none_bg = (255, 255, 255, 255), (255, 255, 255, 255)
                                                    ep1_fg = (
                                                        none_fg
                                                        if ep1_read is None
                                                        else (read_fg if ep1_read else noread_fg)
                                                    )
                                                    ep1_bg = (
                                                        none_bg
                                                        if ep1_read is None
                                                        else (read_bg if ep1_read else noread_bg)
                                                    )
                                                    ep2_fg = (
                                                        none_fg
                                                        if ep2_read is None
                                                        else (read_fg if ep2_read else noread_fg)
                                                    )
                                                    ep2_bg = (
                                                        none_bg
                                                        if ep2_read is None
                                                        else (read_bg if ep2_read else noread_bg)
                                                    )
                                                    TextBox(
                                                        "前" if show_event_bonus else "前篇",
                                                        TextStyle(font=DEFAULT_FONT, size=12, color=ep1_fg),
                                                    ).set_bg(info_bg)
                                                    TextBox(
                                                        "后" if show_event_bonus else "后篇",
                                                        TextStyle(font=DEFAULT_FONT, size=12, color=ep2_fg),
                                                    ).set_bg(info_bg)

                            # 加成
                            if recommend_type not in ["challenge", "challenge_all", "no_event"]:
                                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsp).set_padding(8):
                                    TextBox("加成", th_style2).set_h(gh // 2).set_content_align("c")
                                    Spacer(h=6)
                                    for deck in result_decks:
                                        if rqd.is_wl:
                                            bonus = f"{deck.event_bonus_rate:.1f}+{deck.support_deck_bonus_rate:.1f}%"
                                            total = f"{deck.event_bonus_rate + deck.support_deck_bonus_rate:.1f}%"
                                        else:
                                            bonus = None
                                            total = f"{deck.event_bonus_rate:.1f}%"
                                        with Frame().set_content_align("rb"):
                                            if bonus is not None:
                                                TextBox(
                                                    bonus, TextStyle(font=DEFAULT_FONT, size=14, color=(150, 150, 150))
                                                ).set_offset((0, -6 - voffset * 2))
                                            with Frame().set_content_align("c"):
                                                TextBox(total, tb_style).set_h(gh).set_content_align("c").set_offset(
                                                    (0, -voffset)
                                                )

                            # 实效
                            if rqd.live_type in ["multi", "cheerful"]:
                                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsp).set_padding(8):
                                    target_skill = rqd.target == "skill"
                                    text = "实效" + th_main_sign if target_skill else "实效"
                                    style = th_style1 if target_skill else th_style2
                                    TextBox(text, style).set_h(gh // 2).set_content_align("c")
                                    Spacer(h=6)
                                    for deck in result_decks:
                                        with Frame().set_content_align("rb"):
                                            if rqd.multi_live_teammate_score_up is not None:
                                                teammate_text = f"队友 {int(rqd.multi_live_teammate_score_up)}"
                                                TextBox(
                                                    teammate_text,
                                                    TextStyle(font=DEFAULT_FONT, size=14, color=(125, 125, 125)),
                                                ).set_offset((0, -8 - voffset * 2))
                                            with Frame().set_content_align("c"):
                                                TextBox(f"{deck.multi_live_score_up:.1f}%", tb_style).set_h(
                                                    gh
                                                ).set_content_align("c").set_offset((0, -voffset))

                            # 综合力和算法
                            if recommend_type not in ["bonus", "wl_bonus"]:
                                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsp).set_padding(8):
                                    target_power = rqd.target == "total_power"
                                    text = "综合力" + th_main_sign if target_power else "综合力"
                                    style = th_style1 if target_power else th_style2
                                    TextBox(text, style).set_h(gh // 2).set_content_align("c")
                                    Spacer(h=6)
                                    for deck in result_decks:
                                        with Frame().set_content_align("rb"):
                                            if rqd.multi_live_teammate_power is not None:
                                                teammate_text = f"队友 {int(rqd.multi_live_teammate_power)}"
                                                TextBox(
                                                    teammate_text,
                                                    TextStyle(font=DEFAULT_FONT, size=14, color=(125, 125, 125)),
                                                ).set_offset((0, -8 - voffset * 2))
                                            with Frame().set_content_align("c"):
                                                TextBox(str(deck.total_power), tb_style).set_h(gh).set_content_align(
                                                    "c"
                                                ).set_offset((0, -voffset))
                    # 找不到结果
                    else:
                        TextBox("未找到符合条件的卡组", TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(255, 50, 50)))

                # 说明
                with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                    tip_style = TextStyle(font=DEFAULT_FONT, size=16, color=(20, 20, 20))
                    if recommend_type not in ["bonus", "wl_bonus"]:
                        TextBox(
                            "12星卡默认全满，34星及生日卡默认满级，oc的bfes花前技能活动组卡为平均值，挑战组卡为最大值",
                            tip_style,
                        )
                    TextBox("功能移植并修改自33Kit https://3-3.dev/sekai/deck-recommend 算错概不负责", tip_style)
                    alg_and_cost_text = "本次组卡使用算法: "
                    for alg, cost in rqd.cost_times.items():
                        alg_name = RECOMMEND_ALG_NAMES[alg]
                        cost_time = f"{cost:.2f}s"
                        wait_time = f"{rqd.wait_times[alg]:.2f}s"
                        alg_and_cost_text += f"{alg_name} (等待{wait_time}/耗时{cost_time}) + "
                    alg_and_cost_text = alg_and_cost_text[:-3]
                    TextBox(alg_and_cost_text, tip_style)

    add_watermark(canvas)
    return await canvas.get_img()
