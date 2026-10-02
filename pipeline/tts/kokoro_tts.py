# pipeline/tts/kokoro_tts.py
import functools
import os
import numpy as np
import soundfile as sf
from pathlib import Path
from pipeline.cues import cue_times_from_tokens, parse_cues, segment_cue_text
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_KOKORO
from pipeline.tts.base import _apply_speed_to_wav

try:
    from kokoro import KPipeline
except ImportError:
    KPipeline = None  # type: ignore[assignment,misc]

SAMPLE_RATE = 24000
DEFAULT_VOICE = os.getenv("KOKORO_VOICE", "af_heart")


@functools.cache
def _pipeline():
    """Load the model once per process (on the GPU when CUDA is available)."""
    return KPipeline(lang_code="a")


def _generate_sync(segments: list[dict], output_path: Path, voice: str | None = None, on_segment=None):
    if KPipeline is None:
        raise ImportError("Install kokoro: pip install kokoro")
    pipeline = _pipeline()
    all_audio: list[np.ndarray] = []
    durations: list[float] = []
    cue_times: list[list[float | None]] = []

    for index, segment in enumerate(segments):
        clean, offsets = parse_cues(segment_cue_text(segment))
        seg_chunks: list[np.ndarray] = []
        # KPipeline yields Result objects (graphemes, phonemes, audio when
        # unpacked) whose .tokens carry start_ts/end_ts relative to that chunk.
        tokens: list[tuple[str, float | None]] = []
        chunk_start = 0.0
        for result in pipeline(clean, voice=voice or DEFAULT_VOICE):
            _gs, _ps, audio = result
            for tok in getattr(result, "tokens", None) or []:
                ts = getattr(tok, "start_ts", None)
                tokens.append((getattr(tok, "text", ""), None if ts is None else chunk_start + ts))
            if audio is None:
                continue
            seg_chunks.append(np.asarray(audio, dtype=np.float32))
            chunk_start += len(seg_chunks[-1]) / SAMPLE_RATE
        seg_audio = np.concatenate(seg_chunks) if seg_chunks else np.array([], dtype=np.float32)
        dur = len(seg_audio) / SAMPLE_RATE
        durations.append(dur)
        all_audio.append(seg_audio)
        cue_times.append(cue_times_from_tokens(clean, offsets, tokens, dur))
        if on_segment is not None:
            on_segment(index, len(clean))

    full_audio = np.concatenate(all_audio) if all_audio else np.array([], dtype=np.float32)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), full_audio, SAMPLE_RATE)
    return output_path, durations, cue_times


async def generate_audio(segments: list[dict], output_path: Path, speed: float = 1.0,
                         *, voice: str | None = None, model: str | None = None, on_segment=None):
    """Returns (wav_path, durations, cue_times); see pipeline/cues.py.
    on_segment(index, chars) is called as each segment finishes (from a worker thread)."""
    path, durations, cue_times = await api_call_with_retry(
        lambda: _generate_sync(segments, output_path, voice, on_segment),
        timeout=TIMEOUT_TTS_KOKORO,
        label="kokoro_tts",
    )
    if speed != 1.0:
        _apply_speed_to_wav(output_path, speed)
        durations = [d / speed for d in durations]
        cue_times = [[None if t is None else round(t / speed, 3) for t in c] for c in cue_times]
    return path, durations, cue_times
