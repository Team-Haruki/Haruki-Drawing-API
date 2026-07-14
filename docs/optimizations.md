# Haruki Drawing API — 性能与内存优化记录

本文档记录对项目进行的各轮性能 / 内存优化工作。

> 各节保留了当时的优化动机与手法；其中凡是描述**当前行为**的部分（函数签名、缓存 key、配置项、日志 logger 名）
> 均已按现状校正，可直接作为现有模式的依据。渲染后端（Rust + Skia）迁移的当前状态见
> [`rust-skia-renderer-migration.md`](./rust-skia-renderer-migration.md)。

---

## 一、内存泄漏修复

**涉及文件**

- `src/sekai/base/utils.py`
- `src/sekai/base/painter.py`
- `src/sekai/sk/drawer.py`
- `src/core/main.py`

### 问题列表

| 级别     | 位置                                  | 描述                                                                                                                              |
|--------|-------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| HIGH   | `base/utils.py`                     | `tmp/` 目录下的临时文件永不清理，随时间无限增长                                                                                                    |
| HIGH   | `base/utils.py` / `base/painter.py` | 线程池（`base/utils.py` 的 `ThreadPoolExecutor`）与进程池（`base/painter.py` 的 `_painter_process_pool: ProcessPoolExecutor`）在应用退出时从未 `.shutdown()` |
| HIGH   | `base/painter.py`                   | `Painter.get()` 里异常路径不执行 `finally`，线程本地 Painter 对象可能永久残留                                                                        |
| MEDIUM | `base/painter.py`                   | 字体缓存每线程最大 128 条，而自由线程模式下线程数可达 32+，总内存占用大                                                                                       |
| MEDIUM | `base/painter.py`                   | Painter 磁盘缓存（`PAINTER_CACHE_DIR` 下的 PNG 文件）从不清理过期条目                                                                             |
| LOW    | `sk/drawer.py`                      | SK 图表使用的额外资源在进程存续期间永久持有                                                                                                        |

> **勘误（下列符号当年就写错了，不是后来变的；本节其余内容为当时的执行记录）**
> - 进程池从来不在 `base/utils.py`：`src/sekai/base/utils.py` 只有 `_default_pool_executor`
>   （`ThreadPoolExecutor`），`ProcessPoolExecutor` 一直是 `src/sekai/base/painter.py:43` 的
>   `_painter_process_pool`，由 `shutdown_painter()` 关闭。
> - **进程池已于 2026-07-14 整个删除**(`use_process_pool` / `process_pool_workers` /
>   `process_pool_threshold` 与 `_painter_process_pool` 一并移除)。它是 GIL 时代的设计——存在意义就是
>   绕开 GIL。3.14t 上没有 GIL 可绕,但把每张解码好的图 pickle 过进程边界的代价一分不少:实测并发 8 时
>   吞吐 `1.35 → 2.00 r/s`(**+48%**),而全部 python 进程的 RSS 合计几乎不变(2384 vs 2325 MB)——
>   它只是把内存挪进子进程。本节下文关于 `ProcessPoolExecutor` 的记述是当年的执行记录,保留不改。
> - Painter 磁盘缓存**从未**基于 `diskcache`：`src/` 里没有、也从来没有过该依赖
>   （`git log -S diskcache --all` 只命中一次修改本文档自身的提交）。它自始至终就是
>   `PAINTER_CACHE_DIR` 下的 PNG 文件，靠 `glob("<cache_key>__*.png")` 命中 / 失效
>   （`base/painter.py:795`）。此处只订正符号，不为它补写实现史。

### 修复方案

**临时文件清理** (`base/utils.py`)

新增 `cleanup_expired_tmp_files()`：`TempFilePath.__exit__` 把「路径 + 过期时刻」登记到待删列表
（`remove_after=None` 表示用完立即删除），清理函数只删已到期的条目。
`core/main.py` 的 lifespan 起后台任务，每 `TMP_CLEANUP_INTERVAL`（当前 300 秒）扫一次；
shutdown 阶段 `shutdown_utils()` 再兜底清理一次。

