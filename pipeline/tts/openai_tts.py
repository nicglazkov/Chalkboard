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

    import wave
    import io

    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_pcm: list[bytes] = []
    durations: list[float] = []
    wav_params = None

    for segment in segments:
        response = openai.audio.speech.create(
            model=OPENAI_MODEL,
            voice=OPENAI_VOICE,
            input=segment["text"],
            response_format="wav",
        )
        with wave.open(io.BytesIO(response.content)) as wf:
            if wav_params is None:
                wav_params = wf.getparams()
            frames = wf.readframes(wf.getnframes())
            all_pcm.append(frames)
            durations.append(wf.getnframes() / wf.getframerate())

    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(wav_params.nchannels)
        out_wav.setsampwidth(wav_params.sampwidth)
        out_wav.setframerate(wav_params.framerate)
        out_wav.writeframes(b"".join(all_pcm))

    return output_path, durations


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    return await asyncio.to_thread(_generate_sync, segments, output_path)
