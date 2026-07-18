# Custom Profile Card → Skia 可行性评估

> 2026-07-12,基于四路代码审计(架构/像素操作普查/性能实测/Skia 映射)。
> 结论:**没有硬阻塞,分阶段可迁**;当初"排除 + 零上行价值"的旧结论被实测推翻。
> 配套:[`skia-migration-restart-plan.md`](./skia-migration-restart-plan.md)、[`skia-migration-todo.md`](./skia-migration-todo.md)。

> **2026-07-18 状态更新**：本文主体是实施前的可行性评估，以下架构与性能数字保留作基线；实际进度
> 以本节和 `skia-migration-todo.md` 为准。

## 当前落地状态（2026-07-18）

- **Phase 0 已完成**：TMP metadata、字形 SDF/轮廓、sprite/atlas 与线程本地字体改为进程级有界缓存，
  并接入 `/cache/stats`；fontTools 成本从每请求重复支付降为签名不变时的一次性成本。
- **Phase 1 已完成**（IR capability 8）：新增 `Transform`。无旋转元素继续用 Python 两步 BICUBIC
  预缩 + 整数位置贴图以保持 Pillow oracle；旋转元素改由 native matrix 单 pass 合成。
- **Phase 2 已完成当前范围**（IR capability 9）：新增 `SdfQuad` 与 A8 raw-buffer transport。Python 保留
  TMP 布局和 PIL uint8 双三次场变形，Rust 执行逐像素着色/合成；28 个 quad 着色约 6 ms。
- **Phase 2b 暂缓且可选**：freetype-rs + 精确 EDT + glyph cache 可进一步去掉 fontTools 的全进程
  GIL 风险，但 Phase 0 已显著降低紧迫性，应等待真实符号卡 payload 和生产 profile 再决定。
- **数据与门禁**：已有两张真实 CN custom-profile 卡可离线重建；`custom_profile_card` 与
  `_collections` 已进入全量 sweep，`_symbol` / `_stamps` 仍因缺真实 payload 记为 `no-payload`。
  当前全量结果为 65 个可渲染用例 `ok`、0 failure，双后端 warm parity 为 0 cache drift/error。
- **终态边界**：custom profile 已不是纯 Pillow 端点，但也不会强行套入 `plot.py` widget 树；它是有
  具体理由的手写 scene 例外。PIL 预缩/AFFINE oracle 语义、TMP 布局和 SDF 场准备在 oracle 退役前保留。

## 实施前架构快照(src/sekai/profile/custom_profile/,~11.7k 行)

四层管线:端点(profile.py:55)→ drawer.py 异步 shim(**每请求新建 PNGRenderer**,整卡一个池任务)
→ `build_native_contents` 把 Unity 布局 JSON 的 **14 类元素桶**展平为按 Unity 绘制序排序的扁平元素树
→ 逐元素栅格化(TMP-SDF 文字 / SDF shape / 14 种 prefab 小部件 / 卡面 / 称号 / 御神签等)
→ 仿射变换(BICUBIC + 2× 旋转超采样)合成到固定 2048×909 画布。

- 输入自包含:`card`(Unity 布局导出)+ `resources`(Cloud 内联的 masterdata 索引)+ `profile_context`(17 键白名单),`masterdata=None`。
- 运行时核心远小于 11.7k 行:约 1.5k 行是 CLI/audit/probe 对拍体系,svg.py 920 行里运行时只用 TMP 富文本标签解析(SVG 渲染器是离线工具)。
- `custom_profile_parallel_workers=1` + 默认 direct-raster 路径 → 渲染器内建并行分支实际是死代码。
- **结果零跨请求缓存**:字形 SDF/图集/sprite 缓存全是实例属性,随请求丢弃;TMP metadata 每请求重新解析。
- `mini_chara`/`screen_filter` 两类元素至今未实现(渲染时跳过),迁移无需覆盖。
- 复用 `src/sekai/honor/drawer`(Pillow)渲染内嵌称号。

## 实施前性能基线(2048×909,3.14t,macOS,中位数)

| 场景 | 冷(=生产行为) | 暖(同 renderer 二渲) |
|---|---|---|
| 一般卡(6 段汉字文本 14 独特字 + 背景 + 4 贴图) | **1.66s** | 0.20s |
| 符号画卡(60 个大号装饰字 + 6 贴图) | **2.76s** | 2.08s |

