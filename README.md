# Chalkboard

Multi-agent pipeline: topic → validated Manim animation + voiceover.

## Quick start

### Prerequisites
- Python 3.11+
- `espeak-ng` (for Kokoro TTS): `brew install espeak-ng` / `apt install espeak-ng`
- Docker (for rendering)
- `ANTHROPIC_API_KEY` env var

### Install
```bash
pip install -r requirements.txt
```

### Run
```bash
python main.py --topic "explain how B-trees work" --effort medium
```

### Render (Docker)
```bash
docker build -f docker/Dockerfile -t chalkboard-render .
docker run --rm -v "$(pwd)/output:/output" chalkboard-render <run_id>
```

Output: `output/<run_id>/final.mp4`

## Config (env vars)
| Var | Default | Options |
|-----|---------|---------|
| `TTS_BACKEND` | `kokoro` | `kokoro`, `openai`, `elevenlabs` |
| `MANIM_QUALITY` | `medium` | `low`, `medium`, `high` |
| `DEFAULT_EFFORT` | `medium` | `low`, `medium`, `high` |
| `OUTPUT_DIR` | `./output` | any path |
| `CHECKPOINT_DB` | `pipeline_state.db` | any path |

## Resume a crashed run
```bash
python main.py --topic "..." --run-id <previous-run-id>
```
