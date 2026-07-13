FROM python:3.14-slim-trixie AS builder

# 构建部分三方包（例如 psutil）需要编译工具链
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 安装 uv（避免依赖 ghcr 拉取权限）
RUN pip install --no-cache-dir uv
# 工作目录
WORKDIR /app/haruki_drawing_api

# 设置uv缓存目录
ENV UV_CACHE_DIR=/root/.cache/uv \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_PYTHON=cpython-3.14.3+freethreaded \
    UV_PROJECT_ENVIRONMENT=/app/haruki_drawing_api/.venv
# 复制依赖文件
COPY pyproject.toml uv.lock ./

# 安装依赖
RUN --mount=type=cache,target=$UV_CACHE_DIR \
    uv python install ${UV_PYTHON} \
    && uv venv ${UV_PROJECT_ENVIRONMENT} --python ${UV_PYTHON} \
    && uv sync --frozen --no-install-project --no-dev --python ${UV_PROJECT_ENVIRONMENT}/bin/python

# 可选的 haruki_skia_renderer 原生渲染 wheel(D2:不发 index,CI 在 docker workflow 里
# 预构建后放进 docker/skia-wheels/)。目录为空时跳过安装,镜像照常构建,运行时 fail-open 回退 Pillow。
COPY docker/skia-wheels/ /tmp/skia-wheels/
RUN --mount=type=cache,target=$UV_CACHE_DIR \
    set -eux; \
    if ls /tmp/skia-wheels/*.whl >/dev/null 2>&1; then \
        uv pip install --python ${UV_PROJECT_ENVIRONMENT}/bin/python /tmp/skia-wheels/*.whl; \
    else \
        echo "no haruki_skia_renderer wheel bundled; skipping native Skia renderer install"; \
    fi; \
    rm -rf /tmp/skia-wheels

# 运行阶段
FROM python:3.14-slim-trixie AS runtime

# 工作目录
WORKDIR /app/haruki_drawing_api
# 复制虚拟环境
COPY --from=builder /app/haruki_drawing_api/.venv /app/haruki_drawing_api/.venv
COPY --from=builder /opt/uv/python /opt/uv/python

# 设置时区，配置环境变量，确保优先使用虚拟环境中的 Python 和 Bin
ENV TZ=Asia/Shanghai \
    PYTHON_GIL=0 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    PATH="/app/haruki_drawing_api/.venv/bin:$PATH"

# 安装图片渲染运行时依赖：
# - libstdc++/libgcc/expat/zlib 是 pjsekai-scores-rs-skia-image wheel 剩余的外部 ELF 依赖。
# - libgl/glib/x11 相关库用于现有图像依赖链。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libstdc++6 \
    libgcc-s1 \
    libexpat1 \
    zlib1g \
    libfreetype6 \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    fonts-noto-color-emoji \
    # 设置时区
    tzdata \
    openntpd \
    # 下载中文字体
    fontconfig \
    ttf-wqy-zenhei \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && fc-cache -fv \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# pjsekai-scores-rs-skia-image bundles an auditwheel FreeType that is too old
# for Skia's FT_Palette_Data_Get reference. Prefer Debian's runtime FreeType and fail
# the image build early if the Python extension still cannot be imported.
RUN set -eux; \
    system_freetype="$(ldconfig -p | awk '/libfreetype\.so\.6 / { print $NF; exit }')"; \
    bundled_freetype="$(find /app/haruki_drawing_api/.venv/lib -path '*/pjsekai_scores_rs_skia_image.libs/libfreetype-*.so.6' -print -quit)"; \
    test -n "$system_freetype"; \
    test -n "$bundled_freetype"; \
    rm "$bundled_freetype"; \
    ln -s "$system_freetype" "$bundled_freetype"; \
    /app/haruki_drawing_api/.venv/bin/python -c "import pjsekai_scores_rs; from pjsekai_scores_rs import Drawing; print(Drawing.jpg)"

# 复制项目代码
COPY . .

# haruki_skia_renderer 自检(仿上方 pjsekai_scores_rs 模式,但条件执行):
# 装了 wheel 就必须能导入且通过 IR capability 握手,失败让镜像构建尽早报错;
# 没装 wheel 仅提示,运行时 fail-open 回退 Pillow。
#
# 门槛值从 canvas.py 的 REQUIRED_NATIVE_IR_CAPABILITY 解析而来,而不是在这里再写一个数字——
# 曾经这里写死 >= 3 而代码要求 5,于是一个 cap-3/4 的旧 wheel 能通过镜像自检、打印"self-check passed",
# 再在运行时被 load_native_renderer() 拒掉,每个绘图端点静默回退 Pillow。自检必须跟着代码走。
# (放在 COPY 之后,因为要读源码。)
RUN /app/haruki_drawing_api/.venv/bin/python - <<'PY'
import importlib.util
import pathlib
import re

source = pathlib.Path("src/sekai/skia_renderer/canvas.py").read_text(encoding="utf-8")
match = re.search(r"^REQUIRED_NATIVE_IR_CAPABILITY\s*=\s*(\d+)", source, re.MULTILINE)
assert match, "cannot find REQUIRED_NATIVE_IR_CAPABILITY in canvas.py"
required = int(match.group(1))

if importlib.util.find_spec("haruki_skia_renderer") is None:
    print("haruki_skia_renderer not bundled; Skia IR rendering will fail-open to Pillow")
else:
    import haruki_skia_renderer as m

    capability = getattr(m, "IR_CAPABILITY", 0)
    assert capability >= required, (
        f"stale haruki_skia_renderer wheel: IR_CAPABILITY={capability} < {required} required by canvas.py"
    )
    print(f"haruki_skia_renderer self-check passed (IR_CAPABILITY={capability} >= {required})")
PY

# 暴露端口
EXPOSE 8000

# 挂载data文件夹，也就是config中的base_dir，必须挂载到实体机上，且在screenshot-service中，也必须挂载该文件夹，二者必须保持名称一致

VOLUME ["/app/haruki_drawing_api/data", "/app/haruki_drawing_api/config.yaml"]

# 使用自由线程解释器启动
ENTRYPOINT ["/app/haruki_drawing_api/.venv/bin/python", "-X", "gil=0", "-m", "granian"]
CMD ["--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--workers-kill-timeout", "30", "src.core.main:app"]
