import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import asyncio


def test_elevenlabs_generate_audio_writes_file(tmp_path):
    segments = [{"text": "Hello.", "estimated_duration_sec": 0.5}]
    output_path = tmp_path / "voiceover.wav"

    fake_audio_bytes = b"\x00" * 200

    with patch("pipeline.tts.elevenlabs_tts.ElevenLabs") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        mock_client.text_to_speech.convert.return_value = iter([fake_audio_bytes])

        from pipeline.tts.elevenlabs_tts import generate_audio
        wav_path, durations = asyncio.run(generate_audio(segments, output_path))

    assert wav_path == output_path
    assert output_path.exists()
    assert len(durations) == 1
