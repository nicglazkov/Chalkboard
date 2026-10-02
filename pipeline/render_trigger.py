# pipeline/render_trigger.py
import json
from pathlib import Path
from config import OUTPUT_DIR, TTS_BACKEND, MANIM_QUALITY, NARRATOR
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
    _, actual_durations = await generate_audio(
        state["script_segments"], wav_path, speed=speed, **voice_kwargs
    )

    # Build segments.json with actual durations
    segments_out = [
        {"text": seg["text"], "actual_duration_sec": dur}
        for seg, dur in zip(state["script_segments"], actual_durations, strict=True)
    ]

    # Write all output files
    (run_dir / "scene.py").write_text(state["manim_code"])
    (run_dir / "segments.json").write_text(json.dumps(segments_out, indent=2))
    (run_dir / "script.txt").write_text(state["script"])
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
