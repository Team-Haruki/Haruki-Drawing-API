import asyncio
from datetime import datetime, timedelta
import io
import logging
import os
from os.path import join as pjoin
from pathlib import Path
from typing import Literal
from uuid import uuid4

import aiohttp
from PIL import Image

from src.settings import ASSETS_BASE_DIR, DEFAULT_THREAD_POOL_SIZE, SCREENSHOT_API_PATH, TMP_PATH


def get_readable_timedelta(delta: timedelta, precision: str = "m", use_en_unit=False) -> str:
    """
    将时间段转换为可读字符串
    """
    match precision:
        case "s":
            precision = 3
        case "m":
            precision = 2
        case "h":
            precision = 1
        case "d":
            precision = 0

    s = int(delta.total_seconds())
    if s < 0:
        return "0秒" if not use_en_unit else "0s"
    d = s // (24 * 3600)
    s %= 24 * 3600
    h = s // 3600
    s %= 3600
    m = s // 60
    s %= 60

    ret = ""
    if d > 0:
        ret += f"{d}天" if not use_en_unit else f"{d}d"
    if h > 0 and (precision >= 1 or not ret):
        ret += f"{h}小时" if not use_en_unit else f"{h}h"
    if m > 0 and (precision >= 2 or not ret):
        ret += f"{m}分钟" if not use_en_unit else f"{m}m"
    if s > 0 and (precision >= 3 or not ret):
        ret += f"{s}秒" if not use_en_unit else f"{s}s"
    return ret


async def get_img_from_path(base_path: Path, path: str) -> Image.Image:
    """
    通过路径获取图片
    """
    if path is None:
        raise ValueError("图片路径不能为空(None)")
    safe_path = path.lstrip("/")

    full_path = base_path / safe_path

    if not full_path.exists():
        raise FileNotFoundError(f"图片文件不存在: {full_path}")
    img = Image.open(full_path)
    return img


def get_str_display_length(s: str) -> int:
    """
    获取字符串的显示长度，中文字符算两个字符
    """
    length = 0
    for c in s:
        length += 1 if ord(c) < 128 else 2
    return length


def get_readable_datetime(t: datetime, show_original_time=True, use_en_unit=False):
    """
    将时间点转换为可读字符串
    """
    if not use_en_unit:
        day_unit, hour_unit, minute_unit, second_unit = ("天", "小时", "分钟", "秒")
    else:
        day_unit, hour_unit, minute_unit, second_unit = ("d", "h", "m", "s")
    now = datetime.now()
    diff = t - now
    text, suffix = "", "后"
    if diff.total_seconds() < 0:
        suffix = "前"
        diff = -diff
    if diff.total_seconds() < 60:
        text = f"{int(diff.total_seconds())}{second_unit}"
    elif diff.total_seconds() < 60 * 60:
        text = f"{int(diff.total_seconds() / 60)}{minute_unit}"
    elif diff.total_seconds() < 60 * 60 * 24:
        text = f"{int(diff.total_seconds() / 60 / 60)}{hour_unit}{int(diff.total_seconds() / 60 % 60)}{minute_unit}"
    else:
        text = f"{diff.days}{day_unit}"
    text += suffix
    if show_original_time:
        text = f"{t.strftime('%Y-%m-%d %H:%M:%S')} ({text})"
    return text


def truncate(s: str, limit: int) -> str:
    """
    截断字符串到指定长度，中文字符算两个字符
    """
    s = str(s)
    if s is None:
        return "<None>"
    length = 0
    for i, c in enumerate(s):
        if length >= limit:
            return s[:i] + "..."
        length += 1 if ord(c) < 128 else 2
    return s


def get_float_str(value: float, precision: int = 2) -> str:
    """格式化浮点数"""
    format_str = f"{{0:.{precision}f}}".format(value)
    if "." in format_str:
        format_str = format_str.rstrip("0").rstrip(".")
    return format_str


async def concat_images(images, direction="h"):
    """水平或垂直拼接图片"""
    if not images:
        return None

    # 过滤掉None值
    images = [img for img in images if img is not None]
    if not images:
        return None

    if direction == "h":
        # 水平拼接
        total_width = sum(img.width for img in images)
        max_height = max(img.height for img in images)

        result = Image.new("RGBA", (total_width, max_height), (0, 0, 0, 0))
        x_offset = 0
        for img in images:
            result.paste(img, (x_offset, 0))
            x_offset += img.width
    else:
        # 垂直拼接
        max_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)

        result = Image.new("RGBA", (max_width, total_height), (0, 0, 0, 0))
        y_offset = 0
        for img in images:
            result.paste(img, (0, y_offset))
            y_offset += img.height

    return result


def plt_fig_to_image(fig, transparent=True) -> Image.Image:
    """
    matplot图像转换为PIL.Image对象
    """
    buf = io.BytesIO()
    fig.savefig(buf, transparent=transparent, format="png")
    buf.seek(0)
    img = Image.open(buf)
    img.load()
    return img


def get_chara_nickname(cid: int) -> str:
    return {
        1: "ick",
        2: "saki",
        3: "hnm",
        4: "shiho",
        5: "mnr",
        6: "hrk",
        7: "airi",
        8: "szk",
        9: "khn",
        10: "an",
        11: "akt",
        12: "toya",
        13: "tks",
        14: "emu",
        15: "nene",
        16: "rui",
        17: "knd",
        18: "mfy",
        19: "ena",
        20: "mzk",
        21: "miku",
        22: "rin",
        23: "len",
        24: "luka",
        25: "meiko",
        26: "kaito",
        27: "miku_light_sound",
        28: "miku_idol",
        29: "miku_street",
        30: "miku_theme_park",
        31: "miku_school_refusal",
        32: "rin",
        33: "rin",
        34: "rin",
        35: "rin",
        36: "rin",
        37: "len",
        38: "len",
        39: "len",
        40: "len",
        41: "len",
        42: "luka",
        43: "luka",
        44: "luka",
        45: "luka",
        46: "luka",
        47: "meiko",
        48: "meiko",
        49: "meiko",
        50: "meiko",
        51: "meiko",
        52: "kaito",
        53: "kaito",
        54: "kaito",
        55: "kaito",
        56: "kaito",
    }.get(cid)


