# Render image for RENDER_BACKEND=docker (machines without a local TeX install).
# Anyone with a local TeX Live install can use RENDER_BACKEND=local instead, which runs
# the same docker/*.py runtime modules natively.
FROM manimcommunity/manim:v0.21.0
USER root
# House typography (docker/chalkboard_style.py): CMU Serif text, DejaVu Sans Mono code.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-cmu fonts-inter fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f
# LaTeX packages used by the shared preamble in chalkboard_style.PREAMBLE.
RUN tlmgr install amsmath amsfonts mathtools tools jknapltx rsfs doublestroke cancel \
        siunitx lm microtype babel-english xcolor standalone preview \
    && mktexlsr
USER manimuser
WORKDIR /render
# /render holds the scene runtime: chalkboard_base (layout checks), chalkboard_style
# (TeX preamble + fonts), chalkboard_tokens / _components / _moves / _templates
# (design system). Scenes import them by module name.
ENV PYTHONPATH=/render
COPY --chmod=755 docker/render.sh /render/render.sh
COPY docker/chalkboard_*.py /render/
COPY docker/chalkboard_templates /render/chalkboard_templates
ENTRYPOINT ["/render/render.sh"]
