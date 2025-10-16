"""
卡牌相关的通用工具函数
"""

from PIL import Image, ImageDraw
from typing import Optional
from src.base.configs import ASSETS_BASE_DIR
from src.base.utils import get_img_from_path


async def build_card_full_thumbnail(
    thumbnail_img: Image.Image,
    card_info,
    frame_img: Optional[Image.Image] = None,
    attr_img: Optional[Image.Image] = None,
    rare_img: Optional[Image.Image] = None,
    after_training: bool = False,
    user_card_info=None,
    custom_text: str = None
):
    """
    构建完整卡牌缩略图
    包含稀有度框体、属性图标、稀有度星标、训练等级图标等

    Args:
        thumbnail_img: 基础缩略图
        card_info: 卡牌信息对象，需要包含 card_rarity_type 等属性
        frame_img: 稀有度框体图片
        attr_img: 属性图标图片
        rare_img: 星级图标图片
        after_training: 是否为特训后状态
        user_card_info: 用户卡牌信息
        custom_text: 自定义文本

    Returns:
        处理后的完整缩略图
    """
    img = thumbnail_img.copy()
    img_w, img_h = img.size

    # 调整资源大小
    if frame_img:
        frame_img = frame_img.resize((img_w, img_h))

    if attr_img:
        attr_img = attr_img.resize((int(img_w * 0.22), int(img_h * 0.25)))

    # 稀有度星标数量
    if hasattr(card_info, 'card_rarity_type') and card_info.card_rarity_type == "rarity_birthday":
        rare_num = 1
    else:
        # 提取稀有度数字
        if hasattr(card_info, 'card_rarity_type'):
            rarity_num = int(card_info.card_rarity_type.split("_")[1]) if "_" in str(card_info.card_rarity_type) else 1
            rare_num = rarity_num
        else:
            rare_num = 1

    # 绘制各种元素到图片上
    from PIL import ImageDraw

    # 如果是用户卡片则绘制等级/加成
    if user_card_info and custom_text:
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
        try:
            from PIL import ImageFont
            font = ImageFont.load_default()
            draw.text((6, img_h - 31), custom_text, font=font, fill=(255, 255, 255, 255))
        except:
            pass
    elif user_card_info:
        # 支持多种字段命名方式
        level = None
        if hasattr(user_card_info, 'level'):
            level = user_card_info.level
        elif isinstance(user_card_info, dict):
            level = user_card_info.get('level')

        if level:
            draw = ImageDraw.Draw(img)

            draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
            try:
                # 导入字体获取函数
                from src.base.painter import get_font, DEFAULT_BOLD_FONT

                font = get_font(DEFAULT_BOLD_FONT, 20)


                draw.text((6, img_h - 31), f"Lv.{level}", font=font, fill=(255, 255, 255, 255))
            except Exception as e:
                # 如果字体加载失败，使用默认字体
                try:
                    font = ImageFont.load_default()
                    draw.text((6, img_h - 31), f"Lv.{level}", font=font, fill=(255, 255, 255, 255))
                except:
                    pass

    # 创建一个独立的图层来合成各个组件
    composite_img = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))

    # 首先将基础缩略图复制到合成图层（不使用mask）
    if img.mode == 'RGBA':
        composite_img.paste(img, (0, 0), img.split()[-1])
    else:
        composite_img.paste(img, (0, 0))

    # 绘制稀有度框体
    if frame_img:
        composite_img.paste(frame_img, (0, 0), frame_img)

    # 绘制训练等级（如果有用户卡牌信息）
    if user_card_info:
        # 支持多种字段命名方式
        master_rank = None
        if hasattr(user_card_info, 'master_rank'):
            master_rank = user_card_info.master_rank
        elif hasattr(user_card_info, 'masterRank'):
            master_rank = user_card_info.masterRank
        elif isinstance(user_card_info, dict):
            master_rank = user_card_info.get('masterRank') or user_card_info.get('master_rank')

        if master_rank:
            try:
                rank_path = f"card/train_rank_{master_rank}.png"

                rank_img = await get_img_from_path(ASSETS_BASE_DIR, rank_path)

                rank_img = rank_img.resize((int(img_w * 0.35), int(img_h * 0.35)))
                rank_img_w, rank_img_h = rank_img.size
                composite_img.paste(rank_img, (img_w - rank_img_w, img_h - rank_img_h), rank_img)
            except (FileNotFoundError, AttributeError):
                pass

    # 绘制属性图标（左上角）
    if attr_img:
        composite_img.paste(attr_img, (0, 0), attr_img)

    # 绘制稀有度星标（左下角）
    if rare_img:
        hoffset = 6
        voffset = 6 if not user_card_info else 24
        scale = 0.17 if not user_card_info else 0.15
        rare_img_resized = rare_img.resize((int(img_w * scale), int(img_h * scale)))
        rare_w, rare_h = rare_img_resized.size

        for i in range(rare_num):
            composite_img.paste(rare_img_resized, (hoffset + rare_w * i, img_h - rare_h - voffset), rare_img_resized)

    # 清理边缘的近黑色透明像素
    composite_img = _clean_edge_pixels(composite_img)

    # 使用高质量抗锯齿圆角处理
    composite_img = _apply_antialiased_rounded_corners(composite_img, radius=10)

    return composite_img


