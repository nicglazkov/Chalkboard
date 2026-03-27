# pipeline/agents/manim_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are an expert Manim Community Edition (v0.20.1) developer.
Generate a complete, runnable Manim scene for an educational animation.

STRICT REQUIREMENTS:
- The scene class MUST be named exactly `ChalkboardScene` (inherits from Scene)
- Use `from manim import *` plus stdlib imports as needed (json, pathlib, etc.)
- Each narration segment gets an animation block followed by self.wait(duration_sec)
- Use self.play(..., run_time=X) for animations
- The code must be syntactically valid Python
- At the start of construct(), load actual segment durations:
    _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
    _d = [s["actual_duration_sec"] for s in _seg_data]
    _d = _d + [2.0] * max(0, N - len(_d))
  Replace N with the exact integer from "Total segments: N" shown above.
- Never hardcode a float literal as the argument to self.wait() — always use _d[i]
- When an animation fills part of a segment's time, subtract the animation's run_time from _d[i]:
    self.wait(max(0.0, _d[i] - X))  where X is the numeric value passed to run_time= above

KNOWN API PITFALLS (v0.20.1):
- Brace.get_text(*text) does NOT accept font_size — set it on the returned object: t = brace.get_text('x'); t.scale(0.8)
- VGroup.arrange() returns None — assign before arranging, don't chain
- Always pass run_time as a keyword arg: self.play(anim, run_time=1.0)
- Never use VGroup(*self.mobjects) — self.mobjects can contain non-VMobjects; use *[FadeOut(m) for m in self.mobjects] instead
- Never hardcode self.wait(X) with a float literal — always use _d[i] loaded from segments.json
- Use max(0.0, _d[i] - X) where X is the run_time= value passed to self.play() in that segment
- Pad _d with: _d = _d + [2.0] * max(0, N - len(_d)) where N is the literal integer segment count
- Segment numbers and _d indices are both 0-based — Segment 0 → _d[0], Segment 1 → _d[1]

Respond with JSON only: {"manim_code": "<complete Python code as string>"}"""


def _format_segments(segments: list[dict]) -> str:
    n = len(segments)
    header = f"Total segments: {n} (use _d[0] through _d[{max(0, n-1)}])"
    lines = [header]
    for i, seg in enumerate(segments):  # 0-based
        duration = seg.get("estimated_duration_sec", 0.0)
        text = seg.get("text", "")
        lines.append(f"  Segment {i} — est. {duration:.1f}s — use _d[{i}] at runtime: {text}")
    return "\n".join(lines)


def manim_agent(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    user_msg = (
        f"Create a Manim animation for this educational script.\n\n"
        f"Topic: {state['topic']}\n\n"
        f"Narration segments with timings:\n{_format_segments(state['script_segments'])}\n\n"
        f"Full script for context:\n{state['script']}"
    )

    if state.get("code_feedback"):
        user_msg += f"\n\nPrevious attempt had issues. Rewrite the scene fully, addressing:\n{state['code_feedback']}"

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16384,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {"manim_code": {"type": "string"}},
                    "required": ["manim_code"],
                    "additionalProperties": False,
                },
            }
        },
    )

    data = json.loads(response.content[0].text)
    return {"manim_code": data["manim_code"], "status": "validating"}
