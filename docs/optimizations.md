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
`clear_runtime_memory_caches()` 是统一的进程内缓存清理入口：清理 `_image_cache` / `_thumb_cache` /
missing-placeholder / 路径与 AssetRef 缓存 / `_composed_image_cache` / `skia_payload_cache` / custom-profile
缓存，并调用原生扩展的 `clear_renderer_caches()` 清空 Rust Moka 栅格与尺寸缓存。`shutdown_utils()` 在关闭
线程池后复用该入口，并再调一次 `cleanup_expired_tmp_files()`——注意它**只删已到期的条目**，
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
  `encoded_image_payload_to_response(payload)`，把已编码的 `EncodedImagePayload` 经 `_image_response()`
  作**单条 body 消息**（`Response(content=...)`）发出，省掉一次解码 + 重编码。
  > ⚠️ **不要改回 `StreamingResponse(io.BytesIO(...))`**（本文早期版本就是这么写的，那是个 bug）：
  > 字节本来就整块在内存里，流式一点内存都省不下；但它把一个**同步**可迭代对象交给 Starlette，而
  > Starlette 会用 `iterate_in_threadpool()` 逐项取——**`BytesIO` 的迭代协议是按行的**，二进制 PNG 于是
  > 在每个 `0x0A` 处被切开：实测平均 384 字节一块，一张 870 KB 的图约 2300 次线程池往返 + 2300 条 ASGI
  > 消息（7.3 MB 的 card/box 约 19000 块），`Content-Length` 也丢了。并发 8 时，服务端 0.12 秒画完的图，
  > 客户端要等约 10 秒，而 CPU 全程 95% 空闲。修复后吞吐 **32 倍**（0.78 → 24.8 req/s）。
  > 回归锁：`tests/test_image_response.py` 断言 **ASGI body 消息数 == 1**（不能断言字节内容——坏版本的
  > 字节也是对的，只有消息数会露馅）。见 commit `5792b02`。
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

原有 `get_img_resized(... 24, 24)` 改为 `AssetImageRef + ImageBox(fill, sampling="linear")`。Pillow replay
仍用原 BILINEAR 内核，Skia 路径则不再在 Python 解码和缓存这张图。

同批次把 `misc/chara_birthday` 无 padding 的 80×80 card thumbnail 也改为 ref+linear；calendar icon
与 alias jacket 刻意保留 `get_img_resized`：它们在 40/92 BILINEAR 预缩后还会因 4px padding 再缩到
32/84（Painter 默认 BICUBIC）。折叠成一次 draw 会改变历史像素，不能用“显式 linear”直接替代。

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


## 六、Native 文字与素材准备（2026-09-08）

常规文字保持原有 FreeType BASIC 字形算法。Rust 缓存不可变 A8 蒙版及排版结果，避免相同文字
在每次请求中反复 load glyph、排版和栅格化；位置、颜色和最终混合仍在原绘制节点上处理。
字体路径、文件签名、字号和文本共同构成 key，命中仍遵守调用者的像素限额。

`HARUKI_SKIA_TEXT_MASK_CACHE_MB` 默认 64 MiB，设为 `0` 后重启可禁用；这是独立于 raster/fragment
池的进程预算。`/cache/stats` 的 `native_renderer_cache.text_mask_cache_*` 提供容量和命中统计，
公共缓存清空接口会一并清除。FreeType face 仍属于各自线程，不共享 mutable face。

自定义名片的备用字形距离场接入既有 `GLYPH_SDF_CACHE`，与动态字形共用
`custom_profile_glyph_cache_size/max_mb`，没有增加新池。缓存按输入 mask 内容、尺寸、spread、
threshold 和算法版本区分，float32 字段存成不可变 bytes，按实际四字节样本计重；不改变精确 EDT
或引入 OpenCV。场景内存限制在命中之前检查，字形/配置变化、禁用、清空和淘汰均保持重新计算能力。

歌曲列表使用 `get_asset_image_refs` 批量探测封面与去重成绩图标，两个批次用 `asyncio.gather`
重叠。树中直接使用这些 metadata refs；它们不组成新的图片/resize缓存，缺图、路径 override
和排序保持原行为。

冷对拍关闭文字及字形缓存，验证重新计算；热对拍使用正常缓存，验证命中、跨页面穿插和清空后的
像素一致性。性能比较计入完整响应编码，并与修改前同一工作区快照交替运行；禁止使用对拍耗时
宣称提速。正式测量记录见 `out/native-speed-implementation/`。