**线程池 / 进程池关闭** (`base/utils.py`, `base/painter.py`, `sk/drawer.py`)

新增 `shutdown_utils()`、`shutdown_painter()` 和 `shutdown_sk_drawer()`，在 lifespan 的 shutdown
阶段依次调用，确保 `base/utils.py` 的 `ThreadPoolExecutor` 与 `base/painter.py` 的
`ProcessPoolExecutor`（`_painter_process_pool`，由 `shutdown_painter()` 负责）优雅退出。
`shutdown_utils()` 另外清空全部六份进程内缓存（`_image_cache` / `_thumb_cache` / missing-placeholder /
`_load_asset_image_ref_cached` / `_composed_image_cache` / `skia_payload_cache`，
逐个 `img.close()` 后清零字节计数），并再调一次 `cleanup_expired_tmp_files()`——注意它**只删已到期的条目**，
未到期的会被重新排回待删列表，随进程退出而遗留在磁盘上，等下次启动的清理任务处理。

**Painter 异常安全** (`base/painter.py`)

将 `Painter.get()` 中的资源释放逻辑迁移到 `try/finally` 块，保证即使绘图过程中抛异常
也会正确清理线程本地 Painter 状态。

**字体缓存缩减** (`base/painter.py`)

将每线程字体 LRU 缓存上限从 128 降至 32，大幅降低多线程场景下的总字体内存占用，
同时保留高频字号的缓存效果。

**磁盘缓存定期清理** (`base/painter.py`)

新增 `Painter.cleanup_old_disk_cache(max_age_days=7)`：Painter 的磁盘缓存现在就是
`PAINTER_CACHE_DIR` 下的一堆 PNG 文件，按 mtime 删除超过 7 天的条目。
它与 `cleanup_expired_composed_image_disk_cache()`（composed 图片的落盘缓存，见 `base/utils.py`）
一起在 lifespan 启动阶段执行一次，之后由后台任务每 `DISK_CACHE_CLEANUP_INTERVAL`（当前 3600 秒）重复执行。

---

## 二、可配置图片导出格式（PNG / JPG）

**涉及文件**

- `src/settings.py`
- `src/core/utils.py`
- `configs.yaml`
- `configs.docker.yaml`

### 需求

本轮改动时，所有绘图端点都经由 `src/core/utils.py` 中的 `image_to_response()` 返回图片。
需要在配置中新增 `export_image_format`（`"png"` 或 `"jpg"`）和 `jpg_quality`（整数），
不同格式对应不同的编码逻辑。

> 现状订正：「所有端点都走 `image_to_response()`」已不成立。`src/core/pjsk/` 下 18 个 router
> 全部导入了 `encoded_image_payload_to_response()`；其中 `src/core/pjsk/deck.py` **根本不导入**
> `image_to_response()`，只有 `return encoded_image_payload_to_response(payload)` 一条返回路径。
> 其余 17 个 router 两者都在用。详见下文「当前形态」。

### 实现

`src/settings.py` — `DrawingSettings` 新增字段：

```python
export_image_format: Literal["png", "jpg"] = "png"
jpg_quality: int = 85
```

`src/core/utils.py` — `image_to_response()` 分支处理：

- 格式为 `"jpg"` 时，将 RGBA 图像转换为 RGB（避免透明通道报错），
  以 `quality=jpg_quality` 保存为 JPEG，`Content-Type` 设为 `image/jpeg`。
- 格式为 `"png"` 时，行为不变。

`configs.yaml` / `configs.docker.yaml` 新增：

```yaml
drawing:
  export_image_format: "png"   # "png" or "jpg"
  jpg_quality: 85
```

### 当前形态

- 端点可用 `image_to_response(image, export_format=None, jpg_quality=None, *, jpeg_subsampling=None)`
  的参数逐次覆盖全局配置；编码本身走 `run_in_pool()`，不阻塞事件循环。
- Skia 后端直接产出编码字节时不再回到 PIL：`src/core/utils.py` 另有
  `encoded_image_payload_to_response(payload)`，把已编码的 `EncodedImagePayload` 直接包成
  `StreamingResponse`，省掉一次解码 + 重编码。
