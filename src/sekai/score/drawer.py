from PIL import Image

from src.sekai.base.draw import (
    BG_PADDING,
    DIFF_COLORS,
    SEKAI_BLUE_BG,
    Canvas,
    TextBox,
    add_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import BLACK, WHITE
from src.sekai.base.plot import (
    FillBg,
    HSplit,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextStyle,
    VSplit,
)
from src.sekai.base.utils import get_img_from_path, truncate
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT

# =========================== 从.model导入数据类型 =========================== #
from .model import (
    CustomRoomScoreRequest,
    MusicBoardRequest,
    MusicMetaRequest,
    ScoreControlRequest,
)


# 合成控分图片
async def compose_score_control_image(
    rqd: ScoreControlRequest,
) -> Image.Image:
    r"""compose_score_control_image

    合成控分图片 (普通房间)

    Args
    ----
    rqd : ScoreControlRequest
        绘制控分图片所必须的数据

    Returns
    -------
    PIL.Image.Image
    """
    SHOW_SEG_LEN = 50

    def get_score_str(score: int) -> str:
        score_str = str(score)
        score_str = score_str[::-1]
        score_str = ",".join([score_str[i : i + 4] for i in range(0, len(score_str), 4)])
        return score_str[::-1]

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=16, color=(50, 50, 50))
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(255, 50, 50))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            # 标题
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(8):
                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(4):
                    music_cover = await get_img_from_path(ASSETS_BASE_DIR, rqd.music_cover_path)
                    ImageBox(music_cover, size=(20, 20), use_alpha_blend=False)
                    TextBox(f"【{rqd.music_id}】{rqd.music_title} (任意难度)", style1)
                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(4):
                    TextBox(f"歌曲基础分 {rqd.music_basic_point}   目标PT: ", style1)
                    TextBox(f" {rqd.target_point}", style3)
                if rqd.music_basic_point != 100 and rqd.target_point > 1000:
                    TextBox("基础分非100有误差风险，不推荐控较大PT", style3)
                if rqd.target_point > 3000:
                    TextBox("目标PT过大可能存在误差，推荐以多次控分", style3)
                TextBox("控分教程：选取表中一个活动加成和体力", style1)
                TextBox("游玩歌曲到对应分数范围内放置", style1)
                TextBox("友情提醒：控分前请核对加成和体力设置", style3)
                TextBox("特别注意核对加成是否多了0.5", style3)

            # 数据
            with (
                HSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(8)
                .set_omit_parent_bg(True)
                .set_item_bg(roundrect_bg(alpha=80))
            ):
                for i in range(0, len(rqd.valid_scores), SHOW_SEG_LEN):
                    scores = rqd.valid_scores[i : i + SHOW_SEG_LEN]
                    gh, gw1, gw2, gw3, gw4 = 20, 54, 48, 90, 90
                    bg1 = FillBg((255, 255, 255, 200))
                    bg2 = FillBg((255, 255, 255, 100))
                    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4).set_padding(8):
                        with HSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                            TextBox("加成", style1).set_bg(bg1).set_size((gw1, gh)).set_content_align("c")
                            TextBox("火", style1).set_bg(bg1).set_size((gw2, gh)).set_content_align("c")
                            TextBox("分数下限", style1).set_bg(bg1).set_size((gw3, gh)).set_content_align("c")
                            TextBox("分数上限", style1).set_bg(bg1).set_size((gw4, gh)).set_content_align("c")
                        for i, item in enumerate(scores):
                            bg = bg2 if i % 2 == 0 else bg1
                            score_min = get_score_str(item.score_min)
                            if score_min == "0":
                                score_min = "0 (放置)"
                            score_max = get_score_str(item.score_max)
                            with HSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                                TextBox(f"{item.event_bonus}", style2).set_bg(bg).set_size((gw1, gh)).set_content_align(
                                    "r"
                                )
                                TextBox(f"{item.boost}", style2).set_bg(bg).set_size((gw2, gh)).set_content_align("r")
                                TextBox(f"{score_min}", style2).set_bg(bg).set_size((gw3, gh)).set_content_align("r")
                                TextBox(f"{score_max}", style2).set_bg(bg).set_size((gw4, gh)).set_content_align("r")

    add_watermark(canvas)
    return await canvas.get_img()


