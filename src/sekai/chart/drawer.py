from pathlib import Path

from PIL import Image
from pjsekai_scores_rs import Drawing, Score

from src.sekai.base.utils import TempFilePath, run_in_pool, screenshot
from src.settings import ASSETS_BASE_DIR

from .model import GenerateMusicChartRequest


async def generate_music_chart(rqd: GenerateMusicChartRequest) -> Image.Image:
    r"""generate_music_chart

    生成谱面图片

    Args
    ----
    rqd : GenerateMusicChartRequest
        生成谱面图片所必需的数据

    Returns
    -------
    PIL.Image.Image
    """
    style_sheet = ""
    if rqd.style_path:
        style_sheet = (ASSETS_BASE_DIR / rqd.style_path).read_text(encoding="utf-8")

    with TempFilePath("svg") as svg_path:

        def get_svg():
            score = Score.open(str(ASSETS_BASE_DIR / rqd.sus_path))
            score.set_meta(
                title=rqd.title,
                artist=rqd.artist,
                difficulty=rqd.difficulty,
                playlevel=str(rqd.play_level),
                jacket=(ASSETS_BASE_DIR / rqd.jacket_path).as_uri(),
                songid=str(rqd.music_id),
            )
            drawing = Drawing(
                note_host=(ASSETS_BASE_DIR / rqd.note_host).as_uri(),
                style_sheet=style_sheet,
                skill=rqd.skill,
                music_meta=rqd.music_meta,
                target_segment_seconds=rqd.target_segment_seconds,
            )
            svg_content = drawing.svg(score)
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg_content)
            return Path(svg_path).resolve().as_uri()

        svg_uri = await run_in_pool(get_svg)
        # 用浏览器微服务
        return await screenshot(svg_uri, format="png", full_page=True)