- 两条路径都会打一条 `image.response ... backend=<pillow|skia|skia_cache|skia_fallback>` 的 INFO 日志
  （标签定义见 `src/sekai/skia_renderer/render_stats.py`，聚合计数由 `GET /render-stats` 暴露）。

---

## 三、asyncio.gather 并发优化

**涉及文件（11 个）**

`card`, `deck`, `education`, `event`, `gacha`, `misc`, `music`, `mysekai`, `profile`, `score`, `stamp`
各自的 `drawer.py`。

> 注：公开仓库里的 `src/sekai/mysekai/drawer.py` 是占位 stub，真实实现是本地的
> `src/sekai/mysekai/drawer.real.py`（见 CLAUDE.md），本节 mysekai 相关的改动都落在 `drawer.real.py` 中。

### 背景

项目运行在 CPython 3.14 自由线程（no-GIL）模式下。`get_img_from_path()` 本身是
async 函数，通过 `run_in_pool()` 把 I/O 卸载到 `ThreadPoolExecutor`。
在自由线程模式下，多个线程可以真正并行执行，因此把串行的 `await` 序列改为
`asyncio.gather()` 能带来实际的 wall-clock 加速。

### 问题模式

**模式 A — 列表推导式串行 await**

```python
# 改前
imgs = [await get_img_from_path(BASE, p) for p in paths]
# 改后
imgs = await asyncio.gather(*[get_img_from_path(BASE, p) for p in paths])
```

**模式 B — 多个独立 await 顺序执行**

```python
# 改前
a = await load(x)
b = await load(y)
c = await load(z)
# 改后
a, b, c = await asyncio.gather(load(x), load(y), load(z))
```

**模式 C — 布局树内部 await（需预加载缓存）**

布局树（`HSplit` / `VSplit` / `Grid` 等）通过 `with` 上下文管理器注册子组件，
在 `with` 块内不能 `await`。需要在进入布局树之前把所有图片预加载到字典缓存：

```python
# 改后：先预加载
_cache = dict(zip(paths, await asyncio.gather(*[load(p) for p in paths])))
# 再在树内同步查询
with Grid():
    for p in paths:
        ImageBox(_cache[p])
```

**模式 C 的当前形态 —— 预加载的往往是 ref 而不是解码后的位图**

`ImageBox` / `ImageBg` / `Painter.paste*` 现在都接受 `ImageSource = PIL.Image | AssetImageRef | EncodedImageRef`
（`src/sekai/base/utils.py`）。多数 builder 已把 `get_img_from_path()` 换成
`get_asset_image_ref()`——只探 header、不解码像素，Skia 后端可以直接把源路径写进 IR，
Pillow 后端在真正 paste 时才按需解码。gather 预加载的模式不变，变的只是缓存里放的东西。
细节见 [`rust-skia-renderer-migration.md`](./rust-skia-renderer-migration.md)。

同理，卡面缩略图这类「多层合成」的小图不再预先 compose 成一张 PIL 图：
`get_card_full_thumbnail_layers()` 只在树外并行取回各层 ref，树内由 `CardFullThumbnailBox`
（`src/sekai/profile/drawer.py`）组装成子树。

### 各文件改动摘要

