# Custom Profile 全量 Skia 迁移

更新：2026-08-10

## 目标

`/api/pjsk/profile/custom-profile-card` 的成功原生路径最终必须同时满足：

- 所有可见元素都有明确处理结果，`missing == unresolved == 0`；
- `mem_images == 0`、`custom_profile_pil_mem_raster == 0`；
- `/render-stats` 将请求计为 `native_pure`，而不是仅仅 `backend=skia`；
- 恶意 scale、超大字体、超多元素、超大源图和多素材累计内存都在分配前拒绝；
- native 失败继续 fail-open，但 Pillow 回退不能绕过相同的输入、路径和源图预算。

## 当前里程碑

已原生化：

- `shape`：`SdfShape` 在 Rust 中解码 straight RGBA，独立读取距离场和 alpha，计算
  fwidth/outline 并合成；Python 不再建立按 scale 放大的 NumPy 数组。
- `general_background`、`story_background`、`stand_member`、普通 `collection`、`other`、
  `stamp`：`UnityImage` 由 Rust 完整解码、顺序执行 object/post 两段缩放并放置。
- normal/birthday/bonds/empty badge：显式 request 与 masterdata 派生 request 都复用共享
  `HonorBadgeBox` 的 asset-backed `NativeSubtree`，再放入严格 `UnitySubscene`；统一资源
  manifest 负责分支、必需/可选素材和 hybrid 语义，Rust asset-info 代替 Pillow 图片头探测。
- `EditUserName`、`Comment`、`TotalPower`、`MultiLive`、`ChallengeLive`、
  `MusicClearInfo`、`MusicClearSelectTabInfo`、`CharacterRankAndChallengeStage` 及其
  scroll 变体：Pillow 和 Skia 消费同一份 General display list；Skia 使用原生字体度量、
  `SlicedImage` 九宫格、asset-backed sprite、IR Text 和递归 viewport/clip。scroll
  依然预检裁剪区外的全部角色行，缺失/损坏素材语义不因裁剪而改变。
- `LeaderCard`、`Deck` 和直接 `card_member`：Pillow 与 Skia 消费同一份
  `CardDisplayList`。卡面 cover、frame、属性、稀有度、等级和 master-rank 布局只保留
  一份；Deck 的每张卡先在自然尺寸子场景完成，再按历史两段 Lanczos 顺序缩放。
- `StoryFavorite`：Pillow 与 native adapter 消费同一份 General display list；空数据和
  无横幅 fallback cell 可复用现有原生 sprite/Text。带横幅的请求仍严格 fallback：
  Lanczos cover 已精确，但 Pillow 离散 L-mask 与 Skia rrect clip 的圆角边缘不同，不能
  在没有精确 mask primitive 时冒充 pure native。
- `HonorDeck`：共享 plan 固化 profile/ordinary request-key 优先级、panel 和三个 slot；
  所有 Honor 分支都可复用原生子树。所有 slot 在写场景前原子预检，禁止只画出一部分
  badge。
- 普通 `/profile` 也通过通用 `RasterSubscene` 嵌入同一棵 Honor 子树：子树在自然尺寸
  隔离合成，随后只对完整 snapshot 做最终缩放和整图阴影；三个真实 fixture badge 不再
  经过 Pillow 合成或 `mem:` 传输。
- 严格普通 TMP 文本子集：动态源字体、无 rich/decorative、outline、underlay 或材质效果
  的文本直接发出 IR Text，支持非均匀缩放与旋转，不再建立 RGBA `mem:` layer。超出该子集
  仍明确走现有 SdfQuad/Pillow 路径。
- rich/decorative TMP 的静态 atlas 字形：Python 只保留 TMP layout、裁剪框、目标尺寸、
  Pillow AFFINE 逆矩阵与着色参数；`SdfAtlasQuad` 在 Rust 中读取 atlas alpha，并执行与
  Pillow 12.3 L/BICUBIC 匹配的 crop-resize、warp 和 SDF shading。静态字形不再产生 A8
  `mem:` 或 Pillow 像素操作。
- rich/decorative TMP 的动态/回退源字体字形：Python 只选择严格位于数据根目录内的源字体、
  codepoint、TMP bbox/padding、目标尺寸、warp 与 shading 参数；`SdfFontQuad` 在 Rust 中用
  未 hint 的 Skia 字体轮廓按固定 24 段展开曲线，直接生成 uint8 signed-distance field，再
  复用同一套 Pillow 12.3 兼容的 L/BICUBIC resize/warp。成功路径不再调用 fontTools/NumPy
  contour grid、Pillow L image 或传输 A8 `mem:`。
- `paste_lerp`：Rust 精确实现 Honor 历史
  `destination.paste(source, pos, source)` 的 straight-RGBA 四通道插值；仅接受严格预检的
  integral stretch，并由隔离子场景包含 Src/paste_lerp 对目的像素的读写。
