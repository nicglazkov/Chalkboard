# pipeline/tts/openai_tts.py
# Requires: pip install openai
import asyncio
import io
import os
import wave
from pathlib import Path
from pipeline.cues import segment_cue_text, strip_cues
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_SEGMENT

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

OPENAI_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
# gpt-4o-mini-tts takes delivery instructions; older tts-1 models ignore them.
OPENAI_INSTRUCTIONS = os.getenv(
    "OPENAI_TTS_INSTRUCTIONS",
    "You are narrating an educational explainer video. Speak clearly and warmly at a "
    "steady teaching pace, with natural emphasis on key terms. Read math aloud the way "
    "a good lecturer would.",
)
MAX_CONCURRENT = 6  # segments synthesized in parallel


async def generate_audio(segments: list[dict], output_path: Path, speed: float = 1.0,
                         *, voice: str | None = None, model: str | None = None,
                         on_segment=None) -> tuple[Path, list[float]]:
    if openai is None:
        raise ImportError("Install openai: pip install openai")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    client = openai.OpenAI()
    gate = asyncio.Semaphore(MAX_CONCURRENT)

    model_id = model or OPENAI_MODEL

    def _call(text: str):
        kwargs = dict(model=model_id, voice=voice or OPENAI_VOICE, input=text,
                      response_format="wav", speed=speed)
        if model_id.startswith("gpt-"):
            kwargs["instructions"] = OPENAI_INSTRUCTIONS
        response = client.audio.speech.create(**kwargs)
        with wave.open(io.BytesIO(response.content)) as wf:
            params = wf.getparams()
            frames = wf.readframes(wf.getnframes())
            actual_nframes = len(frames) // (wf.getnchannels() * wf.getsampwidth())
            return params, frames, actual_nframes / wf.getframerate()

    async def _one(i: int, seg: dict):
        text = strip_cues(segment_cue_text(seg))
        async with gate:
            out = await api_call_with_retry(
                lambda: _call(text), timeout=TIMEOUT_TTS_SEGMENT, label="openai_tts"
            )
        if on_segment is not None:
            on_segment(i, len(text))
        return out

    results = await asyncio.gather(*(_one(i, s) for i, s in enumerate(segments)))

    wav_params = results[0][0]
    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(wav_params.nchannels)
        out_wav.setsampwidth(wav_params.sampwidth)
        out_wav.setframerate(wav_params.framerate)
        out_wav.writeframes(b"".join(frames for _, frames, _ in results))

    # No timestamps from this API: render_trigger estimates cue times
    # proportionally from the measured durations.
    return output_path, [duration for _, _, duration in results]
