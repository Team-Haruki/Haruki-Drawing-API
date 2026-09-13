# Haruki Drawing API

Haruki Drawing API 是 Team Haruki 的 Project Sekai 图片生成服务。它接收 JSON 请求并输出 PNG/JPG，覆盖玩家资料、卡牌、活动、歌曲、谱面、招募、成绩和 MySekai 等页面。

当前版本为 `3.1.1`。生产绘图必须使用 Rust + Skia 后端；缺失或过旧的原生扩展会阻止构建/启动，渲染失败不再调用 Pillow。Pillow 仅作为开发对照环境的依赖，继续消费共享 widget 树来验证像素。

## 运行要求

- CPython 3.14 free-threaded（3.14t）
- 启动时必须关闭 GIL；普通 CPython 3.14 会被服务拒绝
- 匹配 CPython 3.14t 和部署平台、满足当前 IR capability 的 `haruki_skia_renderer` wheel
- 项目素材目录和 `configs.yaml`
- 推荐使用 `uv`

## 本地启动

安装锁定依赖后启动 Granian：

```bash
uv sync --frozen
uv run maturin develop --release --manifest-path rust/haruki_skia_renderer/Cargo.toml
uv run --no-sync granian --interface asgi --host 0.0.0.0 --port 8000 src.core.main:app
```

也可以显式使用自由线程解释器：

```bash
python -X gil=0 -m granian --interface asgi --host 0.0.0.0 --port 8000 src.core.main:app
```

配置从项目根目录的 `configs.yaml` 读取。环境变量使用 `HARUKI_` 前缀和双下划线表示嵌套项，并覆盖 YAML，例如：

```bash
HARUKI_DRAWING__THREAD_POOL_SIZE=16 \
HARUKI_DRAWING__USE_SKIA_PLOT=true \
uv run granian --interface asgi --host 0.0.0.0 --port 8000 src.core.main:app
```

## Docker

```bash
docker compose up --build
```

Compose 默认把 `./data` 挂载到容器内 `/pjskdata/Data`，并把 `configs.docker.yaml` 挂载为运行配置。`data/`、`out/`、根目录请求 JSON、环境文件以及私有 MySekai 实现均被排除在 Docker 构建上下文之外。

公开仓库中的 `src/sekai/mysekai/drawer.py` 只是接口占位文件。生产环境必须将真实实现 bind-mount 到同一路径；不要把 `drawer.real.py` 复制进镜像或提交到仓库。

Docker 构建前必须在 `docker/skia-wheels/` 放入且只放入一个匹配目标平台的 wheel。构建检查原生能力、实际编解码及生产依赖树，Pillow、Matplotlib、Pilmoji 均不得存在。标签发布使用 GitHub 托管 runner 构建并通过 ABI、能力握手和原生编解码检查的同一个 wheel。

## 运维端点

- `GET /health`：进程存活状态
- `GET /ready`：流量接入就绪状态及资源阈值
- `GET /cache/stats`：图片、Skia 原生栅格/尺寸缓存与其他渲染缓存统计
- `GET /render-stats`：各端点的 Skia、回退、禁用和错误计数

`HARUKI_DRAWING__USE_SKIA_PLOT` 必须保持 `true`；设为 `false` 会拒绝启动。需要恢复旧 Pillow 服务时，应回滚至此前包含旧后端的镜像。

## 开发与验证

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest -q
```

修改绘图路径后还应执行 Pillow/Skia 对拍：

```bash
uv run python -X gil=0 scripts/skia_parity_sweep.py
uv run python -X gil=0 scripts/skia_warm_parity.py --backend both
```

Linux 发布门槛（输出目录必须不存在，避免复用旧报告）：

```bash
uv run python -X gil=0 scripts/skia_release_gate.py --out-dir out/release-gate
```

该命令要求完整资产和 `out/parity-payloads/`，串联冷像素、禁止 Pillow 的绘图入口/完整服务、双后端热缓存检查。私有 MySekai 与尚未捕获的 symbol/stamps 按约定仅作诊断，不阻塞发布。

标签工作流复用 `.github/workflows/skia-wheels.yml`，在 GitHub 托管 runner 上构建并检查 wheel；镜像作业下载同一次工作流中通过检查的 Linux wheel，保留生产依赖无 Pillow 检查和实际编解码自检。发布不需要自建 runner 或私有素材。

完整素材对拍保留为手动验收：渲染或缓存逻辑变化时，在具备资产和样本的 Linux 环境运行上述命令。也可手动触发 `.github/workflows/renderer-release.yml`；只有该可选工作流需要配置 `RENDER_VALIDATION_RUNNER`、`RENDER_ASSETS_DIR`、`RENDER_PAYLOAD_DIR`、`RENDER_CONFIG_PATH`。路径必须位于 runner checkout 之外；请求样本与图片不上传为工作流诊断产物。

生产依赖安装使用 `uv sync --frozen --no-dev`，随后安装匹配的 wheel；默认开发组包含 `legacy-renderer`，因此本地对拍与测试仍可使用 Pillow。

并发拉图示例：

```bash
python scripts/concurrent_fetch_images.py \
  --base-url http://127.0.0.1:8000 \
  --endpoint /api/pjsk/profile/ \
  --payload-file payloads/profile.json \
  --requests 100 \
  --concurrency 16 \
  --output-dir out/profile-load \
  --save-errors
```

更多性能、缓存和 Skia 迁移背景见 `docs/optimizations.md` 与 `docs/rust-skia-renderer-migration.md`。

## 私有实现与素材

本仓库不分发生产 MySekai drawer、游戏素材、用户上传内容或请求抓包。部署方需要自行准备并确认其使用权限。请勿将这些内容打入公开镜像或提交到版本库。

## 来源与许可

- 谱面预览能力源自 [Sekai-World](https://github.com/Sekai-World) / pjsekai.moe
- 技能覆盖与 Music meta 数据来源说明沿用 3.3.dev（xfl03）
- 项目代码以 [MIT License](./LICENSE) 发布
