# Sekai Drawing API

## 简单使用方法

### 1. 基本用法
```bash
python run.py <json_file> [output_file]
```

### 2. 示例
```bash
# 指定输出文件名
python run.py input.json output.png

# 使用默认输出文件名（与JSON文件同名的PNG文件）
python run.py input.json
```

### 3. 运行参数
- `json_file`: 包含卡牌信息的JSON文件路径
- `output_file`: 输出图片文件路径（可选，默认为JSON文件名+.png）

## JSON文件格式

### 卡牌详情 (CardDetailRequest)
```json
{
  "card_info": {
    "id": 1011,
    "character_id": 21,
    "character_name": "星街みなと",
    "unit": "vivid_bad",
    "release_at": 1728705600000,
    "supply_type": "永久",
    "card_rarity_type": "rarity_4",
    "attr": "cool",
    "prefix": "PERFECT DIVE",
    "assetbundle_name": "res021_no011",
    "skill_type": "score_up"
  },
  "region": "jp",
  "power_info": {
    "power_total": 31742,
    "power1": 10590,
    "power2": 10590,
    "power3": 10562
  },
  "skill_info": {
    "skill_id": 11,
    "skill_name": "PERFECT DIVE",
    "skill_type": "score_up",
    "skill_detail": "5秒間 PERFECT SCORE 110/115/120/130%UP",
    "skill_type_icon_path": "skill/skill_score_up.png",
    "skill_detail_cn": "5秒内 PERFECT SCORE 110/115/120/130%UP"
  },
  "special_skill_info": {
    "skill_id": 12,
    "skill_name": "PERFECT DIVE+",
    "skill_type": "score_up",
    "skill_detail": "5秒間 PERFECT SCORE 110/115/120/130%UP、更に...",
    "skill_type_icon_path": "skill/skill_score_up.png",
    "skill_detail_cn": "5秒内 PERFECT SCORE 110/115/120/130%UP、更..."
  },
  "card_images": [
    "card/normal/res021_no011_normal.png",
    "card/normal/res021_no011_after_training.png"
  ],
  "thumbnail_images": [
    "card/thumbnails/res021_no011_normal.png",
    "card/thumbnails/res021_no011_after_training.png"
  ],
  "costume_images": [
    "thumbnail/costume_rip/cos0213_head.png",
    "thumbnail/costume_rip/cos0213_body.png",
    "thumbnail/costume_rip/cos0213_hair_front.png"
  ],
  "character_icon_path": "chara_icon/hsk.png",
  "unit_logo_path": "logo_vivid_bad.png"
}
```

## 功能特性

- ✅ 支持卡牌详情图片生成
- ✅ 支持特训后技能
- ✅ 支持缩略图、框体、属性图标、星级显示
- ✅ 支持活动信息、卡池信息、衣装展示
- ✅ 自动发光三角形背景
- ✅ 简单命令行接口

## 环境要求

- Python 3.7+
- PIL/Pillow
- pydantic
- asyncio

## 输出

- 生成PNG格式图片
- 标准尺寸：1256×1038px（根据内容自动调整）
- 支持中文显示