FROM manimcommunity/manim:v0.20.1
# Extend with additional LaTeX packages if needed:
# RUN tlmgr install <pkg>
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
USER manimuser
WORKDIR /render
COPY --chmod=755 docker/render.sh /render/render.sh
ENTRYPOINT ["/render/render.sh"]