本机合并后的 69 个公共样本，各侧预热两次、交替测五次：中位耗时之和
15802.41 → 12842.51 ms（1.230×，时间减少 18.73%），69 项 PNG 字节全部一致。
缺字名片 11.33×、背包列表 1.35×、歌曲列表 1.26×、卡牌仓库 1.16×；这些是本轮配对结果，
不能与此前调查的绝对毫秒值跨轮相减，也不是生产流量加权吞吐。少数样本复测快慢反转，
追加 CPU 时间诊断后争议样本差距约在 ±3% 内，未据此宣称所有场景全面提速。

验证：1292 项 Python 测试、105 项 Rust 测试通过；严格冷对拍 77 项通过，69 个公共样本的
实际服务检查通过；两侧热对拍各 76 项一致，0 缓存漂移（另有既有时钟样本及用户排除样本）。
报告保留全部原始复测和私有诊断边界；Linux 固定配额负载和发布 gate 仍由 release runner 验证。

### 2026-09-08：修复与当前 main 对拍暴露的片段复用开销

称号在加载素材、构建/降低徽章树之前查询原生徽章片段。静态键不包含 `dt`/`timezone`，
但包含请求字段、中央素材清单的签名、渲染配置和代码指纹；片段仍验证实际素材与字体依赖。
命中的不可变像素引用跨越本次渲染，水印继续逐请求生成。性能对拍在计时前清空原有的称号
整页 payload 缓存，避免把整页命中当成渲染提速。

VLive 接入 `prepare_cached_canvas`，命中条目后跳过素材加载和布局。缓存键先对 Pydantic 模型
执行 `model_dump(mode="json")` 再收集素材签名，并使用实际绘制的开始/结束/状态文本；旧分钟桶
没有覆盖分钟内变化的倒计时，直接传模型对象的素材收集也没有覆盖内部路径。背景毛玻璃和水印
保持在条目片段之外。新增测试验证时间变化、素材到达/替换/消失、清空/关闭缓存与水印更新。

基线仍为 main `c616b79` 默认混合路径。三方 24 次平衡顺序复测：生日称号 main/修复前/修复后
分别为 4.76/4.39/3.64 ms；VLive 为 147.49/178.15/162.30 ms。四种称号均快于 main，VLive
比本轮修复前减少约 8.9%，但仍慢于 main 约 10%。剩余主要开销在原生毛玻璃透明度修正图层；
三个保持该样本像素一致的原生实验均更慢，已经撤回，本轮没有保留 Rust 或扩展改动。

验证：1295 项 Python 测试通过；严格冷回归 77 ok、2 无样本、公开失败 0；严格热回归两后端
各 76 ok、1 个既有时钟非确定项、2 无样本，漂移/错误 0。10 次实际 ASGI 热请求全部纯原生，
片段命中 34 次，无 Pillow 导入，变化的 `dt` 没有命中称号整页缓存。69 个公共样本修复前后
PNG 全部一致。私有 MySekai 和未捕获符号/贴纸维持此前的验收排除范围。

完整记录及按加速倍数排序的表：`out/main-regression-fix/REPORT.md`；全量三方数据为
`bench/results.json`，重点复测为 `recheck/results.json`。全量当前分支相对 main 为约 1.795×，
这是此前全部改动的累计效果，不能归因于本轮两个片段优化。

### 2026-09-08：复用原生三角背景，追回 VLive 热路径

顶层、完整、无变换的 TriangleBg 将不可变像素瓦片放入既有 Rust raster 池。背景和素材共用
`HARUKI_SKIA_RASTER_CACHE_MB` 容量，没有增加独立缓存池；瓦片最多 512 行并遵守 max-entry，
单个背景含 key 的计重不超过全池四分之一。命中持有的全部瓦片也必须满足当前场景的剩余内存
预算。尺寸、实际调色板字节和全部三角形输入构成 key；颜色及几何变化正常失效，不冻结时钟。
水印、内容和毛玻璃仍逐请求绘制，未把时间相关页面放入最终响应缓存。

首次仍绘制完整背景，再截取像素快照，避免分块重算渐变改变舍入。回放前取得全部强引用，
部分缺失就重画；查询不会遇到首块缺失就退出，以免 Moka 只累积前缀瓦片的访问频率。补缓存
只复制缺失部分，避免缓存满时重复复制仍存在的瓦片。原生 cache stats 新增
`background_cache_hits/misses/bypasses`；条目/字节并入原 raster 统计，原清空入口一并清除。

