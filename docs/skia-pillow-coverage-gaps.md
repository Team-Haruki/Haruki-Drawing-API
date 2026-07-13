# Skia Render IR 迁移缺口清单

本清单基于对 Pillow 组件库（`src/sekai/base/painter.py` + `plot.py` + `draw.py` + `img_utils.py`）
与 Skia Render IR（`rust/haruki_skia_renderer/src/{ir,interp,lib}.rs` + `src/sekai/skia_renderer/ir_builder.py`）
的逐功能对拍，记录二者的能力差距，作为后续迁移其余端点的工作依据。

配套文档：[`rust-skia-renderer-migration.md`](./rust-skia-renderer-migration.md) 记录“做了什么、怎么做”，
[`skia-migration-todo.md`](./skia-migration-todo.md) 记录“端点级剩余工作”，本份记录“渲染能力还差什么”。

## 现状（2026-07-13）

**能力缺口已基本关闭**：下方 2026-06 首轮审计列出的 ⚠️/❌ 项（径向渐变 stub、stroke 渐变、per-corner
半径、ImageBg 模糊/对齐、彩色 emoji、渐变文字、图片 tint/crop、alpha 蒙版、水印光栅页脚、`method="separate"`）
现已全部实现。仍未做的只剩下方[「仍未关闭的缺口」](#仍未关闭的缺口)四条，且都不挡生产。

- **门控**：`drawing.use_skia_plot`（`src/settings.py`，**默认 `True`**）是唯一的 Skia 门控。
  早期的 `use_skia_card_box` / `use_skia_card_list` / `skia_card_list_fallback_to_pillow` 已随专用场景
  构建器一起删除。
- **原生能力版本**：`IR_CAPABILITY = 5`（`lib.rs`，含 `SelfImage` 画布快照节点），
  `REQUIRED_NATIVE_IR_CAPABILITY = 5`（`skia_renderer/canvas.py`）；低于此版本的 wheel 拒绝加载并回退 Pillow。
- **对拍**：`scripts/skia_parity_sweep.py` 共 **63 个用例、63 ok、0 失败**，已无 pillow-only 用例
  （honor 已迁）。注意三角背景由**未播种的全局 `random`** 散布，同一棵树两次渲染本身就有差异，
  故带背景端点只能断言宽松的均值差。`scripts/skia_legacy_baseline.py` 另外用 baseline git ref 的 Pillow
  输出对当前 Pillow 输出做基线回归，覆盖“Pillow vs Skia 同树对拍”看不到的盲区。
- **可观测性**：`skia_renderer/render_stats.py` 按端点计数 `skia|cache_hit|fallback|disabled|error`
  （`GET /render-stats`），`image.response` 日志带 `backend=` 字段；Skia payload 缓存在 `payload_cache.py`，
  经 `/cache-stats` 上报。

| 口径 | 首轮审计 | 当前 | 说明 |
|---|---|---|---|
| 仅 Painter 基础绘图原语 | ~70–75% | **~100%** | 多 stop/径向渐变、`separate` 轴向混合、stroke 渐变、per-corner 半径、图片 tint/crop/轮廓阴影、alpha 蒙版、ImageBg 模糊对齐、TriangleBg 自定义色相均已实现 |
| 完整组件库（含 `plot.py` 布局 + 富文本） | ~45–50% | **~75%** | 富文本（换行/多行/省略/inline 多色/描边/渐变/自适应/emoji）已由 IR 原语 + Python helper 覆盖；布局引擎按架构留在 Python（见下），动图两侧均已无 |

**核心判断**：IR 是忠实的**低层 draw list**，不是 **widget tree**。画原语与富文本已齐全；布局求解按
Constraint A 刻意留在 Python —— `plot.py` 照常算布局，`IRPainter`（`Painter` 子类）把绘制调用录成绝对
坐标 IR。这是设计，不是缺口。

## 仍未关闭的缺口

| 缺口 | 现状 | 是否挡生产 |
|---|---|---|
| **动图 GIF/APNG 输出** | 编码器只出单帧 PNG/JPEG。同时 Pillow 侧的 GIF/APNG helper 已从 `base/img_utils.py` 删除（零调用方）—— **两侧都没有动图能力**，无端点需要 | 否 |
| **`profile/custom-profile-card`** | 独立 Unity/TMP-SDF 栅格器（`src/sekai/profile/custom_profile/`），无 `plot.py` widget 树，根本不在影子层范围。**唯一仍是纯 Pillow 的绘图端点** | 否（见 [`custom-profile-skia-feasibility.md`](./custom-profile-skia-feasibility.md)） |
| **Tier-2 原生图表** | `sk` player-trace/rank-trace 的 matplotlib 位图、`chart` 的 `pjsekai_scores_rs` 位图仍在 Python/crate 侧栅格化，作 mem 图传入；**外壳**（圆角白卡 / 图例列 / 水印页脚）已 IR 化 | 否 |
| **「任意 Node 渲染结果作蒙版」** | `Group.mask` 只接受**图片** alpha（asset 路径或 `mem:<key>`），这已由公共原语 `Painter.push_mask/pop_mask` 在两端表达（Pillow=离屏缓冲 + `ImageChops.multiply`，Skia=`Group{mask}` DstIn）。唯一消费者（honor bonds 的 `putalpha(mask.split()[3])`）已覆盖，暂无更高价值消费者 | 否 |

## 状态图例

| 标记 | 含义 |
|---|---|
| ✅ | 完全覆盖（功能等价或更优） |
| ⚠️ | 部分覆盖（有对应物，但缺子功能） |
| ❌ | 完全缺失（IR 无对应物） |
| ➖ | 不适用（HTTP/缓存/资产等基础设施，非渲染原语） |

## 架构：IRPainter 影子层

可行性调查结论：绝大多数 drawer 都汇聚到 `plot.py` widget 树 → `Painter` 的十余个方法。因此迁移的杠杆
不是逐端点重写，而是 **`IRPainter`（`Painter` 子类）**：重写那些方法，把 widget 树的绘制调用录成 IR 节点
而非画像素 —— **同一棵 widget 树照常跑、布局照常算**（符合 Constraint A），输出变成 IR。

- `src/sekai/skia_renderer/ir_painter.py` —— `IRPainter`：覆盖 `text` / `paste(_with_alpha_blend)` /
  `rect` / `roundrect` / `pieslice` / `shadow_roundrect` / `blurglass_roundrect` /
  `push_clip_roundrect`+`pop_clip` / `draw_random_triangle_bg` + 区域模型；映射 FontDesc→role/命名字体、
  LinearGradient/RadialGradient/AdaptiveTextColor、paste 阴影→alpha 轮廓阴影、渐变文字→字形 overlay。
  不可表达的 op 抛 `SkiaUnsupported` → 回退 Pillow。
- 运行时内存图（`paste` 收到的是 PIL 对象，无路径）经 **`mem:<key>`** 传输（native `render_scene`
  接受 `{key: png bytes}`）解决；有磁盘路径的资产直接传路径（pristine asset 直传，draw-time 缩放）。
- `src/sekai/skia_renderer/canvas.py::render_canvas_payload(canvas, endpoint=...)` —— 把已构建的 Canvas 经
  IRPainter → native 渲染；失败/未启用返回 `None` 回退 Pillow（fail-open，永不 500），并在此记账
  skia/fallback/disabled/error（`/render-stats`）。门控 `drawing.use_skia_plot`（默认开）。
  `canvas_size_within_limit()` 是尺寸护栏（DoS 级 64 Mpx，不是 Pillow 那条 4096² 预算）。
  （曾有一个 `render_cached_canvas_payload(...)` 包装整页结果缓存，已随「整页缓存不做」的结论一起删除。）

运行时内存图基础设施：`render_scene` 接受 `{key: 值}`，值可为 **编码字节（PNG/JPEG）** 或
**`(w, h, rgba)` 原始像素元组**（零编解码，Rust 侧 `skia_safe::images::raster_from_data`）。IRPainter 默认
走原始 RGBA（实测端到端比 PNG 快 ~1.6×）。输出缩放经 `Scene.scale`（1× 渲染后整图 resize，floor 截断
匹配 `int(size*scale)`）。

端点级迁移状态（哪些端点走 Skia、各自的 payload 缓存策略）见
[`skia-migration-todo.md`](./skia-migration-todo.md)；下方端点表是 2026-06-24 的历史快照。

## 缓存与 Skia 路径的关系（现状）

影子层不改变任何既有缓存，但各端点的缓存形态不同，迁移时容易误判：

| 端点 | 缓存形态 | 对 Skia 的含义 |
|---|---|---|
| `card/list`、`card/box`、`honor` | 整图结果缓存（`get_composed_image_cached` mem TTL，honor 无 disk 层） | Skia 侧另有 payload 缓存（`payload_cache.py`，键 = Pillow 缓存键 + `\|skia\|格式\|质量`） |
| `event/list`、`vlive/list` | **逐条目** mem + disk 缓存（`get/put_composed_image_disk_cached`，`_*_LIST_ENTRY_CACHE_NAMESPACE`） | 条目是 `ImageBox(缓存 PIL)`，影子层把缓存条目当 mem 图传，缓存原封不动 |
| `gacha`(list/detail) | **无条目级合成缓存**；只是把 logo/banner 经全局图片缓存预加载后塞进 `ImageBox` | 每次请求都要重排整棵树，Skia 与 Pillow 同等；无“缓存条目当 mem 图传”这回事 |
| `profile`、`vlive/list`、`chart`、`misc/alias-list` | **无整页结果缓存**（调用方 Haruki-Cloud 按 payload 缓存，本地页级缓存必不命中；alias-list 更是 cloud 刻意绕过自身缓存以免 DT 水印过期） | 每请求真渲染 |

> `src/sekai/profile/drawer.py::_build_cached_profile_module_image` / `_build_cached_profile_module_widget`
> 是**死代码**（零调用方）：profile 的模块级预渲染缓存已废弃，identity 模块的注释也解释了为什么不能走它
> （自适应文字色必须在最终背景上求值）。任何“profile 模块预渲染留 Pillow”的说法都已过期。

---

# 历史记录

> 以下为执行日志，反映**当时**的状态，保留作记录。当前状态以上文为准；原始审计矩阵见
> [首轮能力矩阵](#首轮能力矩阵2026-06-23现已全部关闭)，其「现状」列已按今日代码核实。

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

该批次结束时仍未做（刻意）：**布局引擎**（HSplit/VSplit/Grid/Flow —— 按架构由 Python 预算绝对坐标）；
**动图 GIF/APNG 输出**；`method="separate"` 渐变（当时用 `combine` 近似）。
—— 其中 `separate` 已于后续实现（`interp.rs::separate_endpoints`，端点重映射，非近似）；动图仍未做。

## 剩余迁移收尾（2026-06-24 第二轮）

一次「剩余未开始项」审计（5 路并行盘点 + 综合）结论：plot.py widget 树端点迁移已实质完成，
剩下的是「一串已缓存的子渲染（边际价值）+ 两个确实超范围的专有引擎 + 几个便宜收尾 + 一个真正的
性能热点」。据此完成：

> **当时的覆盖（2026-06-24，按 16 路逐端点审计核实）**：53 个 `@router.post` 绘图端点中
> **50 个已接 Skia 影子层**（门控 `use_skia_plot`，当时默认关）。仅 3 个未接：
> `profile/custom-profile-card`、`chart`、`honor`。
> —— **已过期**：`chart`（外壳）与 `honor`（完整 IR 路径 + `Group.mask` + 两遍水印页脚）此后均已迁入
> Skia，端点总数也已增长；今日只剩 `profile/custom-profile-card` 未接，对拍 63/63 全绿。

| 项 | 内容 | 验证 |
|---|---|---|
| **新原语**：Image `source_rect` | Image 节点源像素裁剪窗 `[x0,y0,x1,y1]`，在 fit 之前应用（对应 Pillow `img.crop(box)`）；fit 数学在裁剪局部坐标算，再平移回原图。解锁 mysekai 站点 crop_bbox / 网格中心裁剪、honor bonds 叠加裁剪 | 像素级（裁剪选对象限、均匀） |
| **新原语**：`TintMode::recolor` | 保留源 alpha 作蒙版、RGB 替换为常量色（SrcIn），色 alpha 缩放结果 alpha；区别于 multiply/mix。用于如 mysekai 红色出生点标记 | 像素级（设 RGB、保 mask；alpha 缩放） |
| **真热点**：mysekai msr_map 单图 | 最后一个每请求、未缓存、纯 Pillow 的整图场景。单图 build 改返回 Canvas（而非 `get_img()`），经共享 IRPainter 路径走原生并行渲染 | Skia vs Pillow 对拍（mean 0.27、p99=0） |
| **关闭回退**：渐变文字 | `IRPainter.text` 原本对渐变填充抛 `SkiaUnsupported` → 整张画布回退 Pillow。改为把渐变端点映射到字形 overlay，复用 `_fill()`。Rust Text 节点本就渲染渐变 | 视觉一致，mean 3.4（字形栅格差异） |
| **收尾**：sk player-trace/rank-trace | 最后两个无 Skia 路径的 sk 端点 | mean<1 |

当时判定为边际/超范围（**部分已过期**）：`gacha` 的 `concat_images([star]*n)` → HSplit；msr_map 多图网格
转原生子组；`profile/custom_profile`（~10.5k 行 Unity 保真栅格器，SDF/TMP/仿射/blend —— **仍然超范围**）；
`honor`（当时判「全缓存、bonds 需 alpha-mask，留 Pillow」—— **已过期**，honor 已迁）；`chart`
（**已过期**，外壳已迁）；卡缩略图/profile 模块预渲染（**已过期**：缩略图已是 `CardFullThumbnailBox`
子树，profile 模块预渲染是死代码）；GIF/APNG 导出（仍未做）；Tier-2 原生图表（仍未做）。

## 端点迁移：IRPainter 影子层（2026-06-24 快照）

> 下表是该批次结束时的端点快照。当时门控默认关；今日 `use_skia_plot` 默认开，`card/list`、`card/box`
> 已退回共享 widget 树（专用场景构建器 `skia_renderer/card_render.py` 与 `scripts/compare_card_render.py`
> 已删除），`honor`、`chart`、`mysekai`、`inventory`、`misc/help` 亦已接入。当前端点状态见
> [`skia-migration-todo.md`](./skia-migration-todo.md)。

| 端点 | 当时状态 | 备注 |
|---|---|---|
| `card/list`、`card/box` | ✅ Skia | 当时用手写 IRBuilder 场景直迁（早于影子层）；**后已退役**，改走与 `card/detail` 相同的共享 widget 树 |
| `card/detail` | ✅ Skia(影子层) | 进度审计发现的唯一真缺口；split 为 `_build_card_detail_canvas` + 影子层 |
| `stamp/list`、`costume/list`、`costume/detail` | ✅ Skia(影子层) | Wave 1；与 Pillow 布局逐尺寸一致 |
| **`get_profile_card`(keystone)** | ✅ 验证 + 锁测试 | W4 基石；~15 端点内嵌。三角背景/毛玻璃/头像 mem 图/自适应+多色文字全覆盖；`tests/test_profile_card_skia.py` |
| `profile`(/api/pjsk/profile) | ✅ Skia(影子层) | W4；scale 1.5 + 用户背景(mem 图)；88 文字/27 alpha-paste/10 毛玻璃/honors/pcards 全过，逐尺寸一致(1876×979) |
| `music`(detail/brief-list/list/progress/rewards{detail,basic}) | ✅ Skia(影子层) | W4；6 端点机械接线。抽检 `music/list` 逐尺寸一致(773×568) |
| `event`(detail/record/**planner**) | ✅ Skia(影子层) | W4+W5；`detail` 逐尺寸一致(1638×1020)。`planner` 委托 deck，走 deck 的 Skia 路径 |
| `education`(challenge-live/power-bonus/area-item/bonds/leader-count/character-mission-{overview,all}) | ✅ Skia(影子层) | W4；7 端点机械接线。抽检 `challenge-live` 逐尺寸一致(890×532) |
| `deck/recommend` | ✅ Skia(影子层,heavy worker) | W5；最大/最密的 drawer。逐尺寸一致(1198×694)，单请求 **2.12×**、并发 K10 **2.35×** |
| `score`(control/custom-room/music-meta/music-board) | ✅ Skia(影子层) | W2；4 端点纯 widget 树，全部抽检逐尺寸一致 |
| `sk`(line/query/check-room/csb/speed/winrate) | ✅ Skia(影子层) | W1；6 端点。含 scale 1.5/2.0 与 csb **条件 scale**(`1.5 if <10 else 1.0`) |
| `sk`(player-trace/rank-trace) | ✅ Skia(影子层) | matplotlib 仍渲染图表位图，外壳走影子层，位图当 mem 图传。逐尺寸一致 mean<1 |
| `misc/chara_birthday` | ✅ Skia(影子层,heavy worker) | W1；Canvas bg 是 `ImageBg(运行时卡图)`→ mem 图。抽检逐尺寸一致(672×752) |
| `misc/alias_list` | ✅ Skia(影子层) | 收尾；运行时 alpha-trim 剪影走 mem 图。逐尺寸一致(1185×433)。**注**：当时的整页结果缓存此后已删除（调用方已按 payload 缓存，本地页级缓存必不命中） |
| `gacha`(list/detail)、`event/list`、`vlive/list` | ✅ Skia(影子层) | W6 图中图。实测(20 条目暖缓存)Skia 全胜：vlive **2.46×**、gacha 1.60×、event/list 1.38×；并发 K8 优势放大。**注**：当时“条目本就是 `ImageBox(缓存PIL)`”只对 `event/list`、`vlive/list` 成立，`gacha` 并无条目级合成缓存（见上文[缓存与 Skia 路径的关系](#缓存与-skia-路径的关系现状)） |
| `profile/custom`、`chart`、`honor` | ❌ 当时排除 | `chart`、`honor` 此后已迁；仅 `profile/custom` 仍排除 |

## 首轮能力矩阵（2026-06-23，现已全部关闭）

> 保留原始审计结论作对照；「现状」列按今日代码核实。

### ✅ 已完全覆盖（首轮即覆盖）

| Painter 能力 | Skia 对应 | 备注 |
|---|---|---|
| region 平移模型（offset） | `Group(offset)` | Skia 另支持 rect/rrect clip 与 `mask`，是超集 |
| 延迟命令缓冲执行模型 | Scene IR 树 + `render_scene` | 渲染顺序 = 树顺序，坐标 offset 相对解析 |
| 单行纯色文字 + L/C/R 对齐 | `Text` 节点 | align 基于 advance 测量，真实生效 |
| 文字 CJK「哇」顶基线归一化 | `Baseline::CjkTop` | 复刻「哇」ink 高度锚点，另支持 ascender/alphabetic |
| 半透明文字 | `Text` 的 `Color4` alpha | Skia paint 直接吃 alpha |
| `paste`（拉伸 + alpha-aware） | `Image` fit=stretch | RGBA 源 source-over |
| `paste_with_alpha_blend`（整体 alpha） | `Image.alpha` | 整体不透明度 + source-over |
| `rect` 实心/线性渐变填充 + 实心 stroke | `Rect` 节点 | — |
| `roundrect` 半径/逐角开关/stroke | `RoundRect` 节点 | Skia 原生 RRect AA，优于 supersample-and-shrink |
| `pieslice` 扇形/角度/填充 | `PieSlice` 节点 | AA 优于 Painter 原生 pieslice |
| `blurglass_roundrect` 毛玻璃 | `BlurGlass` 节点 | 背景模糊+tint+三层接触阴影+5 段对角高光+描边 |
| `draw_random_triangle_bg` 时段三角背景 | `TriangleBg` 节点 | 7 段粉色调色板插值+双层 aspect 缩放散布 |
| 线性渐变（2 色，p1/p2） | `GradientSpec::Linear` | RGBA 插值 + `TileMode::Clamp` |

### ⚠️→✅ 首轮的部分覆盖项

| 能力 | 首轮 Skia 现状 | 现状 |
|---|---|---|
| region 增量 helper（shrink/expand/move/restore） | 仅 `group()` 上下文栈 save/restore | ✅ 按设计：Python 侧算好绝对 offset 传入 |
| 字体解析（任意 `FontDesc(path,size)`） | 仅 3 个固定 role | ✅ `FontsIr.extra` + `FontRef.name` 支持任意命名字体 |
| `paste` 投影阴影 | `Shadow` 仅模糊圆角矩形 | ✅ alpha 轮廓投影阴影（跟随图片 alpha） |
| `rect`/`roundrect`/`pieslice` stroke | stroke 仅 `Color4` | ✅ stroke 可用渐变 |
| `roundrect` 圆角 | 单一半径 | ✅ `corner_radii` 四角独立半径 |
| `LinearGradient` | 忽略 `method`，限 2 stop | ✅ N-stop + `method="separate"`（`interp.rs::separate_endpoints` 端点重映射，非近似） |
| 渐变穿任意 mask（`Gradient.get_img(mask=...)`） | 仅形状自身几何作 mask | ✅ 渐变文字（字形 overlay）+ `Group.mask`（图片 alpha 作蒙版，saveLayer + DstIn）。仅「任意 Node 渲染结果作蒙版」未做，无消费者 |
| `adjust_image_alpha_inplace` | `Image.alpha` 仅 multiply | ➖ 该 Pillow helper 已删除，无消费者 |
| `center_crop_by_aspect_ratio`（纯裁剪） | `Image` fit=cover（裁剪 + 缩放） | ✅ `fit=crop` 纯裁剪 + `Image.source_rect` 源像素裁剪窗 |
| `ImageBg`（站点背景） | 仅全屏 cover + NEAREST 采样 | ✅ fit/fill/fixed/repeat + 9 向对齐 + `GaussianBlur(3)` + 压暗（`IRBuilder.image_bg(mode, align, blur, fade)`；采样 bilinear+mipmap） |
| `RandomTriangleBg` | `TriangleBg` 仅 `hour` 参数 | ✅ + `main_hue` + `size_fixed_rate` |
| `Canvas.get_img`（缩放渲染） | 直接按 canvas 尺寸渲染 | ✅ `Scene.scale`；尺寸护栏 `canvas.py::canvas_size_within_limit()` |

### ❌→✅ 首轮的完全缺失项

**1. 布局引擎** —— ✅ **按架构决定留在 Python**（非缺口）。`plot.py` 是声明式 flexbox 式 widget 引擎
（`Frame / HSplit / VSplit / Grid / Flow / Spacer`、ratios、九宫对齐、auto-wrap、`allow_draw_outside`
裁剪、`draw_funcs` 回调、contextvar 嵌套）；IR 的 `Group` 只平移（+ clip/mask），不做布局求解。
IRPainter 让同一棵树照常求解，输出绝对坐标 IR。

**2. 富文本** —— ✅ 全部关闭：

| 首轮缺失能力 | 现状 |
|---|---|
| 换行 / 多行 / 行数上限 / 行距 | ✅ `IRBuilder.wrap_text` / `multiline_text`（Python 预排版，每行一个 `Text` 节点） |
| 溢出省略号 / 自动缩字 | ✅ `multiline_text`（省略号）；影子层由 plot.py `TextBox` 求解 |
| 描边/outline、字间距、行间距 | ✅ Text 节点 outline + `letter_spacing` |
| inline 逐字多色（`<#hex>` 标记） | ✅ `colored_text` + `parse_colored_segments` |
| 渐变文字（渐变穿字形 alpha） | ✅ Text `fill` 支持渐变；IRPainter 侧走字形 overlay 映射 |
| 自适应对比色（`AdaptiveTextColor`） | ✅ IR 原语（背景平均亮度采样选色） |
| 彩色 emoji（Pilmoji + Google emoji） | ✅ emoji 字体 fallback（`FontsIr.emoji`，emoji run 路由到 emoji typeface）；live 路径由 `canvas.py` 传 `DEFAULT_EMOJI_FONT` |
| 文字投影（`draw_shadowed_text`） | ✅ `shadowed_text`（两个 Text 节点） |

**3. 图片重着色** —— ✅ `Image.tint` mode=`multiply` / `mix` / `recolor`；`Image.source_rect` 源像素裁剪窗；
`Group.mask` 覆盖 `putalpha(mask.split()[3])`（honor bonds 已迁）。

**4. 水印** —— ✅ 关闭，但形态与首轮设想不同：

- `Watermark` 节点有了 `IRBuilder.watermark()`，但**live 路径并不用它**：画布水印是 `plot.py` 的
  `add_request_watermark` → `TextBox`，随 widget 树经 `IRPainter.text` 渲染。
- `add_watermark_to_image` 的**光栅页脚**（扩展画布 + 采样底部条作页脚底 + 多行右对齐 + 手工阴影）已在
  Skia 侧复刻：`chart` 与 `honor` 手写 IR，两遍渲染（pass 1 出主图 → pass 2 以 `source_rect` 取底部条拉伸
  作页脚底 + 右对齐阴影文字），Python 全程不碰像素。见 `src/sekai/chart/drawer.py`、`src/sekai/honor/skia.py`。
- 两遍换行 + 自动缩字：`base/draw.py::get_watermark_render_spec` 在 Python 侧求解，两条路径共用。

**5. 动图输出** —— ❌ 仍未做（见[仍未关闭的缺口](#仍未关闭的缺口)）：编码器只出单帧 PNG/JPEG。
`TransparentAnimatedGifConverter` 等 Pillow 侧 helper 亦已删除（零调用方）。

**6. 径向渐变** —— ✅ 真实径向渐变（`GradientSpec::Radial`，多 stop，`interp.rs` 里走
`gradient::shaders::radial_gradient`），首轮的「只平涂中心色」stub 已替换。

## 已知非目标 / 不适用（➖）

以下属基础设施或 Painter 私有缓存，不计入渲染原语覆盖率：
Painter 磁盘缓存 / 线程进程池 offload / image id 序列化、`open_image` 等缓存加载器、
请求时区+时间字符串拼装（纯 Python，任意渲染器可复用）。Skia 侧已有自己的资产路径校验、
Moka 目标栅格缓存（字节预算 + mtime/size 失效）、GIL 释放渲染。

---

> 首轮审计方法：三路并行盘点（Painter / 效果与布局 / Skia IR）+ 交叉对拍合成。如功能有增删，请同步更新本表。
