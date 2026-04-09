FROM manimcommunity/manim:v0.20.1
# Extend with additional LaTeX packages if needed:
# RUN tlmgr install <pkg>
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
USER manimuser
WORKDIR /render
COPY --chmod=755 docker/render.sh /render/render.sh
# Layout validation mixin; sys.path.insert(0, "/render") in --check mode exposes it
COPY docker/chalkboard_base.py /render/chalkboard_base.py
ENTRYPOINT ["/render/render.sh"]
