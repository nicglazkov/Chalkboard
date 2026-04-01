# pipeline/render_trigger.py
import json
from pathlib import Path
from config import OUTPUT_DIR, TTS_BACKEND, MANIM_QUALITY
from pipeline.state import PipelineState
from pipeline.tts.base import get_backend


async def render_trigger(state: PipelineState) -> dict:
    run_id = state["run_id"]
    run_dir = Path(OUTPUT_DIR) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Generate TTS audio, get actual per-segment durations
    generate_audio = get_backend(TTS_BACKEND)
    wav_path = run_dir / "voiceover.wav"
    speed = state.get("speed", 1.0)
    _, actual_durations = await generate_audio(state["script_segments"], wav_path, speed=speed)

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
        "quality": MANIM_QUALITY,
        "topic": state["topic"],
    }, indent=2))

    return {"status": "approved"}
