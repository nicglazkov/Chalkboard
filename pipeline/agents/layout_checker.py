# pipeline/agents/layout_checker.py
"""
layout_checker — async LangGraph node.

Runs the generated scene.py headlessly (natively, or in Docker --check mode)
to validate layout before committing to a full render. ChalkboardSceneBase writes
layout_report.json to the run directory during the dry-run.
"""
import asyncio
import json
from pathlib import Path
from config import OUTPUT_DIR
from pipeline import render as render_backend
from pipeline import pacing
from pipeline.retry import TIMEOUT_LAYOUT_CHECKER
from pipeline.state import PipelineState


async def layout_checker(state: PipelineState) -> dict:
    run_id = state["run_id"]
    attempts = state["code_attempts"]
    run_dir = Path(OUTPUT_DIR).resolve() / run_id
    report_path = run_dir / "layout_report.json"

    # Write scene.py and a stub segments.json so the dry-run can find them.
    # render_trigger hasn't run yet, so we use estimated durations as placeholders.
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scene.py").write_text(state["manim_code"])
    pace = pacing.resolve_pace(state.get("pace"))
    stub_segments = [_stub_segment(s, pace, state.get("speed", 1.0))
                     for s in state.get("script_segments", [])]
    (run_dir / "segments.json").write_text(json.dumps(stub_segments))

    # Remove stale report from a previous attempt
    report_path.unlink(missing_ok=True)

    cmd, env = render_backend.check_cmd(run_dir)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except Exception as e:
        return {
            "code_feedback": f"Layout check failed to start: {e}",
            "code_attempts": attempts + 1,
            "layout_renderable": False,
        }

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=TIMEOUT_LAYOUT_CHECKER,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()  # drain to avoid zombie
        return {
            "code_feedback": (
                f"Layout check timed out after {int(TIMEOUT_LAYOUT_CHECKER)}s. "
                "Simplify the scene — reduce total mobjects or animation count."
            ),
            "code_attempts": attempts + 1,
            "layout_renderable": False,
        }

    if not report_path.exists():
        # Crash during the dry-run: never renderable as-is.
        # The traceback's tail names the failing line; the head is import noise.
        stderr_text = stderr.decode(errors="replace")[-1500:]
        return {
            "code_feedback": (
                "Layout check did not produce a report — scene likely crashed "
                "during dry-run. Fix the error below and ensure end_layout_check() "
                "is called at the end of construct().\n\n"
                f"Error output:\n{stderr_text}"
            ),
            "code_attempts": attempts + 1,
            "layout_renderable": False,
        }

    try:
        report = json.loads(report_path.read_text())
    except Exception as e:
        return {
            "code_feedback": f"Layout report unreadable: {e}",
            "code_attempts": attempts + 1,
            "layout_renderable": False,
        }

    if report.get("passed"):
        return {"code_feedback": None, "layout_renderable": True}

    # The scene ran end to end; only geometry/timing complaints remain. Routing
    # may render it anyway once retries run out (better than no video).
    return {
        "code_feedback": _format_violations(report.get("violations", [])),
        "code_attempts": attempts + 1,
        "layout_renderable": True,
    }


def _stub_segment(s: dict, pace=None, speed: float = 1.0) -> dict:
    """segments.json entry for the dry-run. Before TTS exists, durations and
    cue times are estimated from the script's estimate at the run's pace
    (delivery speed, lead-in, pauses and the silent hold, pipeline/pacing.py),
    so self.cue(k) works, late cues and animations running into the hold are
    caught early, and the hold is never mistaken for an overrun. Measured
    values (QA regeneration passes real segments) are kept as they are."""
    return pacing.estimate_segment(s, pace or pacing.resolve_pace(), speed)


def _format_violations(violations: list) -> str:
    lines = ["Layout check failed. Fix these issues before rendering:"]
    for v in violations:
        vtype = v.get("type", "unknown").upper().replace("_", " ")
        seg = v.get("segment", "?")
        lines.append("")
        lines.append(f"[Segment {seg} — {vtype}]")
        lines.append(v.get("description", "No description"))
    return "\n".join(lines)