- `pillow_lanczos`：Rust 对 asset-backed Image stretch/居中 cover 和零旋转
  `UnitySubscene` 实现 Pillow 12.3 兼容的 straight-RGBA Lanczos-3；所有源图、中间图、
  crop、readback、Skia copy 和系数 scratch 都计入场景预算。不支持的组合直接令整场
  fail-open，不允许静默换成 Catmull-Rom。
- 最终场景合成与 PNG 编码。

已补安全/正确性契约：

- 路由元素数、有限数、scale、文字大小/长度限制，以及 custom-profile 专用并发槽；
- Pillow 与 Rust 共用的单层像素限制；Rust IR 额外携带全场字节预算；
- native 源图在绘制前完整解码，单节点和多素材累计内存都受限；
- `UnitySubscene` 在任何素材解码前完成纯几何/峰值内存预检，子树内缺失或损坏素材会
  令整场 fail-open；
- 请求提供的素材路径 canonicalize 后必须位于配置的数据根目录；
- 可见 `missing/unresolved` 元素会在调用 Rust 前令整场 fallback，禁止返回残缺的
  “Skia 成功”；
- 遗留 `SdfQuad` 的 Python/Pillow A8 field 会正确计入 hybrid telemetry；静态
  `SdfAtlasQuad` 和动态 `SdfFontQuad` 无 `mem:` 时按 native 计数；
- 修复普通元素误分配 2048×909 空 direct layer 的问题。

两张现有真实 fixture 的当前观测：

| fixture | native 元素 | hybrid 元素 | `mem:` | 原始字节 |
|---|---:|---:|---:|---:|
| `custom_profile_card` | 8 | 0 | 0 | 0 |
| `custom_profile_card_collections` | 9 | 0 | 0 | 0 |

两张卡均完整、无 missing/unresolved，且都已经是 `native_pure`。Pillow 对拍当前为：

- `custom_profile_card`: mean 0.844, p99 17；
- `custom_profile_card_collections`: mean 1.307, p99 37。

基础三项、统计三项、Card/Deck、HonorDeck、普通 TMP 文本和完整 collections 的专项门禁均已达到：

- `native == visible`、`hybrid=0`、`mem_images=mem_bytes=0`；
- IR 中没有 `mem:` 引用；
- 请求级 Pillow touch snapshot 为空。

当前握手为 `IR_CAPABILITY=19`、`ASSET_INFO_CAPABILITY=1`、
`TEXT_METRICS_CAPABILITY=1`。旧 wheel 缺少任一必需能力时必须 fail-open，不能静默省略
节点或把 Pillow 度量计成 native-pure。

动态源字体 SDF 有独立的进程级 Moka cache，默认 64 MiB、单项 4 MiB，可用
`HARUKI_SKIA_SDF_FONT_CACHE_MB` / `HARUKI_SKIA_SDF_FONT_CACHE_MAX_ENTRY_MB` 回滚或调节；
`renderer_cache_stats()` 暴露 entries/bytes/limits，单请求 native metrics 暴露
hit/miss/coalesced/bypass。缺字、字体文件回退、非法 Unicode、超限 outline/field、超过
64M 次距离计算或错误 Transform 嵌套都会令整场 fail-open，不能返回少字的“原生成功”。

collection 的主要差异来自透明像素上的 straight-RGBA Pillow BICUBIC 与 Skia premul
Catmull-Rom 语义。它通过现有 custom-profile 预算，但在扩大静态素材覆盖前仍需增加
透明 bbox、缩小和旋转专项 fixture。

## 剩余迁移批次

### A. Honor / Bonds Honor

normal/birthday/bonds/empty 已完成共享子树：先在自然尺寸透明离屏面渲染
`HonorBadgeBox`，再由 custom profile 的 `UnitySubscene` 执行两段缩放和 Unity
placement；普通 `/profile` 则用 `RasterSubscene` 执行显式目标矩形和整图阴影。

隔离节点是必要契约，不能直接把 Honor 子树摊平到主画布：

- badge 内的 `paste_src` 会清掉主画布上透明角下面的既有像素；
- 对每个子节点施加 CTM 不等价于“完整 badge 栅格化后再整体缩放”。

真实 fixture 的三个 `HonorDeck` slot 已补齐：原有 normal main，加上生产脱敏 capture
中的两个 birthday sub（6833/6877）及六个 JP 公共素材。生成器的 `--honor-capture`
会核对 capture schema、三个 slot、region、request-path 与素材全集，并拒绝覆盖内容不同
的本地文件。该元素专项对拍为 RGB mean 0.0394、p99 1、alpha exact，
`native_pure=1`、Pillow touch 为空、`mem_images=mem_bytes=0`；原子预检仍保留，缺任一
slot/request/素材时整元素 fallback，禁止“部分原生”。

bonds 的共享几何 plan 明确表示“整图 resize → destination clip”，没有误写成 IR
`source_rect`（后者是先 crop source 再 resize）；frame/word/star 等覆盖层通过
`paste_lerp` 保留历史 alpha-mask paste。代码路径已经原生化，发布覆盖声明前仍需补
bonds main/sub 与 HonorDeck bonds slot 的真实 capture。

### B. Card Member 与 General Prefab

