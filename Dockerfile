# Layer order is chosen so a release that only bumps the version re-ships only the small top layers
# (native wheel when it changed, app code, labels). From the bottom of the runtime image:
#   1. debian:trixie-slim (shared with the asset-updater image on the render nodes)
#   2. apt runtime packages                      -> changes with the package list / base image
#   3. free-threaded CPython from uv             -> changes with PYTHON_BUILD / UV_VERSION only
#   4. third-party wheels (uv export of the lock without the project itself)
#                                                -> changes only when a dependency changes, not on a
#                                                   version bump (the project line is not exported)
#   5. haruki_skia_renderer wheel                -> changes only when the renderer bytes change
#   6. app code                                  -> every release
# Every self-check runs with PYTHONDONTWRITEBYTECODE=1 so it adds no __pycache__ to the image.

ARG PYTHON_BUILD=cpython-3.14.3+freethreaded
ARG VENV=/app/haruki_drawing_api/.venv
ARG SITE_PACKAGES=/app/haruki_drawing_api/.venv/lib/python3.14t/site-packages

FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

# ── Runtime base: system packages first, so they sit below everything that changes more often ──
FROM debian:trixie-slim AS base

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8

# Runtime system packages (verified with ldd over every ELF file in the image):
# - libstdc++6 / libgcc-s1 / zlib1g / libexpat1: external ELF dependencies of the Skia renderer
#   wheel (it bundles its own FreeType, fontconfig, libpng, brotli and bz2) and of numpy/granian.
# - libfreetype6: custom-profile rendering loads it through ctypes.util.find_library("freetype").
# - fontconfig + ttf-wqy-zenhei: the system CJK face Skia's FontMgr falls back to when a configured
#   font file is missing (startup reports such a fallback). Emoji come from the data volume's font dir.
# - ca-certificates / netbase / tzdata: TLS roots, /etc/services and /etc/protocols, the timezone.
# pjsekai-scores-rs 0.6+ is pure Rust and needs no system library. Nothing links libGL, glib or X11.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    netbase \
    tzdata \
    libstdc++6 \
    libgcc-s1 \
    libexpat1 \
    zlib1g \
    libfreetype6 \
    fontconfig \
    ttf-wqy-zenhei \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && fc-cache -f \
    && rm -rf \
    /var/lib/apt/lists/* \
    /var/cache/debconf/*-old \
    /usr/share/doc/* \
    /usr/share/info/* \
    /usr/share/lintian/* \
    /usr/share/man/*

# ── Free-threaded CPython, keyed only by PYTHON_BUILD and the uv version ──
FROM debian:trixie-slim AS python
ARG PYTHON_BUILD
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_PYTHON_DOWNLOADS=manual \
    UV_NO_CACHE=1
# Trim what a headless service never loads: Tcl/Tk (tkinter, IDLE, turtle demo), the IDLE editor and
# the C headers. The interpreter's own stdlib __pycache__ is kept (it speeds up startup).
RUN set -eux; \
    uv python install "${PYTHON_BUILD}"; \
    root="$(uv python find "${PYTHON_BUILD}")"; root="$(dirname "$(dirname "$(readlink -f "$root")")")"; \
    cd "$root"; \
    rm -rf include bin/idle3* \
      lib/libtcl* lib/libtk* lib/tcl* lib/tk* lib/itcl* lib/thread* \
      lib/python3.14t/tkinter lib/python3.14t/idlelib lib/python3.14t/turtledemo lib/python3.14t/turtle.py \
      lib/python3.14t/lib-dynload/_tkinter.*; \
    "$root/bin/python3.14t" -c "import sys, ssl, sqlite3, ctypes; assert not sys._is_gil_enabled()"

# ── Third-party wheels from the lock, without the project (a version bump does not change them) ──
FROM python AS lock
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements.txt -o /requirements.txt

FROM python AS deps
ARG PYTHON_BUILD
ARG VENV
COPY --from=lock /requirements.txt /tmp/requirements.txt
# Every runtime dependency ships a cp314t manylinux wheel; --no-build makes a missing one a build failure.
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_NO_CACHE=0 UV_LINK_MODE=copy sh -eux -c '\
    uv venv "$0" --python "$1"; \
    uv pip install --python "$0/bin/python" --require-hashes --no-deps --no-build --compile-bytecode \
      -r /tmp/requirements.txt' "${VENV}" "${PYTHON_BUILD}"

# ── The Skia renderer wheel on its own; the service refuses to start without it ──
FROM python AS native
ARG PYTHON_BUILD
COPY docker/skia-wheels/ /tmp/skia-wheels/
RUN set -eux; \
    test "$(find /tmp/skia-wheels -maxdepth 1 -name '*.whl' | wc -l)" -eq 1; \
    uv pip install --python "$(uv python find "${PYTHON_BUILD}")" --target /native --no-deps --no-build \
      --compile-bytecode /tmp/skia-wheels/*.whl

# ── Runtime ──
FROM base AS runtime
ARG VENV
ARG SITE_PACKAGES

WORKDIR /app/haruki_drawing_api
COPY --from=python /opt/uv/python /opt/uv/python
COPY --from=deps ${VENV} ${VENV}
COPY --from=native /native/ ${SITE_PACKAGES}/

ENV PYTHON_GIL=0 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    PATH="${VENV}/bin:$PATH"

# 复制项目代码（docker/ 只装着构建用的 wheel，不进运行镜像）
COPY --exclude=docker . .

# Validate the actual runtime dependency boundary and render with the installed extensions.
# The codec smoke reads the capability requirement from Python and exercises native image APIs.
# PYTHONDONTWRITEBYTECODE keeps these checks from leaving __pycache__ in the image.
RUN PYTHONDONTWRITEBYTECODE=1 /app/haruki_drawing_api/.venv/bin/python -X gil=0 - <<'PYTHON'
import importlib.util
import sys

assert not sys._is_gil_enabled(), "the service requires CPython free-threading"
for name in ("PIL", "matplotlib", "pilmoji"):
    assert importlib.util.find_spec(name) is None, f"legacy renderer leaked into production: {name}"
# pjsekai-scores-rs 0.6+ renders charts with its pure-Rust backend and links no system library.
import pjsekai_scores_rs
from pjsekai_scores_rs import Drawing
print(f"chart self-check passed ({pjsekai_scores_rs.RASTER_BACKEND}, {Drawing.jpg})")
from fontTools.ttLib import TTFont  # TMP vector contours require this independently of Matplotlib.
from src.sekai.skia_renderer.canvas import load_native_renderer
native = load_native_renderer()
print(f"native renderer self-check passed (IR_CAPABILITY={native.IR_CAPABILITY})")
# Custom-profile text loads the system FreeType through ctypes.
import ctypes.util
assert ctypes.util.find_library("freetype"), "libfreetype not found"
# Object storage (Garage) and the render index; both must load without re-enabling the GIL.
import opendal
import asyncpg
import granian
import numpy
assert not sys._is_gil_enabled(), "opendal/asyncpg re-enabled the GIL"
print(f"storage self-check passed (opendal={opendal.__version__}, asyncpg={asyncpg.__version__})")
PYTHON
RUN PYTHONDONTWRITEBYTECODE=1 /app/haruki_drawing_api/.venv/bin/python -X gil=0 scripts/skia_codec_smoke.py

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
