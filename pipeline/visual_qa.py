# pipeline/visual_qa.py
import asyncio
import base64
import json
import subprocess
from pathlib import Path
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_VISUAL_QA

SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["warning", "error"]},
                    "description": {"type": "string"},
                },
                "required": ["severity", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["passed", "issues"],
    "additionalProperties": False,
}


def _extract_frames(video_path: Path, qa_dir: Path, n_frames: int | None = None) -> list[Path]:
    """Extract evenly-spaced frames. n_frames defaults to 1 per 30s, min 5, max 10."""
    qa_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    duration = float(result.stdout.strip())

    if duration <= 0.0:
        raise ValueError(f"Video has no duration (got {duration!r}): {video_path}")

    if n_frames is None:
        n_frames = max(5, min(int(duration / 30), 10))

    frame_paths = []
    for i in range(n_frames):
        t = duration * i / n_frames  # sample at 0%, 1/n%, ..., (n-1)/n% — never seek to exact end
        frame_path = qa_dir / f"frame_{i:02d}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
             "-frames:v", "1", str(frame_path)],
            capture_output=True, check=True, timeout=30,
        )
        frame_paths.append(frame_path)

    return frame_paths


def visual_qa(video_path: Path, qa_dir: Path, client=None, scene_code: str | None = None) -> dict:
    """
    Run visual QA on a rendered video by sampling frames and reviewing with Claude.
    scene_code: if provided, included in the prompt so Claude can pinpoint which
                class/method is responsible for each issue.
    Returns {"passed": bool, "issues": [{"severity": "warning"|"error", "description": str}]}
    """
    if client is None:
        client = anthropic.Anthropic()

    frame_paths = _extract_frames(video_path, qa_dir)

    content = [
        {
            "type": "text",
            "text": (
                "You are reviewing frames from an educational Manim animation for visual quality issues. "
                "Check each frame for: overlapping text or shapes, text extending off-screen, "
                "unreadably small text, poor color contrast, and visual clutter. "
                "A frame passes if all elements are clearly readable and none extend beyond the frame boundary."
            ),
        }
    ]

    if scene_code:
        content.append({
            "type": "text",
            "text": (
                "The Manim source code that produced this video is provided below. "
                "When reporting issues, reference the specific method or construct "
                "in the code that is likely responsible (e.g., 'the show_comparison method "
                "creates overlapping labels').\n\n"
                f"```python\n{scene_code}\n```"
            ),
        })

    for i, frame_path in enumerate(frame_paths):
        frame_data = base64.standard_b64encode(frame_path.read_bytes()).decode()
        content.append({"type": "text", "text": f"Frame {i + 1}/{len(frame_paths)}:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": frame_data},
        })

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )

    response = asyncio.run(
        api_call_with_retry(_call, timeout=TIMEOUT_VISUAL_QA, label="visual_qa")
    )
    return json.loads(response.content[0].text)
