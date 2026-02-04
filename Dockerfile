FROM python:3.13-slim-bookworm AS builder

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
# 工作目录
WORKDIR /app/haruki_drawing_api

# 设置uv缓存目录
ENV UV_CACHE_DIR=/root/.cache/uv
# 复制依赖文件
COPY pyproject.toml uv.lock ./

# 安装依赖
RUN --mount=type=cache,target=$UV_CACHE_DIR \
    uv sync --frozen --no-install-project --no-dev

# 运行阶段
FROM python:3.13-slim-bookworm AS runtime

# 工作目录
WORKDIR /app/haruki_drawing_api
# 复制虚拟环境
COPY --from=builder /app/haruki_drawing_api/.venv /app/haruki_drawing_api/.venv

# 设置时区，配置环境变量，确保优先使用虚拟环境中的 Python 和 Bin
ENV TZ=Asia/Shanghai \
    PATH="/app/haruki_drawing_api/.venv/bin:$PATH"

# 安装 opencv 所需库
RUN apt-get update && apt-get install -y --no-install-recommends \
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


# 复制项目代码
COPY . .

# 暴露端口
EXPOSE 8000

# 挂载data文件夹，也就是config中的base_dir，必须挂载到实体机上，且在screenshot-service中，也必须挂载该文件夹，二者必须保持名称一致

VOLUME ["/app/haruki_drawing_api/data", "/app/haruki_drawing_api/config.yaml"]

# 使用uv启动
ENTRYPOINT ["uvicorn", "src.core.main:app"]
CMD ["--host", "0.0.0.0", "--port", "8000"]