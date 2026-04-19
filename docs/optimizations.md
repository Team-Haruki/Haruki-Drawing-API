# Haruki Drawing API — 性能与内存优化记录

本文档记录本次会话中对项目进行的各轮优化工作。

---

## 一、内存泄漏修复

**涉及文件**

- `src/sekai/base/utils.py`
- `src/sekai/base/painter.py`
- `src/sekai/sk/drawer.py`
- `src/core/main.py`

### 问题列表

| 级别     | 位置                | 描述                                                                  |
|--------|-------------------|---------------------------------------------------------------------|
| HIGH   | `base/utils.py`   | `tmp/` 目录下的临时文件永不清理，随时间无限增长                                         |
| HIGH   | `base/utils.py`   | `ThreadPoolExecutor` / `ProcessPoolExecutor` 在应用退出时从未 `.shutdown()` |
| HIGH   | `base/painter.py` | `Painter.get()` 里异常路径不执行 `finally`，线程本地 Painter 对象可能永久残留            |
| MEDIUM | `base/painter.py` | 字体缓存每线程最大 128 条，而自由线程模式下线程数可达 32+，总内存占用大                            |
| MEDIUM | `base/painter.py` | `diskcache.Cache` 磁盘缓存从不清理过期条目                                      |
| LOW    | `sk/drawer.py`    | SK 图表使用的额外资源在进程存续期间永久持有                                             |

### 修复方案

**临时文件清理** (`base/utils.py`)

新增 `cleanup_expired_tmp_files(max_age_seconds)` 函数，在 `core/main.py` 的 lifespan
中每 30 分钟执行一次，清理超过 1 小时未修改的 `tmp/` 文件。

**线程池 / 进程池关闭** (`base/utils.py`, `sk/drawer.py`)

新增 `shutdown_utils()` 和 `shutdown_sk_drawer()`，在 lifespan 的 shutdown 阶段被调用，
确保 `ThreadPoolExecutor` / `ProcessPoolExecutor` 优雅退出。

**Painter 异常安全** (`base/painter.py`)

将 `Painter.get()` 中的资源释放逻辑迁移到 `try/finally` 块，保证即使绘图过程中抛异常
也会正确清理线程本地 Painter 状态。

**字体缓存缩减** (`base/painter.py`)

将每线程字体 LRU 缓存上限从 128 降至 32，大幅降低多线程场景下的总字体内存占用，
同时保留高频字号的缓存效果。

**磁盘缓存定期清理** (`base/painter.py`)

新增 `cleanup_old_disk_cache()`，在 lifespan 启动阶段调用，清理超过 7 天的磁盘缓存条目。

---

## 二、可配置图片导出格式（PNG / JPG）

**涉及文件**

- `src/settings.py`
- `src/core/utils.py`
- `configs.yaml`
- `configs.docker.yaml`

### 需求

所有 14 个绘图端点统一通过 `src/core/utils.py` 中的 `image_to_response()` 返回图片。
需要在配置中新增 `export_image_format`（`"png"` 或 `"jpg"`）和 `jpg_quality`（整数），
不同格式对应不同的编码逻辑。

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

---

## 三、asyncio.gather 并发优化

**涉及文件（11 个）**

`card`, `deck`, `education`, `event`, `gacha`, `misc`, `music`, `mysekai`, `profile`, `score`, `stamp`
各自的 `drawer.py`。

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

---

## 五、Resize 缓存（全局，跨请求）

**涉及文件**

- `src/sekai/base/utils.py`
- `src/sekai/mysekai/drawer.py`
- `src/sekai/profile/drawer.py`

### 背景

即使 `_image_cache` 已为原图缓存，每次请求仍会对同一张图片执行相同尺寸的
`resize` 操作，因为 resize 结果仅保存在 per-request 的局部 dict 中，进程
重启或新请求都无法复用。

### 缓存 key 扩展

`_image_cache` 的 key 从 3-tuple 扩展为 5-tuple，同时存放原图与 resized 结果：

```
(full_path_str, mtime_ns, file_size, target_w, target_h)
```

- `(target_w, target_h) = (0, 0)` — 原始尺寸（不 resize）
- `(target_w, target_h) = (w, h)` — exact resize
- `(target_w, target_h) = (-max_w, -max_h)` — contain-resize（负值区分）

### 新增 API

| 函数                                                         | 说明                                    |
|------------------------------------------------------------|---------------------------------------|
| `get_img_resized(base, path, w, h)`                        | Exact resize，结果缓存                     |
| `get_img_resized_long_edge(base, path, long_edge)`         | Long-edge 等比缩放，结果缓存                   |
| `batch_load_and_contain_resize(base, paths, max_w, max_h)` | 批量 contain-resize，同步，供 run_in_pool 使用 |

### 应用场景

**`mysekai/drawer.py` — harvest_points（地图采集点图标）**

改前：`resize_keep_ratio()` 直接调用，结果存入 per-request 局部 dict，每次请求全部重算：

```
harvest_points resize: 0.07–0.42s / 请求
```

改后：`asyncio.gather` 并行调用 `get_img_resized_long_edge()`，结果进全局缓存：

```
harvest_points resize: 0.01–0.05s / 请求（暖缓存）
```

**`mysekai/drawer.py` — 家具 / 音乐 / 对话缩略图**

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

Resize 缓存复用全局 `_image_cache`，需在 `configs.yaml` 中启用：

```yaml
drawing:
  image_cache_size: 2560     # 缓存条目数上限
  image_cache_max_mb: 2048   # 缓存总内存上限（MB）
```

两项均为 0 时缓存关闭，所有 resize 退化为每次重算。
