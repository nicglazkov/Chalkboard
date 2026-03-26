# pipeline/tts/elevenlabs_tts.py
# Requires: pip install elevenlabs
import asyncio
import os
from pathlib import Path

try:
    from elevenlabs import ElevenLabs
except ImportError:
    ElevenLabs = None  # type: ignore[assignment,misc]

ELEVENLABS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — swap via ELEVENLABS_VOICE_ID env var


def _generate_sync(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    if ElevenLabs is None:
        raise ImportError("Install elevenlabs: pip install elevenlabs")

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", ELEVENLABS_VOICE_ID)
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

    # MVP caveat: concatenates raw MP3 chunks — suitable for single-segment scripts only.
    # A proper implementation would decode and re-encode into a single audio container.
    with open(output_path, "wb") as f:
        f.write(b"".join(all_bytes))

    return output_path, durations


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    return await asyncio.to_thread(_generate_sync, segments, output_path)
