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


# seconds-per-frame and max-frames per density level
_QA_DENSITY = {
    "normal": (30, 10),  # 1 frame per 30s, cap at 10
    "high":   (15, 20),  # 1 frame per 15s, cap at 20
}


def _extract_frames(
    video_path: Path, qa_dir: Path,
    n_frames: int | None = None,
    seconds_per_frame: int = 30,
    max_frames: int = 10,
) -> list[Path]:
    """Extract evenly-spaced frames. n_frames defaults to 1 per seconds_per_frame, min 5."""
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
        n_frames = max(5, min(int(duration / seconds_per_frame), max_frames))

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


def _segment_boundary_timestamps(
    segments: list[dict],
    max_frames: int = 10,
) -> list[tuple[float, int, str]]:
    """
    Compute frame timestamps at segment boundaries.

    Returns list of (timestamp_sec, segment_index, script_text) tuples, capped
    at max_frames. Samples:
      - t=0.5  (intro, before first content)
      - end of each segment (cumulative sum of actual_duration_sec)
      - midpoint of any segment longer than 4s
    """
    timestamps: list[tuple[float, int, str]] = []
    cumulative = 0.0

    # Intro sample
    timestamps.append((0.5, 0, segments[0].get("text", "") if segments else ""))

    for i, seg in enumerate(segments):
        dur = seg.get("actual_duration_sec", seg.get("estimated_duration_sec", 2.0))
        text = seg.get("text", "")

        # Midpoint for long segments
        if dur > 4.0:
            mid_t = cumulative + dur / 2.0
            timestamps.append((round(mid_t, 2), i, text))

        cumulative += dur
        timestamps.append((round(cumulative, 2), i, text))

    # Deduplicate and sort
    seen: set[float] = set()
    unique: list[tuple[float, int, str]] = []
    for t, idx, txt in sorted(timestamps, key=lambda x: x[0]):
        if t not in seen:
            seen.add(t)
            unique.append((t, idx, txt))

    # Cap to max_frames, keeping even distribution
    if len(unique) <= max_frames:
        return unique

    step = len(unique) / max_frames
    return [unique[round(i * step)] for i in range(max_frames)]


def _extract_frames_at_timestamps(
    video_path: Path,
    qa_dir: Path,
    timestamps: list[tuple[float, int, str]],
) -> list[tuple[Path, float, int, str]]:
    """
    Extract frames at specific timestamps.
    Returns list of (frame_path, timestamp, segment_index, script_text).
    """
    qa_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, (t, seg_idx, text) in enumerate(timestamps):
        frame_path = qa_dir / f"frame_{i:02d}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
             "-frames:v", "1", str(frame_path)],
            capture_output=True, check=True, timeout=30,
        )
        results.append((frame_path, t, seg_idx, text))
    return results


def visual_qa(
    video_path: Path,
    qa_dir: Path,
    client=None,
    scene_code: str | None = None,
    density: str = "normal",
    segments: list[dict] | None = None,
    layout_report_path: Path | None = None,
) -> dict:
    """
    Run visual QA on a rendered video.

    segments: if provided, frames are sampled at segment boundaries with script
              context included in the prompt. Falls back to even spacing if None.
    layout_report_path: if provided (first QA only), violated segments from the
                        dry-run get extra frame samples.
    Returns {"passed": bool, "issues": [{"severity": "warning"|"error", "description": str}]}
    """
    if client is None:
        client = anthropic.Anthropic()

    spf, max_f = _QA_DENSITY.get(density, _QA_DENSITY["normal"])

    if segments:
        # Boost max_frames for violated segments if layout_report available
        extra_segments: set[int] = set()
        if layout_report_path and layout_report_path.exists():
            try:
                report = json.loads(layout_report_path.read_text())
                for v in report.get("violations", []):
                    if "segment" in v:
                        extra_segments.add(v["segment"])
            except Exception:
                pass

        effective_max = min(max_f + len(extra_segments), max_f * 2)
        ts_list = _segment_boundary_timestamps(segments, max_frames=effective_max)
        frame_tuples = _extract_frames_at_timestamps(video_path, qa_dir, ts_list)
    else:
        # Fallback: even spacing (legacy behaviour, used when segments.json unavailable)
        frame_paths = _extract_frames(video_path, qa_dir, seconds_per_frame=spf, max_frames=max_f)
        frame_tuples = [(fp, 0.0, None, None) for fp in frame_paths]

    content = [
        {
            "type": "text",
            "text": (
                "You are reviewing frames from an educational Manim animation for visual quality issues. "
                "Check each frame for: overlapping text or shapes, text extending off-screen, "
                "unreadably small text, poor color contrast, and visual clutter. "
                "A frame passes if all elements are clearly readable and none extend beyond the frame boundary. "
                "When reporting issues, include the segment number if shown in the frame label."
            ),
        }
    ]

    if scene_code:
        content.append({
            "type": "text",
            "text": (
                "The Manim source code that produced this video is provided below. "
                "When reporting issues, reference the specific segment or method "
                "in the code that is likely responsible.\n\n"
                f"```python\n{scene_code}\n```"
            ),
        })

    for i, (frame_path, t, seg_idx, seg_text) in enumerate(frame_tuples):
        if seg_idx is not None and seg_text:
            label = (
                f"Frame {i + 1}/{len(frame_tuples)} at t={t:.1f}s — "
                f"end of Segment {seg_idx}: \"{seg_text[:120]}\""
            )
        else:
            label = f"Frame {i + 1}/{len(frame_tuples)}"

        frame_data = base64.standard_b64encode(frame_path.read_bytes()).decode()
        content.append({"type": "text", "text": label})
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