- 卡面 crop/cover、frame、attr、rarity、master-rank、等级文字已经抽成共享
  `CardDisplayList` 并由 Pillow/IR 两端消费；当前活动路径没有 mask，迁移没有擅自增加圆角。
- 基础、统计和 CharacterRank 系列已经完成共享 display list + 原生 replay，第二张真实
  fixture 的 6,541,908 字节 hybrid raster 已全部移除。
- 第一张真实 fixture 的 `LeaderCard`（1,992,800 B）、`Deck`（757,944 B）、普通 text
  （403,920 B）和 `HonorDeck`（700,000 B）已经全部清零；两张现有真实 fixture 均无
  hybrid raster。
- 卡牌等级条的 `Rect blend="src"` 和精确 Pillow Lanczos 已完成。顶层 `card_member`
  已有严格的 asset-backed synthetic 门禁，但仍缺真实 capture；发布 native 覆盖声明时
  必须补 full/clip 各一张真实输入。
- `StoryFavorite` 已完成共享布局；空数据/fallback-only 可走现有原生 primitives。横幅
  圆角仍需 Pillow L-mask 兼容 primitive，且现有两张 capture 的 favorites 都为空；还需
  补 banner、fallback、超过八项滚动三类真实 fixture。
- 禁止在 drawer 内按 backend 分叉复制布局。

### C. TMP Text

普通动态源字体的无效果子集已经直接使用 IR Text。rich/decorative 的静态 atlas 与动态
source-font 字形也已分别通过 `SdfAtlasQuad` / `SdfFontQuad` 把 SDF 像素生成、crop-resize、
仿射 warp 与 shading 全部移入 Rust；Python 仍保留 TMP 解析和布局 oracle。真实字体专项
验证中，旧 fontTools/Pillow 字段与 `SdfFontQuad` 的 2,400 个像素逐字节一致，冷请求记录
miss，第二次相同字形记录 hit。剩余工作按以下顺序继续：

1. 用类别级最小 fixture 验证 static rich/decorative、symbol、旋转和 underlay；不保留
   完整请求、整卡、用户或 profile/resources 数据；
2. 用类别 fixture 覆盖 dynamic/fallback 的 symbol、中日文字体、缺字及 fallback font；
   `SdfFontQuad` 实现与缓存本身已完成；
3. 最后把装饰 TMP 剩余的 Python/FreeType/fontTools 度量选择收敛为严格 native batch，并
   清点 em-block、emoji、材质效果等仍会落入遗留 `SdfQuad` 的类别。

每一步都要覆盖 rich tags、空行、alignment、outline/underlay、symbol、emoji、旋转和
中日文字体。

### D. Dynamic Content

`mini_chara` 与 `screen_filter` 当前缺 DynamicAtlasStudio texture/uvRect 输入。要么扩展
请求资源契约并捕获真实 fixture，要么明确标为不支持；不能继续在 native 成功路径中
静默跳过。

## 合并与上线门禁

### 真实输入的保留边界

后续补 symbol、stamp、card-member、bonds honor 和 StoryFavorite 覆盖时，不保留完整请求、
完整 `customProfileCard`、`profile_context` 或资源总表。root-only 审阅进程只能通过
`scripts/parity_payloads/custom_profile_category_fixture.py` 提取一个类别中明确选中的元素；
输出 schema 会递归拒绝完整请求字段、任意 `user*` 字段和直接用户标识，并以 0600 写入。
TMP symbol 文本只保留 TMP 标签、空白、标点和符号类别，普通字母、数字及中日文字会被替换。

类别 fixture 的依赖也必须是人工裁剪后的该类别最小子集；禁止把 `resources` 或
`profile_context` 换名后整体塞入 `dependencies`。完成有用的类别提取后应立即清除原始输入。

每一批至少通过：

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest -q

PYLIB=$(uv run python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
RUSTFLAGS="-L $PYLIB -C link-arg=-lpython3.14t" \
  cargo test --release --manifest-path rust/haruki_skia_renderer/Cargo.toml

uv run maturin develop --release \
  --manifest-path rust/haruki_skia_renderer/Cargo.toml
uv run python -X gil=0 scripts/skia_parity_sweep.py \
  --only custom_profile_card,custom_profile_card_collections
```

新增内容桶必须带独立 fixture；最终 strict gate 应断言：

- `custom_profile_complete == 1`；
- `custom_profile_native_elements == custom_profile_visible_elements`；
- `custom_profile_hybrid_elements == 0`；
- `custom_profile_mem_images == custom_profile_mem_bytes == 0`；
- IR JSON 不含 `mem:`，请求 Pillow touch snapshot 为空；
- native 成功路径不调用 `render_content_for_card` 或任何 Pillow/NumPy 像素操作；
  `prepare_direct_sdf_quads` 只允许承担布局/几何规划，不能生成像素；
- `native_pure == skia` 请求数。

上线采用同一个 `HARUKI_DRAWING__USE_SKIA_PLOT` 回滚开关，先在有明确 cgroup 内存限制的
canary 上验证 shape-heavy、透明素材、最大合法请求和恶意超限请求，再扩大流量。