# 合成自定义房间控分图片
async def compose_custom_room_score_control_image(rqd: CustomRoomScoreRequest) -> Image.Image:
    r"""compose_custom_room_score_control_image

    合成自定义房间控分图片

    Args
    ----
    rqd : CustomRoomScoreRequest
        绘制信息

    Returns
    -------
    PIL.Image.Image
    """
    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50))
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(200, 50, 50))

    # 合成图片
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with (
            VSplit()
            .set_content_align("lt")
            .set_item_align("lt")
            .set_sep(8)
            .set_padding(16)
            .set_bg(roundrect_bg(alpha=80))
        ):
            # 标题
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8):
                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(4):
                    TextBox("自定义房间控分 目标PT: ", style1)
                    TextBox(f" {rqd.target_point}", style3)
                TextBox(
                    """
该方法用于距离目标PT不足100时补救，使用方式:
1. 选定表格中的一组歌曲和活动加成
2. 自己配置好活动加成（注意检查小数），并将体力设置为0
3. 创建自定义房间，邀请另一个玩家进入房间
4. 选择该歌曲（任意难度），两个人均放置整首歌
""".strip(),
                    style2,
                    use_real_line_count=True,
                )
                TextBox(
                    """
若有上传Suite抓包，使用"/控分组卡"可以更快配出队伍
可用同PT系数的歌曲替代表中歌曲
数据来自x@SYLVIA0x0，目前验证不足仅供参考
""".strip(),
                    style2,
                    use_real_line_count=True,
                )

            # 数据
            gh, vsep, hsep = 40, 6, 6
            w1, w2, w3 = 140, 360, 100

            def bg_fn(i: int):
                return FillBg((255, 255, 255, 200)) if i % 2 == 0 else FillBg((255, 255, 255, 100))

            with HSplit().set_content_align("lt").set_item_align("lt").set_sep(hsep):
                # 活动加成
                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                    TextBox("活动加成", style1).set_size((w1, gh)).set_content_align("c").set_bg(bg_fn(0))
                    for i, (_, event_bonus) in enumerate(rqd.candidate_pairs):
                        bg = bg_fn(i + 1)
                        TextBox(f"{event_bonus} %", style2).set_size((w1, gh)).set_content_align("c").set_padding(
                            (16, 0)
                        ).set_bg(bg)
                # 歌曲
                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                    TextBox("可用歌曲", style1).set_size((w2, gh)).set_content_align("c").set_bg(bg_fn(0))
                    for i, (event_rate, _) in enumerate(rqd.candidate_pairs):
                        bg = bg_fn(i + 1)
                        with (
                            HSplit()
                            .set_content_align("c")
                            .set_item_align("c")
                            .set_sep(4)
                            .set_padding((8, 0))
                            .set_size((w2, gh))
                            .set_bg(bg)
                        ):
                            music_list = rqd.music_list_map.get(str(event_rate), [])
                            if not music_list:
                                music_list = rqd.music_list_map.get(int(event_rate), [])

                            if not music_list:
                                TextBox("-", style2)
                            else:
                                for j, music_info in enumerate(music_list):
                                    if j > 0:
                                        TextBox(" / ", style2)
                                    music_cover = await get_img_from_path(ASSETS_BASE_DIR, music_info["music_cover"])
                                    ImageBox(music_cover, size=(gh - 2, gh - 2), use_alpha_blend=False)
                                    TextBox(f"{truncate(music_info['music_title'], 16)}", style2)
                # PT系数
                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                    TextBox("PT系数", style1).set_size((w3, gh)).set_content_align("c").set_bg(bg_fn(0))
                    for i, (event_rate, _) in enumerate(rqd.candidate_pairs):
                        bg = bg_fn(i + 1)
                        TextBox(f"{event_rate}", style2).set_size((w3, gh)).set_content_align("c").set_padding(
                            (8, 0)
                        ).set_bg(bg)

    add_watermark(canvas)
    return await canvas.get_img()


