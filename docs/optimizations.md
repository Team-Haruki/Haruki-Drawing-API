# Haruki Drawing API — 性能与内存优化记录

本文档记录本次会话中对项目进行的三轮优化工作。

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
