# 卡牌绘制API JSON参数文档

## 概述

三个卡牌绘制函数的JSON参数说明：
- `compose_card_detail_image` - 卡牌详情图片
- `compose_card_list_image` - 卡牌列表图片
- `compose_box_image` - 卡牌一览图片

所有路径相对于 `ASSETS_BASE_DIR`（默认 `D:/pjskdata/data`）

---

## 用户信息模型 (DetailedProfileCardRequest)

### 基本字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | string | ✓ | - | 用户UID，支持隐私隐藏（显示为 `**********456789`） |
| `region` | string | ✓ | - | 服务器：`jp`/`tw`/`cn`/`kr` |
| `nickname` | string | ✓ | - | 用户名（最多64字符） |
| `source` | string | ✓ | - | 数据来源：`sekai-best`、`api` 等 |
| `update_time` | int | ✓ | - | 更新时间戳（毫秒），自动显示为相对时间 |
| `mode` | string | ✗ | `null` | 获取模式：`normal`、`fast` 等 |
| `is_hide_uid` | bool | ✗ | `false` | 是否隐藏UID（保留最后6位） |
| `leader_image_path` | string | ✓ | - | 头像图片路径 |
| `has_frame` | bool | ✗ | `false` | 是否显示头像框 |
| `frame_path` | string | ✗ | `null` | 头像框资源路径 |

### 游戏数据字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `user_cards` | array | ✗ | `[]` | 用户拥有的卡牌列表，用于显示拥有状态 |
| `user_decks` | array | ✗ | `[]` | 用户卡组信息（预留） |
| `user_gamedata` | object | ✗ | `{}` | 游戏数据（预留） |

### user_cards 字段说明

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `cardId` | int | ✓ | - | 卡牌ID |
| `level` | int | ✗ | `1` | 等级 1-60 |
| `masterRank` | int | ✗ | `0` | 特训等级 0-5，≥1时显示特训后版本 |
| `defaultImage` | string | ✗ | `"normal"` | 默认图片：`normal`/`special_training` |
| `specialTrainingStatus` | string | ✗ | `"not_done"` | 特训状态：`done`/`not_done` |

**拥有状态判断**：
1. 特训后版本：`specialTrainingStatus="done"` 且 `masterRank≥1`
2. 普通版本：在 `user_cards` 中存在但不满足特训条件
3. 未拥有：不在 `user_cards` 中

---

## 1. 卡牌详情图片 (`compose_card_detail_image`)

### JSON示例

```json
{
  "card_info": {
    "id": 1011,
    "character_id": 20,
    "character_name": "晓山瑞希",
    "unit": "school_refusal",
    "release_at": 1728723600000,
    "supply_type": "非限定",
    "card_rarity_type": "rarity_4",
    "attr": "cute",
    "prefix": "失われてしまったもの",
    "assetbundle_name": "res020_no038",
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
    "skill_name": "みんなとの時間",
    "skill_type": "score_up",
    "skill_detail": "5秒間 PERFECTのときのみスコアが110/115/120/130%UPする",
    "skill_type_icon_path": "skill/skill_score_up.png",
    "skill_detail_cn": "5秒内 PERFECT 得分提高110/115/120/130%"
  },
  "event_info": {
    "event_id": 145,
    "event_name": "荊棘の道は何処へ",
    "start_time": 1728723600000,
    "end_time": 1729490399000,
    "event_banner_path": "event/event_thorns_2024.png",
    "bonus_attr": "Cute",
    "unit": "school_refusal",
    "banner_cid": 20
  },
  "gacha_info": {
    "gacha_id": 598,
    "gacha_name": "朽ちゆく花はやがてガチャ",
    "start_time": 1728723600000,
    "end_time": 1729479599000,
    "gacha_banner_path": "gacha/banner_gacha598.png"
  },
  "card_images": [
    "card/normal/res020_no038_normal.png",
    "card/normal/res020_no038_after_training.png"
  ],
  "thumbnail_images": [
    "card/thumbnails/res020_no038_normal.png",
    "card/thumbnails/res020_no038_after_training.png"
  ],
  "costume_images": [
    "thumbnail/costume_rip/cos0676_head.png",
    "thumbnail/costume_rip/cos0676_unique_head.png",
    "thumbnail/costume_rip/cos0676_body.png"
  ],
  "character_icon_path": "chara_icon/mzk.png",
  "unit_logo_path": "logo_school_refusal.png",
  "background_image_path": "bg/bg_school_refusal.png",
  "event_attr_icon_path": "card/attr_icon_cute.png",
  "event_unit_icon_path": "icon_school_refusal.png",
  "event_chara_icon_path": "chara_icon/mzk.png"
}
```

