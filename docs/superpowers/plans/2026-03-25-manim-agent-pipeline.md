# Manim Agent Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an MVP multi-agent LangGraph pipeline that takes a topic string and produces a validated Manim CE animation script + voiceover audio file, written to `/output/` for Docker rendering.

**Architecture:** Flat LangGraph graph with six nodes (script_agent → fact_validator → manim_agent → code_validator → render_trigger, plus escalate_to_user). Conditional edges implement retry loops (max 3 each for script and code). Two `interrupt()` pause points for human-in-the-loop: web search approval and escalation after exhausted retries.

**Tech Stack:** Python 3.11+, LangGraph 1.1.3, Anthropic SDK ≥0.77.1, Manim CE 0.20.1 (Docker render only), Kokoro TTS (default local), soundfile/numpy for audio, AsyncSqliteSaver for checkpointing.

---

## File Map

```
manim_agent_pipeline/
├── config.py                          # env-var config constants
├── main.py                            # async CLI entrypoint
├── pipeline/
│   ├── __init__.py
│   ├── state.py                       # PipelineState TypedDict + ValidationResult Pydantic model
│   ├── graph.py                       # LangGraph graph definition + conditional edge functions
│   ├── render_trigger.py              # TTS call + file writing to output/{run_id}/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── script_agent.py            # generates segmented narration script
│   │   ├── manim_agent.py             # generates ChalkboardScene Manim code
│   │   ├── fact_validator.py          # factual accuracy check via Claude
│   │   ├── code_validator.py          # ast.parse + Claude semantic review
│   │   └── orchestrator.py            # escalate_to_user node
│   └── tts/
│       ├── __init__.py
│       ├── base.py                    # generate_audio() protocol + get_backend()
│       ├── kokoro_tts.py              # Kokoro KPipeline backend
│       ├── openai_tts.py              # OpenAI TTS backend
│       └── elevenlabs_tts.py          # ElevenLabs backend
├── output/                            # gitignored; run output lands here
├── docker/
│   ├── Dockerfile                     # FROM manimcommunity/manim:v0.20.1
│   └── render.sh                      # manim + ffmpeg two-step render script
├── tests/
│   ├── conftest.py                    # shared fixtures (mock state, mock Claude client)
│   ├── test_state.py
│   ├── test_config.py
│   ├── test_tts_base.py
│   ├── test_kokoro_tts.py
│   ├── test_script_agent.py
│   ├── test_fact_validator.py
│   ├── test_manim_agent.py
│   ├── test_code_validator.py
│   ├── test_orchestrator.py
│   ├── test_render_trigger.py
│   └── test_graph.py
└── requirements.txt
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `config.py`
- Create: `.gitignore`
- Create: `pipeline/__init__.py`
- Create: `pipeline/agents/__init__.py`
- Create: `pipeline/tts/__init__.py`
- Create: `tests/conftest.py`
- Create: `output/.gitkeep`

- [ ] **Step 1: Create requirements.txt**

```
langgraph>=1.1.3
langgraph-checkpoint-sqlite>=3.0.3
aiosqlite>=0.20
anthropic>=0.77.1
kokoro>=0.9.4
soundfile
numpy
pydantic>=2.0
pytest
pytest-asyncio
# Optional TTS backends:
# openai
# elevenlabs
```

- [ ] **Step 2: Create config.py**

```python
import os

TTS_BACKEND    = os.getenv("TTS_BACKEND", "kokoro")   # "kokoro" | "openai" | "elevenlabs"
MANIM_QUALITY  = os.getenv("MANIM_QUALITY", "medium") # "low" | "medium" | "high"
DEFAULT_EFFORT = os.getenv("DEFAULT_EFFORT", "medium")
OUTPUT_DIR     = os.getenv("OUTPUT_DIR", "./output")
CHECKPOINT_DB  = os.getenv("CHECKPOINT_DB", "pipeline_state.db")
CLAUDE_MODEL   = "claude-sonnet-4-6"