main c616b79 默认混合路径、本轮前纯原生快照、当前实现三方各 24 次平衡顺序复测，完整 PNG
响应中位数：VLive 135.96/149.41/124.45 ms；活动列表 22.07/23.11/18.55 ms；音乐列表
991.41/477.79/464.57 ms。当前分别比 main 快约 9.2%、19.0% 和 2.134×。69 个公共样本
修复前后 PNG 全部一致；全量六次中位数合计本轮前 8.837 s、当前 8.186 s，减少约 7.37%。
这是本机样本集合的合计，不是生产流量加权吞吐，不能将不同轮次绝对耗时直接相减。

固定 VLive IR 的 24 次隔离复测：热缓存 137.52→110.08 ms，先填满 raster 池再测为
135.60→110.09 ms；每次清空所有原生缓存为 144.18→140.35 ms，未测到明显冷退化。
但每次改变背景颜色导致完全未命中时为 134.50→137.51 ms，约多 3 ms（2.2%）。首次保存
有复制成本，低频且背景每次变化的流量不能按热缓存收益估计。

尚未宣称所有样本全面超越 main：旋转装饰名片本轮 24 次复测仍慢约 5.7%（CPU 约 3.2%），
部分毫秒级称号在修复前后的中位数也有波动；所有原始轮次和按倍数排序的表保存在
`out/native-background-optimization/REPORT.md`。本轮只改原生背景复用，没有改这些名片的绘制
逻辑或移除透明度修正。Rust 扩展已重新构建，IR 能力号仍为 28。

验证完成：1307 项 Python 测试、105 项 Rust 测试通过；严格冷回归 77 ok，69 个公共样本
实际服务无 Pillow 检查通过。12 次实际 ASGI 热请求全部 native_pure、零 Pillow 导入，
背景时钟正常推进；4 次页面背景中 1 次有效命中，3 次重新计算，没有假设固定命中率。

标准热回归没有缓存漂移，但多人查房的相对日期文本在运行中跨过 11:04 的“几天前”边界，
导致严格 gate 返回 1。以 11:03 / 11:05 精确复现原先两个图像哈希，且两后端均为变化后的
热图等于同时刻冷图。未扩充豁免名单；仅在诊断 wrapper 固定相对日期显示时钟后，完整严格
两后端复验各 76 ok、1 个既有 event_planner 非确定项、2 无样本，漂移/错误和 strict_issues
均为 0。生产时钟没有改动。原始失败和复验均保留在报告中，便于区分时钟变化与缓存错误。

### 2026-09-08：复用源字体已确认缺字，追回装饰名片热路径

进一步分析旋转装饰名片发现，原生 SdfQuad 的 3 个节点只占约 0.11 ms，而 Python 在每个
请求对源字体缺少的 `〜`（U+301C）重复解析 cmap，单次查询约 29.29 ms 后返回 None。
现在只有成功解析并确认的缺字结果才以不可变 `SourceGlyphAbsent` 放入既有有界轮廓池。
键覆盖独立命名空间、字体路径、mtime_ns、大小和码点，字号无关；元数据计入原容量，
清空/禁用/淘汰沿用原入口。FreeType 每次优先查询，临时失败不缓存，字体变化会失效。
请求内 TTFont 对象也核对签名后才复用，没有跨线程共享可变字体句柄。

main c616b79 默认混合路径、本轮前背景优化最终状态、当前实现三方各 24 次平衡顺序复测，
完整 PNG 响应中位数（main / 本轮前 / 当前）：

| 样本 | main ms | 本轮前 ms | 当前 ms | 本轮加速 |
|---|---:|---:|---:|---:|
| 字体回退名片 | 207.06 | 109.85 | 21.51 | 5.108× |
| 装饰文字名片 | 42.66 | 40.93 | 11.28 | 3.629× |
| 旋转装饰名片 | 41.92 | 42.96 | 14.72 | 2.918× |
| 描边文字名片 | 53.55 | 49.20 | 20.77 | 2.369× |

字体回退样本在旧 main 实际回退 Pillow，其他三项走其默认 native/hybrid；当前全部纯原生。
旋转装饰热请求现在比 main 快 2.848×。69 个公共样本本轮前后 PNG 字节全部一致。

每次清空缓存的 12 次复测没有数倍收益：装饰文字 295.92→305.50 ms、旋转装饰
318.94→311.37 ms、字体回退 779.50→786.06 ms，前后变化约 ±3.2% 内。前两项冷请求
仍比 main 慢约三倍，属于本轮前已有差距。全量 6 次热请求中 chart 也比 main 慢约 2.3%，
因此不宣称所有场景全面超越。

另外试验过仅缓存背景渐变：真实秒数推进时有收益，但调色板每次变化时退化约 5%，
已完整撤回，并重建、核对扩展 SHA256 与本轮前一致。本轮最终只改两处 custom-profile
Python 源文件，没有增加 Rust/IR 改动。原始数据与完整排序见
`out/native-followup-optimization/REPORT.md`。

