# tests/test_speed.py
import io
import wave
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# _build_atempo (pure function — no mocking needed)
# ---------------------------------------------------------------------------

def test_build_atempo_normal_speed():
    from pipeline.tts.base import _build_atempo
    assert _build_atempo(1.0) == "atempo=1.000000"


def test_build_atempo_fast():
    from pipeline.tts.base import _build_atempo
    assert _build_atempo(1.5) == "atempo=1.500000"


def test_build_atempo_very_fast_chains_filters():
    from pipeline.tts.base import _build_atempo
    result = _build_atempo(4.0)
    # 4.0 requires two filters: 2.0 * 2.0
    assert result.count("atempo=") == 2
    product = 1.0
    for part in result.split(","):
        product *= float(part.split("=")[1])
    assert abs(product - 4.0) < 0.001


def test_build_atempo_slow():
    from pipeline.tts.base import _build_atempo
    assert _build_atempo(0.75) == "atempo=0.750000"


def test_build_atempo_very_slow_chains_filters():
    from pipeline.tts.base import _build_atempo
    result = _build_atempo(0.25)
    product = 1.0
    for part in result.split(","):
        product *= float(part.split("=")[1])
    assert abs(product - 0.25) < 0.001


# ---------------------------------------------------------------------------
# _apply_speed_to_wav (mocks subprocess)
# ---------------------------------------------------------------------------

def test_apply_speed_to_wav_calls_ffmpeg(tmp_path):
    from pipeline.tts.base import _apply_speed_to_wav
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"\x00")
    with patch("pipeline.tts.base.subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0)
        # Mock Path.replace so the tmp file rename doesn't fail
        with patch.object(Path, "replace"):
            _apply_speed_to_wav(wav, 1.5)
    assert mock_sub.run.called
    cmd = mock_sub.run.call_args[0][0]
    assert "ffmpeg" in cmd
    assert any("atempo=1.500000" in arg for arg in cmd)


# ---------------------------------------------------------------------------
# OpenAI TTS speed param
# ---------------------------------------------------------------------------

def _make_wav_bytes(n_frames: int = 100, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * n_frames * 2)
    return buf.getvalue()


def test_openai_tts_passes_speed_to_api(tmp_path):
    from pipeline.tts.openai_tts import generate_audio
    segments = [{"text": "Hello.", "estimated_duration_sec": 0.5}]
    mock_response = MagicMock()
    mock_response.content = _make_wav_bytes()
    mock_openai = MagicMock()
    mock_openai.audio.speech.create.return_value = mock_response

    with patch("pipeline.tts.openai_tts.openai", mock_openai):
        asyncio.run(generate_audio(segments, tmp_path / "out.wav", speed=1.5))

    call_kwargs = mock_openai.audio.speech.create.call_args.kwargs
    assert call_kwargs["speed"] == 1.5


def test_openai_tts_default_speed_is_1(tmp_path):
    from pipeline.tts.openai_tts import generate_audio
    segments = [{"text": "Hello.", "estimated_duration_sec": 0.5}]
    mock_response = MagicMock()
    mock_response.content = _make_wav_bytes()
    mock_openai = MagicMock()
    mock_openai.audio.speech.create.return_value = mock_response

    with patch("pipeline.tts.openai_tts.openai", mock_openai):
        asyncio.run(generate_audio(segments, tmp_path / "out.wav"))

    call_kwargs = mock_openai.audio.speech.create.call_args.kwargs
    assert call_kwargs.get("speed", 1.0) == 1.0


# ---------------------------------------------------------------------------
# Speed field in PipelineState
# ---------------------------------------------------------------------------

def test_pipeline_state_has_speed_field():
    from pipeline.state import PipelineState
    # TypedDict keys are available via __annotations__
    assert "speed" in PipelineState.__annotations__
