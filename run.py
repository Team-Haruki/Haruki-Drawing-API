#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试脚本 - 用于测试Haruki-Drawing-API-Alpha的各项功能
"""
import asyncio
import json
import sys
import os
from pathlib import Path

# 添加src到路径
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.card.drawer import (
    compose_card_detail_image,
    compose_card_list_image,
    compose_box_image,
    CardBasicInfo,
    CardPowerInfo,
    SkillInfo,
    CardDetailRequest,
    CardListRequest,
    CardBoxRequest
)
from src.profile.drawer import DetailedProfileCardRequest
from datetime import datetime

def load_json_test_data(filename):
    """加载JSON测试数据"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"测试数据文件不存在: {filename}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON格式错误: {e}")
        return None

async def test_card_list():
    """测试卡牌列表功能"""
    print("=== 测试卡牌列表功能 ===")

    try:
        # 尝试加载JSON测试数据
        test_data = load_json_test_data('compose_card_list_image.json')

        if test_data:
            print(f"从JSON加载测试数据，包含 {len(test_data['cards'])} 张卡牌")
            cards = [CardBasicInfo(**card_data) for card_data in test_data['cards']]
            rqd = CardListRequest(
                cards=cards,
                region=test_data['region'],
                user_info=None,
                background_image_path=test_data.get('background_image_path')
            )
        else:
            # 使用默认测试数据
            print("使用默认测试数据")
            cards = [
                CardBasicInfo(
                    id=1011, character_id=20, character_name='晓山瑞希',
                    unit='school_refusal', release_at=1728723600000,
                    supply_type='非限定', card_rarity_type='rarity_4',
                    attr='cute', prefix='失われてしまったもの',
                    assetbundle_name='res020_no038', skill_type='score_up'
                ),
                CardBasicInfo(
                    id=1252, character_id=5, character_name='花里みのり',
                    unit='idol', release_at=1759201200000,
                    supply_type='BFes限定', card_rarity_type='rarity_4',
                    attr='cute', prefix='キラキラのアイドル！',
                    assetbundle_name='res005_no047', skill_type='score_up'
                )
            ]

            rqd = CardListRequest(
                cards=cards,
                region='jp',
                user_info=None,
                background_image_path=None
            )

        print("开始生成卡牌列表图片...")
        result = await compose_card_list_image(rqd)
        print("卡牌列表生成成功!")
        print(f"图片尺寸: {result.size}")

        # 保存图片
        output_path = 'test_card_list_output.png'
        result.save(output_path)
        print(f"图片已保存到: {output_path}")

        return True

    except Exception as e:
        print(f"卡牌列表测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_card_detail():
    """测试卡牌详情功能"""
    print("\n=== 测试卡牌详情功能 ===")

    try:
        # 尝试加载JSON测试数据
        test_data = load_json_test_data('compose_card_detail_image.json')

        if test_data:
            print("从JSON加载测试数据")
            card_info = CardBasicInfo(**test_data['card_info'])
            power_info = CardPowerInfo(**test_data['power_info'])
            skill_info = SkillInfo(**test_data['skill_info'])

            rqd = CardDetailRequest(
                card_info=card_info,
                region=test_data['region'],
                power_info=power_info,
                skill_info=skill_info,
                special_skill_info=None,
                event_info=None,
                gacha_info=None,
                card_images=test_data['card_images'],
                thumbnail_images=test_data['thumbnail_images'],
                costume_images=test_data['costume_images'],
                character_icon_path=test_data['character_icon_path'],
                unit_logo_path=test_data['unit_logo_path'],
                background_image_path=test_data.get('background_image_path'),
                event_attr_icon_path=test_data.get('event_attr_icon_path'),
                event_unit_icon_path=test_data.get('event_unit_icon_path'),
                event_chara_icon_path=test_data.get('event_chara_icon_path')
            )
        else:
            # 使用默认测试数据
            print("使用默认测试数据")
            card_info = CardBasicInfo(
                id=1011, character_id=20, character_name='晓山瑞希',
                unit='school_refusal', release_at=1728723600000,
                supply_type='非限定', card_rarity_type='rarity_4',
                attr='cute', prefix='失われてしまったもの',
                assetbundle_name='res020_no038', skill_type='score_up'
            )

            power_info = CardPowerInfo(
                power_total=31742, power1=10590, power2=10590, power3=10562
            )

            skill_info = SkillInfo(
                skill_id=1, skill_name='最高のステージに', skill_type='score_up',
                skill_detail='5秒間毎 PERFECT 判定の度为 110/115/120/130% UPする',
                skill_type_icon_path=None
            )

            rqd = CardDetailRequest(
                card_info=card_info,
                region='jp',
                power_info=power_info,
                skill_info=skill_info,
                special_skill_info=None,
                event_info=None,
                gacha_info=None,
                card_images=['card/normal/res020_no038_normal.png'],
                thumbnail_images=['card/thumbnails/res020_no038_normal.png'],
                costume_images=['costume/res020_no038_01.png', 'costume/res020_no038_02.png'],
                character_icon_path='character/chr_small_20.png',
                unit_logo_path='unit/logo_school_refusal.png',
                background_image_path=None
            )

        print("开始生成卡牌详情图片...")
        result = await compose_card_detail_image(rqd)
        print("卡牌详情生成成功!")
        print(f"图片尺寸: {result.size}")

        # 保存图片
        output_path = 'test_card_detail_output.png'
        result.save(output_path)
        print(f"图片已保存到: {output_path}")

        return True

    except Exception as e:
        print(f"卡牌详情测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_card_box():
    """测试卡牌一览功能（需要终章边框资源）"""
    print("\n=== 测试卡牌一览功能 ===")

    try:
        # 尝试加载JSON测试数据
        test_data = load_json_test_data('compose_box_image.json')

        if test_data:
            print("从JSON加载测试数据")
            cards = [CardBasicInfo(**card_data) for card_data in test_data['cards']]

            # 创建用户信息对象
            user_info = None
            if 'user_info' in test_data:
                user_info = DetailedProfileCardRequest(**test_data['user_info'])
                print(f"加载用户信息: {user_info.nickname}, 边框: {user_info.has_frame}")

            rqd = CardBoxRequest(
                cards=cards,
                region=test_data['region'],
                user_info=user_info,
                show_id=test_data.get('show_id', False),
                show_box=test_data.get('show_box', False),
                use_after_training=test_data.get('use_after_training', True),
                background_image_path=test_data.get('background_image_path'),
                card_image_paths=test_data.get('card_image_paths', []),
                character_icon_paths=test_data.get('character_icon_paths', {}),
                term_limited_icon_path=test_data.get('term_limited_icon_path'),
                fes_limited_icon_path=test_data.get('fes_limited_icon_path')
            )
        else:
            print("未找到compose_box_image.json，跳过box测试")
            return False

        print("开始生成卡牌一览图片...")
        result = await compose_box_image(rqd)
        print("卡牌一览生成成功!")
        print(f"图片尺寸: {result.size}")

        # 保存图片
        output_path = 'test_card_box_output.png'
        result.save(output_path)
        print(f"图片已保存到: {output_path}")

        return True

    except Exception as e:
        print(f"卡牌一览测试失败: {e}")
        print("这可能是因为缺少终章边框资源文件")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """主测试函数"""
    print("Haruki-Drawing-API-Alpha 功能测试")
    print("=" * 50)

    results = []

    # 测试卡牌列表
    results.append(await test_card_list())

    # 测试卡牌详情
    results.append(await test_card_detail())

    # 测试卡牌一览（box）
    results.append(await test_card_box())

    # 总结
    print("\n" + "=" * 50)
    print("测试结果总结:")
    test_names = ["卡牌列表", "卡牌详情", "卡牌一览"]

    for i, (name, success) in enumerate(zip(test_names, results)):
        status = "通过" if success else "失败"
        print(f"  {name}: {status}")

    passed = sum(results)
    total = len(results)
    print(f"\n总计: {passed}/{total} 项测试通过")

    if passed == total:
        print("所有测试通过!")
    else:
        print("部分测试失败，请检查错误信息")

if __name__ == "__main__":
    asyncio.run(main())