from pydantic import BaseModel
from typing import List, Optional
from src.base.painter import Color

class StampData(BaseModel):
    r"""StampData

    表情数据，表情的id和路径

    Attributes
    ----------
    id : int
        表情的id
    image_path : str
        表情的路径
    text_color : Color
        id文字的颜色，
        这个颜色用来指示表情是否有可以用来制作的底图。
        默认为没有 (200, 0, 0, 255) 红色
    """
    id: int
    image_path: str
    text_color: Color = (200, 0, 0, 255)

class StampListRequest(BaseModel):
    r"""StampListRequest

    绘制表情列表所必需的数据

    Attributes
    ----------
    prompt_message : Optional[ str ] = None
        提示性文字
    stamps : List[ StampData ] = []
        表情列表
    """
    prompt_message: Optional[str] = None
    stamps: List[StampData] = []