# pipeline/tts/openai_tts.py
# Requires: pip install openai
import asyncio
from pathlib import Path

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

OPENAI_MODEL = "gpt-4o-mini-tts"
OPENAI_VOICE = "alloy"
SAMPLE_RATE = 24000


def _generate_sync(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    if openai is None:
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
        # Estimate duration from WAV file size (2 bytes/sample, mono, 24kHz, 44-byte header)
        pcm_bytes = max(0, len(chunk_bytes) - 44)
        duration = pcm_bytes / (SAMPLE_RATE * 2)
        durations.append(duration)
        all_bytes.append(chunk_bytes)

    # MVP caveat: concatenates raw WAV bytes across segments — valid for single-segment
    # scripts, multi-segment produces multiple WAV headers. Fix by decode+re-encode before production.
    with open(output_path, "wb") as f:
        f.write(b"".join(all_bytes))

    return output_path, durations


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    return await asyncio.to_thread(_generate_sync, segments, output_path)
