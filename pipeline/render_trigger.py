# pipeline/render_trigger.py
import json
from pathlib import Path
from config import OUTPUT_DIR, TTS_BACKEND, MANIM_QUALITY, NARRATOR
from pipeline.cues import parse_cues, proportional_cue_times, segment_cue_text, strip_cues
from pipeline.state import PipelineState
from pipeline.tts.base import get_backend
from pipeline.tts.voices import resolve as resolve_narrator


async def render_trigger(state: PipelineState) -> dict:
    run_id = state["run_id"]
    run_dir = Path(OUTPUT_DIR) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Generate TTS audio, get actual per-segment durations
    narrator = state.get("narrator") or NARRATOR or None
    spec = resolve_narrator(narrator)
    generate_audio = get_backend(spec["backend"] if spec else TTS_BACKEND)
    voice_kwargs = {"voice": spec["voice"], "model": spec["model"] or None} if spec else {}
    wav_path = run_dir / "voiceover.wav"
    speed = state.get("speed", 1.0)
    result = await generate_audio(
        state["script_segments"], wav_path, speed=speed, **voice_kwargs
    )
    # Backends return (path, durations) or (path, durations, cue_times).
    actual_durations = result[1]
    cue_times = result[2] if len(result) > 2 else None

    # segments.json: clean text, measured duration, cue times (seconds from the
    # segment start, cues[k-1] = marker [[k]]) and the marked text.
    segments_out = []
    for i, (seg, dur) in enumerate(zip(state["script_segments"], actual_durations, strict=True)):
        raw = segment_cue_text(seg)
        clean, offsets = parse_cues(raw)
        cues = cue_times[i] if cue_times and i < len(cue_times) else None
        if cues is None or len(cues) < (max(offsets) if offsets else 0):
            cues = proportional_cue_times(clean, offsets, dur)
        out = {"text": clean, "actual_duration_sec": dur, "cues": cues}
        if offsets:
            out["cue_text"] = raw
        segments_out.append(out)

    # Write all output files
    (run_dir / "scene.py").write_text(state["manim_code"])
    (run_dir / "segments.json").write_text(json.dumps(segments_out, indent=2))
    (run_dir / "script.txt").write_text(strip_cues(state["script"]))
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "scene_class_name": "ChalkboardScene",
        "quality": state.get("quality") or MANIM_QUALITY,
        "topic": state["topic"],
        "title": state.get("title", ""),
        "effort": state.get("effort_level", "medium"),
        "audience": state.get("audience", "intermediate"),
        "tone": state.get("tone", "casual"),
        "theme": state.get("theme", "chalkboard"),
        "template": state.get("template"),
        "speed": state.get("speed", 1.0),
        "narrator": narrator or TTS_BACKEND,
    }, indent=2))

    return {"status": "approved"}