# ======================= 临时文件 ======================= #

# generate music chart 使用，用于保存临时的svg图片使用浏览器截图生成png图片
# 这个路径和存放所需资源（note host和jacket）的路径都必须与那个浏览器微服务设置同一个volumes
TEMP_FILE_DIR = ASSETS_BASE_DIR / TMP_PATH
# 暂时保存的临时文件，当达到设定的时间时会将其中的文件删除
# TODO: 由于music chart 生成的svg图片不需要设置删除时间，因此还没有实现这个超时删除功能
_tmp_files_to_remove: list[tuple[str, datetime]] = []
# TODO: 如果一个临时文件已经生成了，而这时程序被关闭导致内存中的这个list丢失，
# 或者TempFilePath上下文没来得及退出，这样都会导致临时文件被保留，需要一个定时清理的程序
# 这样的定时程序之后再做


def rand_filename(ext: str) -> str:
    """
    rand_filename

    生成随机的文件名

    :param ext: 文件扩展名
    :type ext: str
    :return: 随机文件名
    :rtype: str
    """
    if ext.startswith("."):
        ext = ext[1:]
    return f"{uuid4()}.{ext}"


def create_folder(folder_path) -> str:
    """
    创建文件夹，返回文件夹路径
    """
    folder_path = str(folder_path)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


def create_parent_folder(file_path) -> str:
    """
    创建文件所在的文件夹，返回文件路径
    """
    parent_folder = os.path.dirname(file_path)
    create_folder(parent_folder)
    return file_path


def remove_file(file_path):
    """
    remove_file

    删除file_path指定的文件

    :param file_path: 说明
    """
    if os.path.exists(file_path):
        os.remove(file_path)


class TempFilePath:
    """
    临时文件路径
    remove_after为None表示使用后立即删除，否则延时删除
    """

    def __init__(self, ext: str, remove_after: timedelta | None = None):
        self.ext = ext
        self.path = os.path.abspath(pjoin(TEMP_FILE_DIR, rand_filename(ext)))
        self.remove_after = remove_after
        create_parent_folder(self.path)

    def __enter__(self) -> str:
        return self.path

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.remove_after is None:
            remove_file(self.path)
        else:
            _tmp_files_to_remove.append((self.path, datetime.now() + self.remove_after))


# ============================ 异步和任务 ============================ #

try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    logging.info("uvloop not installed, using default asyncio event loop")
    pass

from concurrent.futures import ThreadPoolExecutor

_default_pool_executor = ThreadPoolExecutor(max_workers=DEFAULT_THREAD_POOL_SIZE)


async def run_in_pool(func, *args, pool=None):
    if pool is None:
        global _default_pool_executor
        pool = _default_pool_executor
    return await asyncio.get_event_loop().run_in_executor(pool, func, *args)


# ============================ chromedp截图 ============================ #


async def screenshot(
    url: str,
    *,
    width: int = 1920,
    height: int = 1080,
    format: Literal["png", "jpeg", "webp"] = "png",
    quanlity: int = 90,
    wait_time: int = 0,
    wait_for: str | None = None,
    full_page: bool = False,
    headers: dict | None = None,
    user_agent: str | None = None,
    device_scale: float = 1.0,
    mobile: bool = False,
    landscape: bool = False,
    req_timeout: int = 30,
    clip: dict[Literal["x", "y", "width", "height"], float] | None = None,
) -> Image.Image:
    r"""screenshot

    调用chromedp截图微服务

    Args
    ----
    url : str
        资源连接，如果是本地资源，请使用file://+绝对路径，并且保证该路径被挂载到微服务的volumes下
    width : int = 1920
        窗口宽度
    height : int = 1080
        窗口高度
    format : Literal[ 'png', 'jpeg', 'webp' ] = 'png'
        返回的截图格式
    quanlity : int = 90
        压缩质量(1 - 100)
    wait_time : int = 0
        额外等待时间(毫秒)
    wait_for : Optional[ str ] = None
        等待元素出现(CSS选择器)
    full_page : bool = False
        全页面截图
    headers : Optional[ dict ] = None
        自定义请求头
    user_agent : Optional[ str ] = None
        自定义User-Agent
    device_scale : float = 1.0
        设备像素比
    mobile : bool = false
        移动端模拟
    landscape : bool = false
        横屏模式
    timeout : int = 30
        超时时间(秒, 最大120)
    clip : Optional[ dict[ Literal[ 'x', 'y', 'width', 'height' ], float ] ] = None
        裁剪区域
    """
    # locals() 获取当前所有的局部变量，在函数开头调用，获取所有的参数
    params = {k: v for k, v in locals().items() if v is not None}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request("post", SCREENSHOT_API_PATH, json=params) as resp:
                if resp.status != 200:
                    try:
                        error = await resp.json()
                        error = error["error"]
                    except Exception:
                        error = await resp.text
                    raise Exception(error)
                if resp.content_type not in ("image/jpeg", "image/webp", "image/png"):
                    raise Exception(f"未知的响应体类型{resp.content_type}")
                return Image.open(io.BytesIO(await resp.read()))
    except aiohttp.ClientConnectionError:
        raise Exception("连接截图API失败")