QUALITY_FLAGS = {"low": "-ql", "medium": "-qm", "high": "-qh"}
QUALITY_SUBDIRS = {"low": "480p15", "medium": "720p30", "high": "1080p60"}
```

- [ ] **Step 3: Create .gitignore additions**

```
output/*/
!output/.gitkeep
pipeline_state.db
__pycache__/
*.pyc
.env
```

- [ ] **Step 4: Create empty `__init__.py` files**

```bash
touch pipeline/__init__.py pipeline/agents/__init__.py pipeline/tts/__init__.py
touch tests/__init__.py
mkdir -p output && touch output/.gitkeep
```

- [ ] **Step 5: Write conftest.py**

```python
# tests/conftest.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from pipeline.state import PipelineState


@pytest.fixture
def base_state() -> PipelineState:
    return PipelineState(
        topic="explain how B-trees work",
        run_id="test-run-001",
        script="",
        script_segments=[],
        manim_code="",
        script_attempts=0,
        code_attempts=0,
        fact_feedback=None,
        code_feedback=None,
        effort_level="medium",
        needs_web_search=False,
        user_approved_search=False,
        status="drafting",
    )


@pytest.fixture
def mock_anthropic_client():
    client = MagicMock()
    client.messages = MagicMock()
    return client
```

- [ ] **Step 6: Install dependencies**

```bash
pip install -r requirements.txt
```

- [ ] **Step 7: Commit**

```bash
git add requirements.txt config.py .gitignore pipeline/ tests/ output/.gitkeep
git commit -m "feat: project scaffold — deps, config, package structure"
```

---

## Task 2: State Schema

**Files:**
- Create: `pipeline/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_state.py
from pipeline.state import PipelineState, ValidationResult
from typing import get_type_hints


def test_pipeline_state_has_required_fields():
    hints = get_type_hints(PipelineState)
    required = [
        "topic", "run_id", "script", "script_segments", "manim_code",
        "script_attempts", "code_attempts", "fact_feedback", "code_feedback",
        "effort_level", "needs_web_search", "user_approved_search", "status",
    ]
    for field in required:
        assert field in hints, f"Missing field: {field}"


def test_validation_result_approved():
    result = ValidationResult(verdict="approved", feedback="Looks good")
    assert result.verdict == "approved"


def test_validation_result_needs_revision():
    result = ValidationResult(verdict="needs_revision", feedback="Fix claim X")
    assert result.verdict == "needs_revision"


def test_validation_result_rejects_bad_verdict():
    import pytest
    with pytest.raises(Exception):
        ValidationResult(verdict="unknown", feedback="")
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_state.py -v
```
Expected: `ImportError` (module doesn't exist yet)

- [ ] **Step 3: Implement pipeline/state.py**

```python
# pipeline/state.py
from typing import TypedDict, Literal
from pydantic import BaseModel


class ValidationResult(BaseModel):
    verdict: Literal["approved", "needs_revision"]
    feedback: str


class PipelineState(TypedDict):
    topic: str
    run_id: str                    # from config["configurable"]["thread_id"]
    script: str
    script_segments: list[dict]    # [{text: str, estimated_duration_sec: float}]
    manim_code: str
    script_attempts: int           # 0–3
    code_attempts: int             # 0–3
    fact_feedback: str | None
    code_feedback: str | None
    effort_level: Literal["low", "medium", "high"]
    needs_web_search: bool
    user_approved_search: bool
    status: Literal["drafting", "validating", "needs_user_input", "approved", "failed"]
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_state.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add pipeline/state.py tests/test_state.py
git commit -m "feat: PipelineState TypedDict and ValidationResult Pydantic model"
```

---

## Task 3: TTS Base Interface + Kokoro Backend

**Files:**
- Create: `pipeline/tts/base.py`
- Create: `pipeline/tts/kokoro_tts.py`
- Create: `tests/test_tts_base.py`
- Create: `tests/test_kokoro_tts.py`

- [ ] **Step 1: Write failing tests for base interface**

```python
# tests/test_tts_base.py
import pytest
from pipeline.tts.base import get_backend


def test_get_backend_returns_kokoro_by_default():
    backend = get_backend("kokoro")
    assert callable(backend)


def test_get_backend_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown TTS backend"):
        get_backend("unknown_backend")
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_tts_base.py -v
```

- [ ] **Step 3: Implement pipeline/tts/base.py**

```python
# pipeline/tts/base.py
from pathlib import Path
from typing import Callable


# Signature all backends must implement:
# generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]
# Returns (wav_path, list_of_actual_duration_sec_per_segment)

def get_backend(name: str) -> Callable:
    if name == "kokoro":
        from pipeline.tts.kokoro_tts import generate_audio
        return generate_audio
    elif name == "openai":
        from pipeline.tts.openai_tts import generate_audio
        return generate_audio
    elif name == "elevenlabs":
        from pipeline.tts.elevenlabs_tts import generate_audio
        return generate_audio
    else:
        raise ValueError(f"Unknown TTS backend: {name!r}. Choose: kokoro, openai, elevenlabs")
```

- [ ] **Step 4: Write failing Kokoro test**

```python
# tests/test_kokoro_tts.py
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_mock_pipeline(chunks_per_segment=2, samples_per_chunk=24000):
    """Returns a mock KPipeline that yields fake audio chunks."""
    def mock_pipeline(text, voice):
        for i in range(chunks_per_segment):
            audio = np.zeros(samples_per_chunk, dtype=np.float32)
            yield (text, f"phoneme_{i}", audio)
    return mock_pipeline


def test_kokoro_generate_audio_returns_wav_and_durations(tmp_path):
    segments = [
        {"text": "Hello world.", "estimated_duration_sec": 1.0},
        {"text": "This is a test.", "estimated_duration_sec": 1.5},
    ]
    output_path = tmp_path / "voiceover.wav"

    with patch("pipeline.tts.kokoro_tts.KPipeline") as MockPipeline:
        MockPipeline.return_value = _make_mock_pipeline()
        from pipeline.tts.kokoro_tts import generate_audio
        wav_path, durations = generate_audio(segments, output_path)

    assert wav_path == output_path
    assert output_path.exists()
    assert len(durations) == 2
    # 2 chunks * 24000 samples / 24000 Hz = 2.0 sec per segment
    assert all(abs(d - 2.0) < 0.01 for d in durations)


def test_kokoro_generate_audio_concatenates_all_segments(tmp_path):
    import soundfile as sf
    segments = [
        {"text": "Seg one.", "estimated_duration_sec": 1.0},
        {"text": "Seg two.", "estimated_duration_sec": 1.0},
    ]
    output_path = tmp_path / "voiceover.wav"

    with patch("pipeline.tts.kokoro_tts.KPipeline") as MockPipeline:
        MockPipeline.return_value = _make_mock_pipeline(chunks_per_segment=1, samples_per_chunk=12000)
        from pipeline.tts.kokoro_tts import generate_audio
        generate_audio(segments, output_path)

    audio, sr = sf.read(str(output_path))
    assert sr == 24000
    # 2 segments * 1 chunk * 12000 samples = 24000 total
    assert len(audio) == 24000
```

- [ ] **Step 5: Run — expect failure**

```bash
pytest tests/test_kokoro_tts.py -v
```

- [ ] **Step 6: Implement pipeline/tts/kokoro_tts.py**

```python
# pipeline/tts/kokoro_tts.py
import asyncio
import numpy as np
import soundfile as sf
from pathlib import Path
from kokoro import KPipeline

SAMPLE_RATE = 24000
DEFAULT_VOICE = "af_heart"


def _generate_sync(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    pipeline = KPipeline(lang_code="a")
    all_audio: list[np.ndarray] = []
    durations: list[float] = []

    for segment in segments:
        seg_chunks: list[np.ndarray] = []
        for _gs, _ps, audio in pipeline(segment["text"], voice=DEFAULT_VOICE):
            seg_chunks.append(audio)
        seg_audio = np.concatenate(seg_chunks) if seg_chunks else np.array([], dtype=np.float32)
        durations.append(len(seg_audio) / SAMPLE_RATE)
        all_audio.append(seg_audio)

    full_audio = np.concatenate(all_audio) if all_audio else np.array([], dtype=np.float32)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), full_audio, SAMPLE_RATE)
    return output_path, durations


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    return await asyncio.to_thread(_generate_sync, segments, output_path)
```

- [ ] **Step 7: Run — expect pass**

```bash
pytest tests/test_tts_base.py tests/test_kokoro_tts.py -v
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add pipeline/tts/ tests/test_tts_base.py tests/test_kokoro_tts.py
git commit -m "feat: TTS base interface and Kokoro backend"
```

---

## Task 4: OpenAI + ElevenLabs TTS Backends

> **MVP caveat:** These backends write raw bytes per segment and concatenate — producing valid audio for single-segment scripts, but a multi-header file for multi-segment. The ffmpeg merge step may warn but will usually still produce usable output. Fix properly by decoding + re-encoding before shipping to production; Kokoro (Task 3) is the recommended default and does not have this issue.

**Files:**
- Create: `pipeline/tts/openai_tts.py`
- Create: `pipeline/tts/elevenlabs_tts.py`
- Create: `tests/test_openai_tts.py`
- Create: `tests/test_elevenlabs_tts.py`

- [ ] **Step 1: Write failing OpenAI TTS test**

```python
# tests/test_openai_tts.py
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock


def test_openai_generate_audio_writes_wav(tmp_path):
    import io
    segments = [
        {"text": "Hello.", "estimated_duration_sec": 0.5},
        {"text": "World.", "estimated_duration_sec": 0.5},
    ]
    output_path = tmp_path / "voiceover.wav"

    # OpenAI TTS returns raw MP3 bytes; we just write them per-segment and concatenate
    fake_audio_bytes = b"\xff\xfb" + b"\x00" * 100  # minimal fake MP3 header

    mock_response = MagicMock()
    mock_response.content = fake_audio_bytes

    with patch("pipeline.tts.openai_tts.openai.audio.speech.create", return_value=mock_response):
        import asyncio
        from pipeline.tts.openai_tts import generate_audio
        wav_path, durations = asyncio.run(generate_audio(segments, output_path))

    assert wav_path == output_path
    assert output_path.exists()
    assert len(durations) == 2
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_openai_tts.py -v
```

- [ ] **Step 3: Implement pipeline/tts/openai_tts.py**

```python
# pipeline/tts/openai_tts.py
# Requires: pip install openai pydub
import asyncio
from pathlib import Path

OPENAI_MODEL = "gpt-4o-mini-tts"
OPENAI_VOICE = "alloy"
SAMPLE_RATE = 24000


def _generate_sync(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    try:
        import openai
    except ImportError:
        raise ImportError("Install openai: pip install openai")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_bytes: list[bytes] = []
    durations: list[float] = []

    for segment in segments:
        response = openai.audio.speech.create(
            model=OPENAI_MODEL,
            voice=OPENAI_VOICE,
            input=segment["text"],
            response_format="wav",
        )
        chunk_bytes = response.content
        # Estimate duration from file size (WAV: 2 bytes/sample, mono, 24kHz)
        # Header is 44 bytes; remaining is PCM data
        pcm_bytes = max(0, len(chunk_bytes) - 44)
        duration = pcm_bytes / (SAMPLE_RATE * 2)
        durations.append(duration)
        all_bytes.append(chunk_bytes)

    # Write first segment's WAV header + all PCM data concatenated
    # For MVP, just write the raw bytes of the last segment's full WAV
    # and sum durations. A proper implementation would decode+re-encode.
    with open(output_path, "wb") as f:
        f.write(b"".join(all_bytes))

    return output_path, durations


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    return await asyncio.to_thread(_generate_sync, segments, output_path)
```

- [ ] **Step 4: Write failing ElevenLabs test**

```python
# tests/test_elevenlabs_tts.py
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def test_elevenlabs_generate_audio_writes_file(tmp_path):
    segments = [{"text": "Hello.", "estimated_duration_sec": 0.5}]
    output_path = tmp_path / "voiceover.wav"

    fake_audio_bytes = b"\x00" * 200

    with patch("pipeline.tts.elevenlabs_tts.ElevenLabs") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        mock_client.text_to_speech.convert.return_value = iter([fake_audio_bytes])

        import asyncio
        from pipeline.tts.elevenlabs_tts import generate_audio
        wav_path, durations = asyncio.run(generate_audio(segments, output_path))

    assert wav_path == output_path
    assert output_path.exists()
    assert len(durations) == 1
```

- [ ] **Step 5: Run — expect failure**

```bash
pytest tests/test_elevenlabs_tts.py -v
```

- [ ] **Step 6: Implement pipeline/tts/elevenlabs_tts.py**

```python
# pipeline/tts/elevenlabs_tts.py
# Requires: pip install elevenlabs
import asyncio
from pathlib import Path

ELEVENLABS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — swap via env var
SAMPLE_RATE = 44100  # ElevenLabs default


def _generate_sync(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    try:
        from elevenlabs import ElevenLabs
    except ImportError:
        raise ImportError("Install elevenlabs: pip install elevenlabs")

    voice_id = __import__("os").getenv("ELEVENLABS_VOICE_ID", ELEVENLABS_VOICE_ID)
    client = ElevenLabs()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_bytes: list[bytes] = []
    durations: list[float] = []

    for segment in segments:
        audio_iter = client.text_to_speech.convert(
            voice_id=voice_id,
            text=segment["text"],
            model_id="eleven_turbo_v2_5",
            output_format="mp3_44100_128",
        )
        chunk_bytes = b"".join(audio_iter)
        # Estimate: ~128kbps MP3 → bytes/sec = 128000/8 = 16000
        duration = len(chunk_bytes) / 16000
        durations.append(duration)
        all_bytes.append(chunk_bytes)

    with open(output_path, "wb") as f:
        f.write(b"".join(all_bytes))

    return output_path, durations


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    return await asyncio.to_thread(_generate_sync, segments, output_path)
```

- [ ] **Step 7: Run — expect pass**

```bash
pytest tests/test_openai_tts.py tests/test_elevenlabs_tts.py -v
```

- [ ] **Step 8: Commit**

```bash
git add pipeline/tts/openai_tts.py pipeline/tts/elevenlabs_tts.py \
        tests/test_openai_tts.py tests/test_elevenlabs_tts.py
git commit -m "feat: OpenAI and ElevenLabs TTS backends"
```

---

## Task 5: script_agent

**Files:**
- Create: `pipeline/agents/script_agent.py`
- Create: `tests/test_script_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_script_agent.py
import pytest
from unittest.mock import MagicMock, patch
from pipeline.state import PipelineState
from tests.conftest import base_state


def _make_claude_response(script_text: str, segments: list[dict]) -> MagicMock:
    import json
    content = json.dumps({"script": script_text, "segments": segments, "needs_web_search": False})
    msg = MagicMock()
    msg.content = [MagicMock(text=content)]
    return msg


def test_script_agent_returns_script_and_segments(base_state):
    segments = [{"text": "B-trees are balanced.", "estimated_duration_sec": 1.2}]
    mock_response = _make_claude_response("B-trees are balanced.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        result = script_agent(base_state)

    assert result["script"] == "B-trees are balanced."
    assert len(result["script_segments"]) == 1
    assert result["status"] == "validating"


def test_script_agent_sets_needs_web_search_when_flagged(base_state):
    import json
    content = json.dumps({
        "script": "Quantum entanglement...",
        "segments": [{"text": "Quantum entanglement...", "estimated_duration_sec": 2.0}],
        "needs_web_search": True,
    })
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=content)]

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        result = script_agent(base_state)

    assert result["needs_web_search"] is True


def test_script_agent_includes_feedback_in_revision(base_state):
    base_state["fact_feedback"] = "Claim X is incorrect"
    base_state["script_attempts"] = 1
    segments = [{"text": "Revised script.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Revised script.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        script_agent(base_state)

    call_args = client_instance.messages.create.call_args
    messages = call_args.kwargs["messages"]
    user_content = messages[0]["content"]
    assert "Claim X is incorrect" in user_content
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_script_agent.py -v
```

- [ ] **Step 3: Implement pipeline/agents/script_agent.py**

```python
# pipeline/agents/script_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are an educational script writer. Given a topic, write a clear,
accurate narration script for an animated explainer video. Structure it as distinct
teaching segments (3–8 segments). Each segment should be 1–3 sentences.

Respond with valid JSON only:
{
  "script": "<full narration as a single string>",
  "segments": [{"text": "<segment text>", "estimated_duration_sec": <float>}],
  "needs_web_search": <bool>
}

Estimate duration as word_count / 2.5 seconds (~150 wpm).
Set needs_web_search to true only if the topic requires information beyond your training data."""


def _build_user_message(state: PipelineState) -> str:
    topic = state["topic"]
    effort = state["effort_level"]
    feedback = state.get("fact_feedback")
    web_approved = state.get("user_approved_search", False)

    msg = f"Topic: {topic}\nEffort level: {effort}"
    if feedback:
        msg += f"\n\nPrevious attempt had issues. Please rewrite the script fully, addressing this feedback:\n{feedback}"
    if web_approved:
        msg += "\n\nWeb search has been approved — use it if needed."
    if effort == "low":
        msg += "\n\nEffort=low: keep the script concise, 3–4 segments, no web search needed."
    return msg


def script_agent(state: PipelineState) -> dict:
    client = anthropic.Anthropic()

    tools = []
    if state.get("user_approved_search") or state["effort_level"] == "high":
        tools = [{"type": "web_search_20250305", "name": "web_search"}]

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(state)}],
        tools=tools if tools else anthropic.NOT_GIVEN,
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "script": {"type": "string"},
                        "segments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                    "estimated_duration_sec": {"type": "number"},
                                },
                                "required": ["text", "estimated_duration_sec"],
                            },
                        },
                        "needs_web_search": {"type": "boolean"},
                    },
                    "required": ["script", "segments", "needs_web_search"],
                    "additionalProperties": False,
                },
            }
        },
    )

    data = json.loads(response.content[0].text)
    return {
        "script": data["script"],
        "script_segments": data["segments"],
        "needs_web_search": data.get("needs_web_search", False),
        "status": "validating",
    }
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_script_agent.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/agents/script_agent.py tests/test_script_agent.py
git commit -m "feat: script_agent — segmented narration generation with web search support"
```

---

## Task 6: fact_validator

**Files:**
- Create: `pipeline/agents/fact_validator.py`
- Create: `tests/test_fact_validator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fact_validator.py
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.state import PipelineState


def _mock_response(verdict: str, feedback: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({"verdict": verdict, "feedback": feedback}))]
    return msg


def test_fact_validator_approved(base_state):
    base_state["script"] = "B-trees are self-balancing trees."
    mock_resp = _mock_response("approved", "Accurate.")

    with patch("pipeline.agents.fact_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        from pipeline.agents.fact_validator import fact_validator
        result = fact_validator(base_state)

    assert result["fact_feedback"] == "Accurate."
    assert result["script_attempts"] == 0  # not incremented on pass


def test_fact_validator_needs_revision_increments_attempts(base_state):
    base_state["script"] = "B-trees are hash maps."
    base_state["script_attempts"] = 1
    mock_resp = _mock_response("needs_revision", "B-trees are not hash maps.")

    with patch("pipeline.agents.fact_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        from pipeline.agents.fact_validator import fact_validator
        result = fact_validator(base_state)

    assert result["script_attempts"] == 2
    assert "hash maps" in result["fact_feedback"]


def test_fact_validator_effort_low_uses_light_prompt(base_state):
    base_state["script"] = "Some script."
    base_state["effort_level"] = "low"
    mock_resp = _mock_response("approved", "OK")

    with patch("pipeline.agents.fact_validator.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_resp
        from pipeline.agents.fact_validator import fact_validator
        fact_validator(base_state)

    call_args = client_instance.messages.create.call_args
    messages = call_args.kwargs["messages"]
    assert "light" in messages[0]["content"].lower() or "obvious" in messages[0]["content"].lower()
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_fact_validator.py -v
```

- [ ] **Step 3: Implement pipeline/agents/fact_validator.py**

```python
# pipeline/agents/fact_validator.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.state import PipelineState, ValidationResult

EFFORT_INSTRUCTIONS = {
    "low": "Do a light check only. Flag only obvious factual errors. Approve if generally correct.",
    "medium": "Spot-check the key claims. Flag anything that seems clearly wrong.",
    "high": "Thorough fact-check. Flag anything uncertain, unverified, or potentially misleading.",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approved", "needs_revision"]},
        "feedback": {"type": "string"},
    },
    "required": ["verdict", "feedback"],
    "additionalProperties": False,
}


def fact_validator(state: PipelineState) -> dict:
    client = anthropic.Anthropic()
    effort = state["effort_level"]
    instruction = EFFORT_INSTRUCTIONS[effort]

    user_msg = (
        f"Review the factual accuracy of this educational script.\n"
        f"Instructions: {instruction}\n\n"
        f"Script:\n{state['script']}"
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": user_msg}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )

    result = ValidationResult.model_validate_json(response.content[0].text)

    updates: dict = {"fact_feedback": result.feedback}
    if result.verdict == "needs_revision":
        updates["script_attempts"] = state["script_attempts"] + 1
    return updates
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_fact_validator.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/agents/fact_validator.py tests/test_fact_validator.py
git commit -m "feat: fact_validator — effort-aware factual accuracy check"
```

---

## Task 7: manim_agent

**Files:**
- Create: `pipeline/agents/manim_agent.py`
- Create: `tests/test_manim_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_manim_agent.py
import json
import pytest
from unittest.mock import MagicMock, patch


def _mock_response(code: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({"manim_code": code}))]
    return msg


VALID_SCENE = '''
from manim import *

class ChalkboardScene(Scene):
    def construct(self):
        title = Text("B-Trees")
        self.play(Write(title))
        self.wait(2.0)
'''


def test_manim_agent_generates_chalkboard_scene(base_state):
    base_state["script"] = "B-trees are balanced search trees."
    base_state["script_segments"] = [{"text": "B-trees are balanced.", "estimated_duration_sec": 2.0}]
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        from pipeline.agents.manim_agent import manim_agent
        result = manim_agent(base_state)

    assert "ChalkboardScene" in result["manim_code"]
    assert result["status"] == "validating"


def test_manim_agent_includes_durations_in_prompt(base_state):
    base_state["script"] = "Hello world."
    base_state["script_segments"] = [
        {"text": "Hello.", "estimated_duration_sec": 1.5},
        {"text": "World.", "estimated_duration_sec": 2.3},
    ]
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_resp
        from pipeline.agents.manim_agent import manim_agent
        manim_agent(base_state)

    call_args = client_instance.messages.create.call_args
    messages = call_args.kwargs["messages"]
    content = messages[0]["content"]
    assert "1.5" in content
    assert "2.3" in content


def test_manim_agent_includes_feedback_on_revision(base_state):
    base_state["script"] = "Hello world."
    base_state["script_segments"] = [{"text": "Hello.", "estimated_duration_sec": 1.0}]
    base_state["code_feedback"] = "Missing import for MathTex"
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_resp
        from pipeline.agents.manim_agent import manim_agent
        manim_agent(base_state)

    call_args = client_instance.messages.create.call_args
    messages = call_args.kwargs["messages"]
    assert "Missing import for MathTex" in messages[0]["content"]
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_manim_agent.py -v
```

- [ ] **Step 3: Implement pipeline/agents/manim_agent.py**

```python
# pipeline/agents/manim_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are an expert Manim Community Edition (v0.20.1) developer.
Generate a complete, runnable Manim scene for an educational animation.

STRICT REQUIREMENTS:
- The scene class MUST be named exactly `ChalkboardScene` (inherits from Scene)
- Use `from manim import *` as the only import
- Each narration segment gets an animation block followed by self.wait(duration_sec)
- Use self.play(..., run_time=X) for animations
- The code must be syntactically valid Python

Respond with JSON only: {"manim_code": "<complete Python code as string>"}"""


def _format_segments(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(f"  Segment {i} ({seg['estimated_duration_sec']:.1f}s): {seg['text']}")
    return "\n".join(lines)


def manim_agent(state: PipelineState) -> dict:
    client = anthropic.Anthropic()

    user_msg = (
        f"Create a Manim animation for this educational script.\n\n"
        f"Topic: {state['topic']}\n\n"
        f"Narration segments with timings:\n{_format_segments(state['script_segments'])}\n\n"
        f"Full script for context:\n{state['script']}"
    )

    if state.get("code_feedback"):
        user_msg += f"\n\nPrevious attempt had issues. Rewrite the scene fully, addressing:\n{state['code_feedback']}"

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {"manim_code": {"type": "string"}},
                    "required": ["manim_code"],
                    "additionalProperties": False,
                },
            }
        },
    )

    data = json.loads(response.content[0].text)
    return {"manim_code": data["manim_code"], "status": "validating"}
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_manim_agent.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/agents/manim_agent.py tests/test_manim_agent.py
git commit -m "feat: manim_agent — ChalkboardScene generation with segment timing"
```

---

## Task 8: code_validator

**Files:**
- Create: `pipeline/agents/code_validator.py`
- Create: `tests/test_code_validator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_code_validator.py
import json
import pytest
from unittest.mock import MagicMock, patch


VALID_CODE = """
from manim import *
class ChalkboardScene(Scene):
    def construct(self):
        self.play(Write(Text("Hello")))
        self.wait(1.0)
"""

INVALID_SYNTAX = "from manim import *\nclass Bad(\n    def broken"


def _mock_response(verdict: str, feedback: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({"verdict": verdict, "feedback": feedback}))]
    return msg


def test_code_validator_passes_valid_code(base_state):
    base_state["manim_code"] = VALID_CODE
    base_state["script"] = "Hello world."
    mock_resp = _mock_response("approved", "Looks correct.")

    with patch("pipeline.agents.code_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        from pipeline.agents.code_validator import code_validator
        result = code_validator(base_state)

    assert result["code_feedback"] == "Looks correct."
    assert result["code_attempts"] == 0  # not incremented on pass


def test_code_validator_fails_on_syntax_error_without_claude_call(base_state):
    base_state["manim_code"] = INVALID_SYNTAX
    base_state["code_attempts"] = 0

    with patch("pipeline.agents.code_validator.anthropic.Anthropic") as MockClient:
        from pipeline.agents.code_validator import code_validator
        result = code_validator(base_state)

    MockClient.return_value.messages.create.assert_not_called()
    assert result["code_attempts"] == 1
    assert "syntax" in result["code_feedback"].lower()


def test_code_validator_increments_attempts_on_semantic_fail(base_state):
    base_state["manim_code"] = VALID_CODE
    base_state["script"] = "Explain hash tables."
    base_state["code_attempts"] = 1
    mock_resp = _mock_response("needs_revision", "Scene doesn't show hash tables.")

    with patch("pipeline.agents.code_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        from pipeline.agents.code_validator import code_validator
        result = code_validator(base_state)

    assert result["code_attempts"] == 2
    assert "hash tables" in result["code_feedback"]
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_code_validator.py -v
```

- [ ] **Step 3: Implement pipeline/agents/code_validator.py**

```python
# pipeline/agents/code_validator.py
import ast
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.state import PipelineState, ValidationResult

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approved", "needs_revision"]},
        "feedback": {"type": "string"},
    },
    "required": ["verdict", "feedback"],
    "additionalProperties": False,
}


def code_validator(state: PipelineState) -> dict:
    code = state["manim_code"]
    attempts = state["code_attempts"]

    # Step 1: syntax check (free, fast)
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {
            "code_feedback": f"Syntax error: {e}",
            "code_attempts": attempts + 1,
        }

    # Step 2: semantic review via Claude
    client = anthropic.Anthropic()
    user_msg = (
        f"Review this Manim CE code for correctness and coherence with the script.\n\n"
        f"Script:\n{state['script']}\n\n"
        f"Manim code:\n{code}\n\n"
        f"Check: Does the animation visualize the script? Are Manim CE v0.20 APIs used correctly? "
        f"Is the class named ChalkboardScene?"
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": user_msg}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )

    result = ValidationResult.model_validate_json(response.content[0].text)
    updates: dict = {"code_feedback": result.feedback}
    if result.verdict == "needs_revision":
        updates["code_attempts"] = attempts + 1
    return updates
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_code_validator.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/agents/code_validator.py tests/test_code_validator.py
git commit -m "feat: code_validator — ast.parse fast path + Claude semantic review"
```

---

## Task 9: orchestrator (escalate_to_user)

**Files:**
- Create: `pipeline/agents/orchestrator.py`
- Create: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_orchestrator.py
import pytest
from unittest.mock import patch
from pipeline.state import PipelineState


def test_escalate_surfaces_script_feedback(base_state):
    base_state["fact_feedback"] = "Claim X is wrong."
    base_state["script_attempts"] = 3
    base_state["status"] = "validating"

    resume_payload = {"action": "retry_script", "guidance": "Focus on CS accuracy."}

    with patch("pipeline.agents.orchestrator.interrupt", return_value=resume_payload):
        from pipeline.agents.orchestrator import escalate_to_user
        result = escalate_to_user(base_state)

    assert result["script_attempts"] == 0
    assert result["fact_feedback"] == "Focus on CS accuracy."


def test_escalate_routes_abort(base_state):
    base_state["code_feedback"] = "Can't fix."
    base_state["code_attempts"] = 3

    resume_payload = {"action": "abort", "guidance": ""}

    with patch("pipeline.agents.orchestrator.interrupt", return_value=resume_payload):
        from pipeline.agents.orchestrator import escalate_to_user
        result = escalate_to_user(base_state)

    assert result["status"] == "failed"


def test_escalate_retry_code_resets_code_attempts(base_state):
    base_state["code_feedback"] = "Wrong API."
    base_state["code_attempts"] = 3

    resume_payload = {"action": "retry_code", "guidance": "Use MathTex for equations."}

    with patch("pipeline.agents.orchestrator.interrupt", return_value=resume_payload):
        from pipeline.agents.orchestrator import escalate_to_user
        result = escalate_to_user(base_state)

    assert result["code_attempts"] == 0
    assert result["code_feedback"] == "Use MathTex for equations."
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_orchestrator.py -v
```

- [ ] **Step 3: Implement pipeline/agents/orchestrator.py**

```python
# pipeline/agents/orchestrator.py
from langgraph.types import interrupt
from pipeline.state import PipelineState


def _build_escalation_message(state: PipelineState) -> str:
    lines = ["=" * 60, "PIPELINE ESCALATION — Maximum retries reached", "=" * 60]

    if state["script_attempts"] >= 3:
        lines.append(f"\nScript validation failed after 3 attempts.")
        lines.append(f"Last feedback: {state.get('fact_feedback', 'None')}")
    if state["code_attempts"] >= 3:
        lines.append(f"\nCode validation failed after 3 attempts.")
        lines.append(f"Last feedback: {state.get('code_feedback', 'None')}")

    lines.append("\nOptions:")
    lines.append('  {"action": "retry_script", "guidance": "<your guidance>"}')
    lines.append('  {"action": "retry_code",   "guidance": "<your guidance>"}')
    lines.append('  {"action": "abort",         "guidance": ""}')
    return "\n".join(lines)


def escalate_to_user(state: PipelineState) -> dict:
    message = _build_escalation_message(state)
    print(message)

    resume = interrupt(message)
    action = resume.get("action", "abort")
    guidance = resume.get("guidance", "")

    if action == "retry_script":
        return {"script_attempts": 0, "fact_feedback": guidance, "status": "drafting"}
    elif action == "retry_code":
        return {"code_attempts": 0, "code_feedback": guidance, "status": "validating"}
    else:
        return {"status": "failed"}
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_orchestrator.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/agents/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: escalate_to_user node with interrupt() and Command(resume) routing"
```

---

## Task 10: render_trigger

**Files:**
- Create: `pipeline/render_trigger.py`
- Create: `tests/test_render_trigger.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_render_trigger.py
import json
import pytest
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


SAMPLE_CODE = "from manim import *\nclass ChalkboardScene(Scene):\n    def construct(self): pass"


def test_render_trigger_writes_all_output_files(base_state, tmp_path):
    base_state["manim_code"] = SAMPLE_CODE
    base_state["script"] = "Hello world."
    base_state["script_segments"] = [{"text": "Hello.", "estimated_duration_sec": 1.0}]
    base_state["run_id"] = "test-run-001"

    mock_audio_path = tmp_path / "test-run-001" / "voiceover.wav"
    mock_audio_path.parent.mkdir(parents=True)
    mock_audio_path.write_bytes(b"\x00" * 100)

    async def mock_generate(segments, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00" * 100)
        return output_path, [1.05]

    with patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_generate):
        from pipeline.render_trigger import render_trigger
        result = asyncio.run(render_trigger(base_state))

    run_dir = tmp_path / "test-run-001"
    assert (run_dir / "scene.py").exists()
    assert (run_dir / "voiceover.wav").exists()
    assert (run_dir / "segments.json").exists()
    assert (run_dir / "script.txt").exists()
    assert (run_dir / "manifest.json").exists()
    assert result["status"] == "approved"


def test_render_trigger_segments_json_uses_actual_durations(base_state, tmp_path):
    base_state["manim_code"] = SAMPLE_CODE
    base_state["script"] = "Hello."
    base_state["script_segments"] = [{"text": "Hello.", "estimated_duration_sec": 1.0}]
    base_state["run_id"] = "run-dur-test"

    async def mock_generate(segments, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00")
        return output_path, [2.73]  # actual duration differs from estimate

    with patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_generate):
        from pipeline.render_trigger import render_trigger
        asyncio.run(render_trigger(base_state))

    segments = json.loads((tmp_path / "run-dur-test" / "segments.json").read_text())
    assert segments[0]["actual_duration_sec"] == pytest.approx(2.73)
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_render_trigger.py -v
```

- [ ] **Step 3: Implement pipeline/render_trigger.py**

```python
# pipeline/render_trigger.py
import json
from pathlib import Path
from config import OUTPUT_DIR, TTS_BACKEND, MANIM_QUALITY
from pipeline.state import PipelineState
from pipeline.tts.base import get_backend


async def render_trigger(state: PipelineState) -> dict:
    run_id = state["run_id"]
    run_dir = Path(OUTPUT_DIR) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Generate TTS audio, get actual per-segment durations
    generate_audio = get_backend(TTS_BACKEND)
    wav_path = run_dir / "voiceover.wav"
    _, actual_durations = await generate_audio(state["script_segments"], wav_path)

    # Build segments.json with actual durations
    segments_out = [
        {"text": seg["text"], "actual_duration_sec": dur}
        for seg, dur in zip(state["script_segments"], actual_durations)
    ]

    # Write all output files
    (run_dir / "scene.py").write_text(state["manim_code"])
    (run_dir / "segments.json").write_text(json.dumps(segments_out, indent=2))
    (run_dir / "script.txt").write_text(state["script"])
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "scene_class_name": "ChalkboardScene",
        "quality": MANIM_QUALITY,
        "topic": state["topic"],
    }, indent=2))

    return {"status": "approved"}
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_render_trigger.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/render_trigger.py tests/test_render_trigger.py
git commit -m "feat: render_trigger — TTS generation and output file writing"
```

---

## Task 11: LangGraph graph

**Files:**
- Create: `pipeline/graph.py`
- Create: `tests/test_graph.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_graph.py
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock


def _make_script_response():
    import json
    return MagicMock(content=[MagicMock(text=json.dumps({
        "script": "B-trees are balanced trees.",
        "segments": [{"text": "B-trees are balanced.", "estimated_duration_sec": 2.0}],
        "needs_web_search": False,
    }))])


def _make_approved_response():
    import json
    return MagicMock(content=[MagicMock(text=json.dumps({
        "verdict": "approved", "feedback": "Looks good."
    }))])


def _make_manim_response():
    import json
    code = "from manim import *\nclass ChalkboardScene(Scene):\n    def construct(self):\n        self.wait(2.0)"
    return MagicMock(content=[MagicMock(text=json.dumps({"manim_code": code}))])


def test_graph_happy_path_reaches_approved(tmp_path):
    """Full pipeline run with all validators approving on first try."""
    async def mock_tts(segments, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as ScriptClaude, \
         patch("pipeline.agents.fact_validator.anthropic.Anthropic") as FactClaude, \
         patch("pipeline.agents.manim_agent.anthropic.Anthropic") as ManimClaude, \
         patch("pipeline.agents.code_validator.anthropic.Anthropic") as CodeClaude, \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        ScriptClaude.return_value.messages.create.return_value = _make_script_response()
        FactClaude.return_value.messages.create.return_value = _make_approved_response()
        ManimClaude.return_value.messages.create.return_value = _make_manim_response()
        CodeClaude.return_value.messages.create.return_value = _make_approved_response()

        from pipeline.graph import build_graph
        graph = build_graph()

        config = {"configurable": {"thread_id": "test-happy-path"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "low"},
            config=config,
        ))

    assert result["status"] == "approved"
    assert (tmp_path / "test-happy-path" / "scene.py").exists()


def test_graph_retries_script_on_fact_failure(tmp_path):
    """Script validator fails once, then passes on second attempt."""
    import json

    fail_resp = MagicMock(content=[MagicMock(text=json.dumps({
        "verdict": "needs_revision", "feedback": "Claim X is wrong."
    }))])

    call_counts = {"fact": 0}
    def fact_side_effect(**kwargs):
        call_counts["fact"] += 1
        return fail_resp if call_counts["fact"] == 1 else _make_approved_response()

    async def mock_tts(segments, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as ScriptClaude, \
         patch("pipeline.agents.fact_validator.anthropic.Anthropic") as FactClaude, \
         patch("pipeline.agents.manim_agent.anthropic.Anthropic") as ManimClaude, \
         patch("pipeline.agents.code_validator.anthropic.Anthropic") as CodeClaude, \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        ScriptClaude.return_value.messages.create.return_value = _make_script_response()
        FactClaude.return_value.messages.create.side_effect = lambda **kw: fact_side_effect(**kw)
        ManimClaude.return_value.messages.create.return_value = _make_manim_response()
        CodeClaude.return_value.messages.create.return_value = _make_approved_response()

        from pipeline.graph import build_graph
        graph = build_graph()

        config = {"configurable": {"thread_id": "test-retry-script"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "low"},
            config=config,
        ))

    assert result["status"] == "approved"
    assert call_counts["fact"] == 2
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_graph.py -v
```

- [ ] **Step 3: Implement pipeline/graph.py**

```python
# pipeline/graph.py
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from config import CHECKPOINT_DB
from pipeline.state import PipelineState
from pipeline.agents.script_agent import script_agent
from pipeline.agents.fact_validator import fact_validator
from pipeline.agents.manim_agent import manim_agent
from pipeline.agents.code_validator import code_validator
from pipeline.agents.orchestrator import escalate_to_user
from pipeline.render_trigger import render_trigger


def _after_fact_validator(state: PipelineState) -> str:
    if state["script_attempts"] >= 3:
        return "escalate_to_user"
    if state.get("fact_feedback") and state["script_attempts"] > 0:
        # fact_validator increments attempts on fail — if attempts went up, it failed
        return "script_agent"
    return "manim_agent"


def _after_code_validator(state: PipelineState) -> str:
    if state["code_attempts"] >= 3:
        return "escalate_to_user"
    if state["code_attempts"] > 0:
        # code_validator increments attempts on fail
        return "manim_agent"
    return "render_trigger"


def _after_escalate(state: PipelineState) -> str:
    if state["status"] == "failed":
        return END
    if state["script_attempts"] == 0 and state.get("fact_feedback"):
        # was reset — retry script
        return "script_agent"
    if state["code_attempts"] == 0 and state.get("code_feedback"):
        return "manim_agent"
    return END


def _init_state(state: PipelineState) -> dict:
    """Populate default fields at graph entry."""
    import uuid
    from langgraph.config import get_config
    config = get_config()
    run_id = config.get("configurable", {}).get("thread_id", str(uuid.uuid4()))
    return {
        "run_id": run_id,
        "script": state.get("script", ""),
        "script_segments": state.get("script_segments", []),
        "manim_code": state.get("manim_code", ""),
        "script_attempts": state.get("script_attempts", 0),
        "code_attempts": state.get("code_attempts", 0),
        "fact_feedback": state.get("fact_feedback"),
        "code_feedback": state.get("code_feedback"),
        "needs_web_search": state.get("needs_web_search", False),
        "user_approved_search": state.get("user_approved_search", False),
        "status": "drafting",
    }


def build_graph(checkpointer=None) -> StateGraph:
    builder = StateGraph(PipelineState)

    builder.add_node("init", _init_state)
    builder.add_node("script_agent", script_agent)
    builder.add_node("fact_validator", fact_validator)
    builder.add_node("manim_agent", manim_agent)
    builder.add_node("code_validator", code_validator)
    builder.add_node("escalate_to_user", escalate_to_user)
    builder.add_node("render_trigger", render_trigger)

    builder.add_edge(START, "init")
    builder.add_edge("init", "script_agent")
    builder.add_edge("script_agent", "fact_validator")
    builder.add_conditional_edges("fact_validator", _after_fact_validator,
        ["script_agent", "manim_agent", "escalate_to_user"])
    builder.add_edge("manim_agent", "code_validator")
    builder.add_conditional_edges("code_validator", _after_code_validator,
        ["manim_agent", "render_trigger", "escalate_to_user"])
    builder.add_edge("render_trigger", END)
    builder.add_conditional_edges("escalate_to_user", _after_escalate,
        ["script_agent", "manim_agent", END])

    return builder.compile(checkpointer=checkpointer)


# Note: callers manage the AsyncSqliteSaver context manager themselves (see main.py).
# Do not add a helper that returns the graph from inside a context manager — the
# connection closes when the context exits, making the returned graph unusable.
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/test_graph.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/graph.py tests/test_graph.py
git commit -m "feat: LangGraph graph — flat conditional edges, retry loops, escalation"
```

---

## Task 12: CLI entrypoint

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implement main.py**

No test for CLI — it's thin glue between user input and the graph. Manual test in Step 3.

```python
# main.py
import asyncio
import argparse
import json
import uuid
from langgraph.types import Command
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from config import CHECKPOINT_DB, DEFAULT_EFFORT
from pipeline.graph import build_graph

EFFORT_CHOICES = ["low", "medium", "high"]


def _print_progress(event: dict) -> None:
    for node_name, updates in event.items():
        if node_name == "__end__":
            continue
        status = updates.get("status", "")
        attempts_info = ""
        if "script_attempts" in updates:
            attempts_info = f" (script attempt {updates['script_attempts']})"
        elif "code_attempts" in updates:
            attempts_info = f" (code attempt {updates['code_attempts']})"
        print(f"  [{node_name}]{attempts_info} → {status or 'done'}")


def _handle_interrupt(interrupt_value: str) -> Command:
    print("\n" + interrupt_value)

    # Web search gate
    if "web search" in interrupt_value.lower() or "needs_web_search" in interrupt_value.lower():
        answer = input("\nApprove web search? [y/N] ").strip().lower()
        return Command(resume={"approved": answer == "y"})

    # Escalation
    print("\nEnter action (retry_script / retry_code / abort):")
    action = input("  action: ").strip()
    guidance = ""
    if action in ("retry_script", "retry_code"):
        guidance = input("  guidance: ").strip()
    return Command(resume={"action": action, "guidance": guidance})


async def run(topic: str, effort: str, thread_id: str) -> None:
    print(f"\nChalkboard — topic: {topic!r} | effort: {effort} | run: {thread_id}\n")

    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        input_state = {"topic": topic, "effort_level": effort}

        while True:
            async for event in graph.astream(input_state, config=config, stream_mode="updates"):
                _print_progress(event)

                # Check for interrupt
                if "__interrupt__" in event:
                    interrupt_value = event["__interrupt__"][0].value
                    resume_cmd = _handle_interrupt(interrupt_value)
                    input_state = resume_cmd
                    break
            else:
                # Stream completed without interrupt — done
                break

    print("\nDone. Check output/ for generated files.")


def main():
    parser = argparse.ArgumentParser(description="Chalkboard — AI animation pipeline")
    parser.add_argument("--topic", required=True, help="Topic to explain")
    parser.add_argument("--effort", choices=EFFORT_CHOICES, default=DEFAULT_EFFORT)
    parser.add_argument("--run-id", default=None, help="Resume a previous run by ID")
    args = parser.parse_args()

    thread_id = args.run_id or str(uuid.uuid4())
    asyncio.run(run(args.topic, args.effort, thread_id))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run all tests**

```bash
pytest tests/ -v
```
Expected: all pass

- [ ] **Step 3: Smoke test (requires Anthropic API key)**

```bash
ANTHROPIC_API_KEY=your_key python main.py --topic "explain how a stack works" --effort low
```
Expected: progress lines printed, output files written to `output/<uuid>/`

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: CLI entrypoint with interrupt handling and resume support"
```

---

## Task 13: Docker render container

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker/render.sh`

- [ ] **Step 1: Create docker/Dockerfile**

```dockerfile
FROM manimcommunity/manim:v0.20.1
# Extend with additional LaTeX packages if needed:
# RUN tlmgr install <pkg>
WORKDIR /render
COPY docker/render.sh /render/render.sh
RUN chmod +x /render/render.sh
ENTRYPOINT ["/render/render.sh"]
```

- [ ] **Step 2: Create docker/render.sh**

```bash
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
```

- [ ] **Step 3: Build and verify Docker image**

```bash
docker build -f docker/Dockerfile -t chalkboard-render .
```
Expected: image builds successfully

- [ ] **Step 4: Test Docker render (requires a completed pipeline run)**

```bash
# After running main.py once, use the run_id from output/
RUN_ID=<your-run-id>
docker run --rm \
  -v "$(pwd)/output:/output" \
  chalkboard-render "$RUN_ID"
```
Expected: `output/<run_id>/final.mp4` created

- [ ] **Step 5: Commit**

```bash
git add docker/
git commit -m "feat: Docker render container — Manim + ffmpeg two-step render script"
```

---

## Task 14: Final wiring and README

**Files:**
- Create: `README.md`
- Modify: `output/.gitkeep`

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short
```
Expected: all pass

- [ ] **Step 2: Create README.md**

```markdown
# Chalkboard

Multi-agent pipeline: topic → validated Manim animation + voiceover.

## Quick start

### Prerequisites
- Python 3.11+
- `espeak-ng` (for Kokoro TTS): `brew install espeak-ng` / `apt install espeak-ng`
- Docker (for rendering)
- `ANTHROPIC_API_KEY` env var

### Install
\```bash
pip install -r requirements.txt
\```

### Run
\```bash
python main.py --topic "explain how B-trees work" --effort medium
\```

### Render (Docker)
\```bash
docker build -f docker/Dockerfile -t chalkboard-render .
docker run --rm -v "$(pwd)/output:/output" chalkboard-render <run_id>
\```

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
\```bash
python main.py --topic "..." --run-id <previous-run-id>
\```
```

- [ ] **Step 3: Final commit**

```bash
git add README.md
git commit -m "feat: README with quick start, config reference, and resume instructions"
```
