#!/bin/bash
set -e

# Usage: docker run --rm -v "$(pwd)/output:/output" chalkboard-render <run_id>
# Reads manifest.json to get scene_class_name and quality.

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

echo "Rendering ${SCENE_CLASS} at quality=${QUALITY}..."
manim ${QUALITY_FLAG} --media_dir "${RUN_DIR}/media" \
  "${RUN_DIR}/scene.py" "${SCENE_CLASS}"

VIDEO="${RUN_DIR}/media/videos/scene/${SUBDIR}/${SCENE_CLASS}.mp4"
if [ ! -f "$VIDEO" ]; then
  echo "ERROR: Expected output not found at ${VIDEO}"
  exit 1
fi

echo "Merging voiceover..."
ffmpeg -y \
  -i "${VIDEO}" \
  -i "${RUN_DIR}/voiceover.wav" \
  -c:v copy -c:a aac \
  "${RUN_DIR}/final.mp4"

echo "Done: ${RUN_DIR}/final.mp4"
