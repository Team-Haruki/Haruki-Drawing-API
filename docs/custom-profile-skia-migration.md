# Custom Profile 全量 Skia 迁移

更新：2026-07-31

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
- 显式 `honorRequests` 中的 normal/birthday badge：共享 `HonorBadgeBox` 在严格
  `UnitySubscene` 中渲染，不复制一套 IR 布局；Rust asset-info 代替 Pillow 图片头探测。
- `EditUserName`、`Comment`、`TotalPower`、`MultiLive`、`ChallengeLive`、
  `MusicClearInfo`、`MusicClearSelectTabInfo`、`CharacterRankAndChallengeStage` 及其
  scroll 变体：Pillow 和 Skia 消费同一份 General display list；Skia 使用原生字体度量、
  `SlicedImage` 九宫格、asset-backed sprite、IR Text 和递归 viewport/clip。scroll
  依然预检裁剪区外的全部角色行，缺失/损坏素材语义不因裁剪而改变。
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
- `SdfQuad` 的 Python/Pillow A8 field 会正确计入 hybrid telemetry；
- 修复普通元素误分配 2048×909 空 direct layer 的问题。

两张现有真实 fixture 的当前观测：

| fixture | native 元素 | hybrid 元素 | `mem:` | 原始字节 |
|---|---:|---:|---:|---:|
| `custom_profile_card` | 4 | 4 | 4 | 4,545,392 |
| `custom_profile_card_collections` | 9 | 0 | 0 | 0 |

两张卡均完整、无 missing/unresolved；collections 真实卡已经是 `native_pure`。Pillow
对拍当前为：

- `custom_profile_card`: mean 0.587, p99 6；
- `custom_profile_card_collections`: mean 1.307, p99 37。

基础三项、统计三项和完整 collections 的专项门禁均已达到：

- `native == visible`、`hybrid=0`、`mem_images=mem_bytes=0`；
- IR 中没有 `mem:` 引用；
- 请求级 Pillow touch snapshot 为空。

当前握手为 `IR_CAPABILITY=14`、`ASSET_INFO_CAPABILITY=1`、
`TEXT_METRICS_CAPABILITY=1`。旧 wheel 缺少任一必需能力时必须 fail-open，不能静默省略
节点或把 Pillow 度量计成 native-pure。

collection 的主要差异来自透明像素上的 straight-RGBA Pillow BICUBIC 与 Skia premul
Catmull-Rom 语义。它通过现有 custom-profile 预算，但在扩大静态素材覆盖前仍需增加
透明 bbox、缩小和旋转专项 fixture。

## 剩余迁移批次

### A. Honor / Bonds Honor

normal/birthday 的 `UnitySubscene` 基础已经完成：先在自然尺寸透明离屏面渲染共享
`HonorBadgeBox`，再复用 `UnityImage` 的两段缩放和 Unity placement。

不能直接把 honor 子树 splice 到主画布：

- badge 内的 `paste_src` 会清掉主画布上透明角下面的既有像素；
- 对每个子节点施加 CTM 不等价于“完整 badge 栅格化后再整体缩放”。

剩余工作是把这套 emitter 接入 `HonorDeck` 的 profile slots，并把 bonds 背景/头像的
resize-then-clip 操作补到共享 Painter/IR。每个 slot 必须声明 required/optional/fallback；
真实 fixture 的三个 slot 目前只提供一个显式 request，不能把另外两个静默缺失计成完整。

### B. Card Member 与 General Prefab

- 把卡面 crop/cover、圆角 mask、frame、attr、rarity、master-rank、等级文字抽成共享
  display list，供 Pillow 与 IR 两端消费。
- 基础、统计和 CharacterRank 系列已经完成共享 display list + 原生 replay，第二张真实
  fixture 的 6,541,908 字节 hybrid raster 已全部移除。
- 第一张真实 fixture 的直接收益顺序是 `LeaderCard`（1,992,800 B）→ `Deck`
  （757,944 B）→ `HonorDeck`（700,000 B）→ 普通 text（403,920 B）。卡牌布局必须先
  抽成共享 `CardDisplayList`；不能在 `skia.py` 复制 Pillow composer。
- 卡牌等级条需要 `Rect blend="src"`，该通用 IR 能力已随 capability 14 完成。剩余关键
  能力是 cover/overlay/最终 Deck 缩放的 Pillow-compatible Lanczos，以及嵌套离屏 resize。
- 当前 Leader/full 和 Deck/clip 三条历史路径都没有启用 mask；迁移不能顺手增加圆角，
  否则两端会一起偏离现有输出。顶层 `card_member` 还没有真实 fixture，启用 native 前
  必须补 capture。
- 禁止在 drawer 内按 backend 分叉复制布局。

### C. TMP Text

Python 暂时保留 TMP 解析和布局 oracle，逐步把像素工作移到 Rust：

1. Rust 直接读取 atlas/dynamic glyph，移除 RGBA text layer；
2. 把 glyph field warp 移入 Rust，移除 A8 `mem:`；
3. 将字体轮廓/EDT 与 glyph cache 移入 native；
4. 最后替换 Pillow 字体度量，使普通与装饰 TMP 都不触碰 Pillow。

每一步都要覆盖 rich tags、空行、alignment、outline/underlay、symbol、emoji、旋转和
中日文字体。

### D. Dynamic Content

`mini_chara` 与 `screen_filter` 当前缺 DynamicAtlasStudio texture/uvRect 输入。要么扩展
请求资源契约并捕获真实 fixture，要么明确标为不支持；不能继续在 native 成功路径中
静默跳过。

## 合并与上线门禁

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
- native 成功路径不调用 `render_content_for_card`、`prepare_direct_sdf_quads` 或任何
  Pillow/NumPy 像素操作；
- `native_pure == skia` 请求数。

上线采用同一个 `HARUKI_DRAWING__USE_SKIA_PLOT` 回滚开关，先在有明确 cgroup 内存限制的
canary 上验证 shape-heavy、透明素材、最大合法请求和恶意超限请求，再扩大流量。
