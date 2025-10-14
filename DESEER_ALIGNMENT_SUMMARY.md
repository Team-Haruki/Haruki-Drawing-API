# Haruki-Drawing-API-Alpha 代码对齐总结

## 工作目标
将 Haruki-Drawing-API-Alpha 项目的代码对齐到 Deseer 的实现方式，以避免因我们之前的大量修改导致的兼容性问题。

## 工作时间
2025-10-14

## 主要修改内容

### 1. profile/drawer.py 代码对齐

#### 1.1 边框生成函数对齐
**修改前（我们的版本）**：
```python
base = get_img_from_path(frame_base_path, "horizontal/frame_base.png")
ct = get_img_from_path(frame_base_path,"vertical/frame_centertop.png")
...
scale_ratio = frame_w / inner_w
final_size = int(w * scale_ratio)
img = resize_keep_ratio(img, final_size / img.width, mode="scale")
```

**修改后（Deseer版本）**：
```python
base = await get_img_from_path(frame_base_path, "horizontal/frame_base.png")
ct = await get_img_from_path(frame_base_path,"vertical/frame_centertertop.png")
...
img = resize_keep_ratio(img, frame_w / inner_w, mode="scale")
```

#### 1.2 带框头像控件函数对齐
**修改前（我们的版本）**：
```python
frame_img = await get_player_frame_image(frame_path, avatar_w)
if frame_img:
    container_size = frame_img.width
    with Frame().set_size((container_size, container_size)).set_content_align('c') as ret:
        ImageBox(avatar_img, size=(avatar_w, avatar_w), use_alpha_blend=False)
        ImageBox(frame_img, use_alpha_blend=True)
```

**修改后（Deseer版本）**：
```python
frame_img = await get_player_frame_image(frame_path ,avatar_w + 5)
with Frame().set_size((avatar_w, avatar_w)).set_content_align('c').set_allow_draw_outside(True) as ret:
    ImageBox(avatar_img, size=(avatar_w, avatar_w), use_alpha_blend=False)
    if frame_img:
        ImageBox(frame_img, use_alpha_blend=True)
```

#### 1.3 用户信息卡片函数对齐
**主要修改**：
- 背景使用 `roundrect_bg(alpha=80)` 而不是 `roundrect_bg()`
- 头像加载直接使用 `get_img_from_path(ASSETS_BASE_DIR, profile.leader_image_path)`
- 移除了回退方案代码

#### 1.4 数据模型简化
**修改前**：
```python
class DetailedProfileCardRequest(BaseModel):
    """用户信息模型 - 扩展版本，支持完整的游戏数据"""
    # ... 大量额外字段
    user_cards: Optional[List[Dict]] = None
    user_decks: Optional[List[Dict]] = None
    user_gamedata: Optional[Dict] = None
```

**修改后**：
```python
class DetailedProfileCardRequest(BaseModel):
    id: str
    region: str
    nickname: str
    source: str
    update_time: int
    mode: str = None
    is_hide_uid: bool = False
    leader_image_path: str
    has_frame: bool = False
    frame_path: Optional[str] = None
    user_cards: Optional[List[Dict]] = None  # 保留box功能需要的字段
```

### 2. 删除的代码
- ✅ 所有 v2 版本的函数和模型类
- ✅ 回退方案代码（如 `generate_user_avatar`）
- ✅ 整合的 user 模块中的复杂代码
- ✅ 向后兼容的别名和辅助函数

### 3. 保留的核心功能
- ✅ `get_user_card_ids` 函数（box功能必需）
- ✅ `DetailedProfileCardRequest` 中的 `user_cards` 字段
- ✅ 完整的 box 功能
- ✅ 终章边框功能

## 修改效果

### 测试结果
✅ **所有核心功能正常**：
- 卡牌列表功能：正常生成图片
- 卡牌详情功能：正常生成图片
- 卡牌一览（box）功能：正常显示用户信息和边框
- 终章边框：正确环绕用户头像

### 兼容性改进
✅ **使用Deseer的实现方式**：
- 异步图片加载方式对齐
- 边框缩放逻辑对齐
- 容器处理方式对齐
- 背景样式设置对齐

✅ **保持功能完整性**：
- 保留所有box功能的核心逻辑
- 保留用户信息显示功能
- 保留终章边框支持

## 文件变更统计

### 修改的文件
- `src/profile/drawer.py` - 核心对齐工作

### 删除的代码行数
- 删除了约 300+ 行的额外代码
- 删除了 6 个额外的模型类
- 删除了 10+ 个辅助函数

### 项目大小对比
- 修改前：约 3.1M
- 修改后：约 3.1M（包含测试图片）

## 对齐原则

1. **核心对齐**：所有共同函数都使用 Deseer 的实现方式
2. **功能保留**：保留 Deseer 没有但我们需要的功能（如box功能）
3. **接口兼容**：确保对外接口保持不变
4. **代码简化**：删除不必要的回退方案和冗余代码

## 后续建议

1. **保持更新同步**：定期检查 Deseer 项目的更新
2. **测试优先**：修改后先测试再提交
3. **文档更新**：及时更新修改记录
4. **渐进式修改**：避免一次性大量修改

## 结论

✅ **对齐成功**：代码已成功对齐到 Deseer 的实现方式，同时保留了所有必要的功能。项目现在应该与 Deseer 的项目更加兼容，减少了因差异导致的错误。