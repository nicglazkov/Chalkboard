#!/bin/bash
set -e

# Usage: docker run --rm -v "$(pwd)/output:/output" chalkboard-render <run_id>
# Reads manifest.json to get scene_class_name and quality.
# Set PREVIEW_MODE=1 to render at low quality into media_preview/ dir.

RUN_ID="${1:?Usage: render.sh <run_id>}"
RUN_DIR="/output/${RUN_ID}"
MANIFEST="${RUN_DIR}/manifest.json"

if [ ! -f "$MANIFEST" ]; then
  echo "ERROR: manifest.json not found at ${MANIFEST}"
  exit 1
fi

SCENE_CLASS=$(python3 -c "import json,sys; d=json.load(open('${MANIFEST}')); print(d['scene_class_name'])")
QUALITY=$(python3 -c "import json,sys; d=json.load(open('${MANIFEST}')); print(d.get('quality','medium'))")

case "$QUALITY" in
  low)    QUALITY_FLAG="-ql"; SUBDIR="480p15" ;;
  medium) QUALITY_FLAG="-qm"; SUBDIR="720p30" ;;
  high)   QUALITY_FLAG="-qh"; SUBDIR="1080p60" ;;
  *)      QUALITY_FLAG="-qm"; SUBDIR="720p30" ;;
esac

# Preview mode: override to low quality, separate media dir to avoid clobbering full render
if [ "${PREVIEW_MODE:-0}" = "1" ]; then
  QUALITY_FLAG="-ql"
  SUBDIR="480p15"
  MEDIA_DIR="${RUN_DIR}/media_preview"
else
  MEDIA_DIR="${RUN_DIR}/media"
fi

echo "Rendering ${SCENE_CLASS} at quality=${QUALITY}..."
manim ${QUALITY_FLAG} --media_dir "${MEDIA_DIR}" \
  "${RUN_DIR}/scene.py" "${SCENE_CLASS}"

VIDEO="${MEDIA_DIR}/videos/scene/${SUBDIR}/${SCENE_CLASS}.mp4"
if [ ! -f "$VIDEO" ]; then
  echo "ERROR: Expected output not found at ${VIDEO}"
  exit 1
fi

# Print video path for host-side merge step
echo "RENDER_COMPLETE:${VIDEO}"