| 文件                    | 主要改动                                                  | 估计加速        |
|-----------------------|-------------------------------------------------------|-------------|
| `misc/drawer.py`      | 3 个独立 await + 列表推导 → 1 次 gather                       | ~3×         |
| `stamp/drawer.py`     | 所有印章图片进树前预加载                                          | ~N× (N=印章数) |
| `education/drawer.py` | 5 个函数各自预加载图标（jewel/shard/chara/unit/attr/bond/leader） | ~2–5×       |
| `music/drawer.py`     | 声乐 logo + 活动 banner 并行 gather                         | ~2×         |
| `score/drawer.py`     | 曲目封面 + Meta 封面并行预加载                                   | ~N×         |
| `card/drawer.py`      | 卡面/服装/缩略图/图标合并为单次 gather；列表缩略图 gather                 | ~5–8×       |
| `event/drawer.py`     | 卡牌缩略图 + 活动图片全部 gather 到字典                             | ~4–6×       |
| `deck/drawer.py`      | 条件图标 + 卡牌缩略图 + 对比封面合并为单次 gather                       | ~3–5×       |
| `profile/drawer.py`   | 框架部件 / 卡牌缩略图 / 播放图标 / 角色排名图标全部 gather                 | ~3–6×       |
| `gacha/drawer.py`     | 列表 logo 预加载；详情（logo/banner/cost icon/卡牌/稀有度图）预加载      | ~5–8×       |
| `mysekai/drawer.py`   | 天气/到访角色/地区资源/家具/大门升级材料全部预加载；genre/tag/misc 图标循环并行化    | ~3–10×      |

### 重要注意事项

**phenom 图片需 `.copy()`**

当同一路径的图片会被后续代码修改（`resize`、`draw X` 等），
必须对预加载缓存中的图片调用 `.copy()`，避免修改影响其他使用同一缓存条目的场景。

**布局树子组件 draw 不并行化**

`HSplit` / `VSplit` / `Grid` 的 `draw()` 阶段操作共享可变的 `Painter` 状态，
是有意串行执行的，无需也不应并行化。

---

## 四、性能日志

各 `compose_*` 函数的主要 `asyncio.gather` 预加载阶段均加入了 `logger.debug`
计时日志，格式统一为：

```
[perf] <function_name> preload <N> items: 0.123s
```

日志默认在 `DEBUG` 级别输出，生产环境使用 `INFO` 级别时不会产生噪声。
如需启用，设置对应模块的日志级别为 `DEBUG` 即可，例如：

```python
import logging

logging.getLogger("src.sekai.card.drawer").setLevel(logging.DEBUG)
```

### 命名 perf logger（INFO 级别，默认可见）

在上面的 `[perf]` DEBUG 计时之外，重端点各自有一个专用的 `*.perf` logger，直接以 `INFO` 输出
（缓存命中/未命中、各阶段耗时、后端选择等），用于线上排查而无需改日志级别。当前存在的 logger：

| Logger                                                                                 | 位置                                  |
|----------------------------------------------------------------------------------------|-------------------------------------|
| `card.draw.perf` / `card.endpoint.perf`                                                  | `src/sekai/card/drawer.py`、`src/core/pjsk/card.py` |
| `event.draw.perf`                                                                        | `src/sekai/event/drawer.py`         |
| `vlive.draw.perf`                                                                        | `src/sekai/vlive/drawer.py`         |
| `honor.draw.perf`                                                                        | `src/sekai/honor/skia.py`           |
| `chart.draw.perf`                                                                        | `src/sekai/chart/drawer.py`         |
| `plot.draw.perf`（**不输出计时**，只在 Skia 回退/异常时告警）                                | `src/sekai/skia_renderer/`（canvas / render_stats） |
| `mysekai.endpoint.perf` / `mysekai.map.perf` / `mysekai.fixture_list.perf` / `mysekai.musicrecord.perf` / `mysekai.talk_list.perf` | `src/core/pjsk/mysekai.py`、`src/sekai/mysekai/drawer.real.py` |

上表只列**真正会出日志**的 logger。另有一个 `misc.birthday.perf`（`_birthday_perf_logger`，
定义在 `src/sekai/misc/drawer.py:58`）：它在整个 `src/` 里**没有任何调用点**，因此不输出任何东西，
调它的日志级别也不会有效果——想要生日端点的耗时，得先给它补上调用。

新增性能敏感路径时沿用同一命名（`<模块>.<场景>.perf`），不要另起 logger 体系。

---

## 五、Resize 缓存（全局，跨请求）

**涉及文件**

- `src/sekai/base/utils.py`
- `src/sekai/mysekai/drawer.real.py`（公开仓库中的 `drawer.py` 是 stub）
- `src/sekai/profile/drawer.py`