### 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `card_info` | object | 卡牌基本信息 |
| `region` | string | 服务器区域 |
| `power_info` | object | 综合力信息 |
| `skill_info` | object | 普通技能信息 |
| `card_images` | array | 卡面图片路径 `[普通, 特训后]` |
| `thumbnail_images` | array | 缩略图路径 `[普通, 特训后]` |
| `costume_images` | array | 衣装图片路径 `[头像, 特殊头像, 身体]` |
| `character_icon_path` | string | 角色图标路径 |
| `unit_logo_path` | string | 团队logo路径 |

### 可选字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `special_skill_info` | object | `null` | 特训技能信息 |
| `event_info` | object | `null` | 活动信息 |
| `gacha_info` | object | `null` | 卡池信息 |
| `background_image_path` | string | `null` | 自定义背景 |
| `event_*_path` | string | `null` | 活动相关图标路径 |

### card_info 对象字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | int | ✓ | 卡牌ID |
| `character_id` | int | ✓ | 角色ID |
| `character_name` | string | ✓ | 角色名 |
| `unit` | string | ✓ | 团队标识 |
| `release_at` | int | ✓ | 发布时间戳 |
| `supply_type` | string | ✓ | 供给类型：`非限定`/`限定`/`协作` |
| `card_rarity_type` | string | ✓ | 稀有度：`rarity_1`~`rarity_4` |
| `attr` | string | ✓ | 属性：`cute`/`cool`/`happy`/`mysterious`/`pure` |
| `prefix` | string | ✓ | 卡牌名称 |
| `assetbundle_name` | string | ✓ | 资源包名 |
| `skill_type` | string | ✓ | 技能类型 |

---

## 2. 卡牌列表图片 (`compose_card_list_image`)

### JSON示例

```json
{
  "cards": [
    {
      "id": 1011,
      "character_id": 20,
      "character_name": "晓山瑞希",
      "unit": "school_refusal",
      "release_at": 1728723600000,
      "supply_type": "非限定",
      "card_rarity_type": "rarity_4",
      "attr": "cute",
      "prefix": "失われてしまったもの",
      "assetbundle_name": "res020_no038",
      "skill_type": "score_up"
    },
    {
      "id": 1252,
      "character_id": 5,
      "character_name": "花里みのり",
      "unit": "idol",
      "release_at": 1759201200000,
      "supply_type": "BFes限定",
      "card_rarity_type": "rarity_4",
      "attr": "cute",
      "prefix": "キラキラのアイドル！",
      "assetbundle_name": "res005_no047",
      "skill_type": "score_up"
    }
  ],
  "region": "jp",
  "user_info": {
    "id": "1234567890123456",
    "region": "jp",
    "nickname": "玩家名称",
    "source": "sekai-best",
    "update_time": 1728723600000,
    "mode": "normal",
    "is_hide_uid": false,
    "leader_image_path": "user/leader.png",
    "has_frame": false,
    "frame_path": null,
    "user_cards": [
      {
        "cardId": 1011,
        "level": 60,
        "masterRank": 5,
        "defaultImage": "special_training",
        "specialTrainingStatus": "done"
      },
      {
        "cardId": 1252,
        "level": 1,
        "masterRank": 0,
        "defaultImage": "normal",
        "specialTrainingStatus": "not_done"
      }
    ],
    "user_decks": [],
    "user_gamedata": {}
  },
  "background_image_path": "bg/bg_school_refusal.png"
}
```

### 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `cards` | array | 卡牌信息数组 |
| `region` | string | 服务器区域 |

### 可选字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_info` | object | `null` | 用户信息（显示拥有状态） |
| `background_image_path` | string | `null` | 自定义背景 |

### cards 数组字段

每个卡牌对象包含与 `card_info` 相同的基础字段（参考上方）。

---

## 3. 卡牌一览图片 (`compose_box_image`)

### JSON示例

