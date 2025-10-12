from PIL import Image
from pathlib import Path
from datetime import timedelta, datetime

def get_readable_timedelta(delta: timedelta, precision: str = 'm', use_en_unit=False) -> str:
    """
    将时间段转换为可读字符串
    """
    match precision:
        case 's': precision = 3
        case 'm': precision = 2
        case 'h': precision = 1
        case 'd': precision = 0

    s = int(delta.total_seconds())
    if s < 0: return "0秒" if not use_en_unit else "0s"
    d = s // (24 * 3600)
    s %= (24 * 3600)
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
        ret += f"{s}秒"   if not use_en_unit else f"{s}s"
    return ret

async def get_img_from_path(base_path: Path, path: str) -> Image.Image:
    """
    通过路径获取图片
    """
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
    l = 0
    for c in s:
        l += 1 if ord(c) < 128 else 2
    return l

def get_readable_datetime(t: datetime, show_original_time=True, use_en_unit=False):
    """
    将时间点转换为可读字符串
    """
    day_unit, hour_unit, minute_unit, second_unit = ("天", "小时", "分钟", "秒") if not use_en_unit else ("d", "h", "m", "s")
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
    if s is None: return "<None>"
    l = 0
    for i, c in enumerate(s):
        if l >= limit:
            return s[:i] + "..."
        l += 1 if ord(c) < 128 else 2
    return s