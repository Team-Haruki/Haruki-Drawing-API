#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sekai Drawing API - 简单运行脚本
使用方法: python run.py <json_file> [output_file]
"""

import sys
import json
import asyncio
import os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.card.drawer import CardDetailRequest, CardListRequest, CardBoxRequest, compose_card_detail_image, compose_card_list_image, compose_box_image


async def main():
    if len(sys.argv) < 2:
        print("使用方法: python run.py <json_file> [output_file]")
        print("示例: python run.py input.json output.png")
        sys.exit(1)

    json_file = Path(sys.argv[1])

    if not json_file.exists():
        print(f"错误: JSON文件不存在: {json_file}")
        sys.exit(1)

    # 生成输出文件名
    output_dir = Path(r"D:\pjskdata\output")
    if len(sys.argv) >= 3:
        output_file = output_dir / sys.argv[2]
    else:
        output_file = output_dir / json_file.with_suffix('.png').name

    try:
        # 读取JSON参数
        with open(json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)

        # 根据JSON数据类型判断使用哪个函数
        if "card_info" in json_data:
            # 卡牌详情
            rqd = CardDetailRequest(**json_data)
            print(f"正在生成卡牌详情图片...")
            print(f"卡牌ID: {rqd.card_info.id}")
            img = await compose_card_detail_image(rqd)
        elif "cards" in json_data and "character_icon_paths" in json_data:
            # 卡牌一览
            rqd = CardBoxRequest(**json_data)
            print(f"正在生成卡牌一览图片...")
            print(f"卡牌数量: {len(rqd.cards)}")
            img = await compose_box_image(rqd)
        elif "cards" in json_data:
            # 卡牌列表
            rqd = CardListRequest(**json_data)
            print(f"正在生成卡牌列表图片...")
            print(f"卡牌数量: {len(rqd.cards)}")
            img = await compose_card_list_image(rqd)
        else:
            print(f"[ERROR] 未知的JSON格式")
            sys.exit(1)

        # 保存图片
        output_file.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_file, "PNG")

        print(f"[OK] 生成成功!")
        print(f"文件路径: {output_file}")
        print(f"图片尺寸: {img.size[0]}×{img.size[1]}")

    except Exception as e:
        print(f"[ERROR] 生成失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())