本轮验证：1315 项 Python、105 项 Rust 测试通过；严格冷回归 77 ok、公共服务检查
69/69 ok。固定相对日期显示时钟的完整严格热回归，两侧各 76 ok、1 个既有非确定项、
2 无样本，零漂移/错误，未扩充豁免名单。18 次真实 ASGI 热请求全部 native_pure、
零 Pillow 导入，新缺字缓存命中 13 次；生产背景时钟未固定。Ruff 与 diff 检查通过。

### 2026-09-08：消除装饰名片约三倍的冷缓存退化

继续定位首次渲染发现：源字体轮廓会为少数字符重复展开整份字形/度量表，TMP 元数据
也提前构造了一万多个没有被使用的度量对象。现在复用请求内带签名的 FontTools reader，
按需解码 TrueType glyf/hmtx/vmtx，并省去轮廓绘制不需要的 CFF 度量字典。索引标签只用于
查找，字形几何仍交给原 FontTools；保留复合字形、lsb 修正、小数 CFF 和命名 seac，
CFF2/VARC/可变 TrueType 维持通用读取路径。reader 的 context manager 覆盖可变表和文件
游标的锁，因为同一请求的参考渲染也可能并行处理图层；从 bytes 惰性解析避免滞留 OS fd。

TMPGlyphTable 在既有八项元数据缓存中按需生成 frozen 度量，签名捕获时已经读取所有
原始行，不延迟素材读取、不新增池。Rust source_outline_sdf 对相同 FontTools 展平轮廓
执行原 NumPy 的 float32 距离/绕数/量化，保留原计算次序、int16 环绕和 ties-to-even。
输入有像素、点数、工作量和有限坐标上限；老扩展或超出 helper 范围仍使用原 NumPy。
没有新 IR 节点，能力号维持 28，需重建 wheel 才能取得这部分收益。

24 次均衡顺序三方冷请求复测（每次清空应用/原生缓存，完整 PNG 响应中位数）：

| 样本 | main c616b79 ms | 本轮前 ms | 当前 ms | 本轮加速 |
|---|---:|---:|---:|---:|
| 装饰文字名片 | 137.91 | 352.04 | 114.97 | 3.062× |
| 旋转装饰名片 | 112.58 | 327.75 | 111.78 | 2.932× |
| 字体回退名片 | 962.40 | 850.69 | 141.17 | 6.026× |

装饰文字冷请求快于 main；旋转项中位数只差 0.80 ms，按“基本追平”报告，不能宣称稳定
领先。其 CPU 中位数 main/当前为 118.88/111.48 ms。这里的冷请求不包括进程启动、模型
校验和 OS 页缓存清空，其他测试轮次不能直接相减。main 字体回退样本实际走默认 Pillow
回退，另外两个目标走其原生字形路径。

重点热请求各 24 次基本持平；按用户要求排除 chart 性能评估后的 68 个公共样本，本轮
前后 PNG 全部逐字节一致。完整数据、优化阶段与微测参数限制见
`out/native-cold-profile-optimization/REPORT.md`。1348 项 Python、105 项 Rust 测试已通过；
18 次实际 ASGI 请求全部 native_pure、零 Pillow 导入，原生源字形距离场计算执行 12 次，
TMP 元数据只按需生成 7 个度量值。

本轮最终严格冷回归 77 ok、零问题；固定相对日期显示时钟的完整严格热回归两侧各
76 ok、1 个既有非确定项、2 无样本，零缓存漂移/错误，未新增豁免。全部流水线退出码 0。


### 2026-09-08：保持 native 并恢复 main 普通文字外观

普通页面 IRPainter 文字恢复 main 的 Skia glyph 路径；嵌入 NativeSubtree 和显式 mask-lerp
保留原生 FreeType BASIC。活动列表整页保留 BASIC 作为紧像素预算的兼容例外；不在 drawer
复制布局、不放宽预算。WebP 补画和已有缩放/透明度修复保留。

68 公共热样本与 main：53 项 RGB mean 缩小、15 项不变、0 项变差；多人查房/查询/停车榜/
分数表平均差下降约 99.7%–99.8%，逐像素完全相同仍为 6 项。完整 PNG 中位数累计耗时
相对修改前增加 4.13%，仍比 main 快 1.79×。最终 Python 1349 passed；严格冷 77 ok；
严格热两侧各 76 ok、1 既有非确定性、2 无样本，零漂移/错误；公共 no-Pillow 服务门槛通过。
范围、初版失败记录、最终数据和剩余差异见本地 out/main-text-parity/REPORT.md。
