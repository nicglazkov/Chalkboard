# pipeline/tts/base.py
import subprocess
from pathlib import Path
from typing import Callable


# Signature all backends must implement:
# generate_audio(segments, output_path, speed=1.0) -> tuple[Path, list[float]]
# Returns (wav_path, list_of_actual_duration_sec_per_segment)


def _build_atempo(speed: float) -> str:
    """Build ffmpeg atempo filter chain. atempo only accepts [0.5, 2.0]; chain for extremes."""
    filters = []
    s = speed
    while s > 2.0:
        filters.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        filters.append("atempo=0.5")
        s /= 0.5
    filters.append(f"atempo={s:.6f}")
    return ",".join(filters)


def _apply_speed_to_wav(wav_path: Path, speed: float) -> None:
    """Apply tempo scaling to wav_path in-place using ffmpeg atempo."""
    tmp = wav_path.with_suffix(".speed_tmp.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-af", _build_atempo(speed), str(tmp)],
        check=True, capture_output=True, timeout=120,
    )
    tmp.replace(wav_path)


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
