from io import BytesIO
import json

from PIL import Image
from pjsekai_scores_rs import Drawing, Score

from src.sekai.base.utils import run_in_pool
from src.settings import ASSETS_BASE_DIR, FONT_DIR

from .model import GenerateMusicChartRequest

CHART_FONT_FILENAMES = (
    "SourceHanSansSC-Regular.otf",
    "SourceHanSansSC-Bold.otf",
    "SourceHanSansSC-Heavy.otf",
    "TwitterColorEmoji-SVGinOT.ttf",
)


def chart_font_kwargs() -> dict[str, list[str]]:
    font_paths = [str(FONT_DIR / filename) for filename in CHART_FONT_FILENAMES if (FONT_DIR / filename).is_file()]
    if font_paths:
        return {"font_paths": font_paths}
    return {"font_dirs": [str(FONT_DIR)]}


def load_score(rqd: GenerateMusicChartRequest) -> Score:
    if rqd.chart_json is not None:
        if isinstance(rqd.chart_json, str):
            return Score.from_json(rqd.chart_json)
        return Score.from_json(json.dumps(rqd.chart_json, ensure_ascii=False))
    if not rqd.sus_path:
        raise ValueError("either chart_json or sus_path is required")
    return Score.open(str(ASSETS_BASE_DIR / rqd.sus_path))


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

    def render_png() -> Image.Image:
        score = load_score(rqd)
        score.set_meta(
            title=rqd.title,
            artist=rqd.artist,
            difficulty=rqd.difficulty,
            playlevel=str(rqd.play_level),
            jacket=str(ASSETS_BASE_DIR / rqd.jacket_path),
            songid=str(rqd.music_id),
        )
        drawing = Drawing(
            note_host=str(ASSETS_BASE_DIR / rqd.note_host),
            style_sheet=style_sheet,
            skill=rqd.skill,
            music_meta=rqd.music_meta,
            target_segment_seconds=rqd.target_segment_seconds,
            **chart_font_kwargs(),
        )
        png_bytes = drawing.png(score)
        image = Image.open(BytesIO(png_bytes))
        image.load()
        return image

    return await run_in_pool(render_png)
