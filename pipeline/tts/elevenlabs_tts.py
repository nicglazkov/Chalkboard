# pipeline/tts/elevenlabs_tts.py
# Requires: pip install elevenlabs
import asyncio
import io
import os
import wave
from pathlib import Path

try:
    from elevenlabs import ElevenLabs
except ImportError:
    ElevenLabs = None  # type: ignore[assignment,misc]

ELEVENLABS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — swap via ELEVENLABS_VOICE_ID env var
SAMPLE_RATE = 24000


def _generate_sync(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    if ElevenLabs is None:
        raise ImportError("Install elevenlabs: pip install elevenlabs")

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", ELEVENLABS_VOICE_ID)
    client = ElevenLabs()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_pcm: list[bytes] = []
    durations: list[float] = []

    for segment in segments:
        audio_iter = client.text_to_speech.convert(
            voice_id=voice_id,
            text=segment["text"],
            model_id="eleven_turbo_v2_5",
            output_format="pcm_24000",  # raw 16-bit PCM, no headers
        )
        pcm = b"".join(audio_iter)
        all_pcm.append(pcm)
        durations.append(len(pcm) / (SAMPLE_RATE * 2))  # 16-bit = 2 bytes/sample

    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(1)
        out_wav.setsampwidth(2)
        out_wav.setframerate(SAMPLE_RATE)
        out_wav.writeframes(b"".join(all_pcm))

    return output_path, durations


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    return await asyncio.to_thread(_generate_sync, segments, output_path)
