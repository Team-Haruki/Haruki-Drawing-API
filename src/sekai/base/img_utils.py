from pathlib import Path

import numpy as np
from PIL import Image

# ============================ 工具函数 ============================ #


def open_image(file_path: str | Path, load: bool = True) -> Image.Image:
    """
    打开图片文件并返回脱离文件句柄的 PIL Image 对象。

    参数 ``load`` 保留是为了兼容旧调用方；为保证并发安全，
    这里总是先解码再返回副本。
    """
    with Image.open(file_path) as img:
        img.load()
        return img.copy()


def multiply_image_by_color(img: Image.Image, color: tuple[int, ...]) -> Image.Image:
    """
    将图像的每个像素按通道乘以指定颜色。传 RGB 颜色时补 A=255,A 通道保持不变;
    传 RGBA 颜色时 A 通道同样参与相乘(``ImageTint("multiply")`` 依赖这一点对齐 Skia 的 Modulate)。
    """
    if img.mode.upper() not in ["RGB", "RGBA"]:
        img = img.convert("RGBA")
    channel = 4 if img.mode.upper() == "RGBA" else 3
    img_np = np.array(img, dtype=np.float32)
    if len(color) == 3:
        color = (*color, 255)
    color_np = np.array(color[:channel], dtype=np.float32)
    img_np = img_np * color_np / 255
    img_np = np.clip(img_np, 0, 255).astype(np.uint8)
    return Image.fromarray(img_np, mode=img.mode)


def mix_image_by_color(img: Image.Image, color: tuple[int, ...]) -> Image.Image:
    """
    将图像与指定颜色混合，使用颜色的A通道作为混合因子
    """
    if img.mode.upper() not in ["RGB", "RGBA"]:
        img = img.convert("RGBA")
    assert len(color) == 4, "Color must be a tuple of 4 elements (R, G, B, A)"
    # 仅混合 RGB 部分，用 A 作为混合因子
    factor = color[3] / 255.0
    color_np = np.array(color[:3], dtype=np.float32)
    img_np = np.array(img, dtype=np.float32)
    img_np[..., :3] = img_np[..., :3] * (1 - factor) + color_np * factor
    img_np = np.clip(img_np, 0, 255).astype(np.uint8)
    return Image.fromarray(img_np, mode=img.mode)


def adjust_image_alpha_inplace(img: Image.Image, value: float, method: str) -> None:
    """
    调整图像的透明度（原地修改）
    """
    assert method in ("set", "multiply")
    if isinstance(value, float):
        value = int(value * 255)
    if img.mode.upper() not in ["RGBA"]:
        img = img.convert("RGBA")
    alpha_channel = img.split()[-1]
    if method == "set":
        alpha_channel = Image.new("L", img.size, value)
    elif method == "multiply":
        alpha_channel = Image.eval(alpha_channel, lambda a: int(a * value / 255))
    img.putalpha(alpha_channel)


def center_crop_by_aspect_ratio(img: Image.Image, aspect_ratio: float) -> Image.Image:
    """
    根据给定的宽高比裁剪图像中心部分
    """
    if img.mode.upper() not in ["RGB", "RGBA"]:
        img = img.convert("RGBA")
    width, height = img.size
    target_width = width
    target_height = int(width / aspect_ratio)
    if target_height > height:
        target_height = height
        target_width = int(height * aspect_ratio)
    left = (width - target_width) // 2
    top = (height - target_height) // 2
    right = left + target_width
    bottom = top + target_height
    return img.crop((left, top, right, bottom))