# 合成歌曲meta图片
async def compose_music_meta_image(requests: list[MusicMetaRequest]) -> Image.Image:
    r"""compose_music_meta_image

    合成歌曲Meta图片，支持多首歌曲对比

    Args
    ----
    requests : List[MusicMetaRequest]
        歌曲Meta信息请求列表
    """
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with HSplit().set_content_align("lt").set_item_align("lt").set_sep(8):
            for rqd in requests:
                music_cover = await get_img_from_path(ASSETS_BASE_DIR, rqd.music_cover_path)

                style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK)
                style2 = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50))

                with (
                    VSplit()
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(8)
                    .set_bg(roundrect_bg(alpha=80))
                    .set_padding(16)
                ):
                    # 歌曲标题
                    with HSplit().set_content_align("l").set_item_align("l").set_sep(4):
                        ImageBox(music_cover, size=(48, 48), use_alpha_blend=False)
                        TextBox(
                            f"【{rqd.music_id}】{rqd.music_title}",
                            TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK),
                        )
                    TextBox(
                        "以日服为准，参考分数使用5张技能加分100%，数据来源：33Kit",
                        TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK),
                    )

                    # 信息
                    with (
                        VSplit()
                        .set_content_align("lt")
                        .set_item_align("lt")
                        .set_sep(8)
                        .set_item_bg(roundrect_bg(alpha=80))
                    ):
                        for meta in rqd.metas:
                            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(8):
                                diff = meta.difficulty

                                # Best skill order solo calculation (simplified logic for display)
                                # Assuming caller handled best skill order or just display index 1-5 ordered by value
                                # Here we just re-implement the sorting logic for visual
                                best_skill_order_solo = list(range(5))
                                # We need to access skill_score_solo, but it's in the model
                                scores_solo = meta.skill_score_solo
                                best_skill_order_solo.sort(key=lambda x: scores_solo[x], reverse=True)
                                best_skill_order_solo_idx = [best_skill_order_solo.index(i) for i in range(5)]

                                solo_skill, auto_skill, multi_skill = 1.0, 1.0, 1.8

                                solo_score = meta.base_score + sum(meta.skill_score_solo) * solo_skill
                                auto_score = meta.base_score_auto + sum(meta.skill_score_auto) * auto_skill
                                multi_score = (
                                    meta.base_score
                                    + sum(meta.skill_score_multi) * multi_skill
                                    + meta.fever_score * 0.5
                                    + 0.01875
                                )

                                solo_skill_account = sum(meta.skill_score_solo) * solo_skill / solo_score
                                auto_skill_account = sum(meta.skill_score_auto) * auto_skill / auto_score
                                multi_skill_account = sum(meta.skill_score_multi) * multi_skill / multi_score

                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox(
                                        diff.upper(), TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=WHITE)
                                    ).set_bg(RoundRectBg(DIFF_COLORS.get(diff, (0, 0, 0)), radius=6)).set_padding(4)
                                    Spacer(w=8)
                                    TextBox("时长", style1)
                                    TextBox(f" {meta.music_time}s", style2)
                                    TextBox("  每秒点击数", style1)
                                    TextBox(f" {meta.tap_count / meta.music_time:.1f}", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("基础分数", style1)
                                    TextBox("（单人）", style1)
                                    TextBox(f" {meta.base_score * 100:.1f}%", style2)
                                    TextBox("  （AUTO）", style1)
                                    TextBox(f" {meta.base_score_auto * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("Fever分数", style1)
                                    TextBox(f" {meta.fever_score * 100:.1f}%", style2)
                                    TextBox("  活动PT系数", style1)
                                    TextBox(f" {meta.event_rate:.0f}", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("技能分数（单人）", style1)
                                    for s in meta.skill_score_solo:
                                        TextBox(f"  {s * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("技能分数（多人）", style1)
                                    for s in meta.skill_score_multi:
                                        TextBox(f"  {s * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("技能分数（AUTO）", style1)
                                    for s in meta.skill_score_auto:
                                        TextBox(f"  {s * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("单人最优技能顺序（1-5代表强到弱的卡牌）", style1)
                                    for idx in best_skill_order_solo_idx:
                                        TextBox(f" {idx + 1}", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("参考分数", style1)
                                    TextBox("（单人）", style1)
                                    TextBox(f" {solo_score * 100:.1f}%", style2)
                                    TextBox("（AUTO）", style1)
                                    TextBox(f" {auto_score * 100:.1f}%", style2)
                                    TextBox("（多人）", style1)
                                    TextBox(f" {multi_score * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("技能占比", style1)
                                    TextBox("（单人）", style1)
                                    TextBox(f" {solo_skill_account * 100:.1f}%", style2)
                                    TextBox("（AUTO）", style1)
                                    TextBox(f" {auto_skill_account * 100:.1f}%", style2)
                                    TextBox("（多人）", style1)
                                    TextBox(f" {multi_skill_account * 100:.1f}%", style2)

    add_watermark(canvas)
    return await canvas.get_img()


# 合成歌曲排行图片
async def compose_music_board_image(
    rqd: MusicBoardRequest,
) -> Image.Image:
    r"""compose_music_board_image

    合成歌曲排行图片

    Args
    ----
    rqd : MusicBoardRequest
        绘制请求数据
    """
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK)
    item_style = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with (
            VSplit()
            .set_content_align("lt")
            .set_item_align("lt")
            .set_sep(8)
            .set_padding(16)
            .set_bg(roundrect_bg(alpha=80))
        ):
            # 标题
            TextBox(rqd.title_text, title_style, use_real_line_count=True)
            if rqd.description:
                TextBox(rqd.description, title_style, use_real_line_count=True)

            # 表格配置
            # 定义列: (标题, 宽度权重, 对齐方式)
            columns = [
                ("排名", 1.2, "c"),
                ("歌曲", 6.0, "l"),
                ("难度", 1.5, "c"),
            ]

            # 根据target添加动态列
            if rqd.target == "score":
                columns.append(("分数", 2.0, "c"))
            elif rqd.target in ("pt", "pt/time"):
                columns.append(("PT", 2.0, "c"))
                columns.append(("LIVE分数", 2.0, "c"))
            if rqd.target == "pt/time":
                columns.append(("PT/h", 2.0, "c"))

            columns.append(("技能占比", 2.0, "c"))

            if rqd.target in ("pt/time", "time"):
                columns.append(("周回/h", 2.0, "c"))

            if rqd.target in ("pt", "pt/time", "time"):
                columns.append(("PT系数", 1.5, "c"))

            columns.append(("时长", 1.5, "c"))
            columns.append(("每秒点击", 1.5, "c"))

            # 计算每列宽度
            # 使用固定单位宽度计算总宽
            unit_w = 60
            ratios = [c[1] for c in columns]

            # 这里的hsep由HSplit自动处理，不再手动计算
            gh = 40  # 行高
            hsep = 5
            vsep = 5

            def row_bg_fn(i: int):
                return FillBg((255, 255, 255, 160)) if i % 2 == 0 else FillBg((255, 255, 255, 60))

            # 主容器：水平排列各列
            with HSplit().set_content_align("c").set_item_align("c").set_sep(hsep):
                # Helper to create standard column logic
                # But headers are specific, and content is specific.
                # We will unroll the known columns and loop the dynamic ones if possible, or just unroll all.

                # 1. 排名 Column
                w = int(ratios[0] * unit_w)
                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                    # Header
                    TextBox("排名", title_style).set_size((w, gh)).set_content_align("c").set_bg(row_bg_fn(0))
                    # Data
                    for i, row in enumerate(rqd.items):
                        bg = row_bg_fn(i + 1)
                        style = item_style
                        if (row.music_id, row.difficulty) in rqd.spec_mid_diffs:
                            style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(255, 50, 50))
                        TextBox(f"#{row.rank}", style).set_size((w, gh)).set_content_align("c").set_bg(bg)

                # 2. 歌曲 Column
                w = int(ratios[1] * unit_w)
                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                    TextBox("歌曲", title_style).set_size((w, gh)).set_content_align("c").set_bg(row_bg_fn(0))
                    for i, row in enumerate(rqd.items):
                        bg = row_bg_fn(i + 1)
                        with (
                            HSplit()
                            .set_content_align("l")
                            .set_item_align("l")
                            .set_sep(4)
                            .set_padding((8, 0))
                            .set_size((w, gh))
                            .set_bg(bg)
                        ):
                            music_cover = await get_img_from_path(ASSETS_BASE_DIR, row.music_cover_path)
                            ImageBox(music_cover, size=(gh - 8, gh - 8), use_alpha_blend=False)
                            TextBox(f"{truncate(row.music_title, 18)}", item_style)

                # 3. 难度 Column
                w = int(ratios[2] * unit_w)
                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                    TextBox("难度", title_style).set_size((w, gh)).set_content_align("c").set_bg(row_bg_fn(0))
                    for i, row in enumerate(rqd.items):
                        diff_bg = FillBg(DIFF_COLORS.get(row.difficulty, (200, 200, 200)))
                        TextBox(f"{row.level}", TextStyle(DEFAULT_BOLD_FONT, 20, WHITE)).set_size(
                            (w, gh)
                        ).set_content_align("c").set_bg(diff_bg)

                # 动态列
                current_col_idx = 3

                # Helper for adding a simple text column
                def add_text_column(header_text, value_getter):
                    nonlocal current_col_idx
                    w_col = int(ratios[current_col_idx] * unit_w)
                    current_col_idx += 1
                    with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                        TextBox(header_text, title_style).set_size((w_col, gh)).set_content_align("c").set_bg(
                            row_bg_fn(0)
                        )
                        for i, row in enumerate(rqd.items):
                            bg = row_bg_fn(i + 1)
                            TextBox(value_getter(row), item_style).set_size((w_col, gh)).set_content_align("c").set_bg(
                                bg
                            )

                if rqd.target == "score":
                    add_text_column("分数", lambda r: f"{(r.live_type_score or 0) * 100:.1f}%")
                elif rqd.target in ("pt", "pt/time"):
                    add_text_column("PT", lambda r: f"{r.live_type_pt or 0}")
                    add_text_column("LIVE分数", lambda r: f"{(r.live_type_real_score or 0):.0f}")

                if rqd.target == "pt/time":
                    add_text_column("PT/h", lambda r: f"{(r.live_type_pt_per_hour or 0):.0f}")

                add_text_column("技能占比", lambda r: f"{(r.live_type_skill_account or 0) * 100:.1f}%")

                if rqd.target in ("pt/time", "time"):
                    add_text_column("周回/h", lambda r: f"{(r.play_count_per_hour or 0):.1f}")

                if rqd.target in ("pt", "pt/time", "time"):
                    add_text_column("PT系数", lambda r: f"{r.event_rate:.0f}")

                add_text_column("时长", lambda r: f"{r.music_time:.1f}")
                add_text_column("每秒点击", lambda r: f"{r.tps:.1f}")

    add_watermark(canvas)
    return await canvas.get_img()
