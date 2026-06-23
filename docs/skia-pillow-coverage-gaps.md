# Skia Render IR 迁移缺口清单

本清单基于对 Pillow 组件库（`src/sekai/base/painter.py` + `plot.py` + `draw.py` + `img_utils.py`）
与 Skia Render IR（`rust/haruki_skia_renderer/src/{ir,interp,lib}.rs` + `src/sekai/skia_renderer/ir_builder.py`）
的逐功能对拍，记录二者的能力差距，作为后续迁移其余端点的工作依据。

与 [`rust-skia-renderer-migration.md`](./rust-skia-renderer-migration.md) 配套：那份记录“做了什么、怎么做”，本份记录“还差什么、迁移前要补什么”。

## 状态图例

| 标记 | 含义 |
|---|---|
| ✅ | 完全覆盖（功能等价或更优） |
| ⚠️ | 部分覆盖（有对应物，但缺子功能） |
| ❌ | 完全缺失（IR 无对应物） |
| ➖ | 不适用（HTTP/缓存/资产等基础设施，非渲染原语） |

## 覆盖度总览

> **2026-06-24 更新**：下表的多数缺口已在「大规模补齐」批次中实现，详见
> [本批次已实现](#本批次已实现2026-06-24)。下方矩阵保留了原始审计结论作为对照，
> 状态标注已就地更新。

| 口径 | 原始 | 当前 | 说明 |
|---|---|---|---|
| 仅 Painter 基础绘图原语 | ~70–75% | **~95%** | 多 stop/径向渐变、stroke 渐变、per-corner 半径、图片 tint/crop/轮廓阴影、ImageBg 模糊对齐、TriangleBg 自定义色相均已补；仅 `method="separate"` 近似 |
| 完整组件库（含 `plot.py` 布局 + 富文本） | ~45–50% | **~75%** | 富文本（换行/多行/省略/inline 多色/描边/渐变/自适应/emoji）已由 IR 原语 + Python helper 覆盖；剩布局引擎（按架构留在 Python）与动图输出 |

**核心判断**：IR 是忠实的**低层 draw list**，不是 **widget tree**。画原语与富文本现已基本齐全；
剩余的是**布局引擎**（按 Constraint A 刻意留在 Python，由 helper 预算绝对坐标）与**动图输出**（按需再做）。

## 本批次已实现（2026-06-24）

一次性补齐了核心渲染原语、文字增强、富文本 helper 与彩色 emoji；全程卡牌端点**逐像素一致**
（`max|Δ|=0`，12/60 卡）。提交序列 `124fca4..b700d5a`。

| 分组 | 内容 |
|---|---|
| 渐变/填充 | N-stop 线性渐变、真实径向渐变（替换 stub）、stroke 可用渐变 |
| 形状 | RoundRect 四角独立半径（`corner_radii`） |
| 图片 | tint（multiply/mix）、`fit=crop`（纯裁剪不缩放）、alpha 轮廓投影阴影 |
| 背景 | ImageBg fit/fill/fixed/repeat + 9 向对齐 + `GaussianBlur(3)` + 压暗（采样改 bilinear+mipmap）；TriangleBg `main_hue` 自定义色相 + `size_fixed_rate` |
| 文字 | 渐变文字、描边/outline、`letter_spacing`、自适应对比色（背景平均亮度采样） |
| Emoji | 彩色 emoji 字体 fallback（emoji run 路由到 emoji 字体；opt-in，未配置则不变） |
| 字体 | 任意命名字体（`FontsIr.extra` + `FontRef.name`），不再限 3 role |
| 水印 | `IRBuilder.watermark()` 接线（启用既有 Watermark 节点） |
| 富文本 helper（Python） | `wrap_text` / `multiline_text`（换行+省略号）/ `colored_text` + `parse_colored_segments`（inline `<#hex>`）/ `shadowed_text` / `measure_text` |

仍未做（刻意）：**布局引擎**（HSplit/VSplit/Grid/Flow —— 按架构由 Python 预算绝对坐标，
helper 已提供文字侧支持）；**动图 GIF/APNG 输出**（无端点需要，按需再做）；`method="separate"`
渐变（用 `combine` 近似，仅对角渐变略有差异）。

## 端点迁移：IRPainter 影子层（2026-06-24）

可行性调查结论：约 16/20 个 drawer 都汇聚到 `plot.py` widget 树 → `Painter` 的 13 个方法。
因此迁移的杠杆不是逐端点重写，而是 **`IRPainter`(Painter 子类)**:重写那 13 个方法，把 widget 树的
绘制调用录成 IR 节点而非画像素——**同一棵 widget 树照常跑、布局照常算**(符合 Constraint A),输出变成 IR。

- `src/sekai/skia_renderer/ir_painter.py` —— IRPainter:覆盖 text/paste(_with_alpha_blend)/rect/
  roundrect/pieslice/blurglass_roundrect/draw_random_triangle_bg + 区域模型;映射 FontDesc→role/
  命名字体、LinearGradient/RadialGradient/AdaptiveTextColor、paste 阴影→alpha 轮廓阴影。不可表达的
  op 抛 `SkiaUnsupported` → 回退 Pillow。
- 运行时内存图(`paste` 收到的是 PIL 对象,无路径)经 **`mem:<key>`** 传输(native `render_scene`
  接受 `{key: png bytes}`)解决。
- `src/sekai/skia_renderer/canvas.py::render_canvas_payload(canvas)` —— 把已构建的 Canvas 经 IRPainter
  → native 渲染;失败/未启用返回 None 回退 Pillow。门控 `drawing.use_skia_plot`(默认关)。

运行时内存图基础设施(W0):`render_scene` 接受 `{key: 值}`,值可为 **编码字节(PNG/JPEG)** 或
**`(w, h, rgba)` 原始像素元组**(零编解码,Rust 侧 `images::raster_from_data`)。IRPainter 默认走
原始 RGBA(实测端到端比 PNG 快 ~1.6×;100 图请求仅加 ~4–8ms,非阻塞)。输出缩放经 `Scene.scale`
(1× 渲染后整图 resize,floor 截断匹配 `int(size*scale)`)。

| 端点 | 状态 | 备注 |
|---|---|---|
| `card/list`、`card/box` | ✅ Skia | 直接用 IRBuilder 手迁(早于影子层) |
| `stamp/list`、`costume/list`、`costume/detail` | ✅ Skia(影子层) | Wave 1;与 Pillow 布局逐尺寸一致 |
| **`get_profile_card`(keystone)** | ✅ 验证 + 锁测试 | W4 基石;~15 端点内嵌。影子层无缺口(三角背景/毛玻璃/头像 mem 图/自适应+多色文字全覆盖),输出与 Pillow 一致;`tests/test_profile_card_skia.py` |
| `profile`(/api/pjsk/profile) | ✅ Skia(影子层) | W4;scale 1.5 + 用户背景(mem 图);88 文字/27 alpha-paste/10 毛玻璃/honors/pcards 全过,逐尺寸一致(1876×979) |
| `music`(detail/brief-list/list/progress/rewards{detail,basic}) | ✅ Skia(影子层) | W4;6 端点机械接线。抽检 `music/list` 经 Skia 真渲染、逐尺寸一致(773×568) |
| `event`(detail/record/**planner**) | ✅ Skia(影子层) | W4+W5;`detail` 逐尺寸一致(1638×1020)。`planner` 委托 deck,走 deck 的 Skia 路径;`list` 属 W6 |
| `education`(challenge-live/power-bonus/area-item/bonds/leader-count/character-mission-{overview,all}) | ✅ Skia(影子层) | W4;7 端点机械接线。抽检 `challenge-live` 逐尺寸一致(890×532) |
| `deck/recommend` | ✅ Skia(影子层,heavy worker) | W5;最大/最密的 drawer(dense set_offset 表 + profile card + 卡缩略图)。heavy worker 先试 Skia 再回退。逐尺寸一致(1198×694),单请求 **2.12×**、并发 K10 **2.35×** |
| `score`(control/custom-room/music-meta/music-board) | ✅ Skia(影子层) | W2;4 端点纯 widget 树。全部抽检逐尺寸一致(350×462 / 844×428 / 1907×764 / 1514×438),无不支持 op(`music/detail` 已随 music 迁) |
| `sk`(line/query/check-room/csb/speed/winrate) | ✅ Skia(影子层) | W1;6 端点。含 scale 1.5/2.0 与 csb **条件 scale**(`1.5 if <10 else 1.0`,build 返回 (canvas, scale) 元组)。抽检 query(1.5×)、csb 大小两档、均逐尺寸一致。`player-trace`/`rank-trace` 是 matplotlib,留 Pillow(排除) |
| `misc/chara_birthday` | ✅ Skia(影子层,heavy worker) | W1;Canvas bg 是 `ImageBg(运行时卡图)`→ mem 图。heavy worker 先试 Skia。抽检逐尺寸一致(672×752) |
| `gacha / event-list / vlive`(W6) | ⏳ 待迁 | 图中图 + 缓存策略 |
| `profile/custom`、`chart`、`mysekai`、`honor`、`sk` traces | ❌ 排除/特殊 | 详见下方"排除" |

`card/list`、`card/box` 的缩略图与技能图标在 Python 布局层合成（`card_common.py:147-159`、`card_list.py:177-190`），未沉入 IR。

## ✅ 已完全覆盖

| Painter 能力 | Skia 对应 | 备注 |
|---|---|---|
| region 平移模型（offset，无 clip） | `Group(offset)`（`ir.rs:185`，`interp.rs:111`） | Skia 还额外支持 rect/rrect clip，是超集 |
| 延迟命令缓冲执行模型 | Scene IR 树 + `render_scene`（`interp.rs:84`） | 渲染顺序 = 树顺序，坐标 offset 相对解析 |
| 单行纯色文字 + L/C/R 对齐 | `Text` 节点（`interp.rs:425`） | align 基于 advance 测量，真实生效 |
| 文字 CJK「哇」顶基线归一化 | `Baseline::CjkTop`（`interp.rs:454`） | 复刻「哇」ink 高度锚点，另支持 ascender/alphabetic，是超集 |
| 半透明文字 | `Text` 的 `Color4` alpha | Skia paint 直接吃 alpha，无需 overlay 缩放 trick |
| `paste`（拉伸 + alpha-aware） | `Image` fit=stretch（`interp.rs:391`） | RGBA 源 source-over，无需 mode 转换 |
| `paste_with_alpha_blend`（整体 alpha） | `Image.alpha`（`interp.rs:387`） | `set_alpha_f` 整体不透明度 + source-over |
| `rect` 实心/线性渐变填充 + 实心 stroke | `Rect` 节点（`interp.rs:300`） | — |
| `roundrect` 半径/逐角开关/stroke | `RoundRect` 节点（`interp.rs:315`） | Skia 原生 RRect AA，优于 Painter 的 supersample-and-shrink |
| `pieslice` 扇形/角度/填充 | `PieSlice` 节点 `draw_arc(use_center)`（`interp.rs:332`） | AA 优于 Painter 原生 pieslice |
| `blurglass_roundrect` 毛玻璃 | `BlurGlass` 节点（`lib.rs:492`） | 背景模糊+tint+三层接触阴影+5 段对角高光+描边，精细复刻 |
| `draw_random_triangle_bg` 时段三角背景 | `TriangleBg` 节点（`lib.rs:136`） | 7 段粉色调色板插值+双层 aspect 缩放散布（确定性 RNG） |
| 线性渐变（2 色，p1/p2） | `GradientSpec::Linear`（`interp.rs:262`） | RGBA 插值 + `TileMode::Clamp` |

## ⚠️ 部分覆盖（有，但缺子功能）

| 能力 | Skia 现状 | 缺失子功能 | 迁移建议 |
|---|---|---|---|
| region 增量 helper（shrink/expand/move/restore） | 仅 `group()` 上下文栈 save/restore | 无 shrink/expand/move 算术原语 | Python 侧算好绝对 offset 传入（已是现状） |
| 字体解析（任意 `FontDesc(path,size)`） | 仅 3 个固定 role（default/bold/heavy，heavy→bold 回退，`lib.rs:826`） | 无法按 text 节点引用任意字体文件 | 若新端点用到其它字体，需扩展 FontRegistry 注册表 |
| `paste` 投影阴影 | `Shadow` 节点仅模糊圆角矩形（`interp.rs:355`） | 不能跟随图片 alpha 轮廓，无内阴影剔除 | 非矩形 sprite 阴影只能近似；需要时新增「alpha 轮廓阴影」原语 |
| `rect`/`roundrect`/`pieslice` stroke | stroke 仅 `Color4` | **stroke 不能用渐变** | 新增 stroke 渐变支持，或 Python 拆成两层 |
| `roundrect` 圆角 | 单一半径 | 无 per-corner **不同**半径（仅逐角开关） | 扩展 `RoundRectNode` 为四角独立半径 |
| `LinearGradient` | 忽略 `method`，限 2 stop | 无 `separate` 轴向混合模式；无多 stop | 需要时给 `GradientSpec` 加 stop 数组 + method |
| 渐变穿任意 mask（`Gradient.get_img(mask=...)`） | 仅形状自身几何作 mask | 无法让渐变穿文字字形/图片轮廓 | 需要时新增「mask 填充」原语（渐变文字依赖此） |
| `adjust_image_alpha_inplace` | `Image.alpha` 仅 multiply | 无 `set` 模式；不能作用于已渲染内容 | 多数场景 multiply 够用 |
| `center_crop_by_aspect_ratio`（纯裁剪） | `Image` fit=cover（裁剪 + **缩放**） | 无纯裁剪不缩放模式 | 满铺场景视觉近似；需要精确时新增 crop-only fit |
| `ImageBg`（站点背景） | 仅全屏 cover + NEAREST 采样（`lib.rs:691`） | 无 align/repeat/fixed；无 `GaussianBlur(3)`+压暗淡化 | 站点背景迁移前需补 ImageBg 模糊/对齐/平铺 |
| `RandomTriangleBg` | `TriangleBg` 仅 `hour` 参数 | 无 `main_hue` 自定义色相；无 `size_fixed_rate` | 非粉色/自定义色相背景需扩展参数 |
| `Canvas.get_img`（缩放渲染） | `render_scene` 直接按 canvas 尺寸渲染 | 无内置 scale-then-BILINEAR-downscale；4096² 面积上限是 plot.py 层防护 | Python 直接定 canvas 尺寸 |

## ❌ 完全缺失（IR 无对应物，影响其余端点迁移）

### 1. 布局引擎（最大缺口）

`plot.py` 是一套声明式 flexbox 式 widget/布局引擎（**不是图表库**）：
`Frame / HSplit / VSplit / Grid / Flow / Spacer`、ratios、expand/fixed 尺寸、padding/margin、
九宫对齐、offset+anchor、auto-wrap、`allow_draw_outside` 裁剪、`draw_funcs` 回调钩子、contextvar 嵌套。

- **IR 现状**：`Group` 只能平移（+ clip），没有任何布局求解。
- **迁移影响**：所有 flexbox 式布局必须由 Python drawer **预先算成绝对坐标**再发 IR。这是单一最大的概念缺口。
- **路线**：① Python 复用 plot.py 求解布局 → 输出绝对坐标 IR（快，但富文本/tint 仍要新原语）；或 ② 给 IR 加布局原语（重，但能真正复刻组件库）。

### 2. 富文本

| 缺失能力 | Pillow 来源 | 说明 |
|---|---|---|
| 换行 / 多行 / 行数上限 / 行距 | `plot.py TextBox` | IR 文字单行；需 Python 预排版，每行发一个 `Text` 节点 |
| 溢出省略号 / 二分宽度裁剪 / 自动缩字 | `plot.py TextBox` | 无 IR 原语 |
| 描边/outline、字间距、行间距 | — | Painter 本身也无;但 plot.py 层有,IR 缺 |
| inline 逐字多色（`<#hex>` 标记） | `plot.py ColoredTextBox` | 只能 Python 拆成多个相邻 Text 节点并测量 x 偏移 |
| 渐变文字（渐变穿字形 alpha） | `painter.py:1076` | `Text.fill` 仅 `Color4`，无 shader |
| 自适应对比色（`AdaptiveTextColor`） | `painter.py:529,1081` | MVP 显式推迟；逐区域均值亮度 / 逐像素 BoxBlur(8) 亮度选色 |
| 彩色 emoji（Pilmoji + Google emoji） | `painter.py:705` | IR 的 emoji font role 是 `dead_code`，未接 FontRegistry |
| 文字投影（`draw_shadowed_text`） | `plot.py` | 需 Python 发两个 Text 节点 |

### 3. 图片重着色

| 缺失能力 | 说明 |
|---|---|
| `multiply_image_by_color`（乘法 tint） | `Image` 节点只有 fit/alpha/anchor；属性/角色 sprite 上色无法在 IR 内做 |
| `mix_image_by_color`（向色彩 alpha 加权 lerp） | 同上 |

> 当前只能在 Python 预先把 asset 着色后再引用,或新增 Image 节点 tint 字段。

### 4. 水印

| 缺失能力 | 说明 |
|---|---|
| `Watermark` 节点接线 | 节点已实现（`ir.rs:338`，`interp.rs:193`，支持多行/逐行对齐）但**无 IRBuilder 方法、未启用** |
| 实际行为 | 两个 live 端点只发一个固定角落的纯 `Text` 节点（`card_list.py:247`，`card_box.py:333`） |
| `add_watermark_to_image`（光栅页脚） | 扩展画布 + 采样底部条作页脚底 + 多行右对齐 + 手工阴影 —— 全缺 |
| 两遍换行 + 自动缩字到 ≤2 行 | `wrap_watermark_text` 等 —— IR 委托 Python，但 live 路径根本没调用 |

### 5. 动图输出

| 缺失能力 | 说明 |
|---|---|
| GIF / APNG / 透明 GIF 输出 | 编码器只出单帧 PNG/JPEG（`lib.rs:696`） |
| `TransparentAnimatedGifConverter`（RGBA→P 透明） | 无调色板/GIF 透明机制 |

> 任何动图端点都无法用现有 IR。

### 6. 径向渐变

- `GradientSpec::Radial` 是 **stub**：解释器只平涂中心色 `c2`（`interp.rs:283`），径向参数是 `dead_code`，且无 IRBuilder 方法产出。MVP 显式推迟。
- 影响：依赖径向渐变的填充/主题色无法表达。

## 建议新增的 IR 原语（按优先级）

原始候选清单与现状（✅ 已在 2026-06-24 批次完成）:

1. **布局求解策略落地** —— ✅ 定调：按 Constraint A 留在 Python（预算绝对坐标）；文字侧已提供
   `wrap_text`/`multiline_text`/`colored_text` 等 helper。容器级（splits/grid/flow）仍待具体端点迁移时按需在 Python 实现。
2. **富文本节点** —— ✅ 多行/换行/省略号（`multiline_text`）、inline 多色（`colored_text` + `parse_colored_segments`）、描边/渐变/自适应（IR 原语）。
3. **Image tint 字段** —— ✅ `multiply` / `mix`。
4. **ImageBg 增强** —— ✅ `GaussianBlur(3)` + 压暗 + align/repeat。
5. **径向渐变 + stroke 渐变 + mask 填充** —— ✅ 径向 + stroke 渐变 + 渐变文字（穿字形）；任意 mask 填充仍未做（很少需要）。
6. **动图输出 + Watermark 接线 + 任意字体** —— Watermark 接线 ✅、任意字体 ✅；**动图 GIF/APNG 仍未做**（无端点需要）。

## 已知非目标 / 不适用（➖）

以下属基础设施或 Painter 私有缓存,不计入渲染原语覆盖率:
Painter 磁盘缓存 / 线程进程池 offload / image id 序列化、`open_image` 等缓存加载器、
请求时区+时间字符串拼装(纯 Python,任意渲染器可复用)。Skia 侧已有自己的资产路径校验、
进程级解码图 LRU(4096)、GIL 释放渲染。

---

> 审计方法:三路并行盘点(Painter / 效果与布局 / Skia IR)+ 交叉对拍合成,关键论断
> (plot.py 性质、Watermark 未启用、径向 stub)经源码二次核实。如功能有增删,请同步更新本表。
