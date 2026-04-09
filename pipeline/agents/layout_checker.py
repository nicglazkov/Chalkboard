# pipeline/agents/layout_checker.py
"""
layout_checker — async LangGraph node.

Runs the generated scene.py headlessly in Docker (--check mode) to validate
layout before committing to a full render. ChalkboardSceneBase writes
layout_report.json to the run directory during the dry-run.
"""
import asyncio
import json
from pathlib import Path
from config import OUTPUT_DIR
from pipeline.retry import TIMEOUT_LAYOUT_CHECKER
from pipeline.state import PipelineState

DOCKER_IMAGE = "chalkboard-render"


async def layout_checker(state: PipelineState) -> dict:
    run_id = state["run_id"]
    attempts = state["code_attempts"]
    run_dir = Path(OUTPUT_DIR).resolve() / run_id
    report_path = run_dir / "layout_report.json"

    # Remove stale report from a previous attempt
    report_path.unlink(missing_ok=True)

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{run_dir}:/output",
        DOCKER_IMAGE,
        "--check",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        return {
            "code_feedback": f"Layout check failed to start: {e}",
            "code_attempts": attempts + 1,
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
        }

    if not report_path.exists():
        stderr_text = stderr.decode(errors="replace")[:600]
        return {
            "code_feedback": (
                "Layout check did not produce a report — scene likely crashed "
                "during dry-run. Fix the error below and ensure end_layout_check() "
                "is called at the end of construct().\n\n"
                f"Error output:\n{stderr_text}"
            ),
            "code_attempts": attempts + 1,
        }

    try:
        report = json.loads(report_path.read_text())
    except Exception as e:
        return {
            "code_feedback": f"Layout report unreadable: {e}",
            "code_attempts": attempts + 1,
        }

    if report.get("passed"):
        return {"code_feedback": None}

    return {
        "code_feedback": _format_violations(report.get("violations", [])),
        "code_attempts": attempts + 1,
    }


def _format_violations(violations: list) -> str:
    lines = ["Layout check failed. Fix these issues before rendering:"]
    for v in violations:
        vtype = v.get("type", "unknown").upper().replace("_", " ")
        seg = v.get("segment", "?")
        lines.append("")
        lines.append(f"[Segment {seg} — {vtype}]")
        lines.append(v.get("description", "No description"))
    return "\n".join(lines)