```json
{
  "cards": [
    {
      "id": 1011,
      "character_id": 20,
      "character_name": "晓山瑞希",
      "unit": "school_refusal",
      "release_at": 1728723600000,
      "supply_type": "非限定",
      "card_rarity_type": "rarity_4",
      "attr": "cute",
      "prefix": "失われてしまったもの",
      "assetbundle_name": "res020_no038",
      "skill_type": "score_up"
    },
    {
      "id": 1252,
      "character_id": 5,
      "character_name": "花里みのり",
      "unit": "idol",
      "release_at": 1759201200000,
      "supply_type": "BFes限定",
      "card_rarity_type": "rarity_4",
      "attr": "cute",
      "prefix": "キラキラのアイドル！",
      "assetbundle_name": "res005_no047",
      "skill_type": "score_up"
    }
  ],
  "region": "jp",
  "user_info": {
    "id": "1234567890123456",
    "region": "jp",
    "nickname": "玩家名称",
    "source": "sekai-best",
    "update_time": 1728723600000,
    "mode": "normal",
    "is_hide_uid": false,
    "leader_image_path": "user/leader.png",
    "has_frame": false,
    "frame_path": null,
    "user_cards": [
      {
        "cardId": 1011,
        "level": 60,
        "masterRank": 5,
        "defaultImage": "special_training",
        "specialTrainingStatus": "done"
      },
      {
        "cardId": 1252,
        "level": 1,
        "masterRank": 0,
        "defaultImage": "normal",
        "specialTrainingStatus": "not_done"
      }
    ],
    "user_decks": [],
    "user_gamedata": {}
  },
  "show_id": false,
  "show_box": false,
  "use_after_training": true,
  "background_image_path": "bg/bg_school_refusal.png",
  "character_icon_paths": {
    "20": "chara_icon/mzk.png",
    "5": "chara_icon/mnr.png"
  },
  "term_limited_icon_path": "card/term_limited.png",
  "fes_limited_icon_path": "card/fes_limited.png"
}
```

### 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `cards` | array | 卡牌信息数组 |
| `region` | string | 服务器区域 |
| `character_icon_paths` | object | 角色ID→图标路径映射 |

### ⚠️ 重要说明

**`card_image_paths` 参数被忽略！** 函数根据 `assetbundle_name` 自动构建路径：
- 普通版本：`card/thumbnails/{assetbundle_name}_normal.png`
- 特训版本：`card/thumbnails/{assetbundle_name}_after_training.png`

### 可选字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_info` | object | `null` | 用户信息 |
| `show_id` | bool | `false` | 显示卡牌ID |
| `show_box` | bool | `false` | 只显示拥有的卡牌 |
| `use_after_training` | bool | `true` | 优先显示特训后版本 |
| `background_image_path` | string | `null` | 自定义背景 |
| `card_image_paths` | array | `[]` | **已废弃** |
| `term_limited_icon_path` | string | `null` | 期间限定图标 |
| `fes_limited_icon_path` | string | `null` | FES限定图标 |

### character_icon_paths 示例

```json
{
  "1": "chara_icon/ikk.png",    // 一歌
  "5": "chara_icon/mnr.png",    // みのり
  "20": "chara_icon/mzk.png",   // 瑞希
  "21": "chara_icon/rsk.png"    // 志帆
}
```

---

## 特殊功能

- **UID隐藏**：`is_hide_uid=true` 时显示为 `**********456789`
- **时间显示**：自动转换为相对时间，如 `2小时前`
- **头像框**：需提供完整的头像框组件文件
- **拥有状态**：根据 `user_cards` 显示拥有标识
- **特训状态**：根据 `masterRank` 和 `specialTrainingStatus` 显示特训后版本
- **错误处理**：图片缺失时优雅降级，不中断生成

---

## 图标格式

- **小属性图标**：`card/attr_cute.png` (62×68) - 缩略图使用
- **大属性图标**：`card/attr_icon_cute.png` (88×88) - 活动信息使用
- **稀有度框体**：`card/frame_{rarity}.png`
- **星级图标**：`card/rare_star_normal.png`
- **技能图标**：`skill/skill_{type}.png`

---

## 图片处理

- 自动圆角处理（10px）
- 自动添加稀有度框体、属性图标、星级
- 支持自定义背景，默认使用发光三角形背景
- 错误时优雅降级

---

## 示例文件

参考 `D:/pjskdata/compose_card_detail_image.json` 获取完整示例。