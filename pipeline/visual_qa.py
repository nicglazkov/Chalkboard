# pipeline/visual_qa.py
import base64
import json
import subprocess
from pathlib import Path
import anthropic
from config import CLAUDE_MODEL

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


def _extract_frames(video_path: Path, qa_dir: Path, n_frames: int = 5) -> list[Path]:
    """Extract n evenly-spaced frames from video_path into qa_dir."""
    qa_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, check=True,
    )
    duration = float(result.stdout.strip())

    if duration <= 0.0:
        raise ValueError(f"Video has no duration (got {duration!r}): {video_path}")

    frame_paths = []
    for i in range(n_frames):
        t = duration * i / max(1, n_frames - 1)
        frame_path = qa_dir / f"frame_{i:02d}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
             "-frames:v", "1", str(frame_path)],
            capture_output=True, check=True,
        )
        frame_paths.append(frame_path)

    return frame_paths


def visual_qa(video_path: Path, qa_dir: Path, client=None) -> dict:
    """
    Run visual QA on a rendered video by sampling frames and reviewing with Claude.

    Returns {"passed": bool, "issues": [{"severity": "warning"|"error", "description": str}]}
    """
    if client is None:
        client = anthropic.Anthropic()

    frame_paths = _extract_frames(video_path, qa_dir)

    content = [
        {
            "type": "text",
            "text": (
                "You are reviewing frames from an educational animation video for visual quality issues. "
                "Check each frame for: overlapping text or shapes, text extending off-screen, "
                "unreadably small text, poor color contrast, and visual clutter. "
                "A frame passes if all elements are clearly readable and none extend beyond the frame boundary."
            ),
        }
    ]

    for i, frame_path in enumerate(frame_paths):
        frame_data = base64.standard_b64encode(frame_path.read_bytes()).decode()
        content.append({"type": "text", "text": f"Frame {i + 1}/{len(frame_paths)}:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": frame_data,
            },
        })

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )

    return json.loads(response.content[0].text)
