import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import asyncio


def test_openai_generate_audio_writes_wav(tmp_path):
    segments = [
        {"text": "Hello.", "estimated_duration_sec": 0.5},
        {"text": "World.", "estimated_duration_sec": 0.5},
    ]
    output_path = tmp_path / "voiceover.wav"

    fake_audio_bytes = b"\xff\xfb" + b"\x00" * 100

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
