from PIL import Image
from .pjsekai.scores import Score, Drawing
from .pjsekai.scores.score import Meta
from .model import GenerateMusicChartRequest
from src.sekai.base.utils import TempFilePath, run_in_pool, screenshot
from src.sekai.base.configs import ASSETS_BASE_DIR
import os
import io
async def generate_music_chart(
        rqd: GenerateMusicChartRequest
)->Image.Image:
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
    style_sheet = ''
    if rqd.style_path:
        style_sheet = (ASSETS_BASE_DIR / rqd.style_path).read_text(encoding='utf-8')
    
    with TempFilePath("svg") as svg_path:
        def get_svg():
            score = Score.open( ASSETS_BASE_DIR / rqd.sus_path, encoding='UTF-8')
            score.meta = Meta(
                title=rqd.title,
                artist=rqd.artist,
                difficulty=rqd.difficulty,
                playlevel=str(rqd.play_level),
                jacket=(ASSETS_BASE_DIR / rqd.jacket_path).as_uri(),
                songid=rqd.music_id
            )
            drawing = Drawing(
                score=score,
                style_sheet=style_sheet,
                note_host=(ASSETS_BASE_DIR / rqd.note_host).as_uri(),
                skill=rqd.skill,
                music_meta=rqd.music_meta,
                target_segment_seconds=rqd.target_segment_seconds
            )
            drawing.svg().saveas(svg_path)
        await run_in_pool(get_svg)
        # 用浏览器微服务
        return await screenshot(
            f"file://{os.path.abspath(svg_path)}",
            format='png',
            full_page=True
        )
