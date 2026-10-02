# pipeline/tts/elevenlabs_tts.py
"""ElevenLabs narration over the REST API (httpx; no SDK needed).

Segments are synthesized separately (their durations drive the animation), so
the joins are where narration usually sounds stitched together. On models that
support request stitching (eleven_v4, multilingual_v2, ...) segments are made
in order and each request names the previous ones (previous_request_ids) plus
the neighbouring text, so intonation carries across segment boundaries.
eleven_v3 rejects both (verified 2026-10-02), so it runs in parallel without.
"""
import asyncio
import os
import wave
from pathlib import Path

import httpx

from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_SEGMENT
from pipeline.tts.base import _apply_speed_to_wav

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
ELEVENLABS_VOICE_ID = "1iNDh1muacMMMHXvS7Ym"  # "Skye" (narrator "aria"); override with ELEVENLABS_VOICE_ID
ELEVENLABS_MODEL_ID = "eleven_v4"
SAMPLE_RATE = 24000
# Parallel requests when stitching is unavailable. Plans cap concurrency
# (Free 2, Starter 3, Creator 5, Pro 10); 429s back off and retry.
MAX_CONCURRENT = int(os.getenv("ELEVENLABS_CONCURRENCY", "3"))
STITCH = os.getenv("ELEVENLABS_STITCH", "1") != "0"
_NO_STITCH_MODELS = ("eleven_v3",)


def _supports_stitching(model_id: str) -> bool:
    return STITCH and not model_id.startswith(_NO_STITCH_MODELS)


async def generate_audio(
    segments: list[dict],
    output_path: Path,
    speed: float = 1.0,
    *,
    voice: str | None = None,
    model: str | None = None,
) -> tuple[Path, list[float]]:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    voice_id = voice or os.getenv("ELEVENLABS_VOICE_ID") or ELEVENLABS_VOICE_ID
    model_id = model or os.getenv("ELEVENLABS_MODEL_ID") or ELEVENLABS_MODEL_ID
    texts = [s["text"] for s in segments]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=TIMEOUT_TTS_SEGMENT, headers={"xi-api-key": key})

    def _call(i: int, previous_ids: list[str]) -> tuple[bytes, str | None]:
        body: dict = {"text": texts[i], "model_id": model_id}
        if _supports_stitching(model_id):
            if i > 0:
                body["previous_text"] = texts[i - 1]
            if i + 1 < len(texts):
                body["next_text"] = texts[i + 1]
            if previous_ids:
                body["previous_request_ids"] = previous_ids[-3:]
        r = client.post(API.format(voice=voice_id), params={"output_format": "pcm_24000"}, json=body)
        if r.status_code in (401, 403, 422):
            # Bad key, missing permission, invalid request: retrying won't help.
            raise ValueError(f"ElevenLabs {r.status_code} (not retried): {r.text[:300]}")
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
        return r.content, r.headers.get("request-id")

    try:
        if _supports_stitching(model_id):
            pcms, ids = [], []
            for i in range(len(texts)):
                pcm, rid = await api_call_with_retry(
                    lambda i=i: _call(i, ids), timeout=TIMEOUT_TTS_SEGMENT, label="elevenlabs_tts"
                )
                pcms.append(pcm)
                if rid:
                    ids.append(rid)
        else:
            gate = asyncio.Semaphore(MAX_CONCURRENT)

            async def _one(i: int):
                async with gate:
                    pcm, _ = await api_call_with_retry(
                        lambda: _call(i, []), timeout=TIMEOUT_TTS_SEGMENT, label="elevenlabs_tts"
                    )
                    return pcm

            pcms = list(await asyncio.gather(*(_one(i) for i in range(len(texts)))))
    finally:
        client.close()

    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(1)
        out_wav.setsampwidth(2)
        out_wav.setframerate(SAMPLE_RATE)
        out_wav.writeframes(b"".join(pcms))
    durations = [len(p) / (SAMPLE_RATE * 2) for p in pcms]

    if speed != 1.0:
        _apply_speed_to_wav(output_path, speed)
        durations = [d / speed for d in durations]

    return output_path, durations
