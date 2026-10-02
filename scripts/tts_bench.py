#!/usr/bin/env python3
"""Compare voices on the same explainer script.

    python scripts/tts_bench.py --out bench/ [--takes aria,milo@eleven_v3,kokoro] [--whisper]

A take is a narrator name from pipeline/tts/voices.py, optionally with
@<model> to override its model (e.g. aria@eleven_v3). Writes <out>/<take>.wav
and <out>/results.json (synthesis time, audio length, per-segment durations,
optional Whisper word error rate). WER only flags mispronounced or
hallucinated audio; listen to the files to judge naturalness.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402,F401  (loads .env)
from pipeline.tts.base import get_backend  # noqa: E402
from pipeline.tts.voices import NARRATORS  # noqa: E402

# Math-heavy on purpose: spoken notation, numbers, a name, a question.
SCRIPT = [
    "Here's a strange fact: the function e to the x is its own derivative. "
    "Its slope at every point is exactly its height.",
    "Start from the definition. The derivative is the limit, as h goes to zero, "
    "of e to the x plus h, minus e to the x, all over h.",
    "Factor out e to the x, and everything hinges on one number: the limit of "
    "e to the h minus one, over h. Try h equals one thousandth, and you get about 1.0005.",
    "That limit is exactly one. So why does Euler's number, roughly 2.718, "
    "make calculus so clean? Because it's the only base where growth equals size.",
]

DEFAULT_TAKES = [
    "aria", "milo",                                   # eleven_v4
    "aria@eleven_v3", "milo@eleven_v3",
    "kokoro",
]


def _load_16k(path: Path):
    """Mono 16 kHz float32 via ffmpeg (faster-whisper's own decoder needs a newer
    PyAV than Manim allows)."""
    import subprocess
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _norm(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9. ]", " ", s.lower()).replace(".", " ").split()


def wer(ref: str, hyp: str) -> float:
    r, h = _norm(ref), _norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


async def run_take(take: str, out: Path) -> dict:
    name, _, model_override = take.partition("@")
    spec = NARRATORS[name]
    model = model_override or spec["model"] or None
    gen = get_backend(spec["backend"])
    segs = [{"text": t, "estimated_duration_sec": len(t.split()) / 2.5} for t in SCRIPT]
    t0 = time.perf_counter()
    _, durs = await gen(segs, out / f"{take}.wav", voice=spec["voice"], model=model)
    return {"take": take, "narrator": name, "backend": spec["backend"], "voice": spec["voice"],
            "model": model or "", "synth_sec": round(time.perf_counter() - t0, 2),
            "audio_sec": round(sum(durs), 2), "segment_sec": [round(d, 2) for d in durs]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="bench")
    ap.add_argument("--takes", default=",".join(DEFAULT_TAKES))
    ap.add_argument("--whisper", action="store_true", help="transcribe with faster-whisper and report WER")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for take in args.takes.split(","):
        try:
            r = asyncio.run(run_take(take, out))
        except Exception as e:
            r = {"take": take, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        print(json.dumps(r))
        results.append(r)
    if args.whisper:
        from faster_whisper import WhisperModel
        model = WhisperModel("large-v3", device="cuda", compute_type="float16")
        ref = " ".join(SCRIPT)
        for r in results:
            if "error" in r:
                continue
            segs, _ = model.transcribe(_load_16k(out / f"{r['take']}.wav"), language="en", beam_size=5)
            hyp = " ".join(s.text for s in segs)
            r["wer"] = round(wer(ref, hyp), 3)
            r["transcript"] = hyp.strip()
            print(r["take"], "WER", r["wer"])
    (out / "results.json").write_text(json.dumps({"script": SCRIPT, "results": results}, indent=2))


if __name__ == "__main__":
    main()
