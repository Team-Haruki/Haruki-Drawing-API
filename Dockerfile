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

# The service requires its matching native wheel; an absent or incompatible wheel is a build failure.
COPY docker/skia-wheels/ /tmp/skia-wheels/
RUN --mount=type=cache,target=$UV_CACHE_DIR \
    set -eux; \
    test "$(find /tmp/skia-wheels -maxdepth 1 -name '*.whl' | wc -l)" -eq 1; \
    uv pip install --python ${UV_PROJECT_ENVIRONMENT}/bin/python /tmp/skia-wheels/*.whl; \
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

# Validate the actual runtime dependency boundary and render with the installed extension.
# The codec smoke reads the capability requirement from Python and exercises native image APIs.
RUN /app/haruki_drawing_api/.venv/bin/python -X gil=0 - <<'PYTHON'
import importlib.util
import sys

assert not sys._is_gil_enabled(), "the service requires CPython free-threading"
for name in ("PIL", "matplotlib", "pilmoji"):
    assert importlib.util.find_spec(name) is None, f"legacy renderer leaked into production: {name}"
from fontTools.ttLib import TTFont  # TMP vector contours require this independently of Matplotlib.
from src.sekai.skia_renderer.canvas import load_native_renderer
native = load_native_renderer()
print(f"native renderer self-check passed (IR_CAPABILITY={native.IR_CAPABILITY})")
PYTHON
RUN /app/haruki_drawing_api/.venv/bin/python -X gil=0 scripts/skia_codec_smoke.py

# 构建溯源。放在自检之后:ARG 的值每次构建都变,写在上面会让它下面的每一层缓存全部失效。
# .github/workflows/docker.yml 一直在传这三个 --build-arg,但 Dockerfile 里没有对应的 ARG,
# 所以它们被静默丢弃了 —— 金丝雀时 `docker inspect` 查不到镜像是哪个 commit 构的。
ARG VERSION=dev
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/Team-Haruki/Haruki-Drawing-API"

# 暴露端口
EXPOSE 8000

# 挂载 data 文件夹（即 config 中的 base_dir），必须挂载到实体机上。
# 配置文件按需 bind-mount 到 /app/haruki_drawing_api/configs.yaml（见 docker-compose.yaml），
# 不需要声明为 VOLUME —— 旧的 config.yaml（单数）这个路径根本没有代码读它，settings.py 读的是 configs.yaml。
VOLUME ["/app/haruki_drawing_api/data"]

# 使用自由线程解释器启动
ENTRYPOINT ["/app/haruki_drawing_api/.venv/bin/python", "-X", "gil=0", "-m", "granian"]
CMD ["--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--workers-kill-timeout", "30", "src.core.main:app"]
