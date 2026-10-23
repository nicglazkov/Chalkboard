# The whole app in one image: web UI, pipeline and Manim, rendering inside the
# container (RENDER_BACKEND=local). Start it with `docker compose up` (see compose.yaml).
# docker/Dockerfile is the separate render-only image used by RENDER_BACKEND=docker.
FROM manimcommunity/manim:v0.21.0
USER root
# ffmpeg for the voiceover merge, espeak-ng for Kokoro, house fonts (docker/chalkboard_style.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg espeak-ng fontconfig fonts-cmu fonts-inter fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f
# LaTeX packages used by the shared preamble in chalkboard_style.PREAMBLE.
RUN tlmgr install amsmath amsfonts mathtools tools jknapltx rsfs doublestroke cancel \
        siunitx lm microtype babel-english xcolor standalone preview \
    && mktexlsr
WORKDIR /app
# CPU-only PyTorch for Kokoro keeps the image a few GB smaller.
COPY requirements.txt requirements-render.txt ./
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-render.txt
COPY . .
ENV RENDER_BACKEND=local \
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