def _clean_edge_pixels(img: Image.Image) -> Image.Image:
    """
    清理图片边缘的近黑色透明像素，避免圆角处理后出现黑色边缘

    Args:
        img: 输入图片

    Returns:
        清理后的图片
    """
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    w, h = img.size
    pixels = img.load()

    # 清理边缘的近黑色透明像素
    for x in range(w):
        for y in range(h):
            r, g, b, a = pixels[x, y]
            # 如果是近黑色且透明度很低的像素，设为完全透明
            if a > 0 and a < 50 and r < 50 and g < 50 and b < 50:
                pixels[x, y] = (0, 0, 0, 0)

    return img


def _apply_antialiased_rounded_corners(img: Image.Image, radius: int = 10) -> Image.Image:
    """
    应用高质量抗锯齿圆角处理

    Args:
        img: 输入图片
        radius: 圆角半径

    Returns:
        应用圆角后的图片
    """
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    img_w, img_h = img.size

    # 使用4倍尺寸创建抗锯齿圆角
    scale = 4
    big_w, big_h = img_w * scale, img_h * scale

    # 创建大尺寸的mask
    big_mask = Image.new('L', (big_w, big_h), 0)
    big_draw = ImageDraw.Draw(big_mask)

    # 在大尺寸上绘制圆角矩形
    big_draw.rounded_rectangle(
        (0, 0, big_w, big_h),
        radius=radius * scale,
        fill=255
    )

    # 高质量重采样到目标尺寸
    mask = big_mask.resize((img_w, img_h), Image.Resampling.LANCZOS)

    # 应用mask
    img.putalpha(mask)

    return img


def apply_rounded_corners(img: Image.Image, radius: int = 10) -> Image.Image:
    """
    为图片应用圆角效果，使用抗锯齿处理

    Args:
        img: 输入图片
        radius: 圆角半径,默认10px

    Returns:
        应用圆角后的图片
    """
    # 清理边缘的近黑色透明像素
    img = _clean_edge_pixels(img)

    # 使用高质量抗锯齿圆角处理
    img = _apply_antialiased_rounded_corners(img, radius)

    return img


# ========== 卡牌相关辅助函数 ==========

def has_after_training(card):
    """
    判断卡牌是否有特训后版本

    Args:
        card: 卡牌信息对象

    Returns:
        bool: 是否有特训后版本
    """
    return card.card_rarity_type in ["rarity_3", "rarity_4"]

def only_has_after_training(card):
    """
    判断卡牌是否只有特训后版本（如生日卡）

    Args:
        card: 卡牌信息对象

    Returns:
        bool: 是否只有特训后版本
    """
    # 生日卡特殊处理
    return card.card_rarity_type == "rarity_birthday"

async def get_card_full_thumbnail(card, after_training=False, user_card_info=None):
    """
    获取卡牌完整缩略图

    Args:
        card: 卡牌信息对象
        after_training: 是否获取特训后版本
        user_card_info: 用户卡牌信息（包含等级、大师等级等）

    Returns:
        PIL.Image: 完整缩略图
    """
    # 构建缩略图路径（适配当前环境的路径结构）
    image_type = "after_training" if after_training else "normal"
    thumbnail_path = f"card/thumbnails/{card.assetbundle_name}_{image_type}.png"

    try:

        base_thumbnail = await get_img_from_path(ASSETS_BASE_DIR, thumbnail_path)

    except FileNotFoundError:
        # 如果找不到文件，返回None
        return None

    # 获取缩略图所需的资源
    try:
        # 稀有度框体
        frame_path = f"card/frame_{card.card_rarity_type}.png"
        frame_img = await get_img_from_path(ASSETS_BASE_DIR, frame_path)

    except FileNotFoundError:
        frame_img = None

    try:
        # 属性图标
        attr_path = f"card/attr_{card.attr}.png"

        attr_img = await get_img_from_path(ASSETS_BASE_DIR, attr_path)

    except FileNotFoundError:
        attr_img = None

    try:
        # 星级图标 - 根据lunabot逻辑选择正确的星星
        star_type = "after_training" if after_training else "normal"

        rare_img = await get_img_from_path(ASSETS_BASE_DIR, f"card/rare_star_{star_type}.png")

    except FileNotFoundError:
        rare_img = None

    # 构建完整缩略图
    full_thumbnail = await build_card_full_thumbnail(
        base_thumbnail,
        card,
        frame_img=frame_img,
        attr_img=attr_img,
        rare_img=rare_img,
        after_training=after_training,
        user_card_info=user_card_info
    )

    return full_thumbnail