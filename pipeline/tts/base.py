# pipeline/tts/base.py
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