### 背景

即使 `_image_cache` 已为原图缓存，每次请求仍会对同一张图片执行相同尺寸的
`resize` 操作，因为 resize 结果仅保存在 per-request 的局部 dict 中，进程
重启或新请求都无法复用。

### 缓存 key

原图与 resize 结果放在同一套缓存里（通用池 / 缩略图池，见下），key 是 6-tuple（`_ImageCacheKey`）：

```
(full_path_str, mtime_ns, file_size, target_w, target_h, resample)
```

- `(target_w, target_h) = (0, 0)`，`resample = 0` — 原始尺寸（不 resize）
- `(target_w, target_h) = (w, h)` — exact resize
- `(target_w, target_h) = (-max_w, -max_h)` — contain-resize（负值区分）
- `resample` 也进 key：`PASTE_RESAMPLE`（BICUBIC，ref-backed paste 用）与 `get_img_resized()`
  的 BILINEAR / LANCZOS 结果在同一尺寸下互不串味。改动 resample 默认值等于换一组 key。

**落在哪个池**由路径决定，不由调用方决定：路径里含 `"thumbnail"` 的走 `_thumb_cache`，其余走
`_image_cache`（`_is_thumbnail_path()` / `_cache_enabled()`）。resize 结果同样按这条规则分流，
所以缩略图的缩放结果计入缩略图池的配额。

### 新增 API

| 函数                                                                        | 说明                                    |
|---------------------------------------------------------------------------|---------------------------------------|
| `get_img_resized(base, path, w, h, *, resample=BILINEAR, on_missing=...)`  | Exact resize，结果缓存                     |
| `get_img_resized_long_edge(base, path, long_edge, *, resample=BILINEAR)`   | Long-edge 等比缩放（内部转成 exact resize 走同一缓存） |
| `batch_load_and_contain_resize(base, paths, max_w, max_h)`                 | 批量 contain-resize，同步，供 run_in_pool 使用 |

### 应用场景

**`mysekai/drawer.real.py` — harvest_points（地图采集点图标）**

改前：`resize_keep_ratio()` 直接调用，结果存入 per-request 局部 dict，每次请求全部重算：

```
harvest_points resize: 0.07–0.42s / 请求
```

改后：`asyncio.gather` 并行调用 `get_img_resized_long_edge()`，结果进全局缓存：

```
harvest_points resize: 0.01–0.05s / 请求（暖缓存）
```

**`mysekai/drawer.real.py` — 家具 / 音乐 / 对话缩略图**

三份重复的 `_batch_load_and_resize` 局部函数统一替换为
`batch_load_and_contain_resize()`，缩略图 resize 结果同样写入全局缓存。

**`profile/drawer.py` — x_icon（24×24）**

单张图标改用 `get_img_resized(... 24, 24)`。

### 实测效果（/map 端点，4 地图，~160 采集点）

|                       | 首次请求（冷） | 再次请求（暖） |
|-----------------------|---------|---------|
| harvest_points resize | ~0.38s  | ~0.10s  |
| 全流程 draw              | 1.563s  | 0.751s  |
| 端到端 total             | 1.621s  | 0.792s  |

总体约 **2× 提速**（draw 阶段），主要收益来自 resize 从 O(N·请求数) 降为
O(N) 首次 + O(1) 后续。

### 配置要求

Resize 缓存复用全局图片池（`_image_cache` / `_thumb_cache`），需在 `configs.yaml` 中启用。
仓库内当前的默认值：

```yaml
drawing:
  image_cache_size: 1024       # 通用池条目数上限
  image_cache_max_mb: 256      # 通用池内存上限（MB）
  thumbnail_cache_size: 2048   # 缩略图池条目数上限
  thumbnail_cache_max_mb: 256  # 缩略图池内存上限（MB）
```

某一池的 size 或 max_mb 为 0 时该池关闭，落在它上面的 resize 全部退化为每次重算
（缩略图路径不会因此回落到通用池）。运行时命中/淘汰计数见 `GET /cache/stats`（`src/core/health.py`）。
