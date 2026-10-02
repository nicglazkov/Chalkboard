# pipeline/tts/elevenlabs_tts.py
# Requires: pip install elevenlabs
import asyncio
import os
import wave
from pathlib import Path
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_SEGMENT
from pipeline.tts.base import _apply_speed_to_wav

try:
    from elevenlabs import ElevenLabs
except ImportError:
    ElevenLabs = None  # type: ignore[assignment,misc]

ELEVENLABS_VOICE_ID = "1iNDh1muacMMMHXvS7Ym"  # "Skye" (narrator "aria"); override with ELEVENLABS_VOICE_ID
ELEVENLABS_MODEL_ID = "eleven_v3"
SAMPLE_RATE = 24000
# Segments synthesized at once. Plans cap concurrent requests (Free 2, Starter 3,
# Creator 5, Pro 10); 429s are retried.
MAX_CONCURRENT = int(os.getenv("ELEVENLABS_CONCURRENCY", "3"))
# Pass neighbouring segment text so prosody flows across segment joins.
# Models that reject it (400) fall back to plain requests automatically.
CONTEXT = os.getenv("ELEVENLABS_CONTEXT", "1") != "0"


async def generate_audio(
    segments: list[dict],
    output_path: Path,
    speed: float = 1.0,
    *,
    voice: str | None = None,
    model: str | None = None,
) -> tuple[Path, list[float]]:
    if ElevenLabs is None:
        raise ImportError("Install elevenlabs: pip install elevenlabs")

    voice_id = voice or os.getenv("ELEVENLABS_VOICE_ID") or ELEVENLABS_VOICE_ID
    model_id = model or os.getenv("ELEVENLABS_MODEL_ID") or ELEVENLABS_MODEL_ID
    client = ElevenLabs()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(MAX_CONCURRENT)
    # eleven_v3 rejects previous_text/next_text (verified 2026-10-02).
    use_context = [CONTEXT and not model_id.startswith("eleven_v3")]
    texts = [s["text"] for s in segments]

    def _call(i: int):
        kwargs = dict(voice_id=voice_id, text=texts[i], model_id=model_id, output_format="pcm_24000")
        if use_context[0]:
            if i > 0:
                kwargs["previous_text"] = texts[i - 1]
            if i + 1 < len(texts):
                kwargs["next_text"] = texts[i + 1]
        try:
            pcm = b"".join(client.text_to_speech.convert(**kwargs))
        except Exception as e:  # model without context support: retry plain, once for all
            if use_context[0] and ("previous_text" in str(e) or "next_text" in str(e)):
                use_context[0] = False
                kwargs.pop("previous_text", None)
                kwargs.pop("next_text", None)
                pcm = b"".join(client.text_to_speech.convert(**kwargs))
            else:
                raise
        return pcm, len(pcm) / (SAMPLE_RATE * 2)

    async def _one(i: int):
        async with gate:
            return await api_call_with_retry(
                lambda: _call(i), timeout=TIMEOUT_TTS_SEGMENT, label="elevenlabs_tts"
            )

    results = await asyncio.gather(*(_one(i) for i in range(len(segments))))

    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(1)
        out_wav.setsampwidth(2)
        out_wav.setframerate(SAMPLE_RATE)
        out_wav.writeframes(b"".join(pcm for pcm, _ in results))
    durations = [d for _, d in results]

    if speed != 1.0:
        _apply_speed_to_wav(output_path, speed)
        durations = [d / speed for d in durations]

    return output_path, durations
