# pipeline/tts/elevenlabs_tts.py
"""ElevenLabs narration over the REST API (httpx; no SDK needed).

Segments are synthesized separately (their durations drive the animation), so
the joins are where narration usually sounds stitched together. On models that
support request stitching (eleven_v4, multilingual_v2, ...) segments are made
in order and each request names the previous ones (previous_request_ids) plus
the neighbouring text, so intonation carries across segment boundaries.
eleven_v3 rejects both (verified 2026-10-02), so it runs in parallel without.

Requests go to the /with-timestamps endpoint (same body, JSON response with
`audio_base64` and a per-character `alignment` of the input text; verified
2026-10-02 with stitching on eleven_v4), which gives the exact time of every
[[n]] cue marker in a segment.
"""
import asyncio
import base64
import os
import wave
from pathlib import Path

import httpx

from pipeline.cues import cue_times_from_alignment, parse_cues, proportional_cue_times, segment_cue_text
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_SEGMENT
from pipeline.tts.base import _apply_speed_to_wav

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
ELEVENLABS_VOICE_ID = "1iNDh1muacMMMHXvS7Ym"  # "Skye" (narrator "aria"); override with ELEVENLABS_VOICE_ID
ELEVENLABS_MODEL_ID = "eleven_v4"
SAMPLE_RATE = 24000
# Parallel requests when stitching is unavailable. Plans cap concurrency
# (Free 2, Starter 3, Creator 5, Pro 10); 429s back off and retry.
MAX_CONCURRENT = int(os.getenv("ELEVENLABS_CONCURRENCY", "3"))
STITCH = os.getenv("ELEVENLABS_STITCH", "1") != "0"
_NO_STITCH_MODELS = ("eleven_v3",)
# Native delivery speed (voice_settings.speed): documented range 0.7-1.2.
# Outside it the audio is time-stretched with ffmpeg atempo instead.
NATIVE_SPEED_RANGE = (0.7, 1.2)


def _supports_stitching(model_id: str) -> bool:
    return STITCH and not model_id.startswith(_NO_STITCH_MODELS)


def _decode(r: httpx.Response) -> tuple[bytes, dict | None]:
    """(pcm, alignment) from a with-timestamps response; raw PCM bodies
    (plain endpoint, test doubles) come back without alignment."""
    if "json" in (r.headers.get("content-type") or ""):
        data = r.json()
        return base64.b64decode(data.get("audio_base64") or ""), data.get("alignment")
    return r.content, None


async def generate_audio(
    segments: list[dict],
    output_path: Path,
    speed: float = 1.0,
    *,
    voice: str | None = None,
    model: str | None = None,
    on_segment=None,
) -> tuple[Path, list[float], list[list[float | None]]]:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    voice_id = voice or os.getenv("ELEVENLABS_VOICE_ID") or ELEVENLABS_VOICE_ID
    model_id = model or os.getenv("ELEVENLABS_MODEL_ID") or ELEVENLABS_MODEL_ID
    parsed = [parse_cues(segment_cue_text(s)) for s in segments]
    texts = [clean for clean, _ in parsed]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=TIMEOUT_TTS_SEGMENT, headers={"xi-api-key": key})
    voice_settings = _native_speed_settings(client, voice_id, speed)
    stretch = speed if (speed != 1.0 and voice_settings is None) else 1.0

    def _call(i: int, previous_ids: list[str]) -> tuple[bytes, dict | None, str | None]:
        body: dict = {"text": texts[i], "model_id": model_id}
        if voice_settings is not None:
            body["voice_settings"] = voice_settings
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
        pcm, alignment = _decode(r)
        return pcm, alignment, r.headers.get("request-id")

    try:
        if _supports_stitching(model_id):
            results, ids = [], []
            for i in range(len(texts)):
                pcm, alignment, rid = await api_call_with_retry(
                    lambda i=i: _call(i, ids), timeout=TIMEOUT_TTS_SEGMENT, label="elevenlabs_tts"
                )
                results.append((pcm, alignment))
                if on_segment is not None:
                    on_segment(i, len(texts[i]))
                if rid:
                    ids.append(rid)
        else:
            gate = asyncio.Semaphore(MAX_CONCURRENT)

            async def _one(i: int):
                async with gate:
                    pcm, alignment, _ = await api_call_with_retry(
                        lambda: _call(i, []), timeout=TIMEOUT_TTS_SEGMENT, label="elevenlabs_tts"
                    )
                    if on_segment is not None:
                        on_segment(i, len(texts[i]))
                    return pcm, alignment

            results = list(await asyncio.gather(*(_one(i) for i in range(len(texts)))))
    finally:
        client.close()

    pcms = [pcm for pcm, _ in results]
    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(1)
        out_wav.setsampwidth(2)
        out_wav.setframerate(SAMPLE_RATE)
        out_wav.writeframes(b"".join(pcms))
    durations = [len(p) / (SAMPLE_RATE * 2) for p in pcms]

    cue_times = []
    for (clean, offsets), (_, alignment), dur in zip(parsed, results, durations):
        if alignment:
            cue_times.append(cue_times_from_alignment(
                clean, offsets, alignment.get("characters") or [],
                alignment.get("character_start_times_seconds") or [], dur))
        else:
            cue_times.append(proportional_cue_times(clean, offsets, dur))

    if stretch != 1.0:
        _apply_speed_to_wav(output_path, stretch)
        durations = [d / stretch for d in durations]
        cue_times = [[None if t is None else round(t / stretch, 3) for t in c] for c in cue_times]

    return output_path, durations, cue_times


def _native_speed_settings(client: httpx.Client, voice_id: str, speed: float) -> dict | None:
    """voice_settings that keep the voice's stored settings and change only
    `speed`, or None when native speed does not apply (speed 1.0, out of the
    documented range, or the stored settings could not be read: voice_settings
    in a request replaces the stored ones, so they are never sent partially)."""
    if speed == 1.0 or not NATIVE_SPEED_RANGE[0] <= speed <= NATIVE_SPEED_RANGE[1]:
        return None
    try:
        r = client.get(f"https://api.elevenlabs.io/v1/voices/{voice_id}/settings")
        stored = r.json() if r.status_code == 200 else None
    except Exception:
        stored = None
    if not isinstance(stored, dict):
        return None
    return {**stored, "speed": round(float(speed), 3)}
