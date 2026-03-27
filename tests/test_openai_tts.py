import io
import wave
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_wav_bytes(n_frames: int = 100, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * n_frames * 2)
    return buf.getvalue()


def test_openai_generate_audio_writes_wav(tmp_path):
    segments = [
        {"text": "Hello.", "estimated_duration_sec": 0.5},
        {"text": "World.", "estimated_duration_sec": 0.5},
    ]
    output_path = tmp_path / "voiceover.wav"

    fake_audio_bytes = _make_wav_bytes()

    mock_response = MagicMock()
    mock_response.content = fake_audio_bytes

    mock_openai = MagicMock()
    mock_openai.audio.speech.create.return_value = mock_response

    with patch("pipeline.tts.openai_tts.openai", mock_openai):
        from pipeline.tts.openai_tts import generate_audio
        wav_path, durations = asyncio.run(generate_audio(segments, output_path))

    assert wav_path == output_path
    assert output_path.exists()
    assert len(durations) == 2
