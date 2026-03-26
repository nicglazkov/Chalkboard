import pytest
import numpy as np
from pathlib import Path
from unittest.mock import patch
import asyncio


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
        wav_path, durations = asyncio.run(generate_audio(segments, output_path))

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
        asyncio.run(generate_audio(segments, output_path))

    audio, sr = sf.read(str(output_path))
    assert sr == 24000
    # 2 segments * 1 chunk * 12000 samples = 24000 total
    assert len(audio) == 24000
