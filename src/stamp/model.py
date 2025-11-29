from pydantic import BaseModel
from typing import List
from src.base.painter import Color

class StampData(BaseModel):
    r"""StampData

    表情数据，表情的id和路径

    Attributes
    ----------
    stamp_id : int
        表情的id
    image_path : str
        表情的路径
    text_color : Color
        id文字的颜色，
        这个颜色用来指示表情是否有可以用来制作的底图。
        默认为没有 (200, 0, 0, 255) 红色
    """
    stamp_id: int
    image_path: str
    text_color: Color = (200, 0, 0, 255)