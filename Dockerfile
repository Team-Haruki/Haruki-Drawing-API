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

# pjsekai-scores-rs-skia-image 0.4.1 bundles an auditwheel FreeType that is too old
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

# 暴露端口
EXPOSE 8000

# 挂载data文件夹，也就是config中的base_dir，必须挂载到实体机上，且在screenshot-service中，也必须挂载该文件夹，二者必须保持名称一致

VOLUME ["/app/haruki_drawing_api/data", "/app/haruki_drawing_api/config.yaml"]

# 使用自由线程解释器启动
ENTRYPOINT ["/app/haruki_drawing_api/.venv/bin/python", "-X", "gil=0", "-m", "granian"]
CMD ["--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--workers-kill-timeout", "30", "src.core.main:app"]
