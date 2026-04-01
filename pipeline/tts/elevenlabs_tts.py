# pipeline/tts/elevenlabs_tts.py
# Requires: pip install elevenlabs
import os
import wave
from pathlib import Path
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_SEGMENT
from pipeline.tts.base import _apply_speed_to_wav

try:
    from elevenlabs import ElevenLabs
except ImportError:
    ElevenLabs = None  # type: ignore[assignment,misc]

ELEVENLABS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — swap via ELEVENLABS_VOICE_ID env var
SAMPLE_RATE = 24000


async def generate_audio(segments: list[dict], output_path: Path, speed: float = 1.0) -> tuple[Path, list[float]]:
    if ElevenLabs is None:
        raise ImportError("Install elevenlabs: pip install elevenlabs")

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", ELEVENLABS_VOICE_ID)
    client = ElevenLabs()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_pcm: list[bytes] = []
    durations: list[float] = []

    for segment in segments:
        def _call(seg=segment):
            audio_iter = client.text_to_speech.convert(
                voice_id=voice_id,
                text=seg["text"],
                model_id="eleven_turbo_v2_5",
                output_format="pcm_24000",
            )
            pcm = b"".join(audio_iter)
            duration = len(pcm) / (SAMPLE_RATE * 2)
            return pcm, duration

        pcm, duration = await api_call_with_retry(
            _call, timeout=TIMEOUT_TTS_SEGMENT, label="elevenlabs_tts"
        )
        all_pcm.append(pcm)
        durations.append(duration)

    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(1)
        out_wav.setsampwidth(2)
        out_wav.setframerate(SAMPLE_RATE)
        out_wav.writeframes(b"".join(all_pcm))

    if speed != 1.0:
        _apply_speed_to_wav(output_path, speed)
        durations = [d / speed for d in durations]

    return output_path, durations
