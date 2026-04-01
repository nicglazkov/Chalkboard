# pipeline/tts/kokoro_tts.py
import numpy as np
import soundfile as sf
from pathlib import Path
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_KOKORO
from pipeline.tts.base import _apply_speed_to_wav

try:
    from kokoro import KPipeline
except ImportError:
    KPipeline = None  # type: ignore[assignment,misc]

SAMPLE_RATE = 24000
DEFAULT_VOICE = "af_heart"


def _generate_sync(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    if KPipeline is None:
        raise ImportError("Install kokoro: pip install kokoro")
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


async def generate_audio(segments: list[dict], output_path: Path, speed: float = 1.0) -> tuple[Path, list[float]]:
    path, durations = await api_call_with_retry(
        lambda: _generate_sync(segments, output_path),
        timeout=TIMEOUT_TTS_KOKORO,
        label="kokoro_tts",
    )
    if speed != 1.0:
        _apply_speed_to_wav(output_path, speed)
        durations = [d / speed for d in durations]
    return path, durations
