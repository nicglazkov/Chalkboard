# pipeline/tts/openai_tts.py
# Requires: pip install openai
import io
import wave
from pathlib import Path
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_SEGMENT

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

OPENAI_MODEL = "gpt-4o-mini-tts"
OPENAI_VOICE = "alloy"


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    if openai is None:
        raise ImportError("Install openai: pip install openai")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_pcm: list[bytes] = []
    durations: list[float] = []
    wav_params = None

    for segment in segments:
        def _call(seg=segment):
            response = openai.audio.speech.create(
                model=OPENAI_MODEL,
                voice=OPENAI_VOICE,
                input=seg["text"],
                response_format="wav",
            )
            with wave.open(io.BytesIO(response.content)) as wf:
                params = wf.getparams()
                frames = wf.readframes(wf.getnframes())
                actual_nframes = len(frames) // (wf.getnchannels() * wf.getsampwidth())
                duration = actual_nframes / wf.getframerate()
                return params, frames, duration

        params, frames, duration = await api_call_with_retry(
            _call, timeout=TIMEOUT_TTS_SEGMENT, label="openai_tts"
        )
        if wav_params is None:
            wav_params = params
        all_pcm.append(frames)
        durations.append(duration)

    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(wav_params.nchannels)
        out_wav.setsampwidth(wav_params.sampwidth)
        out_wav.setframerate(wav_params.framerate)
        out_wav.writeframes(b"".join(all_pcm))

    return output_path, durations