热点归因:
1. **冷开销大头**:字形 SDF 按请求全丢 + `TTFont` 对每个未命中字符全量重解析字体(~104ms/字;14 字 ≈1.46s)+ `load_font` 无缓存(每请求重开字体文件 200-400 次,0.2-0.3s)。
2. **暖开销**:逐字符实例 numpy SDF 着色(500px quad ≈17ms/字)+ Pillow 仿射 + premul 往返;旋转层 2× 超采样(1600×900 层 259ms)。
3. 模块内**零 perf 日志**;生产上几乎每个请求都触发通用 `pool.task` 慢任务告警(≥0.2s)。

**⚠️ 独立发现:fontTools 的编译扩展未声明 free-threading 安全,import 后全进程 GIL 被重新启用
(实测 `sys._is_gil_enabled()` 变 True)。生产靠 `-X gil=0` 强制压着(官方 at-your-own-risk)。
custom_profile 是全仓唯一引 fontTools 的渲染模块。**

## Skia 可表达性(无硬阻塞)

- **TMP-SDF 着色 = "numpy 写的 fragment shader"**:`shade_tmp_sdf_field`(renderer.py:8389)逐参数复刻
  Unity material floats(_FaceDilate/_OutlineSoftness/_Underlay*/_ScaleRatioA-C 等),纯逐像素 clamp 数学。
  skia-safe 0.99 的 **RuntimeEffect/SkSL**(CPU raster 可用)可逐行直译,SDF atlas 作 child shader;
  或 Rust 手写像素循环(小 quad 上更快、bit-parity 最可控)。代码里已有"先变换 field 再着色"的
  direct 路径(renderer.py:9645),证明管线天然是 shader 形态。
- **Skia 自带字体栅格化 + stroke 承载不了该参数集**(TMP 的 outlineSize 写进 _UnderlayDilate,
  face_dilate/softness 是 SDF 阈值操作,与几何描边不同族)——只配做 fallback,不是路线。
- SDF 数据格式已定型(metadata.json + atlas PNG + 字形表),Rust serde 直接消费同一份。
- 混合/变换零缺口:用到的 blend 只有 SrcOver/DstIn/DstOut/Lighten(解释器已在用);9-slice → `source_rect`×9;
  每字符单色,无顶点渐变;PIL BICUBIC ↔ Skia CubicResampler(Catmull-Rom)同核。
- 动态字形:ctypes FreeType → **freetype-rs 链同一 libfreetype 可 1:1**;EDT(Felzenszwalb)约百行。
  禁止改用 Skia 字体度量(macOS CoreText 后端不一致)。
- **先决缺口只有一个**:IR 的 Group 只有 offset+clip,**无矩阵/旋转**——Transform 节点是任何形态的前提。

## 原推荐路线(渐进,每步独立有收益)

| 阶段 | 内容 | 量级 | 收益 |
|---|---|---|---|
| **0** | 纯 Python:进程级 TMP metadata/字形 SDF/`load_font` 缓存(不动渲染逻辑) | **S** | 冷路径立减 ~60-85%(1.7s → ~0.2-0.5s),与迁移无耦合 |
| **1** | IR 加 **Transform(矩阵)节点**;合成层搬 Skia——Python 各 render_* 照旧出 mem 图,仿射/超采样旋转/premul 往返换原生 canvas | **S~M** | 消掉 Pillow 合成管线与 premul halo 类差异 |
| **2** | **SdfQuad 节点**(SDF field mem 图 + shading uniforms,SkSL 或像素循环);字形 SDF 进程级缓存;度量走 freetype-rs | **M** | 符号画卡 2.1s 常驻成本的解法;甩掉 fontTools GIL 风险。文字重卡估 10-40× |
| 3(可选) | 14 种 prefab 小部件逐个 IR 化(现有节点已覆盖大部分),顺带收掉 honor | L | 终态单路径 |
| ✗ | 整库搬 Rust | XL | 不推荐:8-12k 行数月级重写,等于把 Unity 复刻重走一遍 |

**TMP 布局引擎(~1500 行纯浮点,6 月底刚经历"修了又整体回滚",全模块最脆弱区)留在 Python
发字符 quad,不搬。**

## 原动手前置

1. **对拍数据只在生产**:本地五个 `custom_profile_*` 资产目录全缺,payload 无法离线构造
   (版面数据来自实时 Sekai API 的 userCustomProfileCards)。需从生产拉 `tmp-font-assets/{region}`
   + shape/unity-ui sprite + 若干真实卡 payload。
2. 全程沿用 fail-open 灰度模式(Skia 失败回退 Pillow);Python 路径与 audit/probe 体系保留为对拍基线。
3. 像素 parity 风险面:PIL BICUBIC 缩小语义(kernel 随缩放比放大)、underlay 整数位移 vs 亚像素采样、
   EDT 双实现差异——对拍阈值按节点类型放宽,与 58 端点迁移同一套方法。
