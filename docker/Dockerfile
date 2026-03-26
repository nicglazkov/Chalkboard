FROM manimcommunity/manim:v0.20.1
# Extend with additional LaTeX packages if needed:
# RUN tlmgr install <pkg>
WORKDIR /render
COPY docker/render.sh /render/render.sh
RUN chmod +x /render/render.sh
ENTRYPOINT ["/render/render.sh"]
