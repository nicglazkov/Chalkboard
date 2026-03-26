# Chalkboard

Multi-agent pipeline: topic → validated Manim animation + voiceover.

## Quick start

### 1. API keys

Copy the example env file and fill in your keys:

```bash
cp .env.example .env
```

Then open `.env` and set at minimum:
```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...        # if using TTS_BACKEND=openai
```

The `.env` file is gitignored — your keys stay local and are never committed.

### 2. Choose a TTS backend

| Backend | Quality | Requires |
|---------|---------|---------|
| `kokoro` (default) | Best | PyTorch ≥ 2.4, `espeak-ng` — **not available on Intel Macs** |
| `openai` | Great | `pip install openai`, `OPENAI_API_KEY` |
| `elevenlabs` | Great | `pip install elevenlabs`, `ELEVENLABS_API_KEY` |

> **Intel Mac users:** PyTorch ≥ 2.4 has no x86_64 macOS wheels, so Kokoro won't install.
> Use `TTS_BACKEND=openai` or `TTS_BACKEND=elevenlabs` instead.

Set your backend before running:
```bash
export TTS_BACKEND=openai   # or elevenlabs
```

### 3. Prerequisites
- Python 3.10+
- `espeak-ng` (Kokoro only): `brew install espeak-ng` / `apt install espeak-ng`
- Docker (for the final render step)

### 4. Install
```bash
pip install -r requirements.txt
pip install openai   # if using TTS_BACKEND=openai
```

### 5. Run
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
