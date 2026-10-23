# The whole app in one image: web UI, pipeline and Manim, rendering inside the
# container (RENDER_BACKEND=local). Start it with `docker compose up --build` (compose.yaml).
# docker/Dockerfile is the separate render-only image used by RENDER_BACKEND=docker.
FROM manimcommunity/manim:v0.21.0
USER root
# perl + curl: tlmgr needs both and the base image ships neither.
# build tools + cairo/pango headers: pycairo builds from source for the app's Python.
# ffmpeg for the voiceover merge, espeak-ng for Kokoro, house fonts (docker/chalkboard_style.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends perl curl ca-certificates \
        build-essential pkg-config libcairo2-dev libpango1.0-dev \
        ffmpeg espeak-ng fontconfig fonts-cmu fonts-inter fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f
# LaTeX packages used by the shared preamble in chalkboard_style.PREAMBLE.
# tlmgr exits 0 even when downloads fail, so check the files are really there.
RUN tlmgr update --self \
    && tlmgr install amsmath amsfonts mathtools tools jknapltx rsfs doublestroke cancel \
        siunitx lm microtype babel-english xcolor standalone preview \
    && mktexlsr \
    && kpsewhich mathtools.sty siunitx.sty cancel.sty dsfont.sty mathrsfs.sty microtype.sty standalone.cls preview.sty
# The base image's Python (3.14, in /opt/venv) is too new for Kokoro (needs < 3.13),
# so the app gets its own 3.12 venv.
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    VIRTUAL_ENV=/opt/app-venv \
    PATH=/opt/app-venv/bin:$PATH
RUN uv venv --python 3.12 /opt/app-venv
WORKDIR /app
# CPU-only PyTorch for Kokoro keeps the image a few GB smaller.
COPY requirements.txt requirements-render.txt ./
RUN uv pip install --no-cache --extra-index-url https://download.pytorch.org/whl/cpu \
        --index-strategy unsafe-best-match -r requirements-render.txt
COPY . .
ARG GIT_COMMIT=unknown
ENV CHALKBOARD_GIT_COMMIT=$GIT_COMMIT \
    RENDER_BACKEND=local \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000 \
    OUTPUT_DIR=/app/output \
    CHECKPOINT_DB=/data/pipeline_state.db \
    LIBRARY_DB=/data/library.db \
    HF_HOME=/data/huggingface \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data /app/output
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/version', timeout=4)"
CMD ["python", "run_server.py"]
